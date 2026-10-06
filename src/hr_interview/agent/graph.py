"""面试流程 LangGraph 图（v2 编排内核·流程视角，书 ch10.3）。

阶段状态机与护栏路由由图节点 + 条件边承载；节点内复用现有 LLMClient
（FakeLLM/MockLLM 协议未变，无 Key 无网门禁不变）。SSE 事件经 events
队列旁路（interviewer.ReplyStream 消费），State 只带结构化结果；
持久化唯一真相仍是 store.py SQLite，每回合从 DAO 重建，无 checkpointer。

行为等价锚（对照 v1 interviewer.py）：
- guard_total 用旧 round 判顶（append/update 之后比较旧值）
- 工具轮上限 _MAX_TOOL_ROUNDS：第 4 次工具调用执行后才报「单轮往返次数异常」
- finish_interview 工具自带出报告与 done(ended)，图不再补发
- 阶段触顶强制推进不问 LLM，stage 事件带 forced=True
"""
from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from hr_interview.agent.prompts import (
    PHASE_QUESTION_LIMIT,
    build_system_prompt,
    next_phase,
)
from hr_interview.agent.tools import _MAX_TOOL_ROUNDS, TOOLS, execute_tool
from hr_interview.evaluation import build_report
from hr_interview.llm import LLMClient, tool_msg
from hr_interview.matching import build_focus
from hr_interview.store import Store, now_iso

__all__ = ["InterviewState", "build_graph"]

_HISTORY_LIMIT = 20  # 与 v1 一致：注入上下文的最近消息条数
_EMPTY_FOCUS = {"opening": [], "tech": [], "project": []}


class InterviewState(TypedDict, total=False):
    mode: str            # "start" | "reply"
    interview_id: str
    answer_text: str
    round: int
    phase: str
    question_count: int
    tool_rounds: int     # ask 已走次数（工具轮护栏）
    messages: list[dict]  # 本回合 LLM 对话（工具轮间延续，不落库）
    final: dict          # ask 产出的 final 消息
    ended: bool          # finish_interview 工具已触发
    error: str           # 终止错误（事件已发）
    question: str        # start 模式的开场题


def _emit(config: dict, event: dict) -> None:
    q = (config or {}).get("configurable", {}).get("events")
    if q is not None:
        q.put(event)


def _load_focus(store: Store, iv: dict) -> dict:
    if not iv["jd_id"]:
        return dict(_EMPTY_FOCUS)
    match = store.latest_match(iv["candidate_id"], iv["jd_id"])
    if match is None:
        return dict(_EMPTY_FOCUS)
    try:
        strengths = json.loads(match["strengths_json"] or "[]")
        items = json.loads(match["items_json"] or "[]")
    except json.JSONDecodeError:
        return dict(_EMPTY_FOCUS)
    return build_focus({"strengths": strengths, "items": items})


def _make_begin(store: Store):
    def begin(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        if state["mode"] == "start":
            if iv is None:
                raise ValueError(f"面试不存在：{state['interview_id']}")
            return {}
        if iv is None:
            _emit(config, {"type": "error", "message": f"面试不存在：{state['interview_id']}"})
            return {"error": "not_found"}
        if iv["status"] != "live":
            _emit(config, {"type": "error", "message": "面试已结束，不能再作答。"})
            return {"error": "not_live"}
        return {"phase": iv["phase"], "question_count": iv["question_count"]}

    return begin


def _make_start_question(llm: LLMClient, store: Store):
    def start_question(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        asked = [m for m in store.get_messages(state["interview_id"]) if m["role"] == "assistant"]
        if asked:  # 幂等：重复 start 返回最近一问，不再烧 LLM
            return {"question": asked[-1]["content"]}
        candidate = store.get_candidate(iv["candidate_id"])
        jd = store.get_jd(iv["jd_id"]) if iv["jd_id"] else None
        system = build_system_prompt(iv, candidate, jd, _load_focus(store, iv), [])
        msg = llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "（面试开始，请按当前阶段规则出第一题。）"},
            ],
            temperature=0.7,
        )
        question = (msg.get("content") or "").strip() or "请先做一个简短的自我介绍。"
        store.append_message(state["interview_id"], "assistant", question, iv["phase"])
        store.update_interview(state["interview_id"], {"question_count": 1})
        return {"question": question}

    return start_question


def _make_ingest(store: Store):
    def ingest(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        store.append_message(state["interview_id"], "user", state["answer_text"], iv["phase"])
        round_no = iv["round"] + 1
        store.update_interview(state["interview_id"], {"round": round_no})
        _emit(config, {
            "type": "session", "interview_id": state["interview_id"],
            "round": round_no, "phase": iv["phase"],
        })
        return {"round": round_no, "phase": iv["phase"], "question_count": iv["question_count"]}

    return ingest


def _make_ask(llm: LLMClient, store: Store):
    def ask(state: InterviewState, config: dict) -> dict:
        if state.get("messages"):
            messages = state["messages"]  # 工具轮重入：带上下文续问（Review Focus 3）
        else:
            iv = store.get_interview(state["interview_id"])
            candidate = store.get_candidate(iv["candidate_id"])
            jd = store.get_jd(iv["jd_id"]) if iv["jd_id"] else None
            evals = store.list_answer_evals(state["interview_id"])
            system = build_system_prompt(iv, candidate, jd, _load_focus(store, iv), evals[-3:])
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in store.get_messages(state["interview_id"], limit=_HISTORY_LIMIT)
            ]
            messages = [{"role": "system", "content": system}, *history]

        final: dict | None = None
        for ev in llm.chat_stream(messages=messages, tools=TOOLS, temperature=0.7):
            if ev["type"] == "final":
                final = ev["message"]
            else:
                _emit(config, ev)
        if final is None:
            _emit(config, {"type": "error", "message": "AI 没有返回内容，请重试。"})
            return {"error": "no_final", "messages": messages}
        return {
            "final": final, "messages": messages,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }

    return ask


def _make_execute_tools(llm: LLMClient, store: Store):
    def execute_tools(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        candidate = store.get_candidate(iv["candidate_id"])
        messages = [*state["messages"], state["final"]]
        ended = bool(state.get("ended"))
        for call in state["final"].get("tool_calls") or []:
            result, event = execute_tool(
                call.name, call.arguments,
                store=store, interview_id=state["interview_id"],
                candidate_name=candidate["name"], llm=llm, round_no=state["round"],
            )
            messages.append(tool_msg(call.id, result))
            if event is not None:
                if event["type"] == "done":
                    ended = True
                _emit(config, event)
        return {"messages": messages, "ended": ended}

    return execute_tools


def _make_persist_question(store: Store):
    def persist_question(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        content = (state["final"].get("content") or "").strip()
        store.append_message(state["interview_id"], "assistant", content, iv["phase"])
        qc = iv["question_count"] + 1
        store.update_interview(state["interview_id"], {"question_count": qc})
        return {"question_count": qc}

    return persist_question


def _make_forced_advance(store: Store):
    def forced_advance(state: InterviewState, config: dict) -> dict:
        after = store.get_interview(state["interview_id"])
        nxt = next_phase(after["phase"])
        if nxt is not None and after["question_count"] >= PHASE_QUESTION_LIMIT:
            store.update_interview(state["interview_id"], {"phase": nxt, "question_count": 0})
            _emit(config, {"type": "stage", "phase": nxt, "forced": True})
            after = store.get_interview(state["interview_id"])
        _emit(config, {
            "type": "done", "status": "live",
            "round": state["round"], "phase": after["phase"],
        })
        return {}

    return forced_advance


def _make_tool_exhausted(store: Store):
    def tool_exhausted(state: InterviewState, config: dict) -> dict:
        _emit(config, {"type": "error", "message": "单轮往返次数异常，请重试。"})
        return {"error": "tool_exhausted"}

    return tool_exhausted


def _make_finish(llm: LLMClient, store: Store):
    def finish(state: InterviewState, config: dict) -> dict:
        iv = store.get_interview(state["interview_id"])
        candidate = store.get_candidate(iv["candidate_id"])
        report = build_report(llm, candidate["name"], store.list_answer_evals(state["interview_id"]))
        store.update_interview(state["interview_id"], {
            "status": "ended", "ended_at": now_iso(),
            "report_json": json.dumps(report, ensure_ascii=False),
        })
        _emit(config, {"type": "done", "status": "ended", "report": report})
        return {}

    return finish


# ---- 路由（条件边）----


def _route_begin(state: InterviewState) -> str:
    if state.get("error"):
        return "end"
    return "start_question" if state["mode"] == "start" else "ingest"


def _route_after_ingest(max_total_rounds: int):
    def route(state: InterviewState) -> str:
        # v1 语义：用旧 round 判顶（Review Focus 4）
        return "finish" if state["round"] - 1 >= max_total_rounds else "ask"

    return route


def _route_after_ask(state: InterviewState) -> str:
    if state.get("error"):
        return "end"
    calls = (state.get("final") or {}).get("tool_calls") or []
    return "execute_tools" if calls else "persist_question"


def _route_after_tools(state: InterviewState) -> str:
    if state.get("ended"):
        return "end"  # done(ended) 已由工具发出，图不再补发
    if state.get("tool_rounds", 0) >= _MAX_TOOL_ROUNDS:
        return "tool_exhausted"
    return "ask"


def build_graph(llm: LLMClient, store: Store, max_total_rounds: int = 12):
    g = StateGraph(InterviewState)
    g.add_node("begin", _make_begin(store))
    g.add_node("start_question", _make_start_question(llm, store))
    g.add_node("ingest", _make_ingest(store))
    g.add_node("ask", _make_ask(llm, store))
    g.add_node("execute_tools", _make_execute_tools(llm, store))
    g.add_node("persist_question", _make_persist_question(store))
    g.add_node("forced_advance", _make_forced_advance(store))
    g.add_node("tool_exhausted", _make_tool_exhausted(store))
    g.add_node("finish", _make_finish(llm, store))

    g.add_edge(START, "begin")
    g.add_conditional_edges(
        "begin", _route_begin,
        {"start_question": "start_question", "ingest": "ingest", "end": END},
    )
    g.add_edge("start_question", END)
    g.add_conditional_edges(
        "ingest", _route_after_ingest(max_total_rounds),
        {"ask": "ask", "finish": "finish"},
    )
    g.add_conditional_edges(
        "ask", _route_after_ask,
        {"execute_tools": "execute_tools", "persist_question": "persist_question", "end": END},
    )
    g.add_conditional_edges(
        "execute_tools", _route_after_tools,
        {"ask": "ask", "end": END, "tool_exhausted": "tool_exhausted"},
    )
    g.add_edge("persist_question", "forced_advance")
    g.add_edge("forced_advance", END)
    g.add_edge("tool_exhausted", END)
    g.add_edge("finish", END)
    return g.compile()
