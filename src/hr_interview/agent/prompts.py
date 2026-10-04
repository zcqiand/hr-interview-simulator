"""面试官提示词与阶段机（M02.F01）：人设、阶段规则、追问策略、上下文组装。

追问由评价驱动（本仓突出点）：近轮评价注入系统提示词——
低分维度追问要证据、高分维度往深挖、跑题拉回，策略只在这里写一遍。
"""
from __future__ import annotations

import json

# 阶段状态机：单向，不可回退
PHASES: tuple[str, ...] = ("opening", "tech", "project", "reverse", "closing")
PHASE_LABELS: dict[str, str] = {
    "opening": "开场",
    "tech": "技术考察",
    "project": "项目深挖",
    "reverse": "候选人反问",
    "closing": "收尾",
}
PHASE_RULES: dict[str, str] = {
    "opening": "简要欢迎后让候选人自我介绍，可顺势点名【出题侧重】里的强项暖场。",
    "tech": "围绕技术侧重（miss 项）出验证题，确认真实掌握度；含糊就追问实现细节。",
    "project": "让候选人讲项目故事，针对存疑点（partial 项）深挖：决策权衡、量化结果、本人角色。",
    "reverse": "邀请候选人提问；如实简答，不透露未公开信息，不评价通过概率。",
    "closing": "感谢候选人，给一句总体印象（不给最终结论，结论在报告里），然后调用 finish_interview。",
}
PHASE_QUESTION_LIMIT = 3  # 每阶段提问上限，触顶强制推进

_FOLLOWUP_RULES = (
    "追问策略（由上一轮评价驱动，出下一题前必须先看【近轮评价】）：\n"
    "- 某维度评分 ≤2：追问要证据——具体做法、数据、本人角色，不接受空泛表述。\n"
    "- 某维度评分 ≥4：往深挖一层——为什么不用别的方案、边界与代价在哪。\n"
    "- 回答跑题：先用一句话拉回当前问题，再继续。\n"
    "- 不许重复已问过的问题；一次只问一个；不对候选人说你「通过/不通过」。"
)

_BASE = (
    "你是一位资深技术面试官，专业、友善、直击要点，全程用中文。\n"
    "纪律：\n"
    "- 不得引用【候选人画像/简历】里没有的经历，不许脑补。\n"
    "- 每轮候选人回答后，必须先调用 record_answer_eval 评价入档，再出下一题。\n"
    "- 阶段只能用 advance_stage 单向推进；要结束时调用 finish_interview（它会生成总评报告）。\n"
    "- 评分与证据要克制：证据必须摘回答原话，不确定就给中性分。"
)


def next_phase(phase: str) -> str | None:
    """阶段机单向推进；closing 或未知阶段返回 None。"""
    try:
        idx = PHASES.index(phase)
    except ValueError:
        return None
    return PHASES[idx + 1] if idx + 1 < len(PHASES) else None


def build_system_prompt(
    interview: dict,
    candidate: dict,
    jd: dict | None,
    focus: dict,
    recent_evals: list[dict],
) -> str:
    """组装面试官系统提示词：人设纪律 + 当前阶段 + 追问策略 + 候选人材料 + 近轮评价。"""
    phase = interview["phase"]
    parts: list[str] = [
        _BASE,
        "阶段机（单向不可回退）：" + "→".join(PHASES)
        + f"\n当前阶段：{phase}（{PHASE_LABELS.get(phase, phase)}）。{PHASE_RULES.get(phase, '')}"
        + f"\n每阶段最多 {PHASE_QUESTION_LIMIT} 个问题，问满必须 advance_stage。",
        _FOLLOWUP_RULES,
        "【候选人画像】\n" + (candidate.get("profile_json") or "{}"),
        "【简历节选】\n" + (candidate.get("resume_text") or "（无）")[:2000],
    ]
    if jd:
        try:
            reqs = json.loads(jd.get("requirements_json") or "{}")
        except json.JSONDecodeError:
            reqs = {}
        head = f"【目标岗位】{jd['title']}" + (f"（{jd['company']}）" if jd.get("company") else "")
        parts.append(
            head
            + "\n必须项：" + json.dumps(reqs.get("must") or [], ensure_ascii=False)
            + "\n加分项：" + json.dumps(reqs.get("nice") or [], ensure_ascii=False)
        )
    parts.append("【出题侧重】\n" + json.dumps(focus, ensure_ascii=False))
    if recent_evals:
        lines = [
            f"第{e['round']}轮（{e['phase']}）评分 {e.get('scores_json') or '{}'}；"
            f"点评：{e.get('comment') or '无'}；证据：{e.get('evidence') or '无'}"
            for e in recent_evals
        ]
        parts.append("【近轮评价（驱动你的追问）】\n" + "\n".join(lines))
    return "\n\n".join(parts)
