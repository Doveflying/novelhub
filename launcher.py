"""书萤 · 独立应用入口（PyInstaller 打包用）。

和 ``nh.py`` 的区别：这个入口默认就是「起服务 + 开窗口」，
双击 exe 直接能用，不需要命令行参数。

    python launcher.py              起服务并开应用窗口
    python launcher.py --no-window  只起服务
    python launcher.py --console    保留控制台（排查用）

打包：``python -m PyInstaller novelhub.spec --noconfirm --clean``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 冻结后 __file__ 在 _MEIPASS 里，源码运行时把仓库根放进 sys.path
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fix_stdio() -> None:
    """windowed exe 没有 stdout/stderr，任何 print 都会炸。补成黑洞流。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def _ensure_config() -> None:
    """首次启动把 config.example.json 拷成 config.json，省得用户一脸懵。"""
    from novelhub import paths

    cfg_path = paths.config_path()
    if cfg_path.exists():
        return
    example = paths.resource("config.example.json")
    if not example.exists():
        return
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(example.read_text(encoding="utf-8-sig"), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _fix_stdio()
    _ensure_config()
    from novelhub.desktop import main as desktop_main

    return desktop_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
