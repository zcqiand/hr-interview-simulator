"""MockLLM（M04.F01.I03 运行时离线演示件）。

与测试用 FakeLLM 不同：MockLLM 按收到的消息**反应**——
分流四种 judge（简历/JD/匹配/报告），面试循环里先评价后出题、合理轮数后收尾。
LLM_MODE=mock 时 `python -m hr_interview` 全链路离线可演示。
"""
from __future__ import annotations

import json

from hr_interview.agent.interviewer import Interviewer
from hr_interview.mock_llm import MockLLM
from hr_interview.store import Store


def _sys(tag: str) -> dict:
    return {"role": "system", "content": f"你是{tag}。只输出 JSON 本体。"}


# ---- chat 分流：四种 judge ----


def test_mock_routes_resume_parser():
    llm = MockLLM()
    msg = llm.chat(messages=[_sys("简历解析器"), {"role": "user", "content": "张三 3 年 Python"}])
    data = json.loads(msg["content"])
    for key in ("skills", "experiences", "education", "highlights"):
        assert isinstance(data[key], list)


def test_mock_routes_jd_parser():
    llm = MockLLM()
    msg = llm.chat(messages=[_sys("JD 解析器"), {"role": "user", "content": "负责后端\n精通 Python\n有 CPA 优先"}])
    data = json.loads(msg["content"])
    assert isinstance(data["must"], list) and isinstance(data["nice"], list)


def test_mock_routes_matcher_with_verdicts():
    llm = MockLLM()
    user = (
        "【候选人画像】{\"skills\": [\"Python\"]}\n"
        "【简历原文】张三，3 年 Python 后端。\n"
        "【岗位必须项】[\"Python\", \"Kubernetes 运维\"]\n"
        "【岗位加分项】[]"
    )
    msg = llm.chat(messages=[_sys("岗位匹配评估器"), {"role": "user", "content": user}])
    data = json.loads(msg["content"])
    by_req = {i["requirement"]: i["verdict"] for i in data["items"]}
    assert by_req["Python"] == "match"           # 简历里有
    assert by_req["Kubernetes 运维"] == "miss"   # 简历里没有


def test_mock_routes_report_writer():
    llm = MockLLM()
    msg = llm.chat(messages=[_sys("面试总结官"), {"role": "user", "content": "【候选人】张三…"}])
    data = json.loads(msg["content"])
    assert isinstance(data["summary"], str) and isinstance(data["suggestions"], list)


# ---- chat_stream：面试行为机（先评价后出题，合理轮数收尾）----


def _setup(tmp_path):
    s = Store(str(tmp_path / "m.db"))
    cid = s.create_candidate(
        "张三", None, resume_text="3 年 Python 后端",
        profile_json=json.dumps({"skills": ["Python"]}, ensure_ascii=False),
    )
    return s, s.create_interview(cid, None, None)


def test_mock_interview_full_loop_to_report(tmp_path):
    s, iid = _setup(tmp_path)
    iv = Interviewer(MockLLM(), s)
    first = iv.start(iid)
    assert first["question"]

    done = None
    for _ in range(6):  # 演示脚本应在 4 轮问答内自动收尾
        events = list(iv.reply(iid, f"第 {_ + 1} 轮回答：我用 Python 做过高并发服务。"))
        ends = [e for e in events if e["type"] == "done" and e.get("status") == "ended"]
        if ends:
            done = ends[0]
            break
    assert done is not None, "MockLLM 应自动收尾出报告"
    assert done["report"]["summary"]
    evals = s.list_answer_evals(iid)
    assert len(evals) >= 3
    assert all(json.loads(e["scores_json"]) for e in evals)
    assert s.get_interview(iid)["status"] == "ended"
