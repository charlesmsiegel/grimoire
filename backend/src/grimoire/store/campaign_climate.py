"""The campaign's default climate: `campaigns/<cid>/climate.json` (#237).

One file, one owner. This was the only campaign-level JSON in the store whose
shape — `{"default_climate": "<id>"}` — was spelled out at every call site
instead: create, update, the weather resolver and the climate-referrer scan,
two of them doing filesystem IO from inside the HTTP layer. A schema change
had to be found by grep, and the four readers had drifted into disagreeing
about what a missing or malformed file means.

The split that survives that consolidation is between the two audiences, not
between the layers:

- `read_default` / `resolve_default` are **lenient and never raise**. They run
  inside prompt assembly, where anything that raises takes a turn down, and
  this file is hand-editable.
- `write_default` is **strict**. It is reached only where a user is present to
  be told, which makes it the only place a mistake can be reported at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, campaigns, climates

FILENAME = "climate.json"
KEY = "default_climate"


def path(cid: str) -> Path:
    """The campaign's climate file (`weather/overrides.py` is the model).

    Campaign-local, deliberately: the weather design *cut* a world-level
    default rather than forgetting one (`docs/superpowers/specs/
    2026-07-27-weather-design.md`), so there is nothing above this to inherit
    and `campaign_root` is the whole resolution. Should a world tier ever
    arrive, it arrives here — not in the call sites that used to reach for
    `campaign_root / "climate.json"` themselves, which is how the overlay gets
    bypassed everywhere else in this codebase.
    """
    return campaigns.campaign_root(cid) / FILENAME


def read_default(cid: str) -> str | None:
    """The configured climate id, or None when unset. Never raises.

    Every unusable shape reads as unset rather than as an error: no file (every
    campaign predating the weather work), a truncated hand edit, a JSON scalar
    (`json.loads("7").get` is an AttributeError), a non-string id (a list is
    truthy and would raise `TypeError: unhashable` inside the registry lookup),
    or an empty string.
    """
    try:
        raw = json.loads(path(cid).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    wanted = raw.get(KEY) if isinstance(raw, dict) else None
    return wanted if isinstance(wanted, str) and wanted else None


def resolve_default(cid: str) -> dict:
    """The climate document every untagged location falls back to.

    The shipped preset when the id is unset or dangling — a climate deleted
    after the fact must not break a turn.
    """
    wanted = read_default(cid)
    return (climates.get(wanted) if wanted else None) or climates.get(climates.FALLBACK_ID)


def check_default(climate_id: str) -> None:
    """Raise `ClimateError` unless the registry knows `climate_id`.

    Separate from `write_default` for one caller: campaign creation must reject
    a bad climate *before* it makes a directory, and the file it would be
    written to does not exist yet at that point. Sharing this keeps the
    create-time and update-time rejections from drifting apart.

    So creation validates twice. The alternative is a write path that skips
    validation, and the whole point of this module is that there isn't one; a
    second registry scan is cheap, and creation already fails late elsewhere
    (an invalid calendar raises after the directory exists).
    """
    if climates.get(climate_id) is None:
        raise climates.ClimateError(f"unknown climate: {climate_id!r}")


def write_default(cid: str, climate_id: str) -> str:
    """Point the campaign at `climate_id`, rejecting one the registry lacks.

    Resolver leniency covers files that go dangling later; nothing should be
    able to write a dangling reference in the first place. A misspelled default
    silently moves *every* untagged location in the campaign.
    """
    check_default(climate_id)
    atomic.write_text(path(cid), json.dumps({KEY: climate_id}))
    return climate_id
