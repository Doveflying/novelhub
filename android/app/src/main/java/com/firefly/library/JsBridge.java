package com.firefly.library;

import android.annotation.SuppressLint;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 书萤 · Venera JS 源引擎（用 WebView 的 V8 当解释器）。
 *
 * 为什么需要这个类：APK 里没有 node，也没有 quickjs/duktape，而
 * {@code novelhub/sources/venera_js.py} 靠 subprocess 调 node 跑源脚本。
 * 手机上没有 node 时，39 个源文件全废。Android 自带 WebView（系统级 V8），
 * 拿它当 JS 引擎是零额外体积、且 DOM 完整（源里 184 处用 document 全局）。
 *
 * 线程模型（关键）：
 *   - Flask 在工作线程里调 {@link #eval}，它会阻塞等结果；
 *   - WebView 必须在有 Looper 的线程创建并调用 loadUrl，这里用主线程；
 *   - 所以 eval() 把 "loadUrl" post 到主线程，然后在工作线程上 await latch。
 *     主线程负责派活和收活，不会因为等 latch 而卡死（它压根不等）。
 *
 * 同步桥：{@link HostBridge#host} 标了 @JavascriptInterface，JS 调用时
 * WebView 会阻塞 JS 线程直到 Java 返回。Venera 的 Convert.* 本来就是同步
 * 语义，正好对上，不用把整条链路改成异步。
 */
public final class JsBridge {

    private static final String TAG = "BookFlyJs";
    private static final long DEFAULT_TIMEOUT_MS = 180_000L;

    private static volatile JsBridge instance;

    private final Context appContext;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<String, Job> jobs = new ConcurrentHashMap<>();
    private final AtomicBoolean ready = new AtomicBoolean(false);

    private WebView web;
    private CountDownLatch readyLatch = new CountDownLatch(1);

    private JsBridge(Context ctx) {
        this.appContext = ctx.getApplicationContext();
    }

    public static JsBridge get(Context ctx) {
        JsBridge b = instance;
        if (b == null) {
            synchronized (JsBridge.class) {
                b = instance;
                if (b == null) {
                    b = new JsBridge(ctx);
                    instance = b;
                }
            }
        }
        return b;
    }

    /** 引擎是否已经可以接活。 */
    public boolean isReady() {
        return ready.get();
    }

    /**
     * 初始化：在主线程建一个不可见的 WebView 并加载 jshost/index.html。
     * 必须从主线程调用（或至少能 post 到主线程）。重复调用是安全的。
     */
    public void init() {
        if (ready.get() || web != null) return;
        main.post(this::createWebView);
    }

    /** 阻塞等待引擎就绪，返回是否成功。 */
    public boolean awaitReady(long timeoutMs) {
        init();
        try {
            return readyLatch.await(timeoutMs, TimeUnit.MILLISECONDS) && ready.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    private void createWebView() {
        if (web != null) return;
        try {
            web = new WebView(appContext);
            WebSettings s = web.getSettings();
            s.setJavaScriptEnabled(true);
            s.setDomStorageEnabled(true);
            s.setAllowFileAccess(true);
            s.setAllowContentAccess(true);
            s.setBlockNetworkLoads(false);
            // 源要跨域拉数据：这里只用 XMLHttpRequest/自定义 Network，
            // 关掉 file 起源的限制更省事
            s.setAllowUniversalAccessFromFileURLs(true);
            s.setAllowFileAccessFromFileURLs(true);
            s.setCacheMode(WebSettings.LOAD_NO_CACHE);
            s.setMediaPlaybackRequiresUserGesture(true);

            web.addJavascriptInterface(new HostBridge(), "bridge");

            web.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageFinished(WebView view, String url) {
                    // 页面加载完不代表 bridge.js 已经执行完；再确认一次标志位
                    view.evaluateJavascript(
                            "(function(){return !!(window.__veneraReady && window.__veneraRun);})()",
                            value -> {
                                boolean ok = "true".equals(value);
                                Log.i(TAG, "js host ready=" + ok);
                                if (ok) {
                                    ready.set(true);
                                } else {
                                    Log.e(TAG, "bridge.js 没有就绪，__veneraReady 检查失败");
                                }
                                readyLatch.countDown();
                            });
                }
            });

            web.loadUrl("file:///android_asset/jshost/index.html");
        } catch (Throwable t) {
            Log.e(TAG, "创建 WebView 失败", t);
            readyLatch.countDown();
        }
    }

    /**
     * 在 JS 引擎里跑一个源动作，阻塞直到拿到结果。
     *
     * @param jobId     调用方生成的唯一 id（Python 侧用它找上下文）
     * @param code      源的完整 JS 源码
     * @param action    meta / search / explore / info / ep
     * @param paramsJson 动作参数（JSON 字符串）
     * @return 引擎返回的 JSON 字符串，形如 {"ok":true,"data":...}
     */
    public String eval(String jobId, String code, String action, String paramsJson, long timeoutMs)
            throws Exception {
        if (!awaitReady(20_000)) {
            throw new IllegalStateException("JS 引擎未就绪（WebView 初始化失败或超时）");
        }

        Job job = new Job();
        jobs.put(jobId, job);

        String payload = "{\"job\":" + quote(jobId)
                + ",\"code\":" + quote(code)
                + ",\"action\":" + quote(action)
                + ",\"params\":" + (paramsJson == null || paramsJson.isEmpty() ? "{}" : paramsJson)
                + "}";

        main.post(() -> {
            if (web == null) {
                job.fail("WebView 已销毁");
                return;
            }
            try {
                web.evaluateJavascript(
                        "(function(){try{window.__veneraRun(" + payload + ");}catch(e){"
                                + "bridge.done(" + quote(jobId) + ", JSON.stringify({ok:false,error:String(e)}));"
                                + "}})()",
                        null);
            } catch (Throwable t) {
                job.fail("evaluateJavascript 失败：" + t);
            }
        });

        boolean finished = job.latch.await(timeoutMs <= 0 ? DEFAULT_TIMEOUT_MS : timeoutMs,
                TimeUnit.MILLISECONDS);
        jobs.remove(jobId);
        if (!finished) {
            throw new IllegalStateException("JS 源执行超时（" + action + "）");
        }
        if (job.error != null) {
            throw new IllegalStateException(job.error);
        }
        return job.result == null ? "{\"ok\":false,\"error\":\"引擎没有返回结果\"}" : job.result;
    }

    /** 释放 WebView。进程退出/Activity 销毁时调用。 */
    public void shutdown() {
        jobs.values().forEach(j -> j.fail("引擎已关闭"));
        jobs.clear();
        main.post(() -> {
            if (web != null) {
                try {
                    web.destroy();
                } catch (Throwable ignored) {
                }
                web = null;
            }
            ready.set(false);
            readyLatch = new CountDownLatch(1);
        });
    }

    /** 每个在跑的调用一份，等 latch 释放。 */
    private static final class Job {
        final CountDownLatch latch = new CountDownLatch(1);
        volatile String result;
        volatile String error;

        void complete(String json) {
            result = json;
            latch.countDown();
        }

        void fail(String msg) {
            error = msg;
            latch.countDown();
        }
    }

    /* ------------------------------------------------------------------ 桥 */

    /**
     * 暴露给 JS 的同步接口。
     *
     * <p>方法名必须叫 {@code host} / {@code done}，因为 bridge.js 里写的是
     * {@code bridge.host(...)} 和 {@code bridge.done(...)}。
     */
    private final class HostBridge {

        /** JS → Java → Python 的同步回调。返回值直接回给 JS。 */
        @JavascriptInterface
        public String host(String json) {
            try {
                com.chaquo.python.PyObject mod =
                        com.chaquo.python.Python.getInstance().getModule("bookfly.jshost");
                return mod.callAttr("handle", json).toString();
            } catch (Throwable t) {
                Log.e(TAG, "host 回调失败", t);
                return "{\"error\":" + quote(String.valueOf(t)) + "}";
            }
        }

        /** JS 执行完毕，交回结果并释放 Flask 线程。 */
        @JavascriptInterface
        public void done(String jobId, String json) {
            Job job = jobs.get(jobId);
            if (job != null) job.complete(json);
        }
    }

    /** 最小 JSON 字符串转义。 */
    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder(s.length() + 16);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    if (c < 0x20 || c == 0x2028 || c == 0x2029) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
        return sb.toString();
    }

    /** 给 Python 侧用的 job id 生成器（避免两边格式不一致）。 */
    public static String newJobId() {
        return "job-" + UUID.randomUUID();
    }
}
