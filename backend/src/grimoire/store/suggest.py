"""Ephemeral scene-suggestion and scene-intent prompt builder/parser: assemble
deterministic campaign signals (a story-so-far anchor, open plot threads with
dormancy, a status-annotated cast, calendar facts at the current moment, seedable
ids), build the one-shot prompt, and parse the model's proposed openings (or, for
scene-intent, the metadata implied by the user's own typed description).
Assembly + prompt/parse only; the LLM call lives in the route (mirrors
absorb/prompt.py) and the prompt text in templates/scene_suggestions/ and
templates/scene_intent/.
"""

from __future__ import annotations

import json

from .. import prompts
from . import (birthdays, calendars, characters, chronicle, clock, events,
               greetings, overlay, pcs, playing, plot)
from .appearances import cast as appearances_cast, paths as appearances_paths
from .campaigns import paths as campaigns_paths


def _char_name(aroot, aid: str) -> str:
    """`aroot` is an `appearances.locked_actor_root`; callers pass roster ids."""
    try:
        return characters.read_character(aroot, aid)["meta"].get("name", aid)
    except characters.CharacterNotFound:
        return aid


def _tok(ref: str) -> str:
    kind, _, aid = str(ref).partition("/")
    return f"{kind}:{aid}"


def build_snapshot(cid: str, offscreen: bool = False) -> dict:
    croot = campaigns_paths.campaign_root(cid)    # calendar.json is campaign-local
    aroot = appearances_paths.locked_actor_root(cid)    # roster actors are locked, so campaign-side
    roster = appearances_cast.roster(cid)

    try:
        open_threads = plot.open_threads(cid)
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

    def _dormancy(last_scene: str) -> int:
        if last_scene and last_scene in scene_ids:
            return len(scene_ids) - 1 - scene_ids.index(last_scene)
        return len(scene_ids)  # unknown/missing last_scene (deleted or not-yet-absorbed scene) -> treat as maximally cold

    for t in open_threads:
        t["dormancy"] = _dormancy(t.get("last_scene", ""))

    friendly, holidays_today, upcoming = "", [], None
    events_today: list[str] = []
    if now:
        try:
            facts = calendars.today_facts(calendars.read_calendar(croot), now)
            friendly, holidays_today, upcoming = facts["friendly"], facts["holidays_today"], facts["upcoming"]
        except (calendars.CalendarError, KeyError):
            pass
        # The campaign's scheduled events (#101), merged into the same two
        # fields the calendar's holidays feed — the same merge, through the same
        # `sooner`, that the Today prompt section makes. A suggestion prompt that
        # knew about the coronation while the scene block did not (or the other
        # way round) is exactly the drift that reads as the model inventing
        # things. `day_facts` is tolerant end to end, so no guard here.
        scheduled = events.day_facts(cid, croot, now)
        events_today = scheduled["events_today"]
        upcoming = events.sooner(upcoming, scheduled["upcoming"])

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
    for c in overlay.list_characters(cid):
        tok = f"characters:{c['id']}"
        if offscreen and tok in player_tokens:
            continue   # don't offer the model a token the parser will discard
        seen.add(tok)
        cast.append({"token": tok, "name": c.get("name", c["id"]),
                     "tagline": overlay.tagline(cid, c["id"]),
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

    return {"now": now, "friendly": friendly, "holidays_today": holidays_today,
            "events_today": events_today,
            "upcoming": upcoming, "birthdays": birthdays.upcoming(cid, now, roster),
            "story_so_far": story_so_far, "open_threads": open_threads,
            "cast": cast, "available_locations": available_locations}


GREETING_EXCERPT = 300


def greeting_candidates(cid: str, after: str | None = None, pcless: bool = False) -> list[dict]:
    """Available greetings worth ranking — only when more than two are startable
    (with two or fewer the chooser simply shows them all)."""
    avail = [g for g in playing.available_greetings(cid, after)
             if g["available"] and g.get("pcless", False) == pcless]
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


def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None,
                 offscreen: bool = False, direction: str = "") -> list[dict]:
    # the templates pick the instruction variant and addenda from the same vars
    vars = {"s": snapshot, "offscreen": offscreen,
            "greeting_candidates": greeting_candidates,
            "direction": direction.strip()[:DIRECTION_LIMIT]}
    return [{"role": "system", "content": prompts.render("scene_suggestions/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_suggestions/user.j2", **vars)}]


INTENT_LIMIT = 2000


def build_intent_prompt(cid: str, typed: str, offscreen: bool = False) -> list[dict]:
    """Prompt for extracting metadata from the user's own scene description.

    Over the FULL snapshot, story-so-far included: "the morning after the
    funeral" is exactly the kind of phrase this has to resolve, and only the
    recent chronicle can resolve it."""
    # `direction` is here because scene_intent/user.j2 INCLUDES
    # scene_suggestions/user.j2, which reads it (Task 3) — and both this env and
    # verify_templates render with StrictUndefined, so omitting it is a hard
    # failure, not a silently-empty block.
    vars = {"s": build_snapshot(cid, offscreen=offscreen), "offscreen": offscreen,
            "greeting_candidates": None, "direction": "",
            "typed": typed.strip()[:INTENT_LIMIT]}
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
    char_ids = {c["id"] for c in overlay.list_characters(cid)}
    player_tokens = {f"{a['kind']}:{a['id']}" for a in appearances_cast.roster(cid) if a["role"] == "player"}
    loc_ids = {e["id"] for e in overlay.list_entities(cid, "locations")}
    return char_ids, player_tokens, loc_ids


def date_normalizer(cid: str):
    """Canonical native date or "" — a suggested date is only a hint, so never raise."""
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        return lambda _s: ""

    def norm(s: str) -> str:
        if not s:
            return ""
        try:
            return calendars.normalize(provider, s)
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
    norm = date_normalizer(cid)

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
            "date": date_normalizer(cid)(_str_field(parsed.get("date", ""))),
            "location": loc if loc in loc_ids else "",
            "cast": cast}


def parse_next_date(text: str, cid: str) -> str:
    """The model's general next-scene date estimate, validated; "" when absent/bad."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    return date_normalizer(cid)(str(raw).strip())


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
