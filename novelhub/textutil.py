"""HTML → 正文清洗工具（各站点适配器共用）。"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

# 站点拼接在正文里的提示语 / 广告残留
MARKER_PATTERNS = [
    r"[（(]\s*內?容加載失敗[^）)]*[）)]",
    r"[（(]\s*内?容加载失败[^）)]*[）)]",
    r"本章未完[，,、]?\s*[請请]?[點点]?[擊击]?下一[页頁]?[^。！\n]{0,20}",
    r"[（(]\s*本章[^）)]{0,12}下一[页頁][^）)]*[）)]",
    r"請記住本站[^。\n]{0,30}",
    r"请记住本站[^。\n]{0,30}",
    r"[（(]\s*未完待續\s*[）)]",
    r"手机用户请浏览[^。\n]{0,40}",
    r"章节错误[，,]?\s*点此举报[^。\n]{0,20}",
]

_MARKER_RE = re.compile("|".join(MARKER_PATTERNS))

DROP_TAGS = ("script", "style", "noscript", "ins", "iframe", "form", "button", "svg")


def strip_markers(text: str) -> str:
    return _MARKER_RE.sub("", text)


def _clean_container(container: Tag, base_url: str) -> None:
    for tag in container.find_all(DROP_TAGS):
        tag.decompose()
    # 链接只保留文字
    for a in container.find_all("a"):
        a.unwrap()
    # 图片地址补全
    for img in container.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        if src:
            img["src"] = urljoin(base_url, src)
        for attr in list(img.attrs.keys()):
            if attr not in ("src", "alt"):
                del img[attr]
    # 清理锚点、空 span
    for tag in container.find_all(["span", "font", "big", "small"]):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()


def container_to_html(container: Tag, base_url: str = "") -> str:
    """把正文容器转成干净的 <p>/<img> 片段 HTML。"""
    _clean_container(container, base_url)
    parts: list[str] = []

    def emit_paragraph(node: Tag) -> None:
        txt = node.get_text("\n", strip=False)
        txt = strip_markers(txt)
        lines = [ln.strip() for ln in txt.split("\n")]
        lines = [ln for ln in lines if ln]
        for line in lines:
            parts.append(f"<p>{escape_min(line)}</p>")
        for img in node.find_all("img"):
            src = img.get("src", "")
            if src:
                parts.append(f'<p class="img"><img src="{escape_attr(src)}" alt="插图"></p>')

    # 优先按 <p> 收集
    ps = [p for p in container.find_all("p") if p.find_parent("p") is None]
    if ps:
        for p in ps:
            emit_paragraph(p)
        # <p> 之外直接挂的图片
        for img in container.find_all("img"):
            if img.find_parent("p") is None:
                src = img.get("src", "")
                if src:
                    parts.append(f'<p class="img"><img src="{escape_attr(src)}" alt="插图"></p>')
        return "\n".join(parts)

    # 没有 <p>：按 <br> 切
    for br in container.find_all("br"):
        br.replace_with(NavigableString("\n"))
    text = strip_markers(container.get_text("\n"))
    for line in text.split("\n"):
        line = line.strip()
        if line:
            parts.append(f"<p>{escape_min(line)}</p>")
    for img in container.find_all("img"):
        src = img.get("src", "")
        if src:
            parts.append(f'<p class="img"><img src="{escape_attr(src)}" alt="插图"></p>')
    return "\n".join(parts)


def html_to_text(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "lxml")
    lines: list[str] = []
    for p in soup.find_all("p"):
        if p.find("img"):
            alt = p.find("img").get("alt") or "插图"
            lines.append(f"［{alt}］")
            continue
        t = p.get_text(" ", strip=True)
        if t:
            lines.append(t)
    return "\n".join(lines)


def escape_min(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def escape_attr(text: str) -> str:
    return escape_min(text).replace('"', "&quot;")


def clean_title(raw: str) -> str:
    """去掉标题尾部页码标记：（2/4） (1/4) 等。"""
    t = re.sub(r"\s*[（(]\s*\d+\s*/\s*\d+\s*[)）]\s*$", "", raw or "")
    t = re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def make_paragraph_html(html: str) -> str:
    """确保任意 HTML 片段都是 <p> 结构（导入时用）。"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return container_to_html(soup, "")
