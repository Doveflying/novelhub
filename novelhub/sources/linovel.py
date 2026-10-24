"""轻之文库（linovel.net）适配器。

- 书籍页：``/book/<id>.html``，正文分卷在 ``.volume`` 里，章节链接 ``/book/<id>/<cid>.html``
- 章节页：``/book/<id>/<cid>.html``，正文容器 ``.article-content``，单页全文（服务器渲染）
- 页面自带简繁切换，默认简体（``?cht=1`` 才是繁体）
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..net import Fetcher
from ..textutil import clean_title, container_to_html, html_to_text, strip_markers
from .base import BookMeta, Catalog, ChapterRef, Source, VolumeRef

_BOOK_RE = re.compile(r"linovel\.net/(?:book/)?(\d+)")
# 正文里混着的页头信息
NOISE = re.compile(r"^\s*(更新时间|本章字数|本章字數|字数|字數)\s*[:：]")
_NOISE_URL = re.compile(r"^(javascript:|#)")
# 页面上的行动按钮，不是章节
_UI_LABELS = {"开始阅读", "继续阅读", "立即阅读", "加入书架", "已加入书架", "收藏", "推荐", "打赏"}


def _is_chapter_link(name: str) -> bool:
    return bool(name) and name.strip() not in _UI_LABELS


class LinovelSource(Source):
    id = "linovel"
    name = "轻之文库（linovel.net）"
    hosts = ("linovel.net",)

    def match(self, url: str) -> bool:
        return "linovel.net" in url

    def book_id(self, url: str) -> str:
        m = _BOOK_RE.search(url)
        return m.group(1) if m else ""

    def canonical(self, url: str) -> str:
        bid = self.book_id(url)
        p = re.match(r"(https?://[^/]+)", url)
        base = p.group(1) if p else "https://www.linovel.net"
        return f"{base}/book/{bid}.html" if bid else url

    # ---------------- 目录 ----------------
    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        book_url = self.canonical(url)
        bid = self.book_id(book_url)
        html = fetcher.get(book_url)
        soup = BeautifulSoup(html, "lxml")

        def pick(selectors: list[str]) -> str:
            for sel in selectors:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    return el.get_text(" ", strip=True)
            return ""

        def meta(prop: str) -> str:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            return (tag.get("content") or "").strip() if tag else ""

        title = pick(["h1", ".book-title"]) or meta("og:title")
        if not title and soup.title:
            title = soup.title.get_text(strip=True).split("_")[0]
        author = pick(["a[href*='/author/']", ".book-author", ".author"])
        if not author:
            m = re.search(r"作\s*者\s*[:：]?\s*([^\s|<>]{2,16})", soup.get_text(" ", strip=True))
            author = m.group(1) if m else ""
        cover = ""
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if re.search(r"(cover|/book/)", src):
                cover = urljoin(book_url, src)
                break
        desc = meta("og:description") or meta("description")
        category = ""
        m = re.search(r"\|([^|]{1,12})\|", soup.title.get_text() if soup.title else "")
        if m:
            category = m.group(1)

        volumes: list[VolumeRef] = []
        chapters: list[ChapterRef] = []
        seen: set[str] = set()
        idx = 0
        chapter_re = re.compile(rf"/book/{bid}/(\d+)\.html")

        blocks = soup.select(".volume") or [soup]
        for vi, block in enumerate(blocks):
            head = block.select_one(".volume-title, .volume-name, h3, h4")
            vtitle = clean_title(head.get_text(" ", strip=True)) if head else ""
            if not vtitle:
                vtitle = f"第{vi + 1}卷" if len(blocks) > 1 else "正文"
            volumes.append(VolumeRef(idx=vi, title=vtitle, url=""))
            for a in block.select(f'a[href*="/book/{bid}/"]'):
                href = a.get("href", "")
                if _NOISE_URL.match(href) or not chapter_re.search(href):
                    continue
                full = urljoin(book_url, href)
                if full in seen:
                    continue
                name = a.get_text(" ", strip=True)
                if not _is_chapter_link(name):
                    continue
                seen.add(full)
                chapters.append(
                    ChapterRef(idx=idx, title=clean_title(name), url=full, vol_idx=vi)
                )
                idx += 1

        # 兜底：整页找章节链接
        if not chapters:
            for a in soup.select(f'a[href*="/book/{bid}/"]'):
                href = a.get("href", "")
                if _NOISE_URL.match(href) or not chapter_re.search(href):
                    continue
                full = urljoin(book_url, href)
                if full in seen:
                    continue
                name = a.get_text(" ", strip=True)
                if not _is_chapter_link(name):
                    continue
                seen.add(full)
                chapters.append(
                    ChapterRef(
                        idx=idx,
                        title=clean_title(name) or f"第{idx + 1}章",
                        url=full,
                        vol_idx=0,
                    )
                )
                idx += 1
            if chapters:
                volumes = [VolumeRef(idx=0, title="正文", url="")]

        meta_obj = BookMeta(
            source=self.id,
            source_id=bid,
            url=book_url,
            title=clean_title(title) or f"linovel-{bid}",
            author=author,
            cover=cover,
            category=category,
            status="",
            description=desc,
        )
        return Catalog(meta=meta_obj, volumes=volumes, chapters=chapters)

    # ---------------- 搜索（聚合搜索用） ----------------
    def search(
        self, fetcher: Fetcher, keyword: str, limit: int = 20, use_cache: Optional[bool] = None
    ) -> list[dict]:
        """站内搜索。

        正确的入口是 ``/search/?kw=关键词``（导航栏表单的 action）。
        旧版 ``search.html?q=`` 早已失效：参数被忽略，永远返回随机推荐书，
        所以这里必须用 ``/search/?kw=``，结果在 ``.rank-book-list a.search-book``。
        """
        from urllib.parse import quote

        url = f"https://www.linovel.net/search/?kw={quote(keyword)}"
        html = fetcher.get(url, use_cache=use_cache)
        soup = BeautifulSoup(html, "lxml")
        out: list[dict] = []
        seen: set[str] = set()

        for a in soup.select(".rank-book-list a.search-book, a.search-book"):
            href = a.get("href") or ""
            m = re.search(r"/book/(\d+)\.html", href)
            if not m:
                continue
            bid = m.group(1)
            if bid in seen:
                continue
            seen.add(bid)

            title_el = a.select_one(".book-name, .book-title, .book-meta .name")
            title = clean_title(
                (title_el.get_text(" ", strip=True) if title_el else "") or a.get("title", "")
            )
            author_el = a.select_one(".book-author, .author")
            author = (author_el.get_text(" ", strip=True) if author_el else "").strip()
            author = re.sub(r"^[/\s]+|[/\s]*著$", "", author)[:30]
            img = a.find("img")
            cover = (img.get("data-src") or img.get("src") or "") if img else ""

            out.append(
                {
                    "source": self.id,
                    "source_id": bid,
                    "title": (title or f"book-{bid}")[:60],
                    "author": author,
                    "cover": cover,
                    "url": f"https://www.linovel.net/book/{bid}.html",
                    "kind": "novel",
                }
            )
            if len(out) >= limit:
                break
        return out

    # ---------------- 章节 ----------------
    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        html = fetcher.get(chapter.url, referer=book_url)
        soup = BeautifulSoup(html, "lxml")
        box = (
            soup.select_one(".article-content")
            or soup.select_one(".chapter-content")
            or soup.select_one("article")
        )
        if box is None:
            raise RuntimeError(f"未找到正文容器 .article-content：{chapter.url}")

        # 去掉页头信息（更新时间 / 本章字数）和重复的标题行
        for p in box.find_all("p"):
            t = p.get_text(" ", strip=True)
            if NOISE.match(t):
                p.decompose()
        first_p = box.find("p")
        if first_p and clean_title(first_p.get_text(" ", strip=True)) == clean_title(
            chapter.title
        ):
            first_p.decompose()

        frag = strip_markers(container_to_html(box, chapter.url))
        return html_to_text(frag), frag
