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
from collections.abc import Callable
from dataclasses import dataclass

from .. import prompts
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


def _when(in_days: int | None, precision: str | None = None) -> str:
    """Decision 10's phrase for an item's distance from now."""
    if precision == "month":
        return "day unknown"
    if in_days is None:
        return "undated"
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
    precision = {i["ref"]: i.get("precision") for i in snapshot.get("timeline", [])
                 if i["kind"] in TEMPORAL_KINDS}
    rows = []
    for i in chosen:
        d = ordered[i]
        in_days = d["pressure"]["in_days"]
        temporal = d["kind"] in TEMPORAL_KINDS
        when = (_when(in_days, precision.get(d["ref"])) if temporal
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
    rows = [{**items[i], "when": _when(items[i]["in_days"], items[i].get("precision"))}
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


def build_intent_prompt(cid: str, typed: str, offscreen: bool = False) -> list[dict]:
    """Prompt for extracting metadata from the user's own scene description.

    Over the FULL snapshot, story-so-far included: "the morning after the
    funeral" is exactly the kind of phrase this has to resolve, and only the
    recent chronicle can resolve it. The legacy (`drivers=False`) snapshot and
    render: the intent prompt is promised byte-identical (spec §15), so it
    keeps its Upcoming line and does no pressure or driver work."""
    # `direction`, `drivers` and `view` are here because scene_intent/user.j2
    # INCLUDES scene_suggestions/user.j2, which reads them — and both this env
    # and verify_templates render with StrictUndefined, so omitting one is a
    # hard failure, not a silently-empty block.
    vars = {"s": build_snapshot(cid, offscreen=offscreen, drivers=False),
            "offscreen": offscreen, "greeting_candidates": None, "direction": "",
            "drivers": False, "view": None, "typed": typed.strip()[:INTENT_LIMIT]}
    return [{"role": "system", "content": prompts.render("scene_intent/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_intent/user.j2", **vars)}]


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
    (`parse_output`, `parse_next_date`, `parse_intent`) -- that is the whole
    point. Stored RECORDS are not (`ref_validator`): their dates were
    canonicalized on write, so one that no longer parses means the campaign
    changed calendars under it, and re-reading a Gregorian date through a Hebrew
    string matcher would invent a moment nobody wrote. It is also the path that
    could least afford it -- the ledger has no cap, it is revalidated on every
    read, and a fuzzy miss costs a whole window scan per row.
    """
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
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
        return strict
    anchor = clock.now(cid)

    def norm(s: str) -> str:
        if not s:
            return ""
        try:
            return calendars.resolve(provider, s, anchor)
        except calendars.CalendarError:
            return ""
    return norm


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
    re-checked against the campaign as it stands now.

    The read-side half of the scene ledger's validation (#88). It has to happen
    on every read, not only on write, because an idea is durable and a campaign
    is not -- the character it casts can be deleted and the location it names
    can be renamed between the day it was saved and the day it is picked, and a
    picker handed a dangling id would send it straight to `addCastBatch`.

    Each idea's own `pcless` decides which player tokens are legal, exactly as
    `offscreen` does for a fresh suggestion, so one read can hold ideas of both
    modes.
    """
    check = ref_validator(cid)
    return [{**i, **check(i["cast"], i["location"], i["date"], i["pcless"])}
            for i in ideas]


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


def parse_output(text: str, cid: str, offscreen: bool = False) -> list[dict]:
    parsed = _extract_json(text)
    if isinstance(parsed, dict):
        suggestions = parsed.get("suggestions", [])
    elif isinstance(parsed, list):
        suggestions = parsed
    else:
        suggestions = []
    if not isinstance(suggestions, list):
        return []
    char_ids, player_tokens, loc_ids = valid_ids(cid)
    norm = date_normalizer(cid, tolerant=True)

    out: list[dict] = []
    for e in suggestions:
        if not isinstance(e, dict):
            continue
        title, premise = str(e.get("title", "")).strip(), str(e.get("premise", "")).strip()
        if not title or not premise:
            continue
        raw_cast = e.get("cast", [])
        cast = ([t for t in (str(x).strip() for x in raw_cast)
                 if token_ok(t, char_ids, player_tokens, offscreen)]
                if isinstance(raw_cast, list) else [])
        loc = str(e.get("location", "")).strip()
        out.append({"title": title, "premise": premise, "cast": cast,
                    "location": loc if loc in loc_ids else "",
                    "date": norm(str(e.get("date", "")).strip())})
    return out


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
    error. Store and calendar failures underneath (`valid_ids` reads entities,
    `date_normalizer` imports a user-authored provider) are NOT covered by that
    and surface as the route's ordinary 500, exactly as they do for
    `parse_output`."""
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


def parse_next_date(text: str, cid: str) -> str:
    """The model's general next-scene date estimate, validated; "" when absent/bad."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    return date_normalizer(cid, tolerant=True)(str(raw).strip())


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
