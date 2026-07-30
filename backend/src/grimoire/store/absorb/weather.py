"""Narrated weather: staging it as before/after rows, and writing an approved
row back as an `extractor` override span.

Both halves share the span rule and the `weather` store's axis vocabulary, so
they sit together: `materializer.materialize` calls `_weather_edits` and
`apply.apply_edits` calls `_apply_weather`.
"""

from __future__ import annotations

from .. import calendars, entities, overlay, weather as weather_store
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read


def _weather_edits(cid: str, sid: str, parsed: dict) -> list[dict]:
    """Narrated weather as staged before/after rows, one per changed axis.

    Narration gives a value, not a span, so the span rule is: **default to the
    block containing the narrated moment**, and honour an explicit duration
    when the narration states one ("the rain set in for three days"), rounded
    outward to whole blocks. Narration that implies onset rather than extent
    ("rain begins") takes the default — one block, re-narratable next turn.
    """
    rows = parsed.get("weather_edits") or []
    if not rows:
        return []
    moments = scenes_read.get_time_history(cid, sid)
    native = moments[-1] if moments else None
    if not native:
        return []  # no moment means no block to pin the span to
    history = scenes_read.get_location_history(cid, sid)
    scene_location = history[-1] if history else None

    out = []
    for e in rows:
        location = (e.get("location") or "").strip() or scene_location
        if not location:
            continue
        if location != scene_location:
            # The model can emit a misspelled id or a display name, and
            # `current_weather` answers for *any* id — a deleted location keeps
            # resolving on purpose, so it cannot tell a typo from a tombstone.
            # Without this the edit stages, applies, and lands under an orphan
            # weather.json key no scene can reach, against materialize's stated
            # contract of dropping targets that do not exist.
            try:
                overlay.read_entity(cid, "locations", location)
            except (entities.EntityNotFound, KeyError, OSError):
                continue
        resolved = weather_store.current_weather(cid, location, native)
        if resolved is None:
            continue  # unparseable moment: the same case the resolver declines
        for axis in weather_store.AXES:
            after = (e.get(axis) or "").strip()
            if not after or after == resolved[axis]:
                continue
            out.append({
                "id": f"weather:{location}:{axis}", "kind": "weather",
                "target": {"kind": "weather", "id": location},
                "label": f"Weather at {location} — {axis}",
                "field": axis, "before": resolved[axis], "after": after,
                "authored": False,
                "payload": {"location": location, "axis": axis, "native": native,
                            "duration_blocks": e.get("duration_blocks", ""),
                            "note": e.get("note", "")},
            })
    return out


def _apply_weather(cid: str, edit: dict, after: str) -> bool:
    """Write one narrated axis as an `extractor` override span.

    Returns whether anything was written. The campaign calendar can change
    between staging a proposal and approving it, at which point the stored
    native moment no longer parses — and reporting that edit as applied would
    tell the user their weather landed when no override exists.

    The span covers the block containing the narrated moment, extended by an
    explicit `duration_blocks` when the narration stated an extent. Endpoints
    round outward to whole blocks, which `overrides.put` does by resolving them
    through the block grid rather than through raw minutes.
    """
    payload = edit.get("payload") or {}
    native, axis = payload.get("native"), edit.get("field")
    location = payload.get("location")
    if not native or axis not in weather_store.AXES or not location:
        return False
    try:
        cfg = calendars.read_calendar(campaigns_paths.campaign_root(cid))
        provider = calendars.get_provider(cfg["primary"])
        fixed = calendars.fixed_of(provider, native)
        minutes = calendars.minutes_of(native)
    except (calendars.CalendarError, KeyError, TypeError, AttributeError, OSError):
        return False
    start = weather_store.blocks.ordinal(fixed, minutes)
    try:
        span = max(1, int(payload.get("duration_blocks") or 1))
    except (TypeError, ValueError):
        span = 1
    end = start + span
    weather_store.overrides.put_ordinals(
        cid, location, native, start, end, {axis: after},
        note=str(payload.get("note") or ""), source="extractor")
    return True
