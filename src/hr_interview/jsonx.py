"""从 LLM 回复文本里稳健抠出第一个 JSON 对象。

覆盖三种常见形态：纯 JSON、```json 围栏、前后带解释文字。
统一策略：定位首个 `{`，raw_decode 到配对括号（容忍尾部散文）。
"""
from __future__ import annotations

import json


def extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    idx = text.find("{")
    if idx < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[idx:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
