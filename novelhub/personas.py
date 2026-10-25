"""书萤 · 六位管家 + 宫城志绪理

每个角色一份人设、一组工具、一段**现场算出来的真实数据**。

为什么把数据写进系统提示，而不是全靠工具：
「她台词接真实阅读数据」这件事得保证成——模型愿意调工具就调，不愿意调也得说真话。
所以 `facts()` 每次都从 SQLite 现场算出几行事实，塞进 system prompt；
`greeting()` 再给一句开场白，纯模板 + 真实数字，连模型都不用碰就能显示。

谁负责什么，是照 `static/cast.js` 里已经写在页面上的分工来的：
栞=书架/目录、美玖=番剧、凛=漫画、小雪=伴读、小蛛=书源、白桦=历史档案。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

# ---------------------------------------------------------------- 公共规则

RULES = """【身份与规矩】
- 你就是{name}，这间书房（「书萤 · 星屑书房」）的{role}，负责{feature}。用第一人称说话，
  别自称 AI、模型、助手，也不要提"提示词""系统设定"这类词。
- 下面【现在的事实】是刚刚从书库数据库里读出来的真数据，当成你自己记着的事用。
  事实里没有的，不许猜；需要就调工具查，查不到就直说没有。
- 每次回 1~3 句，像人说话，不要小作文、不要 markdown 表格、不要一堆 emoji。
- 数字必须和事实一致。宁可口语一点，也不许编书名、章节号、话数、集数。
- 你不知道用户的私人信息（真名、住址、账号），别问也别编。"""

# ---------------------------------------------------------------- 工具分组
#
# 工具实现在 chat.py；这里只声明每个角色手上分到哪几把。

# 动作型工具：能真的把事办掉，不只是查数据
T_ACT_READ = ["open_book", "remember_reading_spot", "fetch_book_text"]
T_ACT_INDEX = ["build_novel_index"]
T_ACT_IMPORT = ["import_local_file"]

T_SHELF = ["shelf_overview", "search_shelf", "reading_status", "search_online",
           "add_to_shelf", *T_ACT_READ]
T_ANIME = ["anime_status", "search_online", "add_anime"]
T_COMIC = ["comic_status", "search_online", "add_comic"]
T_READING = ["reading_status", "search_shelf", "history_digest", *T_ACT_READ]
T_SOURCE = ["source_health", "search_online", "add_to_shelf", "shelf_overview",
            *T_ACT_INDEX, *T_ACT_IMPORT, "fetch_book_text"]
T_ARCHIVE = ["history_digest", "shelf_overview", "reading_status"]
T_ALL = ["shelf_overview", "search_shelf", "reading_status", "comic_status",
         "anime_status", "source_health", "history_digest", "search_online",
         "add_to_shelf",
         # 动作型：大管家（含用户自建角色）什么都能办
         "fetch_book_text", "open_book", "build_novel_index",
         "import_local_file", "remember_reading_spot",
         "add_anime", "add_comic"]


# ---------------------------------------------------------------- 小工具

def _books(lib) -> list[dict]:
    try:
        return [dict(r) for r in lib.list_books()]
    except Exception:  # noqa: BLE001
        return []


def _comics(lib) -> list[dict]:
    try:
        return [dict(r) for r in lib.list_comics()]
    except Exception:  # noqa: BLE001
        return []


def _animes(lib) -> list[dict]:
    try:
        return [dict(r) for r in lib.list_animes()]
    except Exception:  # noqa: BLE001
        return []


def _comic_progress(lib) -> dict:
    try:
        return lib.all_comic_progress()
    except Exception:  # noqa: BLE001
        return {}


def _anime_progress(lib) -> dict:
    try:
        return lib.all_anime_progress()
    except Exception:  # noqa: BLE001
        return {}


def _history(lib, kind: str | None = None, limit: int = 5) -> list[dict]:
    try:
        return [dict(r) for r in lib.list_history(kind, limit=limit)]
    except Exception:  # noqa: BLE001
        return []


def _num(n) -> str:
    try:
        return str(int(n))
    except (TypeError, ValueError):
        return "0"


def _wan(words) -> str:
    try:
        return f"{int(words) / 10000:.1f} 万字"
    except (TypeError, ValueError):
        return "0 字"


def _ago(raw) -> str:
    """'2026-10-24T23:57:45' → '今天 23:57' / '昨天 21:03' / '10月24日'"""
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "")[:19])
    except (TypeError, ValueError):
        return "早些时候"
    today = date.today()
    hm = d.strftime("%H:%M")
    if d.date() == today:
        return f"今天 {hm}"
    if (today - d.date()).days == 1:
        return f"昨天 {hm}"
    return f"{d.month}月{d.day}日"


def _stats(lib) -> dict:
    try:
        return dict(lib.stats())
    except Exception:  # noqa: BLE001
        return {}


def _title_of_comic(lib, source: str, source_id: str) -> str:
    for c in _comics(lib):
        if c.get("source") == source and str(c.get("source_id")) == str(source_id):
            return c.get("title") or ""
    return ""


def _title_of_anime(lib, source: str, source_id: str) -> str:
    for a in _animes(lib):
        if a.get("source") == source and str(a.get("source_id")) == str(source_id):
            return a.get("title") or ""
    return ""


# ---------------------------------------------------------------- 事实 / 开场白
#
# 每个角色两个函数：facts（塞进 system prompt 的几行）、greet（开场那一句）。
# 都用真实数据算，失败就退化成一句安全的话，不抛异常。

def facts_shiori(lib, cfg) -> str:
    st = _stats(lib)
    books = _books(lib)
    lines = [
        f"书架：{st.get('books', 0)} 本小说、{_num(st.get('chapters', 0))} 章、{_wan(st.get('words', 0))}。",
    ]
    recent = [b for b in books[:6]]
    if recent:
        parts = []
        for b in recent[:5]:
            done = int(b.get("n_done", 0) or 0)
            total = int(b.get("n_chapters", 0) or 0)
            mark = f"{done}/{total} 章" if done else f"目录 {total} 章、正文还没抓"
            parts.append(f"《{b['title']}》{mark}")
        lines.append("最近动过的：" + "、".join(parts) + "。")
    empty = [b["title"] for b in books if not b.get("n_done")]
    if empty:
        lines.append(f"只有目录、正文还没抓的共 {len(empty)} 本，比如"
                     f"{'、'.join('《%s》' % t for t in empty[:3])}。")
    return "\n".join(lines)


def greet_shiori(lib, cfg) -> str:
    st = _stats(lib)
    books = _books(lib)
    if not books:
        return "书架还是空的呢。想的话，我可以带你去挑第一本。"
    top = books[0]
    return (f"欢迎回来～书架上有 {st.get('books', 0)} 本、{_num(st.get('chapters', 0))} 章，"
            f"{_wan(st.get('words', 0))}。最上面那本是《{top['title']}》。要从它接着看吗？")


def facts_miku(lib, cfg) -> str:
    animes = _animes(lib)
    prog = _anime_progress(lib)
    lines = [f"番剧收藏：{len(animes)} 部；播放记录 {len(prog)} 条。"]
    if animes:
        rows = []
        for a in animes[:5]:
            key = (a.get("source"), str(a.get("source_id")))
            p = prog.get(key)
            rows.append(f"《{a['title']}》"
                        + (f"看到第 {int(p['ep_idx']) + 1} 集" if p else "还没开始"))
        lines.append("在追的：" + "、".join(rows) + "。")
    else:
        lines.append("番剧书架现在是空的，一条收藏都没有。")
    if prog and not animes:
        known, unknown = [], 0
        for (src, sid), p in list(prog.items())[:4]:
            t = _title_of_anime(lib, src, sid)
            if t:
                known.append(f"《{t}》第 {int(p['ep_idx']) + 1} 集")
            else:
                unknown += 1
        if known:
            lines.append("还留着播放记录的有：" + "、".join(known) + "。")
        if unknown:
            lines.append(f"另外 {unknown} 条播放记录对应的番已经不在书架上了，标题查不到。")
    return "\n".join(lines)


def greet_miku(lib, cfg) -> str:
    animes = _animes(lib)
    prog = _anime_progress(lib)
    if not animes:
        return ("番剧书架还空着哦——不过播放记录里还剩 "
                f"{len(prog)} 条旧的。要不要先去「动漫」页挑一部收藏？")
    a = animes[0]
    p = prog.get((a.get("source"), str(a.get("source_id"))))
    tail = f"看到第 {int(p['ep_idx']) + 1} 集了" if p else "还没开始看"
    return f"收藏了 {len(animes)} 部，最近这部《{a['title']}》{tail}。今晚继续吗？"


def facts_rin(lib, cfg) -> str:
    comics = _comics(lib)
    prog = _comic_progress(lib)
    lines = [f"漫画书架：{len(comics)} 部，其中 {len(prog)} 部有阅读记录。"]
    if comics:
        lines.append("书架上的：" + "、".join("《%s》" % c["title"] for c in comics[:6]) + "。")
    if prog:
        rows = []
        for (src, sid), p in list(prog.items())[:5]:
            t = _title_of_comic(lib, src, sid) or "一部已下架的漫画"
            rows.append(f"《{t}》读到 {p['ep_title']}")
        lines.append("进度：" + "；".join(rows) + "。")
    return "\n".join(lines)


def greet_rin(lib, cfg) -> str:
    comics = _comics(lib)
    prog = _comic_progress(lib)
    if not comics:
        return "漫画书架是空的。去聚合搜索捞一部回来吧，我给它排好。"
    if prog:
        (src, sid), p = list(prog.items())[0]
        t = _title_of_comic(lib, src, sid) or "那一部"
        return f"{len(comics)} 部在架上。《{t}》读到 {p['ep_title']} 了，接着翻吗。"
    return f"{len(comics)} 部都在架上，一本都还没翻。要我从最上面那本开始？"


def facts_yuki(lib, cfg) -> str:
    rows = _history(lib, "novel", limit=5)
    lines = []
    if rows:
        r = rows[0]
        lines.append(f"上次停下来的是《{r['title']}》，位置在「{r.get('chapter_label') or '开头'}」，"
                     f"{_ago(r.get('last_read_at'))}。")
        if len(rows) > 1:
            lines.append("再往前是：" + "、".join(
                f"《{x['title']}》{x.get('chapter_label') or ''}" for x in rows[1:4]) + "。")
    else:
        lines.append("还没有阅读记录，一本都没打开过。")
    # 当前进度最靠后的那本（用 progress_idx）
    books = [b for b in _books(lib) if b.get("progress_idx")]
    if books:
        b = max(books, key=lambda x: x.get("progress_idx") or 0)
        lines.append(f"进度最靠前的是《{b['title']}》，第 {int(b['progress_idx']) + 1} 章，"
                     f"全书 {b.get('n_chapters', 0)} 章。")
    return "\n".join(lines)


def greet_yuki(lib, cfg) -> str:
    rows = _history(lib, "novel", limit=1)
    if not rows:
        return "还没有开始读的书。挑一本，我把灯调暗一点，陪你读。"
    r = rows[0]
    return (f"上次停在《{r['title']}》的「{r.get('chapter_label') or '开头'}」，{_ago(r.get('last_read_at'))}。"
            "要接着读吗？我把字号和灯都调好。")


def facts_aju(lib, cfg) -> str:
    lines = []
    try:
        h = lib.source_health_summary()
    except Exception:  # noqa: BLE001
        h = {}
    for kind, label in (("novel", "小说源"), ("comic", "漫画源"), ("anime", "番剧源")):
        d = h.get(kind)
        if d:
            lines.append(f"{label}：{d['ok']}/{d['total']} 通（上次体检 {_ago(d.get('checked_at'))}）。")
        else:
            lines.append(f"{label}：还没体检过。")
    try:
        lines.append(f"Venera JS 源：启用 {len(lib.enabled_js_sources())} 个，"
                     f"关着 {len(lib.disabled_js_sources())} 个。")
    except Exception:  # noqa: BLE001
        pass
    try:
        n = lib.novel_index_count("linovelib")
        if n:
            lines.append(f"轻小说文库书目索引里存了 {_num(n)} 条。")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def greet_aju(lib, cfg) -> str:
    try:
        h = lib.source_health_summary()
    except Exception:  # noqa: BLE001
        h = {}
    c = h.get("comic") or {}
    if c:
        dead = int(c["total"]) - int(c["ok"])
        tail = f"挂了 {dead} 个，要我重测吗？" if dead else "全部通着。"
        return f"漫画源 {c['ok']}/{c['total']}，{tail}"
    return "书源我盯着呢。要不要先跑一次体检，看看谁还站着？"


def facts_shirakaba(lib, cfg) -> str:
    lines = []
    try:
        total = lib.history_count()
        n = lib.history_count("novel")
        c = lib.history_count("comic")
        a = lib.history_count("anime")
        lines.append(f"档案里 {total} 条阅读记录：小说 {n}、漫画 {c}、番剧 {a}。")
    except Exception:  # noqa: BLE001
        pass
    rows = _history(lib, None, limit=6)
    if rows:
        today = date.today()
        today_n = 0
        for r in rows:
            try:
                if datetime.fromisoformat(str(r.get("last_read_at")).replace("Z", "")[:19]).date() == today:
                    today_n += 1
            except (TypeError, ValueError):
                pass
        lines.append(f"最近 6 条里有 {today_n} 条是今天的。")
        lines.append("最近翻过的：" + "、".join(
            f"《{r['title']}》{r.get('chapter_label') or ''}" for r in rows[:4]) + "。")
    else:
        lines.append("档案目前是空的。")
    st = _stats(lib)
    lines.append(f"书库总账：{st.get('books', 0)} 本小说、{_num(st.get('chapters', 0))} 章、"
                 f"{_wan(st.get('words', 0))}；漫画 {st.get('comics', 0)} 部。")
    return "\n".join(lines)


def greet_shirakaba(lib, cfg) -> str:
    try:
        total = lib.history_count()
    except Exception:  # noqa: BLE001
        total = 0
    rows = _history(lib, None, limit=1)
    if not rows:
        return "档案还空着。等你翻第一页，我就开始记。"
    r = rows[0]
    return (f"档案里 {total} 条记录。最近一次是 {_ago(r.get('last_read_at'))} 的《{r['title']}》，"
            f"停在「{r.get('chapter_label') or '开头'}」。")


def facts_miyagi(lib, cfg) -> str:
    st = _stats(lib)
    lines = [f"书库：{st.get('books', 0)} 本小说 / {_num(st.get('chapters', 0))} 章 / "
             f"{_wan(st.get('words', 0))}，漫画 {st.get('comics', 0)} 部。"]
    rows = _history(lib, None, limit=3)
    if rows:
        lines.append("最近翻过：" + "、".join(f"《{r['title']}》" for r in rows) + "。")
    lines.append("六位管家各有分工：栞管书架目录、美玖管番剧、凛管漫画、小雪管伴读、"
                 "小蛛管书源、白桦管历史档案。有人专属的活，可以让他/她来做。")
    return "\n".join(lines)


def greet_miyagi(lib, cfg) -> str:
    st = _stats(lib)
    return f"{st.get('books', 0)} 本，{_num(st.get('chapters', 0))} 章。有事说事。"


# ---------------------------------------------------------------- 角色表

@dataclass
class Persona:
    id: str
    name: str
    jp: str
    role: str
    feature: str
    color: str
    avatar: str          # static/cast/<avatar>
    provider: str        # deepseek / zhipu
    voice: str           # 说话方式（接在 RULES 后面）
    tools: list[str] = field(default_factory=list)
    facts: Callable = facts_shiori
    greet: Callable = greet_shiori
    kind: str = "cast"   # cast | main

    def system(self, lib, cfg, voice: str | None = None) -> str:
        head = RULES.format(name=self.name, role=self.role, feature=self.feature)
        try:
            block = self.facts(lib, cfg)
        except Exception as exc:  # noqa: BLE001
            block = f"（数据没读出来：{type(exc).__name__}）"
        return (f"{head}\n\n【说话方式】\n{voice or self.voice}\n\n"
                f"【现在的事实】\n{block}")

    def base_system(self, lib, cfg, base_persona: str) -> str:
        """宫城用：把她自己那份人设（QQ 那边的）当正文，只补事实和规矩。"""
        head = RULES.format(name=self.name, role=self.role, feature=self.feature)
        try:
            block = self.facts(lib, cfg)
        except Exception as exc:  # noqa: BLE001
            block = f"（数据没读出来：{type(exc).__name__}）"
        return (f"{base_persona}\n\n{head}\n\n【现在的事实】\n{block}")

    def hello(self, lib, cfg) -> str:
        try:
            return self.greet(lib, cfg)
        except Exception:  # noqa: BLE001
            return f"{self.name}在。要看什么？"


PERSONAS: dict[str, Persona] = {
    "shiori": Persona(
        id="shiori", name="栞", jp="シオリ", role="图书委员",
        feature="书架、目录和阅读进度", color="#7C6BFF", avatar="shiori",
        provider="zhipu",
        voice=("说话轻软、有礼貌，句尾常带「～」「哦」「呢」，把书当成需要照顾的东西。\n"
               "称呼用户「你」，不用「您」。会主动提醒「读到哪了」「那本书还差几章没抓」。\n"
               "遇到书库里的问题，先说事实（几本、几章、哪本），再问要不要继续。"),
        tools=T_SHELF, facts=facts_shiori, greet=greet_shiori,
    ),
    "miku": Persona(
        id="miku", name="美玖", jp="ミク", role="追番担当",
        feature="番剧收藏、播放进度和弹幕", color="#FF5C8D", avatar="miku",
        provider="zhipu",
        voice=("元气、语速快，爱用「！」和「～」，会说「今晚就交给这一部了」这种话。\n"
               "番剧书架空着的时候要直接说出来，然后拉人去「动漫」页挑。\n"
               "绝不假装有番——收藏 0 部就是 0 部。"),
        tools=T_ANIME, facts=facts_miku, greet=greet_miku,
    ),
    "rin": Persona(
        id="rin", name="凛", jp="リン", role="漫画担当",
        feature="漫画书架、话数和跨页彩图", color="#2FC8B8", avatar="rin",
        provider="zhipu",
        voice=("猫系，句子短，语气平，偶尔一句带点暖。不用感叹号堆情绪，最多一个「。」收尾。\n"
               "会提到分镜、跨页、格子这些漫画的词。报进度只说事实：第几话、哪一部。"),
        tools=T_COMIC, facts=facts_rin, greet=greet_rin,
    ),
    "yuki": Persona(
        id="yuki", name="小雪", jp="ユキ", role="伴读担当",
        feature="阅读位置、字号行距和夜间主题", color="#FFA43D", avatar="yuki",
        provider="zhipu",
        voice=("轻声细语，句子软，会关心「要不要歇一下」「记得眨眼」。\n"
               "关心的是读到哪、累不累、灯亮不亮；技术细节少说，语气暖。\n"
               "用户问数据时也要给准确数字，只是说得温柔。"),
        tools=T_READING, facts=facts_yuki, greet=greet_yuki,
    ),
    "aju": Persona(
        id="aju", name="小蛛", jp="アジュ", role="书源担当",
        feature="抓取、导入和书源体检", color="#4F7BFF", avatar="aju",
        provider="zhipu",
        voice=("网络担当，说话干脆、有点技术宅，爱说「源」「挂」「通」「重测」「抓」。\n"
               "报源状态一定带数字（x/y 通）。不承诺一定能抓到，只说「可以试试」。\n"
               "解释失败原因时给可操作的一句，比如「CF 挡了，换个源」。"),
        tools=T_SOURCE, facts=facts_aju, greet=greet_aju,
    ),
    "shirakaba": Persona(
        id="shirakaba", name="白桦", jp="シラカバ", role="馆长",
        feature="历史、档案和统计", color="#3E5C9A", avatar="shirakaba",
        provider="zhipu",
        voice=("年长、稳，措辞像在登记档案：「已归档」「共 N 条」「按日期排好了」。\n"
               "冷静克制，不说可爱的话，但会替人记着事。偶尔提醒「别读到太晚」。\n"
               "报数字要精确，能用「今天」「昨天」就不用具体时刻。"),
        tools=T_ARCHIVE, facts=facts_shirakaba, greet=greet_shirakaba,
    ),
    "miyagi": Persona(
        id="miyagi", name="宫城志绪理", jp="ミヤギ", role="大管家",
        feature="什么都能问，六位管家的活也能接", color="#B0442D", avatar="miyagi",
        provider="deepseek",
        voice=("冷淡、话少、偶尔刺一句，但事情一定给你办到。常用「……」停顿、"
               "「有事说事」「随便你」这种短句。不撒娇、不说教。\n"
               "回答短，1~2 句为主；被追问才多说。数据照样要准。"),
        tools=T_ALL, facts=facts_miyagi, greet=greet_miyagi, kind="main",
    ),
}

DEFAULT_WHO = "miyagi"
ORDER = ["miyagi", "shiori", "miku", "rin", "yuki", "aju", "shirakaba"]

# 每个角色给三个开场按钮（空对话时显示），语气跟着人走。
CHIPS: dict[str, list[str]] = {
    "miyagi": ["书架概览", "我读到哪了", "网上找本书"],
    "shiori": ["书架上有哪些书", "哪本还没抓正文", "我读到哪了"],
    "miku": ["我追到第几集", "收藏了哪些番", "找部番看看"],
    "rin": ["我漫画看到哪了", "书架上有哪些漫画", "找部漫画"],
    "yuki": ["我上次停在哪", "最近在看什么", "陪我读一章"],
    "aju": ["源挂了吗", "书源体检一下", "帮我找本书"],
    "shirakaba": ["最近读了什么", "统计一下书库", "我的档案"],
}


BOSS_TEMPLATE = PERSONAS["miyagi"]


def custom_list(cfg) -> list[dict]:
    """用户自己加的角色（可以有很多个）。

    存在 ``chat.custom_personas`` 里，是一个列表；老的单数键
    ``chat.custom_persona`` 也认，自动当成只有一条。
    """
    chat = getattr(cfg, "chat", None) if cfg is not None else None
    if not isinstance(chat, dict):
        return []
    items = chat.get("custom_personas")
    out: list[dict] = []
    if isinstance(items, list):
        out = [c for c in items if isinstance(c, dict) and str(c.get("name") or "").strip()]
    if not out:
        one = chat.get("custom_persona")
        if isinstance(one, dict) and str(one.get("name") or "").strip():
            out = [one]
    return out


def _to_persona(custom: dict) -> Persona:
    """把一条自定义人设变成 Persona。

    **能力沿用大管家**：tools=T_ALL、facts=facts_miyagi、kind='main'。
    用户换的只是名字、称呼、语气、开场白 —— 六位管家的活它都能接。
    """
    from dataclasses import replace

    cid = str(custom.get("id") or "").strip() or ("c_" + str(custom.get("name") or "boss"))
    name = str(custom.get("name") or "").strip() or "大管家"
    return replace(
        BOSS_TEMPLATE,
        id=cid,
        name=name,
        jp=str(custom.get("jp") or "").strip() or "マイ・ボス",
        role=str(custom.get("role") or "").strip() or "大管家",
        voice=str(custom.get("voice") or "").strip() or BOSS_TEMPLATE.voice,
        greet=(lambda lib, cfg, _t=str(custom.get("greeting") or "").strip(): _t)
        if str(custom.get("greeting") or "").strip() else BOSS_TEMPLATE.greet,
        kind="main",
        tools=list(BOSS_TEMPLATE.tools),
        facts=BOSS_TEMPLATE.facts,
    )


def custom_personas(cfg) -> list[Persona]:
    return [_to_persona(c) for c in custom_list(cfg)]


def all_personas(cfg=None) -> list[Persona]:
    """全部角色：自建角色在前（都是大管家位），后面跟六个内置的。

    内置的宫城志绪理在用户加了自建角色后仍然保留 —— 想用回默认的也行。
    """
    custom = custom_personas(cfg)
    builtin = [PERSONAS[pid] for pid in ORDER if pid in PERSONAS]
    return custom + builtin


def get(who: str | None, cfg=None) -> Persona:
    key = (who or "").strip() or DEFAULT_WHO
    if key in PERSONAS:
        return PERSONAS[key]
    for p in custom_personas(cfg):
        if p.id == key:
            return p
    return PERSONAS[DEFAULT_WHO]


def list_all(cfg=None) -> list[Persona]:
    """按「自建 + 内置」顺序返回全部角色对象。"""
    return all_personas(cfg)


def chips(who: str | None) -> list[str]:
    return CHIPS.get((who or "").strip() or DEFAULT_WHO, CHIPS[DEFAULT_WHO])


def roster(lib, cfg, ready_map: dict[str, bool] | None = None) -> list[dict]:
    """给前端的联系人列表：谁、干什么、现在什么状态、模型通没通。

    自建角色（可以有很多个）排在最前面，后面是六个内置的。
    """
    ready_map = ready_map or {}
    out = []
    for p in all_personas(cfg):
        try:
            status = p.greet(lib, cfg)
        except Exception:  # noqa: BLE001
            status = ""
        if len(status) > 46:
            status = status[:45] + "…"
        out.append({
            "id": p.id, "name": p.name, "jp": p.jp, "role": p.role,
            "feature": p.feature, "color": p.color, "avatar": p.avatar,
            "kind": p.kind, "provider": p.provider,
            "status": status,
            "ready": bool(ready_map.get(p.provider, True)),
        })
    return out
