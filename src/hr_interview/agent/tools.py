"""面试官工具表（M02.F02）：评价入档 / 阶段推进 / 收尾出报告。

工具是面试官 Agent 的手脚；评价铁纪律（证据摘原话、分数夹取）统一走
evaluation.eval_from_payload，工具层不做第二套校验。
"""
from __future__ import annotations

from hr_interview.agent.prompts import next_phase
from hr_interview.evaluation import build_report, eval_from_payload
from hr_interview.llm import LLMClient
from hr_interview.store import now_iso

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "record_answer_eval",
            "description": "评价候选人刚给出的回答并入档。每轮回答后必须先调用它，再出下一题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "面试官刚问的问题"},
                    "answer": {"type": "string", "description": "候选人回答原文"},
                    "scores": {
                        "type": "object",
                        "properties": {
                            "tech_depth": {"type": "integer", "description": "技术深度 1-5"},
                            "clarity": {"type": "integer", "description": "表达清晰 1-5"},
                            "evidence": {"type": "integer", "description": "证据可信 1-5"},
                            "fit": {"type": "integer", "description": "岗位匹配 1-5"},
                        },
                        "required": ["tech_depth", "clarity", "evidence", "fit"],
                    },
                    "evidence": {
                        "type": "string",
                        "description": "支撑评分的回答原话摘句，必须逐字来自回答",
                    },
                    "comment": {"type": "string", "description": "一句话中文点评"},
                },
                "required": ["question", "answer", "scores"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "advance_stage",
            "description": "推进到下一面试阶段（单向）。当前阶段的问题问完时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_interview",
            "description": "结束面试并生成结构化总评报告。反问与收尾完成后调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_MAX_TOOL_ROUNDS = 4  # 单轮回答的 LLM 往返上限，防无限工具循环


def execute_tool(
    name: str,
    args: dict,
    *,
    store,
    interview_id: str,
    candidate_name: str,
    llm: LLMClient,
    round_no: int,
) -> tuple[str, dict | None]:
    """执行一个工具调用，返回 (给 LLM 的结果文本, 给前端的 SSE 事件或 None)。"""
    if not isinstance(args, dict):
        args = {}

    if name == "record_answer_eval":
        iv = store.get_interview(interview_id)
        scores = args.get("scores")
        ev = eval_from_payload(
            store, interview_id, round_no,
            iv["phase"] if iv else "",
            str(args.get("question") or ""),
            str(args.get("answer") or ""),
            scores if isinstance(scores, dict) else {},
            str(args.get("evidence") or ""),
            str(args.get("comment") or ""),
        )
        return "评价已入档，请继续出下一题或推进阶段。", {"type": "eval", "eval": ev}

    if name == "advance_stage":
        iv = store.get_interview(interview_id)
        nxt = next_phase(iv["phase"]) if iv else None
        if nxt is None:
            return "已是最后一个阶段，不能再推进。", None
        store.update_interview(interview_id, {"phase": nxt, "question_count": 0})
        return f"已进入阶段 {nxt}。", {"type": "stage", "phase": nxt}

    if name == "finish_interview":
        iv = store.get_interview(interview_id)
        if iv is None or iv["status"] != "live":
            return "面试已结束。", None
        report = build_report(llm, candidate_name, store.list_answer_evals(interview_id))
        store.update_interview(interview_id, {"status": "ended", "ended_at": now_iso()})
        return "面试已结束，总评报告已生成。", {"type": "done", "status": "ended", "report": report}

    return f"未支持的工具：{name}", None
