package app.grimoire

/** A listening socket lets the WebView start connecting while Python imports
 * the app. It is not a terminal success: imports, lifespan and serving can
 * still fail. Keep observers until failure or explicit activity teardown. */
class ServerStartup {
    private sealed interface State {
        data object Waiting : State
        data class Listening(val port: Int) : State
        data class Failed(val message: String) : State
    }

    @Volatile private var state: State = State.Waiting
    val port: Int get() = (state as? State.Listening)?.port ?: -1
    val failure: String? get() = (state as? State.Failed)?.message
    private val listeners = linkedMapOf<Any, Pair<(Int) -> Unit, (String) -> Unit>>()

    @Synchronized
    fun subscribe(onReady: (Int) -> Unit, onFailure: (String) -> Unit): () -> Unit {
        val current = state
        if (current is State.Failed) {
            onFailure(current.message)
            return {}
        }
        val key = Any()
        listeners[key] = onReady to onFailure
        if (current is State.Listening) onReady(current.port)
        return { unsubscribe(key) }
    }

    @Synchronized
    private fun unsubscribe(key: Any) { listeners.remove(key) }

    @Synchronized
    fun publishPort(p: Int) {
        if (state != State.Waiting) return
        state = State.Listening(p)
        // Callbacks can unsubscribe, subscribe, or publish failure reentrantly.
        // A snapshot avoids iterator invalidation; membership/state checks keep
        // a destroyed observer or terminal failure from receiving a stale port.
        for ((key, observer) in listeners.toList()) {
            if (state is State.Listening && listeners.containsKey(key)) observer.first(p)
        }
    }

    @Synchronized
    fun publishFailure(message: String) {
        if (state is State.Failed) return
        state = State.Failed(message)
        for ((key, observer) in listeners.toList()) {
            if (listeners.remove(key) != null) observer.second(message)
        }
    }
}
