"""LLM 接入层（M04.F01.I02/I03）：OpenAI 兼容直调 + 可注入假件。

三个实现同一形状：
- LiveLLM   真实调用（chat / chat_stream），归一化 content / reasoning / tool_calls
- FakeLLM   测试脚本件（pytest 用，顺序弹出脚本轮次）
- MockLLM   运行时离线演示件（LLM_MODE=mock，见 mock_llm.py）

chat_stream 产出事件：{"type":"delta"|"reasoning","text":str} 与 {"type":"final","message":dict}。
assistant 消息统一为 {"role","content","tool_calls":[{id,name,arguments:dict}]|None}。

MiniMax-M3 两个已验证坑（记忆 minimax-m3-integration-pitfalls）：
- 思维链内联在 content 的 <think>…</think> → _ThinkSplitter 流式状态机剥离
- 工具轮 assistant 消息回传须转协议形状（arguments 为 JSON 串）→ _to_wire
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


def assistant_msg(content: str | None = None, tool_calls: list[ToolCall] | None = None) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,  # type: ignore[dict-item]
    }


def tool_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def safe_json_arguments(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"__raw__": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"__raw__": raw}


class LLMClient(Protocol):
    def chat(
        self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8
    ) -> dict: ...

    def chat_stream(
        self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8
    ) -> Iterator[dict]: ...


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class _ThinkSplitter:
    """把 content 里的内联思维链（MiniMax-M3 的 <think>…</think>）归一化为 reasoning 事件。

    标签可能跨 chunk，用缓冲 + 前缀探测处理；正文与其余内容原样放行。
    """

    def __init__(self):
        self._state = "normal"
        self._buf = ""
        self.visible_parts: list[str] = []  # 剥离后的正文（final 消息用）

    def _push(self, out: list[dict], kind: str, text: str) -> None:
        if text:
            out.append({"type": kind, "text": text})
            if kind == "delta":
                self.visible_parts.append(text)

    def _safe_len(self, tag: str) -> int:
        """缓冲中可安全放行的长度（末尾可能是 tag 前缀的部分要留下）。"""
        for k in range(min(len(self._buf), len(tag) - 1), 0, -1):
            if self._buf.endswith(tag[:k]):
                return len(self._buf) - k
        return len(self._buf)

    def feed(self, text: str) -> list[dict]:
        self._buf += text
        out: list[dict] = []
        while True:
            if self._state == "normal":
                i = self._buf.find(_THINK_OPEN)
                if i >= 0:
                    self._push(out, "delta", self._buf[:i])
                    self._buf = self._buf[i + len(_THINK_OPEN):]
                    self._state = "think"
                    continue
                n = self._safe_len(_THINK_OPEN)
                self._push(out, "delta", self._buf[:n])
                self._buf = self._buf[n:]
                break
            i = self._buf.find(_THINK_CLOSE)
            if i >= 0:
                self._push(out, "reasoning", self._buf[:i])
                self._buf = self._buf[i + len(_THINK_CLOSE):]
                self._state = "normal"
                continue
            n = self._safe_len(_THINK_CLOSE)
            self._push(out, "reasoning", self._buf[:n])
            self._buf = self._buf[n:]
            break
        return out

    def flush(self) -> list[dict]:
        if not self._buf:
            return []
        kind = "delta" if self._state == "normal" else "reasoning"
        out = [{"type": kind, "text": self._buf}]
        self._buf = ""
        return out


def _strip_think(content: str) -> str:
    """非流式：剥掉 <think>…</think>，只留正文。"""
    if _THINK_OPEN not in content:
        return content
    before, _, rest = content.partition(_THINK_OPEN)
    _, _, after = rest.partition(_THINK_CLOSE)
    return before + after


def _to_wire(messages: list[dict]) -> list[dict]:
    """把内部消息形状转成 OpenAI 协议形状：ToolCall dataclass → dict，arguments → JSON 串。"""
    out: list[dict] = []
    for m in messages:
        calls = m.get("tool_calls")
        if m.get("role") == "assistant" and calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.arguments, ensure_ascii=False),
                            },
                        }
                        for c in calls
                    ],
                }
            )
        else:
            out.append(m)
    return out


class LiveLLM:
    """OpenAI 兼容 /chat/completions 直调（含 MiniMax-M3 等 reasoning 模型）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: object | None = None,
        timeout: float = 180.0,
    ):
        self._model = model
        if client is not None:  # 测试注入
            self._client = client
        else:
            from openai import OpenAI

            self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    # ---- normalization ----

    def _normalize_message(self, msg: object) -> dict:
        calls: list[ToolCall] | None = None
        raw_calls = getattr(msg, "tool_calls", None)
        if raw_calls:
            calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=safe_json_arguments(tc.function.arguments),
                )
                for tc in raw_calls
            ]
        return assistant_msg(getattr(msg, "content", None), calls)

    def _request_kwargs(self, messages, tools, temperature):
        kwargs: dict = {
            "model": self._model,
            "messages": _to_wire(messages),
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    # ---- LLMClient ----

    def chat(self, *, messages, tools=None, temperature: float = 0.8) -> dict:
        resp = self._client.chat.completions.create(**self._request_kwargs(messages, tools, temperature))
        msg = self._normalize_message(resp.choices[0].message)
        if msg["content"]:
            msg["content"] = _strip_think(msg["content"])
        return msg

    def chat_stream(self, *, messages, tools=None, temperature: float = 0.8) -> Iterator[dict]:
        stream = self._client.chat.completions.create(
            stream=True, **self._request_kwargs(messages, tools, temperature)
        )
        splitter = _ThinkSplitter()
        call_acc: dict[int, dict] = {}
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                yield {"type": "reasoning", "text": reasoning}
            if delta.content:
                for ev in splitter.feed(delta.content):
                    yield ev
            for part in getattr(delta, "tool_calls", None) or []:
                acc = call_acc.setdefault(part.index, {"id": "", "name": "", "args": ""})
                if part.id:
                    acc["id"] = part.id
                if part.function and part.function.name:
                    acc["name"] = part.function.name
                if part.function and part.function.arguments:
                    acc["args"] += part.function.arguments
        for ev in splitter.flush():
            yield ev
        calls = None
        if call_acc:
            calls = [
                ToolCall(id=acc["id"] or f"call_{i}", name=acc["name"], arguments=safe_json_arguments(acc["args"]))
                for i, acc in sorted(call_acc.items())
            ]
        text = "".join(splitter.visible_parts)
        yield {"type": "final", "message": assistant_msg(text, calls)}


@dataclass
class FakeLLM:
    """测试件：按顺序弹出脚本轮次。

    每轮可以是 assistant_msg(...) 结果（含/不含 tool_calls），
    chat_stream 会把 content 按词切分产出 delta 事件后再给 final。
    """

    turns: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # 记录收到的请求供断言

    def chat(self, *, messages, tools=None, temperature: float = 0.8) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.turns:
            raise AssertionError("FakeLLM 脚本耗尽：测试给的轮次少于实际请求次数")
        return self.turns.pop(0)

    def chat_stream(self, *, messages, tools=None, temperature: float = 0.8) -> Iterator[dict]:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.turns:
            raise AssertionError("FakeLLM 脚本耗尽：测试给的轮次少于实际请求次数")
        msg = self.turns.pop(0)
        if msg.get("content"):
            for word in msg["content"].split(" "):
                yield {"type": "delta", "text": word + " "}
        yield {"type": "final", "message": msg}
