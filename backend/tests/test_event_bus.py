import asyncio
import logging

import pytest

from grimoire.event_bus import WILDCARD, Event, EventBus


async def test_subscribe_and_emit_async_handler() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    bus.subscribe("turn_started", handler)
    await bus.emit(Event(type="turn_started", payload={"turn_id": "t1"}))

    assert len(seen) == 1
    assert seen[0].type == "turn_started"
    assert seen[0].payload == {"turn_id": "t1"}
    assert seen[0].id
    assert seen[0].timestamp > 0


async def test_sync_handler_supported() -> None:
    bus = EventBus()
    seen: list[str] = []

    bus.subscribe("scene_started", lambda e: seen.append(e.type))
    await bus.emit(Event(type="scene_started"))

    assert seen == ["scene_started"]


async def test_emit_routes_only_to_matching_type() -> None:
    bus = EventBus()
    a: list[Event] = []
    b: list[Event] = []

    bus.subscribe("turn_started", lambda e: a.append(e))
    bus.subscribe("turn_complete", lambda e: b.append(e))

    await bus.emit(Event(type="turn_started"))
    await bus.emit(Event(type="turn_complete"))
    await bus.emit(Event(type="scene_started"))

    assert [e.type for e in a] == ["turn_started"]
    assert [e.type for e in b] == ["turn_complete"]


async def test_wildcard_subscriber_receives_every_event() -> None:
    bus = EventBus()
    seen: list[str] = []

    bus.subscribe(WILDCARD, lambda e: seen.append(e.type))

    for t in ("turn_started", "scene_ended", "image_ready"):
        await bus.emit(Event(type=t))

    assert seen == ["turn_started", "scene_ended", "image_ready"]


async def test_unsubscribe_stops_handler() -> None:
    bus = EventBus()
    seen: list[Event] = []
    sub = bus.subscribe("turn_started", lambda e: seen.append(e))

    await bus.emit(Event(type="turn_started"))
    sub.unsubscribe()
    await bus.emit(Event(type="turn_started"))

    assert len(seen) == 1
    assert bus.subscriber_count("turn_started") == 0


async def test_unsubscribe_is_idempotent() -> None:
    bus = EventBus()
    sub = bus.subscribe("x", lambda e: None)
    sub.unsubscribe()
    sub.unsubscribe()
    assert bus.subscriber_count() == 0


async def test_subscription_as_context_manager() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async with bus.subscribe("turn_started", lambda e: seen.append(e)):
        await bus.emit(Event(type="turn_started"))

    await bus.emit(Event(type="turn_started"))
    assert len(seen) == 1


async def test_failing_handler_does_not_block_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus()
    seen: list[str] = []

    def boom(_: Event) -> None:
        raise RuntimeError("kaboom")

    bus.subscribe("turn_started", boom)
    bus.subscribe("turn_started", lambda e: seen.append("ok"))

    with caplog.at_level(logging.ERROR, logger="grimoire.event_bus"):
        await bus.emit(Event(type="turn_started"))

    assert seen == ["ok"]
    assert any("event handler raised" in r.message for r in caplog.records)


async def test_concurrent_handlers_run_in_parallel() -> None:
    bus = EventBus()
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[int] = []

    async def slow(idx: int) -> None:
        started.set()
        await release.wait()
        finished.append(idx)

    for i in range(3):
        bus.subscribe("turn_started", lambda _e, i=i: slow(i))

    emit_task = asyncio.create_task(bus.emit(Event(type="turn_started")))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert finished == []
    release.set()
    await asyncio.wait_for(emit_task, timeout=1.0)
    assert sorted(finished) == [0, 1, 2]


async def test_emit_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    await bus.emit(Event(type="never_subscribed"))


async def test_subscribe_rejects_empty_event_type() -> None:
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.subscribe("", lambda e: None)


async def test_event_defaults_are_unique() -> None:
    a = Event(type="x")
    b = Event(type="x")
    assert a.id != b.id


async def test_subscriber_count_totals() -> None:
    bus = EventBus()
    bus.subscribe("a", lambda e: None)
    bus.subscribe("a", lambda e: None)
    bus.subscribe("b", lambda e: None)
    assert bus.subscriber_count("a") == 2
    assert bus.subscriber_count("b") == 1
    assert bus.subscriber_count() == 3
