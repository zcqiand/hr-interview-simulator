"""评价引擎（M03.F01 维度评价, M03.F02 总评报告）——本仓突出点的记忆侧。

- record_eval：每轮回答按 4 维度评分（1-5）+ 证据入档。
  铁纪律：**证据必须摘回答原话**——evidence 做归一化子串校验，不是原话就丢弃并标记；
  分数越界夹取；缺失维度给中性 3 分；格式异常兜底重试一次，再失败落全 3 分启发式。
- aggregate / verdict_of：逐轮评分聚合为维度均分与强/中/弱判定（>=4 强 / >=3 中 / 其余弱）。
- build_report：LLM 汇总总评与改进建议（重试一次），失败落规则兜底（按最弱维度出建议）。

树锚点：M03.F01.I01 / M03.F01.I02 / M03.F02.I01。
"""
from __future__ import annotations

import json
from typing import Protocol

from hr_interview.jsonx import extract_json

DIMENSIONS: list[tuple[str, str]] = [
    ("tech_depth", "技术深度"),
    ("clarity", "表达清晰"),
    ("evidence", "证据可信"),
    ("fit", "岗位匹配"),
]

_EVAL_PROMPT = (
    "你是面试评估器。基于面试官提问与候选人回答，按 4 个维度打 1-5 整数分：\n"
    "tech_depth 技术深度 / clarity 表达清晰 / evidence 证据可信 / fit 岗位匹配。\n"
    '只输出 JSON 本体：{"scores": {"tech_depth": n, "clarity": n, "evidence": n, "fit": n},'
    ' "evidence": "回答中最能支撑评分的原话摘句（必须逐字来自回答）",'
    ' "comment": "中文一句话点评"}\n'
    "评分只依据回答本身与岗位要求；回答里没有的内容不许脑补。"
)
_REPORT_PROMPT = (
    "你是面试总结官。根据全程逐轮评价写总评，只输出 JSON 本体：\n"
    '{"summary": "200 字内中文总评，至少引用一处原话证据",'
    ' "suggestions": ["可执行的改进建议，2-4 条"]}'
)
_RETRY_SUFFIX = "输出格式不对。请只输出一个 JSON 对象，不要围栏与解释。"


class LLM(Protocol):
    def chat(self, *, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.8) -> dict: ...


class StoreLike(Protocol):
    def insert_answer_eval(
        self, interview_id: str, round_no: int, phase: str,
        question: str, answer: str, scores_json: str, evidence: str, comment: str,
    ) -> str: ...


def _norm(text: str) -> str:
    """空白归一 + 小写——证据子串校验用。"""
    return " ".join((text or "").split()).lower()


def _clamp(value, default: int = 3) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(1, min(5, n))


def eval_from_payload(
    store: StoreLike, interview_id: str,
    round_no: int, phase: str, question: str, answer: str,
    scores_raw: dict, evidence_raw: str = "", comment_raw: str = "",
) -> dict:
    """校验 + 落库一次评价（工具路径与 judge 路径共用）。

    铁纪律在此执行：分数夹取 1-5、缺失维度中性 3 分、证据必须摘回答原话否则丢弃。
    """
    scores = {key: _clamp(scores_raw.get(key)) for key, _ in DIMENSIONS}
    evidence = str(evidence_raw or "").strip()
    comment = str(comment_raw or "").strip()
    verified = False
    if evidence:
        verified = _norm(evidence) in _norm(answer)
        if not verified:
            evidence = ""
            comment = (comment + "；" if comment else "") + "（证据与原话不符已剔除）"
    store.insert_answer_eval(
        interview_id, round_no, phase, question, answer,
        json.dumps(scores, ensure_ascii=False), evidence, comment,
    )
    return {
        "interview_id": interview_id,
        "round": round_no,
        "phase": phase,
        "question": question,
        "answer": answer,
        "scores": scores,
        "evidence": evidence,
        "evidence_verified": verified,
        "comment": comment,
    }


def record_eval(
    llm: LLM, store: StoreLike, interview_id: str,
    round_no: int, phase: str, question: str, answer: str,
    jd_hint: str = "",
) -> dict:
    """LLM judge 路径：独立按回答评分（面试循环外的补充手段）。"""
    scores: dict = {}
    evidence = ""
    comment = ""

    user = (
        (f"【岗位要求】\n{jd_hint}\n" if jd_hint else "")
        + f"【面试官提问】\n{question}\n【候选人回答】\n{answer}"
    )
    messages: list[dict] = [
        {"role": "system", "content": _EVAL_PROMPT},
        {"role": "user", "content": user},
    ]
    for _ in range(2):
        msg = llm.chat(messages=messages, temperature=0.1)
        data = extract_json(msg.get("content"))
        raw_scores = data.get("scores") if isinstance(data, dict) else None
        if isinstance(raw_scores, dict):
            scores = {key: _clamp(raw_scores.get(key)) for key, _ in DIMENSIONS}
            evidence = str(data.get("evidence") or "").strip()
            comment = str(data.get("comment") or "").strip()
            break
        messages = [*messages, msg, {"role": "user", "content": _RETRY_SUFFIX}]

    if not scores:  # 两次都失败 → 启发式兜底
        scores = {key: 3 for key, _ in DIMENSIONS}
        comment = "（兜底）未能自动评分，给出中性分"

    return eval_from_payload(
        store, interview_id, round_no, phase, question, answer, scores, evidence, comment
    )


# ---- 聚合 ----


def verdict_of(avg: float) -> str:
    if avg >= 4:
        return "强"
    if avg >= 3:
        return "中"
    return "弱"


def aggregate(evals: list[dict]) -> dict:
    """evals 为 answer_evals 行（含 scores_json）。"""
    sums = {key: 0.0 for key, _ in DIMENSIONS}
    counts = {key: 0 for key, _ in DIMENSIONS}
    for e in evals:
        try:
            scores = json.loads(e.get("scores_json") or "{}")
        except json.JSONDecodeError:
            continue
        for key, _ in DIMENSIONS:
            v = scores.get(key)
            if isinstance(v, (int, float)):
                sums[key] += v
                counts[key] += 1
    dims = []
    for key, label in DIMENSIONS:
        avg = round(sums[key] / counts[key], 2) if counts[key] else 0.0
        dims.append({"key": key, "label": label, "avg": avg, "verdict": verdict_of(avg) if counts[key] else "-"})
    return {"dimensions": dims, "rounds": len(evals)}


# ---- 总评报告 ----


def build_report(llm: LLM, candidate_name: str, evals: list[dict]) -> dict:
    agg = aggregate(evals)
    detail = []
    for e in evals:
        detail.append({
            "question": (e.get("question") or "")[:120],
            "answer": (e.get("answer") or "")[:300],
            "scores": json.loads(e.get("scores_json") or "{}"),
            "evidence": e.get("evidence") or "",
            "comment": e.get("comment") or "",
        })
    user = (
        f"【候选人】{candidate_name}\n"
        f"【维度聚合】{json.dumps(agg['dimensions'], ensure_ascii=False)}\n"
        f"【逐轮明细】{json.dumps(detail, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": _REPORT_PROMPT},
        {"role": "user", "content": user},
    ]
    for _ in range(2):
        msg = llm.chat(messages=messages, temperature=0.3)
        data = extract_json(msg.get("content"))
        if data is not None and isinstance(data.get("summary"), str) and isinstance(data.get("suggestions"), list):
            return {
                "candidate_name": candidate_name,
                "rounds": agg["rounds"],
                "dimensions": agg["dimensions"],
                "summary": str(data["summary"]).strip(),
                "suggestions": [str(x) for x in data["suggestions"]][:4],
                "detail": detail,
            }
        messages = [*messages, msg, {"role": "user", "content": _RETRY_SUFFIX}]

    return {
        "candidate_name": candidate_name,
        "rounds": agg["rounds"],
        "dimensions": agg["dimensions"],
        "summary": _rule_summary(candidate_name, agg),
        "suggestions": _rule_suggestions(agg),
        "detail": detail,
    }


def _rule_summary(candidate_name: str, agg: dict) -> str:
    if agg["rounds"] == 0:
        return f"{candidate_name} 本场没有可评价的回答。"
    dims = agg["dimensions"]
    best = max(dims, key=lambda d: d["avg"])
    worst = min(dims, key=lambda d: d["avg"])
    return (
        f"{candidate_name} 共 {agg['rounds']} 轮作答。"
        f"最强维度是{best['label']}（均分 {best['avg']}，判定：{best['verdict']}），"
        f"最弱维度是{worst['label']}（均分 {worst['avg']}，判定：{worst['verdict']}）。"
        "（规则汇总，建议以逐轮明细为准）"
    )


def _rule_suggestions(agg: dict) -> list[str]:
    weak = [d for d in agg["dimensions"] if d["verdict"] == "弱"]
    mid = [d for d in agg["dimensions"] if d["verdict"] == "中"]
    out = [f"重点补强{d['label']}：围绕该维度准备 2-3 个可量化案例" for d in weak]
    out += [f"提升{d['label']}：回答时先结论后展开，主动给数据" for d in mid]
    if not out:
        out.append("整体表现均衡，下一步按目标公司真题做限时模拟")
    return out[:4]
