"""拷贝漫画（copy_manga）：网页版实现，不依赖 App API 的 IP 风控。

App 接口 ``api.copy2000.online/api/v3/comic2`` 对同一出口 IP 有极小配额，
超过就返回 ``code 210``（“请到官网更新最新 APP……等待 1 小时”），换版本号、
换设备指纹、换代理出口都绕不过。网页版（``copy4000.com`` / ``copy3000.com``）
没有这层限制，代价是返回的内容都做了 AES 加密，需要自己解。

网页版协议（实测）：

* 详情页 ``/comic/<path_word>``：HTML 里带书名、作者、封面、简介、
  第一话链接、``<span id="dnt" value="9">``（请求时要作为 ``dnts`` 头发回）。
* 目录接口 ``/comicdetail/<path_word>/chapters?limit=1000&offset=0``：
  JSON 的 ``results`` 是一段密文，解出来是 ``{"build": {...}, "groups": {...}}``。
* 阅读页 ``/comic/<path_word>/chapter/<uuid>``：HTML 里 ``contentKey``
  是密文，解出来是 ``[{"url": "..."}, ...]`` 的图片列表；
  页脚 ``第N / M話`` 给出进度，``comicContent-next`` 给出下一话。

解密（网页 JS 里的 ``comic_content_pass`` / ``comic_detail_pass``）：

* 密钥 ``cct`` / ``ccz`` 是页面里 16 字符的常量（当前 ``0p0zzpvv.nmn.oop``），
  按 UTF-8 取字节做 AES-128 key。
* 密文形如 ``<16 字符 IV><hex 密文>``：前 16 字符按 UTF-8 当 IV，
  后面按十六进制解成字节，AES-CBC + PKCS7。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import re
import string
import time
import warnings
from typing import Optional

from ..config import Config
from ..net import Fetcher

# 站点的证书链不完整（部分镜像 IP 直连时 SAN 不匹配），这里统一关校验，
# 否则每次请求都刷一条 InsecureRequestWarning。
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

SOURCE_KEY = "copy_manga"
SOURCE_NAME = "拷贝漫画"
SOURCE_ID = "copy_manga"

BASE = "https://www.copy4000.com"
# 备用镜像：不同域名的章节接口行为不一样，实测 copy4000 给完整内容，
# copy3000 从某个时间点起只给空壳（``contentKey`` 为空、``comicCount`` 为 0），
# 所以按顺序试，命中能用的就记住。
MIRRORS = (
    "https://www.copy4000.com",
    "https://copy4000.com",
    "https://www.copy3000.com",
    "https://copy3000.com",
)
DEFAULT_CCT = "0p0zzpvv.nmn.oop"

WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# App 接口签名用的 key（保留：某些镜像网页挂了还能走 App 接口搜）
_APP_KEY = base64.b64decode("M2FmMDg1OTAzMTEwMzJlZmUwNjYwNTUwYTA1NjNhNTM=")
APP_HOSTS = ("https://api.copy2000.online", "https://api.copy-manga.com")
APP_UMSTRING = "b4c89ca4104ea9a97750314d791520ac"
_DEVICE_POOL = (
    ("3371150V-9327", "EB0O.675141.548"),
    ("4482161V-8412", "SM-S9180.827361.012"),
    ("5593272V-7523", "23127PN0CC.918234.331"),
    ("6604383V-6634", "V2309A.547182.194"),
)


# ---------------------------------------------------------------- 解密
def _hex_to_bytes(s: str) -> bytes:
    """对齐 CryptoJS ``enc.Hex.parse``：两位一组，非法按 0。"""
    out = bytearray()
    for i in range(0, len(s) - 1, 2):
        try:
            out.append(int(s[i : i + 2], 16) & 0xFF)
        except ValueError:
            out.append(0)
    return bytes(out)


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    out = dec.update(data) + dec.finalize()
    if out:
        pad = out[-1]
        if 1 <= pad <= 16 and out[-pad:] == bytes([pad]) * pad:
            out = out[:-pad]
    return out


def decrypt_blob(blob: str, key: str) -> str:
    """``<16 字符 IV><hex>`` -> 明文。"""
    if not blob or len(blob) < 34:
        raise ValueError("密文太短")
    iv = blob[:16].encode("utf-8")
    data = _hex_to_bytes(blob[16:])
    raw = _aes_cbc_decrypt(key.encode("utf-8"), iv, data)
    return raw.decode("utf-8", errors="replace")


def decrypt_json(blob: str, key: str):
    return json.loads(decrypt_blob(blob, key))


# ---------------------------------------------------------------- 页面解析
_RE_CCT = re.compile(r"var\s+c(?:ct|cz)\s*=\s*'([^']+)'")
_RE_DNT = re.compile(r'id="dnt"[^>]*value="([^"]*)"')
_RE_TITLE = re.compile(r'<h6[^>]*title="([^"]*)"')
_RE_COVER = re.compile(r'class="lazyload"[^>]*data-src="([^"]+)"')
_RE_INTRO = re.compile(r'<p class="intro"[^>]*>(.*?)</p>', re.S)
_RE_FIRST = re.compile(r'href="(/comic/[^"]+/chapter/[^"]+)"')
_RE_NEXT = re.compile(
    r'<div class="comicContent-next">\s*<a href="([^"]+)"', re.S
)
_RE_PROG = re.compile(r"第(\d+)&nbsp;/&nbsp;(\d+)[話话]")
_RE_CHAP_TITLE = re.compile(r'<h4 class="header">([^<]*)</h4>')
_RE_CONTENT_KEY = re.compile(r"var contentKey = '([^']*)'")
_RE_GROUP_TAB = re.compile(
    r'class="nav-link[^"]*"\s+data-toggle="tab"\s+href="#([^"]+)"[^>]*>([^<]*)<'
)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


class CopyMangaSource:
    """拷贝漫画网页版源，接口与 JsSource 对齐（search / info / episode）。"""

    key = SOURCE_KEY
    name = SOURCE_NAME

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._fetcher: Optional[Fetcher] = None
        self._base = BASE
        self._cct = DEFAULT_CCT

    # ---------------- HTTP ----------------
    def _f(self) -> Fetcher:
        if self._fetcher is None:
            import copy as _copy

            # 拷贝漫画的站内延迟设小一点：它走 Cloudflare，慢在握手而不是频率限制，
            # 沿用全局 delay=1.2s 会让一次详情要 45s 以上。
            fast = _copy.copy(self.cfg)
            fast.delay = 0.25
            fast.jitter = 0.15
            fast.timeout = 20
            fast.retries = 1
            fast.retry_backoff = 0.8
            self._fetcher = Fetcher(fast)
            self._fetcher.session.headers.update(
                {"User-Agent": WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8"}
            )
        return self._fetcher

    def close(self) -> None:
        if self._fetcher is not None:
            self._fetcher.close()
            self._fetcher = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _fetch(self, url: str, *, referer: str = "", as_json: bool = False,
               extra_headers: Optional[dict] = None, timeout: float = 10.0,
               tries: int = 3) -> Optional[str]:
        """直接发一次请求（不走 Fetcher 的缓存/长退避）。

        站点成功率只有 20~60%、单次 1~11s，长退避会把一次详情拖到几十秒，
        所以这里自己控制：短超时、快速重试、失败换代理。
        """
        f = self._f()
        headers = {"User-Agent": WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8"}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        for attempt in range(tries):
            try:
                resp = f.session.get(url, headers=headers, timeout=timeout,
                                     verify=False, allow_redirects=True)
            except Exception:  # noqa: BLE001
                f._rotate_proxy()
                continue
            if resp.status_code == 200:
                try:
                    return resp.json() if as_json else resp.text
                except Exception:  # noqa: BLE001
                    pass
            f._rotate_proxy()
        return None

    def _fetch_any(self, path: str, *, referer: str = "", as_json: bool = False,
                   extra_headers: Optional[dict] = None, need: str = "",
                   timeout: float = 10.0) -> Optional[str]:
        """并行打所有镜像，返回第一份合格响应。

        站点每个域名都时不时 ReadTimeout / SSLError，串行试四个域名最坏要
        40s+；并发出去通常 2~3s 就有结果。
        """
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        bases = [self._base] + [b for b in MIRRORS if b != self._base]
        extra = dict(extra_headers or {})
        extra.setdefault("Origin", self._base)

        def one(base: str):
            body = self._fetch(base + path, referer=referer or base + "/",
                               as_json=as_json, extra_headers=extra, timeout=timeout,
                               tries=1)
            if body is None:
                return None
            if as_json:
                if not isinstance(body, dict):
                    return None
                enc = body.get("results")
                if isinstance(enc, str) and len(enc) > 40:
                    return (base, enc)
                return None
            if "comicParticulars" not in body and "comicContent" not in body:
                return None
            if need:
                m = re.search(need, body)
                if not m or not m.group(1):
                    return None
            return (base, body)

        ex = ThreadPoolExecutor(max_workers=len(bases))
        try:
            futures = {ex.submit(one, b) for b in bases}
            # 谁先成功用谁，不等慢的镜像（它们会跑到超时才回）
            while futures:
                done, futures = wait(futures, timeout=timeout + 2,
                                     return_when=FIRST_COMPLETED)
                if not done:
                    break
                for fut in done:
                    try:
                        got = fut.result()
                    except Exception:  # noqa: BLE001
                        continue
                    if got:
                        self._base = got[0]
                        return got[1]
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return None

    def _get_html(self, path_or_url: str, *, referer: str = "", need: str = "") -> str:
        """取一份像样的 HTML；``need`` 指定必须出现的非空标记（跳过空壳页）。"""
        path = path_or_url
        if path.startswith("http"):
            path = re.sub(r"^https?://[^/]+", "", path)
        for _ in range(4):
            html = self._fetch_any(path, referer=referer, need=need, timeout=12.0)
            if html:
                return html
        raise RuntimeError("拷贝漫画页面取不到（镜像全部超时或返回空壳）")

    def _get_json_enc(self, path: str, *, referer: str, dnts: str = "") -> str:
        """请求 /comicdetail/... 这类返回密文的接口，返回密文字符串。"""
        extra = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if dnts:
            extra["dnts"] = dnts
        for _ in range(2):
            enc = self._fetch_any(path, referer=referer, as_json=True,
                                  extra_headers=extra, timeout=11.0)
            if enc:
                return enc
        raise RuntimeError("拷贝漫画接口失败（镜像全部超时）")

    # ---------------- 搜索 ----------------
    def _app_search(self, keyword: str, limit: int) -> list[dict]:
        """App 搜索接口没有 210 限制，用它拿 path_word。"""
        deviceinfo, device = random.choice(_DEVICE_POOL)
        pseudoid = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
        out: list[dict] = []
        last: Exception | None = None
        for host in APP_HOSTS:
            ts = str(int(time.time()))
            headers = {
                "User-Agent": "COPY/3.0.9",
                "source": "copyApp",
                "deviceinfo": deviceinfo,
                "dt": time.strftime("%Y.%m.%d"),
                "platform": "3",
                "referer": "com.copymanga.app-3.0.9",
                "version": "3.0.9",
                "device": device,
                "pseudoid": pseudoid,
                "Accept": "application/json",
                "region": "0",
                "authorization": "Token",
                "umstring": APP_UMSTRING,
                "x-auth-timestamp": ts,
                "x-auth-signature": hmac.new(_APP_KEY, ts.encode(), hashlib.sha256).hexdigest(),
            }
            try:
                resp = self._f().session.get(
                    host + "/api/v3/search/comic",
                    params={"q": keyword, "limit": limit, "offset": 0},
                    headers=headers,
                    timeout=10,
                    verify=False,
                )
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
            if data.get("code") != 200:
                last = RuntimeError(str(data.get("message"))[:60])
                continue
            for it in (data.get("results") or {}).get("list") or []:
                authors = ", ".join(a.get("name", "") for a in (it.get("author") or []))
                out.append(
                    {
                        "id": it.get("path_word", ""),
                        "title": it.get("name") or "",
                        "subTitle": authors,
                        "cover": it.get("cover") or "",
                        "tags": [],
                        "popular": it.get("popular") or 0,
                    }
                )
            if out:
                return out
        if last:
            raise RuntimeError(f"拷贝漫画搜索失败：{last}")
        return out

    def _web_search(self, keyword: str, limit: int) -> list[dict]:
        """网页搜索页兜底（App 接口挂了时用）。"""
        from urllib.parse import quote

        html = self._get_html("/search?q=" + quote(keyword))
        out: list[dict] = []
        for m in re.finditer(
            r'href="(/comic/([^"/]+))"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{1,80})<', html
        ):
            pw = m.group(2)
            title = _clean(m.group(3))
            if not pw or not title:
                continue
            if any(r["id"] == pw for r in out):
                continue
            out.append({"id": pw, "title": title, "subTitle": "", "cover": "", "tags": []})
            if len(out) >= limit:
                break
        return out

    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        try:
            rows = self._app_search(keyword, limit)
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            try:
                rows = self._web_search(keyword, limit)
            except Exception:  # noqa: BLE001
                rows = []
        return rows[:limit]

    # ---------------- 详情 ----------------
    def info(self, comic_id: str) -> dict:
        """返回与 Venera 源一致的 info：chapters 是 ``{组: {章节id: 章节名}}``。"""
        path = "/comic/" + comic_id
        html = self._get_html(path)
        m = _RE_CCT.search(html)
        if m:
            self._cct = m.group(1)
        md = _RE_DNT.search(html)
        dnt = md.group(1) if md else ""

        title = ""
        mt = _RE_TITLE.search(html)
        if mt:
            title = _clean(mt.group(1))
        if not title:
            mt2 = re.search(r"<title>([^<]*)</title>", html)
            title = _clean(mt2.group(1)).split("-")[0].strip() if mt2 else comic_id

        cover = ""
        mc = _RE_COVER.search(html)
        if mc:
            cover = mc.group(1)

        intro = ""
        mi = _RE_INTRO.search(html)
        if mi:
            intro = _clean(mi.group(1))[:300]

        author = ""
        ma = re.search(r"作者：</span>\s*<span[^>]*>(.*?)</span>", html, re.S)
        if ma:
            author = _clean(ma.group(1))

        status = ""
        ms = re.search(r"狀態：</span>\s*<span[^>]*>([^<]*)<", html)
        if ms:
            status = _clean(ms.group(1))

        tags = [_clean(t) for t in re.findall(r'href="/comics\?theme=[^"]*"[^>]*>#([^<]+)<', html)]

        # 章节：{组key: {章节id: 章节名}}
        groups: dict[str, dict[str, str]] = {}
        group_names: dict[str, str] = {}
        try:
            enc = self._get_json_enc(
                f"/comicdetail/{comic_id}/chapters?limit=1000&offset=0",
                referer=self._base + path,
                dnts=dnt,
            )
            data = decrypt_json(enc, self._cct)
            for gkey, gval in (data.get("groups") or {}).items():
                bucket: dict[str, str] = {}
                for c in gval.get("chapters") or []:
                    cid = c.get("id") or c.get("uuid") or ""
                    if cid:
                        bucket[str(cid)] = _clean(c.get("name") or "")
                if bucket:
                    groups[gkey] = bucket
                    group_names[gkey] = _clean(gval.get("name") or gkey)
        except Exception:  # noqa: BLE001
            groups = {}

        # 接口没给章节（个别书 groups.count=0）→ 顺着第一话的 next 链走一遍
        if not groups:
            walked = self._walk_chapters(comic_id, html)
            if walked:
                groups = {"default": walked}
                group_names = {"default": "默认"}

        return {
            "id": comic_id,
            "title": title,
            "subTitle": author,
            "cover": cover,
            "description": intro,
            "status": status,
            "tags": tags,
            "chapters": groups,
            "groupNames": group_names,
            "url": self._base + path,
        }

    def _walk_chapters(self, comic_id: str, html: str) -> dict[str, str]:
        """从第一话开始按 ``下一話`` 链接串出章节表（目录接口空时用）。"""
        m = _RE_FIRST.search(html)
        if not m:
            return {}
        out: dict[str, str] = {}
        url = m.group(1)
        seen: set[str] = set()
        mp = _RE_PROG.search(html)
        total = int(mp.group(2)) if mp else 0
        guard = max(total + 5, 30)
        for _ in range(guard):
            if not url or url in seen:
                break
            seen.add(url)
            try:
                page = self._get_html(url, referer=self._base + "/comic/" + comic_id)
            except Exception:  # noqa: BLE001
                break
            uuid = url.rstrip("/").rsplit("/", 1)[-1]
            mt = _RE_CHAP_TITLE.search(page)
            name = _clean(mt.group(1)) if mt else f"第{len(out) + 1}话"
            out[uuid] = name
            mn = _RE_NEXT.search(page)
            if not mn:
                break
            nxt = mn.group(1)
            if not nxt:
                break
            url = re.sub(r"^https?://[^/]+", "", nxt) if nxt.startswith("http") else nxt
        return out

    # ---------------- 章节图片 ----------------
    def episode(self, comic_id: str, ep_id: str) -> list[str]:
        path = f"/comic/{comic_id}/chapter/{ep_id}"
        # contentKey 必须非空，空壳页说明该镜像这次没给正文
        html = self._get_html(
            path,
            referer=self._base + "/comic/" + comic_id,
            need=r"var contentKey = '([^']+)'",
        )
        m = _RE_CCT.search(html)
        if m:
            self._cct = m.group(1)
        mk = _RE_CONTENT_KEY.search(html)
        if not mk or not mk.group(1):
            return []
        data = decrypt_json(mk.group(1), self._cct)
        urls: list[str] = []
        for item in data if isinstance(data, list) else []:
            u = item.get("url") if isinstance(item, dict) else None
            if u:
                urls.append(u)
        return urls


# ---------------------------------------------------------------- 模块级便捷函数
def search(cfg: Config, keyword: str, limit: int = 10) -> list[dict]:
    with CopyMangaSource(cfg) as src:
        return src.search(keyword, limit)


def info(cfg: Config, comic_id: str) -> dict:
    with CopyMangaSource(cfg) as src:
        return src.info(comic_id)


def episode(cfg: Config, comic_id: str, ep_id: str) -> list[str]:
    with CopyMangaSource(cfg) as src:
        return src.episode(comic_id, ep_id)
