"""The independent copy of the scene data contract: `gather` assembles from
public store reads what `context._assemble` gathers, and `rendered_messages`
renders it the way `context.assemble` composes a turn.

Kept here rather than imported for the reason this whole harness exists: an
independent reconstruction is what makes "the builder and the template agree"
mean something. `gather` is split into one helper per block of the contract,
in the order `context._assemble` reads them.
"""

from __future__ import annotations

import re

from grimoire.store import appearances as ap
from grimoire.store import (
    calendars,
    campaigns,
    characters,
    checks,
    chronicle,
    config,
    context,
    default_prompts,
    entities,
    events,
    groupstate,
    modules,
    overlay,
    pcs,
    playstate,
    relationships,
    response_presets,
    response_targets,
    scenes,
    sheets,
    styles,
    voice_anchors,
    worlds,
)
from grimoire.store import dossiers as dstore
from grimoire.store import taglines as tstore
from grimoire.store import voice_anchors as vastore
from grimoire.store import voice_drift as vdstore
from grimoire.store import weather as wstore
from grimoire.store.continuity import effective
from grimoire.store.tracker import fields as tfields
from grimoire.store.tracker import settings as tsettings
from grimoire.store.tracker import view as tview
from grimoire.store.tracker import walk as twalk

from .fixture import AUTHORS_NOTE, Fixture
from .harness import render


def _secrecy_of(meta: dict) -> str:
    """`entities.normalize_secrecy`, re-implemented rather than imported: this
    harness is the independent copy of the data contract, so it spells the rule
    out the way the templates/README.md description does."""
    level = (meta.get("secrecy") or "public").strip().lower()
    return level if level in ("public", "secret", "gm-only") else "public"


def _cast_blocks(cid, npc_cards, npc_ids):
    """Mirror of context.assemble's cast_blocks. Kept here rather than imported
    for the reason this whole script exists: an independent reconstruction is
    what makes "the builder and the template agree" mean something."""
    def _str(card, key):
        v = card.get(key)
        return v.strip() if isinstance(v, str) else ""

    shown = context.cast.voice_safe_names([_str(d, "name") for d in npc_cards])
    out = []
    for name, card, char_id in zip(shown, npc_cards, npc_ids, strict=True):
        parts = [_str(card, "description"), _str(card, "personality"), _str(card, "scenario")]
        out.append({
            "name": name,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(overlay.voice_anchor_record(cid, char_id)["text"]),
            "example": voice_anchors.truncate(_str(card, "mes_example"),
                                              voice_anchors.VOICE_EXAMPLE_CAP),
        })
    return out


def _npcs(fx: Fixture, cast: list[dict]) -> tuple[list, list, list]:
    """The present NPCs' locked cards, their ids, and the states worth showing."""
    npc_cards, npc_ids, states = [], [], []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = ap.locked_version(fx.cid, a["kind"], a["id"])
        npc_cards.append(characters.read_card(fx.croot, a["id"], vid)["data"])
        npc_ids.append(a["id"])
        st = playstate.read_state(fx.croot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            name = characters.read_character(fx.croot, a["id"])["meta"].get("name", a["id"])
            states.append({"name": name, **st})
    return npc_cards, npc_ids, states


def _player_record(fx: Fixture, kind: str, actor_id: str, version) -> tuple[dict, str]:
    """A player's persona or card at `version`, tagged with its kind, and its name."""
    if kind == "pcs":
        p = pcs.read_persona(fx.croot, actor_id, version)
        return {"kind": "pcs", **p}, p.get("name", actor_id)
    d = characters.read_card(fx.croot, actor_id, version)["data"]
    return {"kind": "characters", **d}, d.get("name", actor_id)


def _players(fx: Fixture, cast: list[dict]) -> tuple[list, list]:
    """The present players, at their locked versions."""
    players, player_names = [], []
    for a in cast:
        if a["role"] != "player":
            continue
        record, name = _player_record(fx, a["kind"], a["id"],
                                      ap.locked_version(fx.cid, a["kind"], a["id"]))
        players.append(record)
        player_names.append(name)
    return players, player_names


def _references(fx: Fixture, pcless: bool) -> tuple[list, list]:
    """An offscreen scene's absent players, at their roster versions."""
    refs, ref_names = [], []
    if not pcless:
        return refs, ref_names
    for a in ap.roster(fx.cid):
        if a["role"] != "player":
            continue
        record, name = _player_record(fx, a["kind"], a["id"], a["version"])
        refs.append(record)
        ref_names.append(name)
    return refs, ref_names


def _story(fx: Fixture, cfg: dict, full_recap: int) -> tuple[list, list]:
    """The recap window's chronicle records, and each one's story line."""
    depth = full_recap or max(int(cfg.get("recap_depth", "5")), 0)
    records = chronicle.recent(fx.cid, depth) if depth > 0 else []
    story_entries = [((r.get("summary") or r.get("one_line") or "") if full_recap
                      else (r.get("one_line") or r.get("summary") or "")).strip()
                     for r in records]
    return records, story_entries


def _current_setting(fx: Fixture, current_loc) -> tuple[str, bool]:
    """The current location's body, and whether it is secret; nothing for a
    gm-only one."""
    if not current_loc:
        return "", False
    loc_meta = entities.read_entity(fx.croot, "locations", current_loc)
    loc_secrecy = _secrecy_of(loc_meta["meta"])
    if loc_secrecy == "gm-only":
        return "", False
    return loc_meta["body"].strip(), loc_secrecy == "secret"


def _recent_text(cfg: dict, scene: dict, wi_seed: str) -> str:
    """The scan window the keyed reads match against, plus any seed."""
    scan = max(int(cfg.get("context_scan_depth", "8")), 0)
    recent_text = "\n".join(m["content"] for m in scene["messages"][-scan:]) if scan else ""
    if wi_seed:
        recent_text = (recent_text + "\n" + wi_seed).strip()
    return recent_text


def _says_any(keys, text: str) -> bool:
    """Whether `text` says any of `keys` as a whole word, ignoring case."""
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keys)


def _archive(fx: Fixture, cfg: dict, records: list, scene_id: str, recent_text: str) -> list:
    """Archive retrieval (#127): absorbed scenes OUTSIDE the recap window whose
    keywords the scan window says, newest id first, capped at archive_depth.
    The recap window and the scene being played are excluded so no scene can
    arrive twice."""
    seen = {r.get("id", "") for r in records} | {scene_id}
    archive_entries = []
    for r in chronicle.read_chronicle(fx.cid).values():
        rid = r.get("id", "")
        keys = [str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()]
        text = (r.get("summary") or r.get("one_line") or "").strip()
        if not rid or rid in seen or not keys or not text:
            continue
        if _says_any(keys, recent_text):
            archive_entries.append({"id": rid, "date": (r.get("date") or "").strip(), "text": text})
    archive_entries.sort(key=lambda h: h["id"], reverse=True)
    return archive_entries[:max(int(cfg.get("archive_depth", "3")), 0)]


def _split(entry_list: list, field: str) -> tuple[list, list]:
    """`field` of each entry, split into the public ones and the secret ones."""
    return ([e[field] for e in entry_list if e["secrecy"] != "secret"],
            [e[field] for e in entry_list if e["secrecy"] == "secret"])


def _keyed_entries(fx: Fixture, current_loc) -> list[dict]:
    """Every world-info candidate: each entity but the current location, and
    no keyless location."""
    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in entities.list_entities(fx.croot, kind):
            if kind == "locations" and meta["id"] == current_loc:
                continue
            e = entities.read_entity(fx.croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "secrecy": _secrecy_of(e["meta"]), "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    return entries


def _world_info(fx: Fixture, current_loc, tokens: list[str], recent_text: str) -> dict:
    """World info, recalled lore and group state, each split by secrecy."""
    present = set(tokens) | ({f"locations:{current_loc}"} if current_loc else set())
    activated = context.activate(_keyed_entries(fx, current_loc), recent_text,
                                 frozenset(present))
    world_info_bodies, secret_world_info_bodies = _split(activated, "body")
    group_states, secret_group_states = [], []
    for e in activated:
        if e["kind"] != "groups":
            continue
        st = groupstate.read_state(fx.croot, e["id"])
        if st and any(st[k] for k in groupstate.FIELDS):
            bucket = secret_group_states if e["secrecy"] == "secret" else group_states
            bucket.append({"name": e["name"], **st})
    return {"world_info_bodies": world_info_bodies,
            "secret_world_info_bodies": secret_world_info_bodies,
            "recalled_lore_bodies": [],   # recall is off in this harness, as by default
            "secret_recalled_lore_bodies": [],
            "group_states": group_states,
            "secret_group_states": secret_group_states}


def _type_label(sheets_def: dict, type_id) -> str:
    st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
    return (st.get("label", type_id) if isinstance(st, dict)
            else (type_id if isinstance(type_id, str) else ""))


def _field_entries(sheets_def: dict, type_id, merged: dict) -> list[str]:
    """One `key value` entry per assembled field of the sheet type."""
    line_entries = []
    for f in (modules.assembled_fields(sheets_def, type_id) if isinstance(type_id, str) else []):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        v = merged.get(key)
        if f.get("type") == "resource" and isinstance(v, dict):
            line_entries.append(f"{key} {v.get('current')}/{v.get('max')}")
        else:
            line_entries.append(f"{key} {v}")
    return line_entries


def _sheet_row(sh: dict, sheets_def: dict, ref: str, label: str) -> dict:
    """One actor's mechanics sheet block: its fields and derived values, four
    to a line, or the invalid marker."""
    type_id = sh["sheet_type"]
    type_label = _type_label(sheets_def, type_id)
    if sh["errors"]:
        return {"ref": ref, "label": label, "type_label": type_label,
                "lines": ["(sheet invalid)"]}
    defaults = sheets.default_fields(sheets_def, type_id) if isinstance(type_id, str) else {}
    merged = {**defaults, **sh["fields"]}
    line_entries = _field_entries(sheets_def, type_id, merged)
    line_entries += [f"{name} {value}" for name, value in sh["derived"].items()]
    lines = [" · ".join(line_entries[i:i + 4]) for i in range(0, len(line_entries), 4)]
    return {"ref": ref, "label": label, "type_label": type_label, "lines": lines}


def _sheet_actors(fx: Fixture, cast: list[dict], current_loc) -> list[tuple]:
    """Who may carry a sheet: the cast, then the current location."""
    actors = [(a["kind"], a["id"], a["name"]) for a in cast]
    if current_loc:
        try:
            loc = entities.read_entity(fx.croot, "locations", current_loc)
            actors.append(("locations", current_loc, loc["meta"].get("name", current_loc)))
        except entities.EntityNotFound:
            pass
    return actors


def _mechanics_sheets(fx: Fixture, sheets_def: dict, actors: list[tuple]) -> tuple[list, set]:
    """Each actor's sheet block, and the sheet types of the valid ones."""
    mechanics_sheets, present_types = [], set()
    for kind, eid, label in actors:
        sh = sheets.read(fx.cid, kind, eid)
        if sh is None:
            continue
        mechanics_sheets.append(_sheet_row(sh, sheets_def, f"{kind}:{eid}", label))
        if not sh["errors"] and isinstance(sh["sheet_type"], str):
            present_types.add(sh["sheet_type"])
    return mechanics_sheets, present_types


def _mechanics_rules(mid: str, pack: dict, present_types: set, recent_text: str) -> list[str]:
    """The always-on rules, then those for a present sheet type, then up to six
    keyed ones the scan window says."""
    always_docs, type_docs, key_docs = [], [], []
    for doc in pack["rules"]:
        if doc["always"]:
            always_docs.append(doc)
        elif set(doc["sheet_types"]) & present_types:
            type_docs.append(doc)
        elif doc["keys"] and _says_any(doc["keys"], recent_text):
            key_docs.append(doc)
    mechanics_rules = []
    for doc in always_docs + type_docs + key_docs[:6]:
        rule = modules.read_rule(mid, doc["id"])
        if rule is not None:
            mechanics_rules.append(rule["body"].strip())
    return mechanics_rules


def _mechanics(fx: Fixture, scene_id: str, cast: list[dict], current_loc,
               recent_text: str) -> dict:
    """The bound module's rules, sheets and checks; all empty without one."""
    mid = modules.resolve(fx.cid)
    if mid is None:
        return {"mechanics_rules": [], "mechanics_sheets": [], "mechanics_checks": []}
    pack = modules.load_pack(mid)
    sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
    mechanics_sheets, present_types = _mechanics_sheets(
        fx, sheets_def, _sheet_actors(fx, cast, current_loc))
    return {"mechanics_rules": _mechanics_rules(mid, pack, present_types, recent_text),
            "mechanics_sheets": mechanics_sheets,
            "mechanics_checks": checks.available_checks(fx.cid, scene_id)}


def _today(fx: Fixture, scene_id: str, time_history: list):
    if not time_history:
        return None
    facts = calendars.today_facts(calendars.read_calendar(fx.croot), time_history[-1])
    # Mirrors context.world_state._today_data, scheduled events included:
    # that function merges the campaign's own dated events into the same two
    # fields, so a mirror that skipped them would render a different Today
    # block for any campaign that has one.
    scheduled = events.day_facts(fx.cid, fx.croot, time_history[-1])
    return {"friendly": facts["friendly"], "weekday": facts["weekday"],
            "secondary_friendly": facts["secondary_friendly"],
            "holidays_today": facts["holidays_today"],
            "events_today": scheduled["events_today"],
            "upcoming": events.sooner(facts["upcoming"], scheduled["upcoming"]),
            "cast": context.cast_datetime_facts(fx.cid, scene_id, time_history[-1])}


def _weather(fx: Fixture, scene_id: str, time_history: list):
    """Mirrors context._weather_data. Derived rather than fixtured: a constant
    would disagree with the real assembly for every scene that has no
    location or no moment, which is most of the scenarios below."""
    location_history = scenes.get_location_history(fx.cid, scene_id)
    if not (location_history and time_history):
        return None
    got = wstore.current_weather(fx.cid, location_history[-1], time_history[-1])
    if not got:
        return None
    weather_now = {k: got[k] for k in ("condition", "temperature", "wind")}
    weather_now["notes"] = got.get("notes") or []
    return weather_now


def _offscene_active(fx: Fixture, roster: list[dict], present_chars: set) -> list[dict]:
    """The roster's absent NPCs that have a dossier."""
    offscene_active = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present_chars:
            continue
        body = dstore.read(fx.croot, a["id"])
        if body:
            name = characters.read_character(fx.croot, a["id"])["meta"]["name"]
            offscene_active.append({"name": name, "dossier": body})
    return offscene_active


def _offscene_known(fx: Fixture, cfg: dict, roster: list[dict], present_chars: set) -> list[dict]:
    """The campaign's characters off the roster that have a tagline."""
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}
    offscene_known = []
    for char_id in characters.character_refs(fx.croot):
        if char_id in roster_ids or char_id in present_chars:
            continue
        tag = tstore.read(fx.croot, char_id)
        if not tag:
            continue
        ch = characters.read_character(fx.croot, char_id)
        offscene_known.append({"id": char_id, "name": ch["meta"]["name"], "tagline": tag,
                               "versions": [v["id"] for v in ch["versions"]]})
    # context.cast._scope_known cuts this tier to `offscene_known_limit` and
    # ranks the survivors by relevance, which is a judgement about a scene and
    # not a data read this mirror can honestly reproduce. The fixture store
    # below stays well under the ceiling so the two agree without it -- asserted
    # rather than assumed, because a fixture that grew past the ceiling would
    # otherwise fail as an unexplained byte mismatch in the section join.
    # A raised error rather than an `assert`, which -O strips: this is a
    # precondition of the comparison below, not a check the harness collects.
    limit = int(cfg.get("offscene_known_limit", config.DEFAULT_OFFSCENE_KNOWN_LIMIT) or 0)
    if limit and len(offscene_known) > limit:
        raise SystemExit(
            f"verify fixture has {len(offscene_known)} tier-3 characters, over the "
            f"offscene_known_limit of {limit}; this mirror does not implement the cut, "
            f"so shrink the fixture or teach gather() the relevance rule")
    return offscene_known


def _tracker_lines(fx: Fixture, scene_id: str) -> list:
    """Mirrors context._assemble's scene state: the record at the transcript's
    tail, seen by the narrator (this harness composes with no assigned actor),
    over the whole scene cast; nothing at all with the tracker off."""
    if not tsettings.enabled(fx.cid):
        return []
    _key, snapshot = twalk.current(fx.cid, scene_id)
    if not snapshot:
        return []
    return tview.lines_for(snapshot, tfields.effective(fx.cid, scene_id), None,
                           twalk.roster(fx.cid, scene_id))



def _standing(fx: Fixture, scene: dict, cfg: dict) -> dict:
    """The standing prompts, the world profile, the response budget and the
    prose style."""
    campaign_meta = campaigns.read_campaign(fx.cid)["meta"]
    # Mirrors context._assemble: style keeps its legacy cascade; the new
    # continuation target resolves separately from it.
    budget = response_presets.resolve(scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    targets = response_targets.resolve(scene_meta=scene["meta"],
                                       campaign_meta=campaign_meta, config=cfg)
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except styles.StyleNotFound:
        resolved_style = None
    world = worlds.read_world(campaign_meta["world"])
    standing_fields = default_prompts.resolve(fx.cid)
    return {"global_system_prompt": cfg.get("system_prompt", ""),
            "world_default_prompt": standing_fields["world"]["text"],
            "campaign_default_prompt": standing_fields["text"],
            "scene_focus": scene["meta"].get("focus", ""),
            # Mirrors context._assemble: the campaign world's profile (#38).
            "world_overview": {"genre": world["meta"]["genre"], "tone": world["meta"]["tone"],
                               "themes": world["meta"]["themes"],
                               "description": world["body"].strip()},
            "budget": targets["continuation"],
            "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
            "prose_style_body": resolved_style["body"].strip() if resolved_style else ""}


def _cast_data(fx: Fixture, scene: dict, cast: list[dict]) -> dict:
    """The present cast's cards, voice blocks, states and nominated speaker."""
    npc_cards, npc_ids, states = _npcs(fx, cast)
    return {"npc_cards": npc_cards,
            # Mirror of context.assemble's cast_blocks -- the one structure
            # character_descriptions and the three voice sections all read.
            "cast_blocks": _cast_blocks(fx.cid, npc_cards, npc_ids),
            "named_npc_count": sum(
                1 for b in _cast_blocks(fx.cid, npc_cards, npc_ids) if b["name"]),
            "states": states,
            # Mirrors context._assemble: derived from the present NPCs' card
            # names and the raw transcript, and None while the toggle is off.
            "speaker": (context.speaker.nominate(
                [d.get("name", "") for d in npc_cards if d.get("name")],
                [dict(m) for m in scene["messages"]])
                if config.speaker_turn_taking() else None)}


def gather(fx: Fixture, scene_id: str, pcless: bool, wi_seed: str = "",
           full_recap: int = 0) -> dict:
    """Mirror context._assemble's data gathering through public store reads —
    this is the data contract documented in templates/README.md."""
    cfg = config.read_config()
    scene = scenes.read_scene(fx.cid, scene_id)
    cast = ap.scene_cast(fx.cid, scene_id)
    players, player_names = _players(fx, cast)
    refs, ref_names = _references(fx, pcless)
    tokens = [f"{a['kind']}:{a['id']}" for a in cast]
    records, story_entries = _story(fx, cfg, full_recap)
    history_ids = scenes.get_location_history(fx.cid, scene_id)
    current_loc = history_ids[-1] if history_ids else None
    current_setting, current_setting_secret = _current_setting(fx, current_loc)
    recent_text = _recent_text(cfg, scene, wi_seed)
    time_history = scenes.get_time_history(fx.cid, scene_id)
    present_chars = {a["id"] for a in cast if a["kind"] == "characters"}
    roster = ap.roster(fx.cid)
    return {**_standing(fx, scene, cfg),
            **_cast_data(fx, scene, cast),
            "tracker_lines": _tracker_lines(fx, scene_id), "tracker_narrator": True,
            # Mirrors context._assemble: the global switch, on by default. Only
            # an NPC-assigned response_actor.j2 reads it; no case here is one.
            "perception_rider": True,
            "relationship_lines": relationships.render_present(
                fx.cid, tokens, lambda t: relationships.actor_name(fx.cid, t)),
            "players": players,
            "ref_names": ref_names, "refs": refs, "story_entries": story_entries,
            "archive_entries": _archive(fx, cfg, records, scene_id, recent_text),
            "history_evidence": [],
            "plot_lines": effective.render_threads(fx.cid, with_id=False),
            "commitment_lines": effective.render_commitments(fx.cid, with_id=False),
            "today": _today(fx, scene_id, time_history),
            "weather": _weather(fx, scene_id, time_history),
            "current_setting": current_setting,
            "current_setting_secret": current_setting_secret,
            **_world_info(fx, current_loc, tokens, recent_text),
            "offscene_active": _offscene_active(fx, roster, present_chars),
            "offscene_known": _offscene_known(fx, cfg, roster, present_chars),
            "player_names": player_names, "pcless": pcless,
            "story_full": bool(full_recap), "opener": False,
            **_mechanics(fx, scene_id, cast, current_loc, recent_text)}


#: `Section.heading`, spelled out independently for the same reason the order
#: below is: a block that was split into several sections for the token
#: breakdown's sake shares one heading, and the render path opens each
#: contiguous run of them with it (context.assemble is the other copy).
#: Template path -> the heading template it shares.
_SHARED_HEADINGS = {
    "scene/sections/off_scene_cast_active.j2": "scene/off_scene_cast_heading.j2",
    "scene/sections/off_scene_cast_known.j2": "scene/off_scene_cast_heading.j2",
}

#: The catalog's sections from the global prompt through the character state,
#: and from the story so far's archive through the response format.
_HEAD_SECTIONS = ["scene/sections/global_system_prompt.j2",
                  "scene/sections/world_default_prompt.j2",
                  "scene/sections/campaign_default_prompt.j2",
                  "scene/sections/scene_focus.j2",
                  "scene/sections/prose_style.j2",
                  "scene/sections/natural_prose.j2",
                  "scene/sections/model_guidance.j2",
                  "scene/sections/card_system_prompts.j2",
                  "scene/sections/world_overview.j2",
                  "scene/sections/character_descriptions.j2",
                  # Catalog order: the voice block sits between who a character IS
                  # and what is true of them right now (context.assemble.SECTIONS).
                  "scene/sections/voice_policy.j2",
                  "scene/sections/voice_anchors.j2",
                  "scene/sections/voice_examples.j2",
                  "scene/sections/character_state.j2"]
_TAIL_SECTIONS = ["scene/sections/archive.j2",
                  "scene/sections/history_recall.j2",
                  "scene/sections/plot_threads.j2",
                  "scene/sections/commitments.j2",
                  "scene/sections/today.j2",
                  "scene/sections/weather.j2",
                  "scene/sections/current_setting.j2",
                  "scene/sections/world_info.j2",
                  "scene/sections/recalled_lore.j2",
                  "scene/sections/group_state.j2",
                  "scene/sections/mechanics_rules.j2",
                  "scene/sections/mechanics_sheets.j2",
                  "scene/sections/off_scene_cast_active.j2",
                  "scene/sections/off_scene_cast_known.j2",
                  "scene/sections/mechanics_response_format.j2",
                  "scene/sections/response_format.j2"]


def _section_names(data: dict, opener: bool) -> list[str]:
    """The section templates a turn renders, in catalog order."""
    names = []
    if opener:
        names.append("scene/opener_instruction/"
                     + ("adapt_" if data.get("opener_adapt") else "")
                     + ("offscreen" if data["pcless"] else "standard") + ".j2")
    names += _HEAD_SECTIONS
    if not opener:                    # Section(except_opener=True)
        names.append("scene/sections/tracker_state.j2")
    names += ["scene/sections/active_speaker.j2",
              "scene/sections/relationships.j2",
              "scene/sections/player_personas.j2"]
    if data["pcless"]:
        names += ["scene/sections/offscreen_scene.j2", "scene/sections/absent_players.j2"]
    names.append("scene/sections/story_so_far/"
                 + ("full" if data["story_full"] else "compact") + ".j2")
    names += _TAIL_SECTIONS
    names.append("scene/sections/response_budget.j2")
    return names


def rendered_system(data: dict, opener: bool = False) -> str:
    """Mirror of context.assemble._render_sections + scene/system.j2: render
    each section, drop the empty ones, join with blank lines.

    The order is spelled out here rather than read off `context.SECTIONS`,
    which is the point — the prompt's section order is now a single list in
    code, and this is the independent copy that makes reordering it a
    deliberate two-sided change instead of a silent one. `_SHARED_HEADINGS` is
    the same deal for `Section.heading`.
    """
    data = {"model_guidance": "", **data}
    sections: list[str] = []
    last_head = None
    for n in _section_names(data, opener):
        body = render(n, **data).strip()
        if not body:                      # an empty section does not split a run
            continue
        head = _SHARED_HEADINGS.get(n)
        if head and head != last_head:    # one copy per CONTIGUOUS run
            body = render(head, **data).strip() + "\n\n" + body
        sections.append(body)
        last_head = head
    if data.get("response_actor") and not opener:
        sections.append(render("scene/response_actor.j2", **data).strip())
    return render("scene/system.j2", sections=sections).strip()


NARRATOR_INSTRUCTION = "Set the scene as narrator. Do not write any NPC or PC actions or dialogue."
ADAPT_NARRATOR_INSTRUCTION = ("Set the scene as narrator, from the greeting's setting and situation. "
                              "Do not write any NPC or PC actions or dialogue.")


def _note_split(messages: list[dict], depth: int) -> int:
    """Where an author's note at `depth` goes: before the `depth`-th most
    recent post (0 = after the last), clamped to the start, then snapped back
    to the start of a run of player posts, or the start. Written out here
    rather than imported -- this script is a mirror of the real path."""
    if depth <= 0:
        return len(messages)
    for i in range(max(len(messages) - depth, 0), -1, -1):
        if (i < len(messages) and messages[i]["role"] == "user"
                and (i == 0 or messages[i - 1]["role"] != "user")):
            return i
    return 0


def _authors_note() -> dict:
    return {"role": "system", "content": render("scene/authors_note.j2", level="campaign",
                                                name="", text=AUTHORS_NOTE["text"])}


def _history_with_note(messages: list[dict]) -> list[dict]:
    """The projected history, the fixture's author's note at its split point."""
    out: list[dict] = []
    split = _note_split(messages, AUTHORS_NOTE["depth"])
    for n, m in enumerate(messages):
        if n == split:
            out.append(_authors_note())
        line = render("scene/history_line.j2", m=m)
        if out and out[-1]["role"] == m["role"] and out[-1]["role"] != "system":
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    if split == len(messages):
        out.append(_authors_note())
    return out


def _post_history(fx: Fixture, scene_id: str, data: dict) -> str:
    """Mirrors context._assemble exactly — this script's whole point is proving
    the templates render what the real path renders, so the corrective is
    measured from the same scene rather than injected from a fixture.
    This mirror has no assigned response actor, so the actor-scoped drift
    corrective is absent just as it is in context._assemble."""
    correction = ""
    # Same mirroring for the voice corrective: read off the scene's own cast and
    # flag files, not injected, so a change to either half shows up here.
    voice_notes = [{"name": characters.read_character(fx.croot, a["id"])["meta"]["name"],
                    "note": vdstore.read(fx.croot, a["id"])}
                   for a in ap.scene_cast(fx.cid, scene_id)
                   if a["kind"] == "characters" and a["role"] == "npc"
                   and vdstore.read(fx.croot, a["id"]) and vastore.read(fx.croot, a["id"])]
    voice = render("scene/voice_correction.j2", voice_notes=voice_notes) if voice_notes else ""
    return render("scene/post_history.j2", npc_cards=data["npc_cards"],
                  voice_correction=voice, length_correction=correction)


def rendered_messages(fx: Fixture, scene_id: str, data: dict, note: str | None = None,
                      opener_prompt: str | None = None) -> list[dict]:
    out = []
    system = rendered_system(data, opener=opener_prompt is not None)
    if system:
        out.append({"role": "system", "content": system})
    if opener_prompt is None:
        out += _history_with_note(scenes.read_scene(fx.cid, scene_id)["messages"])
    if note is not None:
        out.append({"role": "user", "content": note})
    if opener_prompt is not None:
        out.append({"role": "user", "content": opener_prompt})
        # An opener has no history: the note follows its prompt.
        out.append(_authors_note())
    post = _post_history(fx, scene_id, data)
    if post:
        out.append({"role": "system", "content": post})
    if opener_prompt is not None:
        out.append({"role": "system", "content": ADAPT_NARRATOR_INSTRUCTION
                    if data.get("opener_adapt") else NARRATOR_INSTRUCTION})
    return out
