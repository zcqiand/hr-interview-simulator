"""面试官 Agent（M02.F01）：阶段状态机 + 评价驱动的追问 + SSE 事件流。

reply() 是生成器，产事件：session / delta / reasoning / eval / stage / done / error。
护栏（M02.F01.I04）：每阶段提问上限（触顶强制推进）、全程轮次上限（触顶自动收尾出报告）、
单轮 LLM 往返上限、面试结束后作答直接报错。
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from hr_interview.agent.prompts import (
    PHASE_QUESTION_LIMIT,
    PHASES,
    build_system_prompt,
    next_phase,
)
from hr_interview.agent.tools import _MAX_TOOL_ROUNDS, TOOLS, execute_tool
from hr_interview.evaluation import build_report
from hr_interview.llm import LLMClient, tool_msg
from hr_interview.matching import build_focus
from hr_interview.store import Store, now_iso

__all__ = ["PHASES", "Interviewer"]

_EMPTY_FOCUS = {"opening": [], "tech": [], "project": []}
_HISTORY_LIMIT = 20  # 注入上下文的最近消息条数


class Interviewer:
    """无状态服务对象：持 LLM 与 Store，所有状态都在库里。"""

    def __init__(self, llm: LLMClient, store: Store, max_total_rounds: int = 12):
        self._llm = llm
        self._store = store
        self._max_total_rounds = max_total_rounds

    def next_phase(self, phase: str) -> str | None:
        return next_phase(phase)

    # ---- 开场（M02.F01.I01）----

    def start(self, interview_id: str) -> dict:
        iv = self._store.get_interview(interview_id)
        if iv is None:
            raise ValueError(f"面试不存在：{interview_id}")
        asked = [m for m in self._store.get_messages(interview_id) if m["role"] == "assistant"]
        if asked:  # 幂等：重复 start 返回最近一问，不再烧 LLM
            return {"interview_id": interview_id, "question": asked[-1]["content"]}
        candidate = self._store.get_candidate(iv["candidate_id"])
        jd = self._store.get_jd(iv["jd_id"]) if iv["jd_id"] else None
        system = build_system_prompt(iv, candidate, jd, self._load_focus(iv), [])
        msg = self._llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "（面试开始，请按当前阶段规则出第一题。）"},
            ],
            temperature=0.7,
        )
        question = (msg.get("content") or "").strip() or "请先做一个简短的自我介绍。"
        self._store.append_message(interview_id, "assistant", question, iv["phase"])
        self._store.update_interview(interview_id, {"question_count": 1})
        return {"interview_id": interview_id, "question": question}

    # ---- 追问回合（M02.F01.I03 / M02.F02.I01）----

    def reply(self, interview_id: str, answer_text: str) -> Iterator[dict]:
        iv = self._store.get_interview(interview_id)
        if iv is None:
            yield {"type": "error", "message": f"面试不存在：{interview_id}"}
            return
        if iv["status"] != "live":
            yield {"type": "error", "message": "面试已结束，不能再作答。"}
            return
        candidate = self._store.get_candidate(iv["candidate_id"])
        jd = self._store.get_jd(iv["jd_id"]) if iv["jd_id"] else None

        self._store.append_message(interview_id, "user", answer_text, iv["phase"])
        round_no = iv["round"] + 1
        self._store.update_interview(interview_id, {"round": round_no})
        yield {"type": "session", "interview_id": interview_id, "round": round_no, "phase": iv["phase"]}

        # 全程轮次护栏：已达上限 → 不再出题，自动收尾出报告
        if iv["round"] >= self._max_total_rounds:
            yield from self._finish(interview_id, candidate["name"])
            return

        # 近轮评价注入系统提示词——追问由评价驱动（本仓突出点）
        evals = self._store.list_answer_evals(interview_id)
        system = build_system_prompt(iv, candidate, jd, self._load_focus(iv), evals[-3:])
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in self._store.get_messages(interview_id, limit=_HISTORY_LIMIT)
        ]
        messages: list[dict] = [{"role": "system", "content": system}, *history]

        phase = iv["phase"]
        for _ in range(_MAX_TOOL_ROUNDS):
            final: dict | None = None
            for ev in self._llm.chat_stream(messages=messages, tools=TOOLS, temperature=0.7):
                if ev["type"] == "final":
                    final = ev["message"]
                else:
                    yield ev
            if final is None:
                yield {"type": "error", "message": "AI 没有返回内容，请重试。"}
                return
            calls = final.get("tool_calls") or []
            if not calls:  # 最终提问 → 落库并计数
                self._store.append_message(
                    interview_id, "assistant", (final.get("content") or "").strip(), phase
                )
                qc = self._store.get_interview(interview_id)["question_count"] + 1
                self._store.update_interview(interview_id, {"question_count": qc})
                break
            messages = [*messages, final]  # 工具轮回传由 LiveLLM._to_wire 转协议形状
            ended = False
            for call in calls:
                result, event = execute_tool(
                    call.name, call.arguments,
                    store=self._store, interview_id=interview_id,
                    candidate_name=candidate["name"], llm=self._llm, round_no=round_no,
                )
                messages.append(tool_msg(call.id, result))
                if event is not None:
                    if event["type"] == "stage":
                        phase = event["phase"]
                    elif event["type"] == "done":
                        ended = True
                    yield event
            if ended:
                return
        else:
            yield {"type": "error", "message": "单轮往返次数异常，请重试。"}
            return

        # 阶段触顶护栏：问满仍未推进 → 强制推进（不再问 LLM）
        after = self._store.get_interview(interview_id)
        nxt = next_phase(after["phase"])
        if nxt is not None and after["question_count"] >= PHASE_QUESTION_LIMIT:
            self._store.update_interview(interview_id, {"phase": nxt, "question_count": 0})
            yield {"type": "stage", "phase": nxt, "forced": True}
            after = self._store.get_interview(interview_id)

        yield {"type": "done", "status": "live", "round": round_no, "phase": after["phase"]}

    # ---- 收尾（M02.F02 finish / M03.F02.I01）----

    def _finish(self, interview_id: str, candidate_name: str) -> Iterator[dict]:
        report = build_report(
            self._llm, candidate_name, self._store.list_answer_evals(interview_id)
        )
        self._store.update_interview(interview_id, {
            "status": "ended", "ended_at": now_iso(),
            "report_json": json.dumps(report, ensure_ascii=False),
        })
        yield {"type": "done", "status": "ended", "report": report}

    def _load_focus(self, iv: dict) -> dict:
        if not iv["jd_id"]:
            return dict(_EMPTY_FOCUS)
        match = self._store.latest_match(iv["candidate_id"], iv["jd_id"])
        if match is None:
            return dict(_EMPTY_FOCUS)
        try:
            strengths = json.loads(match["strengths_json"] or "[]")
            items = json.loads(match["items_json"] or "[]")
        except json.JSONDecodeError:
            return dict(_EMPTY_FOCUS)
        return build_focus({"strengths": strengths, "items": items})
