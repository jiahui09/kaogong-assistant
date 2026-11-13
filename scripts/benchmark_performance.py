#!/usr/bin/env python3
"""隔离、可复现的性能对照；真实库只读，写入仅发生在自动清理的临时副本。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import signal
import sqlite3
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "fbb39cb"


def load_app(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    module.KAOGONG_DB = ROOT / "data" / "kaogong.db"
    module.IMG_ROOT = ROOT / "data" / "90-图片"
    return module


def measure(fn, iterations: int) -> dict:
    start = time.perf_counter()
    fn()
    first = (time.perf_counter() - start) * 1000
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return {
        "first_ms": first, "median_ms": statistics.median(times),
        "p95_ms": times[math.ceil(len(times) * 0.95) - 1], "samples": iterations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE, help="优化前 Git 提交")
    parser.add_argument("--iterations", type=int, default=9)
    parser.add_argument("--sizes", type=int, nargs="*", default=[10000], help="除当前记录量外的扩展测试规模")
    parser.add_argument("--output", type=Path, default=ROOT / "performance-results.json")
    args = parser.parse_args()
    if args.iterations < 3 or any(n < 0 for n in args.sizes):
        parser.error("iterations 至少为 3，sizes 不可为负数")
    # 在可捕获的终止信号下也执行 TemporaryDirectory 清理。
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    baseline = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=ROOT, text=True).strip()

    def source(path: str) -> str:
        return subprocess.check_output(["git", "show", f"{baseline}:{path}"], cwd=ROOT, text=True)

    output = {
        "baseline_commit": baseline,
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "node": subprocess.check_output(["node", "--version"], text=True).strip(),
            "cpu": platform.processor() or platform.machine(), "logical_cpus": os.cpu_count(),
        },
        "database_bytes": {name: (ROOT / "data" / f"{name}.db").stat().st_size for name in ["kaogong", "user", "shenlun"]},
        "method": "单进程串行；first 单列，随后 samples 次计时；未清空 OS 页缓存；Agent 导入/LLM/浏览器不计入函数耗时；前后严格比对 Agent 返回值",
    }
    # 放在 agent 下以解析其 node_modules，退出自动清理。
    with tempfile.TemporaryDirectory(prefix=".performance-", dir=ROOT / "agent") as directory:
        temp = Path(directory)
        before_path = temp / "before_app.py"
        baseline_init = temp / "init_user_db.py"
        baseline_init.write_text(source("scripts/init_user_db.py"))
        before_path.write_text(source("app/app.py").replace(
            'ROOT / "scripts" / "init_user_db.py"', repr(str(baseline_init))
        ))
        before, after = load_app(before_path, "before_app"), load_app(ROOT / "app" / "app.py", "after_app")
        for module, filename in [(before, "python-before.db"), (after, "python-after.db")]:
            module.USER_DB = temp / filename
            module.ensure_user_db()
        python_results = {}
        cases = {
            "open_user": lambda app: app.open_user().close(),
            "random_10": lambda app: app.fetch_questions(None, None, None, None, 10),
            "random_100": lambda app: app.fetch_questions(None, None, None, None, 100),
            "random_10_with_images": lambda app: app.fetch_questions(None, None, None, None, 10, False),
            "random_10_filtered": lambda app: app.fetch_questions(None, None, "资料分析", None, 10),
        }
        for name, fn in cases.items():
            a = measure(lambda: fn(before), args.iterations)
            b = measure(lambda: fn(after), args.iterations)
            python_results[name] = {"before": a, "after": b, "speedup": a["median_ms"] / b["median_ms"]}
            print(f"{name}: {a['median_ms']:.3f} -> {b['median_ms']:.3f} ms", flush=True)
        output["python"] = python_results

        # SQLite backup 保证得到完整一致的用户库副本，不复制进行中的事务文件。
        with sqlite3.connect(f"file:{ROOT / 'data/user.db'}?mode=ro", uri=True, factory=after.ClosingConnection) as src:
            with sqlite3.connect(temp / "user.db", factory=after.ClosingConnection) as dest:
                src.backup(dest)
        questions = ROOT / "data" / "kaogong.db"
        (temp / "paths.ts").write_text(
            f"export const KAOGONG_DB={json.dumps(str(questions))};\n"
            f"export const USER_DB={json.dumps(str(temp / 'user.db'))};\n"
            f"export const ROOT={json.dumps(str(ROOT))};\n"
        )
        for name in ["tools.ts", "knowledge.ts"]:
            (temp / name).write_text(source(f"agent/src/{name}"))
        with sqlite3.connect(temp / "user.db", factory=after.ClosingConnection) as db:
            sample = db.execute("SELECT submission_id, question_id, user_answer, is_correct, time_spent_sec FROM answers ORDER BY id").fetchall()
        if not sample:
            raise RuntimeError("需至少一条作答记录用于扩展基准；请先使用应用完成一次练习")
        sizes = sorted(set([len(sample)] + [n for n in args.sizes if n >= len(sample)]))
        agent_results = {}
        current_size = len(sample)
        for size in sizes:
            with sqlite3.connect(temp / "user.db", factory=after.ClosingConnection) as db:
                db.executemany(
                    "INSERT INTO answers(submission_id,question_id,user_answer,is_correct,time_spent_sec) VALUES (?,?,?,?,?)",
                    (sample[i % len(sample)] for i in range(size - current_size)),
                )
            current_size = size
            env = dict(os.environ, BENCH_ITERATIONS=str(args.iterations))
            result = subprocess.run(
                ["node", "--import", "tsx", "src/benchmark.ts", str(temp / "tools.ts"), str(temp / "user.db"), str(questions)],
                cwd=ROOT / "agent", env=env, capture_output=True, text=True, check=True, timeout=300,
            )
            agent_results[str(size)] = json.loads(result.stdout)
            print(f"agent answers={size}: {json.dumps(agent_results[str(size)], ensure_ascii=False)}", flush=True)
        output["agent"] = agent_results
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(f"结果已保存至 {args.output}")


if __name__ == "__main__":
    main()
