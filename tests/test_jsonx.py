"""jsonx.extract_json：从 LLM 回复里稳健抠出第一个 JSON 对象。"""
from __future__ import annotations

from hr_interview.jsonx import extract_json


def test_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_code_block():
    text = "解析结果如下：\n```json\n{\"skills\": [\"Python\"]}\n```\n请查收。"
    assert extract_json(text) == {"skills": ["Python"]}


def test_prose_around_object():
    text = '好的，这是结构化结果： {"must": ["SQL"], "nice": []} 以上。'
    assert extract_json(text) == {"must": ["SQL"], "nice": []}


def test_nested_and_cjk():
    text = '{"a": {"b": "中文，带逗号"}, "c": [1, 2]}'
    assert extract_json(text) == {"a": {"b": "中文，带逗号"}, "c": [1, 2]}


def test_no_json_returns_none():
    assert extract_json("这里没有结构化内容") is None


def test_broken_json_returns_none():
    assert extract_json('{"a": ') is None
