"""LightNovelHub —— 本地轻小说聚合书库 + 抓取 + 阅读器。"""

__version__ = "0.2.0"

from .config import Config, load_config
from .store import Library

__all__ = ["Config", "load_config", "Library", "__version__"]
