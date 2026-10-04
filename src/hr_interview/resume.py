"""简历/JD 解析（M00.F01 简历解析, M00.F02 岗位 JD）。

- extract_pdf_text：pypdf 文本提取；加密/无文本层 → 带原因的 ValueError（不硬猜）。
- parse_resume / parse_jd：LLM 结构化为 JSON；格式异常兜底重试一次；
  再失败落启发式（简历→空画像+原文直用；JD→按行拆清单）。

树锚点：M00.F01.I01 / M00.F01.I02 / M00.F02.I01。
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Protocol

from hr_interview.jsonx import extract_json

_MIN_TEXT_LEN = 20  # 少于此认定无有效文本层


class LLM(Protocol):
    def chat(self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8) -> dict: ...


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，请提供未加密的简历文件。")
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    text = "\n".join(parts).strip()
    if len(text) < _MIN_TEXT_LEN:
        raise ValueError("PDF 没有文本层（可能是扫描件），无法自动解析。请改用文本版简历。")
    return text


# ---- LLM 结构化 ----

_RESUME_PROMPT = (
    "你是简历解析器。把简历原文结构化为 JSON，只输出 JSON 本身，不要任何解释。\n"
    '格式：{"skills": ["…"], "experiences": ["…"], "education": ["…"], "highlights": ["…"]}\n'
    "四个字段一律字符串数组，原文没有的给空数组。"
)
_RESUME_FIELDS = ("skills", "experiences", "education", "highlights")

_JD_PROMPT = (
    "你是 JD 解析器。把招聘 JD 提炼为要求清单，只输出 JSON 本身，不要任何解释。\n"
    '格式：{"must": ["必须项…"], "nice": ["加分项…"]}\n'
    "两个字段一律字符串数组。"
)

_RETRY_SUFFIX = (
    "输出格式不对。请只输出一个 JSON 对象，不要代码围栏、不要解释、不要其他文字。"
)


def parse_resume(llm: LLM, resume_text: str) -> dict:
    empty = {k: [] for k in _RESUME_FIELDS}
    messages: list[dict] = [
        {"role": "system", "content": _RESUME_PROMPT},
        {"role": "user", "content": resume_text},
    ]
    for _ in range(2):
        msg = llm.chat(messages=messages, temperature=0.1)
        data = extract_json(msg.get("content"))
        if data is not None and all(isinstance(data.get(k), list) for k in _RESUME_FIELDS):
            return {**{k: [str(x) for x in data[k]] for k in _RESUME_FIELDS}, "parsed": True}
        messages = [*messages, msg, {"role": "user", "content": _RETRY_SUFFIX}]
    return {**empty, "parsed": False}


def parse_jd(llm: LLM, jd_text: str) -> dict:
    messages: list[dict] = [
        {"role": "system", "content": _JD_PROMPT},
        {"role": "user", "content": jd_text},
    ]
    for _ in range(2):
        msg = llm.chat(messages=messages, temperature=0.1)
        data = extract_json(msg.get("content"))
        if data is not None and isinstance(data.get("must"), list) and isinstance(data.get("nice"), list):
            return {
                "must": [str(x) for x in data["must"]],
                "nice": [str(x) for x in data["nice"]],
                "parsed": True,
            }
        messages = [*messages, msg, {"role": "user", "content": _RETRY_SUFFIX}]
    return {**_jd_heuristic(jd_text), "parsed": False}


def _jd_heuristic(jd_text: str) -> dict:
    """兜底：按行拆要求清单；含「优先/加分」进 nice，其余进 must。"""
    must: list[str] = []
    nice: list[str] = []
    for raw in jd_text.splitlines():
        line = raw.strip().strip("·-—•").strip()
        line = re.sub(r"^\d+[.、)）]\s*", "", line)
        if not line:
            continue
        (nice if ("优先" in line or "加分" in line) else must).append(line)
    return {"must": must, "nice": nice}
