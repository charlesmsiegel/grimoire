"""Cast-derived data: the campaign's offscreen player references, the two-tier
off-scene character directory, the drift-measurement roster, and per-actor
calendar facts.

Four of these five start from the appearance record -- the campaign roster or
the scene cast -- and resolve the actors it names campaign-side. `_char_name`
is the shared name lookup the directory builds on; it takes a root and reads no
appearance record of its own.
"""

from __future__ import annotations

from .. import calendars, characters, dossiers, overlay, pcs
from ..appearances import (cast as appearances_cast, paths as appearances_paths,
                           versions as appearances_versions)
from ..campaigns import paths as campaigns_paths


def _campaign_player_refs(cid: str, aroot) -> tuple[list[dict], list[str]]:
    """(persona data dicts, names) of every campaign-level player actor, seated in
    the scene or not — the offscreen reference cast. Each dict carries "kind" so
    the persona-block templates pick the right format.

    `aroot` is an `appearances.locked_actor_root`: the roster is the appearance
    record, so every actor here has a campaign-side copy."""
    refs: list[dict] = []
    names: list[str] = []
    for a in appearances_cast.roster(cid):
        if a["role"] != "player":
            continue
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(aroot, a["id"], a["version"])
                refs.append({"kind": "pcs", **p})
                names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(aroot, a["id"], a["version"])["data"]
                refs.append({"kind": "characters", **data})
                names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    return refs, names


def _char_name(root, cid: str) -> str:
    try:
        return characters.read_character(root, cid)["meta"]["name"]
    except characters.CharacterNotFound:
        return cid


def _cast_directory_data(croot, cid: str, sid: str) -> tuple[list[dict], list[dict]]:
    """Off-scene cast data for the two-tier directory (the template renders the text):
    campaign-active characters (dossier paragraph) and every other world character
    (tagline + available versions)."""
    present = {a["id"] for a in appearances_cast.scene_cast(cid, sid) if a["kind"] == "characters"}
    roster = appearances_cast.roster(cid)
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}

    active: list[dict] = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present:
            continue
        body = dossiers.read(croot, a["id"])
        if body:
            active.append({"name": _char_name(croot, a["id"]), "dossier": body})

    known: list[dict] = []
    for char_id in overlay.character_refs(cid):
        if char_id in roster_ids or char_id in present:
            continue
        tag = overlay.tagline(cid, char_id)
        if not tag:
            continue
        versions = [v["id"] for v in characters.read_character(overlay.char_root(cid, char_id), char_id)["versions"]]
        known.append({"name": _char_name(overlay.char_root(cid, char_id), char_id),
                     "tagline": tag, "versions": versions})
    return active, known


def _drift_roster(cid: str, npc_names: list[str], player_names: list[str]) -> list[str]:
    """Names drift measurement canonicalizes speaker labels against.

    The present cast plus the CAMPAIGN roster — actors that have appeared in this
    campaign, which `appearances.roster_names` keeps after they leave a scene.
    The window reaches back three turns, so a departed character still has blocks
    in it; dropping their name would split "Winifred" and "Winifred Vance" into
    two speakers on an ordinary departure, inventing a `speakers` violation while
    hiding the real `blocks_per_speaker` one.

    Deliberately NOT every character the campaign can see. Pulling in inherited
    world characters who were never in this campaign's history adds names that
    cannot appear in the measured turns but can still collide: an unrelated
    "Winifred Vale" elsewhere in the world would make the label "Winifred"
    ambiguous and break canonicalization for the Winifred actually on screen.

    Deduplicated, and that is load-bearing: scenes.match_name resolves an exact
    match only when it is UNIQUE, so a name appearing twice in this list would
    make it return None and silently disable canonicalization for that character.
    """
    names = list(npc_names) + list(player_names) + appearances_cast.roster_names(cid)
    return list(dict.fromkeys(n for n in names if n))


def cast_datetime_facts(cid: str, sid: str, native: str) -> list[dict]:
    """Age / birthday-today for each in-scene actor that has a birthdate. Others skipped."""
    croot = campaigns_paths.campaign_root(cid)          # calendar.json is campaign-local
    aroot = appearances_paths.locked_actor_root(cid)    # cast actors are locked, so campaign-side
    cfg = calendars.read_calendar(croot)
    provider = calendars.get_provider(cfg["primary"])
    out: list[dict] = []
    for a in appearances_cast.scene_cast(cid, sid):
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                persona = pcs.read_persona(aroot, a["id"], vid)
                birth, name = persona.get("birthdate", ""), persona.get("name", a["id"])
            else:
                meta = characters.read_character(aroot, a["id"])["meta"]
                birth, name = meta.get("birthdate", ""), meta.get("name", a["id"])
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound):
            continue
        if not birth:
            continue
        try:
            out.append({"kind": a["kind"], "id": a["id"], "name": name,
                        "age": calendars.age(provider, birth, native),
                        "birthday_today": calendars.is_anniversary(provider, birth, native)})
        except calendars.CalendarError:
            continue
    return out
