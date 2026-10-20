"""suggest: ideas responsibilities."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import _idea_provenance as idea_shapes
from .. import calendars, characters, clock, overlay, pcs
from ..campaigns import paths as campaigns_paths
from ..continuity import drivers as continuity_drivers
from ..continuity import effective
from . import _contracts, _dates, _references


def validate_ideas(cid: str, ideas: list[dict]) -> list[dict]:
    """Saved scene ideas (`scene_ideas.records`' shape) with every reference
    re-checked against the campaign as it stands now, and their driver
    provenance annotated (spec §17, §17.1).

    The read-side half of the scene ledger's validation (#88). It has to happen
    on every read, not only on write, because an idea is durable and a campaign
    is not -- the character it casts can be deleted and the location it names
    can be renamed between the day it was saved and the day it is picked, and a
    picker handed a dangling id would send it straight to `addCastBatch`.

    Each idea's own `pcless` decides which player tokens are legal, exactly as
    `offscreen` does for a fresh suggestion, so one read can hold ideas of both
    modes.

    **Provenance.** Each idea also gets `drivers` (`[{ref, action, kind, label,
    state}]`), `time_anchor` (`{ref, relation, native, kind, label, friendly,
    state}` or None), `stale_reason` and `anchor_date`:

    - every stored thread or commitment ref is first mapped through
      `effective.live_canon` and de-duplicated by its canonical (the first
      stored entry, and its action, wins): the file can hold a source and its
      canonical, since `scene_ideas` cannot canonicalize, and the read shows one;
    - each ref is classified `live`, `finished` (thread closed, commitment
      resolved, event passed or fired on a day that is not today, occurrence
      behind now) or `dangling` (record gone, or an event anchor whose stored
      day is no longer the event's: "moved"). Dangling refs are not returned, but `stale_reason` is
      derived from every stored ref BEFORE they go: a moved anchor, then a
      `before`/`by`/`on` anchor that has passed, then no stored ref live (a
      finished `after` anchor counts as live -- its passing is what the idea
      waits for);
    - labels are read, never stored: a record's title, an event's current name,
      a holiday's name from its ref (a bounded name's digest becomes "…"), a
      birthday's actor's current name, else the id;
    - `anchor_date` is the anchor's day when it is `on`, live and dated.

    **Unknown is never stale.** An unreadable ledger, status, calendar or clock
    classifies as live, and each idea is annotated inside its own guard, falling
    back to its stored refs unclassified -- so one bad read can never empty the
    saved list (the route's `_tolerant` answers `[]` on any exception).

    **Cost.** When no idea carries provenance, nothing beyond the reference
    check is read. Otherwise the ledgers, the live canon, the calendar and the
    clock are read once for the whole list (`_IdeaContext.load`). Nothing here
    writes.
    """
    check = _references.ref_validator(cid)
    rows = [{**i, **check(i["cast"], i["location"], i["date"], i["pcless"])}
            for i in ideas]
    if not any(idea_shapes.drivers(i.get("drivers")) or idea_shapes.anchor(i.get("time_anchor"))
               for i in rows):
        return [{**i, **_NO_ANNOTATION} for i in rows]
    ctx = _IdeaContext.load(cid)
    return [{**i, **_annotated(i, ctx)} for i in rows]


# ---- saved ideas: provenance on write, staleness on read (spec §17) ---------

_NO_ANNOTATION: dict[str, Any] = {"drivers": [], "time_anchor": None, "stale_reason": "",
                                  "anchor_date": ""}

#: `holiday:<fixed>:<name>` and `birthday:<kind>:<id>:<fixed>` (spec §4).
_HOLIDAY_REF = re.compile(r"^holiday:(-?\d+):(.+)$")
_BIRTHDAY_DAY_REF = re.compile(r"^birthday:([^:]+):([^:]+):(-?\d+)$")

#: What `notices._bounded` appends to a name it shortened.
_BOUNDED_DIGEST = re.compile(r"~[0-9a-f]{16}$")

#: The kinds whose refs name a stored record, which exists or does not.
_STORED_KINDS = ("thread", "commitment", "event")

#: The anchor relations whose anchor, once past, leaves nothing to wait for.
_PASSING_RELATIONS = ("before", "by", "on")


def _kind_of(ref: str) -> str:
    return ref.split(":", 1)[0]


def _occurrence_day(ref: str) -> int | None:
    """The fixed day a day-occurrence ref embeds, or None."""
    holiday = _HOLIDAY_REF.match(ref)
    if holiday:
        return int(holiday.group(1))
    birthday = _BIRTHDAY_DAY_REF.match(ref)
    return int(birthday.group(3)) if birthday else None


def _month_ref(ref: str) -> tuple[int, str] | None:
    """`_month_of`, for a ref whose head is a whole `birthday:<kind>:<id>`."""
    head = ref.rpartition(":month:")[0].split(":")
    return _dates._month_of(ref) if len(head) == 3 and all(head) else None


def _well_formed_occurrence(ref: str) -> bool:
    """Holidays and birthdays are computed, never stored, so a well-formed ref
    is all there is to test (Decision 16)."""
    return _occurrence_day(ref) is not None or _month_ref(ref) is not None


def _soft_ledgers(cid: str) -> effective.Ledgers | None:
    try:
        return effective.Ledgers.load(cid)
    except Exception:  # noqa: BLE001 -- existence is unknown then, and unknown keeps the ref
        return None


def _soft_canon(cid: str, ledgers: effective.Ledgers | None) -> dict[str, str]:
    try:
        return effective.live_canon(cid, ledgers)
    except Exception:  # noqa: BLE001 -- an unreadable continuity doc canonicalizes nothing (Decision 4)
        return {}


def _exists(ref: str, ledgers: effective.Ledgers | None) -> bool:
    """Existence in any status; an unreadable ledger keeps the ref."""
    kind = _kind_of(ref)
    if kind in _STORED_KINDS:
        return ledgers is None or ledgers.exists(ref) is not False
    return kind in _contracts.TEMPORAL_KINDS and _well_formed_occurrence(ref)


def _event_record(ledgers: effective.Ledgers | None, ref: str):
    """The raw events.json row for `event:<id>`: a dict, None when it is gone,
    or False when events.json cannot be read."""
    table = ledgers.events if ledgers is not None else None
    if not isinstance(table, dict):
        return False
    rec = table.get(ref.split(":", 1)[1])
    return rec if isinstance(rec, dict) else None


def _anchor_native(cid: str, ref: str, ledgers: effective.Ledgers | None) -> str | None:
    """`time_anchor.native` at save time: an event's stored date, a day
    occurrence's day in the calendar's notation (None without a calendar: no
    date is invented), a month ref's "" (it has no day)."""
    if _kind_of(ref) == "event":
        rec = _event_record(ledgers, ref)
        date = rec.get("date") if isinstance(rec, dict) else ""
        return date.strip() if isinstance(date, str) else ""
    if _month_ref(ref) is not None:
        return ""
    provider = _dates._soft_provider(campaigns_paths.campaign_root(cid))
    day = _occurrence_day(ref)
    native = _dates._plugin_soft(provider.format, day) if provider is not None else None
    return native if isinstance(native, str) and native else None


def _provenance_anchor(cid: str, raw, ledgers: effective.Ledgers | None,
                       canon: dict[str, str]) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("ref"), str):
        return None
    ref = canon.get(raw["ref"].strip(), raw["ref"].strip())
    if _kind_of(ref) not in _contracts.TEMPORAL_KINDS or not _exists(ref, ledgers):
        return None
    relation = raw.get("relation")
    relation = relation if isinstance(relation, str) and relation in _contracts.ANCHOR_RELATIONS else "on"
    native = _anchor_native(cid, ref, ledgers)
    if native is None:
        return None
    return {"ref": ref, "relation": "on" if _month_ref(ref) else relation, "native": native}


def _provenance_pair(entry, canon: dict[str, str]) -> dict | None:
    """One driver entry as a canonical `{ref, action}`, or None when it is
    malformed or its action is wrong for its kind."""
    pair = idea_shapes.drivers([entry])
    if not pair:
        return None
    ref = canon.get(pair[0]["ref"].strip(), pair[0]["ref"].strip())
    action = pair[0]["action"]
    allowed = continuity_drivers.ACTIONS_BY_KIND.get(_kind_of(ref), ())
    return {"ref": ref, "action": action} if action in allowed else None


def idea_provenance(cid: str, drivers, time_anchor) -> dict:
    """A saved idea's provenance, validated on write (spec §17, Decision 16):
    `{"drivers": [{ref, action}], "time_anchor": {ref, relation, native} | None}`.

    The test is **existence, not activity**: a closed thread, a resolved
    commitment and a fired event are all kept, because the read needs them to
    say why the idea went stale (Appendix A T2). Refs canonicalize through
    `effective.live_canon`; a pair whose action is wrong for its kind, a
    record that does not exist (an unreadable ledger keeps it) and a malformed
    occurrence ref are dropped, and the first entry for a ref wins. The anchor
    passes the same test, takes `on` for an unknown relation or a month ref,
    and stores its `native`; a dropped anchor takes its `anchor` entry with
    it, and any `anchor` entry naming another ref is dropped, so a record never
    claims two anchors.

    Reads no driver snapshot and no pressure, so a Save does no horizon work,
    and never raises for a malformed shape. It takes no `offscreen`: whether a
    ref exists does not depend on the mode.
    """
    entries = drivers if isinstance(drivers, list) else []
    if not entries and not isinstance(time_anchor, dict):
        return {"drivers": [], "time_anchor": None}
    ledgers = _soft_ledgers(cid)
    canon = _soft_canon(cid, ledgers)
    anchor = _provenance_anchor(cid, time_anchor, ledgers, canon)
    kept = anchor["ref"] if anchor else None
    out: list[dict] = []
    for entry in entries:
        pair = _provenance_pair(entry, canon)
        if pair is None or pair["ref"] in {p["ref"] for p in out}:
            continue
        if pair["action"] == "anchor" and pair["ref"] != kept:
            continue
        if _exists(pair["ref"], ledgers):
            out.append(pair)
    return {"drivers": out, "time_anchor": anchor}


@dataclass
class _IdeaContext:
    """What the saved-idea annotation reads, once per list: the ledgers, the
    live canon, the calendar and the clock, each read tolerantly."""
    cid: str
    ledgers: effective.Ledgers | None
    canon: dict[str, str]
    provider: Any
    now_fixed: int | None
    names: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, cid: str) -> _IdeaContext:
        ledgers = _soft_ledgers(cid)
        provider = _dates._soft_provider(campaigns_paths.campaign_root(cid))
        try:
            now = clock.now(cid)
        except Exception:  # noqa: BLE001 -- no readable present: nothing reads as behind it
            now = ""
        now_fixed = _dates._fixed(provider, now) if isinstance(now, str) else None
        return cls(cid, ledgers, _soft_canon(cid, ledgers), provider, now_fixed)


def _reading(kind: str, label: str, state: str, friendly: str = "", day: str = "",
             moved: bool = False) -> dict:
    return {"kind": kind, "label": label, "state": state, "friendly": friendly,
            "day": day, "moved": moved}


def _record_reading(ref: str, kind: str, ctx: _IdeaContext) -> dict:
    rid = ref.split(":", 1)[1]
    ledgers = ctx.ledgers
    known = ledgers.exists(ref) if ledgers is not None else None
    if ledgers is None or known is None:
        return _reading(kind, rid, "live")
    if not known:
        return _reading(kind, rid, "dangling")
    table = ledgers.threads if kind == "thread" else ledgers.commitments
    rec = table[rid] if isinstance(table, dict) else {}
    title = rec.get("title")
    label = title.strip() if isinstance(title, str) and title.strip() else rid
    return _reading(kind, label, "live" if effective.is_live(kind, rec.get("status"))
                    else "finished")


def _event_reading(ref: str, ctx: _IdeaContext, native: str) -> dict:
    eid = ref.split(":", 1)[1]
    rec = _event_record(ctx.ledgers, ref)
    if rec is False:
        return _reading("event", eid, "live")
    if rec is None:
        return _reading("event", eid, "dangling")
    name, date = rec.get("name"), rec.get("date")
    label = name.strip() if isinstance(name, str) and name.strip() else eid
    date = date.strip() if isinstance(date, str) else ""
    fixed = _dates._fixed(ctx.provider, date)
    friendly = _dates._friendly_of(ctx.provider, fixed)
    day = calendars.split_native(date)[0]
    stored = calendars.split_native(native)[0] if native else ""
    if stored and day and stored != day:
        return _reading("event", label, "dangling", friendly or day, moved=True)
    fired = isinstance(rec.get("fired"), dict) and bool(rec["fired"])
    passed = fixed is not None and ctx.now_fixed is not None and fixed < ctx.now_fixed
    # §13.5's own-day carve-out: reaching the day fires the event, and an idea
    # anchored on it is still for today, as a holiday or birthday on today is.
    # With no calendar or no `now`, whether the day is today is unknown, and
    # unknown is never stale (Decision 17); an undatable event on a readable
    # calendar has no day to be today, so fired still finishes it.
    unknown = ctx.provider is None or ctx.now_fixed is None
    today = fixed is not None and fixed == ctx.now_fixed
    finished = passed or (fired and not today and not unknown)
    return _reading("event", label, "finished" if finished else "live", friendly,
                    day if fixed is not None else "")


def _actor_name(ctx: _IdeaContext, actor: str) -> str:
    """The actor's current name, else its id."""
    if actor not in ctx.names:
        kind, _, aid = actor.partition(":")
        name = aid
        try:
            if kind == "pcs":
                name = pcs.name_of(overlay.pc_root(ctx.cid, aid), aid)
            elif kind == "characters":
                name = characters.birthdate_meta(overlay.char_root(ctx.cid, aid), aid)[0]
        except (characters.CharacterNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            name = aid
        ctx.names[actor] = name if isinstance(name, str) and name.strip() else aid
    return ctx.names[actor]


def _occurrence_label(ref: str, ctx: _IdeaContext) -> str:
    """A holiday's name from its ref (a bounded name's digest read as "…"); a
    birthday's actor's current name."""
    if _kind_of(ref) == "holiday":
        return _BOUNDED_DIGEST.sub("…", ref.split(":", 2)[2])
    body = ref[len("birthday:"):]
    head = body.rpartition(":month:")[0] if ":month:" in body else body.rpartition(":")[0]
    return _actor_name(ctx, head)


def _month_reading(ref: str, month: tuple[int, str], ctx: _IdeaContext) -> dict:
    """A month ref is behind now by `(year, month index)` order, not by key."""
    label = _occurrence_label(ref, ctx)
    year, key = month
    listed = _dates._plugin_soft(ctx.provider.months, year) if ctx.provider is not None else None
    months = listed if isinstance(listed, list) else []
    found = next((i for i, m in enumerate(months)
                  if isinstance(m, dict) and str(m.get("key")).casefold() == key.casefold()),
                 None)
    if found is None:
        return _reading(_kind_of(ref), label, "live")
    friendly = f"{months[found].get('name', key)} {year}"
    now = (_dates._plugin_soft(ctx.provider.describe, ctx.now_fixed)
           if ctx.now_fixed is not None else None)
    try:
        behind = isinstance(now, dict) and (year, found) < (int(now["year"]), int(now["month"]) - 1)
    except (KeyError, TypeError, ValueError):
        behind = False
    return _reading(_kind_of(ref), label, "finished" if behind else "live", friendly)


def _occurrence_reading(ref: str, ctx: _IdeaContext) -> dict:
    month = _month_ref(ref)
    if month is not None:
        return _month_reading(ref, month, ctx)
    day = _occurrence_day(ref)
    if day is None:
        raise ValueError(f"not an occurrence ref: {ref!r}")
    behind = ctx.now_fixed is not None and day < ctx.now_fixed
    native = _dates._plugin_soft(ctx.provider.format, day) if ctx.provider is not None else None
    return _reading(_kind_of(ref), _occurrence_label(ref, ctx),
                    "finished" if behind else "live", _dates._friendly_of(ctx.provider, day),
                    native if isinstance(native, str) else "")


def _classify(ref: str, ctx: _IdeaContext, native: str = "") -> dict:
    """One stored ref's reading: kind, label, state (`live` / `finished` /
    `dangling`), friendly day, the day itself, and whether it moved."""
    kind = _kind_of(ref)
    if kind in ("thread", "commitment"):
        return _record_reading(ref, kind, ctx)
    if kind == "event":
        return _event_reading(ref, ctx, native)
    if kind in _contracts.TEMPORAL_KINDS:
        return _occurrence_reading(ref, ctx)
    raise ValueError(f"not a driver ref: {ref!r}")


def _stale_reason(anchor: dict | None, reading: dict | None, states: dict[str, dict]) -> str:
    """Spec §17.1, first match wins. `states` is every stored ref's reading,
    keyed by canonical ref, the anchor's already counted live when it is a
    finished `after` anchor."""
    if anchor is not None and reading is not None:
        if reading["moved"]:
            return f"{reading['label']} moved to {reading['friendly']}"
        if anchor["relation"] in _PASSING_RELATIONS and reading["state"] == "finished":
            # The chip's label is a birthday's bare actor name; the sentence
            # names the birthday, or it reads as a death notice.
            subject = (f"{reading['label']}'s birthday" if reading["kind"] == "birthday"
                       else reading["label"])
            return f"{subject} has passed"
    if not states or any(r["state"] == "live" for r in states.values()):
        return ""
    remaining = {r["kind"] for r in states.values() if r["state"] != "dangling"}
    if not remaining:
        return "Its drivers no longer exist"
    if remaining == {"thread"}:
        return "Every thread it was about is closed"
    if remaining == {"commitment"}:
        return "Every commitment it was about is resolved"
    return "Nothing it was about is still open"


def _anchor_reading(idea: dict, ctx: _IdeaContext) -> tuple[dict, dict] | None:
    """The stored anchor (its ref canonical) and its reading, or None."""
    stored = idea_shapes.anchor(idea.get("time_anchor"))
    if stored is None:
        return None
    anchor = {**stored, "ref": ctx.canon.get(stored["ref"], stored["ref"])}
    return anchor, _classify(anchor["ref"], ctx, stored["native"])


def _anchor_fields(found: tuple[dict, dict] | None) -> dict:
    """`time_anchor` and `anchor_date`: none for a dangling anchor; a date
    only for a live, dated `on` anchor (§17.1's server-supplied anchor date)."""
    if found is None or found[1]["state"] == "dangling":
        return {"time_anchor": None, "anchor_date": ""}
    anchor, reading = found
    on_live = anchor["relation"] == "on" and reading["state"] == "live"
    return {"time_anchor": {**anchor, "kind": reading["kind"], "label": reading["label"],
                            "friendly": reading["friendly"], "state": reading["state"]},
            "anchor_date": reading["day"] if on_live else ""}


def _annotate(idea: dict, ctx: _IdeaContext) -> dict:
    found = _anchor_reading(idea, ctx)
    readings: dict[str, dict] = {}
    drivers: list[dict] = []
    for entry in idea_shapes.drivers(idea.get("drivers")):
        ref = ctx.canon.get(entry["ref"], entry["ref"])
        if ref in readings:
            continue
        readings[ref] = found[1] if found and ref == found[0]["ref"] else _classify(ref, ctx)
        drivers.append({"ref": ref, "action": entry["action"]})
    liveness = dict(readings)
    anchor, reading = found if found is not None else (None, None)
    if anchor is not None and reading is not None:
        waiting = anchor["relation"] == "after" and reading["state"] == "finished"
        liveness[anchor["ref"]] = {**reading, "state": "live"} if waiting else reading
    return {"drivers": [{**d, "kind": readings[d["ref"]]["kind"],
                         "label": readings[d["ref"]]["label"],
                         "state": readings[d["ref"]]["state"]}
                        for d in drivers if readings[d["ref"]]["state"] != "dangling"],
            "stale_reason": _stale_reason(anchor, reading, liveness),
            **_anchor_fields(found)}


def _unannotated(idea: dict) -> dict:
    """The fallback when an idea's annotation raised: its stored refs, as live
    and labelled by themselves, and nothing stale."""
    anchor = idea_shapes.anchor(idea.get("time_anchor"))
    return {"drivers": [{**e, "kind": _kind_of(e["ref"]), "label": e["ref"], "state": "live"}
                        for e in idea_shapes.drivers(idea.get("drivers"))],
            "time_anchor": ({**anchor, "kind": _kind_of(anchor["ref"]), "label": anchor["ref"],
                             "friendly": "", "state": "live"} if anchor else None),
            "stale_reason": "", "anchor_date": ""}


def _annotated(idea: dict, ctx: _IdeaContext) -> dict:
    try:
        return _annotate(idea, ctx)
    except Exception:  # noqa: BLE001 -- the annotation is an enhancement; the idea must still list
        return _unannotated(idea)
