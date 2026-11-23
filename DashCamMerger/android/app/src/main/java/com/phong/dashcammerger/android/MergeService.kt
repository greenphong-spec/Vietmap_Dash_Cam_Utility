package com.phong.dashcammerger.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.phong.dashcammerger.R
import com.phong.dashcammerger.core.Progress

/**
 * AR-13: keeps a scan or merge running with the screen off or the app in the background.
 *
 * The work itself stays on [MergeSession]'s thread; this service only makes the process a
 * foreground process (so Android does not freeze or kill it), holds a partial wake lock (so
 * the CPU does not sleep), and shows progress in a notification with a Cancel button.
 * It stops itself as soon as the session is no longer busy.
 */
class MergeService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var foreground = false

    private val tick = object : Runnable {
        override fun run() {
            val session = MergeSession
            if (!session.busy) {
                finish(session)
                return
            }
            NotificationManagerCompat.from(this@MergeService).let {
                if (it.areNotificationsEnabled()) {
                    try {
                        it.notify(NOTIF_PROGRESS, progressNotification(session))
                    } catch (_: SecurityException) {
                    }
                }
            }
            handler.postDelayed(this, UPDATE_MS)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            MergeSession.cancel()
            return START_NOT_STICKY
        }
        if (!foreground) {
            createChannel(this)
            ServiceCompat.startForeground(
                this, NOTIF_PROGRESS, progressNotification(MergeSession),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
            )
            foreground = true
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "DashCamMerger:merge").apply {
                setReferenceCounted(false)
                acquire(WAKE_LOCK_MAX_MS)
            }
            handler.post(tick)
        }
        // Not restarted after a kill: the merge thread died with the process.
        return START_NOT_STICKY
    }

    private fun finish(session: MergeSession) {
        handler.removeCallbacks(tick)
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        foreground = false
        // A merge that ran unattended ends with a "done" notification the user can tap.
        if (session.phase == Phase.Done || session.phase == Phase.Error) {
            val nm = NotificationManagerCompat.from(this)
            if (nm.areNotificationsEnabled()) {
                try {
                    nm.notify(NOTIF_DONE, doneNotification(session))
                } catch (_: SecurityException) {
                }
            }
        }
        stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        releaseWakeLock()
        super.onDestroy()
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun progressNotification(session: MergeSession): Notification {
        val merging = session.phase == Phase.Merging
        val pct = session.snapshot?.takeIf { merging }?.percent
            ?: (session.progress * 100).toInt().coerceIn(0, 100)
        val cancel = PendingIntent.getService(
            this, 1,
            Intent(this, MergeService::class.java).setAction(ACTION_CANCEL),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // AR-14.7: percent, time left and finish clock time from the live snapshot.
        val now = System.currentTimeMillis()
        val snap = session.snapshot.takeIf { merging }
        val title = when {
            snap != null -> Progress.notificationTitle(snap, now)
            merging -> "Merging dashcam clips – $pct %"
            else -> "Scanning dashcam clips"
        }
        val text = snap?.let { Progress.notificationText(it, now) } ?: session.status
        val big = if (snap != null) "$text\n${session.status}" else session.status
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_merge)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(big))
            .setProgress(100, pct, !merging)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openAppIntent())
            .apply { if (merging) addAction(0, "Cancel", cancel) }
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun doneNotification(session: MergeSession): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_merge)
            .setContentTitle("DashCam Merger")
            .setContentText(session.status)
            .setStyle(NotificationCompat.BigTextStyle().bigText(session.status))
            .setAutoCancel(true)
            .setContentIntent(openAppIntent())
            .build()

    companion object {
        private const val CHANNEL_ID = "merge"
        private const val NOTIF_PROGRESS = 1
        private const val NOTIF_DONE = 2
        private const val ACTION_CANCEL = "com.phong.dashcammerger.CANCEL"
        private const val UPDATE_MS = 1000L
        /** Safety net: the lock is released when the session ends; this only caps a hang. */
        private const val WAKE_LOCK_MAX_MS = 6 * 60 * 60 * 1000L

        private fun createChannel(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Merge progress", NotificationManager.IMPORTANCE_LOW)
            )
        }

        /** Called when a scan or merge starts; the service stops itself when it ends. */
        fun start(ctx: Context) {
            NotificationManagerCompat.from(ctx).cancel(NOTIF_DONE)
            ContextCompat.startForegroundService(ctx, Intent(ctx, MergeService::class.java))
        }
    }
}
