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


def _scoped(expr: str) -> str:
    """把开头的 ``//`` 换成 ``.//``。

    lxml 里对某个元素调 ``el.xpath("//div")`` 会**从整个文档根**开始搜，
    不是从 el 开始 —— 于是每条搜索结果都取到全站第一个匹配，分集名全一样。
    加个 ``.`` 才是「在这个节点范围内找」。``//x`` 本身语义就是
    ``/descendant-or-self::node()/child::x``，从根元素换成 ``.//x`` 等价。
    """
    e = (expr or "").strip()
    if e.startswith("//"):
        return "." + e
    return e


def _pick(node, expr: str, base_url: str = "", want: str = "text") -> str:
    """按 expr 从 node 取一个字符串。

    支持两种规则方言：

    **显式属性方言**（本项目的 rules.json 用的）
        ""             → node 自身文本
        "text()"       → node 自身文本
        "a"            → 子节点 a 的文本
        "a@href"       → 子节点 a 的 href

    **元素选择器方言**（Kazumi 原生规则）
        "//h2/a"       → 这个 xpath 只是把 ``<a>`` 选出来，
                         要链接就得自己从元素上取 href

    所以 want="href"/"src" 时：先看有没有写死 ``@attr``；没有就直接选元素，
    再从元素自身（或其内含的 a）上取 href。取不到 href 就退一步看文本
    像不像一个地址，避免把标题当成链接。
    """
    expr = (expr or "").strip()
    if expr in ("", "//", "."):
        if want in ("href", "src"):
            return _own_link(node, base_url, want)
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
            found = node.xpath(_scoped(expr))
        except etree.XPathError:
            return ""
        # xpath 可能返回节点集，也可能直接返回字符串（text() 的情况）
        if not found:
            return "" if not attr else _fallback_attr(node, attr, base_url)
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
    if want in ("href", "src"):
        return _own_link(first, base_url, want)
    return _node_text(first)


def _own_link(node, base_url: str, want: str = "href") -> str:
    """从一个元素上取链接：本身是 <a> 就取它的 href；否则找内含的 a。"""
    if isinstance(node, str):
        return _looks_like_url(node)
    keys = ("href", "data-href", "data-url", "data-src") if want == "href" else \
           ("src", "data-src", "data-original", "data-echo")
    v = ""
    for k in keys:
        got = node.get(k) if hasattr(node, "get") else None
        if got:
            v = _squash(got)
            break
    if not v and hasattr(node, "iter"):
        for a in node.iter():
            tag = getattr(a, "tag", "")
            if not isinstance(tag, str):
                continue
            for k in keys:
                got = a.get(k) if hasattr(a, "get") else None
                if got:
                    v = _squash(got)
                    break
            if v:
                break
    if not v:
        return _looks_like_url(_node_text(node))
    if v.startswith(("javascript:", "#", "mailto:")):
        return ""
    return urljoin(base_url, v) if base_url else v


def _fallback_attr(node, attr: str, base_url: str) -> str:
    """写死 ``@attr`` 但选择器没命中时，退到节点自身再取一次。"""
    v = node.get(attr) if hasattr(node, "get") else None
    v = _squash(v or "")
    if v and base_url and attr in ("href", "src"):
        v = urljoin(base_url, v)
    return v


def _looks_like_url(s: str) -> str:
    """文本像路径/URL 才当链接用，否则返回空 —— 防止把标题当链接。"""
    t = _squash(s)
    if not t or len(t) > 300 or " " in t:
        return ""
    if t.startswith(("http://", "https://", "/", "./", "../")) or "/" in t:
        return t
    return ""


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
    """安全地跑一次 xpath，出错返回空列表。

    ``expr`` 开头的 ``//`` 会被改成 ``.//``：lxml 里 ``el.xpath("//x")``
    是从文档根搜的，会把作用域搞错（分集名全变成全站第一个）。
    """
    expr = (expr or "").strip()
    if not expr:
        return []
    try:
        out = root.xpath(_scoped(expr))
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
        link = _pick(n, rule.search_result, url, want="href")
        cover = _pick(n, rule.search_cover, url, want="src") if rule.search_cover else ""
        if not link:
            # 规则选择器没命中 a，最后再摸一次条目里第一个链接
            link = _own_link(n, url, "href")
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
    if not title:
        # 详情页结构常常和搜索页不同，searchName 选不中；
        # 最后退到 <title>，再退到页面里的 h1。
        heads = _select(root, "//title/text()") or _select(root, "//h1")
        for h in heads:
            t = _clean_page_title(_squash(h)) if isinstance(h, str) else _node_text(h)
            t = _clean_page_title(t)
            if t:
                title = t
                break

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
        进击的巨人最终季完结篇前篇-进击的巨人最终季完结篇前篇在线观看-动漫-MX
    直接当番名显示很难看，这里尽量还原成「进击的巨人 最终季 完结篇 后篇」。
    """
    s = _squash(s)
    if not s:
        return ""
    # 有《》就以它为准
    m = re.search(r"《([^》]{1,80})》", s)
    if m:
        return m.group(1).strip()

    # 否则按常见分隔符切，取第一段。
    # 注意有的站用不带空格的 "-"（MXdm 就是这样），必须一起切。
    # 但 "】" 是右括号不是分隔符 —— 拿它切会把「【国语】」切断。
    for sep in (" - ", "-", "_", "|", "｜", "–", "—", ">", "»", ":"):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if head:
                s = head
                break

    # 反复剥「在线观看」「简介」这类尾巴，一次可能剥不干净。
    # 注意不能把「第2季」这种吃掉 —— 季数丢了弹幕会串到别的前作上。
    tail_re = re.compile(
        r"[\s\-_|]*("
        r"在线观看|在线播放|在线看|免费观看|免费播放|免费观看全集|免费高清|免费看|"
        r"高清完整版|高清在线|完整版|详情介绍|剧情介绍|作品介绍|介绍|简介|"
        r"全集|动漫|番剧|动画|卡通|樱花动漫|在线|高清"
        r")[\s]*$"
    )
    for _ in range(6):
        new = tail_re.sub("", s).strip()
        if new == s:
            break
        s = new

    # 站名前面常挂 emoji / 花边符号（🍴进击！…），去掉
    s = re.sub(r"^[^\w\u4e00-\u9fff《【\[(]+", "", s).strip()
    # 括号没配对就当它不存在，别留一个孤零零的「【国语」
    for lb, rb in (("【", "】"), ("《", "》"), ("(", ")")):
        if s.count(lb) > s.count(rb):
            s = s[:s.rindex(lb)].strip()
    return _squash(s)


def _parse_roads(root, rule: XPathRule, page_url: str) -> list[dict]:
    """解析线路 → 分集。返回 [{road, eps:[{episode,name,url}]}]。"""
    if not rule.chapter_result:
        return []

    # 情况 A：有 chapterRoads，按线路分组
    # "//" / "/" / "." 是 Kazumi 里「没有线路概念」的写法，不是真的 xpath
    raw_roads = (rule.chapter_roads or "").strip()
    groups = [] if raw_roads in ("", "//", "/", ".") else _select(root, raw_roads)
    # 过滤掉纯字符串结果（xpath 直接取 text() 的写法）
    groups = [g for g in groups if not isinstance(g, str)]

    roads: list[dict] = []
    if groups:
        # 有显式线路分组：分集在各自线路节点内找。Kazumi 规则的
        # chapterResult 就是相对该行的选择器，scope 到 g 才是对的。
        for i, g in enumerate(groups):
            eps = _collect_eps(g, rule.chapter_result, page_url,
                               rule.chapter_name)
            if not eps:
                # 少数站点把分集放在线路节点的兄弟/父级里，退到整页再来一次
                eps = _collect_eps(root, rule.chapter_result, page_url,
                                   rule.chapter_name)
            if eps:
                roads.append({"road": _road_name(g, i), "eps": eps})
    else:
        # 情况 B：没有线路概念，整页就是一条线路
        eps = _collect_eps(root, rule.chapter_result, page_url, rule.chapter_name)
        if eps:
            roads.append({"road": "默认线路", "eps": eps})
    return roads


def _road_name(node, idx: int) -> str:
    """线路名：节点的**自身**文本够短才用，否则退回「线路N」。

    线路节点通常是 ``<ul>``，它的 text_content 是所有集名的拼接，
    直接拿来当名字会得到一大串垃圾。
    """
    own = _squash(getattr(node, "text", "") or "")
    if own and len(own) <= 20:
        return own
    label = ""
    for a in (node.iter() if hasattr(node, "iter") else []):
        tag = getattr(a, "tag", "")
        if not isinstance(tag, str):
            continue
        if tag in ("h3", "h4", "dt", "strong", "b", "span", "button"):
            t = _squash(getattr(a, "text", "") or "")
            if t and len(t) <= 20:
                label = t
                break
    return label or f"线路{idx + 1}"


def _collect_eps(scope, expr: str, page_url: str, name_expr: str = "") -> list[dict]:
    """从一个范围里收分集。

    expr 取的是链接：可以是 ``a@href``（显式属性方言），也可以直接是
    ``//li/a``（Kazumi 方言，元素本身就是要取 href 的 <a>）。
    name_expr 若给了就用它取集名，否则退回用 <a> 的文本。
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

    seen: set[str] = set()
    for a in nodes:
        if isinstance(a, str):
            continue
        href = a.get("href") or "" if hasattr(a, "get") else ""
        if not href:
            href = _own_link(a, "", "href")
        href = _squash(href)
        if not href or href.startswith(("javascript:", "#")):
            continue
        href = urljoin(page_url, href)
        if href in seen:
            continue
        seen.add(href)
        if name_expr:
            name = _pick(a, name_expr) or _node_text(a)
        else:
            name = _node_text(a)
        name = name or f"第{len(eps) + 1}集"
        eps.append({
            "episode": _ep_no(name, len(eps) + 1),
            "name": name,
            "url": href,
        })
    return eps


_EP_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


_EP_PATTERNS = (
    re.compile(r"第\s*(\d{1,4})\s*[话話集期]"),   # 第01话 / 第12集
    re.compile(r"更新至?\s*(\d{1,4})"),            # 更新至05话
    re.compile(r"\bEP?\s*(\d{1,4})\b", re.I),      # EP12 / E05
    re.compile(r"\bS\d{1,2}E(\d{1,3})\b", re.I),   # S01E12
    re.compile(r"\[(\d{1,4})\]"),                  # [12]
)
# 分集名里常见的画质/编码/字幕标签，出现在数字后面时那个数字不是集号
_QUALITY_TAIL = set("pPkKiI")
_QUALITY_WORDS = re.compile(
    r"(1080|720|2160|480|4k|8k|hevc|avc|x26[45]|h26[45]|aac|flac|web-?dl|webrip"
    r"|blu-?ray|bdrip|hdrip|简中|繁中|简体|繁体|中字|内嵌|外挂|双语|字幕|高清|超清|蓝光"
    r"|完结|全集|更新至?\d*话?|hd|bd|tc|ts)",
    re.I,
)


def _ep_no(name: str, fallback: int) -> int:
    """从集名里抠集号（"第01话" → 1，"EP12" → 12）；抠不到就用序号。

    弹幕是按集号去匹配的，这个字段必须真的有值。麻烦的是站名里常混着
    "简中1080P" 这类标签 —— 直接取第一个数字会得到 1080，所以先按
    明确的集号模式匹配，并且跳过紧跟画质单位的数字。
    """
    s = _squash(name or "")
    if not s:
        return fallback

    for pat in _EP_PATTERNS:
        m = pat.search(s)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                continue

    # 没有任何明确模式：取“不是画质标签”的数字
    for m in _EP_NUM_RE.finditer(s):
        tail = s[m.end():m.end() + 1]
        if tail in _QUALITY_TAIL:
            continue
        head = s[max(0, m.start() - 1):m.start()]
        if head and head.isalpha():
            continue
        val = m.group(1)
        if _QUALITY_WORDS.fullmatch(val):
            continue
        try:
            v = int(float(val))
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 2000:
            return v
    return fallback


def load_xpath_rules(cfg: Config, rules_path=None) -> dict[str, XPathRule]:
    """读 rules.json，筛出能走 xpath 抓取的规则。

    Kazumi 原生规则**没有 searchMode 字段**（默认就是 xpath），所以不能
    要求它显式等于 "xpath"，否则会把绝大多数规则筛掉。只有显式写成
    别的模式（"api" 等，返回 JSON 的）才排除。

    返回 {小写 key: XPathRule}。坏规则直接跳过，不让一条写错的 xpath
    把整个源列表搞崩。
    """
    import json
    from pathlib import Path

    from .. import paths

    if rules_path is None:
        rules_path = paths.resource("refs", "anime", "rules.json")
    p = Path(rules_path)
    if not p.exists():
        return {}
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(rules, dict):
        # 有的规则集是 {key: rule} 形态
        rules = [dict(v, name=v.get("name") or k) if isinstance(v, dict) else v
                 for k, v in rules.items()]
    if not isinstance(rules, list):
        return {}

    out: dict[str, XPathRule] = {}
    for d in rules:
        if not isinstance(d, dict):
            continue
        if str(d.get("type") or "anime").lower() != "anime":
            continue
        mode = str(d.get("searchMode") or "xpath").lower()
        if mode != "xpath":
            continue
        rule = XPathRule.from_dict(d)
        if rule.validate():
            continue
        out[rule.name.lower()] = rule
    return out
