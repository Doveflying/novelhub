package com.firefly.library;

import android.annotation.SuppressLint;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
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
 */
public class MainActivity extends AppCompatActivity {

    private static final String TAG = "BookFly";

    private WebView web;
    private ProgressBar progress;
    private TextView status;
    private FrameLayout splash;

    private String baseUrl;
    private volatile boolean serverReady = false;

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
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
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
        });
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
        try {
            if (serverReady) {
                Python.getInstance().getModule("bookfly.server").callAttr("shutdown");
            }
        } catch (Throwable t) {
            Log.w(TAG, "shutdown failed", t);
        }
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
