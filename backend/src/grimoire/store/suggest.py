"""Ephemeral scene-suggestion helper: assemble deterministic campaign signals (open plot
threads, long-absent cast, calendar facts at the current moment, seedable ids), build the
one-shot prompt, and parse the model's proposed openings. Assembly + prompt/parse only;
the LLM call lives in the route (mirrors absorb.py / briefs.py).
"""

from __future__ import annotations

import json

from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               entities, pcs, plot, worlds)

RECENT_WINDOW = 5


def _world_root(cid: str):
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


def _char_name(croot, aid: str) -> str:
    try:
        return characters.read_character(croot, aid)["meta"].get("name", aid)
    except characters.CharacterNotFound:
        return aid


def _recent_char_ids(cid: str) -> set[str]:
    ids: set[str] = set()
    for r in chronicle.recent(cid, RECENT_WINDOW):
        for ref in r.get("cast", []) or []:
            kind, _, aid = str(ref).partition("/")
            if kind == "characters" and aid:
                ids.add(aid)
    return ids


def _birthdays(cid: str, croot, now: str) -> list[dict]:
    if not now:
        return []
    try:
        cfg = calendars.read_calendar(croot)
        provider = calendars.get_provider(cfg["primary"])
        now_fixed = calendars.fixed_of(provider, now)
    except (calendars.CalendarError, KeyError):
        return []
    out: list[dict] = []
    for a in appearances.roster(cid):
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


def build_snapshot(cid: str) -> dict:
    croot = campaigns.campaign_root(cid)
    wroot = _world_root(cid)

    try:
        open_threads = plot.open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        open_threads = []

    recent = chronicle.recent(cid, 1)
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
    for a in appearances.roster(cid):
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in recent_ids:
            continue
        b = briefs.read_brief(croot, a["id"])
        absent_cast.append({"name": _char_name(croot, a["id"]),
                            "tagline": (b["tagline"] if b else "") or ""})

    available_cast = [{"token": f"characters:{c['id']}", "name": c.get("name", c["id"])}
                      for c in characters.list_characters(wroot)]
    for a in appearances.roster(cid):
        if a["role"] != "player":
            continue
        try:
            name = (pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
                    if a["kind"] == "pcs" else _char_name(croot, a["id"]))
        except pcs.PCNotFound:
            name = a["id"]
        available_cast.append({"token": f"{a['kind']}:{a['id']}", "name": name})

    available_locations = [{"id": e["id"], "name": e.get("name", e["id"])}
                           for e in entities.list_entities(croot, "locations")]

    return {"now": now, "friendly": friendly, "holidays_today": holidays_today,
            "upcoming": upcoming, "birthdays": _birthdays(cid, croot, now),
            "open_threads": open_threads, "absent_cast": absent_cast,
            "available_cast": available_cast, "available_locations": available_locations}
