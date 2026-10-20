"""suggest: snapshot responsibilities."""

from __future__ import annotations

from collections.abc import Callable

from .. import (
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
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..continuity import drivers as continuity_drivers
from ..continuity import effective, pressure
from . import _contracts


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

#: A commitment's reading when no driver answers for it.
_NO_READING = {"state": "ok", "in_days": None, "friendly": ""}


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
        return max(calendars.warn_days(croot), _contracts.NEAR_MIN_DAYS)
    except Exception:  # noqa: BLE001 -- an unreadable calendar.json still gets the floor
        return _contracts.NEAR_MIN_DAYS


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
                 if i["kind"] in _contracts._SOONER_KINDS and i["label"] == upcoming["name"]
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
