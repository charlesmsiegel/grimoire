"""suggest: dates responsibilities."""

from __future__ import annotations

import re
from collections.abc import Callable

from .. import calendars, clock
from ..campaigns import paths as campaigns_paths
from . import _contracts


def date_normalizer(cid: str, tolerant: bool = False):
    """Canonical native date or "" — a suggested date is only a hint, so never raise.

    `tolerant=True` goes through `calendars.resolve`, which also accepts a date
    written the way the PROMPT displays one ("25 Kislev 5786"). Only the
    calendar's own renderings are added, so even then this is as strict about
    what a date MEANS as `normalize` was: a wider set of spellings, not a looser
    reading. `resolve` needs an anchor to search around, and the campaign clock
    is what "the moment this reply is about" means everywhere else in this
    module. It is read once per normalizer rather than per row, for the reason
    `ref_validator` resolves its id sets once: one reply can carry four dates.

    Off by default, and the callers divide cleanly. Model TEXT is tolerant
    (`parse_intent` here; `parse_output` and `parse_next_date` call the same
    `normalize_date` directly, anchored on the snapshot's `now`) -- that is the
    whole point. Stored RECORDS are not (`ref_validator`): their dates were
    canonicalized on write, so one that no longer parses means the campaign
    changed calendars under it, and re-reading a Gregorian date through a Hebrew
    string matcher would invent a moment nobody wrote. It is also the path that
    could least afford it -- the ledger has no cap, it is revalidated on every
    read, and a fuzzy miss costs a whole window scan per row.
    """
    provider = _soft_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        return lambda _s: ""
    if not tolerant:
        def strict(s: str) -> str:
            if not s:
                return ""
            try:
                return calendars.normalize(provider, s)
            except calendars.CalendarError:
                return ""
            except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything; a stored date it cannot read is no date (§24, §26)
                return ""
        return strict
    anchor = clock.now(cid)
    return lambda s: normalize_date(provider, anchor, s)


def _soft_provider(croot):
    """`calendars.primary_provider(croot)`, or None when resolving it raises
    anything at all (Decision 25). `primary_provider` absorbs `CalendarError`
    and `KeyError`; a plugin whose constructor raises something else used to
    escape it, and every parser behind this degrades to undated instead (§26)."""
    try:
        return calendars.primary_provider(croot)
    except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything
        return None


def normalize_date(provider, near: str, text) -> str:
    """Model text as a canonical native date, or "" -- never raises for bad
    text. `calendars.resolve` anchored on `near`, so a date written the way the
    prompt displays one resolves too. No provider means no date."""
    if provider is None or not isinstance(text, str) or not text.strip():
        return ""
    try:
        return calendars.resolve(provider, text.strip(), near)
    except calendars.CalendarError:
        return ""
    except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything; an unreadable date is no date
        return ""


_MONTH_KEY = re.compile(r"^(-?\d+)-(.+)$")


def _month_of(ref: str) -> tuple[int, str] | None:
    """`(year, month_key)` of a month-only birthday ref
    (`birthday:<kind>:<id>:month:<year>-<key>`, spec §4), else None. The month
    comes from the ref because pressure items and anchor options carry no
    `year`/`month_key` (Decision 12). A key may hold `-`; a year may be
    negative; the last `:month:` is the separator, so an actor id of `month`
    cannot confuse it."""
    if not isinstance(ref, str) or not ref.startswith("birthday:"):
        return None
    _head, sep, tail = ref.rpartition(":month:")
    found = _MONTH_KEY.match(tail) if sep else None
    return (int(found.group(1)), found.group(2)) if found else None


def _plugin_soft(fn: Callable, *args):
    """`fn(*args)` over provider code, or None when it raises anything."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything; a day it cannot read is no day
        return None


def _fixed(provider, native: str) -> int | None:
    """The primary fixed day of a native date, time of day split off."""
    if provider is None or not native:
        return None
    return _plugin_soft(calendars.fixed_of, provider, native)


def _friendly_of(provider, fixed: int | None) -> str:
    if provider is None or fixed is None:
        return ""
    described = _plugin_soft(provider.describe, fixed)
    return str(described.get("friendly", "")) if isinstance(described, dict) else ""


def _now_fixed(provider, snapshot: dict) -> int | None:
    fixed = snapshot.get("fixed")
    if isinstance(fixed, int):
        return fixed
    return _fixed(provider, snapshot.get("now") or "")


def _anchor_day(provider, anchor: dict) -> int | None:
    """D: the option's fixed day, else its native's (time of day dropped)."""
    fixed = anchor.get("fixed")
    return fixed if isinstance(fixed, int) else _fixed(provider, anchor.get("native") or "")


def _month_key(provider, fixed: int) -> tuple[int, str] | None:
    """`(year, case-folded month key)` of a fixed day, or None if unreadable."""
    described = _plugin_soft(provider.describe, fixed)
    if not isinstance(described, dict):
        return None
    months = _plugin_soft(provider.months, described.get("year"))
    try:
        return described["year"], str(months[described["month"] - 1]["key"]).casefold()
    except (KeyError, IndexError, TypeError):
        return None


def _in_month(provider, month: tuple[int, str], now: int | None,
              date: str) -> tuple[str, bool]:
    """A month-only `on`: the date is kept only inside the anchor's month, and
    not behind `now` -- the anchor month can be the present one, and every
    other anchored relation already holds that lower bound."""
    d = _fixed(provider, date)
    if d is None:
        return "", False
    year, key = month
    ok = _lower_ok(d, now) and _month_key(provider, d) == (year, key.casefold())
    return (date, False) if ok else ("", True)


def _lower_ok(d: int, now: int | None) -> bool:
    return now is None or now <= d


#: Spec §15.3's relation rules over fixed days: (d, D, now) -> date allowed.
#: `on` is absent: it derives the date rather than checking one.
_RELATION_RULES: dict[str, Callable[[int, int, int | None], bool]] = {
    "before": lambda d, big_d, now: _lower_ok(d, now) and d < big_d,
    "by": lambda d, big_d, now: _lower_ok(d, now) and d <= big_d,
    "after": lambda d, big_d, _now: big_d < d <= big_d + calendars.RESOLVE_WINDOW_DAYS,
}


def _against_anchor(provider, anchor: dict, relation: str, now: int | None,
                    date: str) -> tuple[str, bool]:
    month = _month_of(anchor["ref"])
    if month is not None:
        return _in_month(provider, month, now, date) if date else ("", False)
    big_d = _anchor_day(provider, anchor)
    if big_d is None:
        return date, False
    rule = _RELATION_RULES.get(relation)
    if rule is None:   # `on`: derived from the anchor, canonical, no time of day
        return _plugin_soft(provider.format, big_d) or "", False
    d = _fixed(provider, date)
    if d is None:
        return "", False
    return (date, False) if rule(d, big_d, now) else ("", True)


def _against_mode(provider, snapshot: dict, controls: _contracts.Controls, now: int | None,
                  date: str) -> tuple[str, bool]:
    """`near` and `move` (spec §16.3); skipped with no `now` (Decision 12)."""
    if controls.time_mode not in ("near", "move") or now is None:
        return date, False
    d = _fixed(provider, date)
    if d is None:
        return date, False
    if controls.time_mode == "near":
        ok = now <= d <= now + snapshot.get("near_days", _contracts.NEAR_MIN_DAYS)
    else:
        ok = d > now
    return (date, False) if ok else ("", True)


def judge_date(provider, snapshot: dict, controls: _contracts.Controls, time_anchor: dict | None,
               date: str) -> tuple[str, str | None]:
    """`(date, rejected_by)` for one already normalized native (spec §15.3,
    Decision 12). An `on` anchor derives the date; `before`/`by`/`after`
    check it on fixed days; a month-only `on` keeps it only inside its month;
    then `near`/`move` check what is left. `rejected_by` names the rule that
    blanked the date -- `"anchor"` or `"time"` -- and is None when nothing
    did: an anchored card's date can be the anchor's own and still be refused
    by the time setting, and the card has to say which (§16.4). An absent
    date is never rejected, and with no calendar nothing is derived or
    checked."""
    if provider is None:
        return "", None
    now = _now_fixed(provider, snapshot)
    if time_anchor is not None:
        anchor = next((a for a in snapshot.get("anchors", []) if a["ref"] == time_anchor["ref"]),
                      None)
        date, rejected = ((date, False) if anchor is None else
                          _against_anchor(provider, anchor, time_anchor["relation"], now, date))
        if rejected:
            return "", "anchor"
    if not date:
        return "", None
    date, rejected = _against_mode(provider, snapshot, controls, now, date)
    return date, "time" if rejected else None


def check_date(provider, snapshot: dict, controls: _contracts.Controls, time_anchor: dict | None,
               date: str) -> tuple[str, bool]:
    """`(date, rejected)`: `judge_date` without saying which rule refused it."""
    date, rejected_by = judge_date(provider, snapshot, controls, time_anchor, date)
    return date, rejected_by is not None
