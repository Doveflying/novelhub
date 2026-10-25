"""简繁转换。

两条路，优先用能用的那条：

1. **``zhconv``**（推荐）：纯 Python，带完整字表 + 词组消歧，能正确处
   「裡/妳/麵/為/顯著」这类 APK 词表漏掉的字。
2. **APK 里的 OpenCC 词表**（兜底）：从 Venera 的 ``assets/opencc.txt`` 原样搬来，
   3980 条「简繁」单字映射，落在 ``novelhub/data/opencc_s2t.txt``。
   Venera 只用它给漫画标签做简繁归一，所以是**子集**——缺繁体独有字
   （``裡``、``著``、``麵``、``乾``…），没有词组表。没装 zhconv 时才用它。

本项目的主要用途是反方向：**哔哩轻小说等源站正文是繁体**，读起来费劲，
这里做繁体转简体。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import paths

TABLE = paths.resource("data", "opencc_s2t.txt")

try:  # pragma: no cover - 取决于环境
    import zhconv as _zhconv
except Exception:  # noqa: BLE001
    _zhconv = None

# APK 词表反转时会撞车的几个高频繁体字，按常用义兜底
_T2S_OVERRIDE = {
    "著": "着",
    "夥": "伙",
    "乾": "干",
    "麼": "么",
    "祇": "只",
    "藉": "借",
    "餘": "余",
    "鬥": "斗",
    "裡": "里",
    "妳": "你",
    "麵": "面",
    "為": "为",
}


def backend() -> str:
    """当前生效的后端：``zhconv`` 或 ``apk-table``。"""
    return "zhconv" if _zhconv is not None else "apk-table"


def _load() -> tuple[dict[int, str], dict[int, str]]:
    """APK 词表 → (t2s, s2t) 两个 ``str.translate`` 映射。"""
    t2s: dict[int, str] = {}
    s2t: dict[int, str] = {}
    try:
        raw = TABLE.read_text(encoding="utf-8")
    except OSError:
        return t2s, s2t
    for line in raw.splitlines():
        if not line or line.startswith("#") or len(line) < 2:
            continue
        simp, trad = line[0], line[1]
        if simp == trad:
            continue
        s2t[ord(simp)] = trad
        t2s[ord(trad)] = simp
    for trad, simp in _T2S_OVERRIDE.items():
        t2s[ord(trad)] = simp
    return t2s, s2t


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[int, str], dict[int, str]]:
    return _load()


def to_simplified(text: str) -> str:
    """繁体 → 简体。非中文字符原样保留。"""
    if not text:
        return text
    if _zhconv is not None:
        return _zhconv.convert(text, "zh-cn")
    t2s, _ = _tables()
    return text.translate(t2s) if t2s else text


def to_traditional(text: str) -> str:
    """简体 → 繁体。"""
    if not text:
        return text
    if _zhconv is not None:
        return _zhconv.convert(text, "zh-tw")
    _, s2t = _tables()
    return text.translate(s2t) if s2t else text


def has_traditional(text: str, threshold: int = 3) -> bool:
    """粗略判断这段文字是不是繁体。

    做法：转简体后和原文比，差异字够多就算繁体。zhconv 不可用时退回
    「是否含只有繁体才用的字」。
    """
    if not text:
        return False
    sample = text[:4000]
    if _zhconv is not None:
        conv = _zhconv.convert(sample, "zh-cn")
        return sum(1 for a, b in zip(sample, conv) if a != b) >= threshold
    t2s, s2t = _tables()
    if not t2s:
        return False
    hits = 0
    for ch in sample:
        o = ord(ch)
        if o in t2s and o not in s2t:
            hits += 1
            if hits >= threshold:
                return True
    return False


def html_to_simplified(fragment_html: str) -> str:
    """只转文本节点，保留标签与属性（阅读页正文用）。

    一次性整体转换比逐节点转换快，也避免把 ``<script>``/属性值里的东西改坏——
    正文 HTML 里没有脚本，属性只有 src/alt。
    """
    if not fragment_html:
        return fragment_html
    if "<" not in fragment_html:
        return to_simplified(fragment_html)
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in fragment_html:
        if ch == "<":
            if buf:
                out.append(to_simplified("".join(buf)))
                buf.clear()
            depth += 1
            out.append(ch)
        elif ch == ">":
            depth = max(0, depth - 1)
            out.append(ch)
        elif depth > 0:
            out.append(ch)
        else:
            buf.append(ch)
    if buf:
        out.append(to_simplified("".join(buf)))
    return "".join(out)
