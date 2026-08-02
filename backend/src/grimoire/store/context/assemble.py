"""Assembly: the single gathering pass and the entry points that render it.

`_assemble` collects every section's data once; `build_messages`,
`build_director_messages`, `build_opener_messages` and `context_sections` all
run off that one dict; its keys are keyword arguments to the templates, so
their order is immaterial.

`_SECTIONS` is the prompt's section order — the one list, not a mirror of one.
It used to be a mirror: templates/scene/system.j2 re-`include`d each section
itself, so the prompt and the token breakdown were two render paths over the
same data and only the breakdown was allowed to be approximate. That is fine
while every section is all-or-nothing and becomes a lie the moment a packer
drops one, so `_render_sections` now renders the list and system.j2 only joins
what it is handed.
"""

from __future__ import annotations

from typing import NamedTuple

from ... import prompts
from .. import (characters, config, entities, length_drift, lengths, overlay,
                pcs, plot, response_presets, styles)
from ..appearances import (cast as appearances_cast, paths as appearances_paths,
                           versions as appearances_versions)
from ..campaigns import paths as campaigns_paths, read as campaigns_read
from ..scenes import read as scenes_read, turns as scenes_turns
# Module objects, not names: `_assemble` binds a local `cast` (hence the alias),
# and `cast._drift_roster` has to stay patchable from the test that counts it.
from . import (archive, cast as cast_data, macros, mechanics, pack, story,
               tokens, world_state)

OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result."""
    a = _assemble(cid, sid, wi_seed=prompt, full_recap=OPENER_RECAP_DEPTH)
    # Both trailing messages are rendered before packing so their tokens can be
    # reserved: neither is droppable, so neither may go uncounted.
    user_text = macros.expand_macros(prompt, macros.scene_substitutions(cid, sid), cid, sid)
    shape = prompts.render("scene/opener_shape.j2", npc_names=a["npc_names"])
    p = _packed(a, cid, sid, opener=True, reserve=(user_text, shape))
    messages = [{"role": "system", "content": _system_text(p["sections"])},
                {"role": "user", "content": user_text}]
    if a["post_history"]:  # mirrors build_messages
        messages.append({"role": "system", "content": a["post_history"]})
    # the shape rules go last, right before generation, so they outrank everything above
    messages.append({"role": "system", "content": shape})
    return messages


def _assemble(cid: str, sid: str, wi_seed: str = "", full_recap: int = 0,
              turn: dict | None = None) -> dict:
    """One pass gathering the template data + projected history + post-history.
    build_* render templates/scene/system.j2 from data; context_sections renders
    the per-section templates for the token breakdown. `wi_seed` folds extra text
    (the opener prompt) into the world-info activation window; `full_recap` (> 0)
    selects the full story-so-far variant over the compact recap. `turn` is a
    one-shot, unpersisted override (e.g. a per-turn response-length chip) that
    outranks every stored scope in response_presets.resolve -- see build_messages."""
    scene = scenes_read.read_scene(cid, sid)
    history = [dict(m) for m in scene["messages"]]
    croot = campaigns_paths.campaign_root(cid)          # campaign-local: dossiers, calendar, group state
    aroot = appearances_paths.locked_actor_root(cid)    # cast/roster actors are locked, so campaign-side
    cast = appearances_cast.scene_cast(cid, sid)

    npc_cards: list[dict] = []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            npc_cards.append(characters.read_card(aroot, a["id"], vid)["data"])
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue

    players: list[dict] = []
    player_names: list[str] = []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(aroot, a["id"], vid)
                players.append({"kind": "pcs", **p})
                player_names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(aroot, a["id"], vid)["data"]
                players.append({"kind": "characters", **data})
                player_names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound, characters.VersionNotFound):
            continue

    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    pcless = scene["meta"].get("pcless") == "true"
    refs: list[dict] = []
    ref_names: list[str] = []
    if pcless:
        refs, ref_names = cast_data._campaign_player_refs(cid, aroot)
    # {{char}} is not resolved here (#137): baked at creation time instead, see
    # scene_substitutions.
    subs = {"{{user}}": ", ".join(player_names or ref_names)}

    try:
        depth = max(int(config.read_config().get("context_scan_depth", "8")), 0)
    except (ValueError, TypeError):
        depth = 8
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in history[-depth:]) if depth else ""
    if wi_seed:  # opener: the prompt stands in for the (absent) recent history
        recent_text = (recent_text + "\n" + wi_seed).strip()

    history_ids = scenes_read.get_location_history(cid, sid)
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
    campaign_meta = campaigns_read.read_campaign(cid)["meta"]
    # One per-field cascade resolves BOTH the prose style and the length budget
    # over turn -> scene -> campaign -> global. It subsumes the old style-only
    # resolver: with no response presets set anywhere (every pre-existing
    # install) it walks the same style_id keys in the same order, so the
    # migration is a no-op.
    budget = response_presets.resolve(turn=turn or {}, scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except (styles.StyleNotFound, OSError, UnicodeDecodeError):
        # resolve() already skips ids that don't exist; this also covers a file
        # that exists but can't be read, which must not break generation either.
        resolved_style = None
    offscene_active, offscene_known = cast_data._cast_directory_data(croot, cid, sid)
    activated_wi = world_state._world_info(cid, recent_text, exclude, frozenset(present))
    mech = mechanics._mechanics(cid, sid, cast, recent_text)
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "global_system_prompt": cfg.get("system_prompt", ""),
        "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
        "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
        "budget": {k: budget[k] for k in lengths.KNOBS},
        "npc_cards": npc_cards,
        "states": world_state._character_states(aroot, cast),
        "relationship_lines": story._relationship_lines(cid, cast),
        "players": players, "ref_names": ref_names, "refs": refs,
        "story_entries": story._story_entries(cid, depth=full_recap or None, full=bool(full_recap)),
        # The archive excludes what the recap already shows, and (via `before`)
        # this scene and any scene after it: a scene absorbed earlier still has
        # a chronicle record, so without that bound, continuing an old scene
        # could recall the present -- or the future -- as a past event.
        "archive_entries": archive._archive_entries(
            cid, recent_text, story._recap_ids(cid, full_recap or None), before=sid),
        "plot_lines": plot.render_open(cid, with_id=False),
        "today": world_state._today_data(cid, sid, croot),
        "weather": world_state._weather_data(cid, sid),
        "current_setting": current_setting,
        "world_info_bodies": [e["body"] for e in activated_wi],
        "group_states": world_state._group_states(cid, croot, activated_wi),
        "offscene_active": offscene_active, "offscene_known": offscene_known,
        "player_names": player_names,
        "mechanics_rules": mech["mechanics_rules"], "mechanics_sheets": mech["mechanics_sheets"],
        "mechanics_checks": mech["mechanics_checks"],
    }

    # The roster is passed as a thunk: it opens one card file per campaign actor,
    # and measure() bails out immediately on a scene with no recorded turns —
    # which is every scene until its first tracked generation lands.
    drift = length_drift.measure(history, scenes_turns.get_turn_sizes(cid, sid),
                                 lambda: cast_data._drift_roster(cid, npc_names, player_names),
                                 {k: budget[k] for k in lengths.KNOBS})
    length_correction = (prompts.render("scene/length_correction.j2",
                                        drift=drift,
                                        budget={k: budget[k] for k in lengths.KNOBS})
                         if drift else "")

    post_history = prompts.render("scene/post_history.j2", npc_cards=npc_cards,
                                  length_correction=length_correction)
    post_history = macros.expand_macros(post_history, subs, cid, sid) if post_history else ""

    sub_history = [{"role": m["role"], "content": macros.expand_macros(m["content"], subs, cid, sid)}
                   for m in story._project_history(history)]
    return {"data": data, "subs": subs, "history": sub_history,
            "post_history": post_history, "npc_names": npc_names}


class Section(NamedTuple):
    """One system-message section: its inspector label, its template, the tier
    the packer drops it at, and the two selectors that decide whether it renders
    at all."""
    label: str
    template: str
    tier: str
    pcless_only: bool = False
    opener_only: bool = False


#: The system message, in order. `_render_sections` renders it and system.j2
#: joins the result — this list, not the template, is where the order lives.
_SECTIONS = [
    Section("Opener instruction", "scene/opener_instruction", pack.LOCK_IN, opener_only=True),
    Section("Global system prompt", "scene/sections/global_system_prompt.j2", pack.LOCK_IN),
    Section("Prose style", "scene/sections/prose_style.j2", pack.LOCK_IN),
    Section("Natural prose", "scene/sections/natural_prose.j2", pack.LOCK_IN),
    Section("System prompt", "scene/sections/card_system_prompts.j2", pack.LOCK_IN),
    Section("Character descriptions", "scene/sections/character_descriptions.j2", pack.LOCK_IN),
    Section("Character state", "scene/sections/character_state.j2", pack.SPOTLIGHT),
    Section("Relationships", "scene/sections/relationships.j2", pack.SPOTLIGHT),
    Section("Player personas", "scene/sections/player_personas.j2", pack.LOCK_IN),
    Section("Offscreen scene", "scene/sections/offscreen_scene.j2", pack.LOCK_IN, pcless_only=True),
    Section("Absent player characters", "scene/sections/absent_players.j2", pack.LOCK_IN, pcless_only=True),
    Section("Message examples", "scene/sections/message_examples.j2", pack.BACKGROUND),
    Section("Story so far", "scene/sections/story_so_far", pack.BACKGROUND),
    Section("Earlier scenes", "scene/sections/archive.j2", pack.ARCHIVE),
    Section("Plot threads", "scene/sections/plot_threads.j2", pack.SPOTLIGHT),
    Section("Today", "scene/sections/today.j2", pack.SPOTLIGHT),
    Section("Weather", "scene/sections/weather.j2", pack.SPOTLIGHT),
    Section("Current setting", "scene/sections/current_setting.j2", pack.SPOTLIGHT),
    Section("World info", "scene/sections/world_info.j2", pack.SPOTLIGHT),
    Section("Group state", "scene/sections/group_state.j2", pack.SPOTLIGHT),
    Section("Mechanics rules", "scene/sections/mechanics_rules.j2", pack.SPOTLIGHT),
    Section("Mechanics sheets", "scene/sections/mechanics_sheets.j2", pack.SPOTLIGHT),
    Section("Off-scene cast", "scene/sections/off_scene_cast.j2", pack.BACKGROUND),
    Section("Mechanics response format", "scene/sections/mechanics_response_format.j2", pack.LOCK_IN),
    Section("Response format", "scene/sections/response_format.j2", pack.LOCK_IN),
    Section("Response budget", "scene/sections/response_budget.j2", pack.LOCK_IN),
]

#: The two sections that pick a variant file from the assembled data. Everything
#: else in `_SECTIONS` names its template outright.
_VARIANTS = {
    "scene/opener_instruction": lambda d: "offscreen" if d["pcless"] else "standard",
    "scene/sections/story_so_far": lambda d: "full" if d["story_full"] else "compact",
}


def _section_template(section: Section, data: dict) -> str:
    pick = _VARIANTS.get(section.template)
    return f"{section.template}/{pick(data)}.j2" if pick else section.template


def _render_sections(a: dict, cid: str, sid: str, opener: bool = False) -> list[dict]:
    """Every applicable section, rendered and macro-expanded once, in order.

    THE render path: `build_messages` joins what survives packing into the
    system message and `context_sections` reports the same list, so a section
    the inspector shows as sent is a section that was sent. Empty sections drop
    out here, exactly as system.j2's per-section `if s.strip()` used to.
    """
    data = {**a["data"], "opener": opener}
    out = []
    for section in _SECTIONS:
        if section.pcless_only and not data["pcless"]:
            continue
        if section.opener_only and not opener:
            continue
        text = macros.expand_macros(prompts.render(_section_template(section, data), **data),
                                    a["subs"], cid, sid).strip()
        if text:
            out.append({"label": section.label, "text": text, "tier": section.tier})
    return out


def _packed(a: dict, cid: str, sid: str, opener: bool = False,
            reserve: tuple[str, ...] = ()) -> dict:
    """Render the sections and fit them, with the history, under the configured
    budget. The opener carries no history, so it packs against an empty one.

    `reserve` is every message the caller appends AFTER the system message --
    a director note, regenerate guidance, a roll-result block, the opener's
    prompt and shape rules. The packer cannot drop any of them, so leaving them
    uncounted would pack to a ceiling the real request then sails straight past,
    which is the provider-side truncation this exists to prevent. post_history
    is always reserved; callers name the rest.
    """
    budget = pack.budget_tokens()
    # Only worth the tokeniser calls when there is a budget to charge against.
    reserved = (tokens.count_tokens(a["post_history"])
                + sum(tokens.count_tokens(t) for t in reserve)) if budget > 0 else 0
    # `compose` is the real renderer, so the string the packer measures is the
    # string `_system_text` then produces -- not an estimate of it.
    return pack.pack(_render_sections(a, cid, sid, opener=opener),
                     [] if opener else a["history"], reserved, budget,
                     compose=_compose_system)


def _compose_system(texts: list[str]) -> str:
    """The system message as it will be sent, from section texts."""
    return prompts.render("scene/system.j2", sections=texts).strip()


def _system_text(packed_sections: list[dict]) -> str:
    """Join the sections that survived packing into the system message."""
    return _compose_system([s["text"] for s in packed_sections if not s["dropped"]])


def build_messages(cid: str, sid: str, turn: dict | None = None,
                   reserve: tuple[str, ...] = ()) -> list[dict]:
    """`turn` is a one-shot, unpersisted response-preset override (a pending
    per-turn length chip) that beats the scene/campaign/global cascade for this
    call only -- see response_presets.resolve. Callers that need it to survive a
    failed generation (retry, regenerate) must re-pass it themselves; nothing
    here remembers it.

    `reserve` is any message the CALLER will append to the returned list
    (regenerate guidance, a roll-result block). Pass it, or the budget is
    charged for less than the request actually sends -- see _packed."""
    a = _assemble(cid, sid, turn=turn)
    p = _packed(a, cid, sid, reserve=reserve)
    messages: list[dict] = []
    system_text = _system_text(p["sections"])
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += p["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def build_director_messages(cid: str, sid: str, note: str, turn: dict | None = None) -> list[dict]:
    """One offscreen director turn: full system + history, then the note as the
    final user message. The note rides only this call — never persisted. `turn`
    is the same one-shot response-preset override as build_messages."""
    a = _assemble(cid, sid, turn=turn)
    # expanded up front so the note's tokens are reserved before packing: it is
    # a mandatory message, so the budget has to know about it
    note_text = macros.expand_macros(note, a["subs"], cid, sid)
    p = _packed(a, cid, sid, reserve=(note_text,))
    messages: list[dict] = []
    system_text = _system_text(p["sections"])
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += p["history"]
    messages.append({"role": "user", "content": note_text})
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def context_sections(cid: str, sid: str) -> list[dict]:
    """The token-breakdown view: every section the packer produced, in prompt
    order, each carrying its `tier` and whether the packer `dropped` it — then
    the (possibly trimmed) history and the post-history block.

    Dropped sections stay in the list with their text: the inspector's job is to
    show what was cut, and a drop the user cannot see is the silent truncation
    this replaced. Runs the same render and pack `build_messages` runs.
    """
    a = _assemble(cid, sid)
    p = _packed(a, cid, sid)
    out = [{"label": s["label"], "text": s["text"], "tier": s["tier"], "dropped": s["dropped"]}
           for s in p["sections"]]
    hist = "\n\n".join(m["content"] for m in p["history"])
    if hist:
        out.append({"label": "Conversation history", "text": hist, "tier": pack.HISTORY,
                    "dropped": False, "trimmed": p["history_trimmed"]})
    if a["post_history"]:
        out.append({"label": "Post-history instructions", "text": a["post_history"],
                    "tier": pack.LOCK_IN, "dropped": False})
    return out
