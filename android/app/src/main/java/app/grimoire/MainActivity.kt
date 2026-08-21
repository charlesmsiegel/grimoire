package app.grimoire

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.Manifest
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.view.Gravity
import android.view.View
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts

/** Full-screen WebView over the embedded grimoire server. All product UI is
 * the shared React frontend; this activity only hosts it. */
class MainActivity : ComponentActivity() {

    private lateinit var web: WebView
    private lateinit var loading: View
    private lateinit var status: TextView

    // <input type="file"> support (character card import, image upload)
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private val fileChooser =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            filePathCallback?.onReceiveValue(
                WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
            )
            filePathCallback = null
        }

    /** Asked once, on first launch.
     *
     *  Declaring `POST_NOTIFICATIONS` in the manifest is not enough on 13+ at
     *  `targetSdk 34`: it is denied on a fresh install until the activity asks.
     *  Without this, completion notifications are permanently off for every new
     *  user and "degraded mode" stops being a choice and becomes the default.
     *  The result is deliberately ignored -- a refusal is a real answer, and the
     *  reply is on disk either way. */
    private val askNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    /** The scene a completion notification was tapped for, as
     *  `grimoire://scene/<cid>/<identity>`.
     *
     *  Identity, not id: an id goes stale the moment a scene is renamed and a
     *  notification can sit unread for a long time, so the page resolves it
     *  through `GET /scene-by-identity` and opens whatever the scene is called
     *  now rather than a route that 404s. */
    private fun sceneFrom(intent: Intent?): Pair<String, String>? {
        val data = intent?.data ?: return null
        if (data.scheme != "grimoire" || data.host != "scene") return null
        val parts = data.pathSegments ?: return null
        return if (parts.size >= 2) parts[0] to parts[1] else null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        web = WebView(this).apply { visibility = View.GONE }
        status = TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 16f
            gravity = Gravity.CENTER
            setPadding(48, 24, 48, 0)
        }
        loading = FrameLayout(this).also { box ->
            box.addView(
                ProgressBar(this),
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    Gravity.CENTER,
                ),
            )
            box.addView(
                status,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    Gravity.CENTER or Gravity.BOTTOM,
                ).apply { bottomMargin = 160 },
            )
        }
        setContentView(FrameLayout(this).also {
            it.addView(web)
            it.addView(loading)
        })

        configureWebView()

        // System back walks SPA history; at the root it leaves the app.
        onBackPressedDispatcher.addCallback(this) {
            if (web.canGoBack()) web.goBack() else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        startService(Intent(this, ServerService::class.java))
        ServerRuntime.ensureStarted(this)
        val opening = sceneFrom(intent)
        ServerRuntime.subscribe(
            onReady = { port ->
                runOnUiThread {
                    if (web.url == null) {
                        val base = "http://127.0.0.1:$port/"
                        // The SPA resolves the identity itself; handing it the
                        // stale id here would just move the 404 one layer up.
                        web.loadUrl(
                            if (opening == null) base
                            else base + "open?campaign=${opening.first}" +
                                "&identity=${opening.second}",
                        )
                    }
                }
            },
            onFailure = { message ->
                runOnUiThread { status.text = message }
            },
        )
    }

    private fun configureWebView() {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
        }

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                // keep the app on the loopback origin; anything else (external
                // links in lore/chub pages) goes to the system browser
                if (request.url.host in setOf("127.0.0.1", "localhost")) return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url)) }
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                loading.visibility = View.GONE
                web.visibility = View.VISIBLE
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                return try {
                    fileChooser.launch(params.createIntent())
                    true
                } catch (e: ActivityNotFoundException) {
                    filePathCallback = null
                    false
                }
            }
        }

        // character/version exports arrive as downloads from the loopback
        // server; hand them to DownloadManager (loopback is device-wide, so
        // its process can reach our port)
        web.setDownloadListener { url, _, contentDisposition, mimeType, _ ->
            val request = DownloadManager.Request(Uri.parse(url))
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS,
                    URLUtil.guessFileName(url, contentDisposition, mimeType),
                )
            (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
        }
    }
}
