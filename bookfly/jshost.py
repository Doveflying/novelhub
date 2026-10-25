"""Venera JS 源的宿主侧实现（Android：WebView 当 JS 引擎，这里当能力后端）。

桌面端跑 ``novelhub/jsruntime/runner.mjs``（Node 子进程）。Android 上没有 node
二进制，改用系统 WebView 的 V8 执行同一套 ``init.js`` 契约；凡是需要真实能力的
操作（HTTP / 加密 / 键值存储）都通过 ``@JavascriptInterface`` 同步回调到这里。

调用链：

    Flask 线程 → JsBridge.eval()            [Java，阻塞等结果]
        → WebView 里 __veneraRun()
            → sendMessage() → bridge.host(json)      [同步]
                → 本模块 handle(json) → 原路返回
        → bridge.done() 释放 Flask 线程

HTTP 走 ``novelhub.net.Fetcher``，和桌面端完全同一套：代理探测、重试、Cloudflare
判定、页面缓存全部复用，两端行为不会漂移。加密走 ``hashlib`` / ``cryptography``，
语义对齐 runner.mjs（AES 默认 PKCS#7，解密保留填充，由源自己处理）。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac as _hmac
import json
import threading
import zlib
from typing import Any, Optional

# 每个 job 一份上下文（Fetcher + 键值存储 + 源名）
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 编解码工具
def _b64_to_bytes(s: Any) -> bytes:
    if s is None:
        return b""
    if isinstance(s, bytes):
        return s
    return base64.b64decode(str(s))


def _bytes_to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _as_bytes(v: Any) -> bytes:
    """Convert.* 的入参：可能是 base64 字符串，也可能是 UTF-8 文本。"""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode("utf-8") if v is not None else b""


# ---------------------------------------------------------------- AES
def _aes(mode: str, enc: bool, value: bytes, key: bytes, iv: bytes, block_size: Any) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding

    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key 长度非法：{len(key)}")

    m = mode.lower()
    if m == "ecb":
        cipher_mode = modes.ECB()
    elif m == "cbc":
        cipher_mode = modes.CBC(iv)
    elif m == "cfb":
        # 8 位块对应 CFB8，其余按 CFB128
        cipher_mode = modes.CFB8(iv) if int(block_size or 128) == 8 else modes.CFB(iv)
    elif m == "ofb":
        cipher_mode = modes.OFB(iv)
    else:
        raise ValueError(f"未知 AES 模式：{mode}")

    if m in ("ecb", "cbc"):
        padder = sym_padding.PKCS7(128)
        if enc:
            p = padder.padder()
            value = p.update(value) + p.finalize()
    c = Cipher(algorithms.AES(key), cipher_mode)
    if enc:
        ctx = c.encryptor()
    else:
        ctx = c.decryptor()
    return ctx.update(value) + ctx.finalize()


def _rsa_decrypt(value: bytes, key: str) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    pem = key if "-----BEGIN" in key else (
        "-----BEGIN PRIVATE KEY-----\n" + "\n".join(
            key[i:i + 64] for i in range(0, len(key), 64)
        ) + "\n-----END PRIVATE KEY-----"
    )
    priv = serialization.load_pem_private_key(pem.encode(), password=None)
    return priv.decrypt(value, asym_padding.PKCS1v15())


def _convert(req: dict) -> Any:
    """对齐 init.js 的 Convert 契约。

    入参/出参的形态要按 init.js 的注释分清楚，不能一律当 base64：
      - encodeUtf8(str) -> ArrayBuffer    入参是普通字符串
      - decodeUtf8(ArrayBuffer) -> str    入参是 base64
      - encodeBase64(ArrayBuffer) -> str  入参 base64，出参普通字符串
      - decodeBase64(str) -> ArrayBuffer  入参普通字符串，出参 base64
      - md5/sha*/aes*(ArrayBuffer) -> ArrayBuffer
      - hmacString(...) -> str（hex）
    """
    t = str(req.get("type") or "")
    enc = bool(req.get("isEncode"))
    raw_value = req.get("value")
    key = _b64_to_bytes(req.get("key"))
    iv = _b64_to_bytes(req.get("iv"))
    bs = req.get("blockSize")

    if t == "utf8":
        if enc:
            return str(raw_value if raw_value is not None else "").encode("utf-8")
        return _b64_to_bytes(raw_value).decode("utf-8", "replace")

    if t == "gbk":
        # Python 原生就有 gbk/gb18030，比 Node 端（只能解码）更完整
        if enc:
            return str(raw_value if raw_value is not None else "").encode("gb18030", "replace")
        return _b64_to_bytes(raw_value).decode("gb18030", "replace")

    if t == "base64":
        if enc:
            # 入参是 ArrayBuffer（base64），出参是 base64 字符串
            return _bytes_to_b64(_b64_to_bytes(raw_value))
        # 入参是普通字符串，出参是 ArrayBuffer（base64）
        return base64.b64decode(str(raw_value or ""))

    if t in ("md5", "sha1", "sha256", "sha512"):
        h = hashlib.new(t)
        h.update(_b64_to_bytes(raw_value))
        return h.digest()

    if t == "hmac":
        algo = str(req.get("hash") or "sha256").lower().replace("-", "")
        mac = _hmac.new(key, _b64_to_bytes(raw_value), algo).digest()
        return mac.hex() if req.get("isString") else mac

    if t.startswith("aes-"):
        return _aes(t[4:], enc, _b64_to_bytes(raw_value), key, iv, bs)

    if t == "rsa":
        return _rsa_decrypt(_b64_to_bytes(raw_value), str(req.get("key") or ""))

    raise ValueError(f"未知 convert 类型：{t}")


# ---------------------------------------------------------------- 键值存储
def _store_load(ctx: dict) -> dict:
    if ctx.get("store") is not None:
        return ctx["store"]
    data: dict = {}
    p = ctx.get("store_path")
    if p:
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
    ctx["store"] = data
    return data


def _store_save(ctx: dict) -> None:
    p = ctx.get("store_path")
    if not p:
        return
    import os

    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ctx.get("store") or {}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _store_op(ctx: dict, req: dict) -> Any:
    fn = str(req.get("fn") or "")
    k = str(req.get("key") or "")
    data = _store_load(ctx)
    if fn == "load":
        return data.get(k)
    if fn == "save":
        data[k] = req.get("value")
        _store_save(ctx)
        return None
    if fn == "delete":
        data.pop(k, None)
        _store_save(ctx)
        return None
    if fn == "isLogged":
        return bool(data.get("token"))
    raise ValueError(f"未知 store 操作：{fn}")


# ---------------------------------------------------------------- HTTP
def _decompress(raw: bytes, encoding: str) -> bytes:
    """按 Content-Encoding 解压响应体。

    正常情况下 requests 会自己处理：它默认发 ``accept-encoding: gzip, deflate``
    并在读 body 时解压。麻烦在于源会在 headers 里写死更宽的一串
    （``gzip, deflate, br, zstd``），一旦调用方显式指定，requests 就
    **不再自动解压**，resp.content 是压缩字节，源再 JSON.parse 直接崩
    （实测 manhuaren 就是 Content-Encoding: br + 一串写死的头）。

    所以主策略是「不把 accept-encoding 透传出去」，让 requests 用自己的
    协商与解压链路；这个函数只是兜底，处理少数仍带编码头的响应。
    """
    enc = (encoding or "").strip().lower()
    if not enc or enc == "identity":
        return raw

    for one in [e.strip() for e in enc.split(",") if e.strip()]:
        try:
            if one in ("gzip", "x-gzip"):
                raw = gzip.decompress(raw)
            elif one == "deflate":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    # 有些服务器发裸 deflate（无 zlib 头）
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            elif one == "br":
                try:
                    import brotli  # type: ignore[import-not-found]

                    raw = brotli.decompress(raw)
                except ImportError:
                    try:
                        import brotlicffi as brotli  # type: ignore[import-not-found]

                        raw = brotli.decompress(raw)
                    except ImportError:
                        return raw
            elif one == "zstd":
                try:
                    import zstandard  # type: ignore[import-not-found]

                    raw = zstandard.ZstdDecompressor().decompress(raw)
                except ImportError:
                    return raw
        except Exception:  # noqa: BLE001
            # 解压失败就当已经解过（有的站点标了 gzip 其实没压）
            return raw
    return raw


def _decode_bytes(raw: bytes, content_type: str = "") -> str:
    """和 net.Fetcher._decode 同样的策略：先看 charset，再按 utf-8/gb18030/big5 试。

    JS 源普遍直接 JSON.parse(res.body)，解码错了就整个源挂掉，所以这里
    不能只靠 utf-8。
    """
    if "charset=" in content_type:
        enc = content_type.split("charset=")[-1].split(";")[0].strip().strip('"')
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


# 这些头由 requests/urllib3 自己管，透传反而会破坏解压与分块
_DROP_HEADERS = {
    "accept-encoding",
    "content-length",
    "connection",
    "transfer-encoding",
    "host",
    "te",
    "upgrade",
    "proxy-connection",
}


def _http(ctx: dict, req: dict) -> dict:
    f = ctx.get("fetcher")
    if f is None:
        raise RuntimeError("job 没有初始化 Fetcher")

    url = str(req.get("url") or "")
    method = str(req.get("method") or "GET").upper()
    raw_headers = dict(req.get("headers") or {})
    data = req.get("data")

    # 源写死的 accept-encoding / host / content-length 必须丢掉：
    #  - accept-encoding 会让 requests 停止自动解压（br 尤其致命，PyPI 上没有
    #    brotli 时只能拿到乱码）
    #  - host 会和重定向后的真实主机冲突
    #  - content-length 由 requests 按实际 body 重算
    headers = {
        k: v
        for k, v in raw_headers.items()
        if str(k).lower() not in _DROP_HEADERS
    }

    # 统一成 str / bytes 交给 requests
    body: Optional[Any] = None
    if data is not None:
        if isinstance(data, dict) and "b64" in data:
            body = _b64_to_bytes(data["b64"])
        elif isinstance(data, str):
            body = data
        elif isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = str(data)

    # 复用 Fetcher 的 session：代理、UA、cookie 都在上面，和桌面端同一条链路。
    # 注意不要走 Fetcher.get()，它带页面缓存且只接受 GET。
    resp = f.session.request(
        method,
        url,
        headers=headers,
        data=body,
        timeout=f.cfg.timeout,
        allow_redirects=True,
        stream=True,
    )
    f.requests_made += 1
    raw = f._read_body(resp)
    # 兜底：极少数站点即使没要求也带 Content-Encoding
    raw = _decompress(raw, resp.headers.get("Content-Encoding", ""))
    f.bytes_in += len(raw)
    ctype = resp.headers.get("Content-Type", "")
    return {
        "status": resp.status_code,
        "headers": {k.lower(): v for k, v in resp.headers.items()},
        "body": _decode_bytes(raw, ctype),
        "b64": _bytes_to_b64(raw),
    }


# ---------------------------------------------------------------- job 生命周期
def begin_job(job_id: str, store_path: str = "", cfg=None) -> None:
    """Flask 线程在调 Java 之前调这个，把上下文挂上。"""
    from novelhub.config import load_config
    from novelhub.net import Fetcher

    c = cfg or load_config()
    with _LOCK:
        _JOBS[job_id] = {
            "cfg": c,
            "fetcher": Fetcher(c),
            "store_path": store_path,
            "store": None,
        }


def end_job(job_id: str) -> None:
    with _LOCK:
        ctx = _JOBS.pop(job_id, None)
    if ctx:
        try:
            ctx["fetcher"].close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 主入口
def handle(payload: str) -> str:
    """Java 侧 @JavascriptInterface 每次回调都进这里。返回 JSON 字符串。

    二进制结果统一用 base64 传（WebView 的 Java↔JS 桥不支持 byte[]）。
    """
    try:
        req = json.loads(payload or "{}")
    except ValueError as exc:
        return json.dumps({"error": f"host 收到非法 JSON：{exc}"}, ensure_ascii=False)

    job = str(req.get("job") or "")
    op = str(req.get("op") or "")

    with _LOCK:
        ctx = _JOBS.get(job)
    if ctx is None:
        return json.dumps({"error": f"job 不存在或已结束：{job}"}, ensure_ascii=False)

    try:
        if op == "convert":
            out = _convert(req)
            if isinstance(out, str):
                return json.dumps({"value": out}, ensure_ascii=False)
            return json.dumps({"b64": _bytes_to_b64(out)})

        if op == "http":
            return json.dumps(_http(ctx, req), ensure_ascii=False)

        if op == "store":
            return json.dumps({"value": _store_op(ctx, req)}, ensure_ascii=False)

        if op == "setting":
            return json.dumps({"value": _setting(ctx, req)}, ensure_ascii=False)

        if op == "log":
            return json.dumps({"value": None}, ensure_ascii=False)

        return json.dumps({"error": f"未知 op：{op}"}, ensure_ascii=False)

    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False
        )


def _setting(ctx: dict, req: dict) -> Any:
    """源设置项：用户改过就返回改过的，否则返回 schema 里的 default。

    和 runner.mjs 的 loadSetting 完全一致——很多源直接吃这个默认值
    （jm 用 parseInt(loadSetting('apiDomain')) 选镜像域名）。
    """
    k = str(req.get("key") or "")
    default = req.get("default")
    data = _store_load(ctx)
    ov = data.get("__settings") or {}
    if isinstance(ov, dict) and k in ov:
        return ov[k]
    return default
