#!/usr/bin/env python3
"""阶段 0 非交互验收：抽 10 题、模拟作答、写入 user.db、核对错题列表。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import importlib.util

_spec = importlib.util.spec_from_file_location("kaogong_app", APP_DIR / "app.py")
assert _spec and _spec.loader
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

KAOGONG_DB = app.KAOGONG_DB
USER_DB = app.USER_DB
ensure_user_db = app.ensure_user_db
fetch_questions = app.fetch_questions
save_submission = app.save_submission


def main() -> int:
    if not KAOGONG_DB.exists():
        print(f"[FAIL] 缺少题库: {KAOGONG_DB}")
        return 1

    ensure_user_db()
    questions = fetch_questions(
        year=None,
        region=None,
        module="言语理解与表达",
        category="逻辑填空",
        limit=10,
        prefer_no_image=True,
    )
    if len(questions) < 10:
        print(f"[FAIL] 抽题不足 10 道，实际 {len(questions)}")
        return 1

    # 故意答错一半：前 5 题选错误项，后 5 题选正确答案
    answers: dict[int, str] = {}
    expected_wrong: list[int] = []
    for i, q in enumerate(questions):
        qid = int(q["id"])
        right = (q.get("answer") or "").strip().upper()
        keys = [(o.get("key") or "").upper() for o in (q.get("options") or [])]
        if not keys or not right:
            print(f"[FAIL] 题目 {qid} 缺少选项或答案")
            return 1
        if i < 5:
            wrong = next((k for k in keys if k != right), keys[0])
            answers[qid] = wrong
            if wrong != right:
                expected_wrong.append(qid)
        else:
            answers[qid] = right

    result = save_submission(questions, answers)
    wrong_ids = [d["question_id"] for d in result["details"] if not d["is_correct"]]

    print("[INFO] submission_id =", result["submission_id"])
    print("[INFO] score =", result["score"], f"({result['correct']}/{result['total']})")
    print("[INFO] wrong_ids =", wrong_ids)

    if result["total"] != 10:
        print("[FAIL] total != 10")
        return 1
    if sorted(wrong_ids) != sorted(expected_wrong):
        print("[FAIL] 错题列表与预期不符")
        print("  expected:", expected_wrong)
        return 1

    # 核对 user.db
    with sqlite3.connect(USER_DB) as conn:
        sub = conn.execute(
            "SELECT total, correct, score FROM submissions WHERE id=?",
            (result["submission_id"],),
        ).fetchone()
        ans_n = conn.execute(
            "SELECT COUNT(*) FROM answers WHERE submission_id=?",
            (result["submission_id"],),
        ).fetchone()[0]
        wrong_n = conn.execute(
            "SELECT COUNT(*) FROM answers WHERE submission_id=? AND is_correct=0",
            (result["submission_id"],),
        ).fetchone()[0]

    if not sub:
        print("[FAIL] submissions 无记录")
        return 1
    if ans_n != 10 or wrong_n != len(expected_wrong):
        print(f"[FAIL] answers 记录异常 ans_n={ans_n} wrong_n={wrong_n}")
        return 1

    # 抽查错题展示字段齐全
    for d in result["details"]:
        if not d["is_correct"]:
            assert d["stem"], "错题缺少题干"
            assert d["user_answer"], "错题缺少用户答案"
            assert d["correct_answer"], "错题缺少正确答案"

    print("[PASS] 阶段 0 验收通过")
    print(
        json.dumps(
            {
                "submission_id": result["submission_id"],
                "score": result["score"],
                "correct": result["correct"],
                "wrong_count": len(wrong_ids),
                "user_db": str(USER_DB),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
