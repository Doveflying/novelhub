package com.firefly.library;

import android.annotation.SuppressLint;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 书萤 · Android 主界面。
 *
 * 结构很简单：Python 起一个只监听 127.0.0.1 的 Flask，WebView 指过去。
 * 这样做的好处是手机端和桌面端共用同一套模板 / 样式 / 业务逻辑，
 * 不用维护第二份 UI。
 *
 * 几个必须踩对的点：
 *  - Python.start() 必须在任何 Python 调用之前，且一个进程只做一次。
 *  - 起服务是阻塞的（要等 Flask 起来），所以放子线程，起好再切回主线程加载。
 *  - 允许 http://127.0.0.1 的混合内容，否则 https 页面里加载 http 资源会被拦。
 *  - 返回键要能回退 WebView 历史，不然一按就退出。
 *  - **<input type="file"> 必须自己实现 onShowFileChooser**，否则点了毫无反应
 *    （WebView 默认不处理，这就是之前「选文件像摆设」的原因）。
 *  - 选文件走 SAF（ACTION_OPEN_DOCUMENT），**不需要任何存储权限** ——
 *    系统文件选择器自带读取授权，用户也不会被弹「允许访问照片和文件」。
 *  - setAllowContentAccess 必须为 true，否则拿到的 content:// URI 读不了。
 */
public class MainActivity extends AppCompatActivity {

    private static final String TAG = "BookFly";

    /** 选文件用的请求码，随便定，只要不和别的冲突 */
    private static final int REQ_FILE_CHOOSER = 1001;

    private WebView web;
    private ProgressBar progress;
    private TextView status;
    private FrameLayout splash;

    private String baseUrl;
    private volatile boolean serverReady = false;

    /** WebView 等这个回调来取用户选的文件；没用完就置空会让下次点不动 */
    private ValueCallback<Uri[]> filePathCallback;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 让内容延伸到状态栏/导航栏下面，配合 CSS 的 env(safe-area-inset-*)
        Window w = getWindow();
        w.setFlags(WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS);

        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        progress = findViewById(R.id.progress);
        status = findViewById(R.id.status);
        splash = findViewById(R.id.splash);

        configureWebView();

        status.setText("正在启动 Python 运行时…");

        new Thread(this::bootPython, "py-boot").start();
    }

    private void bootPython() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            Python py = Python.getInstance();

            // 先跑自检，把版本/数据目录打到 logcat，出问题好查
            PyObject health = py.getModule("bookfly.server").callAttr("health");
            Log.i(TAG, "python health: " + health.toString());

            // 漫画源的 JS 引擎：APK 里没有 node，用隐藏 WebView 的 V8 顶上。
            // 这里只是提前点火（幂等 + 异步），真正跑源时 Python 侧会再 await 一次。
            JsBridge.get(this).init();

            PyObject mod = py.getModule("bookfly.server");
            PyObject url = mod.callAttr("start_server");
            baseUrl = url.toString();
            Log.i(TAG, "server at " + baseUrl);

            serverReady = true;
            runOnUiThread(this::loadApp);
        } catch (Throwable t) {
            Log.e(TAG, "python boot failed", t);
            final String msg = t.getClass().getSimpleName() + ": " + t.getMessage();
            runOnUiThread(() -> {
                status.setText("启动失败\n\n" + msg
                        + "\n\n日志：adb logcat -s BookFly python.stderr");
                progress.setVisibility(View.GONE);
            });
        }
    }

    private void loadApp() {
        if (baseUrl == null) return;
        status.setText("加载书库…");
        web.loadUrl(baseUrl + "/");
    }

    private void configureWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        // 选文件走 SAF 会返回 content:// URI，必须允许 WebView 读 content 协议，
        // 否则文件选完了也拿不到内容（表现就是「选了没反应」）。
        s.setAllowContentAccess(true);
        s.setAllowFileAccess(true);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        // 本地 http 服务 + 可能的 https 外链图片，混合内容放行
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setTextZoom(100);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMediaPlaybackRequiresUserGesture(false);

        web.setBackgroundColor(0xFFFFFFFF);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                String u = req.getUrl().toString();
                // 站内（本地服务）自己处理，站外链接不劫持，避免用户被困在 WebView 里
                return !u.startsWith("http://127.0.0.1");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                splash.setVisibility(View.GONE);
                progress.setVisibility(View.GONE);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int p) {
                progress.setVisibility(p >= 100 ? View.GONE : View.VISIBLE);
                progress.setProgress(p);
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d(TAG, "js: " + m.message() + " @" + m.lineNumber());
                return true;
            }

            /**
             * 网页里点 <input type="file"> 会走到这里。
             *
             * 不实现这个方法，点击就完全没有反应 —— 之前导入页「选文件像摆设」
             * 就是这个原因，和存储权限无关：SAF 本身就把读取权限授给了我们。
             */
            @Override
            public boolean onShowFileChooser(WebView view,
                                             ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                // 上一次没消费掉的回调必须先还回去，否则 WebView 会一直挂着
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                }
                filePathCallback = callback;

                Intent intent = buildFileChooserIntent(params);
                try {
                    startActivityForResult(intent, REQ_FILE_CHOOSER);
                    return true;
                } catch (ActivityNotFoundException e) {
                    Log.w(TAG, "ACTION_OPEN_DOCUMENT 不可用，退回 ACTION_GET_CONTENT", e);
                    try {
                        startActivityForResult(
                                buildLegacyChooserIntent(params), REQ_FILE_CHOOSER);
                        return true;
                    } catch (ActivityNotFoundException e2) {
                        Log.e(TAG, "系统里没有可用的文件选择器", e2);
                        filePathCallback = null;
                        callback.onReceiveValue(null);
                        return false;
                    }
                }
            }
        });
    }

    /** 首选 SAF：不需要任何存储权限，用户选完就授了读权限。 */
    private Intent buildFileChooserIntent(WebChromeClient.FileChooserParams params) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        applyMimeFilter(intent, params);
        if (isMultiple(params)) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        }
        return intent;
    }

    /** 兜底：老式 GET_CONTENT，兼容少数没有 DocumentsUI 的 ROM。 */
    private Intent buildLegacyChooserIntent(WebChromeClient.FileChooserParams params) {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        applyMimeFilter(intent, params);
        if (isMultiple(params)) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        }
        return intent;
    }

    private boolean isMultiple(WebChromeClient.FileChooserParams params) {
        return params != null
                && params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE;
    }

    private void applyMimeFilter(Intent intent, WebChromeClient.FileChooserParams params) {
        String[] mimes = mimesFromAccept(params == null ? null : params.getAcceptTypes());
        if (mimes.length == 0) {
            intent.setType("*/*");
            return;
        }
        if (mimes.length == 1) {
            intent.setType(mimes[0]);
        } else {
            // 多个类型必须配 */* + EXTRA_MIME_TYPES，否则系统只按第一个过滤
            intent.setType("*/*");
            intent.putExtra(Intent.EXTRA_MIME_TYPES, mimes);
        }
    }

    /**
     * 把 HTML 的 accept 拆成真实 MIME。
     *
     * 关键：accept 里混着扩展名（.epub / .md），Android 的 intent 不认扩展名，
     * 直接塞进 EXTRA_MIME_TYPES 会被忽略甚至让选择器把文件灰掉。
     * 所以扩展名一律映射成 MIME，并额外补上 octet-stream —— 不少 ROM 认不出
     * epub 的 MIME，会退化成一堆 application/octet-stream，不补就选不了。
     */
    private String[] mimesFromAccept(String[] accept) {
        Set<String> out = new LinkedHashSet<>();
        if (accept != null) {
            for (String raw : accept) {
                if (raw == null) continue;
                for (String token : raw.split(",")) {
                    String t = token.trim().toLowerCase();
                    if (t.isEmpty()) continue;
                    if (t.startsWith(".")) {
                        String mapped = mimeOfExtension(t);
                        out.add(mapped);
                    } else if (t.contains("/")) {
                        out.add(t);
                    }
                }
            }
        }
        // 认不出 MIME 的机器会把这些文件报成 octet-stream，带上才能选
        boolean hasKnown = out.contains("text/plain") || out.contains("application/epub+zip");
        if (!out.isEmpty()) {
            out.add("application/octet-stream");
        }
        if (out.isEmpty() && !hasKnown) {
            return new String[0];
        }
        List<String> list = new ArrayList<>(out);
        return list.toArray(new String[0]);
    }

    private String mimeOfExtension(String ext) {
        switch (ext) {
            case ".txt":
                return "text/plain";
            case ".epub":
                return "application/epub+zip";
            case ".md":
            case ".markdown":
                return "text/markdown";
            case ".pdf":
                return "application/pdf";
            default:
                return "application/octet-stream";
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER) {
            if (filePathCallback == null) {
                super.onActivityResult(requestCode, resultCode, data);
                return;
            }
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null) {
                // parseResult 能同时处理单选和多选（ClipData）
                result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            }
            filePathCallback.onReceiveValue(result);
            filePathCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        // 页面正在选文件时被销毁，回调必须收尾，否则会泄漏
        if (filePathCallback != null) {
            filePathCallback.onReceiveValue(null);
            filePathCallback = null;
        }
        try {
            if (serverReady) {
                Python.getInstance().getModule("bookfly.server").callAttr("shutdown");
            }
        } catch (Throwable t) {
            Log.w(TAG, "shutdown failed", t);
        }
        try {
            JsBridge.get(this).shutdown();
        } catch (Throwable t) {
            Log.w(TAG, "js bridge shutdown failed", t);
        }
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
