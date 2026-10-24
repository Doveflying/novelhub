"""本地资源导入：TXT / EPUB / Markdown / 目录批量。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from .store import Library
from .textutil import html_to_text

# 章节标题行：第一章 / 第1话 / 序幕 / 后记 / Chapter 1 ...
CHAPTER_HEAD_RE = re.compile(
    r"^[\s　]*("
    r"第[0-9零一二三四五六七八九十百千万两〇]{1,8}[章节話话回幕節节集部篇尾]"
    r"|(?:序章|序言|序幕|楔子|引子|前言|后记|後記|尾声|尾聲|终章|終章|番外|间章|間章)"
    r"|Chapter\s+\d+"
    r"|Prologue|Epilogue"
    r")",
    re.I,
)

VOLUME_RE = re.compile(
    r"^[\s　]*第[0-9零一二三四五六七八九十百千万两〇]{1,8}[卷部][\s　]*[:：、.．]?.*$"
)

AUTHOR_RE = re.compile(r"^\s*(?:作者|作\s*者|Author|著者)\s*[:：]\s*(.+?)\s*$", re.I)


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            text = data.decode(enc)
            if text.count("\ufffd") < len(text) * 0.01:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_chapter_heading(line: str) -> bool:
    """判断一行是不是章节标题（而不是以『第三章内容。』这类正文开头的句子）。"""
    s = line.strip()
    if not s or len(s) > 40:
        return False
    m = CHAPTER_HEAD_RE.match(s)
    if not m:
        return False
    rest = s[m.end():].strip()
    if not rest:
        return True
    # 正文句子的特征
    if rest[0] in "，。,；;！？…":
        return False
    if rest[-1] in "。，,；;！？…":
        return False
    return True


def _guess_author(text: str) -> str:
    for line in text.split("\n")[:15]:
        m = AUTHOR_RE.match(line)
        if m:
            return m.group(1).strip()[:40]
    return ""


def split_chapters(
    text: str,
) -> tuple[list[tuple[str, str, int]], dict[int, str], bool]:
    """按标题行切章。

    返回 ([(标题, 正文, 卷序), ...], {卷序: 卷名}, 是否识别到结构)。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chapters: list[tuple[str, str, int]] = []
    volumes: dict[int, str] = {}
    vol_idx = 0
    cur_title: Optional[str] = None
    cur_vol = 0
    buf: list[str] = []
    found = False
    heading_at = 0  # 最近一次标题所在行号，用于识别「文件头」

    def flush() -> None:
        if cur_title is None:
            return
        body = "\n".join(buf).strip()
        if body:
            chapters.append((cur_title.strip(), body, cur_vol))

    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) <= 60 and VOLUME_RE.match(stripped) and not _is_chapter_heading(stripped):
            flush()
            cur_title, buf = None, []
            vol_idx += 1
            volumes[vol_idx] = stripped
            cur_vol = vol_idx
            found = True
            heading_at = i
            continue
        if _is_chapter_heading(stripped):
            flush()
            found = True
            cur_title = stripped
            buf = []
            heading_at = i
            continue
        buf.append(line)
    flush()
    if not found:
        return [], {}, False
    # 第一章之前的零散文字作为「前言」保留
    return chapters, volumes, True



class Importer:
    def __init__(self, lib: Library):
        self.lib = lib

    # ---------------- TXT ----------------
    def import_txt(self, path: Path, title: str = "", author: str = "") -> int:
        text = decode_bytes(path.read_bytes())
        title = title or path.stem
        author = author or _guess_author(text)
        chs, volumes, structured = split_chapters(text)
        if not structured:
            chs = [("全文", text.strip(), 0)]
        bid = self.lib.add_local_book(title, author, source_id=f"local-txt-{path.name}")
        if volumes:
            self.lib.replace_volumes(
                bid, [{"idx": k, "title": v} for k, v in sorted(volumes.items())]
            )
        self._write_chapters(bid, chs)
        return bid

    # ---------------- Markdown ----------------
    def import_md(self, path: Path, title: str = "", author: str = "") -> int:
        text = path.read_text(encoding="utf-8", errors="replace")
        title = title or path.stem
        parts = re.split(r"^(#{1,3})\s+(.+)$", text, flags=re.M)
        chs: list[tuple[str, str, int]] = []
        # parts: [前言, level, heading, body, level, heading, body, ...]
        if len(parts) >= 4:
            for i in range(1, len(parts), 3):
                level, head, body = parts[i], parts[i + 1], parts[i + 2]
                body = body.strip()
                if level == "#":
                    continue
                chs.append((head.strip(), body, 1 if level == "##" else 0))
        if not chs:
            chs = [("全文", text.strip(), 0)]
        bid = self.lib.add_local_book(title, author, source_id=f"local-md-{path.name}")
        self._write_chapters(bid, chs)
        return bid

    # ---------------- EPUB ----------------
    def import_epub(self, path: Path, title: str = "", author: str = "") -> int:
        from ebooklib import epub, ITEM_DOCUMENT

        eb = epub.read_epub(str(path))
        title = title or (eb.get_metadata("DC", "title") or [[path.stem]])[0][0]
        if not author:
            authors = eb.get_metadata("DC", "creator")
            author = authors[0][0] if authors else ""

        title_map = {}
        for it in eb.toc:
            if isinstance(it, (list, tuple)) and len(it) == 2:
                sec, links = it
                for lk in links or []:
                    if hasattr(lk, "href"):
                        title_map[lk.href.split("#")[0]] = lk.title
            elif hasattr(it, "href"):
                title_map[it.href.split("#")[0]] = getattr(it, "title", "")

        chs: list[tuple[str, str, int]] = []
        for item in eb.get_items_of_type(ITEM_DOCUMENT):
            name = item.get_name()
            if name.endswith(("nav.xhtml", "toc.xhtml")):
                continue
            soup = BeautifulSoup(item.get_content(), "lxml")
            for bad in soup.find_all(["script", "style"]):
                bad.decompose()
            text = soup.get_text("\n", strip=True)
            if not text.strip():
                continue
            head = soup.find(["h1", "h2", "h3"])
            ch_title = (
                title_map.get(name) or (head.get_text(strip=True) if head else "") or name
            )
            chs.append((ch_title, text, 0))
        if not chs:
            raise RuntimeError(f"EPUB 中没有可导入的正文：{path}")
        bid = self.lib.add_local_book(title, author, source_id=f"local-epub-{path.name}")
        self._write_chapters(bid, chs)
        # 封面
        try:
            for item in eb.get_items():
                if "cover" in (item.get_name() or "").lower() and item.get_content():
                    ext = Path(item.get_name()).suffix or ".jpg"
                    p = Path(self.lib.path).parent / "covers" / f"{bid}{ext}"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(item.get_content())
                    self.lib.set_cover_path(bid, str(p))
                    break
        except Exception:
            pass
        return bid

    # ---------------- 内部 ----------------
    def _write_chapters(self, bid: int, chs: Iterable[tuple[str, str, int]]) -> None:
        from .textutil import escape_min

        idx = 0
        for t, body, vol in chs:
            if not body.strip():
                continue
            frag = "\n".join(
                f"<p>{escape_min(ln.strip())}</p>"
                for ln in body.split("\n")
                if ln.strip()
            )
            self.lib.add_chapter(bid, idx, t, body.strip(), frag, vol_idx=vol)
            idx += 1

    # ---------------- 统一入口 ----------------
    def import_path(self, path: str | Path, title: str = "", author: str = "") -> list[int]:
        p = Path(path)
        if not p.exists():
            raise RuntimeError(f"路径不存在：{p}")
        if p.is_dir():
            ids = []
            for f in sorted(p.iterdir()):
                if f.suffix.lower() in (".txt", ".epub", ".md"):
                    try:
                        ids += self.import_path(f)
                    except Exception:
                        continue
            return ids
        suffix = p.suffix.lower()
        if suffix == ".txt":
            return [self.import_txt(p, title, author)]
        if suffix == ".epub":
            return [self.import_epub(p, title, author)]
        if suffix in (".md", ".markdown"):
            return [self.import_md(p, title, author)]
        raise RuntimeError(f"不支持的格式：{suffix}（支持 txt/epub/md）")
