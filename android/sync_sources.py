"""把 novelhub 包与前端资源同步进 Android 工程。

    python android/sync_sources.py

为什么需要这一步：Chaquopy 只打包 ``src/main/python`` 下的内容，
而 novelhub 包在仓库根。用拷贝而不是 symlink —— Windows 上 symlink
要管理员权限，而且 gradle 对 junction 的处理不一致。

同步内容：
    novelhub/*.py, novelhub/data/, novelhub/jsruntime/, novelhub/sources/
    novelhub/webapp/{templates,static}
    refs/anime/rules.json, refs/venera/{configs-index.json,sources/}
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "android" / "app" / "src" / "main" / "python"

# 目录级同步：(源, 目标相对 DEST)
DIRS = [
    ("novelhub", "novelhub"),
    ("refs/anime", "refs/anime"),
]

# 单文件
FILES = [
    ("novelhub/webapp/static/manifest.webmanifest", "novelhub/webapp/static/manifest.webmanifest"),
]

# 显式排除：这些是开发期产物或体积过大
EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    drop = set()
    for n in names:
        p = Path(_dir) / n
        if p.is_dir() and n in EXCLUDE_DIRS:
            drop.add(n)
        elif p.is_file() and p.suffix in EXCLUDE_SUFFIX:
            drop.add(n)
    return drop


def copy_tree(src: Path, dst: Path) -> int:
    if not src.exists():
        return 0
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=_ignore)
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def main() -> int:
    if not DEST.exists():
        DEST.mkdir(parents=True, exist_ok=True)

    total = 0
    for src_rel, dst_rel in DIRS:
        src = ROOT / src_rel
        dst = DEST / dst_rel
        n = copy_tree(src, dst)
        print(f"  {src_rel:28s} -> {dst_rel:28s} {n:5d} files")
        total += n

    for src_rel, dst_rel in FILES:
        src = ROOT / src_rel
        dst = DEST / dst_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total += 1
            print(f"  {src_rel:28s} -> {dst_rel}")

    # venera 源：可选，体积 1MB，有就带上
    vsrc = ROOT / "refs" / "venera"
    vdst = DEST / "refs" / "venera"
    if (vsrc / "configs-index.json").exists():
        vdst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vsrc / "configs-index.json", vdst / "configs-index.json")
        total += 1
        if (vsrc / "sources").exists():
            n = copy_tree(vsrc / "sources", vdst / "sources")
            total += n
            print(f"  refs/venera/sources          -> refs/venera/sources         {n:5d} files")

    # 清掉可能被拷进来的 pycache
    for p in DEST.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)

    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"\n同步完成：{total} 个文件，{size / 1024 / 1024:.1f} MB -> {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
