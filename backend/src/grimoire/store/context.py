"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later.
"""

from __future__ import annotations

import functools
import re

from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               config, entities, pcs, playstate, plot, relationships, scenes, worlds)


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


def _world_info(croot, recent_text: str, exclude: frozenset = frozenset(),
                present: frozenset = frozenset()) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue  # a keyless location surfaces only as the current setting, never always-on
            entries.append({"name": e["meta"].get("name", meta["id"]),
                            "body": e["body"].strip(), "keys": keys, "owners": owners})
    selected = activate(entries, recent_text, present)
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


def _today_block(cid: str, sid: str, croot) -> str:
    history = scenes.get_time_history(cid, sid)
    if not history:
        return ""
    cfg = calendars.read_calendar(croot)
    try:
        facts = calendars.today_facts(cfg, history[-1])
    except calendars.CalendarError:
        return ""  # garbled date — omit, don't crash
    head = f"It is {facts['friendly']} ({facts['weekday']})"
    head += f"; {facts['secondary_friendly']}." if facts["secondary_friendly"] else "."
    lines = [head]
    if facts["holidays_today"]:
        lines.append("Holidays today: " + ", ".join(facts["holidays_today"]) + ".")
    if facts["upcoming"]:
        u = facts["upcoming"]
        lines.append(f"Upcoming: {u['name']} in {u['in_days']} days.")
    facts_cast = cast_datetime_facts(cid, sid, history[-1])
    if facts_cast:
        bits = [f"it is {c['name']}'s birthday (age {c['age']})" if c["birthday_today"]
                else f"{c['name']} (age {c['age']})" for c in facts_cast]
        lines.append("Present today: " + "; ".join(bits) + ".")
    return "# Today\n" + "\n".join(lines)


def _state_block(name: str, st: dict) -> list[str]:
    """Lines for one NPC in the # Character state block: 'Name: <current_state>' then any
    indented Knows:/Suspects: (continuation lines re-indented so multi-line prose stays
    attached). When current_state is empty the first knowledge label rides the name line,
    so there is never a dangling 'Name:'."""
    cs = st["current_state"].strip()
    knowledge = [(label, st[field].strip().replace("\n", "\n    "))
                 for label, field in (("Knows", "knows"), ("Suspects", "suspects")) if st[field].strip()]
    if cs:
        return [f"{name}: {cs}"] + [f"  {label}: {v}" for label, v in knowledge]
    if not knowledge:
        return []
    (first_label, first_v), *rest = knowledge
    return [f"{name}: {first_label}: {first_v}"] + [f"  {label}: {v}" for label, v in rest]


def _character_state(croot, cast) -> str:
    try:
        lines = []
        for a in cast:
            if a["role"] != "npc" or a["kind"] != "characters":
                continue
            st = playstate.read_state(croot, a["id"])
            if st and (st["current_state"] or st["knows"] or st["suspects"]):
                try:
                    name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
                except characters.CharacterNotFound:
                    name = a["id"]
                lines.extend(_state_block(name, st))
        return "# Character state\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return ""


def _relationships(cid: str, croot, cast) -> str:
    try:
        tokens = [f"{a['kind']}:{a['id']}" for a in cast]
        lines = relationships.render_present(cid, tokens, lambda t: relationships.actor_name(croot, t))
        return "# Relationships\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return ""


def _plot_threads(cid: str) -> str:
    lines = plot.render_open(cid, with_id=False)  # tolerant of a garbled plot.json
    return "# Plot threads\n" + "\n".join(lines) if lines else ""


def _story_so_far(cid: str) -> str:
    # Always-on, non-critical: a garbled chronicle/config must omit the block, never
    # crash the context build (the store may live in a synced folder). Mirrors the
    # tolerance of _today_block / the current-setting block.
    try:
        depth = max(int(config.read_config().get("recap_depth", "5")), 0)
        if not depth:
            return ""
        lines = [f"- {s}" for s in
                 ((r.get("one_line") or r.get("summary") or "").strip()
                  for r in chronicle.recent(cid, depth)) if s]
        return "# Story so far\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — corrupt chronicle.json / config: omit, don't crash
        return ""


def _assemble(cid: str, sid: str) -> dict:
    """One pass producing substituted, labeled system sections + history + post-history.
    Shared by build_messages (joins sections into the system message) and
    context_sections (exposes them for the token breakdown)."""
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

    sys: list[tuple[str, str]] = []

    def add(label: str, text: str) -> None:
        text = text.strip()
        if text:
            sys.append((label, _substitute(text, subs)))

    add("Global system prompt", config.read_config().get("system_prompt", ""))
    add("System prompt", "\n\n".join(d.get("system_prompt", "").strip() for d in npc_cards if d.get("system_prompt", "").strip()))
    add("Character descriptions", "\n\n".join(b for b in (_npc_block(d) for d in npc_cards) if b))
    add("Character state", _character_state(croot, cast))
    add("Relationships", _relationships(cid, croot, cast))
    add("Player personas", "\n\n".join(b for b in player_blocks if b))
    add("Message examples", "\n\n".join(d.get("mes_example", "").strip() for d in npc_cards if d.get("mes_example", "").strip()))

    add("Story so far", _story_so_far(cid))
    add("Plot threads", _plot_threads(cid))
    add("Today", _today_block(cid, sid, croot))

    history_ids = scenes.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    exclude: frozenset = frozenset()
    if current_loc:
        try:
            loc_body = entities.read_entity(croot, "locations", current_loc)["body"].strip()
            exclude = frozenset({current_loc})
            add("Current setting", "# Current setting\n" + loc_body if loc_body else "")
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    present = {f"{a['kind']}:{a['id']}" for a in cast}
    if current_loc:
        present |= {f"locations:{current_loc}"}
    add("World info", _world_info(croot, recent_text, exclude, frozenset(present)))
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))
    add("Off-scene cast", _cast_directory(croot, wroot, cid, sid))

    post_history = "\n\n".join(
        d.get("post_history_instructions", "").strip() for d in npc_cards
        if d.get("post_history_instructions", "").strip()
    ).strip()
    post_history = _substitute(post_history, subs) if post_history else ""

    sub_history = [{"role": m["role"], "content": _substitute(m["content"], subs)} for m in history]
    return {"system": sys, "history": sub_history, "post_history": post_history}


def build_messages(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    messages: list[dict] = []
    system_text = "\n\n".join(t for _, t in a["system"]).strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def context_sections(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    out = [{"label": label, "text": text} for label, text in a["system"]]
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
