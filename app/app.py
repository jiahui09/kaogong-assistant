#!/usr/bin/env python3
"""考公助手 Streamlit 前端（行测 / 申论分模块）。"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
KAOGONG_DB = ROOT / "data" / "kaogong.db"
SHENLUN_DB = ROOT / "data" / "shenlun.db"
USER_DB = ROOT / "data" / "user.db"
AGENT_BASE = "http://127.0.0.1:8787"

DEFAULT_LIMIT = 10
DEFAULT_EXAM_MINUTES = 120
FOCUS_REGIONS = ("国考", "河南")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def open_kaogong(readonly: bool = True) -> sqlite3.Connection:
    if not KAOGONG_DB.exists():
        raise FileNotFoundError(
            f"未找到题库 {KAOGONG_DB}。请先运行: bash scripts/prepare_kaogong.sh"
        )
    uri = f"file:{KAOGONG_DB}?mode=ro" if readonly else str(KAOGONG_DB)
    conn = sqlite3.connect(uri, uri=readonly)
    conn.row_factory = sqlite3.Row
    return conn


def open_shenlun(readonly: bool = True) -> sqlite3.Connection:
    if not SHENLUN_DB.exists():
        raise FileNotFoundError(
            f"未找到申论库 {SHENLUN_DB}。请先运行: python3 scripts/prepare_shenlun.py"
        )
    uri = f"file:{SHENLUN_DB}?mode=ro" if readonly else str(SHENLUN_DB)
    conn = sqlite3.connect(uri, uri=readonly)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_user_db() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "init_user_db", ROOT / "scripts" / "init_user_db.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.init_user_db(USER_DB)


def open_user() -> sqlite3.Connection:
    ensure_user_db()
    conn = sqlite3.connect(USER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def parse_options(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    out: list[str] = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return "".join(out).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 行测题库
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_filter_options() -> dict[str, list[str]]:
    with open_kaogong() as conn:
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM questions WHERE year!='' ORDER BY year DESC"
        )]
        regions = [r[0] for r in conn.execute(
            "SELECT DISTINCT region FROM questions WHERE region!='' ORDER BY region"
        )]
        modules = [r[0] for r in conn.execute(
            "SELECT DISTINCT module FROM questions WHERE module!='' ORDER BY module"
        )]
        categories = [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM questions WHERE category!='' ORDER BY category"
        )]
    return {
        "years": years,
        "regions": regions,
        "modules": modules,
        "categories": categories,
    }


@st.cache_data(show_spinner=False)
def list_xingce_papers(focus_only: bool = True) -> list[dict[str, Any]]:
    clauses = ["a.paper != ''"]
    params: list[Any] = []
    if focus_only:
        clauses.append(f"a.region IN ({','.join('?' for _ in FOCUS_REGIONS)})")
        params.extend(FOCUS_REGIONS)
    sql = f"""
        SELECT a.paper, a.region, a.year, COUNT(*) AS n
        FROM appearances a
        JOIN questions q ON q.qid = a.qid
        WHERE {' AND '.join(clauses)}
        GROUP BY a.paper, a.region, a.year
        HAVING n >= 20
        ORDER BY
          CASE a.region WHEN '国考' THEN 0 WHEN '河南' THEN 1 ELSE 2 END,
          a.year DESC,
          n DESC
    """
    with open_kaogong() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetch_questions(
    year: str | None,
    region: str | None,
    module: str | None,
    category: str | None,
    limit: int,
    prefer_no_image: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["answer != ''"]
    params: list[Any] = []
    if year:
        clauses.append("year = ?")
        params.append(year)
    if region:
        clauses.append("region = ?")
        params.append(region)
    if module:
        clauses.append("module = ?")
        params.append(module)
    if category:
        clauses.append("category = ?")
        params.append(category)
    order = "has_image ASC, RANDOM()" if prefer_no_image else "RANDOM()"
    sql = f"""
        SELECT id, qid, module, category, kadian, region, year, paper,
               stem, options, answer, analysis, material, has_image
        FROM questions
        WHERE {' AND '.join(clauses)}
        ORDER BY {order}
        LIMIT ?
    """
    params.append(limit)
    with open_kaogong() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_normalize_question(dict(row)) for row in rows]


def fetch_paper_questions(paper: str, region: str, year: str) -> list[dict[str, Any]]:
    sql = """
        SELECT q.id, q.qid, q.module, q.category, q.kadian, a.region, a.year, a.paper,
               q.stem, q.options, q.answer, q.analysis, q.material, q.has_image,
               a.seq
        FROM appearances a
        JOIN questions q ON q.qid = a.qid
        WHERE a.paper = ? AND a.region = ? AND a.year = ?
        ORDER BY COALESCE(a.seq, 999999), q.id
    """
    with open_kaogong() as conn:
        rows = conn.execute(sql, (paper, region, year)).fetchall()
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _normalize_question(dict(row))
        qid = int(item["id"])
        if qid in seen:
            continue
        seen.add(qid)
        out.append(item)
    return out


def _normalize_question(item: dict[str, Any]) -> dict[str, Any]:
    item["options"] = parse_options(item.get("options"))
    item["stem_text"] = strip_html(item.get("stem"))
    item["material_text"] = strip_html(item.get("material"))
    item["analysis_text"] = strip_html(item.get("analysis"))
    return item


# ---------------------------------------------------------------------------
# 申论题库
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def list_shenlun_papers(focus_only: bool = True) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if focus_only:
        clauses.append("(province IN ('国考','河南') OR exam_type='国考')")
    sql = f"""
        SELECT id, pid, exam_type, year, province, title,
               json_array_length(material) AS material_count,
               json_array_length(questions) AS question_count
        FROM papers
        WHERE {' AND '.join(clauses)}
        ORDER BY
          CASE WHEN province='国考' OR exam_type='国考' THEN 0
               WHEN province='河南' THEN 1 ELSE 2 END,
          year DESC,
          id DESC
    """
    with open_shenlun() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_shenlun_paper(paper_id: int) -> dict[str, Any] | None:
    with open_shenlun() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["materials"] = json.loads(item.get("material") or "[]")
    except json.JSONDecodeError:
        item["materials"] = []
    try:
        item["questions_list"] = json.loads(item.get("questions") or "[]")
    except json.JSONDecodeError:
        item["questions_list"] = []
    return item


# ---------------------------------------------------------------------------
# 用户持久化：交卷 / 错题本 / 档案 / 对话
# ---------------------------------------------------------------------------

def upsert_wrong_book(details: list[dict[str, Any]]) -> int:
    """把错题写入 wrong_book；已存在则累加次数并更新最近作答。"""
    n = 0
    now = now_iso()
    with open_user() as conn:
        for d in details:
            if d.get("is_correct"):
                continue
            qid = int(d["question_id"])
            row = conn.execute(
                "SELECT id, wrong_count FROM wrong_book WHERE question_id=?",
                (qid,),
            ).fetchone()
            opts = json.dumps(d.get("options") or [], ensure_ascii=False)
            if row:
                conn.execute(
                    """
                    UPDATE wrong_book
                    SET last_user_answer=?, wrong_count=wrong_count+1,
                        last_wrong_at=?, updated_at=?, status='open',
                        analysis=COALESCE(NULLIF(?, ''), analysis)
                    WHERE question_id=?
                    """,
                    (
                        d.get("user_answer"),
                        now,
                        now,
                        d.get("analysis") or "",
                        qid,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO wrong_book(
                        question_id, qid, module, category, stem, options_json,
                        correct_answer, last_user_answer, analysis, wrong_count,
                        note, status, first_wrong_at, last_wrong_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        qid,
                        d.get("qid"),
                        d.get("module"),
                        d.get("category"),
                        d.get("stem"),
                        opts,
                        d.get("correct_answer"),
                        d.get("user_answer"),
                        d.get("analysis") or "",
                        1,
                        "",
                        "open",
                        now,
                        now,
                        now,
                    ),
                )
            n += 1
        conn.commit()
    return n


def list_wrong_book(
    status: str | None = "open", module: str | None = None
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if status and status != "(全部)":
        clauses.append("status = ?")
        params.append(status)
    if module and module != "(全部)":
        clauses.append("module = ?")
        params.append(module)
    sql = f"""
        SELECT * FROM wrong_book
        WHERE {' AND '.join(clauses)}
        ORDER BY last_wrong_at DESC, id DESC
    """
    with open_user() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_wrong_note(question_id: int, note: str) -> None:
    with open_user() as conn:
        conn.execute(
            "UPDATE wrong_book SET note=?, updated_at=? WHERE question_id=?",
            (note, now_iso(), int(question_id)),
        )
        conn.commit()


def update_wrong_status(question_id: int, status: str) -> None:
    with open_user() as conn:
        conn.execute(
            "UPDATE wrong_book SET status=?, updated_at=? WHERE question_id=?",
            (status, now_iso(), int(question_id)),
        )
        conn.commit()


def save_submission(
    questions: list[dict[str, Any]],
    answers: dict[int, str],
    *,
    mode: str = "practice",
    paper: str | None = None,
    region: str | None = None,
    year: str | None = None,
    duration_sec: int | None = None,
    timed_limit_sec: int | None = None,
    time_spent: dict[int, int] | None = None,
) -> dict[str, Any]:
    ensure_user_db()
    total = len(questions)
    correct = 0
    detail: list[dict[str, Any]] = []
    time_spent = time_spent or {}
    for q in questions:
        qid = int(q["id"])
        user_ans = (answers.get(qid) or "").strip().upper()
        right = (q.get("answer") or "").strip().upper()
        is_correct = int(user_ans == right and user_ans != "")
        if is_correct:
            correct += 1
        detail.append(
            {
                "question_id": qid,
                "qid": q.get("qid"),
                "stem": q.get("stem_text") or strip_html(q.get("stem")),
                "module": q.get("module"),
                "category": q.get("category"),
                "options": q.get("options") or [],
                "user_answer": user_ans or "(未作答)",
                "correct_answer": right or "(无答案)",
                "is_correct": bool(is_correct),
                "analysis": q.get("analysis_text") or strip_html(q.get("analysis")),
                "time_spent_sec": int(time_spent.get(qid) or 0) or None,
            }
        )
    score = round(100.0 * correct / total, 2) if total else 0.0
    created_at = now_iso()
    with open_user() as conn:
        cur = conn.execute(
            """
            INSERT INTO submissions(
                created_at, total, correct, score, mode, paper, region, year,
                duration_sec, timed_limit_sec
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                created_at,
                total,
                correct,
                score,
                mode,
                paper,
                region,
                year,
                duration_sec,
                timed_limit_sec,
            ),
        )
        submission_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO answers(
                submission_id, question_id, user_answer, is_correct, time_spent_sec
            ) VALUES (?,?,?,?,?)
            """,
            [
                (
                    submission_id,
                    d["question_id"],
                    d["user_answer"],
                    int(d["is_correct"]),
                    d.get("time_spent_sec"),
                )
                for d in detail
            ],
        )
        conn.commit()

    wrong_n = upsert_wrong_book(detail)
    return {
        "submission_id": submission_id,
        "created_at": created_at,
        "total": total,
        "correct": correct,
        "score": score,
        "mode": mode,
        "paper": paper,
        "region": region,
        "year": year,
        "duration_sec": duration_sec,
        "timed_limit_sec": timed_limit_sec,
        "details": detail,
        "wrong_book_upserted": wrong_n,
    }


def load_learner_profile() -> dict[str, Any]:
    with open_user() as conn:
        row = conn.execute("SELECT * FROM learner_profile WHERE id=1").fetchone()
    return dict(row) if row else {}


def save_learner_profile(
    display_name: str, target_exam: str, focus_regions: str, notes: str
) -> None:
    with open_user() as conn:
        conn.execute(
            """
            UPDATE learner_profile
            SET display_name=?, target_exam=?, focus_regions=?, notes=?, updated_at=?
            WHERE id=1
            """,
            (display_name, target_exam, focus_regions, notes, now_iso()),
        )
        conn.commit()


def load_chat_messages(limit: int = 50) -> list[dict[str, Any]]:
    with open_user() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM chat_messages
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def append_chat_message(role: str, content: str) -> None:
    with open_user() as conn:
        conn.execute(
            "INSERT INTO chat_messages(role, content, created_at) VALUES (?,?,?)",
            (role, content, now_iso()),
        )
        conn.commit()


def save_study_plan(days: int, plan_text: str, source: str = "plan") -> None:
    with open_user() as conn:
        conn.execute(
            "INSERT INTO study_plans(days, plan_text, created_at, source) VALUES (?,?,?,?)",
            (days, plan_text, now_iso(), source),
        )
        conn.commit()


def save_shenlun_attempt(paper_id: int, question_qid: str, answer_text: str) -> None:
    with open_user() as conn:
        conn.execute(
            """
            INSERT INTO shenlun_attempts(paper_id, question_qid, answer_text, created_at)
            VALUES (?,?,?,?)
            """,
            (int(paper_id), question_qid, answer_text, now_iso()),
        )
        conn.commit()


def recent_submissions(limit: int = 8) -> list[dict[str, Any]]:
    with open_user() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, total, correct, score, mode, paper, region, year,
                   duration_sec, timed_limit_sec
            FROM submissions
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Agent HTTP
# ---------------------------------------------------------------------------

def _post_json(path: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        f"{AGENT_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_one_explanation(question_id: int, user_answer: str) -> dict[str, Any]:
    try:
        body = _post_json(
            "/explain-one",
            {"question_id": int(question_id), "user_answer": user_answer},
            timeout=180,
        )
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "text": "", "error": "AI 返回空解析"}
        return {"ok": True, "text": text, "error": None}
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return {
            "ok": False,
            "text": "",
            "error": f"AI 解析失败（HTTP {exc.code}）：{detail or exc.reason}",
        }
    except urlerror.URLError as exc:
        return {
            "ok": False,
            "text": "",
            "error": f"AI 解析暂不可用（连接失败：{exc.reason}）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "error": f"AI 解析暂不可用（{exc}）"}


def fetch_study_plan(days: int) -> dict[str, Any]:
    try:
        body = _post_json("/plan", {"days": int(days)}, timeout=240)
        plan = str(body.get("plan") or "").strip()
        if not plan:
            return {"ok": False, "plan": "", "error": "AI 返回空规划"}
        return {"ok": True, "plan": plan, "error": None}
    except urlerror.URLError as exc:
        return {
            "ok": False,
            "plan": "",
            "error": f"学习规划暂不可用（连接失败：{exc.reason}）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "plan": "", "error": f"学习规划暂不可用（{exc}）"}


def fetch_chat_reply(message: str) -> dict[str, Any]:
    try:
        body = _post_json("/chat", {"message": message}, timeout=240)
        text = str(body.get("reply") or body.get("text") or "").strip()
        if not text:
            return {"ok": False, "reply": "", "error": "AI 返回空回复"}
        return {"ok": True, "reply": text, "error": None}
    except urlerror.URLError as exc:
        return {
            "ok": False,
            "reply": "",
            "error": f"对话暂不可用（连接失败：{exc.reason}）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reply": "", "error": f"对话暂不可用（{exc}）"}


def cache_key_for_one(question_id: int, user_answer: str) -> str:
    return f"{int(question_id)}::{(user_answer or '').strip().upper()}"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def init_session() -> None:
    defaults: dict[str, Any] = {
        "subject": "xingce",  # xingce | shenlun
        "mode": "practice",
        "questions": [],
        "answers": {},
        "quiz_meta": {},
        "result": None,
        "page": "quiz",
        "one_explanations": {},
        "one_errors": {},
        "one_request_count": {},
        "study_plan": None,
        "study_plan_error": None,
        "study_plan_days": 7,
        "exam_started_at": None,
        "exam_limit_sec": DEFAULT_EXAM_MINUTES * 60,
        "question_entered_at": {},
        "time_spent": {},
        "shenlun_paper_id": None,
        "shenlun_answers": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def option_label(opt: dict[str, Any]) -> str:
    key = opt.get("key") or "?"
    label = strip_html(opt.get("label") or opt.get("html") or "")
    return f"{key}. {label}" if label else str(key)


def format_duration(sec: int | None) -> str:
    if sec is None:
        return "-"
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def touch_question_timer(qid: int) -> None:
    now = time.time()
    entered = st.session_state.question_entered_at or {}
    spent = dict(st.session_state.time_spent or {})
    prev = entered.get("_current")
    prev_at = entered.get("_at")
    if prev and prev_at:
        spent[int(prev)] = int(spent.get(int(prev), 0) + max(0, now - float(prev_at)))
    entered["_current"] = qid
    entered["_at"] = now
    st.session_state.question_entered_at = entered
    st.session_state.time_spent = spent


def finalize_timers() -> dict[int, int]:
    now = time.time()
    entered = st.session_state.question_entered_at or {}
    spent = dict(st.session_state.time_spent or {})
    prev = entered.get("_current")
    prev_at = entered.get("_at")
    if prev and prev_at:
        spent[int(prev)] = int(spent.get(int(prev), 0) + max(0, now - float(prev_at)))
    st.session_state.time_spent = spent
    st.session_state.question_entered_at = {}
    return spent


def start_quiz(
    questions: list[dict[str, Any]],
    *,
    mode: str,
    meta: dict[str, Any],
    timed_limit_sec: int | None = None,
) -> None:
    st.session_state.questions = questions
    st.session_state.answers = {}
    st.session_state.result = None
    st.session_state.page = "quiz"
    st.session_state.mode = mode
    st.session_state.quiz_meta = meta
    st.session_state.time_spent = {}
    st.session_state.question_entered_at = {}
    st.session_state.exam_started_at = time.time() if mode == "exam" else None
    st.session_state.exam_limit_sec = timed_limit_sec or DEFAULT_EXAM_MINUTES * 60
    st.session_state.one_explanations = {}
    st.session_state.one_errors = {}


# ---------------------------------------------------------------------------
# UI: 计时（自动刷新）
# ---------------------------------------------------------------------------

@st.fragment(run_every=1)
def render_exam_timer_banner() -> None:
    """每秒自动刷新，不依赖用户操作。"""
    if st.session_state.mode != "exam" or not st.session_state.exam_started_at:
        return
    started = float(st.session_state.exam_started_at)
    elapsed = int(time.time() - started)
    limit = int(st.session_state.exam_limit_sec or 0)
    remain = limit - elapsed
    answered = sum(
        1
        for q in st.session_state.questions
        if st.session_state.answers.get(int(q["id"]))
    )
    total = len(st.session_state.questions)
    pace = (elapsed / answered) if answered else 0
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("已用时", format_duration(elapsed))
    col2.metric("参考剩余", format_duration(remain) if remain >= 0 else "已超时")
    col3.metric("作答进度", f"{answered}/{total}")
    col4.metric("平均每题", format_duration(int(pace)) if answered else "-")
    if remain < 0:
        st.info("参考时长已到，可继续作答；本模式不做强制交卷。")
    else:
        st.caption("计时每秒自动刷新，仅供参考，到时不会强制交卷。")


# ---------------------------------------------------------------------------
# UI: 行测
# ---------------------------------------------------------------------------

def render_practice_filters() -> None:
    opts = load_filter_options()
    st.sidebar.subheader("练习模式筛选")
    regions = list(opts["regions"])
    preferred = [r for r in FOCUS_REGIONS if r in regions]
    others = [r for r in regions if r not in FOCUS_REGIONS]
    region_choices = ["(不限)"] + preferred + others

    year = st.sidebar.selectbox("年份", ["(不限)"] + opts["years"], index=0, key="p_year")
    region = st.sidebar.selectbox("省份/地区", region_choices, index=0, key="p_region")
    module = st.sidebar.selectbox(
        "科目/模块", ["(不限)"] + opts["modules"], index=0, key="p_module"
    )
    category = st.sidebar.selectbox(
        "题型", ["(不限)"] + opts["categories"], index=0, key="p_category"
    )
    limit = st.sidebar.number_input(
        "题目数量", min_value=1, max_value=50, value=DEFAULT_LIMIT, step=1, key="p_limit"
    )
    prefer_no_image = st.sidebar.checkbox("优先无图题（推荐）", value=True, key="p_img")

    if st.sidebar.button("开始练习 / 换一组", type="primary", use_container_width=True):
        qs = fetch_questions(
            None if year == "(不限)" else year,
            None if region == "(不限)" else region,
            None if module == "(不限)" else module,
            None if category == "(不限)" else category,
            int(limit),
            prefer_no_image=prefer_no_image,
        )
        if not qs:
            st.sidebar.error("当前筛选条件下没有题目，请放宽条件。")
        else:
            start_quiz(
                qs,
                mode="practice",
                meta={
                    "year": year,
                    "region": region,
                    "module": module,
                    "category": category,
                    "limit": int(limit),
                },
            )
            st.rerun()


def render_exam_filters() -> None:
    st.sidebar.subheader("套卷模拟（真题卷）")
    focus_only = st.sidebar.checkbox("只看国考 + 河南省考（推荐）", value=True, key="exam_focus")
    papers = list_xingce_papers(focus_only=focus_only)
    if not papers:
        st.sidebar.warning("未找到可用套卷。")
        return
    labels = [
        f"[{p['region']}] {p['year']} · {p['paper']}（{p['n']}题）" for p in papers
    ]
    idx = st.sidebar.selectbox(
        "选择试卷",
        options=list(range(len(labels))),
        format_func=lambda i: labels[i],
        key="exam_paper_idx",
    )
    minutes = st.sidebar.number_input(
        "参考时长（分钟，不强制交卷）",
        min_value=30,
        max_value=240,
        value=DEFAULT_EXAM_MINUTES,
        step=5,
        key="exam_minutes",
    )
    if st.sidebar.button("进入套卷模拟", type="primary", use_container_width=True):
        p = papers[int(idx)]
        qs = fetch_paper_questions(p["paper"], p["region"], p["year"])
        if not qs:
            st.sidebar.error("该套卷没有可做题目。")
        else:
            start_quiz(
                qs,
                mode="exam",
                meta={
                    "paper": p["paper"],
                    "region": p["region"],
                    "year": p["year"],
                    "count": len(qs),
                },
                timed_limit_sec=int(minutes) * 60,
            )
            st.rerun()


def render_quiz() -> None:
    questions: list[dict[str, Any]] = st.session_state.questions
    if not questions:
        st.info("请在左侧选择练习筛选或套卷，开始做题。")
        return

    meta = st.session_state.quiz_meta or {}
    if st.session_state.mode == "exam":
        st.markdown(
            f"**套卷模拟**：`{meta.get('region')}` {meta.get('year')} · {meta.get('paper')}"
        )
        render_exam_timer_banner()
    else:
        st.caption(
            f"练习模式：{len(questions)} 道｜"
            f"年份={meta.get('year','(不限)')} / 地区={meta.get('region','(不限)')} / "
            f"模块={meta.get('module','(不限)')} / 题型={meta.get('category','(不限)')}"
        )

    for i, q in enumerate(questions, start=1):
        qid = int(q["id"])
        with st.container(border=True):
            st.markdown(
                f"**第 {i} 题** · `{q.get('module')}` / `{q.get('category')}` · "
                f"{q.get('region')} {q.get('year')} · id={qid}"
            )
            if q.get("material_text"):
                with st.expander("材料", expanded=False):
                    st.write(q["material_text"])
            st.write(q.get("stem_text") or "(题干为空)")

            opts = q.get("options") or []
            choices = [option_label(o) for o in opts]
            keys = [(o.get("key") or "").upper() for o in opts]
            if not choices:
                st.warning("该题无选项数据，跳过。")
                continue

            touch_question_timer(qid)
            radio_options = ["(请选择)"] + choices
            current = st.session_state.answers.get(qid)
            default_idx = keys.index(current) + 1 if current in keys else 0
            selected = st.radio(
                f"选项##{qid}",
                options=radio_options,
                index=default_idx,
                key=f"radio_{qid}",
                label_visibility="collapsed",
            )
            if selected == "(请选择)":
                st.session_state.answers.pop(qid, None)
            else:
                st.session_state.answers[qid] = keys[choices.index(selected)]

    answered = sum(1 for q in questions if st.session_state.answers.get(int(q["id"])))
    st.write(f"已作答 {answered} / {len(questions)}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("交卷批分", type="primary", use_container_width=True):
            spent = finalize_timers()
            duration = None
            limit = None
            paper = region = year = None
            if st.session_state.mode == "exam" and st.session_state.exam_started_at:
                duration = int(time.time() - float(st.session_state.exam_started_at))
                limit = int(st.session_state.exam_limit_sec or 0)
                paper = (st.session_state.quiz_meta or {}).get("paper")
                region = (st.session_state.quiz_meta or {}).get("region")
                year = (st.session_state.quiz_meta or {}).get("year")
            result = save_submission(
                questions,
                st.session_state.answers,
                mode=st.session_state.mode,
                paper=paper,
                region=region,
                year=year,
                duration_sec=duration,
                timed_limit_sec=limit,
                time_spent=spent,
            )
            st.session_state.result = result
            st.session_state.page = "result"
            st.rerun()
    with col2:
        if st.button("清空本题答案", use_container_width=True):
            st.session_state.answers = {}
            st.rerun()


def render_bank_analysis(detail: dict[str, Any]) -> bool:
    analysis = (detail.get("analysis") or "").strip()
    if analysis:
        st.markdown("**题库解析**")
        st.write(analysis)
        return True
    st.caption("本题题库暂无解析。")
    return False


def render_on_demand_ai(
    detail: dict[str, Any], *, button_prefix: str, force_visible: bool = False
) -> None:
    qid = int(detail["question_id"])
    user_answer = str(detail.get("user_answer") or "")
    key = cache_key_for_one(qid, user_answer)
    one_map: dict[str, str] = st.session_state.one_explanations
    one_errors: dict[str, str] = st.session_state.one_errors
    has_bank = bool((detail.get("analysis") or "").strip())
    label = "补充 AI 解析" if has_bank and not force_visible else "AI 解析"
    help_text = (
        "题库已有解析；仅在需要额外讲解时再调用 AI。"
        if has_bank
        else "题库无解析，可调用 AI 生成。"
    )
    if st.button(label, key=f"{button_prefix}_explain_{qid}", help=help_text):
        if key in one_map:
            st.toast("已使用缓存解析", icon="✅")
        else:
            with st.spinner("正在请求 AI 解析..."):
                resp = fetch_one_explanation(qid, user_answer)
            counts = dict(st.session_state.one_request_count)
            counts[key] = int(counts.get(key, 0)) + 1
            st.session_state.one_request_count = counts
            if resp.get("ok"):
                one_map[key] = str(resp.get("text") or "")
                one_errors.pop(key, None)
            else:
                one_errors[key] = str(resp.get("error") or "AI 解析失败")
            st.session_state.one_explanations = one_map
            st.session_state.one_errors = one_errors
    if key in one_map:
        st.markdown("**AI 解析**")
        st.write(one_map[key])
    elif key in one_errors:
        st.warning(one_errors[key])


def render_result() -> None:
    result = st.session_state.result
    if not result:
        st.warning("还没有交卷记录。")
        return
    st.success(
        f"交卷成功！submission_id={result['submission_id']}｜"
        f"得分 {result['score']} 分｜正确 {result['correct']}/{result['total']}"
    )
    st.caption(
        f"交卷时间：{result['created_at']}｜模式：{result.get('mode')}｜"
        f"已同步错题本 {result.get('wrong_book_upserted', 0)} 题"
    )
    if result.get("mode") == "exam":
        st.info(
            f"套卷用时 {format_duration(result.get('duration_sec'))} / "
            f"参考时长 {format_duration(result.get('timed_limit_sec'))}｜"
            f"{result.get('region')} {result.get('year')} · {result.get('paper')}"
        )

    wrong = [d for d in result["details"] if not d["is_correct"]]
    right = [d for d in result["details"] if d["is_correct"]]
    st.subheader(f"错题列表（{len(wrong)}）")
    if not wrong:
        st.write("全部正确，没有错题。")
    else:
        for i, d in enumerate(wrong, start=1):
            with st.container(border=True):
                st.markdown(
                    f"**错题 {i}** · `{d.get('module')}` / `{d.get('category')}` · "
                    f"question_id={d['question_id']}"
                )
                st.write(d.get("stem") or "")
                for opt in d.get("options") or []:
                    st.write(f"- {option_label(opt)}")
                st.error(f"你的答案：{d['user_answer']}")
                st.info(f"正确答案：{d['correct_answer']}")
                if d.get("time_spent_sec"):
                    st.caption(f"本题停留约 {format_duration(d['time_spent_sec'])}")
                has_bank = render_bank_analysis(d)
                render_on_demand_ai(
                    d, button_prefix=f"wrong_{i}", force_visible=not has_bank
                )

    with st.expander(f"全部题目（含正确 {len(right)}）", expanded=False):
        for i, d in enumerate(result["details"], start=1):
            mark = "✅" if d["is_correct"] else "❌"
            st.markdown(
                f"{mark} **#{i}** id={d['question_id']} 你选 {d['user_answer']} / "
                f"正确 {d['correct_answer']}"
            )
            has_bank = render_bank_analysis(d)
            render_on_demand_ai(
                d, button_prefix=f"all_{i}", force_visible=not has_bank
            )

    if st.button("再做一组 / 返回选题", type="primary"):
        st.session_state.result = None
        st.session_state.answers = {}
        st.session_state.questions = []
        st.session_state.page = "quiz"
        st.session_state.exam_started_at = None
        st.rerun()


def render_wrong_book() -> None:
    st.subheader("错题本（持久化）")
    st.caption("交卷后自动入库；可在此写笔记、标记已掌握。")
    modules = ["(全部)"]
    with open_user() as conn:
        modules += [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT module FROM wrong_book WHERE module IS NOT NULL AND module!='' ORDER BY module"
            )
        ]
    c1, c2 = st.columns(2)
    status = c1.selectbox("状态", ["open", "mastered", "(全部)"], index=0, key="wb_status")
    module = c2.selectbox("模块", modules, index=0, key="wb_module")
    rows = list_wrong_book(status=status, module=module)
    st.write(f"共 {len(rows)} 条")
    if not rows:
        st.info("暂无错题。去做几套题后会自动出现在这里。")
        return

    for i, row in enumerate(rows, start=1):
        qid = int(row["question_id"])
        with st.container(border=True):
            st.markdown(
                f"**#{i}** `{row.get('module')}` / `{row.get('category')}` · "
                f"id={qid} · 错 {row.get('wrong_count')} 次 · 状态={row.get('status')}"
            )
            st.write(row.get("stem") or "")
            try:
                opts = json.loads(row.get("options_json") or "[]")
            except json.JSONDecodeError:
                opts = []
            for opt in opts:
                st.write(f"- {option_label(opt)}")
            st.error(f"最近作答：{row.get('last_user_answer')}")
            st.info(f"正确答案：{row.get('correct_answer')}")
            if row.get("analysis"):
                with st.expander("题库解析", expanded=False):
                    st.write(row["analysis"])

            note_key = f"wb_note_{qid}"
            note_val = st.text_area(
                "我的笔记",
                value=row.get("note") or "",
                key=note_key,
                height=90,
            )
            b1, b2, b3 = st.columns(3)
            if b1.button("保存笔记", key=f"wb_save_{qid}"):
                update_wrong_note(qid, note_val)
                st.success("笔记已保存")
            if b2.button("标记已掌握", key=f"wb_ok_{qid}"):
                update_wrong_status(qid, "mastered")
                st.rerun()
            if b3.button("重新打开", key=f"wb_open_{qid}"):
                update_wrong_status(qid, "open")
                st.rerun()

            # 按需 AI：构造成 detail
            detail = {
                "question_id": qid,
                "user_answer": row.get("last_user_answer") or "",
                "analysis": row.get("analysis") or "",
            }
            has_bank = bool((row.get("analysis") or "").strip())
            render_on_demand_ai(
                detail, button_prefix=f"wb_{i}", force_visible=not has_bank
            )


def render_plan_and_chat() -> None:
    st.subheader("学习规划与对话辅导")
    st.caption("读取做题统计、套卷用时、错题本趋势、学习者档案与历史对话。")
    days = st.number_input(
        "规划天数",
        min_value=1,
        max_value=180,
        value=int(st.session_state.get("study_plan_days") or 7),
        step=1,
    )
    st.session_state.study_plan_days = int(days)
    if st.button("生成阶段性学习规划", type="primary"):
        with st.spinner("正在基于统计与知识库生成规划..."):
            resp = fetch_study_plan(int(days))
        if resp.get("ok"):
            st.session_state.study_plan = resp.get("plan")
            st.session_state.study_plan_error = None
            save_study_plan(int(days), str(resp.get("plan") or ""), source="plan")
        else:
            st.session_state.study_plan_error = resp.get("error")
    if st.session_state.get("study_plan_error"):
        st.warning(st.session_state.study_plan_error)
    if st.session_state.get("study_plan"):
        st.markdown(st.session_state.study_plan)

    st.markdown("---")
    st.markdown("### 辅导对话框（持久化）")
    for m in load_chat_messages(40):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            st.caption(m["created_at"])
    prompt = st.chat_input("例如：河南资料分析总超时，两周怎么安排？")
    if prompt:
        append_chat_message("user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                resp = fetch_chat_reply(prompt)
            if resp.get("ok"):
                reply = str(resp.get("reply") or "")
                st.markdown(reply)
                append_chat_message("assistant", reply)
            else:
                err = str(resp.get("error") or "对话失败")
                st.warning(err)
                append_chat_message("assistant", f"[系统] {err}")


# ---------------------------------------------------------------------------
# UI: 申论模块（独立）
# ---------------------------------------------------------------------------

def render_shenlun_module() -> None:
    st.subheader("申论模块")
    st.caption(
        "题源来自开源 SLB 申论真题库（本地导入）。当前不做自动评分，聚焦读材料、审题与作答练习。"
    )
    if not SHENLUN_DB.exists():
        st.error("未找到 `data/shenlun.db`，请先运行：`python3 scripts/prepare_shenlun.py`")
        return

    focus_only = st.checkbox("只看国考 + 河南（推荐）", value=True, key="sl_focus")
    papers = list_shenlun_papers(focus_only=focus_only)
    if not papers:
        st.warning("没有可用申论卷。")
        return

    labels = [
        f"[{p.get('province') or p.get('exam_type')}] {p['year']} · {p['title']} "
        f"（材料{p['material_count']} / 题{p['question_count']}）"
        for p in papers
    ]
    idx = st.selectbox(
        "选择申论试卷",
        options=list(range(len(labels))),
        format_func=lambda i: labels[i],
        key="sl_paper_idx",
    )
    if st.button("打开该卷", type="primary"):
        st.session_state.shenlun_paper_id = int(papers[int(idx)]["id"])
        st.session_state.shenlun_answers = {}
        st.rerun()

    paper_id = st.session_state.get("shenlun_paper_id")
    if not paper_id:
        st.info("请选择试卷后点击「打开该卷」。")
        # 展示库存概况
        with open_shenlun() as conn:
            total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            henan = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE province LIKE '%河南%'"
            ).fetchone()[0]
            guokao = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE province LIKE '%国考%' OR exam_type='国考'"
            ).fetchone()[0]
        st.write(f"本地申论库：共 **{total}** 套｜河南 **{henan}**｜国考相关 **{guokao}**")
        return

    paper = get_shenlun_paper(int(paper_id))
    if not paper:
        st.error("试卷不存在。")
        return

    st.markdown(
        f"### {paper.get('title')}\n"
        f"`{paper.get('province')}` · {paper.get('year')} · {paper.get('exam_type')}"
    )
    with st.expander("给定资料", expanded=True):
        materials = paper.get("materials") or []
        if isinstance(materials, list):
            for i, m in enumerate(materials, start=1):
                st.markdown(f"**材料 {i}**")
                st.write(m if isinstance(m, str) else json.dumps(m, ensure_ascii=False))
        else:
            st.write(str(materials))

    st.markdown("### 作答要求")
    answers = dict(st.session_state.get("shenlun_answers") or {})
    for i, q in enumerate(paper.get("questions_list") or [], start=1):
        qid = str(q.get("qid") or f"q{i}")
        with st.container(border=True):
            st.markdown(
                f"**第 {i} 题** · `{q.get('type') or '未标注'}` · "
                f"{q.get('score_max') or '?'} 分 · {q.get('word_limit') or ''}"
            )
            st.write(q.get("stem") or "")
            val = st.text_area(
                "作答区",
                value=answers.get(qid, ""),
                key=f"sl_ans_{paper_id}_{qid}",
                height=160,
            )
            answers[qid] = val
            if st.button("保存本题作答", key=f"sl_save_{paper_id}_{qid}"):
                save_shenlun_attempt(int(paper_id), qid, val)
                st.success("已写入 user.db（不做自动评分）")
    st.session_state.shenlun_answers = answers

    if st.button("保存本卷全部作答"):
        for qid, text in answers.items():
            if str(text).strip():
                save_shenlun_attempt(int(paper_id), qid, str(text))
        st.success("本卷已保存到 user.db")


# ---------------------------------------------------------------------------
# Sidebar / main
# ---------------------------------------------------------------------------

def render_memory_sidebar() -> None:
    st.sidebar.markdown("---")
    st.sidebar.subheader("学习者档案（持久化）")
    profile = load_learner_profile()
    name = st.sidebar.text_input(
        "称呼", value=profile.get("display_name") or "自用考生", key="prof_name"
    )
    target = st.sidebar.text_input(
        "目标考试",
        value=profile.get("target_exam") or "国考+河南省考",
        key="prof_target",
    )
    focus = st.sidebar.text_input(
        "重点地区（逗号分隔）",
        value=profile.get("focus_regions") or "国考,河南",
        key="prof_focus",
    )
    notes = st.sidebar.text_area(
        "个人备注 / 弱项备忘",
        value=profile.get("notes") or "",
        key="prof_notes",
        height=80,
    )
    if st.sidebar.button("保存档案", use_container_width=True):
        save_learner_profile(name, target, focus, notes)
        st.sidebar.success("已写入 user.db")

    recents = recent_submissions(5)
    if recents:
        st.sidebar.caption("最近行测交卷")
        for r in recents:
            tag = "卷" if r.get("mode") == "exam" else "练"
            st.sidebar.write(
                f"[{tag}] #{r['id']} {r['score']}分 "
                f"{r['correct']}/{r['total']} · {r.get('region') or ''}"
            )


def render_xingce_module() -> None:
    mode_ui = st.sidebar.radio(
        "行测做题模式",
        options=["练习模式", "套卷模拟"],
        index=0 if st.session_state.mode != "exam" else 1,
        key="xingce_mode_ui",
    )
    if mode_ui == "练习模式":
        render_practice_filters()
    else:
        render_exam_filters()

    tab_quiz, tab_result, tab_wrong, tab_plan = st.tabs(
        ["做题", "阅卷结果", "错题本", "学习规划 / 对话"]
    )
    with tab_quiz:
        render_quiz()
    with tab_result:
        if st.session_state.page == "result" or st.session_state.result:
            render_result()
        else:
            st.info("交卷后在这里查看得分、题库解析与（按需）AI 解析。")
    with tab_wrong:
        render_wrong_book()
    with tab_plan:
        render_plan_and_chat()


def main() -> None:
    st.set_page_config(page_title="考公助手（自用版）", layout="wide")
    st.title("考公助手（自用版）")
    st.caption("行测 / 申论分模块 · 套卷计时自动刷新 · 错题本笔记 · 国考/河南重点")

    init_session()
    ensure_user_db()

    subject = st.sidebar.radio(
        "大模块",
        options=["行测", "申论"],
        index=0 if st.session_state.subject != "shenlun" else 1,
        key="subject_ui",
    )
    st.session_state.subject = "shenlun" if subject == "申论" else "xingce"
    render_memory_sidebar()

    if st.session_state.subject == "xingce":
        if not KAOGONG_DB.exists():
            st.error(
                f"题库不存在：`{KAOGONG_DB}`\n\n请先运行：`bash scripts/prepare_kaogong.sh`"
            )
            st.stop()
        render_xingce_module()
    else:
        render_shenlun_module()


if __name__ == "__main__":
    main()
