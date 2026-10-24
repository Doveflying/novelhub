# -*- coding: utf-8 -*-
"""Kazumi 风格 xpath 规则引擎：用 rules.json 描述非 API 站点，不改代码就能加源。

规则字段（对齐 refs/anime/rules.json，也兼容 Kazumi 的写法）：

搜索
    searchMode      "xpath"
    searchURL       带 @keyword 占位的搜索地址
    searchList      结果列表的 xpath（每个节点是一条结果）
    searchName      节点内取标题的相对 xpath
    searchResult    节点内取详情链接/ID 的相对 xpath

详情 / 分集
    chapterMode     "xpath"
    chapterRoads    线路分组的 xpath（每个节点是一条线路）
    chapterResult   线路内取分集的相对 xpath（每个 <a> 是一集）

取值约定
    "a@href"     取属性
    "a@text"     取文本（等价于 a/text()）
    "//div"      绝对 xpath
    "./div"      相对当前节点
    留空 / "//"  表示就是当前节点本身

设计取舍
- 只依赖 lxml，不引 parsel（少一个依赖）
- xpath 写错不抛异常，返回空列表，错误记在 RuleError 里
  （站点改版很常见，一条规则坏掉不该让整个源列表崩）
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urljoin

from lxml import html as lxml_html
from lxml import etree

from ..config import Config
from ..net import Fetcher


class RuleError(RuntimeError):
    """规则本身有问题（xpath 写错、必填字段缺失）。"""


@dataclass
class XPathRule:
    """一条 xpath 模式规则的完整描述。"""

    name: str
    base_url: str = ""
    referer: str = ""
    user_agent: str = ""
    use_webview: bool = False
    use_post: bool = False

    search_url: str = ""
    search_list: str = ""
    search_name: str = ""
    search_result: str = ""
    search_cover: str = ""

    chapter_url: str = ""
    chapter_roads: str = ""
    chapter_result: str = ""
    chapter_name: str = ""

    title_xpath: str = ""

    raw: dict = field(default_factory=dict)

    # ---------- 构造 ----------
    @classmethod
    def from_dict(cls, d: dict) -> "XPathRule":
        return cls(
            name=str(d.get("name") or "").strip(),
            base_url=str(d.get("baseURL") or "").strip(),
            referer=str(d.get("referer") or "").strip(),
            user_agent=str(d.get("userAgent") or "").strip(),
            use_webview=bool(d.get("useWebview")),
            use_post=bool(d.get("usePost")),
            search_url=str(d.get("searchURL") or "").strip(),
            search_list=str(d.get("searchList") or "").strip(),
            search_name=str(d.get("searchName") or "").strip(),
            search_result=str(d.get("searchResult") or "").strip(),
            search_cover=str(d.get("searchCover") or "").strip(),
            chapter_url=str(d.get("chapterURL") or "").strip(),
            chapter_roads=str(d.get("chapterRoads") or "").strip(),
            chapter_result=str(d.get("chapterResult") or "").strip(),
            chapter_name=str(d.get("chapterName") or "").strip(),
            title_xpath=str(d.get("titleXPath") or "").strip(),
            raw=d,
        )

    def validate(self) -> list[str]:
        """返回缺失/可疑字段，空列表表示可用。"""
        bad: list[str] = []
        if not self.name:
            bad.append("name 为空")
        if not self.search_url:
            bad.append("searchURL 为空")
        if "@keyword" not in self.search_url:
            bad.append("searchURL 里没有 @keyword 占位符")
        if not self.search_list:
            bad.append("searchList 为空")
        if not self.search_result:
            bad.append("searchResult 为空")
        if self.chapter_mode == "xpath" and not self.chapter_result:
            bad.append("chapterResult 为空")
        return bad

    @property
    def search_mode(self) -> str:
        return str(self.raw.get("searchMode") or "xpath").lower()

    @property
    def chapter_mode(self) -> str:
        return str(self.raw.get("chapterMode") or "xpath").lower()


# ---------------------------------------------------------------- 取值

_ATTR_RE = re.compile(r"^(?P<path>.*?)@(?P<attr>[\w:-]+)$")


def encode_sid(url: str) -> str:
    """把详情页 URL 编成可以放进 URL 路径的 ID。

    路由是 ``/play/<source>/<sid>/<ep>`` 和 ``/anime/<source>/<sid>``，
    sid 里带 ``/`` 和 ``:`` 会把路径拆坏（实测 404）。
    用 urlsafe base64 去掉 ``=`` 填充，得到纯 ``[A-Za-z0-9_-]``。
    """
    raw = (url or "").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_sid(sid: str) -> str:
    """encode_sid 的逆运算。解不出来就原样返回（兼容纯数字 ID 的老数据）。"""
    s = (sid or "").strip()
    if not s or s.startswith(("http://", "https://")):
        return s
    pad = "=" * (-len(s) % 4)
    try:
        out = base64.urlsafe_b64decode(s + pad).decode("utf-8")
    except Exception:  # noqa: BLE001
        return s
    return out if out.startswith(("http://", "https://")) else s


def _pick(node, expr: str, base_url: str = "") -> str:
    """按 expr 从 node 取一个字符串。

    expr 形态：
        ""             → node 自身文本
        "//"           → node 自身文本（rules.json 里见过这种写法）
        "text()"       → node 自身文本
        "@href"        → node 的属性
        "a"            → 子节点 a 的文本
        "a@href"       → 子节点 a 的 href
        "//div[2]/a"   → 绝对 xpath
        "a/text()"     → 显式 text()
    """
    expr = (expr or "").strip()
    if expr in ("", "//", "."):
        return _node_text(node)

    attr = ""
    m = _ATTR_RE.match(expr)
    if m:
        expr = m.group("path").strip()
        attr = m.group("attr")
        # ".../@title" 剥掉 @title 后尾部会剩一个 "/"，
        # 直接拿去做 xpath 会语法错，必须去掉
        expr = expr.rstrip("/").strip()

    if expr in ("", "."):
        target = [node]
    else:
        try:
            found = node.xpath(expr)
        except etree.XPathError:
            return ""
        # xpath 可能返回节点集，也可能直接返回字符串（text() 的情况）
        if not found:
            return ""
        if isinstance(found[0], str):
            return _squash(found[0])
        target = found

    first = target[0]
    if attr:
        if attr == "text":
            return _node_text(first)
        v = first.get(attr) if hasattr(first, "get") else None
        v = _squash(v or "")
        if v and base_url and attr in ("href", "src"):
            v = urljoin(base_url, v)
        return v
    return _node_text(first)


def _node_text(node) -> str:
    if isinstance(node, str):
        return _squash(node)
    try:
        t = node.text_content()
    except AttributeError:
        t = "".join(node.itertext())
    return _squash(t)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _select(root, expr: str) -> list:
    """安全地跑一次 xpath，出错返回空列表。"""
    expr = (expr or "").strip()
    if not expr:
        return []
    try:
        out = root.xpath(expr)
    except etree.XPathError:
        return []
    if not out:
        return []
    if isinstance(out[0], str):
        return out
    return out


# ---------------------------------------------------------------- 抓取

def _fetch_html(cfg: Config, url: str, rule: XPathRule,
                form: Optional[dict] = None) -> str:
    """抓一页 HTML。rule 里的 referer / userAgent 优先。"""
    ref = rule.referer or None
    with Fetcher(cfg) as f:
        if rule.user_agent:
            f.session.headers["User-Agent"] = rule.user_agent
        if form is not None:
            return f.post(url, form, referer=ref)
        return f.get(url, referer=ref, use_cache=False)


def _parse(html_text: str):
    """HTML → lxml 根节点。编码问题交给 lxml 自己猜（它认 meta charset）。"""
    if not html_text:
        return None
    try:
        return lxml_html.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return None


# ---------------------------------------------------------------- 对外接口

def search(cfg: Config, rule: XPathRule, keyword: str, limit: int = 12) -> list[dict]:
    """xpath 规则搜索，返回和 anime.search() 同构的 meta 列表。"""
    if not rule.search_url or not rule.search_list:
        raise RuleError(f"{rule.name} 规则不完整：缺少 searchURL / searchList")

    url = rule.search_url.replace("@keyword", quote(keyword))
    form = None
    if rule.use_post:
        # 少数站点搜索是 POST，把 keyword 塞表单
        form = {"wd": keyword, "keyword": keyword, "searchword": keyword}
        url = url.split("?")[0]
    text = _fetch_html(cfg, url, rule, form)
    root = _parse(text)
    if root is None:
        raise RuleError(f"{rule.name} 搜索页解析失败（不是 HTML？）")

    nodes = _select(root, rule.search_list)
    rows: list[dict] = []
    for n in nodes:
        if isinstance(n, str):
            continue
        title = _pick(n, rule.search_name) if rule.search_name else ""
        link = _pick(n, rule.search_result, url)
        cover = _pick(n, rule.search_cover, url) if rule.search_cover else ""
        if not link:
            continue
        # 有的站 searchResult 给的是相对路径，urljoin 已经补过 base；
        # 但 base 可能是搜索页而不是站点根，这里再兜一次
        if rule.base_url:
            link = urljoin(rule.base_url, link)
        if not title:
            title = link.rstrip("/").rsplit("/", 1)[-1]
        rows.append({
            "source": rule.name.lower(),
            "source_name": rule.name,
            # source_id 用编码后的形式，才能安全放进 URL 路径
            "source_id": encode_sid(link),
            "title": title,
            "cover": cover,
            "year": "",
            "remark": "",
            "remarks": "",
            "total": 0,
            "isend": 0,
            "score": 0.0,
            "tags": "",
            "area": "",
            "kind": "anime",
            "url": link,
        })
        if len(rows) >= limit:
            break
    return rows


def detail(cfg: Config, rule: XPathRule, detail_url: str) -> dict:
    """xpath 规则详情：抓详情页，解析线路与分集。

    detail_url 可能是：
      - 完整 URL（内部直接调用时）
      - encode_sid() 编过的串（从路由传进来的 source_id）
      - 纯 ID（少数规则用数字 ID）
    """
    url = decode_sid(str(detail_url))
    if not url.startswith(("http://", "https://")):
        if rule.chapter_url and "@id" in rule.chapter_url:
            url = rule.chapter_url.replace("@id", quote(str(url)))
        elif rule.base_url:
            url = urljoin(rule.base_url, str(url))

    text = _fetch_html(cfg, url, rule)
    root = _parse(text)
    if root is None:
        raise RuleError(f"{rule.name} 详情页解析失败：{url}")

    # 标题：优先用规则里显式给的 titleXPath，其次 searchName 绝对化试一次
    title = ""
    if rule.title_xpath:
        raw_title = _pick(root, rule.title_xpath)
        # titleXPath 指向 <title> 时带站名后缀，剥一下
        title = _clean_page_title(raw_title) if "title" in rule.title_xpath.lower() else raw_title
    if not title and rule.search_name:
        cand = rule.search_name
        if not cand.startswith(("/", ".")):
            cand = "//" + cand
        title = _pick(root, cand)

    roads = _parse_roads(root, rule, url)
    total = max((len(r["eps"]) for r in roads), default=0)

    info = {
        "source": rule.name.lower(),
        "source_name": rule.name,
        "source_id": encode_sid(url),
        "title": title or url.rstrip("/").rsplit("/", 1)[-1],
        "cover": "",
        "year": "",
        "intro": "",
        "remark": "",
        "remarks": "",
        "total": total,
        "isend": 0,
        "score": 0.0,
        "tags": "",
        "area": "",
        "actor": "",
        "director": "",
        "kind": "anime",
        "url": url,
        "roads": roads,
    }
    return info


def _clean_page_title(s: str) -> str:
    """从 <title> 里剥掉站名后缀和《》书名号。

    采集站的详情页 <title> 常写成
        《进击的巨人 最终季 完结篇 后篇》简介 - 七色番动漫视频网 - (つェ⊂) 7SeFuN
    直接当番名显示很难看，这里尽量还原成「进击的巨人 最终季 完结篇 后篇」。
    """
    s = _squash(s)
    if not s:
        return ""
    # 有《》就以它为准
    m = re.search(r"《([^》]{1,80})》", s)
    if m:
        return m.group(1).strip()
    # 否则按常见分隔符切，取第一段
    for sep in (" - ", "_", "|", "–", "—"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    # 去掉「简介」「在线观看」这类尾巴
    s = re.sub(r"\s*(简介|全集|在线观看|免费观看|高清完整版|动漫|番剧)\s*$", "", s)
    return _squash(s)


def _parse_roads(root, rule: XPathRule, page_url: str) -> list[dict]:
    """解析线路 → 分集。返回 [{road, eps:[{name,url}]}]。"""
    if not rule.chapter_result:
        return []

    # 情况 A：有 chapterRoads，按线路分组
    groups = _select(root, rule.chapter_roads) if rule.chapter_roads else []
    # 过滤掉纯字符串结果（xpath 直接取 text() 的写法）
    groups = [g for g in groups if not isinstance(g, str)]

    roads: list[dict] = []
    if groups:
        # 有显式线路分组：线路名取分组节点文本；
        # 分集 xpath 若是绝对路径（以 / 开头），就在整页范围里取，
        # 否则在该线路节点内取（相对路径写法）。
        absolute = rule.chapter_result.startswith("/")
        for i, g in enumerate(groups):
            road_name = _node_text(g)[:40] or f"线路{i + 1}"
            scope = root if absolute else g
            eps = _collect_eps(scope, rule.chapter_result, page_url,
                               rule.chapter_name)
            if eps:
                roads.append({"road": road_name, "eps": eps})
    else:
        # 情况 B：没有线路概念，整页就是一条线路
        eps = _collect_eps(root, rule.chapter_result, page_url, rule.chapter_name)
        if eps:
            roads.append({"road": "默认线路", "eps": eps})
    return roads


def _collect_eps(scope, expr: str, page_url: str, name_expr: str = "") -> list[dict]:
    """从一个范围里收分集。

    expr 取的是链接（通常是 ".../a@href"）；name_expr 若给了就用它取集名，
    否则退回用 <a> 的文本。
    """
    eps: list[dict] = []

    # 先拿节点，再取 href —— 这样 name_expr 能复用同一批节点
    node_expr = expr
    if expr.endswith("@href"):
        node_expr = expr[:-5]
    elif "@" in expr:
        node_expr = expr.rsplit("@", 1)[0]

    nodes = _select(scope, node_expr) if node_expr else []
    if not nodes:
        # 退化：expr 直接就是属性路径
        return eps

    for a in nodes:
        if isinstance(a, str):
            continue
        href = a.get("href") or ""
        if not href or href.startswith("javascript:"):
            continue
        href = urljoin(page_url, href)
        if name_expr:
            name = _pick(a, name_expr) or _node_text(a)
        else:
            name = _node_text(a)
        eps.append({"name": name or f"第{len(eps) + 1}集", "url": href})
    return eps


def load_xpath_rules(cfg: Config, rules_path=None) -> dict[str, XPathRule]:
    """读 rules.json，筛出 searchMode=xpath 且能通过校验的规则。

    返回 {小写 key: XPathRule}。坏规则直接跳过，不让一条写错的 xpath
    把整个源列表搞崩。
    """
    import json
    from pathlib import Path

    if rules_path is None:
        rules_path = (Path(__file__).resolve().parent.parent.parent
                      / "refs" / "anime" / "rules.json")
    p = Path(rules_path)
    if not p.exists():
        return {}
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(rules, list):
        return {}

    out: dict[str, XPathRule] = {}
    for d in rules:
        if not isinstance(d, dict):
            continue
        if str(d.get("type") or "anime").lower() != "anime":
            continue
        if str(d.get("searchMode") or "").lower() != "xpath":
            continue
        rule = XPathRule.from_dict(d)
        if rule.validate():
            continue
        out[rule.name.lower()] = rule
    return out
