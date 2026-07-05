"""Ephemeral scene-suggestion helper: assemble deterministic campaign signals (open plot
threads, long-absent cast, calendar facts at the current moment, seedable ids), build the
one-shot prompt, and parse the model's proposed openings. Assembly + prompt/parse only;
the LLM call lives in the route (mirrors absorb.py) and the prompt text in
templates/scene_suggestions/.
"""

from __future__ import annotations

import json

from .. import prompts
from . import (appearances, calendars, campaigns, characters, chronicle,
               entities, greetings, pcs, plot, taglines)

RECENT_WINDOW = 5


def _char_name(croot, aid: str) -> str:
    try:
        return characters.read_character(croot, aid)["meta"].get("name", aid)
    except characters.CharacterNotFound:
        return aid


def _recent_char_ids(cid: str) -> set[str]:
    ids: set[str] = set()
    try:
        recent = chronicle.recent(cid, RECENT_WINDOW)
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        return ids
    for r in recent:
        for ref in r.get("cast", []) or []:
            kind, _, aid = str(ref).partition("/")
            if kind == "characters" and aid:
                ids.add(aid)
    return ids


def _birthdays(croot, now: str, roster: list[dict]) -> list[dict]:
    if not now:
        return []
    try:
        cfg = calendars.read_calendar(croot)
        provider = calendars.get_provider(cfg["primary"])
        now_fixed = calendars.fixed_of(provider, now)
    except (calendars.CalendarError, KeyError):
        return []
    out: list[dict] = []
    for a in roster:
        try:
            if a["kind"] == "pcs":
                birth = pcs.read_persona(croot, a["id"], a["version"]).get("birthdate", "")
                name = pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
            else:
                birth = characters.read_character(croot, a["id"])["meta"].get("birthdate", "")
                name = _char_name(croot, a["id"])
        except (characters.CharacterNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
        if not birth:
            continue
        try:
            when = None
            for d in range(0, calendars.UPCOMING_WINDOW_DAYS + 1):
                if calendars.is_anniversary(provider, birth, provider.format(now_fixed + d)):
                    when = "today" if d == 0 else f"in {d} days"
                    break
            if when is None:
                continue
            out.append({"name": name, "age": calendars.age(provider, birth, now), "when": when})
        except calendars.CalendarError:
            continue
    return out


def build_snapshot(cid: str, offscreen: bool = False) -> dict:
    croot = campaigns.campaign_root(cid)
    roster = appearances.roster(cid)

    try:
        open_threads = plot.open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        open_threads = []

    try:
        recent = chronicle.recent(cid, 1)
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        recent = []
    now = recent[-1].get("date", "") if recent else ""

    friendly, holidays_today, upcoming = "", [], None
    if now:
        try:
            facts = calendars.today_facts(calendars.read_calendar(croot), now)
            friendly, holidays_today, upcoming = facts["friendly"], facts["holidays_today"], facts["upcoming"]
        except (calendars.CalendarError, KeyError):
            pass

    recent_ids = _recent_char_ids(cid)
    absent_cast = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in recent_ids:
            continue
        absent_cast.append({"name": _char_name(croot, a["id"]),
                            "tagline": taglines.read(croot, a["id"])})

    available_cast, seen = [], set()
    for c in characters.list_characters(croot):
        tok = f"characters:{c['id']}"
        seen.add(tok)
        available_cast.append({"token": tok, "name": c.get("name", c["id"])})
    if not offscreen:  # offscreen scenes never cast the player
        for a in roster:
            if a["role"] != "player":
                continue
            tok = f"{a['kind']}:{a['id']}"
            if tok in seen:
                continue
            seen.add(tok)
            try:
                name = (pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
                        if a["kind"] == "pcs" else _char_name(croot, a["id"]))
            except pcs.PCNotFound:
                name = a["id"]
            available_cast.append({"token": tok, "name": name})

    available_locations = [{"id": e["id"], "name": e.get("name", e["id"])}
                           for e in entities.list_entities(croot, "locations")]

    return {"now": now, "friendly": friendly, "holidays_today": holidays_today,
            "upcoming": upcoming, "birthdays": _birthdays(croot, now, roster),
            "open_threads": open_threads, "absent_cast": absent_cast,
            "available_cast": available_cast, "available_locations": available_locations}


GREETING_EXCERPT = 300


def greeting_candidates(cid: str, after: str | None = None, pcless: bool = False) -> list[dict]:
    """Available greetings worth ranking — only when more than two are startable
    (with two or fewer the chooser simply shows them all)."""
    from . import playing  # lazy: playing pulls in context; keep the import graph flat
    avail = [g for g in playing.available_greetings(cid, after)
             if g["available"] and g.get("pcless", False) == pcless]
    if len(avail) <= 2:
        return []
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []
    for g in avail:
        try:
            body = greetings.read_greeting(croot, g["id"])["body"]
        except greetings.GreetingNotFound:
            body = ""
        out.append({"id": g["id"], "name": g["name"],
                    "excerpt": " ".join(body.split())[:GREETING_EXCERPT]})
    return out


def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None,
                 offscreen: bool = False) -> list[dict]:
    # the templates pick the instruction variant and addenda from the same vars
    vars = {"s": snapshot, "offscreen": offscreen, "greeting_candidates": greeting_candidates}
    return [{"role": "system", "content": prompts.render("scene_suggestions/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_suggestions/user.j2", **vars)}]


def _valid_ids(cid: str):
    croot = campaigns.campaign_root(cid)
    char_ids = {c["id"] for c in characters.list_characters(croot)}
    player_tokens = {f"{a['kind']}:{a['id']}" for a in appearances.roster(cid) if a["role"] == "player"}
    loc_ids = {e["id"] for e in entities.list_entities(croot, "locations")}
    return char_ids, player_tokens, loc_ids


def _date_normalizer(cid: str):
    """Canonical native date or "" — a suggested date is only a hint, so never raise."""
    try:
        provider = calendars.get_provider(
            calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
    except (calendars.CalendarError, KeyError):
        return lambda _s: ""

    def norm(s: str) -> str:
        if not s:
            return ""
        try:
            return calendars.normalize(provider, s)
        except calendars.CalendarError:
            return ""
    return norm


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
    char_ids, player_tokens, loc_ids = _valid_ids(cid)
    norm = _date_normalizer(cid)

    def _valid_token(tok: str) -> bool:
        kind, _, aid = tok.partition(":")
        if kind == "characters" and aid in char_ids:
            return True
        return not offscreen and tok in player_tokens

    out: list[dict] = []
    for e in suggestions:
        if not isinstance(e, dict):
            continue
        title, premise = str(e.get("title", "")).strip(), str(e.get("premise", "")).strip()
        if not title or not premise:
            continue
        raw_cast = e.get("cast", [])
        cast = ([t for t in (str(x).strip() for x in raw_cast) if _valid_token(t)]
                if isinstance(raw_cast, list) else [])
        loc = str(e.get("location", "")).strip()
        out.append({"title": title, "premise": premise, "cast": cast,
                    "location": loc if loc in loc_ids else "",
                    "date": norm(str(e.get("date", "")).strip())})
    return out


def parse_next_date(text: str, cid: str) -> str:
    """The model's general next-scene date estimate, validated; "" when absent/bad."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    return _date_normalizer(cid)(str(raw).strip())


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
