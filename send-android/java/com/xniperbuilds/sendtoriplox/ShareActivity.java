package com.xniperbuilds.sendtoriplox;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Where a share lands. There is no screen here on purpose.
 *
 * This is the entire reason the app exists. A web share target always opens
 * the page it belongs to - the platform gives it no way not to - so sharing a
 * video meant watching a browser window appear, do its job and sit there. Here
 * the activity finishes before it is ever drawn.
 *
 * It writes the link down and hands the sending to a job. It deliberately does
 * not send anything itself: an activity that finishes immediately leaves the
 * process with no foreground part, and Android then freezes or kills it
 * mid-request. That cost three of every four shared links until it was found.
 */
public class ShareActivity extends Activity {

    private static final Pattern LINK = Pattern.compile("https?://\\S+");

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        String shared = "";
        Intent intent = getIntent();
        if (intent != null && Intent.ACTION_SEND.equals(intent.getAction())) {
            // getCharSequenceExtra, not getStringExtra: an app sharing as
            // text/html hands over a Spanned, and asking for a String would
            // get null from it and drop a perfectly good link.
            CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            if (text != null) {
                shared = text.toString();
            }
        }

        Matcher found = LINK.matcher(shared);
        String link = found.find() ? found.group() : "";

        if (!new Store(this).paired()) {
            // Nowhere to send it. Open the app rather than failing quietly - a
            // toast alone would leave someone stuck.
            Toast.makeText(this, R.string.not_paired, Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, HomeActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            finish();
            return;
        }

        if (link.length() == 0) {
            Toast.makeText(this, R.string.no_link, Toast.LENGTH_SHORT).show();
            finish();
            return;
        }

        Outbox.add(this, link);
        // Now, not when the system feels like it. A share is a user action, so
        // starting a foreground service here is allowed - and it is the only
        // thing that goes immediately on phones with aggressive battery
        // management, which is most of them.
        SendService.start(this);
        Toast.makeText(this, R.string.sending, Toast.LENGTH_SHORT).show();

        // Before anything is drawn. The share sheet closes and the phone goes
        // back to whatever it was doing.
        finish();
    }
}
