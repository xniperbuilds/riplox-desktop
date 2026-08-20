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
        put(context, "url", url);
    }

    /**
     * Text that is not a link - a licence key, a password, a line to paste.
     *
     * Its own entry point rather than a flag on add(), so that nothing about
     * the link path changes shape. A share that works today must still take
     * exactly the same route tomorrow.
     */
    static void addText(Context context, String text) {
        put(context, "text", text);
    }

    private static void put(Context context, String field, String value) {
        synchronized (LOCK) {
            JSONArray items = read(context);
            try {
                JSONObject item = new JSONObject();
                item.put(field, value);
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

    /** The oldest one still waiting, or null. */
    static JSONObject first(Context context) {
        synchronized (LOCK) {
            JSONArray items = read(context);
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item != null) {
                    return item;
                }
            }
            return null;
        }
    }

    /**
     * The sealed envelope for one waiting link - sealed once, then kept.
     *
     * This is the whole defence against sending the same link twice. Sealing
     * uses a fresh random nonce, and the nonce is what the PC recognises a
     * repeat by: seal the same link a second time and the PC has no way left
     * to know it is the same link, so it downloads it again. Fourteen links
     * shared at a switched-off PC came back with four copies of some of them
     * for exactly this reason.
     *
     * Sealed on the first attempt and written down beside the link, so every
     * later attempt sends the identical message and the PC refuses it as the
     * replay it is.
     */
    static JSONObject envelope(Context context, JSONObject item, byte[] key) throws Exception {
        synchronized (LOCK) {
            String url = item.optString("url");
            String text = item.optString("text");
            long at = item.optLong("at");

            JSONObject kept = new JSONObject();
            if (item.optString("n", "").length() > 0
                    && item.optString("c", "").length() > 0
                    && item.optString("r", "").length() > 0) {
                kept.put("n", item.optString("n"));
                kept.put("c", item.optString("c"));
                kept.put("r", item.optString("r"));
                return kept;
            }

            JSONObject body = new JSONObject();
            if (text.length() > 0) {
                // No quality: there is nothing to download. The PC keeps this
                // sealed until somebody presses Copy.
                body.put("text", text);
            } else {
                body.put("url", url);
                body.put("quality", "");       // whatever the PC is set to
            }
            JSONObject sealed = Relay.envelope(key, body);

            JSONArray items = read(context);
            for (int i = 0; i < items.length(); i++) {
                JSONObject stored = items.optJSONObject(i);
                // Matched on the text as well as the link. Text entries carry
                // no url, so two of them would both look like "" and the wrong
                // one could be given this envelope - the timestamp alone is a
                // thin thing to rest that on.
                if (stored == null || !url.equals(stored.optString("url"))
                        || !text.equals(stored.optString("text"))
                        || at != stored.optLong("at")) {
                    continue;
                }
                stored.put("n", sealed.optString("n"));
                stored.put("c", sealed.optString("c"));
                stored.put("r", sealed.optString("r"));
            }
            write(context, items);
            return sealed;
        }
    }

    /**
     * Drop one that has been delivered, leaving the rest alone.
     *
     * Identified by its text as well as its link, for the same reason the
     * envelope cache is: getting this wrong deletes somebody else's pending
     * share, which is a message that silently never arrives.
     */
    static void done(Context context, String url, String text, long at) {
        synchronized (LOCK) {
            JSONArray items = read(context);
            JSONArray kept = new JSONArray();
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) {
                    continue;
                }
                if (url.equals(item.optString("url"))
                        && text.equals(item.optString("text"))
                        && at == item.optLong("at")) {
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
