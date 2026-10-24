"""聚合搜索：一个关键词，同时问多个源（小说源 + 漫画源 + 本地库）。

小说源（可勾选）：
- ``linovel``   轻之文库站内搜索（实时）
- ``linovelib`` 轻小说文库书目索引（本地索引，站内搜索被 JS 门禁挡住）
- ``local``     本地书架里已抓的书

漫画源：Venera 兼容 JS 源，逐个可用。
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional

from .config import Config
from .net import Fetcher
from .sources.venera_js import JsSource, VeneraRegistry

NOVEL_SOURCES = (
    ("linovel", "轻之文库"),
    ("linovelib", "哔哩轻小说"),
    ("bangumi", "Bangumi 书目"),
    ("local", "本地书架"),
)


# ---------------- 小说源 ----------------
def _lib_book_ids(lib, source: str) -> dict[str, int]:
    """该源下已经在书架里的书：source_id -> book_id。"""
    if lib is None:
        return {}
    try:
        rows = lib.conn().execute(
            "SELECT id, source_id FROM books WHERE source=?", (source,)
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {str(r["source_id"]): int(r["id"]) for r in rows}


def _relevance(q: str, title: str) -> int:
    """关键词和书名的重合度：命中 2-gram 的个数。"""
    qq = re.sub(r"\s+", "", q or "")
    tt = re.sub(r"\s+", "", title or "")
    if not qq or not tt:
        return 0
    grams = {qq[i : i + 2] for i in range(max(1, len(qq) - 1))}
    return sum(1 for g in grams if g in tt)


def _novel_linovel(cfg: Config, q: str, limit: int, lib) -> list[dict]:
    from .sources.linovel import LinovelSource

    src = LinovelSource()
    with Fetcher(cfg) as f:
        rows = src.search(f, q, limit=limit)
    have = _lib_book_ids(lib, "linovel")
    # 轻之文库的搜索是模糊的，经常混进一堆完全不相干的书 → 只留书名有重合的
    keep = [r for r in rows if _relevance(q, r.get("title", "")) > 0]
    rows = keep
    for r in rows:
        r["kind"] = "novel"
        r["source_name"] = "轻之文库"
        bid = have.get(str(r.get("source_id")))
        r["book_id"] = bid
        r["action"] = "open" if bid else "add"
    return rows


def _novel_index(cfg: Config, q: str, limit: int, lib) -> list[dict]:
    """轻小说文库：查本地书目索引。"""
    if lib is None:
        return []
    from .sources.linovelib import INDEX_SOURCE

    rows = lib.search_novel_index(q, source=INDEX_SOURCE, limit=limit)
    have = _lib_book_ids(lib, INDEX_SOURCE)
    out = []
    for r in rows:
        bid = have.get(str(r["source_id"]))
        out.append(
            {
                "source": INDEX_SOURCE,
                "source_name": "哔哩轻小说",
                "source_id": str(r["source_id"]),
                "title": r.get("title") or "",
                "author": r.get("author") or "",
                "cover": r.get("cover") or "",
                "category": r.get("category") or "",
                "intro": (r.get("intro") or "")[:120],
                "url": r.get("url") or "",
                "kind": "novel",
                "book_id": bid,
                "action": "open" if bid else "add",
            }
        )
    return out


def _novel_local(cfg: Config, q: str, limit: int, lib) -> list[dict]:
    """本地书架：书名/作者命中 + 正文命中两种。"""
    if lib is None:
        return []
    qq = q.strip().lower()
    out: list[dict] = []
    books = [dict(r) for r in lib.list_books()]

    def cover_of(b) -> str:
        return f"/cover/{b['id']}" if (b.get("cover_path") or "") else ""

    for b in books:
        title = (b["title"] or "").lower()
        author = (b["author"] or "").lower()
        if qq and (qq in title or qq in author):
            done, total = b["n_done"], b["n_chapters"]
            out.append(
                {
                    "source": "local",
                    "source_name": "本地书架",
                    "source_id": str(b["id"]),
                    "title": b["title"] or "",
                    "author": b.get("author") or "",
                    "cover": cover_of(b),
                    "category": f"{done}/{total} 章已存",
                    "intro": "",
                    "url": f"/book/{b['id']}",
                    "kind": "novel",
                    "book_id": int(b["id"]),
                    "action": "open",
                }
            )
        if len(out) >= limit:
            break

    # 正文命中：告诉用户"这本书里出现过这个词"
    if len(out) < limit:
        try:
            hits = lib.search(q, limit=limit * 2)
        except Exception:  # noqa: BLE001
            hits = []
        seen_books = {r["book_id"] for r in out}
        for h in hits:
            bid = int(h["book_id"])
            if bid in seen_books:
                continue
            seen_books.add(bid)
            out.append(
                {
                    "source": "local",
                    "source_name": "本地书架",
                    "source_id": f"{bid}#{h['idx']}",
                    "title": f"{h['book_title']} · 第{h['idx'] + 1}章 {h.get('title') or ''}".strip(),
                    "author": "",
                    "cover": "",
                    "category": "正文命中",
                    "intro": (h.get("snippet") or "")[:120],
                    "url": f"/read/{bid}/{h['idx']}",
                    "kind": "novel",
                    "book_id": bid,
                    "action": "open",
                }
            )
            if len(out) >= limit:
                break
    return out


def _novel_bangumi(cfg: Config, q: str, limit: int, lib) -> list[dict]:
    """Bangumi 只给书目（书名/封面/简介），顺手用书名去索引里找可读版本。"""
    from .sources.bangumi import search as bgm_search

    rows = bgm_search(cfg, q, limit=min(limit, 8))
    out = []
    for r in rows:
        r["book_id"] = None
        # 标注"索引里有没有可读版本"
        try:
            hits = lib.search_novel_index(r["title"], source="linovelib", limit=1) if lib else []
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            h = dict(hits[0])
            r["readable"] = {
                "source": "linovelib",
                "source_id": str(h["source_id"]),
                "title": h.get("title") or "",
                "url": h.get("url") or "",
            }
        out.append(r)
    return out


_NOVEL_FUNCS = {
    "linovel": _novel_linovel,
    "linovelib": _novel_index,
    "bangumi": _novel_bangumi,
    "local": _novel_local,
}


def _anime_search(cfg: Config, key: str, q: str, limit: int = 8) -> list[dict]:
    from .sources.anime import search as anime_search

    out = []
    for r in anime_search(cfg, key, q, limit=limit):
        r["url"] = f"/anime/{key}/{r['source_id']}"
        r["action"] = "open"
        out.append(r)
    return out


# ---------------- 漫画源 ----------------
def _comic_search(cfg: Config, key: str, path: Path | None, q: str, limit: int = 8) -> list[dict]:
    if key == "copy_manga":
        from .sources.copy_manga import CopyMangaSource

        with CopyMangaSource(cfg) as src:
            rows = src.search(q, limit)
        return [
            {
                "source": key,
                "source_name": "拷贝漫画",
                "source_id": str(c.get("id", "")),
                "title": c.get("title") or "",
                "author": c.get("subTitle") or "",
                "cover": c.get("cover") or "",
                "tags": c.get("tags") or [],
                "url": f"/comic/{key}/{c.get('id')}",
                "kind": "comic",
                "action": "open",
            }
            for c in rows
        ]

    src = JsSource(cfg, path)
    out = []
    # 大部分源 1~5s，个别（GfmhApp / comick）要 20s+；给足时间但不超过聚合总超时
    for c in src.search(q, 1, timeout=25)[:limit]:
        out.append(
            {
                "source": key,
                "source_name": key,
                "source_id": str(c.get("id", "")),
                "title": c.get("title") or "",
                "author": c.get("subTitle") or "",
                "cover": c.get("cover") or "",
                "tags": c.get("tags") or [],
                "url": f"/comic/{key}/{c.get('id')}",
                "kind": "comic",
                "action": "open",
            }
        )
    return out


def aggregate(
    cfg: Config,
    q: str,
    *,
    kinds: Iterable[str] = ("novel", "comic"),
    novel_keys: Optional[list[str]] = None,
    comic_keys: Optional[list[str]] = None,
    anime_keys: Optional[list[str]] = None,
    lib=None,
    limit_novel: int = 15,
    limit_comic: int = 8,
    limit_anime: int = 8,
    timeout: float = 15.0,
) -> dict:
    """并发查所有源，返回 {novels, comics, animes, errors}。"""
    kinds = set(kinds)
    novels: list[dict] = []
    comics: list[dict] = []
    animes: list[dict] = []
    errors: list[str] = []

    jobs: list[tuple[str, str]] = []
    # --- 小说源 ---
    if "novel" in kinds:
        keys = list(novel_keys) if novel_keys is not None else [k for k, _ in NOVEL_SOURCES]
        for k in keys:
            if k in _NOVEL_FUNCS:
                jobs.append(("novel", k))
            else:
                errors.append(f"{k}: 未知小说源")

    # --- 番剧源 ---
    if "anime" in kinds:
        from .sources.anime import ANIME_SOURCES

        if anime_keys is None:
            anime_keys = list(getattr(cfg, "anime_sources_default", None) or ANIME_SOURCES)
        akeys = list(anime_keys)
        for k in akeys:
            if k in ANIME_SOURCES:
                jobs.append(("anime", k))
            else:
                errors.append(f"{k}: 未知番剧源")

    # --- 漫画源 ---
    vreg = VeneraRegistry(cfg)
    installed = vreg.installed()
    if "comic" in kinds:
        if comic_keys is None and getattr(cfg, "comic_sources_default", None):
            comic_keys = list(cfg.comic_sources_default)
        wanted = set(comic_keys) if comic_keys is not None else None
        if wanted is not None:
            installed = [(e, p) for e, p in installed if e.key in wanted]
        for entry, _p in installed:
            jobs.append(("comic", entry.key))
        # 内置拷贝漫画源：未显式指定源时默认参与
        if wanted is None or "copy_manga" in wanted:
            jobs.append(("comic", "copy_manga"))

    if not jobs:
        return {"query": q, "novels": [], "comics": [], "animes": [], "errors": errors or ["没有选择任何源"]}

    names = {e.key: e.name for e, _p in VeneraRegistry(cfg).installed()}
    names["copy_manga"] = "拷贝漫画"
    by_key = {e.key: p for e, p in installed}

    ex = ThreadPoolExecutor(max_workers=8)
    futures = {}
    for kind, key in jobs:
        if kind == "novel":
            fut = ex.submit(_NOVEL_FUNCS[key], cfg, q, limit_novel, lib)
        elif kind == "anime":
            fut = ex.submit(_anime_search, cfg, key, q, limit_anime)
        else:
            path = by_key.get(key)
            if path is None and key != "copy_manga":
                continue
            fut = ex.submit(_comic_search, cfg, key, path, q, limit_comic)
        futures[fut] = (kind, key)

    try:
        for fut in as_completed(futures, timeout=timeout):
            kind, name = futures[fut]
            try:
                rows = fut.result() or []
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {str(exc)[:80]}")
                continue
            if kind == "novel":
                novels.extend(rows)
            elif kind == "anime":
                animes.extend(rows)
            else:
                for r in rows:
                    r["source_name"] = names.get(name, name)
                comics.extend(rows)
    except TimeoutError:
        done = {id(f) for f in futures if f.done()}
        slow = [futures[f][1] for f in futures if id(f) not in done]
        errors.append("超时：" + ",".join(slow[:6]))
    finally:
        # 不等慢源：取消未开始的，运行中的丢给后台
        ex.shutdown(wait=False, cancel_futures=True)

    # 同一本书去重（同源同 id）
    def dedupe(rows: list[dict]) -> list[dict]:
        seen, out = set(), []
        for r in rows:
            k = (r.get("source"), r.get("source_id"))
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    return {
        "query": q,
        "novels": dedupe(novels),
        "animes": dedupe(animes),
        "comics": dedupe(comics),
        "errors": errors,
    }
