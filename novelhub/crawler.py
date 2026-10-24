"""抓取编排：目录 → 章节（含分页合并）→ 入库，支持断点续传。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .config import Config
from .net import Fetcher
from .sources import Catalog, SourceRegistry
from .sources.base import ChapterRef
from .store import Library


@dataclass
class CrawlResult:
    book_id: int
    title: str
    total: int = 0
    fetched: int = 0
    skipped: int = 0
    mode: str = "full"  # full | catalog-only
    failed: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "total": self.total,
            "fetched": self.fetched,
            "skipped": self.skipped,
            "mode": self.mode,
            "failed": self.failed,
        }


def _image_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


class Crawler:
    def __init__(self, cfg: Config, lib: Library, registry: SourceRegistry):
        self.cfg = cfg
        self.lib = lib
        self.registry = registry

    # ---------------- 目录 ----------------
    def inspect(self, url: str) -> Catalog:
        src = self.registry.detect(url)
        if src is None:
            raise RuntimeError(
                "没有匹配的站点适配器。已支持："
                + "、".join(s.id for s in self.registry.all)
                + "。其它站点可在 config.json 的 sources.generic 中配置。"
            )
        with Fetcher(self.cfg) as f:
            return src.fetch_catalog(f, url)

    def save_book(self, catalog: Catalog) -> int:
        meta = catalog.meta
        book_id = self.lib.upsert_book(
            {
                "source": meta.source,
                "source_id": meta.source_id,
                "url": meta.url,
                "title": meta.title,
                "author": meta.author,
                "cover": meta.cover,
                "category": meta.category,
                "status": meta.status,
                "description": meta.description,
            }
        )
        self.lib.replace_volumes(
            book_id,
            [{"idx": v.idx, "title": v.title, "url": v.url} for v in catalog.volumes],
        )
        self.lib.replace_catalog(
            book_id,
            [
                {"idx": c.idx, "title": c.title, "url": c.url, "vol_idx": c.vol_idx}
                for c in catalog.chapters
            ],
        )
        return book_id

    # ---------------- 封面 ----------------
    def _save_cover(self, book_id: int, cover_url: str) -> None:
        if not cover_url:
            return
        try:
            with Fetcher(self.cfg) as f:
                data = f.get_bytes(cover_url)
        except Exception:
            return
        if not data:
            return
        path = self.cfg.data_path / "covers" / f"{book_id}{_image_ext(data)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.lib.set_cover_path(book_id, str(path))

    # ---------------- 抓取 ----------------
    def crawl(
        self,
        url: str,
        *,
        limit: Optional[int] = None,
        volumes: Optional[Iterable[int]] = None,
        only_pending: bool = True,
        force: bool = False,
        catalog_only: bool = False,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> CrawlResult:
        catalog = self.inspect(url)
        src = self.registry.detect(url)
        assert src is not None
        book_id = self.save_book(catalog)
        self._save_cover(book_id, catalog.meta.cover)

        vol_filter = set(volumes) if volumes is not None else None
        chapters = [c for c in catalog.chapters if vol_filter is None or c.vol_idx in vol_filter]

        result = CrawlResult(book_id=book_id, title=catalog.meta.title, total=len(chapters))

        # 只建目录，不抓正文（在线读取模式 / 全站索引都用这个）
        if catalog_only or not self.cfg.persist_chapters:
            result.mode = "catalog-only"
            result.skipped = len(chapters)
            return result
        done = {int(r["idx"]) for r in self.lib.list_chapters(book_id) if (r["words"] or 0) > 0}

        todo = []
        for ch in chapters:
            if only_pending and not force and ch.idx in done:
                result.skipped += 1
                continue
            todo.append(ch)
        if limit is not None:
            todo = todo[: max(0, limit)]

        if not todo:
            return result

        old_delay = self.cfg.delay
        self.cfg.delay = max(self.cfg.delay, self.cfg.delay * src.delay_factor)
        try:
            with Fetcher(self.cfg) as fetcher:
                for i, ch in enumerate(todo, 1):
                    if should_stop and should_stop():
                        break
                    if on_progress:
                        on_progress(i, len(todo), ch.title)
                    try:
                        text, frag = src.fetch_chapter(fetcher, ch, catalog.meta.url)
                    except Exception as exc:  # 单章失败不影响整体
                        result.failed.append((ch.title, str(exc)[:200]))
                        continue
                    if not text.strip():
                        result.failed.append((ch.title, "正文为空（可能是需登录/特权章节）"))
                        continue
                    if self.cfg.localize_images and frag:
                        from .media import localize_html

                        frag, n_img = localize_html(frag, book_id, self.cfg, fetcher)
                        if n_img:
                            print(f"\n  ↓ 插图 {n_img} 张已落地")
                    self.lib.add_chapter(
                        book_id,
                        ch.idx,
                        ch.title,
                        text,
                        frag,
                        vol_idx=ch.vol_idx,
                        url=ch.url,
                    )
                    result.fetched += 1
        finally:
            self.cfg.delay = old_delay
        return result

    # ---------------- 批量：列表页 → 书单 → 建目录 ----------------
    def batch(
        self,
        list_url: str,
        *,
        top: int = 20,
        pages: int = 1,
        catalog_only: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict:
        from .discover import discover, expand_list_urls

        urls = expand_list_urls(list_url, pages)
        books = []
        seen: set[tuple[str, str]] = set()
        for u in urls:
            for b in discover(self.cfg, self.registry, u, limit=top):
                key = (b.source, b.source_id)
                if key in seen:
                    continue
                seen.add(key)
                books.append(b)
            if len(books) >= top:
                break
        books = books[:top]

        items: list[dict] = []
        for i, b in enumerate(books, 1):
            if on_progress:
                on_progress(i, len(books), b.title or b.source_id)
            try:
                res = self.crawl(b.url, catalog_only=catalog_only, only_pending=True)
                row = res.as_dict()
                row["url"] = b.url
                items.append(row)
            except Exception as exc:  # noqa: BLE001
                items.append({"url": b.url, "title": b.title, "error": str(exc)[:160]})
        ok = [r for r in items if r.get("book_id")]
        return {
            "found": len(books),
            "pages": len(urls),
            "indexed": len(ok),
            "failed": len(items) - len(ok),
            "chapters": sum(r.get("total", 0) for r in ok),
            "items": items,
        }

    def resume(self, book_id: int, **kwargs) -> CrawlResult:
        book = self.lib.get_book(book_id)
        if book is None:
            raise RuntimeError(f"书库中没有 id={book_id}")
        if not book["url"]:
            raise RuntimeError("该书没有来源 URL（本地导入），无法续抓")
        return self.crawl(book["url"], only_pending=True, **kwargs)

    # ---------------- 在线读取（不落库） ----------------
    def _live_cache_file(self, book_id: int, idx: int):
        return self.cfg.data_path / "livecache" / str(book_id) / f"{idx}.json"

    def read_live_cache(self, book_id: int, idx: int) -> Optional[tuple[str, str]]:
        ttl = float(self.cfg.live_cache_ttl_hours or 0)
        if ttl <= 0:
            return None
        p = self._live_cache_file(book_id, idx)
        if not p.exists():
            return None
        try:
            import json
            import time

            if time.time() - p.stat().st_mtime > ttl * 3600:
                p.unlink(missing_ok=True)
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("text", ""), data.get("html", "")
        except Exception:
            return None

    def write_live_cache(self, book_id: int, idx: int, text: str, html: str) -> None:
        if not self.cfg.live_cache_ttl_hours:
            return
        p = self._live_cache_file(book_id, idx)
        try:
            import json

            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"text": text, "html": html, "title": ""}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def fetch_chapter_live(
        self, book_id: int, idx: int, use_cache: bool = True
    ) -> tuple[str, str]:
        """按需抓一章，只返回内容，不写数据库；可选的磁盘临时缓存（带 TTL）。"""
        book = self.lib.get_book(book_id)
        if book is None:
            raise RuntimeError(f"书库中没有 id={book_id}")
        row = self.lib.get_chapter(book_id, idx)
        if row is None:
            raise RuntimeError(f"没有第 {idx} 章")

        if use_cache:
            cached = self.read_live_cache(book_id, idx)
            if cached and cached[1]:
                return cached

        src = self.registry.detect(book["url"] or row["url"] or "")
        if src is None:
            raise RuntimeError("这本没有可用的站点适配器，只能读已下载的正文")
        ch = ChapterRef(
            idx=idx, title=row["title"], url=row["url"] or "", vol_idx=row["vol_idx"]
        )
        with Fetcher(self.cfg) as f:
            text, html = src.fetch_chapter(f, ch, book["url"] or "")
            if self.cfg.localize_images and html:
                from .media import localize_html

                html, _n = localize_html(html, book_id, self.cfg, f)
        if html:
            self.write_live_cache(book_id, idx, text, html)
        return text, html

    def iter_chapter_live(self, book_id: int, idx: int):
        """逐页产出正文 HTML：第一页先给，后面的边抓边吐（SSE 用）。"""
        from .textutil import html_to_text, strip_markers

        book = self.lib.get_book(book_id)
        if book is None:
            raise RuntimeError(f"书库中没有 id={book_id}")
        row = self.lib.get_chapter(book_id, idx)
        if row is None:
            raise RuntimeError(f"没有第 {idx} 章")

        cached = self.read_live_cache(book_id, idx)
        if cached and cached[1]:
            yield cached[1]
            return

        src = self.registry.detect(book["url"] or row["url"] or "")
        if src is None:
            raise RuntimeError("这本没有可用的站点适配器，只能读已下载的正文")
        ch = ChapterRef(
            idx=idx, title=row["title"], url=row["url"] or "", vol_idx=row["vol_idx"]
        )
        parts: list[str] = []
        with Fetcher(self.cfg) as f:
            for frag in src.iter_chapter(f, ch, book["url"] or ""):
                if not frag.strip():
                    continue
                if self.cfg.localize_images:
                    from .media import localize_html

                    frag, _n = localize_html(frag, book_id, self.cfg, f)
                parts.append(frag)
                yield frag
        html = strip_markers("\n".join(parts))
        if html:
            self.write_live_cache(book_id, idx, html_to_text(html), html)
