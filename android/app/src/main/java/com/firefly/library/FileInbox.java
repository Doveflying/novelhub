package com.firefly.library;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * 把 SAF（Storage Access Framework）选中的文件搬到 App 私有目录。
 *
 * 为什么要这一步：
 * 手机 WebView 上传大文件是「把整个文件塞进 HTTP multipart」，几十 MB 的
 * epub 经常传到一半断掉，而且没有断点续传。既然 Python 后端就在本机，
 * 更靠谱的做法是：Java 侧用 ContentResolver 把文件复制到私有目录，
 * 再把**路径**交给 Python 直接读 —— 不经过网络，不受上传体积限制。
 *
 * 目录约定：<filesDir>/inbox/ ，Python 侧从那里取。
 */
final class FileInbox {

    private static final String TAG = "BookFly";
    private static final String DIR = "inbox";

    private FileInbox() {}

    static File dir(Context ctx) {
        File d = new File(ctx.getFilesDir(), DIR);
        if (!d.exists()) {
            //noinspection ResultOfMethodCallIgnored
            d.mkdirs();
        }
        return d;
    }

    /** 清空收件箱（上一次没导入成功的残留别越堆越多）。 */
    static void clear(Context ctx) {
        File d = dir(ctx);
        File[] kids = d.listFiles();
        if (kids == null) return;
        for (File f : kids) {
            //noinspection ResultOfMethodCallIgnored
            f.delete();
        }
    }

    /**
     * 把若干个 content:// URI 复制进收件箱。
     *
     * @return 复制成功的本地绝对路径列表（顺序与入参一致，失败的跳过）
     */
    static List<String> copyIn(Context ctx, Uri[] uris) {
        List<String> out = new ArrayList<>();
        if (uris == null || uris.length == 0) return out;

        ContentResolver cr = ctx.getContentResolver();
        File d = dir(ctx);

        for (Uri uri : uris) {
            if (uri == null) continue;
            String name = displayName(cr, uri);
            if (name == null || name.trim().isEmpty()) {
                name = "import-" + System.currentTimeMillis();
            }
            // 去掉路径分隔符，防目录穿越
            name = name.replace('/', '_').replace('\\', '_');

            File dst = uniqueTarget(d, name);
            try (InputStream in = cr.openInputStream(uri);
                 OutputStream os = new FileOutputStream(dst)) {
                if (in == null) {
                    Log.w(TAG, "openInputStream 返回 null: " + uri);
                    continue;
                }
                byte[] buf = new byte[64 * 1024];
                int n;
                long total = 0;
                while ((n = in.read(buf)) > 0) {
                    os.write(buf, 0, n);
                    total += n;
                }
                os.flush();
                if (total <= 0) {
                    Log.w(TAG, "复制到 0 字节，丢弃: " + uri);
                    //noinspection ResultOfMethodCallIgnored
                    dst.delete();
                    continue;
                }
                Log.i(TAG, "已收入收件箱: " + dst.getName() + " (" + total + " bytes)");
                out.add(dst.getAbsolutePath());
            } catch (Exception e) {
                Log.w(TAG, "复制失败: " + uri, e);
                //noinspection ResultOfMethodCallIgnored
                dst.delete();
            }
        }
        return out;
    }

    /** 重名时加 (1)(2) 后缀，别覆盖上一次导入的。 */
    private static File uniqueTarget(File dir, String name) {
        File f = new File(dir, name);
        if (!f.exists()) return f;
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 1; i < 1000; i++) {
            f = new File(dir, base + "(" + i + ")" + ext);
            if (!f.exists()) return f;
        }
        return new File(dir, System.currentTimeMillis() + "_" + name);
    }

    /** 从 OpenableColumns 取用户看到的文件名。 */
    private static String displayName(ContentResolver cr, Uri uri) {
        Cursor c = null;
        try {
            c = cr.query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null);
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) return c.getString(idx);
            }
        } catch (Exception e) {
            Log.w(TAG, "取文件名失败: " + uri, e);
        } finally {
            if (c != null) c.close();
        }
        return uri.getLastPathSegment();
    }
}
