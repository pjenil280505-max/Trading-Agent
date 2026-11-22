package org.openmarketos.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import kotlin.math.min
import kotlin.random.Random

/**
 * "Alerts on this phone": keeps one encrypted connection to the paired computer and turns its
 * notifications (alerts, bots, risk limits, order proposals) into Android notifications.
 * Reconnects with back-off, and immediately when the network comes back.
 */
class AlertService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var connected = false
    private var stopped = false
    private var attempt = 0
    private var urlIndex = 0
    private var netCallback: ConnectivityManager.NetworkCallback? = null
    private var nextId = 100

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        channels()
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, ONGOING_ID, ongoing(getString(R.string.alerts_connecting)), type)
        val cm = getSystemService(ConnectivityManager::class.java)
        netCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                main.post { if (!connected && !stopped) { attempt = 0; main.removeCallbacksAndMessages(null); connect() } }
            }
        }.also { cm.registerDefaultNetworkCallback(it) }
        connect()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        stopped = true
        main.removeCallbacksAndMessages(null)
        socket?.close(1000, "alerts turned off")
        netCallback?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        super.onDestroy()
    }

    private fun connect() {
        val p = Store(this).load()
        if (p == null || !Store(this).alerts) { stopSelf(); return }
        val base = p.urls[urlIndex % p.urls.size]
        val ws = base.replaceFirst("https://", "wss://") + "/api/ws"
        socket = Pinning.client(p.fingerprint, readTimeoutSeconds = 0).newWebSocket(Request.Builder().url(ws).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(JSONObject().put("type", "auth").put("token", p.token).toString())
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val evt = try { JSONObject(text) } catch (e: Exception) { return }
                    val data = evt.optJSONObject("data") ?: JSONObject()
                    when (evt.optString("topic")) {
                        "hello" -> main.post { connected = true; attempt = 0; update(getString(R.string.alerts_connected)) }
                        "notification" -> notifyUser(data.optString("title"), data.optString("body"),
                            urgent = data.optString("level") in setOf("warning", "error", "critical"))
                        "risk" -> if (data.has("breach")) notifyUser(getString(R.string.risk_limit), data.optString("breach"), true)
                        "proposal" -> notifyUser(getString(R.string.proposal_title), getString(R.string.proposal_body), true)
                        "error" -> main.post { signedOut() }
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    main.post { if (code == 4401) signedOut() else retry() }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    main.post { retry() }
                }
            })
    }

    private fun retry() {
        connected = false
        if (stopped) return
        attempt += 1
        val p = Store(this).load()
        if (p != null && p.urls.size > 1) urlIndex += 1  // the computer may be on another of its addresses
        val delayMs = min(60_000L, 2_000L shl min(attempt, 5)) + Random.nextLong(0, 1_000)
        update(getString(R.string.alerts_reconnecting))
        main.postDelayed({ if (!stopped) connect() }, delayMs)
    }

    private fun signedOut() {
        notifyUser(getString(R.string.signed_out_title), getString(R.string.signed_out_body), true)
        Store(this).alerts = false
        stopSelf()
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(this, 0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun channels() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_STATUS, getString(R.string.channel_status), NotificationManager.IMPORTANCE_MIN))
        nm.createNotificationChannel(NotificationChannel(CH_ALERTS, getString(R.string.channel_alerts), NotificationManager.IMPORTANCE_HIGH))
    }

    private fun ongoing(text: String): Notification = NotificationCompat.Builder(this, CH_STATUS)
        .setSmallIcon(R.drawable.ic_notification).setContentTitle(getString(R.string.app_name)).setContentText(text)
        .setOngoing(true).setContentIntent(openApp()).setPriority(NotificationCompat.PRIORITY_MIN).build()

    private fun update(text: String) {
        if (!canNotify()) return
        try {
            NotificationManagerCompat.from(this).notify(ONGOING_ID, ongoing(text))
        } catch (e: SecurityException) {
            // notification permission was withdrawn; the service keeps running without a visible status
        }
    }

    private fun canNotify(): Boolean = NotificationManagerCompat.from(this).areNotificationsEnabled()

    private fun notifyUser(title: String, body: String, urgent: Boolean) = main.post {
        if (!canNotify() || title.isBlank()) return@post
        val n = NotificationCompat.Builder(this, CH_ALERTS).setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title).setContentText(body).setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true).setContentIntent(openApp())
            .setPriority(if (urgent) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT).build()
        try {
            NotificationManagerCompat.from(this).notify(nextId++, n)
        } catch (e: SecurityException) {
            // notification permission was withdrawn
        }
    }

    companion object {
        private const val ONGOING_ID = 1
        private const val CH_STATUS = "connection"
        private const val CH_ALERTS = "alerts"
    }
}

/** Turns alerts back on after the phone restarts, if the user had them on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val store = Store(context)
        if (store.alerts && store.load() != null) {
            try {
                androidx.core.content.ContextCompat.startForegroundService(context, Intent(context, AlertService::class.java))
            } catch (e: Exception) {
                // the system may refuse background starts; alerts resume when the app is next opened
            }
        }
    }
}
