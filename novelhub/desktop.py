"""书萤 · 桌面启动器（独立 exe 入口）。

双击 exe 之后要发生的事，按顺序：

1. 单实例：再点一次只把已有窗口叫到前台，不开第二个服务。
2. 选端口：优先 8899，被占就往后找，最后交给系统分配。
3. 起 Flask（线程模式）在后台，等到 /healthz 真的能回 200 才算启动完成。
4. 用 Edge/Chrome 的 ``--app`` 模式开一个无地址栏窗口 —— 观感上就是个原生应用，
   不额外背一个 WebView 运行时。窗口尺寸按屏幕工作区自适应、缩放跟随系统 DPI。
   找不到 Chromium 系浏览器就退回默认浏览器。
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


# ---------------------------------------------------------------- 屏幕/窗口尺寸
def _screen_work_area() -> tuple[int, int]:
    """主屏工作区尺寸，单位 CSS 像素（DIP）。

    Chromium 的 ``--window-size/--window-position`` 用的就是 DIP，
    所以必须换成和浏览器一致的单位，否则高 DPI 屏上窗口会忽大忽小。

    矩形坐标和 DPI 会随进程的 DPI 感知状态一起虚拟化（感知时返回物理像素
    + 真实 DPI，未感知时返回虚拟化像素 + 96），两者相除正好抵消，
    所以感知与否算出来的 DIP 尺寸一致。取不到就退 1600x1000。
    """
    if os.name != "nt":
        return 1600, 1000
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
        if not hmon:
            raise OSError("no primary monitor")
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            raise OSError("GetMonitorInfoW failed")
        w = max(mi.rcWork.right - mi.rcWork.left, 0)
        h = max(mi.rcWork.bottom - mi.rcWork.top, 0)
        dpi = 96
        try:
            ux, uy = wintypes.UINT(), wintypes.UINT()
            # MDT_EFFECTIVE_DPI = 0；shcore 是 Win8.1+，没有就按 96 算
            if ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(ux), ctypes.byref(uy)) == 0 and ux.value:
                dpi = ux.value
        except Exception:  # noqa: BLE001
            pass
        scale = dpi / 96.0 if dpi else 1.0
        return max(int(w / scale), 640), max(int(h / scale), 480)
    except Exception:  # noqa: BLE001
        return 1600, 1000


def _window_rect() -> tuple[int, int, int, int]:
    """应用窗口的 (宽, 高, x, y)，单位 DIP，在主屏工作区内居中。

    原来写死 ``1440x940`` 还带 ``--force-device-scale-factor=1``：
    2560x1600 / 150% 缩放的屏上，窗口只有巴掌大、字还是 1 倍渲染，没法看。
    现在按工作区给「几乎占满但留边距」的尺寸，缩放跟随系统。
    """
    w, h = _screen_work_area()
    width = min(1920, max(1080, int(w * 0.94)))
    height = min(1280, max(720, int(h * 0.92)))
    x = max(0, (w - width) // 2)
    y = max(0, (h - height) // 2)
    return width, height, x, y


def open_app_window(browser: Path, url: str, profile_dir: Path) -> subprocess.Popen | None:
    """用 --app 模式开无地址栏窗口，独立 user-data-dir 避免污染用户浏览器。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    width, height, x, y = _window_rect()
    args = [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        f"--window-size={width},{height}",
        f"--window-position={x},{y}",
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


_BROWSER_EXES = {"msedge.exe", "chrome.exe", "brave.exe", "opera.exe", "vivaldi.exe", "chromium.exe"}


def _window_exe(hwnd: int) -> str:
    """取窗口所属进程的 exe 文件名（小写）。失败返回空串。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return ""


def _find_app_windows() -> list[int]:
    """找出「书萤应用窗口」——浏览器进程 + 标题含 APP_NAME 的可见顶层窗口。

    两个判据都要，缺一不可：

    - 只按标题：快捷方式指向的 dist\\书萤 文件夹，在资源管理器里标题是
      「书萤 - 文件资源管理器」，也会命中，于是启动器永远以为窗口还在，
      用户关了应用进程也不退出。
    - 只按进程：Chromium 起 --app 时常常把窗口交给已在运行的浏览器主进程，
      我们 spawn 的那个进程秒退，按 PID 找不到。

    所以：进程必须是浏览器，标题必须含 APP_NAME。
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []
        buf = ctypes.create_unicode_buffer(512)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextW(hwnd, buf, 512)
            if not n or APP_NAME not in buf.value:
                return True
            if _window_exe(hwnd) in _BROWSER_EXES:
                found.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        return found
    except Exception:  # noqa: BLE001
        return []


def _wait_for_app_window(timeout: float = 30.0) -> bool:
    """等应用窗口出现。出现返回 True，超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _find_app_windows():
            return True
        time.sleep(0.4)
    return False


def _profile_dir() -> Path:
    from novelhub import paths

    return paths.data_root() / "window-profile"


def _clear_port_file() -> None:
    """退出时把 .instance.port 清掉，否则下次启动会拿旧端口去唤醒不存在的实例。"""
    try:
        p = _lock_path().with_name(".instance.port")
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _message_box(text: str, title: str = APP_NAME, error: bool = False) -> None:
    """windowed exe 没有控制台，出错必须弹窗，否则用户只看到「双击没反应」。"""
    try:
        import ctypes

        flags = 0x00000010 if error else 0x00000040  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:  # noqa: BLE001
        print(f"{title}: {text}", file=sys.stderr)


def _install_crash_log() -> Path | None:
    """把未捕获异常写到数据目录，方便用户把文件发回来。"""
    import traceback

    try:
        from novelhub import paths

        log = paths.data_root() / "startup-error.log"
    except Exception:  # noqa: BLE001
        return None

    def _hook(exc_type, exc, tb):
        try:
            log.write_text(
                "".join(traceback.format_exception(exc_type, exc, tb)), encoding="utf-8"
            )
        except OSError:
            pass
        _message_box(
            f"启动时出错：{exc_type.__name__}: {exc}\n\n"
            f"详细信息已写入：\n{log}",
            error=True,
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=APP_ID, add_help=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--no-window", action="store_true", help="只起服务，不自动开窗口")
    ap.add_argument("--console", action="store_true", help="保留控制台输出")
    ap.add_argument("--browser", default="", help="指定浏览器 exe 路径")
    args = ap.parse_args(argv)

    _install_crash_log()

    # 已经有实例在跑：把它的窗口叫到前台，别再起一个
    if not _acquire_single_instance():
        port = _read_running_port()
        if not port:
            _message_box("书萤已经在运行，但读不到它的端口。\n"
                         "请先在任务管理器结束「书萤」进程再试。")
            return 0
        url = f"http://127.0.0.1:{port}/"
        browser = find_chromium()
        if browser and not args.no_window:
            open_app_window(browser, url, _profile_dir())
        else:
            open_fallback(url)
        return 0

    port = pick_port(args.port)
    try:
        run_server(port)
    except Exception as exc:  # noqa: BLE001
        _message_box(f"服务启动失败：{exc}", error=True)
        return 1

    if not wait_ready(port):
        _message_box(
            "服务没有在 40 秒内就绪。\n\n"
            "常见原因：\n"
            "  · 8899 附近端口被占用（关掉其他书萤实例）\n"
            "  · 数据目录不可写（检查 exe 所在文件夹权限）",
            error=True,
        )
        return 1

    _write_running_port(port)
    url = f"http://127.0.0.1:{port}/"

    try:
        if args.no_window:
            print(f"{APP_NAME} 已启动：{url}")
            while True:
                time.sleep(3600)

        browser = Path(args.browser) if args.browser else find_chromium()
        if browser is None or not browser.exists():
            open_fallback(url)
            # 退回默认浏览器时没法感知窗口关闭，只能挂着
            while True:
                time.sleep(3600)

        proc = open_app_window(browser, url, _profile_dir())
        if proc is None:
            open_fallback(url)
            while True:
                time.sleep(3600)

        if not _wait_for_app_window(30.0):
            # 窗口没等到：可能浏览器把它当标签页开了，或启动很慢。
            # 这时不能直接退出（服务刚起来），保持存活让用户自己关。
            while True:
                time.sleep(3600)

        # 窗口已在：轮询它是否还在。连续多秒都看不到才算关闭，
        # 避免页面跳转瞬间标题变化导致误判。
        #
        # 只按标题会踩一个坑：页面加载时标题先是 URL、不含应用名，
        # 加载一慢（首次运行还要初始化 profile）启动器就以为用户关了窗口，
        # 一收摊背后的 Flask 服务也跟着停，浏览器窗口变成连不上的僵尸。
        # 所以「我们 spawn 的浏览器进程还活着」也算应用还在：
        # --user-data-dir 是独立的，这个进程正常会活到窗口关闭。
        gone = 0
        while True:
            time.sleep(1.0)
            if _find_app_windows():
                gone = 0
                continue
            proc_alive = proc is not None and proc.poll() is None
            gone += 1
            # 进程还在：标题抖动、关窗后进程残留，都多等等（上限 60 秒）；
            # 进程没了：窗口被交给了别的浏览器进程，只能靠标题，宽限 15 秒。
            if gone >= (60 if proc_alive else 15):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        _clear_port_file()
