"""验证看板娘可以拖动，且位置会被记住。

    python design/_test_mascot_drag.py

用 CDP 派发真实的 pointer 事件，模拟从右下角拖到左上角，
再刷新页面确认位置保持。
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
PORT = 9381
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
    raise RuntimeError("chrome devtools not ready")


def main() -> int:
    prof = tempfile.mkdtemp(prefix="cdp-drag-")
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
        send("Page.navigate", {"url": "http://127.0.0.1:8920/"})
        time.sleep(1.8)

        # 0) 看板娘存在且可见
        print("[0] 初始状态")
        info0 = ev("""(() => {
          const m = document.getElementById('mascot');
          const f = document.querySelector('.mascot-face');
          if (!m || !f) return {found:false};
          const r = f.getBoundingClientRect();
          return {found:true, on:m.classList.contains('on'),
                  left:Math.round(r.left), top:Math.round(r.top),
                  w:Math.round(r.width), h:Math.round(r.height)};
        })()""")
        print(f"    {info0}")
        check(bool(info0 and info0.get("found")), "看板娘存在")
        if not (info0 and info0.get("found")):
            return 1

        # 1) 用 pointer 事件拖动
        print("[1] 拖动到左上角")
        start_x = info0["left"] + info0["w"] / 2
        start_y = info0["top"] + info0["h"] / 2
        target_x, target_y = 70, 120
        ev(f"""(() => {{
          const f = document.querySelector('.mascot-face');
          const opts = (x,y) => ({{clientX:x, clientY:y, pointerId:1, pointerType:'touch',
                                  bubbles:true, cancelable:true, isPrimary:true, button:0}});
          f.dispatchEvent(new PointerEvent('pointerdown', opts({start_x},{start_y})));
          for (let i=1;i<=8;i++) {{
            const x = {start_x} + ({target_x}-{start_x})*i/8;
            const y = {start_y} + ({target_y}-{start_y})*i/8;
            f.dispatchEvent(new PointerEvent('pointermove', opts(x,y)));
          }}
          f.dispatchEvent(new PointerEvent('pointerup', opts({target_x},{target_y})));
          return true;
        }})()""")
        time.sleep(0.6)
        info1 = ev("""(() => {
          const f = document.querySelector('.mascot-face');
          const r = f.getBoundingClientRect();
          return {left:Math.round(r.left), top:Math.round(r.top)};
        })()""")
        print(f"    {info1}")
        check(info1["left"] < info0["left"] - 50, "位置向左移动了")
        check(abs(info1["top"] - target_y) < 90, "位置向上移动了")

        # 2) 位置写进 localStorage
        print("[2] 位置持久化")
        saved = ev("localStorage.getItem('nh-mascot-pos')")
        print(f"    localStorage: {saved}")
        check(bool(saved), "位置已存 localStorage")

        # 3) 刷新后还在原处
        print("[3] 刷新后保持")
        send("Page.navigate", {"url": "http://127.0.0.1:8920/"})
        time.sleep(1.8)
        info2 = ev("""(() => {
          const f = document.querySelector('.mascot-face');
          if (!f) return null;
          const r = f.getBoundingClientRect();
          return {left:Math.round(r.left), top:Math.round(r.top)};
        })()""")
        print(f"    {info2}")
        check(info2 is not None, "刷新后看板娘还在")
        if info2:
            check(abs(info2["left"] - info1["left"]) < 20,
                  "刷新后水平位置保持")
            check(abs(info2["top"] - info1["top"]) < 20,
                  "刷新后垂直位置保持")

        # 4) 吸附到边缘
        # 注意：.mascot 是 flex 容器（气泡 + 头像），left/top 定位的是容器，
        # 头像在容器最右边。所以判断「吸附」要看容器的 left，不是头像的 rect.left。
        print("[4] 贴边吸附")
        container = ev("""(() => {
          const m = document.getElementById('mascot');
          const r = m.getBoundingClientRect();
          return {left: Math.round(r.left), top: Math.round(r.top),
                  w: Math.round(r.width),
                  inlineLeft: m.style.left, inlineTop: m.style.top};
        })()""")
        print(f"    {container}")
        check(container["left"] <= 16, "拖到左边后容器吸附在左边缘")
        check(abs(container["top"] - 97) < 30, "垂直位置保持落点")

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
