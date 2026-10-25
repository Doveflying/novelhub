"""书萤 · Android 端 Python 入口（Chaquopy 里被 Java 调用）。

和桌面 exe 的 launcher.py 做同一件事：起 Flask，但环境换成了 Android：

- 只读资源（模板 / static / refs / 词表）在 APK 的 assets 里，
  Chaquopy 会把它们解包到 ``sys.path`` 可读的位置，这里指过去。
- 可写数据（书库 / 缓存 / 导出）必须落在 App 私有目录，
  Chaquopy 把它放在 ``os.environ["HOME"]``。
- 绑定 127.0.0.1 的一个随机端口，只给 WebView 用，不对外暴露。

Java 侧调用约定：
    start_server() -> str    返回形如 "http://127.0.0.1:34567" 的 base url
    shutdown()               停服务
"""
from __future__ import annotations

import os
import socket
import sys
import threading

_server = None
_port = 0
_lock = threading.Lock()


def _prepare_env() -> None:
    """把 novelhub 的路径解析指到 Android 该去的地方。

    必须在 import novelhub 之前设置，因为 config.py 在 import 时就固化
    ROOT / CONFIG_PATH / DATA_ROOT。
    """
    home = os.environ.get("HOME") or os.path.expanduser("~")
    data_dir = os.path.join(home, "firefly")
    os.makedirs(data_dir, exist_ok=True)

    # 数据根：相对路径（data/、exports/）都相对它
    os.environ.setdefault("NOVELHUB_DATA_DIR", data_dir)
    # 配置文件也放这里，别写进只读的 assets
    os.environ.setdefault("NOVELHUB_CONFIG", os.path.join(data_dir, "config.json"))
    # 移动端默认不用本地代理（Android 上没有 127.0.0.1:7890 那种东西）
    os.environ.setdefault("NOVELHUB_PROXY", "")

    # Chaquopy 把 python 源与数据文件放在 sys.path 里；
    # novelhub 包本身随 src/main/python 一起打包，这里确保它在搜索路径上。
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def _ensure_config() -> None:
    """首次启动写一份 config.json，把代理关掉、数据目录指到私有目录。"""
    cfg_path = os.environ.get("NOVELHUB_CONFIG", "")
    if not cfg_path or os.path.exists(cfg_path):
        return
    try:
        import json

        cfg = {
            "data_dir": "data",
            "export_dir": "exports",
            "db_file": "library.db",
            "proxy": "",
            "delay": 1.2,
            "jitter": 0.8,
            "timeout": 40,
            "retries": 3,
            "retry_backoff": 4.0,
            "cache": True,
            "offline": False,
            "persist_chapters": True,
            "live_cache_ttl_hours": 24,
            "prefetch_count": 2,
            "localize_images": True,   # 手机上把插图落到本地，断网也能看
            "convert_to_simplified": True,
            "max_pages_per_chapter": 20,
            "index_refresh_hours": 6.0,
            "index_refresh_pages": 3,
            # 手机上第一次启动会自动补一批书目索引（哔哩轻小说站内搜索被封，
            # 只能查本地索引；不补的话用户「搜什么都搜不到」）。
            # 0 = 关掉自动补。
            "index_seed_pages": 60,
            "concurrency": 1,
            # 这两项必须写：留空会让聚合搜索退回到「全部已安装源」，
            # 37 个漫画源一起并发，慢源/坏源会把结果拖到超时，
            # 表现就是「搜半天没结果」。跟桌面版 config.json 保持一致。
            "comic_sources_default": [
                "copy_manga", "zaimanhua", "dm5", "manhuaren", "ManHuaGui",
                "rumanhua_fixed_v15", "manhuauo_banana_v2", "hot_manga", "jcomic",
            ],
            "anime_sources_default": ["girigiri", "ffzy", "apibdzy"],
            "sources": {"generic": []},
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server() -> str:
    """起 Flask（后台线程），返回 base url。重复调用返回同一个端口。"""
    global _server, _port
    with _lock:
        if _server is not None:
            return f"http://127.0.0.1:{_port}"

        _prepare_env()
        _ensure_config()

        from novelhub.config import load_config
        from novelhub.webapp.app import create_app

        cfg = load_config()
        app = create_app(cfg)

        _port = _free_port()

        import logging

        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        from werkzeug.serving import make_server

        _server = make_server("127.0.0.1", _port, app, threaded=True)
        t = threading.Thread(target=_server.serve_forever, name="flask", daemon=True)
        t.start()

        return f"http://127.0.0.1:{_port}"


def shutdown() -> None:
    global _server
    with _lock:
        if _server is not None:
            try:
                _server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            _server = None


def health() -> str:
    """给 Java 侧做自检：能不能 import 到 novelhub、数据目录在哪。"""
    try:
        _prepare_env()
        import novelhub

        return (
            f"ok version={novelhub.__version__} "
            f"data={os.environ.get('NOVELHUB_DATA_DIR')} "
            f"py={sys.version.split()[0]}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"fail {type(exc).__name__}: {exc}"
