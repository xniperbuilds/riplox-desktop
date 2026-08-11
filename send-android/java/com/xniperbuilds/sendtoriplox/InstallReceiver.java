package com.xniperbuilds.sendtoriplox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.widget.Toast;

/**
 * The system's answer to an install.
 *
 * A committed session does not install anything on its own - it comes back
 * here first asking for the user to be shown the confirmation screen, and
 * comes back a second time with what they decided.
 */
public class InstallReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS,
                PackageInstaller.STATUS_FAILURE);

        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) {
                // A broadcast has no task of its own to show an activity in.
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(confirm);
            }
            return;
        }

        if (status == PackageInstaller.STATUS_SUCCESS) {
            // Often unseen: the app is replaced and restarted around now.
            say(context, context.getString(R.string.updated));
            return;
        }

        // Saying no is a decision, not a fault, and needs no message.
        if (status == PackageInstaller.STATUS_FAILURE_ABORTED) {
            return;
        }

        String why = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
        say(context, context.getString(R.string.update_failed,
                why == null || why.length() == 0 ? "Android refused it" : why));
    }

    private void say(Context context, String words) {
        Toast.makeText(context, words, Toast.LENGTH_LONG).show();
    }
}
