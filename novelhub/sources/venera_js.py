"""Venera 兼容的 JS 源：安装 / 调用 / 落成本地漫画。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

from ..config import Config
from ..net import Fetcher
from .. import paths

# 只读：随程序发布的 refs/venera（索引、内置源、runner）
ROOT = paths.resource_root()
SOURCES_DIR = ROOT / "refs" / "venera" / "sources"
RUNNER = paths.resource("jsruntime", "runner.mjs")
INDEX_URL = "https://cdn.jsdelivr.net/gh/venera-app/venera-configs@main/index.json"
SOURCE_URL = "https://cdn.jsdelivr.net/gh/venera-app/venera-configs@main/{file}"


def _writable_sources_dir() -> Path:
    """用户新装的源要写盘：冻结后 refs/ 在只读包里，得落到数据目录。"""
    if paths.is_frozen():
        p = paths.data_root() / "data" / "venera" / "sources"
        p.mkdir(parents=True, exist_ok=True)
        return p
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    return SOURCES_DIR


def _source_file(name: str) -> Path | None:
    """先找用户安装目录，再找随包内置目录。"""
    for base in (_writable_sources_dir(), SOURCES_DIR):
        p = base / name
        if p.exists() and p.stat().st_size > 500:
            return p
    return None


# ------------------------------------------------------------------ 执行器选择
# Android 上没有 node，也装不进来（Chaquopy 的索引里没有任何 JS 引擎），
# 改用系统 WebView 的 V8：Java 侧 JsBridge 起一个隐藏 WebView，
# bookfly/jshost.py 提供 HTTP/加密/存储能力。
_IS_ANDROID = sys.platform == "android" or "ANDROID_DATA" in os.environ


def _android_runner():
    """返回 Android 执行器；不在 Android 或桥不可用时返回 None。"""
    if not _IS_ANDROID:
        return None
    try:
        import java  # noqa: F401  type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return None
    return _run_on_webview


def _run_on_webview(src: "JsSource", action: str, params: dict, timeout: int) -> Any:
    """用 WebView 的 V8 跑一个源动作。

    流程：
      1. 在 Python 侧登记 job 上下文（Fetcher / 键值存储路径）
      2. 调 Java JsBridge.eval()，它会阻塞直到 JS 把结果交回来
      3. JS 期间所有 Network.* / Convert.* 都同步回调到 bookfly.jshost.handle
    """
    from java import jclass  # type: ignore[import-not-found]

    from bookfly import jshost  # type: ignore[import-not-found]

    Bridge = jclass("com.firefly.library.JsBridge")
    bridge = Bridge.get(_android_context())

    job_id = f"job-{src.path.stem}-{time.time_ns()}"
    jshost.begin_job(job_id, store_path=str(src.store), cfg=src.cfg)
    try:
        raw = bridge.eval(
            job_id,
            src.path.read_text(encoding="utf-8", errors="replace"),
            action,
            json.dumps(params or {}, ensure_ascii=False),
            int(timeout * 1000),
        )
    finally:
        jshost.end_job(job_id)

    data = json.loads(str(raw))
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error"))[:300])
    return data.get("data")


def _android_context():
    """拿 Application 当 Context：不需要 Activity，生命周期也够长。"""
    from java import jclass  # type: ignore[import-not-found]

    return jclass("android.app.ActivityThread").currentApplication()


@dataclass
class SourceEntry:
    key: str
    name: str
    file: str
    version: str = ""
    description: str = ""


class VeneraRegistry:
    """读取 venera-configs 的源清单，按 key 安装到 refs/venera/sources。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.index_file = ROOT / "refs" / "venera" / "configs-index.json"
        self.sources_dir = _writable_sources_dir()

    def index(self) -> list[SourceEntry]:
        entries: list[SourceEntry] = []
        # 按「文件名小写」去重：Windows 文件系统不区分大小写，
        # 官方清单里的 ManHuaGui.js / Komiic.js 和本地多出来的
        # manhuagui.js / komiic.js 会解析到同一个文件，不去重就会
        # 在聚合搜索里把同一个源查两遍。
        seen: set[str] = set()
        if self.index_file.exists():
            try:
                raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            except Exception:
                raw = []
            for item in raw:
                file = item.get("fileName", "")
                key = item.get("key") or Path(file or "?").stem
                dedup = (file or key).lower()
                if dedup in seen:
                    continue
                seen.add(dedup)
                entries.append(
                    SourceEntry(
                        key=key,
                        name=item.get("name", ""),
                        file=file,
                        version=item.get("version", ""),
                        description=item.get("description", ""),
                    )
                )
        # 本地额外安装的源（比如用户丢进来的仓库压缩包），用户目录优先
        local_files: dict[str, Path] = {}
        for base in (self.sources_dir, SOURCES_DIR):
            for p in sorted(base.glob("*.js")):
                local_files.setdefault(p.name.lower(), p)
        for lname, p in local_files.items():
            if lname in seen:
                continue
            seen.add(lname)
            entries.append(self._entry_from_file(p))
        return entries

    @staticmethod
    def _entry_from_file(p: Path) -> SourceEntry:
        import re

        text = p.read_text(encoding="utf-8", errors="replace")[:4000]
        name = re.search(r'\bname\s*=\s*["\'`]([^"\'`]+)', text)
        key = re.search(r'\bkey\s*=\s*["\'`]([^"\'`]+)', text)
        ver = re.search(r'\bversion\s*=\s*["\'`]([^"\'`]+)', text)
        return SourceEntry(
            key=(key.group(1) if key else p.stem),
            name=(name.group(1) if name else p.stem),
            file=p.name,
            version=(ver.group(1) if ver else ""),
        )

    def refresh_index(self) -> int:
        with Fetcher(self.cfg) as f:
            data = json.loads(f.get(INDEX_URL, use_cache=False))
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(data)

    def install(self, key: str) -> Path:
        entry = next((e for e in self.index() if e.key == key), None)
        if entry is None:
            # 允许直接用文件名（ManHuaGui.js -> ManHuaGui）
            found = _source_file(f"{key}.js")
            if found is not None:
                return found
            raise RuntimeError(f"清单里没有源：{key}")
        existing = _source_file(entry.file)
        if existing is not None:
            return existing
        target = self.sources_dir / entry.file
        with Fetcher(self.cfg) as f:
            js = f.get(SOURCE_URL.format(file=entry.file), use_cache=False)
        target.write_text(js, encoding="utf-8")
        return target

    def installed(self) -> list[tuple[SourceEntry, Path]]:
        out = []
        for e in self.index():
            p = _source_file(e.file)
            if p is not None:
                out.append((e, p))
        return out


class JsSource:
    """一个已安装的 Venera 源，可执行 search / info / ep。"""

    def __init__(self, cfg: Config, path: str | Path):
        self.cfg = cfg
        self.path = Path(path)
        self.proxy = cfg.proxy
        if not self.path.exists():
            raise RuntimeError(f"源文件不存在：{self.path}")
        self.store = cfg.data_path / "jsruntime" / f"{self.path.stem}.json"

    # ---------------- 调用 ----------------
    def call(self, action: str, params: Optional[dict] = None, timeout: int = 120) -> Any:
        """跑一个源动作。执行器按平台自动选择：

        - Android：WebView 的 V8（APK 里没有 node，也没法塞进去）
        - 桌面：node 子进程跑 runner.mjs

        两条路返回同样的 ``{"ok": bool, "data"|"error"}``，上层不用关心。
        """
        runner = _android_runner() if _IS_ANDROID else None
        if runner is not None:
            return runner(self, action, params or {}, timeout)

        from ..config import detect_proxies

        candidates = [self.proxy] if self.proxy else []
        for p in detect_proxies():
            if p not in candidates:
                candidates.append(p)
        last_err: Exception | None = None
        for i, proxy in enumerate(candidates[:3] or [None]):
            payload = {
                "file": str(self.path),
                "action": action,
                "params": params or {},
                "proxy": proxy,
                "store": str(self.store),
            }
            try:
                proc = subprocess.run(
                    ["node", str(RUNNER), json.dumps(payload, ensure_ascii=False)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    # Windows 上必须加这个：不加的话每跑一次 JS 源（搜漫画/取详情）
                    # 都会弹出一个 node 控制台黑框，搜索时一直闪。
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "找不到 node。桌面端请装 Node.js 18+；"
                    "Android 端应由 WebView 引擎接管，走到这里说明桥没起来。"
                ) from exc
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                except Exception as exc:
                    raise RuntimeError(f"源返回的不是 JSON：{proc.stdout[:200]}") from exc
                if not data.get("ok"):
                    raise RuntimeError(str(data.get("error"))[:300])
                # 记住本实例命中的代理，不回写共享 cfg
                if proxy:
                    self.proxy = proxy
                return data["data"]
            last_err = RuntimeError(self._first_error(proc.stderr or proc.stdout or ""))
            if i + 1 < len(candidates):
                continue
        raise RuntimeError(f"JS 源执行失败：{last_err}")

    @staticmethod
    def _first_error(stderr: str) -> str:
        noise = (
            "node:", "at ", "^", "...", "}", "{", "(Use", "Node.js", "code:", "throw",
            "trigger", "Warning", "file:///", "host:", "port:", "localAddress", "address:",
            "syscall:", "errno:", "stack:", "path:", "cause:",
        )
        for line in stderr.splitlines():
            s = line.strip()
            if s and not s.startswith(noise):
                return s[:200]
        return (stderr.strip().splitlines() or ["unknown"])[-1][:200]

    # ---------------- 便捷方法 ----------------
    def meta(self) -> dict:
        return self.call("meta")

    def search(self, keyword: str, page: int = 1, timeout: int = 40) -> list[dict]:
        data = self.call("search", {"keyword": keyword, "page": page}, timeout=timeout) or {}
        return data.get("comics") or []

    def info(self, comic_id: str) -> dict:
        return self.call("info", {"id": str(comic_id)}) or {}

    def episode(self, comic_id: str, ep_id: str) -> list[str]:
        data = self.call("ep", {"comicId": str(comic_id), "epId": str(ep_id)}) or {}
        return data.get("images") or []


def download_images(
    cfg: Config, urls: list[str], out_dir: Path, referer: str = "", pause: float = 0.4
) -> list[Path]:
    """把一话的图片落盘，返回本地路径列表。"""
    import time

    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with Fetcher(cfg) as f:
        for i, u in enumerate(urls, 1):
            try:
                data = f.get_bytes(u, referer=referer or None)
            except Exception:
                continue
            ext = ".jpg"
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                ext = ".png"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                ext = ".webp"
            p = out_dir / f"{i:03d}{ext}"
            p.write_bytes(data)
            saved.append(p)
            time.sleep(pause)
    return saved


def gallery_html(title: str, images: list[Path]) -> str:
    body = "\n".join(f'<img src="{p.name}" alt="">' for p in images)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>
body{{margin:0;background:#141414;color:#ddd;font-family:system-ui}}
h1{{font-size:15px;font-weight:500;padding:12px 16px;margin:0;border-bottom:1px solid #2a2a2a}}
.wrap{{max-width:820px;margin:0 auto;padding:10px}}
img{{display:block;width:100%;height:auto;margin:0 0 6px;background:#222}}
</style></head><body><h1>{title} · {len(images)} 页</h1><div class="wrap">{body}</div></body></html>"""
