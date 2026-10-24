"""导出：TXT / EPUB / Markdown / 单文件 HTML。"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import Config
from .net import Fetcher
from .store import Library

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

READER_CSS = """
body { font-family: "Songti SC", "Source Han Serif SC", serif; line-height: 1.8;
       margin: 0 auto; max-width: 42em; padding: 1.2em; text-align: justify; }
h1 { font-size: 1.35em; margin: 1.6em 0 1em; text-align: center; }
p { text-indent: 2em; margin: 0 0 0.7em; }
p.img { text-indent: 0; text-align: center; }
img { max-width: 100%; height: auto; }
.vol { text-align: center; font-size: 1.6em; margin: 3em 0 1.5em; font-weight: bold; }
"""


def safe_filename(name: str, default: str = "book") -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or default


def _img_mime(src: str, data: bytes) -> tuple[str, str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data[:4] == b"GIF8":
        return ".gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    ext = Path(urlparse(src).path).suffix.lower()
    if ext in _MIME:
        return ext, _MIME[ext]
    return ".jpg", "image/jpeg"


class Exporter:
    def __init__(self, cfg: Config, lib: Library):
        self.cfg = cfg
        self.lib = lib

    # ---------------- 组装 ----------------
    def _collect(self, book_id: int, volumes: Optional[list[int]] = None):
        book = self.lib.get_book(book_id)
        if book is None:
            raise RuntimeError(f"书库中没有 id={book_id}")
        vols = {v["idx"]: v["title"] for v in self.lib.list_volumes(book_id)}
        chapters = self.lib.list_chapters(book_id)
        if volumes:
            keep = set(volumes)
            chapters = [c for c in chapters if c["vol_idx"] in keep]
        groups: list[tuple[int, str, list]] = []
        by_vol: dict[int, list] = {}
        for ch in chapters:
            by_vol.setdefault(ch["vol_idx"], []).append(ch)
        for vi in sorted(by_vol):
            groups.append((vi, vols.get(vi) or (f"第{vi + 1}卷" if vi else "正文"), by_vol[vi]))
        return book, groups

    def _full_chapter(self, book_id: int, idx: int):
        return self.lib.get_chapter(book_id, idx)

    # ---------------- TXT ----------------
    def to_txt(self, book_id: int, out: Path, volumes=None) -> Path:
        book, groups = self._collect(book_id, volumes)
        lines: list[str] = [book["title"], f"作者：{book['author'] or '未知'}", ""]
        multi_vol = len(groups) > 1
        for vi, vtitle, chs in groups:
            if multi_vol:
                lines += ["", f"=== {vtitle} ===", ""]
            for ch in chs:
                full = self._full_chapter(book_id, ch["idx"])
                lines += ["", ch["title"], ""]
                lines.append((full["text"] if full and full["text"] else "（本章未抓取）"))
        out.write_text("\n".join(lines).replace("\n\n\n", "\n\n"), encoding="utf-8")
        return out

    # ---------------- Markdown ----------------
    def to_md(self, book_id: int, out: Path, volumes=None) -> Path:
        book, groups = self._collect(book_id, volumes)
        buf: list[str] = [f"# {book['title']}", "", f"> 作者：{book['author'] or '未知'}", ""]
        for vi, vtitle, chs in groups:
            buf += [f"## {vtitle}", ""]
            for ch in chs:
                full = self._full_chapter(book_id, ch["idx"])
                buf += [f"### {ch['title']}", ""]
                text = full["text"] if full and full["text"] else "（本章未抓取）"
                for line in text.split("\n"):
                    if not line.strip():
                        continue
                    if "［" in line and "］" in line and len(line) < 20:
                        buf += [f"*{line}*", ""]
                    else:
                        buf += [line, ""]
        out.write_text("\n".join(buf), encoding="utf-8")
        return out

    # ---------------- 单文件 HTML ----------------
    def to_html(self, book_id: int, out: Path, volumes=None) -> Path:
        book, groups = self._collect(book_id, volumes)
        parts = [
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>{html_lib.escape(book['title'])}</title><style>{READER_CSS}"
            "body{background:#faf7f2;color:#222}</style></head><body>",
            f"<h1>{html_lib.escape(book['title'])}</h1>",
            f"<p class='img'>{html_lib.escape(book['author'] or '')}</p>",
        ]
        for vi, vtitle, chs in groups:
            if len(groups) > 1:
                parts.append(f"<div class='vol'>{html_lib.escape(vtitle)}</div>")
            for ch in chs:
                full = self._full_chapter(book_id, ch["idx"])
                parts.append(f"<h1>{html_lib.escape(ch['title'])}</h1>")
                body = full["html"] if full and full["html"] else f"<p>（本章未抓取）</p>"
                body = re.sub(r"<img[^>]*src=\"([^\"]+)\"[^>]*>", self._img_tag, body)
                body = self._media_to_data_uri(body, book_id)
                parts.append(body)
        parts.append("</body></html>")
        out.write_text("\n".join(parts), encoding="utf-8")
        return out

    def _media_to_data_uri(self, fragment: str, book_id: int) -> str:
        """单文件 HTML 导出时，把已落地的插图内联成 data URI，保证拷走也能看。"""
        import base64

        def repl(m: re.Match) -> str:
            src = m.group(1)
            if not src.startswith("/media/"):
                return m.group(0)
            name = src.rsplit("/", 1)[-1]
            path = self.cfg.data_path / "images" / str(book_id) / name
            if not path.is_file():
                return m.group(0)
            mime = _MIME.get(path.suffix.lower(), "image/jpeg")
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return f'<img src="data:{mime};base64,{b64}" alt="插图">'

        return re.sub(r'<img[^>]*?src="(/media/[^"]+)"[^>]*>', repl, fragment)

    def _img_tag(self, m: re.Match) -> str:
        return f'<img src="{m.group(1)}" alt="插图">'

    # ---------------- EPUB ----------------
    def to_epub(
        self,
        book_id: int,
        out: Path,
        volumes=None,
        download_images: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Path:
        from ebooklib import epub

        book, groups = self._collect(book_id, volumes)
        eb = epub.EpubBook()
        eb.set_identifier(f"novelhub-{book['source']}-{book['source_id']}")
        eb.set_title(book["title"])
        eb.set_language("zh-CN")
        if book["author"]:
            eb.add_author(book["author"])
        if book["description"]:
            eb.add_metadata("DC", "description", book["description"])

        if book["cover_path"] and Path(book["cover_path"]).exists():
            data = Path(book["cover_path"]).read_bytes()
            ext, mime = _img_mime(book["cover_path"], data)
            eb.set_cover(f"cover{ext}", data)

        css = epub.EpubItem(
            uid="style",
            file_name="style/main.css",
            media_type="text/css",
            content=READER_CSS,
        )
        eb.add_item(css)

        total = sum(len(g[2]) for g in groups)
        n = 0
        img_cache: dict[str, Optional[tuple[str, str, str]]] = {}
        fetcher = Fetcher(self.cfg) if download_images else None
        spine: list = ["nav"]
        toc: list = []

        try:
            for vi, vtitle, chs in groups:
                links = []
                for ch in chs:
                    n += 1
                    if on_progress:
                        on_progress(n, total, ch["title"])
                    full = self._full_chapter(book_id, ch["idx"])
                    body = full["html"] if full and full["html"] else "<p>（本章未抓取）</p>"
                    body = self._process_epub_body(
                        body, book["url"], fetcher, img_cache, eb, book_id
                    )
                    item = epub.EpubHtml(
                        title=ch["title"], file_name=f"text/c{ch['idx']:04d}.xhtml", lang="zh-CN"
                    )
                    item.content = (
                        "<html><head><title>"
                        + html_lib.escape(ch["title"])
                        + "</title></head><body><h1>"
                        + html_lib.escape(ch["title"])
                        + "</h1>"
                        + body
                        + "</body></html>"
                    )
                    item.add_item(css)
                    eb.add_item(item)
                    spine.append(item)
                    links.append(item)
                if len(groups) > 1:
                    sec = epub.Section(vtitle or f"卷{vi}", href=f"text/v{vi}.xhtml")
                    eb.add_item(
                        epub.EpubHtml(
                            title=vtitle or f"卷{vi + 1}",
                            file_name=f"text/v{vi}.xhtml",
                            lang="zh-CN",
                            content=(
                                "<html><head><title>"
                                + html_lib.escape(vtitle or "")
                                + "</title></head><body><h1>"
                                + html_lib.escape(vtitle or "")
                                + "</h1></body></html>"
                            ),
                        )
                    )
                    toc.append((sec, links))
                else:
                    toc.extend(links)
        finally:
            if fetcher:
                fetcher.close()

        eb.toc = toc
        eb.spine = spine
        eb.add_item(epub.EpubNcx())
        eb.add_item(epub.EpubNav())
        epub.write_epub(str(out), eb, {})
        return out

    def _process_epub_body(
        self, body: str, base_url: str, fetcher: Optional[Fetcher], cache: dict, eb, book_id: int
    ) -> str:
        from ebooklib import epub

        soup = BeautifulSoup(body, "lxml")
        for i, img in enumerate(soup.find_all("img")):
            src = img.get("src", "")
            if not src or (fetcher is None and not src.startswith("/media/")):
                img.replace_with(BeautifulSoup("<p class='img'>［插图］</p>", "lxml").p)
                continue
            if src not in cache:
                try:
                    if src.startswith("/media/"):
                        # 已落地的插图：直接从 data/images 打包
                        local = self.cfg.data_path / "images" / str(book_id) / src.rsplit("/", 1)[-1]
                        data = local.read_bytes()
                        ext, mime = _img_mime(src, data)
                    else:
                        data = fetcher.get_bytes(src, referer=base_url)
                        ext, mime = _img_mime(src, data)
                    name = f"images/{abs(hash(src)) % (10 ** 10)}{ext}"
                    eb.add_item(
                        epub.EpubItem(
                            uid=f"img{abs(hash(src)) % (10 ** 10)}",
                            file_name=name,
                            media_type=mime,
                            content=data,
                        )
                    )
                    cache[src] = (name, mime, src)
                except Exception:
                    cache[src] = None
            got = cache[src]
            if got is None:
                img.replace_with(BeautifulSoup("<p class='img'>［插图］</p>", "lxml").p)
            else:
                img["src"] = got[0]
                img.attrs.pop("alt", None)
                img["alt"] = "插图"
        return str(soup.body or soup)

    # ---------------- 统一入口 ----------------
    def export(
        self,
        book_id: int,
        fmt: str = "epub",
        out_dir: Optional[Path] = None,
        volumes=None,
        download_images: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Path:
        book = self.lib.get_book(book_id)
        if book is None:
            raise RuntimeError(f"书库中没有 id={book_id}")
        out_dir = Path(out_dir) if out_dir else self.cfg.export_path
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(book["title"], f"book-{book_id}")
        if volumes:
            stem += "-" + "-".join(str(v + 1) for v in sorted(volumes)) + "卷"
        fmt = fmt.lower()
        if fmt == "txt":
            return self.to_txt(book_id, out_dir / f"{stem}.txt", volumes)
        if fmt == "md":
            return self.to_md(book_id, out_dir / f"{stem}.md", volumes)
        if fmt == "html":
            return self.to_html(book_id, out_dir / f"{stem}.html", volumes)
        if fmt == "epub":
            return self.to_epub(
                book_id,
                out_dir / f"{stem}.epub",
                volumes,
                download_images,
                on_progress,
            )
        raise RuntimeError(f"不支持的格式：{fmt}（可选 txt/epub/md/html）")
