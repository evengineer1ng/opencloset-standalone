package com.openclaw.openclosetmobile.logging

import android.content.Context
import android.os.Build
import android.os.Process
import android.util.Log
import com.openclaw.openclosetmobile.BuildConfig
import java.io.File
import java.io.IOException
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Cross-startup logging utility. Writes to Android Logcat and to a rolling file
 * under filesDir/logs/opencloset-mobile.log so a crash before the first frame
 * still leaves a forensic trail that the user can export from Settings.
 */
object MobileLog {
    const val TAG_APP = "OC_APP"
    const val TAG_STARTUP = "OC_STARTUP"
    const val TAG_SYNC = "OC_SYNC"
    const val TAG_NET = "OC_NET"
    const val TAG_UI = "OC_UI"

    private const val LOG_DIR = "logs"
    private const val LOG_FILE = "opencloset-mobile.log"
    private const val MAX_FILE_BYTES = 512L * 1024L
    private const val KEEP_BYTES_AFTER_TRUNCATE = 256L * 1024L

    private val timestampFormat: ThreadLocal<SimpleDateFormat> = object : ThreadLocal<SimpleDateFormat>() {
        override fun initialValue(): SimpleDateFormat =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.US)
    }

    @Volatile
    private var logFile: File? = null
    private val fileLock = Any()

    fun init(context: Context) {
        synchronized(fileLock) {
            if (logFile != null) {
                return
            }
            val dir = File(context.filesDir, LOG_DIR)
            if (!dir.exists()) {
                dir.mkdirs()
            }
            logFile = File(dir, LOG_FILE)
        }
    }

    fun d(tag: String, event: String, message: String = "", throwable: Throwable? = null) {
        write(Log.DEBUG, tag, event, message, throwable)
    }

    fun i(tag: String, event: String, message: String = "", throwable: Throwable? = null) {
        write(Log.INFO, tag, event, message, throwable)
    }

    fun w(tag: String, event: String, message: String = "", throwable: Throwable? = null) {
        write(Log.WARN, tag, event, message, throwable)
    }

    fun e(tag: String, event: String, message: String = "", throwable: Throwable? = null) {
        write(Log.ERROR, tag, event, message, throwable)
    }

    fun logDeviceInfo() {
        i(
            TAG_STARTUP,
            "device.info",
            "manufacturer=${Build.MANUFACTURER} model=${Build.MODEL} sdk=${Build.VERSION.SDK_INT} " +
                "version=${BuildConfig.VERSION_NAME} code=${BuildConfig.VERSION_CODE}",
        )
    }

    fun readRecentLines(maxLines: Int = 200): String {
        val file = logFile ?: return ""
        return try {
            if (!file.exists()) {
                ""
            } else {
                val lines = file.readLines()
                if (lines.size <= maxLines) lines.joinToString("\n")
                else lines.subList(lines.size - maxLines, lines.size).joinToString("\n")
            }
        } catch (e: IOException) {
            "(failed to read log: ${e.message})"
        }
    }

    fun clear() {
        val file = logFile ?: return
        synchronized(fileLock) {
            runCatching { if (file.exists()) file.writeText("") }
        }
    }

    fun installUncaughtExceptionHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                e(TAG_APP, "uncaught.exception", "thread=${thread.name}", throwable)
            } catch (_: Throwable) {
            }
            previous?.uncaughtException(thread, throwable)
        }
    }

    private fun write(level: Int, tag: String, event: String, message: String, throwable: Throwable?) {
        val ts = timestampFormat.get()!!.format(Date())
        val pid = Process.myPid()
        val threadName = Thread.currentThread().name
        val levelChar = levelChar(level)
        val stack = throwable?.let { stackToString(it) }.orEmpty()
        val rendered = buildString {
            append(ts)
            append(' ')
            append(levelChar)
            append('/')
            append(tag)
            append(" pid=").append(pid)
            append(" t=").append(threadName)
            append(" event=").append(event)
            if (message.isNotEmpty()) {
                append(" msg=").append(message)
            }
            if (stack.isNotEmpty()) {
                append('\n').append(stack)
            }
        }

        when (level) {
            Log.DEBUG -> Log.d(tag, "$event $message", throwable)
            Log.INFO -> Log.i(tag, "$event $message", throwable)
            Log.WARN -> Log.w(tag, "$event $message", throwable)
            Log.ERROR -> Log.e(tag, "$event $message", throwable)
            else -> Log.v(tag, "$event $message", throwable)
        }

        appendToFile(rendered)
    }

    private fun appendToFile(line: String) {
        val file = logFile ?: return
        synchronized(fileLock) {
            try {
                if (file.length() > MAX_FILE_BYTES) {
                    truncateInPlace(file)
                }
                file.appendText(line)
                file.appendText("\n")
            } catch (_: IOException) {
            } catch (_: SecurityException) {
            }
        }
    }

    private fun truncateInPlace(file: File) {
        try {
            val total = file.length()
            if (total <= KEEP_BYTES_AFTER_TRUNCATE) {
                return
            }
            val skip = total - KEEP_BYTES_AFTER_TRUNCATE
            val tail = file.inputStream().use { input ->
                input.skip(skip)
                input.readBytes()
            }
            // Drop a partial first line, since `skip` may land mid-line.
            val newlineIdx = tail.indexOf('\n'.code.toByte())
            val cleanTail = if (newlineIdx >= 0 && newlineIdx < tail.size - 1) {
                tail.copyOfRange(newlineIdx + 1, tail.size)
            } else tail
            file.writeBytes(cleanTail)
        } catch (_: IOException) {
        }
    }

    private fun stackToString(throwable: Throwable): String {
        val sw = StringWriter()
        PrintWriter(sw).use { throwable.printStackTrace(it) }
        return sw.toString()
    }

    private fun levelChar(level: Int): Char = when (level) {
        Log.DEBUG -> 'D'
        Log.INFO -> 'I'
        Log.WARN -> 'W'
        Log.ERROR -> 'E'
        else -> 'V'
    }
}
