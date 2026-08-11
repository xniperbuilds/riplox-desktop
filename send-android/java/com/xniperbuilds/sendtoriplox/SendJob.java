package com.xniperbuilds.sendtoriplox;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Sends whatever is in the outbox, owned by the system rather than by a screen.
 *
 * A JobService keeps the process alive for as long as the work runs, which a
 * bare thread started by a finishing activity does not - that was the bug
 * behind "I shared four links and only the last one arrived". If the network
 * is down or the relay is unreachable the job asks to be run again, so a link
 * shared in a tunnel goes out when the signal comes back.
 */
public class SendJob extends JobService {

    private static final int JOB_ID = 4711;
    private volatile boolean stopped;

    /** Ask the system to drain the outbox as soon as it reasonably can. */
    static void schedule(Context context) {
        JobInfo job = new JobInfo.Builder(JOB_ID,
                new ComponentName(context, SendJob.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                // As soon as possible; without a deadline the system may sit
                // on it, and a shared link is worth nothing an hour later.
                .setOverrideDeadline(0)
                .setBackoffCriteria(15_000, JobInfo.BACKOFF_POLICY_EXPONENTIAL)
                .build();

        JobScheduler scheduler = (JobScheduler)
                context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler != null) {
            scheduler.schedule(job);
        }
    }

    @Override
    public boolean onStartJob(final JobParameters params) {
        final Context context = getApplicationContext();
        final Store store = new Store(context);

        if (!store.paired() || Outbox.size(context) == 0) {
            return false;                       // nothing to do
        }

        new Thread(new Runnable() {
            @Override
            public void run() {
                boolean again = false;
                JSONArray items = Outbox.take(context);

                for (int i = 0; i < items.length() && !stopped; i++) {
                    JSONObject item = items.optJSONObject(i);
                    if (item == null) {
                        continue;
                    }
                    String url = item.optString("url");
                    long at = item.optLong("at");

                    try {
                        JSONObject body = new JSONObject();
                        body.put("url", url);
                        body.put("quality", "");   // whatever the PC is set to
                        String why = Relay.deliver(store.room(), store.key(), body);

                        // Anything the PC actually answered is a final answer,
                        // including a refusal - repeating it would only queue
                        // the same link again tomorrow.
                        Outbox.done(context, url, at);
                        String words = Relay.words(why);
                        say(context, words.length() > 0 ? words
                                : context.getString(R.string.left_for_pc));
                    } catch (Exception exc) {
                        // Could not be delivered at all. Leave it in the
                        // outbox and let the system bring us back.
                        again = true;
                    }
                }

                jobFinished(params, again || stopped);
            }
        }, "riplox-outbox").start();

        return true;                            // still working
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        stopped = true;
        return true;                            // come back for what is left
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
