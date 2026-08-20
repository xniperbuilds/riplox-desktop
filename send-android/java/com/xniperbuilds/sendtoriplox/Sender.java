package com.xniperbuilds.sendtoriplox;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * One outbox, one sender.
 *
 * Every share used to start its own sender, and each one took the whole outbox
 * to work through. That is fine while a link leaves in a moment - it is gone
 * from the list before the next share arrives. It stops being fine the moment
 * a link takes time to leave, and one did: the sender waited up to twelve
 * seconds for the PC's own word on each link, and with the PC switched off it
 * waited the full twelve every time. Share fourteen videos in a row at a
 * sleeping PC and sender number two, three and four each found the first links
 * still sitting there and sent them again. That is what "I shared it once and
 * it downloaded four times" was.
 *
 * Two things fix it, and both are here because either alone still leaves a
 * gap:
 *
 *   * only one drain runs at a time. A share that arrives while one is running
 *     is simply added to the outbox - the running drain re-reads it after
 *     every link, so it picks the new one up on its own.
 *   * a link leaves the outbox as soon as the relay has it, before anything is
 *     waited for. The waiting is for a message on the screen; the link is
 *     already delivered and must not still be sitting somewhere re-sendable.
 *
 * And behind both, the thing that makes a genuine retry harmless: the envelope
 * is sealed once and kept, so a second attempt is byte-for-byte the same
 * message and the PC refuses it as a replay. Nothing here can prevent a
 * response going missing on a bad network - only that can make it not matter.
 */
final class Sender {

    /** Held for as long as one drain is working through the outbox. */
    private static final AtomicBoolean BUSY = new AtomicBoolean(false);

    private Sender() {
    }

    /**
     * Send whatever is waiting.
     *
     * Returns true when something is still left afterwards, which is the
     * caller's cue to arrange another go later.
     */
    static boolean drain(Context context) {
        Store store = new Store(context);
        if (!store.paired()) {
            return false;              // nowhere to send it; not a retry either
        }

        if (!BUSY.compareAndSet(false, true)) {
            // Someone else has the outbox. Adding a second sender here is the
            // bug this class exists to stop.
            return false;
        }

        boolean leftover = false;
        try {
            while (true) {
                JSONObject item = Outbox.first(context);
                if (item == null) {
                    break;
                }
                String url = item.optString("url");
                String text = item.optString("text");
                long at = item.optLong("at");

                JSONObject envelope;
                try {
                    envelope = Outbox.envelope(context, item, store.key());
                } catch (Exception exc) {
                    // A link that cannot even be sealed will never be sendable
                    // - keeping it would only circle forever.
                    Outbox.done(context, url, text, at);
                    continue;
                }

                try {
                    Relay.leave(store.room(), envelope);
                } catch (Exception exc) {
                    // No network. The rest would fail the same way, so stop
                    // and let the retry path bring us back.
                    leftover = true;
                    break;
                }

                // It is in the postbox. Off the list now, before the wait
                // below - a link still on the list is a link something else
                // can pick up and send again.
                Outbox.done(context, url, text, at);

                // Only for what the toast says. A short wait while more links
                // are queued, because the PC answers in a moment when it is
                // running and there is no point holding up thirteen others for
                // one that is not.
                int hold = Outbox.size(context) > 0 ? 2 : 12;
                String words = Relay.words(
                        Relay.verdict(store.room(), store.key(), envelope.optString("r"), hold));
                say(context, words.length() > 0 ? words
                        : context.getString(R.string.left_for_pc));
            }
        } finally {
            BUSY.set(false);
        }

        // Something may have been shared in the moment between the last look
        // and letting go of the outbox. Reported rather than sent from here,
        // so this never turns into a loop that cannot be interrupted.
        return leftover || Outbox.size(context) > 0;
    }

    private static void say(final Context context, final String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(context, message, Toast.LENGTH_LONG).show();
            }
        });
    }
}
