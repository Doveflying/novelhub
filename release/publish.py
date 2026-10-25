"""创建 GitHub Release 并上传产物。

    python release/publish.py            # 建 release + 上传
    python release/publish.py --dry-run  # 只打印将要做什么

为什么用 Python 而不是 PowerShell：PowerShell 的 ConvertTo-Json 会把
中文 body 包成 {"value" => "..."} 形态，GitHub 直接 422；而且
Invoke-RestMethod 对 UTF-8 请求体的处理在不同版本上不一致。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REL_DIR = ROOT / "release"
TAG = "v0.4.1"
REPO = "Doveflying/novelhub"

ASSETS = [
    ("bookfly-windows-x64.zip", "Windows 10/11 x64 独立应用（解压即用，无需 Python）"),
    ("bookfly-android.apk", "Android 7.0+ 安装包（内置 Python 运行时）"),
]


def token() -> str:
    """从 git credential manager 取 GitHub token（push 能过说明它有效）。"""
    p = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=30,
    )
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise SystemExit("取不到 GitHub 凭据，请先 git push 一次以缓存凭据")


def api(url: str, tok: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "bookfly-release",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    # DELETE 返回 204 无响应体，别硬解析
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def upload(upload_url: str, tok: str, path: Path, label: str) -> dict:
    """上传单个资产。

    三个坑，全在这里踩过：

    1. **文件名别用中文。** GitHub 的 ``?name=`` 对非 ASCII 处理不稳，
       实测「书萤-windows-x64.zip」会被存成「-windows-x64.zip」。
       资产名一律 ASCII，中文说明放 release 正文里。
    2. **大文件别用 urllib。** 48MB 的 APK 用 urllib 上传，
       反复 ``EOF occurred in violation of protocol``；换 curl 一次过。
    3. **curl 传二进制要 ``--data-binary``**，``-d`` 会当表单处理。
    """
    name = path.name
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    url = f"{upload_url}?name={urllib.parse.quote(name, safe='')}"

    cmd = [
        "curl.exe", "-sS", "--fail-with-body",
        "-X", "POST",
        "-H", f"Authorization: Bearer {tok}",
        "-H", "Accept: application/vnd.github+json",
        "-H", "User-Agent: bookfly-release",
        "-H", f"Content-Type: {ctype}",
        "--data-binary", f"@{path}",
        "--max-time", "900",
        "--retry", "3", "--retry-delay", "3", "--retry-all-errors",
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, timeout=1000)
    out_text = (p.stdout or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0:
        raise SystemExit(
            f"上传失败：{name}\n  curl exit={p.returncode}\n  {out_text[:400]}"
        )
    try:
        out = json.loads(out_text)
    except json.JSONDecodeError:
        raise SystemExit(f"上传返回非 JSON：{out_text[:400]}")
    out["_label"] = label
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    notes = (REL_DIR / "NOTES.md").read_text(encoding="utf-8")

    missing = [n for n, _ in ASSETS if not (REL_DIR / n).exists()]
    if missing:
        print(f"缺少产物：{missing}", file=sys.stderr)
        return 1

    for n, _ in ASSETS:
        p = REL_DIR / n
        print(f"  {n:28s} {p.stat().st_size / 1024 / 1024:6.1f} MB")

    if args.dry_run:
        print(f"\n[dry-run] 将创建 {TAG} 并上传 {len(ASSETS)} 个资产")
        return 0

    tok = token()

    # 已存在就复用，避免重复创建报 422
    rel = None
    try:
        rel = api(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", tok)
        print(f"复用已有 release: {rel['html_url']}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        rel = api(
            f"https://api.github.com/repos/{REPO}/releases",
            tok,
            method="POST",
            body={
                "tag_name": TAG,
                "target_commitish": "main",
                "name": "书萤 v0.4.1 — Windows 独立应用 + Android APK",
                "body": notes,
                "draft": False,
                "prerelease": False,
            },
        )
        print(f"创建 release: {rel['html_url']}")

    existing = {a["name"] for a in rel.get("assets", [])}
    upload_url = rel["upload_url"].split("{")[0]

    # 清掉名字被编码搞坏的历史资产（例如中文丢成 "-windows-x64.zip"）
    for a in rel.get("assets", []):
        if a["name"] not in {n for n, _ in ASSETS}:
            try:
                api(f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}",
                    tok, method="DELETE")
                print(f"  删除异常命名的旧资产：{a['name']}")
            except urllib.error.HTTPError:
                pass

    for name, label in ASSETS:
        if name in existing:
            print(f"  已存在，跳过：{name}")
            continue
        asset = upload(upload_url, tok, REL_DIR / name, label)
        print(f"  已上传 {name}  ({asset['size'] / 1024 / 1024:.1f} MB)")
        print(f"         {asset['browser_download_url']}")

    final = api(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", tok)
    print(f"\n完成：{final['html_url']}")
    print(f"资产 {len(final['assets'])} 个：")
    for a in final["assets"]:
        print(f"  {a['name']:28s} {a['size'] / 1024 / 1024:6.1f} MB  {a['download_count']} 次下载")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
