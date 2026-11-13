#!/usr/bin/env python3
"""性能改动回归测试：全部数据库写入临时目录，不需要 Agent 或网络。"""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kaogong_app_tests", ROOT / "app" / "app.py")
assert spec and spec.loader
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class PerformanceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_user, self.old_questions = app.USER_DB, app.KAOGONG_DB
        self.addCleanup(self.restore_paths)
        app.USER_DB = Path(self.temp.name) / "user.db"
        app.KAOGONG_DB = Path(self.temp.name) / "questions.db"
        with sqlite3.connect(app.KAOGONG_DB, factory=app.ClosingConnection) as db:
            db.execute("""CREATE TABLE questions (
                id INTEGER PRIMARY KEY, qid TEXT, module TEXT, category TEXT,
                kadian TEXT, region TEXT, year TEXT, paper TEXT, stem TEXT,
                options TEXT, answer TEXT, analysis TEXT, material TEXT, has_image INTEGER
            )""")
            db.executemany("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (i, str(i), "资料分析" if i <= 3 else "判断推理", "类型", "考点", "河南", "2026", "试卷", "<p>题干</p>", '[{"key":"A","label":"选项"}]', "A" if i != 6 else "", "解析", "材料", int(i >= 3))
                for i in range(1, 7)
            ])

    def restore_paths(self):
        app.USER_DB, app.KAOGONG_DB = self.old_user, self.old_questions

    def test_migration_runs_once_and_read_sees_fresh_data(self):
        original = importlib.util.spec_from_file_location
        with patch.object(importlib.util, "spec_from_file_location", wraps=original) as loader:
            for _ in range(3):
                with app.open_user() as db:
                    db.execute("SELECT COUNT(*) FROM answers").fetchone()
            self.assertEqual(loader.call_count, 1)
        with sqlite3.connect(app.USER_DB, factory=app.ClosingConnection) as external:
            external.execute("INSERT INTO chat_messages(role,content,created_at) VALUES ('user','new','now')")
        self.assertEqual(app.load_chat_messages()[0]["content"], "new")

    def test_missing_and_replaced_database_are_reinitialized(self):
        app.ensure_user_db()
        app.USER_DB.rename(app.USER_DB.with_suffix(".old"))
        app.ensure_user_db()
        with app.open_user() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM learner_profile").fetchone()[0], 1)
        replacement = app.USER_DB.with_suffix(".replacement")
        with sqlite3.connect(replacement, factory=app.ClosingConnection) as db:
            db.execute("CREATE TABLE sentinel (id INTEGER)")
        replacement.replace(app.USER_DB)
        app.ensure_user_db()
        with app.open_user() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_concurrent_initialization(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: app.ensure_user_db(), range(24)))
        with app.open_user() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM learner_profile").fetchone()[0], 1)

    def test_failed_migration_can_retry(self):
        with patch.object(importlib.util, "spec_from_file_location", side_effect=RuntimeError("transient")):
            with self.assertRaisesRegex(RuntimeError, "transient"):
                app.ensure_user_db()
        app.ensure_user_db()
        self.assertTrue(app.USER_DB.exists())

    def test_old_schema_migrates(self):
        with sqlite3.connect(app.USER_DB, factory=app.ClosingConnection) as db:
            db.executescript("""
                CREATE TABLE submissions(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, total INTEGER NOT NULL, correct INTEGER NOT NULL, score REAL NOT NULL);
                CREATE TABLE answers(id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, question_id INTEGER NOT NULL, user_answer TEXT NOT NULL, is_correct INTEGER NOT NULL);
                INSERT INTO submissions VALUES(1,'now',1,1,100);
                INSERT INTO answers VALUES(1,1,1,'A',1);
            """)
        app.ensure_user_db()
        with app.open_user() as db:
            self.assertIn("mode", {r[1] for r in db.execute("PRAGMA table_info(submissions)")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 1)

    def test_connections_close_and_rollback(self):
        with app.open_user() as db:
            db.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with app.open_user() as db:
                db.execute("INSERT INTO chat_messages(role,content,created_at) VALUES ('user','bad','now')")
                raise RuntimeError("rollback")
        self.assertEqual(app.load_chat_messages(), [])
        with app.open_kaogong() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM questions")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_sampling_preserves_filters_order_and_limit(self):
        rows = app.fetch_questions("2026", "河南", "资料分析", "类型", 10)
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["id"] for r in rows}, {1, 2, 3})
        self.assertEqual([r["has_image"] for r in rows], [0, 0, 1])
        self.assertTrue(all(r["stem_text"] == "题干" for r in rows))
        for _ in range(5):
            rows = app.fetch_questions(None, None, None, None, 2)
            self.assertEqual(len({r["id"] for r in rows}), 2)
            self.assertTrue(all(r["has_image"] == 0 for r in rows))
        rows = app.fetch_questions(None, None, None, None, 10, False)
        self.assertEqual({r["id"] for r in rows}, {1, 2, 3, 4, 5})
        self.assertEqual(app.fetch_questions("missing", None, None, None, 10), [])
        for limit in [0, -1]:
            self.assertEqual(app.fetch_questions(None, None, None, None, limit), [])

    def test_submission_draft_wrong_book_and_star_round_trip(self):
        rows = app.fetch_questions(None, None, "资料分析", None, 3)
        result = app.save_submission(rows, {1: "A"}, mode="exam", duration_sec=100)
        self.assertEqual((result["total"], result["correct"], result["wrong_book_upserted"]), (3, 1, 2))
        self.assertEqual(len(app.list_wrong_book()), 2)
        qid = next(r["id"] for r in rows if r["id"] != 1)
        app.set_question_starred(qid, True)
        self.assertIn(qid, app.load_starred_ids())
        app.update_wrong_status(qid, "mastered")
        self.assertEqual(app.get_wrong_question(qid)["status"], "mastered")
        app.save_quiz_draft({"questions": rows, "answers": {"1": "A"}})
        self.assertEqual(app.load_quiz_draft()["answers"], {"1": "A"})
        app.clear_quiz_draft()
        self.assertIsNone(app.load_quiz_draft())


if __name__ == "__main__":
    unittest.main(verbosity=2)
