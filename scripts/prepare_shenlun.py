#!/usr/bin/env python3
"""从开源 SLB 申论真题 Markdown 构建本地 data/shenlun.db（只读题库）。

数据来源：https://github.com/mkih76/SLB 仓库内 data/shenlun_zhenti/
不爬网；若本机已有 /tmp/SLB 则直接复用，否则浅克隆一次。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DB = ROOT / "data" / "shenlun.db"
TMP_SLB = Path("/tmp/SLB")
SRC_DIR = TMP_SLB / "data" / "shenlun_zhenti"
IMPORT_SCRIPT = TMP_SLB / "scripts" / "import_zhenti.py"

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid TEXT UNIQUE NOT NULL,
    source TEXT,
    exam_type TEXT,
    year INTEGER,
    province TEXT,
    title TEXT NOT NULL,
    material TEXT NOT NULL,
    questions TEXT NOT NULL,
    answer_keys TEXT,
    source_url TEXT,
    status TEXT DEFAULT 'published'
);
CREATE INDEX IF NOT EXISTS ix_shenlun_year ON papers(year);
CREATE INDEX IF NOT EXISTS ix_shenlun_province ON papers(province);
CREATE INDEX IF NOT EXISTS ix_shenlun_exam_type ON papers(exam_type);
"""


def ensure_slb_repo() -> None:
    if SRC_DIR.exists() and any(SRC_DIR.glob("*.md")):
        print(f"[OK] 使用已有申论源：{SRC_DIR}")
        return
    print("[INFO] 克隆 SLB 申论开源仓库（仅取题源，不启动其服务）...")
    if TMP_SLB.exists():
        shutil.rmtree(TMP_SLB)
    subprocess.check_call(
        ["git", "clone", "--depth", "1", "https://github.com/mkih76/SLB.git", str(TMP_SLB)]
    )
    if not SRC_DIR.exists():
        sys.exit(f"[FAIL] 未找到 {SRC_DIR}")


def load_importer():
    # 复用 SLB 的解析逻辑，避免自研爬虫/解析器
    sys.path.insert(0, str(IMPORT_SCRIPT.parent))
    import import_zhenti as iz  # type: ignore

    return iz


def build_db() -> None:
    ensure_slb_repo()
    iz = load_importer()
    md_files = sorted(
        f for f in SRC_DIR.glob("*.md") if f.stat().st_size >= 1500 and not f.name.startswith("_")
    )
    print(f"[INFO] 待解析 Markdown：{len(md_files)}")

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()

    parsed = []
    skipped = 0
    for f in md_files:
        rec = iz.parse_one(f)
        if not rec:
            skipped += 1
            continue
        # 过滤明显不是真题卷的素养短文
        title = str(rec.get("title") or "")
        if re.search(r"素养|思维方法|九法|必读系列", title) and "试题" not in title:
            skipped += 1
            continue
        parsed.append(rec)

    with sqlite3.connect(OUT_DB) as conn:
        conn.executescript(SCHEMA)
        for rec in parsed:
            conn.execute(
                """
                INSERT INTO papers(
                    pid, source, exam_type, year, province, title,
                    material, questions, answer_keys, source_url, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec["pid"],
                    rec.get("source"),
                    rec.get("exam_type"),
                    int(rec.get("year") or 0),
                    rec.get("province"),
                    rec.get("title"),
                    rec.get("material"),
                    rec.get("questions"),
                    rec.get("answer_keys") or "{}",
                    rec.get("source_url"),
                    rec.get("status") or "published",
                ),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        henan = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE province LIKE '%河南%'"
        ).fetchone()[0]
        guokao = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE province LIKE '%国考%' OR exam_type='国考'"
        ).fetchone()[0]

    print(
        f"[OK] 写入 {OUT_DB}：共 {total} 套；河南 {henan}；国考相关 {guokao}；跳过 {skipped}"
    )
    # 抽样
    with sqlite3.connect(OUT_DB) as conn:
        rows = conn.execute(
            """
            SELECT year, province, title,
                   json_array_length(material) AS mats,
                   json_array_length(questions) AS qs
            FROM papers
            WHERE province IN ('河南','国考') OR exam_type='国考'
            ORDER BY year DESC LIMIT 5
            """
        ).fetchall()
        for r in rows:
            print("  sample:", r)


if __name__ == "__main__":
    build_db()
