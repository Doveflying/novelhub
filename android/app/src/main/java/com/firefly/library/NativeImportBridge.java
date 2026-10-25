package com.firefly.library;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Log;
import android.webkit.JavascriptInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.util.List;

/**
 * 主界面 WebView 的 JS 接口：给「导入本地文件」提供一条不走网络的通道。
 *
 * 用法（页面里的 import.js）：
 *   if (window.NativeImport) {
 *       window.NativeImport.pickAndCopy();   // 打开系统选择器，选完自动复制
 *   }
 *   复制完成后原生侧调用 window.onNativeImport(pathsJson) 回调页面，
 *   页面再 POST /api/import/inbox 让 Python 直接读本地文件。
 *
 * 为什么这么绕：手机 WebView 用 <input type="file"> 上传大文件是
 * 把整个文件塞进 multipart，几十 MB 的 epub 常传到一半断；
 * 而且没有进度之外的任何补救手段。改成「原生复制 + 本地路径导入」后，
 * 完全不经过网络，多大都能进。
 */
public final class NativeImportBridge {

    private static final String TAG = "BookFly";

    /** 选择器请求码，和 MainActivity 里的要区分开。 */
    static final int REQ_NATIVE_IMPORT = 0x4E49;  // 'NI'

    private final Activity activity;

    NativeImportBridge(Activity activity) {
        this.activity = activity;
    }

    /** 页面判断「有没有原生导入通道」用。 */
    @JavascriptInterface
    public boolean available() {
        return true;
    }

    /** 打开系统文件选择器；选完由 onActivityResult 复制并回调页面。 */
    @JavascriptInterface
    public void pickAndCopy() {
        activity.runOnUiThread(() -> {
            try {
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                // 和页面 accept 对齐：扩展名在 intent 里无效，只认 MIME
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{
                        "text/plain",
                        "application/epub+zip",
                        "text/markdown",
                        "text/x-markdown",
                        "application/octet-stream",
                });
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                activity.startActivityForResult(intent, REQ_NATIVE_IMPORT);
            } catch (Exception e) {
                Log.w(TAG, "原生导入选择器打不开，让页面退回网页上传", e);
                // 传 "__unavailable__" 而不是 null：null 表示「用户取消了」，
                // 页面会提示「没有选中文件」；这里要让页面改走网页上传。
                if (activity instanceof MainActivity) {
                    ((MainActivity) activity).dispatchNativeImportUnavailable();
                }
            }
        });
    }

    /** 收件箱目录（页面可以显示，方便排查）。 */
    @JavascriptInterface
    public String inboxDir() {
        return FileInbox.dir(activity).getAbsolutePath();
    }

    /** 清空收件箱。 */
    @JavascriptInterface
    public void clear() {
        FileInbox.clear(activity);
    }

    /** 选择器回来后由 MainActivity 调用；返回复制成功的路径（可能为空）。 */
    static List<String> handleResult(Activity act, Intent data) {
        if (data == null) return new java.util.ArrayList<>();
        Uri[] uris = collectUris(data);
        if (uris.length == 0) return new java.util.ArrayList<>();
        return FileInbox.copyIn(act, uris);
    }

    private static Uri[] collectUris(Intent data) {
        // 多选在 ClipData，单选在 getData
        if (data.getClipData() != null) {
            int n = data.getClipData().getItemCount();
            Uri[] out = new Uri[n];
            for (int i = 0; i < n; i++) {
                out[i] = data.getClipData().getItemAt(i).getUri();
            }
            return out;
        }
        Uri single = data.getData();
        return single == null ? new Uri[0] : new Uri[]{single};
    }

    /** 路径列表转 JSON 数组字面量，交给页面。 */
    static String toJson(List<String> paths) {
        if (paths == null) return "null";
        JSONArray arr = new JSONArray();
        for (String p : paths) arr.put(p);
        return arr.toString();
    }

    /** 调试用：把结果拼成一句话。 */
    static String describe(List<String> paths) {
        if (paths == null) return "null";
        JSONObject o = new JSONObject();
        try {
            o.put("count", paths.size());
        } catch (Exception ignored) {
        }
        return o.toString();
    }
}
