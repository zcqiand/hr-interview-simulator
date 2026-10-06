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

from hr_interview.crew_bridge import build_jd_crew, build_resume_crew, extract_crew_json

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


# ---- LLM 结构化（v2·CrewAI 解析员）----

_RESUME_FIELDS = ("skills", "experiences", "education", "highlights")


def parse_resume(llm: LLM, resume_text: str) -> dict:
    empty = {k: [] for k in _RESUME_FIELDS}
    for _ in range(2):
        data = extract_crew_json(build_resume_crew(llm, resume_text).kickoff())
        if data is not None and all(isinstance(data.get(k), list) for k in _RESUME_FIELDS):
            return {**{k: [str(x) for x in data[k]] for k in _RESUME_FIELDS}, "parsed": True}
    return {**empty, "parsed": False}


def parse_jd(llm: LLM, jd_text: str) -> dict:
    for _ in range(2):
        data = extract_crew_json(build_jd_crew(llm, jd_text).kickoff())
        if data is not None and isinstance(data.get("must"), list) and isinstance(data.get("nice"), list):
            return {
                "must": [str(x) for x in data["must"]],
                "nice": [str(x) for x in data["nice"]],
                "parsed": True,
            }
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
