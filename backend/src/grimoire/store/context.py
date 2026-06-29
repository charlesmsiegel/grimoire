"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later.
"""

from __future__ import annotations

import re

from . import appearances, briefs, campaigns, characters, config, entities, pcs, scenes, worlds


def activate(entries: list[dict], recent_text: str) -> list[dict]:
    """Select world-info entries: keyless = always-on; else any key whole-word (ci) in recent_text."""
    out: list[dict] = []
    for e in entries:
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
    return {"{{user}}": ", ".join(n for n in player_names if n),
            "{{char}}": ", ".join(n for n in npc_names if n)}


def _npc_block(data: dict) -> str:
    parts = [data.get(f, "").strip() for f in ("description", "personality", "scenario")]
    return "\n".join(p for p in parts if p)


def _pc_persona_block(p: dict) -> str:
    head = ", ".join(x for x in (p.get("name", ""), p.get("pronouns", "")) if x)
    body = "\n".join(x for x in (p.get("summary", ""), p.get("description", "")) if x)
    return "\n".join(x for x in (head, body) if x).strip()


def _char_player_block(data: dict) -> str:
    body = "\n".join(data.get(f, "").strip() for f in ("description", "personality") if data.get(f, "").strip())
    return "\n".join(x for x in (data.get("name", ""), body) if x).strip()


def _world_info(croot, recent_text: str, exclude: frozenset = frozenset()) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            entries.append({"name": e["meta"].get("name", meta["id"]), "body": e["body"].strip(), "keys": keys})
    selected = activate(entries, recent_text)
    return "\n\n".join(e["body"] for e in selected if e["body"])


OPENER_INSTRUCTION = (
    "Write the opening narration for a new scene based on the prompt below. "
    "Set the scene vividly in the second person. Do not speak or act for the player."
)


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """A world-informed, character-less opener: instruction + player personas + activated
    world-info (driven by the prompt). Ephemeral — the caller does not persist the result."""
    croot = campaigns.campaign_root(cid)
    subs = scene_substitutions(cid, sid)
    player_blocks: list[str] = []
    for a in appearances.scene_cast(cid, sid):
        if a["role"] != "player":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                player_blocks.append(_pc_persona_block(pcs.read_persona(croot, a["id"], vid)))
            else:
                player_blocks.append(_char_player_block(characters.read_card(croot, a["id"], vid)["data"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    parts = [OPENER_INSTRUCTION] + [b for b in player_blocks if b]
    wi = _world_info(croot, prompt)
    if wi:
        parts.append(wi)
    system_text = _substitute("\n\n".join(parts), subs)
    return [{"role": "system", "content": system_text},
            {"role": "user", "content": _substitute(prompt, subs)}]


def _char_name(root, cid: str) -> str:
    try:
        return characters.read_character(root, cid)["meta"]["name"]
    except characters.CharacterNotFound:
        return cid


def _cast_directory(croot, wroot, cid: str, sid: str) -> str:
    """Off-scene cast as two tiers: campaign-active characters (paragraph) and every
    other world character (tagline + available versions). Empty string if neither tier
    has any briefed members."""
    present = {a["id"] for a in appearances.scene_cast(cid, sid) if a["kind"] == "characters"}
    roster = appearances.roster(cid)
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}

    active: list[str] = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present:
            continue
        b = briefs.read_brief(croot, a["id"])
        if b and b["body"]:
            active.append(f"{_char_name(croot, a['id'])}: {b['body']}")

    known: list[str] = []
    for char_id in characters.character_refs(wroot):
        if char_id in roster_ids or char_id in present:
            continue
        b = briefs.read_brief(wroot, char_id)
        if not b or not b["tagline"]:
            continue
        versions = ", ".join(v["id"] for v in characters.read_character(wroot, char_id)["versions"])
        suffix = f" (available as: {versions})" if versions else ""
        known.append(f"{_char_name(wroot, char_id)}: {b['tagline']}{suffix}")

    if not active and not known:
        return ""
    parts = ["# Other characters in this world",
             "# (Not present. Introduce them only if the story calls for it.)"]
    if active:
        parts.append("## Active in this campaign, elsewhere\n" + "\n".join(active))
    if known:
        parts.append("## Known to exist\n" + "\n".join(known))
    return "\n\n".join(parts)


def build_messages(cid: str, sid: str) -> list[dict]:
    scene = scenes.read_scene(cid, sid)
    history = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
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

    player_blocks: list[str] = []
    player_names: list[str] = []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], vid)
                player_blocks.append(_pc_persona_block(p))
                player_names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(croot, a["id"], vid)["data"]
                player_blocks.append(_char_player_block(data))
                player_names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound, characters.VersionNotFound):
            continue

    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    subs = {"{{user}}": ", ".join(player_names), "{{char}}": ", ".join(npc_names)}

    try:
        depth = max(int(config.read_config().get("context_scan_depth", "8")), 0)
    except (ValueError, TypeError):
        depth = 8
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in history[-depth:]) if depth else ""

    parts: list[str] = []
    parts += [d.get("system_prompt", "").strip() for d in npc_cards if d.get("system_prompt", "").strip()]
    parts += [b for b in (_npc_block(d) for d in npc_cards) if b]
    parts += [b for b in player_blocks if b]
    parts += [d.get("mes_example", "").strip() for d in npc_cards if d.get("mes_example", "").strip()]
    history_ids = scenes.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    exclude: frozenset = frozenset()
    if current_loc:
        try:
            loc_body = entities.read_entity(croot, "locations", current_loc)["body"].strip()
            exclude = frozenset({current_loc})
            if loc_body:
                parts.append("# Current setting\n" + loc_body)
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    wi = _world_info(croot, recent_text, exclude)
    if wi:
        parts.append(wi)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))
    directory = _cast_directory(croot, wroot, cid, sid)
    if directory:
        parts.append(directory)
    system_text = "\n\n".join(parts).strip()
    post_history = "\n\n".join(
        d.get("post_history_instructions", "").strip() for d in npc_cards
        if d.get("post_history_instructions", "").strip()
    ).strip()

    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": _substitute(system_text, subs)})
    messages += [{"role": m["role"], "content": _substitute(m["content"], subs)} for m in history]
    if post_history:
        messages.append({"role": "system", "content": _substitute(post_history, subs)})
    return messages
