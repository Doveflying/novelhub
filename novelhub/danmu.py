# -*- coding: utf-8 -*-
"""弹弹play 开放弹幕网络客户端。

接口文档：https://doc.dandanplay.com/open/

签名算法（签名验证模式）
    X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))
    Path 是 URL 里域名之后、? 之前的路径，如 /api/v2/comment/12345

两种鉴权模式
    签名模式（推荐）：X-AppId + X-Timestamp + X-Signature
    凭证模式（服务端）：X-AppId + X-AppSecret
本项目是本地服务端，默认走凭证模式（少一次哈希，且 AppSecret 不外泄给浏览器）。

主要用到的接口
    GET /api/v2/search/episodes?anime=关键词     搜番剧 → 拿 animeId / episodeId
    GET /api/v2/search/episodes?episode=集名     按集名搜
    GET /api/v2/comment/{episodeId}?withRelated=true   拿弹幕

弹幕格式（官方注释 XML，转成项目内部结构）
    <d p="出现时间,类型,颜色,用户ID,时间戳,池,用户哈希,行ID">文本</d>
    类型：1=滚动 4=底部 5=顶部
    颜色：十进制 RGB 整数
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .config import Config


class DanmuError(RuntimeError):
    pass


class DanmuNotConfigured(DanmuError):
    """没配 AppId/AppSecret —— 调用方应该降级成「无弹幕」而不是报错。"""


@dataclass
class DanmuComment:
    """一条弹幕，字段对齐前端 canvas 渲染需要的东西。"""

    text: str
    time: float          # 出现时间（秒）
    mode: int            # 1=滚动 4=底部 5=顶部
    color: int           # 0xRRGGBB
    size: int = 25
    uid: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "time": round(self.time, 3),
            "mode": self.mode,
            "color": "#%06x" % (self.color & 0xFFFFFF),
            "size": self.size,
        }


def _sign(app_id: str, app_secret: str, path: str, ts: Optional[int] = None) -> dict:
    """生成签名模式的三个头。"""
    ts = int(ts if ts is not None else time.time())
    data = f"{app_id}{ts}{path}{app_secret}"
    sig = base64.b64encode(hashlib.sha256(data.encode("utf-8")).digest()).decode()
    return {"X-AppId": app_id, "X-Timestamp": str(ts), "X-Signature": sig}


class DanDanPlay:
    """弹弹play 客户端。所有请求走本地缓存，避免触发限流。"""

    BASE = "https://api.dandanplay.net"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        d = getattr(cfg, "dandanplay", None) or {}
        self.app_id = str(d.get("app_id") or "").strip()
        self.app_secret = str(d.get("app_secret") or "").strip()
        self.mode = str(d.get("auth_mode") or "credential").lower()
        self.enabled = bool(d.get("enabled", True))
        self.cache_hours = float(d.get("cache_hours") or 6)
        self.cache_dir = cfg.data_path / "danmu"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "application/json",
        })
        if cfg.proxy:
            self.session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})

    # ---------------- 基础 ----------------
    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _headers(self, path: str) -> dict:
        if not self.configured:
            raise DanmuNotConfigured(
                "未配置弹弹play凭据。在 config.json 的 dandanplay 段填 app_id / app_secret。"
            )
        if self.mode == "signature":
            return _sign(self.app_id, self.app_secret, path)
        return {"X-AppId": self.app_id, "X-AppSecret": self.app_secret}

    def _cache_file(self, key: str) -> Path:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{h}.json"

    def _cached(self, key: str):
        f = self._cache_file(key)
        if not f.exists():
            return None
        age_h = (time.time() - f.stat().st_mtime) / 3600.0
        if age_h > self.cache_hours:
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _store(self, key: str, data) -> None:
        try:
            self._cache_file(key).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _get(self, path: str, params: Optional[dict] = None, *, cache_key: str = "",
             use_cache: bool = True):
        if use_cache and cache_key:
            hit = self._cached(cache_key)
            if hit is not None:
                return hit

        url = self.BASE + path
        headers = self._headers(path)
        try:
            resp = self.session.get(url, params=params, headers=headers,
                                    timeout=self.cfg.timeout)
        except requests.RequestException as exc:
            raise DanmuError(f"弹弹play 请求失败：{exc}") from exc

        if resp.status_code == 403:
            msg = resp.headers.get("X-Error-Message", "")
            raise DanmuError(
                f"弹弹play 鉴权失败（403 {msg}）。"
                "检查 config.json 里 dandanplay.app_id / app_secret 是否正确，"
                "以及本机时间是否准确（时间戳偏差大会被拒）。"
            )
        if resp.status_code != 200:
            raise DanmuError(f"弹弹play HTTP {resp.status_code}：{path}")

        ctype = resp.headers.get("Content-Type", "")
        text = resp.text
        if "json" in ctype or text.lstrip().startswith("{"):
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise DanmuError(f"弹弹play 返回不是合法 JSON：{text[:120]}") from exc
            # 业务错误也是 200
            if isinstance(data, dict) and data.get("success") is False:
                raise DanmuError(
                    "弹弹play 业务错误 %s：%s"
                    % (data.get("errorCode"), data.get("errorMessage")))
            if use_cache and cache_key:
                self._store(cache_key, data)
            return data

        # 弹幕接口直接返回 XML
        if use_cache and cache_key:
            self._store(cache_key, {"__xml__": text})
        return {"__xml__": text}

    # ---------------- 业务 ----------------
    def search_anime(self, keyword: str) -> list[dict]:
        """按番剧名搜，返回 [{animeId, animeTitle, episodes:[...]}]。"""
        data = self._get("/api/v2/search/episodes", {"anime": keyword},
                         cache_key=f"search:{keyword}")
        out = []
        for a in (data.get("animes") or []):
            eps = []
            for e in (a.get("episodes") or []):
                eps.append({
                    "episodeId": e.get("episodeId"),
                    "episodeTitle": e.get("episodeTitle") or "",
                })
            out.append({
                "animeId": a.get("animeId"),
                "animeTitle": a.get("animeTitle") or "",
                "type": a.get("type") or "",
                "typeDescription": a.get("typeDescription") or "",
                "episodes": eps,
            })
        return out

    def get_comments(self, episode_id: int, with_related: bool = True) -> list[DanmuComment]:
        """取某个节目的弹幕。"""
        path = f"/api/v2/comment/{int(episode_id)}"
        params = {"withRelated": "true" if with_related else "false",
                  "chConvert": 0}
        data = self._get(path, params,
                         cache_key=f"comment:{episode_id}:{with_related}")
        xml_text = data.get("__xml__") if isinstance(data, dict) else None
        if not xml_text:
            return []
        return parse_comment_xml(xml_text)


# ---------------------------------------------------------------- 解析

_MODE_MAP = {1: 1, 2: 1, 3: 1, 4: 4, 5: 5}  # 1/2/3 都当滚动


def parse_comment_xml(xml_text: str) -> list[DanmuComment]:
    """官方注释 XML → DanmuComment 列表。

    格式：<d p="时间,类型,颜色,用户ID,时间戳,池,用户哈希,行ID">文本</d>
    解析失败的条目直接跳过（弹幕库里脏数据很常见）。
    """
    out: list[DanmuComment] = []
    if not xml_text or "<d " not in xml_text:
        return out
    # 有些返回带 BOM 或前后杂字符，截到 <i> ... </i>
    start = xml_text.find("<i>")
    end = xml_text.rfind("</i>")
    if start != -1 and end != -1:
        xml_text = xml_text[start:end + 4]
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # 逐条正则兜底（XML 里偶有未转义字符）
        for m in re.finditer(r'<d p="([^"]*)"[^>]*>(.*?)</d>', xml_text, re.S):
            c = _make_comment(m.group(1), m.group(2))
            if c:
                out.append(c)
        return out

    for d in root.iter("d"):
        p = d.get("p") or ""
        text = (d.text or "").strip()
        c = _make_comment(p, text)
        if c:
            out.append(c)
    return out


def _make_comment(p: str, text: str) -> Optional[DanmuComment]:
    if not text:
        return None
    parts = p.split(",")
    if len(parts) < 4:
        return None
    try:
        t = float(parts[0])
        mode = int(float(parts[1]))
        color = int(float(parts[2]))
    except (ValueError, TypeError):
        return None
    if t < 0 or t > 24 * 3600:
        return None
    return DanmuComment(
        text=text,
        time=t,
        mode=_MODE_MAP.get(mode, 1),
        color=color & 0xFFFFFF,
        uid=parts[3] if len(parts) > 3 else "",
    )


def client(cfg: Config) -> DanDanPlay:
    return DanDanPlay(cfg)
