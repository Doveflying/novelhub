"""本地网页阅读器（Flask）。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ..config import Config, load_config
from ..crawler import Crawler
from ..export import Exporter
from ..importer import Importer
from ..sources import build_registry
from ..store import Library


_EPNO_RE = __import__("re").compile(r"第\s*(\d{1,4})\s*[话話集]|(?<!\d)(\d{1,4})(?!\d)")


def _pick_best_episode(eps: list[dict], ep_no: int, title_hint: str = "") -> int:
    """在一部番的分集列表里定位当前集，返回下标。

    光用 ep_no-1 当下标很脆：弹弹play 的分集里会混进总集篇、OVA、SP，
    或者干脆从第 0 集开始，一错就整集弹幕全串。
    所以先看分集标题里的集号，再看集名相似度，最后才退回下标。
    """
    if not eps:
        return 0

    # 1) 分集标题里抠得出集号的，优先按集号精确匹配
    for i, e in enumerate(eps):
        t = str(e.get("episodeTitle") or "")
        m = _EPNO_RE.search(t)
        if m:
            got = m.group(1) or m.group(2)
            try:
                if got and int(got) == ep_no:
                    return i
            except (TypeError, ValueError):
                pass

    # 2) 有集名提示就按编辑距离找最像的
    hint = (title_hint or "").strip()
    if hint:
        best_i, best_d = 0, None
        for i, e in enumerate(eps):
            t = str(e.get("episodeTitle") or "")
            d = _levenshtein(t, hint)
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_d is not None:
            return best_i

    # 3) 兜底：按下标，越界就夹住
    return min(max(ep_no - 1, 0), len(eps) - 1)


def _levenshtein(a: str, b: str) -> int:
    """编辑距离。番名差一两个字（第几季、剧场版）时靠它排序最稳。"""
    a = a or ""
    b = b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1,          # 删
                           cur[j - 1] + 1,       # 插
                           prev[j - 1] + (ca != cb)))  # 换
        prev = cur
    return prev[-1]


def _pick_best_anime(cands: list[dict], keyword: str) -> dict:
    """从弹弹play 搜索结果里挑最匹配的一部。

    弹弹play 会把同一部番的各季拆成独立 anime，光看「包含」会把
    「进击的巨人」匹配到「进击的巨人 最终季」上，弹幕就串季了。
    这里改成按编辑距离排序（思路来自 animeko 的 DanmakuMatchers.mostRelevant）：
    番名距离为主，长度差为辅，取最小者。
    """
    kw = (keyword or "").strip()
    if not cands:
        return {}
    if not kw:
        return cands[0]

    # 完全相等直接返回，不用算距离
    for a in cands:
        if (a.get("animeTitle") or "").strip() == kw:
            return a

    def score(a: dict) -> tuple:
        t = (a.get("animeTitle") or "").strip()
        d = _levenshtein(t, kw)
        # 长度差当作次级判据：距离相同的时候，更接近原名的优先
        return (d, abs(len(t) - len(kw)))

    return min(cands, key=score)


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load_config()
    lib = Library(cfg.db_path)
    registry = build_registry(cfg)
    crawler = Crawler(cfg, lib, registry)
    exporter = Exporter(cfg, lib)
    importer = Importer(lib)

    # 模板 / 静态资源在冻结 exe 里位于 _MEIPASS，不能靠 Flask 默认的
    # 相对 __name__ 推导（onefile 解包后 __file__ 不在源码树里）。
    from .. import paths as _paths

    _tpl = _paths.resource("webapp", "templates")
    _static = _paths.resource("webapp", "static")
    app = Flask(
        __name__,
        template_folder=str(_tpl),
        static_folder=str(_static),
    )
    app.secret_key = "novelhub-local-only"
    app.config["JSON_AS_ASCII"] = False
    app.jinja_env.globals.update(mode="reader")
    app.jinja_env.auto_reload = True   # 改模板后刷新即可见效，不用重启

    # 繁体源（哔哩轻小说等）→ 简体显示。config.convert_to_simplified 关掉就原样输出。
    from ..zhconvert import to_simplified as _to_simplified

    @app.template_filter("zh")
    def _zh_filter(value):
        if not getattr(cfg, "convert_to_simplified", True):
            return value
        return _to_simplified(value) if isinstance(value, str) else value
    # 静态资源版本号：按 static/ 下文件的 mtime+size 算哈希。
    # 以前用 int(time.time())，每次重启都变 → 浏览器缓存永远失效；现在只有真的改了文件才变。
    static_dir = Path(app.static_folder or "static")

    def _asset_version() -> str:
        try:
            items = []
            for p in static_dir.rglob("*"):
                if p.suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4"}:
                    continue  # 大图不参与，改名/换图本来就该手动刷
                st = p.stat()
                items.append(f"{p.relative_to(static_dir).as_posix()}:{int(st.st_mtime)}:{st.st_size}")
            items.sort()
            return hashlib.md5("|".join(items).encode("utf-8")).hexdigest()[:8]
        except OSError:
            return str(int(time.time()))

    @app.context_processor
    def _inject_asset_v():
        return {"asset_v": _asset_version()}

    # 带上 ?v=hash 之后就可以放心让浏览器长缓存；文件一改 hash 变，URL 也变。
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 7
    app.extensions["library"] = lib
    app.extensions["config"] = cfg

    tasks: dict[str, dict] = {}
    live_cache: dict[tuple[int, int], tuple[str, str]] = {}
    inflight: set[tuple[int, int]] = set()
    lock = threading.RLock()
    # 书目索引扫描进度（后台线程写，前端轮询读）
    index_state: dict = {        "running": False,
        "pages": 0,
        "budget": 0,
        "total": 0,
        "list": "",
        "error": "",
        "done": None,
        "started": 0.0,
        "mode": "full",
        "last_refresh": 0.0,
    }

    def _jstate():
        with lock:
            st = dict(health_state)
        st["ok"] = True
        return jsonify(st)

    # 源健康检查进度（排行榜页轮询）
    health_state: dict = {
        "running": False,
        "done": 0,
        "total": 0,
        "ok": 0,
        "fail": 0,
        "error": "",
        "started": 0.0,
    }

    def _start_index_refresh(hours: float = 0.0) -> None:
        """后台增量刷新书目索引：只扫新书/最近更新头部几页。"""
        from ..sources.linovelib import refresh_index

        with lock:
            if index_state.get("running"):
                return
            index_state.update(
                running=True,
                mode="refresh",
                pages=0,
                budget=max(1, int(getattr(cfg, "index_refresh_pages", 3) or 3)) * 2,
                total=lib.novel_index_count("linovelib"),
                list="自动增量刷新",
                error="",
                started=time.time(),
            )

        def on_progress(p: dict) -> None:
            with lock:
                index_state.update(
                    pages=p.get("pages", 0),
                    total=p.get("total", 0),
                    list=p.get("list", ""),
                )

        def work() -> None:
            try:
                res = refresh_index(
                    cfg, lib,
                    pages=int(getattr(cfg, "index_refresh_pages", 3) or 3),
                    on_progress=on_progress,
                )
                with lock:
                    index_state.update(
                        running=False,
                        pages=res["requests"],
                        total=res["total"],
                        error="",
                        done=True,
                        last_refresh=time.time(),
                        list=f"增量完成：扫 {res['seen']} 条 / 写入 {res['added']} 行",
                    )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    index_state.update(running=False, error=str(exc)[:200])

        threading.Thread(target=work, daemon=True).start()

    # 定时器：按 config.index_refresh_hours 周期跑增量（0 = 关）
    def _index_scheduler() -> None:
        hours = float(getattr(cfg, "index_refresh_hours", 0) or 0)
        if hours <= 0:
            return
        time.sleep(20)  # 启动后先等一下，别和首页请求抢
        while True:
            _start_index_refresh(hours)
            time.sleep(max(300.0, hours * 3600))

    threading.Thread(target=_index_scheduler, daemon=True).start()

    def _live_chapter(bid: int, idx: int) -> tuple[str, str]:
        """在线读一章：内存 → 磁盘临时缓存 → 现抓。都不写书库正文。"""
        key = (bid, idx)
        with lock:
            if key in live_cache:
                return live_cache[key]
        data = crawler.fetch_chapter_live(bid, idx)
        with lock:
            if len(live_cache) > 60:
                live_cache.clear()
            live_cache[key] = data
        return data

    def _prefetch(bid: int, after_idx: int, count: int) -> None:
        """后台预取后面几章，翻页时就不用干等。"""
        if count <= 0:
            return
        stored = {c["idx"] for c in lib.list_chapters(bid) if (c["words"] or 0) > 0}
        upcoming = [c["idx"] for c in lib.list_chapters(bid) if c["idx"] > after_idx][:count]
        for idx in upcoming:
            if idx in stored:
                continue
            key = (bid, idx)
            with lock:
                if key in live_cache or key in inflight:
                    continue
                inflight.add(key)

            def work(i: int = idx) -> None:
                try:
                    _live_chapter(bid, i)
                except Exception:
                    pass
                finally:
                    with lock:
                        inflight.discard((bid, i))

            threading.Thread(target=work, daemon=True).start()

    # ---------------- 后台抓取任务 ----------------
    def _start_task(
        url: str, limit: int | None, force: bool, volumes: list[int] | None, catalog_only: bool = False
    ) -> str:
        tid = uuid.uuid4().hex[:8]
        with lock:
            tasks[tid] = {
                "id": tid,
                "url": url,
                "status": "running",
                "done": 0,
                "total": 0,
                "current": "准备中…",
                "result": None,
                "error": "",
                "started": time.time(),
            }

        def progress(i: int, n: int, title: str) -> None:
            with lock:
                tasks[tid].update(done=i, total=n, current=title)

        def work() -> None:
            try:
                res = crawler.crawl(
                    url,
                    limit=limit,
                    force=force,
                    volumes=volumes,
                    catalog_only=catalog_only,
                    on_progress=progress,
                )
                with lock:
                    tasks[tid]["status"] = "done"
                    tasks[tid]["result"] = res.as_dict()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    tasks[tid]["status"] = "error"
                    tasks[tid]["error"] = str(exc)

        threading.Thread(target=work, daemon=True).start()
        return tid

    def _start_batch(list_url: str, top: int, catalog_only: bool = True) -> str:
        tid = uuid.uuid4().hex[:8]
        with lock:
            tasks[tid] = {
                "id": tid,
                "url": list_url,
                "kind": "batch",
                "status": "running",
                "done": 0,
                "total": 0,
                "current": "解析列表页…",
                "result": None,
                "error": "",
                "started": time.time(),
            }

        def progress(i: int, n: int, title: str) -> None:
            with lock:
                tasks[tid].update(done=i, total=n, current=title)

        def work() -> None:
            try:
                res = crawler.batch(
                    list_url, top=top, catalog_only=catalog_only, on_progress=progress
                )
                with lock:
                    tasks[tid]["status"] = "done"
                    tasks[tid]["result"] = res
            except Exception as exc:  # noqa: BLE001
                with lock:
                    tasks[tid]["status"] = "error"
                    tasks[tid]["error"] = str(exc)

        threading.Thread(target=work, daemon=True).start()
        return tid

    # ---------------- 模板辅助 ----------------
    @app.context_processor
    def inject() -> dict:
        return {"stats": lib.stats(), "sources": [s.id for s in registry.all]}

    def _book_or_404(bid: int):
        b = lib.get_book(bid)
        if b is None:
            abort(404)
        return b

    # ---------------- 书架 ----------------
    @app.get("/")
    def index():
        books = lib.list_books()
        cont = []
        for b in books:
            pidx = b["progress_idx"]
            if pidx is None or not b["n_chapters"]:
                continue
            ch = lib.get_chapter(b["id"], pidx)
            if ch is None:
                continue
            prog = lib.get_progress(b["id"])
            pct = max(1, round(100 * (pidx + 1) / b["n_chapters"]))
            cont.append(
                {
                    "book": b,
                    "chapter": ch,
                    "pct": pct,
                    "at": (prog["updated_at"] if prog else ""),
                }
            )
        cont.sort(key=lambda x: x["at"] or "", reverse=True)
        done_count = sum(
            1 for b in books if b["n_chapters"] and b["n_done"] >= b["n_chapters"]
        )
        reading_count = sum(
            1
            for b in books
            if b["progress_idx"] is not None
            and not (b["n_chapters"] and b["n_done"] >= b["n_chapters"])
        )
        online_count = sum(
            1 for b in books if b["n_chapters"] and b["n_done"] == 0
        )
        return render_template(
            "index.html",
            books=books,
            continue_items=cont[:3],
            done_count=done_count,
            reading_count=reading_count,
            online_count=online_count,
        )

    # ---------------- 目录 ----------------
    @app.get("/book/<int:bid>")
    def book(bid: int):
        b = _book_or_404(bid)
        chapters = lib.list_chapters(bid)
        volumes = {v["idx"]: v["title"] for v in lib.list_volumes(bid)}
        groups = []
        cur = None
        for ch in chapters:
            if cur is None or ch["vol_idx"] != cur[0]:
                title = volumes.get(ch["vol_idx"])
                if not title:
                    title = f"第{ch['vol_idx'] + 1}卷" if ch["vol_idx"] else "正文"
                cur = (ch["vol_idx"], title, [])
                groups.append(cur)
            cur[2].append(ch)
        prog = lib.get_progress(bid)
        n_all, n_done = lib.chapter_count(bid)
        n_words = sum(int(c["words"] or 0) for c in chapters)
        progress_ch = None
        progress_vol = ""
        if prog is not None:
            progress_ch = next(
                (c for c in chapters if c["idx"] == prog["chapter_idx"]), None
            )
            if progress_ch is not None:
                progress_vol = volumes.get(progress_ch["vol_idx"], "")
        return render_template(
            "book.html",
            book=b,
            groups=groups,
            progress=prog,
            progress_ch=progress_ch,
            progress_vol=progress_vol,
            n_all=n_all,
            n_done=n_done,
            n_words=n_words,
            n_vols=len(groups),
        )

    # ---------------- 阅读 ----------------
    @app.get("/read/<int:bid>/<int:idx>")
    def read(bid: int, idx: int):
        b = _book_or_404(bid)
        chapters = lib.list_chapters(bid)
        if not chapters:
            flash("这本书还没有章节，先去抓取或导入。")
            return redirect(url_for("book", bid=bid))
        pos = next((i for i, c in enumerate(chapters) if c["idx"] == idx), None)
        if pos is None:
            idx = chapters[0]["idx"]
            pos = 0
        ch = lib.get_chapter(bid, idx)
        prev_ch = chapters[pos - 1] if pos > 0 else None
        next_ch = chapters[pos + 1] if pos + 1 < len(chapters) else None
        volumes = {v["idx"]: v["title"] for v in lib.list_volumes(bid)}

        # 在线读取：显式 ?live=1，或者这一章本地没有正文（目录有、正文没抓）
        # 注意：persist_chapters 只决定"抓下来要不要写库"，不决定"要不要抓"
        want_live = request.args.get("live") == "1" or (not (ch and ch["html"]) and bool(b["url"]))
        live_error = ""
        html_body = _zhify(_proxyify(ch["html"], b["url"] or "")) if ch and ch["html"] else _text_to_html(ch["text"] if ch else "")
        online = False
        live_defer = False
        if want_live and b["url"]:
            if request.args.get("sync") == "1":
                try:
                    _text, live_html = _live_chapter(bid, idx)
                    html_body = _zhify(_proxyify(live_html, b["url"] or "")) or _text_to_html(_text)
                    online = True
                    if request.args.get("noprefetch") != "1":
                        _prefetch(bid, idx, int(cfg.prefetch_count or 0))
                except Exception as exc:  # noqa: BLE001
                    live_error = str(exc)[:160]
                    flash(f"在线读取失败：{live_error}")
            else:
                # 先出骨架，前端再拉正文，避免页面卡在取源站的几十秒里
                live_defer = True
                html_body = ""

        lib.set_progress(bid, idx, 0.0)
        # 记阅读历史（同一本书只留一行，累加次数）
        try:
            lib.touch_history(
                "novel", str(bid), source=(b["source"] or ""),
                title=b["title"] or "",
                sub=(b["author"] or ""),
                cover=f"/cover/{bid}" if (b["cover_path"] or "") else (b["cover"] or ""),
                url=f"/read/{bid}/{idx}",
                chapter_label=(ch["title"] if ch else "") or f"第{idx + 1}章",
            )
        except Exception:  # noqa: BLE001
            pass
        # 顺手把后面几章在后台预热：翻页/连读时就不用干等源站
        if next_ch is not None and b["url"] and request.args.get("noprefetch") != "1":
            try:
                _prefetch(bid, idx, max(1, int(cfg.prefetch_count or 1)))
            except Exception:  # noqa: BLE001
                pass
        return render_template(
            "read.html",
            book=b,
            chapter=ch,
            chapters=chapters,
            pos=pos,
            prev_ch=prev_ch,
            next_ch=next_ch,
            volumes=volumes,
            html_body=html_body,
            online=online,
            live_defer=live_defer,
            stored=bool(ch and ch["html"]),
            live_error=live_error,
            next_warm=bool(
                next_ch
                and crawler.read_live_cache(bid, next_ch["idx"])
            ),
        )

    def _zhify(html: str) -> str:
        """正文 HTML 的繁体转简体（只动文本节点，标签和 src 保留）。"""
        if not html or not getattr(cfg, "convert_to_simplified", True):
            return html
        from ..zhconvert import html_to_simplified

        try:
            return html_to_simplified(html)
        except Exception:  # noqa: BLE001
            return html

    def _text_to_html(text: str) -> str:
        from html import escape

        out = []
        for line in (text or "").split("\n"):
            line = line.strip()
            if line:
                out.append(f"<p>{escape(line)}</p>")
        return "\n".join(out)

    def _proxyify(html: str, referer: str = "") -> str:
        """把正文里的 <img src> 换成 /api/img 代理。

        源站图片有防盗链（Referer 校验 / CDN 直连 403），浏览器直接加载不出来，
        必须由后端带 Referer 抓回来再吐给前端。顺带剔掉 1x1 追踪图。
        """
        if not html or "<img" not in html:
            return html or ""
        from urllib.parse import quote

        ref = f"&ref={quote(referer, safe='')}" if referer else ""

        def repl(m):
            tag = m.group(0)
            for attr in ("data-src", "data-original", "src"):
                mm = re.search(rf'{attr}\s*=\s*"([^"]+)"', tag)
                if not mm:
                    continue
                src = mm.group(1).strip()
                if not src.startswith(("http://", "https://")):
                    # 已经是本地 /media 或已代理的 /api/img，原样保留
                    return tag
                if re.search(r"(1x1|spacer|blank)\.(gif|png)", src, re.I):
                    return ""
                proxied = "/api/img?u=" + quote(src, safe="") + ref
                return f'<img class="illus" src="{proxied}" loading="lazy" alt="插图">'
            return tag

        return re.sub(r"<img[^>]*>", repl, html)

    @app.post("/api/progress")
    def api_progress():
        data = request.get_json(silent=True) or {}
        try:
            lib.set_progress(int(data["book_id"]), int(data["chapter_idx"]), float(data.get("scroll", 0)))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False}), 400
        return jsonify({"ok": True})

    # ---------------- 搜索（本地即时 + 在线聚合异步填充） ----------------
    @app.get("/search")
    def search():
        from ..sources.venera_js import VeneraRegistry
        from ..sources.linovelib import INDEX_SOURCE, INDEX_NAME

        q = request.args.get("q", "").strip()
        kind = request.args.get("kind", "all")
        local = lib.search(q) if q else []

        vreg = VeneraRegistry(cfg)
        installed_keys = {e.key for e, _ in vreg.installed()}
        # 默认只勾 config.comic_sources_default 里那些快源；没配就全勾
        # 注意：同名 key 的条目（不同版本）也全部列出，方便看清有哪些版本可选
        fast = [k for k in (getattr(cfg, "comic_sources_default", None) or []) if k in installed_keys]
        comic_sources = [
            {
                "key": e.key,
                "name": e.name,
                "version": e.version,
                "file": e.file,
                "enabled": (e.key in fast) if fast else True,
            }
            for e in vreg.index()
            if e.key in installed_keys
        ]
        # 内置 Python 漫画源（不依赖 node / Venera JS 运行时）
        from ..sources.copy_manga import SOURCE_KEY as CM_KEY, SOURCE_NAME as CM_NAME

        comic_sources.insert(
            0,
            {
                "key": CM_KEY,
                "name": CM_NAME,
                "version": "web",
                "file": "copy_manga.py",
                "enabled": True,
                "builtin": True,
                "note": "最新资源",
            },
        )
        index_count = lib.novel_index_count(INDEX_SOURCE)
        novel_sources = [
            {"key": "linovel", "name": "轻之文库", "note": "站内实时搜索", "enabled": True},
            {
                "key": INDEX_SOURCE,
                "name": "哔哩轻小说",
                "note": f"书目索引 {index_count} 本" if index_count else "索引为空，点『建索引』",
                "enabled": True,
            },
            {"key": "bangumi", "name": "Bangumi 书目", "note": "书名/封面/简介", "enabled": True},
            {"key": "local", "name": "本地书架", "note": "已抓的书 + 正文命中", "enabled": True},
        ]
        from ..sources.anime import ANIME_SOURCES

        return render_template(
            "search.html",
            q=q,
            kind=kind,
            results=local,
            comic_sources=comic_sources,
            novel_sources=novel_sources,
            anime_sources=[
                {
                    "key": k,
                    "name": v[0],
                    "enabled": k in (getattr(cfg, "anime_sources_default", None) or list(ANIME_SOURCES)),
                }
                for k, v in ANIME_SOURCES.items()
            ],
            index_count=index_count,
        )

    @app.get("/api/novelindex/status")
    def api_index_status():
        from ..sources.linovelib import INDEX_SOURCE

        with lock:
            st = dict(index_state)
        st["count"] = lib.novel_index_count(INDEX_SOURCE)
        return jsonify(ok=True, **st)

    @app.post("/api/novelindex/build")
    def api_index_build():
        from ..sources.linovelib import build_index, refresh_index

        body = request.form.get("mode") or (request.get_json(silent=True) or {}).get("mode") or "full"
        pages = request.form.get("pages") or (request.get_json(silent=True) or {}).get("pages") or 60
        try:
            budget = max(1, min(2000, int(pages)))
        except (TypeError, ValueError):
            budget = 60
        incremental = body == "refresh"
        with lock:
            if index_state.get("running"):
                return jsonify(ok=True, **dict(index_state))
            index_state.update(
                running=True,
                pages=0,
                budget=(budget if not incremental else 6),
                total=lib.novel_index_count("linovelib"),
                list="增量刷新（新书/最近更新）" if incremental else "",
                error="",
                started=time.time(),
                mode="refresh" if incremental else "full",
            )

        def on_progress(p: dict) -> None:
            with lock:
                index_state.update(
                    pages=p.get("pages", 0),
                    budget=p.get("budget", budget),
                    total=p.get("total", 0),
                    list=p.get("list", ""),
                )

        def work() -> None:
            try:
                if incremental:
                    res = refresh_index(cfg, lib, pages=budget, on_progress=on_progress)
                    out = {
                        "pages": res["requests"],
                        "total": res["total"],
                        "done": True,
                        "added": res["added"],
                    }
                else:
                    res = build_index(cfg, lib, budget=budget, on_progress=on_progress)
                    out = {
                        "pages": res["pages"],
                        "total": res["total"],
                        "done": res.get("done"),
                        "added": res.get("added", 0),
                    }
                with lock:
                    index_state.update(running=False, error="", **out)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    index_state.update(running=False, error=str(exc)[:200])

        threading.Thread(target=work, daemon=True).start()
        with lock:
            return jsonify(ok=True, **dict(index_state))

    @app.get("/api/aggregate")
    def api_aggregate():
        from ..aggregate import aggregate, NOVEL_SOURCES
        from ..sources.venera_js import VeneraRegistry

        q = request.args.get("q", "").strip()
        if not q:
            return jsonify(ok=False, error="空关键词"), 400

        kind = request.args.get("kind", "all")
        novels_raw = request.args.get("novels", None)
        animes_raw = request.args.get("animes", None)
        novel_on = request.args.get("novel", "1") != "0"
        comics_raw = request.args.get("comics", None)

        kinds: list[str] = []
        novel_keys: list[str] | None = None
        if kind in ("all", "novel") and novel_on:
            kinds.append("novel")
            if novels_raw is not None:
                novel_keys = [k for k in novels_raw.split(",") if k.strip()]
        anime_keys: list[str] | None = None
        if kind in ("all", "anime"):
            kinds.append("anime")
            if animes_raw is not None:
                anime_keys = [k for k in animes_raw.split(",") if k.strip()]
        want_comic = kind in ("all", "comic") and (comics_raw is None or comics_raw.strip() != "")
        if want_comic:
            kinds.append("comic")
        if not kinds:
            return jsonify(ok=True, novels=[], comics=[], animes=[], errors=["没有选择任何源"])
        if kind in ("all", "novel") and novel_on and novel_keys == []:
            return jsonify(ok=True, novels=[], comics=[], animes=[], errors=["没有选择小说源"])
        if kind in ("all", "anime") and anime_keys == []:
            return jsonify(ok=True, novels=[], comics=[], animes=[], errors=["没有选择动漫源"])

        comic_keys: list[str] | None = None
        if want_comic:
            if comics_raw is not None:
                comic_keys = [k for k in comics_raw.split(",") if k.strip()]
            else:
                # 没指定源时用 config 里那批"实测能跑"的默认源。
                # 退回到全部已安装会让 37 个源一起并发，一堆慢源/坏源把聚合拖到超时。
                disabled = set(lib.disabled_js_sources())
                defaults = [
                    k for k in (getattr(cfg, "comic_sources_default", None) or [])
                    if k not in disabled
                ]
                comic_keys = defaults or [
                    e.key for e, _ in VeneraRegistry(cfg).installed() if e.key not in disabled
                ]

        try:
            # 番剧源普遍比漫画慢（一次要拉整部剧的播放列表），单查番剧时放宽总超时
            tmo = 25.0 if kinds == ["anime"] else 15.0
            res = aggregate(
                cfg, q, kinds=kinds, novel_keys=novel_keys, comic_keys=comic_keys,
                anime_keys=anime_keys, lib=lib, timeout=tmo,
            )
            res["novel_sources"] = len(novel_keys) if novel_keys is not None else len(NOVEL_SOURCES)
            return jsonify(ok=True, **res)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:200])

    # ---------------- 换源阅读（小说）：同一本书换到别的源 ----------------
    @app.get("/switch/<int:bid>")
    def switch_source(bid: int):
        from ..aggregate import aggregate

        b = _book_or_404(bid)
        title = (b["title"] or "").strip()
        rows, errors = [], []
        others = [k for k in ("linovel", "linovelib") if k != b["source"]]

        # 1) 哔哩轻小说：查本地书目索引（快、精确）
        if title and "linovelib" in others:
            short = re.split(r"[～~（(\s]", title)[0].strip() or title
            for kw in dict.fromkeys([title, short]):
                for r in lib.search_novel_index(kw, source="linovelib", limit=8):
                    r = dict(r)
                    rows.append(
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
                            "action": "add",
                            "book_id": None,
                        }
                    )
                if rows:
                    break

        # 2) 轻之文库：实时搜，但只留书名真的对得上的
        if title and "linovel" in others:
            from ..aggregate import _relevance, _novel_linovel

            try:
                hits = _novel_linovel(cfg, title, 12, lib)
            except Exception as exc:  # noqa: BLE001
                hits, errors = [], [f"linovel: {str(exc)[:80]}"]
            short = re.split(r"[～~（(\s]", title)[0].strip() or title
            for r in hits:
                # 只留书名里真的含这段文字的，轻之文库的模糊结果太杂
                if short and (short in (r.get("title") or "")):
                    rows.append(r)

        # 去掉已是当前源自己的
        rows = [r for r in rows if r.get("source") != b["source"]]

        # 3) 一个都对不上时，给「相近」候选（书名前 4 个字，只查书目索引，快且不乱）
        fuzzy = False
        if not rows and title and "linovelib" in others:
            key4 = re.split(r"[～~（(\s！!。，,]", title)[0].strip()[:4]
            if len(key4) >= 2:
                for r in lib.search_novel_index(key4, source="linovelib", limit=8):
                    r = dict(r)
                    rows.append(
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
                            "action": "add",
                            "book_id": None,
                            "fuzzy": True,
                        }
                    )
                fuzzy = bool(rows)
        return render_template(
            "switch.html", book=b, rows=rows[:12], errors=errors, tried=others, fuzzy=fuzzy
        )

    # ---------------- 番剧：书架 / 详情 / 播放 ----------------
    # ---------------- 阅读历史 ----------------
    @app.get("/history")
    def history():
        kind = request.args.get("kind", "").strip()
        if kind not in ("novel", "comic", "anime"):
            kind = ""
        rows = lib.list_history(kind or None, limit=300)
        items = []
        for r in rows:
            d = dict(r)
            # 小说封面：优先本地文件，其次外链
            cover = d.get("cover") or ""
            if d["kind"] == "novel" and str(cover).startswith("/cover/"):
                pass  # 已经是本地封面路由
            items.append(d)

        # 按「今天 / 昨天 / 更早」分组；解析失败的一律归到更早
        from datetime import date, datetime, timedelta

        today = date.today()

        def _day_label(raw) -> str:
            try:
                d = datetime.fromisoformat(str(raw).replace("Z", "")).date()
            except (TypeError, ValueError):
                return "更早"
            if d == today:
                return "今天"
            if d == today - timedelta(days=1):
                return "昨天"
            return "更早"

        groups: list[dict] = []
        index: dict[str, dict] = {}
        for it in items:
            label = _day_label(it.get("last_read_at"))
            it["group"] = label
            if label not in index:
                index[label] = {"label": label, "items": []}
                groups.append(index[label])
            index[label]["items"].append(it)

        return render_template(
            "history.html",
            items=items,
            groups=groups,
            kind=kind,
            counts={
                "novel": lib.history_count("novel"),
                "comic": lib.history_count("comic"),
                "anime": lib.history_count("anime"),
            },
            total=lib.history_count(),
        )

    @app.post("/history/delete/<int:hid>")
    def history_delete(hid: int):
        lib.delete_history(hid)
        return redirect(url_for("history"))

    @app.post("/history/clear")
    def history_clear():
        kind = request.form.get("kind", "").strip()
        lib.clear_history(kind if kind in ("novel", "comic", "anime") else None)
        return redirect(url_for("history", kind=kind) if kind else url_for("history"))

    @app.get("/animes")
    def animes():
        from ..sources.anime import ANIME_SOURCES

        prog = lib.all_anime_progress()
        items = []
        for a in lib.list_animes():
            p = prog.get((a["source"], str(a["source_id"])))
            items.append({"anime": a, "progress": p})
        return render_template(
            "animes.html",
            items=items,
            sources=[{"key": k, "name": v[0]} for k, v in ANIME_SOURCES.items()],
        )

    @app.get("/anime/<source>/<sid>")
    def anime_detail(source: str, sid: str):
        from ..sources.anime import ANIME_SOURCES, detail

        if source not in ANIME_SOURCES:
            abort(404)
        kw = request.args.get("title", "").strip()
        try:
            info = detail(cfg, source, sid)
        except Exception as exc:  # noqa: BLE001
            return (
                f"<h3>取不到这部剧</h3><p>{exc}</p>"
                f"<p><a href=\"/animes\">回动漫书架</a></p>",
                502,
            )
        road_i = int(request.args.get("road", 0) or 0)
        if road_i >= len(info["roads"]):
            road_i = 0
        prog = lib.get_anime_progress(source, sid)
        saved = lib.find_anime(source, sid)
        return render_template(
            "anime.html",
            info=info,
            road_i=road_i,
            progress=prog,
            saved=saved,
            title_kw=kw,
        )

    @app.post("/anime/save")
    def anime_save():
        from ..sources.anime import detail

        source = request.form.get("source", "")
        sid = request.form.get("sid", "")
        if not (source and sid):
            abort(400)
        try:
            info = detail(cfg, source, sid)
        except Exception as exc:  # noqa: BLE001
            flash(f"收藏失败：{exc}")
            return redirect(url_for("animes"))
        lib.upsert_anime(info)
        return redirect(url_for("anime_detail", source=source, sid=sid))

    @app.post("/anime/delete/<int:aid>")
    def anime_delete(aid: int):
        lib.delete_anime(aid)
        return redirect(url_for("animes"))

    @app.get("/play/<source>/<sid>/<int:ep_i>")
    def anime_play(source: str, sid: str, ep_i: int):
        from ..sources.anime import ANIME_SOURCES, detail

        if source not in ANIME_SOURCES:
            abort(404)
        road_i = int(request.args.get("road", 0) or 0)
        try:
            info = detail(cfg, source, sid)
        except Exception as exc:  # noqa: BLE001
            return f"<h3>取不到剧集</h3><p>{exc}</p>", 502
        roads = info["roads"]
        if not roads:
            return "<h3>这个源没有可播线路</h3>", 404
        road_i = min(max(road_i, 0), len(roads) - 1)
        eps = roads[road_i]["eps"]
        ep_i = min(max(ep_i, 0), len(eps) - 1)
        ep = eps[ep_i]
        prog = lib.get_anime_progress(source, sid)
        # 记观看历史
        try:
            lib.touch_history(
                "anime", str(sid), source=source,
                title=(info.get("title") or sid),
                sub=(info.get("year") or ""),
                cover=(info.get("cover") or ""),
                url=f"/play/{source}/{sid}/{ep_i}",
                chapter_label=(ep.get("name") or f"第{ep_i + 1}集"),
            )
        except Exception:  # noqa: BLE001
            pass
        return render_template(
            "play.html",
            info=info,
            road_i=road_i,
            ep_i=ep_i,
            ep=ep,
            eps=eps,
            progress=prog,
        )

    @app.get("/api/anime/eps")
    def api_anime_eps():
        from ..sources.anime import detail

        source = request.args.get("source", "")
        sid = request.args.get("id", "")
        if not (source and sid):
            return jsonify(ok=False, error="缺少参数"), 400
        try:
            return jsonify(ok=True, **detail(cfg, source, sid))
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:200])

    @app.post("/api/anime/progress")
    def api_anime_progress():
        d = request.get_json(silent=True) or {}
        try:
            lib.set_anime_progress(
                d["source"],
                str(d["source_id"]),
                ep_idx=int(d.get("ep_idx", 0)),
                ep_title=d.get("ep_title", ""),
                ep_url=d.get("ep_url", ""),
                road=d.get("road", ""),
                total=int(d.get("total", 0)),
                position=float(d.get("position", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return jsonify(ok=False), 400
        return jsonify(ok=True)

    # ---------------- 弹幕（弹弹play 开放弹幕网络） ----------------
    @app.get("/api/danmu/search")
    def api_danmu_search():
        """按番剧名搜弹弹play，返回候选番剧与分集。"""
        from ..danmu import client, DanmuError, DanmuNotConfigured

        kw = (request.args.get("anime") or "").strip()
        if not kw:
            return jsonify(ok=False, error="缺少 anime 参数"), 400
        dc = client(cfg)
        if not dc.configured:
            return jsonify(ok=False, configured=False,
                           error="未配置弹弹play凭据（config.json → dandanplay）")
        try:
            data = dc.search_anime(kw)
        except DanmuNotConfigured as exc:
            return jsonify(ok=False, configured=False, error=str(exc))
        except DanmuError as exc:
            return jsonify(ok=False, configured=True, error=str(exc)[:300])
        return jsonify(ok=True, configured=True, animes=data)

    @app.get("/api/danmu")
    def api_danmu():
        """取某个 episodeId 的弹幕。

        参数：
            episode_id  弹弹play 节目编号（优先）
            anime       番剧名（没给 episode_id 时用它搜，取第一部的第 1 集）
            ep          集号（从 1 开始，配合 anime 用）
        """
        from ..danmu import client, DanmuError, DanmuNotConfigured

        dc = client(cfg)
        if not dc.configured:
            return jsonify(ok=False, configured=False, comments=[],
                           error="未配置弹弹play凭据")

        ep_id = (request.args.get("episode_id") or "").strip()
        anime = (request.args.get("anime") or "").strip()
        ep_title_hint = (request.args.get("eptitle") or "").strip()
        ep_no = request.args.get("ep", "1")
        try:
            ep_no = max(1, int(ep_no))
        except (TypeError, ValueError):
            ep_no = 1

        try:
            if not ep_id:
                if not anime:
                    return jsonify(ok=False, error="缺少 episode_id 或 anime"), 400
                cands = dc.search_anime(anime)
                if not cands:
                    return jsonify(ok=False, configured=True, comments=[],
                                   error=f"弹弹play 没搜到「{anime}」")
                # 优先取名字最接近的
                best = _pick_best_anime(cands, anime)
                eps = best.get("episodes") or []
                if not eps:
                    return jsonify(ok=False, configured=True, comments=[],
                                   error=f"「{best.get('animeTitle')}」没有分集")
                idx = _pick_best_episode(eps, ep_no, ep_title_hint)
                ep_id = str(eps[idx]["episodeId"])
                ep_title = eps[idx].get("episodeTitle") or ""
            else:
                ep_title = ""

            comments = dc.get_comments(int(ep_id),
                                       with_related=bool(cfg.dandanplay.get("with_related", True)))
        except DanmuNotConfigured as exc:
            return jsonify(ok=False, configured=False, comments=[], error=str(exc))
        except (DanmuError, ValueError) as exc:
            return jsonify(ok=False, configured=True, comments=[],
                           error=str(exc)[:300])

        return jsonify(ok=True, configured=True, episode_id=ep_id,
                       episode_title=ep_title,
                       count=len(comments),
                       comments=[c.to_dict() for c in comments])

    # --------- m3u8 / 切片代理（源站要 Referer，浏览器直连还会被 CORS 拦） ---------
    def _hls_fetch(url: str) -> bytes:
        from ..net import Fetcher

        ref = ""
        for name, (_n, base, referer) in __import__(
            "novelhub.sources.anime", fromlist=["ANIME_SOURCES"]
        ).ANIME_SOURCES.items():
            host = base.split("/")[2] if "://" in base else ""
            if host and host in url:
                ref = referer or ""
                break
        if not ref:
            # 播放域名和接口域名常常不同（akua./v9./b3.），统一用片头 Referer 兜底
            ref = "https://ani.girigirilove.com/" if "girigiri" in url else ""
        with Fetcher(cfg) as f:
            return f.get_bytes(url, referer=ref or None)

    def _seg_proxied(target: str) -> str:
        from urllib.parse import quote as _q

        if target.endswith(".m3u8") or ".m3u8?" in target:
            return "/api/hls?u=" + _q(target, safe="")
        return "/api/hls/seg?u=" + _q(target, safe="")

    @app.get("/api/hls")
    def api_hls():
        from urllib.parse import quote as _q
        from urllib.parse import urljoin

        u = (request.args.get("u") or "").strip()
        if not u.startswith(("http://", "https://")):
            abort(400)
        try:
            raw = _hls_fetch(u).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return Response(f"# 拉取失败：{exc}", status=502, mimetype="text/plain")
        base = u.rsplit("/", 1)[0] + "/"
        out = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                out.append("")
                continue
            if s.startswith("#"):
                def _uri(m):
                    return 'URI="' + _seg_proxied(urljoin(base, m.group(1))) + '"'

                out.append(re.sub(r'URI="([^"]+)"', _uri, s))
            else:
                out.append(_seg_proxied(urljoin(base, s)))
        return Response(
            "\n".join(out),
            mimetype="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )

    @app.get("/api/hls/seg")
    def api_hls_seg():
        u = (request.args.get("u") or "").strip()
        if not u.startswith(("http://", "https://")):
            abort(400)
        try:
            data = _hls_fetch(u)
        except Exception as exc:  # noqa: BLE001
            return Response(str(exc)[:160], status=502, mimetype="text/plain")
        mime = "video/mp2t"
        if u.endswith(".aac") or u.endswith(".mp4"):
            mime = "video/mp4"
        elif u.endswith(".key"):
            mime = "application/octet-stream"
        return Response(
            data,
            mimetype=mime,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=3600"},
        )

    # ---------------- 一键加入书架（聚合结果点"加入"） ----------------
    @app.get("/add")
    def add_book():
        url = request.args.get("url", "").strip()
        if not url:
            return redirect(url_for("index"))
        # 两次请求：先秒回一个"正在加入"页，再去真的抓目录（抓目录可能要几秒到几十秒）
        if request.args.get("go") != "1":
            nxt = url_for("add_book", url=url, go=1)
            return (
                "<!doctype html><meta charset='utf-8'>"
                "<title>正在加入…</title>"
                "<style>body{font:15px/1.9 system-ui;max-width:36em;margin:12vh auto;"
                "padding:0 1.2em;color:#333}code{font-size:12px;color:#888}</style>"
                "<p>正在读取这本书的目录，请稍等…</p>"
                "<p><code></code></p>"
                f"<noscript><a href='{nxt}'>继续</a></noscript>"
                f"<script>setTimeout(function(){{location.replace({json.dumps(nxt)});}},60);</script>"
            )
        try:
            catalog = crawler.inspect(url)
            bid = crawler.save_book(catalog)
        except Exception as exc:  # noqa: BLE001
            return (
                f"<h3>加入失败</h3><p>{exc}</p>"
                "<p><a href=\"/search\">返回搜索</a> · "
                f"<a href=\"{url}\" target=\"_blank\" rel=\"noreferrer\">直接打开原页</a></p>",
                400,
            )
        return redirect(url_for("book", bid=bid))

    # ---------------- 漫画书架 ----------------
    @app.get("/comics")
    def comics():
        progress = lib.all_comic_progress()
        items = []
        for c in lib.list_comics():
            p = progress.get((c["source"], str(c["source_id"])))
            items.append(
                {
                    "comic": c,
                    "last_ep": p["ep_id"] if p else "",
                    "last_title": p["ep_title"] if p else "",
                    "last_at": p["updated_at"] if p else "",
                }
            )
        return render_template("comics.html", items=items)

    @app.post("/comics/delete/<int:cid>")
    def comics_delete(cid: int):
        lib.delete_comic(cid)
        return redirect(url_for("comics"))

    # ---------------- 抓取 ----------------
    @app.get("/crawl")
    def crawl_page():
        return render_template("crawl.html", url=request.args.get("url", ""))

    @app.post("/crawl")
    def crawl_start():
        url = (request.form.get("url") or "").strip()
        if not url:
            flash("请填写书籍地址")
            return redirect(url_for("crawl_page"))
        limit_raw = (request.form.get("limit") or "").strip()
        limit = int(limit_raw) if limit_raw.isdigit() else None
        force = request.form.get("force") == "1"
        vols_raw = (request.form.get("volumes") or "").strip()
        volumes = []
        if vols_raw:
            for part in vols_raw.replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    if "-" in part:
                        a, b = part.split("-", 1)
                        volumes += list(range(int(a) - 1, int(b)))
                    else:
                        volumes.append(int(part) - 1)
                except ValueError:
                    continue
        tid = _start_task(
            url, limit, force, volumes or None, request.form.get("catalog_only") == "1"
        )
        return redirect(url_for("task_page", tid=tid))

    @app.post("/crawl/resume/<int:bid>")
    def crawl_resume(bid: int):
        b = _book_or_404(bid)
        if not b["url"]:
            flash("本地导入的书无法续抓")
            return redirect(url_for("book", bid=bid))
        tid = _start_task(b["url"], None, False, None)
        return redirect(url_for("task_page", tid=tid))

    @app.get("/task/<tid>")
    def task_page(tid: str):
        t = tasks.get(tid)
        if not t:
            abort(404)
        return render_template("task.html", task=t)

    @app.get("/api/task/<tid>")
    def api_task(tid: str):
        t = tasks.get(tid)
        if not t:
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, **{k: v for k, v in t.items() if k != "id"}})

    # ---------------- 发现 / 批量 ----------------
    @app.get("/discover")
    def discover_page():
        from ..discover import discover as do_discover

        url = request.args.get("url", "").strip()
        books, error = [], ""
        if url:
            try:
                books = do_discover(cfg, registry, url, limit=int(request.args.get("top", 30)))
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:200]
        return render_template("discover.html", url=url, books=books, error=error)

    @app.post("/discover/batch")
    def discover_batch():
        url = (request.form.get("url") or "").strip()
        if not url:
            flash("请填写列表页地址")
            return redirect(url_for("discover_page"))
        top = int(request.form.get("top") or 10)
        tid = _start_batch(url, top, request.form.get("full") != "1")
        return redirect(url_for("task_page", tid=tid))

    # ---------------- 导入 ----------------
    @app.get("/import")
    def import_page():
        return render_template("import.html")

    @app.post("/import")
    def import_upload():
        files = request.files.getlist("files")
        if not files:
            flash("没有选择文件")
            return redirect(url_for("import_page"))
        tmp_dir = cfg.data_path / "uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ids: list[int] = []
        errors: list[str] = []
        for f in files:
            if not f or not f.filename:
                continue
            suffix = Path(f.filename).suffix.lower()
            if suffix not in (".txt", ".epub", ".md", ".markdown"):
                errors.append(f"{f.filename}: 不支持的格式")
                continue
            dst = tmp_dir / f.filename
            f.save(dst)
            try:
                ids += importer.import_path(dst)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f.filename}: {exc}")
        if errors:
            flash("；".join(errors))
        if len(ids) == 1:
            return redirect(url_for("book", bid=ids[0]))
        return redirect(url_for("index"))

    @app.post("/import/path")
    def import_local_path():
        p = (request.form.get("path") or "").strip().strip('"')
        if not p:
            flash("请填写本地路径")
            return redirect(url_for("import_page"))
        try:
            ids = importer.import_path(p)
        except Exception as exc:  # noqa: BLE001
            flash(str(exc))
            return redirect(url_for("import_page"))
        flash(f"导入完成：{len(ids)} 本")
        if len(ids) == 1:
            return redirect(url_for("book", bid=ids[0]))
        return redirect(url_for("index"))

    # ---------------- 封面 / 插图 / 导出 / 删除 ----------------
    @app.get("/media/<int:bid>/<path:name>")
    def media(bid: int, name: str):
        base = (cfg.data_path / "images" / str(bid)).resolve()
        target = (base / name).resolve()
        if base != target.parent or not target.is_file():
            abort(404)
        return send_file(target)

    @app.get("/cover/<int:bid>")
    def cover(bid: int):
        b = _book_or_404(bid)
        if b["cover_path"] and Path(b["cover_path"]).exists():
            return send_file(b["cover_path"])
        abort(404)

    @app.get("/export/<int:bid>")
    def export_book(bid: int):
        fmt = request.args.get("fmt", "epub").lower()
        if fmt not in ("txt", "epub", "md", "html"):
            abort(400)
        try:
            path = exporter.export(bid, fmt)
        except Exception as exc:  # noqa: BLE001
            flash(f"导出失败：{exc}")
            return redirect(url_for("book", bid=bid))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.post("/delete/<int:bid>")
    def delete_book(bid: int):
        lib.delete_book(bid)
        return redirect(url_for("index"))

    @app.post("/rename/<int:bid>")
    def rename_book(bid: int):
        lib.rename_book(bid, request.form.get("title", ""), request.form.get("author", ""))
        return redirect(url_for("book", bid=bid))

    # ---------------- JSON API ----------------
    @app.get("/api/books")
    def api_books():
        return jsonify([dict(b) for b in lib.list_books()])

    @app.get("/api/book/<int:bid>")
    def api_book(bid: int):
        b = _book_or_404(bid)
        return jsonify(
            {
                "book": dict(b),
                "volumes": [dict(v) for v in lib.list_volumes(bid)],
                "chapters": [dict(c) for c in lib.list_chapters(bid)],
            }
        )

    @app.get("/api/chapter/<int:bid>/<int:idx>")
    def api_chapter(bid: int, idx: int):
        ch = lib.get_chapter(bid, idx)
        if ch is None:
            abort(404)
        b = lib.get_book(bid)
        want_live = request.args.get("live") == "1" or (not ch["html"] and b and b["url"])
        if want_live:
            try:
                _text, html = _live_chapter(bid, idx)
                if b and b["url"] and request.args.get("noprefetch") != "1":
                    _prefetch(bid, idx, int(cfg.prefetch_count or 0))
                return jsonify(
                    ok=True,
                    online=True,
                    title=ch["title"],
                    html=_zhify(_proxyify(html, (b["url"] if b else "") or "")),
                    text=_text,
                    words=len(re.sub(r"\s+", "", _text or "")),
                )
            except Exception as exc:  # noqa: BLE001
                return jsonify(ok=False, online=False, error=str(exc)[:200]), 200
        return jsonify(
            ok=True,
            online=False,
            title=ch["title"],
            html=_zhify(_proxyify(ch["html"], (b["url"] if b else "") or "")),
            text=ch["text"],
            words=ch["words"],
        )

    # --------- 预热接口：把后面几章抓进本地缓存（不落库），翻章即读 ---------
    @app.get("/api/prefetch")
    def api_prefetch():
        bid = request.args.get("bid", type=int)
        idx = request.args.get("idx", type=int)
        n = request.args.get("n", default=int(cfg.prefetch_count or 1), type=int)
        if bid is None or idx is None:
            return jsonify(ok=False, error="缺少参数"), 400
        b = lib.get_book(bid)
        chapters = lib.list_chapters(bid)
        upcoming = [c["idx"] for c in chapters if c["idx"] > idx][: max(0, min(n, 5))]
        warm, missing = [], []
        for i in upcoming:
            row = lib.get_chapter(bid, i)
            if row is not None and row["html"]:
                warm.append(i)          # 已经落库了
            elif crawler.read_live_cache(bid, i):
                warm.append(i)          # 缓存里已有
            else:
                missing.append(i)
        if missing and b is not None and b["url"]:
            try:
                _prefetch(bid, idx, len(upcoming))
            except Exception:  # noqa: BLE001
                pass
        return jsonify(ok=True, upcoming=upcoming, warm=warm, warming=missing)

    @app.get("/api/chapter/<int:bid>/<int:idx>/stream")
    def api_chapter_stream(bid: int, idx: int):
        """边抓边吐：每抓到一页就推一段，前端立刻追加显示。

        先发一条 ``event: meta``（章标题 + 下一章），前端用它做连读和标题栏更新；
        最后 ``event: done`` 里也带 next，方便一直往下接。
        """
        from ..textutil import html_to_text

        ch = lib.get_chapter(bid, idx)
        if ch is None:
            abort(404)
        b = lib.get_book(bid)
        chapters = lib.list_chapters(bid)
        pos = next((i for i, c in enumerate(chapters) if c["idx"] == idx), None)
        nxt = chapters[pos + 1] if pos is not None and pos + 1 < len(chapters) else None
        meta = {
            "idx": idx,
            "title": ch["title"] or f"第{idx + 1}章",
            "next": nxt["idx"] if nxt else None,
            "next_title": (nxt["title"] if nxt else "") or "",
            "pos": (pos + 1) if pos is not None else 0,
            "total": len(chapters),
        }
        meta_line = "event: meta\ndata: " + json.dumps(meta, ensure_ascii=False) + "\n\n"

        # 库里已有正文就直接一次给完，不必流式
        if ch["html"] and request.args.get("live") != "1":
            payload = json.dumps(
                {"html": _zhify(_proxyify(ch["html"], (b["url"] if b else "") or "")), "words": ch["words"] or 0},
                ensure_ascii=False,
            )
            done = {"words": ch["words"] or 0, "next": meta["next"], "next_title": meta["next_title"]}
            body = (
                meta_line
                + f"data: {payload}\n\n"
                + "event: done\ndata: " + json.dumps(done, ensure_ascii=False) + "\n\n"
            )
            return Response(
                body,
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        def gen():
            words = 0
            yield meta_line
            try:
                for frag in crawler.iter_chapter_live(bid, idx):
                    frag = _zhify(_proxyify(frag, (b["url"] if b else "") or ""))
                    n = len(re.sub(r"\s+", "", html_to_text(frag)))
                    words += n
                    yield "data: " + json.dumps(
                        {"html": frag, "words": n, "total": words}, ensure_ascii=False
                    ) + "\n\n"
                yield "event: done\ndata: " + json.dumps(
                    {"words": words, "next": meta["next"], "next_title": meta["next_title"]},
                    ensure_ascii=False,
                ) + "\n\n"
            except Exception as exc:  # noqa: BLE001
                yield "event: error\ndata: " + json.dumps(
                    {"error": str(exc)[:200]}, ensure_ascii=False
                ) + "\n\n"
            finally:
                if b and b["url"] and request.args.get("noprefetch") != "1":
                    try:
                        _prefetch(bid, idx, int(cfg.prefetch_count or 0))
                    except Exception:
                        pass

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ---------------- 图片代理（逐张懒加载，避免 CORS/Referer 问题） ----------------
    @app.get("/api/img")
    def api_img():
        from ..net import Fetcher

        u = (request.args.get("u") or "").strip()
        ref = (request.args.get("ref") or "").strip()
        if not u.startswith(("http://", "https://")):
            abort(400)
        cache_dir = cfg.data_path / "cache" / "img"
        cache_dir.mkdir(parents=True, exist_ok=True)
        import hashlib

        cf = cache_dir / hashlib.sha1(u.encode("utf-8")).hexdigest()
        if cf.exists() and cf.stat().st_size > 0:
            data = cf.read_bytes()
        else:
            try:
                with Fetcher(cfg) as f:
                    data = f.get_bytes(u, referer=ref or None)
            except Exception as exc:  # noqa: BLE001
                # 占位图，别让整章排版崩掉；参数里带 r= 可绕过缓存重试
                svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="360" height="120">'
                    '<rect width="100%" height="100%" fill="#eee"/>'
                    '<text x="50%" y="46%" font-size="14" fill="#888" text-anchor="middle">'
                    "插图加载失败（点击重试）</text>"
                    '<text x="50%" y="68%" font-size="11" fill="#aaa" text-anchor="middle">'
                    f"{str(exc)[:60]}</text></svg>"
                ).encode("utf-8")
                # 用 502 而不是 200：<img> 只有收到错误状态才会触发 onerror，
                # 页面上的「刷新」浮条和插图的重试 UI 都靠这个事件。
                # 直接拿浏览器打开这个 URL 时仍能看到这段 SVG 提示。
                return Response(
                    svg,
                    mimetype="image/svg+xml",
                    status=502,
                    headers={"Cache-Control": "no-store", "X-Img-Error": "1"},
                )
        if len(data) > 25 * 1024 * 1024:
            abort(413)
        mime = "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif data[:4] == b"GIF8":
            mime = "image/gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        return Response(data, mimetype=mime, headers={"Cache-Control": "max-age=86400"})

    # ---------------- 漫画：源 + 一话图片 ----------------
    def _js_source(key: str):
        """按 key 取漫画源：拷贝漫画是内置 Python 源，其余是 Venera JS 源。"""
        from ..sources.venera_js import JsSource, VeneraRegistry

        if key == "copy_manga":
            from ..sources.copy_manga import CopyMangaSource

            return CopyMangaSource(cfg)
        path = VeneraRegistry(cfg).install(key)
        return JsSource(cfg, path)

    @app.get("/api/comic/ep")
    def api_comic_ep():
        key = request.args.get("key", "")
        comic_id = request.args.get("comic", "")
        ep_id = request.args.get("ep", "")
        if not (key and comic_id and ep_id):
            return jsonify(ok=False, error="缺少参数"), 400
        try:
            src = _js_source(key)
            urls = src.episode(comic_id, ep_id)
            return jsonify(ok=True, urls=urls)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:300])

    @app.get("/api/comic/progress")
    def api_comic_progress_set():
        key = request.args.get("key", "")
        comic_id = request.args.get("comic", "")
        ep_id = request.args.get("ep", "")
        title = request.args.get("t", "")
        if not (key and comic_id and ep_id):
            return jsonify(ok=False), 400
        try:
            lib.set_comic_progress(key, comic_id, ep_id, title)
            # 章节名是前端异步拿到的，顺手回填历史里那一行的「读到第几话」
            if title:
                row = lib.find_comic(key, comic_id)
                lib.touch_history(
                    "comic", str(comic_id), source=key,
                    title=(row["title"] if row else "") or "",
                    sub=(row["author"] if row else "") or "",
                    cover=(row["cover"] if row else "") or "",
                    url=f"/comic/{key}/{comic_id}/{ep_id}",
                    chapter_label=title,
                    bump=False,
                )
            return jsonify(ok=True)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:120])

    @app.get("/api/comic/info")
    def api_comic_info():
        key = request.args.get("key", "")
        comic_id = request.args.get("comic", "")
        try:
            return jsonify(ok=True, info=_js_source(key).info(comic_id))
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:300])

    @app.get("/comic/<key>/<comic_id>/<ep_id>")
    def comic_read(key: str, comic_id: str, ep_id: str):
        ep_title = request.args.get("t", "")
        try:
            lib.set_comic_progress(key, comic_id, ep_id, ep_title)
        except Exception:
            pass
        # 记历史：书名/封面从漫画书架里取（详情页打开时已经收进去了）
        try:
            row = lib.find_comic(key, comic_id)
            lib.touch_history(
                "comic", str(comic_id), source=key,
                title=(row["title"] if row else "") or f"{key} / {comic_id}",
                sub=(row["author"] if row else "") or "",
                cover=(row["cover"] if row else "") or "",
                url=f"/comic/{key}/{comic_id}/{ep_id}",
                chapter_label=ep_title or "",
            )
        except Exception:  # noqa: BLE001
            pass
        return render_template(
            "comic.html", key=key, comic_id=comic_id, ep_id=ep_id, ep_title=ep_title
        )

    @app.get("/comic/<key>/<comic_id>")
    def comic_detail(key: str, comic_id: str):
        info, error = {}, ""
        try:
            src = _js_source(key)
            info = src.info(comic_id)
            try:
                lib.upsert_comic(
                    {
                        "source": key,
                        "source_id": str(comic_id),
                        "title": info.get("title") or f"{key}-{comic_id}",
                        "author": info.get("subTitle") or "",
                        "cover": info.get("cover") or "",
                        "description": info.get("description") or "",
                        "tags": info.get("tags") or [],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                import sys

                print(f"[comic upsert] {key}/{comic_id} 失败: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:200]
        chapters = []
        groups = info.get("chapters") or {}
        if isinstance(groups, dict):
            for gname, eps in groups.items():
                if isinstance(eps, dict):
                    for eid, title in eps.items():
                        chapters.append((str(gname), str(eid), str(title)))
        prog = lib.get_comic_progress(key, comic_id)
        return render_template(
            "comic_detail.html",
            key=key,
            comic_id=comic_id,
            info=info,
            chapters=chapters,
            error=error,
            progress=prog,
        )

    # ---------------- 源排行榜（健康检查） ----------------
    @app.get("/sources/rank")
    def sources_rank():
        rows = lib.list_source_health()
        summary = lib.source_health_summary()
        checked = max((r["checked_at"] or "" for r in rows), default="")
        # 标出哪些源正在参与聚合搜索（配置里的默认源 + 已启用的漫画源）
        novel_keys = {"linovel", "linovelib", "local"}
        anime_keys = set(getattr(cfg, "anime_sources_default", None) or [])
        comic_keys = set(getattr(cfg, "comic_sources_default", None) or [])
        for r in rows:
            if r["kind"] == "novel":
                r["in_use"] = r["key"] in novel_keys
            elif r["kind"] == "anime":
                r["in_use"] = r["key"] in anime_keys
            else:
                r["in_use"] = r["key"] in comic_keys and lib.js_source_enabled(r["key"])
        return render_template(
            "rank.html", rows=rows, summary=summary, checked_at=checked
        )

    @app.get("/api/sourcehealth/status")
    def api_health_status():
        with lock:
            st = dict(health_state)
        st["ok"] = True
        return jsonify(st)

    @app.post("/api/sourcehealth/run")
    def api_health_run():
        from ..health import run_all

        raw = request.form.get("kinds") or "novel,comic,anime"
        kinds = [k for k in raw.split(",") if k.strip() in ("novel", "comic", "anime")]
        if not kinds:
            kinds = ["novel", "comic", "anime"]
        with lock:
            if health_state.get("running"):
                return _jstate()
            health_state.update(running=True, done=0, total=0, error="", started=time.time())

        def on_progress(p: dict) -> None:
            with lock:
                health_state.update(done=p.get("done", 0), total=p.get("total", 0))

        def work() -> None:
            try:
                res = run_all(cfg, lib, kinds=kinds, on_progress=on_progress)
                with lock:
                    health_state.update(
                        running=False,
                        done=res["total"],
                        total=res["total"],
                        ok=res["ok"],
                        fail=res["fail"],
                        error="",
                    )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    health_state.update(running=False, error=str(exc)[:200])

        threading.Thread(target=work, daemon=True).start()
        with lock:
            return _jstate()

    @app.post("/api/sourcehealth/retest")
    def api_health_retest():
        """只重测一个源：排行榜每行「重测」按钮用，避免为一行重跑全部。"""
        from ..health import probe_one

        kind = (request.form.get("kind") or "").strip()
        key = (request.form.get("key") or "").strip()
        name = (request.form.get("name") or "").strip()
        keyword = (request.form.get("keyword") or "").strip()
        if kind not in ("novel", "comic", "anime") or not key:
            return jsonify(ok=False, error="参数不对"), 400
        try:
            rec = probe_one(cfg, kind, key, keyword or None, name)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)[:200]), 500
        try:
            lib.upsert_source_health(rec)
        except Exception:  # noqa: BLE001
            pass
        return jsonify(ok=True, result=rec)

    # ---------------- 源管理 ----------------
    @app.get("/sources")
    def sources_page():
        from ..sources.venera_js import VeneraRegistry

        vreg = VeneraRegistry(cfg)
        installed = {e.key: p for e, p in vreg.installed()}
        rows = [
            {
                "key": e.key,
                "name": e.name,
                "version": e.version,
                "installed": e.key in installed,
                "enabled": lib.js_source_enabled(e.key),
            }
            for e in vreg.index()
        ]
        return render_template(
            "sources.html",
            rows=rows,
            installed_count=len(installed),
            enabled_count=sum(1 for r in rows if r["enabled"]),
        )

    @app.post("/sources/toggle")
    def sources_toggle():
        key = (request.form.get("key") or "").strip()
        enabled = request.form.get("enabled") == "1"
        if key:
            lib.set_js_source_enabled(key, enabled)
        return redirect(url_for("sources_page"))

    @app.post("/api/sourcehealth/prune")
    def api_health_prune():
        """把「在用但不可用」的源从聚合默认列表里摘掉（漫画源同时禁用）。"""
        rows = lib.list_source_health()
        novel_keys = {"linovel", "linovelib", "local"}
        anime_keys = list(getattr(cfg, "anime_sources_default", None) or [])
        comic_keys = list(getattr(cfg, "comic_sources_default", None) or [])
        removed = []

        for r in rows:
            if r["ok"] or r["kind"] == "novel":
                continue
            if r["kind"] == "anime" and r["key"] in anime_keys:
                anime_keys.remove(r["key"])
                removed.append(f"动漫 {r['name']}")
            elif r["kind"] == "comic" and r["key"] in comic_keys:
                comic_keys.remove(r["key"])
                lib.set_js_source_enabled(r["key"], False)
                removed.append(f"漫画 {r['name']}")

        if removed:
            cfg.anime_sources_default = anime_keys
            cfg.comic_sources_default = comic_keys
            cfg.save()
        return jsonify(ok=True, removed=removed, anime=anime_keys, comic=comic_keys)

    @app.post("/sources/install")
    def sources_install():
        from ..sources.venera_js import VeneraRegistry

        key = (request.form.get("key") or "").strip()
        try:
            VeneraRegistry(cfg).install(key)
            flash(f"已安装源 {key}")
        except Exception as exc:  # noqa: BLE001
            flash(f"安装失败：{str(exc)[:150]}")
        return redirect(url_for("sources_page"))

    # ---------------- 聊天：宫城 + 六位管家 ----------------
    # 宫城沿用 QQ 那边配好的 deepseek；六位管家走智谱 glm-4-flash（免费）。
    # 每个人的 system prompt 里都塞了一段从 SQLite 现算的真实数据，见 novelhub/personas.py。
    from .. import personas as cast
    from ..chat import ChatEngine

    chat_engine = ChatEngine(cfg)
    app.extensions["chat"] = chat_engine

    def _chat_ready_map() -> dict:
        out = {}
        for prov in {p.provider for p in cast.PERSONAS.values()}:
            try:
                out[prov] = chat_engine.conf(prov).ready
            except Exception:  # noqa: BLE001
                out[prov] = False
        return out

    def _sse(event: str, payload) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @app.get("/chat")
    def chat_page():
        who = cast.get(request.args.get("who")).id
        persona = cast.PERSONAS[who]
        ready = _chat_ready_map()
        conf = chat_engine.conf(persona.provider)
        return render_template(
            "chat.html",
            persona=persona,
            who=who,
            conf=conf,
            ready=ready.get(persona.provider, False),
            roster=cast.roster(lib, cfg, ready),
            greeting=persona.hello(lib, cfg),
            chips=cast.CHIPS.get(who, []),
        )

    @app.get("/api/chat/info")
    def api_chat_info():
        who = cast.get(request.args.get("who")).id
        persona = cast.PERSONAS[who]
        conf = chat_engine.conf(persona.provider)
        return jsonify(
            ok=True, ready=conf.ready, who=persona.id, name=persona.name,
            model=conf.model, provider=persona.provider, source=conf.source,
            tools=conf.tools_enabled, status=persona.hello(lib, cfg),
            hint="" if conf.ready else (
                "还没配模型 API。去「设置 → 聊天模型」填 base_url / api_key / model，"
                "任何 OpenAI 兼容接口都可以。"),
            roster=cast.roster(lib, cfg, _chat_ready_map()),
        )

    # ---------------- 设置 ----------------
    @app.get("/settings")
    def settings_page():
        from .. import __version__
        from ..chat import load_chat_config, provider_catalog
        from ..config import CONFIG_PATH

        chat_conf = load_chat_config(cfg)
        try:
            stats = lib.stats()
        except Exception:  # noqa: BLE001
            stats = {"books": 0, "chapters": 0}
        return render_template(
            "settings.html",
            chat=chat_conf,
            chat_ready=chat_conf.ready,
            chat_provider=(getattr(cfg, "chat", None) or {}).get("provider") or "",
            chat_use_env=bool((getattr(cfg, "chat", None) or {}).get("use_env_key") is True),
            providers=provider_catalog(),
            net=cfg,
            stats=stats,
            version=__version__,
            data_dir=str(cfg.data_path),
            config_path=str(CONFIG_PATH),
            saved=request.args.get("saved") or "",
            error=request.args.get("error") or "",
        )

    @app.post("/settings")
    def settings_save():
        section = (request.form.get("section") or "").strip()
        chat_cfg = dict(getattr(cfg, "chat", None) or {})

        if section == "chat":
            # 空 Key 表示「不修改已保存的」，避免每次保存都要重输
            api_key = (request.form.get("api_key") or "").strip()
            chat_cfg["base_url"] = (request.form.get("base_url") or "").strip().rstrip("/")
            chat_cfg["model"] = (request.form.get("model") or "").strip()
            chat_cfg["provider"] = (request.form.get("preset") or "").strip()
            chat_cfg["use_env_key"] = bool(request.form.get("use_env_key"))
            if api_key:
                chat_cfg["api_key"] = api_key
            elif "api_key" not in chat_cfg:
                chat_cfg["api_key"] = ""
            for k, cast_fn in (("temperature", float), ("max_tokens", int)):
                raw = (request.form.get(k) or "").strip()
                if raw:
                    try:
                        chat_cfg[k] = cast_fn(raw)
                    except ValueError:
                        pass
            cfg.chat = chat_cfg
            cfg.save()
            # chat_engine.conf() 每次现读 config.json，不用重载
            return redirect(url_for("settings_page", saved="聊天模型已保存"))

        if section == "fetch":
            proxy = (request.form.get("proxy") or "").strip()
            cfg.proxy = "auto" if proxy == "auto" else proxy
            for k, cast_fn in (("delay", float), ("jitter", float)):
                raw = (request.form.get(k) or "").strip()
                if raw:
                    try:
                        setattr(cfg, k, cast_fn(raw))
                    except ValueError:
                        pass
            cfg.localize_images = bool(request.form.get("localize_images"))
            cfg.convert_to_simplified = bool(request.form.get("convert_to_simplified"))
            cfg.save()
            return redirect(url_for("settings_page", saved="抓取设置已保存"))

        return redirect(url_for("settings_page", error="未知的设置分区"))

    @app.post("/api/chat/test")
    def api_chat_test():
        """只验证「地址通 / Key 认 / 模型名存在」，不消耗对话额度。"""
        from ..chat import probe_model

        body = request.get_json(silent=True) or {}
        return jsonify(
            probe_model(
                base_url=(body.get("base_url") or "").strip(),
                api_key=(body.get("api_key") or "").strip(),
                model=(body.get("model") or "").strip(),
            )
        )

    @app.post("/api/chat/reset")
    def api_chat_reset():
        body = request.get_json(silent=True) or {}
        who = cast.get(body.get("who")).id
        chat_engine.reset(f"{who}|{str(body.get('sid') or 'web')[:64]}")
        return jsonify(ok=True, who=who)

    @app.post("/api/chat")
    def api_chat():
        body = request.get_json(silent=True) or {}
        text = str(body.get("text") or "").strip()
        sid = str(body.get("sid") or "web")[:64]
        who = cast.get(body.get("who")).id
        if not text:
            return jsonify(ok=False, error="空消息"), 400
        if len(text) > 4000:
            text = text[:4000]

        def gen():
            try:
                for ev, payload in chat_engine.reply_stream(sid, text, lib=lib, who=who):
                    yield _sse(ev, payload)
            except Exception as exc:  # noqa: BLE001
                yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"[:300]})
            yield ": end\n\n"

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/sw.js")
    def service_worker():
        """Service Worker 必须从根路径发，否则默认作用域只有 /static/。

        放在 static/sw.js 再注册 scope:'/' 会报
        「scope is not under the max scope allowed」，装到主屏后
        离线页与页面缓存全部失效。这里直接从根发，并补 Service-Worker-Allowed。
        """
        from flask import send_from_directory

        resp = send_from_directory(app.static_folder, "sw.js", mimetype="text/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/healthz")
    def healthz():
        """桌面启动器用它判断「服务真的起来了」，别在模板没加载完时就开窗口。"""
        from .. import __version__

        try:
            books = lib.stats().get("books")
        except Exception:  # noqa: BLE001
            books = None
        return {"ok": True, "app": "firefly-library", "version": __version__, "books": books}

    @app.errorhandler(404)
    def nf(_):
        return render_template("404.html"), 404

    return app


def serve(host: str = "127.0.0.1", port: int = 8899, debug: bool = False) -> None:
    app = create_app()
    print(f"阅读器已启动：http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
