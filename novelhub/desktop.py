"""书萤 · 桌面启动器（独立 exe 入口）。

双击 exe 之后要发生的事，按顺序：

1. 单实例：再点一次只把已有窗口叫到前台，不开第二个服务。
2. 选端口：优先 8899，被占就往后找，最后交给系统分配。
3. 起 Flask（线程模式）在后台，等到 /healthz 真的能回 200 才算启动完成。
4. 用 Edge/Chrome 的 ``--app`` 模式开一个无地址栏窗口 —— 观感上就是个原生应用，
   不额外背一个 WebView 运行时。找不到 Chromium 系浏览器就退回默认浏览器。
5. 关掉窗口 = 进程退出（``--app`` 窗口关闭后浏览器进程结束，我们检测到后收摊）。

命令行：
    FireflyLibrary.exe                 正常启动
    FireflyLibrary.exe --no-window     只起服务，手动开浏览器（调试用）
    FireflyLibrary.exe --port 9000     指定端口
    FireflyLibrary.exe --console       保留控制台（排查问题）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "书萤"
APP_ID = "FireflyLibrary"


# ---------------------------------------------------------------- 单实例
def _lock_path() -> Path:
    from novelhub import paths

    return paths.data_root() / ".instance.lock"


def _acquire_single_instance() -> bool:
    """用独占文件句柄当锁。拿不到说明已经有一个在跑。"""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(path, "a+")
    except OSError:
        return True  # 锁文件都建不了就别拦着启动了
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    # 句柄活到进程结束，锁自然释放
    _KEEP.append(fh)
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return True


_KEEP: list = []


def _read_running_port() -> int | None:
    """从锁文件旁边的小 json 里读上次用的端口，用来唤醒已有实例。"""
    p = _lock_path().with_name(".instance.port")
    try:
        return int(json.loads(p.read_text(encoding="utf-8"))["port"])
    except Exception:  # noqa: BLE001
        return None


def _write_running_port(port: int) -> None:
    p = _lock_path().with_name(".instance.port")
    try:
        p.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 端口
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int = 8899, tries: int = 40) -> int:
    if _port_free(preferred):
        return preferred
    for p in range(preferred + 1, preferred + 1 + tries):
        if _port_free(p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(port: int, timeout: float = 40.0) -> bool:
    url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


# ---------------------------------------------------------------- 浏览器
_CHROMIUM_CANDIDATES = [
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
    r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe",
]


def find_chromium() -> Path | None:
    for raw in _CHROMIUM_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.exists():
            return p
    return None


def open_app_window(browser: Path, url: str, profile_dir: Path) -> subprocess.Popen | None:
    """用 --app 模式开无地址栏窗口，独立 user-data-dir 避免污染用户浏览器。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--window-size=1440,940",
        "--force-device-scale-factor=1",
    ]
    try:
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return None


def open_fallback(url: str) -> None:
    webbrowser.open(url)


# ---------------------------------------------------------------- 主流程
def run_server(port: int) -> threading.Thread:
    from novelhub.config import load_config
    from novelhub.webapp.app import create_app

    cfg = load_config()
    app = create_app(cfg)
    # 关掉 werkzeug 的启动横幅与请求日志：exe 里没有控制台，写了也没人看
    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    def _serve() -> None:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)

    t = threading.Thread(target=_serve, name="flask", daemon=True)
    t.start()
    return t


def _window_proc_for(pid: int) -> int:
    """按 PID 找到该进程的顶层窗口，返回 hwnd（找不到返回 0）。"""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(_cb, 0)
        return found[0] if found else 0
    except Exception:  # noqa: BLE001
        return 0


def _wait_for_app_window(proc: subprocess.Popen, timeout: float = 25.0) -> int:
    """等 --app 窗口真的出现，返回 hwnd；超时返回 0。

    直接 poll 进程退出不可靠：Chromium 起 --app 时常常由已有浏览器进程接管，
    启动器进程秒退但窗口还在。所以以「该 PID 的可见顶层窗口」为准。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None and proc.returncode not in (0, None):
            return 0
        hwnd = _window_proc_for(proc.pid)
        if hwnd:
            return hwnd
        time.sleep(0.3)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=APP_ID, add_help=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--no-window", action="store_true", help="只起服务，不自动开窗口")
    ap.add_argument("--console", action="store_true", help="保留控制台输出")
    ap.add_argument("--browser", default="", help="指定浏览器 exe 路径")
    args = ap.parse_args(argv)

    if not _acquire_single_instance():
        port = _read_running_port()
        if port:
            url = f"http://127.0.0.1:{port}/"
            browser = find_chromium()
            if browser and not args.no_window:
                open_app_window(browser, url, _profile_dir())
            else:
                open_fallback(url)
        return 0

    port = pick_port(args.port)
    run_server(port)
    if not wait_ready(port):
        print("服务启动失败：/healthz 无响应", file=sys.stderr)
        return 1
    _write_running_port(port)
    url = f"http://127.0.0.1:{port}/"

    if args.no_window:
        print(f"{APP_NAME} 已启动：{url}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    browser = Path(args.browser) if args.browser else find_chromium()
    if browser is None or not browser.exists():
        open_fallback(url)
        # 退回默认浏览器时没法感知窗口关闭，只能挂着等用户从托盘/任务管理器结束
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    proc = open_app_window(browser, url, _profile_dir())
    if proc is None:
        open_fallback(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    hwnd = _wait_for_app_window(proc)
    if hwnd == 0:
        # 窗口没等到（可能被已有实例接管），保持服务存活
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    # 有窗口了：轮询窗口是否还在，没了就退出（=关窗即退）
    import ctypes

    user32 = ctypes.windll.user32
    while True:
        time.sleep(1.0)
        if not user32.IsWindow(hwnd):
            return 0
        if proc.poll() is not None and proc.returncode not in (0, None):
            return 0


def _profile_dir() -> Path:
    from novelhub import paths

    return paths.data_root() / "window-profile"


if __name__ == "__main__":
    raise SystemExit(main())
