"""HTTP 抓取层：代理、限速、重试、编码识别、本地缓存。"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import urllib3
from requests import exceptions as req_exc
from urllib3 import exceptions as u3_exc

from .config import Config, detect_proxies, invalidate_proxy_cache


def _log_incomplete(url: str, got: int, exc: Exception) -> None:
    """记录「响应体不完整但可用」的站点，方便排查源质量。"""
    try:
        INCOMPLETE_RESPONSES.append({"url": url, "bytes": got,
                                     "reason": type(exc).__name__})
        del INCOMPLETE_RESPONSES[:-50]  # 只留最近 50 条
    except Exception:
        pass


# 进程级：最近出现「chunked 无终止块」的请求（/sources 页面可以展示）
INCOMPLETE_RESPONSES: list[dict] = []


def _looks_complete(chunks: list[bytes]) -> bool:
    """判断已收到的字节是否已经构成一个完整的响应体。

    只做保守判断，误判的代价是「提前截断」——所以要求非常明确：
    - JSON：尾部（忽略空白）是 } 或 ]
    - XML：尾部是 >
    - HTML：尾部是 </html> 或 </HTML>
    单看尾部一个字节不够（JSON 内部也可能有 }），所以再要求括号配平。
    """
    if not chunks:
        return False
    tail = chunks[-1][-64:].strip()
    if not tail:
        return False
    last = tail[-1:]
    if last in (b"}", b"]"):
        return _braces_balanced(b"".join(chunks))
    if last == b">":
        return tail.endswith((b"</html>", b"</HTML>", b"</i>", b"</xml>"))
    return False


def _braces_balanced(buf: bytes) -> bool:
    """粗略判断 JSON 的 {} 和 [] 是否配平（跳过字符串里的括号）。"""
    depth_c = depth_s = 0
    in_str = False
    esc = False
    # 只扫尾部 200KB，够用了
    for ch in buf[-200000:]:
        if in_str:
            if esc:
                esc = False
            elif ch == 0x5C:      # backslash
                esc = True
            elif ch == 0x22:      # "
                in_str = False
            continue
        if ch == 0x22:
            in_str = True
        elif ch == 0x7B:          # {
            depth_c += 1
        elif ch == 0x7D:          # }
            depth_c -= 1
        elif ch == 0x5B:          # [
            depth_s += 1
        elif ch == 0x5D:          # ]
            depth_s -= 1
        if depth_c < 0 or depth_s < 0:
            return False
    return depth_c == 0 and depth_s == 0 and not in_str


class FetchError(RuntimeError):
    pass


class BlockedError(FetchError):
    """Cloudflare 质询 / 站点风控页面。"""


class Fetcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": cfg.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,en;q=0.7",
            }
        )
        if cfg.proxy:
            self.session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
        self.proxy = cfg.proxy
        # 代理会掉线/换节点：记一份候选，出错时轮换
        self._candidates = [cfg.proxy] if cfg.proxy else []
        for p in detect_proxies():
            if p not in self._candidates:
                self._candidates.append(p)
        self._pip = 0
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self.requests_made = 0
        self.bytes_in = 0

    # ---------------- 基础 ----------------
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = 0.0
            if self.cfg.delay > 0:
                last = self._last.get(host, 0.0)
                target = last + self.cfg.delay + random.uniform(0, self.cfg.jitter)
                wait = max(0.0, target - time.time())
            self._last[host] = time.time() + wait
        if wait:
            time.sleep(wait)

    def _cache_file(self, url: str) -> Path:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cfg.cache_path / f"{h}.html"

    @staticmethod
    def _read_body(resp: requests.Response) -> bytes:
        """把响应体读成 bytes，容忍「服务端不发 chunked 终止块」的站点。

        实测 api.wujinapi.com 走 Cloudflare，响应头是
        ``Transfer-Encoding: chunked`` 但连接不关也不发 0 长度终止块。
        requests 的 ``resp.content`` 会一直等 EOF，直到 timeout 抛
        ChunkedEncodingError —— 明明数据已经收全了，却整条失败。

        这里改成流式累加：读的过程中断了就把已收到的部分返回，
        只要拿到了非空内容就当成功。
        """
        chunks: list[bytes] = []
        total = 0
        try:
            for piece in resp.iter_content(chunk_size=16384):
                if not piece:
                    continue
                chunks.append(piece)
                total += len(piece)
                # 有的站（实测 api.wujinapi.com）不发 chunked 终止块也不关连接，
                # iter_content 会一直等到 timeout。这里做「完整性早停」：
                # 收到足够字节且尾部是完整的 JSON/HTML 收尾，就不再等 EOF。
                if total >= 2048 and _looks_complete(chunks):
                    break
        except (req_exc.ChunkedEncodingError,
                req_exc.ConnectionError,
                req_exc.Timeout,
                u3_exc.ProtocolError,
                u3_exc.IncompleteRead) as exc:
            # 已收到的数据仍然可用；一个字节都没有才继续抛
            if not chunks:
                raise
            got = sum(len(c) for c in chunks)
            _log_incomplete(resp.url, got, exc)
        return b"".join(chunks)

    @staticmethod
    def _decode(resp: requests.Response, encoding: Optional[str]) -> str:
        raw = Fetcher._read_body(resp)
        if encoding:
            return raw.decode(encoding, errors="replace")
        ctype = resp.headers.get("Content-Type", "")
        if "charset=" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip()
            try:
                return raw.decode(enc, errors="replace")
            except LookupError:
                pass
        for enc in ("utf-8", "gb18030", "big5"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _looks_blocked(text: str, status: int) -> bool:
        if status in (403, 429, 503):
            return True
        head = text[:4000].lower()
        return (
            "just a moment" in head
            or "cf-challenge" in head
            or "checking your browser" in head
            or "__cf_chl_opt" in head
            or "attention required! | cloudflare" in head
        )

    # ---------------- 抓取 ----------------
    def get(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        encoding: Optional[str] = None,
        use_cache: Optional[bool] = None,
    ) -> str:
        use_cache = self.cfg.cache if use_cache is None else use_cache
        cache_file = self._cache_file(url)
        if use_cache and cache_file.exists():
            return cache_file.read_text(encoding="utf-8", errors="replace")
        if self.cfg.offline:
            raise FetchError(f"离线模式且无缓存：{url}")

        headers = {"Referer": referer} if referer else {}
        last_err: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            self._throttle(url)
            try:
                resp = self.session.get(
                    url, headers=headers, timeout=self.cfg.timeout,
                    allow_redirects=True, stream=True,
                )
            except requests.RequestException as exc:
                last_err = exc
                # 代理问题就换下一个候选，再给一次机会
                if self._rotate_proxy():
                    continue
                time.sleep(self.cfg.retry_backoff * (attempt + 1) + random.random())
                continue

            self.requests_made += 1
            # 注意：不要在这里碰 resp.content —— 开了 stream=True 之后
            # 它会重新把整个 body 读一遍，等于把 _read_body 的容错绕过去。
            text = self._decode(resp, encoding)
            self.bytes_in += len(text.encode("utf-8", "ignore"))

            if resp.status_code == 200 and not self._looks_blocked(text, 200):
                if use_cache:
                    cache_file.write_text(text, encoding="utf-8")
                return text

            if self._looks_blocked(text, resp.status_code):
                last_err = BlockedError(
                    f"HTTP {resp.status_code} 被 Cloudflare/风控拦截：{url}"
                )
                # 换个 UA 再试，退避更久
                self.session.headers["User-Agent"] = self._rotate_ua()
                time.sleep(self.cfg.retry_backoff * (attempt + 1) * 1.5)
                continue

            last_err = FetchError(f"HTTP {resp.status_code}：{url}")
            time.sleep(self.cfg.retry_backoff * (attempt + 1))

        raise FetchError(
            f"抓取失败（{url}）：{last_err}"
            + (
                "\n提示：该类站点在本地网络下 DNS 可能被污染 / 被 Cloudflare 拦截，"
                "请在 config.json 中配置 proxy（或启动本地代理后使用 auto）。"
                if isinstance(last_err, BlockedError)
                else ""
            )
        )

    def post(
        self,
        url: str,
        data: dict,
        *,
        referer: Optional[str] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """表单 POST（站内搜索框多为 POST，GET 会 404）。不写缓存。"""
        if self.cfg.offline:
            raise FetchError(f"离线模式不允许 POST：{url}")
        headers = {"Referer": referer} if referer else {}
        last_err: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            self._throttle(url)
            try:
                resp = self.session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=self.cfg.timeout,
                    allow_redirects=True,
                    stream=True,
                )
            except requests.RequestException as exc:
                last_err = exc
                if self._rotate_proxy():
                    continue
                time.sleep(self.cfg.retry_backoff * (attempt + 1) + random.random())
                continue

            self.requests_made += 1
            text = self._decode(resp, encoding)
            self.bytes_in += len(text.encode("utf-8", "ignore"))
            if resp.status_code == 200 and not self._looks_blocked(text, 200):
                return text
            if self._looks_blocked(text, resp.status_code):
                last_err = BlockedError(
                    f"HTTP {resp.status_code} 被 Cloudflare/风控拦截：{url}"
                )
                self.session.headers["User-Agent"] = self._rotate_ua()
                time.sleep(self.cfg.retry_backoff * (attempt + 1) * 1.5)
                continue
            last_err = FetchError(f"HTTP {resp.status_code}：{url}")
            time.sleep(self.cfg.retry_backoff * (attempt + 1))

        raise FetchError(f"POST 失败（{url}）：{last_err}")

    def _rotate_proxy(self) -> bool:
        """代理掉线/节点被封时切到下一个候选；切成功返回 True。

        只改本实例的 session，不回写共享 cfg——否则一个源换过的节点
        会被后面所有源继承，越跑越慢。
        候选用尽时清掉探测缓存，下次构造 Fetcher 会重新探一遍端口。
        """
        while self._pip + 1 < len(self._candidates):
            self._pip += 1
            proxy = self._candidates[self._pip]
            self.session.proxies.update({"http": proxy, "https": proxy})
            self.proxy = proxy
            return True
        invalidate_proxy_cache()
        return False

    @staticmethod
    def _rotate_ua() -> str:
        pool = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        ]
        return random.choice(pool)

    def get_bytes(self, url: str, *, referer: Optional[str] = None) -> bytes:
        cache_dir = self.cfg.data_path / "cache" / "img"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cf = cache_dir / hashlib.sha1(url.encode("utf-8")).hexdigest()
        if cf.exists() and cf.stat().st_size > 0:
            return cf.read_bytes()
        if self.cfg.offline:
            raise FetchError(f"离线模式且无图片缓存：{url}")
        headers = {"Referer": referer} if referer else {}
        last_err: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            self._throttle(url)
            try:
                resp = self.session.get(url, headers=headers,
                                        timeout=self.cfg.timeout, stream=True)
                if resp.status_code == 200:
                    body = self._read_body(resp)
                    if body:
                        cf.write_bytes(body)
                        self.bytes_in += len(body)
                        self.requests_made += 1
                        return body
                    last_err = FetchError(f"空响应：{url}")
                else:
                    last_err = FetchError(f"HTTP {resp.status_code}：{url}")
            except requests.RequestException as exc:
                last_err = exc
                # 图片 CDN 常见被单节点挡：换下一个候选代理再试
                if self._rotate_proxy():
                    continue
            time.sleep(self.cfg.retry_backoff * (attempt + 1))
        raise FetchError(f"图片下载失败（{url}）：{last_err}")

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
