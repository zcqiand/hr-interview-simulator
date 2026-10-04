"""评价引擎（M03.F01 维度评价 + M03.F02 总评报告）。

核心纪律：证据必须摘回答原话（evidence 子串校验），不许面试官脑补。
"""
from __future__ import annotations

import json

from hr_interview.evaluation import (
    DIMENSIONS,
    aggregate,
    build_report,
    record_eval,
    verdict_of,
)
from hr_interview.llm import FakeLLM, assistant_msg
from hr_interview.store import Store

_ANSWER = "我做过一个秒杀系统，用 Redis 做了库存预扣，QPS 从 800 提到 5000。"
_GOOD_EVAL = (
    '{"scores": {"tech_depth": 4, "clarity": 4, "evidence": 5, "fit": 3},'
    f' "evidence": "用 Redis 做了库存预扣",'
    ' "comment": "有量化结果，技术细节可信"}'
)


def _store_with_interview(tmp_path) -> tuple[Store, str]:
    s = Store(str(tmp_path / "t.db"))
    cid = s.create_candidate("张三", None)
    iid = s.create_interview(cid, None, None)
    return s, iid


# ---- record_eval（M03.F01.I01/I02）----


def test_record_eval_good(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    llm = FakeLLM(turns=[assistant_msg(content=_GOOD_EVAL)])
    ev = record_eval(llm, s, iid, round_no=1, phase="tech", question="讲一个性能优化案例", answer=_ANSWER)
    assert ev["scores"] == {"tech_depth": 4, "clarity": 4, "evidence": 5, "fit": 3}
    assert ev["evidence"] == "用 Redis 做了库存预扣"
    assert ev["evidence_verified"] is True  # 摘自原话
    rows = s.list_answer_evals(iid)
    assert len(rows) == 1
    assert rows[0]["answer"] == _ANSWER


def test_record_eval_evidence_must_quote_answer(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    payload = (
        '{"scores": {"tech_depth": 4, "clarity": 4, "evidence": 5, "fit": 3},'
        ' "evidence": "我主导了集团级中台建设",'
        ' "comment": "吹得挺大"}'
    )
    llm = FakeLLM(turns=[assistant_msg(content=payload)])
    ev = record_eval(llm, s, iid, 1, "tech", "问", _ANSWER)
    assert ev["evidence"] == ""          # 不是原话 → 丢弃
    assert ev["evidence_verified"] is False


def test_record_eval_score_clamped(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    payload = (
        '{"scores": {"tech_depth": 7, "clarity": 0, "evidence": 3, "fit": "4"},'
        f' "evidence": "秒杀系统",'
        ' "comment": "ok"}'
    )
    llm = FakeLLM(turns=[assistant_msg(content=payload)])
    ev = record_eval(llm, s, iid, 1, "tech", "问", _ANSWER)
    assert ev["scores"]["tech_depth"] == 5
    assert ev["scores"]["clarity"] == 1
    assert ev["scores"]["fit"] == 4  # 数字串也接受


def test_record_eval_missing_dimension_neutral(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    payload = '{"scores": {"tech_depth": 4}, "evidence": "秒杀系统", "comment": "略"}'
    llm = FakeLLM(turns=[assistant_msg(content=payload)])
    ev = record_eval(llm, s, iid, 1, "tech", "问", _ANSWER)
    assert ev["scores"]["tech_depth"] == 4
    assert ev["scores"]["clarity"] == 3  # 缺失维度给中性分
    assert ev["scores"]["fit"] == 3


def test_record_eval_retry_once(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    llm = FakeLLM(turns=[assistant_msg(content="答得不错"), assistant_msg(content=_GOOD_EVAL)])
    ev = record_eval(llm, s, iid, 1, "tech", "问", _ANSWER)
    assert ev["evidence_verified"] is True
    assert len(llm.calls) == 2


def test_record_eval_heuristic_fallback(tmp_path):
    s, iid = _store_with_interview(tmp_path)
    llm = FakeLLM(turns=[assistant_msg(content="嗯"), assistant_msg(content="哼")])
    ev = record_eval(llm, s, iid, 1, "tech", "问", _ANSWER)
    assert ev["scores"] == {k: 3 for k, _ in DIMENSIONS}
    assert ev["evidence_verified"] is False
    assert "兜底" in ev["comment"]
    assert len(llm.calls) == 2  # 不调第三次


# ---- aggregate / verdict_of ----


def _eval_row(scores: dict) -> dict:
    return {"scores_json": json.dumps(scores, ensure_ascii=False), "question": "q", "answer": "a"}


def test_verdict_boundaries():
    assert verdict_of(4.0) == "强"
    assert verdict_of(4.5) == "强"
    assert verdict_of(3.0) == "中"
    assert verdict_of(3.9) == "中"
    assert verdict_of(2.5) == "弱"
    assert verdict_of(1.0) == "弱"


def test_aggregate_mixed_rounds():
    evals = [
        _eval_row({"tech_depth": 4, "clarity": 4, "evidence": 5, "fit": 3}),
        _eval_row({"tech_depth": 5, "clarity": 2, "evidence": 3, "fit": 3}),
    ]
    agg = aggregate(evals)
    by_key = {d["key"]: d for d in agg["dimensions"]}
    assert by_key["tech_depth"]["avg"] == 4.5
    assert by_key["tech_depth"]["verdict"] == "强"
    assert by_key["clarity"]["avg"] == 3.0
    assert by_key["clarity"]["verdict"] == "中"
    assert by_key["evidence"]["avg"] == 4.0
    assert agg["rounds"] == 2


def test_aggregate_empty():
    agg = aggregate([])
    assert agg["rounds"] == 0
    assert all(d["avg"] == 0 for d in agg["dimensions"])


# ---- build_report（M03.F02.I01）----

_EVALS = [
    _eval_row({"tech_depth": 5, "clarity": 4, "evidence": 5, "fit": 4}),
    _eval_row({"tech_depth": 2, "clarity": 4, "evidence": 2, "fit": 3}),
]


def test_build_report_llm_summary():
    llm = FakeLLM(turns=[assistant_msg(content='{"summary": "技术强表达稳，系统设计待补。", "suggestions": ["补系统设计题练习", "回答多给量化数据"]}')])
    report = build_report(llm, "张三", _EVALS)
    assert report["summary"] == "技术强表达稳，系统设计待补。"
    assert len(report["suggestions"]) == 2
    assert report["candidate_name"] == "张三"
    assert report["rounds"] == 2
    assert report["dimensions"][0]["key"] == "tech_depth"
    sent = llm.calls[0]["messages"][-1]["content"]
    assert "张三" in sent and "tech_depth" in sent  # 聚合+逐轮明细都进提示词


def test_build_report_retry_and_rule_fallback():
    llm = FakeLLM(turns=[assistant_msg(content="总结：还行。"), assistant_msg(content="依旧不是 JSON")])
    report = build_report(llm, "张三", _EVALS)
    assert len(llm.calls) == 2
    assert report["summary"]           # 规则兜底也有话可说
    assert isinstance(report["suggestions"], list) and report["suggestions"]
    # 最弱维度（证据可信 3.5 vs 技术深度 3.5 vs fit 3.5 — 全 3.5？）重新算：
    # tech (5+2)/2=3.5, clarity 4, evidence (5+2)/2=3.5, fit 3.5 → 最弱并列，建议非空即可
    by_key = {d["key"]: d for d in report["dimensions"]}
    assert by_key["clarity"]["avg"] == 4.0
