# 小说源：聚合搜索怎么做的、哪些站能用

目标：和漫画一样，输入一个关键词，多个源同时查，结果混在一张列表里，点开就能读。

实测环境：本机 + 本地 Clash 代理（`proxy: auto`），2026-10-24。

## 一、探测结论（都是实测，不是猜）

| 站 | 搜索接口 | 结果 |
|---|---|---|
| **轻之文库 linovel.net** | `GET /search.html?q=xx&type=book` | ✅ 可用，`.work-list li` 解析出书名/作者/封面 |
| **轻小说文库 linovelib.com** | `POST /S6/`（首页搜索框的 action） | ⚠️ **返回 200 + 0 字节**，见下 |
| 轻小说文库（移动/镜像） | `m.linovelib.com` / `bilinovel.com` | ❌ 连接被重置 / 域名 DNS 污染 |
| 文库8 wenku8.net | `GET /modules/article/search.php` | ❌ 读超时（本机网络到不了） |
| ESJ Zone esjzone.cc | 任意 `/search?…` | ❌ 全部回落到同一个「論壇」页，搜索是 JS 驱动的 |
| SF轻小说 sfacg.com | `/search/?keyword=` | ❌ 404（接口路径不对，站点改版） |
| 刺猬猫 ciweimao.com | `/search`、`/get-search-book-list` | ❌ 404 |
| 铅笔小说 23qb.com | `/search/xx.html` | ❌ 403 |
| 半仙/banxia、hetushu、shuquge、bqg128、uuks5、22biqu、trxs、69shu、masiro… | 各种 GET 搜索 | ❌ 全部 TLS 重置 / 超时 / Cloudflare 拦截 |

互联网搜索引擎兜底也试了：`bing`/`cn.bing`/`so.com`/`sogou`/`duckduckgo` 上跑
`site:linovelib.com 天使`，返回的都不是该站结果（site 限定被忽略 / 结果是
`bing.com/ck/a` 跳转壳），不可用。

**所以：能真正按关键词站的只有轻之文库一家。**

## 二、轻小说文库为什么不能搜，以及绕法

首页搜索框是：

```html
<form name="t_frmsearch" method="post" action="https://www.linovelib.com/S6/">
  <input name="searchkey" type="text">
</form>
```

`/S6/` 还挂了段混淆 JS 当作门禁（解出来是）：

```js
var base = '/S6/?search_guard=';
link.href = base + '0';   // css
script.src = base + '2';  // js
```

对 `/S6/` 做过的所有尝试，**全部 200 + 0 字节**（`content-type: text/html`）：

- `GET /S6/?searchkey=…`、`POST /S6/`（表单/JSON/`X-Requested-With`/浏览器全套
  `Sec-Fetch-*` 头）、`http://` 明文、`?search_guard=0/2`、`/S6/天使`
- 站点首页本身是好的（76KB），所以不是代理挂了，是这个端点对非浏览器请求
  直接吐空 —— 典型的 JS/风控门禁。

绕法（已实现）：**扫榜单页建本地书目索引，再拿关键词查索引**。

```
https://www.linovelib.com/modules/article/toplist.php?order=<order>&sortid=<1..20>&fullflag=1&page=<n>
```

- 每页 30 本，卡片 `div.rank_d_list` 里有书名、作者、文库、简介、封面、书籍链接
- `order` 取 8 种：`allvisit / monthvisit / weekvisit / allvote / monthvote / goodnum / postdate / lastupdate`
- `sortid` 1..20（角川、电击、MF、集英社… 每个文库一个）
- 站点 `sitemap.xml` 有全站 **5488 本**书的 `/novel/<id>.html`（无书名），可用来估算覆盖率
- 单榜单翻到 ~100 页后为空，靠 8×20 个榜单的并集叠覆盖

爬完写入 SQLite `novel_index` 表（`source/source_id/title/author/cover/category/url/intro`），
`novel_index_meta.cursor` 记游标，**可断点续扫**。

```powershell
python nh.py index --pages 300     # 本次最多扫 300 页，再跑一次继续
python nh.py find "转生"            # 离线验证：索引 + 本地书架
```

实测：榜单模式 300 页 → **3197 本**；再扫 180 页唯一书目停在 3198 不动（排行榜高度重叠）。
之后靠深度模式补到 **5411 本 / 全站 5488（98.6%）**，共扫 478 页榜单 + 2287 次书籍页请求。

按文库分布：其他文库 1239、电击文库 640、角川文库 558、MF文库J 346、富士见文库 341。
搜索「转生」命中 8 条、含《转生成蜘蛛又怎样！》《关于我转生变成史莱姆这档事》《无职转生》等；
搜索「天使」命中《天使的3P！》《扑杀天使朵库萝》《Lateral～水平思考推理的天使～》。

### 榜单模式的天花板 → 深度模式

再扫 180 页（`monthvisit` 及之后）后**唯一书目数停在 3198 不动**了 —— 各排行榜高度重叠，
榜单模式的天花板就是 3200 本左右（全站 5488 本，58%）。

于是加了深度模式：`sitemap.xml` 里有全站 5488 本的 `/novel/<id>.html`（没书名），
按这个顺序**逐本抓书籍页**，从 `og:novel:*` 元信息里取书名/作者/文库/封面/简介/状态：

```powershell
python nh.py index --deep --pages 1500   # 本次最多抓 1500 本，再跑一次继续
```

书籍页元信息很完整（实测 `novel/4242.html`）：

```
og:novel:book_name = 这场恋爱是全世界最美的雨
og:novel:author    = 宇山佳佑
og:novel:category  = 日本轻小说 集英社
og:novel:status    = 完结
og:image           = .../files/article/image/4/4242/4242s.jpg
```

已索引的会跳过，游标存 `novel_index_meta` 的 `linovelib-deep`，**可断点续抓**。
每本一次请求（~1s），1500 本约 25 分钟一轮；`--pages` 就是本轮请求预算。

网页上也能建：搜索页「选择源」面板里点 **建索引 / 继续扫**，进度实时显示
`正在扫：12/60 页 · 已索引 560 本 · allvisit/sid4/p3`。

## 三、代码结构

| 位置 | 作用 |
|---|---|
| `novelhub/sources/linovel.py` `search()` | 轻之文库站内搜索 |
| `novelhub/sources/linovelib.py` `parse_listing()` / `build_index()` | 榜单解析 + 可续扫索引器 |
| `novelhub/store.py` `upsert_novel_index / search_novel_index / novel_index_meta` | 索引存取 + 相关度排序 |
| `novelhub/aggregate.py` `NOVEL_SOURCES` + `_novel_*` | 三个小说源并发调度 |
| `novelhub/webapp/app.py` `/api/aggregate?novels=…`、`/api/novelindex/build|status`、`/add?url=` | 网页接口 |
| `nh.py index` / `nh.py find` | 命令行 |

小说源有三个，可逐个勾选：

1. `linovel` 轻之文库（站内搜索，实时）
2. `linovelib` 轻小说文库（书目索引，本地）
3. `local` 本地书架（书名/作者命中 + **正文命中**，正文命中直接跳那一章）

结果里 `action` 决定点进去干什么：

- `open` + `book_id` → 已在书架，直连 `/book/<id>`
- `add` → 不在书架，走 `/add?url=<原文页>`：先秒回一个「正在读取目录…」页，
  再后台抓目录入库，最后跳到书页（实测 0.02s + 3.7s）

## 四、还没做到的

- 轻小说文库「全站全文搜索」：需要真浏览器过 JS 门禁（headless Edge/Chrome
  保活 cookie），这跟之前 `mycomic` 需要 `cf_clearance` 是同一类问题。
- 索引覆盖率上限受榜单页深度限制（单榜 ~100 页 × 30 本）。要 100% 覆盖 5488 本，
  得逐本抓书籍页拿书名（5488 次请求），目前没做。
- 其它小说站：本机网络到不了，已记录在第一节的表里，代码里留了 `NOVEL_SOURCES`
  可插拔位置，能连上时补一个 `_novel_xxx()` 即可。
