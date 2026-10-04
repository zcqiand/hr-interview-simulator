"""REST + SSE 路由（M00/M01/M02.F01.I01/M03.F02.I02——M05 前端唯一消费面）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from hr_interview.agent.interviewer import Interviewer
from hr_interview.matching import match_jd
from hr_interview.resume import extract_pdf_text, parse_jd, parse_resume

router = APIRouter(prefix="/api")


def _load_json(raw: str | None, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except json.JSONDecodeError:
        return fallback


# ---- M00.F02 岗位 JD ----


class JdIn(BaseModel):
    title: str
    jd_text: str
    company: str = ""


@router.post("/jds", status_code=201)
def create_jd(body: JdIn, request: Request):
    llm, store = request.app.state.llm, request.app.state.store
    reqs = parse_jd(llm, body.jd_text)
    jid = store.create_jd(
        body.title, body.jd_text, body.company,
        json.dumps({"must": reqs["must"], "nice": reqs["nice"]}, ensure_ascii=False),
    )
    return {
        "id": jid, "title": body.title, "company": body.company,
        "requirements": {"must": reqs["must"], "nice": reqs["nice"]},
        "parsed": reqs["parsed"],
    }


# ---- M00.F01 简历（PDF 上传 + 文本建档）----


class ResumeTextIn(BaseModel):
    name: str
    text: str
    jd_id: str | None = None


def _save_candidate(llm, store, name: str, text: str, jd_id: str | None) -> dict:
    if jd_id and store.get_jd(jd_id) is None:
        raise HTTPException(status_code=404, detail="JD 不存在")
    profile = parse_resume(llm, text)
    cid = store.create_candidate(
        name, jd_id, resume_text=text,
        profile_json=json.dumps(profile, ensure_ascii=False),
    )
    return {"id": cid, "name": name, "profile": profile}


@router.post("/resumes/text", status_code=201)
def create_resume_text(body: ResumeTextIn, request: Request):
    llm, store = request.app.state.llm, request.app.state.store
    return _save_candidate(llm, store, body.name, body.text, body.jd_id)


@router.post("/resumes", status_code=201)
async def upload_resume(
    request: Request,
    file: UploadFile,
    name: str = Form(...),
    jd_id: str = Form(""),
):
    llm, store = request.app.state.llm, request.app.state.store
    data = await file.read()
    try:
        text = extract_pdf_text(data)
    except ValueError as exc:  # 加密 / 无文本层——如实报原因，不硬猜
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _save_candidate(llm, store, name, text, jd_id or None)


# ---- M01.F01 匹配打分 ----


class MatchIn(BaseModel):
    candidate_id: str
    jd_id: str


@router.post("/matches", status_code=201)
def create_match(body: MatchIn, request: Request):
    llm, store = request.app.state.llm, request.app.state.store
    cand = store.get_candidate(body.candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="候选人不存在")
    jd = store.get_jd(body.jd_id)
    if jd is None:
        raise HTTPException(status_code=404, detail="JD 不存在")
    result = match_jd(
        llm,
        _load_json(cand["profile_json"], {}),
        cand["resume_text"],
        _load_json(jd["requirements_json"], {}),
    )
    mid = store.save_match(
        body.candidate_id, body.jd_id, result["total_score"],
        json.dumps(result["items"], ensure_ascii=False),
        json.dumps(result["strengths"], ensure_ascii=False),
        json.dumps(result["gaps"], ensure_ascii=False),
    )
    return {
        "id": mid, "candidate_id": body.candidate_id, "jd_id": body.jd_id,
        "total_score": result["total_score"], "items": result["items"],
        "strengths": result["strengths"], "gaps": result["gaps"],
        "parsed": result["parsed"],
    }


@router.get("/matches/{match_id}")
def get_match(match_id: str, request: Request):
    store = request.app.state.store
    row = store.get_match(match_id)
    if row is None:
        raise HTTPException(status_code=404, detail="匹配结果不存在")
    return {
        **row,
        "items": _load_json(row["items_json"], []),
        "strengths": _load_json(row["strengths_json"], []),
        "gaps": _load_json(row["gaps_json"], []),
    }


# ---- M02 面试 ----


class InterviewIn(BaseModel):
    candidate_id: str
    jd_id: str | None = None
    match_id: str | None = None


class ReplyIn(BaseModel):
    answer: str


def _interviewer(request: Request) -> Interviewer:
    return Interviewer(request.app.state.llm, request.app.state.store)


@router.post("/interviews", status_code=201)
def create_interview(body: InterviewIn, request: Request):
    store = request.app.state.store
    if store.get_candidate(body.candidate_id) is None:
        raise HTTPException(status_code=404, detail="候选人不存在")
    if body.jd_id and store.get_jd(body.jd_id) is None:
        raise HTTPException(status_code=404, detail="JD 不存在")
    iid = store.create_interview(body.candidate_id, body.jd_id, body.match_id)
    iv = store.get_interview(iid)
    return {"id": iid, "phase": iv["phase"], "status": iv["status"]}


@router.post("/interviews/{iid}/start")
def start_interview(iid: str, request: Request):
    if request.app.state.store.get_interview(iid) is None:
        raise HTTPException(status_code=404, detail="面试不存在")
    return _interviewer(request).start(iid)


@router.post("/interviews/{iid}/reply")
def reply_interview(iid: str, body: ReplyIn, request: Request):
    if request.app.state.store.get_interview(iid) is None:
        raise HTTPException(status_code=404, detail="面试不存在")
    agent = _interviewer(request)

    def gen():
        for event in agent.reply(iid, body.answer):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/interviews/{iid}")
def get_interview_detail(iid: str, request: Request):
    store = request.app.state.store
    iv = store.get_interview(iid)
    if iv is None:
        raise HTTPException(status_code=404, detail="面试不存在")
    evals = []
    for row in store.list_answer_evals(iid):
        evals.append({
            "round": row["round"], "phase": row["phase"],
            "question": row["question"], "answer": row["answer"],
            "scores": _load_json(row["scores_json"], {}),
            "evidence": row["evidence"], "comment": row["comment"],
        })
    return {"interview": iv, "messages": store.get_messages(iid), "evals": evals}


# ---- M03.F02.I02 报告 ----


@router.get("/interviews/{iid}/report")
def get_report(iid: str, request: Request):
    iv = request.app.state.store.get_interview(iid)
    if iv is None:
        raise HTTPException(status_code=404, detail="面试不存在")
    if iv["status"] != "ended" or not iv.get("report_json"):
        raise HTTPException(status_code=409, detail="面试尚未结束，报告还未生成")
    return _load_json(iv["report_json"], {})
