"""Bangumi 元数据源（第三类小说源：只做发现，不提供正文）。

用它的理由：国内小说站基本都连不上，但 `api.bgm.tv` 通。它的价值是
**书名权威 + 中文名 + 封面 + 简介**，搜到之后再去哔哩轻小说的书目索引里
找同名可读版本 —— 这样「换源阅读」才有东西可选。

接口：`GET https://api.bgm.tv/search/subject/{keyword}?type=1&responseGroup=small&max_results=N`
（type=1 是书籍，type=2 是动画）
"""
from __future__ import annotations

from urllib.parse import quote

from ..config import Config
from ..net import Fetcher

BGM_SEARCH = "https://api.bgm.tv/search/subject/{kw}?type=1&responseGroup=small&max_results={n}"
BGM_SUBJECT = "https://api.bgm.tv/v0/subjects/{id}"


def search(cfg: Config, keyword: str, limit: int = 10) -> list[dict]:
    """搜 Bangumi 书籍条目。"""
    import json

    url = BGM_SEARCH.format(kw=quote(keyword), n=max(3, min(limit, 20)))
    with Fetcher(cfg) as f:
        body = f.get(url, referer="https://bgm.tv/", use_cache=False)
    try:
        data = json.loads(body)
    except ValueError:
        return []

    out = []
    for it in (data.get("list") or [])[:limit]:
        name_cn = (it.get("name_cn") or "").strip()
        name = (it.get("name") or "").strip()
        img = (it.get("images") or {}).get("large") or (it.get("images") or {}).get("common") or ""
        if img.startswith("http://"):
            img = "https://" + img[len("http://"):]
        out.append(
            {
                "source": "bangumi",
                "source_name": "Bangumi（书目）",
                "source_id": str(it.get("id")),
                "title": name_cn or name,
                "title_jp": name if name_cn else "",
                "author": "",
                "cover": img,
                "category": "",
                "intro": (it.get("summary") or "")[:120],
                "url": f"https://bgm.tv/subject/{it.get('id')}",
                "kind": "novel",
                "action": "meta",
                "book_id": None,
                "bgm_id": it.get("id"),
            }
        )
    return out


def cross_to_index(lib, title: str, limit: int = 5) -> list[dict]:
    """把 Bangumi 搜到的书名，拿去哔哩轻小说索引里找可读版本。"""
    if lib is None or not title:
        return []
    rows = lib.search_novel_index(title, source="linovelib", limit=limit)
    out = []
    for r in rows:
        r = dict(r)
        out.append(
            {
                "source": "linovelib",
                "source_name": "哔哩轻小说",
                "source_id": str(r["source_id"]),
                "title": r.get("title") or "",
                "author": r.get("author") or "",
                "category": r.get("category") or "",
                "cover": r.get("cover") or "",
                "intro": (r.get("intro") or "")[:120],
                "url": r.get("url") or "",
                "kind": "novel",
                "action": "add",
                "book_id": None,
            }
        )
    return out
