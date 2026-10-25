"""批量截图 + 布局体检：一个 Chrome 实例跑完所有页面 / 断点。

用法：
    python design/_audit.py                # 全量
    python design/_audit.py shelf book     # 只跑部分页面
"""
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
PORT = 9334
BASE = "http://127.0.0.1:8899"

PAGES = {
    "shelf": "/",
    "book": "/book/10",
    "reader": "/read/10/0",
    "comics": "/comics",
    "animes": "/animes",
    "history": "/history",
    "crawl": "/crawl",
    "import": "/import",
    "search": "/search?q=魔法",
    "sources": "/sources",
    "chat": "/chat?who=shiori",
}

SIZES = [(1440, 1000, "d"), (768, 1024, "t"), (390, 844, "m")]

PROBE = r"""
(() => {
  const de = document.documentElement;
  const vw = de.clientWidth;
  const clipped = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (cs.position === "fixed") return true;          // 抽屉 / 弹层，屏幕外属正常
      if (["auto", "scroll", "hidden", "clip"].includes(cs.overflowX)) return true;
      p = p.parentElement;
    }
    return false;
  };
  const out = { scrollW: de.scrollWidth, clientW: vw, overflow: [] };
  document.querySelectorAll("body *").forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.position === "fixed" || cs.display === "none" || cs.visibility === "hidden") return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (r.right > vw + 1.5 || r.left < -1.5) {
      if (clipped(el)) return;
      out.overflow.push({
        sel: el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""),
        left: Math.round(r.left), right: Math.round(r.right)
      });
    }
  });
  out.overflow = out.overflow.slice(0, 8);
  return out;
})()
"""


def wait_json(url, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("chrome devtools not ready")


class Session:
    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="cdp-audit-")
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--hide-scrollbars", f"--remote-debugging-port={PORT}",
             "--remote-allow-origins=*",
             f"--user-data-dir={self.profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        self.seq = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    def send(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def eval(self, js):
        res = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
        return res.get("result", {}).get("value")

    def shot(self, path, full=True):
        params = {"format": "png", "fromSurface": True}
        if full:
            size = self.send("Page.getLayoutMetrics")["cssContentSize"]
            params["captureBeyondViewport"] = True
            params["clip"] = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
        data = self.send("Page.captureScreenshot", params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def viewport(self, w, h):
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def main():
    only = set(sys.argv[1:])
    os.makedirs("design/shots", exist_ok=True)
    s = Session()
    report = {}
    try:
        for key, path in PAGES.items():
            if only and key not in only:
                continue
            for w, h, tag in SIZES:
                s.viewport(w, h)
                s.send("Page.navigate", {"url": BASE + path})
                time.sleep(1.0)
                # 等图片解码，避免截到半张图
                s.eval("Promise.all([...document.images].map(i => i.complete ? 1 : new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 1200); })))")
                probe = s.eval(PROBE)
                out = f"design/shots/{key}-{tag}.png"
                s.shot(out)
                report[f"{key}-{tag}"] = probe
                flag = "OK " if not probe["overflow"] and probe["scrollW"] <= probe["clientW"] else "!! "
                print(flag + f"{key:8s} {tag}  scroll {probe['scrollW']}/{probe['clientW']}  "
                      + json.dumps(probe["overflow"], ensure_ascii=False))
    finally:
        s.close()
    with open("design/shots/_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
