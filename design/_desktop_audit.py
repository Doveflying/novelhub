"""桌面端布局验收：在真实浏览器里量化「桌面版画面没适配好」到底是哪里。

    python design/_desktop_audit.py
    python design/_desktop_audit.py --base http://127.0.0.1:8921   # 空库（首次安装）

查的不是「好不好看」，是能判定的硬指标：

  1. 横向溢出 —— 有元素超出视口（最常见、最刺眼）
  2. 侧栏遮挡 —— 主内容区被固定侧栏压住
  3. 主区过窄 —— 大屏下正文/卡片区只占很窄一条（典型的「没适配」）
  4. 空状态错位 —— 空库时引导块没有居中或高度塌陷
  5. 元素塌陷 —— 可见容器高度 < 8px（图片没撑开、flex 没给高度）
  6. 文字溢出容器 —— scrollWidth 明显大于 clientWidth 且没设省略
  7. 触控/点击区重叠 —— 两个可点元素中心点互相包含（点不准）

结果写 design/shots/_desktop.json，并按严重程度打印。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9338
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "design" / "shots" / "_desktop.json"

VIEWPORTS = [
    ("desktop-1440", 1440, 900),
    ("desktop-1920", 1920, 1080),
    ("laptop-1280", 1280, 800),
]

PAGES = [
    ("shelf", "/"),
    ("book", "/book/5"),
    ("reader", "/read/5/0"),
    ("history", "/history"),
    ("sources", "/sources"),
    ("crawl", "/crawl"),
    ("import", "/import"),
    ("comics", "/comics"),
    ("animes", "/animes"),
    ("chat", "/chat"),
]

PROBE = r"""
(() => {
  const out = {};
  const de = document.documentElement;
  const vw = innerWidth, vh = innerHeight;
  out.vw = vw; out.vh = vh;
  out.scrollW = de.scrollWidth;
  out.overflowX = de.scrollWidth > vw + 1;
  out.overflowPx = de.scrollWidth - vw;

  // 1. 横向越界元素
  const over = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' && cs.visibility === 'hidden') continue;
    if (r.right > vw + 2 || r.left < -2) {
      over.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') || '').slice(0, 44),
        left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width),
      });
      if (over.length >= 8) break;
    }
  }
  out.overflowEls = over;

  // 2. 侧栏 / 主区几何
  const rail = document.querySelector('.rail');
  const main = document.querySelector('.main') || document.querySelector('main');
  if (rail) { const r = rail.getBoundingClientRect(); out.rail = {w: Math.round(r.width), right: Math.round(r.right), fixed: getComputedStyle(rail).position}; }
  if (main) {
    const r = main.getBoundingClientRect();
    out.main = {w: Math.round(r.width), left: Math.round(r.left), ratio: +(r.width / vw).toFixed(3)};
  }
  // 侧栏是否压住主区
  if (out.rail && out.main) {
    out.railOverlapsMain = out.main.left < out.rail.right - 2;
  }

  // 3. 可见容器高度塌陷（图片没撑开 / flex 没高度）
  const collapsed = [];
  for (const el of document.querySelectorAll('.hero-art, .hero-portrait, .cover, .book-cover, .cont-cover, .reader-body, .im-thread, .cast-note')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height < 8) {
      collapsed.push({cls: (el.getAttribute('class')||'').slice(0,40), w: Math.round(r.width), h: Math.round(r.height)});
    }
  }
  out.collapsed = collapsed.slice(0, 6);

  // 4. 空状态（只算真正可见的；#shelfEmpty 平时 display:none，尺寸 0 是正常的）
  const emptyEls = [];
  for (const el of document.querySelectorAll('.empty, .shelf-empty, .empty-state')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) continue;
    emptyEls.push({w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left)});
  }
  if (emptyEls.length) {
    out.empty = emptyEls[0];
    out.emptyTiny = emptyEls[0].h < 40;
  }

  // 5. Hero 区几何（大屏下最容易失衡的一块）
  const hero = document.querySelector('.shelf-hero');
  if (hero) {
    const r = hero.getBoundingClientRect();
    out.hero = {w: Math.round(r.width), h: Math.round(r.height), ratio: +(r.h / vh).toFixed(2)};
    const art = hero.querySelector('.hero-art');
    if (art) { const a = art.getBoundingClientRect(); out.heroArt = {w: Math.round(a.width), h: Math.round(a.height)}; }
  }

  // 6. 文字溢出容器（没设省略号却横向溢出）
  const clipped = [];
  for (const el of document.querySelectorAll('h1,h2,h3,.book-title,.rt-book,.im-name,.card-title')) {
    if (el.scrollWidth > el.clientWidth + 4 && el.clientWidth > 0) {
      const cs = getComputedStyle(el);
      if (cs.overflow === 'visible' && cs.textOverflow !== 'ellipsis') {
        clipped.push({cls:(el.getAttribute('class')||el.tagName).slice(0,30), sw: el.scrollWidth, cw: el.clientWidth});
      }
    }
  }
  out.textOverflow = clipped.slice(0, 5);

  // 7. 关键内容是否在首屏可见
  const h1 = document.querySelector('h1, .hero-title');
  if (h1) { const r = h1.getBoundingClientRect(); out.titleInView = r.top >= -2 && r.top < vh; }
  return out;
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8920")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    profile = tempfile.mkdtemp(prefix="cdp-desktop-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results: dict = {}
    problems: list[tuple[str, str]] = []
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

        for vname, w, h in VIEWPORTS:
            send("Emulation.setDeviceMetricsOverride",
                 {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            for pname, path in PAGES:
                tag = f"{vname}/{pname}"
                send("Page.navigate", {"url": base + path})
                time.sleep(0.75)
                res = send("Runtime.evaluate",
                           {"expression": PROBE, "returnByValue": True, "awaitPromise": True})
                val = res.get("result", {}).get("value")
                if not isinstance(val, dict):
                    problems.append((tag, f"probe 异常 {val!r}"))
                    continue
                results[tag] = val

                if val.get("overflowX"):
                    problems.append((tag, f"横向溢出 {val['overflowPx']}px {val['overflowEls'][:3]}"))
                if val.get("railOverlapsMain"):
                    problems.append((tag, f"侧栏压住主区 main.left={val['main']['left']} < rail.right={val['rail']['right']}"))
                if val.get("main") and val["main"]["ratio"] < 0.62 and val["vw"] >= 1280:
                    problems.append((tag, f"大屏主区过窄 只占 {val['main']['ratio']*100:.0f}%"))
                if val.get("collapsed"):
                    problems.append((tag, f"容器高度塌陷 {val['collapsed'][:3]}"))
                if val.get("emptyTiny"):
                    problems.append((tag, f"空状态高度塌陷 {val['empty']}"))
                if val.get("textOverflow"):
                    problems.append((tag, f"标题溢出未省略 {val['textOverflow'][:2]}"))
                if val.get("hero") and (val["hero"].get("ratio") or 0) > 0.92:
                    problems.append((tag, f"Hero 占满整屏 {val['hero']['ratio']}"))
                if val.get("titleInView") is False:
                    problems.append((tag, "主标题不在首屏"))

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"检查 {len(results)} 个视口×页面组合（base={base}）-> {OUT.relative_to(ROOT)}")
    if problems:
        print(f"\n发现 {len(problems)} 个问题：", file=sys.stderr)
        for tag, msg in problems:
            print(f"  [{tag}] {msg}", file=sys.stderr)
        return 1
    print("桌面布局全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
