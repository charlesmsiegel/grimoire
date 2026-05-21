"""WebSocket + event bus bridge tests for :mod:`grimoire.api.stream`."""

from __future__ import annotations

import pytest

from grimoire.api.container import ServiceContainer
from grimoire.api.stream import StreamManager
from grimoire.event_bus import Event, EventBus


@pytest.mark.asyncio
async def test_event_bus_forwards_campaign_events() -> None:
    bus = EventBus()
    stream = StreamManager(event_bus=bus)
    received: list[dict] = []

    async def fake_send(message: dict) -> None:
        received.append(message)

    class _FakeWS:
        async def accept(self) -> None: ...
        async def send_json(self, message: dict) -> None:
            await fake_send(message)

        async def close(self) -> None: ...

    ws = _FakeWS()
    await stream.connect("c1", ws)  # type: ignore[arg-type]

    await bus.emit(
        Event(
            type="drift_detected",
            payload={"campaign_id": "c1", "character_ref": "alistair", "score": 0.6},
        )
    )

    assert received == [
        {
            "type": "drift_detected",
            "campaign_id": "c1",
            "character_ref": "alistair",
            "score": 0.6,
        }
    ]
    await stream.aclose()


@pytest.mark.asyncio
async def test_push_to_unknown_campaign_is_noop() -> None:
    stream = StreamManager(event_bus=None)
    await stream.push("nope", {"type": "x"})  # must not raise


def test_ws_health_endpoint(client, container: ServiceContainer) -> None:
    container.stream = StreamManager(event_bus=container.event_bus)
    response = client.get("/ws/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["campaigns"] == []


def test_ws_health_unavailable_without_container_stream(
    client, container: ServiceContainer
) -> None:
    container.stream = None
    response = client.get("/ws/health")
    # Lifespan re-creates a StreamManager if one isn't set, so it should be ok.
    assert response.status_code == 200


def test_websocket_receives_event_bus_messages(client, container: ServiceContainer) -> None:
    """End-to-end: connect a real WS client, emit on the event bus the stream
    is subscribed to, and verify the message is forwarded."""
    bus = container.event_bus
    stream = container.stream
    assert bus is not None and stream is not None

    with client.websocket_connect("/ws/campaigns/c1/stream") as ws:
        # Run the emit on the server loop via the TestClient's portal so the
        # subscription handler executes synchronously with respect to the WS.
        client.portal.call(
            bus.emit,
            Event(
                type="drift_detected",
                payload={"campaign_id": "c1", "character_ref": "alistair", "score": 0.7},
            ),
        )
        msg = ws.receive_json()
        assert msg["type"] == "drift_detected"
        assert msg["character_ref"] == "alistair"


@pytest.mark.parametrize(
    "event_type",
    ["health_status_changed", "error_reported"],
)
@pytest.mark.asyncio
async def test_observability_events_are_broadcast(event_type: str) -> None:
    """§12: ``health_status_changed`` and ``error_reported`` events carry no
    campaign_id so they must reach every subscribed socket via broadcast."""
    bus = EventBus()
    stream = StreamManager(event_bus=bus)
    received: list[dict] = []

    class _FakeWS:
        async def accept(self) -> None: ...
        async def send_json(self, message: dict) -> None:
            received.append(message)

        async def close(self) -> None: ...

    await stream.connect("c1", _FakeWS())  # type: ignore[arg-type]
    await stream.connect("c2", _FakeWS())  # type: ignore[arg-type]

    payload = (
        {"target_id": "prov", "level": "healthy"}
        if event_type == "health_status_changed"
        else {"module": "orchestrator", "error_kind": "boom"}
    )
    await bus.emit(Event(type=event_type, payload=payload))

    # Both sockets should have received the message (no campaign_id → broadcast).
    assert [m["type"] for m in received] == [event_type, event_type]
    await stream.aclose()


@pytest.mark.parametrize(
    "event_type",
    ["alternate_added", "primary_switched", "alternate_pinned", "alternate_deleted"],
)
@pytest.mark.asyncio
async def test_alternate_events_are_forwarded(event_type: str) -> None:
    bus = EventBus()
    stream = StreamManager(event_bus=bus)
    received: list[dict] = []

    class _FakeWS:
        async def accept(self) -> None: ...
        async def send_json(self, message: dict) -> None:
            received.append(message)

        async def close(self) -> None: ...

    await stream.connect("c1", _FakeWS())  # type: ignore[arg-type]
    await bus.emit(
        Event(
            type=event_type,
            payload={"campaign_id": "c1", "post_id": "p1", "alternate_id": "a1"},
        )
    )
    assert received and received[0]["type"] == event_type
    assert received[0]["post_id"] == "p1"
    await stream.aclose()
