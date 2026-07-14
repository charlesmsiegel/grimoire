"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later.

This module gathers DATA; the prompt text and section layout live in
templates/scene/ (see templates/README.md for the variable contract).
build_messages & co. render templates/scene/system.j2 from that data.
"""

from __future__ import annotations

import functools
import re

from .. import prompts
from . import (appearances, calendars, campaigns, characters, checks, chronicle,
               config, dossiers, entities, groupstate, modules, overlay, pcs, playstate,
               plot, relationships, scenes, sheets, styles)


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
        if any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys):
            out.append(e)
    return out


def _substitute(text: str, subs: dict[str, str]) -> str:
    for token, value in subs.items():
        if value:  # only replace when non-empty, so player-less scenes keep {{user}} literal
            # function replacement => `value` is inserted literally (no backslash/group expansion)
            text = re.sub(re.escape(token), lambda _m, v=value: v, text, flags=re.IGNORECASE)
    return text


def scene_substitutions(cid: str, sid: str) -> dict[str, str]:
    """Token map for a scene's current cast: {{user}} -> player names, {{char}} -> NPC names."""
    croot = campaigns.campaign_root(cid)
    npc_names: list[str] = []
    player_names: list[str] = []
    for a in appearances.scene_cast(cid, sid):
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["role"] == "npc":
                npc_names.append(characters.read_card(croot, a["id"], vid)["data"].get("name", ""))
            elif a["kind"] == "pcs":
                player_names.append(pcs.read_persona(croot, a["id"], vid).get("name", a["id"]))
            else:
                player_names.append(characters.read_card(croot, a["id"], vid)["data"].get("name", a["id"]))
        except (characters.CharacterNotFound, characters.VersionNotFound,
                pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
    if not any(player_names) and scenes.is_pcless(cid, sid):
        player_names = _campaign_player_refs(cid, croot)[1]
    return {"{{user}}": ", ".join(n for n in player_names if n),
            "{{char}}": ", ".join(n for n in npc_names if n)}


def _project_history(messages: list[dict]) -> list[dict]:
    """Script lines (templates/scene/history_line.j2) -> conversation roles; merge
    consecutive same-role messages so providers that expect strict alternation are
    happy."""
    out: list[dict] = []
    for m in messages:
        line = prompts.render("scene/history_line.j2", m=m)
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    return out


def _campaign_player_refs(cid: str, croot) -> tuple[list[dict], list[str]]:
    """(persona data dicts, names) of every campaign-level player actor, seated in
    the scene or not — the offscreen reference cast. Each dict carries "kind" so
    the persona-block templates pick the right format."""
    refs: list[dict] = []
    names: list[str] = []
    for a in appearances.roster(cid):
        if a["role"] != "player":
            continue
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], a["version"])
                refs.append({"kind": "pcs", **p})
                names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(croot, a["id"], a["version"])["data"]
                refs.append({"kind": "characters", **data})
                names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    return refs, names


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


OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result."""
    a = _assemble(cid, sid, wi_seed=prompt, full_recap=OPENER_RECAP_DEPTH)
    messages = [{"role": "system", "content": _system_text(a, opener=True)},
                {"role": "user", "content": _substitute(prompt, scene_substitutions(cid, sid))}]
    if a["post_history"]:  # mirrors build_messages
        messages.append({"role": "system", "content": a["post_history"]})
    # the shape rules go last, right before generation, so they outrank everything above
    messages.append({"role": "system",
                     "content": prompts.render("scene/opener_shape.j2", npc_names=a["npc_names"])})
    return messages


def _char_name(root, cid: str) -> str:
    try:
        return characters.read_character(root, cid)["meta"]["name"]
    except characters.CharacterNotFound:
        return cid


def _cast_directory_data(croot, cid: str, sid: str) -> tuple[list[dict], list[dict]]:
    """Off-scene cast data for the two-tier directory (the template renders the text):
    campaign-active characters (dossier paragraph) and every other world character
    (tagline + available versions)."""
    present = {a["id"] for a in appearances.scene_cast(cid, sid) if a["kind"] == "characters"}
    roster = appearances.roster(cid)
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


def cast_datetime_facts(cid: str, sid: str, native: str) -> list[dict]:
    """Age / birthday-today for each in-scene actor that has a birthdate. Others skipped."""
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    provider = calendars.get_provider(cfg["primary"])
    out: list[dict] = []
    for a in appearances.scene_cast(cid, sid):
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                persona = pcs.read_persona(croot, a["id"], vid)
                birth, name = persona.get("birthdate", ""), persona.get("name", a["id"])
            else:
                meta = characters.read_character(croot, a["id"])["meta"]
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


def _today_data(cid: str, sid: str, croot) -> dict | None:
    history = scenes.get_time_history(cid, sid)
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
            "cast": cast_datetime_facts(cid, sid, history[-1])}


def _character_states(croot, cast) -> list[dict]:
    try:
        out = []
        for a in cast:
            if a["role"] != "npc" or a["kind"] != "characters":
                continue
            st = playstate.read_state(croot, a["id"])
            if st and (st["current_state"] or st["knows"] or st["suspects"]):
                try:
                    name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
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


def _sheet_type_label(sheets_def: dict, type_id) -> str:
    st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
    if isinstance(st, dict) and st.get("label"):
        return st["label"]
    return type_id if isinstance(type_id, str) else ""


def _sheet_summary_lines(sheets_def: dict, sheet: dict) -> list[str]:
    """key value entries (resources as key cur/max) for a sheet's assembled
    fields, then its derived values, chunked into ~4-entry lines."""
    type_id = sheet["sheet_type"]
    merged = ({**sheets.default_fields(sheets_def, type_id), **sheet["fields"]}
              if isinstance(type_id, str) else {})
    entries: list[str] = []
    for f in (modules.assembled_fields(sheets_def, type_id) if isinstance(type_id, str) else []):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        v = merged.get(key)
        if f.get("type") == "resource" and isinstance(v, dict):
            entries.append(f"{key} {v.get('current')}/{v.get('max')}")
        else:
            entries.append(f"{key} {v}")
    for name, value in sheet["derived"].items():
        entries.append(f"{name} {value}")
    return [" · ".join(entries[i:i + 4]) for i in range(0, len(entries), 4)]


def _rule_keys_match(keys: list[str], recent_text: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys)


def _mechanics(cid: str, sid: str, cast, recent_text: str) -> dict:
    """Module-driven prompt data (#162): activated rules docs (frontmatter
    always -> sheet_types -> keys, keys capped at 6), compact sheet summaries
    for sheeted cast + the current location, and the available-checks table.
    All empty when no module resolves (modules.resolve)."""
    with sheets.lock_for(cid):
        mid = modules.resolve(cid)
        if mid is None:
            return {"mechanics_rules": [], "mechanics_sheets": [], "mechanics_checks": []}
        pack = modules.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}

        history_ids = scenes.get_location_history(cid, sid)
        current_loc = history_ids[-1] if history_ids else None
        actors = [(a["kind"], a["id"], a["name"]) for a in cast]
        if current_loc:
            try:
                loc_name = overlay.read_entity(cid, "locations", current_loc)["meta"].get("name", current_loc)
                actors.append(("locations", current_loc, loc_name))
            except entities.EntityNotFound:
                pass  # referenced location was deleted — omit from sheet summaries

        mechanics_sheets: list[dict] = []
        present_types: set[str] = set()
        for kind, eid, label in actors:
            sheet = sheets.read(cid, kind, eid)
            if sheet is None:
                continue
            type_id = sheet["sheet_type"]
            entry = {"ref": f"{kind}:{eid}", "label": label,
                     "type_label": _sheet_type_label(sheets_def, type_id)}
            if sheet["errors"]:
                entry["lines"] = ["(sheet invalid)"]
            else:
                if isinstance(type_id, str):
                    present_types.add(type_id)
                entry["lines"] = _sheet_summary_lines(sheets_def, sheet)
            mechanics_sheets.append(entry)

        always_docs, type_docs, key_docs = [], [], []
        for doc in pack["rules"]:
            if doc["always"]:
                always_docs.append(doc)
            elif set(doc["sheet_types"]) & present_types:
                type_docs.append(doc)
            elif doc["keys"] and _rule_keys_match(doc["keys"], recent_text):
                key_docs.append(doc)
        mechanics_rules: list[str] = []
        for doc in always_docs + type_docs + key_docs[:6]:
            rule = modules.read_rule(mid, doc["id"])
            if rule is not None:
                mechanics_rules.append(rule["body"].strip())

        return {"mechanics_rules": mechanics_rules, "mechanics_sheets": mechanics_sheets,
                "mechanics_checks": checks.available_checks(cid, sid)}


def _relationship_lines(cid: str, cast) -> list[str]:
    try:
        tokens = [f"{a['kind']}:{a['id']}" for a in cast]
        return relationships.render_present(cid, tokens, lambda t: relationships.actor_name(cid, t))
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return []


def _story_entries(cid: str, depth: int | None = None, full: bool = False) -> list[str]:
    # Always-on, non-critical: a garbled chronicle/config must omit the block, never
    # crash the context build (the store may live in a synced folder). `depth=None`
    # reads the configured recap_depth (compact one-liners); the opener passes an
    # explicit depth with full=True so the template renders full summaries.
    try:
        if depth is None:
            depth = max(int(config.read_config().get("recap_depth", "5")), 0)
        if depth <= 0:
            return []
        first, second = ("summary", "one_line") if full else ("one_line", "summary")
        return [(r.get(first) or r.get(second) or "").strip()
                for r in chronicle.recent(cid, depth)]
    except Exception:  # noqa: BLE001 — corrupt chronicle.json / config: omit, don't crash
        return []


def _assemble(cid: str, sid: str, wi_seed: str = "", full_recap: int = 0) -> dict:
    """One pass gathering the template data + projected history + post-history.
    build_* render templates/scene/system.j2 from data; context_sections renders
    the per-section templates for the token breakdown. `wi_seed` folds extra text
    (the opener prompt) into the world-info activation window; `full_recap` (> 0)
    selects the full story-so-far variant over the compact recap."""
    scene = scenes.read_scene(cid, sid)
    history = [dict(m) for m in scene["messages"]]
    croot = campaigns.campaign_root(cid)
    cast = appearances.scene_cast(cid, sid)

    npc_cards: list[dict] = []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            npc_cards.append(characters.read_card(croot, a["id"], vid)["data"])
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue

    players: list[dict] = []
    player_names: list[str] = []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], vid)
                players.append({"kind": "pcs", **p})
                player_names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(croot, a["id"], vid)["data"]
                players.append({"kind": "characters", **data})
                player_names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound, characters.VersionNotFound):
            continue

    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    pcless = scene["meta"].get("pcless") == "true"
    refs: list[dict] = []
    ref_names: list[str] = []
    if pcless:
        refs, ref_names = _campaign_player_refs(cid, croot)
    subs = {"{{user}}": ", ".join(player_names or ref_names), "{{char}}": ", ".join(npc_names)}

    try:
        depth = max(int(config.read_config().get("context_scan_depth", "8")), 0)
    except (ValueError, TypeError):
        depth = 8
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in history[-depth:]) if depth else ""
    if wi_seed:  # opener: the prompt stands in for the (absent) recent history
        recent_text = (recent_text + "\n" + wi_seed).strip()

    history_ids = scenes.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    current_setting = ""
    exclude: frozenset = frozenset()
    if current_loc:
        try:
            current_setting = overlay.read_entity(cid, "locations", current_loc)["body"].strip()
            exclude = frozenset({current_loc})
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    present = {f"{a['kind']}:{a['id']}" for a in cast}
    if current_loc:
        present |= {f"locations:{current_loc}"}

    cfg = config.read_config()
    campaign_meta = campaigns.read_campaign(cid)["meta"]
    resolved_style = styles.resolve_style(
        scene_style_id=scene["meta"].get("style_id", ""),
        campaign_style_id=campaign_meta.get("style_id", ""),
        default_style_id=cfg.get("default_style_id", ""))
    offscene_active, offscene_known = _cast_directory_data(croot, cid, sid)
    activated_wi = _world_info(cid, recent_text, exclude, frozenset(present))
    mech = _mechanics(cid, sid, cast, recent_text)
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "global_system_prompt": cfg.get("system_prompt", ""),
        "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
        "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
        "npc_cards": npc_cards,
        "states": _character_states(croot, cast),
        "relationship_lines": _relationship_lines(cid, cast),
        "players": players, "ref_names": ref_names, "refs": refs,
        "story_entries": _story_entries(cid, depth=full_recap or None, full=bool(full_recap)),
        "plot_lines": plot.render_open(cid, with_id=False),
        "today": _today_data(cid, sid, croot),
        "current_setting": current_setting,
        "world_info_bodies": [e["body"] for e in activated_wi],
        "group_states": _group_states(cid, croot, activated_wi),
        "offscene_active": offscene_active, "offscene_known": offscene_known,
        "player_names": player_names,
        "mechanics_rules": mech["mechanics_rules"], "mechanics_sheets": mech["mechanics_sheets"],
        "mechanics_checks": mech["mechanics_checks"],
    }

    post_history = prompts.render("scene/post_history.j2", npc_cards=npc_cards)
    post_history = _substitute(post_history, subs) if post_history else ""

    sub_history = [{"role": m["role"], "content": _substitute(m["content"], subs)}
                   for m in _project_history(history)]
    return {"data": data, "subs": subs, "history": sub_history,
            "post_history": post_history, "npc_names": npc_names}


def _system_text(a: dict, opener: bool = False) -> str:
    return _substitute(prompts.render("scene/system.j2", **{**a["data"], "opener": opener}),
                       a["subs"]).strip()


def build_messages(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    messages: list[dict] = []
    system_text = _system_text(a)
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def build_director_messages(cid: str, sid: str, note: str) -> list[dict]:
    """One offscreen director turn: full system + history, then the note as the
    final user message. The note rides only this call — never persisted."""
    a = _assemble(cid, sid)
    messages: list[dict] = []
    system_text = _system_text(a)
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    messages.append({"role": "user", "content": note})
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


# The token-breakdown view: one entry per system section, mirroring the order in
# templates/scene/system.j2. (label, template, pcless-only)
_SECTIONS = [
    ("Global system prompt", "scene/sections/global_system_prompt.j2", False),
    ("Prose style", "scene/sections/prose_style.j2", False),
    ("System prompt", "scene/sections/card_system_prompts.j2", False),
    ("Character descriptions", "scene/sections/character_descriptions.j2", False),
    ("Character state", "scene/sections/character_state.j2", False),
    ("Relationships", "scene/sections/relationships.j2", False),
    ("Player personas", "scene/sections/player_personas.j2", False),
    ("Offscreen scene", "scene/sections/offscreen_scene.j2", True),
    ("Absent player characters", "scene/sections/absent_players.j2", True),
    ("Message examples", "scene/sections/message_examples.j2", False),
    ("Story so far", "scene/sections/story_so_far/compact.j2", False),
    ("Plot threads", "scene/sections/plot_threads.j2", False),
    ("Today", "scene/sections/today.j2", False),
    ("Current setting", "scene/sections/current_setting.j2", False),
    ("World info", "scene/sections/world_info.j2", False),
    ("Group state", "scene/sections/group_state.j2", False),
    ("Mechanics rules", "scene/sections/mechanics_rules.j2", False),
    ("Mechanics sheets", "scene/sections/mechanics_sheets.j2", False),
    ("Off-scene cast", "scene/sections/off_scene_cast.j2", False),
    ("Mechanics response format", "scene/sections/mechanics_response_format.j2", False),
    ("Response format", "scene/sections/response_format.j2", False),
]


def context_sections(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    out = []
    for label, template, pcless_only in _SECTIONS:
        if pcless_only and not a["data"]["pcless"]:
            continue
        text = _substitute(prompts.render(template, **a["data"]), a["subs"]).strip()
        if text:
            out.append({"label": label, "text": text})
    hist = "\n\n".join(m["content"] for m in a["history"])
    if hist:
        out.append({"label": "Conversation history", "text": hist})
    if a["post_history"]:
        out.append({"label": "Post-history instructions", "text": a["post_history"]})
    return out


@functools.lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:
        return len(text) // 4
