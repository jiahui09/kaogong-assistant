#!/usr/bin/env python3
"""用 Streamlit AppTest 测量 Python 端整页执行；不代表浏览器绘制耗时。"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def timed_run(app):
    start = time.perf_counter()
    app.run()
    elapsed = (time.perf_counter() - start) * 1000
    if app.exception:
        raise RuntimeError([item.message for item in app.exception])
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="fbb39cb")
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--output", type=Path, default=ROOT / "performance-ui-results.json")
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("iterations 至少为 3")
    baseline = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=ROOT, text=True).strip()
    output = {"baseline_commit": baseline, "method": "Streamlit AppTest Python端整页执行；从真实用户库的隔离副本恢复草稿；单独记录首次执行及后续rerun；不含真实浏览器绘制、网络传输、图片下载与模型"}
    with tempfile.TemporaryDirectory(prefix="kaogong-ui-") as directory:
        for version in ["before", "after"]:
            user = Path(directory) / f"{version}.db"
            with sqlite3.connect(f"file:{ROOT / 'data/user.db'}?mode=ro", uri=True) as src:
                dst = sqlite3.connect(user)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            source = subprocess.check_output(["git", "show", f"{baseline}:app/app.py"], cwd=ROOT, text=True) if version == "before" else (ROOT / "app/app.py").read_text()
            source = source.replace("ROOT = Path(__file__).resolve().parent.parent", f"ROOT = Path({str(ROOT)!r})")
            source = source.replace('USER_DB = ROOT / "data" / "user.db"', f"USER_DB = Path({str(user)!r})")
            app = AppTest.from_string(source, default_timeout=30)
            first = timed_run(app)
            questions = len(app.session_state["questions"])
            values = sorted(timed_run(app) for _ in range(args.iterations))
            app.sidebar.radio[0].set_value("申论")
            shenlun = timed_run(app)
            output[version] = {
                "first_ms": first, "rerun_median_ms": statistics.median(values),
                "rerun_p95_ms": values[math.ceil(len(values) * .95) - 1],
                "samples": len(values), "restored_questions": questions,
                "switch_to_shenlun_ms": shenlun, "exceptions": 0,
            }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
