#!/usr/bin/env python3
"""初始化 / 升级用户答题库 data/user.db（不修改公考脑库原始库）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_DB = ROOT / "data" / "user.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    score REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'practice',
    paper TEXT,
    region TEXT,
    year TEXT,
    duration_sec INTEGER,
    timed_limit_sec INTEGER
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    time_spent_sec INTEGER,
    FOREIGN KEY (submission_id) REFERENCES submissions(id)
);

CREATE TABLE IF NOT EXISTS learner_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_name TEXT NOT NULL DEFAULT '自用考生',
    target_exam TEXT NOT NULL DEFAULT '国考+河南省考',
    focus_regions TEXT NOT NULL DEFAULT '国考,河南',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    days INTEGER NOT NULL,
    plan_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'plan'
);

CREATE TABLE IF NOT EXISTS wrong_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL UNIQUE,
    qid TEXT,
    module TEXT,
    category TEXT,
    stem TEXT,
    options_json TEXT,
    correct_answer TEXT,
    last_user_answer TEXT,
    analysis TEXT,
    wrong_count INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    first_wrong_at TEXT NOT NULL,
    last_wrong_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shenlun_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question_qid TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_drafts (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    draft_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_answers_submission ON answers(submission_id);
CREATE INDEX IF NOT EXISTS ix_answers_question ON answers(question_id);
CREATE INDEX IF NOT EXISTS ix_chat_created ON chat_messages(created_at);
CREATE INDEX IF NOT EXISTS ix_wrong_status ON wrong_book(status);
CREATE INDEX IF NOT EXISTS ix_wrong_module ON wrong_book(module);
"""


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

    sub_cols = _existing_columns(conn, "submissions")
    for col, ddl in [
        ("mode", "ALTER TABLE submissions ADD COLUMN mode TEXT NOT NULL DEFAULT 'practice'"),
        ("paper", "ALTER TABLE submissions ADD COLUMN paper TEXT"),
        ("region", "ALTER TABLE submissions ADD COLUMN region TEXT"),
        ("year", "ALTER TABLE submissions ADD COLUMN year TEXT"),
        ("duration_sec", "ALTER TABLE submissions ADD COLUMN duration_sec INTEGER"),
        ("timed_limit_sec", "ALTER TABLE submissions ADD COLUMN timed_limit_sec INTEGER"),
    ]:
        if col not in sub_cols:
            conn.execute(ddl)

    ans_cols = _existing_columns(conn, "answers")
    if "time_spent_sec" not in ans_cols:
        conn.execute("ALTER TABLE answers ADD COLUMN time_spent_sec INTEGER")

    # 默认学习者档案
    row = conn.execute("SELECT id FROM learner_profile WHERE id=1").fetchone()
    if not row:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO learner_profile(id, display_name, target_exam, focus_regions, notes, updated_at)
            VALUES (1, '自用考生', '国考+河南省考', '国考,河南', '', ?)
            """,
            (now,),
        )


def init_user_db(path: Path = USER_DB) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        migrate(conn)
        conn.commit()
    return path


if __name__ == "__main__":
    db = init_user_db()
    print(f"[OK] 已初始化/升级 {db}")
