package com.xniperbuilds.sendtoriplox;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The short way home: when this phone and the PC are on the same Wi-Fi, the
 * link does not need to travel to a data centre and back.
 *
 * The PC says where it is. Every verdict it seals carries the addresses it can
 * be reached at on its own network, so this never has to guess and never has
 * to discover anything - no mDNS, no broadcast, no scanning. Because it
 * arrives on every reply, a PC that moved network or was handed a new address
 * by DHCP corrects itself on the very next send.
 *
 * ⚠ THE RULE THIS FILE IS WRITTEN UNDER
 *
 * Every check here exists to make the local attempt cheap. So every check must
 * fail OPEN - if it cannot answer, it must let the attempt happen anyway and
 * let the ping decide. A check that fails closed turns "I could not tell" into
 * "never use the LAN", and a feature that never happens looks exactly like a
 * feature that was never built. Nobody would ever report it.
 *
 * The one exception is the room check, which fails CLOSED: handing a message
 * to the wrong PC is worse than not using the LAN at all.
 *
 * ⚠ Plain HTTP, deliberately. What travels is the same AES-GCM envelope the
 * relay carries and cannot open; TLS would wrap ciphertext in ciphertext, and
 * no certificate authority issues for 192.168.x.x. Nothing leaves the building.
 */
final class Lan {

    private Lan() {
    }

    /** A PC on this network answers in milliseconds. This is generous. */
    private static final int PING_MS = 1200;
    private static final int SEND_MS = 6000;

    /**
     * How long to leave a network alone after it refused to work.
     *
     * Guest, hotel and some mesh networks block device-to-device traffic
     * outright. Without this, every send on such a network would pay the ping
     * timeout before falling back - forever, and invisibly. One timeout per
     * network per ten minutes is worth paying; one per send is not.
     */
    private static final long SULK_MS = 10 * 60 * 1000L;

    private static final Map<String, Long> REFUSED =
            Collections.synchronizedMap(new HashMap<String, Long>());

    // -- which network to speak on ----------------------------------------

    /**
     * The Wi-Fi network, or null if this phone is not on one.
     *
     * ⚠ This is the load-bearing part, and it is not an optimisation.
     *
     * With mobile data and Wi-Fi both up - the ordinary state of most phones -
     * Android sends an ordinary connection over whichever it considers the
     * default, and that is frequently mobile data. A connection to
     * 192.168.x.x over the cellular interface does not reach the PC; it simply
     * fails. The feature would work for some people, fail for others, and give
     * no reason either way.
     *
     * So every local connection is bound to the Wi-Fi network explicitly.
     * ACCESS_NETWORK_STATE is all this needs and the app already holds it.
     */
    private static Network wifi(Context context) {
        try {
            ConnectivityManager manager = (ConnectivityManager)
                    context.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (manager == null) {
                return null;
            }
            for (Network network : manager.getAllNetworks()) {
                NetworkCapabilities can = manager.getNetworkCapabilities(network);
                if (can != null && can.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                    return network;
                }
            }
        } catch (Exception ignored) {
            // Cannot ask. Fall through: no Wi-Fi means no local attempt, which
            // is the same answer as not being on one.
        }
        return null;
    }

    // -- where this phone is ----------------------------------------------

    /**
     * This phone's own IPv4 addresses, with the size of the network each is on.
     *
     * ⚠ May legitimately come back empty. From Android 11, an app targeting R
     * or later only sees interfaces that have an address, and this call has
     * been progressively restricted. An empty answer therefore means "cannot
     * tell", never "not on a network" - see nearby().
     */
    private static List<InterfaceAddress> own() {
        List<InterfaceAddress> found = new ArrayList<>();
        try {
            Enumeration<NetworkInterface> faces = NetworkInterface.getNetworkInterfaces();
            while (faces != null && faces.hasMoreElements()) {
                NetworkInterface face = faces.nextElement();
                if (!face.isUp() || face.isLoopback()) {
                    continue;
                }
                for (InterfaceAddress address : face.getInterfaceAddresses()) {
                    InetAddress one = address.getAddress();
                    if (one instanceof Inet4Address && one.isSiteLocalAddress()) {
                        found.add(address);
                    }
                }
            }
        } catch (Exception ignored) {
            // Restricted, or nothing to read. Both mean "cannot tell".
        }
        return found;
    }

    private static long asLong(byte[] raw) {
        long value = 0;
        for (byte piece : raw) {
            value = (value << 8) | (piece & 0xFF);
        }
        return value;
    }

    /**
     * Is that address plausibly on a network this phone is on?
     *
     * ⚠ Fails OPEN. If the platform will not say what this phone's addresses
     * are, this answers yes and lets the ping settle it - one short timeout at
     * worst. Answering no would switch the whole feature off on any device
     * that restricts the call, silently, and that restriction is spreading.
     */
    private static boolean nearby(String host) {
        List<InterfaceAddress> mine = own();
        if (mine.isEmpty()) {
            return true;                       // cannot tell: let the ping decide
        }
        byte[] theirs;
        try {
            InetAddress parsed = InetAddress.getByName(host);
            if (!(parsed instanceof Inet4Address)) {
                return false;
            }
            theirs = parsed.getAddress();
        } catch (Exception exc) {
            return false;                      // not an address at all
        }
        long them = asLong(theirs);
        for (InterfaceAddress one : mine) {
            int bits = one.getNetworkPrefixLength();
            if (bits <= 0 || bits > 32) {
                continue;
            }
            long mask = bits == 32 ? 0xFFFFFFFFL : (~((1L << (32 - bits)) - 1)) & 0xFFFFFFFFL;
            if ((asLong(one.getAddress().getAddress()) & mask) == (them & mask)) {
                return true;
            }
        }
        return false;
    }

    // -- talking to the PC -------------------------------------------------

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

    /** A connection that is guaranteed to go over Wi-Fi, not mobile data. */
    private static HttpURLConnection open(Network network, String url, int timeoutMs)
            throws Exception {
        HttpURLConnection conn = (HttpURLConnection) network.openConnection(new URL(url));
        conn.setConnectTimeout(timeoutMs);
        conn.setReadTimeout(timeoutMs);
        conn.setRequestProperty("Accept", "application/json");
        return conn;
    }

    /** What to call this network when remembering that it refused. */
    private static String nameOf(Network network) {
        return network == null ? "" : network.toString();
    }

    /**
     * Find the PC among the addresses it gave us, or null.
     *
     * ⚠ The room must match, and that check fails CLOSED. Another Riplox on
     * the same network would answer a ping perfectly happily, and handing it
     * something meant for a different PC is worse than using the relay.
     */
    static Reach find(Context context, List<String> candidates, String room) {
        if (context == null || candidates == null || candidates.isEmpty()
                || room == null || room.length() == 0) {
            return null;
        }
        Network network = wifi(context);
        if (network == null) {
            return null;                       // no Wi-Fi: nothing is nearby
        }
        String name = nameOf(network);
        Long refusedAt = REFUSED.get(name);
        if (refusedAt != null && System.currentTimeMillis() - refusedAt < SULK_MS) {
            return null;
        }

        boolean tried = false;
        for (String hostPort : candidates) {
            int cut = hostPort.lastIndexOf(':');
            if (cut < 1 || !nearby(hostPort.substring(0, cut))) {
                continue;
            }
            tried = true;
            HttpURLConnection conn = null;
            try {
                conn = open(network, "http://" + hostPort + "/lan-ping", PING_MS);
                JSONObject said = new JSONObject(read(conn));
                if (said.optBoolean("ok", false) && room.equals(said.optString("room"))) {
                    return new Reach(network, hostPort, name);
                }
            } catch (Exception ignored) {
                // Unreachable, refused, or not Riplox. The relay is next.
            } finally {
                if (conn != null) {
                    conn.disconnect();
                }
            }
        }

        // Only remember a refusal if something was actually attempted. A
        // network where no candidate was even nearby has refused nothing, and
        // sulking about it would keep the LAN off after coming back home.
        if (tried) {
            REFUSED.put(name, System.currentTimeMillis());
        }
        return null;
    }

    /**
     * Hand the sealed envelope straight to the PC. Returns its verdict, or null.
     *
     * ⚠ "unknown" is not an answer, it is a stranger: some other PC replied,
     * could not open the envelope, and said so. Null, and the message goes to
     * the relay where the right PC is listening.
     */
    static String deliver(Reach reach, JSONObject envelope) {
        if (reach == null) {
            return null;
        }
        HttpURLConnection conn = null;
        try {
            conn = open(reach.network, "http://" + reach.hostPort + "/lan-send", SEND_MS);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            OutputStream out = conn.getOutputStream();
            out.write(envelope.toString().getBytes("UTF-8"));
            out.close();

            JSONObject said = new JSONObject(read(conn));
            String why = said.optString("why", "");
            if (why.length() > 0 && !"unknown".equals(why)) {
                return why;
            }
        } catch (Exception ignored) {
            // Reached it a moment ago and not now. The relay still works.
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
        REFUSED.put(reach.name, System.currentTimeMillis());
        return null;
    }

    /** Forget every refusal - used when the pairing changes. */
    static void reset() {
        REFUSED.clear();
    }

    /** A PC that answered, and the network it answered on. */
    static final class Reach {
        final Network network;
        final String hostPort;
        final String name;

        Reach(Network network, String hostPort, String name) {
            this.network = network;
            this.hostPort = hostPort;
            this.name = name;
        }
    }
}
