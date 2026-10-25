import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333


def wait_json(url, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("chrome devtools not ready")


def main():
    url = sys.argv[1]
    out = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
    js = sys.argv[5] if len(sys.argv) > 5 else ""
    full = os.environ.get("FULL") == "1"

    profile = tempfile.mkdtemp(prefix="cdp-prof-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20)
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
        send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
        })
        send("Page.navigate", {"url": url})
        time.sleep(1.2)

        if js:
            res = send("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
            print(json.dumps(res.get("result", {}).get("value"), ensure_ascii=False, indent=2))
            time.sleep(0.5)

        params = {"format": "png", "fromSurface": True}
        if full:
            metrics = send("Page.getLayoutMetrics")
            size = metrics["cssContentSize"]
            params["captureBeyondViewport"] = True
            params["clip"] = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
        shot = send("Page.captureScreenshot", params)
        with open(out, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
