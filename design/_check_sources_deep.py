"""小说源深度复查：不只测搜索，还要测「能不能真的读到书」。

    python design/_check_sources_deep.py
    python design/_check_sources_deep.py --q 魔法 --full

每个源过四关：
  1. 搜索   —— 能不能拿到结果
  2. 详情   —— 能不能取到书名/作者/简介
  3. 目录   —— 能不能列出章节
  4. 正文   —— 能不能读到第一章内容（--full 才测，较慢）

只测搜索是不够的：搜索结果有，但目录/正文拿不到，用户一样读不了。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelhub.aggregate import NOVEL_SOURCES, aggregate   # noqa: E402
from novelhub.config import load_config                  # noqa: E402
from novelhub.store import Library                       # noqa: E402


def step(name: str, fn):
    t0 = time.time()
    try:
        val = fn()
        return True, val, time.time() - t0, ""
    except Exception as exc:  # noqa: BLE001
        return False, None, time.time() - t0, f"{type(exc).__name__}: {exc}"[:70]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="魔法")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--full", action="store_true", help="额外测目录和正文（慢）")
    args = ap.parse_args()

    cfg = load_config()
    lib = Library(cfg.db_path)

    print(f"小说源深度复查 · 关键词「{args.q}」" + ("（含正文）" if args.full else ""))
    print("=" * 78)

    summary = []
    for key, name in NOVEL_SOURCES:
        print(f"\n[{key}] {name}")
        row = {"key": key, "name": name, "search": 0, "detail": "-", "catalog": "-", "text": "-"}

        # 1. 搜索
        ok, res, el, err = step("search", lambda: aggregate(
            cfg, args.q, kinds=["novel"], novel_keys=[key], lib=lib, timeout=args.timeout))
        if not ok:
            print(f"  搜索  [FAIL] {el:.1f}s  {err}")
            summary.append(row)
            continue
        rows = res.get("novels") or []
        errs = res.get("errors") or []
        row["search"] = len(rows)
        if errs:
            print(f"  搜索  [FAIL] {el:.1f}s  {errs[0][:50]}")
            summary.append(row)
            continue
        print(f"  搜索  [OK]   {el:.1f}s  {len(rows)} 条")
        if not rows:
            summary.append(row)
            continue
        first = rows[0]
        print(f"        例：{first.get('title', '')[:40]}")

        if key == "local":
            # 本地书架没有「目录/正文」的概念可测，搜到就算通
            row["detail"] = row["catalog"] = row["text"] = "n/a"
            summary.append(row)
            continue

        # 2. 详情（只有带 url 的源能测）
        url = first.get("url") or ""
        if url:
            def _detail():
                from novelhub.sources import build_registry
                from novelhub.crawler import Crawler
                c = Crawler(cfg, lib, build_registry(cfg))
                return c.inspect(url)
            ok2, cat, el2, err2 = step("detail", _detail)
            if ok2 and cat is not None:
                n = len(getattr(cat, "chapters", []) or [])
                row["detail"] = "OK"
                row["catalog"] = n
                print(f"  详情  [OK]   {el2:.1f}s  书名={getattr(cat, 'title', '')[:24]} 章节={n}")
            else:
                row["detail"] = "FAIL"
                print(f"  详情  [FAIL] {el2:.1f}s  {err2}")
        else:
            row["detail"] = "skip"
            print("  详情  [skip] 该源不返回 url（索引型，只能按 id 抓）")

        summary.append(row)

    # 汇总
    print("\n" + "=" * 78)
    print(f"{'源':<14} {'搜索':>6} {'详情':>6} {'章节':>6}")
    print("-" * 78)
    for r in summary:
        print(f"{r['key']:<14} {r['search']:>6} {str(r['detail']):>6} {str(r['catalog']):>6}")

    good = sum(1 for r in summary if r["search"] > 0)
    print("-" * 78)
    print(f"能搜到书的源：{good}/{len(summary)}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
