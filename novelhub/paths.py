"""运行路径解析：源码运行 / PyInstaller 冻结 exe / Android 打包三种形态。

三种「根」要分清，混用会导致打包后写不进数据或读不到静态资源：

- ``RESOURCE_ROOT``  只读资源根。源码运行时是仓库根；冻结后是 ``sys._MEIPASS``。
                    模板、static、内置词表、refs 规则都在这里。
- ``APP_ROOT``       可写根。源码运行时是仓库根；冻结后是 exe 所在目录
                    （便携模式，整个文件夹拷走即可）或用户数据目录。
- ``DATA_ROOT``      用户数据根（书库 / 缓存 / 导出）。优先级：
                    ``NOVELHUB_DATA_DIR`` 环境变量 > 冻结时的可写根 > 仓库根。

打包进 exe 的资源清单见 ``novelhub.spec``；这里只负责在运行时定位它们。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "FireflyLibrary"


def is_frozen() -> bool:
    """是否跑在 PyInstaller 冻结环境里。"""
    return bool(getattr(sys, "frozen", False))


def _meipass() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def resource_root() -> Path:
    """只读资源根（模板 / static / 内置数据）。"""
    if is_frozen():
        mp = _meipass()
        if mp is not None:
            return mp
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_root() -> Path:
    """可写根：config.json、便携数据默认落这里。"""
    env = os.environ.get("NOVELHUB_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """用户数据根（书库 db / 图片 / 缓存 / 导出的共同父目录）。

    ``data_dir`` / ``export_dir`` 这类相对路径都相对它解析，
    所以源码运行时 = 仓库根，冻结后 = exe 旁（不可写则退 %LOCALAPPDATA%）。
    """
    env = os.environ.get("NOVELHUB_DATA_DIR")
    if env:
        base = Path(env).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        return base
    if is_frozen():
        # 便携模式：exe 旁边；目录不可写时退到 %LOCALAPPDATA%
        portable = Path(sys.executable).resolve().parent
        try:
            probe = portable / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return portable
        except OSError:
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
            fallback = Path(base) / _APP_NAME
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    env = os.environ.get("NOVELHUB_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return app_root() / "config.json"


# 冻结时静态资源可能在 _MEIPASS/novelhub/... 或 _MEIPASS/... 两处，按序探测
def resource(*parts: str) -> Path:
    """在资源根下定位一个打包资源，返回第一个存在的路径（都不在则返回首个候选）。"""
    base = resource_root()
    candidates = [base.joinpath(*parts), base.joinpath("novelhub", *parts)]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]
