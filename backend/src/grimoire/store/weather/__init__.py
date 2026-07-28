"""Weather for a campaign moment.

Pure: nothing is stored, and any block resolves in O(1) whatever the campaign's
age. Plan 2 layers manual and extractor overrides on top of the procedural draw
inside `current_weather` without changing this signature.
"""

from __future__ import annotations

from . import blocks, draw as _draw, seasons, settings
from .. import calendars, campaigns


def current_weather(cid: str, location_id: str | None, native: str | None) -> dict | None:
    """Resolved weather, or None when there is nothing to resolve.

    None covers three real cases, none of which may raise: a scene with no
    location, a scene with no moment, and a stored moment the campaign's
    current calendar can no longer parse — which happens when the primary
    provider is switched after scenes exist.
    """
    if not location_id or not native:
        return None

    # read_calendar is inside the guard: it catches JSONDecodeError but not a
    # valid-JSON non-object, so a hand-edited `calendar.json` containing `[]`
    # reaches `raw.get(...)` and raises AttributeError.
    try:
        cfg = calendars.read_calendar(campaigns.campaign_root(cid))
        provider = calendars.get_provider(cfg["primary"])
        fixed = calendars.fixed_of(provider, native)
        minutes = calendars.minutes_of(native)
    except (calendars.CalendarError, KeyError, TypeError, AttributeError, OSError):
        return None

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
    # which rejects day 0. Rare, but it would raise inside prompt assembly.
    try:
        fraction = seasons.year_fraction(provider, owning_day)
    except (calendars.CalendarError, ValueError, OverflowError):
        return None
    season = seasons.season_for(climate, fraction)

    axes = _draw.draw(cid, resolved["zone"], season, resolved["persistence"], ordinal)
    return {**axes, "climate": climate["id"], "season": season["name"]}
