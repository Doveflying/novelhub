"""插图落地：把正文里的远程 <img> 下载到本地，并把 src 改成本地路径。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from .config import Config
from .net import Fetcher

IMG_RE = re.compile(r'<img[^>]*?src="([^"]+)"[^>]*?>', re.I)
_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def _ext(data: bytes, url: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg") else ".jpg"


def media_dir(cfg: Config, book_id: int) -> Path:
    p = cfg.data_path / "images" / str(book_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def localize_html(
    html: str, book_id: int, cfg: Config, fetcher: Fetcher
) -> tuple[str, int]:
    """把 html 里的远程图片下载到 data/images/<book_id>/，返回 (新 html, 下载张数)。

    已经是本地路径（/media/...）的会被跳过，所以重复调用是安全的。
    """
    if not html or "<img" not in html:
        return html, 0
    target = media_dir(cfg, book_id)
    downloaded = 0
    cache: dict[str, Optional[str]] = {}

    def repl(m: re.Match) -> str:
        nonlocal downloaded
        src = m.group(1)
        if not src or src.startswith("/media/") or src.startswith("data:"):
            return m.group(0)
        if src in cache:
            name = cache[src]
        else:
            name = None
            try:
                data = fetcher.get_bytes(src)
                if data:
                    name = hashlib.sha1(src.encode("utf-8")).hexdigest()[:16] + _ext(data, src)
                    path = target / name
                    if not path.exists() or path.stat().st_size != len(data):
                        path.write_bytes(data)
                        downloaded += 1
            except Exception:
                name = None
            cache[src] = name
        if not name:
            return m.group(0)  # 下载失败就保持外链，不影响阅读
        return f'<img src="/media/{book_id}/{name}" alt="插图">'

    return IMG_RE.sub(repl, html), downloaded
