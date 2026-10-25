"""导入页交互诊断：点「选文件」到底有没有触发 file chooser。

    python design/_diag_import.py

模拟手机 WebView 的条件（窄视口 + 触控），用 CDP 直接派发真实鼠标事件，
看 input[type=file] 的 click 是否被拦、以及 label/input 的关联是否异常。

判据（任一命中就是「点了没反应」的原因）：
  1. label 的 for 指向自身内部控件（双重关联，部分 WebView 会吞事件）
  2. input 的可见尺寸为 0（WebView 不认为可点）
  3. pointer-events:none / visibility:hidden
  4. 有 JS 在 click 上 preventDefault
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
PORT = 9339
BASE = "http://127.0.0.1:8920"
ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
(() => {
  const out = {};
  const input = document.getElementById('impFiles');
  const label = document.querySelector('label[for="impFiles"]');
  if (!input) { out.error = 'input#impFiles 不存在'; return out; }

  out.input = {
    type: input.type,
    multiple: input.multiple,
    accept: input.accept,
    disabled: input.disabled,
  };

  // 1. label 双重关联：label 内部包含 for 指向的那个控件
  if (label) {
    out.label = {
      hasFor: label.getAttribute('for'),
      containsTarget: label.contains(input),
      nestedLabelCount: label.querySelectorAll('label').length,
    };
    out.doubleBinding = out.label.hasFor === 'impFiles' && out.label.containsTarget;
  }

  // 2. 尺寸与可见性
  const r = input.getBoundingClientRect();
  const cs = getComputedStyle(input);
  out.geometry = {
    w: Math.round(r.width), h: Math.round(r.height),
    top: Math.round(r.top), left: Math.round(r.left),
    display: cs.display, visibility: cs.visibility,
    opacity: cs.opacity, pointerEvents: cs.pointerEvents,
    position: cs.position,
  };
  out.zeroSize = r.width < 4 || r.height < 4;
  out.unclickable = cs.pointerEvents === 'none' || cs.visibility === 'hidden' || cs.display === 'none';

  // 3. 命中测试：在 label 中心点，最上层元素是谁
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const topEl = document.elementFromPoint(cx, cy);
  out.hitTest = {
    tag: topEl ? topEl.tagName : null,
    id: topEl ? topEl.id : null,
    cls: topEl ? (topEl.getAttribute('class') || '').slice(0, 40) : null,
    isInput: topEl === input,
    isInsideLabel: label ? label.contains(topEl) : false,
  };

  // 4. 有没有别的东西盖在上面
  if (topEl && topEl !== input && label && !label.contains(topEl)) {
    out.coveredBy = topEl.tagName + '.' + (topEl.getAttribute('class') || '');
  }
  return out;
})()
"""

CLICK_PROBE = r"""
(() => {
  // 挂一次性监听，看点击有没有走到 input，以及有没有被 preventDefault
  window.__impClicks = { inputClick: 0, labelClick: 0, prevented: 0, chooserOpened: 0 };
  const input = document.getElementById('impFiles');
  const label = document.querySelector('label[for="impFiles"]');
  if (input) input.addEventListener('click', e => {
    window.__impClicks.inputClick++;
    if (e.defaultPrevented) window.__impClicks.prevented++;
  }, true);
  if (label) label.addEventListener('click', () => { window.__impClicks.labelClick++; }, true);
  // 现代浏览器在打开选择器时会派发 cancel 事件；能收到说明真的尝试打开了
  if (input) input.addEventListener('cancel', () => { window.__impClicks.chooserOpened++; });
  return true;
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
    profile = tempfile.mkdtemp(prefix="cdp-diag-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
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
        # 手机视口 + 触控，贴近 WebView 环境
        send("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})

        send("Page.navigate", {"url": BASE + "/import"})
        time.sleep(1.4)

        res = send("Runtime.evaluate",
                   {"expression": PROBE, "returnByValue": True, "awaitPromise": True})
        info = res.get("result", {}).get("value") or {}
        print(json.dumps(info, ensure_ascii=False, indent=1))

        if info.get("doubleBinding"):
            problems.append(
                "label[for=impFiles] 同时包裹 input#impFiles —— 双重关联，"
                "部分 WebView 会把点击转发两次或直接吞掉")
        if info.get("zeroSize"):
            problems.append(f"input 尺寸为 0：{info.get('geometry')}")
        if info.get("unclickable"):
            problems.append(f"input 不可点：{info.get('geometry')}")
        if info.get("coveredBy"):
            problems.append(f"input 被其他元素覆盖：{info['coveredBy']}")
        # 命中测试要落在 input 或它的容器里（现在不再依赖 label）
        hit = info.get("hitTest") or {}
        if not (hit.get("isInput") or hit.get("isInsideLabel")):
            problems.append(f"命中测试没落在可点区域：{hit}")

        # 真点一次，看事件有没有走到 input
        send("Runtime.evaluate", {"expression": CLICK_PROBE, "returnByValue": True})
        g = info.get("geometry") or {}
        cx, cy = g.get("left", 0) + g.get("w", 0) / 2, g.get("top", 0) + g.get("h", 0) / 2
        for t in ("mousePressed", "mouseReleased"):
            send("Input.dispatchMouseEvent", {
                "type": t, "x": cx, "y": cy, "button": "left", "clickCount": 1,
                "pointerType": "touch",
            })
        time.sleep(0.8)
        res2 = send("Runtime.evaluate",
                    {"expression": "window.__impClicks", "returnByValue": True})
        clicks = res2.get("result", {}).get("value") or {}
        print(f"\n点击结果: {clicks}")
        if clicks.get("inputClick", 0) == 0:
            problems.append("点击没有到达 input[type=file] —— 这就是「点了没反应」")
        if clicks.get("prevented", 0) > 0:
            problems.append("有监听器对 input 的 click 调了 preventDefault")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    if problems:
        print(f"\n发现 {len(problems)} 个问题：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print("\n导入页交互正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
