"""离线自检：不联网也能验证切章、入库、导出、阅读器是否正常。

用法：python selftest.py
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from novelhub.config import Config  # noqa: E402
from novelhub.export import Exporter  # noqa: E402
from novelhub.importer import Importer, split_chapters  # noqa: E402
from novelhub.store import Library  # noqa: E402

SAMPLE = """测试之书
作者：某人

第一卷 起风

第1章 开端
这是第一章的正文。
第二行文字。

第2章 发展
这是第二章。

第3章 转折
第三章内容。

第二卷 落雨

第4章 终局
最后一章。
"""


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(("  ✓ " if cond else "  ✗ ") + label + (f"  {detail}" if detail else ""))
    return cond


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        cfg = Config(data_dir=str(root / "data"), export_dir=str(root / "exports"), proxy="")
        lib = Library(cfg.db_path)

        # 1. 切章
        chs, vols, structured = split_chapters(SAMPLE)
        ok &= check("TXT 切章", structured and len(chs) == 4, f"{len(chs)} 章")
        ok &= check("卷识别", vols == {1: "第一卷 起风", 2: "第二卷 落雨"}, str(vols))
        ok &= check(
            "正文开头不被误判成标题",
            any(c[0] == "第3章 转折" for c in chs) and not any("第三章内容" == c[0] for c in chs),
        )

        # 2. 导入
        f = root / "测试之书.txt"
        f.write_text(SAMPLE, encoding="utf-8")
        imp = Importer(lib)
        bid = imp.import_path(f)[0]
        book = lib.get_book(bid)
        ok &= check("导入书籍", book is not None and book["title"] == "测试之书")
        ok &= check("作者识别", (book["author"] or "") == "某人", book["author"] or "")
        n_all, n_done = lib.chapter_count(bid)
        ok &= check("章节入库", n_all == 4 and n_done == 4, f"{n_done}/{n_all}")
        ok &= check("分卷入库", len(lib.list_volumes(bid)) == 2)

        # 3. 搜索
        hits = lib.search("第三章内容")
        ok &= check("全文搜索", len(hits) == 1, hits[0]["title"] if hits else "无结果")

        # 4. 导出
        exp = Exporter(cfg, lib)
        for fmt in ("txt", "md", "html", "epub"):
            p = exp.export(bid, fmt, download_images=False)
            size = p.stat().st_size if p.exists() else 0
            good = size > 200
            if fmt == "epub":
                with zipfile.ZipFile(p) as z:
                    good = good and any(n.endswith(".opf") for n in z.namelist())
            ok &= check(f"导出 {fmt.upper()}", good, f"{size} bytes")

        # 5. 阅读器
        try:
            from novelhub.webapp.app import create_app

            app = create_app(cfg)
            c = app.test_client()
            home = c.get("/")
            ok &= check("首页", home.status_code == 200 and "书架" in home.get_data(as_text=True))
            bk = c.get(f"/book/{bid}")
            ok &= check("目录页", bk.status_code == 200)
            rd = c.get(f"/read/{bid}/0")
            ok &= check("阅读页", rd.status_code == 200 and "chapter-body" in rd.get_data(as_text=True))
            sr = c.get("/search?q=%E7%AC%AC%E4%B8%89%E7%AB%A0")
            ok &= check("搜索页", sr.status_code == 200)
            api = c.get("/api/books").get_json()
            ok &= check("JSON API", isinstance(api, list) and len(api) >= 1)
            app.extensions["library"].close()
        except Exception as exc:  # noqa: BLE001
            ok &= check("阅读器", False, str(exc))

        # 5.5 书目索引（聚合搜索的小说源之一）
        try:
            n = lib.upsert_novel_index(
                "linovelib",
                [
                    {"source_id": "999001", "title": "测试用的天使物语", "author": "测试",
                     "url": "https://www.linovelib.com/novel/999001.html", "category": "电击文库"},
                    {"source_id": "999002", "title": "另一本转生物语", "author": "谁",
                     "url": "https://www.linovelib.com/novel/999002.html"},
                ],
            )
            ok &= check("索引写入", n >= 1, f"{n} 行")
            hits = lib.search_novel_index("天使", source="linovelib", limit=5)
            ok &= check("索引检索", any(h["source_id"] == "999001" for h in hits), f"{len(hits)} 条")
            from novelhub.aggregate import aggregate

            res = aggregate(
                cfg, "天使", kinds=["novel"], novel_keys=["linovelib", "local"],
                lib=lib, timeout=6,
            )
            ok &= check(
                "聚合搜索（小说源）",
                any(x["source"] == "linovelib" for x in res["novels"])
                and not [e for e in res["errors"] if e.startswith("linovelib")],
                f"{len(res['novels'])} 条 / 错误 {res['errors']}",
            )
            from novelhub.sources.linovelib import parse_listing

            fake = (
                '<div class="rank_d_list"><div class="rank_d_book_img">'
                '<a href="/novel/4242.html"><img data-original="https://x/cover.jpg"></a></div>'
                '<div class="rank_d_book_intro"><div class="rank_d_b_name" title="样例书">'
                '<a href="/novel/4242.html">样例书</a></div>'
                '<div class="rank_d_b_cate"><a href="/authorarticle/a.html">作者甲</a>'
                '<a>电击文库</a><a>完结</a></div></div></div>'
            )
            rows = parse_listing(fake)
            ok &= check(
                "榜单解析",
                len(rows) == 1 and rows[0]["title"] == "样例书"
                and rows[0]["author"] == "作者甲" and rows[0]["category"] == "电击文库",
                str(rows),
            )
            lib.clear_novel_index("linovelib")
        except Exception as exc:  # noqa: BLE001
            ok &= check("书目索引", False, str(exc))

        # 5.6 拷贝漫画：解密 + 解析（纯本地，不联网）
        try:
            from novelhub.sources import copy_manga as CM

            # 加密 -> 解密 往返（与网页版 contentKey 同格式）
            import os as _os

            from cryptography.hazmat.primitives import padding as _pad
            from cryptography.hazmat.primitives.ciphers import (
                Cipher as _Cipher,
                algorithms as _alg,
                modes as _modes,
            )

            key_s = CM.DEFAULT_CCT
            iv_s = "abcdefgh12345678"
            plain = '[{"url": "https://x/1.jpg"}, {"url": "https://x/2.jpg"}]'
            pd = _pad.PKCS7(128).padder()
            buf = pd.update(plain.encode("utf-8")) + pd.finalize()
            enc = _Cipher(_alg.AES(key_s.encode()), _modes.CBC(iv_s.encode())).encryptor()
            blob = iv_s + (enc.update(buf) + enc.finalize()).hex()
            back = CM.decrypt_json(blob, key_s)
            ok &= check(
                "拷贝漫画解密",
                isinstance(back, list) and back[0]["url"] == "https://x/1.jpg",
                f"{len(back)} 项",
            )

            # 详情页解析
            fake_detail = (
                '<div class="comicParticulars-title"><h6 title="样例漫画">样例漫画</h6>'
                '<img class="lazyload" data-src="https://x/c.jpg"></div>'
                '<p class="intro">简介文字</p>'
                '<span>作者：</span><span class="t"><a>某人</a></span>'
                '<span>狀態：</span><span class="t">已完結</span>'
                '<span id="dnt" style="display:none;" value="9"></span>'
                '<a href="/comic/demo/chapter/u1">開始閱讀</a>'
            )
            ok &= check(
                "拷贝漫画详情解析",
                CM._clean(CM._RE_TITLE.search(fake_detail).group(1)) == "样例漫画"
                and CM._RE_DNT.search(fake_detail).group(1) == "9"
                and CM._RE_FIRST.search(fake_detail).group(1) == "/comic/demo/chapter/u1",
                "标题/dnt/首话",
            )

            # 章节页解析（进度 + 下一话）
            fake_chap = (
                '<h4 class="header">第3話</h4>'
                '<span class="comicCount">72</span>'
                '<span>第3&nbsp;/&nbsp;72話</span>'
                '<div class="comicContent-next">\n <a href="/comic/demo/chapter/u4">下一話</a>'
            )
            ok &= check(
                "拷贝漫画章节解析",
                CM._RE_PROG.search(fake_chap).groups() == ("3", "72")
                and CM._RE_NEXT.search(fake_chap).group(1) == "/comic/demo/chapter/u4"
                and CM._clean(CM._RE_CHAP_TITLE.search(fake_chap).group(1)) == "第3話",
                "进度/下一话/标题",
            )
        except Exception as exc:  # noqa: BLE001
            ok &= check("拷贝漫画", False, str(exc))

        # 5.7 简繁转换（用 Venera APK 带的 OpenCC 词表 + zhconv）
        try:
            from novelhub.zhconvert import (backend, has_traditional,
                                            html_to_simplified, to_simplified)

            ok &= check(
                "简繁转换",
                to_simplified("進擊的巨人") == "进击的巨人"
                and to_simplified("這裡有一個問題") == "这里有一个问题"
                and to_simplified("這個世界只有我一個人") == "这个世界只有我一个人",
                "后端 " + backend(),
            )
            ok &= check(
                "繁体判定",
                has_traditional("這是繁體中文", 2) and not has_traditional("这是简体中文", 2),
                "",
            )
            ok &= check(
                "正文转换保留标签",
                html_to_simplified('<p>這是<strong>繁體</strong></p><img src="a.jpg">')
                == '<p>这是<strong>繁体</strong></p><img src="a.jpg">',
                "",
            )
        except Exception as exc:  # noqa: BLE001
            ok &= check("简繁转换", False, str(exc))

        # 5.8 阅读历史：写入 / 累加 / 列表 / 删除 / 清空
        try:
            lib.clear_history()
            lib.touch_history("novel", "h1", source="linovelib", title="历史测试书",
                              sub="某人", url="/read/1/0", chapter_label="第1章")
            lib.touch_history("novel", "h1", source="linovelib", chapter_label="第2章")
            lib.touch_history("comic", "h2", source="copy_manga", title="历史测试漫画",
                              url="/comic/copy_manga/h2/e1", chapter_label="第1话")

            rows = lib.list_history()
            novel = [r for r in rows if r["kind"] == "novel"]
            ok &= check(
                "历史写入与累加",
                len(rows) == 2 and len(novel) == 1 and novel[0]["hits"] == 2
                and novel[0]["chapter_label"] == "第2章",
                "%d 条 / novel hits=%s" % (len(rows), novel[0]["hits"] if novel else "-"),
            )
            ok &= check(
                "历史分类计数",
                lib.history_count("novel") == 1 and lib.history_count("comic") == 1
                and lib.history_count() == 2,
                "",
            )
            # bump=False 只补字段不算一次打开
            lib.touch_history("novel", "h1", source="linovelib",
                              chapter_label="第3章", bump=False)
            row = lib.list_history("novel")[0]
            ok &= check(
                "历史补字段不加次数",
                row["hits"] == 2 and row["chapter_label"] == "第3章",
                "hits=%d label=%s" % (row["hits"], row["chapter_label"]),
            )
            hid = rows[0]["id"]
            lib.delete_history(hid)
            ok &= check("历史删除单条", lib.history_count() == 1, "")
            lib.clear_history()
            ok &= check("历史清空", lib.history_count() == 0, "")
        except Exception as exc:  # noqa: BLE001
            ok &= check("阅读历史", False, str(exc))

        # 6. 清理
        lib.delete_book(bid)
        ok &= check("删除书籍", lib.get_book(bid) is None)
        lib.close()

    print("\n" + ("全部通过 ✅" if ok else "有失败项 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
