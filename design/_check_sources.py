"""检验所有小说源：能不能连、能不能搜、耗时多少。

    python design/_check_sources.py
    python design/_check_sources.py --q 魔法
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="魔法")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    cfg = load_config()
    lib = Library(cfg.db_path)

    print(f"检验 {len(NOVEL_SOURCES)} 个小说源，关键词「{args.q}」\n")
    print(f"{'源 key':<12} {'名称':<12} {'结果':>4}  {'耗时':>7}  状态")
    print("-" * 62)

    ok = 0
    for key, name in NOVEL_SOURCES:
        t0 = time.time()
        try:
            res = aggregate(cfg, args.q, kinds=["novel"], novel_keys=[key],
                            lib=lib, timeout=args.timeout)
            el = time.time() - t0
            rows = res.get("novels") or []
            errs = res.get("errors") or []
            if errs:
                status = f"[FAIL] {errs[0][:34]}"
            elif rows:
                status = "[OK]   可用"
                ok += 1
            else:
                status = "[WARN] 能连但没结果"
            print(f"{key:<12} {name:<12} {len(rows):>4}  {el:>6.1f}s  {status}")
            if rows:
                print(f"{'':<12} 例：{rows[0].get('title', '')[:38]}")
        except Exception as exc:  # noqa: BLE001
            el = time.time() - t0
            print(f"{key:<12} {name:<12} {0:>4}  {el:>6.1f}s  [FAIL] {type(exc).__name__}: {exc}"[:100])

    print("-" * 62)
    print(f"可用 {ok}/{len(NOVEL_SOURCES)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
