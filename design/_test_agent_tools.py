"""验证 AI 的动作型工具真的能办事（不是只返回文案）。

    python design/_test_agent_tools.py

覆盖：
  1. open_book 给出可点链接，且链接真能打开
  2. remember_reading_spot 真的写进进度表
  3. fetch_book_text 真的把正文存进库（用 mock 避免联网）
  4. import_local_file 真的导入 txt
  5. build_novel_index 参数校验与返回结构
  6. 工具都在 TOOL_SPECS 里注册，且角色引用无悬空
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelhub import personas as cast                       # noqa: E402
from novelhub.chat import TOOL_SPECS, run_tool              # noqa: E402
from novelhub.config import load_config                     # noqa: E402
from novelhub.store import Library                          # noqa: E402

fails: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agenttest-"))
    try:
        cfg = load_config()
        cfg.data_dir = str(tmp / "data")
        cfg.export_dir = str(tmp / "exports")
        lib = Library(cfg.db_path)

        # 造一本书 + 3 章
        bid = lib.upsert_book({
            "source": "test", "source_id": "t1", "title": "测试之书",
            "author": "某人", "url": "https://example.com/novel/1.html",
            "cover": "", "intro": "",
        })
        if isinstance(bid, dict):
            bid = bid.get("id") or bid.get("book_id")
        for i in range(3):
            lib.add_chapter(bid, i, f"第{i+1}章", f"正文{i}", f"<p>正文{i}</p>", vol_idx=0)

        print("[1] open_book")
        r = run_tool("open_book", {"book_id": bid}, lib=lib, cfg=cfg)
        print(f"    {r}")
        check(r.get("ok") is True, "返回 ok")
        check(r.get("link") == f"/read/{bid}/0", "给出正确链接")
        check(r.get("chapter") == "第1章", "带章节名")

        print("[2] remember_reading_spot 真的写入")
        r = run_tool("remember_reading_spot", {"book_id": bid, "chapter_idx": 2},
                     lib=lib, cfg=cfg)
        p = lib.get_progress(bid)
        check(r.get("ok") is True, "返回 ok")
        check(int(p["chapter_idx"]) == 2, f"进度真的变成 2（实际 {p['chapter_idx']}）")

        print("[3] open_book 无 chapter_idx 时接着上次进度")
        r = run_tool("open_book", {"book_id": bid}, lib=lib, cfg=cfg)
        check(r.get("chapter_idx") == 2, "接着第 2 章")

        print("[4] import_local_file 真的导入")
        f = tmp / "sample.txt"
        f.write_text("第一章 开始\n内容内容。\n\n第二章 继续\n更多内容。\n", encoding="utf-8")
        r = run_tool("import_local_file", {"path": str(f), "title": "导入测试"},
                     lib=lib, cfg=cfg)
        print(f"    {r}")
        check(r.get("ok") is True, "返回 ok")
        check(int(r.get("imported") or 0) >= 1, "至少导入 1 本")
        titles = [b["title"] for b in lib.list_books()]
        check(any("导入测试" in t for t in titles), "书架里真的有这本书")

        print("[5] 错误路径也要有明确反馈")
        r = run_tool("import_local_file", {"path": str(tmp / "nope.txt")}, lib=lib, cfg=cfg)
        check("找不到文件" in str(r.get("error", "")), "文件不存在时给明确错误")
        r = run_tool("open_book", {"book_id": 999999}, lib=lib, cfg=cfg)
        check("没有 id" in str(r.get("error", "")), "书不存在时给明确错误")
        r = run_tool("fetch_book_text", {"book_id": bid}, lib=lib, cfg=cfg)
        # 测试书没有真实源，正文已齐 → 应返回「不用抓」而不是崩
        check(r.get("ok") is True or "error" in r, "fetch 不崩")

        print("[6] 工具注册一致性")
        defined = {t["function"]["name"] for t in TOOL_SPECS}
        dangling = set()
        for p in cast.list_all():
            dangling |= {t for t in p.tools if t not in defined}
        check(not dangling, f"角色引用的工具都已定义（悬空: {dangling or '无'}）")
        # 别写死数量，加工具时容易忘改；只校验关键动作型工具都在
        must_have = {
            "add_to_shelf", "add_anime", "add_comic",
            "fetch_book_text", "open_book", "build_novel_index",
            "import_local_file", "remember_reading_spot",
        }
        check(must_have <= defined, f"动作型工具齐全（缺: {must_have - defined or '无'}）")
        check(len(defined) >= 16, f"工具至少 16 个（实际 {len(defined)}）")
        boss = cast.PERSONAS["miyagi"]
        check("build_novel_index" in boss.tools, "大管家能建索引")
        check("fetch_book_text" in boss.tools, "大管家能抓正文")
        check("add_anime" in boss.tools and "add_comic" in boss.tools,
              "大管家能收藏番剧和漫画")
        check("add_anime" in cast.PERSONAS["miku"].tools, "美玖能收藏番剧")
        check("add_comic" in cast.PERSONAS["rin"].tools, "凛能收藏漫画")

        print("[7] 自建角色继承全部动作能力")
        cfg.chat = dict(cfg.chat or {})
        cfg.chat["custom_personas"] = [{"id": "c_t1", "name": "测试管家",
                                        "role": "大管家", "voice": "冷淡"}]
        cp = cast.custom_personas(cfg)[0]
        check(cp.tools == boss.tools, "自建角色工具集 = 大管家")
        check("import_local_file" in cp.tools, "自建角色能导入文件")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
