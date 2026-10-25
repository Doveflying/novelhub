"""移动端验收：三档视口 × 全站页面，检查横向溢出、安全区、触控尺寸、PWA 装配。

    python design/_mobile_audit.py

输出 JSON 到 design/shots/_mobile.json，任一页面溢出会打印到 stderr。
比肉眼看截图靠谱：390px 宽下 1px 溢出在缩略图里根本看不出来。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
BASE = os.environ.get("NH_BASE", "http://127.0.0.1:8920")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "design" / "shots" / "_mobile.json"

# 手机 / 大屏手机 / 平板竖屏
VIEWPORTS = [
    ("iphone-390", 390, 844),
    ("android-412", 412, 915),
    ("ipad-768", 768, 1024),
]

PAGES = [
    ("shelf", "/"),
    ("book", "/book/7"),
    ("reader", "/read/7/0"),
    ("history", "/history"),
    ("search", "/search?q=%E5%AE%89%E8%BE%BE"),
    ("sources", "/sources"),
    ("crawl", "/crawl"),
    ("import", "/import"),
    ("comics", "/comics"),
    ("animes", "/animes"),
    ("chat", "/chat"),
]

# 一次注入，返回所有要断言的量
PROBE = r"""
(() => {
  const out = {};
  const de = document.documentElement;
  out.vw = innerWidth;
  out.vh = innerHeight;
  out.scrollW = de.scrollWidth;
  out.overflowX = de.scrollWidth > innerWidth + 1;
  out.overflowPx = de.scrollWidth - innerWidth;

  const cs = getComputedStyle(de);
  out.vhVar = cs.getPropertyValue('--vh').trim();
  out.safeBottom = cs.getPropertyValue('--safe-b').trim();
  out.appH = cs.getPropertyValue('--app-h').trim();

  // 横向越界的元素（最多 8 个）
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.right > innerWidth + 1.5 || r.left < -1.5) {
      bad.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') || '').slice(0, 46),
        left: Math.round(r.left),
        right: Math.round(r.right),
      });
      if (bad.length >= 8) break;
    }
  }
  out.offenders = bad;

  // 触控目标：可见的按钮/链接 < 40px 高（不含内联文本链）
  const small = [];
  for (const el of document.querySelectorAll('button, .tabbar a, .dock-btn, .icon-btn')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.height < 39.5) {
      small.push({cls: (el.getAttribute('class')||'').slice(0,40), h: Math.round(r.height)});
    }
  }
  out.smallTargets = small.slice(0, 10);

  // 输入框字号（iOS 会为 <16px 自动放大）
  const inputs = [];
  for (const el of document.querySelectorAll('input[type=text],input[type=search],input[type=url],input[type=number],textarea,select')) {
    inputs.push(parseFloat(getComputedStyle(el).fontSize));
  }
  out.minInputFont = inputs.length ? Math.min(...inputs) : null;

  // PWA 装配
  out.hasManifest = !!document.querySelector('link[rel=manifest]');
  out.hasTouchIcon = !!document.querySelector('link[rel=apple-touch-icon]');
  out.viewportFit = (document.querySelector('meta[name=viewport]')||{}).content || '';

  // 底部 tabbar 是否被安全区撑开
  const tb = document.querySelector('.tabbar');
  if (tb) out.tabbarBottomPad = getComputedStyle(tb).paddingBottom;

  // 阅读页专用
  const rb = document.querySelector('.reader-body');
  if (rb) {
    const c = getComputedStyle(rb);
    out.readerFont = c.fontSize;
    out.readerLineHeight = c.lineHeight;
    out.readerPadL = c.paddingLeft;
  }
  return out;
})()
"""


def wait_json(url: str, timeout: float = 20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise RuntimeError("chrome devtools not ready")


def main() -> int:
    profile = tempfile.mkdtemp(prefix="cdp-mobile-")
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*", f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: dict = {}
    problems: list[str] = []
    try:
        info = wait_json(f"http://127.0.0.1:{PORT}/json/list")
        page = next(t for t in info if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
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

        for name, w, h in VIEWPORTS:
            send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": w, "height": h, "deviceScaleFactor": 2,
                    "mobile": True, "screenWidth": w, "screenHeight": h,
                },
            )
            send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
            for key, path in PAGES:
                tag = f"{name}/{key}"
                send("Page.navigate", {"url": BASE + path})
                time.sleep(0.85)
                res = send(
                    "Runtime.evaluate",
                    {"expression": PROBE, "returnByValue": True, "awaitPromise": True},
                )
                val = res.get("result", {}).get("value")
                if not isinstance(val, dict):
                    problems.append(f"{tag}: probe returned {val!r}")
                    continue
                results[tag] = val
                if val.get("overflowX"):
                    problems.append(
                        f"{tag}: 横向溢出 {val['overflowPx']}px -> {val['offenders'][:3]}"
                    )
                if val.get("smallTargets"):
                    problems.append(f"{tag}: 触控目标偏小 {val['smallTargets'][:3]}")
                if val.get("minInputFont") is not None and val["minInputFont"] < 16:
                    problems.append(f"{tag}: 输入框字号 {val['minInputFont']}px < 16px（iOS 会放大）")
                if not val.get("hasManifest"):
                    problems.append(f"{tag}: 缺 manifest")
                if not val.get("hasTouchIcon"):
                    problems.append(f"{tag}: 缺 apple-touch-icon")
                if "viewport-fit=cover" not in (val.get("viewportFit") or ""):
                    problems.append(f"{tag}: viewport 缺 viewport-fit=cover")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"检查 {len(results)} 个视口×页面组合 -> {OUT.relative_to(ROOT)}")
    if problems:
        print(f"\n发现 {len(problems)} 个问题：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print("全部通过：无横向溢出、触控目标合规、PWA 装配完整")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
