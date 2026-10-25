# 书萤 · 本地轻小说 / 漫画 / 动漫书房

> 名字取自「书页如翼，一点萤光」：书架上的书像萤火虫一样，在夜里发一点光。
> 图形标 `novelhub/webapp/static/brand/`（`mark.svg` 纯白标 / `favicon.svg` 应用图标 / `lockup.svg` 横版）。
> 项目代码里仍叫 `novelhub`，只有对外显示名换成了「书萤 / FIREFLY LIBRARY」。

把轻小说站点抓下来、把本地 txt/epub 导进来，全部汇总到同一个 SQLite 书库，
再用自带的网页阅读器看，支持导出 EPUB / TXT / Markdown。

```
抓取站点 ─┐
本地文件 ─┼─→ data/library.db ─→ 网页阅读器 / 导出 EPUB·TXT·MD
目录导入 ─┘
```

## 四种用法

同一个书库，四种壳。数据都在 `data/library.db`，换壳不搬数据。

| 用法 | 怎么起 | 适合 |
| --- | --- | --- |
| **独立 exe** | 双击桌面「书萤」快捷方式 | 不想装 Python，拷走整个文件夹就能用 |
| **Android APK** | 装 `dist/android/书萤-debug.apk` | 手机离线看，自带 Python 与整套后端 |
| **手机 / 平板（免装）** | 浏览器打开，菜单里「装到主屏」 | 不想装 APK，断网也能读已缓存的章节 |
| **命令行** | `python nh.py serve` | 改代码、调源、批量抓 |

### 独立应用 exe

```powershell
python -m pip install pyinstaller
python build\make_icons.py                    # 生成 ico / PWA 图标
python -m PyInstaller novelhub.spec --noconfirm --clean
python build\make_shortcut.py                 # 桌面 + 开始菜单快捷方式
# → dist\书萤\书萤.exe（onedir，整个文件夹拷走即可）
```

双击之后：单实例检查 → 选端口（8899 被占就往后找）→ 起服务 →
用 Edge/Chrome 的 `--app` 模式开一个无地址栏窗口 → 关窗即退出。

数据落在 exe 旁边的 `data/`；那个目录不可写时自动退到 `%LOCALAPPDATA%\FireflyLibrary`。
要指定位置就设 `NOVELHUB_DATA_DIR`，要指定配置就设 `NOVELHUB_CONFIG`。

命令行参数：`--no-window`（只起服务）、`--port 9000`、`--console`（保留控制台排查）。
启动失败会弹窗并把堆栈写到数据目录的 `startup-error.log`。

`build\make_shortcut.py` 还有 `--desktop-only` / `--start-menu-only` / `--uninstall`。

### Android APK

用 [Chaquopy](https://chaquo.com/chaquopy/) 把 CPython 3.11 打进 APK，
Flask 在手机本地跑，WebView 指过去。**模板、样式、业务逻辑与桌面端是同一份代码**，
不维护第二套 UI。

```powershell
powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
# → dist\android\书萤-debug.apk（约 46 MB）
adb install -r dist\android\书萤-debug.apk
```

`build_apk.ps1` 会自动 `sync_sources.py` 同步 Python 源码、检查工具链、
构建后把 APK 拷回 `dist/android/`。

构建需要：JDK 17、Gradle 8.9、Android SDK（minSdk 24 / targetSdk 35）。
脚本默认在 `C:\bookfly-android` 构建 —— AGP 会拒绝含非 ASCII 字符的工程路径，
这台机器的用户名是中文，拷到纯 ASCII 目录最省事。

手机上数据在 App 私有目录（`/data/data/com.firefly.library/files/firefly/`），
卸载即清除。默认关代理、开 `localize_images`（插图落本地，断网可看）。

看日志：`adb logcat -s BookFly python.stderr`

### 手机版（PWA，免装）

服务起来后用手机访问同一台机器的地址（局域网要 `--host 0.0.0.0`），
浏览器菜单选「添加到主屏幕 / 安装应用」。装完之后：

- 独立窗口，没有地址栏
- 已读过的章节离线可看（Service Worker 缓存页面外壳与静态资源）
- 阅读页左右滑动翻章，`←` `→` 也能用
- 底部导航和阅读工具栏都避开了刘海与小黑条

移动端样式集中在 `static/mobile.css`（最后加载），改手机端只动这一个文件。

### 命令行

```powershell
cd C:\Users\韩凌云\Desktop\novel

python nh.py proxy                                    # 先确认能连通（会自动探测本地代理）
python nh.py inspect "https://www.bilinovel.com/novel/3805/326116_2.html"   # 只看目录
python nh.py add "https://www.bilinovel.com/novel/3805/326116_2.html" --limit 5   # 试抓 5 章
python nh.py add "https://www.bilinovel.com/novel/3805/326116_2.html"       # 全书（后台跑）
python nh.py serve                                    # 打开 http://127.0.0.1:8899
```

Windows 双击也行：`启动阅读器.bat`。

### 验收脚本

```powershell
python selftest.py                 # 后端全链路自测（抓取/解析/导出/搜索/历史）
python design\_mobile_audit.py     # 手机端：3 档视口 × 11 个页面，查横向溢出/触控尺寸/PWA 装配
python design\_illus_audit.py      # 插图：真实浏览器里查 <img> 数量/是否解出像素/尺寸/溢出
```

`_mobile_audit.py` 和 `_illus_audit.py` 需要先 `python nh.py serve --port 8920`，
结果分别写到 `design/shots/_mobile.json` 与 `_illus.json`。

插图那条为什么不能只看 HTTP 200：`/api/img` 代理失败时会返回 502 + 占位 SVG，
`<img>` 仍然「加载成功」。所以脚本查的是 `naturalWidth > 0`（浏览器真的解出了像素）
和盒子尺寸是否撑满正文列。

## 依赖

已经在本机装好：requests / beautifulsoup4 / lxml / Flask / EbookLib / zhconv / cryptography。
换机器时：

```powershell
python -m pip install -r requirements.txt
```

`zhconv` 只用于简繁转换（没装会退回 APK 带的 OpenCC 单字表），
`cryptography` 只用于拷贝漫画网页版的 AES 解密。

## 代理（很重要）

`bilinovel.com` / `linovelib.com` 在国内网络下 DNS 被污染、并且套了 Cloudflare，
直连会拿到 403 或者超时。程序的处理方式：

- `config.json` 里 `"proxy": "auto"` → 自动探测本机 7891 / 7890 / 10809 / 1080 … 等端口，
  探测到就直接用作 HTTP 代理（现在用的是 `http://127.0.0.1:7891`）。
- 想固定代理：`"proxy": "http://127.0.0.1:7890"`，或 `"proxy": ""` 走直连。
- 临时覆盖：`$env:NOVELHUB_PROXY="http://127.0.0.1:7890"`。
- 连通性自检：`python nh.py proxy`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `python nh.py inspect <URL>` | 解析书名/作者/分卷/章节数，不下载正文 |
| `python nh.py discover <列表页> [--top N]` | 从排行榜/分类页解析书单 |
| `python nh.py batch <列表页> [--top N] [--full]` | 批量建目录（默认只建目录，几秒一本） |
| `python nh.py add <URL> [--limit N] [--vols 1,3-5] [--force]` | 抓取入库（断点续传，已抓章节自动跳过） |
| `python nh.py resume <book_id>` | 补抓未完成章节 |
| `python nh.py list` | 列书库 |
| `python nh.py search <关键词>` | 本地全文搜索（书名/章节名/正文） |
| `python nh.py index [--pages N]` | 扫轻小说文库榜单页建书目索引（可续扫） |
| `python nh.py find <关键词>` | 离线查书目索引 + 本地书架 |
| `python nh.py export <book_id> --fmt epub\|txt\|md\|html [--vols 1-3] [--no-images]` | 导出 |
| `python nh.py import <文件或目录>` | 导入本地 txt / epub / md |
| `python nh.py serve [--port 8899]` | 启动网页阅读器 |
| `python nh.py delete <book_id>` | 删除一本书 |
| `python nh.py sources` | 列出适配器与当前代理 |

`URL` 可以是目录页、书籍页，也可以是任意章节页（比如你贴的 `326116_2.html`），
程序会归一化成这本书的目录 URL。

## 网页阅读器

`python nh.py serve` 后访问 http://127.0.0.1:8899

- **书架**：封面墙、抓取进度条、「继续阅读」直接跳到上次章节
- **目录**：按分卷折叠，未抓章节有标记，可一键「补抓未完成章节」
- **阅读**：字号/行距/版心/四套主题（纸·黄·绿·夜），`←/→` 翻章，滚动位置记忆
- **搜索**：跨书全文命中，带上下文片段
- **抓取**：填 URL → 后台线程跑 → 进度页实时刷新（不会卡住页面）
- **导入**：上传 txt/epub/md，或直接填磁盘路径批量导入
- **导出**：书架/目录页点 EPUB、TXT、Markdown 直接下载

### 界面：星屑书房（二次元皮肤，2026-10）

第一版是「纸上书房 / Paper Study」（衬线标题、朱砂强调色、纸感噪点）；后来在上面又叠了一层
二次元皮肤「星屑书房 / Hoshikuzu Library」：樱花渐变、贴纸相框、星星花瓣，外加六位驻场角色。
方案与组件规范见 `docs/UI-redesign.md`，高保真原型在 `design/prototype.html`。

样式是三层叠加，改的时候从后往前找规则：

| 文件 | 作用 |
| --- | --- |
| `static/style.css` | 最早的旧样式，只剩抓取/导入/书源等老页面在用 |
| `static/ui.css` | 纸上书房设计系统（token + 组件），老页面靠它换肤 |
| `static/otaku.css` | **二次元皮肤，最后加载，覆盖前两层** |

- **应用外壳**：左侧固定导航（媒体 / 我的 / 管理 三组 + 今日担当卡），顶部面包屑 +
  全局搜索（`⌘K` / `Ctrl+K` / `/` 聚焦），右上角添加 / 主题 / 刷新
- **四套主题**：樱 / 昏 / 薄荷 / 夜（键名仍是 `paper/sepia/green/night`，不动 `read.js` 和
  `localStorage`），右上角月亮图标或阅读页「Aa」里切，刷新不闪白
- **书架**：Hero 区（统计 + 相框立绘 + 对话气泡）、继续阅读横排卡、在读/已下载/在线分段筛选、
  书名即时筛选、三种排序、封面墙 ↔ 紧凑列表切换
- **目录页**：大封面 + 统计条 + 简介 + 进度卡 + 角色便签（栞报进度），章节按分卷两栏排
- **阅读页**：沉浸式，顶部进度条，底部悬浮 dock，左右抽屉分别是目录 / 显示设置，
  `←/→` 翻章、`Esc` 关面板；阅读设置变量固定用 `--fs / --lh / --measure`
- **响应式**：≥1180 完整侧栏 → ≤1024 侧栏收成 78px 图标条 → ≤900 Hero 改上下结构 →
  ≤760 手机底部 Tabbar + 「更多」抽屉 → ≤420 再缩一档
- **无障碍**：图标按钮都有 `aria-label`，键盘可走完主要流程，`prefers-reduced-motion` 下关动效

#### 角色系统

六位角色各管一块，`data-page`（Jinja 注入 `request.endpoint`）决定当前页面该谁出场：

| id | 角色 | 担当 | 出场 |
| --- | --- | --- | --- |
| `shiori` | 栞 | 图书委员 | 书架 / 目录页 |
| `miku` | 美玖 | 追番担当 | 动漫 |
| `rin` | 凛 | 漫画担当 | 漫画 |
| `yuki` | 小雪 | 伴读担当 | 阅读页（左下角看板娘） |
| `aju` | 小蛛 | 书源担当 | 抓取 / 导入 / 书源 |
| `shirakaba` | 白桦 | 馆长 | 历史 / 排行 |

- 侧栏「角色」或今日担当卡打开**角色图鉴**：点一张卡即常驻该角色，底部两个开关分别是
  「跟随页面」和「显示看板娘」，选择存 `localStorage`（`nh-cast` / `nh-mascot`）
- 立绘是 6 张插画（1440×1920），脚本裁掉底部水印后生成两套：主图 720×913、头像 280×280，
  都放在 `static/cast/`；`cast.js` 里 `FACE_BASE` 从当前 `<img>` 的路径推目录，换目录不用改代码
- 台词池按页面上下文（书架有在读 / 空书架 / 目录页进度 / 阅读页）挑句子，点气泡换下一句

实现上旧页面（抓取 / 导入 / 发现 / 书源 / 漫画 / 动漫）没有改 DOM，靠 token 自动换肤。
注意：模板改动**要重启服务**才生效（`jinja_env.auto_reload` 在这个项目里实测没生效，
CSS/JS 是静态文件，刷新即可）。

### 阅读：在线读 / 插图 / 换源

**在线读**（目录有、正文没抓的书）：进阅读页先出骨架，前端用 SSE 边抓边显示，
每抓到一页推一段。`persist_chapters` 只管“抓下来要不要写库”，**不再影响要不要抓** —
之前写成 `not cfg.persist_chapters and 没正文` 才触发，结果 `persist_chapters: true` 时
这类书进阅读页就是一页空白（已修）。

- 已抓过的章节直接读库，不走网络（0.06s）
- 点标题下的「在线读（不落库）」可强制走源站
- 断流自动重连 2 次，4 秒无首段自动重试一次，再失败才给失败块

**翻章不等待（预加载下一章）**：翻页逻辑没变，还是点「下一章」翻，但**下一章的正文会提前抓好**。

- 进入任意一章后，阅读器空闲时调 `/api/prefetch?bid=&idx=&n=2`，后端把后面 2 章抓进
  本地缓存（`data/livecache/<bid>/<idx>.json`，24h TTL，**不写数据库**）
- 已经落库的章节直接算「已缓存」，不重复抓
- 下一章就绪后，底部「下一章 →」按钮变成高亮并显示「· 已缓存」，tooltip 写「下一章已缓存，点开即读」
- 翻过去时 SSE 第一帧就是正文，不用再等源站

实测（书 13《妹妹是不能当女友的》第 30 章，正文未抓）：

```
冷读正文首段   4.92s   ← 真去源站抓
预热后同一章   0.03s   ← 命中本地缓存，快 172x
```

`config.json` 的 `prefetch_count` 控制预热几章（默认 2，接口里上限 5）。

**插图**：正文里的 `<img>` 会被改写成 `/api/img?u=…&ref=…` 由后端带 Referer 抓回来
（源站 CDN 有防盗链，浏览器直连 403）。本地已本地化的图直接走 `/media/<bid>/…`。

加载过程有明确提示，不会让你盯着空白：

- 每张图先占位（骨架流光 + 「插图 3 加载中…」），占位块固定 220px 高，页面不会跳动
- 章节标题下有个计数徽章：`插图 15 张 · 已加载 4 · 加载中…`，全部到位后变成灰字
- 单张失败变红块「插图加载失败 · 点这里重试」，点一下带 `&r=时间戳` 重新拉
- 在线读走 SSE 时，每来一段就登记新插图，计数会跟着涨

**换源阅读**：书页有「换源阅读（找同名）」，去别的小说源找同一本：

- 先按全书名精确查，没有就按书名前 4 字给「相近」候选
- 每行一个「用这个源读」，点了走 `/add?url=` 把那个源的版本作为另一份加入书架
- 实测：造一本 `local` 的《无职转生》→ 换源页列出哔哩轻小说的同名书籍

> 小说目前只有两个源（轻之文库 / 哔哩轻小说），同一本书两边都有的不多，所以换源经常是空的；
> 漫画有 22 个源，换源命中率高得多。源越多这个功能越好用。

## 已支持的站点

| 适配器 | 站点 | 状态 | 说明 |
| --- | --- | --- | --- |
| `linovelib` | bilinovel.com、linovelib.com、tw.linovelib.com | ✅ 实测通过 | 自动归一到 PC 模板 `www.linovelib.com` 取**全文**；移动模板一页只给约 480 字就截断，只作兜底 |
| `linovel` | www.linovel.net（轻之文库） | ✅ 实测通过 | 书籍页 `.volume` 分卷、章节 `.article-content` 单页全文（实测 3185 / 3244 字每章） |
| `wenku8` | wenku8.net / .com / .la | ⚠️ 本机线路不通 | 适配器在（GBK、`index.htm` 目录），探测时 SSL 错误/超时，换线路可再试 |
| `narou` | ncode.syosetu.com | ⚠️ 直连 403 | 适配器在（`.p-novel__body`），该站拦 bot，需要带 cookie 的线路 |
| `generic` | 任意静态站 | ➕ 配置即用 | 在 `config.json` 里写 CSS 选择器，不用改代码 |

> 实测不可达（这条线路上）：`qbtr.cc`、`23qb.com`（连接全挂）、`esjzone.cc`（首页是论坛壳）、`kakuyomu.jp`（首页 200 但正文 JS 渲染，需浏览器内核）。

> 同一本书在 `bilinovel.com`（移动模板，`#acontent`）和 `www.linovelib.com`
> （PC 模板，`#TextContent`）上字数差 5 倍以上：移动模板每页只吐约 480 字。
> 程序因此始终走 PC 模板。

### 接入新站点（不用写代码）

编辑 `config.json` → `sources.generic`，加一条：

```json
{
  "id": "mysite",
  "name": "某轻小说站",
  "enabled": true,
  "url_re": "https?://(?:www\\.)?example\\.com/book/(\\d+)",
  "catalog_url": "https://www.example.com/book/{id}/",
  "title": "h1.book-name",
  "author": ".book-author",
  "cover": "img.cover@src",
  "description": "#intro",
  "volume": "div.volume-title",
  "chapter": "div.volume-list a",
  "content": "#content",
  "next_page": "a.next@href",
  "drop": ["div.ad", "script"],
  "encoding": "",
  "delay_factor": 1.0
}
```

- 选择器写法：`css选择器`、`css@属性`、`meta:og:title`、`meta:name:description`
- `volume` 可留空（单卷）；`next_page` 用于一章拆多页的站点
- 保存后 `python nh.py sources` 里能看到 `mysite`，直接 `add` 即可

## 目录结构

```
novel/
├── nh.py                    命令行入口
├── config.json              代理 / 限速 / 自定义站点
├── novelhub/
│   ├── config.py            配置与代理自动探测
│   ├── net.py               HTTP 层：重试、限速、编码识别、缓存
│   ├── store.py             SQLite 书库（books/volumes/chapters/progress
│   │                        + comics/animes/read_history + 搜索）
│   ├── crawler.py           抓取编排（目录→章节→分页合并→断点续传）
│   ├── textutil.py          正文清洗（去广告/提示语、图片绝对化）
│   ├── zhconvert.py         简繁转换（zhconv 优先，APK 词表兜底）
│   ├── aggregate.py         聚合搜索（小说 / 漫画 / 动漫三路并发）
│   ├── export.py            EPUB / TXT / MD / HTML 导出
│   ├── importer.py          本地 txt/epub/md 导入与切章
│   ├── sources/             站点适配器
│   │   ├── linovelib.py     哔哩轻小说（爬虫 + 书目索引）
│   │   ├── linovel.py       轻之文库
│   │   ├── bangumi.py       Bangumi 书目
│   │   ├── anime.py         苹果CMS 采集接口（番剧）
│   │   ├── copy_manga.py    拷贝漫画（网页版 + AES 解密）
│   │   └── venera_js.py     Venera 兼容 JS 源的注册与调用
│   ├── jsruntime/runner.mjs Node 执行器（宿主 API 对齐 Venera 的 init.js）
│   ├── data/                内置数据（opencc 简繁词表、标签表）
│   └── webapp/              Flask 阅读器
│       ├── templates/       index/book/read/comics/history/animes/play/…
│       └── static/          style.css / read.js / comic.js / search.js
│                            / player.js / refresh.js / app.js
├── refs/
│   ├── venera/              Venera 源（39 个 .js）+ init.js + 清单
│   └── anime/rules.json     Kazumi 兼容的番剧规则
├── Venera-RE/               Venera APK 逆向产物（报告 + 反编译）
├── data/
│   ├── library.db           书库
│   ├── cache/               HTML 与图片缓存
│   └── covers/              封面
└── exports/                 导出结果
```

## 抓取策略

- 默认 `delay=1.2s` + 最多 `0.8s` 抖动，单线程；站点风控受不了更快的节奏。
- 每章按「同 chapterid 的 `_N` 分页」自动合并，一本书的章节顺序以目录页顺序为准。
- 抓过的章节写进 `chapters`，`add` / `resume` 只抓空的，中断了随时继续（Ctrl+C 也不丢）。
- 每页 HTML 缓存到 `data/cache/`，重复调试不重复请求；`"offline": true` 可纯离线重跑清洗。
- 单章失败只记录不中断，最后汇总打印；EPUB 导出时 `--no-images` 可跳过插图下载。
- 插图章（`插图`/`彩页`）里的 `<img>` 会被保留，导出 EPUB 时会下载并内嵌。

## 漫画：跑 Venera 的 JS 源

`refs/venera/` 里放的是 Venera（开源漫画阅读器）格式的源，本项目的 Node 执行器
（`novelhub/jsruntime/runner.mjs`）实现了它的宿主 API，可以直接跑这些 `.js`：
`Network.*`（含 cookie 罐）、`Convert.*`（含 AES/RSA/GBK）、`fetch`、
`HtmlDocument/HtmlElement`（linkedom）、`Comic / ComicDetails`、`loadData/saveData`、
`loadSetting`、`log / randomInt / randomDouble / createUuid / compute`。

```powershell
python nh.py js list                                  # 源清单 + 安装状态
python nh.py js search ManHuaGui "女黑手党" --limit 5  # 搜索
python nh.py js info  ManHuaGui 51517                 # 详情 + 章节
python nh.py js ep    zaimanhua 72283 146548          # 下载一话 + 生成预览页
```

### 照着反编译出来的 APK 对齐了执行器

`Venera-RE/` 里有 `com.github.wgh136.venera` v1.6.1 的逆向产物。真正有用的是它
`assets/init.js`（宿主 API 的完整契约，37.6 KB）——里面每个方法都写清了
`sendMessage` 的字段名和参数顺序。对着它把执行器补齐：

| 补齐 | 效果 |
|---|---|
| `Convert.sha512` / AES ECB,CBC,CFB,OFB / `decryptRsa` | `GfmhApp` 从 0 条变 5 条；`ccc`、`jm`、`manwaba` 的加密链路能跑 |
| `Convert.decodeGbk` | 国内站的 GBK 页面能正确解码 |
| `Network.patch/delete/fetchBytes` | 需要 ArrayBuffer 响应的源能跑 |
| `Network.deleteCookies` | 7 个源用它清登录态后重试 |
| `log / randomInt / randomDouble / createUuid / compute` | 9 个源直接调用这些全局函数 |
| `UI.*`（6 个弹窗族） | 无头环境写日志 + 返回默认值，不再抛错 |

**顺手修掉两个真 bug**（不是缺 API，是语义不对）：

1. `loadSetting(k)` 原来返回 `null`。Venera 的语义是"用户没改过就返回 schema 里的 default"。
   `jm.js` 用 `parseInt(loadSetting('apiDomain')) - 1` 选镜像域名，`null` → `NaN` →
   崩在 `jm.js:40`。现在读 `settings[k].default`，允许 store 覆盖。
2. `init()` 没有 `await`。源普遍是 `async init()`（里面拉域名表、算签名种子），
   原来初始化和业务并发跑，`jm` 的 `static apiDomains` 还没填就去读 → undefined。
   现在全部 `await src.init?.()`。

静态扫描 37 个源实际用到的 API：**补齐前 18/37 能跑，补齐后 37/37。**

### 源仓库换了真实地址，多出 15 个源

对象池里挖到 App 实际用的清单地址：

```
https://git.nyne.dev/nyne/venera-configs/raw/branch/main/index.json
```

之前用的是 jsdelivr 镜像那份（22 条），真实清单有 **31 条**。取并集后本地 **37 个**。
新增里能跑通的：`hot_manga` 热辣漫画（20 条/1.3s）、`jcomic`（29 条/4.0s）、
`wnacg` 紳士漫畫。新增但当前网络跑不通的见 `docs/comic-sources.md`。

实测 37 个源：**10 个真能出结果、5 个空返回、22 个失败**。失败原因都不是引擎问题，
分三类：站点的 Cloudflare / 需要浏览器 cookie（`baozi`、`mycomic`、`ccc`）、
站点拒绝当前出口 IP 的 TLS（`ehentai`、`hitomi`、`mh18`、`hcomic`、`happy`）、
要自建服务器（`kavita`、`komga`、`lanraragi`）。

## 繁体源转简体（词表也从 APK 里拿的）

哔哩轻小说的正文是繁体，读起来费劲。APK 的 `assets/opencc.txt` 是 Venera 给漫画标签
用的 OpenCC 单字表（3980 条），复制到 `novelhub/data/opencc_s2t.txt`。

但实测这表**是子集**，做正文转换会漏字：

```
這裡有一個問題，妳知道嗎？   APK表 → 这裡有一个问题，妳知道吗？
                          zhconv → 这里有一个问题，你知道吗？
麵包也一起吃               APK表 → 麵包也一起吃
                          zhconv → 面包也一起吃
```

所以 `novelhub/zhconvert.py` **优先用 `zhconv`**（完整字表 + 词组消歧），
APK 词表作为没装 zhconv 时的兜底。转换范围：阅读页正文 + 章节标题 + 书名 +
目录页章节名 + 书架卡片标题。

开关在 `config.json`：

```json
"convert_to_simplified": true
```
关掉就原样输出繁体。实测：`不相容的異種族妻子們` → `不相容的异种族妻子们`。

## 拷贝漫画：内置源（资源最新）

`copy_manga` 不走 JS 运行时，是 `novelhub/sources/copy_manga.py` 里的 Python 源，
直接对接网页版。资源是这批源里最新的，默认参与聚合搜索，也排在小说的漫画源列表第一位。

它绕过了两件麻烦事：

**1. App 接口的 IP 配额。** `api.copy2000.online/api/v3/comic2` 对同一出口 IP 记账，
额度极小，超了返回 `code 210`（"请更新最新 APP……等待 1 小时"）。补齐官方 3.0.9 的
全量请求头（含 HMAC 签名、设备指纹、`umstring`）、换 14 个版本号、换 4 组设备指纹、
换 `region`、换出口 IP、换 6 个域名、换 TLS 指纹、会话预热、等 1/2/3 分钟 —— 全部照样 210。
所以改成走网页版，网页版没有这层限制。

**2. 网页版内容加密。** 目录和图片列表都是 AES 密文，格式是
`<16 字符 IV><hex 密文>`，key 是页面里的 16 字符常量 `cct`（当前 `0p0zzpvv.nmn.oop`），
按 UTF-8 取字节做 AES-128-CBC + PKCS7。章节列表还要把详情页的
`<span id="dnt" value="9">` 作为 `dnts` 请求头发回去。

```powershell
python nh.py js list                                          # 源清单
# 网页上：/search?q=進擊的巨人&kind=comic  → 勾「拷贝漫画」
```

实测（`jinjidejuren` 進擊的巨人）：

| 动作 | 耗时 | 结果 |
|---|---|---|
| 搜索 | 1.0s | 5 条 |
| 详情 | 6.6s | 72 话 + 全彩版 2 话 |
| 第一话图片 | 0.9s | 35 张 |

四个镜像域名（`www.copy4000.com` / `copy4000.com` / `www.copy3000.com` / `copy3000.com`）
单次成功率 20~60%、偶发 `ReadTimeout`/`SSLError`，所以是**并发打四个、谁先成功用谁**，
并把 `contentKey` 为空（7 KB 空壳页）的响应当失败跳过。串行试最坏 40s+，并发首胜稳定 6~8s。

协议细节、绕不过 210 的完整实验记录见 `docs/comic-sources.md` 的「拷贝漫画」一节。

## 失败重试与自选源

**加载失败先重试，再判断"有没有"**

- 小说阅读：SSE 断流会**自动重连 2 次**（退避 0.8s/1.6s），4 秒无首段也会自动重试一次；
  仍失败才出失败块：`重新加载` / `同步模式再试` / `刷新本页`，并说明"可能源站没有、需要登录或被删"。
- 漫画阅读：图片列表接口失败同样自动重试 2 次；单张图失败会标红，**点一下图片重试**（绕过缓存）。
- 已抓过的章节直接读库，不走网络，所以不会出现"本地有还去重试"的浪费。

**源自己选**

搜索页有「选择源」面板：

- 小说源：轻之文库（在线）
- 漫画源：列出所有**已安装**的源，勾选谁就只查谁；「全选 / 全不选」一键切换
- 选择会写进 URL（`&comics=zaimanhua,dm5&novel=0`），刷新、分享都保持；页面显示
  「N 条 · M/K 个源有结果」和总耗时

实测：只勾 `zaimanhua,dm5` → 16 条，源集合正好这两个；`novel=0` → 只出漫画；
一个都不勾 → 明确提示"没有选择任何源"。

漫画详情页有「换源（找同名）」→ 跳到聚合搜索，用书名到别的源再找一份。

**批量索引翻页**

```powershell
python nh.py batch "https://www.linovelib.com/topfull/allvisit/1.html" --top 4 --pages 2
# 实测：翻 2 页去重后索引 4 本，1142 章目录
```

`--pages N` 支持尾部数字页（`allvisit/1.html` → 2、3…）和 `{p}` 占位符两种写法。

## 源排行榜（`/sources/rank`）

顶栏「源」→ 页面里的「看排行榜」，或直接开 `/sources/rank`。

每个源都用同一个关键词**真跑一遍**（小说「天使」/ 漫画「海贼王」/ 动漫「进击的巨人」），
按「能不能用 + 快不快 + 结果多不多」打 0~100 分：

```
分 = 60 − min(耗时ms/120, 40) + min(结果数, 15) − (结果数==0 ? 15 : 0)
失败/超时 = 0 分
```

表格分小说 / 漫画 / 动漫三块，每行显示分数、耗时、结果数、状态，还有一列 **聚合**：

- `在用` = 这个源正在参与聚合搜索（小说三源固定；漫画看 `comic_sources_default` + 启用状态；动漫看 `anime_sources_default`）
- `在用` + `不可用` 会标红并加「建议关掉」

三个按钮：

| 按钮 | 作用 |
|---|---|
| 全部重测 | 三类全跑（约 2~3 分钟，31 个源） |
| 只测可用的 | 逐行打单源接口，坏源完全不碰，跑完自动刷新 |
| 摘掉在用的坏源 | 把「在用但不可用」的源从 `config.json` 默认列表里删掉，漫画源同时禁用 |

每行右侧还有「重测」，只测这一个源、原地更新那一行、不刷新整页
（`POST /api/sourcehealth/retest`）。

**实测结论（2026-10）**

- 小说 3/3 可用；漫画 22 个源里约 10 个可用、12 个坏（ECONNRESET / 403 / 缺 `cf_clearance`）；
  动漫 6 个源全可用，但 `guangsu` 要 27~33s、`wujin` 3~22s 波动极大。
- 排行榜第一次跑完就抓出真问题：`GfmhApp`/`ikmmh`/`ykmh`/`manwaba` 四个**坏源正躺在默认聚合列表里**，
  每次搜索都在白等它们超时。点「摘掉在用的坏源」后漫画默认源从 10 个降到 6 个健康源。
- **单次探测不可信**：同一天测 `apibdzy` 得到 3s / 8s / 45s / 89s / 163s 五个值，
  `wujin` 3.6s / 12s / 87s / 98s。这是代理线路在抖，不是代码问题 —— 所以才有「重测」按钮。

### 顺手修掉的两个真 bug

1. **代理探测每次都重跑**：`Fetcher.__init__` 每次构造都探一圈本地代理端口，
   单次 **3.5 秒**；而 `_get_json` 每个请求都新建 Fetcher。
   改成进程级缓存（TTL 60s，`invalidate_proxy_cache()` 可强制失效）后，
   第二次起 **0ms**，轻之文库搜索 3519ms → 64ms，再漫画 4107ms → 611ms。
2. **`_rotate_proxy` 回写共享 `cfg.proxy`**：一个源换过节点后，后面所有源都继承那个节点，
   越跑越慢（`wujin` 22s → 50s 超时）。改成只改实例自己的 session / `self.proxy`。
3. **`_clean()` 不认 int**：MacCMS 的 `vod_total`/`vod_id`/`vod_year` 有时给 int，
   直接 `re.sub` 就炸 `expected string or bytes-like object, got 'int'`。
   表现是动漫聚合在 `limit=8` 时 `girigiri`/`ffzy` 双双失败，只剩 `apibdzy` 3 条；
   修好后动漫聚合 **3 条 → 19 条**（三源全回）。



## 阅读历史

顶栏「历史」→ 按时间倒序列出读过的东西，小说 / 漫画 / 动漫分开计数，也能按类型筛选。

和「阅读进度」的区别：进度（`progress` / `comic_progress` / `anime_progress`）每个作品只存
一条，表示"当前读到哪"；历史（`read_history`）记"读过什么"，一个作品一行，
累加打开次数、更新最后阅读时间。所以清空历史不会丢阅读进度。

| 字段 | 说明 |
|---|---|
| `kind` | `novel` / `comic` / `anime` |
| `source` / `source_id` | 哪个源、哪个作品 |
| `title` / `sub` / `cover` | 书名、作者、封面 |
| `url` | 「继续」跳转的落地地址 |
| `chapter_label` | 「读到 第3话 仙台同学很甜什么的」 |
| `hits` | 打开过几次 |
| `first_read_at` / `last_read_at` | 首次 / 最近 |

写入点：打开 `/read/...`、`/comic/<源>/<id>/<话>`、`/play/<源>/<id>/<集>` 时各记一次。
漫画的章节名是前端异步拿的，所以 `/api/comic/progress` 会顺手回填
`chapter_label`，并且用 `bump=False` 只补字段、不重复算一次打开。

历史页可以单条「移除」或「清空」（清空还能只清当前分类）。

```sql
-- 想手动看一眼
SELECT kind, title, chapter_label, hits, last_read_at
FROM read_history ORDER BY last_read_at DESC LIMIT 20;
```

## 聊天：七位管家（`/chat`）

侧栏「我的 → 聊天」。这不是又一个问答框，是**把书房的六个看板娘请上岗**：
宫城志绪理 + 栞 / 美玖 / 凛 / 小雪 / 小蛛 / 白桦，每人一个角色、一组工具、
一段**从数据库现算的真实数据**。左边是管家列表（那行灰字就是她现在的状态），右边对话。

```
浏览器 ──POST /api/chat {text, sid, who}──→ Flask ──SSE──→ 模型
                                             │              │
                                             └── 工具调用 ←─┘
                                    shelf_overview / comic_status / anime_status /
                                    source_health / history_digest / reading_status /
                                    search_shelf / search_online / add_to_shelf
```

| 谁 | 管什么 | 用的模型 |
| --- | --- | --- |
| 宫城志绪理（大管家） | 什么都能问 | deepseek（复用 `C:\qqbot` 那份配置 + 她的人设） |
| 栞（图书委员） | 书架 / 目录 / 进度 | 智谱 `glm-4-flash`（免费） |
| 美玖（追番担当） | 番剧收藏 / 看到第几集 | 同上 |
| 凛（漫画担当） | 漫画书架 / 读到第几话 | 同上 |
| 小雪（伴读担当） | 上次停在哪 / 陪你读 | 同上 |
| 小蛛（书源担当） | 抓取 / 源体检 | 同上 |
| 白桦（馆长） | 历史 / 档案 / 统计 | 同上 |

台词是真数据，两层保证：

1. **进模型之前**：每次请求都从 SQLite 现算几行事实塞进 system prompt，
   规则里写死"事实里没有的必须调工具，查不到就说没有，不许编"。
2. **不经过模型**：开场白和列表里那行状态是模板 + 真实数字，模型挂了也是真话。
   比如小蛛开口就是「漫画源 10/22，挂了 12 个，要我重测吗？」

其它：

- **配置不用抄第二遍**：宫城读 `config.json` 的 `chat.providers.deepseek`，
  没写就连 `C:\qqbot\config.json`（`llm.baseUrl` / `apiKey` / `model` / `persona`）。
- **`${ENV_VAR}` 会展开**：从进程环境和 `HKCU\Environment` 两处找。
  管家那六位用的是环境变量里的 `ZHIPU_API_KEY`。
- **界面按 QQ / 微信的心智做**：两栏、气泡分组、时间分隔、Enter 发送 /
  Shift+Enter 换行、每人一份记录（`localStorage` 键 `nh-chat-v2:<who>`，各 200 条）。
- **失败能重试**：连不上时对话流里出现红字 + 「重试」，这条也存下来，刷新后照样能点。

细节见 `docs/chat-agent.md`。

## 加载不出来时点刷新

两处入口：

1. **顶栏常驻 `⟳`**（搜索框右边，主题键旁边）——任何页面都能点，直接重载当前页。
   这是主入口：图片走 `/api/img` 代理，失败是站点/网络侧的偶发问题，常驻按钮最稳。
2. **失败浮条**（`static/refresh.js`）——检测到失败时从底部浮出一条
   「N 处内容没加载出来 · [刷新] · [×]」，不自动消失，自己决定刷新还是关掉。

浮条检测三种情况：

- `.load-fail` 失败块（正文流失败、漫画图片列表失败）——并给它补一个「刷新」按钮
- 图片失败：`naturalWidth === 0` 且 `complete` 就是失败
- 懒加载图不派发 `error` 的情况（Edge 会把懒加载图换成占位元素）→ 对"看着是空的"
  图 `fetch` 一次探真实状态，只探前 20 张

顺带把 `/api/img` 的失败响应从 `200 + SVG` 改成 **`502 + SVG`**：`<img>` 只有收到错误
状态才会触发 `onerror`，页面上所有重试 UI 和浮条都靠这个事件。直接用浏览器打开那个
URL 仍能看到 SVG 提示。

阅读页本身另有一套更细的：`read.js` 的 SSE 失败会自动重连 2 次，还不行才出
「重新加载 / 同步模式再试 / 刷新本页」；`comic.js` 的单张图失败会标红，点一下重试。

## 源管理 / 漫画进度

- **源管理**：顶栏「源」→ 41 个 JS 源（官方 31 + 本地扩展），可安装、启用/停用；
  停用的源不参与聚合搜索（实测停用 `zaimanhua` 后结果里就没有它的条目了）。
- **漫画进度**：打开任意一话即记录 `comic_progress`；书架显示「读到 第1话」，
  卡片上的「继续阅读」直接跳到那一话，详情页在对应章节标「读到这」。
  当前话标题由阅读页 JS 拉章节表后回写（`/api/comic/progress`），所以书架显示的是标题不是 id。

## 小说 / 漫画分区 + 聚合搜索

顶栏分成 `小说` 和 `漫画` 两个区，数据也分表：小说在 `books/chapters`，漫画在 `comics`，
互不混排。漫画在详情页点开时自动收进漫画书架。

搜索是一个入口、三块结果：

| 块 | 来源 | 说明 |
|---|---|---|
| 本地书库 | `chapters` 全文 | 服务端直出，立刻可见 |
| 在线小说 | ①轻之文库 ②哔哩轻小说书目索引 ③Bangumi 书目 ④本地书架 | 小说源**自己勾**，见下 |
| 在线漫画 | Venera 各源并发 `search.load()` | 实测默认 10 个快源，点击进漫画详情 |

页面不会等在线源：本地结果先渲染，在线结果由 `/api/aggregate` 异步填充，慢源到
`timeout`（默认 15s）就丢，并在底部列出失败源。实测：

- 小说聚合（索引 3197 本时）：`转生` → 33 条 / 3.6s，0 错误
- 小说聚合：`天使` → 5 条 / 1.5s（轻小说文库 3 条 + 本地正文命中 2 条）
- 漫画聚合：`天使` → 72 条 / 15.0s（封顶，失败 2 个源）

### 小说源有四个，可以自己选

| key | 名字 | 怎么来的 |
|---|---|---|
| `linovel` | 轻之文库（站内搜索） | 实时 `GET /search/?kw=…`（导航栏表单的 action） |
| `linovelib` | 哔哩轻小说（书目索引） | 扫榜单页建的本地索引，见下 |
| `bangumi` | Bangumi 书目 | `api.bgm.tv` 元数据：中文名/日文名/封面/简介，**没有正文** |
| `local` | 本地书架 | 书名/作者命中 + **正文命中**（直接跳那一章） |

> **踩过的坑**：旧版用 `search.html?q=关键词&type=book`，这个参数早就失效了 ——
> 站方忽略 `q`，永远返回同一批随机推荐书（30 本，且每次都不一样）。
> 于是「相关性过滤」把它们全丢掉，表现就是「轻之文库永远搜不到东西」。
> 真正的入口是页面里 `<form action="/search/">` 的 `kw` 参数，结果在
> `.rank-book-list a.search-book` 里。改过来后 `天使` 能拿到 15 本全相关的书。

`/api/aggregate?q=转生&kind=novel&novels=linovel,linovelib,bangumi,local` —— 勾谁查谁，
空列表直接回「没有选择小说源」。

`bangumi` 是**发现层**：它的价值是书名权威（中文名 + 日文原名），搜到后会自动去哔哩轻小说
索引里找同名可读版本，卡片上直接给「哔哩轻小说·去读」；索引里没有就显示「仅书目·查索引」。
实测「刀剑神域」四源并发 **4.8s / 12 条 / 0 错误**。

结果里 `action` 决定点进去干什么：`open` 已在书架 → `/book/<id>`；
`add` 不在书架 → `/add?url=<原文页>`（先秒回「正在读取目录…」，再入库跳转，
实测 0.02s + 3.7s）；`meta` 是 Bangumi 条目 → 走上面那套交叉逻辑。

### 轻小说文库：站内搜索被 Cloudflare WAF 挡住，改成书目索引

站内搜索是 `POST /S6/`。实测对照（同一代理、同一 UA、同一台 CF 服务器）：

| 请求 | 状态 | 长度 |
|---|---|---|
| 首页 `/` | 200 | 76,582 B ✅ |
| 书页 `/novel/1546.html` | 200 | 35,187 B ✅ |
| 搜索 `/S6/?searchkey=天使` | 200 | **0 B** ❌ |

用无头 Chrome 直接开 `/S6/` 拿到的是 CF 拒信页：
`Sorry, you have been blocked` + `Ray ID a4f81d68ae879ac2` + `Your IP: 42.84.238.79`，
并提示 "submitting a certain word or phrase" —— **是 WAF 按路径 + 关键词拦搜索端点**，
不是 JS 门禁、不是代理问题。静态页有 CF 缓存所以照常能抓。

所以改成扫榜单页建本地索引：

```powershell
python nh.py index --pages 300            # 榜单模式：本次最多扫 300 页，再跑一次继续
python nh.py index --deep --pages 1500    # 深度模式：按 sitemap 逐本抓元信息，补齐榜单漏的书
python nh.py index --refresh --pages 3    # 增量刷新：只捡新书/最近更新（4~6 个请求，约 12s）
python nh.py find "转生"                   # 离线验证：索引 + 本地书架
```

扫的是 `modules/article/toplist.php?order=<8种排序>&sortid=<1..20 文库>&page=<n>`，
每页 30 本，取书名/作者/文库/简介/封面/链接，写进 SQLite `novel_index`。
实测 300 页 → **3197 本**；再扫 180 页唯一书目停在 3198 不动（排行榜高度重叠），
于是加了深度模式：`sitemap.xml` 给了全站 **5488 本**的书籍 id，逐本抓 `og:novel:*`
元信息补书名，每本 ~1s，可断点续抓。

**深度模式已跑完：索引 5411 本 / 5488（98.6%）**（478 页榜单 + 2287 次书籍页请求）。

### 新书怎么跟上（爬取频率）

先量了站点的真实更新速度（`order=postdate` 新书 / `order=lastupdate` 最近更新，
各看前 3 页 90 本）：

| 榜单 | 当月 | 上月 | 结论 |
|---|---|---|---|
| `postdate`（新书） | 34 本 | 36 本 | 约 **1~2 本/天** |
| `lastupdate`（最近更新） | 53 本 | 37 本 | 约 **3~5 本/天有更新** |

所以**不需要一小时一次**：一小时一次里 22/24 次是白跑，还会加速触发 CF 的 WAF。
现在的做法：

- 增量刷新只扫 `postdate` + `lastupdate` 的头部 3 页 → **4~6 个请求 / 约 12 秒**
- 幂等：已在索引里的书走 UPDATE，不会重复插；实测跑两次索引数不变（5411 → 5411）
- **自动定时**：服务启动 20 秒后跑一次，之后每 `index_refresh_hours` 小时一次（默认 **6 小时**，`0` 关闭）
- 网页搜索页有「增量刷新（捡新书）」按钮，随手点一下也行

配置项（`config.json`）：

```json
{ "index_refresh_hours": 6.0, "index_refresh_pages": 3 }
```

想更勤快就改 3 小时，想省流量改 12；只要不低到 1 小时，站点不会把你当爬虫。
网页搜索页有个「建索引 / 继续扫」按钮，进度实时显示
`正在扫：12/60 页 · 已索引 560 本 · allvisit/sid4/p3`。

细节、失败站点清单、为什么搜索引擎兜底也不行，都写在 `docs/novel-sources.md`。

`config.json` 的 `comic_sources_default` 控制默认参与聚合的漫画源；留空表示全查。

## 动漫（番剧）

接口和规则是从 `Desktop\web\anime_server\rules.json` + `anime_app\assets\plugins\*.json` 里挖出来的，
统一走**苹果 CMS V10 采集接口**（`api.php/provide/vod/`），一次调用就返回剧名/封面/年份/播放线路。

| 源 | 接口 | 实测（2026-10-24 复核） |
|---|---|---|
| `girigiri` | `m3u8.girigirilove.com` | ✅ 9 条结果，m3u8 直链 |
| `apibdzy` | `api.apibdzy.com` | ✅ 3 条，m3u8 直链 |
| `ffzy` | `cj.ffzyapi.com` | ✅ 20 条，2 线路 6 集，m3u8 直链 |
| `lzi` | `cj.lziapi.com` | ✅ 19 条，2 线路，m3u8 直链 |
| `wujin` | `api.wujinapi.com` | ⚠️ 时快时慢（最快 3s，慢时 80s 超时） |
| `guangsu` | `api.guangsuapi.com` | ❌ 本机连接重置 |

源列表现在**从 `refs/anime/rules.json` 读**（`searchMode: "api"` 的规则自动并进来），
加源只改 JSON 不改代码；`refs/anime/rules.json` 是从你 `Desktop\web\anime_server\rules.json` 拷来的，
格式和 Kazumi 一致。

`sorani` / `xfdmnext` 这两个插件只有 JSON API + 网页播放页（要 WebView 解析），没接；
`7sefun` / `DM84` / `aafun` 是 xpath + WebView 规则，同样没接。

默认勾 `girigiri + ffzy + apibdzy`（`config.json` 的 `anime_sources_default`）——
`wujin` 实测常要 16s 以上，默认不勾。搜索页里番剧是**按源各发一个请求**，
慢源不再拖累其它源（girigiri 5.0s / ffzy 4.7s / wujin 16.5s 各自返回）。

**播放**：源给的都是 `.m3u8`，Chrome 不能原生播，所以：

1. 播放页用 `hls.js`（`static/vendor/hls.min.js`，1.5.17，本地文件不依赖外网 CDN）
2. `hls.js` 拉的不是源站地址，而是本机的 `/api/hls?u=<m3u8>` —— 后端带 Referer 取回播放列表，
   把每一行切片地址改写成 `/api/hls/seg?u=<ts>`，再逐片代理回去（源站有 Referer 校验 + CORS 限制）
3. 主播放列表里的 `#EXT-X-KEY`/`#EXT-X-MAP` 的 `URI="…"` 也会一起改写

实测（girigiri 进击的巨人）：

```
/api/hls?u=…       → 200，2966 行，改写 1480 个切片
/api/hls/seg?u=…   → 200，video/mp2t，magic b'G@\x11\x10'
浏览器里 video[src^="blob:"] 已挂载 → hls.js 接上了 MSE
```

**页面上能做的事**：`/animes` 动漫书架（收藏 + 继续观看）、`/anime/<源>/<id>` 详情（线路切换、
整季选集网格、换源找同名）、`/play/<源>/<id>/<集>` 播放（上一集/下一集、播完自动下一集、
进度每 10 秒回写、下次进来接着播）。搜索页多了「动漫」tab，三个源并发查。



网页顶栏「发现」→ 贴排行榜地址 → 解析出书单 → 「全部建目录」。

```powershell
python nh.py discover "https://www.linovelib.com/top.html" --top 12   # 只看书单
python nh.py batch    "https://www.linovelib.com/top.html" --top 20   # 批量建目录
python nh.py batch    "https://www.linovelib.com/topfull/allvisit/1.html" --top 30
```

- 默认**只建目录**（书名/作者/封面/分卷/章节表），正文不抓——实测 3 本 849 章约 1 分钟；
- 建好的书在书架上标「在线读」，点开就读，读完不落盘；
- 想整本下载：`python nh.py resume <book_id>`；
- 可用的列表页：`/top.html`、`/topfull/allvisit/1.html` 等排行榜；分类页 `wenku/...` 会撞 Cloudflare 质询，暂时解析不了。

## 三种存储模式（要不要落到本地）

| 模式 | 怎么用 | 库里存什么 | 适合 |
| --- | --- | --- | --- |
| **全下载**（默认） | `python nh.py add <URL>` | 目录 + 正文（text/html 两列） | 要离线看、要导出 EPUB/TXT、要全文搜索 |
| **只建目录 / 在线读** | `python nh.py add <URL> --catalog-only`<br>或 `config.json` 里 `"persist_chapters": false` | 只有书名/作者/封面/分卷章节表，**正文一列空着** | 只想当个在线书柜，不占空间 |
| **完全不碰** | 直接开原站页面 | 什么都不存 | 只想读一章 |

在线读模式下的行为：

- 点开章节：**先出骨架屏**，随后按页流式（SSE）推送正文——第一页抓完就能读，后面的页边抓边追加；
- 取完只放内存（进程内 LRU，最多 60 章）+ `data/livecache/<book_id>/<idx>.json` 临时缓存（默认 24 小时，过期自动删）；
- **自动预取**后面 2 章（`prefetch_count`），翻页基本不用等；
- **不写书库正文**，所以不算"下载"；断网/过期后重看会重新取；
- 阅读页顶上标「在线读取 · 未落库」，已下载的书也能 `?live=1` 临时在线读；
- `?noprefetch=1` 关预取，`?sync=1` 用同步模式（老式、卡住可用它兜底）。

书架角标直接标状态：`在线读` / `部分` / `已下载`。

## 插图

两种玩法，由 `config.json` 的 `localize_images` 决定：

| `localize_images` | 阅读器 | 导出 EPUB | 磁盘 |
| --- | --- | --- | --- |
| `false`（默认） | `<img>` 指向源站 CDN，联网即显示 | 导出时下载并内嵌 | 不占 |
| `true` | 抓取/在线读时把图下到 `data/images/<book_id>/`，`src` 改成本地 `/media/...` | 直接用本地文件打包 | 约 100–300 KB/张 |

单文件 HTML 导出会把已落地的插图内联成 base64，拷走也能看。

网页「抓取」页里对应的勾选是 **只建目录（正文在线阅读，不落库）**。

## 自检

```powershell
python selftest.py
```

不联网，跑一遍「切章 → 入库 → 搜索 → 四种格式导出 → 阅读器路由 → 书目索引 →
拷贝漫画解密 → 简繁转换 → 阅读历史 → 删除」，**33 项全绿**。
临时库跑完自动清掉，用来确认改动没把管线弄坏。

## 常见问题

- **403 / 超时**：代理没起来或被 Cloudflare 拦了。先 `python nh.py proxy`，再检查 `config.json` 的 `proxy`。代理换端口不用改配置，`auto` 会重扫 7890–7899 / 10808–10809 / 1080 等常见端口。
- **章节显示「未抓取」**：原文该章需要登录/特权（目录里是 `javascript:cid(1)`），抓不到是正常的，其它章不受影响。
- **个别章节段落读起来跳来跳去**：源站该章本身的段落编排就这样（可拿同章在移动模板或别的站对比），不是解析错位；换源或用 `import` 导入一份校对版即可。
- **抓一整本要多久**：这本书 294 章、约 700 页次请求，每页 5–10 秒，1.5–2 小时。挂着跑就行，中途关掉下次 `resume`。
- **TXT 没切对章**：标题行规则认「第X章/第X话/序章/后记/番外/Chapter N」。特殊命名可以在 `importer.py` 的 `CHAPTER_HEAD_RE` 里补一条。

## 使用范围

书库、缓存、导出文件都在本机，供个人阅读与备份使用；抓取间隔已按站点承受能力做了限速。
