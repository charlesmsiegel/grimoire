"""Tests for the time-engine remaining-design features (sections 1-10)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from grimoire.characters import CharactersService
from grimoire.event_bus import Event, EventBus
from grimoire.state_store import StateStore
from grimoire.time_engine import (
    CheckpointTokenError,
    TimeEngineService,
    TimeEngineSubscriber,
    extract_time_advances_from_deltas,
)
from grimoire.types.characters import CharacterData, CharacterRole, VoiceAnchor
from grimoire.types.common import Duration, InGameTime
from grimoire.types.state import DeltaKind, Scope, StateDelta
from grimoire.types.time import (
    ScheduledEvent,
    TimeAdvanceReason,
)

CAMPAIGN = "test-campaign"
World = "test-world"


async def _seed_campaign(store: StateStore) -> None:
    await store.write_library_file(
        library_id=f"worlds/{World}/world/{World}",
        frontmatter={"id": World, "name": World, "version": 1},
        body="",
        source="test:seed",
    )
    await store.upsert_campaign(campaign_id=CAMPAIGN, name="Test Campaign")
    await store.upsert_world_ref(
        campaign_id=CAMPAIGN, world_id=World, priority=1, include=None, track_latest=True
    )


def _character(
    asset_id: str,
    role: CharacterRole = CharacterRole.MAJOR_NPC,
    *,
    household_id: str | None = None,
) -> CharacterData:
    return CharacterData(
        id=asset_id,
        name=asset_id.title(),
        role=role,
        voice=VoiceAnchor(summary="terse"),
        description="",
        body="",
        household_id=household_id,
    )


def _dur(days: int) -> Duration:
    return Duration(iso8601=f"P{days}D", delta=timedelta(days=days))


def _time(year: int = 2024, month: int = 1, day: int = 1) -> InGameTime:
    return InGameTime(moment=datetime(year, month, day, tzinfo=UTC))


# ---------------------------------------------------------------------------
# §2 — shared inter-NPC events pre-pass
# ---------------------------------------------------------------------------


async def test_shared_events_run_before_individual_ticks(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(World, _character("winifred"))
    await characters.create(World, _character("vivienne"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    flo_ref = f"library:worlds/{World}/characters/winifred"
    char_ref = f"library:worlds/{World}/characters/vivienne"

    async def shared(payload):
        return [
            {
                "id": "se-party",
                "summary": "winifred and vivienne planned the masquerade.",
                "participants": [flo_ref, char_ref],
                "in_game_at": payload["to"],
            }
        ]

    seen_events: dict[str, list[str]] = {}

    async def tick(payload):
        seen_events[payload["character_id"]] = [
            e["summary"] for e in payload.get("shared_events", [])
        ]
        return {}

    time_engine._shared_events_fn = shared  # type: ignore[attr-defined]
    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _dur(7), TimeAdvanceReason.EXPLICIT_USER)

    assert len(result.shared_events) == 1
    assert result.shared_events[0].summary.startswith("winifred and vivienne")
    # Both NPC ticks see the event in their payload.
    assert seen_events["winifred"][0].startswith("winifred and vivienne")
    assert seen_events["vivienne"][0].startswith("winifred and vivienne")


async def test_shared_events_skipped_when_disabled(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(World, _character("winifred"))
    await characters.create(World, _character("vivienne"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    called = False

    async def shared(_payload):
        nonlocal called
        called = True
        return []

    time_engine._config = dataclasses.replace(time_engine._config, shared_events_enabled=False)
    time_engine._shared_events_fn = shared  # type: ignore[attr-defined]

    async def tick(_):
        return {}

    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _dur(3), TimeAdvanceReason.EXPLICIT_USER)
    assert called is False


# ---------------------------------------------------------------------------
# §3 — subscribe_calendar wrapper
# ---------------------------------------------------------------------------


async def test_subscribe_calendar_returns_subscription_handle(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await characters.create(World, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    received: list[Event] = []
    subscription = time_engine.subscribe_calendar(lambda e: received.append(e))

    async def tick(_):
        return {}

    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _dur(1), TimeAdvanceReason.EXPLICIT_USER)
    assert len(received) == 1
    # Handle exposes unsubscribe.
    subscription.unsubscribe()
    await time_engine.advance(CAMPAIGN, _dur(1), TimeAdvanceReason.EXPLICIT_USER)
    assert len(received) == 1


async def test_subscribe_calendar_without_bus_raises(
    time_engine: TimeEngineService,
):
    time_engine._event_bus = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        time_engine.subscribe_calendar(lambda _: None)


# ---------------------------------------------------------------------------
# §4 — household-based significance
# ---------------------------------------------------------------------------


async def test_household_significance_ticks_household_member(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(World, _character("hyde", CharacterRole.PC, household_id="estate-1"))
    await characters.create(
        World,
        _character("butler", CharacterRole.MINOR_NPC, household_id="estate-1"),
    )
    # An unrelated minor NPC outside the household — should not tick.
    await characters.create(World, _character("stranger", CharacterRole.MINOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    seen: list[str] = []

    async def tick(payload):
        seen.append(payload["character_id"])
        return {}

    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _dur(2), TimeAdvanceReason.EXPLICIT_USER)
    assert "butler" in seen
    assert "stranger" not in seen
    assert "hyde" not in result.npc_summaries  # PCs still aren't ticked.


# ---------------------------------------------------------------------------
# §5 — faction tick depth (resources + leader actions + conflicts)
# ---------------------------------------------------------------------------


async def test_faction_resource_decay_applies_per_month(
    time_engine: TimeEngineService,
    store: StateStore,
):
    from grimoire.world import WorldService  # local: matches fixture import

    world: WorldService = time_engine._world  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    faction_ref = f"library:worlds/{World}/factions/cabal"
    await world.update_faction_state(
        faction_ref=faction_ref,
        campaign_id=CAMPAIGN,
        patch={
            "resources": {"gold": 100.0, "influence": 50},
            "leaders": ["alistair"],
        },
    )

    # 3 months → ~6% decay default → gold ~94, influence ~47
    result = await time_engine.advance(CAMPAIGN, _dur(90), TimeAdvanceReason.EXPLICIT_USER)
    summary = result.faction_summaries[faction_ref]
    assert "gold" in summary.resource_changes
    assert summary.resource_changes["gold"]["to"] < 100.0


async def test_faction_leader_tick_populates_notable_actions(
    time_engine: TimeEngineService,
    store: StateStore,
):
    from grimoire.world import WorldService

    world: WorldService = time_engine._world  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    # Seed a library faction with a populated ``leaders`` list — the engine
    # picks these up via the library, matching production wiring.
    await world.create_entity(
        World,
        "faction",
        "cabal",
        {"id": "cabal", "name": "Cabal", "leaders": ["alistair"]},
        body="",
    )
    faction_ref = f"library:worlds/{World}/factions/cabal"
    # Materialise an empty faction_state row so the engine sees the
    # faction during ticking.
    await world.update_faction_state(
        faction_ref=faction_ref,
        campaign_id=CAMPAIGN,
        patch={},
    )

    async def leader_fn(payload):
        return [f"Leader {payload['leaders'][0]} schemed."]

    time_engine._faction_leader_fn = leader_fn  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _dur(60), TimeAdvanceReason.EXPLICIT_USER)
    assert result.faction_summaries[faction_ref].notable_actions == [
        "Leader alistair schemed."
    ]


async def test_faction_conflicts_surface_on_result(
    time_engine: TimeEngineService,
    store: StateStore,
):
    from grimoire.world import WorldService

    world: WorldService = time_engine._world  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    faction_ref_a = f"library:worlds/{World}/factions/cabal"
    faction_ref_b = f"library:worlds/{World}/factions/sabbat"
    await world.update_faction_state(
        faction_ref=faction_ref_a, campaign_id=CAMPAIGN, patch={"goals": []}
    )
    await world.update_faction_state(
        faction_ref=faction_ref_b, campaign_id=CAMPAIGN, patch={"goals": []}
    )

    async def conflicts(payload):
        refs = [f["faction_ref"] for f in payload["factions"]]
        return [
            {
                "factions": refs,
                "summary": "Border skirmish over the docks.",
                "intensity": "open",
            }
        ]

    time_engine._faction_conflicts_fn = conflicts  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _dur(60), TimeAdvanceReason.EXPLICIT_USER)
    assert len(result.faction_conflicts) == 1
    assert result.faction_conflicts[0].intensity == "open"


# ---------------------------------------------------------------------------
# §6 — scheduled_event_pre_notice wiring
# ---------------------------------------------------------------------------


async def test_pre_notice_emits_event_once(
    time_engine: TimeEngineService,
    store: StateStore,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    eid = await time_engine.schedule_event(
        ScheduledEvent(
            id="",
            campaign_id=CAMPAIGN,
            at=_time(2024, 1, 10),
            label="Cabal vote",
            kind="plot_beat",
        )
    )

    received: list[Event] = []
    bus.subscribe("scheduled_event_imminent", lambda e: received.append(e))

    # Advance to 2024-01-05 — within the default 7d pre-notice of 1/10.
    result = await time_engine.advance(CAMPAIGN, _dur(4), TimeAdvanceReason.EXPLICIT_USER)
    assert len(received) == 1
    assert received[0].payload["event_id"] == eid
    assert any(e.id == eid for e in result.scheduled_events_upcoming)

    # A second advance that still keeps the event in the pre-notice window
    # should NOT re-warn.
    result2 = await time_engine.advance(CAMPAIGN, _dur(1), TimeAdvanceReason.EXPLICIT_USER)
    assert len(received) == 1
    assert result2.scheduled_events_upcoming == []


# ---------------------------------------------------------------------------
# §7 — activity_ref threading
# ---------------------------------------------------------------------------


async def test_activity_ref_flows_into_mechanics_context(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    from grimoire.mechanics import MechanicsService
    from grimoire.types.mechanics import ModuleManifest

    mechanics: MechanicsService = time_engine._mechanics  # type: ignore[attr-defined]

    class _RecordingModule:
        id = "test-mech"

        def __init__(self) -> None:
            self.contexts: list = []

        def sheet_schema(self, _kind):
            return None

        def validate_sheet(self, _kind, _sheet):
            return None

        def initialize_sheet(self, _kind, _entity_id):
            return {}

        def content_schema(self, _kind):
            return None

        def capabilities_of(self, _ref, _sheet):
            return []

        def power_definitions(self):
            return []

        def evaluate_pre_roll(self, _input, _scene):
            return []

        def resolve_roll(self, _roll, _seed):
            return None

        def validate_narrated_event(self, _event, _scene):
            return None

        def character_creation_steps(self):
            return []

        def time_tick(self, _entity_ref, _sheet, _duration, context):
            self.contexts.append(context)
            return []

        def system_summary(self):
            return ""

    module = _RecordingModule()
    mechanics.register_module(
        ModuleManifest(id="test-mech", name="Test", version="0.1"), module
    )
    await store.write_library_file(
        library_id=f"worlds/{World}/world/{World}",
        frontmatter={"id": World, "name": World, "version": 1},
        body="",
        source="test:seed",
    )
    await store.upsert_campaign(
        campaign_id=CAMPAIGN, name="Test Campaign", mechanics_module="test-mech"
    )
    await store.upsert_world_ref(
        campaign_id=CAMPAIGN, world_id=World, priority=1, include=None, track_latest=True
    )
    await characters.create(World, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    async def tick(_):
        return {}

    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    await time_engine.advance(
        CAMPAIGN,
        _dur(5),
        TimeAdvanceReason.ACTIVITY_DURATION,
        activity_ref="activity:sword-training",
    )
    assert module.contexts
    ctx = module.contexts[0]
    assert ctx.extras.get("activity_ref") == "activity:sword-training"


# ---------------------------------------------------------------------------
# §8 — checkpointing via propose_advance
# ---------------------------------------------------------------------------


async def test_propose_advance_emits_event_when_threshold_exceeded(
    time_engine: TimeEngineService,
    store: StateStore,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    received: list[Event] = []
    bus.subscribe("time_advance_checkpoint_suggested", lambda e: received.append(e))

    suggestion = await time_engine.propose_advance(
        CAMPAIGN, _dur(30), TimeAdvanceReason.EXPLICIT_USER
    )
    assert suggestion.threshold_exceeded is True
    assert len(received) == 1
    assert received[0].payload["token"] == suggestion.token


async def test_propose_advance_does_not_emit_when_under_threshold(
    time_engine: TimeEngineService,
    store: StateStore,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    received: list[Event] = []
    bus.subscribe("time_advance_checkpoint_suggested", lambda e: received.append(e))

    # Default threshold is 7 days; 1 day is well under.
    suggestion = await time_engine.propose_advance(
        CAMPAIGN, _dur(1), TimeAdvanceReason.EXPLICIT_USER
    )
    assert suggestion.threshold_exceeded is False
    assert received == []


async def test_checkpoint_token_must_match_advance(
    time_engine: TimeEngineService,
    store: StateStore,
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    suggestion = await time_engine.propose_advance(
        CAMPAIGN, _dur(30), TimeAdvanceReason.EXPLICIT_USER
    )
    # Different duration → mismatch.
    with pytest.raises(CheckpointTokenError):
        await time_engine.advance(
            CAMPAIGN,
            _dur(7),
            TimeAdvanceReason.EXPLICIT_USER,
            checkpoint_token=suggestion.token,
        )


async def test_checkpoint_token_consumed_on_use(
    time_engine: TimeEngineService,
    store: StateStore,
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    suggestion = await time_engine.propose_advance(
        CAMPAIGN, _dur(30), TimeAdvanceReason.EXPLICIT_USER
    )
    await time_engine.advance(
        CAMPAIGN,
        _dur(30),
        TimeAdvanceReason.EXPLICIT_USER,
        checkpoint_token=suggestion.token,
    )
    # Re-use → expired token.
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))
    with pytest.raises(CheckpointTokenError):
        await time_engine.advance(
            CAMPAIGN,
            _dur(30),
            TimeAdvanceReason.EXPLICIT_USER,
            checkpoint_token=suggestion.token,
        )


# ---------------------------------------------------------------------------
# §9 — NPC drift check
# ---------------------------------------------------------------------------


async def test_drift_check_emits_warnings(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await characters.create(World, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    async def tick(_):
        return {"activities": ["read a book"]}

    async def drift(payload):
        return [
            {
                "character_id": payload["character_id"],
                "severity": "warning",
                "summary": "Behaviour matches card",
                "evidence": [],
            }
        ]

    events: list[Event] = []
    bus.subscribe("npc_drift_detected", lambda e: events.append(e))
    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    time_engine._drift_check_fn = drift  # type: ignore[attr-defined]

    result = await time_engine.advance(CAMPAIGN, _dur(3), TimeAdvanceReason.EXPLICIT_USER)
    assert len(result.drift_warnings) == 1
    assert result.drift_warnings[0].character_id == "alistair"
    assert len(events) == 1


async def test_drift_check_disabled_by_config(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    time_engine._config = dataclasses.replace(time_engine._config, drift_check_enabled=False)
    await _seed_campaign(store)
    await characters.create(World, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    called = False

    async def drift(_):
        nonlocal called
        called = True
        return []

    time_engine._drift_check_fn = drift  # type: ignore[attr-defined]

    async def tick(_):
        return {}

    time_engine._npc_tick_fn = tick  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _dur(1), TimeAdvanceReason.EXPLICIT_USER)
    assert called is False


# ---------------------------------------------------------------------------
# §10 — configurable time precision
# ---------------------------------------------------------------------------


async def test_precision_day_truncates_subday_movement(
    time_engine: TimeEngineService,
    store: StateStore,
):
    time_engine._config = dataclasses.replace(time_engine._config, precision="day")
    await _seed_campaign(store)
    # Set a non-aligned anchor.
    anchor = InGameTime(moment=datetime(2024, 1, 1, 13, 45, tzinfo=UTC))
    await time_engine.set_current(CAMPAIGN, anchor)

    result = await time_engine.advance(
        CAMPAIGN,
        Duration(iso8601="PT3H", delta=timedelta(hours=3)),
        TimeAdvanceReason.EXPLICIT_USER,
    )
    # Both endpoints quantized to day boundaries → no movement.
    assert result.from_time.moment == datetime(2024, 1, 1, tzinfo=UTC)
    assert result.to_time.moment == datetime(2024, 1, 1, tzinfo=UTC)


async def test_precision_hour_quantizes_to_hour(
    time_engine: TimeEngineService,
    store: StateStore,
):
    time_engine._config = dataclasses.replace(time_engine._config, precision="hour")
    await _seed_campaign(store)
    await time_engine.set_current(
        CAMPAIGN,
        InGameTime(moment=datetime(2024, 1, 1, 14, 23, 17, tzinfo=UTC)),
    )
    result = await time_engine.advance(
        CAMPAIGN,
        Duration(iso8601="PT45M", delta=timedelta(minutes=45)),
        TimeAdvanceReason.EXPLICIT_USER,
    )
    # Quantized start: 14:00. Quantized end: floor(14:45) = 14:00.
    assert result.from_time.moment == datetime(2024, 1, 1, 14, tzinfo=UTC)
    assert result.to_time.moment == datetime(2024, 1, 1, 14, tzinfo=UTC)


# ---------------------------------------------------------------------------
# §1 — extract_time_advances_from_deltas + TimeEngineSubscriber
# ---------------------------------------------------------------------------


def test_extract_time_advances_from_deltas_handles_typed_and_dict():
    typed = StateDelta(
        kind=DeltaKind.TIME_ADVANCE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="time:P1D",
        target_table="calendar",
        after={
            "duration": Duration(iso8601="P1D", delta=timedelta(days=1)).model_dump(
                mode="json"
            )
        },
    )
    dicty = {
        "kind": "time_advance",
        "after": {"duration": "P2D"},
    }
    other = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact:x",
        target_table="facts",
        after={},
    )
    durations = extract_time_advances_from_deltas([typed, dicty, other])
    assert [d.iso8601 for d in durations] == ["P1D", "P2D"]


async def test_subscriber_drives_advance_on_turn_complete(
    time_engine: TimeEngineService,
    store: StateStore,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    subscriber = TimeEngineSubscriber(time_engine=time_engine, event_bus=bus)
    subscriber.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={
                    "turn_id": "t-1",
                    "campaign_id": CAMPAIGN,
                    "scene_id": "scene-1",
                    "branch_id": None,
                    "time_advances": [{"duration": "P1D"}],
                },
            )
        )
    finally:
        subscriber.stop()
    current = await time_engine.current(CAMPAIGN)
    assert current is not None
    assert current.moment == datetime(2024, 1, 2, tzinfo=UTC)


async def test_subscriber_no_op_when_no_time_advances(
    time_engine: TimeEngineService,
    store: StateStore,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    subscriber = TimeEngineSubscriber(time_engine=time_engine, event_bus=bus)
    subscriber.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={
                    "turn_id": "t-1",
                    "campaign_id": CAMPAIGN,
                    "scene_id": "scene-1",
                    "time_advances": [],
                },
            )
        )
    finally:
        subscriber.stop()
    # Clock unchanged.
    current = await time_engine.current(CAMPAIGN)
    assert current is not None
    assert current.moment == datetime(2024, 1, 1, tzinfo=UTC)
