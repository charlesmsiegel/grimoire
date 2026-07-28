"""Weather for a campaign moment.

Pure where it can be: the procedural draw stores nothing, and any block
resolves in O(1) whatever the campaign's age. Overrides layer on top of that
draw per *axis*, so a manual `condition` and a procedural wind coexist — which
is what narration usually gives us, since "it was raining" says nothing about
wind.
"""

from __future__ import annotations

from . import blocks, draw as _draw, overrides, seasons, settings
from .. import calendars, campaigns

AXES = overrides.AXES


def _moment(cid: str, native: str):
    """(provider, fixed_day, minutes) for a native moment, or None.

    read_calendar is inside the guard: it catches JSONDecodeError but not a
    valid-JSON non-object, so a hand-edited `calendar.json` containing `[]`
    reaches `raw.get(...)` and raises AttributeError.
    """
    try:
        cfg = calendars.read_calendar(campaigns.campaign_root(cid))
        provider = calendars.get_provider(cfg["primary"])
        return provider, calendars.fixed_of(provider, native), calendars.minutes_of(native)
    except (calendars.CalendarError, KeyError, TypeError, AttributeError, OSError):
        return None


def resolve(cid: str, location_id: str | None, native: str | None) -> dict | None:
    """The full resolution, including per-axis provenance and covering stack.

    `current_weather` is the narrow view of this for prompt assembly; the HUD
    wants the rest. None covers three real cases, none of which may raise: a
    scene with no location, a scene with no moment, and a stored moment the
    campaign's current calendar can no longer parse — which happens when the
    primary provider is switched after scenes exist.
    """
    if not location_id or not native:
        return None
    moment = _moment(cid, native)
    if moment is None:
        return None
    provider, fixed, minutes = moment

    owning_day, _ = blocks.block_of(fixed, minutes)
    ordinal = blocks.ordinal(fixed, minutes)

    resolved = settings.resolve(cid, location_id)
    climate = resolved["climate"]
    # Season comes from the block's owning date, not the queried moment: a
    # night spans midnight and may span a season boundary, and one block must
    # not render two different skies depending which minute inside it is asked.
    #
    # ValueError is in the net because the owning date can fall one day below
    # the provider's range: 0001-01-01T01:00 parses fine, but its block is the
    # previous date's night, and `gregorian.describe` calls `date.fromordinal`,
    # which rejects day 0.
    try:
        fraction = seasons.year_fraction(provider, owning_day)
    except (calendars.CalendarError, ValueError, OverflowError):
        return None
    season = seasons.season_for(climate, fraction)

    procedural = _draw.draw(cid, resolved["zone"], season, resolved["persistence"], ordinal)
    spans = overrides.read(cid)

    axes, source = {}, {}
    for axis in AXES:
        got = overrides.winner(spans, location_id, ordinal, axis)
        if got is None or got[0] == "suppress":
            axes[axis] = procedural[axis]
            source[axis] = "procedural"
        else:
            axes[axis] = got[1][axis]
            source[axis] = got[1].get("source") or "manual"

    return {**axes, "climate": climate["id"], "season": season["name"],
            "source": source, "procedural": procedural,
            "stack": overrides.stack(spans, location_id, ordinal),
            "ordinal": ordinal, "zone": resolved["zone"],
            "persistence": resolved["persistence"], "tables": season}


def current_weather(cid: str, location_id: str | None, native: str | None) -> dict | None:
    """Resolved weather, or None when there is nothing to resolve.

    The prompt-assembly view: three axes plus the climate and season that
    produced them. Callers treat None as "no weather section".
    """
    got = resolve(cid, location_id, native)
    if got is None:
        return None
    return {**{a: got[a] for a in AXES}, "climate": got["climate"], "season": got["season"]}


def sweep(cid: str, sid: str, prev_native: str | None, now_native: str | None) -> list[dict]:
    """What changed, per location and axis, between two moments.

    Both the previous moment and the scene are required: `set_datetime` permits
    arbitrary jumps including backward ones and keeps a separate history per
    scene, so a sweep that knows only the new "now" cannot say what changed.

    Scope is the distinct locations in *that scene's* history — the places the
    story has actually visited — rather than every location in the campaign,
    which would scale with the world rather than the story and fill the digest
    with weather nobody is looking at.

    Only the two end blocks are compared, never the ones between: an advance of
    a month should report what is different now, not narrate the intervening
    weather. Generation is pure, so the changes mostly fall out for free; this
    exists to *name* the transitions for the digest, and stores nothing.
    """
    if not prev_native or not now_native:
        return []
    from .. import scenes
    seen, ordered = set(), []
    for lid in scenes.get_location_history(cid, sid):
        if lid not in seen:
            seen.add(lid)
            ordered.append(lid)

    out = []
    for lid in ordered:
        before = current_weather(cid, lid, prev_native)
        after = current_weather(cid, lid, now_native)
        if before is None or after is None:
            continue
        for axis in AXES:
            if before[axis] != after[axis]:
                out.append({"location": lid, "axis": axis,
                            "before": before[axis], "after": after[axis]})
    return out
