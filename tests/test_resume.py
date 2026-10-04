"""简历/JD 解析（M00.F01.I01/I02, M00.F02.I01）。

PDF 用 pypdf 现场生成内存件（正常/加密/无文本层）；结构化用 FakeLLM 脚本，不真调网。
"""
from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from hr_interview.llm import FakeLLM, assistant_msg
from hr_interview.resume import extract_pdf_text, parse_jd, parse_resume

# ---- 内存 PDF 造件 ----


def _font_ref(writer):
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    return writer._add_object(font)


def _pdf_with_text(text: str) -> bytes:
    """手写 content stream 的最小含文本 PDF（text 须为 ASCII）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    page = writer.pages[0]

    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
    content_ref = writer._add_object(content)

    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = _font_ref(writer)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = fonts

    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = content_ref

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _empty_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---- extract_pdf_text（M00.F01.I01）----


def test_extract_text_from_pdf():
    data = _pdf_with_text("Zhang San Python Developer 3 years")
    text = extract_pdf_text(data)
    assert "Zhang San" in text
    assert "3 years" in text


def test_encrypted_pdf_clear_error():
    with pytest.raises(ValueError, match="加密"):
        extract_pdf_text(_encrypted_pdf())


def test_no_text_layer_clear_error():
    with pytest.raises(ValueError, match="文本层"):
        extract_pdf_text(_empty_pdf())


# ---- parse_resume（M00.F01.I02）：LLM 结构化 + 格式异常兜底重试一次 ----

_GOOD_PROFILE = (
    "```json\n"
    '{"skills": ["Python", "FastAPI"], "experiences": ["3 年后端"],'
    ' "education": ["某某大学 本科"], "highlights": ["带过 3 人小组"]}\n'
    "```"
)


def test_parse_resume_good_json():
    llm = FakeLLM(turns=[assistant_msg(content=_GOOD_PROFILE)])
    prof = parse_resume(llm, "张三 3 年 Python 经验……")
    assert prof["skills"] == ["Python", "FastAPI"]
    assert prof["parsed"] is True
    assert llm.calls[0]["tools"] is None  # 结构化不走工具
    assert "张三" in llm.calls[0]["messages"][-1]["content"]


def test_parse_resume_retry_once_on_bad_json():
    llm = FakeLLM(turns=[assistant_msg(content="我觉得这份简历很不错！"), assistant_msg(content=_GOOD_PROFILE)])
    prof = parse_resume(llm, "……")
    assert prof["parsed"] is True
    assert len(llm.calls) == 2  # 第一次格式异常 → 兜底重试一次


def test_parse_resume_fallback_after_second_failure():
    llm = FakeLLM(turns=[assistant_msg(content="还是说不出 JSON"), assistant_msg(content="依然不行")])
    prof = parse_resume(llm, "张三……")
    assert prof["parsed"] is False  # 兜底：空画像 + 原文直用
    assert prof["skills"] == []
    assert len(llm.calls) == 2  # 不重试第三次


# ---- parse_jd（M00.F02.I01）----

_GOOD_JD = '{"must": ["3 年以上 Python"], "nice": ["有 AI 项目经验优先"]}'


def test_parse_jd_good():
    llm = FakeLLM(turns=[assistant_msg(content=_GOOD_JD)])
    req = parse_jd(llm, "岗位要求：1. 3 年以上 Python 2. 有 AI 项目经验优先")
    assert req["must"] == ["3 年以上 Python"]
    assert req["nice"] == ["有 AI 项目经验优先"]
    assert req["parsed"] is True


def test_parse_jd_fallback_bullet_lines():
    llm = FakeLLM(turns=[assistant_msg(content="好的！"), assistant_msg(content="再说一次")])
    req = parse_jd(llm, "1. 3 年以上 Python\n2. 熟悉 SQL\n3. 有 AI 项目经验优先")
    # 兜底：按行拆清单，含「优先/加分」进 nice，其余进 must
    assert req["parsed"] is False
    assert "3 年以上 Python" in req["must"]
    assert "熟悉 SQL" in req["must"]
    assert any("AI 项目" in n for n in req["nice"])
