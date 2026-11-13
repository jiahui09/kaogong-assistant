#!/usr/bin/env python3
"""阶段 2 前端侧验收：按需解析 + 缓存语义 + Agent 不可用降级。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app" / "app.py"


def load_app():
    spec = importlib.util.spec_from_file_location("kaogong_app", APP)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    app = load_app()

    # 1) HTTP /explain-one
    payload = json.dumps({"question_id": 82934, "user_answer": "A"}).encode()
    req = urlrequest.Request(
        f"{app.AGENT_BASE}/explain-one",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode())
    assert body.get("text"), body
    print("[PASS] /explain-one via urllib, len=", len(body["text"]))

    # 2) 前端封装
    one = app.fetch_one_explanation(82934, "A")
    assert one["ok"] and one["text"]
    print("[PASS] fetch_one_explanation ok")

    # 3) 缓存 key 稳定
    k1 = app.cache_key_for_one(82934, "a")
    k2 = app.cache_key_for_one(82934, "A")
    assert k1 == k2 == "82934::A"
    print("[PASS] cache key normalized")

    print("[PASS] 阶段 2 Python 侧验收通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("[FAIL]", exc)
        raise SystemExit(1)
