package org.openmarketos.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException

class MainActivity : ComponentActivity() {
    private lateinit var store: Store
    private lateinit var pairView: View
    private lateinit var webBox: View
    private lateinit var web: WebView
    private lateinit var errorBox: View
    private lateinit var errorText: TextView
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private var pairing: Store.Pairing? = null
    private var urlIndex = 0

    private val scan = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        val link = PairLink.parse(text)
        if (link == null) say(getString(R.string.not_a_pairing_code)) else startPairing(link)
    }

    private val notifyPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        setAlerts(granted)
        if (!granted) say(getString(R.string.alerts_need_permission))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        store = Store(this)
        pairView = findViewById(R.id.pair_view)
        webBox = findViewById(R.id.web_box)
        web = findViewById(R.id.web)
        errorBox = findViewById(R.id.error_box)
        errorText = findViewById(R.id.error_text)
        status = findViewById(R.id.status)
        progress = findViewById(R.id.progress)
        setupWebView()

        findViewById<Button>(R.id.scan).setOnClickListener {
            scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setBeepEnabled(false)
                .setOrientationLocked(false).setPrompt(getString(R.string.scan_prompt)))
        }
        findViewById<Button>(R.id.connect).setOnClickListener { manualConnect() }
        findViewById<Button>(R.id.retry).setOnClickListener { errorBox.visibility = View.GONE; loadApp() }
        findViewById<Button>(R.id.pair_again).setOnClickListener { forgetAndPair() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webBox.visibility == View.VISIBLE && web.canGoBack()) web.goBack() else finish()
            }
        })

        val link = intent?.dataString?.let { PairLink.parse(it) }
        pairing = store.load()
        when {
            link != null -> { showPair(); startPairing(link) }
            pairing != null -> showWeb()
            else -> showPair()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { PairLink.parse(it) }?.let { showPair(); startPairing(it) }
    }

    // ------------------------------------------------------------------------------------------ pairing
    private fun showPair() {
        pairView.visibility = View.VISIBLE
        webBox.visibility = View.GONE
    }

    private fun say(text: String, busy: Boolean = false) {
        status.text = text
        progress.visibility = if (busy) View.VISIBLE else View.GONE
    }

    private fun manualConnect() {
        val url = PairLink.normalize(findViewById<EditText>(R.id.address).text.toString())
        val code = findViewById<EditText>(R.id.code).text.toString().trim().uppercase()
        if (url == null) { say(getString(R.string.bad_address)); return }
        if (!Regex("^[A-Z0-9]{6,16}$").matches(code)) { say(getString(R.string.bad_code)); return }
        say(getString(R.string.checking_certificate), busy = true)
        Thread {
            try {
                val u = Uri.parse(url)
                val fp = Pinning.fetchFingerprint(u.host!!.trim('[', ']'), u.port)
                runOnUiThread {
                    say("")
                    AlertDialog.Builder(this)
                        .setTitle(R.string.certificate_check)
                        .setMessage(getString(R.string.certificate_question, Pinning.short(fp)))
                        .setPositiveButton(R.string.same) { _, _ -> startPairing(PairLink(listOf(url), code, fp)) }
                        .setNegativeButton(R.string.different) { _, _ -> say(getString(R.string.certificate_mismatch)) }
                        .show()
                }
            } catch (e: IOException) {
                runOnUiThread { say(getString(R.string.cannot_reach, url)) }
            }
        }.start()
    }

    private fun startPairing(link: PairLink) {
        val fp = link.fingerprint
        if (fp == null) { say(getString(R.string.no_certificate_in_code)); return }
        say(getString(R.string.connecting), busy = true)
        Thread {
            val client = Pinning.client(fp)
            val reachable = link.urls.firstOrNull { url ->
                try {
                    client.newCall(Request.Builder().url("$url/api/health").build()).execute().use { it.isSuccessful }
                } catch (e: IOException) {
                    false
                }
            }
            if (reachable == null) {
                runOnUiThread { say(getString(R.string.cannot_reach_any)) }
                return@Thread
            }
            val body = JSONObject().put("code", link.code).put("device_name", "Android — ${Build.MANUFACTURER} ${Build.MODEL}")
                .toString().toRequestBody("application/json".toMediaType())
            try {
                client.newCall(Request.Builder().url("$reachable/api/auth/exchange").post(body).build()).execute().use { r ->
                    val json = JSONObject(r.body?.string() ?: "{}")
                    if (!r.isSuccessful) {
                        runOnUiThread { say(getString(R.string.pairing_refused, json.optString("message", "HTTP ${r.code}"))) }
                        return@Thread
                    }
                    val urls = listOf(reachable) + link.urls.filter { it != reachable }
                    val p = Store.Pairing(urls, fp, json.getString("token"), handedOver = false)
                    store.save(p)
                    runOnUiThread { pairing = p; urlIndex = 0; say(""); showWeb() }
                }
            } catch (e: Exception) {
                runOnUiThread { say(getString(R.string.pairing_failed, e.message ?: e.toString())) }
            }
        }.start()
    }

    private fun forgetAndPair() {
        setAlerts(false)
        store.clear()
        pairing = null
        web.clearHistory()
        web.loadUrl("about:blank")
        android.webkit.CookieManager.getInstance().removeAllCookies(null)
        android.webkit.WebStorage.getInstance().deleteAllData()
        errorBox.visibility = View.GONE
        say("")
        showPair()
    }

    // ------------------------------------------------------------------------------------------ app view
    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            javaScriptCanOpenWindowsAutomatically = false
            userAgentString = "$userAgentString OpenMarketOSApp/${BuildConfig.VERSION_NAME}"
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) safeBrowsingEnabled = true
        }
        web.webViewClient = object : WebViewClient() {
            @SuppressLint("WebViewClientOnReceivedSslError")  // proceeds only for the pinned certificate
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                // the computer's own certificate is expected to be "untrusted": accept exactly the paired one
                val fp = pairing?.fingerprint
                if (fp != null && Pinning.matches(der(error.certificate), fp)) handler.proceed() else {
                    handler.cancel()
                    showError(getString(R.string.certificate_changed))
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.scheme == "omos-app") { appMenu(); return true }
                val origin = pairing?.urls?.getOrNull(urlIndex)?.let { Uri.parse(it) }
                if (origin != null && uri.scheme == "https" && uri.host == origin.host && uri.port == origin.port) return false
                if (uri.scheme == "http" || uri.scheme == "https") {
                    try { startActivity(Intent(Intent.ACTION_VIEW, uri)) } catch (e: Exception) { /* no browser */ }
                }
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                val p = pairing ?: return
                if (!p.handedOver && url.startsWith(p.urls[urlIndex])) {
                    val done = p.copy(handedOver = true)  // the web app now keeps the sign-in itself
                    store.save(done)
                    pairing = done
                }
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                val p = pairing ?: return
                if (urlIndex + 1 < p.urls.size) { urlIndex += 1; loadApp(); return }  // try the computer's other address
                urlIndex = 0
                showError(getString(R.string.computer_unreachable))
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                recreate()
                return true
            }
        }
    }

    private fun der(cert: SslCertificate): ByteArray? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) cert.x509Certificate?.encoded
        else SslCertificate.saveState(cert).getByteArray("x509-certificate")

    private fun showWeb() {
        pairView.visibility = View.GONE
        webBox.visibility = View.VISIBLE
        loadApp()
        if (store.alerts) startAlerts()
    }

    private fun loadApp() {
        val p = pairing ?: return showPair()
        val base = p.urls[urlIndex.coerceIn(0, p.urls.size - 1)]
        // first open after pairing: hand the sign-in token to the web app (it removes it from the address)
        web.loadUrl(if (p.handedOver) "$base/" else "$base/#token=${p.token}")
    }

    private fun showError(text: String) {
        errorText.text = text
        errorBox.visibility = View.VISIBLE
    }

    // ------------------------------------------------------------------------------------------ menu
    private fun appMenu() {
        val alertsLabel = getString(if (store.alerts) R.string.alerts_turn_off else R.string.alerts_turn_on)
        val items = arrayOf(alertsLabel, getString(R.string.reload), getString(R.string.pair_again),
            getString(R.string.about_item))
        AlertDialog.Builder(this).setTitle(R.string.app_name).setItems(items) { _, which ->
            when (which) {
                0 -> if (store.alerts) setAlerts(false) else askAlerts()
                1 -> web.reload()
                2 -> AlertDialog.Builder(this).setMessage(R.string.pair_again_question)
                    .setPositiveButton(R.string.pair_again) { _, _ -> forgetAndPair() }
                    .setNegativeButton(android.R.string.cancel, null).show()
                3 -> AlertDialog.Builder(this).setTitle(R.string.app_name)
                    .setMessage(getString(R.string.about, BuildConfig.VERSION_NAME,
                        pairing?.let { Pinning.short(it.fingerprint) } ?: "—", pairing?.urls?.firstOrNull() ?: "—"))
                    .setPositiveButton(android.R.string.ok, null).show()
            }
        }.show()
    }

    private fun askAlerts() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else setAlerts(true)
    }

    private fun setAlerts(on: Boolean) {
        store.alerts = on
        if (on) startAlerts() else stopService(Intent(this, AlertService::class.java))
    }

    private fun startAlerts() = ContextCompat.startForegroundService(this, Intent(this, AlertService::class.java))
}
