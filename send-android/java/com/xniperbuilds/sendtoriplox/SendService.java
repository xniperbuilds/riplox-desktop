package com.xniperbuilds.sendtoriplox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

/**
 * Sends the outbox now.
 *
 * JobScheduler was the first attempt and it was not enough. It delivers - the
 * links do arrive - but the system decides when, and on the phones this app is
 * actually used on (Transsion's XOS, and most Chinese Android skins) that can
 * be minutes, or not until the app is opened. The report was blunt: "sharing
 * still needs me to open the app first".
 *
 * A share is a user action, so the app is allowed to start a foreground
 * service in that moment, and a foreground service is not something the system
 * postpones. The notification it requires is set to the lowest importance,
 * so it shows without a sound or a heads-up banner and disappears when the
 * work is done, usually within a second or two.
 *
 * SendJob stays behind it as the retry path for anything that had no network.
 */
public class SendService extends Service {

    private static final String CHANNEL = "sending";
    private static final int NOTE_ID = 1;

    static void start(Context context) {
        Intent intent = new Intent(context, SendService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, final int startId) {
        startForeground(NOTE_ID, note());

        final Context context = getApplicationContext();

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    // One sender for the whole outbox, however many shares
                    // start this service. If a drain is already running this
                    // returns at once and the link it was started for is
                    // picked up by the one that is already working - starting
                    // a second sender is how one share became four downloads.
                    if (Sender.drain(context)) {
                        SendJob.schedule(context);
                    }
                } finally {
                    stopSelf(startId);
                }
            }
        }, "riplox-send").start();

        // Restarted if the system kills us mid-send; the outbox says what is
        // left, so nothing is sent twice.
        return START_STICKY;
    }

    private Notification note() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL, getString(R.string.sending),
                    NotificationManager.IMPORTANCE_MIN);
            channel.setShowBadge(false);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
            return new Notification.Builder(this, CHANNEL)
                    .setContentTitle(getString(R.string.sending))
                    .setSmallIcon(android.R.drawable.stat_sys_upload)
                    .setOngoing(true)
                    .build();
        }
        return new Notification.Builder(this)
                .setContentTitle(getString(R.string.sending))
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .build();
    }

    @Override
    public void onDestroy() {
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
