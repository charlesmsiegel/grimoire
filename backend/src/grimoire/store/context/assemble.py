"""Assembly: the single gathering pass and the entry points that render it.

`_assemble` collects every section's data once; `build_messages`,
`build_director_messages`, `build_opener_messages` and `context_sections` all
run off that one dict; its keys are keyword arguments to the templates, so
their order is immaterial. The prompt's section order lives in
templates/scene/system.j2 — `_SECTIONS` mirrors it for the token breakdown and
has to be kept in step with it.
"""

from __future__ import annotations

from ... import prompts
from .. import (characters, config, entities, length_drift, lengths, overlay,
                pcs, plot, response_presets, styles)
from ..appearances import (cast as appearances_cast, paths as appearances_paths,
                           versions as appearances_versions)
from ..campaigns import paths as campaigns_paths, read as campaigns_read
from ..scenes import read as scenes_read, turns as scenes_turns
# Module objects, not names: `_assemble` binds a local `cast` (hence the alias),
# and `cast._drift_roster` has to stay patchable from the test that counts it.
from . import cast as cast_data, macros, mechanics, story, world_state

OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result."""
    a = _assemble(cid, sid, wi_seed=prompt, full_recap=OPENER_RECAP_DEPTH)
    messages = [{"role": "system", "content": _system_text(a, cid, sid, opener=True)},
                {"role": "user", "content": macros.expand_macros(prompt, macros.scene_substitutions(cid, sid), cid, sid)}]
    if a["post_history"]:  # mirrors build_messages
        messages.append({"role": "system", "content": a["post_history"]})
    # the shape rules go last, right before generation, so they outrank everything above
    messages.append({"role": "system",
                     "content": prompts.render("scene/opener_shape.j2", npc_names=a["npc_names"])})
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


def _system_text(a: dict, cid: str, sid: str, opener: bool = False) -> str:
    return macros.expand_macros(prompts.render("scene/system.j2", **{**a["data"], "opener": opener}),
                                a["subs"], cid, sid).strip()


def build_messages(cid: str, sid: str, turn: dict | None = None) -> list[dict]:
    """`turn` is a one-shot, unpersisted response-preset override (a pending
    per-turn length chip) that beats the scene/campaign/global cascade for this
    call only -- see response_presets.resolve. Callers that need it to survive a
    failed generation (retry, regenerate) must re-pass it themselves; nothing
    here remembers it."""
    a = _assemble(cid, sid, turn=turn)
    messages: list[dict] = []
    system_text = _system_text(a, cid, sid)
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def build_director_messages(cid: str, sid: str, note: str, turn: dict | None = None) -> list[dict]:
    """One offscreen director turn: full system + history, then the note as the
    final user message. The note rides only this call — never persisted. `turn`
    is the same one-shot response-preset override as build_messages."""
    a = _assemble(cid, sid, turn=turn)
    messages: list[dict] = []
    system_text = _system_text(a, cid, sid)
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    messages.append({"role": "user", "content": macros.expand_macros(note, a["subs"], cid, sid)})
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


# The token-breakdown view: one entry per system section, mirroring the order in
# templates/scene/system.j2. (label, template, pcless-only)
_SECTIONS = [
    ("Global system prompt", "scene/sections/global_system_prompt.j2", False),
    ("Prose style", "scene/sections/prose_style.j2", False),
    ("Natural prose", "scene/sections/natural_prose.j2", False),
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
    ("Weather", "scene/sections/weather.j2", False),
    ("Current setting", "scene/sections/current_setting.j2", False),
    ("World info", "scene/sections/world_info.j2", False),
    ("Group state", "scene/sections/group_state.j2", False),
    ("Mechanics rules", "scene/sections/mechanics_rules.j2", False),
    ("Mechanics sheets", "scene/sections/mechanics_sheets.j2", False),
    ("Off-scene cast", "scene/sections/off_scene_cast.j2", False),
    ("Mechanics response format", "scene/sections/mechanics_response_format.j2", False),
    ("Response format", "scene/sections/response_format.j2", False),
    ("Response budget", "scene/sections/response_budget.j2", False),
]


def context_sections(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    out = []
    for label, template, pcless_only in _SECTIONS:
        if pcless_only and not a["data"]["pcless"]:
            continue
        text = macros.expand_macros(prompts.render(template, **a["data"]), a["subs"], cid, sid).strip()
        if text:
            out.append({"label": label, "text": text})
    hist = "\n\n".join(m["content"] for m in a["history"])
    if hist:
        out.append({"label": "Conversation history", "text": hist})
    if a["post_history"]:
        out.append({"label": "Post-history instructions", "text": a["post_history"]})
    return out
