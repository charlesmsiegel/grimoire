"""Tests for ``TimeEngineService`` (spec 07).

Covers the public surface: clock storage, ``advance``/``skip_to`` mechanics,
NPC tick fan-out + significance filtering, faction ticks, scheduled events,
commitment aging integration, mechanics ``time_tick`` fan-out, and digest
shape.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from grimoire.characters import CharactersService
from grimoire.continuity import ContinuityService
from grimoire.continuity.types import (
    Commitment as ContinuityCommitment,
)
from grimoire.continuity.types import (
    CommitmentKind,
)
from grimoire.continuity.types import (
    InGameTime as ContinuityInGameTime,
)
from grimoire.event_bus import Event, EventBus
from grimoire.mechanics import MechanicsService
from grimoire.setting import SettingService
from grimoire.state_store import StateStore
from grimoire.time_engine import (
    InvalidSkipError,
    SignificanceConfig,
    TimeEngineConfig,
    TimeEngineService,
    TimeNotSetError,
)
from grimoire.types.characters import CharacterData, CharacterRole, VoiceAnchor
from grimoire.types.common import Duration, InGameTime
from grimoire.types.time import (
    ScheduledEvent,
    TimeAdvanceReason,
)

CAMPAIGN = "test-campaign"
SETTING = "test-setting"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_campaign(store: StateStore, *, mechanics: str | None = None) -> None:
    await store.write_library_file(
        library_id=f"settings/{SETTING}/setting/{SETTING}",
        frontmatter={"id": SETTING, "name": SETTING, "version": 1},
        body="",
        source="test:seed",
    )
    await store.upsert_campaign(
        campaign_id=CAMPAIGN,
        name="Test Campaign",
        mechanics_module=mechanics,
    )
    await store.upsert_setting_ref(
        campaign_id=CAMPAIGN,
        setting_id=SETTING,
        priority=1,
        include=[],
        track_latest=True,
    )


def _character(asset_id: str, role: CharacterRole = CharacterRole.MAJOR_NPC) -> CharacterData:
    return CharacterData(
        id=asset_id,
        name=asset_id.title(),
        role=role,
        voice=VoiceAnchor(summary="terse"),
        description="",
        body="",
    )


def _duration_days(days: int) -> Duration:
    delta = timedelta(days=days)
    return Duration(iso8601=f"P{days}D", delta=delta)


def _time(year: int = 2024, month: int = 1, day: int = 1) -> InGameTime:
    return InGameTime(moment=datetime(year, month, day, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Clock storage
# ---------------------------------------------------------------------------


async def test_current_is_none_when_unset(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    assert await time_engine.current(CAMPAIGN) is None


async def test_set_and_read_current(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2025, 5, 3))
    current = await time_engine.current(CAMPAIGN)
    assert current is not None
    assert current.moment == datetime(2025, 5, 3, tzinfo=UTC)


async def test_advance_requires_anchor(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    with pytest.raises(TimeNotSetError):
        await time_engine.advance(CAMPAIGN, _duration_days(1), TimeAdvanceReason.EXPLICIT_USER)


# ---------------------------------------------------------------------------
# Advance / skip_to
# ---------------------------------------------------------------------------


async def test_advance_moves_clock_and_returns_result(
    time_engine: TimeEngineService, store: StateStore
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    result = await time_engine.advance(
        CAMPAIGN,
        _duration_days(7),
        TimeAdvanceReason.EXPLICIT_USER,
    )

    assert result.from_time.moment == datetime(2024, 1, 1, tzinfo=UTC)
    assert result.to_time.moment == datetime(2024, 1, 8, tzinfo=UTC)
    assert result.duration.iso8601 == "P7D"
    persisted = await time_engine.current(CAMPAIGN)
    assert persisted is not None
    assert persisted.moment == datetime(2024, 1, 8, tzinfo=UTC)


async def test_skip_to_rejects_past_target(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 6, 1))
    with pytest.raises(InvalidSkipError):
        await time_engine.skip_to(CAMPAIGN, _time(2024, 5, 1), TimeAdvanceReason.EXPLICIT_USER)


async def test_skip_to_moves_clock(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    result = await time_engine.skip_to(CAMPAIGN, _time(2024, 1, 15), TimeAdvanceReason.SCENE_BREAK)

    assert result.to_time.moment == datetime(2024, 1, 15, tzinfo=UTC)
    assert result.duration.delta == timedelta(days=14)


# ---------------------------------------------------------------------------
# Scheduled events
# ---------------------------------------------------------------------------


async def test_schedule_and_upcoming(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    eid = await time_engine.schedule_event(
        ScheduledEvent(
            id="",
            campaign_id=CAMPAIGN,
            at=_time(2024, 1, 5),
            label="winifred's birthday",
            kind="one_off",
        )
    )
    assert eid

    upcoming = await time_engine.upcoming_events(CAMPAIGN, _duration_days(7))
    assert [e.label for e in upcoming] == ["winifred's birthday"]


async def test_advance_triggers_scheduled_events_in_window(
    time_engine: TimeEngineService, store: StateStore
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    inside = await time_engine.schedule_event(
        ScheduledEvent(
            id="",
            campaign_id=CAMPAIGN,
            at=_time(2024, 1, 3),
            label="Letter arrives",
            kind="one_off",
        )
    )
    outside = await time_engine.schedule_event(
        ScheduledEvent(
            id="",
            campaign_id=CAMPAIGN,
            at=_time(2024, 2, 1),
            label="Later",
            kind="one_off",
        )
    )

    result = await time_engine.advance(CAMPAIGN, _duration_days(5), TimeAdvanceReason.EXPLICIT_USER)

    triggered_ids = [e.id for e in result.scheduled_events_triggered]
    assert inside in triggered_ids
    assert outside not in triggered_ids

    # The triggered row's `triggered` flag is now 1 in storage.
    upcoming = await time_engine.upcoming_events(CAMPAIGN)
    assert outside in [e.id for e in upcoming]
    assert inside not in [e.id for e in upcoming]


async def test_cancel_event_removes_row(time_engine: TimeEngineService, store: StateStore):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    eid = await time_engine.schedule_event(
        ScheduledEvent(
            id="",
            campaign_id=CAMPAIGN,
            at=_time(2024, 2, 1),
            label="Festival",
            kind="holiday",
        )
    )
    await time_engine.cancel_event(eid)
    upcoming = await time_engine.upcoming_events(CAMPAIGN)
    assert eid not in [e.id for e in upcoming]


# ---------------------------------------------------------------------------
# NPC ticks + significance filtering
# ---------------------------------------------------------------------------


async def test_npcs_tick_via_injected_callable(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(SETTING, _character("alistair", CharacterRole.MAJOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    seen: list[str] = []

    async def tick_fn(payload):
        seen.append(payload["character_id"])
        return {
            "activities": [f"{payload['character_id']} read books"],
            "location_at_end": "library",
            "mood_at_end": "calm",
            "secrets_kept": ["working on something"],
            "next_intent": "keep reading",
            "should_seek_pc": False,
        }

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]

    result = await time_engine.advance(CAMPAIGN, _duration_days(7), TimeAdvanceReason.EXPLICIT_USER)

    assert seen == ["alistair"]
    summary = result.npc_summaries["alistair"]
    assert summary.activities == ["alistair read books"]
    assert summary.state_at_end["location"] == "library"
    assert summary.secrets_kept == ["working on something"]


async def test_pcs_are_not_ticked(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(SETTING, _character("hyde", CharacterRole.PC))
    await characters.create(SETTING, _character("alistair", CharacterRole.MAJOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    seen: list[str] = []

    async def tick_fn(payload):
        seen.append(payload["character_id"])
        return {}

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _duration_days(1), TimeAdvanceReason.EXPLICIT_USER)

    assert seen == ["alistair"]
    assert "hyde" not in result.npc_summaries


async def test_minor_npc_with_open_commitment_ticks(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
    continuity: ContinuityService,
):
    await _seed_campaign(store)
    await characters.create(SETTING, _character("vivienne", CharacterRole.MINOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    char_ref = f"library:settings/{SETTING}/characters/vivienne"
    await continuity.add_commitment(
        ContinuityCommitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="vivienne will deliver the letter.",
            created_in_post="post-1",
            in_game_created_at=ContinuityInGameTime(day_count=0),
            from_id=char_ref,
            weight=2,
        ),
        source="extractor",
    )

    seen: list[str] = []

    async def tick_fn(payload):
        seen.append(payload["character_id"])
        return {}

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]

    await time_engine.advance(CAMPAIGN, _duration_days(3), TimeAdvanceReason.EXPLICIT_USER)
    assert seen == ["vivienne"]


async def test_significance_cap_is_respected(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    for i in range(5):
        await characters.create(SETTING, _character(f"npc-{i}", CharacterRole.MAJOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    time_engine._config = TimeEngineConfig(significance=SignificanceConfig(max_npcs_per_advance=2))

    async def tick_fn(_payload):
        return {}

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _duration_days(1), TimeAdvanceReason.EXPLICIT_USER)
    assert len(result.npc_summaries) == 2


# ---------------------------------------------------------------------------
# Faction ticks
# ---------------------------------------------------------------------------


async def test_faction_tick_only_at_month_resolution(
    time_engine: TimeEngineService,
    setting: SettingService,
    store: StateStore,
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    await setting.update_faction_state(
        faction_ref=f"library:settings/{SETTING}/factions/cabal",
        campaign_id=CAMPAIGN,
        patch={
            "goals": [
                {"id": "control-district", "description": "Take the south bank", "progress": 0.10},
            ],
        },
    )

    short = await time_engine.advance(CAMPAIGN, _duration_days(7), TimeAdvanceReason.EXPLICIT_USER)
    assert short.faction_summaries == {}

    long = await time_engine.advance(CAMPAIGN, _duration_days(60), TimeAdvanceReason.EXPLICIT_USER)
    faction_ref = f"library:settings/{SETTING}/factions/cabal"
    assert faction_ref in long.faction_summaries
    assert long.faction_summaries[faction_ref].goal_progress["control-district"] > 0.10


# ---------------------------------------------------------------------------
# Commitment aging
# ---------------------------------------------------------------------------


async def test_advance_ages_commitments(
    time_engine: TimeEngineService,
    store: StateStore,
    continuity: ContinuityService,
):
    await _seed_campaign(store)
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    cid = await continuity.add_commitment(
        ContinuityCommitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Send the letter by week's end.",
            created_in_post="post-1",
            in_game_created_at=ContinuityInGameTime(day_count=0),
            from_id="character:foo",
            due_by=ContinuityInGameTime(day_count=3),
        ),
        source="extractor",
    )

    result = await time_engine.advance(
        CAMPAIGN, _duration_days(10), TimeAdvanceReason.EXPLICIT_USER
    )

    overdue_ids = [c.id for c in result.commitments_overdue]
    assert cid in overdue_ids


# ---------------------------------------------------------------------------
# Mechanics fan-out
# ---------------------------------------------------------------------------


async def test_mechanics_time_tick_is_called_for_each_present_npc(
    time_engine: TimeEngineService,
    mechanics: MechanicsService,
    characters: CharactersService,
    store: StateStore,
):
    # Register a stub mechanics module that records every time_tick call.
    from grimoire.types.mechanics import ModuleManifest

    class _RecordingModule:
        id = "test-mech"

        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def sheet_schema(self, _kind: str):
            return None

        def validate_sheet(self, _kind, _sheet):  # pragma: no cover - unused
            return None

        def initialize_sheet(self, _kind, _entity_id):
            return {}

        def content_schema(self, _kind):  # pragma: no cover - unused
            return None

        def capabilities_of(self, _ref, _sheet):
            return []

        def power_definitions(self):  # pragma: no cover - unused
            return []

        def evaluate_pre_roll(self, _input, _scene):  # pragma: no cover - unused
            return []

        def resolve_roll(self, roll, _seed):  # pragma: no cover - unused
            return None

        def validate_narrated_event(self, _event, _scene):  # pragma: no cover - unused
            return None

        def character_creation_steps(self):  # pragma: no cover - unused
            return []

        def time_tick(self, entity_ref, _sheet, duration, _context):
            self.calls.append((entity_ref, duration.delta.days))
            return []

        def system_summary(self):  # pragma: no cover - unused
            return ""

    module = _RecordingModule()
    mechanics.register_module(ModuleManifest(id="test-mech", name="Test", version="0.1"), module)

    await _seed_campaign(store, mechanics="test-mech")
    await characters.create(SETTING, _character("alistair", CharacterRole.MAJOR_NPC))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    async def tick_fn(_payload):
        return {}

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _duration_days(5), TimeAdvanceReason.EXPLICIT_USER)
    assert ("character:alistair", 5) in module.calls


# ---------------------------------------------------------------------------
# Event bus + digest
# ---------------------------------------------------------------------------


async def test_emits_time_advance_event(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    bus = EventBus()
    time_engine._event_bus = bus  # type: ignore[attr-defined]

    await _seed_campaign(store)
    await characters.create(SETTING, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    received: list[Event] = []
    bus.subscribe("time_advance", lambda e: received.append(e))

    async def tick_fn(_payload):
        return {}

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _duration_days(1), TimeAdvanceReason.EXPLICIT_USER)

    assert len(received) == 1
    payload = received[0].payload
    assert payload["campaign_id"] == CAMPAIGN
    assert payload["duration_iso"] == "P1D"
    assert "alistair" in payload["npcs_ticked"]


async def test_digest_includes_structured_summary(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    await _seed_campaign(store)
    await characters.create(SETTING, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    async def tick_fn(payload):
        return {"activities": [f"{payload['character_id']} read a book"]}

    async def digest_fn(payload):
        return f"Time passed: {len(payload['npc_summaries'])} characters lived."

    time_engine._npc_tick_fn = tick_fn  # type: ignore[attr-defined]
    time_engine._digest_fn = digest_fn  # type: ignore[attr-defined]
    result = await time_engine.advance(CAMPAIGN, _duration_days(2), TimeAdvanceReason.EXPLICIT_USER)

    assert "alistair read a book" in result.digest
    assert "Time passed: 1 characters lived." in result.digest


async def test_digest_narrative_can_be_disabled(
    time_engine: TimeEngineService,
    store: StateStore,
    characters: CharactersService,
):
    time_engine._config = dataclasses.replace(time_engine._config, digest_narrative=False)
    await _seed_campaign(store)
    await characters.create(SETTING, _character("alistair"))
    await time_engine.set_current(CAMPAIGN, _time(2024, 1, 1))

    called = False

    async def digest_fn(_payload):
        nonlocal called
        called = True
        return "narrative"

    time_engine._digest_fn = digest_fn  # type: ignore[attr-defined]
    await time_engine.advance(CAMPAIGN, _duration_days(1), TimeAdvanceReason.EXPLICIT_USER)
    assert called is False
