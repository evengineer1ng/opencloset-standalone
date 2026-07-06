package com.openclaw.openclosetmobile

import android.app.Application
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.openclaw.openclosetmobile.logging.MobileLog
import com.openclaw.openclosetmobile.sync.MobileSyncWorker
import java.util.concurrent.TimeUnit

class OpenClosetMobileApp : Application() {
    override fun onCreate() {
        super.onCreate()
        MobileLog.init(this)
        MobileLog.installUncaughtExceptionHandler()
        MobileLog.i(MobileLog.TAG_STARTUP, "startup.app.onCreate.begin")
        MobileLog.logDeviceInfo()

        MobileLog.i(MobileLog.TAG_STARTUP, "startup.workmanager.begin")
        runCatching {
            val request = PeriodicWorkRequestBuilder<MobileSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build(),
                )
                .build()

            WorkManager.getInstance(this).enqueueUniquePeriodicWork(
                "opencloset_mobile_sync",
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }.onSuccess {
            MobileLog.i(MobileLog.TAG_STARTUP, "startup.workmanager.ok")
        }.onFailure { error ->
            MobileLog.e(MobileLog.TAG_STARTUP, "startup.workmanager.failed", throwable = error)
        }

        MobileLog.i(MobileLog.TAG_STARTUP, "startup.app.onCreate.end")
    }
}
