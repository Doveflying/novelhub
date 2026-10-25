"""Agent 伙伴：聊天 + 帮用户查本地书库。

设计取舍：

* **模型 API 由用户自己配。** 程序不预置任何 Key、不内置任何厂商凭据，
  也不去读别处（比如某个 QQ bot）的配置。用户在「设置 → 聊天模型」里
  填 base_url / api_key / model 即可，任何 OpenAI 兼容接口都能用
  （DeepSeek、智谱、Kimi、Ollama、vLLM、one-api 之类都行）。
  没配好时聊天页会显示引导，不会静默失败。
* **本地数据默认进上下文。** 每轮都会把书库快照（书目、在读进度、
  历史摘要）拼进 system prompt，模型不用先调工具就知道你书架上有什么；
  需要正文细节时再走 ``search_shelf`` 等工具。
* **工具只读 + 一个写。** 查书架、查在线源、看进度都是只读的；只有
  ``add_to_shelf`` 会改库，且必须拿到 (source, source_id) 才动手 —— 模型得先从
  搜索结果里挑出具体那一本。
* **流式。** 用 OpenAI 兼容的 ``stream: true``，边算边推给前端，
  工具调用也从 delta 里累积。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

# 找不到任何配置时给一段通用人设，至少能用
DEFAULT_PERSONA = (
    "你是「小库」，一个住在本地书库里的阅读伙伴。\n\n"
    "你是什么样的人：\n"
    "- 话不多，但问到书的事会很专业。\n"
    "- 对书架上的书很熟，能记住谁读到哪一章。\n"
    "- 不装可爱，也不摆架子。\n\n"
    "你怎么说话：\n"
    "- 用「我」自称。\n"
    "- 句子短，不用 Markdown 标题和代码块。\n"
    "- 需要查东西就先去查，查完再回答，别猜。\n\n"
    "硬性要求：\n"
    "- 必须真的回答问题，不要空洞的客套话。\n"
    "- 默认不超过 150 字。\n"
    "- 不知道就说不知道，别硬编。"
)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _win_env(name: str) -> str:
    """从 Windows 注册表读用户级环境变量。

    QQ bot 的 config.json 里 apiKey 写的是 ``${DEEPSEEK_API_KEY}``，值是用户级
    环境变量。Flask 服务的父进程可能是在这个变量设置之前启动的，inherited env
    里没有它 —— 所以进程环境找不到时回落到注册表，不然要重启终端才生效。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val or "")
    except Exception:  # noqa: BLE001
        return ""


def expand_env(value: str) -> str:
    """把 ``${VAR}`` 换成实际值；找不到就返回空串。"""

    def sub(m: re.Match) -> str:
        name = m.group(1)
        return os.environ.get(name) or _win_env(name) or ""

    return _ENV_RE.sub(sub, value or "")


# ---------------------------------------------------------------- 配置


@dataclass
class ChatConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 1.0
    max_tokens: int = 800
    timeout: int = 60
    persona: str = ""
    persona_name: str = ""
    history_max: int = 20
    tools_enabled: bool = True
    source: str = ""          # 配置从哪来：user（用户自己填的） / env / default
    provider: str = ""        # 用户自定义的服务商标识（不影响协议）

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


# 只给「默认端点 + 默认模型名」做参考，**不含任何 Key**。
# 用户选一个就能少填两栏，但 Key 必须自己给（或用环境变量）。
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "max_tokens": 900,
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "max_tokens": 500,
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "max_tokens": 800,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "max_tokens": 800,
    },
    "ollama": {
        "label": "Ollama（本地）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "max_tokens": 800,
    },
    "custom": {
        "label": "自定义 / OpenAI 兼容",
        "base_url": "",
        "model": "",
        "max_tokens": 800,
    },
}


def provider_catalog() -> list[dict]:
    """给设置界面用的服务商清单（不含任何密钥）。"""
    return [
        {
            "id": pid,
            "label": spec.get("label") or pid,
            "base_url": spec.get("base_url") or "",
            "model": spec.get("model") or "",
        }
        for pid, spec in PROVIDERS.items()
    ]


def provider_names() -> list[str]:
    return list(PROVIDERS)


def probe_model(base_url: str, api_key: str, model: str, timeout: int = 20) -> dict:
    """测一次连接：地址通不通、Key 认不认、模型名存不存在。

    故意只打 ``/models``（OpenAI 兼容接口都实现），不跑一次真实补全 ——
    免得用户点一下「测试」就消耗额度。
    """
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    mdl = (model or "").strip()

    if not base:
        return {"ok": False, "message": "Base URL 没填"}
    if not key:
        return {"ok": False, "message": "API Key 没填"}
    if not base.startswith(("http://", "https://")):
        return {"ok": False, "message": "Base URL 要以 http:// 或 https:// 开头"}

    url = base + "/models"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        ids = [str(x.get("id") or "") for x in (data.get("data") or []) if isinstance(x, dict)]
        if mdl and ids and mdl not in ids:
            # 有的服务商 /models 只返回部分模型，所以不算硬错误，只提示
            return {
                "ok": True,
                "message": f"连接正常，但模型列表里没有 {mdl}（可能仍可用）",
                "models": ids[:8],
            }
        return {"ok": True, "message": f"连接正常，可用模型 {len(ids)} 个" if ids else "连接正常"}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(500).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        hint = {
            401: "Key 不对或已失效",
            403: "Key 没有这个模型的权限",
            404: "Base URL 路径不对（多半少了 /v1）",
            429: "被限流了，稍后再试",
        }.get(exc.code, f"HTTP {exc.code}")
        return {"ok": False, "message": f"{hint}" + (f"：{detail[:120]}" if detail else "")}
    except urllib.error.URLError as exc:
        return {"ok": False, "message": f"连不上：{exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:160]}


def load_chat_config(cfg=None, provider: str = "") -> ChatConfig:
    """读聊天模型配置。

    优先级（后者只补空位，不覆盖前者）：

    1. ``config.json`` 的 ``chat.base_url / api_key / model``
       —— 用户在「设置」里填的，**唯一的一等公民**
    2. 老配置兼容：``chat.providers.<provider>`` 段
    3. 环境变量 ``DEEPSEEK_API_KEY`` / ``ZHIPU_API_KEY`` 等（可选，方便脚本化）
    4. 内置默认只提供 base_url 与模型名，**不提供 Key**

    不再去读任何外部程序（比如某个 QQ bot）的配置文件 ——
    那是某台机器上的个人布局，不该成为产品的默认行为。
    """
    out = ChatConfig(provider=provider)
    local = getattr(cfg, "chat", None) if cfg is not None else None
    if not isinstance(local, dict):
        local = {}
    tokens_set = False

    def put(key, value, cast=None):
        nonlocal tokens_set
        if value in (None, ""):
            return
        setattr(out, key, cast(value) if cast else value)
        if key == "max_tokens":
            tokens_set = True

    # 1. 用户自己填的（顶层）
    for k in ("base_url", "api_key", "model", "persona", "persona_name"):
        put(k, expand_env(str(local.get(k) or "")))
    for k in ("temperature", "max_tokens", "timeout", "history_max"):
        if local.get(k) is not None:
            put(k, local[k], type(getattr(out, k)))
    if out.base_url or out.api_key or out.model:
        out.source = "user"

    # 2. 老配置：providers 段（按 provider 取，或用户当前选中的那个）
    provs = local.get("providers")
    if isinstance(provs, dict):
        pick = provider or str(local.get("provider") or "")
        d = provs.get(pick) if pick else None
        if not isinstance(d, dict) and len(provs) == 1:
            d = next(iter(provs.values()))
        if isinstance(d, dict):
            put("base_url", expand_env(str(d.get("base_url") or "")))
            put("api_key", expand_env(str(d.get("api_key") or "")))
            put("model", expand_env(str(d.get("model") or "")))
            for k in ("temperature", "max_tokens", "timeout", "history_max"):
                if d.get(k) is not None:
                    put(k, d[k], type(getattr(out, k)))
            if not out.source and (out.base_url or out.api_key or out.model):
                out.source = "user"

    # 3. 环境变量兜底 —— **默认关闭**
    #
    #    为什么默认关：机器上常常有别的程序留下的 DEEPSEEK_API_KEY /
    #    ZHIPU_API_KEY（比如某个 QQ bot 配过）。如果这里默默捡起来用，
    #    就等于「程序带着别人的密钥在跑」，用户既不知情也没授权，
    #    换台机器还会莫名其妙失效。
    #    想要就用 chat.use_env_key = true 显式打开。
    if not out.api_key and local.get("use_env_key") is True:
        pid = provider or str(local.get("provider") or "") or "deepseek"
        env_name = {
            "deepseek": "DEEPSEEK_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(pid, "")
        if env_name:
            v = os.environ.get(env_name) or _win_env(env_name)
            if v:
                out.api_key = v
                if not out.source:
                    out.source = "env"

    # 4. 内置只补 base_url / 模型名，不补 Key
    builtin = dict(PROVIDERS.get(provider or str(local.get("provider") or "")) or {})
    for k in ("base_url", "model"):
        if not getattr(out, k) and builtin.get(k):
            setattr(out, k, str(builtin[k]))
    if not tokens_set and builtin.get("max_tokens"):
        out.max_tokens = int(builtin["max_tokens"])

    if isinstance(local, dict):
        if "tools" in local:
            out.tools_enabled = bool(local["tools"])
        if local.get("history_max") is not None:
            out.history_max = int(local["history_max"])

    if not out.persona:
        out.persona = DEFAULT_PERSONA
        if not out.source:
            out.source = "default"
    if not out.persona_name:
        out.persona_name = _guess_name(out.persona)
    return out


def _guess_name(persona: str) -> str:
    """从「你是XXX，…」里抠出角色名，抠不到就叫「伙伴」。"""
    m = re.search(r"你是([^，,。\n]{1,12})", persona or "")
    return m.group(1).strip() if m else "伙伴"


# ---------------------------------------------------------------- 工具
#
# 每个工具：名字 / 描述 / 参数 schema / 执行函数。
# 描述写得具体一点——模型靠这段决定什么时候调用。

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_shelf",
            "description": "在本地已抓的书里搜索（书名、作者、正文都能命中）。用户问「我有没有这本书」「哪本书里提到过 X」时用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shelf_overview",
            "description": "看书库概况：几本书、几章、多少字、最近在读哪几本。用户问「我书架什么样」「有多少书」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reading_status",
            "description": "看某本书读到哪一章，或列出所有在读的书。用户问「我读到哪了」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "书 id，不填就列全部在读的"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_online",
            "description": "到在线源搜索（本地没有时才用）。kind 可选 novel / comic / anime；慢，几秒到十几秒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["novel", "comic", "anime"],
                        "description": "找小说还是漫画还是番剧，默认 novel",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_shelf",
            "description": "把一本书的目录收进书架（不抓正文）。必须先用 search_online 拿到那条结果的 url，不要凭印象编。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "搜索结果里的 url 字段"},
                    "title": {"type": "string", "description": "书名，只用于回报"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comic_status",
            "description": "看漫画书架和读到第几话。用户问「我漫画看到哪了」「书架上有哪些漫画」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anime_status",
            "description": "看番剧收藏和看到第几集。用户问「我追到第几集」「有哪些番」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_health",
            "description": "看书源体检结果：小说源/漫画源/番剧源各有多少能通，JS 源启用几个。用户问「源挂了没」「哪个源能用」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history_digest",
            "description": "看阅读历史：最近读了什么、各类各多少条。用户问「我最近在看什么」「读过哪些」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["novel", "comic", "anime"],
                        "description": "只看某一类，不填就是全部",
                    },
                },
            },
        },
    },
]


def run_tool(name: str, args: dict, *, lib, cfg) -> dict:
    """执行一个工具，返回给模型看的 dict（尽量短，省 token）。"""
    args = args or {}
    try:
        if name == "search_shelf":
            q = str(args.get("query") or "").strip()
            if not q:
                return {"error": "query 不能为空"}
            books = [dict(r) for r in lib.list_books()]
            ql = q.lower()
            hit_books = [
                {"book_id": b["id"], "title": b["title"], "author": b["author"],
                 "done": b["n_done"], "total": b["n_chapters"]}
                for b in books
                if ql in (b["title"] or "").lower() or ql in (b["author"] or "").lower()
            ][:8]
            text_hits = []
            try:
                for h in lib.search(q, limit=5) or []:
                    text_hits.append({
                        "book_id": h["book_id"], "book": h["book_title"],
                        "chapter": h["title"], "idx": h["idx"],
                        "snippet": (h.get("snippet") or "")[:60],
                    })
            except Exception:  # noqa: BLE001
                pass
            return {"books": hit_books, "in_text": text_hits,
                    "count": len(hit_books) + len(text_hits)}

        if name == "shelf_overview":
            st = lib.stats()
            rows = [dict(r) for r in lib.list_books()][:6]
            return {
                "books": st.get("books", 0),
                "chapters": st.get("chapters", 0),
                "words": st.get("words", 0),
                "recent": [{"book_id": r["id"], "title": r["title"],
                            "done": r["n_done"], "total": r["n_chapters"]} for r in rows],
            }

        if name == "reading_status":
            bid = args.get("book_id")
            if bid:
                b = lib.get_book(int(bid))
                if not b:
                    return {"error": f"没有 id={bid} 的书"}
                p = lib.get_progress(int(bid))
                idx = int((p["chapter_idx"] if p else 0) or 0)
                ch = lib.get_chapter(int(bid), idx)
                total, done = lib.chapter_count(int(bid))
                return {"book_id": int(bid), "title": b["title"],
                        "at_chapter": (ch["title"] if ch else ""),
                        "idx": idx, "stored": done, "total": total}
            prog = []
            for r in lib.list_books():
                p = lib.get_progress(r["id"])
                if p and r["n_done"]:
                    prog.append({"book_id": r["id"], "title": r["title"],
                                 "done": r["n_done"], "total": r["n_chapters"],
                                 "at": int(p["chapter_idx"] or 0)})
            return {"reading": prog[:8]}

        if name == "search_online":
            from .aggregate import aggregate

            q = str(args.get("query") or "").strip()
            kind = str(args.get("kind") or "novel")
            if kind not in ("novel", "comic", "anime"):
                kind = "novel"
            if not q:
                return {"error": "query 不能为空"}
            res = aggregate(cfg, q, kinds=[kind], lib=lib, timeout=20)
            key = {"novel": "novels", "comic": "comics", "anime": "animes"}[kind]
            rows = []
            for r in (res.get(key) or [])[:8]:
                rows.append({
                    "source": r.get("source"), "source_id": r.get("source_id"),
                    "title": r.get("title"), "author": r.get("author") or "",
                })
            return {"results": rows, "errors": (res.get("errors") or [])[:3]}

        if name == "add_to_shelf":
            from .crawler import Crawler
            from .sources import build_registry

            url = str(args.get("url") or "").strip()
            if not url.startswith("http"):
                return {"error": "需要一个完整的书籍 URL（搜索结果里的 url 字段）"}
            crawler = Crawler(cfg, lib, build_registry(cfg))
            res = crawler.crawl(url, catalog_only=True)
            total, _done = lib.chapter_count(res.book_id)
            b = lib.get_book(res.book_id)
            return {"ok": True, "book_id": res.book_id,
                    "title": (b["title"] if b else (args.get("title") or res.title)),
                    "chapters": total,
                    "note": "目录已入库，正文到「抓取」页点开始"}

        if name == "comic_status":
            comics = [dict(r) for r in lib.list_comics()]
            try:
                prog = lib.all_comic_progress()
            except Exception:  # noqa: BLE001
                prog = {}
            by_key = {(c.get("source"), str(c.get("source_id"))): c for c in comics}
            reading = []
            for (src, sid), p in list(prog.items())[:6]:
                c = by_key.get((src, str(sid)))
                reading.append({"title": (c or {}).get("title") or sid,
                                "at": p["ep_title"]})
            return {
                "comics": len(comics),
                "reading": reading,
                "titles": [c.get("title") for c in comics[:8]],
            }

        if name == "anime_status":
            animes = [dict(r) for r in lib.list_animes()]
            try:
                prog = lib.all_anime_progress()
            except Exception:  # noqa: BLE001
                prog = {}
            by_key = {(a.get("source"), str(a.get("source_id"))): a for a in animes}
            watching = []
            for (src, sid), p in list(prog.items())[:6]:
                a = by_key.get((src, str(sid)))
                watching.append({"title": (a or {}).get("title") or sid,
                                 "ep": int(p["ep_idx"]) + 1,
                                 "of": p["total"] or 0})
            return {
                "animes": len(animes),
                "watching": watching,
                "titles": [a.get("title") for a in animes[:8]],
            }

        if name == "source_health":
            try:
                h = lib.source_health_summary()
            except Exception:  # noqa: BLE001
                h = {}
            out = {}
            for kind, label in (("novel", "小说源"), ("comic", "漫画源"), ("anime", "番剧源")):
                d = h.get(kind)
                out[label] = (f"{d['ok']}/{d['total']} 通" if d else "没体检过")
            try:
                out["JS源"] = (f"启用 {len(lib.enabled_js_sources())} / "
                               f"关着 {len(lib.disabled_js_sources())}")
            except Exception:  # noqa: BLE001
                pass
            return out

        if name == "history_digest":
            kind = str(args.get("kind") or "").strip() or None
            if kind not in (None, "novel", "comic", "anime"):
                kind = None
            rows = [dict(r) for r in lib.list_history(kind, limit=8)]
            return {
                "counts": {"total": lib.history_count(),
                           "novel": lib.history_count("novel"),
                           "comic": lib.history_count("comic"),
                           "anime": lib.history_count("anime")},
                "recent": [{"kind": r["kind"], "title": r["title"],
                            "at": r.get("chapter_label") or "",
                            "when": r.get("last_read_at"), "hits": r.get("hits")}
                           for r in rows],
            }

        return {"error": f"没有这个工具：{name}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


# ---------------------------------------------------------------- 会话


@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    updated: float = field(default_factory=time.time)

    def reset(self) -> None:
        self.messages.clear()
        self.updated = time.time()


class ChatEngine:
    """一个进程里管所有会话；按 (角色, 前端给的 sid) 区分。"""

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.sessions: dict[str, Session] = {}

    # ---------- 配置 ----------
    def conf(self, provider: str = "deepseek") -> ChatConfig:
        """每次现读：改 config.json / 环境变量不用重启进程。"""
        return load_chat_config(self.cfg, provider)

    def session(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            s = Session()
            self.sessions[sid] = s
        return s

    def reset(self, sid: str) -> None:
        self.session(sid).reset()

    # ---------- 主流程 ----------
    def reply_stream(self, sid: str, text: str, *, lib, who: str | None = None):
        """产出 (事件名, 数据) 元组，给 Flask 转成 SSE。

        事件：
          meta   —— 角色/模型/provider，前端拿去显示
          delta  —— 正文增量
          tool   —— 调用了什么工具（前端显示「正在数书架…」）
          error  —— 出错
          done   —— 结束，带整段文本
        """
        from . import personas as cast

        persona = cast.get(who)
        conf = self.conf(persona.provider)
        if not conf.ready:
            yield ("error", {"message": (
                f"{persona.name} 这边的模型没接通（provider={persona.provider}）："
                f"检查 config.json 的 chat.providers.{persona.provider}，"
                f"或环境变量 {'ZHIPU_API_KEY' if persona.provider == 'zhipu' else 'DEEPSEEK_API_KEY'}")})
            return

        if persona.kind == "main" and conf.persona and conf.persona.strip() != DEFAULT_PERSONA.strip():
            system = persona.base_system(lib, self.cfg, conf.persona)
        else:
            system = persona.system(lib, self.cfg)

        key = f"{persona.id}|{sid}"
        sess = self.session(key)
        sess.messages.append({"role": "user", "content": text})
        # 只保留最近 N 条，避免越聊越贵
        keep = max(2, conf.history_max)
        if len(sess.messages) > keep:
            sess.messages = sess.messages[-keep:]

        msgs = [{"role": "system", "content": system}] + sess.messages
        allowed = set(persona.tools)
        tools = ([t for t in TOOL_SPECS if t["function"]["name"] in allowed]
                 if conf.tools_enabled else None) or None

        yield ("meta", {"model": conf.model, "name": persona.name,
                        "who": persona.id, "color": persona.color,
                        "provider": persona.provider,
                        "source": conf.source, "tools": bool(tools)})

        final = ""
        for round_i in range(4):          # 最多 4 轮工具调用
            content, calls, err = yield from self._one_round(conf, msgs, tools)
            if err:
                yield ("error", {"message": err})
                return
            if calls:
                # 把助手的工具调用原样记进历史，再补工具结果
                msgs.append({"role": "assistant", "content": content or "",
                             "tool_calls": calls})
                for c in calls:
                    fn = (c.get("function") or {})
                    name = fn.get("name") or ""
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:  # noqa: BLE001
                        args = {}
                    yield ("tool", {"name": name, "args": args, "who": persona.id})
                    result = run_tool(name, args, lib=lib, cfg=self.cfg)
                    msgs.append({"role": "tool", "tool_call_id": c.get("id"),
                                 "content": json.dumps(result, ensure_ascii=False)[:4000]})
                if round_i >= 2:          # 工具调太多次就强制收尾
                    msgs.append({"role": "user",
                                 "content": "（工具够用了，直接回答我，别再调工具）"})
                continue

            final = content or ""
            break

        sess.messages.append({"role": "assistant", "content": final})
        sess.updated = time.time()
        yield ("done", {"text": final, "name": persona.name, "who": persona.id})

    # ---------- 单轮：流式请求 ----------
    def _one_round(self, conf: ChatConfig, msgs: list[dict], tools):
        """返回 (content, tool_calls, error)；正文增量通过 yield 推给调用方。

        注意：这是生成器，用 ``yield from`` 调用 —— 它会把 ("delta", …) 直接
        透传给外层，最后 return 三元组。
        """
        import requests

        body: dict[str, Any] = {
            "model": conf.model,
            "messages": msgs,
            "temperature": conf.temperature,
            "max_tokens": conf.max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = conf.base_url + "/chat/completions"
        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {conf.api_key}",
                },
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=conf.timeout,
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            return "", None, f"连不上模型服务 {url}：{type(exc).__name__}"

        if resp.status_code != 200:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message") or ""
            except Exception:  # noqa: BLE001
                detail = (resp.text or "")[:200]
            return "", None, f"模型返回 {resp.status_code}：{detail[:200]}"

        content_parts: list[str] = []
        calls: dict[int, dict] = {}
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    yield ("delta", {"text": piece})
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index") or 0)
                    slot = calls.setdefault(idx, {"id": "", "type": "function",
                                                  "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
        except Exception as exc:  # noqa: BLE001
            return "".join(content_parts), None, f"读流失败：{type(exc).__name__}"
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        call_list = [calls[k] for k in sorted(calls) if calls[k]["function"]["name"]]
        return "".join(content_parts), (call_list or None), None
