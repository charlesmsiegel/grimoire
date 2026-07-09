package app.grimoire

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * Keeps the app process — and with it the embedded server — alive a little
 * longer than the activity. Phase 3 promotes this to a foreground service
 * while an LLM stream is in flight (docs/android-architecture.md §4); until
 * then it is a plain started service and process death is an accepted,
 * lossless event.
 */
class ServerService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServerRuntime.ensureStarted(this)
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
