"""Per-campaign scheduled events: the dated things a campaign has planned — a
coronation, an eclipse, the night a debt comes due — and the record of the clock
having reached them (#101).

Stored at ``<campaign>/events.json``::

    {eid: {"name", "date": "<native>", "note",
           "fired": null | {"at": "<iso stamp>", "moment": "<native>"}}}

Deliberately not a custom holiday rule (#101's option B). A holiday RECURS and
belongs to the world's calendar — every year has a Midwinter, and a campaign
created from that world inherits the rule. An event happens once, belongs to
this campaign, and the whole point of it is the moment it stops being upcoming.
A year-qualified holiday rule could express the date and nothing else:
``calendar.json`` is copied into the campaign at create and never synced back
(`campaigns.create_campaign` -> `calendars.copy_calendar`), and a holiday has
nowhere to record that it was reached.

**`fired` is a stamp, not a status the reader sets.** The clock writes it on
crossing the event's date — `clock.advance`, and `clock.observe` when a scene's
own date carries the campaign past it — and it says when that happened in both
reckonings at once: `at` is wall-clock (when the app noticed) and `moment` is
the in-world date the clock landed on. It is what makes "has this happened yet"
answerable at all: without it, an event whose date is behind the clock is
indistinguishable from one nobody has reached.

Firing is forward-only. A backward move is a correction — `clock.digest` reports
what it un-lived — and un-stamping on the way back would erase the only record
that the story already played the event.

Nothing here writes narrative and nothing here calls a model. What a fired event
*means* is the reader's to decide; one that wants a scene goes through the
scene-suggestion flow like every other idea (#88), which is the rule the whole
time-advance pipeline follows (`clock`'s module docstring).

Mutators serialize on ``locks.campaign_lock(cid)``: events.json is rewritten
whole, so two unlocked read-modify-writes lose one of them. Calendar work —
resolving the provider, normalizing a date — happens BEFORE the lock, the same
cut `clock` and `scenes.moment.set_datetime` make: `get_provider` imports and
runs user-authored plugin code, and nothing bounds how long that takes.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, calendars, fieldtext, locks, paths
from .campaigns import paths as campaigns_paths

#: A name is a label in a list, and a note is the paragraph behind it. Both are
#: truncated rather than rejected, like `clock.REASON_LIMIT`: a paste that is too
#: long should still record the event it describes. The file is read on every
#: turn (the Today section) and on every advance, which is what bounds them at
#: all — nothing else does, and `POST .../events` is a public endpoint.
NAME_LIMIT = 200
NOTE_LIMIT = 2000


class EventError(Exception):
    """An events.json that cannot be read as one. Raised by the mutators only."""


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "events.json"


def read(cid: str) -> dict:
    """The stored events, or an empty set of them.

    Never raises over the file, matching `clock.read`: the readers are a prompt
    section, a digest and a panel, and a hand-edited file that no longer parses
    must cost the campaign its event list rather than its turn. An id that
    cannot name a campaign at all still raises `CampaignNotFound` out of
    `campaign_root`, which is where that check belongs.
    """
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _mutable(cid: str) -> dict:
    """`read` for a WRITER: the same file, but refusing what it cannot read.

    The tolerance above is a reader's; a mutator inheriting it would answer a
    corrupt or wrongly-shaped events.json with `{}` and then publish that empty
    document over the reader's authored events. `scene_ideas._read_ledger` and
    `facts._read_ledger` draw the same line for the same reason — the difference
    is only that this one also has to catch the parse, since `read` swallows it.
    """
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise EventError("events.json cannot be read") from e
    if not isinstance(raw, dict):
        raise EventError("events.json does not hold a set of events")
    return raw


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _text(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything else.

    events.json is hand-editable and every row here is rendered by React, which
    refuses an object as a child and blanks the panel holding it. Same rule and
    the same reason as `plot._field`, and literally the same function:
    `fieldtext.text`, which is where that rule lives now.
    """
    return fieldtext.text(value, fallback)


def _fired(value) -> dict | None:
    """The fire stamp as `{at, moment}`, or None for anything unstamped.

    Coerced rather than passed through for `clock._row`'s reason: this reaches
    the panel as an object, and one hand-edited `"fired": "yes"` would otherwise
    reach `.get` (a 500) or React (a blank panel). Truthiness of the *stamp*
    decides — `{}` and `null` both mean not fired, which is what an event
    written by hand without the key means too.
    """
    if not isinstance(value, dict) or not value:
        return None
    return {"at": _text(value.get("at")), "moment": _text(value.get("moment"))}


def _row(eid: str, rec, provider=None) -> dict:
    """One projected event. `provider`, when given, adds the friendly date.

    The label is computed here rather than in the route because every consumer
    wants it — panel, digest and prompt section — and resolving it needs the
    date parsed, which this already did to sort. A date this calendar cannot
    read keeps an empty label rather than dropping the row: the row is the only
    place the reader can see, and fix, the date that broke.
    """
    rec = rec if isinstance(rec, dict) else {}
    date = _text(rec.get("date"))
    friendly = ""
    if provider is not None and date:
        try:
            friendly = calendars.friendly(provider, date)
        except calendars.CalendarError:
            friendly = ""
    return {"id": eid, "name": _text(rec.get("name"), eid), "date": date,
            "friendly": friendly, "note": _text(rec.get("note")),
            "fired": _fired(rec.get("fired"))}


def _fixed(provider, date: str) -> int | None:
    """`date` on the fixed-day axis, or None when this calendar cannot read it.

    An event outlives the calendar it was written in — a campaign can be
    re-pointed at another provider — so an unreadable date is ordinary data, not
    an error. Every range question below skips such an event; it stays visible
    in the list, which is the one view that can lead to it being fixed.
    """
    if not date:
        return None
    try:
        return calendars.fixed_of(provider, date)
    except calendars.CalendarError:
        return None


def list_events(cid: str, provider=None) -> list[dict]:
    """Every stored event, soonest first.

    Sorted on the fixed-day axis when a provider resolves it, so two calendars'
    notations still order by when they actually fall; an event whose date this
    calendar cannot parse sorts last, by id, rather than interleaving on a
    string comparison that means nothing. With no provider the order is by id
    alone — a stable answer, not a chronological one.
    """
    rows = [_row(eid, rec, provider) for eid, rec in read(cid).items()]
    if provider is None:
        return sorted(rows, key=lambda r: r["id"])
    order = {r["id"]: _fixed(provider, r["date"]) for r in rows}
    return sorted(rows, key=lambda r: (order[r["id"]] is None,
                                       order[r["id"]] or 0, r["id"]))


def get(cid: str, eid: str) -> dict | None:
    rec = read(cid).get(eid)
    return _row(eid, rec) if isinstance(rec, dict) else None


def _normalized(cid: str, date: str) -> str:
    """`date` in the campaign's own canonical notation. Raises CalendarError.

    Outside every lock in this module, deliberately: this is the call that runs
    a user-authored calendar plugin.
    """
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        raise calendars.CalendarError("this campaign's calendar cannot be loaded")
    return calendars.normalize(provider, date.strip())


def create(cid: str, name: str, date: str, note: str = "") -> str:
    """File a new event and return its id. Raises CalendarError on a bad date.

    A date is required and normalized on the way in: an event that is not on a
    day cannot fire, and one stored in whatever the reader typed would compare
    unequal to the same day written the calendar's way. The id is the name
    slugified, uniquified against what is already there, so two events called
    "The eclipse" can both exist — an event is a moment, and a story can plan
    the same-sounding one twice.
    """
    stamped = _normalized(cid, date)   # before the lock: runs plugin code
    label = str(name or "").strip()[:NAME_LIMIT]
    with locks.campaign_lock(cid):
        data = _mutable(cid)
        eid = paths.uniquify(paths.slugify(label or "event"), lambda c: c in data)
        data[eid] = {"name": label or eid, "date": stamped,
                     "note": str(note or "").strip()[:NOTE_LIMIT], "fired": None}
        _write(cid, data)
        return eid


def update(cid: str, eid: str, name: str | None = None, date: str | None = None,
           note: str | None = None) -> bool:
    """Edit one event. False when no such event exists.

    Each field is three-valued the way `commitments.set_movement`'s `due` is:
    None leaves the stored value alone, so a caller sending only a note cannot
    blank the name. A date can be corrected but not removed — an event with no
    day is not a scheduled event — so an empty `date` is a no-op rather than a
    clear.

    Re-dating an event does NOT clear its fire stamp. The stamp records that the
    clock reached the day the event was on at the time, which happened; deciding
    the event should have been a week later does not un-happen it. `unfire` is
    the way back, and it says what it does.
    """
    stamped = _normalized(cid, date) if date is not None and date.strip() else None
    with locks.campaign_lock(cid):
        data = _mutable(cid)
        rec = data.get(eid)
        if not isinstance(rec, dict):
            return False
        if name is not None and name.strip():
            rec["name"] = name.strip()[:NAME_LIMIT]
        if stamped is not None:
            rec["date"] = stamped
        if note is not None:
            rec["note"] = note.strip()[:NOTE_LIMIT]
        data[eid] = rec
        _write(cid, data)
        return True


def unfire(cid: str, eid: str) -> bool:
    """Clear one event's fire stamp, putting it back on the upcoming list.

    The reader's undo for a stamp the clock wrote — an advance made by mistake,
    an event re-dated forward after it fired. Separate from `update` so that
    editing an event's text can never quietly resurrect it.
    """
    with locks.campaign_lock(cid):
        data = _mutable(cid)
        rec = data.get(eid)
        if not isinstance(rec, dict):
            return False
        rec["fired"] = None
        _write(cid, data)
        return True


def delete(cid: str, eid: str) -> bool:
    with locks.campaign_lock(cid):
        data = _mutable(cid)
        if data.pop(eid, None) is None:
            return False
        _write(cid, data)
        return True


def crossed(cid: str, provider, lo_fixed: int, hi_fixed: int) -> list[dict]:
    """Unfired events landing in `(lo_fixed, hi_fixed]`, soonest first.

    Half-open at the start for `clock._holidays`' reason: the day being left has
    already been lived through, so an event dated to it fired (or did not) when
    the clock arrived there, not on the way out.

    Unfired only. An event the clock has already reached must not fire a second
    time when a later advance re-crosses its day — which is exactly what a
    correction backwards followed by a re-advance does.

    `in_days` counts from `lo_fixed`, the earlier end of the span, the same
    field `clock._holidays` returns and with the same caveat: for a backward
    move that is the moment being returned *to*.
    """
    out = []
    for eid, rec in read(cid).items():
        row = _row(eid, rec, provider)
        if row["fired"] is not None:
            continue
        fixed = _fixed(provider, row["date"])
        if fixed is None or not (lo_fixed < fixed <= hi_fixed):
            continue
        out.append({**row, "in_days": fixed - lo_fixed})
    out.sort(key=lambda e: (e["in_days"], e["id"]))
    return out


def fire(cid: str, eids: list[str], moment: str) -> list[str]:
    """Stamp these events as reached at `moment`. Returns the ids that took it.

    Given ids rather than a span, because the caller has already computed the
    span's crossings for its digest and re-deriving them here would let the two
    disagree — the digest would name one event and the file record another.

    Already-fired and unknown ids are skipped rather than refused: this runs
    after an advance that has already landed, and a stamp that cannot be written
    must not turn a completed move into an error. That is also why the whole
    body tolerates an unreadable file by writing nothing.
    """
    if not eids:
        return []
    stamp = {"at": paths.now_iso(), "moment": moment}
    with locks.campaign_lock(cid):
        try:
            data = _mutable(cid)
        except EventError:
            return []
        done = []
        for eid in eids:
            rec = data.get(eid)
            if not isinstance(rec, dict) or _fired(rec.get("fired")) is not None:
                continue
            rec["fired"] = dict(stamp)
            done.append(eid)
        if done:
            _write(cid, data)
        return done


def upcoming(cid: str, provider, now_fixed: int,
             window: int = calendars.UPCOMING_WINDOW_DAYS) -> list[dict]:
    """Unfired events in `(now_fixed, now_fixed + window]`, soonest first.

    The pre-notice half of the same computation `crossed` does, and the source
    #106 will warn from. `crossed` cannot serve it: that answers what a move
    *passed*, this answers what is ahead of a standing moment.
    """
    return crossed(cid, provider, now_fixed, now_fixed + max(window, 0))


def on_day(cid: str, provider, fixed: int) -> list[dict]:
    """Every event dated to this exact day, fired or not, soonest-name order.

    Fired events included, unlike `crossed` and `upcoming`: this answers "what
    is today", and an event that fired this morning is still today's. The two
    predicates differ because the questions do — firing must happen once, and
    the day it happened on lasts all day.
    """
    return sorted((row for row in (_row(eid, rec, provider)
                                   for eid, rec in read(cid).items())
                   if _fixed(provider, row["date"]) == fixed),
                  key=lambda e: (e["name"], e["id"]))


def sooner(a: dict | None, b: dict | None) -> dict | None:
    """The nearer of two `{name, in_days}` notices, or whichever exists.

    One "Upcoming:" line has to carry both a holiday and a scheduled event, and
    the merge rule has two callers (`context.world_state._today_data` and
    `suggest.build_snapshot`) that must agree — a prompt section and the
    suggestion snapshot disagreeing about what is next is exactly the kind of
    drift that reads as the model inventing things. Ties go to `a`, the holiday:
    it is the one the reader did not have to write down.
    """
    if a is None:
        return b
    if b is None:
        return a
    return b if b["in_days"] < a["in_days"] else a


def day_facts(cid: str, croot, native: str) -> dict:
    """`{"events_today": [name], "upcoming": {name, in_days} | None}` for one
    moment — the campaign-level half of `calendars.today_facts`.

    Beside that function rather than inside it because scheduled events are
    campaign state and `today_facts` takes a calendar config: it knows nothing
    about a campaign and has no business reading one. The callers merge the two,
    through `sooner`.

    Takes `croot` as well as `cid` because both callers are already holding it
    (they just read the calendar config out of it) and resolving it again here
    would be a second walk to the same directory — the same reason
    `birthdays.crossed` takes a resolved provider.

    Tolerant end to end — a calendar that will not load, a moment this calendar
    cannot parse, an unreadable events.json — because the caller is a prompt
    section that must degrade to silence rather than fail a turn.
    """
    blank: dict = {"events_today": [], "upcoming": None}
    provider = calendars.primary_provider(croot)
    if provider is None or not native:
        return blank
    try:
        fixed = calendars.fixed_of(provider, native)
    except calendars.CalendarError:
        return blank
    ahead = upcoming(cid, provider, fixed)
    return {"events_today": [e["name"] for e in on_day(cid, provider, fixed)],
            "upcoming": {"name": ahead[0]["name"], "in_days": ahead[0]["in_days"]}
            if ahead else None}
