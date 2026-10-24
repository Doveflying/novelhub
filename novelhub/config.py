"""全局配置：读写 config.json，自动探测本地代理。"""
from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

# 代理探测结果缓存（进程级）
_PROXY_CACHE: list[str] | None = None
_PROXY_CACHE_AT: float = 0.0

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 常见本地代理/机场端口，auto 模式下按顺序探测
PROXY_PORTS = [
    7891, 7890, 7892, 7893, 7894, 7895, 7896, 7897, 7898, 7899,
    10809, 10808, 1080, 1081, 2080, 8888, 8889, 33210, 20171,
]


@dataclass
class Config:
    data_dir: str = "data"
    export_dir: str = "exports"
    db_file: str = "library.db"
    # 代理： "auto" | "" (直连) | "http://127.0.0.1:7890" | "socks5://127.0.0.1:1080"
    proxy: str = "auto"
    delay: float = 1.2          # 每个请求之间的基础间隔（秒）
    jitter: float = 0.8         # 随机抖动上限（秒）
    timeout: int = 40
    retries: int = 3
    retry_backoff: float = 4.0
    user_agent: str = DEFAULT_UA
    cache: bool = True          # 是否把 HTML 缓存到 data/cache
    offline: bool = False       # 只用缓存，不联网
    persist_chapters: bool = True  # False = 只存目录，正文每次在线取，不落库
    live_cache_ttl_hours: float = 24.0  # 在线读的章节临时缓存时间（0 = 不缓存）
    prefetch_count: int = 2          # 在线读时预取后面几章
    localize_images: bool = False    # 是否把插图下载到 data/images（断网也能看）
    convert_to_simplified: bool = True   # 繁体源（哔哩轻小说等）正文/标题转简体显示
    max_pages_per_chapter: int = 20
    # 书目索引增量刷新：多少小时跑一次（0 = 关闭自动刷新）
    # 实测站点日均新书 1~2 本、日更 3~5 本，6 小时一次足够，别一小时一次
    index_refresh_hours: float = 6.0
    index_refresh_pages: int = 3     # 每次只扫 postdate/lastupdate 头部几页
    # 聚合搜索默认只问这些"实测快"的漫画源；空 = 全部
    comic_sources_default: list = field(default_factory=list)
    anime_sources_default: list = field(default_factory=lambda: ["girigiri", "apibdzy"])
    concurrency: int = 1        # 抓取并发（建议 1，避免触发风控）
    sources: dict = field(default_factory=lambda: {"generic": []})
    # 弹弹play 开放弹幕网络（https://doc.dandanplay.com/open/）
    # 凭据在 https://dev.dandanplay.com 申请；不填则弹幕功能自动降级为关闭
    dandanplay: dict = field(default_factory=lambda: {
        "enabled": True,
        "app_id": "",
        "app_secret": "",
        "auth_mode": "credential",   # credential（服务端用）| signature（客户端用）
        "cache_hours": 6,
        "with_related": True,        # 是否合并第三方站点弹幕
    })

    # ---------- 派生路径 ----------
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_path(self) -> Path:
        p = self.data_path / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_path / self.db_file

    @property
    def export_path(self) -> Path:
        p = Path(self.export_dir)
        if not p.is_absolute():
            p = ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self) -> None:
        data = asdict(self)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _probe(port: int, timeout: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def detect_proxies(ttl: float = 60.0) -> list[str]:
    """探测本机所有可用代理端口（按优先级返回）。

    探测要连一圈端口，单次约 3.5s；每个 Fetcher 都重探会把整站拖慢，
    所以按进程缓存 ttl 秒。
    """
    global _PROXY_CACHE, _PROXY_CACHE_AT
    now = time.time()
    if _PROXY_CACHE is not None and now - _PROXY_CACHE_AT < ttl:
        return list(_PROXY_CACHE)
    found = [f"http://127.0.0.1:{p}" for p in PROXY_PORTS if _probe(p)]
    _PROXY_CACHE = found
    _PROXY_CACHE_AT = now
    return list(found)


def invalidate_proxy_cache() -> None:
    """代理掉线/换节点后强制下次重探。"""
    global _PROXY_CACHE
    _PROXY_CACHE = None


def detect_proxy() -> str:
    """探测本地可用代理；找不到返回空串（直连）。"""
    found = detect_proxies()
    return found[0] if found else ""


def load_config() -> Config:
    cfg = Config()
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        for k, v in raw.items():
            if not hasattr(cfg, k):
                continue
            cur = getattr(cfg, k)
            # 配置里的 dict 段（dandanplay / sources）要和默认值合并，
            # 否则用户在 config.json 只写了 app_id，其余默认键全丢
            if isinstance(cur, dict) and isinstance(v, dict):
                merged = dict(cur)
                merged.update(v)
                setattr(cfg, k, merged)
            else:
                setattr(cfg, k, v)
    if cfg.proxy == "auto":
        cfg.proxy = detect_proxy()
    # 环境变量可覆盖（临时切换代理/离线）
    env_proxy = os.environ.get("NOVELHUB_PROXY")
    if env_proxy is not None:
        cfg.proxy = env_proxy
    if os.environ.get("NOVELHUB_OFFLINE") == "1":
        cfg.offline = True
    return cfg


def resolve_proxy(cfg: Config) -> str:
    if cfg.proxy == "auto":
        return detect_proxy()
    return cfg.proxy or ""
