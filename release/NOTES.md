## 书萤 v0.4.1

本地轻小说 / 漫画 / 动漫书房。这一版补齐了三种交付形态：Windows 独立应用、Android APK、以及免装的 PWA。

### 下载

| 文件 | 适用 | 说明 |
| --- | --- | --- |
| `书萤-windows-x64.zip` | Windows 10/11 x64 | 解压后双击 `书萤.exe`，无需安装 Python |
| `书萤-android.apk` | Android 7.0+（arm64 / x86_64） | 内置 Python 运行时与完整后端，离线可用 |

### Windows 版

解压后整个文件夹就是应用，**双击 `书萤.exe` 即可**：

- 自动选端口（8899 被占会往后找），用 Edge/Chrome 的 `--app` 模式开一个无地址栏窗口
- 关掉窗口 = 退出程序，不留后台进程
- 数据落在 exe 旁边的 `data/`；该目录不可写时退到 `%LOCALAPPDATA%\FireflyLibrary`
- 启动失败会弹窗，并把堆栈写到数据目录的 `startup-error.log`

首次运行 Windows SmartScreen 可能拦一下（未做代码签名），点「更多信息 → 仍要运行」。

### Android 版

`adb install -r 书萤-android.apk`，或直接在手机上打开安装包。

- 内置 CPython 3.11 + Flask，书库跑在手机本地
- 数据在 App 私有目录，卸载即清除
- 默认把插图下载到本地，断网也能看
- 看日志：`adb logcat -s BookFly python.stderr`

### 这一版做了什么

**独立应用 exe**
- 路径解析分清「只读资源根 / 可写根 / 数据根」，冻结后模板从 `_MEIPASS` 读、书库落 exe 旁边
- 单实例锁 + 端口自动避让 + `/healthz` 就绪探测
- 修了三个真 bug：werkzeug 元数据缺失导致服务起不来、关窗不退出（Chromium 把 `--app` 窗口交给已有进程）、窗口误判（资源管理器标题也含「书萤」）

**Android APK**
- Chaquopy 把 CPython 3.11 打进 APK，WebView 指向本地 Flask，模板/样式/业务逻辑与桌面端共用同一份
- `bookfly/jshost.py`：Android 上没有 node 二进制，改用系统 WebView 的 V8 跑 Venera 源的 `init.js` 契约，HTTP/加密/键值存储通过 `@JavascriptInterface` 回调到 Python
- 只打 arm64-v8a / x86_64，体积控制在 48 MB

**手机版（PWA）**
- manifest + service worker + 离线页，可「装到主屏」独立运行
- 安全区适配（刘海/小黑条）、触控目标 ≥40px、输入框 16px 防 iOS 自动放大
- 阅读页左右滑动翻章

**聊天模型**
- 不带任何内置密钥。在「设置 → 聊天模型」填 base_url / api_key / model，任何 OpenAI 兼容接口都能用（DeepSeek / 智谱 / Kimi / OpenAI / Ollama）
- 「测试连接」打 `/models` 验证，不消耗对话额度
- 环境变量兜底默认关闭 —— 避免默默用上机器里别的程序残留的 Key

### 验收

- `selftest.py` 后端全链路自测通过
- 桌面布局 30 组（1440/1920/1280 × 10 页 × 空库/满库）无溢出、无遮挡
- 移动端 33 组（3 档视口 × 11 页）触控与 PWA 装配全部合规
- 插图 15 张在桌面与手机均解出像素、可见、不溢出

### 已知限制

- 未做代码签名，首次运行有 SmartScreen 提示
- APK 是 debug 签名（自用无碍，不上应用商店）
- APK 未包含 `refs/venera` 的第三方 JS 源（版权内容不入库），内置的 Python 源正常可用
