#!/usr/bin/env python3
"""真实浏览器检查计时、错题切换/标记、后台 AI 规划；数据库全部使用临时副本。
依赖 Playwright，可通过 PLAYWRIGHT_MODULE 指向已有安装。AI 使用6秒模拟响应，
只验证交互不被阻塞，不测量真实模型推理速度。
"""
import importlib.util
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent


def main():
    spec = importlib.util.spec_from_file_location("ui_fixture", ROOT / "scripts/test_ui.py")
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    case = fixture.WrongBookUITests()
    case.setUp()
    try:
        user = Path(case.temp.name) / "user.db"
        source = (ROOT / "app/app.py").read_text().replace(
            'ROOT = Path(__file__).resolve().parent.parent', f'ROOT = Path({str(ROOT)!r})'
        ).replace('USER_DB = ROOT / "data" / "user.db"', f'USER_DB = Path({str(user)!r})')
        source = source.replace('if __name__ == "__main__":', '''def fetch_study_plan(days):
    time.sleep(6)
    return {"ok": True, "plan": "模拟异步规划完成"}

if __name__ == "__main__":''')
        app = Path(case.temp.name) / "app.py"
        app.write_text(source)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://localhost:{port}"
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen([
                str(ROOT / ".venv/bin/python"), "-m", "streamlit", "run", str(app),
                "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false",
            ], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with urlopen(base + "/_stcore/health", timeout=.5):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise TimeoutError("Streamlit 启动超时")
                result = subprocess.check_output([
                    "node", str(ROOT / "scripts/check_responsiveness.cjs"), base,
                ], text=True, timeout=50)
                json.loads(result)
                print(result)
            finally:
                process.terminate()
                process.wait(timeout=8)
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
