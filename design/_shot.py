"""按指定视口截图，用于肉眼核对「画面大小」是否合适。

    python design/_shot.py                       # 默认 3 档视口 × 书架页
    python design/_shot.py --page /settings --tag settings

输出到 design/shots/，文件名 {tag}-{w}x{h}.png
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9341
ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "design" / "shots"


def wait_json(url, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise RuntimeError("chrome devtools not ready")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8920")
    ap.add_argument("--page", default="/")
    ap.add_argument("--tag", default="shot")
    ap.add_argument("--sizes", default="1440x900,1920x1080,2560x1600")
    args = ap.parse_args()

    sizes = []
    for s in args.sizes.split(","):
        w, h = s.lower().split("x")
        sizes.append((int(w), int(h)))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="cdp-shot-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    written = []
    try:
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
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

        for w, h in sizes:
            send("Emulation.setDeviceMetricsOverride",
                 {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            send("Page.navigate", {"url": args.base + args.page})
            time.sleep(1.2)
            shot = send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            path = OUTDIR / f"{args.tag}-{w}x{h}.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            written.append(path)
            print(f"  {w}x{h} -> {path.relative_to(ROOT)}")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    print(f"共 {len(written)} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
