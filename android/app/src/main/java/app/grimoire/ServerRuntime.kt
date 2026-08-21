package app.grimoire

import android.content.Context
import android.util.Log
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import kotlin.concurrent.thread

/** Kotlin-side view of the embedded server: Python receives this and calls
 * onPort once the socket is bound and listening. */
fun interface PortCallback {
    fun onPort(port: Int)
}

/**
 * What the backend tells the shell about detached runs.
 *
 * `onRunsChanged` crosses zero, not once per run: the service promotes itself
 * to the foreground on the first live run and demotes on the last. Promotion
 * is what stops Android reclaiming this process mid-generation, which is the
 * whole reason a locked phone can now finish a turn.
 *
 * `onRunTerminal` carries what a notification needs to be worth tapping --
 * which campaign, which scene, and which CLASS of work landed (a `turn` is a
 * reply, a `review` is an end-of-scene form) -- plus the scene IDENTITY rather than its id,
 * because an id goes stale on rename and a notification can sit unread for a
 * long time. The tap resolves it through `GET /scene-by-identity`.
 */
interface RunCallback {
    fun onRunsChanged(live: Int)
    fun onRunTerminal(
        runId: String,
        state: String,
        runClass: String,
        campaignName: String,
        sceneTitle: String,
        cid: String,
        sceneIdentity: String,
    )
}

/**
 * Process-wide owner of the embedded Python server.
 *
 * The server thread starts once per process and blocks in uvicorn for the
 * process's lifetime; Android reclaiming the process is the shutdown path
 * (lossless — the store is file-per-record, see docs/android-architecture.md §2).
 */
object ServerRuntime {
    private const val TAG = "GrimoireServer"

    @Volatile
    var port: Int = -1
        private set

    @Volatile
    var failure: String? = null
        private set

    /** Set by the service before the server starts, so the callbacks below
     *  have somewhere to go. Null on any host without a service. */
    @Volatile
    var runs: RunCallback? = null

    /**
     * Whether the activity is resumed -- set by `MainActivity`'s lifecycle.
     *
     * Read by `RunNotifier` to skip the COMPLETION notification while the
     * player is already watching. Not the ongoing one: that is what holds the
     * process, and it has to be posted whether or not anyone is looking.
     *
     * On the runtime rather than the activity because the activity is exactly
     * what may not exist, and a null check on an activity reference cannot tell
     * "gone" from "backgrounded".
     */
    @Volatile
    var foreground: Boolean = false

    private val portListeners = mutableListOf<(Int) -> Unit>()
    private val failureListeners = mutableListOf<(String) -> Unit>()
    private var started = false

    @Synchronized
    fun ensureStarted(context: Context) {
        if (started) return
        started = true
        val app = context.applicationContext
        thread(name = "grimoire-server") { bootstrap(app) }
    }

    /** Runs [onReady] with the port — immediately if the server is already up,
     * otherwise from the server thread once it is. [onFailure] mirrors that
     * for a bootstrap error. Callers marshal to the main thread themselves. */
    @Synchronized
    fun subscribe(onReady: (Int) -> Unit, onFailure: (String) -> Unit) {
        val p = port
        val f = failure
        when {
            p > 0 -> onReady(p)
            f != null -> onFailure(f)
            else -> {
                portListeners.add(onReady)
                failureListeners.add(onFailure)
            }
        }
    }

    @Synchronized
    private fun publishPort(p: Int) {
        port = p
        portListeners.forEach { it(p) }
        portListeners.clear()
        failureListeners.clear()
    }

    @Synchronized
    private fun publishFailure(message: String) {
        failure = message
        failureListeners.forEach { it(message) }
        portListeners.clear()
        failureListeners.clear()
    }

    private fun bootstrap(app: Context) {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(app))
            }
            val dirs = AssetExtractor.ensureExtracted(app)
            // HOME for the Python process: external app dir when available so
            // the store (~/.grimoire) is visible over USB; store/paths.py's
            // pointer mechanism (and with it the Storage-location settings
            // page) keeps working because we set HOME, not GRIMOIRE_HOME.
            val home = app.getExternalFilesDir(null) ?: app.filesDir
            Log.i(TAG, "starting server (home=$home)")
            Python.getInstance().getModule("android_entry").callAttr(
                "start_server",
                home.absolutePath,
                dirs.frontend.absolutePath,
                dirs.templates.absolutePath,
                PortCallback { p ->
                    Log.i(TAG, "listening on 127.0.0.1:$p")
                    publishPort(p)
                },
                // Forwarded into `app.state`, where the run registry and the
                // runner can reach them. Passing null here -- no service, as in
                // a bare test host -- leaves the backend exactly as it is on
                // desktop, which is the correct degraded mode rather than a
                // failure.
                object : RunCallback {
                    override fun onRunsChanged(live: Int) {
                        runCatching { runs?.onRunsChanged(live) }
                            .onFailure { Log.w(TAG, "onRunsChanged failed", it) }
                    }

                    override fun onRunTerminal(
                        runId: String,
                        state: String,
                        runClass: String,
                        campaignName: String,
                        sceneTitle: String,
                        cid: String,
                        sceneIdentity: String,
                    ) {
                        runCatching {
                            runs?.onRunTerminal(
                                runId, state, runClass, campaignName, sceneTitle, cid,
                                sceneIdentity,
                            )
                        }.onFailure { Log.w(TAG, "onRunTerminal failed", it) }
                    }
                },
            )
            // start_server blocks forever; returning means uvicorn exited
            publishFailure("server exited unexpectedly")
        } catch (e: PyException) {
            Log.e(TAG, "python bootstrap failed", e)
            publishFailure(e.message ?: "python bootstrap failed")
        } catch (e: Exception) {
            Log.e(TAG, "server bootstrap failed", e)
            publishFailure(e.message ?: "server bootstrap failed")
        }
    }
}

/** Extracts the APK's `grimoire/` assets (frontend dist + prompt templates) to
 * app storage, once per install/update — StaticFiles and Jinja need real
 * filesystem paths, not AssetManager streams. */
object AssetExtractor {
    data class Dirs(val frontend: File, val templates: File)

    fun ensureExtracted(context: Context): Dirs {
        val root = File(context.filesDir, "web")
        val marker = File(root, ".extracted")
        val stamp = context.packageManager
            .getPackageInfo(context.packageName, 0).lastUpdateTime.toString()
        if (!marker.exists() || marker.readText() != stamp) {
            root.deleteRecursively()
            copyAssetDir(context, "grimoire", root)
            marker.writeText(stamp)
        }
        return Dirs(File(root, "frontend"), File(root, "templates"))
    }

    private fun copyAssetDir(context: Context, path: String, dest: File) {
        val children = context.assets.list(path) ?: return
        if (children.isEmpty()) {
            // a leaf: an asset file (empty asset dirs don't survive packaging)
            dest.parentFile?.mkdirs()
            context.assets.open(path).use { input ->
                dest.outputStream().use { input.copyTo(it) }
            }
        } else {
            dest.mkdirs()
            for (child in children) {
                copyAssetDir(context, "$path/$child", File(dest, child))
            }
        }
    }
}
