"""源健康检查：把每个源真的问一遍，按可用性/速度/结果数排行。

三类源：
- novel：轻之文库（站内实时搜索）、哔哩轻小说（本地书目索引）、本地书架
- comic：Venera 兼容 JS 源（每个 key 起一个 node 子进程）
- anime：苹果CMS 采集接口（rules.json 里的 api 规则）

评分：失败 0 分；成功按「快 + 结果多」算，方便一眼排序。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

from .config import Config
from .net import Fetcher

# 各类源用的测试关键词（挑大众、各站都该有的）
DEFAULT_KEYWORDS = {
    "novel": "天使",
    "comic": "海贼王",
    "anime": "进击的巨人",
}


def score(ok: bool, ms: int, count: int, timed_out: bool = False) -> int:
    """0~100：能用是前提，再按速度和结果数加分。超时直接判 0。"""
    if not ok or timed_out:
        return 0
    s = 60.0
    s -= min(ms / 120.0, 40.0)          # 1.2s 扣 10 分，4.8s 扣满 40
    s += min(count, 15)                  # 结果多给点分
    if count == 0:
        s -= 15.0                          # 通了但没结果，实用性差
    return max(1, min(100, int(round(s))))


def _record(kind: str, key: str, name: str, ok: bool, ms: int, count: int,
            error: str = "", timed_out: bool = False) -> dict:
    return {
        "kind": kind,
        "key": key,
        "name": name,
        "ok": ok and not timed_out,
        "ms": int(ms),
        "count": int(count),
        "error": (error or ("超时" if timed_out else ""))[:160],
        "score": score(ok, ms, count, timed_out),
        "timed_out": bool(timed_out),
    }


# 单源硬超时（秒）：超过就当不可用，别拖累整轮
PER_SOURCE_TIMEOUT = 20.0
# 动漫采集接口本身慢（wujin ~22s、guangsu ~33s），串行测并放宽超时
ANIME_PER_SOURCE_TIMEOUT = 50.0


def _with_timeout(fn, seconds: float, fallback: dict) -> dict:
    """在独立线程里跑 fn，超时返回 fallback（标 timeout）。"""
    import threading

    box: dict = {}

    def run():
        try:
            box["r"] = fn()
        except Exception as exc:  # noqa: BLE001
            fallback.update(ok=False, score=0, error=str(exc)[:160])
            box["r"] = fallback

    th = threading.Thread(target=run, daemon=True)
    t0 = time.time()
    th.start()
    th.join(seconds)
    if th.is_alive():
        fallback.update(
            ok=False,
            score=0,
            timed_out=True,
            ms=int((time.time() - t0) * 1000),
            error=f"超时（>{int(seconds)}s）",
        )
        return fallback
    return box.get("r", fallback)


# ---------------- 小说 ----------------
def probe_novel(cfg: Config, lib, keyword: str) -> list[dict]:
    out: list[dict] = []

    # 轻之文库：站内实时搜索（绕开缓存，测真实网络）
    t0 = time.time()
    try:
        from .sources.linovel import LinovelSource

        with Fetcher(cfg) as f:
            rows = LinovelSource().search(f, keyword, limit=10, use_cache=False)
        out.append(_record("novel", "linovel", "轻之文库", True, (time.time() - t0) * 1000, len(rows)))
    except Exception as exc:  # noqa: BLE001
        out.append(_record("novel", "linovel", "轻之文库", False,
                           (time.time() - t0) * 1000, 0, str(exc)))

    # 哔哩轻小说：本地索引（不联网，按书目数给分）
    t0 = time.time()
    try:
        n = lib.novel_index_count("linovelib")
        hits = lib.search_novel_index(keyword, source="linovelib", limit=10)
        out.append(_record("novel", "linovelib", "哔哩轻小说（书目索引）", True,
                           (time.time() - t0) * 1000, len(hits)))
    except Exception as exc:  # noqa: BLE001
        out.append(_record("novel", "linovelib", "哔哩轻小说（书目索引）", False,
                           (time.time() - t0) * 1000, 0, str(exc)))

    # 本地书架
    t0 = time.time()
    try:
        books = lib.list_books()
        q = keyword.lower()
        hits = [b for b in books if q in (b["title"] or "").lower()]
        out.append(_record("novel", "local", "本地书架", True,
                           (time.time() - t0) * 1000, len(hits)))
    except Exception as exc:  # noqa: BLE001
        out.append(_record("novel", "local", "本地书架", False,
                           (time.time() - t0) * 1000, 0, str(exc)))
    return out


# ---------------- 漫画 ----------------
def probe_comic(cfg: Config, key: str, path, name: str, keyword: str) -> dict:
    from .sources.venera_js import JsSource

    t0 = time.time()

    def work():
        rows = JsSource(cfg, path).search(keyword, 1, timeout=12)
        return _record("comic", key, name, True, (time.time() - t0) * 1000, len(rows or []))

    return _with_timeout(
        work,
        PER_SOURCE_TIMEOUT,
        _record("comic", key, name, False, 0, 0),
    )


# ---------------- 动漫 ----------------
def probe_anime(cfg: Config, key: str, name: str, keyword: str) -> dict:
    """动漫源测两遍：第一遍不通就换代理重试一次（慢站常是代理被占）。"""
    from .sources.anime import search as anime_search

    t0 = time.time()
    last_err = ""

    def work():
        nonlocal last_err
        for attempt in (1, 2):
            try:
                rows = anime_search(cfg, key, keyword, limit=10)
                return _record("anime", key, name, True, (time.time() - t0) * 1000, len(rows or []))
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
                if attempt == 1:
                    time.sleep(1.0)  # 给代理池一点时间轮换
        return _record("anime", key, name, False, (time.time() - t0) * 1000, 0, last_err)

    return _with_timeout(
        work,
        ANIME_PER_SOURCE_TIMEOUT,
        _record("anime", key, name, False, 0, 0),
    )


# ---------------- 单源重测 ----------------
def probe_one(cfg: Config, kind: str, key: str, keyword: Optional[str] = None,
              name: str = "") -> dict:
    """只重测一个源（排行榜里点「重测」用）。

    只测一次、结果覆盖旧记录；不写库（调用方决定）。
    """
    kw = (keyword or DEFAULT_KEYWORDS.get(kind, "")).strip() or DEFAULT_KEYWORDS.get(kind, "")
    kind = (kind or "").strip()

    if kind == "novel":
        if key == "linovel":
            t0 = time.time()
            try:
                from .sources.linovel import LinovelSource

                with Fetcher(cfg) as f:
                    rows = LinovelSource().search(f, kw, limit=10, use_cache=False)
                return _record("novel", key, name or "轻之文库", True,
                               (time.time() - t0) * 1000, len(rows))
            except Exception as exc:  # noqa: BLE001
                return _record("novel", key, name or "轻之文库", False,
                               (time.time() - t0) * 1000, 0, str(exc))
        return _record("novel", key, name or key, False, 0, 0, "该小说源不支持单独重测")

    if kind == "comic":
        from .sources.venera_js import JsSource, VeneraRegistry

        vreg = VeneraRegistry(cfg)
        hit = next(((e, p) for e, p in vreg.installed() if e.key == key), None)
        if hit is None:
            return _record("comic", key, name or key, False, 0, 0, "源未安装")
        entry, path = hit
        return probe_comic(cfg, key, path, name or entry.name, kw)

    if kind == "anime":
        from .sources.anime import ANIME_SOURCES

        nm = name or (ANIME_SOURCES[key][0] if key in ANIME_SOURCES else key)
        return probe_anime(cfg, key, nm, kw)

    return _record(kind or "?", key or "?", name or key or "?", False, 0, 0, "未知类型")


def run_all(
    cfg: Config,
    lib,
    *,
    kinds: Iterable[str] = ("novel", "comic", "anime"),
    keywords: Optional[dict] = None,
    workers: int = 4,
    timeout: float = 300.0,
    on_progress=None,
) -> dict:
    """把选中的源全测一遍，返回结果列表 + 统计。

    漫画源并发测（快），动漫采集接口串行测（并发会互相抢代理导致假超时）。
    """
    kinds = list(kinds)
    kw = {**DEFAULT_KEYWORDS, **(keywords or {})}
    results: list[dict] = []

    comic_jobs: list[tuple] = []
    anime_jobs: list[tuple] = []
    if "comic" in kinds:
        from .sources.venera_js import VeneraRegistry

        vreg = VeneraRegistry(cfg)
        seen = set()
        for entry, path in vreg.installed():
            if entry.key in seen:
                continue
            seen.add(entry.key)
            comic_jobs.append((entry.key, entry.name, path))
    if "anime" in kinds:
        from .sources.anime import ANIME_SOURCES

        for key, (name, _base, _ref) in ANIME_SOURCES.items():
            anime_jobs.append((key, name, None))

    total_jobs = (3 if "novel" in kinds else 0) + len(comic_jobs) + len(anime_jobs)
    done = 0

    def tick():
        nonlocal done
        done += 1
        if on_progress is not None:
            on_progress({"done": done, "total": max(total_jobs, 1)})

    def collect(fut_map, limit=None):
        try:
            for fut in as_completed(fut_map, timeout=limit or timeout):
                kind, key = fut_map[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = _record(kind, key or "?", key or "?", False, 0, 0, str(exc))
                if isinstance(res, list):
                    results.extend(res)
                    for _ in res:
                        tick()
                else:
                    results.append(res)
                    tick()
        except TimeoutError:
            pass

    # 第一段：小说 + 漫画，并发
    ex = ThreadPoolExecutor(max_workers=max(1, workers))
    futs: dict = {}
    try:
        if "novel" in kinds:
            futs[ex.submit(probe_novel, cfg, lib, kw["novel"])] = ("novel", None)
        for key, name, path in comic_jobs:
            futs[ex.submit(probe_comic, cfg, key, path, name, kw["comic"])] = ("comic", key)
        collect(futs)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    # 第二段：动漫，串行
    if anime_jobs:
        ex2 = ThreadPoolExecutor(max_workers=1)
        futs2: dict = {}
        try:
            for key, name, _path in anime_jobs:
                futs2[ex2.submit(probe_anime, cfg, key, name, kw["anime"])] = ("anime", key)
            collect(futs2, limit=ANIME_PER_SOURCE_TIMEOUT * len(anime_jobs) + 30.0)
        finally:
            ex2.shutdown(wait=False, cancel_futures=True)

    # 落库 + 排序
    for r in results:
        try:
            lib.upsert_source_health(r)
        except Exception:  # noqa: BLE001
            pass

    results.sort(key=lambda r: (-r["score"], r["ms"]))
    ok = [r for r in results if r["ok"]]
    return {
        "results": results,
        "ok": len(ok),
        "fail": len(results) - len(ok),
        "total": len(results),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keywords": kw,
    }
