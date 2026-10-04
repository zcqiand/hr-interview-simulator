"""MockLLM（M04.F01.I03）：运行时离线演示件（LLM_MODE=mock）。

与测试件 FakeLLM 的区别：不按脚本弹出，而是**按输入反应**——
- chat 分流四种 judge：简历解析 / JD 解析 / 岗位匹配 / 总评报告
- chat_stream 跑面试行为机：先 record_answer_eval 评价、再出下一题，
  第 4 轮回答后自动 finish_interview 收尾出报告

演示判定从输入材料里来（关键词比对复用 matching 的兜底逻辑），不编造。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from hr_interview.llm import ToolCall, assistant_msg
from hr_interview.matching import _heuristic_items
from hr_interview.resume import _jd_heuristic

_MAX_QA_ROUNDS = 4  # 演示脚本在 4 轮问答后自动收尾

_NEXT_QUESTIONS: dict[str, str] = {
    "opening": "再挑一个你简历里最有把握的技能，说说具体用到什么程度？",
    "tech": "讲一个你排查过的最难的技术问题，当时是怎么定位的？",
    "project": "在这个项目里，你本人的量化贡献是什么？",
    "reverse": "你有什么想了解的？工作内容、团队、流程都可以问。",
    "closing": "最后还有什么想补充的吗？",
}


def _section(user_text: str, title: str) -> str:
    """抽【标题】段落到下一个【之前的内容（标题与内容同行情兼容）。"""
    m = re.search(rf"【{title}】(.*?)(?=\n【|\Z)", user_text, re.S)
    return m.group(1).strip() if m else ""


def _chunks(text: str, size: int = 8) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i:i + size]


class MockLLM:
    """离线演示件：与 LiveLLM 同形状，可直接注入 Interviewer / 解析器。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._seq = 0

    # ---- chat：四种 judge 分流 ----

    def chat(
        self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8
    ) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if len(messages) > 1 else ""

        if "面试总结官" in system:
            payload = {
                "summary": "（演示）回答结构清楚、有具体项目支撑；技术深度与量化数据还可以再加强。",
                "suggestions": [
                    "每个回答先结论后展开",
                    "准备 2-3 个带数据的案例",
                    "针对目标岗位要求的薄弱项补强",
                ],
            }
        elif "简历解析器" in system:
            skills = sorted(set(re.findall(r"[A-Za-z]{2,}", user)))[:5]
            first_line = user.strip().splitlines()[0][:60] if user.strip() else ""
            payload = {
                "skills": skills or ["（未识别）"],
                "experiences": [first_line] if first_line else [],
                "education": [],
                "highlights": [],
            }
        elif "JD 解析器" in system:
            heur = _jd_heuristic(user)
            payload = {"must": heur["must"], "nice": heur["nice"]}
        elif "岗位匹配评估器" in system:
            try:
                must = json.loads(_section(user, "岗位必须项") or "[]")
                nice = json.loads(_section(user, "岗位加分项") or "[]")
            except json.JSONDecodeError:
                must, nice = [], []
            payload = {"items": _heuristic_items(_section(user, "简历原文"), must, nice)}
        else:
            payload = {"note": "（演示）收到"}
        return assistant_msg(json.dumps(payload, ensure_ascii=False))

    # ---- chat_stream：面试行为机 ----

    def chat_stream(
        self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8
    ) -> Iterator[dict]:
        self.calls.append({"messages": messages, "tools": tools})
        system = messages[0]["content"] if messages else ""
        m = re.search(r"当前阶段：([a-z_]+)", system)
        phase = m.group(1) if m else "opening"
        answers = sum(1 for msg in messages if msg["role"] == "user")

        if messages and messages[-1]["role"] == "tool":
            # 评价已入档 → 出下一题；问答满 4 轮或已到收尾阶段 → 结束出报告
            if answers >= _MAX_QA_ROUNDS or phase == "closing":
                text = "好的，面试到这里。我来生成你的总评报告。"
                for piece in _chunks(text):
                    yield {"type": "delta", "text": piece}
                self._seq += 1
                yield {"type": "final", "message": assistant_msg(text, [
                    ToolCall(id=f"call_{self._seq}", name="finish_interview", arguments={}),
                ])}
                return
            text = _NEXT_QUESTIONS.get(phase, "请再展开讲讲。")
            for piece in _chunks(text):
                yield {"type": "delta", "text": piece}
            yield {"type": "final", "message": assistant_msg(text)}
            return

        # 候选人刚回答 → 评价入档
        answer = messages[-1]["content"] if messages else ""
        question = next(
            (msg["content"] for msg in reversed(messages)
             if msg["role"] == "assistant" and msg.get("content")),
            "",
        )
        evidence = answer.strip()[:12]  # 原话摘句（子串校验必过）
        self._seq += 1
        text = "好，我先记录一下这一轮的回答。"
        for piece in _chunks(text):
            yield {"type": "delta", "text": piece}
        yield {"type": "final", "message": assistant_msg(text, [
            ToolCall(
                id=f"call_{self._seq}",
                name="record_answer_eval",
                arguments={
                    "question": question,
                    "answer": answer,
                    "scores": {"tech_depth": 3, "clarity": 4, "evidence": 4, "fit": 3},
                    "evidence": evidence,
                    "comment": "（演示评分）表述清楚，深度与量化可再加强",
                },
            ),
        ])}
