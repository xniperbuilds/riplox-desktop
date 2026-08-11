package com.xniperbuilds.sendtoriplox;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;

/**
 * Links waiting to go out, on disk.
 *
 * The first version of this app sent the link on a plain thread and finished
 * the activity immediately. That is wrong in a way that only shows up in real
 * use: once the activity is gone the process has no foreground part left, so
 * Android is free to freeze or kill it - and it does. Share four links in a
 * row and three of them die mid-request; opening the app later revives the
 * process and the survivor finally goes. That is exactly what was reported.
 *
 * So the link is written down first and sent afterwards by a job the system
 * owns. Nothing is lost if the process dies, because nothing was ever only in
 * memory.
 */
final class Outbox {

    private static final String FILE = "outbox.json";
    private static final int CAP = 100;
    private static final Object LOCK = new Object();

    private Outbox() {
    }

    private static File file(Context context) {
        return new File(context.getFilesDir(), FILE);
    }

    static void add(Context context, String url) {
        synchronized (LOCK) {
            JSONArray items = read(context);
            try {
                JSONObject item = new JSONObject();
                item.put("url", url);
                item.put("at", System.currentTimeMillis());
                items.put(item);
                while (items.length() > CAP) {
                    items.remove(0);
                }
                write(context, items);
            } catch (Exception ignored) {
            }
        }
    }

    /** Everything waiting, oldest first. */
    static JSONArray take(Context context) {
        synchronized (LOCK) {
            return read(context);
        }
    }

    /** Drop one that has been delivered, leaving the rest alone. */
    static void done(Context context, String url, long at) {
        synchronized (LOCK) {
            JSONArray items = read(context);
            JSONArray kept = new JSONArray();
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) {
                    continue;
                }
                if (url.equals(item.optString("url")) && at == item.optLong("at")) {
                    continue;
                }
                kept.put(item);
            }
            write(context, kept);
        }
    }

    static int size(Context context) {
        synchronized (LOCK) {
            return read(context).length();
        }
    }

    // -- disk ------------------------------------------------------------

    private static JSONArray read(Context context) {
        File path = file(context);
        if (!path.exists()) {
            return new JSONArray();
        }
        try {
            RandomAccessFile handle = new RandomAccessFile(path, "r");
            byte[] raw = new byte[(int) handle.length()];
            handle.readFully(raw);
            handle.close();
            return new JSONArray(new String(raw, "UTF-8"));
        } catch (Exception exc) {
            return new JSONArray();
        }
    }

    private static void write(Context context, JSONArray items) {
        try {
            FileOutputStream out = new FileOutputStream(file(context));
            out.write(items.toString().getBytes("UTF-8"));
            out.close();
        } catch (Exception ignored) {
        }
    }
}
