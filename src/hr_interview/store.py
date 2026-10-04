"""SQLite 存储（候选人/JD/匹配/面试会话/消息/评价档案）。

薄 DAO：单连接 + 锁，够用就好，不引 ORM。
时间戳存 UTC ISO 串。JSON 结构（画像/要求清单/逐条打分/维度评分）存 TEXT 序列化，
由领域层（resume/matching/evaluation）负责编解码——DAO 不理解业务形状。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jds(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT NOT NULL DEFAULT '',
  jd_text TEXT NOT NULL,
  requirements_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  jd_id TEXT REFERENCES jds(id),
  resume_text TEXT NOT NULL DEFAULT '',
  profile_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches(
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  jd_id TEXT NOT NULL REFERENCES jds(id),
  total_score INTEGER NOT NULL,
  items_json TEXT NOT NULL,
  strengths_json TEXT NOT NULL,
  gaps_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interviews(
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  jd_id TEXT REFERENCES jds(id),
  match_id TEXT REFERENCES matches(id),
  phase TEXT NOT NULL DEFAULT 'opening',
  question_count INTEGER NOT NULL DEFAULT 0,
  round INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'live',
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interview_id TEXT NOT NULL REFERENCES interviews(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  phase TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answer_evals(
  id TEXT PRIMARY KEY,
  interview_id TEXT NOT NULL REFERENCES interviews(id),
  round INTEGER NOT NULL,
  phase TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  answer TEXT NOT NULL,
  scores_json TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """所有持久化读写的唯一入口。"""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---- jds ----

    def create_jd(self, title: str, jd_text: str, company: str = "", requirements_json: str = "{}") -> str:
        jid = f"jd_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO jds(id, title, company, jd_text, requirements_json, created_at) VALUES(?,?,?,?,?,?)",
                (jid, title, company, jd_text, requirements_json, now_iso()),
            )
            self._conn.commit()
        return jid

    def get_jd(self, jd_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jds WHERE id=?", (jd_id,)).fetchone()
        return dict(row) if row else None

    def list_jds(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM jds ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def update_jd_requirements(self, jd_id: str, requirements_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jds SET requirements_json=? WHERE id=?", (requirements_json, jd_id)
            )
            self._conn.commit()

    # ---- candidates ----

    def create_candidate(self, name: str, jd_id: str | None, resume_text: str = "", profile_json: str = "{}") -> str:
        cid = f"cand_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO candidates(id, name, jd_id, resume_text, profile_json, created_at) VALUES(?,?,?,?,?,?)",
                (cid, name, jd_id, resume_text, profile_json, now_iso()),
            )
            self._conn.commit()
        return cid

    def get_candidate(self, candidate_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def list_candidates(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM candidates ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def update_candidate_profile(self, candidate_id: str, profile_json: str, resume_text: str | None = None) -> None:
        with self._lock:
            if resume_text is None:
                self._conn.execute(
                    "UPDATE candidates SET profile_json=? WHERE id=?", (profile_json, candidate_id)
                )
            else:
                self._conn.execute(
                    "UPDATE candidates SET profile_json=?, resume_text=? WHERE id=?",
                    (profile_json, resume_text, candidate_id),
                )
            self._conn.commit()

    # ---- matches ----

    def save_match(
        self, candidate_id: str, jd_id: str,
        total_score: int, items_json: str, strengths_json: str, gaps_json: str,
    ) -> str:
        mid = f"m_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO matches(id, candidate_id, jd_id, total_score, items_json, strengths_json, gaps_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (mid, candidate_id, jd_id, total_score, items_json, strengths_json, gaps_json, now_iso()),
            )
            self._conn.commit()
        return mid

    def get_match(self, match_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        return dict(row) if row else None

    def latest_match(self, candidate_id: str, jd_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM matches WHERE candidate_id=? AND jd_id=? ORDER BY rowid DESC LIMIT 1",
            (candidate_id, jd_id),
        ).fetchone()
        return dict(row) if row else None

    # ---- interviews / messages / 评价档案 ----

    def create_interview(self, candidate_id: str, jd_id: str | None, match_id: str | None) -> str:
        iid = f"iv_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO interviews(id, candidate_id, jd_id, match_id, started_at) VALUES(?,?,?,?,?)",
                (iid, candidate_id, jd_id, match_id, now_iso()),
            )
            self._conn.commit()
        return iid

    def get_interview(self, interview_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
        return dict(row) if row else None

    def update_interview(self, interview_id: str, fields: dict) -> None:
        cols = ",".join(f"{c}=?" for c in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE interviews SET {cols} WHERE id=?", (*fields.values(), interview_id)
            )
            self._conn.commit()

    def append_message(self, interview_id: str, role: str, content: str, phase: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(interview_id, role, content, phase, created_at) VALUES(?,?,?,?,?)",
                (interview_id, role, content, phase, now_iso()),
            )
            self._conn.commit()

    def get_messages(self, interview_id: str, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content, phase, created_at FROM ("
            "  SELECT * FROM messages WHERE interview_id=? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id",
            (interview_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_answer_eval(
        self, interview_id: str, round_no: int, phase: str,
        question: str, answer: str, scores_json: str, evidence: str, comment: str,
    ) -> str:
        eid = f"e_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO answer_evals(id, interview_id, round, phase, question, answer, scores_json, evidence, comment, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (eid, interview_id, round_no, phase, question, answer, scores_json, evidence, comment, now_iso()),
            )
            self._conn.commit()
        return eid

    def list_answer_evals(self, interview_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM answer_evals WHERE interview_id=? ORDER BY round",
            (interview_id,),
        ).fetchall()
        return [dict(r) for r in rows]
