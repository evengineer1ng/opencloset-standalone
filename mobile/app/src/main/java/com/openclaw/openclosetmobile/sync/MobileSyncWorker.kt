package com.openclaw.openclosetmobile.sync

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.openclaw.openclosetmobile.MainActivity
import com.openclaw.openclosetmobile.R
import com.openclaw.openclosetmobile.data.MobileRepository
import com.openclaw.openclosetmobile.data.SettingsStore
import kotlinx.coroutines.flow.first

class MobileSyncWorker(
    appContext: Context,
    workerParams: WorkerParameters,
) : CoroutineWorker(appContext, workerParams) {

    override suspend fun doWork(): Result {
        val settingsStore = SettingsStore(applicationContext)
        val repository = MobileRepository(applicationContext)
        val settings = settingsStore.settings.first()
        if (!settings.isConfigured) {
            return Result.success()
        }

        return runCatching {
            val bootstrap = repository.refreshBootstrap(settings)
            settingsStore.updateCachedBootstrap(repository.encodeBootstrap(bootstrap))

            val readyDeliveries = bootstrap.deliveries_by_workspace.values
                .flatten()
                .filter { it.status == "ready" }
                .map { it.id }
                .toSet()
            val newReadyDeliveries = readyDeliveries - settings.lastNotifiedDeliveryIds
            if (newReadyDeliveries.isNotEmpty()) {
                postDeliveryNotification(newReadyDeliveries.size)
            }
            settingsStore.updateLastNotifiedDeliveryIds(readyDeliveries)
            Result.success()
        }.getOrElse {
            Result.retry()
        }
    }

    private fun postDeliveryNotification(newCount: Int) {
        ensureNotificationChannel()
        if (!canPostNotifications()) {
            return
        }
        val intent = Intent(applicationContext, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            applicationContext,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle("OpenCloset delivery ready")
            .setContentText("$newCount new device delivery item(s) are ready.")
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        notifyIfPermitted(notification)
    }

    private fun canPostNotifications(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return true
        }
        return ContextCompat.checkSelfPermission(
            applicationContext,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    @SuppressLint("MissingPermission")
    private fun notifyIfPermitted(notification: android.app.Notification) {
        if (!canPostNotifications()) {
            return
        }
        runCatching {
            NotificationManagerCompat.from(applicationContext).notify(NOTIFICATION_ID, notification)
        }
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            "OpenCloset Mobile",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Background sync and delivery notifications"
        }
        manager.createNotificationChannel(channel)
    }

    companion object {
        const val CHANNEL_ID = "opencloset_mobile_sync"
        const val NOTIFICATION_ID = 7010
    }
}