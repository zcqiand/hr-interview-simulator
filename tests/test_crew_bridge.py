"""CrewAI 桥接件（v2）：LLMClient 适配 + 三 crew 工厂。FakeLLM 驱动，不真调网。"""
from __future__ import annotations

from hr_interview.crew_bridge import (
    LLMClientBridge,
    build_jd_crew,
    build_match_crew,
    build_resume_crew,
    extract_crew_json,
)
from hr_interview.llm import FakeLLM, assistant_msg


def test_bridge_flattens_messages_and_returns_content():
    llm = FakeLLM(turns=[assistant_msg(content="解析结果文本")])
    bridge = LLMClientBridge(llm)
    out = bridge.call([
        {"role": "system", "content": "你是解析器"},
        {"role": "user", "content": "简历原文"},
    ])
    assert out == "解析结果文本"
    sent = llm.calls[0]["messages"]
    assert sent[0]["role"] == "system" and "解析器" in sent[0]["content"]
    assert sent[-1]["content"] == "简历原文"
    assert llm.calls[0]["tools"] is None  # 结构化不走工具


def test_bridge_temperature_is_parsing_grade():
    llm = FakeLLM(turns=[assistant_msg(content="x")])
    LLMClientBridge(llm).call([{"role": "user", "content": "y"}])
    # FakeLLM 不记录 temperature，但桥接件必须以 0.1 调 chat（与 v1 结构化一致）——
    # 通过子类内省钉住默认值
    assert LLMClientBridge(llm).temperature == 0.1


def test_resume_crew_kickoff_single_llm_call():
    llm = FakeLLM(turns=[assistant_msg(content='{"skills": ["Python"]}')])
    out = build_resume_crew(llm, "张三 3 年 Python").kickoff()
    assert "skills" in str(out)
    assert len(llm.calls) == 1  # 一次 kickoff 恰好一次 LLM 调用（Review Focus 2）
    assert "张三" in llm.calls[0]["messages"][-1]["content"]
    assert "简历解析器" in llm.calls[0]["messages"][0]["content"]  # MockLLM 分流标记


def test_jd_crew_kickoff_carries_marker_and_text():
    llm = FakeLLM(turns=[assistant_msg(content='{"must": ["3 年 Python"], "nice": []}')])
    build_jd_crew(llm, "岗位要求：3 年 Python").kickoff()
    assert "JD 解析器" in llm.calls[0]["messages"][0]["content"]
    assert "3 年 Python" in llm.calls[0]["messages"][-1]["content"]


def test_match_crew_kickoff_carries_marker_and_block():
    llm = FakeLLM(turns=[assistant_msg(content='{"items": []}')])
    build_match_crew(llm, "【候选人画像】\n{}\n【岗位必须项】\n[\"Python\"]").kickoff()
    assert "岗位匹配评估器" in llm.calls[0]["messages"][0]["content"]
    assert "岗位必须项" in llm.calls[0]["messages"][-1]["content"]


def test_extract_crew_json_parses_fenced_output():
    llm = FakeLLM(turns=[assistant_msg(content="```json\n{\"skills\": []}\n```")])
    crew = build_resume_crew(llm, "原文")
    assert extract_crew_json(crew.kickoff()) == {"skills": []}
    assert extract_crew_json(object()) is None  # 非 JSON 输出安全返回 None
