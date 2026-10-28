package app.grimoire

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerStartupTest {
    @Test fun failureAfterListeningReachesTheOriginalSubscriber() {
        val startup = ServerStartup()
        val events = mutableListOf<String>()
        startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        startup.publishPort(8173)
        startup.publishFailure("app import failed")
        assertEquals(listOf("port:8173", "failed:app import failed"), events)
    }

    @Test fun subscriberAfterListeningStillReceivesLaterFailure() {
        val startup = ServerStartup()
        startup.publishPort(8173)
        val events = mutableListOf<String>()
        startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        startup.publishFailure("server exited")
        assertEquals(listOf("port:8173", "failed:server exited"), events)
    }

    @Test fun terminalFailureWinsOverAPreviouslyPublishedPort() {
        val startup = ServerStartup()
        startup.publishPort(8173)
        startup.publishFailure("app import failed")
        val events = mutableListOf<String>()
        startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        assertEquals(listOf("failed:app import failed"), events)
        assertEquals(-1, startup.port)
    }

    @Test fun failureBeforeListeningNeverPublishesAUsableOrigin() {
        val startup = ServerStartup()
        val events = mutableListOf<String>()
        startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        startup.publishFailure("Python could not start")
        startup.publishPort(8173)
        assertEquals(listOf("failed:Python could not start"), events)
        assertEquals(-1, startup.port)
    }

    @Test fun destroyedSubscriberReceivesNoFutureEvents() {
        val startup = ServerStartup()
        val events = mutableListOf<String>()
        val unsubscribe = startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        unsubscribe()
        startup.publishPort(8173)
        startup.publishFailure("app import failed")
        assertTrue(events.isEmpty())
    }

    @Test fun listeningSubscriberCanBeDestroyedBeforeServerFailure() {
        val startup = ServerStartup()
        startup.publishPort(8173)
        val events = mutableListOf<String>()
        val unsubscribe = startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        unsubscribe()
        startup.publishFailure("server exited")
        assertEquals(listOf("port:8173"), events)
    }

    @Test fun unsubscribeInsideACallbackDoesNotDropOtherSubscribers() {
        val startup = ServerStartup()
        val events = mutableListOf<String>()
        var unsubscribe: () -> Unit = {}
        unsubscribe = startup.subscribe({ unsubscribe() }, { events.add("destroyed:$it") })
        startup.subscribe({ events.add("port:$it") }, { events.add("failed:$it") })
        startup.publishPort(8173)
        startup.publishFailure("server exited")
        assertEquals(listOf("port:8173", "failed:server exited"), events)
    }
}
