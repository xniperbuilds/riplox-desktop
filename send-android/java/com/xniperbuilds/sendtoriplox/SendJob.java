package com.xniperbuilds.sendtoriplox;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

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
                // The same one sender the share path uses. If a foreground
                // send is already working through the outbox this returns
                // immediately and asks to come back - two senders on one
                // outbox is what sent the same link more than once.
                boolean again = Sender.drain(context);
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
}
