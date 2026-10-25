# -*- mode: python ; coding: utf-8 -*-
"""书萤 · Windows 独立 exe 打包脚本。

    python -m PyInstaller novelhub.spec --noconfirm --clean

产物：``dist/书萤/书萤.exe``（onedir，推荐）
    onedir 启动快（不用每次解包 40MB 到临时目录），杀软误报也少；
    整个 ``dist/书萤`` 文件夹拷走就能用，数据落在 exe 旁边的 ``data/``。

onefile 用 ``--onefile`` 变量切换：
    python -m PyInstaller novelhub.spec --noconfirm --clean -D  # 默认 onedir
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()
ONEFILE = False  # True → 单文件 exe（启动慢、易误报，但只有一个文件）

# ---------------------------------------------------------------- 数据文件
datas: list[tuple[str, str]] = []


def add(src: str | Path, dest: str) -> None:
    p = (ROOT / src).resolve()
    if p.exists():
        datas.append((str(p), dest))
    else:
        print(f"[spec] 跳过不存在的资源：{src}")


# Flask 应用本体：模板 + 静态资源（缺一个页面就 500）
add("novelhub/webapp/templates", "novelhub/webapp/templates")
add("novelhub/webapp/static", "novelhub/webapp/static")

# 内置数据：简繁词表
add("novelhub/data/opencc_s2t.txt", "novelhub/data")

# JS 运行时：Venera 源靠它跑（没有 node 时相关源自动降级）
add("novelhub/jsruntime/runner.mjs", "novelhub/jsruntime")

# 站点规则：番剧 api 源 + xpath 源
add("refs/anime/rules.json", "refs/anime")

# Venera 内置源清单（用户新装的源写到数据目录，这些只读）
add("refs/venera/configs-index.json", "refs/venera")
if (ROOT / "refs/venera/sources").exists():
    add("refs/venera/sources", "refs/venera/sources")

# 默认配置模板（首次启动拷到数据目录）
add("config.example.json", ".")

# ---------------------------------------------------------------- 隐式导入
hiddenimports = [
    # Flask 的 Jinja 扩展、模板过滤器在打包后容易漏
    "jinja2.ext",
    "jinja2.filters",
    "jinja2.utils",
    # 站点适配器都是显式 import，但 collect 一遍更稳（含未来的新源）
    *collect_submodules("novelhub.sources"),
    # 导出 / 导入格式
    "ebooklib",
    "ebooklib.epub",
    "ebooklib.utils",
    # 解析器
    "lxml",
    "lxml.etree",
    "lxml.html",
    "bs4",
    # 简繁转换（装了就带上，没装也能跑）
    "zhconv",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.ciphers.algorithms",
    "cryptography.hazmat.primitives.ciphers.modes",
]

excludes = [
    # 打包体积：这些用不到
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "IPython",
    "pytest",
    "setuptools",
    "pip",
    "wheel",
    "PIL.ImageQt",
    "PyQt5",
    "PySide2",
    "PySide6",
    "wx",
]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="书萤",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "build" / "icons" / "app.ico"),
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="书萤",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "build" / "icons" / "app.ico"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="书萤",
    )
