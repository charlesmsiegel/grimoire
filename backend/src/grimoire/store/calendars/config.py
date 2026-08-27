"""Calendar config IO: <root>/calendar.json = {primary, secondary|None,
confirmed, stale_after_days, warn_days}, each calendar block {provider, region,
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

#: How far ahead a scheduled event or an observance is warned about (#106).
#: Deliberately NOT `UPCOMING_WINDOW_DAYS`: that is the *computation* window the
#: prompt's "Upcoming:" line reads from, and it is wide on purpose — a month of
#: lead time is useful to a model deciding what a scene mentions. A warning
#: shown to the READER is a different thing, and one that fires a month out is
#: noise by the time the day arrives. A week is the span a reader can still
#: plan a scene inside.
#:
#: Beside `stale_after_days` for its reason exactly: it is a campaign-level knob
#: about time, this is the campaign's time config, and a world can set the
#: default its campaigns are created with because the file is copied on create.
WARN_DAYS = 7

#: The widest warn window that will be honoured. A hand-edited `"warn_days":
#: 100000` is not a setting anybody means, and it is the bound on how many
#: observances one notice read has to resolve — the provider is asked for every
#: holiday in the window, and a user-authored plugin is doing that work.
MAX_WARN_DAYS = 365


def _blank(region: str = "US") -> dict:
    return {"provider": "gregorian", "region": region, "custom_holidays": [], "anchor": None}


def default_calendar() -> dict:
    return {"primary": _blank(), "secondary": None, "confirmed": False,
            "stale_after_days": STALE_AFTER_DAYS, "warn_days": WARN_DAYS}


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


def _warn_days(value) -> int:
    """A stored warn window as a whole number of days, or the default.

    Coerced exactly like `_stale_days` and for the same reason — calendar.json
    is hand-editable and this number reaches fixed-day arithmetic — with one
    difference at each end. Zero is *kept* here rather than rejected: a
    threshold of zero calls every record stale, which is meaningless, but a warn
    window of zero warns about nothing, which is a reader saying "not this
    campaign". And the value is capped at `MAX_WARN_DAYS`, because unlike a
    staleness comparison a warn window is work: every day of it is a day the
    calendar provider is asked to enumerate.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return WARN_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        return WARN_DAYS
    if days < 0:
        return WARN_DAYS
    return min(days, MAX_WARN_DAYS)


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
            "stale_after_days": _stale_days(raw.get("stale_after_days")),
            "warn_days": _warn_days(raw.get("warn_days"))}


def write_calendar(root: Path, cfg: dict) -> None:
    out = {"primary": _normalize_block(cfg.get("primary")) or _blank(),
           "secondary": _normalize_block(cfg.get("secondary")),
           "confirmed": bool(cfg.get("confirmed", False)),
           "stale_after_days": _stale_days(cfg.get("stale_after_days")),
           "warn_days": _warn_days(cfg.get("warn_days"))}
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


def warn_days(root: Path) -> int:
    """How far ahead this root warns about an imminent event, in days (#106).

    Through `read_calendar` rather than a second parse of the same file, so a
    corrupt calendar.json answers the default here exactly as it answers the
    default calendar everywhere else — the same shape as `stale_after_days`
    above, which is the reader beside this one.
    """
    return _warn_days(read_calendar(root).get("warn_days"))


def warn_days_for_save(root: Path, sent) -> int:
    """The window a save should persist, given what the request carried.

    `None` means the request expressed no opinion — a client that predates the
    field, or one editing only the calendars — and the answer is then what is
    ALREADY stored, not the default. Those are different numbers whenever a
    reader has set one, and treating them as the same is how saving an unrelated
    calendar field silently resets a chosen window (or, worse, un-switches-off a
    campaign that had deliberately set 0).

    This is the whole reason `warn_days`' no-opinion value is `None` rather than
    `stale_after_days`' `0`: 0 is a real setting here, so the sentinel had to be
    something else, and something else needs a resolver like this one.
    """
    return _warn_days(sent) if sent is not None else warn_days(root)


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
