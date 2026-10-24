"""站点适配器基类与数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..net import Fetcher


@dataclass
class BookMeta:
    source: str
    source_id: str
    url: str
    title: str
    author: str = ""
    cover: str = ""
    category: str = ""
    status: str = ""
    description: str = ""


@dataclass
class ChapterRef:
    idx: int
    title: str
    url: str
    vol_idx: int = 0


@dataclass
class VolumeRef:
    idx: int
    title: str
    url: str = ""


@dataclass
class Catalog:
    meta: BookMeta
    volumes: list[VolumeRef] = field(default_factory=list)
    chapters: list[ChapterRef] = field(default_factory=list)


class Source:
    """站点适配器接口。"""

    id: str = "base"
    name: str = "base"
    # 用于 URL 归属判断（子串匹配）
    hosts: tuple[str, ...] = ()
    # 章节之间建议的请求间隔倍率（部分站点更敏感）
    delay_factor: float = 1.0

    def match(self, url: str) -> bool:
        low = url.lower()
        return any(h in low for h in self.hosts)

    def canonical(self, url: str) -> str:
        """把任意书籍/章节/目录 URL 归一化为书籍目录 URL。"""
        return url

    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        raise NotImplementedError

    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        """返回 (纯文本, 段落 HTML)。"""
        raise NotImplementedError

    def iter_chapter(self, fetcher: Fetcher, chapter: ChapterRef, book_url: str):
        """逐段产出正文 HTML（用于"边读边加载"）。

        默认实现是一次性返回整章；支持分页的站点应覆盖成本生成器。
        """
        _text, html = self.fetch_chapter(fetcher, chapter, book_url)
        if html:
            yield html


class SourceRegistry:
    def __init__(self) -> None:
        self._items: list[Source] = []

    def register(self, src: Source) -> Source:
        self._items.append(src)
        return src

    @property
    def all(self) -> list[Source]:
        return self._items

    def get(self, source_id: str) -> Optional[Source]:
        for s in self._items:
            if s.id == source_id:
                return s
        return None

    def detect(self, url: str) -> Optional[Source]:
        for s in self._items:
            if s.match(url):
                return s
        return None
