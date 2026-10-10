"""Ephemeral scene-suggestion and scene-intent prompt builder/parser: assemble
deterministic campaign signals (a story-so-far anchor, open plot threads with
dormancy, a status-annotated cast, calendar facts at the current moment, seedable
ids), build the one-shot prompt, and parse the model's proposed openings (or, for
scene-intent, the metadata implied by the user's own typed description).
Assembly + prompt/parse only; the LLM call lives in the route (mirrors
absorb/prompt.py) and the prompt text in templates/scene_suggestions/ and
templates/scene_intent/.

The suggestion snapshot consumes the continuity projections (capstone spec
§15): the commitments, a dated timeline (`pressure.build`'s items), the driver
index (`drivers.snapshot`'s drivers), its anchors and the reviewed links
between drivers -- one pressure computation at one `now`, shared by the
timeline and the index. `build_snapshot(drivers=False)` is the intent prompt's
legacy shape: exactly the keys it always had, and no pressure or driver work.

The prompt renders that capture through `driver_view`, which caps the index
and the timeline at `DRIVER_PROMPT_CAP` while always keeping what the reader's
`Controls` pin. The instruction section of the system message never changes;
the driver contract and the controls are addenda after it, so a campaign with
nothing to drive and no control set gets the message it always had.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import prompts, schemas
from . import (
    birthdays,
    calendars,
    characters,
    chronicle,
    clock,
    events,
    greetings,
    overlay,
    pcs,
    playing,
)
from .appearances import cast as appearances_cast
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths
from .continuity import drivers as continuity_drivers
from .continuity import effective, pressure


def _char_name(aroot, aid: str) -> str:
    """`aroot` is an `appearances.locked_actor_root`; callers pass roster ids."""
    try:
        return characters.read_character(aroot, aid)["meta"].get("name", aid)
    except characters.CharacterNotFound:
        return aid


def _tok(ref: str) -> str:
    kind, _, aid = str(ref).partition("/")
    return f"{kind}:{aid}"


#: Beyond this many months, the month list is dropped rather than trimmed. A
#: truncated vocabulary reads as a complete one and would teach a model that
#: the months it was not shown do not exist -- worse than the example alone.
#: Every real calendar is far under it (Gregorian 12, Hebrew 13 in a leap year).
NOTATION_MONTH_LIMIT = 24


def _notation(primary: dict, now: str) -> dict:
    """How THIS campaign's calendar writes a date, for the prompt to quote.

    `{"example": "5786-Kislev-25", "months": ["Tishrei", ...]}`, or blanks.

    Every prompt shows the moment as `friendly` ("25 Kislev 5786"), and the two
    that ask for a date back used to say "in the same notation as the current
    date" -- which no model can do for a notation it has never been shown, since
    `friendly` is not one `date_normalizer` reads back. Gregorian only ever
    survived that on luck: its native form is ISO-8601, which is what a model
    writes into JSON unprompted. Hebrew, and every hand-written plugin, lost
    the date silently. This is what those prompts quote instead.

    Built from the `CalendarProvider` contract alone -- `format` for the
    canonical spelling, `months(year)` for the keys `<year>-<key>-<day>` is
    composed from -- so a plugin author gets this by implementing nothing.

    Takes the primary calendar BLOCK rather than the campaign root, so the one
    `read_calendar` the caller already does serves this too -- resolving it here
    would parse calendar.json a second time in the same snapshot.

    Tolerant exactly as far as the `today_facts` call beside it: an unloadable
    provider or a missing key costs the notation hint and nothing else, while a
    `describe` that is not a mapping at all is left to fail, because that breaks
    every date in the app and is not this function's to hide (the rule
    `clock._holidays` states, and `calendars.resolve` follows).

    Scoped to the year of `now`. A suggestion that skips far enough to cross
    into a year with DIFFERENT months (Hebrew leap years carry Adar I and Adar
    II in place of Adar) is listing the wrong set -- accepted because the
    crossing needs a half-year skip, `hebrew.parse` folds a plain Adar onto the
    observance month anyway, and `resolve` still recognises the friendly form.
    """
    blank = {"example": "", "months": []}
    try:
        provider = calendars.get_provider(primary)
        fixed = calendars.fixed_of(provider, now)
        example = provider.format(fixed)
    except (calendars.CalendarError, KeyError, ValueError, OverflowError, OSError):
        return blank
    try:
        # Separately, so the two halves fail apart: the example is the half that
        # actually teaches the notation, and a calendar that will not enumerate
        # its months should not take it down too.
        months = [str(m["key"]) for m in provider.months(provider.describe(fixed)["year"])]
    except (calendars.CalendarError, KeyError, ValueError, OverflowError, OSError):
        months = []
    return {"example": example, "months": months if len(months) <= NOTATION_MONTH_LIMIT else []}


def _blank_calendar_facts() -> dict:
    return {"notation": {"example": "", "months": []}, "friendly": "", "holidays_today": [],
            "upcoming": None, "events_today": [], "birthdays": []}


def _calendar_facts(cid: str, croot, now: str, roster: list[dict]) -> dict:
    """`{notation, friendly, holidays_today, upcoming, events_today, birthdays}`
    at `now`, or every field blank when the calendar cannot answer.

    The narrower catches inside are exactly what this block always absorbed.
    The broad one around it is for what used to escape: a primary calendar is
    resolved by every read here (`_notation`, `today_facts`, `day_facts`,
    `birthdays.upcoming`), and a user plugin's constructor can raise anything,
    which used to fail the whole snapshot -- and the route with it -- rather
    than degrade the prompt to an undated one (capstone spec §26).
    """
    try:
        return _calendar_reads(cid, croot, now, roster)
    except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything; §26 degrades to undated
        return _blank_calendar_facts()


def _calendar_reads(cid: str, croot, now: str, roster: list[dict]) -> dict:
    out = _blank_calendar_facts()
    if now:
        cal_cfg = calendars.read_calendar(croot)
        out["notation"] = _notation(cal_cfg["primary"], now)
        upcoming = None
        try:
            facts = calendars.today_facts(cal_cfg, now)
            out["friendly"], out["holidays_today"], upcoming = (
                facts["friendly"], facts["holidays_today"], facts["upcoming"])
        except (calendars.CalendarError, KeyError):
            pass
        # The campaign's scheduled events (#101), merged into the same two
        # fields the calendar's holidays feed — the same merge, through the same
        # `sooner`, that the Today prompt section makes. A suggestion prompt that
        # knew about the coronation while the scene block did not (or the other
        # way round) is exactly the drift that reads as the model inventing
        # things. `day_facts` is tolerant end to end, so no guard here.
        scheduled = events.day_facts(cid, croot, now)
        out["events_today"] = scheduled["events_today"]
        out["upcoming"] = events.sooner(upcoming, scheduled["upcoming"])
    out["birthdays"] = birthdays.upcoming(cid, now, roster, visible_characters=True)
    return out


#: The floor of the near-future window, in days: `near` asks for a date in
#: `[now, now + max(warn_days, NEAR_MIN_DAYS)]` (spec §15), so a campaign that
#: warns late, or not at all, still has a week to place a near scene in.
NEAR_MIN_DAYS = 7

#: A commitment's reading when no driver answers for it.
_NO_READING = {"state": "ok", "in_days": None, "friendly": ""}

#: The kinds whose timeline items `events.sooner` can pick: the Upcoming line
#: merges the calendar's holidays and the campaign's scheduled events only.
_SOONER_KINDS = frozenset({"event", "holiday"})


def _dormancy_of(scene_ids: list[str]) -> Callable[[str], int]:
    """Scenes since `last_scene`, over the chronicle's sorted scene ids."""
    def dormancy(last_scene: str) -> int:
        if last_scene and last_scene in scene_ids:
            return len(scene_ids) - 1 - scene_ids.index(last_scene)
        return len(scene_ids)  # unknown/missing last_scene (deleted or not-yet-absorbed scene) -> treat as maximally cold
    return dormancy


def _commitment_rows(cid: str) -> list[dict]:
    try:
        return effective.commitments_or_physical(cid)
    except Exception:  # noqa: BLE001 -- garbled commitments.json costs the commitments, never the snapshot
        return []


def _pressure_of(cid: str, now: str) -> dict:
    try:
        return pressure.build(cid, now=now)
    except Exception:  # noqa: BLE001 -- pressure is meant never to raise; if it does, the prompt loses its dates, not its drivers
        return {"now": now, "friendly": "", "fixed": None, "items": []}


def _near_days(croot) -> int:
    try:
        return max(calendars.warn_days(croot), NEAR_MIN_DAYS)
    except Exception:  # noqa: BLE001 -- an unreadable calendar.json still gets the floor
        return NEAR_MIN_DAYS


def _links_among(index: list[dict]) -> list[dict]:
    """`[{id, a, b, relation}]` rebuilt from each driver's own `links`, one per
    link id, keeping only links whose two endpoints are both in `index`."""
    refs = {d["ref"] for d in index}
    out: list[dict] = []
    seen: set[str] = set()
    for d in index:
        for link in d["links"]:
            if link["id"] in seen:
                continue
            seen.add(link["id"])
            if link["direction"] == "out":
                a, b = d["ref"], link["other"]
            elif link["direction"] == "in":
                a, b = link["other"], d["ref"]
            else:
                a, b = sorted((d["ref"], link["other"]))
            if a in refs and b in refs:
                out.append({"id": link["id"], "a": a, "b": b, "relation": link["relation"]})
    return out


def _sooner_ref(timeline: list[dict], upcoming: dict | None) -> str:
    """The timeline item `events.sooner` picked for the Upcoming line, or ""."""
    if not upcoming:
        return ""
    return next((i["ref"] for i in timeline
                 if i["kind"] in _SOONER_KINDS and i["label"] == upcoming["name"]
                 and i["in_days"] == upcoming["in_days"]), "")


def _driver_capture(cid: str, offscreen: bool, now: str, croot, open_threads: list[dict],
                    dormancy: Callable[[str], int], upcoming: dict | None) -> dict:
    """The snapshot's continuity half: commitments, timeline, driver index,
    anchors, links, the near window and the Upcoming pick's ref. One
    `pressure.build` feeds both the timeline and the index."""
    p = _pressure_of(cid, now)
    snap = continuity_drivers.snapshot(cid, offscreen, pressure_result=p)
    by_ref = {d["ref"]: d for d in snap["drivers"]}
    for t in open_threads:
        t["ref"] = f"thread:{t['id']}"
    commitments = [{**c, "ref": f"commitment:{c['id']}",
                    "dormancy": dormancy(c.get("last_scene", ""))}
                   for c in _commitment_rows(cid)]
    for c in commitments:
        found = by_ref.get(c["ref"])
        c["pressure"] = dict(found["pressure"] if found else _NO_READING)
    cold = {row["ref"]: row["dormancy"] for row in (*open_threads, *commitments)}
    index = [{**d, "dormancy": (cold.get(d["ref"], dormancy(""))
                                if d["kind"] in {"thread", "commitment"} else None)}
             for d in snap["drivers"]]
    return {"commitments": commitments, "timeline": p["items"], "driver_index": index,
            "anchors": snap["anchors"], "links": _links_among(index), "fixed": p["fixed"],
            "near_days": _near_days(croot), "sooner_ref": _sooner_ref(p["items"], upcoming)}


def build_snapshot(cid: str, offscreen: bool = False, *, drivers: bool = True) -> dict:
    croot = campaigns_paths.campaign_root(cid)    # calendar.json is campaign-local
    aroot = appearances_paths.locked_actor_root(cid)    # roster actors are locked, so campaign-side
    roster = appearances_cast.roster(cid)

    try:
        open_threads = effective.threads_or_physical(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        open_threads = []

    try:
        recent = chronicle.recent(cid, 3)
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        recent = []
    # The campaign clock, not the last absorbed scene's date (#100). The two
    # agree until somebody advances time between scenes, which is exactly the
    # case this snapshot used to get wrong -- a suggestion prompt proposing next
    # week from a "now" a month behind the campaign's actual present.
    #
    # The fallback is handed in rather than left to `clock.now` to re-derive:
    # unclocked, it would read and parse the same chronicle.json this function
    # just read three records out of.
    now = clock.now(cid, fallback=recent[-1].get("date", "") if recent else "")
    story_so_far = [{"one_line": r.get("one_line", ""), "location": r.get("location", ""),
                     "date": r.get("date", "")} for r in reversed(recent)]

    try:
        scene_ids = sorted(chronicle.read_chronicle(cid).keys())
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        scene_ids = []

    _dormancy = _dormancy_of(scene_ids)
    for t in open_threads:
        t["dormancy"] = _dormancy(t.get("last_scene", ""))

    cal = _calendar_facts(cid, croot, now, roster)

    present = {_tok(ref) for ref in (recent[-1].get("cast") or [])} if recent else set()
    roster_tokens = {f"{a['kind']}:{a['id']}" for a in roster}
    player_tokens = {f"{a['kind']}:{a['id']}" for a in roster if a["role"] == "player"}

    def _status(tok: str) -> str:
        if tok in present:
            return "present"
        if tok in roster_tokens:
            return "appeared"
        return "unseen"

    cast, seen = [], set()
    # The roster and one view, not the full listing: a cast line is a token,
    # a name and a tagline, and the full row resolved the tagline too -- along
    # with an image listing per character that nothing here reads.
    v = overlay.view(cid)
    for c in overlay.character_roster(cid, v=v):
        tok = f"characters:{c['id']}"
        if offscreen and tok in player_tokens:
            continue   # don't offer the model a token the parser will discard
        seen.add(tok)
        cast.append({"token": tok, "name": c.get("name", c["id"]),
                     "tagline": overlay.tagline(cid, c["id"], v=v),
                     "status": _status(tok),
                     "role": "player" if tok in player_tokens else "npc"})
    if not offscreen:  # offscreen scenes never cast the player
        for a in roster:
            if a["role"] != "player":
                continue
            tok = f"{a['kind']}:{a['id']}"
            if tok in seen:
                continue
            seen.add(tok)
            try:
                name = (pcs.read_pc(aroot, a["id"])["meta"].get("name", a["id"])
                        if a["kind"] == "pcs" else _char_name(aroot, a["id"]))
            except pcs.PCNotFound:
                name = a["id"]
            cast.append({"token": tok, "name": name, "tagline": "",
                         "status": _status(tok), "role": "player"})

    available_locations = [{"id": e["id"], "name": e.get("name", e["id"])}
                           for e in overlay.list_entities(cid, "locations")]

    out = {"now": now, "friendly": cal["friendly"], "notation": cal["notation"],
           "holidays_today": cal["holidays_today"],
           "events_today": cal["events_today"],
           "upcoming": cal["upcoming"], "birthdays": cal["birthdays"],
           "story_so_far": story_so_far, "open_threads": open_threads,
           "cast": cast, "available_locations": available_locations}
    if drivers:
        # The timeline replaces the Upcoming line (Decision 2); the pick is
        # still what `sooner_ref` names, so the timeline keeps it in the prompt.
        del out["upcoming"]
        out.update(_driver_capture(cid, offscreen, now, croot, open_threads, _dormancy,
                                   cal["upcoming"]))
    return out


GREETING_EXCERPT = 300


def greeting_candidates(cid: str, after: str | None = None, pcless: bool = False) -> list[dict]:
    """Available greetings worth ranking — only when more than two are startable
    (with two or fewer the chooser simply shows them all)."""
    # `locations=False`: ranking reads id/name/available/pcless, and resolving
    # each row's location would read every location in the campaign to build an
    # answer this never looks at.
    avail = [g for g in playing.available_greetings(cid, after, locations=False)
             if g["available"] and g.get("pcless", False) == pcless]
    bounded = [g for g in avail if g.get("recommendation")]
    if bounded:
        avail = bounded
    if len(avail) <= 2:
        return []
    out: list[dict] = []
    for g in avail:
        try:
            body = overlay.read_greeting(cid, g["id"])["body"]
        except greetings.GreetingNotFound:
            body = ""
        out.append({"id": g["id"], "name": g["name"],
                    "excerpt": " ".join(body.split())[:GREETING_EXCERPT]})
    return out


DIRECTION_LIMIT = 500

#: How many drivers the prompt's index lists (spec §15), and how many dated
#: items its timeline lists (Decision 11). Pinned refs render past it; the rest
#: is "and N more". A bound on prompt length, not a measurement: the index is
#: one short line per driver, and forty is far more than one batch of three or
#: four suggestions can serve -- tune it against real prompts later.
DRIVER_PROMPT_CAP = 40

#: At most this many must-include refs (spec §16.3): every suggestion has to
#: serve each one, and a batch of three or four cannot serve many at once.
MUST_CAP = 3

TIME_MODES = ("auto", "near", "move", "anchor")
ANCHOR_RELATIONS = ("before", "on", "after", "by")

#: The kinds a must ref may name; temporal constraints go through the anchor.
MUST_KINDS = ("thread", "commitment")

#: The driver kinds a time anchor may name, in `DRIVER_KINDS` order.
TEMPORAL_KINDS = ("event", "birthday", "holiday")


@dataclass(frozen=True)
class Controls:
    """The reader's Story Pressure controls for one batch (spec §16.3).

    The ref tuples keep request order. `relation` is the request's, possibly
    "" (the model's or `on` then applies). Validation and precedence are
    `resolve_controls`'; this is only the value it produces.
    """
    focus: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    must: tuple[str, ...] = ()
    time_mode: str = "auto"
    anchor: str = ""
    relation: str = ""

    @property
    def active(self) -> bool:
        """Any structured control set: the controls addendum renders, and the
        picker shows every generated suggestion (§16.4)."""
        return bool(self.focus or self.avoid or self.must) or self.time_mode != "auto"

    @property
    def pinned(self) -> tuple[str, ...]:
        """The refs the prompt always shows: must, then focus, then the anchor."""
        out: list[str] = []
        for ref in (*self.must, *self.focus, *((self.anchor,) if self.anchor else ())):
            if ref not in out:
                out.append(ref)
        return tuple(out)


NO_CONTROLS = Controls()


class ControlsError(ValueError):
    """A request's controls, refused before any run is reserved (Decision 7).

    `status` 400 with `kind` "bad_controls" and a `reason` (`must_avoid`,
    `must_cap`, `must_kind`, `anchor_missing`, `anchor_without_mode`,
    `anchor_kind`, `anchor_relation`) for a request that can never be valid;
    409 "stale_drivers" with the `refs` the request-time capture lacks, for one
    that was valid against an older read of the campaign.
    """

    def __init__(self, status: int, kind: str, detail: str, *, reason: str = "",
                 refs: list[str] | None = None):
        super().__init__(detail)
        self.status = status
        self.kind = kind
        self.detail = detail
        self.reason = reason
        self.refs = refs


def _bad(reason: str, detail: str) -> ControlsError:
    return ControlsError(400, "bad_controls", detail, reason=reason)


def _distinct(refs) -> list[str]:
    """Stripped, blanks dropped, de-duplicated in order."""
    out: list[str] = []
    for raw in refs:
        ref = raw.strip() if isinstance(raw, str) else ""
        if ref and ref not in out:
            out.append(ref)
    return out


def _live_canon_or_identity(cid: str) -> dict[str, str]:
    try:
        return effective.live_canon(cid)
    except Exception:  # noqa: BLE001 -- an unreadable continuity doc canonicalizes nothing (Decision 4); a ref it would have moved is judged as written
        return {}


def _check_must(must: tuple[str, ...], avoid: tuple[str, ...]) -> None:
    if set(must) & set(avoid):
        raise _bad("must_avoid", "a driver cannot be both must-include and avoided")
    if len(must) > MUST_CAP:
        raise _bad("must_cap", f"at most {MUST_CAP} must-include drivers")
    if any(ref.split(":", 1)[0] not in MUST_KINDS for ref in must):
        raise _bad("must_kind", "only threads and commitments can be must-include; "
                                "use the time anchor for a date")


def _check_anchor(snapshot: dict, time_mode: str, anchor: str, relation: str) -> bool:
    """Decisions 5 and 6: the anchor's structure, judged before staleness, and
    whether the anchor is month-only. A month-only anchor's precision comes
    from its option when the capture has one, else from the ref's own shape."""
    if time_mode == "anchor" and not anchor:
        raise _bad("anchor_missing", "an anchored time mode needs an anchor")
    if not anchor:
        return False
    if time_mode != "anchor":
        raise _bad("anchor_without_mode", "an anchor needs the anchored time mode")
    prefix, sep, rest = anchor.partition(":")
    if not sep or not rest or prefix not in TEMPORAL_KINDS:
        raise _bad("anchor_kind", "an anchor is an event, a holiday or a birthday")
    option = next((a for a in snapshot.get("anchors", []) if a["ref"] == anchor), None)
    month = (option.get("precision") == "month" if option is not None
             else _month_of(anchor) is not None)
    if month and relation not in ("", "on"):
        raise _bad("anchor_relation", "a birthday known only by its month takes only \"on\"")
    return month


def _stale(snapshot: dict, refs: tuple[str, ...], anchor: str) -> list[str]:
    """The refs the request-time capture does not list, in request order. An
    anchor has to be an anchor option: a passed or undated event is a driver,
    never an anchor (Decision 5)."""
    index = {d["ref"] for d in snapshot.get("driver_index", [])}
    out = [ref for ref in refs if ref not in index]
    if anchor and anchor not in {a["ref"] for a in snapshot.get("anchors", [])}:
        out.append(anchor)
    return _distinct(out)


def resolve_controls(cid: str, snapshot: dict, *, focus_refs=(), avoid_refs=(), must_refs=(),
                     time_mode: str = "auto", time_anchor_ref: str = "",
                     time_anchor_relation: str = "") -> Controls:
    """A request's Story Pressure controls validated against `snapshot`, the
    capture the prompt is rendered from (spec §16.3, Decision 7).

    In order: refs are stripped, blanks dropped and de-duplicated; every ref
    canonicalizes through `effective.live_canon` (Decision 4: an alias whose
    target was deleted leaves its source a driver of its own; unreadable means
    the identity); the 400s (`ControlsError`, `bad_controls`) for must/avoid
    and for the anchor; then one 409 `stale_drivers` naming every ref the
    capture does not hold. Precedence runs last: the anchor leaves `avoid`
    (Decision 26), then `focus` loses what `must` or `avoid` holds (Decision 8).
    A relation without an anchor means nothing and is dropped; a month-only
    anchor's blank relation resolves to `on` here, so the prompt never offers
    the model a relation the parser would coerce away (Decision 6)."""
    canon = _live_canon_or_identity(cid)

    def canonical(refs) -> tuple[str, ...]:
        return tuple(_distinct(canon.get(ref, ref) for ref in _distinct(refs)))

    focus, avoid, must = canonical(focus_refs), canonical(avoid_refs), canonical(must_refs)
    anchor = next(iter(canonical([time_anchor_ref])), "")
    _check_must(must, avoid)
    month = _check_anchor(snapshot, time_mode, anchor, time_anchor_relation)
    stale = _stale(snapshot, (*focus, *avoid, *must), anchor)
    if stale:
        raise ControlsError(409, "stale_drivers",
                            "some selected drivers are no longer current", refs=stale)
    avoid = tuple(ref for ref in avoid if ref != anchor)
    focus = tuple(ref for ref in focus if ref not in must and ref not in avoid)
    return Controls(focus=focus, avoid=avoid, must=must, time_mode=time_mode, anchor=anchor,
                    relation=("on" if month else time_anchor_relation) if anchor else "")


def _when(in_days: int | None, precision: str | None = None, dated: bool = False) -> str:
    """Decision 10's phrase for an item's distance from now. `dated` says the
    item has a fixed day: with no present it has no distance, but it is not
    undated (§19.2's rule, kept by the prompt and the chooser alike)."""
    if precision == "month":
        return "day unknown"
    if in_days is None:
        return "no current date" if dated else "undated"
    if in_days == 0:
        return "today"
    if in_days > 0:
        return "in 1 day" if in_days == 1 else f"in {in_days} days"
    return "1 day ago" if in_days == -1 else f"{-in_days} days ago"


def _select(rows: list[dict], rank: Callable[[dict], tuple],
            always: Callable[[dict], bool]) -> list[int]:
    """The indices of `rows` to render, ascending: every row `always` keeps,
    plus the best-ranked rest up to `DRIVER_PROMPT_CAP` in all."""
    kept = [i for i, r in enumerate(rows) if always(r)]
    rest = sorted((i for i, r in enumerate(rows) if not always(r)), key=lambda i: rank(rows[i]))
    return sorted([*kept, *rest[:max(0, DRIVER_PROMPT_CAP - len(kept))]])


def _index_rank(row: dict) -> tuple:
    """Pressure in `SORT_ORDER` (Decision 9: not `PRESSURE_STATES`, which ranks
    `passed` above `today`), then the coldest first, then Slice B's ties."""
    dormancy, in_days = row.get("dormancy"), row["pressure"]["in_days"]
    return (pressure.SORT_ORDER.index(row["pressure"]["state"]), dormancy is None,
            -(dormancy or 0), continuity_drivers.DRIVER_KINDS.index(row["kind"]),
            in_days is None, in_days or 0, row["ref"])


def _timeline_rank(item: dict) -> tuple:
    in_days = item["in_days"]
    return (pressure.SORT_ORDER.index(item["state"]), in_days is None, in_days or 0,
            pressure.KINDS.index(item["kind"]), item["ref"])


def _index_rows(snapshot: dict, pinned: set[str]) -> tuple[list[dict], int]:
    ordered = sorted(snapshot.get("driver_index", []), key=_index_rank)
    chosen = _select(ordered, _index_rank, lambda r: r["ref"] in pinned)
    day = {i["ref"]: i for i in snapshot.get("timeline", []) if i["kind"] in TEMPORAL_KINDS}
    rows = []
    for i in chosen:
        d = ordered[i]
        in_days = d["pressure"]["in_days"]
        item = day.get(d["ref"], {})
        when = (_when(in_days, item.get("precision"), item.get("fixed") is not None)
                if d["kind"] in TEMPORAL_KINDS
                else _when(in_days) if in_days is not None else "")
        rows.append({"ref": d["ref"], "label": d["label"], "kind": d["kind"],
                     "state": d["pressure"]["state"], "when": when})
    return rows, len(ordered) - len(rows)


def _timeline_rows(snapshot: dict, pinned: set[str]) -> tuple[list[dict], int]:
    """Decision 11: pinned refs (on `ref` or `subject`) and the Upcoming pick
    always render, the rest is ranked and capped, and rows keep the
    snapshot's own (calendar) order."""
    items = snapshot.get("timeline", [])
    sooner = snapshot.get("sooner_ref", "")

    def always(item: dict) -> bool:
        return (item["ref"] in pinned or item.get("subject") in pinned
                or (bool(sooner) and item["ref"] == sooner and item["kind"] in _SOONER_KINDS))

    chosen = _select(items, _timeline_rank, always)
    rows = [{**items[i], "when": _when(items[i]["in_days"], items[i].get("precision"),
                                       items[i].get("fixed") is not None)}
            for i in chosen]
    return rows, len(items) - len(rows)


def _rendered_links(snapshot: dict, refs: set[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for link in snapshot.get("links", []):
        if link["id"] in seen or link["a"] not in refs or link["b"] not in refs:
            continue
        seen.add(link["id"])
        out.append({"a": link["a"], "relation": link["relation"], "b": link["b"]})
    return out


def _anchor_view(snapshot: dict, controls: Controls, labels: dict[str, str]) -> dict | None:
    if not controls.anchor:
        return None
    found: dict = next((a for a in snapshot.get("anchors", []) if a["ref"] == controls.anchor), {})
    return {"ref": controls.anchor,
            "label": found.get("label") or labels.get(controls.anchor, controls.anchor),
            "friendly": found.get("friendly") or "", "relation": controls.relation}


def driver_view(snapshot: dict, controls: Controls) -> dict:
    """What the prompt renders of the snapshot's drivers under `controls`: the
    capped, pinned index (Decision 9), the capped timeline (Decision 11), the
    links among rendered drivers, the action vocabulary, and the controls with
    their labels. A snapshot without driver keys yields an empty view."""
    pinned = set(controls.pinned)
    index, more = _index_rows(snapshot, pinned)
    timeline, timeline_more = _timeline_rows(snapshot, pinned)
    labels = {d["ref"]: d["label"] for d in snapshot.get("driver_index", [])}

    def named(refs: tuple[str, ...]) -> list[dict]:
        return [{"ref": r, "label": labels.get(r, r)} for r in refs]

    return {
        "index": index, "more": more, "timeline": timeline, "timeline_more": timeline_more,
        "links": _rendered_links(snapshot, {r["ref"] for r in index}),
        "actions": [{"kind": k, "actions": list(continuity_drivers.ACTIONS_BY_KIND[k])}
                    for k in continuity_drivers.DRIVER_KINDS],
        "high_pressure": [s for s in pressure.SORT_ORDER if s in pressure.HIGH_PRESSURE],
        "relations": list(ANCHOR_RELATIONS),
        "focus": named(controls.focus), "avoid": named(controls.avoid),
        "must": named(controls.must),
        "time_mode": controls.time_mode,
        # the date addendum (what defines the "date" key) renders only under a
        # `now`, so near/move may not ask for a date without one (§26)
        "dated": bool(snapshot.get("now")),
        "near_days": snapshot.get("near_days", NEAR_MIN_DAYS),
        "anchor": _anchor_view(snapshot, controls, labels),
        "active": controls.active,
    }


def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None,
                 offscreen: bool = False, direction: str = "",
                 controls: Controls | None = None) -> list[dict]:
    # the templates pick the instruction variant and addenda from the same vars
    vars = {"s": snapshot, "offscreen": offscreen,
            "greeting_candidates": greeting_candidates,
            "direction": direction.strip()[:DIRECTION_LIMIT],
            "drivers": True, "view": driver_view(snapshot, controls or NO_CONTROLS)}
    return [{"role": "system", "content": prompts.render("scene_suggestions/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_suggestions/user.j2", **vars)}]


INTENT_LIMIT = 2000


def build_intent_prompt(cid: str, typed: str, offscreen: bool = False,
                        schema: dict | None = None) -> list[dict]:
    """Prompt for extracting metadata from the user's own scene description.

    Over the FULL snapshot, story-so-far included: "the morning after the
    funeral" is exactly the kind of phrase this has to resolve, and only the
    recent chronicle can resolve it. The legacy (`drivers=False`) snapshot and
    render: the intent prompt is promised byte-identical (spec §15), so it
    keeps its Upcoming line and does no pressure or driver work.

    The system message ends with the reply's JSON Schema (`intent_schema`,
    01f's pilot), rendered with `schema_json` -- the spelling
    `inference.generate(schema=)` checks the prompt for. `schema` is the one
    the call will send; None builds it here."""
    # `direction`, `drivers` and `view` are here because scene_intent/user.j2
    # INCLUDES scene_suggestions/user.j2, which reads them — and both this env
    # and verify_templates render with StrictUndefined, so omitting one is a
    # hard failure, not a silently-empty block.
    vars = {"s": build_snapshot(cid, offscreen=offscreen, drivers=False),
            "offscreen": offscreen, "greeting_candidates": None, "direction": "",
            "drivers": False, "view": None, "typed": typed.strip()[:INTENT_LIMIT],
            "schema": intent_schema(cid, offscreen) if schema is None else schema}
    return [{"role": "system", "content": prompts.render("scene_intent/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_intent/user.j2", **vars)}]


def _intent_shape(locations: list[str] | None, cast: list[str] | None) -> dict:
    """The intent reply's schema, with `locations` and `cast` as enums, or a
    plain string field where one is None."""
    location: dict = ({"type": "string"} if locations is None
                      else {"type": "string", "enum": [*locations, ""]})
    member: dict = {"type": "string"} if cast is None else {"type": "string", "enum": cast}
    properties = {"title": {"type": "string"}, "date": {"type": "string"},
                  "location": location, "cast": {"type": "array", "items": member}}
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def intent_schema(cid: str, offscreen: bool = False) -> dict:
    """The JSON Schema of an intent reply (01f's pilot): `title` and `date`
    strings, `location` one of the campaign's location ids or "", and `cast`
    a list of the cast tokens `parse_intent` would keep (`valid_ids`,
    `token_ok`). The enums are what structured mode buys: a provider that
    enforces them cannot answer with an id the campaign does not have.

    An empty enum is not portable (`schemas.check` refuses it), so a field
    with nothing to offer -- no locations, no cast -- is a plain string. So is
    one past strict mode's budgets: the cast first, else the locations, else
    both.
    `parse_intent` drops an unknown id either way."""
    char_ids, player_tokens, loc_ids = valid_ids(cid)
    tokens = sorted({f"characters:{c}" for c in char_ids} | set(player_tokens))
    cast = [t for t in tokens if token_ok(t, char_ids, player_tokens, offscreen)] or None
    locations = sorted(i for i in loc_ids if i) or None
    for shape in ((locations, cast), (locations, None), (None, cast), (None, None)):
        schema = _intent_shape(*shape)
        try:
            schemas.check(schema)
        except schemas.SchemaError:
            continue
        return schema
    raise AssertionError("the plain intent schema is portable")  # unreachable


def valid_ids(cid: str):
    """The ids a proposed scene may reference: characters, player tokens,
    locations.

    Public because a *saved* scene idea (#88) has to be held to the same rule
    as a freshly generated one -- checked on write and again on every read,
    since a campaign moves under a durable idea -- and doing that through this
    module is what keeps one definition of "a token this campaign actually
    has". Reads the campaign's entities and roster, so callers resolve it once
    for a whole list rather than per row (see `ref_validator`).
    """
    # Ids off directory listings: validity is membership, so neither the full
    # character rows nor every location's parsed body is needed to decide it.
    v = overlay.view(cid)
    char_ids = set(overlay.character_ids(cid, v=v))
    player_tokens = {f"{a['kind']}:{a['id']}" for a in appearances_cast.roster(cid) if a["role"] == "player"}
    loc_ids = set(overlay.cast_ids(cid, "locations", v=v))
    return char_ids, player_tokens, loc_ids


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


def ref_validator(cid: str):
    """A reference checker bound to one campaign:
    ``(cast, location, date, offscreen) -> {"cast", "location", "date"}``, with
    unknown cast tokens and an unknown location dropped and the date canonical
    or blank.

    Returned as a closure because the id sets it checks against cost a read of
    the campaign's entities and roster (`valid_ids`) and a calendar-provider
    resolution (`date_normalizer`): a caller with a list of records resolves
    them once and reuses the checker, rather than paying per row.
    """
    char_ids, player_tokens, loc_ids = valid_ids(cid)
    norm = date_normalizer(cid)

    def check(cast, location, date="", offscreen=False) -> dict:
        loc = str(location).strip()
        return {"cast": [t for t in (str(x).strip() for x in cast)
                         if token_ok(t, char_ids, player_tokens, offscreen)],
                "location": loc if loc in loc_ids else "",
                "date": norm(str(date).strip())}
    return check


def valid_refs(cid: str, cast: list[str], location: str, date: str = "",
               offscreen: bool = False) -> dict:
    """One record's references, checked. The single-record form of what
    `parse_output` does across a whole reply, for the caller that has a record
    rather than a model's text -- `routes.scenes.post_scene_idea`, saving a
    scene idea (#88)."""
    return ref_validator(cid)(cast, location, date, offscreen)


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
    check = ref_validator(cid)
    rows = [{**i, **check(i["cast"], i["location"], i["date"], i["pcless"])}
            for i in ideas]
    if not any(_stored_entries(i.get("drivers")) or _stored_anchor(i.get("time_anchor"))
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


def _stored_entries(value) -> list[dict]:
    """`[{ref, action}]` with string fields, whatever the caller handed over."""
    if not isinstance(value, list):
        return []
    return [{"ref": e["ref"], "action": e["action"]} for e in value
            if isinstance(e, dict) and isinstance(e.get("ref"), str) and e["ref"].strip()
            and isinstance(e.get("action"), str)]


def _stored_anchor(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    ref, relation, native = value.get("ref"), value.get("relation"), value.get("native")
    if not (isinstance(ref, str) and ref.strip() and isinstance(relation, str)
            and isinstance(native, str)):
        return None
    return {"ref": ref, "relation": relation, "native": native}


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
    return _month_of(ref) if len(head) == 3 and all(head) else None


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
    return kind in TEMPORAL_KINDS and _well_formed_occurrence(ref)


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
    provider = _soft_provider(campaigns_paths.campaign_root(cid))
    day = _occurrence_day(ref)
    native = _plugin_soft(provider.format, day) if provider is not None else None
    return native if isinstance(native, str) and native else None


def _provenance_anchor(cid: str, raw, ledgers: effective.Ledgers | None,
                       canon: dict[str, str]) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("ref"), str):
        return None
    ref = canon.get(raw["ref"].strip(), raw["ref"].strip())
    if _kind_of(ref) not in TEMPORAL_KINDS or not _exists(ref, ledgers):
        return None
    relation = raw.get("relation")
    relation = relation if isinstance(relation, str) and relation in ANCHOR_RELATIONS else "on"
    native = _anchor_native(cid, ref, ledgers)
    if native is None:
        return None
    return {"ref": ref, "relation": "on" if _month_ref(ref) else relation, "native": native}


def _provenance_pair(entry, canon: dict[str, str]) -> dict | None:
    """One driver entry as a canonical `{ref, action}`, or None when it is
    malformed or its action is wrong for its kind."""
    pair = _stored_entries([entry])
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
        provider = _soft_provider(campaigns_paths.campaign_root(cid))
        try:
            now = clock.now(cid)
        except Exception:  # noqa: BLE001 -- no readable present: nothing reads as behind it
            now = ""
        now_fixed = _fixed(provider, now) if isinstance(now, str) else None
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
    fixed = _fixed(ctx.provider, date)
    friendly = _friendly_of(ctx.provider, fixed)
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
    listed = _plugin_soft(ctx.provider.months, year) if ctx.provider is not None else None
    months = listed if isinstance(listed, list) else []
    found = next((i for i, m in enumerate(months)
                  if isinstance(m, dict) and str(m.get("key")).casefold() == key.casefold()),
                 None)
    if found is None:
        return _reading(_kind_of(ref), label, "live")
    friendly = f"{months[found].get('name', key)} {year}"
    now = (_plugin_soft(ctx.provider.describe, ctx.now_fixed)
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
    native = _plugin_soft(ctx.provider.format, day) if ctx.provider is not None else None
    return _reading(_kind_of(ref), _occurrence_label(ref, ctx),
                    "finished" if behind else "live", _friendly_of(ctx.provider, day),
                    native if isinstance(native, str) else "")


def _classify(ref: str, ctx: _IdeaContext, native: str = "") -> dict:
    """One stored ref's reading: kind, label, state (`live` / `finished` /
    `dangling`), friendly day, the day itself, and whether it moved."""
    kind = _kind_of(ref)
    if kind in ("thread", "commitment"):
        return _record_reading(ref, kind, ctx)
    if kind == "event":
        return _event_reading(ref, ctx, native)
    if kind in TEMPORAL_KINDS:
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
    stored = _stored_anchor(idea.get("time_anchor"))
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
    for entry in _stored_entries(idea.get("drivers")):
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
    anchor = _stored_anchor(idea.get("time_anchor"))
    return {"drivers": [{**e, "kind": _kind_of(e["ref"]), "label": e["ref"], "state": "live"}
                        for e in _stored_entries(idea.get("drivers"))],
            "time_anchor": ({**anchor, "kind": _kind_of(anchor["ref"]), "label": anchor["ref"],
                             "friendly": "", "state": "live"} if anchor else None),
            "stale_reason": "", "anchor_date": ""}


def _annotated(idea: dict, ctx: _IdeaContext) -> dict:
    try:
        return _annotate(idea, ctx)
    except Exception:  # noqa: BLE001 -- the annotation is an enhancement; the idea must still list
        return _unannotated(idea)


def _extract_json(text: str):
    """Tolerant of the model wrapping JSON in prose and of a bare top-level array
    (a common LLM deviation from the requested {"suggestions": [...]} object). Tries the
    whole reply first (clean object or array), then a brace slice, then a bracket slice."""
    candidates = [text.strip()]
    for lo, hi in (("{", "}"), ("[", "]")):
        s, e = text.find(lo), text.rfind(hi)
        if s != -1 and e > s:
            candidates.append(text[s:e + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def token_ok(tok: str, char_ids: set[str], player_tokens: set[str], offscreen: bool) -> bool:
    """A cast token this campaign actually has. Public for `valid_ids`' reason.

    The offscreen clause is FIRST and guarded, both deliberately. A PC seated as
    a `characters` actor (CastPanel's role selector allows exactly that) would
    otherwise pass on the `char_ids` check below, and an offscreen scene is
    defined by the player's absence. Guarded, because dropping the `offscreen`
    condition would reject players from ordinary PC scenes."""
    kind, _, aid = tok.partition(":")
    if offscreen and tok in player_tokens:
        return False
    if kind == "characters" and aid in char_ids:
        return True
    return not offscreen and tok in player_tokens


def raw_suggestions(text: str) -> list | None:
    """The reply's suggestion list: a dict's `"suggestions"`, or a bare array (a
    common LLM deviation). None when nothing in the text decodes, `[]` when what
    decoded holds no list. Shared by `parse_output` and the eval grader."""
    parsed = _extract_json(text)
    if parsed is None:
        return None
    suggestions = parsed.get("suggestions", []) if isinstance(parsed, dict) else parsed
    return suggestions if isinstance(suggestions, list) else []


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


def _canon_map(snapshot: dict) -> dict[str, str]:
    """Alias source -> canonical, from the captured thread and commitment rows'
    `aliases` (no store read: what the prompt showed is what the reply is
    held to)."""
    rows = (*snapshot.get("open_threads", []), *snapshot.get("commitments", []))
    return {a["ref"]: row["ref"] for row in rows if "ref" in row
            for a in row.get("aliases") or [] if isinstance(a, dict) and "ref" in a}


def _pair(e, kinds: dict[str, str], canon: dict[str, str]) -> dict | None:
    """One model driver entry as a canonical `{ref, action}`, or None when it is
    malformed, unknown to the captured index, or wrong for its kind."""
    if not isinstance(e, dict):
        return None
    ref, action = e.get("ref"), e.get("action")
    if not isinstance(ref, str) or not isinstance(action, str):
        return None
    ref = canon.get(ref.strip(), ref.strip())
    allowed = continuity_drivers.ACTIONS_BY_KIND.get(kinds.get(ref, ""), ())
    return {"ref": ref, "action": action} if action in allowed else None


def _valid_pairs(entry: dict, snapshot: dict) -> list[dict]:
    """The entry's claimed drivers, validated against the captured index and
    de-duplicated by canonical ref (the first action wins)."""
    kinds = {d["ref"]: d["kind"] for d in snapshot.get("driver_index", [])}
    canon = _canon_map(snapshot)
    raw = entry.get("drivers")
    out: list[dict] = []
    for e in raw if isinstance(raw, list) else []:
        pair = _pair(e, kinds, canon)
        if pair is not None and pair["ref"] not in {p["ref"] for p in out}:
            out.append(pair)
    return out


def _anchor_of(entry: dict, snapshot: dict, controls: Controls) -> dict | None:
    """`{ref, relation}`: the request's anchor when the batch has one (Decision
    14), else the model's when it names an anchor option (Decision 5: a passed
    or undated event is a driver, never an anchor). The relation is the
    request's, else the model's if valid, else `on`; a month-only birthday only
    ever takes `on` (Decision 6)."""
    model = entry.get("time_anchor")
    model = model if isinstance(model, dict) else {}
    relation = model.get("relation")
    relation = relation if isinstance(relation, str) and relation in ANCHOR_RELATIONS else ""
    if controls.anchor:
        ref, relation = controls.anchor, controls.relation or relation
    else:
        raw_ref = model.get("ref")
        ref = raw_ref.strip() if isinstance(raw_ref, str) else ""
        if ref not in {a["ref"] for a in snapshot.get("anchors", [])}:
            return None
    if _month_of(ref) is not None:
        relation = "on"
    return {"ref": ref, "relation": relation or "on"}


def _misses(refs: set[str], controls: Controls) -> tuple[list[str], list[str]]:
    """`(unmet_must, avoided)` over the claimed refs, in request order."""
    return ([r for r in controls.must if r not in refs],
            [r for r in controls.avoid if r in refs])


def claim(entry: dict, snapshot: dict, controls: Controls) -> dict:
    """What one suggestion validly claims (spec §15.2, Decision 14):
    `{drivers: [{ref, action}], time_anchor: {ref, relation} | None,
    unmet_must: [ref], avoided: [ref]}`.

    Model refs canonicalize through the captured rows' aliases, and an unknown
    ref or a wrong action for its kind is dropped -- and nothing else is: a
    temporal driver claimed as `anchor` in `drivers` stays, so a passed or
    undated event (a driver, never an anchor) can be claimed at all. The time
    anchor is added to `drivers` as `anchor` when missing. "A record never
    claims two anchors" is the save's rule (`idea_provenance`, Decision 16),
    not the card's. Avoided refs are reported and left out of `drivers`; an
    avoided anchor stays the anchor. Nothing here drops the suggestion."""
    anchor = _anchor_of(entry, snapshot, controls)
    kept = anchor["ref"] if anchor else None
    pairs = _valid_pairs(entry, snapshot)
    if kept is not None and kept not in {p["ref"] for p in pairs}:
        pairs.append({"ref": kept, "action": "anchor"})
    claimed = {p["ref"] for p in pairs}
    unmet, avoided = _misses(claimed, controls)
    return {"drivers": [p for p in pairs if p["ref"] not in controls.avoid],
            "time_anchor": anchor, "unmet_must": unmet, "avoided": avoided}


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


def _against_mode(provider, snapshot: dict, controls: Controls, now: int | None,
                  date: str) -> tuple[str, bool]:
    """`near` and `move` (spec §16.3); skipped with no `now` (Decision 12)."""
    if controls.time_mode not in ("near", "move") or now is None:
        return date, False
    d = _fixed(provider, date)
    if d is None:
        return date, False
    if controls.time_mode == "near":
        ok = now <= d <= now + snapshot.get("near_days", NEAR_MIN_DAYS)
    else:
        ok = d > now
    return (date, False) if ok else ("", True)


def judge_date(provider, snapshot: dict, controls: Controls, time_anchor: dict | None,
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


def check_date(provider, snapshot: dict, controls: Controls, time_anchor: dict | None,
               date: str) -> tuple[str, bool]:
    """`(date, rejected)`: `judge_date` without saying which rule refused it."""
    date, rejected_by = judge_date(provider, snapshot, controls, time_anchor, date)
    return date, rejected_by is not None


def _claimed_refs(row: dict) -> list[str]:
    anchor = row.get("time_anchor")
    return [*(d["ref"] for d in row.get("drivers", [])), *([anchor["ref"]] if anchor else [])]


def order_cards(rows: list[dict], snapshot: dict, controls: Controls) -> list[dict]:
    """A stable re-ordering (Decision 13): the first card claiming a
    high-pressure driver, then the earliest card covering each focus ref not
    yet covered, then the rest in model order."""
    states = {d["ref"]: d["pressure"]["state"] for d in snapshot.get("driver_index", [])}
    remaining = list(rows)
    hot = next((i for i, r in enumerate(remaining)
                if any(states.get(ref) in pressure.HIGH_PRESSURE for ref in _claimed_refs(r))),
               None)
    out = [remaining.pop(hot)] if hot is not None else []
    covered = {ref for row in out for ref in _claimed_refs(row)}
    focus = set(controls.focus)
    while True:
        nxt = next((i for i, r in enumerate(remaining)
                    if (focus & set(_claimed_refs(r))) - covered), None)
        if nxt is None:
            return out + remaining
        out.append(remaining.pop(nxt))
        covered.update(_claimed_refs(out[-1]))


def _cast_of(entry: dict, ids: tuple, offscreen: bool) -> list[str]:
    char_ids, player_tokens, _loc_ids = ids
    raw_cast = entry.get("cast", [])
    return ([t for t in (str(x).strip() for x in raw_cast)
             if token_ok(t, char_ids, player_tokens, offscreen)]
            if isinstance(raw_cast, list) else [])


def is_card(entry) -> bool:
    """Is this raw entry one `parse_output` keeps -- an object with a title and
    a premise? Shared with the eval grader, so it judges the cards the player
    is shown rather than every entry the reply holds."""
    return (isinstance(entry, dict) and bool(str(entry.get("title", "")).strip())
            and bool(str(entry.get("premise", "")).strip()))


def _card(entry, ctx: dict) -> dict | None:
    """One parsed suggestion with its validated provenance (spec §15.2), or
    None when `is_card` says the app does not keep it."""
    if not is_card(entry):
        return None
    title, premise = str(entry.get("title", "")).strip(), str(entry.get("premise", "")).strip()
    snap, controls, provider = ctx["snapshot"], ctx["controls"], ctx["provider"]
    claimed = claim(entry, snap, controls)
    date = normalize_date(provider, snap.get("now") or "", entry.get("date", ""))
    date, rejected_by = judge_date(provider, snap, controls, claimed["time_anchor"], date)
    fixed = _fixed(provider, date)
    loc = str(entry.get("location", "")).strip()
    labels = ctx["labels"]

    def named(refs: list[str]) -> list[dict]:
        return [{"ref": r, "label": labels.get(r, r)} for r in refs]

    return {"title": title, "premise": premise,
            "cast": _cast_of(entry, ctx["ids"], ctx["offscreen"]),
            "location": loc if loc in ctx["ids"][2] else "",
            "date": date, "date_friendly": _friendly_of(provider, fixed),
            "in_days": (fixed - ctx["now"] if fixed is not None and ctx["now"] is not None
                        else None),
            "date_rejected": rejected_by is not None, "date_rejected_by": rejected_by,
            "drivers": [{"ref": d["ref"], "kind": ctx["kinds"].get(d["ref"], d["ref"].split(":")[0]),
                         "action": d["action"], "label": labels.get(d["ref"], d["ref"])}
                        for d in claimed["drivers"]],
            "time_anchor": _anchor_card(claimed["time_anchor"], snap, labels),
            "unmet_must": named(claimed["unmet_must"]), "avoided": named(claimed["avoided"])}


def _anchor_card(anchor: dict | None, snapshot: dict, labels: dict[str, str]) -> dict | None:
    if anchor is None:
        return None
    ref = anchor["ref"]
    opt: dict = next((a for a in snapshot.get("anchors", []) if a["ref"] == ref), {})
    return {"ref": ref, "kind": opt.get("kind") or ref.split(":", 1)[0],
            "relation": anchor["relation"], "label": opt.get("label") or labels.get(ref, ref),
            "friendly": opt.get("friendly") or "", "in_days": opt.get("in_days")}


def parse_output(text: str, cid: str, offscreen: bool = False, *,
                 snapshot: dict | None = None, controls: Controls | None = None) -> list[dict]:
    """The reply's suggestions, every id and date validated, each with its
    claimed drivers and time anchor resolved against `snapshot` -- the capture
    the prompt was rendered from (spec §16.3) -- and in `order_cards` order.

    Without a snapshot the index is empty and no control applies: the rows
    carry today's fields plus empty provenance. The calendar is resolved once,
    softly (Decision 25): a plugin that cannot load blanks every date and
    rejects none (§26). Store failures under `valid_ids` still surface as the
    run's ordinary failure."""
    suggestions = raw_suggestions(text)
    if not suggestions:
        return []
    snap: dict = snapshot if snapshot is not None else {"now": clock.now(cid)}
    controls = controls or NO_CONTROLS
    provider = _soft_provider(campaigns_paths.campaign_root(cid))
    ctx = {"snapshot": snap, "controls": controls, "provider": provider,
           "now": _now_fixed(provider, snap) if provider is not None else None,
           "ids": valid_ids(cid), "offscreen": offscreen,
           "labels": {d["ref"]: d["label"] for d in snap.get("driver_index", [])},
           "kinds": {d["ref"]: d["kind"] for d in snap.get("driver_index", [])}}
    rows = [row for row in (_card(e, ctx) for e in suggestions) if row is not None]
    return order_cards(rows, snap, controls)


def _str_field(value) -> str:
    """A model field that is supposed to be a string. `str(x)` on a non-string
    (JSON `null`, a number, a nested object) produces a non-empty string like
    "None"/"42"/"{'a': 1}" that then reads as real model output -- e.g. a
    `null` title survives as the literal title "None" instead of falling back
    to blank. Only an actual string is trimmed; anything else counts as
    missing, the same way `cast`'s entries are validated structurally rather
    than coerced."""
    return value.strip() if isinstance(value, str) else ""


def parse_intent(reply: str, cid: str, offscreen: bool = False) -> dict:
    """Metadata extracted from the user's own description, every field validated
    against the campaign.

    Malformed or semantically invalid model output never raises — extraction is
    a convenience, and a miss must leave the user a blank form rather than an
    error. Store failures underneath (`valid_ids` reads entities) are NOT
    covered by that and surface as the route's ordinary 500, exactly as they do
    for `parse_output`. A calendar plugin that cannot load is: the provider
    resolves softly (Decision 25), so the date comes back blank."""
    empty = {"title": "", "date": "", "location": "", "cast": []}
    parsed = _extract_json(reply)
    if isinstance(parsed, list):   # a bare array is a common LLM deviation
        parsed = next((e for e in parsed if isinstance(e, dict)), None)
    if not isinstance(parsed, dict):
        return empty
    char_ids, player_tokens, loc_ids = valid_ids(cid)
    raw_cast = parsed.get("cast", [])
    cast = ([t for t in (str(x).strip() for x in raw_cast)
             if token_ok(t, char_ids, player_tokens, offscreen)]
            if isinstance(raw_cast, list) else [])
    loc = _str_field(parsed.get("location", ""))
    return {"title": _str_field(parsed.get("title", "")),
            "date": date_normalizer(cid, tolerant=True)(_str_field(parsed.get("date", ""))),
            "location": loc if loc in loc_ids else "",
            "cast": cast}


def parse_next_date(text: str, cid: str, *, snapshot: dict | None = None,
                    controls: Controls | None = None) -> str:
    """The model's general next-scene date estimate, validated; "" when
    absent/bad. `near`/`move` check it; an anchor does not, since it answers
    "if none is used" and an anchor constrains suggestions (Decision 12)."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    provider = _soft_provider(campaigns_paths.campaign_root(cid))
    snap: dict = snapshot if snapshot is not None else {"now": clock.now(cid)}
    date = normalize_date(provider, snap.get("now") or "", raw)
    return check_date(provider, snap, controls or NO_CONTROLS, None, date)[0]


def parse_greeting_picks(text: str, allowed: set[str]) -> list[str]:
    """The model's greeting_picks, kept in order: unknown ids and duplicates drop."""
    parsed = _extract_json(text)
    picks = parsed.get("greeting_picks", []) if isinstance(parsed, dict) else []
    if not isinstance(picks, list):
        return []
    out: list[str] = []
    for p in picks:
        if isinstance(p, str) and p in allowed and p not in out:
            out.append(p)
    return out
