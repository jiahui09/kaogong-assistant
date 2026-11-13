#!/usr/bin/env python3
"""通过真实 Streamlit WebSocket 模拟作答；校验草稿、局部刷新、计时与交卷。

仅使用 Streamlit 自带 Tornado/protobuf，不需要浏览器或额外依赖。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from tornado.websocket import websocket_connect
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

ROOT = Path(__file__).resolve().parent.parent


async def interact(port: int, user: Path, iterations: int):
    ws = await websocket_connect(f"ws://127.0.0.1:{port}/_stcore/stream", subprotocols=["streamlit"], connect_timeout=10)
    radios = {}
    buttons = {}
    cache = {}
    auto_fragments = set()
    page_hash = ""

    async def run(message):
        nonlocal page_hash
        start = time.perf_counter()
        ws.write_message(message.SerializeToString(), binary=True)
        byte_count, delta_count, radio_count = 0, 0, 0
        while True:
            raw = await asyncio.wait_for(ws.read_message(), 30)
            if raw is None:
                raise RuntimeError("WebSocket 意外关闭")
            byte_count += len(raw)
            msg = ForwardMsg()
            msg.ParseFromString(raw)
            if msg.WhichOneof("type") == "ref_hash":
                if msg.ref_hash not in cache:
                    raise RuntimeError("找不到引用消息")
                msg = cache[msg.ref_hash]
            elif msg.hash:
                cache[msg.hash] = msg
            kind = msg.WhichOneof("type")
            if kind == "new_session":
                page_hash = msg.new_session.page_script_hash
                if not msg.new_session.fragment_ids_this_run:
                    auto_fragments.clear()
            elif kind == "auto_rerun":
                auto_fragments.add(msg.auto_rerun.fragment_id)
            elif kind == "delta":
                delta_count += 1
                if msg.delta.WhichOneof("type") == "new_element":
                    element = msg.delta.new_element
                    etype = element.WhichOneof("type")
                    if etype == "exception":
                        raise RuntimeError(element.exception.message)
                    if etype == "radio":
                        radio_count += 1
                        r = element.radio
                        radios[r.label] = {"id": r.id, "value": r.default, "fragment": msg.delta.fragment_id}
                    elif etype == "button":
                        buttons[element.button.label] = element.button.id
                        buttons[element.button.id] = element.button.id
            elif kind == "script_finished":
                if msg.script_finished == ForwardMsg.FINISHED_EARLY_FOR_RERUN:
                    continue
                if msg.script_finished == ForwardMsg.FINISHED_WITH_COMPILE_ERROR:
                    raise RuntimeError("Streamlit 编译失败")
                break
        return {"ms": (time.perf_counter() - start) * 1000, "bytes": byte_count, "deltas": delta_count, "radios": radio_count}

    def rerun(fragment="", target=None, value=None, button=None):
        msg = BackMsg()
        msg.rerun_script.page_script_hash = page_hash
        msg.rerun_script.fragment_id = fragment
        for label, state in radios.items():
            item = msg.rerun_script.widget_states.widgets.add()
            item.id = state["id"]
            item.int_value = value if label == target else state["value"]
        if button:
            item = msg.rerun_script.widget_states.widgets.add()
            item.id = button
            item.trigger_value = True
        return msg

    try:
        initial = await run(rerun())
        target = next((label for label in radios if label.startswith("选项##")), None)
        if target is None:
            raise RuntimeError("未恢复到做题页面；需要包含草稿的 user.db")
        qid = int(target.split("##")[1])
        with sqlite3.connect(user) as db:
            draft = json.loads(db.execute("SELECT draft_json FROM quiz_drafts WHERE id=1").fetchone()[0])
        question = next(q for q in draft["questions"] if int(q["id"]) == qid)
        keys = [o.get("key", "").upper() for o in question["options"]]
        samples = []
        fragment = radios[target]["fragment"]
        for i in range(iterations):
            value = 1 + (i % len(keys))
            sample = await run(rerun(fragment, target, value))
            with sqlite3.connect(user) as db:
                saved = json.loads(db.execute("SELECT draft_json FROM quiz_drafts WHERE id=1").fetchone()[0])
            if saved["answers"].get(str(qid)) != keys[value - 1]:
                raise AssertionError("作答未同步持久化")
            samples.append(sample)
            # 还原浏览器的当前选中状态，供下一次请求发送。
            radios[target]["value"] = value
            fragment = radios[target]["fragment"]
        # 模拟定时片段，确认不触发整卷重绘。
        timers = [await run(rerun(f)) for f in sorted(auto_fragments)]
        # 全页操作之后片段与 widget 状态也必须继续正确。
        await run(rerun(button=buttons["清空全部答案"]))
        with sqlite3.connect(user) as db:
            saved = json.loads(db.execute("SELECT draft_json FROM quiz_drafts WHERE id=1").fetchone()[0])
            assert saved["answers"] == {}
        final_value = 1 + ((iterations - 1) % len(keys))
        await run(rerun(radios[target]["fragment"], target, final_value))
        radios[target]["value"] = final_value
        star_button = next(value for value in buttons.values() if value.endswith(f"-star_{qid}"))
        with sqlite3.connect(user) as db:
            starred_before = db.execute("SELECT COUNT(*) FROM starred_questions WHERE question_id=?", (qid,)).fetchone()[0]
        await run(rerun(radios[target]["fragment"], button=star_button))
        with sqlite3.connect(user) as db:
            starred_after = db.execute("SELECT COUNT(*) FROM starred_questions WHERE question_id=?", (qid,)).fetchone()[0]
        assert starred_after != starred_before
        # 实际交卷验证局部刷新后的答案进入评分，并确认清理草稿。
        with sqlite3.connect(user) as db:
            previous = db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        await run(rerun(button=buttons["交卷批分"]))
        with sqlite3.connect(user) as db:
            assert db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == previous + 1
            assert db.execute("SELECT COUNT(*) FROM quiz_drafts").fetchone()[0] == 0
            answer = db.execute("SELECT user_answer FROM answers WHERE submission_id=(SELECT MAX(id) FROM submissions) AND question_id=?", (qid,)).fetchone()[0]
            assert answer == keys[(iterations - 1) % len(keys)]
        values = sorted(s["ms"] for s in samples)
        return {
            "initial_render": initial, "questions": len(draft["questions"]),
            "answer": {"median_ms": statistics.median(values), "p95_ms": values[math.ceil(len(values) * .95) - 1],
                "median_bytes": statistics.median(s["bytes"] for s in samples),
                "median_deltas": statistics.median(s["deltas"] for s in samples),
                "median_radios": statistics.median(s["radios"] for s in samples), "samples": len(samples)},
            "fragment_enabled": bool(fragment), "timer_runs": timers,
            "draft_persistence_checked": True, "submission_checked": True,
            "clear_answers_checked": True, "star_toggle_checked": True,
        }
    finally:
        ws.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="fbb39cb")
    parser.add_argument("--iterations", type=int, default=9)
    parser.add_argument("--output", type=Path, default=ROOT / "performance-interaction-results.json")
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("iterations 至少为3")
    baseline = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=ROOT, text=True).strip()
    output = {"baseline_commit": baseline, "method": "真实Streamlit进程和WebSocket/protobuf作答事件，消息无浏览器绘制；每次事件均核对隔离草稿，最后真实交卷核对答案；未模拟计时器每秒自动调度，计时片段在最后单独请求"}
    with tempfile.TemporaryDirectory(prefix="kaogong-interaction-") as directory:
        temp = Path(directory)
        for version in ["before", "after"]:
            user = temp / f"{version}.db"
            src = sqlite3.connect(f"file:{ROOT / 'data/user.db'}?mode=ro", uri=True)
            dst = sqlite3.connect(user)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            text = subprocess.check_output(["git", "show", f"{baseline}:app/app.py"], cwd=ROOT, text=True) if version == "before" else (ROOT / "app/app.py").read_text()
            text = text.replace("ROOT = Path(__file__).resolve().parent.parent", f"ROOT = Path({str(ROOT)!r})")
            text = text.replace('USER_DB = ROOT / "data" / "user.db"', f"USER_DB = Path({str(user)!r})")
            script = temp / f"{version}.py"
            script.write_text(text)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with (temp / f"{version}.log").open("w") as log:
                process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(script), "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true", "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"], cwd=ROOT, stdout=log, stderr=log)
                try:
                    for _ in range(200):
                        if process.poll() is not None:
                            raise RuntimeError((temp / f"{version}.log").read_text())
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1):
                                break
                        except OSError:
                            time.sleep(.05)
                    else:
                        raise TimeoutError("Streamlit 启动超时")
                    output[version] = asyncio.run(interact(port, user, args.iterations))
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
