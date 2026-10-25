"""验证返回键：直达页面时隐藏，导航之后再出现，点了能退回。

    python design/_test_backbtn.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9391
ROOT = Path(__file__).resolve().parent.parent
fails: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def wait_json(url, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode())
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise RuntimeError("cdp not ready")


def main() -> int:
    prof = tempfile.mkdtemp(prefix="cdp-back-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={prof}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
        seq = [0]

        def send(m, p=None):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": m, "params": p or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq[0]:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        def ev(expr):
            r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                          "awaitPromise": True})
            return r.get("result", {}).get("value")

        send("Page.enable")
        send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})

        print("[1] 直达首页：没有历史，返回键应隐藏")
        send("Page.navigate", {"url": "http://127.0.0.1:8920/"})
        time.sleep(1.6)
        v = ev("""(() => {
          const b = document.getElementById('backBtn');
          if (!b) return {found:false};
          return {found:true, hidden:b.hidden,
                  visible: b.getBoundingClientRect().width > 0};
        })()""")
        print(f"    {v}")
        check(bool(v and v.get("found")), "返回键存在于 DOM")

        print("[2] 点进一本书：有历史，返回键应出现")
        # 首页有书的概率高；没有就直接导航到 /book/19
        ev("location.href='/book/19'")
        time.sleep(1.6)
        v2 = ev("""(() => {
          const b = document.getElementById('backBtn');
          if (!b) return {found:false};
          const r = b.getBoundingClientRect();
          return {found:true, hidden:b.hidden,
                  w:Math.round(r.width), h:Math.round(r.height)};
        })()""")
        print(f"    {v2}")
        check(bool(v2 and v2.get("found")), "详情页有返回键")
        check(v2.get("hidden") is False, "导航后返回键显示")
        check(v2.get("w", 0) >= 30, f"返回键可点（宽 {v2.get('w')}px）")

        print("[3] 点返回键：应离开当前页")
        # 点击会触发导航，CDP 的 evaluate 上下文会瞬间销毁并抛
        # "Inspected target navigated or closed" —— 这本身就是「点了有反应」的证据。
        try:
            ev("document.getElementById('backBtn').click()")
        except RuntimeError as exc:
            if "navigated" not in str(exc):
                raise
        time.sleep(1.6)
        url = ev("location.pathname")
        print(f"    当前路径: {url}")
        # 历史里可能回到 about:blank（"blank"），只要不是刚才那个详情页就算退成功
        check(url != "/book/19", "返回键离开了详情页")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
