"""配置驱动的通用适配器：不用改代码就能接入新站点。

在 config.json 的 sources.generic 里加一条 spec 即可：

{
  "id": "mysite",
  "name": "某轻小说站",
  "enabled": true,
  "url_re": "https?://(?:www\\.)?example\\.com/book/(\\d+)",
  "catalog_url": "https://www.example.com/book/{id}/",
  "title": "h1.book-name",
  "author": ".book-author",
  "cover": "img.cover@src",
  "description": "#intro",
  "volume": "div.volume-title",
  "chapter": "div.volume-list a",
  "content": "#content",
  "next_page": "a.next@href",
  "drop": ["div.ad", "script"],
  "delay_factor": 1.0
}
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..net import Fetcher
from ..textutil import clean_title, container_to_html, html_to_text, strip_markers
from .base import BookMeta, Catalog, ChapterRef, Source, VolumeRef


def _pick(soup: Tag | BeautifulSoup, expr: str) -> str:
    """支持 'css选择器'、'css@属性'、'meta:og:title'、'meta:name:description'。"""
    if not expr:
        return ""
    expr = expr.strip()
    if expr.startswith("meta:"):
        parts = expr.split(":")
        if len(parts) >= 3 and parts[1] in ("og", "property"):
            tag = soup.find("meta", attrs={"property": ":".join(parts[2:])})
        elif len(parts) >= 3:
            tag = soup.find("meta", attrs={"name": ":".join(parts[2:])})
        else:
            tag = soup.find("meta", attrs={"property": parts[1]})
        return (tag.get("content") or "").strip() if tag else ""
    attr = None
    if "@" in expr:
        expr, attr = expr.rsplit("@", 1)
    el = soup.select_one(expr) if expr else None
    if el is None:
        return ""
    if attr:
        val = el.get(attr) or ""
        if not val and el.has_attr("data-src"):
            val = el.get("data-src") or ""
        return str(val).strip()
    return el.get_text(" ", strip=True)


class GenericSource(Source):
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.id = spec.get("id") or "generic"
        self.name = spec.get("name") or self.id
        self.url_re = re.compile(spec.get("url_re") or r"$^")
        self.delay_factor = float(spec.get("delay_factor", 1.0))

    def match(self, url: str) -> bool:
        return bool(self.url_re.search(url))

    def book_id(self, url: str) -> str:
        m = self.url_re.search(url)
        if not m:
            return re.sub(r"\W+", "-", url)[-40:]
        return m.group(1) if m.groups() else m.group(0)

    def canonical(self, url: str) -> str:
        bid = self.book_id(url)
        tpl = self.spec.get("catalog_url")
        if tpl:
            return tpl.replace("{id}", bid).replace("{url}", url)
        return url

    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        catalog_url = self.canonical(url)
        html = fetcher.get(catalog_url, encoding=self.spec.get("encoding"))
        soup = BeautifulSoup(html, "lxml")
        s = self.spec

        title = _pick(soup, s.get("title", "h1")) or self.book_id(catalog_url)
        cover = _pick(soup, s.get("cover", ""))
        meta = BookMeta(
            source=self.id,
            source_id=self.book_id(catalog_url),
            url=catalog_url,
            title=clean_title(title),
            author=_pick(soup, s.get("author", "")),
            cover=urljoin(catalog_url, cover) if cover else "",
            category=_pick(soup, s.get("category", "")),
            status=_pick(soup, s.get("status", "")),
            description=_pick(soup, s.get("description", "")),
        )

        ch_sel = s.get("chapter") or "a"
        vol_sel = s.get("volume") or ""
        ch_elems = [e for e in soup.select(ch_sel) if e.name == "a" and e.get("href")]
        vol_elems = soup.select(vol_sel) if vol_sel else []

        vol_index: dict[int, int] = {id(e): i for i, e in enumerate(vol_elems)}
        ch_set = {id(e) for e in ch_elems}
        volumes: list[VolumeRef] = [
            VolumeRef(idx=i, title=clean_title(e.get_text(" ", strip=True)) or f"第{i + 1}卷")
            for i, e in enumerate(vol_elems)
        ]
        chapters: list[ChapterRef] = []
        cur_vol = 0
        idx = 0
        title_sel = s.get("chapter_title", "")
        for el in soup.find_all(True):
            if id(el) in vol_index:
                cur_vol = vol_index[id(el)]
                continue
            if id(el) not in ch_set:
                continue
            href = el.get("href", "")
            if href.startswith("javascript:") or href.startswith("#"):
                continue
            name = _pick(el, title_sel) if title_sel else el.get_text(" ", strip=True)
            if not name:
                continue
            chapters.append(
                ChapterRef(
                    idx=idx,
                    title=clean_title(name),
                    url=urljoin(catalog_url, href),
                    vol_idx=cur_vol,
                )
            )
            idx += 1

        if chapters and not volumes:
            volumes.append(VolumeRef(idx=0, title="正文", url=""))
        return Catalog(meta=meta, volumes=volumes, chapters=chapters)

    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        s = self.spec
        url = chapter.url
        parts: list[str] = []
        seen: set[str] = set()
        while url and url not in seen and len(seen) < fetcher.cfg.max_pages_per_chapter:
            seen.add(url)
            html = fetcher.get(url, referer=book_url, encoding=s.get("encoding"))
            soup = BeautifulSoup(html, "lxml")
            box = soup.select_one(s.get("content", "#content"))
            if box is None:
                raise RuntimeError(f"未找到正文容器 {s.get('content')}：{url}")
            for d in s.get("drop", []) or []:
                for el in box.select(d):
                    el.decompose()
            parts.append(container_to_html(box, url))
            nxt = s.get("next_page")
            url = ""
            if nxt:
                href = _pick(soup, nxt)
                if href and not href.startswith("javascript:"):
                    url = urljoin(chapter.url, href)
                    if url in seen:
                        url = ""
        frag = strip_markers("\n".join(p for p in parts if p.strip()))
        return html_to_text(frag), frag
