package com.xniperbuilds.sendtoriplox;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * The pairing, and nothing else.
 *
 * A room id and a key this phone generated for itself. They live in the app's
 * own private storage, which no other app can read, and with one addition they
 * are the only thing this app ever stores - there is no history, no queue and
 * no copy of anything that was sent.
 *
 * The addition is where the PC said it can be reached on its own network, so a
 * link shared while both are on the same Wi-Fi does not have to travel to a
 * data centre and back. It is not a secret and it is not a record of anything
 * that was sent - it is the PC's own address on the user's own network, which
 * the PC hands over inside a sealed reply.
 */
final class Store {

    private static final String FILE = "riplox";
    private static final String ROOM = "room";
    private static final String KEY = "key";
    private static final String LAN = "lan";

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
        // A new pairing means a different PC, so anything remembered about
        // where the old one lived is now a wrong answer rather than a stale
        // one. Cleared here rather than left to expire.
        prefs.edit().putString(ROOM, room).putString(KEY, Relay.b64(key))
             .remove(LAN).apply();
        Lan.reset();
    }

    /**
     * Where the PC last said it could be reached, newest first.
     *
     * Several, because which one this phone can reach depends on the phone's
     * network and not on the PC's opinion of itself - a PC with a VPN or a
     * second adapter has more than one address and only some of them are
     * reachable from here.
     */
    java.util.List<String> lan() {
        java.util.List<String> found = new java.util.ArrayList<>();
        String saved = prefs.getString(LAN, "");
        for (String piece : saved.split("\\|")) {
            if (piece.length() > 0) {
                found.add(piece);
            }
        }
        return found;
    }

    void rememberLan(java.util.List<String> addresses) {
        if (addresses == null || addresses.isEmpty()) {
            // The PC had nothing to offer this time - it may be on a network
            // with no usable address. Keep what we had rather than forgetting:
            // it costs one ping to find out, and forgetting costs the feature.
            return;
        }
        StringBuilder joined = new StringBuilder();
        for (String address : addresses) {
            if (joined.length() > 0) {
                joined.append('|');
            }
            joined.append(address);
        }
        String now = joined.toString();
        if (now.equals(prefs.getString(LAN, ""))) {
            return;                            // same as before, nothing to do
        }
        prefs.edit().putString(LAN, now).apply();

        /* ⚠ The PC has moved, or was given a new address. Forget which
         * networks refused us.
         *
         * Lan remembers a refusal for ten minutes so a guest Wi-Fi that blocks
         * device-to-device traffic costs one timeout rather than one per send.
         * But "this network blocks it" and "we were holding the wrong address"
         * both arrive as the same failed ping, and only the first deserves to
         * be remembered.
         *
         * Without this, moving both phone and PC to another Wi-Fi looked like
         * a network that refuses: the first send would fail on the stale
         * address, mark the new network refused, and then every send for the
         * next ten minutes would skip the local path without even trying -
         * while the correct address sat in storage, learned from that very
         * first reply. A new address is new information, so the old verdict
         * goes with it.
         */
        Lan.reset();
    }

    void forget() {
        prefs.edit().clear().apply();
        Lan.reset();
    }
}
