package com.xniperbuilds.sendtoriplox;

import android.util.Base64;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.SecureRandom;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Talking to the Riplox relay.
 *
 * The relay is a postbox: it carries a sealed envelope from this phone to one
 * PC and cannot read what it carries. Everything that matters happens here and
 * on the PC - the key never leaves the two of them.
 *
 * AES-GCM with a 12-byte nonce and a 128-bit tag, which is exactly what the
 * desktop's Python side produces, and base64url without padding, which is what
 * it expects to receive. Both halves were matched deliberately rather than
 * hopefully: a mismatch here fails as "unknown device", which looks like a
 * pairing problem and is not one.
 */
final class Relay {

    static final String BASE = "https://relay.xniperbuilds.com";

    private static final int B64 = Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP;
    private static final SecureRandom RANDOM = new SecureRandom();

    private Relay() {
    }

    // -- encoding ---------------------------------------------------------

    static String b64(byte[] raw) {
        return Base64.encodeToString(raw, B64);
    }

    static byte[] unb64(String text) {
        return Base64.decode(text, B64);
    }

    static byte[] randomBytes(int n) {
        byte[] out = new byte[n];
        RANDOM.nextBytes(out);
        return out;
    }

    // -- the envelope -----------------------------------------------------

    /** {n, c} for a message only the paired PC can open. */
    static JSONObject seal(byte[] key, String plain) throws Exception {
        byte[] nonce = randomBytes(12);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"),
                new GCMParameterSpec(128, nonce));
        byte[] sealed = cipher.doFinal(plain.getBytes("UTF-8"));

        JSONObject out = new JSONObject();
        out.put("n", b64(nonce));
        out.put("c", b64(sealed));
        return out;
    }

    /** The PC's reply, or null if it was not for us. */
    static JSONObject open(byte[] key, String nonce64, String cipher64) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"),
                    new GCMParameterSpec(128, unb64(nonce64)));
            return new JSONObject(new String(cipher.doFinal(unb64(cipher64)), "UTF-8"));
        } catch (Exception ignored) {
            return null;
        }
    }

    // -- http -------------------------------------------------------------

    private static String read(HttpURLConnection conn) throws Exception {
        InputStream in = conn.getResponseCode() >= 400
                ? conn.getErrorStream() : conn.getInputStream();
        if (in == null) {
            return "";
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int read;
        while ((read = in.read(chunk)) > 0) {
            buffer.write(chunk, 0, read);
        }
        in.close();
        return buffer.toString("UTF-8");
    }

    static String post(String path, String body, int timeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(BASE + path).openConnection();
        try {
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Accept", "application/json");
            OutputStream out = conn.getOutputStream();
            out.write(body.getBytes("UTF-8"));
            out.close();
            return read(conn);
        } finally {
            conn.disconnect();
        }
    }

    static String get(String path, int timeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(BASE + path).openConnection();
        try {
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setRequestProperty("Accept", "application/json");
            return read(conn);
        } finally {
            conn.disconnect();
        }
    }

    // -- what the app actually does ---------------------------------------

    /**
     * Leave a sealed message and wait for the PC's own verdict.
     *
     * The relay can only ever say "left for your PC", because it cannot read
     * the message. The PC seals its answer under the same key and leaves it
     * on the way back, so a toast can say "downloading" or "that phone is
     * paused" instead of a hopeful "sent".
     *
     * Returns the PC's word, or "" when nothing came back in time - which is
     * not a failure, only silence.
     */
    static String deliver(String room, byte[] key, JSONObject body) throws Exception {
        JSONObject envelope = envelope(key, body);
        leave(room, envelope);
        return verdict(room, key, envelope.optString("r"), 12);
    }

    /**
     * Seal one message, and hand back the reply id with it.
     *
     * Separate from sending it because a message that is going to be re-sent
     * has to be re-sent *identically*. Sealing again would produce a fresh
     * nonce, and a fresh nonce is a different message to the PC - which is how
     * one link shared once ended up downloaded four times. Seal once, keep it,
     * send the same bytes however many attempts it takes, and the PC's own
     * replay guard does the rest.
     */
    static JSONObject envelope(byte[] key, JSONObject body) throws Exception {
        String rid = b64(randomBytes(12));
        body.put("r", rid);
        body.put("ts", System.currentTimeMillis() / 1000.0);

        JSONObject envelope = seal(key, body.toString());
        // Carried for our own use. The relay reads n and c and ignores the
        // rest, and it learns this id from the /ack request anyway.
        envelope.put("r", rid);
        return envelope;
    }

    /** Leave a sealed envelope in the postbox. Throws if it was not taken. */
    static void leave(String room, JSONObject envelope) throws Exception {
        String answer = post("/send/" + room, envelope.toString(), 15000);
        if (!new JSONObject(answer).optBoolean("ok", false)) {
            throw new Exception("the relay would not take it");
        }
    }

    /**
     * The PC's own word on a message already left, or "" for silence.
     *
     * Never throws: by the time this is called the message is in the postbox,
     * so nothing here can make it un-sent. Silence means the PC is not running
     * - which is not a failure, and must never be treated as one.
     */
    static String verdict(String room, byte[] key, String rid, int hold) {
        JSONObject said = reply(room, key, rid, hold);
        return said == null ? "" : said.optString("why", "");
    }

    /**
     * The whole of what the PC said, opened, or null if it said nothing.
     *
     * Separate from verdict() because the reply now carries a second thing:
     * the addresses the PC can be reached at on its own network. That is how
     * this phone learns to skip the relay next time both are on the same
     * Wi-Fi - it never has to discover anything, and a PC that changed network
     * or was given a new address corrects itself on the very next send.
     */
    static JSONObject reply(String room, byte[] key, String rid, int hold) {
        try {
            JSONObject ack = new JSONObject(
                    get("/ack/" + room + "?r=" + rid + "&hold=" + hold, hold * 1000 + 8000));
            if (ack.optBoolean("ok", false) && ack.has("ack")) {
                JSONObject sealed = ack.getJSONObject("ack");
                return open(key, sealed.optString("n"), sealed.optString("c"));
            }
        } catch (Exception ignored) {
            // The message is in the postbox either way, and the PC picks it up
            // whenever it is next running.
        }
        return null;
    }

    /** The addresses out of a reply, or empty. Never null, never a null entry. */
    static java.util.List<String> lanFrom(JSONObject said) {
        java.util.List<String> found = new java.util.ArrayList<>();
        if (said == null) {
            return found;
        }
        org.json.JSONArray list = said.optJSONArray("lan");
        if (list == null) {
            return found;
        }
        for (int at = 0; at < list.length() && found.size() < 4; at++) {
            String one = list.optString(at, "");
            // An address:port and nothing else. Anything else is not something
            // to open a connection to, whatever sent it.
            if (one.length() > 0 && one.length() <= 64 && one.lastIndexOf(':') > 0) {
                found.add(one);
            }
        }
        return found;
    }

    /** Plain English for what the PC said. */
    static String words(String why) {
        if ("queued".equals(why)) return "Downloading on your PC";
        if ("held".equals(why)) return "Sent - waiting for your approval on the PC";
        if ("paired".equals(why)) return "Paired";
        if ("paused".equals(why)) return "This phone is paused on the PC";
        if ("site".equals(why)) return "This phone is not allowed to send that site";
        if ("day-limit".equals(why)) return "Today's downloads are used up";
        if ("total-limit".equals(why)) return "This phone's allowance is used up";
        if ("replay".equals(why)) return "That one was already sent";
        if ("duplicate".equals(why)) return "Already on your PC - not downloading it twice";
        if ("stale".equals(why)) return "Your phone's clock is too far ahead";
        if ("expired".equals(why)) return "That pairing code has expired";
        if ("used".equals(why)) return "That pairing code was already used";
        if ("revoked".equals(why)) return "Your PC removed this phone - open Riplox Send for a new code";
        if ("unknown".equals(why)) return "Your PC did not recognise this phone";
        if ("bad-link".equals(why)) return "That link was refused";
        return "";
    }
}
