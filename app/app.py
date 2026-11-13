#!/usr/bin/env python3
"""考公助手 Streamlit 前端（行测 / 申论分模块）。"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import unquote

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
KAOGONG_DB = ROOT / "data" / "kaogong.db"
SHENLUN_DB = ROOT / "data" / "shenlun.db"
USER_DB = ROOT / "data" / "user.db"
IMG_ROOT = ROOT / "data" / "90-图片"
AGENT_BASE = "http://127.0.0.1:8787"

DEFAULT_LIMIT = 10
DEFAULT_EXAM_MINUTES = 120
FOCUS_REGIONS = ("国考", "河南")
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
IMG_ATTR_RE = re.compile(
    r"""(\w+)\s*=\s*(?:['\"]([^'\"]*)['\"]|([^\s>]+))""",
    re.IGNORECASE,
)


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


def parse_options(raw: str | list | None) -> list[dict[str, Any]]:
    """兼容 DB 字符串与草稿里已反序列化的 list。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if not isinstance(raw, (str, bytes, bytearray)):
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


def resolve_image_path(src: str) -> Path | None:
    """把题库里的 /90-图片/... 映射到本地 data/90-图片/..."""
    if not src:
        return None
    raw = unquote(src.strip())
    # 常见形式：/90-图片/题目图/xxx.webp 或 90-图片/...
    marker = "90-图片/"
    idx = raw.find(marker)
    if idx >= 0:
        rel = raw[idx + len(marker) :]
    elif raw.startswith("/"):
        rel = raw.lstrip("/")
    else:
        rel = raw
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    path = (IMG_ROOT / rel).resolve()
    try:
        path.relative_to(IMG_ROOT.resolve())
    except ValueError:
        return None
    if path.exists():
        return path
    missing = IMG_ROOT / "__missing__.webp"
    return missing if missing.exists() else None


def parse_img_tag(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in IMG_ATTR_RE.finditer(tag):
        key = m.group(1).lower()
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        attrs[key] = val
    return attrs


def extract_images(html: str | None) -> list[dict[str, Any]]:
    """解析 HTML 中的图片，区分公式小图与题干大图。"""
    if not html:
        return []
    out: list[dict[str, Any]] = []
    for tag in IMG_TAG_RE.findall(html):
        attrs = parse_img_tag(tag)
        src = attrs.get("src") or ""
        path = resolve_image_path(src)
        if not path:
            continue
        width = None
        height = None
        try:
            if attrs.get("width"):
                width = int(re.sub(r"[^0-9]", "", attrs["width"]) or 0) or None
            if attrs.get("height"):
                height = int(re.sub(r"[^0-9]", "", attrs["height"]) or 0) or None
        except ValueError:
            width = height = None
        kind = "formula" if ("公式图" in src or attrs.get("flag") == "tex") else "figure"
        # 没有标注时，按文件尺寸启发式：很扁/很小的当公式
        if kind == "figure":
            try:
                from PIL import Image

                with Image.open(path) as im:
                    w, h = im.size
                if (h and h <= 48) or (w and h and w / max(h, 1) >= 4 and h <= 80):
                    kind = "formula"
                    width = width or w
                    height = height or h
            except Exception:
                pass
        out.append(
            {
                "path": path,
                "src": src,
                "kind": kind,
                "width": width,
                "height": height,
            }
        )
    return out


def extract_image_paths(html: str | None) -> list[Path]:
    return [img["path"] for img in extract_images(html)]


@st.cache_data(show_spinner=False, ttl=3600)
def _load_image_bytes(path_str: str) -> bytes:
    return Path(path_str).read_bytes()


def render_one_image(img: dict[str, Any], *, option_mode: bool = False) -> None:
    path: Path = img["path"]
    kind = img.get("kind") or "figure"
    width = img.get("width")
    try:
        if kind == "formula":
            # 公式/符号：按原始宽度显示，避免被拉成整行大图
            w = int(width or 0)
            if w <= 0:
                w = 80
            w = max(24, min(w, 280))
        elif option_mode:
            w = int(width or 220)
            w = max(80, min(w, 320))
        else:
            # 题干大图：限制最大宽度，避免撑爆页面
            w = int(width or 680)
            w = max(240, min(w, 760))
        # 缓存图片字节，减少整卷滚动时反复读盘
        st.image(_load_image_bytes(str(path)), width=w)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"图片加载失败：{path.name}（{exc}）")


def render_rich_content(html_or_text: str | None, *, fallback_text: str | None = None) -> None:
    """按文本块 + 图片交错渲染；公式小图不再被放大。"""
    raw = html_or_text or ""
    if "<img" not in raw.lower():
        text = fallback_text if fallback_text is not None else strip_html(raw)
        st.write(text or "(内容为空)")
        return

    parts = IMG_TAG_RE.split(raw)
    tags = IMG_TAG_RE.findall(raw)
    # parts[0], tag0, parts[1], tag1, ...
    for i, text_html in enumerate(parts):
        text = strip_html(text_html)
        if text:
            st.write(text)
        if i < len(tags):
            attrs = parse_img_tag(tags[i])
            src = attrs.get("src") or ""
            path = resolve_image_path(src)
            if not path:
                st.caption(f"[图片缺失: {escape(src)}]")
                continue
            width = None
            try:
                if attrs.get("width"):
                    width = int(re.sub(r"[^0-9]", "", attrs["width"]) or 0) or None
            except ValueError:
                width = None
            kind = "formula" if ("公式图" in src or attrs.get("flag") == "tex") else "figure"
            render_one_image(
                {"path": path, "src": src, "kind": kind, "width": width},
            )


def option_has_image(opt: dict[str, Any]) -> bool:
    html = str(opt.get("html") or opt.get("label") or "")
    return bool(extract_images(html)) or "<img" in html.lower()


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
    item["stem_html"] = item.get("stem") or ""
    item["material_html"] = item.get("material") or ""
    item["analysis_html"] = item.get("analysis") or ""
    item["stem_text"] = strip_html(item.get("stem"))
    item["material_text"] = strip_html(item.get("material"))
    item["analysis_text"] = strip_html(item.get("analysis"))
    item["stem_images"] = [str(p) for p in extract_image_paths(item.get("stem"))]
    item["has_render_image"] = bool(item["stem_images"]) or any(
        option_has_image(o) for o in item["options"]
    )
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

def upsert_wrong_book(
    details: list[dict[str, Any]],
    *,
    submission_id: int | None = None,
    paper: str | None = None,
    region: str | None = None,
    year: str | None = None,
) -> int:
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
            paper_v = d.get("paper") or paper
            region_v = d.get("region") or region
            year_v = d.get("year") or year
            if row:
                conn.execute(
                    """
                    UPDATE wrong_book
                    SET last_user_answer=?, wrong_count=wrong_count+1,
                        last_wrong_at=?, updated_at=?, status='open',
                        analysis=COALESCE(NULLIF(?, ''), analysis),
                        paper=COALESCE(?, paper),
                        region=COALESCE(?, region),
                        year=COALESCE(?, year),
                        submission_id=COALESCE(?, submission_id)
                    WHERE question_id=?
                    """,
                    (
                        d.get("user_answer"),
                        now,
                        now,
                        d.get("analysis_html") or d.get("analysis") or "",
                        paper_v,
                        region_v,
                        year_v,
                        submission_id,
                        qid,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO wrong_book(
                        question_id, qid, module, category, stem, options_json,
                        correct_answer, last_user_answer, analysis, wrong_count,
                        note, status, first_wrong_at, last_wrong_at, updated_at,
                        paper, region, year, submission_id, starred
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        qid,
                        d.get("qid"),
                        d.get("module"),
                        d.get("category"),
                        d.get("stem_html") or d.get("stem"),
                        opts,
                        d.get("correct_answer"),
                        d.get("user_answer"),
                        d.get("analysis_html") or d.get("analysis") or "",
                        1,
                        "",
                        "open",
                        now,
                        now,
                        now,
                        paper_v,
                        region_v,
                        year_v,
                        submission_id,
                        0,
                    ),
                )
            n += 1
        conn.commit()
    return n


def list_wrong_book(
    status: str | None = "open",
    module: str | None = None,
    paper: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if status and status != "(全部)":
        clauses.append("status = ?")
        params.append(status)
    if module and module != "(全部)":
        clauses.append("module = ?")
        params.append(module)
    if paper and paper != "(全部)":
        clauses.append("COALESCE(paper, '(练习/未标注)') = ?")
        params.append(paper)
    sql = f"""
        SELECT * FROM wrong_book
        WHERE {' AND '.join(clauses)}
        ORDER BY last_wrong_at DESC, id DESC
    """
    with open_user() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_wrong_question_index(
    paper: str,
    status: str | None = "open",
) -> list[dict[str, Any]]:
    """二级目录：仅取题号摘要，不取题干/选项，保证侧栏轻量。"""
    clauses = ["COALESCE(paper, '(练习/未标注)') = ?"]
    params: list[Any] = [paper]
    if status and status != "(全部)":
        clauses.append("status = ?")
        params.append(status)
    sql = f"""
        SELECT question_id, module, category, status, wrong_count, starred,
               last_user_answer, correct_answer, last_wrong_at
        FROM wrong_book
        WHERE {' AND '.join(clauses)}
        ORDER BY last_wrong_at DESC, id DESC
    """
    with open_user() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_wrong_question(question_id: int) -> dict[str, Any] | None:
    """按需取单题详情。"""
    with open_user() as conn:
        row = conn.execute(
            "SELECT * FROM wrong_book WHERE question_id=?",
            (int(question_id),),
        ).fetchone()
    return dict(row) if row else None


def wrong_book_paper_catalog() -> list[dict[str, Any]]:
    """错题目录：按试卷汇总。"""
    sql = """
        SELECT
          COALESCE(paper, '(练习/未标注)') AS paper,
          COALESCE(region, '') AS region,
          COALESCE(year, '') AS year,
          COUNT(*) AS wrong_n,
          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_n,
          MAX(last_wrong_at) AS last_wrong_at
        FROM wrong_book
        GROUP BY COALESCE(paper, '(练习/未标注)'), COALESCE(region,''), COALESCE(year,'')
        ORDER BY last_wrong_at DESC
    """
    with open_user() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def set_question_starred(question_id: int, starred: bool, note: str = "") -> None:
    ensure_user_db()
    now = now_iso()
    qid = int(question_id)
    with open_user() as conn:
        if starred:
            conn.execute(
                """
                INSERT INTO starred_questions(question_id, note, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(question_id) DO UPDATE SET
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (qid, note, now, now),
            )
            # 若已在错题本，同步标记
            conn.execute(
                "UPDATE wrong_book SET starred=1, updated_at=? WHERE question_id=?",
                (now, qid),
            )
        else:
            conn.execute("DELETE FROM starred_questions WHERE question_id=?", (qid,))
            conn.execute(
                "UPDATE wrong_book SET starred=0, updated_at=? WHERE question_id=?",
                (now, qid),
            )
        conn.commit()


def load_starred_ids() -> set[int]:
    ensure_user_db()
    with open_user() as conn:
        rows = conn.execute("SELECT question_id FROM starred_questions").fetchall()
    return {int(r[0]) for r in rows}


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
                "stem_html": q.get("stem_html") or q.get("stem") or "",
                "module": q.get("module"),
                "category": q.get("category"),
                "paper": q.get("paper") or paper,
                "region": q.get("region") or region,
                "year": q.get("year") or year,
                "options": q.get("options") or [],
                "user_answer": user_ans or "(未作答)",
                "correct_answer": right or "(无答案)",
                "is_correct": bool(is_correct),
                "analysis": q.get("analysis_text") or strip_html(q.get("analysis")),
                "analysis_html": q.get("analysis_html") or q.get("analysis") or "",
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

    wrong_n = upsert_wrong_book(
        detail,
        submission_id=submission_id,
        paper=paper,
        region=region,
        year=year,
    )
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


def save_quiz_draft(payload: dict[str, Any]) -> None:
    """实时保存当前作答草稿，防止页面关闭丢失。"""
    ensure_user_db()
    raw = json.dumps(payload, ensure_ascii=False)
    with open_user() as conn:
        conn.execute(
            """
            INSERT INTO quiz_drafts(id, draft_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                draft_json=excluded.draft_json,
                updated_at=excluded.updated_at
            """,
            (raw, now_iso()),
        )
        conn.commit()


def load_quiz_draft() -> dict[str, Any] | None:
    ensure_user_db()
    with open_user() as conn:
        row = conn.execute(
            "SELECT draft_json, updated_at FROM quiz_drafts WHERE id=1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["draft_json"])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data["_updated_at"] = row["updated_at"]
        return data
    return None


def clear_quiz_draft() -> None:
    ensure_user_db()
    with open_user() as conn:
        conn.execute("DELETE FROM quiz_drafts WHERE id=1")
        conn.commit()


def build_draft_payload() -> dict[str, Any]:
    questions = st.session_state.get("questions") or []
    # 草稿只存必要字段，避免 user.db 膨胀
    slim_qs = [
        {
            "id": int(q["id"]),
            "qid": q.get("qid"),
            "module": q.get("module"),
            "category": q.get("category"),
            "region": q.get("region"),
            "year": q.get("year"),
            "paper": q.get("paper"),
            "stem": q.get("stem_html") or q.get("stem"),
            "options": q.get("options") or [],
            "answer": q.get("answer"),
            "analysis": q.get("analysis_html") or q.get("analysis"),
            "material": q.get("material_html") or q.get("material"),
            "has_image": q.get("has_image"),
        }
        for q in questions
    ]
    return {
        "mode": st.session_state.get("mode"),
        "quiz_meta": st.session_state.get("quiz_meta") or {},
        "answers": {str(k): v for k, v in (st.session_state.get("answers") or {}).items()},
        "time_spent": {str(k): v for k, v in (st.session_state.get("time_spent") or {}).items()},
        "exam_started_at": st.session_state.get("exam_started_at"),
        "exam_limit_sec": st.session_state.get("exam_limit_sec"),
        "current_q_idx": st.session_state.get("current_q_idx", 0),
        "questions": slim_qs,
    }


def persist_draft() -> None:
    if not st.session_state.get("questions"):
        return
    save_quiz_draft(build_draft_payload())


def restore_draft_if_needed() -> bool:
    """若当前无题目但库中有草稿，则恢复。"""
    if st.session_state.get("questions"):
        return False
    draft = load_quiz_draft()
    if not draft or not draft.get("questions"):
        return False
    qs = []
    for raw in draft["questions"]:
        item = dict(raw)
        # 草稿精简字段 -> 正常题目字段
        item["stem"] = raw.get("stem") or ""
        item["analysis"] = raw.get("analysis") or ""
        item["material"] = raw.get("material") or ""
        item["options"] = raw.get("options") or []
        qs.append(_normalize_question(item))
    st.session_state.questions = qs
    st.session_state.answers = {
        int(k): v for k, v in (draft.get("answers") or {}).items()
    }
    st.session_state.time_spent = {
        int(k): int(v) for k, v in (draft.get("time_spent") or {}).items()
    }
    st.session_state.mode = draft.get("mode") or "practice"
    st.session_state.quiz_meta = draft.get("quiz_meta") or {}
    st.session_state.exam_started_at = draft.get("exam_started_at")
    st.session_state.exam_limit_sec = draft.get("exam_limit_sec") or DEFAULT_EXAM_MINUTES * 60
    st.session_state.current_q_idx = int(draft.get("current_q_idx") or 0)
    st.session_state.page = "quiz"
    st.session_state.draft_restored = True
    return True


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
        "current_q_idx": 0,
        "submit_flash": None,
        "draft_restored": False,
        "shenlun_paper_id": None,
        "shenlun_answers": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def option_label(opt: dict[str, Any]) -> str:
    key = opt.get("key") or "?"
    if option_has_image(opt):
        return f"{key}. （见图）"
    label = strip_html(opt.get("label") or opt.get("html") or "")
    return f"{key}. {label}" if label else str(key)


def render_option_visual(opt: dict[str, Any]) -> None:
    key = (opt.get("key") or "?").upper()
    html = str(opt.get("html") or opt.get("label") or "")
    imgs = extract_images(html)
    if not imgs:
        return
    st.caption(f"选项 {key}")
    for img in imgs:
        render_one_image(img, option_mode=True)


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
    st.session_state.current_q_idx = 0
    st.session_state.submit_flash = None
    st.session_state.draft_restored = False
    st.session_state.exam_started_at = time.time() if mode == "exam" else time.time()
    st.session_state.exam_limit_sec = timed_limit_sec or DEFAULT_EXAM_MINUTES * 60
    st.session_state.one_explanations = {}
    st.session_state.one_errors = {}
    # 新开一套题时清掉旧草稿，并立即写入新草稿
    clear_quiz_draft()
    persist_draft()


# ---------------------------------------------------------------------------
# UI: 筛选 / 计时 / 做题
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
    focus_only = st.sidebar.checkbox(
        "只看国考 + 河南省考（推荐）", value=True, key="exam_focus"
    )
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


def on_answer_change(qid: int) -> None:
    """radio 回调：立即写入答案并保存草稿。"""
    key = f"radio_{qid}"
    selected = st.session_state.get(key)
    opts = None
    for q in st.session_state.get("questions") or []:
        if int(q["id"]) == int(qid):
            opts = q.get("options") or []
            break
    if not opts:
        return
    choices = [option_label(o) for o in opts]
    keys = [(o.get("key") or "").upper() for o in opts]
    if selected == "(请选择)" or selected not in choices:
        st.session_state.answers.pop(int(qid), None)
    else:
        st.session_state.answers[int(qid)] = keys[choices.index(selected)]
    persist_draft()


@st.fragment(run_every=1)
def render_sticky_header_timer() -> None:
    """顶部常驻迷你计时条。

    说明：Streamlit 对 position:fixed 支持不稳定，这里用 fragment + 紧凑 caption，
    放在做题区最上方，滚动时仍可通过页面顶部区域持续看到，且几乎不占高度。
    """
    if not st.session_state.get("questions") or not st.session_state.get("exam_started_at"):
        return
    started = float(st.session_state.exam_started_at)
    elapsed = int(time.time() - started)
    answered = sum(
        1 for q in st.session_state.questions if st.session_state.answers.get(int(q["id"]))
    )
    total = len(st.session_state.questions)
    if st.session_state.get("mode") == "exam":
        remain = int(st.session_state.exam_limit_sec or 0) - elapsed
        remain_txt = format_duration(remain) if remain >= 0 else "已超时"
        mode_txt = "套卷"
    else:
        remain_txt = "-"
        mode_txt = "练习"
    # 单行紧凑条，不用 metric，避免占高
    st.markdown(
        f"<div style='padding:4px 10px;border:1px solid rgba(120,120,120,.35);"
        f"border-radius:8px;background:rgba(40,44,56,.08);font-size:13px;'>"
        f"⏱ <b>{mode_txt}</b>"
        f"&nbsp;&nbsp;已用 <b>{format_duration(elapsed)}</b>"
        f"&nbsp;&nbsp;剩余 <b>{remain_txt}</b>"
        f"&nbsp;&nbsp;进度 <b>{answered}/{total}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_quiz() -> None:
    questions: list[dict[str, Any]] = st.session_state.questions
    if not questions:
        draft = load_quiz_draft()
        if draft and draft.get("questions"):
            st.info(
                f"检测到未交卷草稿（更新于 {draft.get('_updated_at')}）。可恢复继续作答。"
            )
            b1, b2 = st.columns(2)
            if b1.button("恢复未完成作答", type="primary", use_container_width=True):
                restore_draft_if_needed()
                st.rerun()
            if b2.button("丢弃草稿", use_container_width=True):
                clear_quiz_draft()
                st.rerun()
        else:
            st.info("请在左侧选择练习筛选或套卷，开始做题。")
        return

    if st.session_state.get("draft_restored"):
        st.success("已恢复未交卷草稿，可继续作答。")
        st.session_state.draft_restored = False

    # 页眉常驻计时（固定顶部，占高很小）
    render_sticky_header_timer()

    meta = st.session_state.quiz_meta or {}
    if st.session_state.mode == "exam":
        st.markdown(
            f"**套卷模拟**：`{meta.get('region')}` {meta.get('year')} · {meta.get('paper')}"
        )
        st.caption("整卷浏览作答；答案自动保存。参考时长到点不强制交卷。")
    else:
        st.caption(
            f"练习模式：{len(questions)} 道｜"
            f"年份={meta.get('year','(不限)')} / 地区={meta.get('region','(不限)')} / "
            f"模块={meta.get('module','(不限)')} / 题型={meta.get('category','(不限)')}"
        )

    answered = sum(1 for q in questions if st.session_state.answers.get(int(q["id"])))
    st.write(f"已作答 {answered} / {len(questions)}（草稿自动保存）")

    starred_ids = load_starred_ids()
    for i, q in enumerate(questions, start=1):
        qid = int(q["id"])
        is_star = qid in starred_ids
        with st.container(border=True):
            head_l, head_r = st.columns([4, 1])
            with head_l:
                st.markdown(
                    f"**第 {i} 题** · `{q.get('module')}` / `{q.get('category')}` · "
                    f"{q.get('region')} {q.get('year')} · id={qid}"
                    + (" · ⭐重点" if is_star else "")
                )
            with head_r:
                star_label = "取消重点" if is_star else "标为重点"
                if st.button(star_label, key=f"star_{qid}", use_container_width=True):
                    set_question_starred(qid, not is_star)
                    st.rerun()

            if q.get("material_html") or q.get("material_text"):
                with st.expander("材料", expanded=False):
                    render_rich_content(
                        q.get("material_html"),
                        fallback_text=q.get("material_text"),
                    )
            render_rich_content(q.get("stem_html"), fallback_text=q.get("stem_text"))

            opts = q.get("options") or []
            choices = [option_label(o) for o in opts]
            if not choices:
                st.warning("该题无选项数据，跳过。")
                continue

            if any(option_has_image(o) for o in opts):
                cols = st.columns(min(4, len(opts)) or 1)
                for j, opt in enumerate(opts):
                    with cols[j % len(cols)]:
                        render_option_visual(opt)

            keys = [(o.get("key") or "").upper() for o in opts]
            radio_options = ["(请选择)"] + choices
            current = st.session_state.answers.get(qid)
            default_idx = keys.index(current) + 1 if current in keys else 0
            st.radio(
                f"选项##{qid}",
                options=radio_options,
                index=default_idx,
                key=f"radio_{qid}",
                label_visibility="collapsed",
                on_change=on_answer_change,
                args=(qid,),
            )

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("交卷批分", type="primary", use_container_width=True):
            for q in questions:
                qid = int(q["id"])
                selected = st.session_state.get(f"radio_{qid}")
                opts = q.get("options") or []
                choices = [option_label(o) for o in opts]
                keys = [(o.get("key") or "").upper() for o in opts]
                if selected in choices:
                    st.session_state.answers[qid] = keys[choices.index(selected)]
                elif selected == "(请选择)":
                    st.session_state.answers.pop(qid, None)

            duration = None
            limit = None
            paper = region = year = None
            if st.session_state.exam_started_at:
                duration = int(time.time() - float(st.session_state.exam_started_at))
            if st.session_state.mode == "exam":
                limit = int(st.session_state.exam_limit_sec or 0)
                paper = (st.session_state.quiz_meta or {}).get("paper")
                region = (st.session_state.quiz_meta or {}).get("region")
                year = (st.session_state.quiz_meta or {}).get("year")
            answered_n = sum(
                1 for q in questions if st.session_state.answers.get(int(q["id"]))
            )
            result = save_submission(
                questions,
                st.session_state.answers,
                mode=st.session_state.mode,
                paper=paper,
                region=region,
                year=year,
                duration_sec=duration,
                timed_limit_sec=limit,
                time_spent={},
            )
            clear_quiz_draft()
            st.session_state.result = result
            st.session_state.page = "result"
            st.session_state.submit_flash = {
                "score": result["score"],
                "correct": result["correct"],
                "total": result["total"],
                "unanswered": len(questions) - answered_n,
                "submission_id": result["submission_id"],
                "mode": result.get("mode"),
                "paper": result.get("paper"),
            }
            st.balloons()
            st.rerun()
    with c2:
        if st.button("保存进度（不交卷）", use_container_width=True):
            persist_draft()
            st.success("进度已保存，关闭页面后可恢复。")
    with c3:
        if st.button("清空全部答案", use_container_width=True):
            st.session_state.answers = {}
            for q in questions:
                st.session_state.pop(f"radio_{int(q['id'])}", None)
            persist_draft()
            st.rerun()

def render_bank_analysis(detail: dict[str, Any]) -> bool:
    analysis_html = (detail.get("analysis_html") or detail.get("analysis") or "").strip()
    analysis_text = (detail.get("analysis_text") or strip_html(analysis_html)).strip()
    if analysis_html or analysis_text:
        st.markdown("**题库解析**")
        render_rich_content(analysis_html, fallback_text=analysis_text)
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
    flash = st.session_state.get("submit_flash")
    if flash:
        st.success(
            f"✅ 交卷成功！得分 {flash.get('score')} 分｜"
            f"正确 {flash.get('correct')}/{flash.get('total')}｜"
            f"未作答 {flash.get('unanswered')}｜"
            f"submission_id={flash.get('submission_id')}"
        )
        st.toast("交卷成功，已写入成绩与错题本", icon="✅")
        st.session_state.submit_flash = None

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
                render_rich_content(d.get("stem_html"), fallback_text=d.get("stem"))
                for opt in d.get("options") or []:
                    st.write(f"- {option_label(opt)}")
                    render_option_visual(opt)
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
    st.subheader("错题本")
    st.caption("右侧目录：一级套卷 / 二级题号。仅在选中题号后加载详情，保证流畅。")

    catalog = wrong_book_paper_catalog()
    if not catalog:
        st.info("暂无错题。交卷后会按试卷自动归档。")
        return

    left, right = st.columns([3.2, 1.35], gap="medium")

    with right:
        st.markdown("#### 目录")
        status = st.selectbox(
            "状态",
            ["open", "mastered", "(全部)"],
            index=0,
            key="wb_status_side",
        )
        paper_labels: list[str] = []
        paper_map: dict[str, dict[str, Any]] = {}
        for c in catalog:
            label = (
                f"{c.get('region') or ''} {c.get('year') or ''} · {c['paper']} "
                f"（未掌握{c['open_n']}/共{c['wrong_n']}）"
            ).strip()
            paper_labels.append(label)
            paper_map[label] = c

        current_paper = st.session_state.get("wb_selected_paper")
        default_idx = 0
        for i, c in enumerate(catalog):
            if c["paper"] == current_paper:
                default_idx = i
                break
        selected_label = st.radio(
            "一级：套卷",
            options=paper_labels,
            index=default_idx,
            key="wb_paper_radio",
        )
        selected_paper = paper_map[selected_label]["paper"]
        if selected_paper != current_paper:
            st.session_state.wb_selected_paper = selected_paper
            st.session_state.wb_selected_qid = None

        index_rows = list_wrong_question_index(selected_paper, status=status)
        if not index_rows:
            st.info("该套卷在当前状态下无错题。")
        else:
            qid_options = []
            for i, r in enumerate(index_rows):
                star = "⭐" if int(r.get("starred") or 0) == 1 else ""
                qid_options.append(
                    f"#{i+1} · id={r['question_id']} · "
                    f"{r.get('module') or ''}/{r.get('category') or ''} · "
                    f"错{r.get('wrong_count')}{star}"
                )
            selected_qid = st.session_state.get("wb_selected_qid")
            default_q = 0
            if selected_qid is not None:
                for i, r in enumerate(index_rows):
                    if int(r["question_id"]) == int(selected_qid):
                        default_q = i
                        break
            choice = st.radio(
                "二级：题号",
                options=qid_options,
                index=default_q,
                key=f"wb_qid_radio_{selected_paper}_{status}",
            )
            qid = int(choice.split("id=")[1].split(" ")[0])
            st.session_state.wb_selected_qid = qid
            st.caption(f"目录 {len(index_rows)} 题（不含题干，轻量查询）")

    with left:
        qid = st.session_state.get("wb_selected_qid")
        if not qid:
            st.info("请在右侧选择套卷和题号。")
            return
        row = get_wrong_question(int(qid))
        if not row:
            st.warning("题目不存在或已删除。")
            return

        starred_ids = load_starred_ids()
        star = "⭐" if (int(qid) in starred_ids or int(row.get("starred") or 0) == 1) else ""
        st.markdown(
            f"### {star} 错题详情 · id={qid}\n"
            f"`{row.get('module')}` / `{row.get('category')}` · 错 {row.get('wrong_count')} 次 · {row.get('status')}"
        )
        st.caption(
            f"试卷：{row.get('region') or ''} {row.get('year') or ''} · "
            f"{row.get('paper') or '(练习/未标注)'} · submission={row.get('submission_id')}"
        )

        stem_text = strip_html(row.get("stem"))
        st.write(stem_text or "(无题干)")
        if st.checkbox("显示配图/选项图", key=f"wb_full_{qid}"):
            render_rich_content(row.get("stem"), fallback_text=stem_text)
            try:
                opts = json.loads(row.get("options_json") or "[]")
            except json.JSONDecodeError:
                opts = []
            for opt in opts:
                st.write(f"- {option_label(opt)}")
                render_option_visual(opt)
        else:
            try:
                opts = json.loads(row.get("options_json") or "[]")
            except json.JSONDecodeError:
                opts = []
            for opt in opts:
                st.write(f"- {option_label(opt)}")

        st.error(f"最近作答：{row.get('last_user_answer')}")
        st.info(f"正确答案：{row.get('correct_answer')}")

        if row.get("analysis") and st.checkbox("显示题库解析", key=f"wb_ana_{qid}"):
            render_rich_content(
                row.get("analysis"),
                fallback_text=strip_html(row.get("analysis")),
            )

        note_val = st.text_area(
            "我的笔记",
            value=row.get("note") or "",
            key=f"wb_note_{qid}",
            height=90,
        )
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("保存笔记", key=f"wb_save_{qid}"):
            update_wrong_note(int(qid), note_val)
            st.success("笔记已保存")
        if b2.button("已掌握", key=f"wb_ok_{qid}"):
            update_wrong_status(int(qid), "mastered")
            st.rerun()
        if b3.button("重开", key=f"wb_open_{qid}"):
            update_wrong_status(int(qid), "open")
            st.rerun()
        star_label = "取消重点" if int(qid) in starred_ids else "标为重点"
        if b4.button(star_label, key=f"wb_star_{qid}"):
            set_question_starred(int(qid), int(qid) not in starred_ids, note=note_val)
            st.rerun()

        if st.checkbox("需要 AI 解析", key=f"wb_ai_toggle_{qid}"):
            detail = {
                "question_id": int(qid),
                "user_answer": row.get("last_user_answer") or "",
                "analysis": row.get("analysis") or "",
            }
            has_bank = bool((row.get("analysis") or "").strip())
            render_on_demand_ai(
                detail, button_prefix=f"wb_{qid}", force_visible=not has_bank
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

    # 交卷后自动切到阅卷页，避免用户以为没交上
    default_tab = 0
    if st.session_state.get("page") == "result" or st.session_state.get("result"):
        default_tab = 1
    tab_quiz, tab_result, tab_wrong, tab_plan = st.tabs(
        ["做题", "阅卷结果", "错题本", "学习规划 / 对话"]
    )
    # Streamlit tabs 无法直接指定默认激活；用结果页提示条补偿
    if default_tab == 1:
        st.info("已交卷：请查看上方「阅卷结果」页签。")
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
    st.caption("整卷浏览 · 顶部迷你计时 · 草稿自动保存 · 错题右侧二级目录")

    init_session()
    ensure_user_db()
    # 页面刷新后自动尝试恢复未交卷草稿（不强制）
    if not st.session_state.get("questions"):
        restore_draft_if_needed()

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
        if not IMG_ROOT.exists():
            st.warning(
                f"未找到配图目录 `{IMG_ROOT}`。含图题将无法显示图片。"
                "请运行 `bash scripts/prepare_kaogong.sh` 同步 `90-图片/`。"
            )
        render_xingce_module()
    else:
        render_shenlun_module()


if __name__ == "__main__":
    main()
