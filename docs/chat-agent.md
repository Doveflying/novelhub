# 聊天：七位管家

2026-10 加的。原本只是把 QQ agent 接进书房，后来扩成**七位管家**：
宫城志绪理之外，书架上那六个看板娘（栞 / 美玖 / 凛 / 小雪 / 小蛛 / 白桦）各自上岗，
每人一个角色、一组工具、一段**现场算出来的真实阅读数据**。

```
浏览器 /chat?who=shiori
   │  POST /api/chat  {text, sid, who}
   ▼
Flask（novelhub/webapp/app.py）
   │  novelhub/personas.py   ← 人设 + 真实数据 + 工具分配
   │  novelhub/chat.py       ← provider 路由 + SSE + 工具执行
   ▼
OpenAI 兼容 /chat/completions
   ├─ 宫城       provider=deepseek  （复用 C:\qqbot 那份配置，带她 969 字人设）
   └─ 六位管家   provider=zhipu     （glm-4-flash，免费）
```

## 1. 谁管什么

| id | 名字 | 角色 | 管什么 | 工具 | provider |
| --- | --- | --- | --- | --- | --- |
| `miyagi` | 宫城志绪理 | 大管家 | 什么都能问 | 全部 9 个 | deepseek |
| `shiori` | 栞 | 图书委员 | 书架 / 目录 / 阅读进度 | shelf_overview, search_shelf, reading_status, search_online, add_to_shelf | zhipu |
| `miku` | 美玖 | 追番担当 | 番剧收藏 / 播放进度 | anime_status, search_online | zhipu |
| `rin` | 凛 | 漫画担当 | 漫画书架 / 话数 | comic_status, search_online | zhipu |
| `yuki` | 小雪 | 伴读担当 | 阅读位置 / 陪伴 | reading_status, search_shelf, history_digest | zhipu |
| `aju` | 小蛛 | 书源担当 | 抓取 / 书源体检 | source_health, search_online, add_to_shelf, shelf_overview | zhipu |
| `shirakaba` | 白桦 | 馆长 | 历史 / 档案 / 统计 | history_digest, shelf_overview, reading_status | zhipu |

分工不是新发明的，是照 `static/cast.js` 里早就写在页面上的角色定位来的。
工具按角色分配还有个副作用：每人的工具表都短，glm-4-flash 挑工具不容易挑错。

## 2. 台词怎么接真实数据

两层，缺一不可：

**第一层：`facts()` —— 现算的事实，写进 system prompt。**
每次请求都从 SQLite 现场查，几行短句，例如栞拿到的是：

```
书架：12 本小说、2726 章、24.4 万字。
最近动过的：《安达与岛村》目录 208 章、正文还没抓、《魔王学院…》0/862 章 …
只有目录、正文还没抓的共 5 本，比如《安达与岛村》、《天使的3P！》…
```

小蛛拿到的是源体检（`小说源 3/3 通`）、小雪拿到的是上次停在哪个位置、
白桦拿到的是档案条数和"最近 6 条里有几条是今天的"。
规则里写死了：**事实里没有的必须调工具，工具查不到就说没有，不许编**。

**第二层：`greeting()` / `roster()` 的 `status` —— 不经过模型的开场白。**
纯模板 + 真实数字，页面一渲染就有，模型挂了也照样是真话：

- 栞：`欢迎回来～书架上有 12 本、2726 章，24.4 万字。最上面那本是《安达与岛村》。`
- 美玖：`番剧书架还空着哦——不过播放记录里还剩 6 条旧的。`
- 凛：`8 部在架上。《邻家天使…》读到 第40话 了，接着翻吗。`
- 小雪：`上次停在《安达与岛村》的「插图」，今天 14:36。`
- 小蛛：`漫画源 10/22，挂了 12 个，要我重测吗？`
- 白桦：`档案里 10 条记录。最近一次是 今天 14:36 的《安达与岛村》。`

左侧管家列表里每人下面那行灰字，就是这句被截到 46 字——所以列表本身就是一张现状仪表盘。

## 3. 记忆怎么分层（重点）

这套东西的"记性"分两层，**重心明确压在阅读数据上**，聊天只是工作记忆：

| 层 | 存哪 | 活多久 | 内容 |
| --- | --- | --- | --- |
| 阅读记忆（真记忆） | `data/library.db`（SQLite） | 永久 | 书/章/字数、目录抓没抓、读到第几章、漫画第几话、番剧第几集、阅读历史、源体检结果 |
| 聊天记忆（工作记忆） | 进程内存 + 浏览器 `localStorage` | 重启即忘 / 换浏览器即没 | 最近 `history_max`（24）条对话 |

所以**重启服务对她们"知道你在看什么"零影响**——那是每次请求现查 SQLite 的。
重启只丢"刚才聊到哪句"，而这恰好是用户明确说的"影响不太大"。

工程上的取舍：

- `personas.facts()` 每次请求都跑（几次 SQL，几毫秒），不缓存——库里的状态永远是最新的。
- 聊天上下文只留 24 条，还被裁到 4 轮工具调用，避免把便宜的 glm-4-flash 的上下文塞满。
- 想让她"记得更久"的正确做法不是加大聊天历史，而是把**更多阅读事实**写进 `facts()`
  （比如"上周读了 3 本、共 18 章"这种聚合），因为那层是数据库，天然持久。

## 4. Provider 配置

两家都是 OpenAI 兼容，只差 base_url / key / model。
填充顺序（后面的只补空位）：`config.json` 的 `chat.providers.<provider>` →
老配置的 `chat` 顶层字段（仅 deepseek）→ QQ bot 的 `llm` 段（仅 deepseek）→ 内置默认。

内置默认：

| provider | base_url | model | key 环境变量 |
| --- | --- | --- | --- |
| deepseek | `https://api.deepseek.com/v1` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| zhipu | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | `ZHIPU_API_KEY` |

`${ENV_VAR}` 会展开：先看进程环境，再看 `HKCU\Environment`（`winreg`）。
qqbot 的 key 存的就是 `${DEEPSEEK_API_KEY}`，不在注册表里读到就 401
（`Your api key: ****KEY} is invalid`）。

智谱那边 2026-10 实测：`glm-4-flash` 免费、支持 function calling，
一次带工具的往返 3.5~5 秒，回答短，适合管家这种一句话的场景。

`GET /api/chat/info?who=aju` 可以确认：

```json
{"ok":true,"ready":true,"who":"aju","name":"小蛛","model":"glm-4-flash",
 "provider":"zhipu","source":"default","status":"漫画源 10/22，挂了 12 个，要我重测吗？",
 "roster":[{"id":"miyagi","ready":true,...}]}
```

## 5. 工具

九个，都在 `chat.py`。除了 `search_online` / `add_to_shelf` 会发网络请求，其余纯查库。

| 工具 | 干什么 | 谁有 |
| --- | --- | --- |
| `shelf_overview` | 书架总览：几本、几章、多少字、最近在读 | 宫城/栞/小蛛/白桦 |
| `search_shelf` | 按书名/作者/正文片段找 | 宫城/栞/小雪 |
| `reading_status` | 某本读到第几章，或列全部在读 | 宫城/栞/小雪/白桦 |
| `comic_status` | 漫画书架 + 读到第几话 | 宫城/凛 |
| `anime_status` | 番剧收藏 + 看到第几集 | 宫城/美玖 |
| `source_health` | 源体检结果 + JS 源启用数 | 宫城/小蛛 |
| `history_digest` | 阅读历史：最近读了什么、各类多少条 | 宫城/小雪/白桦 |
| `search_online` | 走聚合搜索去网上找（慢，几秒到十几秒） | 宫城/栞/美玖/凛/小蛛 |
| `add_to_shelf` | 把某本书的 URL 抓进书架（只收目录） | 宫城/栞/小蛛 |

最多 4 轮工具调用；第 3 轮之后追加一句「（工具够用了，直接回答我，别再调工具）」强制收尾。
工具结果裁到 4000 字符再回灌。`chat.tools: false` 可以整体关掉。

## 6. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/chat?who=<id>` | 页面（不带 who 就是宫城） |
| GET | `/api/chat/info?who=<id>` | 接通状态、模型、provider、该角色的实时状态、整份 roster |
| POST | `/api/chat` | `{text, sid, who}` → `text/event-stream` |
| POST | `/api/chat/reset` | `{sid, who}` → 清掉这个角色的模型上下文 |

SSE 事件：

```
event: meta     {"model":"glm-4-flash","name":"栞","who":"shiori",
                 "color":"#7C6BFF","provider":"zhipu","source":"default","tools":true}
event: tool     {"name":"shelf_overview","args":{},"who":"shiori"}
event: delta    {"text":"书架上共有12本书哦。"}
event: error    {"message":"..."}
event: done     {"text":"整段回答","name":"栞","who":"shiori"}
```

会话按 `who|sid` 分开存进程内存，只保留最近 `history_max`（默认 24）条。
**重启服务 = 模型忘掉上下文**，但可见的对话记录在浏览器里，不会丢。

## 7. 前端

`templates/chat.html` + `static/chat.css` + `static/chat.js`，
样式全走 ui.css / otaku.css 的 token，四套主题（樱/昏/薄荷/夜）自动跟着换。

- **两栏**：左边管家列表（头像 + 名字 + 角色 + 实时状态那行灰字），右边对话。
  ≤760px 时列表横过来变成一条可滑动的头像条，顶栏让位给底栏。
- **每人一份记录**：`localStorage` 键 `nh-chat-v2:<who>`，各自 200 条上限；
  切角色就是换 URL（`?who=`），记录和模型上下文都跟着切换。
- **我说的话反白**（`--ink` 底 + `--bg` 字），她说的话是 `--surface` 底。
  不走"签名色铺满气泡"：白字压在 `#ff4f87` 上只有 3.1:1，反白在两种主题下都 >13:1。
  签名色只留给发送键、光标、焦点环。
- **气泡宽 40em**，整页不滚只滚对话流，宽屏消息列锁 760px。
- **两处动效**：等字的三点呼吸、流式光标。消息本身没有进场动画。
- **清空要两步**：第一次点变「确认清空」，6 秒内再点才清。
- **失败行会存下来**，刷新后「重试」还能点；重试不会重复贴一条自己的话。
- 中文输入法：`Enter` 判断带 `e.isComposing` 和 `keyCode !== 229`，选词回车不会误发。

> 踩过的坑：管家列表项的 class 一开始叫 `imc main`，结果 ui.css 里全局 `.main`
> （`max-width:1240px; padding:26px 34px 72px`）跟着套上，那一项被撑成一坨。
> 现在叫 `imc is-main`。

> 头像：宫城用的是真的那张插画（`static/cast/miyagi-face.jpg`，从 612×1676 的原图
> 裁成 500×500 的方脸框，`object-position: center 13%` 兜住其他比例的原图）。
> 其余六位沿用 `static/cast/<id>-face.jpg`。

> 另一个坑：**改模板要重启 `nh.py serve`**。虽然 `app.jinja_env.auto_reload = True`，
> 但实测改了 `chat.html` 后旧进程仍在吐旧 HTML（`is-main` 一直不生效直到重启）。
> 改 CSS/JS 不用重启（`asset_v` + ETag 会重新拉）。

## 8. 已知的边界

- 会话上下文在内存里，多进程/重启不共享；并发用同一个 `sid` 会互相覆盖。
- 没有限流，长对话只按条数截断，不按 token 计。
- `search_online` / `add_to_shelf` 会真发网络请求（走 `config.json` 的代理），
  慢的时候前端只显示「正在上网找」，没有进度条。
- 事实块每次请求都现算（几次 SQL，开销很小），但**不给模型看原文**——
  它是"档案摘要"，不是 RAG。想让它读正文得另做。
- 记录存 `localStorage`，换浏览器/清缓存就没了。
- 六个管家的性格靠 `personas.py` 里那几行 `voice`，改了就变；改完记得重启。
