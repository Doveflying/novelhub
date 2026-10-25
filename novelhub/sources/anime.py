# -*- coding: utf-8 -*-
"""番剧源：苹果 CMS V10 采集接口（接口来自 web/anime_server/rules.json + anime_app 插件）。

约定（MacCMS V10 ``api.php/provide/vod/``）：

- 搜索：``?ac=detail&wd=关键词`` → ``{"list":[{vod_id, vod_name, vod_pic, vod_play_from, vod_play_url, ...}]}``
- 详情：``?ac=detail&ids=ID``     → 同上，``list[0]``
- 播放：``vod_play_from`` 是 ``$`` 分隔的线路名（如 ``wjm3u8``），
  ``vod_play_url`` 同序，每条线路里是 ``集名$地址#集名$地址``。

地址基本都是 ``.m3u8``，浏览器不能直接播（Chrome 无原生 HLS），所以播放页用
hls.js + 本地切片代理（见 webapp 的 ``/api/hls``）。
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote, urljoin

from ..config import Config
from ..net import Fetcher

# key -> (显示名, 接口根, Referer)
BUILTIN_SOURCES: dict[str, tuple[str, str, str]] = {
    "girigiri": ("girigiri", "https://m3u8.girigirilove.com/api.php/provide/vod/",
                 "https://ani.girigirilove.com/"),
    "wujin": ("无尽", "https://api.wujinapi.com/api.php/provide/vod/", ""),
    "apibdzy": ("哔嘀", "https://api.apibdzy.com/api.php/provide/vod/", "https://api.apibdzy.com/"),
    "guangsu": ("光速", "https://api.guangsuapi.com/api.php/provide/vod/", ""),
    "ffzy": ("非凡", "https://cj.ffzyapi.com/api.php/provide/vod/", ""),
    "lzi": ("量子", "https://cj.lziapi.com/api.php/provide/vod/", ""),
}


def _load_rule_sources() -> dict[str, tuple[str, str, str]]:
    """把 refs/anime/rules.json 里 api 模式的规则并进来（改规则不用改代码）。

    xpath 模式的规则不走这里 —— 它们需要 XPathRule 对象，
    由 ``_load_xpath_sources()`` 注册到 ``XPATH_SOURCES``。
    """
    import json
    from pathlib import Path

    from .. import paths

    out: dict[str, tuple[str, str, str]] = {}
    p = paths.resource("refs", "anime", "rules.json")
    if not p.exists():
        return out
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return out
    for r in rules:
        if r.get("type", "anime") != "anime" or r.get("searchMode") != "api":
            continue
        name = str(r.get("name") or "").strip()
        base = str(r.get("baseURL") or "").strip()
        if not name or not base:
            continue
        if not base.endswith("/"):
            base += "/"
        if "/api.php/provide/vod" not in base:
            continue
        out[name.lower()] = (name, base, str(r.get("referer") or ""))
    return out


def _load_xpath_sources() -> dict[str, tuple[str, str, str]]:
    """把 xpath 模式的规则也挂进 ANIME_SOURCES（供 UI 列表/健康检查用）。

    实际抓取走 rule_xpath 模块，这里只是让它们出现在源列表里。
    """
    from .rule_xpath import load_xpath_rules

    try:
        rules = load_xpath_rules(None)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[str, str, str]] = {}
    for key, rule in rules.items():
        base = rule.base_url
        if base and not base.endswith("/"):
            base += "/"
        out[key] = (rule.name, base, rule.referer)
    return out


ANIME_SOURCES: dict[str, tuple[str, str, str]] = {
    **BUILTIN_SOURCES,
    **_load_rule_sources(),
    **_load_xpath_sources(),
}
# 老的 4 源顺序优先，新增的排后面
_ORDER = ["girigiri", "wujin", "apibdzy", "ffzy", "lzi", "guangsu"]
ANIME_SOURCES = {
    **{k: ANIME_SOURCES[k] for k in _ORDER if k in ANIME_SOURCES},
    **{k: v for k, v in ANIME_SOURCES.items() if k not in _ORDER},
}

# xpath 源（需要走 rule_xpath，不是 MacCMS 接口）
XPATH_SOURCES: dict = {}


def _xpath_rule(key: str):
    """取某个 key 对应的 XPathRule；不是 xpath 源就返回 None。"""
    global XPATH_SOURCES
    if not XPATH_SOURCES:
        try:
            from .rule_xpath import load_xpath_rules

            XPATH_SOURCES = load_xpath_rules(None)
        except Exception:  # noqa: BLE001
            XPATH_SOURCES = {}
    return XPATH_SOURCES.get(key.lower())


def _clean(s) -> str:
    """把采集接口的任意标量（str/int/float/None）压成干净字符串。

    MacCMS 各站的 vod_total / vod_id / vod_year 有时给 int 有时给 str，
    直接 re.sub 会炸 "expected string or bytes-like object, got 'int'"。
    """
    if s is None:
        return ""
    if isinstance(s, bool):
        return "1" if s else "0"
    if isinstance(s, (int, float)):
        # 12.0 这种整数值去掉小数点，避免 "12.0 集"
        return str(int(s)) if float(s).is_integer() else str(s)
    if isinstance(s, (list, tuple)):
        return " ".join(_clean(x) for x in s if x is not None)
    return re.sub(r"\s+", " ", str(s)).strip()


def _year_of(item: dict) -> str:
    for k in ("vod_year", "vod_pubdate"):
        v = _clean(item.get(k))
        m = re.search(r"(19|20)\d{2}", v)
        if m:
            return m.group(0)
    return ""


def parse_play(froms: str, urls: str) -> list[dict]:
    """把 vod_play_from / vod_play_url 解析成 [{road, eps:[{name,url}]}]。"""
    from_list = [x.strip() for x in (froms or "").split("$$$") if x.strip()]
    url_groups = [x for x in (urls or "").split("$$$")]
    out: list[dict] = []
    for i, group in enumerate(url_groups):
        road = from_list[i] if i < len(from_list) else f"线路{i + 1}"
        eps = []
        for chunk in group.split("#"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "$" in chunk:
                name, url = chunk.split("$", 1)
            else:
                name, url = f"第{len(eps) + 1}集", chunk
            name, url = _clean(name), url.strip()
            if not url.startswith(("http://", "https://")):
                continue
            eps.append({"name": name or f"第{len(eps) + 1}集", "url": url})
        if eps:
            out.append({"road": road, "eps": eps})
    return out


def _row(source: str, it: dict) -> dict:
    """MacCMS 的 vod_* → 项目内部 meta。

    这里只取「列表页也能拿到」的字段；roads（线路/分集）在 detail() 里补，
    因为列表页的 vod_play_url 常常是空的或被裁剪过。
    """
    remarks = _clean(it.get("vod_remarks"))
    total, isend = _episode_meta(it, remarks)
    return {
        "source": source,
        "source_name": ANIME_SOURCES.get(source, (source,))[0],
        "source_id": _clean(it.get("vod_id")),
        "title": _clean(it.get("vod_name")),
        "cover": _clean(it.get("vod_pic")),
        "year": _year_of(it),
        "remark": remarks,
        # —— 以下为番剧专属元数据（存入 animes 表的新列）——
        "remarks": remarks,
        "total": total,
        "isend": isend,
        "score": _score_of(it),
        "tags": _clean(it.get("vod_class")),
        "area": _area_of(it),
        "kind": "anime",
    }


def _area_of(it: dict) -> str:
    """取地区，并过滤掉被误填成季度/月份的站。

    girigiri 把 vod_area 塞成了「十月」这种播出季（实测），
    这类值当地区显示很怪，宁可留空。
    """
    v = _clean(it.get("vod_area"))
    if not v:
        return ""
    if re.fullmatch(r"[正一二三四五六七八九十\d]{1,3}\s*月(份)?", v):
        return ""          # 「十月」「10月」→ 播出季，不是地区
    if re.fullmatch(r"(春|夏|秋|冬)(季)?", v):
        return ""          # 「秋季」
    if re.fullmatch(r"[Qq][1-4]|[1-4]季度?", v):
        return ""
    return v[:60]


def _score_of(it: dict) -> float:
    for k in ("vod_score", "vod_douban_score"):
        v = _clean(it.get(k))
        m = re.search(r"\d+(?:\.\d+)?", v)
        if m:
            try:
                s = float(m.group(0))
            except ValueError:
                continue
            if 0 < s <= 10:
                return s
    return 0.0


def _episode_meta(it: dict, remarks: str) -> tuple[int, int]:
    """从 vod_total / vod_isend / 备注 推断 (总集数, 是否完结)。

    各采集站这几个字段很不统一：
    - vod_total 有时是集数，有时是 0 / 缺失
    - vod_isend 常见 0/1，也有 "1"/true
    - 备注里常写 "更新至12集" / "全24集" / "已完结"
    """
    total = 0
    raw_total = _clean(it.get("vod_total"))
    m = re.search(r"\d+", raw_total)
    if m:
        total = int(m.group(0))

    isend = 0
    raw_end = _clean(it.get("vod_isend")).lower()
    if raw_end in ("1", "true", "yes", "y"):
        isend = 1

    # 备注兜底：全N集 / 已完结 → 完结；更新至N集 → 未完结但能拿到总集数
    if not isend and re.search(r"已完结|完结|全集|全\s*\d+\s*集", remarks):
        isend = 1
    if not total:
        m2 = re.search(r"(?:全|更新至|更新到)\s*(\d+)\s*集", remarks)
        if m2:
            total = int(m2.group(1))
    return total, isend


def _get_json(cfg: Config, source: str, url: str) -> dict:
    _name, _base, ref = ANIME_SOURCES.get(source, (source, "", ""))
    with Fetcher(cfg) as f:
        txt = f.get(url, referer=ref or None, use_cache=False)
    try:
        return json.loads(txt)
    except ValueError:
        # 少数站返回前有 BOM / HTML 警告
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def search(cfg: Config, source: str, keyword: str, limit: int = 12) -> list[dict]:
    # xpath 规则源走另一条路径
    rule = _xpath_rule(source)
    if rule is not None:
        from .rule_xpath import search as xp_search

        return xp_search(cfg, rule, keyword, limit)

    _name, base, _ref = ANIME_SOURCES[source]
    url = f"{base}?ac=detail&wd={quote(keyword)}"
    data = _get_json(cfg, source, url)
    rows = [_row(source, it) for it in (data.get("list") or [])]
    rows = [r for r in rows if r["source_id"] and r["title"]]
    return rows[:limit]


def detail(cfg: Config, source: str, sid: str) -> dict:
    # xpath 规则源：sid 是详情页 URL
    rule = _xpath_rule(source)
    if rule is not None:
        from .rule_xpath import detail as xp_detail

        return xp_detail(cfg, rule, sid)

    _name, base, _ref = ANIME_SOURCES[source]
    data = _get_json(cfg, source, f"{base}?ac=detail&ids={quote(str(sid))}")
    lst = data.get("list") or []
    if not lst:
        raise RuntimeError(f"{source} 没有这部剧：{sid}")
    it = lst[0]
    info = _row(source, it)
    roads = parse_play(it.get("vod_play_from") or "", it.get("vod_play_url") or "")
    # 详情页能拿到更准的集数：优先用实际分集数，其次才用 vod_total
    real_eps = max((len(r["eps"]) for r in roads), default=0)
    if real_eps > info["total"]:
        info["total"] = real_eps
    info.update(
        intro=_clean(it.get("vod_content") or it.get("vod_blurb"))[:800],
        actor=_clean(it.get("vod_actor"))[:120],
        director=_clean(it.get("vod_director"))[:120],
        roads=roads,
    )
    return info
