"""store.py 往返测试：内存库（tmp_path）建表→写→读→断言。"""
from __future__ import annotations

import json

from hr_interview.store import Store


def test_jd_roundtrip(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    jid = s.create_jd("后端工程师", "负责面试系统后端…要求 Python…", company="示例虚构公司")
    jd = s.get_jd(jid)
    assert jd is not None
    assert jd["title"] == "后端工程师"
    assert jd["company"] == "示例虚构公司"
    assert "Python" in jd["jd_text"]
    assert s.get_jd("nope") is None
    assert len(s.list_jds()) == 1


def test_jd_requirements_update(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    jid = s.create_jd("岗位", "要求……")
    req = {"must": ["Python"], "nice": ["Docker"]}
    s.update_jd_requirements(jid, json.dumps(req, ensure_ascii=False))
    assert json.loads(s.get_jd(jid)["requirements_json"]) == req


def test_candidate_roundtrip_and_profile_update(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    jid = s.create_jd("岗位", "……")
    cid = s.create_candidate("张三", jid)
    c = s.get_candidate(cid)
    assert c["name"] == "张三"
    assert c["jd_id"] == jid
    assert json.loads(c["profile_json"]) == {}

    prof = {"skills": ["Python"], "experiences": [], "education": [], "highlights": []}
    s.update_candidate_profile(cid, json.dumps(prof), resume_text="原文…")
    c2 = s.get_candidate(cid)
    assert json.loads(c2["profile_json"])["skills"] == ["Python"]
    assert c2["resume_text"] == "原文…"
    assert len(s.list_candidates()) == 1


def test_candidate_without_jd(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    cid = s.create_candidate("李四", None)
    assert s.get_candidate(cid)["jd_id"] is None


def test_match_roundtrip_and_latest(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    jid = s.create_jd("岗位", "……")
    cid = s.create_candidate("张三", jid)
    m1 = s.save_match(cid, jid, 62, "[]", '["Python 扎实"]', '["缺数据库经验"]')
    m2 = s.save_match(cid, jid, 75, "[]", "[]", "[]")
    assert s.get_match(m1)["total_score"] == 62
    assert s.latest_match(cid, jid)["id"] == m2  # 取最新一次
    assert s.latest_match(cid, "jd_none") is None


def test_interview_messages_and_evals(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    cid = s.create_candidate("张三", None)
    iid = s.create_interview(cid, None, None)
    iv = s.get_interview(iid)
    assert iv["phase"] == "opening"
    assert iv["status"] == "live"
    assert iv["round"] == 0

    s.append_message(iid, "assistant", "先自我介绍一下", phase="opening")
    s.append_message(iid, "user", "我叫张三……", phase="opening")
    msgs = s.get_messages(iid)
    assert [m["role"] for m in msgs] == ["assistant", "user"]  # 按时间正序

    s.update_interview(iid, {"phase": "tech", "question_count": 1, "round": 1})
    s.insert_answer_eval(
        iid, 1, "opening", "先自我介绍", "我叫张三，三年 Python……",
        json.dumps({"tech_depth": 3, "clarity": 4, "evidence": 3, "fit": 4}, ensure_ascii=False),
        evidence="三年 Python", comment="整体清楚，技术细节可追问",
    )
    evs = s.list_answer_evals(iid)
    assert len(evs) == 1
    assert evs[0]["round"] == 1
    assert evs[0]["phase"] == "opening"
    assert json.loads(evs[0]["scores_json"])["clarity"] == 4

    s.update_interview(iid, {"status": "ended", "ended_at": "2026-10-04T12:00:00+00:00"})
    assert s.get_interview(iid)["status"] == "ended"
