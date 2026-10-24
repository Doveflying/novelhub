"""文库8（wenku8.net / 轻小说文库）适配器。页面为 GBK 编码。"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..net import Fetcher
from ..textutil import clean_title, container_to_html, html_to_text, strip_markers
from .base import BookMeta, Catalog, ChapterRef, Source, VolumeRef

_BOOK_RE = re.compile(r"wenku8\.net/(?:book|novel/[\d/]+)/(\d+)")


class Wenku8Source(Source):
    id = "wenku8"
    name = "轻小说文库（wenku8）"
    hosts = ("wenku8.net", "wenku8.com", "wenku8.la")
    delay_factor = 1.5

    def canonical(self, url: str) -> str:
        m = re.search(r"wenku8\.\w+/book/(\d+)\.htm", url)
        if m:
            p = re.match(r"(https?://[^/]+)", url)
            return f"{p.group(1)}/book/{m.group(1)}.htm"
        return url

    def book_id(self, url: str) -> str:
        m = re.search(r"/book/(\d+)\.htm", url) or _BOOK_RE.search(url)
        return m.group(1) if m else ""

    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        book_url = self.canonical(url)
        html = fetcher.get(book_url, encoding="gbk")
        soup = BeautifulSoup(html, "lxml")

        title_el = soup.select_one("#title") or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""
        info = soup.select_one("#info") or soup.select_one(".info")
        author = ""
        if info:
            m = re.search(r"作者[:：]\s*([^\s、|]+)", info.get_text(" ", strip=True))
            author = m.group(1) if m else ""
        cover_el = soup.select_one("#content img, .cover img, img")
        cover = urljoin(book_url, cover_el.get("src", "")) if cover_el else ""
        desc = ""
        dec = soup.select_one("#content") or soup.select_one(".intro")
        if dec:
            desc = dec.get_text("\n", strip=True)[:2000]

        # 目录页：优先 book 页里的“开始阅读”链接，其次按 /novel/1/{id}/index.htm
        index_url = ""
        for a in soup.find_all("a", href=True):
            if "index.htm" in a["href"]:
                index_url = urljoin(book_url, a["href"])
                break
        if not index_url:
            index_url = urljoin(book_url, f"/novel/1/{self.book_id(book_url)}/index.htm")

        meta = BookMeta(
            source=self.id,
            source_id=self.book_id(book_url),
            url=index_url,
            title=clean_title(title) or f"wenku8-{self.book_id(book_url)}",
            author=author,
            cover=cover,
            description=desc,
        )

        cat_html = fetcher.get(index_url, encoding="gbk")
        csoup = BeautifulSoup(cat_html, "lxml")
        volumes: list[VolumeRef] = []
        chapters: list[ChapterRef] = []
        idx = 0
        vi = -1
        # 目录页结构：table 内 td.vcss 卷名 + td.ccss 里的章节链接
        for cell in csoup.select("td.vcss, td.ccss, .vcss, .ccss"):
            if "vcss" in (cell.get("class") or []):
                vi += 1
                volumes.append(VolumeRef(idx=max(vi, 0), title=cell.get_text(strip=True), url=""))
                continue
            for a in cell.find_all("a", href=True):
                href = a["href"]
                if not re.search(r"/novel/[\d/]+\.htm", href):
                    continue
                chapters.append(
                    ChapterRef(
                        idx=idx,
                        title=clean_title(a.get_text(strip=True)),
                        url=urljoin(index_url, href),
                        vol_idx=max(vi, 0),
                    )
                )
                idx += 1

        if chapters and not volumes:
            volumes.append(VolumeRef(idx=0, title="正文", url=""))
        return Catalog(meta=meta, volumes=volumes, chapters=chapters)

    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        html = fetcher.get(chapter.url, referer=book_url, encoding="gbk")
        soup = BeautifulSoup(html, "lxml")
        box = soup.find(id="content")
        if box is None:
            raise RuntimeError(f"未找到正文容器 #content：{chapter.url}")
        frag = container_to_html(box, chapter.url)
        frag = strip_markers(frag)
        return html_to_text(frag), frag
