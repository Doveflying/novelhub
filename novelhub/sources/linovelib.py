"""哔哩轻小说 / linovelib 系适配器。

两个模板差别很大，务必注意：

- ``www.linovelib.com``（PC 模板）：正文在 ``#TextContent``，一页约 3000 字，**全文**。
- ``www.bilinovel.com`` / ``w.linovelib.com`` / ``tw.linovelib.com``（移动模板）：
  正文在 ``#acontent``，一页只吐出约 480 字，末尾会拼一句「（內容加載失敗…）」——
  那是服务端截断，不是真的加载失败。

所以这里统一把 URL 归一到 PC 域名，正文按 ``#TextContent`` 取；只有 PC 页拿不到
正文时才回落到移动模板。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..net import Fetcher
from ..textutil import clean_title, container_to_html, html_to_text, strip_markers
from .base import BookMeta, Catalog, ChapterRef, Source, VolumeRef

PC_HOST = "www.linovelib.com"
MOBILE_HOST_FALLBACK = "www.bilinovel.com"

_BOOK_RE = re.compile(r"/novel/(\d+)(?:/(\d+)(?:_(\d+))?)?(?:\.html)?")
_READPARAMS_RE = re.compile(r"var\s+ReadParams\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)


class LinovelibSource(Source):
    id = "linovelib"
    name = "哔哩轻小说（linovelib 系，PC 全文）"
    hosts = ("bilinovel.com", "bilinovel.cn", "linovelib.com", "linovelib.net")

    # ---------------- URL ----------------
    def book_id(self, url: str) -> str:
        m = _BOOK_RE.search(url)
        return m.group(1) if m else ""

    def canonical(self, url: str) -> str:
        bid = self.book_id(url)
        if not bid:
            return url
        return f"https://{PC_HOST}/novel/{bid}/catalog"

    def cover_guess(self, bid: str) -> str:
        try:
            return f"https://{MOBILE_HOST_FALLBACK}/files/article/image/{int(bid) // 1000}/{bid}/{bid}s.jpg"
        except ValueError:
            return ""

    # ---------------- 目录 ----------------
    def fetch_catalog(self, fetcher: Fetcher, url: str) -> Catalog:
        catalog_url = self.canonical(url)
        bid = self.book_id(catalog_url)
        html = fetcher.get(catalog_url)
        soup = BeautifulSoup(html, "lxml")

        # ---- 元数据（PC 页只有 h1 + <title>，其余从 <title>/<meta> 抠）----
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        author, category = "", ""
        if page_title:
            parts = [p.strip() for p in page_title.split("_")]
            title = title or re.sub(r"小说在线阅读$", "", parts[0])
            for p in parts[1:]:
                if p.endswith("作品"):
                    author = p[:-2]
                elif category == "" and ("文库" in p or "社" in p or "其他" == p):
                    category = p
        desc = ""
        md = soup.find("meta", attrs={"name": "description"})
        if md:
            desc = (md.get("content") or "").split("作品简介", 1)[-1].strip()

        # ---- 分卷 / 章节（按文档顺序，vol_ 链接作为卷标记）----
        volumes: list[VolumeRef] = []
        chapters: list[ChapterRef] = []
        vol_idx = -1
        idx = 0
        vol_by_href: dict[str, int] = {}
        pattern = re.compile(rf"^/novel/{bid}/(?:vol_)?(\d+)\.html$")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not pattern.match(href):
                continue
            name = a.get_text(" ", strip=True)
            if "vol_" in href:
                # 同一卷常有「封面图链接 + 卷名链接」两个 anchor，空文本的是封面
                if href in vol_by_href:
                    vol_idx = vol_by_href[href]
                    continue
                if not name:
                    continue
                vol_idx += 1
                vol_by_href[href] = vol_idx
                volumes.append(
                    VolumeRef(idx=vol_idx, title=clean_title(name), url=urljoin(catalog_url, href))
                )
                continue
            if not name:
                continue
            chapters.append(
                ChapterRef(
                    idx=idx,
                    title=clean_title(name),
                    url=urljoin(catalog_url, href),
                    vol_idx=max(vol_idx, 0),
                )
            )
            idx += 1

        if chapters and not volumes:
            volumes.append(VolumeRef(idx=0, title="正文", url=""))
        chapters, volumes = self._compact(volumes, chapters)

        meta = BookMeta(
            source=self.id,
            source_id=bid,
            url=catalog_url,
            title=clean_title(title) or f"book-{bid}",
            author=author,
            cover=self._cover(fetcher, bid),
            category=category,
            status="",
            description=desc,
        )
        return Catalog(meta=meta, volumes=volumes, chapters=chapters)

    @staticmethod
    def _compact(volumes: list[VolumeRef], chapters: list[ChapterRef]):
        """去掉没有章节的空卷，并把 vol_idx 重排成连续的 0..N-1。"""
        if not chapters:
            return chapters, volumes
        used = sorted({c.vol_idx for c in chapters})
        remap = {old: new for new, old in enumerate(used)}
        by_idx = {v.idx: v for v in volumes}
        new_vols: list[VolumeRef] = []
        for old in used:
            v = by_idx.get(old)
            new_vols.append(
                VolumeRef(
                    idx=remap[old],
                    title=(v.title if v and v.title else (f"第{old + 1}卷" if old else "正文")),
                    url=(v.url if v else ""),
                )
            )
        new_chs = [
            ChapterRef(idx=c.idx, title=c.title, url=c.url, vol_idx=remap[c.vol_idx])
            for c in chapters
        ]
        return new_chs, new_vols

    def _cover(self, fetcher: Fetcher, bid: str) -> str:
        guess = self.cover_guess(bid)
        if not guess:
            return ""
        try:  # 顺手试探一次，能下就留着（下载失败会被 crawler 忽略）
            fetcher.get_bytes(guess)
            return guess
        except Exception:
            return ""

    # ---------------- 章节 ----------------
    def iter_chapter(self, fetcher: Fetcher, chapter: ChapterRef, book_url: str):
        """按页产出 HTML：先给第一页，后面的页边抓边吐。"""
        url = chapter.url
        if "linovelib.com/novel/" not in url:
            url = url.replace(urlparse(url).netloc, PC_HOST, 1)

        first = fetcher.get(url, referer=book_url)
        soup = BeautifulSoup(first, "lxml")
        box = soup.select_one("#TextContent") or soup.select_one("#acontent")
        if box is None:
            raise RuntimeError(f"未找到正文容器（#TextContent/#acontent）：{url}")
        yield container_to_html(box, url)

        total = self._total_pages(soup)
        if total and total > 1:
            base = re.sub(r"_(\d+)\.html$", "", url)
            base = re.sub(r"\.html$", "", base)
            for i in range(2, total + 1):
                page_url = f"{base}_{i}.html"
                try:
                    page = fetcher.get(page_url, referer=book_url)
                except Exception:
                    continue
                s2 = BeautifulSoup(page, "lxml")
                b2 = s2.select_one("#TextContent") or s2.select_one("#acontent")
                if b2 is not None:
                    frag = container_to_html(b2, page_url)
                    if frag.strip():
                        yield frag
            return

        seen: set[str] = set()
        nxt = self._next_page(soup, url)
        while nxt and nxt not in seen and len(seen) < fetcher.cfg.max_pages_per_chapter:
            seen.add(nxt)
            try:
                page = fetcher.get(nxt, referer=book_url)
            except Exception:
                break
            s2 = BeautifulSoup(page, "lxml")
            b2 = s2.select_one("#TextContent") or s2.select_one("#acontent")
            if b2 is None:
                break
            frag = container_to_html(b2, nxt)
            if frag.strip():
                yield frag
            nxt = self._next_page(s2, nxt)

    def fetch_chapter(
        self, fetcher: Fetcher, chapter: ChapterRef, book_url: str
    ) -> tuple[str, str]:
        parts = [p for p in self.iter_chapter(fetcher, chapter, book_url) if p.strip()]
        frag = strip_markers("\n".join(parts))
        return html_to_text(frag), frag

    @staticmethod
    def _total_pages(soup: BeautifulSoup) -> int | None:
        h1 = soup.select_one("#mlfy_main_text h1") or soup.find("h1")
        if not h1:
            return None
        m = re.search(r"[（(]\s*(\d+)\s*/\s*(\d+)\s*[)）]", h1.get_text(" ", strip=True))
        return int(m.group(2)) if m else None

    def _fetch_pages_parallel(self, fetcher: Fetcher, urls: list[str], book_url: str) -> list[str]:
        from concurrent.futures import ThreadPoolExecutor

        cfg = fetcher.cfg

        def one(u: str) -> str:
            try:
                with Fetcher(cfg) as f:  # 每个线程一个 session，避免共享状态
                    page = f.get(u, referer=book_url)
                s2 = BeautifulSoup(page, "lxml")
                b2 = s2.select_one("#TextContent") or s2.select_one("#acontent")
                return container_to_html(b2, u) if b2 else ""
            except Exception:
                return ""

        with ThreadPoolExecutor(max_workers=min(3, len(urls))) as ex:
            return list(ex.map(one, urls))

    def _next_page(self, soup: BeautifulSoup, current_url: str) -> str | None:
        cur = _BOOK_RE.search(current_url)
        if not cur:
            return None
        cid = cur.group(2)
        page_no = int(cur.group(3) or 1)
        best: tuple[int, str] | None = None
        for a in soup.find_all("a", href=True):
            m = re.search(rf"/novel/\d+/{cid}_(\d+)\.html", a["href"])
            if not m:
                continue
            n = int(m.group(1))
            if n > page_no and (best is None or n < best[0]):
                best = (n, urljoin(current_url, a["href"]))
        # 兼容老式 ReadParams 分页
        if best is None:
            m = _READPARAMS_RE.search(str(soup))
            if m:
                params = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1)))
                nxt = params.get("url_next") or ""
                new = _BOOK_RE.search(nxt)
                if new and new.group(2) == cid:
                    best = (int(new.group(3) or 2), urljoin(current_url, nxt))
        return best[1] if best else None


# ================= 站内书目索引 =================
# 站内搜索接口 /S6/ 是 JS 门禁（任何请求都只回 200 + 0 字节），
# 所以改成"扫榜单页 → 建本地书名索引 → 关键词本地检索"。
# 索引可断点续扫：novel_index_meta.cursor 记住扫到哪一页。

INDEX_SOURCE = "linovelib"
INDEX_NAME = "哔哩轻小说"
_ORDERS = (
    "allvisit",
    "monthvisit",
    "weekvisit",
    "allvote",
    "monthvote",
    "goodnum",
    "postdate",
    "lastupdate",
)
_SORTIDS = tuple(range(1, 21))
_LIST_URL = (
    "https://www.linovelib.com/modules/article/toplist.php"
    "?order={order}&sortid={sid}&fullflag=1&page={page}"
)
_DONE_RE = re.compile(r"(\u5b8c\u7ed3|\u8fde\u8f7d|\u5df2\u5b8c)")


def parse_listing(html: str, base: str = f"https://{PC_HOST}/") -> list[dict]:
    """从榜单/分类页解析书目卡片。"""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()

    for card in soup.select("div.rank_d_list"):
        a = card.select_one("div.rank_d_b_name a[href*='/novel/']")
        if a is None:
            a = card.select_one("a[href*='/novel/']")
        if a is None:
            continue
        m = _BOOK_RE.search(a.get("href", ""))
        if not m or m.group(2):  # 章节链接，不是书
            continue
        bid = m.group(1)
        if bid in seen:
            continue
        name_box = card.select_one("div.rank_d_b_name")
        title = (name_box.get("title") if name_box else "") or a.get_text(" ", strip=True)
        title = clean_title(title or "")
        if not title:
            continue
        img = card.select_one("img")
        cover = ""
        if img is not None:
            cover = img.get("data-original") or img.get("src") or ""
            if "book-cover-no" in cover:
                cover = img.get("data-original") or ""
        author = category = status = ""
        cate = card.select_one("div.rank_d_b_cate")
        if cate is not None:
            parts = [x.get_text(" ", strip=True) for x in cate.select("a")]
            parts = [p for p in parts if p]
            if parts:
                author = parts[0]
            for p in parts[1:]:
                if _DONE_RE.search(p):
                    status = p
                elif not category:
                    category = p
        intro_box = card.select_one("div.rank_d_b_info")
        intro = intro_box.get_text(" ", strip=True) if intro_box else ""
        seen.add(bid)
        out.append(
            {
                "source_id": bid,
                "title": title,
                "author": author,
                "cover": urljoin(base, cover) if cover else "",
                "category": category or status,
                "url": f"https://{PC_HOST}/novel/{bid}.html",
                "intro": intro,
            }
        )

    if out:
        return out

    # 兜底：纯链接扫描（老模板/其它列表页）
    for a in soup.find_all("a", href=True):
        m = _BOOK_RE.search(a["href"])
        if not m or m.group(2):
            continue
        bid = m.group(1)
        title = clean_title(a.get_text(" ", strip=True))
        if not title or bid in seen or len(title) > 60:
            continue
        seen.add(bid)
        out.append(
            {
                "source_id": bid,
                "title": title,
                "author": "",
                "cover": "",
                "category": "",
                "url": f"https://{PC_HOST}/novel/{bid}.html",
                "intro": "",
            }
        )
    return out


def build_index(
    cfg,
    lib,
    *,
    budget: int = 300,
    on_progress=None,
) -> dict:
    """扫榜单页建索引。budget = 本次最多抓多少页（可续扫）。"""
    import json

    meta = lib.get_index_meta(INDEX_SOURCE)
    try:
        cursor = json.loads(meta.get("cursor") or "{}")
    except ValueError:
        cursor = {}
    oi0 = int(cursor.get("order", 0))
    si0 = int(cursor.get("sid", 0))
    page0 = int(cursor.get("page", 1))
    scanned = int(meta.get("scanned") or 0)
    added = 0
    pages = 0
    failed = 0

    def save(next_cursor: dict) -> None:
        lib.set_index_meta(
            INDEX_SOURCE,
            cursor=json.dumps(next_cursor),
            total=lib.novel_index_count(INDEX_SOURCE),
            scanned=scanned,
        )

    with Fetcher(cfg) as f:
        stop = False
        for oi in range(oi0, len(_ORDERS)):
            if stop:
                break
            order = _ORDERS[oi]
            for si in range(si0 if oi == oi0 else 0, len(_SORTIDS)):
                if stop:
                    break
                sid = _SORTIDS[si]
                page = page0 if (oi == oi0 and si == si0) else 1
                while page <= 300:
                    if pages >= budget:
                        save({"order": oi, "sid": si, "page": page})
                        stop = True
                        break
                    url = _LIST_URL.format(order=order, sid=sid, page=page)
                    try:
                        html = f.get(url, use_cache=False)
                        rows = parse_listing(html)
                    except Exception:
                        failed += 1
                        page += 1
                        pages += 1
                        if failed >= 8:
                            stop = True
                            break
                        continue
                    pages += 1
                    scanned += 1
                    if not rows:
                        break  # 该榜单到底了
                    added += lib.upsert_novel_index(INDEX_SOURCE, rows)
                    save({"order": oi, "sid": si, "page": page + 1})
                    if on_progress is not None:
                        on_progress(
                            {
                                "pages": pages,
                                "budget": budget,
                                "scanned": scanned,
                                "total": lib.novel_index_count(INDEX_SOURCE),
                                "list": f"{order}/sid{sid}/p{page}",
                            }
                        )
                    page += 1
                if stop:
                    break
                if pages >= budget:
                    stop = True
                    break
    total = lib.novel_index_count(INDEX_SOURCE)
    if not stop:
        # 全部榜单扫完：游标归零，下次重扫一遍刷新书目
        lib.set_index_meta(
            INDEX_SOURCE,
            cursor=json.dumps({"order": 0, "sid": 0, "page": 1}),
            total=total,
            scanned=scanned,
        )
    else:
        lib.set_index_meta(
            INDEX_SOURCE,
            cursor=lib.get_index_meta(INDEX_SOURCE).get("cursor") or "",
            total=total,
            scanned=scanned,
        )
    return {
        "pages": pages,
        "budget": budget,
        "added": added,
        "total": total,
        "scanned": scanned,
        "done": not stop,
    }


# ---------- 深度模式：逐本抓书籍页的 og:novel:* 元信息 ----------
DEEP_SOURCE = "linovelib"
_SITEMAP_CACHE = "linovelib_sitemap_ids.txt"


def sitemap_book_ids(cfg, fetcher, max_age_days: int = 7) -> list:
    """取 sitemap 里的全部书籍 id（缓存在 data/cache 下）。"""
    import time

    cache = cfg.data_path / "cache" / _SITEMAP_CACHE
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < max_age_days * 86400:
            ids = cache.read_text(encoding="utf-8").split()
            if ids:
                return ids
    xml = fetcher.get(f"https://{PC_HOST}/sitemap.xml", use_cache=False)
    ids = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        m = re.fullmatch(rf"https?://{re.escape(PC_HOST)}/novel/(\d+)\.html", loc.strip())
        if m:
            ids.append(m.group(1))
    if ids:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(ids), encoding="utf-8")
    return ids


def parse_book_page(html: str, bid: str) -> dict:
    """从书籍页 og:novel:* 元信息解析书目。"""
    def meta(prop: str) -> str:
        m = re.search(
            rf'<meta\s+property="{re.escape(prop)}"\s+content=(?:"([^"]*)"|\'([^\']*)\')',
            html,
            re.I,
        )
        return (m.group(1) or m.group(2) or "").strip() if m else ""

    title = meta("og:novel:book_name") or meta("og:title")
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        if m:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    if not title:
        return {}
    category = meta("og:novel:category")
    status = meta("og:novel:status")
    return {
        "source_id": str(bid),
        "title": clean_title(title),
        "author": meta("og:novel:author"),
        "cover": meta("og:image"),
        "category": (category.split()[-1] if category else "") or status,
        "url": f"https://{PC_HOST}/novel/{bid}.html",
        "intro": meta("og:description")[:200],
    }


def build_index_deep(cfg, lib, *, budget: int = 300, on_progress=None) -> dict:
    """按 sitemap 顺序逐本抓元信息，补齐榜单没覆盖到的书。"""
    import json

    meta = lib.get_index_meta(DEEP_SOURCE + "-deep")
    try:
        cursor = json.loads(meta.get("cursor") or "{}")
    except ValueError:
        cursor = {}
    start = int(cursor.get("i", 0))
    scanned = int(meta.get("scanned") or 0)
    added = 0
    req = 0
    failed = 0

    with Fetcher(cfg) as f:
        ids = sitemap_book_ids(cfg, f)
        if not ids:
            return {"pages": 0, "budget": budget, "added": 0,
                    "total": lib.novel_index_count(DEEP_SOURCE), "scanned": scanned,
                    "done": True, "ids": 0}
        have = {
            str(r["source_id"])
            for r in lib.conn().execute(
                "SELECT source_id FROM novel_index WHERE source=?", (DEEP_SOURCE,)
            ).fetchall()
        }
        i = start
        while i < len(ids) and req < budget:
            bid = ids[i]
            i += 1
            if bid in have:
                continue
            try:
                html = f.get(f"https://{PC_HOST}/novel/{bid}.html", use_cache=False)
                row = parse_book_page(html, bid)
            except Exception:
                failed += 1
                req += 1
                if failed >= 10:
                    break
                continue
            req += 1
            scanned += 1
            if row:
                added += lib.upsert_novel_index(DEEP_SOURCE, [row])
                have.add(bid)
            lib.set_index_meta(
                DEEP_SOURCE + "-deep",
                cursor=json.dumps({"i": i}),
                total=lib.novel_index_count(DEEP_SOURCE),
                scanned=scanned,
            )
            if on_progress is not None:
                on_progress(
                    {
                        "pages": req,
                        "budget": budget,
                        "scanned": scanned,
                        "total": lib.novel_index_count(DEEP_SOURCE),
                        "list": f"{bid} ({i}/{len(ids)})",
                    }
                )
    total = lib.novel_index_count(DEEP_SOURCE)
    return {
        "pages": req,
        "budget": budget,
        "added": added,
        "total": total,
        "scanned": scanned,
        "done": i >= len(ids),
        "ids": len(ids),
        "pos": i,
    }

# ---------- 增量刷新：只扫「新书」和「最近更新」的头部几页 ----------
# 实测（2026-10-24）：postdate 前 3 页 90 本里当月 34 本、上月 36 本；
# lastupdate 前 3 页里当月 53 本 —— 日均新书 1~2 本、日更 3~5 本。
# 所以增量只要 6 个请求左右就够，不需要整站重扫。
_INCR_ORDERS = ("postdate", "lastupdate")


def refresh_index(cfg, lib, *, pages: int = 3, on_progress=None) -> dict:
    """增量刷新索引：扫 postdate / lastupdate 头部 pages 页，新书并入索引。

    不动 build_index 的断点游标，随时可跑；已索引的书会被 UPDATE（书名/封面可能变）。
    """
    added = 0
    seen = 0
    requests = 0
    new_books: list = []
    with Fetcher(cfg) as f:
        for order in _INCR_ORDERS:
            for page in range(1, max(1, pages) + 1):
                url = _LIST_URL.format(order=order, sid=0, page=page)
                try:
                    html = f.get(url, use_cache=False)
                except Exception:  # noqa: BLE001
                    continue
                rows = parse_listing(html)
                if not rows:
                    break
                requests += 1
                seen += len(rows)
                before = lib.novel_index_count(INDEX_SOURCE)
                added += lib.upsert_novel_index(INDEX_SOURCE, rows)
                after = lib.novel_index_count(INDEX_SOURCE)
                if after > before:
                    new_books.extend(rows[: after - before])
                if on_progress is not None:
                    on_progress(
                        {
                            "pages": requests,
                            "budget": len(_INCR_ORDERS) * pages,
                            "scanned": seen,
                            "total": after,
                            "list": f"{order}/p{page}",
                        }
                    )
    total = lib.novel_index_count(INDEX_SOURCE)
    lib.set_index_meta(
        INDEX_SOURCE + "-refresh",
        cursor="",
        total=total,
        scanned=seen,
    )
    return {
        "requests": requests,
        "seen": seen,
        "added": added,
        "total": total,
        "new": new_books[:20],
    }
