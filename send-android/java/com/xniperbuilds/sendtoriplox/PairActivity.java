package com.xniperbuilds.sendtoriplox;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * Pairing, on its own screen because it happens once.
 *
 * By pasted code rather than by camera: the desktop already offers "Copy
 * code", and a QR reader would mean a camera permission and a scanning
 * library many times the size of everything else in this app.
 *
 * The phone generates its own key and sends it inside a message sealed with
 * the invite's key, so the code is spent the moment it is used - anyone who
 * later finds it, in a chat or a screenshot, holds something that opens
 * nothing.
 */
public class PairActivity extends Activity {

    private EditText codeBox;
    private TextView pairState;
    private TextView pairMsg;
    private Button pairButton;
    private Button forgetButton;
    private Store store;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.pair);

        store = new Store(this);
        codeBox = (EditText) findViewById(R.id.code);
        pairState = (TextView) findViewById(R.id.pairState);
        pairMsg = (TextView) findViewById(R.id.pairMsg);
        pairButton = (Button) findViewById(R.id.pair);
        forgetButton = (Button) findViewById(R.id.forget);

        pairButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                pair(codeBox.getText().toString().trim());
            }
        });

        forgetButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                store.forget();
                codeBox.setText("");
                say(R.string.forgotten, false);
                Toast.makeText(PairActivity.this, R.string.forgotten,
                        Toast.LENGTH_SHORT).show();
                paint();
            }
        });

        paint();
        fromLink(getIntent());
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        fromLink(intent);
    }

    /**
     * riploxsend://pair?c=room.key.code, followed from the pairing page.
     *
     * The page could pair itself, and used to - which quietly spent the code
     * on the browser and left the app needing a second one. Now the page sends
     * the code here instead.
     */
    private void fromLink(android.content.Intent intent) {
        if (intent == null || intent.getData() == null) {
            return;
        }
        String code;
        try {
            code = intent.getData().getQueryParameter("c");
        } catch (Exception exc) {
            return;
        }
        if (code == null || code.length() == 0) {
            return;
        }
        codeBox.setText(code);
        pair(code.trim());
    }

    private void paint() {
        boolean paired = store.paired();
        pairState.setText(paired ? R.string.has_pairing : R.string.not_paired_yet);
        pairButton.setText(paired ? R.string.repair : R.string.pair);
        forgetButton.setVisibility(paired ? View.VISIBLE : View.GONE);
        // The code box is never hidden - see the note at the top.
    }

    private void say(int message, boolean bad) {
        pairMsg.setText(message);
        pairMsg.setTextColor(getColor(bad ? R.color.bad : R.color.cyan));
        pairMsg.setVisibility(View.VISIBLE);
    }

    /** room.key.code, exactly as the desktop's Sharing screen shows it. */
    private void pair(String typed) {
        final String[] parts = typed.split("\\.");
        if (parts.length != 3 || !parts[0].matches("[a-f0-9]{16,64}")) {
            say(R.string.bad_code, true);
            return;
        }

        say(R.string.pairing, false);
        pairButton.setEnabled(false);

        new Thread(new Runnable() {
            @Override
            public void run() {
                int message;
                boolean ok = false;
                try {
                    byte[] own = Relay.randomBytes(32);

                    JSONObject hello = new JSONObject();
                    hello.put("kind", "hello");
                    hello.put("code", parts[2]);
                    hello.put("key", Relay.b64(own));
                    hello.put("name", android.os.Build.MODEL);

                    String why = Relay.deliver(parts[0], Relay.unb64(parts[1]), hello);
                    if ("paired".equals(why)) {
                        // Only now is any existing pairing replaced, so a
                        // failed attempt never costs a working one.
                        store.save(parts[0], own);
                        ok = true;
                        message = R.string.is_paired;
                    } else if ("used".equals(why)) {
                        message = R.string.code_used;
                    } else if ("expired".equals(why)) {
                        message = R.string.code_expired;
                    } else if (why.length() == 0) {
                        // Pairing is the one thing that cannot wait in a
                        // postbox: the PC has to be running to record it, and
                        // the code only lasts two minutes.
                        message = R.string.no_answer;
                    } else {
                        message = R.string.pair_failed;
                    }
                } catch (Exception exc) {
                    message = R.string.no_reach;
                }
                done(message, ok);
            }
        }, "riplox-pair").start();
    }

    private void done(final int message, final boolean ok) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                pairButton.setEnabled(true);
                say(message, !ok);
                if (ok) {
                    codeBox.setText("");
                    paint();
                    Toast.makeText(PairActivity.this, R.string.is_paired,
                            Toast.LENGTH_LONG).show();
                    finish();     // straight back to Home, which re-checks
                }
            }
        });
    }
}
