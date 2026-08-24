"""Assembly: the single gathering pass and the entry points that render it.

`_assemble` collects every section's data once; `build_messages`,
`build_director_messages`, `build_opener_messages` and `context_sections` all
run off that one dict; its keys are keyword arguments to the templates, so
their order is immaterial.

`SECTIONS` is the prompt's section catalog — the one list, not a mirror of one.
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
from .. import (
    characters,
    commitments,
    config,
    entities,
    length_drift,
    lengths,
    locks,
    overlay,
    pcs,
    pins,
    plot,
    response_presets,
    styles,
    tokens,
    turnstate,
    voice_anchors,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..scenes import read as scenes_read
from ..scenes import turns as scenes_turns

# Module objects, not names: `_assemble` binds a local `cast` (hence the alias),
# and `cast._drift_roster` has to stay patchable from the test that counts it.
from . import archive, art, layout, macros, mechanics, pack, speaker, story, world_state
from . import cast as cast_data

OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes


def compose_opener(cid: str, sid: str, prompt: str,
                   describe: bool = True) -> tuple[list[dict], dict | None]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result.

    Returns the messages and the breakdown describing them — see `compose_turn`
    for why those two must come out of one pass."""
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
    extra = (("Opener prompt", user_text), ("Opener shape rules", shape))
    return messages, _breakdown(a, p, extra) if describe else None


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """`compose_opener` without the breakdown — see there."""
    return compose_opener(cid, sid, prompt, describe=False)[0]


def _assemble(cid: str, sid: str, wi_seed: str = "", full_recap: int = 0,
              turn: dict | None = None) -> dict:
    """One pass gathering the template data + projected history + post-history.
    build_* render templates/scene/system.j2 from data; context_sections renders
    the per-section templates for the token breakdown. `wi_seed` folds extra text
    (the opener prompt) into the world-info activation window; `full_recap` (> 0)
    selects the full story-so-far variant over the compact recap. `turn` is a
    one-shot, unpersisted override (e.g. a per-turn response-length chip) that
    outranks every stored scope in response_presets.resolve -- see build_messages."""
    # The transcript and the transient-state ledger, read together (#120).
    # `_persist_reply` appends the reply and files its tracker entry under one
    # hold of this lock precisely so the two are never seen apart; a reader that
    # took neither could land between them and send the new narration paired
    # with the PREVIOUS turn's mood, or with none. Two file reads long, and the
    # lock is reentrant, so a caller already holding it pays nothing.
    #
    # BEST-EFFORT, not `campaign_lock`: `post_chat` appends the player's post
    # before calling this and only wires the undo that would take it back off
    # afterwards, so a `StoreBusy` raised here would strand that post with no
    # reply and nothing able to remove it. This path had no lock at all before
    # the pairing, and it must not become a new way for a turn to fail — under
    # contention it reads unlocked and one prompt may carry a stale field.
    with locks.best_effort_campaign_lock(cid):
        scene = scenes_read.read_scene(cid, sid)
        live_turnstate = turnstate.current(cid, sid, len(scene["messages"]),
                                           config.turnstate_depth())
    history = [dict(m) for m in scene["messages"]]
    croot = campaigns_paths.campaign_root(cid)          # campaign-local: dossiers, calendar, group state
    aroot = appearances_paths.locked_actor_root(cid)    # cast/roster actors are locked, so campaign-side
    # The reader's own pins and excludes (#129), resolved once for this scene at
    # this transcript length -- a TTL is counted in posts, so the same rule set
    # answers differently as the scene grows.
    rules = pins.active(cid, sid, len(history))
    pinned_refs, excluded_refs = rules["pinned"], rules["excluded"]
    # An excluded actor is removed HERE, before anything reads the cast, so
    # every consequence of being on stage goes with them: their card, their
    # state, their voice notes, their sheet -- and their ref in `present`, which
    # is what keeps an excluded character's owned lore from standing in a prompt
    # they are no longer in. The appearance record is untouched: an exclude is a
    # context rule, not a departure, and lifting it puts them straight back.
    cast = [a for a in appearances_cast.scene_cast(cid, sid)
            if f"{a['kind']}:{a['id']}" not in excluded_refs]

    npc_cards: list[dict] = []
    npc_ids: list[str] = []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            npc_cards.append(characters.read_card(aroot, a["id"], vid)["data"])
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        # Appended only after the read SUCCEEDS, so the two lists stay aligned
        # for the `zip` below -- an unreadable card drops out of both.
        npc_ids.append(a["id"])

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

    # ONE resolved structure for every section that names a character:
    # `character_descriptions`, `voice_policy`, `voice_anchors`,
    # `voice_examples`. Built here rather than per-template because
    # disambiguation and nameless handling must not be able to diverge between
    # them, and because a template resolving an anchor would be doing store IO
    # from a render.
    #
    # NOTHING is filtered out. Filtering is each template's business, per
    # block: `named_npc_count` is what the voice policy renders on, and
    # dropping the anchorless here would switch that policy off in exactly the
    # case it exists for -- a cast with no anchors yet.
    # A card is hand-editable and importable, so every one of these fields can
    # arrive as a non-string -- `test_a_malformed_card_name_costs_only_its_own_actor`
    # hand-edits `data.name` to a LIST. `_str` keeps a malformed field costing
    # only its own block rather than raising out of the whole assembly, which is
    # the same per-actor failure policy `_character_states` already applies.
    def _str(card: dict, key: str) -> str:
        v = card.get(key)
        return v.strip() if isinstance(v, str) else ""

    raw_names = [_str(d, "name") for d in npc_cards]
    cast_blocks = []
    for shown, card, char_id in zip(cast_data.display_names(raw_names), npc_cards, npc_ids):
        parts = [_str(card, "description"), _str(card, "personality"),
                 _str(card, "scenario")]
        anchor = overlay.voice_anchor_record(cid, char_id)["text"]
        cast_blocks.append({
            "name": shown,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(anchor),
            "example": voice_anchors.truncate(_str(card, "mes_example"),
                                              voice_anchors.VOICE_EXAMPLE_CAP),
        })
    # A COUNT, not a length: reading `len(cast_blocks)` at render time would let
    # a per-block filter move the policy's render condition.
    named_npc_count = sum(1 for b in cast_blocks if b["name"])

    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    pcless = scene["meta"].get("pcless") == "true"
    refs: list[dict] = []
    ref_names: list[str] = []
    if pcless:
        refs, ref_names = cast_data._campaign_player_refs(cid, aroot)
    # {{char}} is not resolved here (#137): baked at creation time instead, see
    # scene_substitutions.
    subs = {"{{user}}": ", ".join(player_names or ref_names)}

    depth = config.scan_depth()
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in history[-depth:]) if depth else ""
    if wi_seed:  # opener: the prompt stands in for the (absent) recent history
        recent_text = (recent_text + "\n" + wi_seed).strip()

    history_ids = scenes_read.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    # An excluded location is still where the scene IS -- the transcript says so
    # and moving is the reader's other lever -- but it stops being described:
    # neither the setting block nor world info renders it, and it unlocks
    # nothing it owns. That is the only reading of "keep this out of the prompt"
    # that the prompt can actually honour.
    loc_excluded = bool(current_loc) and f"locations:{current_loc}" in excluded_refs
    current_setting = ""
    current_setting_secret = False
    exclude: frozenset = frozenset()
    if current_loc and not loc_excluded:
        try:
            loc = overlay.read_entity(cid, "locations", current_loc)
            exclude = frozenset({current_loc})
            # The current location does NOT pass through `activate` -- it is
            # excluded from world info precisely so it can render as the
            # setting instead -- so the secrecy gate has to be applied here as
            # well, or "gm-only never enters the prompt" is false for the one
            # location the scene is actually standing in. Suppressing the block
            # leaves the model to invent a setting, which is the right way to
            # be wrong: the alternative is overriding the level the user set
            # because we decided they needed the description more.
            level = entities.normalize_secrecy(loc["meta"].get("secrecy"))
            if level != entities.GM_ONLY:
                current_setting = loc["body"].strip()
                current_setting_secret = level == entities.SECRET
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    present = {f"{a['kind']}:{a['id']}" for a in cast}
    if current_loc and not loc_excluded:
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
    activated_wi, recalled_wi = world_state._world_info(cid, recent_text, exclude,
                                                       frozenset(present),
                                                       pinned_refs, excluded_refs)
    wi_public, wi_secret = world_state.secrecy_split(activated_wi)
    recalled_public, recalled_secret = world_state.secrecy_split(recalled_wi)
    mech = mechanics._mechanics(cid, sid, cast, recent_text)
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "global_system_prompt": cfg.get("system_prompt", ""),
        "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
        "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
        "budget": {k: budget[k] for k in lengths.KNOBS},
        "npc_cards": npc_cards,
        "cast_blocks": cast_blocks,
        "named_npc_count": named_npc_count,
        # The POV filter (#116) resolves the present cast's names itself, from
        # the cast record: `npc_names`/`player_names` here are one name each and
        # the wrong one for it (see `world_state._actor_aliases`).
        "states": world_state._character_states(aroot, cid, cast, pcless),
        "transient_states": world_state._transient_states(cast, live_turnstate),
        # Derived on every pass and never stored -- see speaker.py. Off by
        # default because it adds tokens to every group turn, and `None`
        # (the toggle off, or fewer than two NPCs) renders no section at all.
        # `history`, not `sub_history`: the raw messages still carry the
        # `speaker` stamp that `_project_history` folds into the text.
        "speaker": (speaker.nominate(npc_names, history, pending=wi_seed)
                    if config.speaker_turn_taking() else None),
        "transient_tracker": config.turnstate_depth() > 0,
        "transient_fields": list(turnstate.FIELDS),
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
        "commitment_lines": commitments.render_open(cid, with_id=False),
        "today": world_state._today_data(cid, sid, croot),
        "weather": world_state._weather_data(cid, sid),
        "current_setting": current_setting,
        "current_setting_secret": current_setting_secret,
        # Split by secrecy (#49): the secret halves render under a heading that
        # tells the model to keep them out of the mouths of characters who have
        # not learned them. GM-only entries are already gone -- `activate`
        # dropped them before anything here could see them.
        "world_info_bodies": wi_public, "secret_world_info_bodies": wi_secret,
        "recalled_lore_bodies": recalled_public,
        "secret_recalled_lore_bodies": recalled_secret,
        # Ranked against the same scan window world info activates on, over a
        # pool built from what this turn already resolved -- see art.catalogue.
        # `[]` on any failure, so a store being synced under us costs the
        # section rather than the turn.
        #
        # SKIPPED ENTIRELY when the reader has switched the section off. Every
        # other key here is a cheap read that the packer may then drop; this one
        # walks the cast's asset sidecars and, with an embeddings endpoint
        # configured, makes a blocking HTTP call -- so computing it for a
        # section that will not render is the one case where "assemble
        # everything, render what survives" costs real money. It also makes the
        # prompt-layout toggle mean what this feature's design says it means:
        # the off switch, not a way to hide output you are still paying for.
        "available_art": (art.catalogue(cid, cast, current_loc if not loc_excluded else None,
                                        activated_wi + recalled_wi, recent_text)
                          if _section_on("available_art") else []),
        # Keyword activations only. A recalled group deliberately does NOT pull
        # its campaign state: that state renders into the `Group state` section,
        # which is `spotlight`, so feeding it from recall would grow a section
        # the packer drops whole and largest-first -- and dropping it would take
        # the states of KEYWORD-activated groups with it. That is the same way
        # sharing the World info section broke "can only add", one section over.
        # Giving recalled state its own droppable section would work too; not
        # having it at all is smaller, and costs a recalled group its state
        # block rather than costing a keyword-activated one.
        "group_states": world_state._group_states(cid, croot, activated_wi),
        "secret_group_states": world_state._group_states(cid, croot, activated_wi,
                                                         secrecy=entities.SECRET),
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

    voice_notes = cast_data._voice_notes(cid, croot, cast)
    voice_correction = (prompts.render("scene/voice_correction.j2", voice_notes=voice_notes)
                        if voice_notes else "")

    post_history = prompts.render("scene/post_history.j2", npc_cards=npc_cards,
                                  voice_correction=voice_correction,
                                  length_correction=length_correction)
    post_history = macros.expand_macros(post_history, subs, cid, sid) if post_history else ""

    sub_history = [{"role": m["role"], "content": macros.expand_macros(m["content"], subs, cid, sid)}
                   for m in story._project_history(history)]
    return {"data": data, "subs": subs, "history": sub_history,
            "post_history": post_history, "npc_names": npc_names,
            "pinned_sections": _pinned_sections(pinned_refs, cast, activated_wi,
                                                current_loc if not loc_excluded else None)}


#: Which sections a pinned cast member holds up, BY `Section.id`. Not by label:
#: from #29 the label is the reader's to edit and two sections may share one, so
#: a protection keyed on the label could hold up the wrong section or, after a
#: rename, none at all — silently, since a pin that protects nothing looks
#: exactly like a pin whose content did not activate.
#:
#: Everything else a pinned character feeds is already `lock-in` (their card,
#: their persona), so naming those here would say nothing; these two are the
#: droppable claims about that character, and a pin on someone is a request to
#: keep the model told who they currently are.
_CAST_SECTIONS = ("character_state", "transient_state")

#: What a pinned world-info entry holds up: the section its body renders into,
#: plus — for a group — the campaign state that activation pulls in beside it.
_WORLD_INFO_SECTION = "world_info"
_GROUP_STATE_SECTION = "group_state"
#: A pinned location that IS the current setting renders there, not in World
#: info (see `world_state._world_info`), so that is the section it protects.
_SETTING_SECTION = "current_setting"


def _section_on(section_id: str) -> bool:
    """Will `section_id` render at all, under the reader's prompt layout?

    Asked in `_assemble` only for data that is expensive to gather -- see the
    `available_art` key. `layout.apply` is what `_render_sections` will consult
    a moment later, so this cannot disagree with it; it costs one `read_config`
    and at most one small file read, which is what that function already
    promises per assemble pass.

    Never raises: `read_layout` answers every malformed file with "no layout",
    and a preference must not be able to take a generation down.
    """
    return any(sec.id == section_id for sec in layout.apply(SECTIONS))


def _pinned_sections(pinned_refs: frozenset, cast: list[dict], activated_wi: list[dict],
                     current_loc: str | None) -> frozenset:
    """The section ids a pin is holding up, for this assembly (#129).

    A pin is a promise about CONTENT, and the packer drops SECTIONS — so the
    promise has to be translated into the sections the pinned content actually
    landed in, once, here, where both are in hand. Two consequences worth
    stating: a pin protects the whole section it lands in, neighbours included
    (sections are dropped whole, so there is no finer unit to protect), and a
    pin on something that selected nothing this turn protects nothing, which is
    right — there is no content of the reader's in the prompt to defend.
    """
    if not pinned_refs:
        return frozenset()
    out = set()
    if any(f"{a['kind']}:{a['id']}" in pinned_refs for a in cast):
        out.update(_CAST_SECTIONS)
    for e in activated_wi:
        if f"{e['kind']}:{e['id']}" not in pinned_refs:
            continue
        out.add(_WORLD_INFO_SECTION)
        if e["kind"] == "groups":
            out.add(_GROUP_STATE_SECTION)
    if current_loc and f"locations:{current_loc}" in pinned_refs:
        out.add(_SETTING_SECTION)
    return frozenset(out)


class Section(NamedTuple):
    """One system-message section: its stable id, its inspector label, its
    template, the tier the packer drops it at, and the three selectors that
    decide whether it renders at all."""
    #: Stable identity, and the reason it is not the label: from #29 the label
    #: is the user's to edit, so two rows may legitimately carry the same
    #: string. Everything that has to name a section across a store write --
    #: `layout.py`'s entries, `/api/prompt-layout`, the inspector's React keys
    #: -- names this instead. It never reaches the model.
    id: str
    label: str
    template: str
    tier: str
    pcless_only: bool = False
    opener_only: bool = False
    #: Rendered on every turn EXCEPT the opener. Only the tracker instruction
    #: (#120) wants this: the opener is streamed unpersisted into a box the user
    #: reads and adopts by hand, so a machine-readable block there is something
    #: they have to delete themselves — and there is no reply after it to strip
    #: it from.
    except_opener: bool = False


#: The section CATALOG: every section this build knows, with the order and the
#: labels it ships. `_render_sections` walks `layout.apply(SECTIONS)` -- the
#: catalog when the user has no layout or has switched theirs off, their merge
#: of it otherwise -- and system.j2 joins the result. This list, not the
#: template, is where the default order lives.
SECTIONS = [
    Section("opener_instruction", "Opener instruction", "scene/opener_instruction",
            pack.LOCK_IN, opener_only=True),
    Section("global_system_prompt", "Global system prompt",
            "scene/sections/global_system_prompt.j2", pack.LOCK_IN),
    Section("prose_style", "Prose style", "scene/sections/prose_style.j2", pack.LOCK_IN),
    Section("natural_prose", "Natural prose", "scene/sections/natural_prose.j2", pack.LOCK_IN),
    Section("card_system_prompts", "System prompt",
            "scene/sections/card_system_prompts.j2", pack.LOCK_IN),
    Section("character_descriptions", "Character descriptions",
            "scene/sections/character_descriptions.j2", pack.LOCK_IN),
    # Three sections rather than one, because they are three kinds of thing and
    # pack.py's tiers already name the difference. The policy is fixed-length
    # instruction text -- LOCK_IN's own description, "the instructions that
    # define the reply itself" -- and is bounded by construction. The other two
    # are per-character information, which is SPOTLIGHT's description and is
    # cast-sized, so exactly what must not be pinned.
    #
    # Anchors are NOT guaranteed to outlive examples. `pack` drops the largest
    # ACTUAL section within a tier, not the one with the larger per-item cap,
    # and a pinned section never drops at all. Examples are usually the larger
    # and so usually go first; that is a tendency, and nothing may depend on it.
    Section("voice_policy", "Voice · the rule",
            "scene/sections/voice_policy.j2", pack.LOCK_IN),
    Section("voice_anchors", "Voice · how they sound",
            "scene/sections/voice_anchors.j2", pack.SPOTLIGHT),
    Section("voice_examples", "Voice · example dialogue",
            "scene/sections/voice_examples.j2", pack.SPOTLIGHT),
    Section("character_state", "Character state",
            "scene/sections/character_state.j2", pack.SPOTLIGHT),
    # Beside the standing state and at the same tier: the same kind of claim
    # about the same characters, with a shorter half-life.
    Section("transient_state", "Transient state",
            "scene/sections/transient_state.j2", pack.SPOTLIGHT),
    # Beside the state sections and at their tier, because it is the same kind
    # of claim: who is live right now. AFTER them, so the model reads what each
    # character is feeling before it reads which of them should carry the turn.
    Section("active_speaker", "Active speaker",
            "scene/sections/active_speaker.j2", pack.SPOTLIGHT),
    Section("relationships", "Relationships", "scene/sections/relationships.j2", pack.SPOTLIGHT),
    Section("player_personas", "Player personas",
            "scene/sections/player_personas.j2", pack.LOCK_IN),
    Section("offscreen_scene", "Offscreen scene", "scene/sections/offscreen_scene.j2",
            pack.LOCK_IN, pcless_only=True),
    Section("absent_players", "Absent player characters", "scene/sections/absent_players.j2",
            pack.LOCK_IN, pcless_only=True),
    Section("story_so_far", "Story so far", "scene/sections/story_so_far", pack.BACKGROUND),
    Section("archive", "Earlier scenes", "scene/sections/archive.j2", pack.ARCHIVE),
    Section("plot_threads", "Plot threads", "scene/sections/plot_threads.j2", pack.SPOTLIGHT),
    Section("commitments", "Commitments", "scene/sections/commitments.j2", pack.SPOTLIGHT),
    Section("today", "Today", "scene/sections/today.j2", pack.SPOTLIGHT),
    Section("weather", "Weather", "scene/sections/weather.j2", pack.SPOTLIGHT),
    Section("current_setting", "Current setting",
            "scene/sections/current_setting.j2", pack.SPOTLIGHT),
    Section("world_info", "World info", "scene/sections/world_info.j2", pack.SPOTLIGHT),
    # ARCHIVE, not SPOTLIGHT, and not folded into World info above: recalled
    # lore is retrieved *because* the conversation touched on it, exactly like
    # "Earlier scenes", and it is the first thing that should go when the
    # prompt does not fit. Sharing a section with the keyword hits would let a
    # recall drop them too.
    Section("recalled_lore", "Recalled lore", "scene/sections/recalled_lore.j2", pack.RECALLED),
    # RECALLED, beside recalled lore and for the same reason: this is content
    # retrieved *because* the conversation touched on it, so it is the first
    # thing that should give way when the prompt does not fit. It is also the
    # section a reader most plausibly wants gone entirely -- and `layout.py`
    # already makes any section switchable by id, which is this feature's whole
    # off switch rather than a second setting of its own.
    Section("available_art", "Available art", "scene/sections/available_art.j2", pack.RECALLED),
    Section("group_state", "Group state", "scene/sections/group_state.j2", pack.SPOTLIGHT),
    Section("mechanics_rules", "Mechanics rules",
            "scene/sections/mechanics_rules.j2", pack.SPOTLIGHT),
    Section("mechanics_sheets", "Mechanics sheets",
            "scene/sections/mechanics_sheets.j2", pack.SPOTLIGHT),
    # The off-scene cast directory is TWO sections, not one, so the token
    # breakdown can price its tiers apart (#2): tier 3 is the unbounded one and
    # folding it in with tier 2 hid exactly the number that decides whether it
    # needs bounding. Adjacent and in this order, because the shared heading
    # rides on whichever renders first and system.j2 joins them back into the
    # single block they were split out of — see off_scene_cast_active.j2.
    #
    # Same tier, so under budget pressure the packer may drop one and keep the
    # other -- a directory that used to be taken or left whole is now divisible.
    # Both halves of that are pinned by tests; the second one is the one to know
    # about:
    #
    # - dropping tier 3 is the harmless case. The heading rides on tier 2, so
    #   what survives is a framed directory that simply names fewer people.
    # - dropping tier 2 leaves tier 3's list under a bare "## Known to exist"
    #   with the directory's "introduce them only if the story calls for it"
    #   gone. And this is the LIKELIER case in exactly the campaigns that feel
    #   budget pressure: `offscene_known_limit` bounds tier 3 to one line each
    #   while tier 2 is unbounded dossier paragraphs, one per roster NPC, so a
    #   mature campaign's tier 2 is the larger half and largest goes first.
    #
    # Accepted rather than solved, and the options were weighed: dropping the
    # two together needs a section-grouping notion `pack` does not have, and
    # giving tier 3 its own lower tier means adding one to `DROP_ORDER` --
    # defensible (a directory of absent characters is worth less than the
    # recap) but a change to the packing model this issue did not ask for.
    # Revisit if the unframed list turns out to cost anything in practice.
    Section("off_scene_cast_active", "Off-scene cast · active elsewhere",
            "scene/sections/off_scene_cast_active.j2", pack.BACKGROUND),
    Section("off_scene_cast_known", "Off-scene cast · known to exist",
            "scene/sections/off_scene_cast_known.j2", pack.BACKGROUND),
    Section("mechanics_response_format", "Mechanics response format",
            "scene/sections/mechanics_response_format.j2", pack.LOCK_IN),
    Section("response_format", "Response format",
            "scene/sections/response_format.j2", pack.LOCK_IN),
    Section("transient_tracker", "Transient state tracker",
            "scene/sections/transient_tracker.j2", pack.LOCK_IN, except_opener=True),
    Section("response_budget", "Response budget",
            "scene/sections/response_budget.j2", pack.LOCK_IN),
]

#: The two sections that pick a variant file from the assembled data. Everything
#: else in `SECTIONS` names its template outright.
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

    `layout.apply` is what makes the order the READER's (#29) — the catalog
    while they have no layout or have switched theirs off, their merge of it
    otherwise. It sits here rather than beside the catalog for the same reason
    the catalog is walked here at all: this is the one render, so a section the
    layout dropped is dropped from the inspector too, and the two cannot
    disagree about what went out.

    `pinned` rides on the section rather than changing its `tier`: the tier says
    what KIND of content it is, which a reader's pin does not alter, and the
    inspector shows both. It is matched on the section's `id`, never its label,
    for the reason `Section.id` exists at all — from #29 the label is the
    reader's to edit and two sections may legitimately share one, so a pin
    keyed on the label could hold up the wrong section or, after a rename,
    none. `.get` on the key so a hand-built `a` (several tests) is still
    renderable.
    """
    data = {**a["data"], "opener": opener}
    pinned = a.get("pinned_sections") or frozenset()
    out = []
    for section in layout.apply(SECTIONS):
        if section.pcless_only and not data["pcless"]:
            continue
        if section.opener_only and not opener:
            continue
        if section.except_opener and opener:
            continue
        text = macros.expand_macros(prompts.render(_section_template(section, data), **data),
                                    a["subs"], cid, sid).strip()
        if text:
            out.append({"id": section.id, "label": section.label,
                        "text": text, "tier": section.tier,
                        "pinned": section.id in pinned})
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
    #
    # `budget` travels with the result. `_breakdown` used to re-read it from
    # config.md, so saving a new ceiling mid-compose made the breakdown report a
    # budget the packing pass never applied -- harmless drift in the live panel,
    # but a frozen snapshot would keep claiming it forever.
    return {**pack.pack(_render_sections(a, cid, sid, opener=opener),
                        [] if opener else a["history"], reserved, budget,
                        compose=_compose_system),
            "budget": budget}


def _compose_system(texts: list[str]) -> str:
    """The system message as it will be sent, from section texts."""
    return prompts.render("scene/system.j2", sections=texts).strip()


def _system_text(packed_sections: list[dict]) -> str:
    """Join the sections that survived packing into the system message."""
    return _compose_system([s["text"] for s in packed_sections if not s["dropped"]])


#: One message a caller wants appended after the system message, as
#: ``(inspector label, role, content)``. See `compose_turn`.
Appended = tuple[str, str, str]


def compose_turn(cid: str, sid: str, turn: dict | None = None,
                 appended: tuple[Appended, ...] = (),
                 describe: bool = True) -> tuple[list[dict], dict | None]:
    """One turn's messages, and the breakdown describing them.

    `describe=False` returns `None` for the breakdown and skips building it.
    That is not a micro-optimisation: on the DEFAULT unbounded budget `pack`
    skips tokenising entirely and deliberately says so, while `_breakdown`
    counts every section, every history message and the composed system prompt.
    Building one for a caller that will throw it away -- `build_messages`, or
    any route while `prompt_log_depth` is 0 -- would put a full tokenizer pass
    on every turn of a feature the user has turned off.

    BOTH out of a single `_assemble` + `_packed` pass, which is what lets a
    snapshot of this turn be trusted later (#157). Running the two entry points
    separately would reintroduce exactly the disagreement `SECTIONS` was
    restructured to remove — and worse across time than within a request, since
    `macros.expand_macros` resolves `{{random}}` and `{{roll}}` at render time,
    so a second pass over an *identical* store still produces different text.

    `turn` is a one-shot, unpersisted response-preset override (a pending
    per-turn length chip) that beats the scene/campaign/global cascade for this
    call only -- see response_presets.resolve. Callers that need it to survive a
    failed generation (retry, regenerate) must re-pass it themselves; nothing
    here remembers it.

    `appended` is every message the caller wants AFTER the system one — the
    regenerate-guidance block, a roll-result block, the declined-roll block.
    Naming it here rather than appending it afterwards is deliberate: it has
    three consequences that must agree (the packer must reserve its tokens or
    the request silently overspends the budget, the message must actually be
    sent, and the record must report it), and three call sites used to spell
    the first two out separately with nothing holding them together.
    """
    a = _assemble(cid, sid, turn=turn)
    p = _packed(a, cid, sid, reserve=tuple(c for _label, _role, c in appended))
    messages: list[dict] = []
    system_text = _system_text(p["sections"])
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += p["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    messages += [{"role": role, "content": content} for _label, role, content in appended]
    if not describe:
        return messages, None
    return messages, _breakdown(a, p, [(label, c) for label, _role, c in appended])


def build_messages(cid: str, sid: str, turn: dict | None = None,
                   appended: tuple[Appended, ...] = ()) -> list[dict]:
    """`compose_turn` without the breakdown — see there.

    The old `reserve=` parameter is gone. It charged the budget for a message
    the caller then appended itself, which left the two halves free to drift
    and gave `compose_turn` no way to report the appended block; `appended`
    does all three jobs at once.
    """
    return compose_turn(cid, sid, turn=turn, appended=appended, describe=False)[0]


def compose_director_turn(cid: str, sid: str, note: str, turn: dict | None = None,
                          describe: bool = True) -> tuple[list[dict], dict | None]:
    """One director turn: full system + history, then the note as the final user
    message. The note rides only this call — never persisted. `turn` is the same
    one-shot response-preset override as `compose_turn`, and the messages and
    breakdown come out of one pass for the same reason.

    Not offscreen-only, and never was: an empty send takes this path in an
    ordinary scene too, and since #83 so does a note typed in the composer's
    Direct mode. Nothing here reads `pcless` — the "Offscreen scene" section
    comes from `_assemble`, off the scene's own flag — so an ordinary scene gets
    its ordinary prompt with the note appended, and that is the whole
    difference. What it does NOT do is tell the model the final user turn is
    direction rather than the player speaking; in a pcless scene the offscreen
    section says so, and in an ordinary one the note reads as the player's own
    contribution, minus the persisting. That is the feature as offered ("steers
    the reply · never posted"), not an oversight — framing it differently is a
    prompt change, and prompt changes are answered by evals, not here."""
    # The note is this turn's actual input, and it is never persisted -- so it
    # seeds retrieval the same way the opener's prompt does, or naming an old
    # scene in a director note could not recall it (nothing else in the scan
    # window has said the word yet).
    a = _assemble(cid, sid, wi_seed=note, turn=turn)
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
    return messages, _breakdown(a, p, [("Director note", note_text)]) if describe else None


def build_director_messages(cid: str, sid: str, note: str, turn: dict | None = None) -> list[dict]:
    """`compose_director_turn` without the breakdown — see there."""
    return compose_director_turn(cid, sid, note, turn=turn, describe=False)[0]


def _breakdown(a: dict, p: dict, extra: list[tuple[str, str]] | None = None) -> dict:
    """The inspector's view of a turn, from an assemble/pack pair the caller
    already has: every section the packer produced, in prompt order, each with
    its `tier`, its `tokens`, and whether the packer `dropped` it — then the
    (possibly trimmed) history, the post-history block, and any `extra`
    messages the caller appends after the system one, plus the totals.

    Takes the pair rather than `(cid, sid)` so the caller that is about to SEND
    these messages can describe exactly them (#157). Recomposing from the store
    would describe a different prompt: `_assemble` re-expands `{{random}}` and
    `{{roll}}` on every pass.

    Dropped sections stay in the list with their text: the inspector's job is to
    show what was cut, and a drop the user cannot see is the silent truncation
    this replaced.

    The rows are an INVENTORY, grouped sections -> history -> post-history ->
    appended, and deliberately not a transcript of the wire order: a director
    note and the opener's prompt are both sent above post_history and reported
    below it. Every row is something that went out and its tokens are counted
    once; which message index it occupied is not a question this panel answers.

    `total_tokens` is what the request actually costs, measured the way the
    packer measures it — the COMPOSED system message, plus each history entry
    counted as the separate message it is sent as, plus the `extra` messages
    `_packed` reserved. It is deliberately not the sum of the section rows:
    those are a per-row breakdown, and token counts do not add up across
    strings that get joined (the blank lines between sections are real tokens,
    and the tiktoken-less heuristic rounds each string on its own). Summing the
    rows instead would let the inspector report a total that disagrees with the
    request it is describing.
    """
    extra = extra or []
    rows = [{"id": s["id"], "label": s["label"], "text": s["text"], "tier": s["tier"],
             "dropped": s["dropped"], "trimmed": 0, "pinned": bool(s.get("pinned")),
             "tokens": tokens.count_tokens(s["text"])}
            for s in p["sections"]]

    hist_tokens = sum(pack.message_cost(m["content"]) for m in p["history"])
    hist = "\n\n".join(m["content"] for m in p["history"])
    if hist:
        # Displayed joined (one readable block), accounted per message with the
        # same per-message framing allowance the packer charges.
        rows.append({"id": "history", "label": "Conversation history", "text": hist,
                     "tier": pack.HISTORY, "dropped": False, "pinned": False,
                     "trimmed": p["history_trimmed"], "tokens": hist_tokens})
    if a["post_history"]:
        rows.append({"id": "post_history", "label": "Post-history instructions",
                     "text": a["post_history"], "pinned": False,
                     "tier": pack.LOCK_IN, "dropped": False, "trimmed": 0,
                     "tokens": tokens.count_tokens(a["post_history"])})
    # `lock-in`, and not merely as a label: `_packed` reserved these, so the
    # packer could not drop them even had it wanted to. Reporting them under any
    # droppable tier would describe a choice the packer never had.
    #: Appended rows are numbered rather than named after their label: two of
    #: them can carry the same one (an opener sends a prompt and shape rules
    #: every time), and the inspector keys its rows on `id`.
    extra_tokens = [tokens.count_tokens(text) for _label, text in extra]
    rows += [{"id": f"appended_{n}", "label": label, "text": text, "tier": pack.LOCK_IN,
              "dropped": False, "trimmed": 0, "pinned": False, "tokens": count}
             for n, ((label, text), count) in enumerate(zip(extra, extra_tokens))]

    kept = [s["text"] for s in p["sections"] if not s["dropped"]]
    total = (tokens.count_tokens(_compose_system(kept)) + hist_tokens
             + tokens.count_tokens(a["post_history"]) + sum(extra_tokens))
    # Trimmed history messages are gone from `rows` entirely -- they are not a
    # section that can be shown struck through -- so their cost has to be added
    # here, or a pack that fit by trimming history alone reports nothing
    # dropped and the inspector stays silent about the cut it just made.
    return {"sections": rows, "total_tokens": total,
            "dropped_tokens": (sum(r["tokens"] for r in rows if r["dropped"])
                               + p["history_trimmed_tokens"]),
            "budget_tokens": p["budget"]}


def context_breakdown(cid: str, sid: str) -> dict:
    """The LIVE inspector view: compose the turn as it would be sent right now
    and describe it. Runs the same render and pack `compose_turn` runs.

    The live view has no appended blocks — those belong to a specific turn
    (regenerate guidance, a roll result), and this composes a hypothetical one.
    """
    a = _assemble(cid, sid)
    return _breakdown(a, _packed(a, cid, sid))


def context_sections(cid: str, sid: str) -> list[dict]:
    """Just the rows of `context_breakdown` — see there."""
    return context_breakdown(cid, sid)["sections"]
