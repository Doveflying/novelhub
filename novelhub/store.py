"""SQLite 书库：书籍 / 分卷 / 章节 / 阅读进度 / 搜索。"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    url         TEXT,
    title       TEXT NOT NULL,
    author      TEXT DEFAULT '',
    cover       TEXT DEFAULT '',
    cover_path  TEXT DEFAULT '',
    category    TEXT DEFAULT '',
    status      TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS volumes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    idx     INTEGER NOT NULL,
    title   TEXT,
    url     TEXT,
    UNIQUE(book_id, idx)
);

CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    vol_idx    INTEGER NOT NULL DEFAULT 0,
    idx        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT DEFAULT '',
    text       TEXT DEFAULT '',
    html       TEXT DEFAULT '',
    words      INTEGER DEFAULT 0,
    fetched_at TEXT,
    UNIQUE(book_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, idx);
CREATE INDEX IF NOT EXISTS idx_chapters_vol  ON chapters(book_id, vol_idx, idx);

CREATE TABLE IF NOT EXISTS progress (
    book_id     INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER DEFAULT 0,
    scroll      REAL DEFAULT 0,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS comics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    author      TEXT DEFAULT '',
    cover       TEXT DEFAULT '',
    description TEXT DEFAULT '',
    tags        TEXT DEFAULT '',
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS comic_progress (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    ep_id      TEXT,
    ep_title   TEXT DEFAULT '',
    updated_at TEXT,
    PRIMARY KEY(source, source_id)
);

CREATE TABLE IF NOT EXISTS js_sources (
    key        TEXT PRIMARY KEY,
    enabled    INTEGER DEFAULT 1,
    updated_at TEXT
);

-- 番剧：收藏 + 观看进度
CREATE TABLE IF NOT EXISTS animes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    title      TEXT NOT NULL,
    cover      TEXT DEFAULT '',
    year       TEXT DEFAULT '',
    intro      TEXT DEFAULT '',
    -- 以下为番剧专属元数据（来自 MacCMS vod_* 字段），旧库由 _migrate() 补列
    total      INTEGER DEFAULT 0,   -- 总集数 vod_total
    isend      INTEGER DEFAULT 0,   -- 1=已完结 vod_isend
    score      REAL DEFAULT 0,      -- 评分 vod_score
    remarks    TEXT DEFAULT '',     -- 更新备注 vod_remarks
    tags       TEXT DEFAULT '',     -- 分类标签 vod_class
    area       TEXT DEFAULT '',     -- 地区 vod_area
    actor      TEXT DEFAULT '',     -- 声优 vod_actor
    director   TEXT DEFAULT '',     -- 导演 vod_director
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS anime_progress (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    ep_idx     INTEGER DEFAULT 0,
    ep_title   TEXT DEFAULT '',
    ep_url     TEXT DEFAULT '',
    road       TEXT DEFAULT '',
    total      INTEGER DEFAULT 0,
    position   REAL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY(source, source_id)
);

-- 外站书目索引（如轻小说文库的榜单/分类页扫出来的书名）
CREATE TABLE IF NOT EXISTS novel_index (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    title      TEXT NOT NULL,
    author     TEXT DEFAULT '',
    cover      TEXT DEFAULT '',
    category   TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    intro      TEXT DEFAULT '',
    seen_at    TEXT,
    PRIMARY KEY(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_novel_index_title ON novel_index(title);

-- 索引扫描游标（可断点续扫）
CREATE TABLE IF NOT EXISTS novel_index_meta (
    source     TEXT PRIMARY KEY,
    cursor     TEXT,
    total      INTEGER DEFAULT 0,
    scanned    INTEGER DEFAULT 0,
    updated_at TEXT
);

-- 源健康检查结果（排行榜用）
CREATE TABLE IF NOT EXISTS source_health (
    kind       TEXT NOT NULL,
    key        TEXT NOT NULL,
    name       TEXT DEFAULT '',
    ok         INTEGER DEFAULT 0,
    ms         INTEGER DEFAULT 0,
    count      INTEGER DEFAULT 0,
    score      INTEGER DEFAULT 0,
    error      TEXT DEFAULT '',
    checked_at TEXT,
    PRIMARY KEY(kind, key)
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 增量迁移：旧库 CREATE TABLE IF NOT EXISTS 不会补列，这里逐列检查后 ALTER
# (表名, 列名, 列定义)
MIGRATIONS: list[tuple[str, str, str]] = [
    ("animes", "total", "INTEGER DEFAULT 0"),
    ("animes", "isend", "INTEGER DEFAULT 0"),
    ("animes", "score", "REAL DEFAULT 0"),
    ("animes", "remarks", "TEXT DEFAULT ''"),
    ("animes", "tags", "TEXT DEFAULT ''"),
    ("animes", "area", "TEXT DEFAULT ''"),
    ("animes", "actor", "TEXT DEFAULT ''"),
    ("animes", "director", "TEXT DEFAULT ''"),
]


def _migrate(c: sqlite3.Connection) -> list[str]:
    """给已存在的旧库补新列。返回实际执行过的变更，便于日志。"""
    done: list[str] = []
    for table, col, decl in MIGRATIONS:
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            continue  # 表还不存在，CREATE TABLE 会建好，不需要 ALTER
        have = {r["name"] for r in rows}
        if col in have:
            continue
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            done.append(f"{table}.{col}")
        except sqlite3.OperationalError:
            pass  # 并发下另一个线程已经加过了
    if done:
        c.commit()
    return done


class Library:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self.migrations: list[str] = []
        with self.conn() as c:
            c.executescript(SCHEMA)
            self.migrations = _migrate(c)

    # ---------------- 连接 ----------------
    def conn(self) -> sqlite3.Connection:
        c: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
            self._conns.append(c)
        return c

    def close(self) -> None:
        """关闭本进程里开过的所有连接（Windows 上不关会锁住 db 文件）。"""
        for c in self._conns:
            try:
                c.close()
            except Exception:
                pass
        self._conns.clear()
        self._local = threading.local()

    # ---------------- 书籍 ----------------
    def upsert_book(self, meta: dict) -> int:
        c = self.conn()
        row = c.execute(
            "SELECT id FROM books WHERE source=? AND source_id=?",
            (meta["source"], meta["source_id"]),
        ).fetchone()
        fields = {
            "url": meta.get("url", ""),
            "title": meta.get("title") or "(未命名)",
            "author": meta.get("author", ""),
            "cover": meta.get("cover", ""),
            "category": meta.get("category", ""),
            "status": meta.get("status", ""),
            "description": meta.get("description", ""),
        }
        if row:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(
                f"UPDATE books SET {sets}, updated_at=? WHERE id=?",
                (*fields.values(), now(), row["id"]),
            )
            c.commit()
            return int(row["id"])
        cur = c.execute(
            "INSERT INTO books(source, source_id, url, title, author, cover, category,"
            " status, description, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                meta["source"],
                meta["source_id"],
                fields["url"],
                fields["title"],
                fields["author"],
                fields["cover"],
                fields["category"],
                fields["status"],
                fields["description"],
                now(),
                now(),
            ),
        )
        c.commit()
        return int(cur.lastrowid)

    def add_local_book(self, title: str, author: str = "", source_id: str | None = None) -> int:
        return self.upsert_book(
            {
                "source": "local",
                "source_id": source_id or f"local-{abs(hash(title)) % 10**12}",
                "title": title,
                "author": author,
                "url": "",
            }
        )

    def get_book(self, book_id: int) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute("SELECT * FROM books WHERE id=?", (book_id,))
            .fetchone()
        )

    def find_book(self, source: str, source_id: str) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute(
                "SELECT * FROM books WHERE source=? AND source_id=?", (source, source_id)
            )
            .fetchone()
        )

    def list_books(self) -> list[sqlite3.Row]:
        return list(
            self.conn().execute(
                """
                SELECT b.*,
                       (SELECT COUNT(*) FROM chapters ch WHERE ch.book_id=b.id) AS n_chapters,
                       (SELECT COUNT(*) FROM chapters ch WHERE ch.book_id=b.id AND ch.words>0) AS n_done,
                       (SELECT COALESCE(SUM(ch.words),0) FROM chapters ch WHERE ch.book_id=b.id) AS n_words,
                       (SELECT chapter_idx FROM progress p WHERE p.book_id=b.id) AS progress_idx
                FROM books b ORDER BY datetime(b.updated_at) DESC, b.id DESC
                """
            )
        )

    def set_cover_path(self, book_id: int, path: str) -> None:
        c = self.conn()
        c.execute("UPDATE books SET cover_path=? WHERE id=?", (path, book_id))
        c.commit()

    def delete_book(self, book_id: int) -> None:
        c = self.conn()
        c.execute("DELETE FROM books WHERE id=?", (book_id,))
        c.commit()

    def rename_book(self, book_id: int, title: str = "", author: str = "") -> None:
        c = self.conn()
        if title:
            c.execute("UPDATE books SET title=? WHERE id=?", (title, book_id))
        if author:
            c.execute("UPDATE books SET author=? WHERE id=?", (author, book_id))
        c.commit()

    # ---------------- 分卷 ----------------
    def replace_volumes(self, book_id: int, volumes: Iterable[dict]) -> None:
        c = self.conn()
        for v in volumes:
            c.execute(
                "INSERT INTO volumes(book_id, idx, title, url) VALUES(?,?,?,?)"
                " ON CONFLICT(book_id, idx) DO UPDATE SET title=excluded.title, url=excluded.url",
                (book_id, v["idx"], v.get("title", ""), v.get("url", "")),
            )
        c.commit()

    def list_volumes(self, book_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn().execute(
                "SELECT * FROM volumes WHERE book_id=? ORDER BY idx", (book_id,)
            )
        )

    # ---------------- 章节 ----------------
    def replace_catalog(self, book_id: int, chapters: Iterable[dict]) -> int:
        """写入目录（保留已有正文）。返回新增/更新条数。"""
        c = self.conn()
        n = 0
        for ch in chapters:
            c.execute(
                "INSERT INTO chapters(book_id, vol_idx, idx, title, url)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(book_id, idx) DO UPDATE SET"
                "   title=excluded.title, vol_idx=excluded.vol_idx,"
                "   url=CASE WHEN excluded.url<>'' THEN excluded.url ELSE chapters.url END",
                (
                    book_id,
                    ch.get("vol_idx", 0),
                    ch["idx"],
                    ch["title"],
                    ch.get("url", ""),
                ),
            )
            n += 1
        c.commit()
        return n

    def add_chapter(
        self,
        book_id: int,
        idx: int,
        title: str,
        text: str,
        html: str = "",
        vol_idx: int = 0,
        url: str = "",
    ) -> None:
        words = count_words(text)
        c = self.conn()
        c.execute(
            "INSERT INTO chapters(book_id, vol_idx, idx, title, url, text, html, words, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(book_id, idx) DO UPDATE SET title=excluded.title, text=excluded.text,"
            " html=excluded.html, words=excluded.words, fetched_at=excluded.fetched_at,"
            " url=CASE WHEN excluded.url<>'' THEN excluded.url ELSE chapters.url END,"
            " vol_idx=excluded.vol_idx",
            (book_id, vol_idx, idx, title, url, text, html, words, now()),
        )
        c.execute("UPDATE books SET updated_at=? WHERE id=?", (now(), book_id))
        c.commit()

    def list_chapters(self, book_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn().execute(
                "SELECT id, vol_idx, idx, title, url, words, fetched_at FROM chapters"
                " WHERE book_id=? ORDER BY idx",
                (book_id,),
            )
        )

    def get_chapter(self, book_id: int, idx: int) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute(
                "SELECT * FROM chapters WHERE book_id=? AND idx=?", (book_id, idx)
            )
            .fetchone()
        )

    def pending_ids(self, book_id: int) -> list[int]:
        return [
            int(r["idx"])
            for r in self.conn().execute(
                "SELECT idx FROM chapters WHERE book_id=? AND (text IS NULL OR text='') ORDER BY idx",
                (book_id,),
            )
        ]

    def chapter_count(self, book_id: int) -> tuple[int, int]:
        r = (
            self.conn()
            .execute(
                "SELECT COUNT(*) n, SUM(CASE WHEN words>0 THEN 1 ELSE 0 END) d"
                " FROM chapters WHERE book_id=?",
                (book_id,),
            )
            .fetchone()
        )
        return int(r["n"] or 0), int(r["d"] or 0)

    # ---------------- 进度 ----------------
    def set_progress(self, book_id: int, chapter_idx: int, scroll: float = 0.0) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO progress(book_id, chapter_idx, scroll, updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(book_id) DO UPDATE SET chapter_idx=excluded.chapter_idx,"
            " scroll=excluded.scroll, updated_at=excluded.updated_at",
            (book_id, chapter_idx, scroll, now()),
        )
        c.commit()

    def get_progress(self, book_id: int) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute("SELECT * FROM progress WHERE book_id=?", (book_id,))
            .fetchone()
        )

    # ---------------- 搜索 ----------------
    def search(self, q: str, limit: int = 50) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return []
        like = "%" + re.sub(r"([%_\\])", r"\\\1", q) + "%"
        rows = self.conn().execute(
            """
            SELECT b.id AS book_id, b.title AS book_title, ch.idx, ch.title, ch.vol_idx,
                   substr(replace(ch.text, char(10), ' '), 1, 4000) AS body,
                   ch.words
            FROM chapters ch JOIN books b ON b.id = ch.book_id
            WHERE ch.title LIKE ? ESCAPE '\\'
               OR ch.text  LIKE ? ESCAPE '\\'
            ORDER BY b.id DESC, ch.idx
            LIMIT ?
            """,
            (like, like, limit),
        )
        out = []
        for r in rows:
            body = r["body"] or ""
            pos = body.find(q)
            if pos < 0:
                snippet = body[:160]
            else:
                start = max(0, pos - 60)
                snippet = ("…" if start else "") + body[start : start + 180] + "…"
            out.append(
                {
                    "book_id": r["book_id"],
                    "book_title": r["book_title"],
                    "idx": r["idx"],
                    "title": r["title"],
                    "snippet": snippet,
                }
            )
        return out

    def stats(self) -> dict:
        c = self.conn()
        b = c.execute("SELECT COUNT(*) n FROM books").fetchone()["n"]
        cm = c.execute("SELECT COUNT(*) n FROM comics").fetchone()["n"]
        ch = c.execute("SELECT COUNT(*) n, COALESCE(SUM(words),0) w FROM chapters").fetchone()
        return {"books": b, "comics": cm, "chapters": ch["n"], "words": ch["w"]}

    # ---------------- 漫画（与小说分开的表） ----------------
    def upsert_comic(self, meta: dict) -> int:
        c = self.conn()
        row = c.execute(
            "SELECT id FROM comics WHERE source=? AND source_id=?",
            (meta["source"], meta["source_id"]),
        ).fetchone()

        # 各源返回的 tags 可能是 list / dict / 字符串，统一成逗号串
        raw_tags = meta.get("tags") or []
        if isinstance(raw_tags, dict):
            tag_list = [str(v) for v in raw_tags.values() if v]
        elif isinstance(raw_tags, (list, tuple, set)):
            tag_list = [str(t) for t in raw_tags if t]
        else:
            tag_list = [str(raw_tags)] if raw_tags else []

        fields = {
            "title": meta.get("title") or "(未命名)",
            "author": str(meta.get("author") or "")[:120],
            "cover": str(meta.get("cover") or "")[:500],
            "description": str(meta.get("description") or "")[:2000],
            "tags": ",".join(tag_list)[:200],
        }
        if row:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(
                f"UPDATE comics SET {sets}, updated_at=? WHERE id=?",
                (*fields.values(), now(), row["id"]),
            )
            c.commit()
            return int(row["id"])
        cur = c.execute(
            "INSERT INTO comics(source, source_id, title, author, cover, description, tags,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                meta["source"], meta["source_id"], fields["title"], fields["author"],
                fields["cover"], fields["description"], fields["tags"], now(), now(),
            ),
        )
        c.commit()
        return int(cur.lastrowid)

    def list_comics(self) -> list[sqlite3.Row]:
        return list(
            self.conn().execute(
                "SELECT * FROM comics ORDER BY datetime(updated_at) DESC, id DESC"
            )
        )

    def find_comic(self, source: str, source_id: str) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute("SELECT * FROM comics WHERE source=? AND source_id=?", (source, source_id))
            .fetchone()
        )

    def delete_comic(self, comic_id: int) -> None:
        c = self.conn()
        c.execute("DELETE FROM comics WHERE id=?", (comic_id,))
        c.commit()

    # ---------------- 漫画进度 ----------------
    def set_comic_progress(
        self, source: str, source_id: str, ep_id: str, ep_title: str = ""
    ) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO comic_progress(source, source_id, ep_id, ep_title, updated_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(source, source_id) DO UPDATE SET"
            " ep_id=excluded.ep_id, ep_title=excluded.ep_title, updated_at=excluded.updated_at",
            (source, str(source_id), str(ep_id), ep_title or "", now()),
        )
        c.commit()

    def get_comic_progress(self, source: str, source_id: str) -> Optional[sqlite3.Row]:
        return (
            self.conn()
            .execute(
                "SELECT * FROM comic_progress WHERE source=? AND source_id=?",
                (source, str(source_id)),
            )
            .fetchone()
        )

    def all_comic_progress(self) -> dict[tuple[str, str], sqlite3.Row]:
        rows = self.conn().execute("SELECT * FROM comic_progress").fetchall()
        return {(r["source"], str(r["source_id"])): r for r in rows}

    # ---------------- JS 源开关 ----------------
    def js_source_enabled(self, key: str) -> bool:
        row = (
            self.conn()
            .execute("SELECT enabled FROM js_sources WHERE key=?", (key,))
            .fetchone()
        )
        return True if row is None else bool(row["enabled"])

    def set_js_source_enabled(self, key: str, enabled: bool) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO js_sources(key, enabled, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
            (key, 1 if enabled else 0, now()),
        )
        c.commit()

    def enabled_js_sources(self) -> list[str]:
        rows = self.conn().execute("SELECT key FROM js_sources WHERE enabled=1").fetchall()
        return [r["key"] for r in rows]

    def disabled_js_sources(self) -> list[str]:
        rows = self.conn().execute("SELECT key FROM js_sources WHERE enabled=0").fetchall()
        return [r["key"] for r in rows]

    # ---------------- 外站书目索引 ----------------
    def upsert_novel_index(self, source: str, rows: list[dict]) -> int:
        """写入一批书目，返回本批新增/更新的条数。"""
        if not rows:
            return 0
        c = self.conn()
        ts = now()
        c.executemany(
            "INSERT INTO novel_index"
            "(source, source_id, title, author, cover, category, url, intro, seen_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(source, source_id) DO UPDATE SET"
            "   title=excluded.title, author=excluded.author, cover=excluded.cover,"
            "   category=excluded.category, url=excluded.url, intro=excluded.intro,"
            "   seen_at=excluded.seen_at",
            [
                (
                    source,
                    str(r.get("source_id", "")),
                    (r.get("title") or "").strip(),
                    (r.get("author") or "").strip(),
                    (r.get("cover") or "").strip(),
                    (r.get("category") or "").strip(),
                    (r.get("url") or "").strip(),
                    (r.get("intro") or "").strip()[:400],
                    ts,
                )
                for r in rows
                if r.get("source_id") and r.get("title")
            ],
        )
        before = c.total_changes
        c.commit()
        return max(0, c.total_changes - before) or len(rows)

    def novel_index_count(self, source: str | None = None) -> int:
        c = self.conn()
        if source:
            row = c.execute(
                "SELECT COUNT(*) n FROM novel_index WHERE source=?", (source,)
            ).fetchone()
        else:
            row = c.execute("SELECT COUNT(*) n FROM novel_index").fetchone()
        return int(row["n"]) if row else 0

    def search_novel_index(
        self,
        q: str,
        source: str | None = None,
        limit: int = 15,
        extra_terms: list[str] | None = None,
    ) -> list[dict]:
        """书名/作者/分类模糊匹配，按相关度（简洁标题优先、书名命中优先）排序。"""
        c = self.conn()
        terms = [t for t in [q.strip()] + list(extra_terms or []) if t.strip()]
        if not terms:
            return []
        where, params = [], []
        for t in terms:
            where.append("(title LIKE ? OR author LIKE ? OR category LIKE ?)")
            like = f"%{t}%"
            params += [like, like, like]
        sql = "SELECT * FROM novel_index WHERE " + " OR ".join(where)
        if source:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY length(title) ASC LIMIT ?"
        params.append(max(limit * 6, 60))
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]

        qq = q.strip().lower()

        def score(r: dict) -> tuple:
            t = (r["title"] or "").lower()
            exact = 0 if t == qq else 1
            prefix = 0 if t.startswith(qq) else 1
            contains = 0 if qq and qq in t else 1
            return (exact, prefix, contains, len(r["title"] or ""))

        rows.sort(key=score)
        return rows[:limit]

    def clear_novel_index(self, source: str | None = None) -> None:
        c = self.conn()
        if source:
            c.execute("DELETE FROM novel_index WHERE source=?", (source,))
        else:
            c.execute("DELETE FROM novel_index")
        c.commit()

    def get_index_meta(self, source: str) -> dict:
        row = self.conn().execute(
            "SELECT * FROM novel_index_meta WHERE source=?", (source,)
        ).fetchone()
        return dict(row) if row else {"source": source, "cursor": "", "total": 0, "scanned": 0}

    def set_index_meta(
        self, source: str, *, cursor: str = "", total: int = 0, scanned: int = 0
    ) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO novel_index_meta(source, cursor, total, scanned, updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor,"
            " total=excluded.total, scanned=excluded.scanned, updated_at=excluded.updated_at",
            (source, cursor, total, scanned, now()),
        )
        c.commit()

    # ---------------- 番剧 ----------------
    @staticmethod
    def _anime_fields(meta: dict) -> dict:
        """把外部 meta（可能混着 roads/eps）收敛成 animes 表的列。"""
        try:
            total = int(meta.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        try:
            score = float(meta.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        isend = meta.get("isend")
        if isend is None:
            # 没给 isend 就从备注里猜（"已完结"/"全12集" 都算完结）
            text = f"{meta.get('remarks') or ''} {meta.get('title') or ''}"
            isend = 1 if re.search(r"已完结|完结|全集|全\s*\d+\s*集", text) else 0
        return {
            "title": meta.get("title") or "(未命名)",
            "cover": meta.get("cover") or "",
            "year": meta.get("year") or "",
            "intro": (meta.get("intro") or "")[:800],
            "total": total,
            "isend": 1 if isend else 0,
            "score": score,
            "remarks": (meta.get("remarks") or "")[:200],
            "tags": (meta.get("tags") or "")[:200],
            "area": (meta.get("area") or "")[:60],
            "actor": (meta.get("actor") or "")[:200],
            "director": (meta.get("director") or "")[:120],
        }

    def upsert_anime(self, meta: dict) -> int:
        """收藏一部番（或是更新它的信息）。"""
        c = self.conn()
        row = c.execute(
            "SELECT id FROM animes WHERE source=? AND source_id=?",
            (meta["source"], str(meta["source_id"])),
        ).fetchone()
        ts = now()
        f = self._anime_fields(meta)
        if row:
            c.execute(
                "UPDATE animes SET title=?, cover=?, year=?, intro=?, total=?, isend=?,"
                " score=?, remarks=?, tags=?, area=?, actor=?, director=?, updated_at=?"
                " WHERE id=?",
                (
                    f["title"], f["cover"], f["year"], f["intro"], f["total"],
                    f["isend"], f["score"], f["remarks"], f["tags"], f["area"],
                    f["actor"], f["director"], ts, row["id"],
                ),
            )
            aid = int(row["id"])
        else:
            cur = c.execute(
                "INSERT INTO animes(source, source_id, title, cover, year, intro,"
                " total, isend, score, remarks, tags, area, actor, director,"
                " created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    meta["source"], str(meta["source_id"]), f["title"], f["cover"],
                    f["year"], f["intro"], f["total"], f["isend"], f["score"],
                    f["remarks"], f["tags"], f["area"], f["actor"], f["director"],
                    ts, ts,
                ),
            )
            aid = int(cur.lastrowid)
        c.commit()
        return aid

    def list_animes(self) -> list:
        return list(
            self.conn().execute(
                "SELECT * FROM animes ORDER BY updated_at DESC"
            ).fetchall()
        )

    def get_anime(self, aid: int):
        return self.conn().execute("SELECT * FROM animes WHERE id=?", (aid,)).fetchone()

    def find_anime(self, source: str, source_id: str):
        return self.conn().execute(
            "SELECT * FROM animes WHERE source=? AND source_id=?", (source, str(source_id))
        ).fetchone()

    def delete_anime(self, aid: int) -> None:
        c = self.conn()
        row = self.get_anime(aid)
        if row:
            c.execute(
                "DELETE FROM anime_progress WHERE source=? AND source_id=?",
                (row["source"], row["source_id"]),
            )
        c.execute("DELETE FROM animes WHERE id=?", (aid,))
        c.commit()

    def set_anime_progress(
        self,
        source: str,
        source_id: str,
        ep_idx: int,
        ep_title: str = "",
        ep_url: str = "",
        road: str = "",
        total: int = 0,
        position: float = 0.0,
    ) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO anime_progress(source, source_id, ep_idx, ep_title, ep_url, road,"
            " total, position, updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(source, source_id) DO UPDATE SET ep_idx=excluded.ep_idx,"
            " ep_title=excluded.ep_title, ep_url=excluded.ep_url, road=excluded.road,"
            " total=excluded.total, position=excluded.position, updated_at=excluded.updated_at",
            (
                source,
                str(source_id),
                int(ep_idx),
                ep_title or "",
                ep_url or "",
                str(road or ""),
                int(total or 0),
                float(position or 0.0),
                now(),
            ),
        )
        c.commit()

    def get_anime_progress(self, source: str, source_id: str):
        return self.conn().execute(
            "SELECT * FROM anime_progress WHERE source=? AND source_id=?",
            (source, str(source_id)),
        ).fetchone()

    def all_anime_progress(self) -> dict:
        rows = self.conn().execute("SELECT * FROM anime_progress").fetchall()
        return {(r["source"], str(r["source_id"])): r for r in rows}

    # ---------------- 源健康检查 ----------------
    def upsert_source_health(self, r: dict) -> None:
        c = self.conn()
        c.execute(
            "INSERT INTO source_health(kind, key, name, ok, ms, count, score, error, checked_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(kind, key) DO UPDATE SET name=excluded.name, ok=excluded.ok,"
            " ms=excluded.ms, count=excluded.count, score=excluded.score,"
            " error=excluded.error, checked_at=excluded.checked_at",
            (
                r.get("kind", ""),
                r.get("key", ""),
                r.get("name", ""),
                1 if r.get("ok") else 0,
                int(r.get("ms") or 0),
                int(r.get("count") or 0),
                int(r.get("score") or 0),
                (r.get("error") or "")[:200],
                now(),
            ),
        )
        c.commit()

    def list_source_health(self, kind: str | None = None) -> list[dict]:
        c = self.conn()
        if kind:
            rows = c.execute(
                "SELECT * FROM source_health WHERE kind=? ORDER BY score DESC, ms ASC",
                (kind,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM source_health ORDER BY kind, score DESC, ms ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def source_health_summary(self) -> dict:
        rows = self.conn().execute(
            "SELECT kind, COUNT(*) n, SUM(ok) ok, MAX(checked_at) t FROM source_health GROUP BY kind"
        ).fetchall()
        return {
            r["kind"]: {"total": r["n"], "ok": r["ok"] or 0, "checked_at": r["t"]}
            for r in rows
        }


def count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.sub(r"\s+", "", text))
