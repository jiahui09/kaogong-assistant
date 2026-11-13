#!/usr/bin/env python3
"""六列错题目录和页眉计时回归；所有写入仅发生在临时用户库。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


class WrongBookUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        user = Path(self.temp.name) / "user.db"
        src = sqlite3.connect(f"file:{ROOT / 'data/user.db'}?mode=ro", uri=True)
        dst = sqlite3.connect(user)
        try:
            src.backup(dst)
            # 固定轻量目录数据，覆盖多页、重点、掌握状态及筛选为空。
            dst.execute("DELETE FROM wrong_book")
            dst.executemany(
                """INSERT INTO wrong_book(question_id,module,category,stem,options_json,
                   correct_answer,last_user_answer,wrong_count,first_wrong_at,last_wrong_at,
                   status,note,updated_at,paper,starred)
                   VALUES (?, '资料分析', '测试题型', '测试题干', '[]', 'A', 'B', 2,
                           'now','now',?,'','now','UI测试套卷',?)""",
                [(900000 + i, "mastered" if i == 25 else "open", int(i == 2)) for i in range(1, 26)],
            )
            dst.commit()
        finally:
            src.close()
            dst.close()
        text = (ROOT / "app/app.py").read_text().replace(
            "ROOT = Path(__file__).resolve().parent.parent", f"ROOT = Path({str(ROOT)!r})"
        ).replace('USER_DB = ROOT / "data" / "user.db"', f"USER_DB = Path({str(user)!r})")
        self.app = AppTest.from_string(text, default_timeout=30).run()
        self.assertFalse(self.app.exception)

    def tiles(self):
        return [b for b in self.app.button if b.key and b.key.startswith("wb_tile_")]

    def test_grid_selection_pagination_and_navigation(self):
        self.assertEqual(len(self.tiles()), 18)
        target = self.tiles()[1].key
        self.app.button(key=target).click().run()
        self.assertEqual(self.app.session_state["wb_selected_qid"], int(target.rsplit("_", 1)[1]))
        self.app.button(key="wb_next_page").click().run()
        self.assertEqual(len(self.tiles()), 6)
        self.assertEqual(self.app.session_state["wb_grid_page"], 1)
        self.app.button(key="wb_prev_question").click().run()
        self.assertEqual(self.app.session_state["wb_grid_page"], 0)
        self.assertEqual(len(self.tiles()), 18)
        self.assertFalse(self.app.exception)

    def test_status_star_filters_and_empty_state(self):
        self.app.checkbox(key="wb_grid_starred").check().run()
        self.assertEqual(len(self.tiles()), 1)
        self.app.selectbox(key="wb_status_side").select("mastered").run()
        self.assertEqual(len(self.tiles()), 0)
        self.assertIsNone(self.app.session_state["wb_selected_qid"])
        self.app.checkbox(key="wb_grid_starred").uncheck().run()
        self.assertEqual(len(self.tiles()), 1)
        qid = self.app.session_state["wb_selected_qid"]
        self.app.button(key=f"wb_open_{qid}").click().run()
        self.assertEqual(len(self.tiles()), 0)
        self.assertFalse(self.app.exception)

    def test_header_timer_is_fixed_and_accessible(self):
        clocks = [m.value for m in self.app.markdown if "exam-header-clock" in m.value]
        if self.app.session_state["questions"]:
            self.assertTrue(clocks)
            self.assertIn("position: fixed", clocks[0])
            self.assertIn("role='timer'", clocks[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
