"""面试流程 LangGraph 图（v2）：护栏/工具轮/事件序/上下文连续性。FakeLLM 驱动。"""
from __future__ import annotations

import json
import queue

from hr_interview.agent.graph import build_graph
from hr_interview.llm import FakeLLM, ToolCall, assistant_msg
from hr_interview.store import Store

_EVAL_ARGS = {
    "question": "自我介绍",
    "answer": "我叫张三，3 年 Python 后端。",
    "scores": {"tech_depth": 3, "clarity": 4, "evidence": 4, "fit": 4},
    "evidence": "3 年 Python 后端",
    "comment": "清楚但偏短",
}


def _setup(tmp_path, **iv_kw) -> tuple[Store, str]:
    s = Store(str(tmp_path / "t.db"))
    cid = s.create_candidate(
        "张三", None,
        resume_text="3 年 Python 后端",
        profile_json=json.dumps({"skills": ["Python"]}, ensure_ascii=False),
    )
    iid = s.create_interview(cid, None, None)
    if iv_kw:
        s.update_interview(iid, iv_kw)
    return s, iid


def _drain(graph, state: dict) -> list[dict]:
    q: queue.SimpleQueue = queue.SimpleQueue()
    graph.invoke(state, {"configurable": {"events": q}})
    out = []
    while not q.empty():
        out.append(q.get())
    return out


def _tool(name: str, args: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


# ---- start 模式 ----


def test_start_returns_question_and_persists(tmp_path):
    s, iid = _setup(tmp_path)
    llm = FakeLLM(turns=[assistant_msg(content="请先做一个简短的自我介绍。")])
    result = build_graph(llm, s).invoke(
        {"mode": "start", "interview_id": iid}, {"configurable": {"events": None}}
    )
    assert "自我介绍" in result["question"]
    assert s.get_interview(iid)["question_count"] == 1
    assert s.get_messages(iid)[-1]["role"] == "assistant"


def test_start_is_idempotent_no_llm_burn(tmp_path):
    s, iid = _setup(tmp_path, question_count=1)
    s.append_message(iid, "assistant", "已问过的问题", "opening")
    llm = FakeLLM(turns=[])  # 空脚本：若烧 LLM 会抛 AssertionError
    result = build_graph(llm, s).invoke(
        {"mode": "start", "interview_id": iid}, {"configurable": {"events": None}}
    )
    assert result["question"] == "已问过的问题"


def test_start_missing_interview_raises(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    llm = FakeLLM(turns=[])
    try:
        build_graph(llm, s).invoke(
            {"mode": "start", "interview_id": "nope"}, {"configurable": {"events": None}}
        )
        raise AssertionError("应当抛 ValueError")
    except ValueError as exc:
        assert "面试不存在" in str(exc)


# ---- reply 模式：主链路 ----


def test_reply_eval_then_next_question_event_order(tmp_path):
    s, iid = _setup(tmp_path, question_count=1)
    llm = FakeLLM(turns=[
        assistant_msg(content="好的。", tool_calls=[_tool("record_answer_eval", _EVAL_ARGS)]),
        assistant_msg(content="你提到 3 年 Python，讲一个你最有代表性的技术难点。"),
    ])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "我叫张三，3 年 Python 后端。"})
    types = [e["type"] for e in events]
    assert types[0] == "session"
    assert "eval" in types and "delta" in types and "done" in types
    assert types[-1] == "done"
    assert events[-1]["status"] == "live"
    ev = next(e for e in events if e["type"] == "eval")["eval"]
    assert ev["evidence_verified"] is True
    assert len(s.list_answer_evals(iid)) == 1
    assert s.get_interview(iid)["question_count"] == 2
    assert "技术难点" in s.get_messages(iid)[-1]["content"]


def test_ask_reentry_keeps_tool_messages(tmp_path):
    """Review Focus 3：第二轮 ask 必须带着第一轮的 assistant(tool_calls)+tool 消息。"""
    s, iid = _setup(tmp_path, question_count=1)
    llm = FakeLLM(turns=[
        assistant_msg(content="好的。", tool_calls=[_tool("record_answer_eval", _EVAL_ARGS)]),
        assistant_msg(content="下一题：讲讲并发。"),
    ])
    _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "我叫张三。"})
    second = llm.calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "tool"]
    assert "评价已入档" in second[-1]["content"]


def test_reply_guard_total_finishes(tmp_path):
    """Review Focus 4：v1 语义——旧 round 判顶；max_total_rounds=1 时第 2 轮直接收尾。"""
    s, iid = _setup(tmp_path, round=1, question_count=1)
    llm = FakeLLM(turns=[assistant_msg(content='{"summary": "整体尚可", "suggestions": ["补数据"]}')])
    events = _drain(build_graph(llm, s, max_total_rounds=1), {"mode": "reply", "interview_id": iid, "answer_text": "回答。"})
    types = [e["type"] for e in events]
    assert types[0] == "session"  # 先 session 再收尾
    assert types == ["session", "done"]
    assert events[-1]["status"] == "ended"
    assert events[-1]["report"]["summary"] == "整体尚可"
    assert s.get_interview(iid)["status"] == "ended"


def test_reply_phase_cap_forced_advance(tmp_path):
    """阶段触顶：问满 3 题未推进 → 强制推进，不问 LLM（stage forced 事件）。"""
    s, iid = _setup(tmp_path, question_count=3)
    llm = FakeLLM(turns=[assistant_msg(content="还在 opening 的最后一题。")])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "答。"})
    stage = next(e for e in events if e["type"] == "stage")
    assert stage["phase"] == "tech" and stage["forced"] is True
    assert events[-1]["phase"] == "tech"


def test_reply_finish_tool_ends_without_done_dup(tmp_path):
    """finish_interview 工具收尾：done(ended) 只发一次，无 forced_advance/done(live)。"""
    s, iid = _setup(tmp_path, question_count=1)
    llm = FakeLLM(turns=[
        assistant_msg(content="好的。", tool_calls=[_tool("record_answer_eval", _EVAL_ARGS)]),
        assistant_msg(content="感谢参与。", tool_calls=[_tool("finish_interview", {}, call_id="call_2")]),
        assistant_msg(content='{"summary": "整体不错", "suggestions": ["补数据"]}'),  # build_report 轮
    ])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "答。"})
    dones = [e for e in events if e["type"] == "done"]
    assert len(dones) == 1 and dones[0]["status"] == "ended"
    assert "report" in dones[0]
    assert s.get_interview(iid)["status"] == "ended"


def test_reply_tool_rounds_exhausted_errors(tmp_path):
    """单轮 4 次往返全是工具调用 → 执行完第 4 次后报错，不再问 LLM。"""
    s, iid = _setup(tmp_path, question_count=1)
    llm = FakeLLM(turns=[
        assistant_msg(content=None, tool_calls=[_tool("record_answer_eval", _EVAL_ARGS, call_id=f"c{i}")])
        for i in range(4)
    ])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "答。"})
    assert events[-1] == {"type": "error", "message": "单轮往返次数异常，请重试。"}
    assert len(llm.calls) == 4  # 第 5 次没问


# ---- reply 模式：错误路径 ----


def test_reply_missing_interview_yields_error(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    llm = FakeLLM(turns=[])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": "nope", "answer_text": "答"})
    assert events == [{"type": "error", "message": "面试不存在：nope"}]


def test_reply_ended_interview_yields_error(tmp_path):
    s, iid = _setup(tmp_path, status="ended")
    llm = FakeLLM(turns=[])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "答"})
    assert events == [{"type": "error", "message": "面试已结束，不能再作答。"}]


def test_reply_no_final_yields_error(tmp_path):
    """chat_stream 没给 final → error，且不再有 done。"""
    s, iid = _setup(tmp_path, question_count=1)

    class EmptyStreamLLM(FakeLLM):
        def chat_stream(self, *, messages, tools=None, temperature=0.8):
            self.calls.append({"messages": messages, "tools": tools})
            if not self.turns:
                raise AssertionError("FakeLLM 脚本耗尽")
            self.turns.pop(0)
            yield {"type": "final", "message": None}  # 异常形状：final 为 None

    llm = EmptyStreamLLM(turns=[assistant_msg(content="x")])
    events = _drain(build_graph(llm, s), {"mode": "reply", "interview_id": iid, "answer_text": "答"})
    # v1 锚：session 先于首次 ask（interviewer.reply:81），错误后无 done
    assert [e["type"] for e in events] == ["session", "error"]
    assert events[-1] == {"type": "error", "message": "AI 没有返回内容，请重试。"}
