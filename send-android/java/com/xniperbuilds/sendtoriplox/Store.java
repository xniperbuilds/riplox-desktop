package com.xniperbuilds.sendtoriplox;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * The pairing, and nothing else.
 *
 * A room id and a key this phone generated for itself. They live in the app's
 * own private storage, which no other app can read, and they are the only
 * thing this app ever stores - there is no history, no queue and no copy of
 * anything that was sent.
 */
final class Store {

    private static final String FILE = "riplox";
    private static final String ROOM = "room";
    private static final String KEY = "key";

    private final SharedPreferences prefs;

    Store(Context context) {
        prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    boolean paired() {
        return room().length() > 0 && prefs.getString(KEY, "").length() > 0;
    }

    String room() {
        return prefs.getString(ROOM, "");
    }

    byte[] key() {
        return Relay.unb64(prefs.getString(KEY, ""));
    }

    void save(String room, byte[] key) {
        prefs.edit().putString(ROOM, room).putString(KEY, Relay.b64(key)).apply();
    }

    void forget() {
        prefs.edit().clear().apply();
    }
}
