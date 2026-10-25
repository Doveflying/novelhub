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
    persona_model: str = ""   # 该角色单独指定的模型（空 = 跟随全局 model）
    model_label: str = ""     # 接入列表里那一项的显示名（给界面用）

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


# 每个服务商常见的模型名，设置页「角色选模型」的下拉从这里取。
# 只是候选，用户还能手填任意模型名（OpenAI 兼容接口都能跑）。
MODEL_CHOICES: dict[str, list[str]] = {
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "zhipu": ["glm-4-flash", "glm-4-air", "glm-4-plus", "glm-4.5-flash"],
    "moonshot": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0905-preview"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
    "ollama": ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b"],
    "custom": [],
}


def model_choices(provider: str = "", current: str = "") -> list[str]:
    """某个服务商可选的模型名；把当前值也带上，避免下拉里没有已存的模型。"""
    out = list(MODEL_CHOICES.get(provider or "", []))
    if not out:
        # 没指定服务商：把所有候选合并去重，用户自己挑
        for v in MODEL_CHOICES.values():
            for m in v:
                if m not in out:
                    out.append(m)
    cur = (current or "").strip()
    if cur and cur not in out:
        out.insert(0, cur)
    return out


def provider_catalog() -> list[dict]:
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


def pick_model_entry(local: dict, persona_id: str = "") -> dict:
    """从 ``chat.models`` 里挑出这个角色该用的那一项。

    - 角色在 ``persona_models`` 里选了一个 id → 用它
    - 没选 → 用列表第一个
    - 列表为空 → 返回 {}（走单 Key 的老逻辑）
    """
    models = local.get("models") if isinstance(local, dict) else None
    if not isinstance(models, list) or not models:
        return {}
    valid = [m for m in models if isinstance(m, dict) and str(m.get("model") or "").strip()]
    if not valid:
        return {}
    pm = local.get("persona_models")
    pm = pm if isinstance(pm, dict) else {}
    want = str(pm.get(persona_id) or "").strip() if persona_id else ""
    if want:
        for m in valid:
            if str(m.get("id") or "") == want:
                return m
        # 兼容旧格式：值直接是模型名
        for m in valid:
            if str(m.get("model") or "").strip() == want:
                return m
    return valid[0]


def generate_persona(cfg, brief: str, provider: str = "", persona_id: str = "") -> dict:
    """让模型根据用户的一句话，生成一份大管家的人设。

    返回 ``{"ok": True, "persona": {name, jp, role, voice, greeting}}``
    或 ``{"ok": False, "message": "..."}``。

    只做一次普通补全，不挂工具 —— 这不是聊天，是「写设定」。
    """
    import json as _json
    import urllib.error
    import urllib.request

    brief = (brief or "").strip()
    if not brief:
        return {"ok": False, "message": "先写一句你想要的角色"}

    conf = load_chat_config(cfg, provider, persona_id)
    if not conf.ready:
        return {"ok": False, "message": "还没有可用的模型，先去上面接一个"}

    system = (
        "你在帮用户设计一个私人书房管家角色。用户会给你一句话描述，"
        "你要据此写出一份人设。\n"
        "严格只输出一个 JSON 对象，不要 markdown、不要解释、不要代码块，字段：\n"
        '{"name": "角色名（2~6字，中文）", "jp": "假名或罗马音（可空）", '
        '"role": "身份称呼（4~8字，如 私人秘书）", '
        '"voice": "说话方式（60~120字，具体到语气、句长、口头禅、称呼用户的方式）", '
        '"greeting": "开场白（一句，20~40字，用这个角色的口吻）"}\n'
        "要求：声音要具体、有辨识度，不要写成「温柔体贴」这种空话；"
        "不要自称 AI 或助手。"
    )
    body = {
        "model": conf.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": brief},
        ],
        "temperature": 0.9,
        "max_tokens": 700,
        "stream": False,
    }
    url = conf.base_url + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=_json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {conf.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=conf.timeout) as r:
            data = _json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        hint = {401: "Key 不对", 403: "Key 没权限", 404: "Base URL 路径不对",
                429: "被限流了"}.get(exc.code, f"HTTP {exc.code}")
        return {"ok": False, "message": f"{hint}" + (f"：{detail}" if detail else "")}
    except urllib.error.URLError as exc:
        return {"ok": False, "message": f"连不上：{exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:160]}

    try:
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:  # noqa: BLE001
        text = ""

    # 模型有时会包一层 ```json
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group(0)
    try:
        got = _json.loads(text)
    except Exception:  # noqa: BLE001
        return {"ok": False, "message": "模型没按格式返回，再试一次或手填"}

    def pick(*keys: str) -> str:
        for k in keys:
            v = got.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    persona = {
        "name": pick("name", "名字", "角色名")[:16],
        "jp": pick("jp", "kana", "假名")[:24],
        "role": pick("role", "身份", "称呼")[:16],
        "voice": pick("voice", "说话方式", "性格", "personality")[:400],
        "greeting": pick("greeting", "开场白", "hello")[:120],
    }
    if not persona["name"]:
        return {"ok": False, "message": "模型没给出名字，再试一次"}
    return {"ok": True, "persona": persona}


def load_chat_config(cfg=None, provider: str = "", persona_id: str = "") -> ChatConfig:
    """读聊天模型配置。

    优先级（后者只补空位，不覆盖前者）：

    1. ``config.json`` 的 ``chat.base_url / api_key / model``
       —— 用户在「设置」里填的，**唯一的一等公民**
    2. ``chat.persona_models.<persona_id>``
       —— 某个角色单独指定的模型（设置页「每个角色用哪个模型」）
    3. 老配置兼容：``chat.providers.<provider>`` 段
    4. 环境变量 ``DEEPSEEK_API_KEY`` / ``ZHIPU_API_KEY`` 等（可选，方便脚本化）
    5. 内置默认只提供 base_url 与模型名，**不提供 Key**

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
    #
    #    顶层 Key 也遵守同一规则：写了 ${VAR} 占位符的，默认不展开，
    #    避免程序悄悄用上机器里别的程序留下的密钥。
    _allow_env_top = local.get("use_env_key") is True
    for k in ("base_url", "api_key", "model", "persona", "persona_name"):
        raw_v = str(local.get(k) or "")
        if k == "api_key" and "${" in raw_v and not _allow_env_top:
            put(k, "")
        else:
            put(k, expand_env(raw_v))
    for k in ("temperature", "max_tokens", "timeout", "history_max"):
        if local.get(k) is not None:
            put(k, local[k], type(getattr(out, k)))
    if out.base_url or out.api_key or out.model:
        out.source = "user"

    # 2. 角色单独指定的模型：覆盖全局 model
    #
    #    这是「设置页只让用户填 Key，再给每个角色选一个模型」的落点。
    #    没给某个角色选，就跟随全局 model。
    pm = local.get("persona_models")
    if isinstance(pm, dict) and persona_id:
        chosen = str(pm.get(persona_id) or "").strip()
        if chosen:
            out.model = chosen
            out.persona_model = chosen

    # 2.5 接入的模型列表（多模型）：角色挑中的那一项带着自己的 Key / 地址 / 模型名
    #
    #     优先于上面的单 Key 配置。没选中的角色用列表里的第一个。
    entry = pick_model_entry(local, persona_id)
    if entry:
        out.base_url = str(entry.get("base_url") or "").rstrip("/") or out.base_url
        ek = str(entry.get("api_key") or "").strip()
        if ek and not ("${" in ek and not local.get("use_env_key") is True):
            out.api_key = expand_env(ek) if "${" in ek else ek
        out.model = str(entry.get("model") or "").strip() or out.model
        out.persona_model = out.model
        out.provider = str(entry.get("provider") or "") or out.provider
        out.model_label = str(entry.get("label") or "").strip()
        if out.base_url or out.api_key or out.model:
            out.source = "user"

    # 2. 老配置：providers 段（按 provider 取，或用户当前选中的那个）
    #
    #    ⚠ 这里的 api_key 常常写成 ``${ZHIPU_API_KEY}`` 这种占位符。
    #    默认**不展开**：机器上可能残留别的程序留下的 Key，静默捡起来用等于
    #    「程序带着别人的密钥在跑」，用户既不知情也没授权，还会把 Key 的末几位
    #    显示在设置页上。只有 chat.use_env_key = true 时才允许展开。
    allow_env = local.get("use_env_key") is True
    provs = local.get("providers")
    if isinstance(provs, dict):
        pick = provider or str(local.get("provider") or "")
        d = provs.get(pick) if pick else None
        if not isinstance(d, dict) and len(provs) == 1:
            d = next(iter(provs.values()))
        if isinstance(d, dict):
            put("base_url", expand_env(str(d.get("base_url") or "")))
            raw_key = str(d.get("api_key") or "")
            put("api_key", expand_env(raw_key) if allow_env else
                ("" if "${" in raw_key else raw_key))
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
    # ---------------- 下面这些是「真办事」的动作型工具 ----------------
    {
        "type": "function",
        "function": {
            "name": "add_anime",
            "description": (
                "把一部番剧加入收藏。用户说「收藏这部」「追这部」「加进我的番剧」时用。"
                "参数从 search_online(kind=anime) 的结果里拿 source 和 source_id，别猜。"
                "收藏成功后就能记播放进度了。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "番剧源 key，如 girigiri / ffzy / apibdzy"},
                    "source_id": {"type": "string", "description": "该源里的剧集 id"},
                },
                "required": ["source", "source_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_comic",
            "description": (
                "把一部漫画加入书架。用户说「收藏这部漫画」「加进漫画书架」时用。"
                "参数从 search_online(kind=comic) 的结果里拿 source 和 source_id。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "漫画源 key"},
                    "source_id": {"type": "string", "description": "该源里的漫画 id"},
                },
                "required": ["source", "source_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_book_text",
            "description": (
                "把一本书的正文抓下来存进本地（只抓还没抓的章节）。"
                "用户说「帮我抓完」「把正文下下来」「这本书只有目录没法读」时用。"
                "抓取要花时间，一次最多抓 limit 章，告诉用户抓了多少、还剩多少。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "书的 id（从 search_shelf / reading_status 拿到）"},
                    "limit": {"type": "integer", "description": "本次最多抓几章，默认 20，别一次抓太多"},
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_book",
            "description": (
                "给用户一个能直接点开读的链接（定位到某一章）。"
                "用户说「我要看这本」「接着上次读」「跳到第 N 章」时用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "书的 id"},
                    "chapter_idx": {"type": "integer", "description": "第几章（从 0 开始），不填就接着上次的进度"},
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_novel_index",
            "description": (
                "建/续扫「哔哩轻小说」的书目索引。"
                "这个源不能实时搜站，只能查本地索引；索引为空就搜不到任何书。"
                "用户抱怨「搜不到书」「书太少」「搜索没结果」时，先用这个建索引，再搜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pages": {"type": "integer", "description": "本次扫多少页，默认 40，可反复调继续扫"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_local_file",
            "description": (
                "导入本机的 txt / epub / md 文件到书架。"
                "用户说「把这个文件加进来」「导入我下载的小说」时用，需要完整文件路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径，比如 D:\\books\\xx.epub"},
                    "title": {"type": "string", "description": "书名，不填就用文件名"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_reading_spot",
            "description": (
                "帮用户把某本书的阅读位置记下来（比如他说「我读到第 30 章了」）。"
                "这不是查询，是写入，只在用户明确要你记的时候用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "书的 id"},
                    "chapter_idx": {"type": "integer", "description": "第几章（从 0 开始）"},
                },
                "required": ["book_id", "chapter_idx"],
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

        # ---------------- 动作型工具：真的去办事 ----------------

        if name == "add_anime":
            from .sources.anime import ANIME_SOURCES, detail

            src = str(args.get("source") or "").strip()
            sid = str(args.get("source_id") or "").strip()
            if not src or not sid:
                return {"error": "需要 source 和 source_id（从 search_online 的结果里取）"}
            if src not in ANIME_SOURCES:
                return {"error": f"没有这个番剧源：{src}，可选 {list(ANIME_SOURCES)[:8]}"}
            try:
                info = detail(cfg, src, sid)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"取详情失败：{type(exc).__name__}: {exc}"[:200]}
            try:
                lib.upsert_anime(info)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"收藏失败：{type(exc).__name__}: {exc}"[:200]}
            eps = len(info.get("episodes") or info.get("roads") or [])
            return {
                "ok": True, "title": info.get("title") or sid,
                "source": src, "source_id": sid, "episodes": eps,
                "link": f"/anime/{src}/{sid}",
                "note": "已经加进收藏了，之后能记播放进度",
            }

        if name == "add_comic":
            from .sources.venera_js import JsSource, VeneraRegistry

            src = str(args.get("source") or "").strip()
            sid = str(args.get("source_id") or "").strip()
            if not src or not sid:
                return {"error": "需要 source 和 source_id（从 search_online 的结果里取）"}
            try:
                reg = VeneraRegistry(cfg)
                hit = next(((e, p) for e, p in reg.installed() if e.key == src), None)
                if hit is None:
                    return {"error": f"没装这个漫画源：{src}"}
                entry, path = hit
                info = JsSource(cfg, path).call("info", {"comicId": str(sid)}) or {}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"取详情失败：{type(exc).__name__}: {exc}"[:200]}
            try:
                lib.upsert_comic({
                    "source": src,
                    "source_id": str(sid),
                    "title": info.get("title") or f"{src}-{sid}",
                    "author": info.get("subTitle") or "",
                    "cover": info.get("cover") or "",
                    "description": info.get("description") or "",
                    "tags": info.get("tags") or [],
                })
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"收藏失败：{type(exc).__name__}: {exc}"[:200]}
            return {
                "ok": True, "title": info.get("title") or f"{src}-{sid}",
                "source": src, "source_id": str(sid),
                "link": f"/comic/{src}/{sid}",
                "note": "已经加进漫画书架了",
            }

        if name == "fetch_book_text":
            from .crawler import Crawler
            from .sources import build_registry

            bid = int(args.get("book_id") or 0)
            if not bid:
                return {"error": "需要 book_id"}
            book = lib.get_book(bid)
            if not book:
                return {"error": f"没有 id={bid} 的书"}
            if not book["url"]:
                return {"error": "这本书是本地导入的，没有源地址，不用抓"}
            limit = max(1, min(80, int(args.get("limit") or 20)))
            total, done = lib.chapter_count(bid)
            if done >= total and total > 0:
                return {"ok": True, "book_id": bid, "title": book["title"],
                        "stored": done, "total": total, "note": "正文已经齐了，不用抓"}

            crawler = Crawler(cfg, lib, build_registry(cfg))
            before = done
            try:
                crawler.resume(bid, limit=limit)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"抓取失败：{type(exc).__name__}: {exc}"[:200]}
            total2, done2 = lib.chapter_count(bid)
            return {
                "ok": True, "book_id": bid, "title": book["title"],
                "fetched_now": max(0, done2 - before),
                "stored": done2, "total": total2,
                "remaining": max(0, total2 - done2),
                "note": ("还剩 %d 章，可以再让我抓一次" % (total2 - done2))
                if total2 > done2 else "正文全部抓完了",
            }

        if name == "open_book":
            bid = int(args.get("book_id") or 0)
            if not bid:
                return {"error": "需要 book_id"}
            book = lib.get_book(bid)
            if not book:
                return {"error": f"没有 id={bid} 的书"}
            idx = args.get("chapter_idx")
            if idx is None:
                p = lib.get_progress(bid)
                idx = int((p["chapter_idx"] if p else 0) or 0)
            idx = max(0, int(idx))
            ch = lib.get_chapter(bid, idx)
            total, done = lib.chapter_count(bid)
            return {
                "ok": True, "book_id": bid, "title": book["title"],
                "chapter_idx": idx,
                "chapter": (ch["title"] if ch else ""),
                "stored": done, "total": total,
                "link": f"/read/{bid}/{idx}",
                "note": "把 link 给用户，他点开就能读",
            }

        if name == "build_novel_index":
            from .sources.linovelib import build_index, INDEX_SOURCE

            pages = max(1, min(200, int(args.get("pages") or 40)))
            before = lib.novel_index_count(INDEX_SOURCE)
            try:
                res = build_index(cfg, lib, budget=pages)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"建索引失败：{type(exc).__name__}: {exc}"[:200]}
            after = lib.novel_index_count(INDEX_SOURCE)
            return {
                "ok": True,
                "added": max(0, after - before),
                "total": after,
                "pages": res.get("pages", 0),
                "failed": res.get("failed", 0),
                "note": ("索引现在有 %d 本，可以再搜了" % after) if after
                else "这一轮没扫到书（可能站点改版或网络不通），换个时间再试",
            }

        if name == "import_local_file":
            from pathlib import Path as _Path

            from .importer import Importer

            raw = str(args.get("path") or "").strip().strip('"')
            if not raw:
                return {"error": "需要文件路径"}
            p = _Path(raw)
            if not p.exists():
                return {"error": f"找不到文件：{raw}"}
            if p.suffix.lower() not in (".txt", ".epub", ".md", ".markdown"):
                return {"error": f"不支持 {p.suffix}，只吃 txt / epub / md"}
            try:
                ids = Importer(lib).import_path(p, title=str(args.get("title") or ""))
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"导入失败：{type(exc).__name__}: {exc}"[:200]}
            out = []
            for bid in (ids or [])[:5]:
                b = lib.get_book(bid)
                if b:
                    # get_book() 的行没有 n_chapters（那是 list_books 的 JOIN 字段），
                    # 这里必须用 chapter_count 现算，否则 KeyError。
                    total, _done = lib.chapter_count(bid)
                    out.append({"book_id": bid, "title": b["title"],
                                "chapters": total})
            return {"ok": True, "imported": len(ids or []), "books": out}

        if name == "remember_reading_spot":
            bid = int(args.get("book_id") or 0)
            idx = int(args.get("chapter_idx") or 0)
            if not bid:
                return {"error": "需要 book_id"}
            book = lib.get_book(bid)
            if not book:
                return {"error": f"没有 id={bid} 的书"}
            total, _done = lib.chapter_count(bid)
            idx = max(0, min(idx, max(0, total - 1)))
            lib.set_progress(bid, idx)
            ch = lib.get_chapter(bid, idx)
            return {"ok": True, "book_id": bid, "title": book["title"],
                    "chapter_idx": idx, "chapter": (ch["title"] if ch else ""),
                    "note": "记好了"}

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
    def conf(self, provider: str = "deepseek", persona_id: str = "") -> ChatConfig:
        """每次现读：改 config.json / 环境变量不用重启进程。"""
        return load_chat_config(self.cfg, provider, persona_id)

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
        conf = self.conf(persona.provider, persona.id)
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
