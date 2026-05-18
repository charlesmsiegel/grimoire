"""Orchestrator subscriber adapter (spec 07 §Triggers, remaining-design §1).

Wires the Time Engine to the orchestrator's ``turn_complete`` event. The
orchestrator emits ``turn_complete`` with a ``time_advances`` field on the
payload listing the per-turn TIME_ADVANCE deltas the extractor produced.
This adapter takes that list, sums the durations, maps the result to a
``TimeAdvanceReason.SCENE_NARRATION`` advance, and calls
``TimeEngineService.advance``.

The companion change in the orchestrator pulls TIME_ADVANCE deltas out of
the in-progress extraction result (before they get applied to the store)
and forwards them on the bus payload, so this subscriber doesn't have to
re-read the delta log.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.types.common import Duration
from grimoire.types.state import DeltaKind, StateDelta
from grimoire.types.time import TimeAdvanceReason

logger = logging.getLogger(__name__)


_ISO_DURATION_RE = re.compile(
    r"^(?P<sign>-)?P"
    r"(?:(?P<weeks>\d+)W)?"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)


def _iso_to_timedelta(iso: str) -> timedelta:
    """Tiny ISO-8601 duration parser covering the days/hours/minutes/seconds
    forms the extractor emits. Months/years aren't supported because
    ``timedelta`` can't represent them exactly; callers can pass a Duration
    with an explicit ``delta`` for those."""
    m = _ISO_DURATION_RE.match(iso)
    if not m:
        return timedelta()
    parts = m.groupdict()
    sign = -1 if parts.get("sign") else 1
    weeks = int(parts.get("weeks") or 0)
    days = int(parts.get("days") or 0)
    hours = int(parts.get("hours") or 0)
    minutes = int(parts.get("minutes") or 0)
    seconds = float(parts.get("seconds") or 0)
    return sign * timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def _ensure_duration_delta(duration: Duration) -> Duration:
    """Fill in ``delta`` when the input was constructed from just an ISO string."""
    if duration.delta.total_seconds() != 0:
        return duration
    parsed = _iso_to_timedelta(duration.iso8601)
    if parsed.total_seconds() == 0:
        return duration
    return Duration(iso8601=duration.iso8601, delta=parsed)


def extract_time_advances_from_deltas(
    deltas: list[StateDelta] | list[Any] | None,
) -> list[Duration]:
    """Pull the duration off each TIME_ADVANCE delta from an extraction.

    The orchestrator calls this on the raw extraction (a list of
    ``StateDelta`` objects, or dicts when the extractor strategy is
    rule-based) and forwards the result on the ``turn_complete`` bus
    payload so this module's subscriber can drive a real advance.
    """
    out: list[Duration] = []
    for d in deltas or []:
        if isinstance(d, StateDelta):
            if d.kind != DeltaKind.TIME_ADVANCE:
                continue
            after = d.after or {}
            duration_blob = after.get("duration") if isinstance(after, dict) else None
        elif isinstance(d, dict):
            kind = d.get("kind")
            kind_val = kind.value if hasattr(kind, "value") else kind
            if kind_val != DeltaKind.TIME_ADVANCE.value:
                continue
            after = d.get("after") or {}
            duration_blob = after.get("duration") if isinstance(after, dict) else None
        else:
            continue
        if duration_blob is None:
            continue
        try:
            if isinstance(duration_blob, Duration):
                out.append(_ensure_duration_delta(duration_blob))
            elif isinstance(duration_blob, dict):
                out.append(_ensure_duration_delta(Duration.model_validate(duration_blob)))
            elif isinstance(duration_blob, str):
                out.append(_ensure_duration_delta(Duration(iso8601=duration_blob)))
        except Exception:  # pragma: no cover - defensive
            logger.exception("could not decode duration %r", duration_blob)
            continue
    return out


def _sum_durations(durations: list[Duration]) -> Duration | None:
    if not durations:
        return None
    from .service import _duration_from_timedelta  # local import to avoid cycle

    total = durations[0].delta
    for d in durations[1:]:
        total += d.delta
    if total.total_seconds() <= 0:
        return None
    return _duration_from_timedelta(total)


class TimeEngineSubscriber:
    """Subscribes to ``turn_complete`` and triggers Time Engine advances.

    Construct once per application; call :meth:`start` to attach to the
    bus. Keep the returned subscription handle on the application
    container so the lifespan teardown can disengage it.
    """

    def __init__(self, *, time_engine: Any, event_bus: EventBus) -> None:
        self._engine = time_engine
        self._bus = event_bus
        self._subscription: Any | None = None

    def start(self) -> Any:
        if self._subscription is not None:
            return self._subscription
        self._subscription = self._bus.subscribe("turn_complete", self._handle)
        return self._subscription

    def stop(self) -> None:
        if self._subscription is None:
            return
        try:
            self._subscription.unsubscribe()
        finally:
            self._subscription = None

    async def _handle(self, event: Event) -> None:
        payload = event.payload or {}
        campaign_id = payload.get("campaign_id")
        if not campaign_id:
            return
        time_advances = payload.get("time_advances") or []
        durations: list[Duration] = []
        for entry in time_advances:
            if isinstance(entry, Duration):
                durations.append(entry)
                continue
            if isinstance(entry, dict):
                blob = entry.get("duration") if "duration" in entry else entry
                try:
                    if isinstance(blob, Duration):
                        durations.append(_ensure_duration_delta(blob))
                    elif isinstance(blob, dict):
                        durations.append(_ensure_duration_delta(Duration.model_validate(blob)))
                    elif isinstance(blob, str):
                        durations.append(_ensure_duration_delta(Duration(iso8601=blob)))
                except Exception:  # pragma: no cover - defensive
                    logger.warning("turn_complete carried undecodable duration %r", blob)
                continue
            if isinstance(entry, str):
                try:
                    durations.append(_ensure_duration_delta(Duration(iso8601=entry)))
                except Exception:  # pragma: no cover - defensive
                    logger.warning("turn_complete carried bad ISO duration %r", entry)
        total = _sum_durations(durations)
        if total is None:
            return
        scene_id = payload.get("scene_id")
        branch_id = payload.get("branch_id")
        try:
            await self._engine.advance(
                campaign_id,
                total,
                TimeAdvanceReason.SCENE_NARRATION,
                scene_id=scene_id,
                branch_id=branch_id,
            )
        except Exception:
            logger.exception(
                "TimeEngineSubscriber: advance failed for campaign=%s duration=%s",
                campaign_id,
                total.iso8601,
            )


__all__ = ["TimeEngineSubscriber", "extract_time_advances_from_deltas"]
