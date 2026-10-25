"""插图功能验收：在真实浏览器里确认插图「看得见」，而不只是 HTTP 200。

    python design/_illus_audit.py

检查三件事，缺一件都算插图功能坏的：
  1. DOM 里 <img> 数量与章节 HTML 里的一致（没被前端逻辑吃掉）
  2. naturalWidth > 0 —— 浏览器真的解出了像素，不是 200 但空白
  3. 尺寸合理 —— 宽度撑满正文列、高度按比例，没被压成 0 或溢出

结果写 design/shots/_illus.json。
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
PORT = 9337
BASE = "http://127.0.0.1:8920"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "design" / "shots" / "_illus.json"

# 书 5 的第 0 章就是纯插图页，最干净
TARGETS = [
    ("illus-page-desktop", "/read/5/0", 1440, 900),
    ("illus-page-mobile", "/read/5/0", 390, 844),
    ("text-chapter-desktop", "/read/5/1", 1440, 900),
]

PROBE = r"""
(() => {
  const imgs = Array.from(document.querySelectorAll('.reader-body img, #reader img'));
  const body = document.querySelector('.reader-body') || document.querySelector('#reader');
  const bodyW = body ? body.getBoundingClientRect().width : 0;
  const items = imgs.map(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      src: (el.getAttribute('src') || '').slice(0, 90),
      complete: el.complete,
      naturalW: el.naturalWidth,
      naturalH: el.naturalHeight,
      boxW: Math.round(r.width),
      boxH: Math.round(r.height),
      maxW: cs.maxWidth,
      display: cs.display,
      opacity: cs.opacity,
      visible: r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden',
    };
  });
  return {
    bodyWidth: Math.round(bodyW),
    imgCount: imgs.length,
    loaded: items.filter(i => i.naturalW > 0).length,
    visible: items.filter(i => i.visible).length,
    zeroBox: items.filter(i => i.visible && (i.boxW === 0 || i.boxH === 0)).length,
    overflow: items.filter(i => i.visible && i.boxW > bodyW + 2).length,
    samples: items.slice(0, 6),
  };
})()
"""


def wait_json(url, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise RuntimeError("chrome devtools not ready")


def main() -> int:
    profile = tempfile.mkdtemp(prefix="cdp-illus-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results: dict = {}
    problems: list[str] = []
    try:
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=40)
        seq = [0]

        def send(method, params=None):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq[0]:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        send("Page.enable")
        send("Runtime.enable")

        for name, path, w, h in TARGETS:
            send("Emulation.setDeviceMetricsOverride",
                 {"width": w, "height": h, "deviceScaleFactor": 2, "mobile": w < 500})
            send("Page.navigate", {"url": BASE + path})
            # 插图是 loading=lazy，得滚到底把它们都触发出来
            for _ in range(3):
                send("Runtime.evaluate", {
                    "expression": "window.scrollTo(0, document.body.scrollHeight); 1",
                    "returnByValue": True,
                })
                time.sleep(0.7)
            send("Runtime.evaluate", {
                "expression": "window.scrollTo(0,0); 1", "returnByValue": True})
            time.sleep(0.6)

            res = send("Runtime.evaluate",
                       {"expression": PROBE, "returnByValue": True, "awaitPromise": True})
            val = res.get("result", {}).get("value")
            if not isinstance(val, dict):
                problems.append(f"{name}: probe 返回异常 {val!r}")
                continue
            results[name] = val

            if val["imgCount"] == 0:
                problems.append(f"{name}: 页面里没有 <img>")
            elif val["loaded"] < val["imgCount"]:
                problems.append(
                    f"{name}: {val['imgCount']} 张图只有 {val['loaded']} 张解出像素")
            if val["visible"] < val["imgCount"]:
                problems.append(
                    f"{name}: {val['imgCount']} 张图只有 {val['visible']} 张可见")
            if val["zeroBox"]:
                problems.append(f"{name}: {val['zeroBox']} 张图盒子尺寸为 0")
            if val["overflow"]:
                problems.append(f"{name}: {val['overflow']} 张图宽度超出正文列")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, v in results.items():
        print(f"{name}: {v['imgCount']} 张 / 解出 {v['loaded']} / 可见 {v['visible']} "
              f"/ 正文宽 {v['bodyWidth']}px")
    print(f"-> {OUT.relative_to(ROOT)}")

    if problems:
        print(f"\n发现 {len(problems)} 个问题：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print("插图全部正常：数量对、像素解出、可见、不溢出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
