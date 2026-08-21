package app.grimoire

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * The two notifications a detached run needs: the ongoing one that keeps this
 * process alive while a turn generates, and the completion one that tells the
 * player their reply arrived.
 *
 * Channels are registered before anything posts. On Android 8.0+ -- every
 * supported device at `minSdk 26` -- posting to an unregistered channel
 * suppresses a completion notification outright and, worse, makes the
 * FOREGROUND notification invalid: the promotion fails and the process becomes
 * reclaimable again, which defeats the guarantee the whole feature rests on.
 */
object RunNotifier {
    private const val TAG = "GrimoireRuns"

    /** Low importance and silent: it exists to hold the process, not to be
     *  read. A turn is not an event the player needs told about while they are
     *  watching it happen. */
    const val ONGOING_CHANNEL = "grimoire.runs.ongoing"

    /** The one that is worth a sound: a reply landed while they were away. */
    const val DONE_CHANNEL = "grimoire.runs.done"

    const val ONGOING_ID = 1

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                ONGOING_CHANNEL,
                context.getString(R.string.channel_ongoing),
                NotificationManager.IMPORTANCE_LOW,
            ).apply { setShowBadge(false) },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                DONE_CHANNEL,
                context.getString(R.string.channel_done),
                NotificationManager.IMPORTANCE_DEFAULT,
            ),
        )
    }

    /** The notification the foreground promotion is built on. Deliberately
     *  wordless about content: it is visible for the whole of a turn, and a
     *  scene title on the lock screen is more than the player asked to share. */
    fun ongoing(context: Context, live: Int): Notification =
        NotificationCompat.Builder(context, ONGOING_CHANNEL)
            .setContentTitle(context.getString(R.string.run_ongoing_title))
            .setContentText(
                context.resources.getQuantityString(R.plurals.run_ongoing_body, live, live),
            )
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(openIntent(context, null, null))
            .build()

    /**
     * Post "your turn finished", or "your review is ready".
     *
     * The tap carries the scene's IDENTITY, not its id. A notification can sit
     * unread for a long time, and an id goes stale the moment the scene is
     * renamed -- so the activity resolves the identity through
     * `GET /scene-by-identity` and opens whatever the scene is called now,
     * rather than a route that 404s.
     */
    fun postTerminal(
        context: Context,
        runId: String,
        state: String,
        runClass: String,
        campaignName: String,
        sceneTitle: String,
        cid: String,
        sceneIdentity: String,
    ) {
        // A cancelled run is the player getting what they asked for. Telling
        // them about it would be a notification for having pressed Stop.
        if (state == "cancelled") return
        // Neither does a player who is watching it happen. This notification is
        // for a reply that landed while they were away, and posting it at
        // default importance -- with a sound -- for every turn they sit through
        // would make the feature something to turn off.
        if (ServerRuntime.foreground) return
        if (!mayPost(context)) {
            // Denied is a degraded install, not an error: the reply is on disk
            // and the app shows it on next open. Logged so a silent phone is
            // explicable rather than mysterious.
            Log.i(TAG, "notifications not permitted; skipping completion for $runId")
            return
        }
        val landed = state == "landed"
        // What landed decides what this may say. An absorb produces a form to
        // read, not narration, and "New Post" would send the player into the
        // scene looking for a reply that was never generated.
        val title = when {
            landed && runClass == "review" ->
                context.getString(R.string.review_done_title, campaignName, sceneTitle)
            landed -> context.getString(R.string.run_done_title, campaignName, sceneTitle)
            else -> context.getString(R.string.run_failed_title, campaignName, sceneTitle)
        }
        val notification = NotificationCompat.Builder(context, DONE_CHANNEL)
            .setContentTitle(title)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setAutoCancel(true)
            .setContentIntent(openIntent(context, cid, sceneIdentity))
            .build()
        runCatching {
            NotificationManagerCompat.from(context).notify(runId.hashCode(), notification)
        }.onFailure { Log.w(TAG, "posting completion for $runId failed", it) }
    }

    /**
     * Whether a completion notification would actually be delivered.
     *
     * `POST_NOTIFICATIONS` became a RUNTIME permission in Android 13 (API 33).
     * On 8.0 through 12L -- which `minSdk 26` still supports -- the platform
     * does not know it at all, and `checkSelfPermission` answers DENIED for a
     * permission that was never required. Checking it unconditionally therefore
     * switched completion notifications off on every pre-13 device, silently,
     * which is most of the range this app claims to support (codex, P1).
     *
     * `areNotificationsEnabled` is the question that has an answer on both
     * sides of that line: it is the user's own Settings toggle, which exists on
     * every version and is not a permission check.
     */
    private fun mayPost(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    private fun openIntent(context: Context, cid: String?, identity: String?): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .setAction(Intent.ACTION_VIEW)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (cid != null && identity != null) {
            // BUILT, not interpolated. `store.safe_id` deliberately permits
            // characters that are reserved in a URI -- `?`, `#`, `%`, `&` --
            // and a campaign directory named with one would silently become a
            // query or a fragment here, so the tap would ask the backend about
            // a truncated campaign or find no scene target at all.
            intent.data = Uri.Builder()
                .scheme("grimoire").authority("scene")
                .appendPath(cid).appendPath(identity)
                .build()
        }
        return PendingIntent.getActivity(
            context,
            (cid.orEmpty() + identity.orEmpty()).hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
