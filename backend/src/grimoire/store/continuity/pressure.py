"""Temporal pressure: one dated item list over events, holidays, birthdays and
deadlines (spec §13), with one state contract every reader shares.

It **composes and never re-scans**. Events come from `events.list_events`,
holidays from `calendars.upcoming_holidays`, birthdays from
`birthdays.occurrences`; what this module adds is one axis (integer fixed days
on the campaign's *primary* calendar), one item shape and one precedence for
what state an item is in (`state_of`). A reader that wants to know whether
something is overdue asks here rather than doing its own arithmetic, so the
suggestion snapshot, the drivers projection and anything after them cannot
disagree about it.

Read-only: it writes nothing and takes no lock, so it is in none of
`locks.DOMAIN_MODULES`, `OUTSIDE_DOMAIN` or `UNREVIEWED`. And **`clock` must
never import it** -- `clock` is a source this composes (`clock.now`), and the
advance digest has its own, older reading of the same records.

**Each source fails soft on its own.** A calendar provider can be user plugin
code that raises anything (`calendars.primary_provider` catches only
`CalendarError`/`KeyError`), so every source -- and the provider and `now`
resolutions themselves, and every per-day call into the provider (`_fixed_of`,
`_labels`) -- runs through `_soft`. A broken calendar degrades the list to
undated items rather than failing the read, and one day a plugin cannot name
costs that item's label rather than its source; nothing invents a date
(spec §3.8, §26). With no readable `now` there is nothing to measure from:
events are still listed, undated relative to now (`in_days: None`, `ok`), and
holidays and birthdays, which only exist relative to a window, are absent.

**Windows.** Every unfired event is listed whatever its distance -- an event is
the campaign's own finite list. Holidays and birthdays are bounded by
`horizon`. Birthdays include today (`birthdays.occurrences` scans
`[now, now + horizon]`). Holidays are asked for from the day *before* now, one
day wider -- `upcoming_holidays` is half-open at its start, `(native,
native + window]` -- so today's observances are included too, and each item's
`in_days` is recomputed from now rather than taken from the shifted row.
Deadlines are not bounded at all: every parseable due is listed.

**Deadlines** (spec §13.5, Decision 8) are read off live *canonical*
commitments (`effective`), so a merged-away source's due is never inherited and
never a second item. Each commitment's candidates -- its parseable due (read
through `aging`, the one place a `due` is parsed) and each `before`/`by`/`on`
link to an event (D-1, D, D) -- are each given a state, and `_choose` keeps the
most urgent, then the earliest. A link to a *reached* event (fired stamp, or
`passed`) is `overdue` whatever the arithmetic says, except a `by`/`on` link on
the event's own day; a fired stamp needs no calendar, so it survives a broken
plugin as an undated `overdue` item. An `after` link sets no deadline and is
listed only while it is `upcoming`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from .. import aging, birthdays, calendars, clock, events, notices
from ..campaigns import paths as campaigns_paths
from . import effective

#: The item kinds, which are also the source names `build` accepts. The order
#: is the tie-break between items on one fixed day.
SOURCES = KINDS = ("event", "holiday", "birthday", "deadline", "linked_deadline")
ALL = frozenset(SOURCES)

#: The decision precedence: `state_of` answers the first that applies, and
#: `PRESSURE_STATES.index` is what "more urgent" means everywhere in the backend.
PRESSURE_STATES = ("overdue", "passed", "today", "due_soon", "upcoming", "stale", "ok")

#: The order a reader is shown states in (spec §16.2). Declared here, beside the
#: precedence it deliberately differs from, so the client mirrors one tuple.
SORT_ORDER = ("overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok")

HIGH_PRESSURE = frozenset({"overdue", "today", "due_soon"})

_DEADLINE_RELATIONS = frozenset({"before", "by", "on"})

#: How many days before the event's day each deadline-setting link falls due
#: (spec §13.5): `before` is D-1, `by` and `on` are D.
_LINK_OFFSET = {"before": 1, "by": 0, "on": 0}

#: The link relations a commitment can carry to an event; `related_to` is none.
_LINK_RELATIONS = _DEADLINE_RELATIONS | {"after"}

#: The sources that yield per-commitment *candidates* rather than items;
#: `_choose` turns them into at most one deadline item per commitment.
_CANDIDATE_KINDS = frozenset({"deadline", "linked_deadline"})

T = TypeVar("T")


def _soft(fn: Callable[..., T], fallback: T, *args: Any) -> T:
    """`fn(*args)`, or `fallback` when it raises anything at all."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- a source may run user calendar plugin code, which can raise anything; one source failing must cost only its own items
        return fallback


def _is_deadline(kind: str, relation: str) -> bool:
    return kind == "deadline" or (kind == "linked_deadline" and relation in _DEADLINE_RELATIONS)


def _reached_state(kind: str, relation: str, in_days: int | None) -> str:
    """A deadline whose event is reached is overdue -- except a `by`/`on` link on
    the event's own day, which is still that day's business."""
    if kind == "linked_deadline" and relation in {"by", "on"} and in_days == 0:
        return "today"
    return "overdue"


def _ahead_state(deadline: bool, in_days: int, warn_days: int) -> str:
    if deadline and warn_days > 0 and 0 < in_days <= warn_days:
        return "due_soon"
    if 0 < in_days <= calendars.UPCOMING_WINDOW_DAYS:
        return "upcoming"
    return "ok"


def state_of(kind: str, relation: str, in_days: int | None, *, warn_days: int,
             passed: bool = False, reached: bool = False) -> str:
    """The item's state under spec §13.4's precedence (first match wins).

    Pure. A *deadline kind* is `deadline`, or a `linked_deadline` whose relation
    sets one (`before`/`by`/`on`); an `after` link never does. `reached` forces
    `overdue` on a deadline kind (a stamp, not arithmetic, so it holds with no
    date at all) except on the event's own day for `by`/`on`. Never `stale`:
    that is a driver's reading of aging, not of a date.
    """
    deadline = _is_deadline(kind, relation)
    if deadline and reached:
        return _reached_state(kind, relation, in_days)
    if deadline and in_days is not None and in_days < 0:
        return "overdue"
    if passed:
        return "passed"
    if in_days is None:
        return "ok"
    if in_days == 0:
        return "today"
    return _ahead_state(deadline, in_days, warn_days)


def _labels(provider, fixed: int | None) -> tuple[str, str]:
    """`(native, friendly)` for a fixed day, or blanks when it cannot be rendered.

    Soft by construction, like `_fixed_of`: both run provider code on one day,
    so one day a plugin cannot handle costs that day's labels (or date), never
    the item, the source or the read.
    """
    if provider is None or fixed is None:
        return "", ""
    return _soft(lambda: (provider.format(fixed), provider.describe(fixed)["friendly"]),
                 ("", ""))


def _fixed_of(provider, native: str) -> int | None:
    if provider is None or not native:
        return None
    return _soft(calendars.fixed_of, None, provider, native)


def _item(kind: str, ref: str, label: str, fixed: int | None, ctx: dict, **extra) -> dict:
    """One item in Decision 4's shape; `extra` overrides the defaults."""
    now_fixed = ctx["now_fixed"]
    in_days = fixed - now_fixed if fixed is not None and now_fixed is not None else None
    native, friendly = _labels(ctx["provider"], fixed)
    out: dict[str, Any] = {
        "ref": ref, "kind": kind, "label": label, "native": native,
        "friendly": friendly, "fixed": fixed, "in_days": in_days,
        "relation": "on", "state": "ok", "subject": None, "due_text": "",
        "precision": "exact" if fixed is not None else None,
        "actor": None, "age": None}
    out.update(extra)
    out["state"] = state_of(kind, out["relation"], out["in_days"],
                            warn_days=ctx["warn_days"], passed=extra.get("passed", False),
                            reached=extra.get("reached", False))
    out.pop("passed", None)
    out.pop("reached", None)
    return out


def _event_items(ctx: dict) -> list[dict]:
    """Every unfired event, plus fired ones on today (Decision 6). `passed` is
    `events`' reading, recomputed on the day dated here: the provider-less row
    fallback (`_event_rows`) cannot read it, and losing it would turn a day the
    clock has gone by into an `ok` item a suggestion could date itself against."""
    out = []
    now_fixed = ctx["now_fixed"]
    for row in ctx["events"]:
        fixed = _fixed_of(ctx["provider"], row["date"])
        today = fixed is not None and fixed == now_fixed
        if row["fired"] is not None and not today:
            continue
        passed = (row["fired"] is None and fixed is not None and now_fixed is not None
                  and fixed < now_fixed)
        out.append(_item("event", f"event:{row['id']}", row["name"], fixed, ctx,
                         native=row["date"], passed=passed))
    return out


def _holiday_items(ctx: dict) -> list[dict]:
    """Observances in `[now, now + horizon]`, one item per ref (Decision 7)."""
    provider, now_fixed = ctx["provider"], ctx["now_fixed"]
    if provider is None or now_fixed is None:
        return []
    cfg = calendars.read_calendar(ctx["croot"])
    rows = calendars.upcoming_holidays(cfg, provider.format(now_fixed - 1), ctx["horizon"] + 1)
    unique: dict[str, dict] = {}
    for h in sorted(rows, key=lambda h: h["fixed"]):
        ref = notices.holiday_key(h["fixed"], h["name"])
        unique.setdefault(ref, _item("holiday", ref, h["name"], h["fixed"], ctx))
    return list(unique.values())


def _birthday_items(ctx: dict) -> list[dict]:
    provider, now_fixed = ctx["provider"], ctx["now_fixed"]
    if provider is None or now_fixed is None:
        return []
    out = []
    for occ in birthdays.occurrences(ctx["cid"], now_fixed, ctx["horizon"], provider=provider):
        relation = "in_month" if occ["precision"] == "month" else "on"
        out.append(_item("birthday", occ["ref"], occ["name"], occ["fixed"], ctx,
                         native=occ["native"], friendly=occ["friendly"],
                         relation=relation, precision=occ["precision"],
                         actor=occ["actor"], age=occ["age"]))
    return out


def _owed_item(ctx: dict, row: dict, kind: str, ref: str, fixed: int | None,
               **extra) -> dict:
    """A deadline-kind item for one live canonical commitment `row`."""
    return _item(kind, ref, row["title"], fixed, ctx, subject=f"commitment:{row['id']}",
                 due_text=row["due"], **extra)


def _due_fixed(actx: dict, due: str) -> int | None:
    """The fixed day a parseable `due` names, through `aging`'s one reading of
    it; None for free text or no due. Only the due is handed over: the scene a
    record last moved in is staleness, which a deadline does not need."""
    base = actx["now_fixed"]
    if base is None or not due:
        return None
    block = aging.age(actx, {"due": due})
    if block["due_in"] is not None:
        return base + block["due_in"]
    if block["days_over"] is not None:
        return base - block["days_over"]
    return None


def _deadline_candidates(ctx: dict) -> list[dict]:
    """One `deadline` candidate per live canonical commitment with a parseable
    due. `aging.prepare` resolves the provider outside any `try` (plugin code),
    which is why this whole source sits inside `build`'s `_soft`; one due a
    calendar cannot read costs only its own candidate."""
    actx = aging.prepare(ctx["cid"], ctx["now"])
    if ctx["now_fixed"] is None:
        return []
    out = []
    for row in effective.commitments(ctx["cid"]):
        fixed = _soft(_due_fixed, None, actx, row["due"])
        if fixed is not None:
            out.append(_owed_item(ctx, row, "deadline", f"commitment:{row['id']}", fixed))
    return out


def _after_item(ctx: dict, link: dict, row: dict, day: int | None,
                reached: bool) -> dict | None:
    """An `after` link sets no deadline; it is listed only while its event is
    unreached and `0 < in_days <= UPCOMING_WINDOW_DAYS` (Decision 8), so it can
    only ever be `upcoming`."""
    now_fixed = ctx["now_fixed"]
    if reached or day is None or now_fixed is None:
        return None
    if not 0 < day - now_fixed <= calendars.UPCOMING_WINDOW_DAYS:
        return None
    return _owed_item(ctx, row, "linked_deadline", link["b"], day, relation="after",
                      _lid=link["id"])


def _link_candidate(ctx: dict, link: dict, row: dict, event: dict) -> dict | None:
    """One link's candidate, or None. Reached is a stamp (`fired`) or a reading
    (`passed`); a fired event stays a candidate with no date and no `now` at
    all, because nothing has to be computed to know it was reached."""
    relation = link["relation"]
    day = _fixed_of(ctx["provider"], event["date"])
    fired = event["fired"] is not None
    reached = fired or bool(event["passed"])
    if relation == "after":
        return _after_item(ctx, link, row, day, reached)
    if not fired and (day is None or ctx["now_fixed"] is None):
        return None
    fixed = day - _LINK_OFFSET[relation] if day is not None else None
    return _owed_item(ctx, row, "linked_deadline", link["b"], fixed, relation=relation,
                      reached=reached, _lid=link["id"])


def _linked_candidates(ctx: dict) -> list[dict]:
    """A candidate per `commitment --before|by|on|after--> event` link, on
    canonical endpoints. A thread's links, a resolved commitment's, a dangling
    or an undated event's and `related_to` contribute nothing."""
    owed = {f"commitment:{c['id']}": c for c in effective.commitments(ctx["cid"])}
    dated = {f"event:{r['id']}": r for r in ctx["events"]}
    out = []
    for link in effective.links(ctx["cid"]):
        row, event = owed.get(link["a"]), dated.get(link["b"])
        if row is None or event is None or link["relation"] not in _LINK_RELATIONS:
            continue
        candidate = _soft(_link_candidate, None, ctx, link, row, event)
        if candidate is not None:
            out.append(candidate)
    return out


def _urgency(item: dict) -> tuple:
    """Most urgent, then earliest (nulls last), then `deadline` before
    `linked_deadline`, then the lowest link id (Decision 8)."""
    fixed = item["fixed"]
    return (PRESSURE_STATES.index(item["state"]), fixed is None, fixed or 0,
            item["kind"] != "deadline", item.get("_lid", ""))


def _choose(candidates: list[dict]) -> list[dict]:
    """One deadline item per commitment, plus every `after` item.

    Ranking by state before date is what keeps "reached -> overdue" true when
    an earlier due exists, a fired event was re-dated, or the clock went back;
    among unreached candidates the state is monotone in `in_days`, so it is
    exactly "earliest wins"."""
    best: dict[str, dict] = {}
    after: list[dict] = []
    for item in candidates:
        if item["relation"] == "after":
            after.append(item)
        elif item["subject"] not in best or _urgency(item) < _urgency(best[item["subject"]]):
            best[item["subject"]] = item
    chosen = [*best.values(), *after]
    for item in chosen:
        item.pop("_lid", None)
    return chosen


_SOURCE_FNS: dict[str, Callable[[dict], list[dict]]] = {
    "event": _event_items,
    "holiday": _holiday_items,
    "birthday": _birthday_items,
    "deadline": _deadline_candidates,
    "linked_deadline": _linked_candidates,
}


def _requested(sources: Iterable[str]) -> frozenset[str]:
    wanted = frozenset(sources)
    unknown = wanted - ALL
    if unknown:
        raise ValueError(f"unknown pressure source(s): {sorted(unknown)}")
    return wanted


def _context(cid: str, now: str | None, horizon: int | None, wanted: frozenset[str]) -> dict:
    """Everything the sources share, each part resolved once and softly."""
    croot = campaigns_paths.campaign_root(cid)
    provider = _soft(calendars.primary_provider, None, croot)
    if now is None:
        now = _soft(clock.now, "", cid)
    now_fixed = _fixed_of(provider, now) if now else None
    rows: list[dict] = []
    if wanted & {"event", "linked_deadline"}:
        rows = _event_rows(cid, provider, now_fixed)
    return {"cid": cid, "croot": croot, "provider": provider, "now": now or "",
            "now_fixed": now_fixed,
            "horizon": max(calendars.UPCOMING_WINDOW_DAYS if horizon is None else horizon, 0),
            "warn_days": _soft(calendars.warn_days, calendars.WARN_DAYS, croot),
            "events": rows}


def _event_rows(cid: str, provider, now_fixed: int | None) -> list[dict]:
    """`events.list_events`, read once. When the provider raises on some row's
    day (plugin code, anything at all), the rows are read again without it: no
    calendar code runs then, so every event is still listed. Each item is then
    dated, labelled and read for `passed` by this module, softly and one day
    at a time, so the fallback costs no item anything the provider can still
    answer for it."""
    rows = _soft(events.list_events, None, cid, provider, now_fixed)
    return _soft(events.list_events, [], cid) if rows is None else rows


def _order(item: dict) -> tuple:
    fixed = item["fixed"]
    return (fixed is None, fixed or 0, KINDS.index(item["kind"]), item["ref"],
            item["subject"] or "")


def build(cid: str, now: str | None = None, horizon: int | None = None,
          sources: Iterable[str] = ALL) -> dict:
    """`{now, friendly, fixed, items}` for this campaign's present.

    `now` None reads the campaign clock; `horizon` None is
    `calendars.UPCOMING_WINDOW_DAYS` and bounds only holidays and birthdays.
    `sources` restricts which sources run at all (an unknown name is a
    `ValueError`); the deadline choice runs over whatever candidates the
    requested deadline sources produced, so a commitment yields at most one
    `deadline`/`linked_deadline` item (plus its `after` items). Items are on
    the fixed-day axis, undated last, ties in `KINDS` order. Never raises for
    an existing campaign.
    """
    wanted = _requested(sources)
    ctx = _context(cid, now, horizon, wanted)
    items: list[dict] = []
    candidates: list[dict] = []
    for kind, fn in _SOURCE_FNS.items():
        if kind in wanted:
            (candidates if kind in _CANDIDATE_KINDS else items).extend(_soft(fn, [], ctx))
    items.extend(_choose(candidates))
    items.sort(key=_order)
    return {"now": ctx["now"], "friendly": _labels(ctx["provider"], ctx["now_fixed"])[1],
            "fixed": ctx["now_fixed"], "items": items}
