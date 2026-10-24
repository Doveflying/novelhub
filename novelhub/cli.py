"""命令行入口：python nh.py <command>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .crawler import Crawler
from .export import Exporter
from .importer import Importer
from .net import Fetcher
from .sources import build_registry
from .store import Library


def _setup_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _make(cfg=None):
    cfg = cfg or load_config()
    lib = Library(cfg.db_path)
    reg = build_registry(cfg)
    return cfg, lib, reg


def _parse_vols(s: str | None) -> list[int] | None:
    if not s:
        return None
    out: list[int] = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return sorted(set(out))


# ---------------- 子命令 ----------------
def cmd_inspect(args) -> int:
    cfg, lib, reg = _make()
    crawler = Crawler(cfg, lib, reg)
    cat = crawler.inspect(args.url)
    m = cat.meta
    print(f"书名：{m.title}")
    print(f"作者：{m.author or '未知'}")
    print(f"站点：{m.source}  源 ID：{m.source_id}")
    print(f"分类：{m.category or '-'}  状态：{m.status or '-'}")
    print(f"封面：{m.cover or '-'}")
    print(f"分卷：{len(cat.volumes)}  章节：{len(cat.chapters)}")
    for v in cat.volumes[:8]:
        n = sum(1 for c in cat.chapters if c.vol_idx == v.idx)
        print(f"  [{v.idx + 1}] {v.title}（{n} 章）")
    if len(cat.volumes) > 8:
        print(f"  ... 省略 {len(cat.volumes) - 8} 卷")
    return 0


def cmd_add(args) -> int:
    cfg, lib, reg = _make()
    crawler = Crawler(cfg, lib, reg)
    vols = _parse_vols(args.vols)
    catalog_only = getattr(args, "catalog_only", False)
    print(f"开始抓取：{args.url}")
    if catalog_only:
        print("模式：只建目录（正文不落库，阅读时在线取）")
    print(f"请求间隔 {cfg.delay}s + 抖动 {cfg.jitter}s，代理：{cfg.proxy or '直连'}")

    def progress(i: int, n: int, title: str) -> None:
        print(f"\r[{i}/{n}] {title[:40]:<40}", end="", flush=True)

    res = crawler.crawl(
        args.url,
        limit=args.limit,
        volumes=vols,
        force=args.force,
        catalog_only=catalog_only,
        on_progress=progress,
    )
    print()
    if res.mode == "catalog-only":
        print(f"目录已建：《{res.title}》{res.total} 章，正文不落库（book_id={res.book_id}）")
        return 0
    print(
        f"完成：《{res.title}》新增 {res.fetched} 章，跳过 {res.skipped} 章，"
        f"失败 {len(res.failed)} 章（book_id={res.book_id}）"
    )
    for title, err in res.failed[:10]:
        print(f"  ✗ {title}：{err}")
    return 0 if not res.failed else 2


def cmd_resume(args) -> int:
    cfg, lib, reg = _make()
    crawler = Crawler(cfg, lib, reg)

    def progress(i: int, n: int, title: str) -> None:
        print(f"\r[{i}/{n}] {title[:40]:<40}", end="", flush=True)

    res = crawler.resume(args.book_id, limit=args.limit, on_progress=progress)
    print()
    print(f"续抓完成：新增 {res.fetched} 章，失败 {len(res.failed)} 章")
    return 0


def cmd_list(args) -> int:
    cfg, lib, _ = _make()
    rows = lib.list_books()
    if not rows:
        print("书库是空的。用 `python nh.py add <URL>` 或 `import <文件>` 添加。")
        return 0
    print(f"{'ID':>4}  {'书名':<34} {'作者':<12} {'已抓/总':>10} {'字数':>9}  来源")
    for b in rows:
        title = (b["title"] or "")[:32]
        author = (b["author"] or "-")[:10]
        print(
            f"{b['id']:>4}  {title:<34} {author:<12} "
            f"{b['n_done']:>4}/{b['n_chapters']:<5} {b['n_words']:>9}  {b['source']}"
        )
    return 0


def cmd_search(args) -> int:
    cfg, lib, _ = _make()
    hits = lib.search(args.query, limit=args.limit)
    if not hits:
        print("没有匹配结果。")
        return 0
    for h in hits:
        print(f"《{h['book_title']}》 {h['title']}  [book {h['book_id']} / ch {h['idx']}]")
        print(f"    {h['snippet']}")
    return 0


def cmd_index(args) -> int:
    """扫榜单页（或按 sitemap 逐本）建/续扫轻小说文库书目索引。"""
    from .sources.linovelib import (
        INDEX_NAME,
        INDEX_SOURCE,
        build_index,
        build_index_deep,
        refresh_index,
    )

    cfg, lib, _ = _make()
    print(f"{INDEX_NAME} 索引：现有 {lib.novel_index_count(INDEX_SOURCE)} 本")

    def progress(p: dict) -> None:
        print(
            f"\r扫 [{p['pages']}/{p['budget']}] {p['list']:<26} 累计 {p['total']} 本",
            end="",
            flush=True,
        )

    if args.refresh:
        res = refresh_index(cfg, lib, pages=args.pages, on_progress=progress)
        print(f"\n增量刷新：{res['requests']} 个请求，扫到 {res['seen']} 条，"
              f"写入 {res['added']} 行，索引共 {res['total']} 本")
        for r in res["new"][:10]:
            print("   新:", r["title"][:34], "|", r["author"], "|", r["url"])
        return 0

    if args.deep:
        res = build_index_deep(cfg, lib, budget=args.pages, on_progress=progress)
        tail = "（跑完了）" if res["done"] else (
            f"（进度 {res.get('pos', 0)} / {res.get('ids', 0)}，再跑一次继续）"
        )
        print(
            f"\n深度模式：抓 {res['pages']} 本，新增/更新 {res['added']}，"
            f"索引共 {res['total']} 本 / 全站 {res.get('ids', 0)} 本{tail}"
        )
        return 0

    res = build_index(cfg, lib, budget=args.pages, on_progress=progress)
    print(f"\n本次扫 {res['pages']} 页，新增/更新 {res['added']} 条，索引共 {res['total']} 本"
          f"{'（已扫完全部榜单）' if res['done'] else '（还能续扫，再跑一次继续）'}")
    return 0


def cmd_novel_search(args) -> int:
    """离线验证：本地书目索引 + 本地书架关键词查询。"""
    from .sources.linovelib import INDEX_SOURCE

    cfg, lib, _ = _make()
    rows = lib.search_novel_index(args.query, source=INDEX_SOURCE, limit=args.limit)
    print(f"轻小说文库索引命中 {len(rows)} 条：")
    for r in rows:
        print(f"  {r['title']}  {r['author'] or '-'}  [{r['category'] or '-'}]  {r['url']}")
    books = lib.list_books()
    q = args.query.lower()
    hits = [b for b in books if q in (b["title"] or "").lower() or q in (b["author"] or "").lower()]
    print(f"本地书架命中 {len(hits)} 本：")
    for b in hits:
        print(f"  [{b['id']}] {b['title']}  {b['n_done']}/{b['n_chapters']}")
    return 0


def cmd_export(args) -> int:
    cfg, lib, _ = _make()
    exp = Exporter(cfg, lib)
    vols = _parse_vols(args.vols)

    def progress(i: int, n: int, title: str) -> None:
        print(f"\r导出 [{i}/{n}] {title[:36]:<36}", end="", flush=True)

    path = exp.export(
        args.book_id,
        fmt=args.fmt,
        out_dir=Path(args.out) if args.out else None,
        volumes=vols,
        download_images=not args.no_images,
        on_progress=progress,
    )
    print(f"\n已导出：{path}")
    return 0


def cmd_import(args) -> int:
    cfg, lib, _ = _make()
    imp = Importer(lib)
    ids = imp.import_path(args.path, title=args.title, author=args.author)
    for bid in ids:
        b = lib.get_book(bid)
        n, d = lib.chapter_count(bid)
        print(f"导入完成：《{b['title']}》 {d}/{n} 章（book_id={bid}）")
    if not ids:
        print("没有导入任何文件。")
    return 0


def cmd_delete(args) -> int:
    cfg, lib, _ = _make()
    lib.delete_book(args.book_id)
    print(f"已删除 book_id={args.book_id}")
    return 0


def cmd_discover(args) -> int:
    from .discover import discover as do_discover

    cfg, lib, reg = _make()
    books = do_discover(cfg, reg, args.url, limit=args.top)
    if not books:
        print("没在这个页面上找到书链接。换一个排行榜/分类页试试。")
        return 1
    print(f"发现 {len(books)} 本：\n")
    for i, b in enumerate(books, 1):
        print(f"{i:>3}. [{b.source}:{b.source_id}] {b.title or '(待解析)'}  {b.url}")
    return 0


def cmd_batch(args) -> int:
    cfg, lib, reg = _make()
    crawler = Crawler(cfg, lib, reg)
    print(f"列表页：{args.url}")
    print(f"只建目录：{'是' if not args.full else '否（会抓正文，很慢）'}  上限 {args.top} 本")

    def progress(i: int, n: int, title: str) -> None:
        print(f"\r[{i}/{n}] {title[:38]:<38}", end="", flush=True)

    res = crawler.batch(
        args.url,
        top=args.top,
        pages=getattr(args, "pages", 1),
        catalog_only=not args.full,
        on_progress=progress,
    )
    print("\n")
    for it in res["items"]:
        if it.get("error"):
            print(f"  ✗ {it.get('title') or it['url']}：{it['error']}")
        else:
            print(f"  ✓ [{it['book_id']}] {it['title']} — {it['total']} 章")
    print(
        f"\n完成：发现 {res['found']} 本，索引成功 {res['indexed']} 本，"
        f"失败 {res['failed']} 本，共 {res['chapters']} 章目录"
    )
    print("正文没抓。要在线阅读直接打开书架；要整本下载用 `python nh.py resume <book_id>`。")
    return 0 if res["indexed"] else 1


def cmd_js(args) -> int:
    from .sources.venera_js import JsSource, VeneraRegistry, download_images, gallery_html

    cfg, lib, reg = _make()
    vreg = VeneraRegistry(cfg)
    action = args.js_cmd

    if action == "list":
        inst = {e.key for e, _ in vreg.installed()}
        print(f"{'key':22} {'名称':16} {'版本':8} 状态")
        for e in vreg.index():
            print(f"{e.key:22} {e.name[:14]:16} {e.version:8} {'已安装' if e.key in inst else ''}")
        return 0

    if action == "install":
        p = vreg.install(args.key)
        print(f"已安装 {args.key} -> {p}")
        return 0

    path = vreg.install(args.key) if args.key else None
    src = JsSource(cfg, path)
    meta = src.meta()
    print(f"源：{meta.get('name')} ({meta.get('key')}) v{meta.get('version')}")

    if action == "meta":
        return 0

    if action == "search":
        comics = src.search(args.query, args.page)
        print(f"搜索「{args.query}」→ {len(comics)} 条")
        for c in comics[: args.limit]:
            tags = ",".join(c.get("tags") or [])[:28]
            print(f"  [{c.get('id')}] {c.get('title')}  {c.get('subTitle') or ''}  {tags}")
        return 0

    if action == "info":
        info = src.info(args.id)
        print(f"标题：{info.get('title')}")
        print(f"作者：{info.get('subTitle') or ''}")
        print(f"标签：{','.join(info.get('tags') or [])}")
        print(f"简介：{(info.get('description') or '')[:120]}")
        groups = info.get("chapters") or {}
        flat: list[tuple[str, str, str]] = []
        if isinstance(groups, dict):
            for gname, eps in groups.items():
                if isinstance(eps, dict):
                    for eid, title in eps.items():
                        flat.append((str(gname), str(eid), str(title)))
                else:
                    flat.append((str(gname), "", str(eps)))
        elif isinstance(groups, list):
            for it in groups:
                flat.append((it.get("group", ""), str(it.get("id", "")), it.get("title", "")))
        print(f"章节：{len(flat)} 话")
        for g, eid, title in flat[: args.limit]:
            prefix = f"[{g}] " if g and g not in ("连载", "单行本") else ""
            print(f"  {eid}  {prefix}{title}")
        return 0

    if action == "ep":
        urls = src.episode(args.comic_id, args.ep_id)
        print(f"本话图片 {len(urls)} 张")
        out = Path(args.out) if args.out else (cfg.export_path / "comic" / f"{args.comic_id}-{args.ep_id}")
        referer = args.referer
        if not referer and urls:
            from urllib.parse import urlparse

            p = urlparse(urls[0])
            referer = f"{p.scheme}://{p.netloc}/"
        saved = download_images(cfg, urls, out, referer=referer)
        g = out / "index.html"
        g.write_text(gallery_html(f"{args.comic_id}-{args.ep_id}", saved), encoding="utf-8")
        print(f"已保存 {len(saved)}/{len(urls)} 张 -> {out}")
        print(f"预览：{g}")
        return 0

    print("用法：nh js list|install|meta|search|info|ep")
    return 1


def cmd_sources(_args) -> int:
    cfg, lib, reg = _make()
    for s in reg.all:
        print(f"- {s.id:<12} {s.name}")
    print(f"\n代理：{cfg.proxy or '直连'}   书库：{cfg.db_path}")
    return 0


def cmd_serve(args) -> int:
    from .webapp.app import create_app

    cfg = load_config()
    app = create_app(cfg)
    print(f"阅读器：http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


def cmd_proxy(_args) -> int:
    cfg, lib, reg = _make()
    print(f"当前代理：{cfg.proxy or '直连'}")
    with Fetcher(cfg) as f:
        try:
            html = f.get("https://www.bilinovel.com/", use_cache=False)
            print(f"连通性：OK（{len(html)} 字节）")
        except Exception as exc:
            print(f"连通性：失败 —— {exc}")
            return 1
    return 0


# ---------------- main ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nh", description="本地轻小说书库：抓取 / 导入 / 导出 / 阅读")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="只看目录，不下载")
    s.add_argument("url")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("add", help="抓取一本书入库")
    s.add_argument("url")
    s.add_argument("--limit", type=int, default=None, help="只抓前 N 章（试跑用）")
    s.add_argument("--vols", default=None, help="只抓指定卷，如 1,3-5")
    s.add_argument("--force", action="store_true", help="重抓已有章节")
    s.add_argument(
        "--catalog-only", dest="catalog_only", action="store_true",
        help="只建目录，正文不落库（阅读时在线取）",
    )
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("resume", help="补抓未完成章节")
    s.add_argument("book_id", type=int)
    s.add_argument("--limit", type=int, default=None)
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("list", help="列出书库")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("search", help="本地全文搜索")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("index", help="扫轻小说文库榜单页，建/续扫本地书目索引")
    s.add_argument("--pages", type=int, default=60, help="本次最多扫多少页（可续扫）")
    s.add_argument(
        "--refresh", action="store_true",
        help="增量刷新：只扫 postdate/lastupdate 头部几页，捡新书（6 个请求左右）",
    )
    s.add_argument(
        "--deep", action="store_true",
        help="深度模式：按 sitemap 逐本抓书籍页元信息，补齐榜单没覆盖的书",
    )
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("find", help="查书目索引 + 本地书架（离线，不联网）")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(func=cmd_novel_search)

    s = sub.add_parser("export", help="导出 txt/epub/md/html")
    s.add_argument("book_id", type=int)
    s.add_argument("--fmt", default="epub", choices=["epub", "txt", "md", "html"])
    s.add_argument("--out", default=None)
    s.add_argument("--vols", default=None)
    s.add_argument("--no-images", action="store_true", help="EPUB 不下载插图")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="导入本地 txt/epub/md")
    s.add_argument("path")
    s.add_argument("--title", default="")
    s.add_argument("--author", default="")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("delete", help="从书库删除")
    s.add_argument("book_id", type=int)
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("discover", help="从排行榜/分类页解析书单")
    s.add_argument("url")
    s.add_argument("--top", type=int, default=30, help="最多列多少本")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("batch", help="批量把列表页里的书建成目录（默认不抓正文）")
    s.add_argument("url")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--pages", type=int, default=1, help="列表页翻几页（如 allvisit/1.html → 2、3…）")
    s.add_argument("--full", action="store_true", help="连正文一起抓（很慢）")
    s.set_defaults(func=cmd_batch)

    s = sub.add_parser("js", help="运行 Venera 格式的 JS 源（漫画）")
    js_sub = s.add_subparsers(dest="js_cmd", required=False)
    p_list = js_sub.add_parser("list", help="列出源清单与安装状态")
    p_list.set_defaults(func=cmd_js, key="", js_cmd="list")
    p_inst = js_sub.add_parser("install", help="下载并安装一个源")
    p_inst.add_argument("key")
    p_inst.set_defaults(func=cmd_js, js_cmd="install")
    p_meta = js_sub.add_parser("meta", help="查看源信息")
    p_meta.add_argument("key")
    p_meta.set_defaults(func=cmd_js, js_cmd="meta")
    p_sch = js_sub.add_parser("search", help="用源搜索漫画")
    p_sch.add_argument("key")
    p_sch.add_argument("query")
    p_sch.add_argument("--page", type=int, default=1)
    p_sch.add_argument("--limit", type=int, default=12)
    p_sch.set_defaults(func=cmd_js, js_cmd="search")
    p_info = js_sub.add_parser("info", help="漫画详情与章节")
    p_info.add_argument("key")
    p_info.add_argument("id")
    p_info.add_argument("--limit", type=int, default=15)
    p_info.set_defaults(func=cmd_js, js_cmd="info")
    p_ep = js_sub.add_parser("ep", help="下载一话图片并生成预览页")
    p_ep.add_argument("key")
    p_ep.add_argument("comic_id")
    p_ep.add_argument("ep_id")
    p_ep.add_argument("--out", default=None)
    p_ep.add_argument("--referer", default="", help="下载图片时的 Referer，默认取图床站点")
    p_ep.set_defaults(func=cmd_js, js_cmd="ep")
    s.set_defaults(func=cmd_js, key="", js_cmd="list")

    s = sub.add_parser("sources", help="列出适配器与配置")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("proxy", help="测试代理连通性")
    s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("serve", help="启动网页阅读器")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8899)
    s.add_argument("--debug", action="store_true")
    s.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断（已抓章节已保存，可 resume 继续）")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
