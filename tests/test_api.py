"""API e2e（TestClient，LLM_MODE=mock 全链路离线）。

流程：建 JD → 传简历 → 匹配打分 → 建面试 → 开场 → 追问循环 → 收尾 → 报告。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hr_interview.config import load_settings
from hr_interview.main import create_app
from hr_interview.mock_llm import MockLLM
from hr_interview.store import Store

from tests.test_resume import _encrypted_pdf, _pdf_with_text


@pytest.fixture()
def client(tmp_path):
    settings = load_settings(
        environ={"LLM_MODE": "mock"}, env_file=tmp_path / "no.env"
    )
    app = create_app(settings, llm=MockLLM(), store=Store(str(tmp_path / "api.db")))
    with TestClient(app) as c:
        yield c


def _sse_events(text: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ---- 全链路 ----


def test_full_flow_mock_offline(client):
    # 1. JD
    r = client.post("/api/jds", json={
        "title": "Python 后端工程师", "company": "虚构科技",
        "jd_text": "精通 Python\n熟悉 FastAPI\n有 Kubernetes 经验优先",
    })
    assert r.status_code == 201
    jd = r.json()
    assert jd["requirements"]["must"]
    # 2. 简历（文本建档）
    r = client.post("/api/resumes/text", json={
        "name": "张三", "text": "张三，3 年 Python 后端，熟悉 FastAPI。", "jd_id": jd["id"],
    })
    assert r.status_code == 201
    cand = r.json()
    assert cand["profile"]["parsed"] is True
    # 3. 匹配打分
    r = client.post("/api/matches", json={"candidate_id": cand["id"], "jd_id": jd["id"]})
    assert r.status_code == 201
    match = r.json()
    assert isinstance(match["total_score"], int)
    # 4. 建面试 + 开场
    r = client.post("/api/interviews", json={
        "candidate_id": cand["id"], "jd_id": jd["id"], "match_id": match["id"],
    })
    assert r.status_code == 201
    iid = r.json()["id"]
    r = client.post(f"/api/interviews/{iid}/start")
    assert r.status_code == 200
    assert r.json()["question"]
    # 5. 追问循环到收尾
    ended = None
    for i in range(6):
        r = client.post(f"/api/interviews/{iid}/reply", json={"answer": f"第 {i + 1} 轮：我用 Python 做过高并发服务。"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(r.text)
        types = {e["type"] for e in events}
        assert "session" in types and "eval" in types and "done" in types
        ends = [e for e in events if e["type"] == "done" and e.get("status") == "ended"]
        if ends:
            ended = ends[0]
            break
    assert ended is not None
    # 6. 报告
    r = client.get(f"/api/interviews/{iid}/report")
    assert r.status_code == 200
    report = r.json()
    assert report["summary"] == ended["report"]["summary"]
    assert report["dimensions"]
    # 7. 面试详情
    r = client.get(f"/api/interviews/{iid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["interview"]["status"] == "ended"
    assert detail["messages"] and detail["evals"]


# ---- 解析失败如实说（工程护栏）----


def test_resume_pdf_upload_ok(client):
    # 造件文本用 ASCII（content stream 走 latin-1）；中文姓名走 form 字段
    pdf = _pdf_with_text("Zhang San, 3 years Python backend, skilled in FastAPI and MySQL.")
    r = client.post(
        "/api/resumes",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"name": "李四"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "李四"
    assert body["profile"]["parsed"] is True
    assert "python" in [s.lower() for s in body["profile"]["skills"]]


def test_resume_encrypted_pdf_reports_reason(client):
    r = client.post(
        "/api/resumes",
        files={"file": ("locked.pdf", _encrypted_pdf(), "application/pdf")},
        data={"name": "王五"},
    )
    assert r.status_code == 400
    assert "加密" in r.json()["detail"]


def test_resume_no_text_layer_reports_reason(client):
    from tests.test_resume import _empty_pdf
    r = client.post(
        "/api/resumes",
        files={"file": ("scan.pdf", _empty_pdf(), "application/pdf")},
        data={"name": "赵六"},
    )
    assert r.status_code == 400
    assert "文本层" in r.json()["detail"]


# ---- 错误路径 ----


def test_reply_unknown_interview_404(client):
    r = client.post("/api/interviews/iv_nope/reply", json={"answer": "在吗"})
    assert r.status_code == 404


def test_reply_after_end_streams_error_event(client):
    # 建到 start
    jd = client.post("/api/jds", json={"title": "T", "jd_text": "Python"}).json()
    cand = client.post("/api/resumes/text", json={"name": "张三", "text": "Python 工程师", "jd_id": jd["id"]}).json()
    iid = client.post("/api/interviews", json={"candidate_id": cand["id"], "jd_id": jd["id"]}).json()["id"]
    client.post(f"/api/interviews/{iid}/start")
    # 直接改库标记结束
    from hr_interview.store import Store
    store = client.app.state.store
    store.update_interview(iid, {"status": "ended"})
    r = client.post(f"/api/interviews/{iid}/reply", json={"answer": "还有吗"})
    events = _sse_events(r.text)
    assert events[0]["type"] == "error"
    assert "结束" in events[0]["message"]


def test_manual_finish_then_report(client):
    jd = client.post("/api/jds", json={"title": "T", "jd_text": "Python"}).json()
    cand = client.post("/api/resumes/text", json={"name": "张三", "text": "Python 工程师", "jd_id": jd["id"]}).json()
    iid = client.post("/api/interviews", json={"candidate_id": cand["id"], "jd_id": jd["id"]}).json()["id"]
    client.post(f"/api/interviews/{iid}/start")
    r = client.post(f"/api/interviews/{iid}/finish")  # 候选人主动结束（M05 结束按钮）
    assert r.status_code == 200
    report = r.json()
    assert report["candidate_name"] == "张三"
    assert client.get(f"/api/interviews/{iid}/report").json()["summary"] == report["summary"]
    # 重复收尾 → 409
    assert client.post(f"/api/interviews/{iid}/finish").status_code == 409


def test_report_before_end_409(client):
    jd = client.post("/api/jds", json={"title": "T", "jd_text": "Python"}).json()
    cand = client.post("/api/resumes/text", json={"name": "张三", "text": "Python 工程师", "jd_id": jd["id"]}).json()
    iid = client.post("/api/interviews", json={"candidate_id": cand["id"], "jd_id": jd["id"]}).json()["id"]
    client.post(f"/api/interviews/{iid}/start")
    r = client.get(f"/api/interviews/{iid}/report")
    assert r.status_code == 409
