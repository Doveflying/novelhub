# Venera 漫画源：实测清单

对象：用户提供的 `venera-configs` 仓库压缩包（21 个 `.js` 源，落在 `refs/venera/sources/`）
执行器：`novelhub/jsruntime/runner.mjs`（Node 24 + linkedom），调用方 `nh js …`
测试方式：每个源执行 `search.load("天使", [], 1)`；0 条时换关键词「海贼」再试一次。
网络：本机 HTTP 代理（端口会漂移，脚本每次自动探测）。

## 结果

| 源 | 结果 | 备注 |
|---|---|---|
| `ikmmh` 爱看漫 | ✅ 100 条 | HTML 源，DOM 选择器 |
| `rumanhua_fixed_v15` 如漫画 | ✅ 42 条 | |
| `ykmh` 优酷漫画 | ✅ 36 条 | |
| `dm5` 动漫屋 | ✅ 22 条 | |
| `manhuaren` 漫画人 | ✅ 22 条 | |
| `zaimanhua` 再漫画 | ✅ 20 条 | **端到端验证**：search → info(41 话) → ep(15 张图 3.4 MB) |
| `manwaba` 漫蛙吧 | ✅ 20 条 | 需要 `Network.sendRequest` |
| `manhuauo_banana_v2` 香蕉漫画 | ✅ 15 条 | |
| `ManHuaGui` 漫画柜 | ✅ 10 条 | info 27 话已通；图片域名 `cf.mhgui.com` 依赖出口节点，踩到被封节点时会 TLS 失败 |
| `GfmhApp` 古风漫画 | ✅ 5 条 | |
| `komiic` | ⚠️ 时通 | GraphQL，早先 51 条，后来超时（接口慢） |
| `baihehui` 百合会 | ⚠️ 0 条 | 搜索接口可能需要登录态 |
| `dongman_la` 动漫啦 | ⚠️ 0 条 | 同上 |
| `qq_comic_official_v1` 腾讯动漫 | ⚠️ 0 条 | 正版接口，可能限制地区/cookie |
| `baozi` 包子漫画 | ❌ 403 | Cloudflare |
| `ccc` CCC追漫台 | ❌ 返回 HTML | 接口被 CF 拦或已变更 |
| `comick` | ❌ 422 / TLS | 接口参数与出口节点问题 |
| `copy_manga` 拷贝漫画 | ✅ 内置 Python 源 | 见下节「拷贝漫画」，不走 JS 运行时 |
| `goda` GoDa | ❌ TLS reset | 同上 |
| `manga_dex` MangaDex | ❌ fetch 失败 | 其 API 需要 UA/被限流 |
| `mycomic` | ❌ 需 `cf_clearance` | 明确要求浏览器里拿到的 Cloudflare cookie |

结论：**21 个源里 10 个当时可用**。失败原因三类：目标站 Cloudflare（403/422/HTML）、
出口节点被封（TLS reset）、需要登录态或浏览器 cookie（不是引擎问题）。

## 执行器能力（本轮补齐）

| Venera API | 实现 |
|---|---|
| `Network.get/post/put/sendRequest` | 自写 HTTP（HTTPS 走代理 CONNECT 隧道），支持对象体自动 JSON、重定向、超时 |
| Cookie | `getCookies/setCookies/clearCookies` + 自动吸收 `Set-Cookie`，请求自动带 `Cookie` |
| `Convert.*` | `encodeUtf8/decodeUtf8/encodeBase64/decodeBase64/md5/sha1/sha256/hexEncode/hexDecode/hmacString` |
| `HtmlDocument` / `HtmlElement` / `HtmlNode` | linkedom 实现：`text / attributes / children / nodes / innerHTML / parent / classNames / id / localName / previousSibling / nextSibling / querySelector / querySelectorAll / getElementById` |
| `fetch` | 兼容 shim（部分源直接用 fetch） |
| `Comic` / `ComicDetails` / `Author` / `Comment` / `Cookie` | 字段容器类 |
| `console.*` | log/warn/error/info（走 stderr，不污染 stdout 的 JSON） |
| `setInterval` / `_Timer` | 实现并在 `dispose` 时清理 |
| 代理容错 | 端口漂移时 Fetcher 与 JS 调用都会切到下一个可用端口 |

## 用法

```powershell
python nh.py js list                                   # 源清单（官方 31 + 本地 21 合并）
python nh.py js search ManHuaGui "女黑手党" --limit 5   # 搜索
python nh.py js info  ManHuaGui 51517 --limit 8         # 详情 + 章节
python nh.py js ep    ManHuaGui 51517 742434            # 下载一话 + 生成预览页
python nh.py js ep    zaimanhua 72283 146548 --out D:\comic\test
```

产物：`exports/comic/<comicId>-<epId>/001.jpg…` + `index.html`（可直接双击看）。

## 阅读时的"边读边加载"

**文字（小说）**：`/api/chapter/<bid>/<idx>/stream` 是 SSE。适配器按页产出 HTML，
每抓到一页就推一帧，前端立刻追加；4 秒内没吐第一段就自动回落到一次性接口。

实测（冷缓存、4 页章 `3805/326116`）：

```
+4.80s 第1段 4220B 累计3221字   ← 这时候已经能读了
+5.34s 第2段 3921B 累计6143字
+6.73s 第3段 3404B 累计8486字
+8.37s 第4段  261B 累计8660字
+8.39s done
```

已存本地的章节走快路径：`+0.06s` 单帧出完。

**漫画**：页面本身 0.1s 出骨架，JS 拉图片列表（实测 4.1s），然后逐张渲染：
第 1 张 `loading=eager`，其余 `loading=lazy`，滚动到才请求；图片经 `/api/img` 代理
（带 Referer、落 `data/cache/img`），避免 CORS 与防盗链。实测单张 214 KB / 0.0s（命中缓存）。

## 拷贝漫画（copy_manga）——内置 Python 源

用户要的「最新资源」就是它，但 Venera 那份 JS 源在当前网络下走不通（TLS reset），
而且它依赖的 App 接口本身有硬限制。所以单独写了 `novelhub/sources/copy_manga.py`，
不走 node / JS 运行时，直接对接**网页版**。

### 为什么不用 App 接口

App 接口（`api.copy2000.online` / `api.copy-manga.com` / `api.copy3000.com`）的
搜索接口没问题，但**详情接口有按出口 IP 计的极小配额**：

```
GET /api/v3/comic2/<path_word>
→ {"code":210,"message":"請到官網更新最新APP…您或您身邊的人曾經下載過破解版本的拷貝漫畫，
   請下載安裝正版之後等待1小時，限制會自動解除。"}
```

实测过、都绕不过去的路子：

| 尝试 | 结果 |
|---|---|
| 补齐官方 3.0.9 全量请求头（`deviceinfo` / `dt` / `device` / `pseudoid` / `umstring` / HMAC 签名） | 搜索能过，详情照样 210 |
| 换版本号 1.0.0 ~ 4.0.0（14 个） | 全部 210，只换提示文案 |
| 换设备指纹池 4 组 + 随机 `pseudoid` | 全部 210 |
| `region` 0/1/2/空 | 全部 210 |
| 换出口 IP（本地代理 7890 / 7892，出口 42.84.238.79） | 全部 210 |
| 换域名 `api.copy3000.com` / `api.copy5000.com` / `api.copy-manga.com` | 全部 210 |
| `curl.exe` 换 TLS 指纹 | 210 |
| 会话预热（先 `h5/homeIndex`、先 `/api/v3/comics`） | 210 |
| 等待 1 / 2 / 3 分钟后再打 | 210 |

结论：**按出口 IP 记账，配额极小且冷却以小时计**，不适合做常用源。

### 网页版协议（可用）

`www.copy4000.com` 和 `www.copy3000.com` 是同一套前端，接口在**本域**，
没有 IP 配额；代价是返回内容都做了 AES 加密。

```
1) GET /comic/<path_word>
   → HTML：书名（h6[title]）、作者、封面（img.lazyload[data-src]）、简介、
     第一话链接、<span id="dnt" value="9">（后续请求要作为 dnts 头发回）、
     16 字符常量 ccz（= cct，AES key）

2) GET /comicdetail/<path_word>/chapters?limit=1000&offset=0
   Header: dnts: <上面那个值>, X-Requested-With: XMLHttpRequest
   → {"code":200,"results":"<16字符IV><hex密文>"}
   解密 → {"build": {...}, "groups": {"default": {"count":72, "chapters":[{"id","name","type"}]}}}

3) GET /comic/<path_word>/chapter/<uuid>
   → HTML 里 var contentKey = '<16字符IV><hex密文>'
   解密 → [{"url": "https://sj.mangafunb.fun/..."}, ...]   ← 每页图片
   页脚「第N / M話」给进度，comicContent-next 给下一话
```

解密方式（对应页面里的 `comic_content_pass` / `comic_detail_pass`）：

```python
key = cct.encode("utf-8")            # 16 字节，AES-128
iv  = blob[:16].encode("utf-8")      # 前 16 个字符本身当 IV
ct  = bytes.fromhex(blob[16:])       # 后面是十六进制密文
pt  = AES-CBC(key, iv).decrypt(ct)   # 去 PKCS7
```

### 实测数据

| 动作 | 耗时 | 结果 |
|---|---|---|
| 搜索「進擊的巨人」 | 1.0s | 5 条 |
| 详情 `jinjidejuren` | 6.6s | 72 话 + 全彩版 2 话 |
| 第一话图片 | 0.9s | 35 张（`sj.mangafunb.fun/.../*.jpg.c1500x.jpg`） |
| 图片代理 `/api/img` | — | 182 KB JPEG 正常返回 |

### 站点不稳定的处理

四个域名（`www.copy4000.com` / `copy4000.com` / `www.copy3000.com` / `copy3000.com`）
单次成功率只有 20~60%，单次 1~12s，偶发 `ReadTimeout` / `SSLError`。所以：

* **并发打四个域名，谁先成功用谁**（`FIRST_COMPLETED`），不等慢的；
* 单次超时 8~12s，外层最多重试 4 轮；
* 记住命中过的域名，下一轮优先；
* 章节页要求 `contentKey` 非空才算成功——`copy3000.com` 有时只返回 7 KB 空壳
  （`contentKey` 为空、`comicCount` 为 0），会被判失败自动跳过。

串行试四个域名最坏 40s+，改成并发首胜后稳定在 6~8s。

## 未解决

- 需要 Cloudflare cookie 的源（`mycomic`、`baozi`）→ 要浏览器内核（Edge headless）或手动导入 cookie；
- 出口节点随机性导致同一源时通时不通 → 已加端口 failover，但无法解决「所有节点都被目标站封」的情况；
- 漫画尚未进书库（`works.kind=comic`）和图册阅读器，目前是 CLI + 本地预览页。
