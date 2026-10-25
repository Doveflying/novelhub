# AutoNovel 评估：用不上整套，但有三样值得看

对象：[auto-novel/auto-novel](https://github.com/auto-novel/auto-novel)
结论先说：**它的定位是日文轻小说机翻平台，不是我们的竞品也不是我们能复用的源。**
中文小说源一个都没有，公共 API 本机也连不上。真正有参考价值的是它的
provider 接口设计和 JA→ZH 翻译流程。

---

## 1. 它是什么（实测）

| 项 | 值 |
|---|---|
| 定位 | 「轻小说机翻机器人」，自动生成机翻并分享的网站（n.novelia.cc） |
| License | **GPL-3.0** |
| 技术栈 | pnpm monorepo + Vue 3（web）+ Kotlin/Ktor（server:8081）+ Next.js（apps/api）+ MongoDB + Elasticsearch + Caddy |
| 规模 | 437 个源码文件 |
| 部署 | docker compose，README 明确写了「**不是为了个人部署设计的，不保证所有功能可用和前向兼容**」 |

架构上分三层：

```
packages/crawler   TS  →  抓日文站（syosetu / kakuyomu / hameln / alphapolis / novelup / pixiv / amazon）
server (Kotlin)    →  provider 适配器 + 元数据/章节仓库 + 翻译任务 + ES 检索
packages/translator TS  →  分段 → 并发翻译 → 合并（Sakura LLM / OpenAI / 有道）
```

---

## 2. 扫了一遍源码：没有中文小说源

437 个源码文件里出现过的一级域名（按次数）：

```
pixiv.net 41 · ncode.syosetu.com 30 · syosetu.org 25 · kakuyomu.jp 21
alphapolis.co.jp 19 · novelup.plus 18 · amazon.co.jp 14
auth.novelia.cc · n.novelia.cc · api.deepseek.com · platform.openai.com
dict.youdao.com · sakura-share.one · i.pximg.net · novel18.syosetu.com
```

中文小说站特征（linovel / linovelib / esjzone / wenku8 / qidian / ciweimao / sfacg…）
**一个都没命中**（`17k`、`bqg` 只出现在 `pnpm-lock.yaml` 里，是包名噪音）。

也就是说：**它补不上我们「只有 2 个真能读的中文小说源」这个缺口。**

---

## 3. 公共 API：能力齐全，但本机连不上

API 路由（`server/.../api/RouteWebNovel.kt`，Ktor Resources）：

```
GET /novel/rank/{providerId}                                  排行
GET /novel/{providerId}/{novelId}                             元数据（含 toc）
GET /novel/{providerId}/{novelId}/chapter/{chapterId}         章节正文
GET /novel/{providerId}/{novelId}/translate-v2/{tid}/chapter/{cid}
GET /novel/{providerId}/{novelId}/file?mode=&type=            EPUB/TXT 导出
```

Caddy 把 `/api*` 反代到 server，所以公共地址是 `https://n.novelia.cc/api/...`。

**实测结果**：

```
n.novelia.cc  DNS → 104.21.92.192（Cloudflare）
http://   → 301（Cloudflare 正常响应，返回 CF-RAY / Nel 头）
https://  → TLS 握手被重置（ConnectionResetError）
            直连 / 代理 7890 / 代理 7892 / curl / Node fetch   全部 000
```

HTTP 能拿到 Cloudflare 的 301，说明 Cloudflare 本身可达，但 HTTPS 在
ClientHello 之后被重置 —— SNI 层面的干扰。所以**这个 API 现在拿不到**。

另外它的 vite dev 配置里对 `/translate-v2/` 有显式拦截：

```js
if (req.url.includes('/translate-v2/')) {
  console.log('检测到小说章节翻译请求，已拦截');
  return false;
}
```

说明公共实例不打算让外部随便调翻译接口。

---

## 4. 上游站可达性（决定能不能移植适配器）

| 站 | 本机直连 |
|---|---|
| `kakuyomu.jp`（カクヨム，KADOKAWA） | ✅ 200 / 319 KB |
| `alphapolis.co.jp` | ✅ 200 / 468 KB |
| `pixiv.net` | ✅ 200 / 75 KB |
| `ncode.syosetu.com`（成为小说家吧） | ❌ 403（199 B，CF 挡页） |
| `yomou.syosetu.com` | ❌ 403 |
| `syosetu.org`（ハーメルン） | ❌ 403 |
| `novelup.plus` | ❌ 202 空响应（JS challenge） |

kakuyomu / alphapolis / pixiv 三个站能直连，其余要过 CF 或 JS 挑战。

**但这三个站都是日文原版。** 我们没有任何翻译能力，接进来用户也读不了 ——
除非再配一套 JA→ZH 机翻（见下）。

---

## 5. 真正值得看的三样（已留档到 `refs/auto-novel/`）

| 文件 | 值得看什么 |
|---|---|
| `providers/Base.kt` | provider 契约极简：`getRank` / `getMetadata` / `getChapter` 三个方法 + 三档异常（换 ID / 限流 / 无权限）。和我们现在的 `search` / `info` / `episode` 几乎一一对应，说明接口切分是对的 |
| `crawler/kakuyomu.ts`、`alphapolis.ts`、`pixiv.ts` | 三个可达站的真实抓取路径：列表页结构、章节 ID 形式、正文选择器 |
| `packages/translator`（仓库里） | 分段 → 并发翻译 → 合并的 pipeline；Sakura LLM（专门做日→中轻小说翻译）/ OpenAI 兼容 / 有道三种后端；还有术语表（glossary）+ 有道词典查词的术语统一 |

它还有个设计值得抄：**翻译结果按 `(novel, chapter, translator)` 存一份，按需生成**，
不是整本一次翻完。长篇小说这样才对。

---

## 6. 用不上 / 有风险的部分

**用不上**

1. 公共 API —— 本机不可达（上面实测）
2. 中文小说源 —— 一个都没有
3. 整套部署 —— Kotlin + ES + MongoDB + Caddy，为多用户平台设计；
   我们一个 Flask + SQLite 跑得好好的
4. `apps/api`（Next.js）—— 它自己的 auth/banner 服务，跟我们无关

**风险：GPL-3.0**

auto-novel 是 GPL-3.0，我们项目**没有 LICENSE 文件**。如果直接复制它的代码
（哪怕只是翻译 prompt 或分段器几十行），整个项目就得按 GPL-3.0 发布。
要用只能「看思路自己重写」，或者接受 GPL。

留档到 `refs/auto-novel/` 的这些文件**不进仓库**（`refs/` 在 .gitignore 里），
只作为本地参考，性质和 `refs/venera/` 一样。

---

## 7. 结论

**当前不用接。** 理由：

1. 它解决的是「日文轻小说 → 机翻中文」，我们解决的是「中文轻小说 → 本地书库阅读」，
   场景不重叠；
2. 它没有中文源，补不上我们的缺口；
3. 公共 API 本机连不上；
4. 唯一能带走的翻译 pipeline 是 GPL-3.0，且需要额外的 LLM/翻译 API key。

**如果哪天真要接**，最短路径是：

1. 移植 `kakuyomu.ts`（7.3 KB，站可达）→ `novelhub/sources/kakuyomu.py`，
   只做「搜索 / 目录 / 生肉正文」；
2. 正文按段落送 JA→ZH 机翻，缓存到 `data/mt/<源>/<书>/<章>.json`（不复用它的代码，
   自己写 prompt）；
3. 翻译结果当普通章节进 `chapters` 表，阅读器不用改。

这样「生肉 + 机翻」是可选开关，不影响现有的中文源。
