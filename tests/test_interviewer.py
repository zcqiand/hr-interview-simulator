"""面试官 Agent（M02.F01 状态机/追问/护栏 + M02.F02 工具循环）。

FakeLLM 脚本驱动，不真调网。阶段机单向：opening→tech→project→reverse→closing。
"""
from __future__ import annotations

import json

from hr_interview.agent.interviewer import PHASES, Interviewer
from hr_interview.llm import FakeLLM, ToolCall, assistant_msg
from hr_interview.store import Store


def _setup(tmp_path) -> tuple[Store, str]:
    s = Store(str(tmp_path / "t.db"))
    cid = s.create_candidate(
        "张三", None,
        resume_text="3 年 Python 后端",
        profile_json=json.dumps({"skills": ["Python"]}, ensure_ascii=False),
    )
    iid = s.create_interview(cid, None, None)
    return s, iid


def _tool(name: str, args: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


# ---- start：开场题（M02.F01.I01/I02）----


def test_start_returns_first_question_and_persists(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[assistant_msg(content="请先做一个简短的自我介绍，重点讲你最熟的项目。")])
    iv = Interviewer(llm, s)
    q = iv.start(iid)
    assert "自我介绍" in q["question"]
    row = s.get_interview(iid)
    assert row["phase"] == "opening"
    assert row["question_count"] == 1
    msgs = s.get_messages(iid)
    assert msgs[-1]["role"] == "assistant"
    # 开场题的提示词带画像与阶段机
    sent = llm.calls[0]["messages"][0]["content"]
    assert "Python" in sent and "opening" in sent


# ---- reply：评价入档 + 出下一题（M02.F02.I01 record_answer_eval）----


def test_reply_eval_then_next_question(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[
        assistant_msg(content="好的。", tool_calls=[
            _tool("record_answer_eval", {
                "question": "自我介绍",
                "answer": "我叫张三，3 年 Python 后端。",
                "scores": {"tech_depth": 3, "clarity": 4, "evidence": 4, "fit": 4},
                "evidence": "3 年 Python 后端",
                "comment": "清楚但偏短",
            }),
        ]),
        assistant_msg(content="你提到 3 年 Python，讲一个你最有代表性的技术难点。"),
    ])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"question_count": 1})
    events = list(iv.reply(iid, "我叫张三，3 年 Python 后端。"))

    types = [e["type"] for e in events]
    assert "session" in types and "eval" in types and "delta" in types and "done" in types
    ev = next(e for e in events if e["type"] == "eval")["eval"]
    assert ev["evidence_verified"] is True  # 证据摘自回答
    assert ev["round"] == 1
    rows = s.list_answer_evals(iid)
    assert len(rows) == 1
    # 出了下一题 → 落库 + question_count++
    assert s.get_interview(iid)["question_count"] == 2
    assert "技术难点" in s.get_messages(iid)[-1]["content"]


def test_reply_tool_eval_evidence_discarded(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[
        assistant_msg(content=None, tool_calls=[
            _tool("record_answer_eval", {
                "question": "q", "answer": "a",
                "scores": {"tech_depth": 5, "clarity": 5, "evidence": 5, "fit": 5},
                "evidence": "回答里根本没这句话", "comment": "脑补",
            }),
        ]),
        assistant_msg(content="下一题。"),
    ])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"question_count": 1})
    events = list(iv.reply(iid, "我的回答"))
    ev = next(e for e in events if e["type"] == "eval")["eval"]
    assert ev["evidence"] == ""
    assert ev["evidence_verified"] is False


def test_reply_injects_recent_evals_into_prompt(tmp_path):
    """追问闭环的驱动机制：上一轮评价必须进系统提示词（M02.F01.I03）。"""
    s, iid = _setup(tmp_path)
    s.insert_answer_eval(
        iid, 1, "tech", "讲一个性能优化案例", "我用了缓存。",
        json.dumps({"tech_depth": 2, "clarity": 3, "evidence": 2, "fit": 3}),
        "我用了缓存", "深度不足，需追问证据",
    )
    llm = FakeLLM(turns=[assistant_msg(content="你说的缓存，命中率是多少？")])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"question_count": 1})
    list(iv.reply(iid, "我用了缓存。"))
    sent = llm.calls[0]["messages"][0]["content"]
    assert "近轮评价" in sent
    assert "深度不足，需追问证据" in sent  # 评价点评驱动追问
    assert "tech_depth" in sent


# ---- advance_stage：阶段推进（M02.F01.I02）----


def test_reply_advance_stage_resets_counter(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[
        assistant_msg(content=None, tool_calls=[_tool("advance_stage", {})]),
        assistant_msg(content="说说你项目中最大的故障。"),
    ])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"phase": "tech", "question_count": 3})
    events = list(iv.reply(iid, "回答……"))
    stage = next(e for e in events if e["type"] == "stage")
    assert stage["phase"] == "project"
    row = s.get_interview(iid)
    assert row["phase"] == "project"
    assert row["question_count"] == 1  # 推进后新题计数从 1 起


# ---- finish_interview：收尾出报告（M02.F02.I01 finish / M03.F02.I01）----


def test_finish_produces_report_and_done(tmp_path):
    s, iid = _setup(tmp_path)
    report_json = json.dumps({"summary": "基础扎实，深度不足。", "suggestions": ["多讲量化结果"]}, ensure_ascii=False)
    llm = FakeLLM(turns=[
        assistant_msg(content=None, tool_calls=[_tool("finish_interview", {})]),
        assistant_msg(content=report_json),  # build_report 消费
    ])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"question_count": 2})
    # 先落一条评价，报告聚合有数据
    s.insert_answer_eval(iid, 1, "tech", "q", "a", json.dumps({"tech_depth": 4, "clarity": 4, "evidence": 4, "fit": 3}), "", "")
    events = list(iv.reply(iid, "最后回答。"))
    done = next(e for e in events if e["type"] == "done")
    assert done["status"] == "ended"
    assert done["report"]["summary"] == "基础扎实，深度不足。"
    assert s.get_interview(iid)["status"] == "ended"


def test_reply_after_end_is_error(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"status": "ended"})
    events = list(iv.reply(iid, "还有吗？"))
    assert events[0]["type"] == "error"
    assert "结束" in events[0]["message"]


# ---- 护栏（M02.F01.I04）：阶段提问触顶强制推进 / 全程轮次触顶自动收尾 ----


def test_phase_limit_forces_advance(tmp_path):
    s, iid = _setup(tmp_path)
    # LLM 不调 advance，只出题 → 护栏强制推进
    llm = FakeLLM(turns=[assistant_msg(content="再问一个技术细节。")])
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"phase": "tech", "question_count": 3})
    events = list(iv.reply(iid, "答……"))
    stages = [e for e in events if e["type"] == "stage"]
    assert stages and stages[-1]["phase"] == "project"  # tech 触顶 → 强推 project
    assert s.get_interview(iid)["phase"] == "project"


def test_total_round_limit_auto_finishes(tmp_path):
    s, iid = _setup(tmp_path)
    report_json = json.dumps({"summary": "轮次已满。", "suggestions": []}, ensure_ascii=False)
    llm = FakeLLM(turns=[assistant_msg(content=report_json)])
    iv = Interviewer(llm, s, max_total_rounds=1)
    s.update_interview(iid, {"round": 1, "question_count": 1})
    events = list(iv.reply(iid, "答……"))
    done = next(e for e in events if e["type"] == "done")
    assert done["status"] == "ended"
    assert done["report"]["summary"] == "轮次已满。"


# ---- 阶段机纯函数 ----


def test_phase_machine_single_direction():
    assert PHASES == ("opening", "tech", "project", "reverse", "closing")
    iv = Interviewer(FakeLLM(), Store(":memory:"))
    assert iv.next_phase("opening") == "tech"
    assert iv.next_phase("closing") is None  # 终点


def test_reply_worker_crash_yields_error_and_terminates(tmp_path):
    """Review Focus 5：图内异常必须兜底成 error 事件且生成器终止，不挂死。"""
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[])  # 空脚本 → ask 内 FakeLLM 抛 AssertionError
    iv = Interviewer(llm, s)
    s.update_interview(iid, {"question_count": 1})
    events = list(iv.reply(iid, "我的回答"))  # 若挂死此行超时
    assert events[-1]["type"] == "error"
