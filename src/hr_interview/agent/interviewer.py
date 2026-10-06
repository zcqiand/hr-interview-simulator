"""面试官 Agent（M02.F01）：LangGraph 编排 + 评价驱动追问 + SSE 事件流。

v2 编排内核：阶段状态机/护栏/工具轮由 agent/graph.py 的 StateGraph 承载
（流程视角，书 ch10.3）。本模块只做接线——graph.invoke 跑在 worker 线程，
SSE 事件经队列旁路实时透出；事件形状与 v1 一致：
session / delta / reasoning / eval / stage / done / error。
"""
from __future__ import annotations

import queue
import threading
from collections.abc import Iterator

from hr_interview.agent.graph import InterviewState, build_graph
from hr_interview.agent.prompts import PHASES, next_phase
from hr_interview.llm import LLMClient
from hr_interview.store import Store

__all__ = ["PHASES", "Interviewer"]


class Interviewer:
    """无状态服务对象：持 LLM 与 Store，所有状态都在库里。"""

    def __init__(self, llm: LLMClient, store: Store, max_total_rounds: int = 12):
        self._llm = llm
        self._store = store
        self._max_total_rounds = max_total_rounds
        self._graph = build_graph(llm, store, max_total_rounds)

    def next_phase(self, phase: str) -> str | None:
        return next_phase(phase)

    # ---- 开场（M02.F01.I01）：同步走图，events=None 静默 ----

    def start(self, interview_id: str) -> dict:
        state: InterviewState = {"mode": "start", "interview_id": interview_id}
        result = self._graph.invoke(state, {"configurable": {"events": None}})
        return {"interview_id": interview_id, "question": result["question"]}

    # ---- 追问回合（M02.F01.I03 / M02.F02.I01）：worker 线程 + 队列旁路 ----

    def reply(self, interview_id: str, answer_text: str) -> Iterator[dict]:
        events: queue.SimpleQueue = queue.SimpleQueue()
        config = {"configurable": {"events": events}}
        state: InterviewState = {
            "mode": "reply", "interview_id": interview_id, "answer_text": answer_text,
        }

        def run() -> None:
            try:
                self._graph.invoke(state, config)
            except Exception as exc:  # 图内未捕获异常兜底为 error 事件（Review Focus 5）
                events.put({"type": "error", "message": f"处理异常：{exc}"})
            finally:
                events.put(None)  # 结束标记：消费端据此终止

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        for event in iter(events.get, None):
            yield event
        worker.join()
