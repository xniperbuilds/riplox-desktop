package com.xniperbuilds.sendtoriplox;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Home: what the PC is doing, and a box to send a link from.
 *
 * Pairing is deliberately not here. It happens once, and putting a setup step
 * on the screen you open every day makes the app look like it is still being
 * configured.
 */
public class HomeActivity extends Activity {

    private static final Pattern LINK = Pattern.compile("https?://\\S+");

    private View dot;
    private TextView status;
    private TextView statusWhy;
    private EditText link;
    private Button send;
    private Button check;
    private Store store;

    private View updateCard;
    private TextView updateWhy;
    private Button update;
    private Updater.Newer waiting;
    /** Owned by the update thread: nothing else may start a second one. */
    private volatile boolean updating;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.home);

        store = new Store(this);
        dot = findViewById(R.id.dot);
        status = (TextView) findViewById(R.id.status);
        statusWhy = (TextView) findViewById(R.id.statusWhy);
        link = (EditText) findViewById(R.id.link);
        send = (Button) findViewById(R.id.send);
        check = (Button) findViewById(R.id.check);
        updateCard = findViewById(R.id.updateCard);
        updateWhy = (TextView) findViewById(R.id.updateWhy);
        update = (Button) findViewById(R.id.update);

        ((TextView) findViewById(R.id.version)).setText(
                getString(R.string.version_line, Updater.installedName(this)));

        update.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                takeUpdate();
            }
        });

        send.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                sendLink();
            }
        });

        check.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                refresh();
            }
        });

        findViewById(R.id.pairing).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(HomeActivity.this, PairActivity.class));
            }
        });

        link.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override
            public boolean onEditorAction(TextView view, int id, android.view.KeyEvent event) {
                if (id == EditorInfo.IME_ACTION_SEND) {
                    sendLink();
                    return true;
                }
                return false;
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Opening the app is itself the question "is this still working?", so
        // it is answered without being asked.
        refresh();

        // A second chance for anything the system has not got around to
        // sending yet - a link shared with no signal, for instance.
        if (Outbox.size(this) > 0) {
            SendJob.schedule(this);
        }

        lookForUpdate();
    }

    // -- updating ---------------------------------------------------------

    /**
     * Asked on every visit rather than on a timer. It is one small request
     * next to the ping this screen already makes, and a schedule would only
     * add a way for the answer to be out of date.
     */
    private void lookForUpdate() {
        if (updating) {
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                Updater.Newer found;
                try {
                    found = Updater.check(HomeActivity.this);
                } catch (Exception exc) {
                    // No connection, or nothing there. Neither is worth a
                    // banner on a screen that has a status light already.
                    found = null;
                }
                offer(found);
            }
        }, "riplox-update-check").start();
    }

    private void offer(final Updater.Newer found) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                if (updating) {
                    return;
                }
                waiting = found;
                if (found == null) {
                    updateCard.setVisibility(View.GONE);
                    return;
                }
                updateWhy.setText(getString(R.string.update_why,
                        found.version, Math.round(found.size / 1024.0)));
                update.setEnabled(true);
                update.setText(R.string.update_now);
                updateCard.setVisibility(View.VISIBLE);
            }
        });
    }

    private void takeUpdate() {
        final Updater.Newer take = waiting;
        if (take == null || updating) {
            return;
        }

        // Android asks, per app, whether it may install anything at all.
        // Sending the user straight to that switch beats letting the install
        // screen refuse and leave them reading a security warning.
        if (!Updater.allowed(this)) {
            Toast.makeText(this, R.string.update_allow, Toast.LENGTH_LONG).show();
            startActivity(Updater.permissionScreen(this));
            return;
        }

        updating = true;
        update.setEnabled(false);
        update.setText(getString(R.string.updating, 0));

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Updater.install(HomeActivity.this, take, new Updater.Progress() {
                        @Override
                        public void at(int percent) {
                            step(getString(R.string.updating, percent));
                        }
                    });
                    // Committed. Android's own dialog has it from here, and
                    // whether it is accepted is no longer this app's business.
                    step(getString(R.string.update_handing_over));
                    updating = false;
                } catch (Exception exc) {
                    updating = false;
                    failed(exc);
                }
            }
        }, "riplox-update").start();
    }

    private void step(final String words) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                update.setText(words);
            }
        });
    }

    private void failed(final Exception exc) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                update.setEnabled(true);
                update.setText(R.string.update_now);
                String why = exc.getMessage();
                Toast.makeText(HomeActivity.this,
                        getString(R.string.update_failed,
                                why == null || why.length() == 0 ? "no connection" : why),
                        Toast.LENGTH_LONG).show();
            }
        });
    }

    // -- the status light -------------------------------------------------

    private void paint(int colour, int title, int why) {
        // setTint rather than setColorFilter, which is deprecated; and
        // mutate(), or tinting this dot would tint every view sharing the
        // same drawable instance.
        dot.getBackground().mutate().setTint(getColor(colour));
        status.setText(title);
        statusWhy.setText(why);
        boolean usable = title == R.string.pc_on || title == R.string.pc_quiet;
        send.setEnabled(usable);
    }

    private void refresh() {
        if (!store.paired()) {
            paint(R.color.idle, R.string.pc_unpaired, R.string.pc_unpaired_why);
            check.setVisibility(View.GONE);
            return;
        }
        check.setVisibility(View.VISIBLE);
        check.setEnabled(false);
        paint(R.color.idle, R.string.checking, R.string.checking);

        new Thread(new Runnable() {
            @Override
            public void run() {
                int colour, title, why;
                try {
                    JSONObject ping = new JSONObject();
                    ping.put("kind", "ping");
                    String said = Relay.deliver(store.room(), store.key(), ping);

                    if ("pong".equals(said)) {
                        colour = R.color.good;
                        title = R.string.pc_on;
                        why = R.string.pc_on_why;
                    } else if ("paused".equals(said)) {
                        colour = R.color.warn;
                        title = R.string.pc_paused;
                        why = R.string.pc_paused_why;
                    } else if ("revoked".equals(said) || "unknown".equals(said)) {
                        colour = R.color.bad;
                        title = R.string.pc_revoked;
                        why = R.string.pc_revoked_why;
                    } else {
                        // Silence is not a failure. The PC answers only while
                        // it is running, and a link left for it still waits.
                        colour = R.color.idle;
                        title = R.string.pc_quiet;
                        why = R.string.pc_quiet_why;
                    }
                } catch (Exception exc) {
                    colour = R.color.bad;
                    title = R.string.pc_offline;
                    why = R.string.pc_offline_why;
                }
                show(colour, title, why);
            }
        }, "riplox-ping").start();
    }

    private void show(final int colour, final int title, final int why) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                check.setEnabled(true);
                paint(colour, title, why);
            }
        });
    }

    // -- sending a link typed here ----------------------------------------

    private void sendLink() {
        if (!store.paired()) {
            startActivity(new Intent(this, PairActivity.class));
            return;
        }

        Matcher found = LINK.matcher(link.getText().toString());
        if (!found.find()) {
            Toast.makeText(this, R.string.no_link, Toast.LENGTH_SHORT).show();
            return;
        }
        final String url = found.group();

        send.setEnabled(false);
        send.setText(R.string.sending);

        new Thread(new Runnable() {
            @Override
            public void run() {
                String message;
                boolean ok = false;
                try {
                    JSONObject body = new JSONObject();
                    body.put("url", url);
                    body.put("quality", "");      // whatever the PC is set to
                    String said = Relay.deliver(store.room(), store.key(), body);
                    String words = Relay.words(said);
                    ok = said.length() == 0 || "queued".equals(said) || "held".equals(said);
                    message = words.length() > 0 ? words
                            : getString(R.string.left_for_pc);
                } catch (Exception exc) {
                    message = getString(R.string.no_reach);
                }
                sent(message, ok);
            }
        }, "riplox-send").start();
    }

    private void sent(final String message, final boolean ok) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                send.setEnabled(true);
                send.setText(R.string.send_now);
                if (ok) {
                    link.setText("");
                }
                Toast.makeText(HomeActivity.this, message, Toast.LENGTH_LONG).show();
                refresh();
            }
        });
    }
}
