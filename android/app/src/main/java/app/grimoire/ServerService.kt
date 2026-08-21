package app.grimoire

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log

/**
 * Keeps the app process — and with it the embedded server — alive.
 *
 * Promoted to a FOREGROUND service while any run is live, and demoted when
 * none is. That promotion is what makes a detached turn actually survive a
 * locked phone: the backend can buffer frames and outlive a socket all it
 * likes, but if Android reclaims this process mid-generation there is nothing
 * left to buffer into. Without it the whole feature works on desktop and
 * quietly does not on the device it was built for.
 *
 * The service listens for run transitions rather than being told by the
 * activity, because the activity is exactly what is gone in the case that
 * matters.
 */
class ServerService : Service() {
    private var promoted = false

    override fun onCreate() {
        super.onCreate()
        // BEFORE the server can produce a run. Posting to an unregistered
        // channel makes the foreground notification invalid, and a refused
        // promotion is a reclaimable process.
        RunNotifier.ensureChannels(this)
        ServerRuntime.runs = object : RunCallback {
            override fun onRunsChanged(live: Int) {
                if (live > 0) promote(live) else demote()
            }

            override fun onRunTerminal(
                runId: String,
                state: String,
                campaignName: String,
                sceneTitle: String,
                cid: String,
                sceneIdentity: String,
            ) {
                RunNotifier.postTerminal(
                    this@ServerService, runId, state, campaignName, sceneTitle,
                    cid, sceneIdentity,
                )
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServerRuntime.ensureStarted(this)
        return START_STICKY
    }

    /**
     * `dataSync`, which is the honest type. `shortService` caps at three
     * minutes; a turn can exceed that and an absorb routinely does, and the cap
     * is enforced by killing the service — which is the failure this exists to
     * prevent, arrived at through the type declaration.
     */
    private fun promote(live: Int) {
        val notification = RunNotifier.ongoing(this, live)
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    RunNotifier.ONGOING_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
                )
            } else {
                startForeground(RunNotifier.ONGOING_ID, notification)
            }
            promoted = true
        }.onFailure {
            // A refused promotion is a degraded install, not a broken turn: the
            // run carries on and the reply still reaches the store. Swallowed
            // here as well as at the Python boundary, because throwing would
            // reach a task group that would cancel every sibling run.
            Log.w("GrimoireRuns", "foreground promotion refused", it)
        }
    }

    private fun demote() {
        if (!promoted) return
        promoted = false
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE)
            } else {
                @Suppress("DEPRECATION")
                stopForeground(true)
            }
        }.onFailure { Log.w("GrimoireRuns", "demotion failed", it) }
    }

    override fun onDestroy() {
        ServerRuntime.runs = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
