#!/usr/bin/env python3
"""隔离启动前后版本的 HTTP 服务，测量无 LLM 的 /stats 与 /health。"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent


def request(base: str, endpoint: str):
    start = time.perf_counter()
    with urlopen(base + endpoint, timeout=30) as response:
        body = json.loads(response.read())
    return (time.perf_counter() - start) * 1000, body


def timing(values):
    values = sorted(values)
    return {"median_ms": statistics.median(values), "p95_ms": values[math.ceil(len(values) * .95) - 1], "samples": len(values)}


def benchmark(source: Path, user: Path, log: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, KAOGONG_AGENT_HOST="127.0.0.1", KAOGONG_AGENT_PORT=str(port))
    base = f"http://127.0.0.1:{port}"
    with log.open("w") as stream:
        start = time.perf_counter()
        process = subprocess.Popen(["node", "--import", "tsx", str(source / "server.ts")], cwd=ROOT / "agent", env=env, stdout=stream, stderr=stream)
        try:
            for _ in range(300):
                if process.poll() is not None:
                    raise RuntimeError(f"HTTP 服务启动失败: {log.read_text()}")
                try:
                    request(base, "/health")
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise TimeoutError("HTTP 服务启动超时")
            startup_ms = (time.perf_counter() - start) * 1000
            serial = [request(base, "/stats")[0] for _ in range(20)]
            endpoints = ["/stats", "/health"] * 20
            with ThreadPoolExecutor(max_workers=8) as pool:
                start = time.perf_counter()
                results = list(pool.map(lambda endpoint: request(base, endpoint), endpoints))
                wall = time.perf_counter() - start
            rss = None
            status = Path(f"/proc/{process.pid}/status")
            if status.exists():
                for line in status.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
            return {
                "startup_to_health_ms": startup_ms,
                "serial_stats": timing(serial),
                "mixed_8_clients": {
                    "stats": timing([r[0] for e, r in zip(endpoints, results) if e == "/stats"]),
                    "health": timing([r[0] for e, r in zip(endpoints, results) if e == "/health"]),
                    "requests_per_sec": len(results) / wall, "requests": len(results),
                },
                "rss_kib_after_requests": rss,
                "total_answered": request(base, "/stats")[1]["total_answered"],
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="fbb39cb")
    parser.add_argument("--output", type=Path, default=ROOT / "performance-http-results.json")
    args = parser.parse_args()
    baseline = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=ROOT, text=True).strip()
    output = {"baseline_commit": baseline, "method": "真实 HTTP、本机回环、独立进程；20次串行stats，8线程混合40次stats/health；不调用模型与知识库；RSS为请求完成后的单次进程快照"}
    with tempfile.TemporaryDirectory(prefix=".performance-http-", dir=ROOT / "agent") as directory:
        temp = Path(directory)
        user = temp / "user.db"
        with sqlite3.connect(f"file:{ROOT / 'data/user.db'}?mode=ro", uri=True) as src:
            dst = sqlite3.connect(user)
            try:
                src.backup(dst)
            finally:
                dst.close()
        for version in ["before", "after"]:
            target = temp / version
            target.mkdir()
            for filename in ["server.ts", "agent.ts", "tools.ts", "knowledge.ts"]:
                text = subprocess.check_output(["git", "show", f"{baseline}:agent/src/{filename}"], cwd=ROOT, text=True) if version == "before" else (ROOT / "agent/src" / filename).read_text()
                (target / filename).write_text(text)
            (target / "paths.ts").write_text("\n".join(f"export const {key}={json.dumps(str(value))};" for key, value in {
                "ROOT": ROOT, "USER_DB": user, "KAOGONG_DB": ROOT / "data/kaogong.db", "SYSTEM_PROMPT_PATH": ROOT / "agent/prompts/system.md",
            }.items()))
            output[version] = benchmark(target, user, temp / f"{version}.log")
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
