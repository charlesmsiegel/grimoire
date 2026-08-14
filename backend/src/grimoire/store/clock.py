"""The campaign clock: one "now" for a whole campaign, moved deliberately and
with a reason, plus a deterministic digest of what the move crossed (#100).

Stored at ``<campaign>/clock.json``::

    {"now": "<native>", "log": [{"from", "to", "reason", "at"}, ...]}

Before this, "now" was derived ad hoc: the latest chronicle record's date, read
independently by the scene date pre-fill and the suggestion snapshot. That is
still the *seed* — an unclocked campaign answers exactly as it did — but it is
no longer the only answer, because a story that skips a month between scenes
had nowhere to say so. `now` reads the stored moment when there is one and
falls back to the chronicle when there is not, so nothing had to be migrated.

Two sources of "now" therefore exist, and the reconciliation rule is one-way:
`observe` moves the clock forward to a per-scene moment that is later than it,
and never backward. A flashback scene dated last winter is a legitimate thing
to play and must not drag the campaign's present with it, while a scene played
forward *is* the campaign moving on. Which of the two a given date is, only its
direction can tell us.

**Nothing here writes narrative.** `scenes.set_datetime` already owns the one
transcript line a time change produces ("*Time passes…*"); an advance is
campaign state plus a digest in the response, never a post.

The digest is deterministic — holidays, birthdays and open threads read off the
fixed-day axis and the campaign's own files, with no model in the loop. A prose
"meanwhile" summary is deliberately not here (#100's option C); it would need a
template and an LLM call, and the numbers have to be trustworthy on their own
first.

Calendar work happens BEFORE the campaign lock, the same rule
`scenes.lifecycle._date_hint` and `scenes.moment.set_datetime` follow:
`get_provider` imports every user-authored provider under ``<home>/calendars/``
and then runs its code, and nothing bounds how long a hand-written plugin
takes. Only the read-modify-write of clock.json itself is serialized.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, birthdays, calendars, chronicle, locks, plot
from .appearances import cast as appearances_cast
from .campaigns import paths as campaigns_paths
from .paths import now_iso


class ClockError(Exception):
    """An advance that cannot be resolved: no anchor, no target, out of range."""


#: A reason is a note, not an essay — truncated rather than rejected, so a long
#: paste still records the advance it explains.
REASON_LIMIT = 200

#: The longest span, in days, the digest will itemize. Beyond it the digest
#: reports `truncated` and lists nothing: crossing thirty years produces a
#: thousand holidays nobody reads, and the scan is O(days) in provider calls.
#: `elapsed_days` stays exact either way — the span is the one number that must
#: never be approximate.
SCAN_LIMIT_DAYS = 400

#: Rows per itemized *crossing* list (holidays, birthdays). A digest is
#: something a reader scans before confirming; past this it is a report. A cap
#: that hits sets `truncated`, so a trimmed list never reads as a complete one.
MAX_ROWS = 60

#: How many advances the log keeps; past it the oldest row is dropped. Say what
#: that costs rather than implying it is free: the *dates* time moved through
#: survive in each scene's `time_history` and in the chronicle, but a dropped
#: row's **reason** is recorded nowhere else and is simply gone. The cap exists
#: because `POST /advance` is a public endpoint and nothing else bounds the
#: file, which `now()` re-parses on every scene date pre-fill and every
#: suggestion snapshot. 500 rows is far past any campaign's real use.
LOG_LIMIT = 500


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "clock.json"


def _blank() -> dict:
    return {"now": "", "log": []}


def read(cid: str) -> dict:
    """The stored clock, or a blank one. Never raises.

    clock.json is hand-editable and its readers are pre-fills and digests, so a
    garbled or wrongly-shaped file degrades to "no clock" the way
    `plot.render_open` and the datetime route's chronicle fallback do. Each
    field is checked on its own: a good `now` beside a nonsense `log` should not
    cost the campaign its present.
    """
    p = _path(cid)
    if not p.exists():
        return _blank()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    stored, log = raw.get("now"), raw.get("log")
    return {"now": stored if isinstance(stored, str) else "",
            "log": [_row(e) for e in log if isinstance(e, dict)] if isinstance(log, list) else []}


def _row(entry: dict) -> dict:
    """One log entry with all four fields present and every one of them text.

    Coerced on the way out, not merely defaulted: the route hands these
    straight to React, which refuses an object as a child and blanks the whole
    panel — a failure no `try` around the read can catch, because the read
    succeeds. `plot._field` is the same helper for the same reason, learned the
    same way.
    """
    return {k: entry[k] if isinstance(entry.get(k), str) else ""
            for k in ("from", "to", "reason", "at")}


def _seed(cid: str, fallback: str | None) -> str:
    """Where the story left off, for a campaign whose clock was never set.

    The caller's datum when it has one, else the latest chronicle date. This
    fallback is what makes the clock safe to adopt everywhere at once — an
    unclocked campaign answers exactly what its callers computed for themselves
    before there was a clock.
    """
    if fallback is not None:
        return fallback
    try:
        recent = chronicle.recent(cid, 1)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: no date, not a crash
        return ""
    if not recent:
        return ""
    date = recent[-1].get("date", "")
    return date if isinstance(date, str) else ""


def now(cid: str, fallback: str | None = None) -> str:
    """The campaign's current moment: the clock, else where the story left off.

    `fallback` is for the caller that has *already* read the chronicle for its own
    reasons (`suggest.build_snapshot` reads the last three records): pass the
    latest record's date and this uses it instead of parsing chronicle.json a
    second time in the same call. The precedence stays defined here — the caller
    supplies the datum, not the rule.
    """
    return read(cid)["now"] or _seed(cid, fallback)


def state(cid: str, fallback: str | None = None) -> dict:
    """`{"now": <resolved>, "log": [...]}` — one read of clock.json for both.

    What the panel wants in one call: `now`'s precedence applied, and the log
    beside it. `now(cid)` plus `read(cid)["log"]` is the same answer from two
    parses of one file.
    """
    stored = read(cid)
    return {"now": stored["now"] or _seed(cid, fallback), "log": stored["log"]}


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2) + "\n")


def _reason(reason: str) -> str:
    return str(reason or "").strip()[:REASON_LIMIT]


def _commit(cid: str, target: str, entry: dict) -> None:
    """Land a moment and its log row — **the caller holds the campaign lock**.

    The lock lives in the public mutators rather than here so that the one
    read-modify-write of clock.json is visibly inside it: the file is rewritten
    whole, so two unlocked writers lose a row. The digest in the caller's
    response was computed against the moment read *before* the lock, which is
    the accepted cost of never running a calendar plugin under it — a concurrent
    advance is a user racing themselves, and each attempt still logs the span it
    actually measured.
    """
    data = read(cid)
    data["now"] = target
    data["log"] = [*data["log"], entry][-LOG_LIMIT:]
    _write(cid, data)


def _provider(cid: str):
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        raise ClockError("this campaign's calendar cannot be loaded")
    return provider


def _stamp(provider, native: str) -> tuple[int, int]:
    """A total order over moments: (fixed day, minutes into it).

    A dateless moment sorts as midnight, so "the 5th" precedes "the 5th at
    21:30" — which is what a scene gaining a time-of-day means.
    """
    return calendars.fixed_of(provider, native), calendars.minutes_of(native) or 0


def _current(provider, cid: str) -> str:
    """The moment being left, in canonical form where the provider can give one.

    Canonicalized rather than taken as stored, because `now` may be a *seed*: a
    chronicle date is whatever the absorb wrote, and a calendar that canonicalizes
    its own notation (Hebrew capitalizes the month) would otherwise make a
    re-advance to the same day look like a move — logging a row whose `from` and
    `to` are one date spelled two ways, and returning `moved: True` for a clock
    that did not budge.

    A moment this calendar cannot read at all is returned unchanged: it is still
    the moment being left, and the digest already reports no span for it.
    """
    stored = now(cid)
    if not stored:
        return ""
    try:
        return calendars.normalize(provider, stored)
    except calendars.CalendarError:
        return stored


def _resolve(provider, cid: str, to: str | None, days: int | None) -> tuple[str, str]:
    """(moment being left, canonical target). Raises ClockError / CalendarError."""
    start = _current(provider, cid)
    if to is not None and str(to).strip():
        return start, calendars.normalize(provider, str(to).strip())
    if days is None:
        raise ClockError("an advance needs either a target date or a number of days")
    try:
        delta = int(days)
    except (TypeError, ValueError) as e:
        raise ClockError(f"not a number of days: {days!r}") from e
    if not start:
        raise ClockError("no current date to advance from — set a date first")
    try:
        target = provider.format(calendars.fixed_of(provider, start) + delta)
    except (calendars.CalendarError, ValueError, OverflowError, OSError) as e:
        # A duration can leave the calendar entirely (Gregorian year 0, a plugin's
        # own bounds) and `format` is under no obligation to raise CalendarError
        # about it. One answer for every way that fails.
        raise ClockError(f"cannot advance {delta} days from {start}") from e
    # Day precision only for durations (#105 owns finer granularity), so the
    # time of day rides along untouched rather than being dropped.
    _, time_str = calendars.split_native(start)
    if time_str:
        target = f"{target}T{time_str}"
    return start, calendars.normalize(provider, target)


def _configured(cid: str, primary):
    """Every configured provider, primary first and already resolved.

    The secondary calendar's holidays land on the same fixed-day axis, so a
    campaign reckoning in two calendars gets both sets crossed — the same thing
    `calendars.today_facts` does for a single moment.
    """
    out = [primary]
    try:
        secondary = calendars.read_calendar(campaigns_paths.campaign_root(cid)).get("secondary")
        if secondary:
            out.append(calendars.get_provider(secondary))
    except (calendars.CalendarError, KeyError):
        pass   # a broken secondary costs its own holidays, nothing else
    return out


def _holidays(cid: str, primary, lo_fixed: int, hi_fixed: int) -> list[dict]:
    """Observances in `(lo_fixed, hi_fixed]`, deduplicated and dated in the
    primary calendar's own notation.

    Half-open at the start for the same reason as `birthdays.crossed`: the day
    being left has already been lived through.

    `in_days` counts from `lo_fixed` — the *earlier* end of the span, which for a
    forward advance is the moment being left ("Christmas, one day in") and for a
    backward one is the moment being returned to. Stated because the two are not
    the same thing and the field would otherwise invite the forward reading in
    both cases.
    """
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for provider in _configured(cid, primary):
        try:
            # `list(... or [])` inside the try, not just the call: a plugin whose
            # `holidays` returns None, or a generator that raises partway through
            # the range, both fail at iteration rather than at the call, and
            # neither should be a 500.
            found = list(provider.holidays(lo_fixed + 1, hi_fixed) or [])
        except Exception:  # noqa: BLE001 — a provider (or a plugin) that cannot answer a range
            continue
        for h in found:
            # Both fields are validated rather than trusted, because a provider
            # may be a user-authored plugin: a non-integer `fixed` would reach
            # `describe` and raise a TypeError this module has no business
            # turning into a 500, and a non-string `name` would reach React,
            # which refuses an object as a child and blanks the panel that was
            # about to show it (the `plot._field` failure).
            try:
                fixed = int(h["fixed"])
            except (KeyError, TypeError, ValueError):
                continue
            name = h.get("name", "")
            name = name if isinstance(name, str) else ""
            if (name, fixed) in seen:
                continue
            seen.add((name, fixed))
            try:
                described = primary.describe(fixed)
                native = primary.format(fixed)
            except (calendars.CalendarError, KeyError, TypeError, ValueError):
                continue
            out.append({"name": name, "native": native,
                        "friendly": described["friendly"],
                        "in_days": fixed - lo_fixed})
    out.sort(key=lambda h: (h["in_days"], h["name"]))
    return out


def digest(cid: str, provider, from_native: str, to_native: str) -> dict:
    """What the move from `from_native` to `to_native` crosses.

    Deterministic and read-only: nothing here writes, so a preview and the
    advance that follows it produce the same numbers from the same inputs.

    `elapsed_days` is signed — a backward move is a correction, and saying so
    is more useful than refusing it. The crossings are computed over the span
    actually traversed in either direction, so a backward move reports the
    holidays and birthdays it just un-lived rather than silently nothing.

    Days, not minutes: a move between two moments of the same day reports
    `elapsed_days: 0` and crosses nothing, which is what day precision means
    (#105 owns finer granularity). The clock still moves — `advance` returns
    `moved: True` — because the moment did change.

    No weather here, though `weather.sweep`'s own docstring says it exists to
    name transitions "for the digest": that sweep is scoped to the locations one
    *scene* has visited, and a campaign-level advance is in no scene. The scene
    datetime route still reports it, which is the only place the scope exists.
    """
    to_fixed = calendars.fixed_of(provider, to_native)
    to_friendly = provider.describe(to_fixed)["friendly"]
    from_fixed, from_friendly = None, ""
    if from_native:
        try:
            from_fixed = calendars.fixed_of(provider, from_native)
            from_friendly = provider.describe(from_fixed)["friendly"]
        except calendars.CalendarError:
            from_fixed = None   # a stored moment this calendar cannot read: no span, still a target

    elapsed = to_fixed - from_fixed if from_fixed is not None else 0
    lo, hi = (min(from_fixed, to_fixed), max(from_fixed, to_fixed)) if from_fixed is not None \
        else (to_fixed, to_fixed)

    truncated = hi - lo > SCAN_LIMIT_DAYS
    holidays: list[dict] = []
    crossed_birthdays: list[dict] = []
    if not truncated and hi > lo:
        holidays = _holidays(cid, provider, lo, hi)
        try:
            roster = appearances_cast.roster(cid)
        except Exception:  # noqa: BLE001 — garbled appearances.json
            roster = []
        crossed_birthdays = birthdays.crossed(provider, lo, hi,
                                             birthdays.gather(cid, roster))
        if len(holidays) > MAX_ROWS or len(crossed_birthdays) > MAX_ROWS:
            truncated = True
            holidays, crossed_birthdays = holidays[:MAX_ROWS], crossed_birthdays[:MAX_ROWS]

    try:
        # Every open thread is untouched by construction: a skip contains no
        # scenes, so nothing in it can have moved one. Which is the point —
        # this is what the campaign still owes after a month nobody played.
        threads = plot.open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        threads = []

    return {"from": from_native or "", "to": to_native,
            "from_friendly": from_friendly, "to_friendly": to_friendly,
            "elapsed_days": elapsed, "backward": elapsed < 0,
            "holidays": holidays, "birthdays": crossed_birthdays,
            # Uncapped, unlike the two crossing lists: this is the campaign's
            # own open ledger (the same list `/ledger` renders in full), so
            # trimming it here would quietly under-report what the skip leaves
            # owed. `truncated` therefore means the crossings alone.
            "open_threads": threads, "truncated": truncated}


def preview(cid: str, to: str | None = None, days: int | None = None) -> dict:
    """The digest an `advance` with these arguments would return, writing nothing."""
    provider = _provider(cid)
    start, target = _resolve(provider, cid, to, days)
    return digest(cid, provider, start, target)


def advance(cid: str, to: str | None = None, days: int | None = None,
            reason: str = "") -> dict:
    """Move the campaign clock, recording why. Returns {moved, now, digest}.

    Exactly one of `to` (skip to a date, in the primary calendar's notation)
    and `days` (advance by a duration) decides the target; `to` wins if both
    arrive. Advancing to the moment already current is a no-op that still
    returns the digest, mirroring `scenes.set_datetime`'s `{"advanced": False}`
    for a repeated date — a second identical click should not add a second row
    to the log.
    """
    provider = _provider(cid)
    start, target = _resolve(provider, cid, to, days)
    computed = digest(cid, provider, start, target)
    if target == start:
        return {"moved": False, "now": start, "digest": computed}
    with locks.campaign_lock(cid):
        _commit(cid, target, {"from": start, "to": target,
                              "reason": _reason(reason), "at": now_iso()})
    return {"moved": True, "now": target, "digest": computed}


def observe(cid: str, native: str, reason: str) -> dict:
    """Reconcile the clock with a moment a scene just took: forward only.

    Called by the scene-datetime route rather than from `scenes.set_datetime`
    itself, which keeps `scenes` free of any import of this module — and so
    keeps the module graph acyclic, since this one reads the chronicle and the
    chronicle reads scenes.

    That puts the reconciliation in a *caller*, so it holds only for callers that
    remember it. `PUT /campaigns/{cid}/scenes/{sid}/datetime` is the only caller
    `set_datetime` has today, which is why one call site is enough; a second one
    has to call this too, or the campaign's present silently stops following the
    scenes that move it.

    Silent about failure by design: a scene's date is already set by the time
    this runs, and a clock that cannot follow it must not turn that completed
    write into an error.
    """
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        return {"moved": False, "now": read(cid)["now"]}
    current = _current(provider, cid)   # canonical, so the log reads consistently
    try:
        canonical = calendars.normalize(provider, native)
        stamp = _stamp(provider, canonical)
    except calendars.CalendarError:
        return {"moved": False, "now": current}
    if current:
        try:
            if _stamp(provider, current) >= stamp:
                return {"moved": False, "now": current}
        except calendars.CalendarError:
            pass   # an unreadable present is no reason to refuse a readable moment
    with locks.campaign_lock(cid):
        _commit(cid, canonical, {"from": current, "to": canonical,
                                 "reason": _reason(reason), "at": now_iso()})
    return {"moved": True, "now": canonical}
