"""匹配打分（M01.F01.I01 逐条对照 + M01.F02.I01 出题侧重）。"""
from __future__ import annotations

from hr_interview.llm import FakeLLM, assistant_msg
from hr_interview.matching import build_focus, match_jd

_PROFILE = {
    "skills": ["Python", "FastAPI"],
    "experiences": ["3 年后端开发"],
    "education": ["本科"],
    "highlights": [],
}
_RESUME = "张三，3 年 Python 后端经验，熟悉 FastAPI，做过高并发服务。"
_REQS = {"must": ["精通 Python", "熟悉 Kubernetes 运维"], "nice": ["了解 FastAPI"]}


def _llm_with(payload: str, n: int = 1) -> FakeLLM:
    return FakeLLM(turns=[assistant_msg(content=payload)] * n)


# ---- LLM 判定 + 计分 ----

_GOOD = (
    '{"items": ['
    '{"requirement": "精通 Python", "verdict": "match", "note": "简历写 3 年 Python"},'
    '{"requirement": "熟悉 Kubernetes 运维", "verdict": "miss", "note": "简历未提及"},'
    '{"requirement": "了解 FastAPI", "verdict": "match", "note": "简历写熟悉 FastAPI"}]}'
)


def test_match_good_json_score_math():
    llm = _llm_with(_GOOD)
    r = match_jd(llm, _PROFILE, _RESUME, _REQS)
    # must: 90 * (1 + 0) / 2 = 45；nice: 10 * 1 / 1 = 10 → 55
    assert r["total_score"] == 55
    assert r["parsed"] is True
    assert [i["verdict"] for i in r["items"]] == ["match", "miss", "match"]
    assert r["strengths"] == ["精通 Python", "了解 FastAPI"]
    assert r["gaps"] == [{"requirement": "熟悉 Kubernetes 运维", "kind": "must", "note": "简历未提及"}]
    # 逐条 note 必须基于材料——提示词里带简历原文
    sent = llm.calls[0]["messages"][-1]["content"]
    assert "FastAPI" in sent and "精通 Python" in sent


def test_match_retry_once_on_bad_json():
    llm = FakeLLM(
        turns=[assistant_msg(content="这位候选人整体不错！"), assistant_msg(content=_GOOD)]
    )
    r = match_jd(llm, _PROFILE, _RESUME, _REQS)
    assert r["parsed"] is True
    assert len(llm.calls) == 2


def test_match_partial_verdict_counts_half():
    payload = (
        '{"items": ['
        '{"requirement": "精通 Python", "verdict": "partial", "note": "只有 3 年"},'
        '{"requirement": "熟悉 Kubernetes 运维", "verdict": "partial", "note": "疑似沾边"}]}'
    )
    llm = _llm_with(payload)
    r = match_jd(llm, _PROFILE, _RESUME, _REQS)
    # must: 90 * 0.5/2 + 90 * 0.5/2 = 45
    assert r["total_score"] == 45


def test_match_invalid_verdict_treated_as_miss():
    payload = (
        '{"items": ['
        '{"requirement": "精通 Python", "verdict": "super", "note": "?!"},'
        '{"requirement": "熟悉 Kubernetes 运维", "verdict": "match", "note": "有"}]}'
    )
    llm = _llm_with(payload)
    r = match_jd(llm, _PROFILE, _RESUME, _REQS)
    assert r["items"][0]["verdict"] == "miss"
    assert r["total_score"] == 45


def test_match_empty_requirements_zero():
    llm = _llm_with(_GOOD)
    r = match_jd(llm, _PROFILE, _RESUME, {"must": [], "nice": []})
    assert r["total_score"] == 0
    assert r["items"] == []
    assert r["parsed"] is False  # 没判任何条目


# ---- 启发式兜底（LLM 两次都不给 JSON）----


def test_match_heuristic_fallback():
    llm = FakeLLM(turns=[assistant_msg(content="不行"), assistant_msg(content="还是不行")])
    reqs = {"must": ["Python", "Kubernetes 运维"], "nice": ["SQL"]}
    r = match_jd(llm, _PROFILE, _RESUME, reqs)
    assert r["parsed"] is False
    assert len(llm.calls) == 2  # 兜底不调第三次
    by_req = {i["requirement"]: i["verdict"] for i in r["items"]}
    assert by_req["Python"] == "match"          # 简历里有 Python
    assert by_req["Kubernetes 运维"] == "miss"  # 简历里没有
    assert by_req["SQL"] == "miss"


def test_match_heuristic_partial_ratio():
    llm = FakeLLM(turns=[assistant_msg(content="x"), assistant_msg(content="y")])
    reqs = {"must": ["Python SQL"], "nice": []}
    r = match_jd(llm, _PROFILE, _RESUME, reqs)
    assert r["items"][0]["verdict"] == "partial"  # Python 命中 / SQL 未命中 → 部分


# ---- 出题侧重（M01.F02.I01）----


def test_build_focus_splits_gaps_and_partials():
    match = {
        "strengths": ["精通 Python", "了解 FastAPI"],
        "gaps": [
            {"requirement": "熟悉 Kubernetes 运维", "kind": "must", "note": "简历未提及"},
            {"requirement": "有高并发经验", "kind": "must", "note": "存疑"},
        ],
        "items": [
            {"requirement": "精通 Python", "kind": "must", "verdict": "match", "note": "3 年"},
            {"requirement": "熟悉 Kubernetes 运维", "kind": "must", "verdict": "miss", "note": "简历未提及"},
            {"requirement": "有高并发经验", "kind": "must", "verdict": "partial", "note": "存疑"},
        ],
    }
    focus = build_focus(match)
    assert focus["opening"] == ["精通 Python", "了解 FastAPI"]      # 开场暖场点名强项
    assert focus["tech"] == [{"requirement": "熟悉 Kubernetes 运维", "note": "简历未提及"}]   # miss→技术题验证
    assert focus["project"] == [{"requirement": "有高并发经验", "note": "存疑"}]              # partial→项目题深挖


def test_match_role_marker_reaches_llm():
    """MockLLM 演示靠 system 里的「岗位匹配评估器」分流（Review Focus 1）。"""
    llm = _llm_with(_GOOD)
    match_jd(llm, _PROFILE, _RESUME, _REQS)
    assert "岗位匹配评估器" in llm.calls[0]["messages"][0]["content"]


def test_match_mock_llm_demo_produces_scored_items():
    """端到端钉：MockLLM 走 crew 路径的打分产物等价 v1（终审 Important #2）。"""
    from hr_interview.mock_llm import MockLLM

    llm = MockLLM()
    r = match_jd(llm, _PROFILE, _RESUME, _REQS)
    assert r["parsed"] is True
    assert len(r["items"]) == 3
    assert all(i["verdict"] in ("match", "partial", "miss") for i in r["items"])
    assert r["total_score"] > 0
