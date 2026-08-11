package com.xniperbuilds.sendtoriplox;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Keeping the app current without a store behind it.
 *
 * The relay already carries the APK and already publishes its version, size
 * and SHA-256, so an update needs nothing new on the server: ask what is
 * there, compare it with what is installed, and stream the new one straight
 * into a PackageInstaller session.
 *
 * Nothing lands on storage on the way. The bytes go into the session as they
 * arrive and the hash is taken over the same pass, so a download that was cut
 * short or interfered with is abandoned before Android is ever asked to
 * install it. The confirmation is the system's own dialog - this app cannot
 * install anything quietly, and should not be able to.
 *
 * There is no FileProvider and no APK sitting in a cache folder because the
 * session API needs neither, and every file that never exists is a file
 * nothing else on the phone can swap underneath us.
 */
final class Updater {

    private Updater() {
    }

    /** What the relay is holding, once it is known to be newer than this. */
    static final class Newer {
        final String version;
        final long size;
        final String sha256;

        Newer(String version, long size, String sha256) {
            this.version = version;
            this.size = size;
            this.sha256 = sha256;
        }
    }

    interface Progress {
        void at(int percent);
    }

    // -- what is installed -------------------------------------------------

    static long installedCode(Context context) {
        try {
            PackageInfo info = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
        } catch (Exception exc) {
            return 0;
        }
    }

    static String installedName(Context context) {
        try {
            String name = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionName;
            return name == null ? "" : name;
        } catch (Exception exc) {
            return "";
        }
    }

    // -- is there a newer one ----------------------------------------------

    /** The newer build, or null when this phone already has the latest. */
    static Newer check(Context context) throws Exception {
        JSONObject said = new JSONObject(Relay.get("/app.json", 10000));

        long code = said.optLong("code", 0);
        String version = said.optString("version", "");
        boolean newer = code > 0
                ? code > installedCode(context)
                : ahead(version, installedName(context));
        if (!newer) {
            return null;
        }

        // Without a size and a hash there is nothing to check the download
        // against, and an unverified APK is not worth offering.
        String sha256 = said.optString("sha256", "");
        long size = said.optLong("size", 0);
        if (sha256.length() != 64 || size <= 0) {
            return null;
        }
        return new Newer(version, size, sha256);
    }

    /** Only reached when the relay is old enough to publish no version code. */
    private static boolean ahead(String there, String here) {
        String[] mine = here.split("\\.");
        String[] theirs = there.split("\\.");
        for (int i = 0; i < Math.max(mine.length, theirs.length); i++) {
            int a = i < theirs.length ? number(theirs[i]) : 0;
            int b = i < mine.length ? number(mine[i]) : 0;
            if (a != b) {
                return a > b;
            }
        }
        return false;
    }

    private static int number(String text) {
        try {
            return Integer.parseInt(text.trim());
        } catch (Exception exc) {
            return 0;
        }
    }

    // -- permission --------------------------------------------------------

    /**
     * Android 8 and up ask, per app, whether it may install anything at all.
     * Better to send the user to that switch on purpose than to let the
     * install screen refuse and leave them reading a security warning.
     */
    static boolean allowed(Context context) {
        return Build.VERSION.SDK_INT < 26
                || context.getPackageManager().canRequestPackageInstalls();
    }

    static Intent permissionScreen(Context context) {
        return new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + context.getPackageName()));
    }

    // -- fetching and installing -------------------------------------------

    static void install(Context context, Newer update, Progress progress) throws Exception {
        PackageInstaller installer = context.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setSize(update.size);

        int id = installer.createSession(params);
        PackageInstaller.Session session = installer.openSession(id);
        boolean committed = false;
        try {
            String got = pour(update, session, progress);
            if (!got.equalsIgnoreCase(update.sha256)) {
                throw new Exception("that download did not match its checksum");
            }
            session.commit(confirmation(context, id).getIntentSender());
            committed = true;
        } finally {
            // A session left behind holds the disk space it was given.
            if (!committed) {
                session.abandon();
            }
            session.close();
        }
    }

    private static String pour(Newer update, PackageInstaller.Session session, Progress progress)
            throws Exception {
        HttpURLConnection conn =
                (HttpURLConnection) new URL(Relay.BASE + "/app.apk").openConnection();
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        InputStream in = null;
        OutputStream out = null;
        try {
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            in = conn.getInputStream();
            out = session.openWrite("riplox", 0, update.size);

            byte[] chunk = new byte[16384];
            long done = 0;
            int last = -1;
            int read;
            while ((read = in.read(chunk)) > 0) {
                out.write(chunk, 0, read);
                digest.update(chunk, 0, read);
                done += read;
                int percent = (int) Math.min(100, done * 100 / update.size);
                if (percent != last && progress != null) {
                    progress.at(percent);
                    last = percent;
                }
            }
            if (done != update.size) {
                throw new Exception("that download stopped early");
            }
            session.fsync(out);
        } finally {
            if (out != null) {
                out.close();
            }
            if (in != null) {
                in.close();
            }
            conn.disconnect();
        }
        return hex(digest.digest());
    }

    private static PendingIntent confirmation(Context context, int id) {
        Intent intent = new Intent(context, InstallReceiver.class);
        // The system fills this intent in with the status of the install, so
        // it has to stay mutable where that became a choice.
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 31) {
            flags |= PendingIntent.FLAG_MUTABLE;
        }
        return PendingIntent.getBroadcast(context, id, intent, flags);
    }

    private static String hex(byte[] raw) {
        StringBuilder out = new StringBuilder(raw.length * 2);
        for (byte b : raw) {
            out.append(Character.forDigit((b >> 4) & 0xF, 16));
            out.append(Character.forDigit(b & 0xF, 16));
        }
        return out.toString();
    }
}
