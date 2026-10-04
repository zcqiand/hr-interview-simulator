"""匹配打分（M01.F01 逐条对照打分, M01.F02 出题侧重）。

- match_jd：候选人画像/简历 × 要求清单逐条 verdict（match|partial|miss），
  计分 must 占 90 分 nice 占 10 分（partial 记半）；LLM judge 格式异常兜底重试一次，
  再失败落关键词启发式（不空转第三次）。
- build_focus：由匹配结果导出面试出题侧重——强项进开场暖场、miss 进技术验证题、
  partial 进项目深挖题（M02 注入系统提示词）。

树锚点：M01.F01.I01 / M01.F02.I01。
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from hr_interview.jsonx import extract_json

_MATCH_PROMPT = (
    "你是岗位匹配评估器。对照候选人与岗位要求逐条判定，只输出 JSON 本体，不要解释。\n"
    '格式：{"items": [{"requirement": "要求原文", "verdict": "match|partial|miss", "note": "中文一句话依据"}]}\n'
    "match=符合 partial=部分符合 miss=不符。note 必须基于候选人材料原文，不许编造。"
)
_RETRY_SUFFIX = "输出格式不对。请只输出一个 JSON 对象，items 数组每项含 requirement/verdict/note。"

_VERDICTS = ("match", "partial", "miss")
_MUST_WEIGHT = 90
_NICE_WEIGHT = 10
_RESUME_CAP = 4000  # 注入提示词的简历原文上限


class LLM(Protocol):
    def chat(self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8) -> dict: ...


def match_jd(llm: LLM, profile: dict, resume_text: str, requirements: dict) -> dict:
    must: list[str] = [str(x) for x in (requirements.get("must") or [])]
    nice: list[str] = [str(x) for x in (requirements.get("nice") or [])]
    if not must and not nice:
        return {"items": [], "total_score": 0, "strengths": [], "gaps": [], "parsed": False}

    user = (
        "【候选人画像】\n" + json.dumps(profile, ensure_ascii=False)
        + "\n【简历原文】\n" + (resume_text or "（无）")[:_RESUME_CAP]
        + "\n【岗位必须项】\n" + json.dumps(must, ensure_ascii=False)
        + "\n【岗位加分项】\n" + json.dumps(nice, ensure_ascii=False)
    )
    messages: list[dict] = [
        {"role": "system", "content": _MATCH_PROMPT},
        {"role": "user", "content": user},
    ]
    items: list[dict] | None = None
    for _ in range(2):
        msg = llm.chat(messages=messages, temperature=0.1)
        data = extract_json(msg.get("content"))
        raw_items = data.get("items") if isinstance(data, dict) else None
        if isinstance(raw_items, list):
            items = _normalize_items(raw_items, must, nice)
            break
        messages = [*messages, msg, {"role": "user", "content": _RETRY_SUFFIX}]

    parsed = items is not None
    if items is None:
        items = _heuristic_items(resume_text, must, nice)

    return {
        "items": items,
        "total_score": _score(items, must_set=set(must)),
        "strengths": [i["requirement"] for i in items if i["verdict"] == "match"],
        "gaps": [
            {"requirement": i["requirement"], "kind": i["kind"], "note": i["note"]}
            for i in items
            if i["verdict"] in ("miss", "partial")
        ],
        "parsed": parsed,
    }


def _normalize_items(raw_items: list, must: list[str], nice: list[str]) -> list[dict]:
    must_set = set(must)
    nice_set = set(nice)
    out: list[dict] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        req = str(row.get("requirement") or "").strip()
        if not req:
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict not in _VERDICTS:
            verdict = "miss"
        kind = "must" if req in must_set else ("nice" if req in nice_set else "must")
        out.append({
            "requirement": req,
            "kind": kind,
            "verdict": verdict,
            "note": str(row.get("note") or "").strip(),
        })
    return out


def _score(items: list[dict], must_set: set[str]) -> int:
    must_rows = [i for i in items if i["kind"] == "must"]
    nice_rows = [i for i in items if i["kind"] == "nice"]
    pts = 0.0
    if must_rows:
        w = _MUST_WEIGHT / len(must_rows)
        pts += sum(w * {"match": 1.0, "partial": 0.5, "miss": 0.0}[r["verdict"]] for r in must_rows)
    if nice_rows:
        w = _NICE_WEIGHT / len(nice_rows)
        pts += sum(w * {"match": 1.0, "partial": 0.5, "miss": 0.0}[r["verdict"]] for r in nice_rows)
    return round(pts)


# ---- 启发式兜底 ----

_TOKEN_SPLIT = re.compile(r"[^A-Za-z]{2,}|[一-鿿]+")


def _req_tokens(req: str) -> list[str]:
    """要求条目的比对 token：ASCII 词（≥2 字符，小写）+ 连续中文段（≥2 字）。"""
    tokens = [t.lower() for t in re.findall(r"[A-Za-z]{2,}", req)]
    tokens += [seg for seg in re.findall(r"[一-鿿]{2,}", req)]
    return tokens


def _heuristic_items(resume_text: str, must: list[str], nice: list[str]) -> list[dict]:
    text = (resume_text or "").lower()
    items: list[dict] = []
    for kind, reqs in (("must", must), ("nice", nice)):
        for req in reqs:
            tokens = _req_tokens(req)
            if not tokens:
                items.append({"requirement": req, "kind": kind, "verdict": "miss", "note": "无法比对"})
                continue
            hits = sum(1 for t in tokens if t in text)
            ratio = hits / len(tokens)
            verdict = "match" if ratio >= 0.6 else ("partial" if hits > 0 else "miss")
            items.append({
                "requirement": req,
                "kind": kind,
                "verdict": verdict,
                "note": f"关键词比对 {hits}/{len(tokens)} 命中（兜底判定）",
            })
    return items


# ---- 出题侧重（M01.F02.I01）----


def build_focus(match_result: dict) -> dict:
    """强项→开场暖场；miss→技术验证题；partial→项目深挖题。均为确定性规则。"""
    opening = list(match_result.get("strengths") or [])[:2]
    tech: list[dict] = []
    project: list[dict] = []
    for i in match_result.get("items") or []:
        if i["verdict"] == "miss" and len(tech) < 3:
            tech.append({"requirement": i["requirement"], "note": i["note"]})
        elif i["verdict"] == "partial" and len(project) < 2:
            project.append({"requirement": i["requirement"], "note": i["note"]})
    return {"opening": opening, "tech": tech, "project": project}
