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
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire.characters import CharactersService
from grimoire.continuity.protocols import Continuity
from grimoire.continuity.types import InGameTime as ContinuityInGameTime
from grimoire.event_bus import Event, EventBus
from grimoire.mechanics.service import MechanicsService
from grimoire.state_store import StateStore
from grimoire.types.characters import CharacterRole, ResolvedCharacter
from grimoire.types.common import CampaignId, CharacterRef, Duration, EventId, InGameTime
from grimoire.types.state import StateDelta
from grimoire.types.time import (
    FactionTickSummary,
    NpcTickSummary,
    ScheduledEvent,
    TimeAdvanceReason,
    TimeAdvanceResult,
    WeatherChange,
)
from grimoire.types.world import WorldCalendar
from grimoire.world import WorldService

from .config import TimeEngineConfig
from .errors import InvalidSkipError, TimeNotSetError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable adapters
# ---------------------------------------------------------------------------

NpcTickInput = dict[str, Any]
NpcTickPayload = dict[str, Any]
NpcTickFn = Callable[[NpcTickInput], Awaitable[NpcTickPayload]]
DigestFn = Callable[[dict[str, Any]], Awaitable[str]]


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _branch_for(campaign_id: str, branch_id: str | None) -> str:
    return branch_id or f"{campaign_id}:main"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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

    # ------------------------------------------------------------------ #
    # Time accessors
    # ------------------------------------------------------------------ #

    async def current(
        self,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
    ) -> InGameTime | None:
        """Return the current in-game time, or ``None`` if never set."""
        branch = _branch_for(campaign_id, branch_id)
        row = await self._store.db.fetchone(
            "SELECT current_in_game_time FROM calendar WHERE branch_id = ?",
            (branch,),
        )
        if row is None:
            return None
        moment = _parse_dt(row["current_in_game_time"])
        if moment is None:
            return None
        return InGameTime(moment=moment)

    async def set_current(
        self,
        campaign_id: CampaignId,
        when: InGameTime,
        *,
        branch_id: str | None = None,
    ) -> None:
        """Overwrite the campaign's clock without running ticks.

        Used by campaign creation and tests; the regular advancement path is
        :meth:`advance` / :meth:`skip_to`, which both go through this method
        after running the rest of the pipeline.
        """
        branch = _branch_for(campaign_id, branch_id)
        await self._store.db.execute(
            """
            INSERT INTO calendar (campaign_id, branch_id, current_in_game_time)
            VALUES (?, ?, ?)
            ON CONFLICT(branch_id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              current_in_game_time = excluded.current_in_game_time
            """,
            (campaign_id, branch, when.moment.isoformat()),
        )

    async def calendar(self, campaign_id: CampaignId) -> WorldCalendar:
        """The active calendar for the campaign (highest-priority world)."""
        return await self._world.calendar_for_campaign(campaign_id)

    # ------------------------------------------------------------------ #
    # Scheduled events
    # ------------------------------------------------------------------ #

    async def schedule_event(
        self, event: ScheduledEvent, *, branch_id: str | None = None
    ) -> EventId:
        """Persist a scheduled event. Returns the (possibly generated) id."""
        eid = event.id or _new_id("evt")
        await self._store.db.execute(
            """
            INSERT INTO scheduled_events (
              id, campaign_id, branch_id, at, kind, label, payload,
              triggered, triggered_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
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
                _branch_for(event.campaign_id, branch_id),
                event.at.moment.isoformat(),
                event.kind,
                event.label,
                json.dumps(event.payload or {}, default=str),
                1 if event.triggered else 0,
                _now_iso(),
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
        *,
        branch_id: str | None = None,
    ) -> list[ScheduledEvent]:
        """List pending events within ``within`` of the current time.

        With ``within=None`` returns every future, non-triggered event for
        the campaign.
        """
        branch = _branch_for(campaign_id, branch_id)
        current = await self.current(campaign_id, branch_id=branch)
        upper: str | None = None
        if within is not None and current is not None:
            upper = (current.moment + within.delta).isoformat()

        if upper is None:
            rows = await self._store.db.fetchall(
                """
                SELECT * FROM scheduled_events
                WHERE campaign_id = ? AND branch_id = ? AND triggered = 0
                ORDER BY at ASC
                """,
                (campaign_id, branch),
            )
        else:
            lower = (current.moment.isoformat()) if current is not None else "0001-01-01T00:00:00"
            rows = await self._store.db.fetchall(
                """
                SELECT * FROM scheduled_events
                WHERE campaign_id = ? AND branch_id = ? AND triggered = 0
                  AND at >= ? AND at <= ?
                ORDER BY at ASC
                """,
                (campaign_id, branch, lower, upper),
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
        branch_id: str | None = None,
        from_time: InGameTime | None = None,
    ) -> TimeAdvanceResult:
        """Advance the campaign clock by ``duration`` and run ticks.

        ``from_time`` overrides the stored value, useful for one-off
        backfills. Otherwise the campaign's current clock is required.
        """
        branch = _branch_for(campaign_id, branch_id)
        start = from_time or await self.current(campaign_id, branch_id=branch)
        if start is None:
            raise TimeNotSetError(
                f"campaign {campaign_id!r} has no in-game time yet; "
                "set one (e.g. via set_current) before advancing"
            )
        to = InGameTime(
            moment=start.moment + duration.delta,
            calendar_id=start.calendar_id,
        )
        return await self._run_pipeline(
            campaign_id=campaign_id,
            branch_id=branch,
            scene_id=scene_id,
            reason=reason,
            from_time=start,
            to_time=to,
            duration=duration,
        )

    async def skip_to(
        self,
        campaign_id: CampaignId,
        target: InGameTime,
        reason: TimeAdvanceReason,
        *,
        scene_id: str | None = None,
        branch_id: str | None = None,
        from_time: InGameTime | None = None,
    ) -> TimeAdvanceResult:
        """Advance to ``target`` (which must be strictly later than now)."""
        branch = _branch_for(campaign_id, branch_id)
        start = from_time or await self.current(campaign_id, branch_id=branch)
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
        delta = target.moment - start.moment
        duration = _duration_from_timedelta(delta)
        return await self._run_pipeline(
            campaign_id=campaign_id,
            branch_id=branch,
            scene_id=scene_id,
            reason=reason,
            from_time=start,
            to_time=target,
            duration=duration,
        )

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #

    async def _run_pipeline(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str,
        scene_id: str | None,
        reason: TimeAdvanceReason,
        from_time: InGameTime,
        to_time: InGameTime,
        duration: Duration,
    ) -> TimeAdvanceResult:
        # The order matters: scheduled events first so NPC ticks see the
        # post-event state; ticks before commitment aging so a NPC who pays
        # off a thread does it before the aging sweep catches the
        # commitment as overdue.
        triggered = await self._fire_scheduled_events(
            campaign_id=campaign_id,
            branch_id=branch_id,
            from_time=from_time,
            to_time=to_time,
        )

        ticked = await self._significant_npcs(campaign_id=campaign_id, branch_id=branch_id)
        npc_summaries = await self._run_npc_ticks(
            campaign_id=campaign_id,
            duration=duration,
            present=ticked,
            from_time=from_time,
            to_time=to_time,
        )

        faction_summaries = await self._run_faction_ticks(
            campaign_id=campaign_id,
            branch_id=branch_id,
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
            branch_id=branch_id,
            duration=duration,
            present=ticked,
        )

        epoch = await self._epoch_for(campaign_id)
        aging = await self._continuity.age(_to_continuity_time(to_time, epoch))
        commit_anchor = to_time

        await self.set_current(campaign_id, to_time, branch_id=branch_id)

        digest_payload: dict[str, Any] = {
            "from": from_time.moment.isoformat(),
            "to": to_time.moment.isoformat(),
            "duration_iso": duration.iso8601,
            "reason": reason.value if hasattr(reason, "value") else str(reason),
            "scheduled_events": [e.label for e in triggered],
            "npc_summaries": [
                {"character_id": s.character_id, "activities": s.activities}
                for s in npc_summaries.values()
            ],
            "faction_summaries": [
                {"faction_id": s.faction_id, "notable_actions": s.notable_actions}
                for s in faction_summaries.values()
            ],
            "weather_changes": [w.summary for w in weather_changes],
            "overdue": [c.id for c in aging.became_overdue],
            "stale": [c.id for c in aging.became_stale],
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
            _shared_commitment(c, campaign_id, branch_id, epoch, commit_anchor)
            for c in aging.became_overdue
        ]
        commitments_due = [
            _shared_commitment(c, campaign_id, branch_id, epoch, commit_anchor)
            for c in aging.became_stale
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
        )

        await self._emit(
            "time_advance",
            {
                "campaign_id": campaign_id,
                "branch_id": branch_id,
                "scene_id": scene_id,
                "reason": digest_payload["reason"],
                "from": digest_payload["from"],
                "to": digest_payload["to"],
                "duration_iso": duration.iso8601,
                "npcs_ticked": list(npc_summaries.keys()),
                "scheduled_events_triggered": [e.id for e in triggered],
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
        branch_id: str,
        from_time: InGameTime,
        to_time: InGameTime,
    ) -> list[ScheduledEvent]:
        lo, hi = from_time.moment, to_time.moment
        if hi < lo:
            lo, hi = hi, lo
        rows = await self._store.db.fetchall(
            """
            SELECT * FROM scheduled_events
            WHERE campaign_id = ? AND branch_id = ?
              AND triggered = 0 AND at > ? AND at <= ?
            ORDER BY at ASC
            """,
            (campaign_id, branch_id, lo.isoformat(), hi.isoformat()),
        )
        triggered: list[ScheduledEvent] = []
        now_iso = _now_iso()
        for row in rows:
            triggered.append(_scheduled_event_from_row(row, triggered=True))
            await self._store.db.execute(
                "UPDATE scheduled_events SET triggered = 1, triggered_at = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
        return triggered

    async def _significant_npcs(
        self, *, campaign_id: CampaignId, branch_id: str
    ) -> list[_PresentCharacter]:
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
            opens = await self._continuity.open_commitments(
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
                branch_id=branch_id,
                limit=cfg.recent_post_window,
            )
            for ref in recent:
                if ref in by_ref and not by_ref[ref].is_pc:
                    kept.setdefault(ref, by_ref[ref])

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
    ) -> dict[str, NpcTickSummary]:
        if not present:
            return {}
        semaphore = asyncio.Semaphore(max(1, self._config.npc_tick_parallelism))

        async def _one(p: _PresentCharacter) -> tuple[str, NpcTickSummary]:
            async with semaphore:
                payload: NpcTickInput = {
                    "campaign_id": campaign_id,
                    "character_ref": p.ref,
                    "character_id": p.asset_id,
                    "role": p.role.value,
                    "duration_iso": duration.iso8601,
                    "from": from_time.moment.isoformat(),
                    "to": to_time.moment.isoformat(),
                    "location_ref": p.location_ref,
                }
                try:
                    result = await self._npc_tick_fn(payload)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("npc_tick callable raised for %s", p.ref)
                    result = await _default_npc_tick(payload)
                summary = _npc_summary_from_payload(p, duration, result)
                await self._emit(
                    "npc_tick_complete",
                    {
                        "campaign_id": campaign_id,
                        "character_ref": p.ref,
                        "activities": summary.activities,
                        "duration_iso": duration.iso8601,
                    },
                )
                return p.asset_id, summary

        results = await asyncio.gather(*[_one(p) for p in present])
        return dict(results)

    async def _run_faction_ticks(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str,
        duration: Duration,
    ) -> dict[str, FactionTickSummary]:
        # Faction ticks are intentionally coarse: spec calls for month-level
        # granularity. Anything shorter than that doesn't run.
        if duration.delta < self._config.faction_tick_resolution:
            return {}
        rows = await self._store.db.fetchall(
            """
            SELECT * FROM faction_state
            WHERE campaign_id = ? AND branch_id = ?
            """,
            (campaign_id, branch_id),
        )
        out: dict[str, FactionTickSummary] = {}
        months = max(1, int(duration.delta.days // 30))
        for row in rows:
            faction_ref = row["faction_ref"]
            try:
                state = json.loads(row["state"]) if row["state"] else {}
            except (TypeError, json.JSONDecodeError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            goals = state.get("goals") or []
            goal_progress: dict[str, float] = {}
            for g in goals:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("id") or "")
                prev = float(g.get("progress") or 0.0)
                # Default: 1% progress per month, capped at 1.0.
                new_progress = min(1.0, prev + 0.01 * months)
                g["progress"] = new_progress
                goal_progress[gid] = new_progress
            state["goals"] = goals
            await self._store.db.execute(
                """
                UPDATE faction_state SET state = ?
                WHERE faction_ref = ? AND branch_id = ?
                """,
                (json.dumps(state, default=str), faction_ref, branch_id),
            )
            out[faction_ref] = FactionTickSummary(
                faction_id=faction_ref,
                duration=duration,
                goal_progress=goal_progress,
                resource_changes={},
                notable_actions=[],
            )
        return out

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
                w_before = await self._world.weather_for(
                    world_id, asset_id, from_time, campaign_id
                )
                w_after = await self._world.weather_for(
                    world_id, asset_id, to_time, campaign_id
                )
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
        branch_id: str,
        duration: Duration,
        present: list[_PresentCharacter],
    ) -> list[StateDelta]:
        """Fan out ``Mechanics.time_tick`` per character.

        With ``mechanics: null`` the call is cheap (returns empty), so we
        don't gate on the module here; the service handles it.
        """
        out: list[StateDelta] = []
        for p in present:
            try:
                deltas = await self._mechanics.time_tick(
                    campaign_id=campaign_id,
                    entity_ref=f"character:{p.asset_id}",
                    duration=duration,
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
        branch_id: str,
        limit: int,
    ) -> list[str]:
        rows = await self._store.db.fetchall(
            """
            SELECT author_pc_ref FROM posts
            WHERE campaign_id = ? AND branch_id = ?
            ORDER BY created_at DESC, order_in_scene DESC
            LIMIT ?
            """,
            (campaign_id, branch_id, limit),
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


def _structured_digest(payload: dict[str, Any]) -> str:
    """Build a compact human-readable structured digest from the payload."""
    parts: list[str] = []
    parts.append(f"From {payload['from']} to {payload['to']} ({payload['duration_iso']}).")
    if payload["scheduled_events"]:
        parts.append("Events: " + ", ".join(payload["scheduled_events"]))
    if payload["npc_summaries"]:
        for s in payload["npc_summaries"]:
            acts = s["activities"]
            line = f"- {s['character_id']}: " + ("; ".join(acts) if acts else "no activity")
            parts.append(line)
    if payload["faction_summaries"]:
        parts.append(
            "Factions: " + ", ".join(s["faction_id"] for s in payload["faction_summaries"])
        )
    if payload["weather_changes"]:
        parts.append("Weather: " + "; ".join(payload["weather_changes"]))
    if payload["overdue"]:
        parts.append("Overdue commitments: " + ", ".join(payload["overdue"]))
    if payload["stale"]:
        parts.append("Stale commitments: " + ", ".join(payload["stale"]))
    return "\n".join(parts)


def _npc_summary_from_payload(
    p: _PresentCharacter, duration: Duration, payload: dict[str, Any]
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
    )


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
    moment = _parse_dt(row["at"])
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
    branch_id: str,
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
        branch_id=branch_id,
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
