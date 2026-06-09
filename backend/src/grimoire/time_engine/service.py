"""Concrete Time Engine implementation (spec 07).

The Time Engine is the per-campaign clock + advancement coordinator. It does
not run on its own: callers (the Orchestrator after extraction, or the UI on
an explicit skip) trigger ``advance``/``skip_to``. The engine then:

* updates the campaign's calendar row,
* runs NPC ticks for the significant subset of offscreen NPCs,
* runs faction ticks at the configured resolution,
* triggers any ``ScheduledEvent`` whose ``at`` falls in the window,
* ages commitments via ``Continuity.age``,
* calls ``Mechanics.time_tick`` for every present character,
* produces a structured + (optional) narrative digest.

The service is intentionally framework-light: the LLM tick generator is an
injectable async callable so tests can wire a deterministic stub and
production can hand in a Gateway-backed adapter. Same for the digest
renderer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire import events
from grimoire.characters import CharactersService
from grimoire.continuity.protocols import Continuity
from grimoire.continuity.registry import resolve_continuity
from grimoire.continuity.types import InGameTime as ContinuityInGameTime
from grimoire.event_bus import Event, EventBus
from grimoire.mechanics.service import MechanicsService
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.state_store import StateStore
from grimoire.types.characters import CharacterRole, ResolvedCharacter
from grimoire.types.common import CampaignId, CharacterRef, Duration, EventId, InGameTime
from grimoire.types.state import StateDelta
from grimoire.types.time import (
    CheckpointSuggestion,
    DriftWarning,
    FactionConflict,
    FactionTickSummary,
    NpcTickSummary,
    ScheduledEvent,
    SharedEvent,
    TimeAdvanceReason,
    TimeAdvanceResult,
    WeatherChange,
)
from grimoire.types.world import WorldCalendar
from grimoire.util import new_id, now_iso, parse_iso_datetime
from grimoire.world import WorldService

from .config import TimeEngineConfig, TimePrecision
from .errors import CheckpointTokenError, InvalidSkipError, TimeNotSetError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable adapters
# ---------------------------------------------------------------------------

NpcTickInput = dict[str, Any]
NpcTickPayload = dict[str, Any]
NpcTickFn = Callable[[NpcTickInput], Awaitable[NpcTickPayload]]
DigestFn = Callable[[dict[str, Any]], Awaitable[str]]
SharedEventsFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]
DriftCheckFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]
FactionConflictsFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]
FactionLeaderFn = Callable[[dict[str, Any]], Awaitable[list[str]]]


async def _default_npc_tick(_payload: NpcTickInput) -> NpcTickPayload:
    """Fallback NPC tick: produces an empty but well-formed summary payload.

    Used when no LLM-backed ticker has been wired in. Tests rely on this so
    the service can be exercised end-to-end without an LLM.
    """
    return {
        "activities": [],
        "location_at_end": "",
        "mood_at_end": "",
        "new_facts": [],
        "relationship_changes": [],
        "secrets_kept": [],
        "next_intent": "",
        "should_seek_pc": False,
        "events_pc_would_witness": [],
    }


async def _default_digest(_payload: dict[str, Any]) -> str:
    """Fallback narrative digest: empty string (i.e. structured-only)."""
    return ""


async def _default_shared_events(_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback shared-events pre-pass: emit nothing.

    Production wiring hands an LLM-backed generator; tests can wire a
    deterministic stub the same way they do for ``_npc_tick_fn``.
    """
    return []


async def _default_drift_check(_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return []


async def _default_faction_conflicts(_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return []


async def _default_faction_leader_actions(_payload: dict[str, Any]) -> list[str]:
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _duration_from_timedelta(td: timedelta) -> Duration:
    """Build a ``Duration`` (ISO-8601 + timedelta) from a ``timedelta``."""
    return Duration(iso8601=_timedelta_to_iso(td), delta=td)


def _timedelta_to_iso(td: timedelta) -> str:
    """Minimal ISO-8601 duration for whole-day / whole-second precision.

    The shared types reserve months/years for the ISO string only; we never
    produce them here because ``timedelta`` is the source of truth at runtime.
    """
    total = int(td.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    out = f"{sign}P"
    if days:
        out += f"{days}D"
    time_part = ""
    if hours:
        time_part += f"{hours}H"
    if minutes:
        time_part += f"{minutes}M"
    if seconds:
        time_part += f"{seconds}S"
    if time_part:
        out += "T" + time_part
    if out in {"P", "-P"}:
        out = "PT0S"
    return out


def _to_continuity_time(when: InGameTime, epoch: datetime | None) -> ContinuityInGameTime:
    """Convert a datetime-based InGameTime into Continuity's day-count form.

    Continuity (task #18) tracks aging in whole days from a campaign epoch;
    the Time Engine works in datetimes. When a world declares its own
    epoch we anchor to it; otherwise the Unix epoch is fine because only
    deltas matter to Continuity.
    """
    anchor = epoch or datetime(1970, 1, 1, tzinfo=UTC)
    moment = when.moment
    if moment.tzinfo is None and anchor.tzinfo is not None:
        moment = moment.replace(tzinfo=anchor.tzinfo)
    if anchor.tzinfo is None and moment.tzinfo is not None:
        anchor = anchor.replace(tzinfo=moment.tzinfo)
    delta = moment - anchor
    return ContinuityInGameTime(day_count=int(delta.total_seconds() // 86400))


@dataclass
class _PresentCharacter:
    ref: CharacterRef
    asset_id: str
    role: CharacterRole
    is_pc: bool
    location_ref: str | None
    last_screen_time_turn: str | None
    household_id: str | None = None


# ---------------------------------------------------------------------------
# Precision quantization (§10)
# ---------------------------------------------------------------------------


def _quantize(moment: datetime, precision: TimePrecision) -> datetime:
    """Snap ``moment`` down to the requested precision.

    ``season`` rounds to whole 90-day buckets anchored to the Unix epoch (a
    season is a 90-day window in v1; calendars with custom season widths can
    override this via a future hook). The cheaper buckets just truncate the
    finer subfields.
    """
    if precision == "minute":
        return moment.replace(second=0, microsecond=0)
    if precision == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    if precision == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "season":
        floor_day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        anchor = datetime(1970, 1, 1, tzinfo=floor_day.tzinfo or UTC)
        if floor_day.tzinfo is None and anchor.tzinfo is not None:
            floor_day = floor_day.replace(tzinfo=anchor.tzinfo)
        days_since_anchor = (floor_day - anchor).days
        bucket = (days_since_anchor // 90) * 90
        return anchor + timedelta(days=bucket)
    return moment


# ---------------------------------------------------------------------------
# Checkpoint tokens (§8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CheckpointTokenData:
    campaign_id: str
    from_iso: str
    to_iso: str
    duration_iso: str
    reason: str
    scene_id: str | None
    activity_ref: str | None


@dataclass
class _CheckpointStore:
    """In-memory token store. Pure dict by design — tokens are
    short-lived; if the process restarts the UI just re-proposes.
    """

    tokens: dict[str, _CheckpointTokenData] = field(default_factory=dict)


class TimeEngineService:
    """Spec 07 implementation.

    Construct with the shared :class:`StateStore`, the per-campaign helpers
    (World, Characters, Mechanics) plus a Continuity instance, optionally
    an :class:`EventBus` for ``time_advance``/``npc_tick_complete`` emission,
    and the two injectable callables for LLM-backed tick + digest generation.
    """

    def __init__(
        self,
        *,
        store: StateStore,
        world: WorldService,
        characters: CharactersService,
        mechanics: MechanicsService,
        continuity: Continuity,
        config: TimeEngineConfig | None = None,
        event_bus: EventBus | None = None,
        npc_tick_fn: NpcTickFn | None = None,
        digest_fn: DigestFn | None = None,
        shared_events_fn: SharedEventsFn | None = None,
        drift_check_fn: DriftCheckFn | None = None,
        faction_conflicts_fn: FactionConflictsFn | None = None,
        faction_leader_fn: FactionLeaderFn | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self._store = store
        self._world = world
        self._characters = characters
        self._mechanics = mechanics
        self._continuity = continuity
        self._config = config or TimeEngineConfig()
        self._event_bus = event_bus
        self._npc_tick_fn = npc_tick_fn or _default_npc_tick
        self._digest_fn = digest_fn or _default_digest
        self._shared_events_fn = shared_events_fn or _default_shared_events
        self._drift_check_fn = drift_check_fn or _default_drift_check
        self._faction_conflicts_fn = faction_conflicts_fn or _default_faction_conflicts
        self._faction_leader_fn = faction_leader_fn or _default_faction_leader_actions
        self._checkpoints = _CheckpointStore()
        self._metrics: MetricsRegistryProtocol = metrics

    # ------------------------------------------------------------------ #
    # Time accessors
    # ------------------------------------------------------------------ #

    async def current(
        self,
        campaign_id: CampaignId,
    ) -> InGameTime | None:
        """Return the current in-game time, or ``None`` if never set."""
        row = await self._store.db.fetchone(
            "SELECT current_in_game_time FROM calendar WHERE campaign_id = ?",
            (campaign_id,),
        )
        if row is None:
            return None
        moment = parse_iso_datetime(row["current_in_game_time"])
        if moment is None:
            return None
        return InGameTime(moment=moment)

    async def set_current(
        self,
        campaign_id: CampaignId,
        when: InGameTime,
    ) -> None:
        """Overwrite the campaign's clock without running ticks.

        Used by campaign creation and tests; the regular advancement path is
        :meth:`advance` / :meth:`skip_to`, which both go through this method
        after running the rest of the pipeline.
        """
        await self._store.db.execute(
            """
            INSERT INTO calendar (campaign_id, current_in_game_time)
            VALUES (?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
              current_in_game_time = excluded.current_in_game_time
            """,
            (campaign_id, when.moment.isoformat()),
        )

    async def calendar(self, campaign_id: CampaignId) -> WorldCalendar:
        """The active calendar for the campaign (highest-priority world)."""
        return await self._world.calendar_for_campaign(campaign_id)

    # ------------------------------------------------------------------ #
    # Scheduled events
    # ------------------------------------------------------------------ #

    async def schedule_event(self, event: ScheduledEvent) -> EventId:
        """Persist a scheduled event. Returns the (possibly generated) id."""
        eid = event.id or new_id("evt")
        await self._store.db.execute(
            """
            INSERT INTO scheduled_events (
              id, campaign_id, at, kind, label, payload,
              triggered, triggered_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
              at = excluded.at,
              kind = excluded.kind,
              label = excluded.label,
              payload = excluded.payload,
              triggered = excluded.triggered
            """,
            (
                eid,
                event.campaign_id,
                event.at.moment.isoformat(),
                event.kind,
                event.label,
                json.dumps(event.payload or {}, default=str),
                1 if event.triggered else 0,
                now_iso(),
            ),
        )
        return eid

    async def cancel_event(self, event_id: EventId) -> None:
        await self._store.db.execute(
            "DELETE FROM scheduled_events WHERE id = ?",
            (event_id,),
        )

    async def upcoming_events(
        self,
        campaign_id: CampaignId,
        within: Duration | None = None,
    ) -> list[ScheduledEvent]:
        """List pending events within ``within`` of the current time.

        With ``within=None`` returns every future, non-triggered event for
        the campaign.
        """
        current = await self.current(campaign_id)
        upper: str | None = None
        if within is not None and current is not None:
            upper = (current.moment + within.delta).isoformat()

        if upper is None:
            rows = await self._store.db.fetchall(
                """
                SELECT * FROM scheduled_events
                WHERE campaign_id = ? AND triggered = 0
                ORDER BY at ASC
                """,
                (campaign_id,),
            )
        else:
            lower = (current.moment.isoformat()) if current is not None else "0001-01-01T00:00:00"
            rows = await self._store.db.fetchall(
                """
                SELECT * FROM scheduled_events
                WHERE campaign_id = ? AND triggered = 0
                  AND at >= ? AND at <= ?
                ORDER BY at ASC
                """,
                (campaign_id, lower, upper),
            )
        return [_scheduled_event_from_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Advancement
    # ------------------------------------------------------------------ #

    async def advance(
        self,
        campaign_id: CampaignId,
        duration: Duration,
        reason: TimeAdvanceReason,
        *,
        scene_id: str | None = None,
        from_time: InGameTime | None = None,
        activity_ref: str | None = None,
        checkpoint_token: str | None = None,
    ) -> TimeAdvanceResult:
        async with self._metrics.measure("time_engine", "advance"):
            return await self._advance_inner(
                campaign_id,
                duration,
                reason,
                scene_id=scene_id,
                from_time=from_time,
                activity_ref=activity_ref,
                checkpoint_token=checkpoint_token,
            )

    async def _advance_inner(
        self,
        campaign_id: CampaignId,
        duration: Duration,
        reason: TimeAdvanceReason,
        *,
        scene_id: str | None = None,
        from_time: InGameTime | None = None,
        activity_ref: str | None = None,
        checkpoint_token: str | None = None,
    ) -> TimeAdvanceResult:
        """Advance the campaign clock by ``duration`` and run ticks.

        ``from_time`` overrides the stored value, useful for one-off
        backfills. Otherwise the campaign's current clock is required.

        ``activity_ref`` (§7) threads through to ``mechanics.time_tick``'s
        context so a mechanic can resolve a specific outstanding activity
        rather than re-deriving it from sheet state.

        ``checkpoint_token`` (§8) confirms a prior ``propose_advance`` call.
        The token's stored parameters must match the call exactly; if they
        diverge ``CheckpointTokenError`` is raised. The token is consumed
        on use.
        """
        start = from_time or await self.current(campaign_id)
        if start is None:
            raise TimeNotSetError(
                f"campaign {campaign_id!r} has no in-game time yet; "
                "set one (e.g. via set_current) before advancing"
            )
        precision = self._config.precision
        start_q = InGameTime(
            moment=_quantize(start.moment, precision),
            calendar_id=start.calendar_id,
        )
        to_raw = start_q.moment + duration.delta
        to = InGameTime(
            moment=_quantize(to_raw, precision),
            calendar_id=start.calendar_id,
        )
        # Re-derive duration from the quantized endpoints so the result
        # matches the wall-clock movement actually applied.
        effective_duration = _duration_from_timedelta(to.moment - start_q.moment)
        if checkpoint_token is not None:
            self._consume_checkpoint_token(
                checkpoint_token,
                campaign_id=campaign_id,
                from_iso=start_q.moment.isoformat(),
                to_iso=to.moment.isoformat(),
                duration_iso=effective_duration.iso8601,
                reason=reason,
                scene_id=scene_id,
                activity_ref=activity_ref,
            )
        return await self._run_pipeline(
            campaign_id=campaign_id,
            scene_id=scene_id,
            reason=reason,
            from_time=start_q,
            to_time=to,
            duration=effective_duration,
            activity_ref=activity_ref,
        )

    async def skip_to(
        self,
        campaign_id: CampaignId,
        target: InGameTime,
        reason: TimeAdvanceReason,
        *,
        scene_id: str | None = None,
        from_time: InGameTime | None = None,
        activity_ref: str | None = None,
        checkpoint_token: str | None = None,
    ) -> TimeAdvanceResult:
        """Advance to ``target`` (which must be strictly later than now)."""
        start = from_time or await self.current(campaign_id)
        if start is None:
            raise TimeNotSetError(
                f"campaign {campaign_id!r} has no in-game time yet; "
                "set one (e.g. via set_current) before skipping"
            )
        if target.moment <= start.moment:
            raise InvalidSkipError(
                f"skip_to target {target.moment.isoformat()} is not after "
                f"current {start.moment.isoformat()}"
            )
        precision = self._config.precision
        start_q = InGameTime(
            moment=_quantize(start.moment, precision),
            calendar_id=start.calendar_id,
        )
        target_q = InGameTime(
            moment=_quantize(target.moment, precision),
            calendar_id=target.calendar_id,
        )
        if target_q.moment <= start_q.moment:
            # Both ends collapsed onto the same precision bucket — extend
            # the target by one bucket so the call still moves the clock.
            target_q = InGameTime(
                moment=start_q.moment + _precision_step(precision),
                calendar_id=target.calendar_id,
            )
        duration = _duration_from_timedelta(target_q.moment - start_q.moment)
        if checkpoint_token is not None:
            self._consume_checkpoint_token(
                checkpoint_token,
                campaign_id=campaign_id,
                from_iso=start_q.moment.isoformat(),
                to_iso=target_q.moment.isoformat(),
                duration_iso=duration.iso8601,
                reason=reason,
                scene_id=scene_id,
                activity_ref=activity_ref,
            )
        return await self._run_pipeline(
            campaign_id=campaign_id,
            scene_id=scene_id,
            reason=reason,
            from_time=start_q,
            to_time=target_q,
            duration=duration,
            activity_ref=activity_ref,
        )

    # ------------------------------------------------------------------ #
    # §8 Checkpointing — propose_advance
    # ------------------------------------------------------------------ #

    async def propose_advance(
        self,
        campaign_id: CampaignId,
        duration: Duration,
        reason: TimeAdvanceReason,
        *,
        scene_id: str | None = None,
        from_time: InGameTime | None = None,
        activity_ref: str | None = None,
    ) -> CheckpointSuggestion:
        """Return a checkpoint suggestion for the caller to confirm.

        Does **not** run the pipeline. Issues a token that the UI exchanges
        for a real :meth:`advance` call (passing ``checkpoint_token=``).
        If the projected duration exceeds ``config.checkpoint_threshold``
        we emit ``time_advance_checkpoint_suggested`` so the Frontend can
        prompt for a state-store fork.
        """
        start = from_time or await self.current(campaign_id)
        if start is None:
            raise TimeNotSetError(
                f"campaign {campaign_id!r} has no in-game time yet; "
                "set one (e.g. via set_current) before proposing an advance"
            )
        precision = self._config.precision
        start_q = InGameTime(
            moment=_quantize(start.moment, precision),
            calendar_id=start.calendar_id,
        )
        to = InGameTime(
            moment=_quantize(start_q.moment + duration.delta, precision),
            calendar_id=start.calendar_id,
        )
        effective_duration = _duration_from_timedelta(to.moment - start_q.moment)
        exceeded = effective_duration.delta > self._config.checkpoint_threshold
        token = new_id("ckpt")
        self._checkpoints.tokens[token] = _CheckpointTokenData(
            campaign_id=campaign_id,
            from_iso=start_q.moment.isoformat(),
            to_iso=to.moment.isoformat(),
            duration_iso=effective_duration.iso8601,
            reason=reason.value if hasattr(reason, "value") else str(reason),
            scene_id=scene_id,
            activity_ref=activity_ref,
        )
        if exceeded:
            await self._emit(
                events.TIME_ADVANCE_CHECKPOINT_SUGGESTED,
                {
                    "campaign_id": campaign_id,
                    "scene_id": scene_id,
                    "token": token,
                    "from": start_q.moment.isoformat(),
                    "to": to.moment.isoformat(),
                    "duration_iso": effective_duration.iso8601,
                    "reason": token_reason_value(reason),
                    "activity_ref": activity_ref,
                },
            )
        return CheckpointSuggestion(
            token=token,
            campaign_id=campaign_id,
            from_time=start_q,
            to_time=to,
            duration=effective_duration,
            reason=reason,
            threshold_exceeded=exceeded,
            scene_id=scene_id,
            activity_ref=activity_ref,
        )

    def _consume_checkpoint_token(
        self,
        token: str,
        *,
        campaign_id: str,
        from_iso: str,
        to_iso: str,
        duration_iso: str,
        reason: TimeAdvanceReason,
        scene_id: str | None,
        activity_ref: str | None,
    ) -> None:
        data = self._checkpoints.tokens.pop(token, None)
        if data is None:
            raise CheckpointTokenError(f"unknown or expired checkpoint token {token!r}")
        reason_str = token_reason_value(reason)
        mismatches = []
        if data.campaign_id != campaign_id:
            mismatches.append(("campaign_id", data.campaign_id, campaign_id))
        if data.from_iso != from_iso:
            mismatches.append(("from", data.from_iso, from_iso))
        if data.to_iso != to_iso:
            mismatches.append(("to", data.to_iso, to_iso))
        if data.duration_iso != duration_iso:
            mismatches.append(("duration_iso", data.duration_iso, duration_iso))
        if data.reason != reason_str:
            mismatches.append(("reason", data.reason, reason_str))
        if data.scene_id != scene_id:
            mismatches.append(("scene_id", data.scene_id or "", scene_id or ""))
        if data.activity_ref != activity_ref:
            mismatches.append(("activity_ref", data.activity_ref or "", activity_ref or ""))
        if mismatches:
            detail = ", ".join(f"{name}: {want!r}!={got!r}" for name, want, got in mismatches)
            raise CheckpointTokenError(
                f"checkpoint token {token!r} parameters do not match advance call ({detail})"
            )

    # ------------------------------------------------------------------ #
    # §3 subscribe_calendar — thin wrapper around the event bus
    # ------------------------------------------------------------------ #

    def subscribe_calendar(
        self,
        handler: Callable[[Event], Awaitable[None]] | Callable[[Event], None],
    ) -> Any:
        """Subscribe ``handler`` to the ``time_advance`` event.

        Returns the underlying :class:`grimoire.event_bus.Subscription`
        handle; call ``.unsubscribe()`` on it to stop receiving events.
        Thin convenience wrapper around the shared :class:`EventBus`;
        callers that already have a bus reference can subscribe directly.
        Raises :class:`RuntimeError` when no event bus was wired in at
        construction.
        """
        if self._event_bus is None:
            raise RuntimeError(
                "subscribe_calendar requires the TimeEngineService to have been "
                "constructed with an event_bus"
            )
        return self._event_bus.subscribe(events.TIME_ADVANCE, handler)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #

    async def _run_pipeline(
        self,
        *,
        campaign_id: CampaignId,
        scene_id: str | None,
        reason: TimeAdvanceReason,
        from_time: InGameTime,
        to_time: InGameTime,
        duration: Duration,
        activity_ref: str | None = None,
    ) -> TimeAdvanceResult:
        # The order matters: scheduled events first so NPC ticks see the
        # post-event state; ticks before commitment aging so a NPC who pays
        # off a thread does it before the aging sweep catches the
        # commitment as overdue.
        triggered = await self._fire_scheduled_events(
            campaign_id=campaign_id,
            from_time=from_time,
            to_time=to_time,
        )

        ticked = await self._significant_npcs(campaign_id=campaign_id)

        # §2 Shared inter-NPC events pre-pass — seeded with the full
        # ticked-NPC list so the LLM (or stub) can produce coherent
        # cross-NPC events once, instead of letting each individual tick
        # invent its own version.
        shared_events = await self._run_shared_events(
            campaign_id=campaign_id,
            present=ticked,
            from_time=from_time,
            to_time=to_time,
            duration=duration,
        )

        npc_summaries = await self._run_npc_ticks(
            campaign_id=campaign_id,
            duration=duration,
            present=ticked,
            from_time=from_time,
            to_time=to_time,
            shared_events=shared_events,
        )

        faction_summaries, faction_conflicts = await self._run_faction_ticks(
            campaign_id=campaign_id,
            duration=duration,
        )

        weather_changes = await self._weather_changes(
            campaign_id=campaign_id,
            from_time=from_time,
            to_time=to_time,
            characters=ticked,
        )

        mechanics_deltas = await self._run_mechanics_ticks(
            campaign_id=campaign_id,
            duration=duration,
            present=ticked,
            activity_ref=activity_ref,
        )

        drift_warnings = await self._run_drift_check(
            campaign_id=campaign_id,
            present=ticked,
            summaries=npc_summaries,
        )

        epoch = await self._epoch_for(campaign_id)
        continuity_for_campaign = resolve_continuity(self._continuity, campaign_id)
        aging = await continuity_for_campaign.age(_to_continuity_time(to_time, epoch))
        commit_anchor = to_time

        await self.set_current(campaign_id, to_time)

        # §6 Scheduled-event pre-notice — emit ``scheduled_event_imminent``
        # for events that fall in the post-advance pre-notice window and
        # have not previously been warned about. Persisted via
        # ``pre_notice_emitted_at`` on the scheduled_events row so we don't
        # re-warn on subsequent advances.
        upcoming_warned = await self._fire_pre_notice_warnings(
            campaign_id=campaign_id,
            from_time=to_time,
        )

        digest_payload: dict[str, Any] = {
            "from": from_time.moment.isoformat(),
            "to": to_time.moment.isoformat(),
            "duration_iso": duration.iso8601,
            "reason": reason.value if hasattr(reason, "value") else str(reason),
            "precision": self._config.precision,
            "scheduled_events": [e.label for e in triggered],
            "npc_summaries": [
                {
                    "character_id": s.character_id,
                    "activities": s.activities,
                    "degraded": s.degraded,
                }
                for s in npc_summaries.values()
            ],
            "faction_summaries": [
                {"faction_id": s.faction_id, "notable_actions": s.notable_actions}
                for s in faction_summaries.values()
            ],
            "shared_events": [
                {"summary": e.summary, "participants": e.participants} for e in shared_events
            ],
            "faction_conflicts": [c.summary for c in faction_conflicts],
            "weather_changes": [w.summary for w in weather_changes],
            "overdue": [c.id for c in aging.became_overdue],
            "stale": [c.id for c in aging.became_stale],
            "upcoming": [e.label for e in upcoming_warned],
        }
        narrative = ""
        if self._config.digest_narrative:
            narrative = await self._digest_fn(digest_payload)
        structured = _structured_digest(digest_payload)
        digest_text = (narrative + ("\n\n" + structured if narrative else structured)).strip()

        # Convert Continuity commitments (dataclasses) into the shared-type
        # form the result carries. Days come from Continuity; we project them
        # back onto datetimes using the calendar epoch when available.
        # `commitments_due` surfaces commitments that went stale this tick
        # (open too long with no resolution); `commitments_overdue` surfaces
        # commitments whose explicit `due_by` just passed.
        commitments_overdue = [
            _shared_commitment(c, campaign_id, epoch, commit_anchor) for c in aging.became_overdue
        ]
        commitments_due = [
            _shared_commitment(c, campaign_id, epoch, commit_anchor) for c in aging.became_stale
        ]

        result = TimeAdvanceResult(
            from_time=from_time,
            to_time=to_time,
            duration=duration,
            npc_summaries=npc_summaries,
            faction_summaries=faction_summaries,
            scheduled_events_triggered=triggered,
            weather_changes=weather_changes,
            commitments_due=commitments_due,
            commitments_overdue=commitments_overdue,
            mechanics_deltas=mechanics_deltas,
            digest=digest_text,
            shared_events=shared_events,
            drift_warnings=drift_warnings,
            faction_conflicts=faction_conflicts,
            scheduled_events_upcoming=upcoming_warned,
        )

        await self._emit(
            events.TIME_ADVANCE,
            {
                "campaign_id": campaign_id,
                "scene_id": scene_id,
                "reason": digest_payload["reason"],
                "from": digest_payload["from"],
                "to": digest_payload["to"],
                "duration_iso": duration.iso8601,
                "npcs_ticked": list(npc_summaries.keys()),
                "scheduled_events_triggered": [e.id for e in triggered],
                "activity_ref": activity_ref,
                "precision": self._config.precision,
            },
        )
        return result

    # ------------------------------------------------------------------ #
    # Pipeline steps
    # ------------------------------------------------------------------ #

    async def _fire_scheduled_events(
        self,
        *,
        campaign_id: CampaignId,
        from_time: InGameTime,
        to_time: InGameTime,
    ) -> list[ScheduledEvent]:
        lo, hi = from_time.moment, to_time.moment
        if hi < lo:
            lo, hi = hi, lo
        rows = await self._store.db.fetchall(
            """
            SELECT * FROM scheduled_events
            WHERE campaign_id = ?
              AND triggered = 0 AND at > ? AND at <= ?
            ORDER BY at ASC
            """,
            (campaign_id, lo.isoformat(), hi.isoformat()),
        )
        triggered: list[ScheduledEvent] = []
        ts = now_iso()
        for row in rows:
            triggered.append(_scheduled_event_from_row(row, triggered=True))
            await self._store.db.execute(
                "UPDATE scheduled_events SET triggered = 1, triggered_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
        return triggered

    async def _significant_npcs(self, *, campaign_id: CampaignId) -> list[_PresentCharacter]:
        """Pick the NPCs that warrant an individual tick.

        Combines:
          * role-based always-tick rules
          * characters with open commitments
          * characters mentioned in the last N posts
        Hard-capped by ``significance.max_npcs_per_advance``.
        """
        cfg = self._config.significance
        resolved = await self._characters.list_for_campaign(campaign_id)
        by_ref: dict[str, _PresentCharacter] = {}
        for r in resolved:
            ref = r.current_state.character_ref or self._ref_from_resolved(r)
            entry = _PresentCharacter(
                ref=ref,
                asset_id=r.character.id,
                role=r.character.role,
                is_pc=(r.character.role == CharacterRole.PC),
                location_ref=r.current_state.location_ref,
                last_screen_time_turn=r.current_state.last_screen_time_turn,
                household_id=r.character.household_id,
            )
            by_ref[ref] = entry

        always_roles = {role.lower() for role in cfg.always_tick_roles}
        kept: dict[str, _PresentCharacter] = {}
        for ref, ent in by_ref.items():
            if ent.is_pc:
                continue  # PCs don't get NPC ticks; the player drives them.
            if ent.role.value.lower() in always_roles:
                kept[ref] = ent

        if cfg.tick_with_open_commitment:
            continuity_for_campaign = resolve_continuity(self._continuity, campaign_id)
            opens = await continuity_for_campaign.open_commitments(
                involving=[ref for ref in by_ref],
                limit=200,
            )
            for c in opens:
                for side in (c.from_id, c.to_id):
                    if side and side in by_ref and not by_ref[side].is_pc:
                        kept.setdefault(side, by_ref[side])

        if cfg.recent_post_window > 0:
            recent = await self._recent_post_author_refs(
                campaign_id=campaign_id,
                limit=cfg.recent_post_window,
            )
            for ref in recent:
                if ref in by_ref and not by_ref[ref].is_pc:
                    kept.setdefault(ref, by_ref[ref])

        # §4 Household-based significance — any NPC sharing a household_id
        # with at least one PC ticks regardless of role / commitments /
        # recent-post visibility. The "PC household" set is computed once.
        if cfg.tick_in_household:
            pc_households = {
                ent.household_id for ent in by_ref.values() if ent.is_pc and ent.household_id
            }
            if pc_households:
                for ref, ent in by_ref.items():
                    if ent.is_pc:
                        continue
                    if ent.household_id and ent.household_id in pc_households:
                        kept.setdefault(ref, ent)

        ordered = sorted(kept.values(), key=lambda p: (p.role.value, p.asset_id))
        return ordered[: cfg.max_npcs_per_advance]

    async def _run_npc_ticks(
        self,
        *,
        campaign_id: CampaignId,
        duration: Duration,
        present: list[_PresentCharacter],
        from_time: InGameTime,
        to_time: InGameTime,
        shared_events: list[SharedEvent],
    ) -> dict[str, NpcTickSummary]:
        if not present:
            return {}
        semaphore = asyncio.Semaphore(max(1, self._config.npc_tick_parallelism))
        # Index shared events by participating character_ref / asset_id so we
        # can slice down the per-NPC view cheaply without re-scanning.
        events_by_participant: dict[str, list[SharedEvent]] = {}
        for ev in shared_events:
            for participant in ev.participants:
                events_by_participant.setdefault(participant, []).append(ev)

        async def _one(p: _PresentCharacter) -> tuple[str, NpcTickSummary]:
            async with semaphore:
                my_events = events_by_participant.get(p.ref, []) + events_by_participant.get(
                    p.asset_id, []
                )
                payload: NpcTickInput = {
                    "campaign_id": campaign_id,
                    "character_ref": p.ref,
                    "character_id": p.asset_id,
                    "role": p.role.value,
                    "duration_iso": duration.iso8601,
                    "from": from_time.moment.isoformat(),
                    "to": to_time.moment.isoformat(),
                    "location_ref": p.location_ref,
                    "household_id": p.household_id,
                    "shared_events": [
                        {
                            "id": e.id,
                            "summary": e.summary,
                            "participants": list(e.participants),
                            "in_game_at": e.in_game_at.moment.isoformat(),
                            "details": dict(e.details),
                        }
                        for e in my_events
                    ],
                }
                degraded = False
                try:
                    result = await self._npc_tick_fn(payload)
                except Exception:
                    logger.exception("npc_tick callable raised for %s", p.ref)
                    result = await _default_npc_tick(payload)
                    degraded = True
                summary = _npc_summary_from_payload(p, duration, result, degraded=degraded)
                await self._emit(
                    events.NPC_TICK_COMPLETE,
                    {
                        "campaign_id": campaign_id,
                        "character_ref": p.ref,
                        "activities": summary.activities,
                        "duration_iso": duration.iso8601,
                        "degraded": summary.degraded,
                    },
                )
                return p.asset_id, summary

        results = await asyncio.gather(*[_one(p) for p in present])
        return dict(results)

    async def _run_faction_ticks(
        self,
        *,
        campaign_id: CampaignId,
        duration: Duration,
    ) -> tuple[dict[str, FactionTickSummary], list[FactionConflict]]:
        # Faction ticks are intentionally coarse: spec calls for month-level
        # granularity. Anything shorter than that doesn't run.
        if duration.delta < self._config.faction_tick_resolution:
            return {}, []
        rows = await self._store.db.fetchall(
            """
            SELECT * FROM faction_state
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        )
        out: dict[str, FactionTickSummary] = {}
        months = max(1, int(duration.delta.days // 30))
        decay = float(self._config.faction_resource_decay_per_month)
        # Pull library-side leaders for every faction in one sweep so the
        # leader-tick has a populated ``leaders`` list even when the
        # per-campaign state blob hasn't materialised them yet (the typical
        # case — update_faction_state only persists campaign-mutable fields).
        leaders_by_ref: dict[str, list[str]] = {}
        seen_worlds: set[str] = set()
        for row in rows:
            world_id, _ = _split_faction_ref(row["faction_ref"])
            if world_id and world_id not in seen_worlds:
                seen_worlds.add(world_id)
                try:
                    for fac in await self._world.list_factions(world_id):
                        leaders_by_ref[f"library:worlds/{world_id}/factions/{fac.id}"] = list(
                            fac.leaders
                        )
                except Exception:  # pragma: no cover - defensive
                    logger.exception("list_factions failed for world %s", world_id)
        faction_views: list[dict[str, Any]] = []
        for row in rows:
            faction_ref = row["faction_ref"]
            try:
                state = json.loads(row["state"]) if row["state"] else {}
            except (TypeError, json.JSONDecodeError):
                state = {}
            if not isinstance(state, dict):
                state = {}

            # Goal progress — same heuristic as before (1%/month), capped.
            goals = state.get("goals") or []
            goal_progress: dict[str, float] = {}
            for g in goals:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("id") or "")
                prev = float(g.get("progress") or 0.0)
                new_progress = min(1.0, prev + 0.01 * months)
                g["progress"] = new_progress
                goal_progress[gid] = new_progress
            state["goals"] = goals

            # Resource decay (§5) — slow drift toward zero for any
            # numeric resource the faction tracks. Authors can override
            # by writing fresh resource values via WorldService outside
            # the engine; the decay only fires here.
            resources = state.get("resources") or {}
            resource_changes: dict[str, Any] = {}
            if isinstance(resources, dict) and decay > 0.0:
                for key, val in list(resources.items()):
                    if isinstance(val, int | float) and not isinstance(val, bool):
                        old = float(val)
                        delta_val = -decay * months * abs(old)
                        new = old + delta_val
                        if abs(new) < 1e-9:
                            new = 0.0
                        resources[key] = type(val)(new) if isinstance(val, int) else new
                        if old != resources[key]:
                            resource_changes[key] = {"from": old, "to": resources[key]}
            state["resources"] = resources

            # Leader-tick (§5) — surface "this is what the leader did"
            # entries as notable_actions. The leader callable defaults to
            # an empty list; production hands in an LLM-backed generator
            # that consults the leader's character card. Leaders come from
            # the library Faction definition; per-campaign state can
            # override via a ``leaders`` array on the state blob.
            leaders = list(state.get("leaders") or leaders_by_ref.get(faction_ref, []))
            notable: list[str] = []
            if self._config.faction_leader_tick and leaders:
                try:
                    notable = list(
                        await self._faction_leader_fn(
                            {
                                "campaign_id": campaign_id,
                                "faction_ref": faction_ref,
                                "leaders": leaders,
                                "goals": goal_progress,
                                "resources": resources,
                                "months": months,
                                "duration_iso": duration.iso8601,
                            }
                        )
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.exception("faction_leader_fn failed for %s", faction_ref)
                    notable = []

            await self._store.db.execute(
                """
                UPDATE faction_state SET state = ?
                WHERE faction_ref = ? AND campaign_id = ?
                """,
                (json.dumps(state, default=str), faction_ref, campaign_id),
            )
            out[faction_ref] = FactionTickSummary(
                faction_id=faction_ref,
                duration=duration,
                goal_progress=goal_progress,
                resource_changes=resource_changes,
                notable_actions=notable,
            )
            faction_views.append(
                {
                    "faction_ref": faction_ref,
                    "goals": goals,
                    "resources": resources,
                    "leaders": leaders,
                }
            )

        # Inter-faction conflict pass (§5) — analogous to §2's shared-events
        # pass but for factions. Run once over the full set so we don't
        # double-count both sides of a rivalry.
        conflicts: list[FactionConflict] = []
        if faction_views:
            try:
                raw = await self._faction_conflicts_fn(
                    {
                        "campaign_id": campaign_id,
                        "factions": faction_views,
                        "months": months,
                        "duration_iso": duration.iso8601,
                    }
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("faction_conflicts_fn failed for %s", campaign_id)
                raw = []
            for item in raw or []:
                if not isinstance(item, dict):
                    continue
                conflicts.append(
                    FactionConflict(
                        factions=[str(f) for f in (item.get("factions") or [])],
                        summary=str(item.get("summary") or ""),
                        intensity=str(item.get("intensity") or "latent"),  # type: ignore[arg-type]
                        details=dict(item.get("details") or {}),
                    )
                )

        return out, conflicts

    async def _weather_changes(
        self,
        *,
        campaign_id: CampaignId,
        from_time: InGameTime,
        to_time: InGameTime,
        characters: list[_PresentCharacter],
    ) -> list[WeatherChange]:
        location_refs: list[str] = []
        seen: set[str] = set()
        for c in characters:
            if c.location_ref and c.location_ref not in seen:
                seen.add(c.location_ref)
                location_refs.append(c.location_ref)
        changes: list[WeatherChange] = []
        for ref in location_refs:
            world_id, asset_id = _split_location_ref(ref)
            if not world_id or not asset_id:
                continue
            try:
                w_before = await self._world.weather_for(world_id, asset_id, from_time, campaign_id)
                w_after = await self._world.weather_for(world_id, asset_id, to_time, campaign_id)
            except Exception:  # pragma: no cover - defensive
                logger.exception("weather lookup failed for %s", ref)
                continue
            if w_before.kind != w_after.kind or w_before.summary != w_after.summary:
                changes.append(
                    WeatherChange(
                        location_ref=ref,
                        at=to_time,
                        summary=w_after.summary or w_after.kind.value,
                        details={
                            "from_kind": w_before.kind.value,
                            "to_kind": w_after.kind.value,
                        },
                    )
                )
        return changes

    async def _run_mechanics_ticks(
        self,
        *,
        campaign_id: CampaignId,
        duration: Duration,
        present: list[_PresentCharacter],
        activity_ref: str | None = None,
    ) -> list[StateDelta]:
        """Fan out ``Mechanics.time_tick`` per character.

        With ``mechanics: null`` the call is cheap (returns empty), so we
        don't gate on the module here; the service handles it.
        ``activity_ref`` (§7) is threaded into the per-character context
        so a mechanic can resolve a specific outstanding activity rather
        than re-deriving it from sheet state.
        """
        # The Mechanics service builds a default TickContext when none is
        # passed; we only construct one when an activity_ref is in play, so
        # that mechanic modules can resolve a specific outstanding activity
        # rather than re-deriving it from sheet state (§7).
        from grimoire.types.mechanics import TickContext

        context = (
            TickContext(
                campaign_id=campaign_id,
                duration=duration,
                extras={"activity_ref": activity_ref},
            )
            if activity_ref
            else None
        )
        out: list[StateDelta] = []
        for p in present:
            try:
                deltas = await self._mechanics.time_tick(
                    campaign_id=campaign_id,
                    entity_ref=f"character:{p.asset_id}",
                    duration=duration,
                    context=context,
                    entity_kind="character",
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("mechanics.time_tick failed for %s", p.ref)
                continue
            for d in deltas:
                if isinstance(d, StateDelta):
                    out.append(d)
                elif isinstance(d, dict):
                    try:
                        out.append(StateDelta.model_validate(d))
                    except Exception:  # pragma: no cover - defensive
                        continue
        return out

    # ------------------------------------------------------------------ #
    # §2 Shared inter-NPC events
    # ------------------------------------------------------------------ #

    async def _run_shared_events(
        self,
        *,
        campaign_id: CampaignId,
        present: list[_PresentCharacter],
        from_time: InGameTime,
        to_time: InGameTime,
        duration: Duration,
    ) -> list[SharedEvent]:
        if not self._config.shared_events_enabled or len(present) < 2:
            return []
        payload = {
            "campaign_id": campaign_id,
            "from": from_time.moment.isoformat(),
            "to": to_time.moment.isoformat(),
            "duration_iso": duration.iso8601,
            "participants": [
                {
                    "character_ref": p.ref,
                    "character_id": p.asset_id,
                    "role": p.role.value,
                    "location_ref": p.location_ref,
                    "household_id": p.household_id,
                }
                for p in present
            ],
        }
        try:
            raw = await self._shared_events_fn(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("shared_events_fn failed for campaign %s", campaign_id)
            return []
        out: list[SharedEvent] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            participants = [str(p) for p in (item.get("participants") or [])]
            summary = str(item.get("summary") or "").strip()
            if not summary or not participants:
                continue
            at_raw = item.get("in_game_at")
            at_dt: datetime | None = None
            if isinstance(at_raw, str) and at_raw:
                try:
                    at_dt = datetime.fromisoformat(at_raw)
                except ValueError:
                    at_dt = None
            elif isinstance(at_raw, datetime):
                at_dt = at_raw
            at = InGameTime(moment=at_dt) if at_dt is not None else to_time
            event_id = str(item.get("id") or new_id("se"))
            out.append(
                SharedEvent(
                    id=event_id,
                    participants=participants,
                    summary=summary,
                    in_game_at=at,
                    details=dict(item.get("details") or {}),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # §6 Scheduled-event pre-notice warnings
    # ------------------------------------------------------------------ #

    async def _fire_pre_notice_warnings(
        self,
        *,
        campaign_id: CampaignId,
        from_time: InGameTime,
    ) -> list[ScheduledEvent]:
        pre = self._config.scheduled_event_pre_notice
        if pre is None or pre.total_seconds() <= 0:
            return []
        lo = from_time.moment.isoformat()
        hi = (from_time.moment + pre).isoformat()
        rows = await self._store.db.fetchall(
            """
            SELECT * FROM scheduled_events
            WHERE campaign_id = ?
              AND triggered = 0 AND at > ? AND at <= ?
              AND pre_notice_emitted_at IS NULL
            ORDER BY at ASC
            """,
            (campaign_id, lo, hi),
        )
        warned: list[ScheduledEvent] = []
        ts = now_iso()
        for row in rows:
            event = _scheduled_event_from_row(row)
            await self._store.db.execute(
                "UPDATE scheduled_events SET pre_notice_emitted_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
            await self._emit(
                events.SCHEDULED_EVENT_IMMINENT,
                {
                    "campaign_id": campaign_id,
                    "event_id": event.id,
                    "label": event.label,
                    "kind": event.kind,
                    "at": event.at.moment.isoformat(),
                },
            )
            warned.append(event)
        return warned

    # ------------------------------------------------------------------ #
    # §9 NPC drift check
    # ------------------------------------------------------------------ #

    async def _run_drift_check(
        self,
        *,
        campaign_id: CampaignId,
        present: list[_PresentCharacter],
        summaries: dict[str, NpcTickSummary],
    ) -> list[DriftWarning]:
        if not self._config.drift_check_enabled or not summaries:
            return []
        by_asset: dict[str, _PresentCharacter] = {p.asset_id: p for p in present}
        warnings: list[DriftWarning] = []
        for asset_id, summary in summaries.items():
            p = by_asset.get(asset_id)
            payload = {
                "campaign_id": campaign_id,
                "character_id": asset_id,
                "character_ref": p.ref if p else None,
                "role": p.role.value if p else None,
                "summary": {
                    "activities": list(summary.activities),
                    "state_at_end": dict(summary.state_at_end),
                    "relationships_changed": list(summary.relationships_changed),
                    "new_facts_about_them": list(summary.new_facts_about_them),
                    "next_intent": summary.next_intent,
                },
            }
            try:
                raw = await self._drift_check_fn(payload)
            except Exception:  # pragma: no cover - defensive
                logger.exception("drift_check_fn failed for %s", asset_id)
                continue
            for item in raw or []:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("severity") or "warning")
                if severity not in {"info", "warning", "critical"}:
                    severity = "warning"
                w = DriftWarning(
                    character_id=str(item.get("character_id") or asset_id),
                    severity=severity,  # type: ignore[arg-type]
                    summary=str(item.get("summary") or ""),
                    evidence=[str(e) for e in (item.get("evidence") or [])],
                )
                warnings.append(w)
                await self._emit(
                    events.NPC_DRIFT_DETECTED,
                    {
                        "campaign_id": campaign_id,
                        "character_id": w.character_id,
                        "severity": w.severity,
                        "summary": w.summary,
                    },
                )
        return warnings

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _epoch_for(self, campaign_id: CampaignId) -> datetime | None:
        try:
            cal = await self._world.calendar_for_campaign(campaign_id)
        except Exception:
            return None
        return cal.epoch

    async def _recent_post_author_refs(
        self,
        *,
        campaign_id: CampaignId,
        limit: int,
    ) -> list[str]:
        rows = await self._store.db.fetchall(
            """
            SELECT author_pc_ref FROM posts
            WHERE campaign_id = ?
            ORDER BY created_at DESC, order_in_scene DESC
            LIMIT ?
            """,
            (campaign_id, limit),
        )
        refs: list[str] = []
        seen: set[str] = set()
        for r in rows:
            ref = r["author_pc_ref"]
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
        return refs

    def _ref_from_resolved(self, r: ResolvedCharacter) -> str:
        if r.character.world_id:
            return f"library:worlds/{r.character.world_id}/characters/{r.character.id}"
        return f"campaign:emergent/character/{r.character.id}"

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.emit(Event(type=event_type, payload=payload))


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def token_reason_value(reason: TimeAdvanceReason | str) -> str:
    return reason.value if hasattr(reason, "value") else str(reason)


def _precision_step(precision: TimePrecision) -> timedelta:
    if precision == "minute":
        return timedelta(minutes=1)
    if precision == "hour":
        return timedelta(hours=1)
    if precision == "day":
        return timedelta(days=1)
    if precision == "season":
        return timedelta(days=90)
    return timedelta(minutes=1)


def _structured_digest(payload: dict[str, Any]) -> str:
    """Build a compact human-readable structured digest from the payload."""
    parts: list[str] = []
    parts.append(f"From {payload['from']} to {payload['to']} ({payload['duration_iso']}).")
    if payload["scheduled_events"]:
        parts.append("Events: " + ", ".join(payload["scheduled_events"]))
    if payload.get("shared_events"):
        for e in payload["shared_events"]:
            parts.append(f"Together ({', '.join(e['participants'])}): {e['summary']}")
    if payload["npc_summaries"]:
        for s in payload["npc_summaries"]:
            acts = s["activities"]
            line = f"- {s['character_id']}: " + ("; ".join(acts) if acts else "no activity")
            if s.get("degraded"):
                line += " [tick failed; defaults applied]"
            parts.append(line)
    if payload["faction_summaries"]:
        parts.append(
            "Factions: " + ", ".join(s["faction_id"] for s in payload["faction_summaries"])
        )
    if payload.get("faction_conflicts"):
        parts.append("Conflicts: " + "; ".join(payload["faction_conflicts"]))
    if payload["weather_changes"]:
        parts.append("Weather: " + "; ".join(payload["weather_changes"]))
    if payload["overdue"]:
        parts.append("Overdue commitments: " + ", ".join(payload["overdue"]))
    if payload["stale"]:
        parts.append("Stale commitments: " + ", ".join(payload["stale"]))
    if payload.get("upcoming"):
        parts.append("Imminent: " + ", ".join(payload["upcoming"]))
    return "\n".join(parts)


def _npc_summary_from_payload(
    p: _PresentCharacter,
    duration: Duration,
    payload: dict[str, Any],
    *,
    degraded: bool = False,
) -> NpcTickSummary:
    state_at_end: dict[str, Any] = {}
    loc = payload.get("location_at_end")
    if loc:
        state_at_end["location"] = loc
    mood = payload.get("mood_at_end")
    if mood:
        state_at_end["mood"] = mood
    return NpcTickSummary(
        character_id=p.asset_id,
        duration=duration,
        state_at_end=state_at_end,
        activities=[str(a) for a in (payload.get("activities") or [])],
        relationships_changed=list(payload.get("relationship_changes") or []),
        new_facts_about_them=list(payload.get("new_facts") or []),
        secrets_kept=[str(s) for s in (payload.get("secrets_kept") or [])],
        next_intent=str(payload.get("next_intent") or ""),
        should_seek_pc=bool(payload.get("should_seek_pc") or False),
        events_pc_would_witness=[str(e) for e in (payload.get("events_pc_would_witness") or [])],
        degraded=degraded,
    )


def _split_faction_ref(ref: str) -> tuple[str | None, str | None]:
    """``library:worlds/<s>/factions/<id>`` → ``(s, id)``."""
    if not ref or not ref.startswith("library:"):
        return None, None
    _, _, path = ref.partition("library:")
    parts = path.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"factions", "faction"}:
        return parts[1], parts[3]
    return None, None


def _split_location_ref(ref: str) -> tuple[str | None, str | None]:
    """``library:worlds/<s>/locations/<id>`` → ``(s, id)``."""
    if not ref:
        return None, None
    if not ref.startswith("library:"):
        return None, None
    _, _, path = ref.partition("library:")
    parts = path.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"locations", "location"}:
        return parts[1], parts[3]
    return None, None


def _scheduled_event_from_row(row: Any, *, triggered: bool | None = None) -> ScheduledEvent:
    payload = row["payload"]
    try:
        decoded = json.loads(payload) if payload else {}
    except (TypeError, json.JSONDecodeError):
        decoded = {}
    moment = parse_iso_datetime(row["at"])
    if moment is None:
        # Defensive: an unparsable timestamp is treated as the epoch so the
        # event can still be inspected by the caller.
        moment = datetime(1970, 1, 1, tzinfo=UTC)
    return ScheduledEvent(
        id=row["id"],
        campaign_id=row["campaign_id"],
        at=InGameTime(moment=moment),
        label=row["label"] or "",
        kind=row["kind"] or "",
        payload=decoded if isinstance(decoded, dict) else {},
        triggered=bool(row["triggered"]) if triggered is None else triggered,
    )


def _shared_commitment(
    c: Any,
    campaign_id: str,
    epoch: datetime | None,
    anchor: InGameTime,
) -> Any:
    """Project a Continuity commitment dataclass into the shared ``Commitment``.

    Continuity tracks time as whole days from the campaign epoch (see
    ``grimoire.continuity.types.InGameTime``). The shared ``Commitment`` model
    expects datetime-based ``InGameTime`` values, so we walk the day count
    back through the same epoch the engine used for aging.
    """
    from grimoire.types.continuity import Commitment as SharedCommitment
    from grimoire.types.continuity import CommitmentKind, CommitmentStatus

    base = epoch or datetime(1970, 1, 1, tzinfo=UTC)
    if anchor.moment.tzinfo is not None and base.tzinfo is None:
        base = base.replace(tzinfo=anchor.moment.tzinfo)

    def _days_to_time(days: int) -> InGameTime:
        return InGameTime(moment=base + timedelta(days=days), calendar_id=anchor.calendar_id)

    try:
        kind = CommitmentKind(str(c.kind.value if hasattr(c.kind, "value") else c.kind))
    except ValueError:
        kind = CommitmentKind.OBLIGATION
    try:
        status = CommitmentStatus(str(c.status.value if hasattr(c.status, "value") else c.status))
    except ValueError:
        status = CommitmentStatus.OPEN

    return SharedCommitment(
        id=c.id,
        campaign_id=campaign_id,
        kind=kind,
        text=c.text,
        created_in_post=c.created_in_post or None,
        in_game_created_at=_days_to_time(c.in_game_created_at.day_count),
        from_id=c.from_id,
        to_id=c.to_id,
        due_by=_days_to_time(c.due_by.day_count) if c.due_by is not None else None,
        status=status,
        weight=c.weight,
        resolved_in_post=c.resolved_in_post,
        tags=list(c.tags or []),
        related_fact_ids=list(c.related_fact_ids or []),
    )


__all__ = ["TimeEngineService"]
