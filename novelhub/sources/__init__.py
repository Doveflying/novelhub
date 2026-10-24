"""站点适配器注册表。"""
from __future__ import annotations

from ..config import Config
from .base import BookMeta, Catalog, ChapterRef, Source, SourceRegistry, VolumeRef
from .generic import GenericSource
from .linovel import LinovelSource
from .linovelib import LinovelibSource
from .narou import NarouSource
from .wenku8 import Wenku8Source

__all__ = [
    "BookMeta",
    "Catalog",
    "ChapterRef",
    "Source",
    "SourceRegistry",
    "VolumeRef",
    "build_registry",
]


def build_registry(cfg: Config) -> SourceRegistry:
    reg = SourceRegistry()
    reg.register(LinovelibSource())
    reg.register(LinovelSource())
    reg.register(Wenku8Source())
    reg.register(NarouSource())
    for spec in (cfg.sources or {}).get("generic", []) or []:
        if not spec.get("enabled", True):
            continue
        try:
            reg.register(GenericSource(spec))
        except Exception:
            continue
    return reg
