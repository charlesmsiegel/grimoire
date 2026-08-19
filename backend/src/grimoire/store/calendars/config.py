"""Calendar config IO: <root>/calendar.json = {primary, secondary|None,
confirmed, stale_after_days}, each calendar block {provider, region,
custom_holidays, anchor}. World-scoped, copied into a campaign on create."""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic
from .base import CalendarError, get_provider

#: How long a thread or commitment may go untouched before the ledger calls it
#: stale (#103). It lives here, beside `confirmed`, because it is the one
#: campaign-level knob about *time* and this is the campaign's time config —
#: there is no general campaign config store, and the global `config.md` would
#: make one number serve a slow-burn chronicle and a three-night thriller
#: alike. Being in calendar.json also means a world can set the default its
#: campaigns are created with, since the file is copied on create.
STALE_AFTER_DAYS = 30


def _blank(region: str = "US") -> dict:
    return {"provider": "gregorian", "region": region, "custom_holidays": [], "anchor": None}


def default_calendar() -> dict:
    return {"primary": _blank(), "secondary": None, "confirmed": False,
            "stale_after_days": STALE_AFTER_DAYS}


def _stale_days(value) -> int:
    """A stored threshold as a positive whole number of days, or the default.

    Coerced rather than trusted, like every other field this module reads back:
    calendar.json is hand-editable, and a `"stale_after_days": "soon"` reaching
    `days_since >= threshold` would raise inside a ledger read — a comparison
    between an int and a str — and empty the section around it. Zero and
    negatives fall back too: a threshold of zero calls every dated record stale
    the day it is written, which is not a setting anybody means.

    `bool` is rejected before `int` accepts it: `True` is an `int` in Python and
    would silently become a one-day threshold.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return STALE_AFTER_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        return STALE_AFTER_DAYS
    return days if days > 0 else STALE_AFTER_DAYS


def _normalize_block(block: dict | None) -> dict | None:
    if not block:
        return None
    base = _blank()
    base.update({k: block[k] for k in ("provider", "region", "custom_holidays", "anchor") if k in block})
    base["custom_holidays"] = base["custom_holidays"] or []
    return base


def _path(root: Path) -> Path:
    return root / "calendar.json"


def read_calendar(root: Path) -> dict:
    p = _path(root)
    if not p.exists():
        return default_calendar()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_calendar()  # corrupt file — fall back rather than crash a render
    primary = _normalize_block(raw.get("primary")) or _blank()
    return {"primary": primary, "secondary": _normalize_block(raw.get("secondary")),
            "confirmed": bool(raw.get("confirmed", False)),
            "stale_after_days": _stale_days(raw.get("stale_after_days"))}


def write_calendar(root: Path, cfg: dict) -> None:
    out = {"primary": _normalize_block(cfg.get("primary")) or _blank(),
           "secondary": _normalize_block(cfg.get("secondary")),
           "confirmed": bool(cfg.get("confirmed", False)),
           "stale_after_days": _stale_days(cfg.get("stale_after_days"))}
    atomic.write_text(_path(root), json.dumps(out, indent=2) + "\n")


def primary_provider(root: Path):
    """The root's primary calendar provider, or None when it cannot be loaded.

    The tolerant read-and-resolve that every caller which only wants to *compute*
    with a calendar needs: an unknown provider id, a plugin that fails to import,
    or a config with no primary block all answer None rather than raising.
    Callers that must report the failure (the calendar settings routes) still go
    through `get_provider` directly and let `CalendarError` out.

    Here rather than once per caller because there were three copies of this
    try/except before the campaign clock (#100) wanted a fourth — and the one it
    reached for lived in `birthdays`, which made "resolve this campaign's
    calendar" read like a question about birthdays.
    """
    try:
        return get_provider(read_calendar(root)["primary"])
    except (CalendarError, KeyError):
        return None


def stale_after_days(root: Path) -> int:
    """This campaign's staleness threshold, in days. Never raises.

    A read of one integer, but through `read_calendar` rather than a second
    parse of the same file, so a corrupt calendar.json answers the default here
    exactly as it answers the default calendar everywhere else. `aging` calls
    it once per ledger read.
    """
    return _stale_days(read_calendar(root).get("stale_after_days"))


def copy_calendar(wroot: Path, croot: Path) -> None:
    write_calendar(croot, read_calendar(wroot))


def validate_calendar(cfg: dict) -> None:
    """Raise CalendarError if any configured calendar has a malformed custom holiday."""
    for block in (cfg.get("primary"), cfg.get("secondary")):
        if not block:
            continue
        provider = get_provider(block)  # raises CalendarError on an unknown provider
        for rule in block.get("custom_holidays", []) or []:
            provider.validate_rule(rule)
