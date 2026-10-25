"""角色系统交互验收：图鉴开合 / 选卡 / 跟随页面 / 显示开关 / 持久化。"""
import json
import sys
import time

sys.path.insert(0, "design")
from _audit import Session, BASE  # noqa: E402


def main():
    s = Session()
    log = {}
    try:
        s.viewport(1440, 1000)
        s.send("Page.navigate", {"url": BASE + "/"})
        time.sleep(1.2)
        s.eval("localStorage.removeItem('nh-cast'); localStorage.removeItem('nh-mascot'); location.reload();")
        time.sleep(1.4)

        # 1. 打开图鉴
        s.eval("document.querySelector('.rail-btn[data-cast-open]').click()")
        time.sleep(0.6)
        log["open"] = s.eval("JSON.stringify({open: document.querySelector('.cast-modal').classList.contains('open') || getComputedStyle(document.querySelector('.cast-modal')).display, cards: document.querySelectorAll('.cast-card').length})")
        s.shot("design/shots/cast-modal-d.png", full=False)

        # 2. 选美玖（常驻）
        s.eval("document.querySelector('[data-cast-pick=\"miku\"]').click()")
        time.sleep(0.8)
        log["pick"] = s.eval("JSON.stringify({mascot: document.querySelector('#mascotImg').getAttribute('src'), rail: document.querySelector('#castRailImg').getAttribute('src'), name: document.querySelector('#mascotName').textContent.trim(), stored: localStorage.getItem('nh-cast')})")
        s.shot("design/shots/cast-picked-d.png", full=False)

        # 3. 跟随页面
        s.eval("document.querySelector('#castAuto').click()")
        time.sleep(0.5)
        log["auto"] = s.eval("JSON.stringify({stored: localStorage.getItem('nh-cast'), mascot: document.querySelector('#mascotImg').getAttribute('src')})")

        # 4. 关掉看板娘
        s.eval("document.querySelector('#castShow').click()")
        time.sleep(0.5)
        log["hide"] = s.eval("JSON.stringify({mascot: localStorage.getItem('nh-mascot'), display: getComputedStyle(document.querySelector('#mascot')).display})")

        # 5. 关图鉴，刷新后检查持久化
        s.eval("document.querySelector('.cast-modal').querySelector('[data-cast-close]') ? document.querySelector('.cast-modal').querySelector('[data-cast-close]').click() : document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        time.sleep(0.4)
        s.send("Page.navigate", {"url": BASE + "/"})
        time.sleep(1.2)
        log["reload"] = s.eval("JSON.stringify({cast: localStorage.getItem('nh-cast'), mascot: localStorage.getItem('nh-mascot'), img: document.querySelector('#mascotImg').getAttribute('src')})")

        # 6. 恢复默认
        s.eval("localStorage.removeItem('nh-cast'); localStorage.setItem('nh-mascot','on');")
        print(json.dumps(log, ensure_ascii=False, indent=1))
    finally:
        s.close()


if __name__ == "__main__":
    main()
