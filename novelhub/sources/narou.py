"""成为小说家吧（syosetu.com）适配器——生肉原文，静态页面。"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..net import Fetcher
from ..textutil import clean_title, container_to_html, html_to_text, strip_markers
from .base import BookMeta, Catalog, ChapterRef, Source, VolumeRef

_NCODE_RE = re.compile(r"ncode\.syosetu\.com/([a-z]\d+[a-z]+)", re.I)


class NarouSource(Source):
    id = "narou"
    name = "小説家になろう（syosetu）"
    hosts = ("ncode.syosetu.com", "syosetu.com")
    delay_factor = 1.5

    def book_id(self, url: str) -> str:
        m = _NCODE_RE.search(url)
        return m.group(1) if m else ""

    def canonical(self, url: str) -> str:
        code = self.book_id(url)
        p = re.match(r"(https?://[^/]+)", url)
        base = p.group(1) if p else "https://ncode.syosetu.com"
        return f"{base}/{code}/" if code else url

    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        book_url = self.canonical(url)
        html = fetcher.get(book_url)
        soup = BeautifulSoup(html, "lxml")

        t = soup.select_one("h1.p-novel__title") or soup.find("h1")
        title = t.get_text(strip=True) if t else self.book_id(book_url)
        a_el = soup.select_one(".p-novel__author a") or soup.select_one(".p-novel__author")
        author = a_el.get_text(strip=True) if a_el else ""
        desc_el = soup.select_one(".p-novel__summary") or soup.select_one("#novel_ex")
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""
        meta = BookMeta(
            source=self.id,
            source_id=self.book_id(book_url),
            url=book_url,
            title=clean_title(title),
            author=author,
            description=desc,
        )

        chapters: list[ChapterRef] = []
        links = soup.select(".p-eplist__sublist a")
        if not links:
            links = soup.select("div.eplist a, .index_box a")
        idx = 0
        for a in links:
            href = a.get("href", "")
            if not href or not re.search(r"/\d+/?$", href):
                continue
            sub = a.select_one(".p-eplist__subtitle")
            name = (sub or a).get_text(" ", strip=True)
            if not name:
                continue
            chapters.append(
                ChapterRef(
                    idx=idx,
                    title=clean_title(name),
                    url=urljoin(book_url, href),
                    vol_idx=0,
                )
            )
            idx += 1
        return Catalog(
            meta=meta, volumes=[VolumeRef(idx=0, title="作品", url="")], chapters=chapters
        )

    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        html = fetcher.get(chapter.url, referer=book_url)
        soup = BeautifulSoup(html, "lxml")
        box = (
            soup.select_one("div.p-novel__body")
            or soup.select_one("div#novel_honbun")
            or soup.select_one("div#novel_no")
        )
        if box is None:
            raise RuntimeError(f"未找到正文容器：{chapter.url}")
        frag = strip_markers(container_to_html(box, chapter.url))
        return html_to_text(frag), frag
