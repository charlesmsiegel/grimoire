"""The world's own state, as the prompt sees it: world-info activation plus the
today / weather / character-state / group-state blocks.

`activate` is the swap point the module docstring names -- everything else here
gathers the data one section renders from.
"""

from __future__ import annotations

import re

from .. import calendars, characters, groupstate, overlay, playstate, weather
from ..scenes import read as scenes_read
# Aliased to match `assemble.py` and `macros.py`, and because `_character_states`
# below takes a parameter named `cast`.
from . import cast as cast_data


def keyword_hit(keys, text: str) -> bool:
    """Whole-word, case-insensitive: does any key appear in `text`?

    Factored out of `activate` so archive retrieval (`archive._archive_entries`)
    selects by exactly these semantics rather than a lookalike that drifts from
    them -- "pact" must keep not matching the key "pac" on both sides of the
    seam.
    """
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keys)


def activate(entries: list[dict], recent_text: str, present: frozenset = frozenset()) -> list[dict]:
    """Select world-info entries. Owned entries (owners non-empty) are silent unless one
    owner ref is in `present`; then keyless = always-on, keyed = any key whole-word (ci) in
    recent_text. Unowned entries behave as before."""
    out: list[dict] = []
    for e in entries:
        owners = e.get("owners") or []
        if owners and not any(o in present for o in owners):
            continue  # owned but no owner in scene -> never leak
        keys = e.get("keys") or []
        if not keys:
            out.append(e)
            continue
        if keyword_hit(keys, recent_text):
            out.append(e)
    return out


def _world_info(cid: str, recent_text: str, exclude: frozenset = frozenset(),
                present: frozenset = frozenset()) -> list[dict]:
    """Activated lore/location/item/group/creature entries as
    {"body", "kind", "id"} dicts — _assemble renders the bodies and uses the
    refs (e.g. activated groups pull their campaign state into context)."""
    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in overlay.list_entities(cid, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = overlay.read_entity(cid, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue  # a keyless location surfaces only as the current setting, never always-on
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    return activate(entries, recent_text, present)


def _today_data(cid: str, sid: str, croot) -> dict | None:
    history = scenes_read.get_time_history(cid, sid)
    if not history:
        return None
    cfg = calendars.read_calendar(croot)
    try:
        facts = calendars.today_facts(cfg, history[-1])
    except calendars.CalendarError:
        return None  # garbled date — omit, don't crash
    return {"friendly": facts["friendly"], "weekday": facts["weekday"],
            "secondary_friendly": facts["secondary_friendly"],
            "holidays_today": facts["holidays_today"], "upcoming": facts["upcoming"],
            "cast": cast_data.cast_datetime_facts(cid, sid, history[-1])}


def _weather_data(cid: str, sid: str) -> dict | None:
    """The sky at the scene's current location and moment, or None.

    Tolerant by construction — `current_weather` returns None rather than
    raising for a missing location, a missing moment, or a stored moment the
    campaign's calendar can no longer parse.
    """
    locations = scenes_read.get_location_history(cid, sid)
    moments = scenes_read.get_time_history(cid, sid)
    got = weather.current_weather(cid, locations[-1] if locations else None,
                                  moments[-1] if moments else None)
    if not got:
        return None
    out = {k: got[k] for k in ("condition", "temperature", "wind")}
    # Authored notes ride along: the model gets "the Wintertide storm" rather
    # than only `storm`, which is why a note is stored at all.
    out["notes"] = got.get("notes") or []
    return out


def _character_states(aroot, cast) -> list[dict]:
    """`aroot` is an `appearances.locked_actor_root` — `cast` comes from the
    appearance record, so both the campaign-local state.md and the actor's
    campaign-side copy are found under it."""
    try:
        out = []
        for a in cast:
            if a["role"] != "npc" or a["kind"] != "characters":
                continue
            st = playstate.read_state(aroot, a["id"])
            if st and (st["current_state"] or st["knows"] or st["suspects"]):
                try:
                    name = characters.read_character(aroot, a["id"])["meta"].get("name", a["id"])
                except characters.CharacterNotFound:
                    name = a["id"]
                out.append({"name": name, **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []


def _group_states(cid: str, croot, activated: list[dict]) -> list[dict]:
    """State for each activated group that has a state.md — same failure policy
    as _character_states: a garbled file omits the block, never crashes."""
    try:
        out = []
        for e in activated:
            if e["kind"] != "groups":
                continue
            st = groupstate.read_state(croot, e["id"])
            if st and any(st[k] for k in groupstate.FIELDS):
                out.append({"name": e["name"], **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []
