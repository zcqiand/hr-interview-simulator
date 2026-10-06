"""CrewAI 桥接件（v2·团队视角，书 ch10.2）：LLMClient 适配 + 三 crew 工厂。

解析岗（简历解析器/JD 解析器）与打分岗（岗位匹配评估器）在此以
Agent/Task/Crew 三要素定义；与 LangGraph 图（agent/graph.py，流程视角）
互不 import（书 IS-05 不混指）。LLM 经 LLMClientBridge 注入——
FakeLLM/MockLLM 原样可用，pytest 无 Key 无网门禁不变。

Agent role 文案承载 MockLLM 的演示分流标记（mock_llm.chat 按 system
里的「简历解析器/JD 解析器/岗位匹配评估器」分流），不许改写。
"""
from __future__ import annotations

import os

# 必须在 crewai import 前置位：测试环境无网，遥测不许外呼
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

from crewai import Agent, Crew, Task  # noqa: E402
from crewai.llms.base_llm import BaseLLM  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from hr_interview.jsonx import extract_json  # noqa: E402
from hr_interview.llm import LLMClient  # noqa: E402

__all__ = [
    "LLMClientBridge",
    "build_resume_crew",
    "build_jd_crew",
    "build_match_crew",
    "extract_crew_json",
]


class LLMClientBridge(BaseLLM):
    """把本仓 LLMClient（LiveLLM/FakeLLM/MockLLM）适配为 CrewAI 可注入 LLM。

    CrewAI 结构化解析只用非流式文本补全；temperature 固定 0.1（与 v1 一致）。
    """

    def __init__(self, client: LLMClient, temperature: float = 0.1):
        super().__init__(model="hr-interview-bridge", temperature=temperature)
        self._client = client

    def call(self, messages: list, tools=None, callbacks=None, **kwargs) -> str:
        msg = self._client.chat(messages=_flatten(messages), temperature=self.temperature or 0.1)
        return msg.get("content") or ""

    # 部分 crewai 版本抽象口是 _call——别名兼容，两种契约都能实例化
    _call = call


def _flatten(messages: list) -> list[dict]:
    """CrewAI 消息（dict 或 Message 对象）→ 本仓 wire 形状，保序。"""
    out: list[dict] = []
    for m in messages:
        if isinstance(m, dict):
            out.append({"role": m.get("role") or "user", "content": m.get("content") or ""})
        else:
            out.append({
                "role": getattr(m, "role", None) or "user",
                "content": getattr(m, "content", None) or "",
            })
    return out


def _agent(role: str, goal: str, backstory: str, llm: LLMClient) -> Agent:
    return Agent(role=role, goal=goal, backstory=backstory, llm=LLMClientBridge(llm))


def build_resume_crew(llm: LLMClient, resume_text: str) -> Crew:
    agent = _agent(
        role="简历解析器",
        goal="把简历原文无损结构化为四字段画像，绝不编造原文没有的内容",
        backstory="资深 HR 助理，只依据原文摘录，输出格式纪律严格。",
        llm=llm,
    )
    task = Task(
        description=(
            "把下面的简历原文结构化为 JSON：四个字段 skills/experiences/education/highlights，"
            "一律字符串数组，原文没有的给空数组。只输出 JSON 本身，不要任何解释。\n"
            "【简历原文】\n" + resume_text
        ),
        expected_output='{"skills": ["…"], "experiences": ["…"], "education": ["…"], "highlights": ["…"]}',
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], memory=False, planning=False)


def build_jd_crew(llm: LLMClient, jd_text: str) -> Crew:
    agent = _agent(
        role="JD 解析器",
        goal="把招聘 JD 提炼为必须项/加分项两清单，不增删要求",
        backstory="资深 HR 助理，只依据原文提炼，输出格式纪律严格。",
        llm=llm,
    )
    task = Task(
        description=(
            "把下面的 JD 提炼为 JSON：字段 must（必须项）/nice（加分项），"
            "一律字符串数组。只输出 JSON 本身，不要任何解释。\n"
            "【JD 原文】\n" + jd_text
        ),
        expected_output='{"must": ["必须项…"], "nice": ["加分项…"]}',
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], memory=False, planning=False)


def build_match_crew(llm: LLMClient, user_block: str) -> Crew:
    agent = _agent(
        role="岗位匹配评估器",
        goal="对照候选人与岗位要求逐条判定 verdict，note 必须基于候选人材料原文",
        backstory="资深技术面试评估官，克制、证据导向，绝不编造。",
        llm=llm,
    )
    task = Task(
        description=(
            "对照候选人与岗位要求逐条判定，输出 JSON："
            '{"items": [{"requirement": "要求原文", "verdict": "match|partial|miss", '
            '"note": "中文一句话依据"}]}。match=符合 partial=部分符合 miss=不符，'
            "note 必须基于候选人材料原文。只输出 JSON 本身，不要任何解释。\n" + user_block
        ),
        expected_output='{"items": [{"requirement": "…", "verdict": "match|partial|miss", "note": "…"}]}',
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], memory=False, planning=False)


def extract_crew_json(kickoff_result) -> dict | None:
    """kickoff 结果 → dict；非 JSON 输出安全返回 None（重试/兜底由调用方循环控制）。"""
    return extract_json(str(kickoff_result))
