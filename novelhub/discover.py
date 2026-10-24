"""站点列表页 → 书单（用于批量建目录索引）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup

from .config import Config
from .net import Fetcher
from .sources import SourceRegistry

# 列表页里书籍链接的形态（按站点顺序匹配）
BOOK_LINK_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/novel/(\d+)(?:\.html|/catalog|/vol_\d+\.html)?/?$"), "linovelib"),
    (re.compile(r"/book/(\d+)\.html$"), "linovel"),
]


@dataclass
class DiscoveredBook:
    source: str
    source_id: str
    url: str
    title: str = ""
    author: str = ""
    cover: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "cover": self.cover,
        }


def _site_hint(url: str) -> str:
    if "linovelib" in url or "bilinovel" in url:
        return "linovelib"
    if "linovel.net" in url:
        return "linovel"
    return ""


def _nearest_text(el, selectors: Iterable[str]) -> str:
    for sel in selectors:
        node = el.select_one(sel) if hasattr(el, "select_one") else None
        if node and node.get_text(strip=True):
            return node.get_text(" ", strip=True)
    # 往父级找
    parent = el.parent
    for _ in range(3):
        if parent is None:
            break
        for sel in selectors:
            node = parent.select_one(sel)
            if node and node.get_text(strip=True):
                return node.get_text(" ", strip=True)
        parent = parent.parent
    return ""


def expand_list_urls(url: str, pages: int = 1) -> list[str]:
    """把一个列表页展开成多页。

    支持三种写法：
    - 显式占位：`.../list.html?page={p}`
    - 尾部数字：`.../allvisit/1.html` -> 2.html、3.html …
    - 其它：只返回原 URL
    """
    pages = max(1, int(pages or 1))
    if pages == 1:
        return [url]
    if "{p}" in url:
        return [url.replace("{p}", str(i)) for i in range(1, pages + 1)]
    m = re.search(r"/(\d+)\.html$", url)
    if m:
        base, n = url[: m.start()], int(m.group(1))
        return [f"{base}/{n + i}.html" for i in range(pages)]
    return [url]


def discover(cfg: Config, registry: SourceRegistry, url: str, limit: int = 60) -> list[DiscoveredBook]:
    """抓一个列表页（排行榜 / 分类 / 搜索结果），解析出书单。"""
    with Fetcher(cfg) as f:
        html = f.get(url)
    soup = BeautifulSoup(html, "lxml")
    hint = _site_hint(url)

    seen: set[tuple[str, str]] = set()
    books: list[DiscoveredBook] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        matched = False
        for pattern, source_id in BOOK_LINK_RES:
            m = pattern.search(href)
            if not m:
                continue
            if hint and source_id != hint:
                continue
            key = (source_id, m.group(1))
            if key in seen:
                matched = True
                break
            seen.add(key)
            src = registry.get(source_id)
            book_url = (
                src.canonical(href if href.startswith("http") else "https://" + url.split("/")[2] + href)
                if src
                else href
            )
            cover = ""
            img = a.find("img")
            if img is None:
                img = a.find_next("img")
            if img is not None:
                cover = img.get("data-src") or img.get("src") or ""
            title = (
                _nearest_text(a, [".book-name", ".book-title", "h3", "h4", "b", "span"])
                or (a.get("title") or "").strip()
                or a.get_text(" ", strip=True)[:40]
            )
            books.append(
                DiscoveredBook(
                    source=source_id,
                    source_id=key[1],
                    url=book_url,
                    title=title,
                    author=_nearest_text(a, [".book-author", ".author", "em"]),
                    cover=cover,
                )
            )
            matched = True
            break
        if len(books) >= limit:
            break
    return books
