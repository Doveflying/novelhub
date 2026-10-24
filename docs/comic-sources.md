# Venera 漫画源：实测清单

对象：用户提供的 `venera-configs` 仓库压缩包（21 个 `.js` 源，落在 `refs/venera/sources/`）
执行器：`novelhub/jsruntime/runner.mjs`（Node 24 + linkedom），调用方 `nh js …`
测试方式：每个源执行 `search.load("海贼", [], 1)`。
网络：本机 HTTP 代理（端口会漂移，脚本每次自动探测）。

---

## 0. 反编译 Venera APK 拿到的三样东西（已落地）

`Venera-RE/` 里是 `com.github.wgh136.venera` v1.6.1 的逆向产物。真正能用的有三件，
其余（Dart AOT 反汇编、Frida 模板）对"跑源"没有直接帮助。

### 0.1 `assets/init.js` —— 宿主 API 的完整契约（37.6 KB）

这是 Venera 塞进 QuickJS 的那份宿主声明，**逐方法写清了 `sendMessage` 的字段**。
之前 `runner.mjs` 是按"能跑就行"凑的，缺了不少；对着 init.js 补齐后：

| 补齐的 API | 说明 |
|---|---|
| `Convert.sha512` | 之前只有 md5/sha1/sha256 |
| `Convert.decodeGbk` / `encodeGbk` | 国内站大量 GBK 页面；decode 已实现，encode 缺（Node 无 GBK 编码器，实测无源依赖） |
| `Convert.encryptAesEcb/decryptAesEcb` | AES-ECB |
| `Convert.encryptAesCbc/decryptAesCbc` | AES-CBC（`GfmhApp`、`manwaba`、`ccc` 要用） |
| `Convert.encryptAesCfb/decryptAesCfb` | AES-CFB，带 `blockSize` |
| `Convert.encryptAesOfb/decryptAesOfb` | AES-OFB |
| `Convert.decryptRsa` | 私钥解响应（PKCS#1 v1.5） |
| `Network.patch` / `delete` / `fetchBytes` | fetchBytes 返回 ArrayBuffer，图片/加密响应要用 |
| `Network.deleteCookies` | 7 个源用它清登录态重试 |
| `UI.showDialog/launchUrl/showLoading/cancelLoading/showInputDialog/showSelectDialog` | 无头环境下打日志 + 返回默认值 |
| `log(level,title,content)` | 全局函数，9 个源直接调用 |
| `randomInt` / `randomDouble` / `createUuid` | 全局随机 |
| `compute(fn,...args)` | App 里丢进 isolate 跑，这里同线程直调 |
| `setClipboard` / `getClipboard` | 内存变量 |

**顺带修了两个真 bug**（不是缺 API，是语义不对）：

1. **`loadSetting(k)` 返回 `null`** —— Venera 的语义是"用户没改过就返回 schema 里的
   `default`"。`jm.js` 用 `parseInt(loadSetting('apiDomain')) - 1` 选镜像域名，
   拿到 `null` 就是 `NaN`，`apiDomains[NaN]` 崩在 `jm.js:40`。
   现在改成读 `this.settings[k].default`，并允许 store 覆盖。

2. **`init()` 没有 await** —— 源普遍是 `async init()`，里面拉域名表、算签名种子。
   `runner.mjs` 原来写 `src.init?.(); out = await src.search.load(...)`，
   初始化和业务并发跑，`jm` 的 `static apiDomains` 还没填就去读 → `apiDomains[0]` undefined。
   现在全部 `await src.init?.()`。

覆盖结果（静态扫描 37 个源实际用到的 API）：

```
补齐前：可跑 18 / 37，19 个被缺 API 挡住
补齐后：可跑 37 / 37
```

### 0.2 源仓库真实地址 —— 比 jsdelivr 多 15 个源

从对象池挖出来的常量：

```
comicSourceListUrl = "https://git.nyne.dev/nyne/venera-configs/raw/branch/main/index.json"
```

之前用的是 `cdn.jsdelivr.net/gh/venera-app/venera-configs@main/index.json`。
两个清单不一样：

| | jsdelivr | git.nyne.dev（App 实际用的） |
|---|---|---|
| 条目 | 22 | **31** |

按真实清单补齐后本地共 **37 个**（两边的并集）。新增里能跑通的有：

| 源 | 说明 |
|---|---|
| `hot_manga` 热辣漫画 | ✅ 20 条，1.3s |
| `jcomic` jcomic.net | ✅ 29 条，4.0s |
| `wnacg` 紳士漫畫 | ✅ 有结果（成人向） |

新增但**当前网络下跑不通**的：`picacg`、`nhentai`（下载超时）、`ehentai`、`hitomi`、
`shonen_jump_plus`（需日区 IP）、`comic_walker`、`kavita`/`komga`/`lanraragi`（自建服务器，
没配置就是空）、`mh1234`（403）、`mxs`、`mh18`、`hcomic`、`happy`。

### 0.3 `assets/opencc.txt` —— 简繁转换词表

3980 条「简繁」单字对，原样复制到 `novelhub/data/opencc_s2t.txt`。
Venera 只用它给漫画标签做简繁归一，所以是**子集**：缺繁体独有字
（`裡`、`著`、`麵`、`乾`、`為`），也没有词组表。

实测这表做正文转换会漏字：

```
這裡有一個問題，妳知道嗎？      APK表→「这裡有一个问题，妳知道吗？」   zhconv→「这里有一个问题，你知道吗？」
乾脆就這麼辦吧，麵包也一起吃     APK表→「…，麵包也一起吃」            zhconv→「…，面包也一起吃」
```

所以 `novelhub/zhconvert.py` 优先用 `zhconv`（完整表 + 词组消歧），
APK 词表作为没装 zhconv 时的兜底。用途是把繁体源（哔哩轻小说）的正文和标题转简体，
开关是 `config.json` 的 `convert_to_simplified`。

### 0.4 没搬的东西

- **Cloudflare 过盾**（`network/cloudflare.dart`）：逻辑是"真 WebView 跑 JS challenge，
  拿 `cf_clearance` 连同 UA 回填 cookie jar"。Python/Node 侧没有浏览器内核做不到，
  `mycomic` / `baozi` 仍然要手动导 cookie。
- **Dart AOT 反汇编**（78 个 `.dart`，25 MB）：业务全在远程 JS 里，读 Dart 侧只能
  看清"桥怎么接"，源怎么写还是看 `init.js` + 源脚本本身。

---

## 1. 结果（37 个源，关键词「海贼」）

| 源 | 结果 | 耗时 | 备注 |
|---|---|---|---|
| `dm5` 动漫屋 | ✅ 22 条 | 1.0s | |
| `manhuaren` 漫画人 | ✅ 22 条 | 0.7s | |
| `hot_manga` 热辣漫画 | ✅ 20 条 | 1.3s | **本次新增** |
| `ManHuaGui` 漫画柜 | ✅ 10 条 | 3.1s | |
| `rumanhua_fixed_v15` 如漫画 | ✅ 9 条 | 0.9s | |
| `GfmhApp` 古风漫画 | ✅ 5 条 | 20.8s | 补齐 AES-CBC 后从 0 条变成 5 条 |
| `jcomic` | ✅ 29 条 | 4.0s | **本次新增**，偶尔 TLS reset |
| `manhuauo_banana_v2` 香蕉漫画 | ✅ 4 条 | 1.2s | |
| `zaimanhua` 再漫画 | ✅ 20 条 | — | 端到端最完整：search → info(41 话) → ep(15 张图 3.4 MB) |
| `wnacg` 紳士漫畫 | ✅ | — | **本次新增**（成人向） |
| `baihehui` / `dongman_la` / `lanraragi` / `mxs` / `qq_comic_official_v1` | ⚠️ 0 条 | | 接口通但返回空：需登录态、或关键词不匹配 |
| `baozi` 包子漫画 | ❌ 403 | | Cloudflare |
| `mycomic` | ❌ 缺 `cf_clearance` | | 明确要求浏览器 cookie |
| `ccc` | ❌ 返回 HTML | | 接口被 CF 拦或已变更（域名本身可达） |
| `ehentai` / `hitomi` / `mh18` / `hcomic` / `kavita` / `mh1234` / `mxs` / `copy_manga`(JS版) | ❌ TLS reset / 超时 | | 站点拒绝当前出口 IP |
| `komga` / `kavita` / `lanraragi` | ❌ 需自建 | | 要填自己的服务器地址 |
| `comick` / `goda` / `jm` / `manwaba` / `ykmh` | ❌ 超时 | | 单次 >45s，判超时 |

**结论：37 个源里 10 个真能出结果，5 个空返回，22 个失败。**
失败原因分三类，都不是引擎问题：

1. **站点的 Cloudflare / 需要浏览器 cookie**（`baozi`、`mycomic`、`ccc`）
2. **站点拒绝当前出口 IP 的 TLS**（`ehentai`、`hitomi`、`mh18`、`hcomic`、`happy`）
3. **要自建服务器**（`kavita`、`komga`、`lanraragi`）

注意 `copy_manga` 这一行的失败指的是 **JS 版**；本项目的拷贝漫画是 Python 内置源
（见下面「拷贝漫画」一节），不依赖 JS 运行时，工作正常。

## 2. 执行器能力（已对齐 APK 的 init.js）

| Venera API | 实现 |
|---|---|
| `Network.get/post/put/patch/delete/sendRequest/fetchBytes` | 自写 HTTP（HTTPS 走代理 CONNECT 隧道），支持对象体自动 JSON、重定向、超时 |
| Cookie | `getCookies/setCookies/deleteCookies/clearCookies` + 自动吸收 `Set-Cookie`，请求自动带 `Cookie` |
| `Convert.*` | `utf8/gbk(decode)/base64/md5/sha1/sha256/sha512/hmac/hmacString/hexEncode/hexDecode` + AES ECB/CBC/CFB/OFB + RSA 解密 |
| `HtmlDocument` / `HtmlElement` / `HtmlNode` | linkedom 实现：`text / attributes / children / nodes / innerHTML / parent / classNames / id / localName / previousSibling / nextSibling / querySelector / querySelectorAll / getElementById` |
| `fetch` | 兼容 shim |
| `Comic` / `ComicDetails` / `Author` / `Comment` / `Cookie` | 字段容器类 |
| `console.*` / `log` | 走 stderr，不污染 stdout 的 JSON |
| `setInterval` / `_Timer` | 实现并在 `dispose` 时清理 |
| `randomInt` / `randomDouble` / `createUuid` / `compute` | 全局函数 |
| `UI.*` | 无头环境下写 stderr、返回默认值 |
| `loadSetting` | 读 `settings[k].default`，支持 store 覆盖 |
| `init()` | **await**（否则依赖 init 里初始化的状态会读到 undefined） |
| 代理容错 | 端口漂移时 Fetcher 与 JS 调用都会切到下一个可用端口 |

## 3. 用法

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
