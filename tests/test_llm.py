"""LiveLLM 归一化（M04.F01.I02）：<think> 剥离、reasoning 字段、流式 tool_calls 累积。

用注入的假 client 模拟 OpenAI SDK chunk 形状，不真调网络。
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from hr_interview.llm import LiveLLM, safe_json_arguments


def _delta(content=None, reasoning=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, reasoning_content=reasoning, tool_calls=tool_calls))])


class _FakeStreamClient:
    """create() 依次返回脚本 payload（stream chunks 或单条 resp）；记录全部请求。"""

    def __init__(self, chunks=None, resp=None):
        self._queue: list = []
        if chunks is not None:
            self._queue.append(iter(chunks))
        if resp is not None:
            self._queue.append(resp)
        self.requests: list[dict] = []
        self.chat = NS(completions=self)

    @property
    def kwargs(self):
        return self.requests[-1]

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._queue.pop(0)


def _llm(chunks) -> tuple[LiveLLM, _FakeStreamClient]:
    client = _FakeStreamClient(chunks)
    return LiveLLM(base_url="https://x/v1", api_key="k", model="m", client=client), client


def _llm_multi(*payloads) -> tuple[LiveLLM, _FakeStreamClient]:
    client = _FakeStreamClient()
    client._queue = list(payloads)
    return LiveLLM(base_url="https://x/v1", api_key="k", model="m", client=client), client


# ---- <think> 内联思维链剥离（MiniMax-M3 实测行为）----

def test_stream_think_split_across_chunks():
    llm, _ = _llm([_delta(content="Hello! <th"), _delta(content="ink>secret thou"), _delta(content="ght</think> World!")])
    events = list(llm.chat_stream(messages=[{"role": "user", "content": "hi"}]))
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
    assert deltas == "Hello!  World!"
    assert reasoning == "secret thought"


def test_stream_without_think_untouched():
    llm, _ = _llm([_delta(content="Hel"), _delta(content="lo!")])
    deltas = "".join(e["text"] for e in llm.chat_stream(messages=[]) if e["type"] == "delta")
    assert deltas == "Hello!"


def test_stream_reasoning_content_field_still_forwarded():
    llm, _ = _llm([_delta(reasoning="thinking…"), _delta(content="Hi")])
    events = list(llm.chat_stream(messages=[]))
    assert any(e["type"] == "reasoning" and e["text"] == "thinking…" for e in events)
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Hi"


def test_stream_tool_calls_accumulate_across_chunks():
    tc1 = NS(index=0, id="call_9", function=NS(name="record_answer_eval", arguments='{"score":'))
    tc2 = NS(index=0, id="", function=NS(name="", arguments=' 4}'))
    llm, _ = _llm([_delta(tool_calls=[tc1]), _delta(tool_calls=[tc2])])
    final = next(e for e in llm.chat_stream(messages=[]) if e["type"] == "final")
    calls = final["message"]["tool_calls"]
    assert calls[0].name == "record_answer_eval"
    assert calls[0].arguments == {"score": 4}
    assert calls[0].id == "call_9"


def test_chat_strips_think_from_content():
    resp = NS(choices=[NS(message=NS(content="<think>hm</think>Answer!", tool_calls=None))])
    client = _FakeStreamClient(resp=resp)
    llm = LiveLLM(base_url="https://x/v1", api_key="k", model="m", client=client)
    msg = llm.chat(messages=[])
    assert msg["content"] == "Answer!"


def test_chat_passes_tools_only_when_given():
    ok = NS(choices=[NS(message=NS(content="ok", tool_calls=None))])
    client = _FakeStreamClient(resp=ok)
    client._queue = [ok, ok]  # chat 两次
    llm = LiveLLM(base_url="https://x/v1", api_key="k", model="m", client=client)
    llm.chat(messages=[])
    assert "tools" not in client.kwargs
    llm.chat(messages=[], tools=[{"type": "function"}])
    assert client.kwargs["tools"] == [{"type": "function"}]


def test_chat_serializes_toolcall_history_to_wire_shape():
    """工具轮的 assistant 消息回传 API 时必须转协议形状（arguments 为 JSON 串）。"""
    tc = NS(id="call_1", function=NS(name="record_answer_eval", arguments='{"score": 4}'))
    stream1 = [_delta(tool_calls=[NS(index=0, id="call_1", function=NS(name="record_answer_eval", arguments='{"score": 4}'))])]
    resp1 = NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))])
    resp2 = NS(choices=[NS(message=NS(content="Great!", tool_calls=None))])
    llm, client = _llm_multi(iter(stream1), resp1, resp2)

    list(llm.chat_stream(messages=[{"role": "user", "content": "I built a queue system."}]))
    history = [
        {"role": "user", "content": "I built a queue system."},
        llm.chat(messages=[]),  # ← 模拟 interviewer 把 final 塞回 history（ToolCall dataclass 形状）
    ]
    llm.chat(messages=history)
    sent = client.kwargs["messages"][-1]
    assert sent["role"] == "assistant"
    wire_call = sent["tool_calls"][0]
    assert wire_call["type"] == "function"
    assert wire_call["function"]["name"] == "record_answer_eval"
    assert isinstance(wire_call["function"]["arguments"], str)
    assert json.loads(wire_call["function"]["arguments"]) == {"score": 4}


# ---- safe_json_arguments ----

def test_safe_json_arguments():
    assert safe_json_arguments('{"a": 1}') == {"a": 1}
    assert safe_json_arguments("") == {}
    assert safe_json_arguments(None) == {}
    assert safe_json_arguments("not json") == {"__raw__": "not json"}
    assert safe_json_arguments("[1,2]") == {"__raw__": [1, 2]}
