"""Assembly: the single gathering pass and the entry points that render it.

`_assemble` collects every section's data once; `build_messages`,
`build_director_messages`, `build_opener_messages` and `context_sections` all
run off that one dict; its keys are keyword arguments to the templates, so
their order is immaterial.

`_prepare` freezes the render once, then packs each available model profile;
the prompt record and outgoing messages are built from that same packed variant.

`SECTIONS` is the prompt's section catalog — the one list, not a mirror of one.
It used to be a mirror: templates/scene/system.j2 re-`include`d each section
itself, so the prompt and the token breakdown were two render paths over the
same data and only the breakdown was allowed to be approximate. That is fine
while every section is all-or-nothing and becomes a lie the moment a packer
drops one, so `_render_sections` now renders the list and system.j2 only joins
what it is handed.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import NamedTuple

from ... import content_parts, model_guidance, prompts
from .. import (
    authors_notes,
    birthdays,
    characters,
    config,
    entities,
    length_drift,
    locks,
    overlay,
    pcs,
    pins,
    response_presets,
    response_targets,
    styles,
    tokens,
    voice_anchors,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..continuity import effective
from ..regex import view as regex_view
from ..scenes import identity as scenes_identity
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from ..tracker import fields as tracker_fields
from ..tracker import settings as tracker_settings
from ..tracker import view as tracker_view
from ..tracker import walk as tracker_walk
from ..worlds import read as worlds_read

# Module objects, not names: `_assemble` binds a local `cast` (hence the alias),
# and `cast._drift_roster` has to stay patchable from the test that counts it.
from . import (
    activation,
    actor,
    archive,
    art,
    authors_note,
    layout,
    macros,
    mechanics,
    pack,
    speaker,
    story,
    world_state,
)
from . import cast as cast_data

OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes

log = logging.getLogger(__name__)


def compose_opener(cid: str, sid: str, prompt: str,
                   describe: bool = True, model: str = "",
                   actor_ref: str = "grimoire", prior: list[dict] | None = None,
                   adapt: bool = False) -> tuple[list[dict], dict | None]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result.

    `adapt` makes the prompt a greeting's body to rewrite for the campaign's
    current state rather than a premise to write from (#91): only the opener
    instruction's variant and the breakdown's label for the prompt change, so
    the adaptation sees exactly the context a from-scratch opener would.

    Returns the messages and the breakdown describing them — see `compose_turn`
    for why those two must come out of one pass."""
    a = _assemble(cid, sid, wi_seed=prompt, full_recap=OPENER_RECAP_DEPTH,
                  actor_ref=actor_ref, opening_narrator=actor_ref == "grimoire",
                  opener=True)
    a["data"]["opener_adapt"] = adapt
    # Both trailing messages are rendered before packing so their tokens can be
    # reserved: neither is droppable, so neither may go uncounted.
    user_text = macros.expand_macros(prompt, macros.scene_substitutions(cid, sid), cid, sid,
                                     datetime_subs=a["datetime_subs"])
    prior_text = "\n\n".join(f"{part['speaker']}: {part['content']}" for part in (prior or []))
    # The opener is written one speaker per call, and the adapt instruction is
    # the same system text for all of them -- so what each call keeps of the
    # greeting has to be said here: the narrator its setting and situation,
    # each NPC its own part of it.
    if actor_ref == "grimoire":
        instruction = ("Set the scene as narrator, from the greeting's setting and situation. "
                       "Do not write any NPC or PC actions or dialogue." if adapt else
                       "Set the scene as narrator. Do not write any NPC or PC actions or dialogue.")
    else:
        instruction = ("Write only this assigned NPC's own part of the greeting, adapted, after the "
                       "preceding opening. Do not write for other actors." if adapt else
                       "Write only this assigned NPC's contribution after the preceding opening. "
                       "Do not write for other actors.")
    # Author's notes: an opener has no history to place them in, so the ones
    # that apply (every-turn notes only -- an opener is turn 0) follow the
    # prompt and any prior contributions, ahead of the instruction, and are
    # reserved like the rest of what is appended.
    note_rows = tuple((f"Author's note — {level}", text) for level, text in a["opener_notes"])
    extra = (("Greeting to adapt" if adapt else "Opener prompt", user_text),
             ("Previous opening contributions", prior_text),
             *note_rows,
             ("Assigned opener instruction", instruction))
    before = [{"role": "user", "content": user_text}]
    if prior_text:
        before.append({"role": "assistant", "content": prior_text})
    before += [{"role": "system", "content": text} for _level, text in a["opener_notes"]]
    return _prepare(a, cid, sid, model=model, describe=describe, opener=True,
                    before_post=tuple(before),
                    after_post=({"role": "system", "content": instruction},), extra=extra)



def build_opener_messages(cid: str, sid: str, prompt: str, model: str = "") -> list[dict]:
    """`compose_opener` without the breakdown — see there."""
    return compose_opener(cid, sid, prompt, describe=False, model=model)[0]


def _assemble(cid: str, sid: str, wi_seed: str = "", full_recap: int = 0,
              turn: dict | None = None, actor_ref: str | None = None,
              eligible_speakers: list[dict] | None = None,
              opening_narrator: bool = False, opener: bool = False,
              images: int = 0) -> dict:
    """One pass gathering the template data + projected history + post-history.
    build_* render templates/scene/system.j2 from data; context_sections renders
    the per-section templates for the token breakdown. `wi_seed` folds extra text
    (the opener prompt) into the world-info activation window; `full_recap` (> 0)
    selects the full story-so-far variant over the compact recap. `turn` is a
    one-shot, unpersisted override (e.g. a per-turn response-length chip) that
    outranks every stored scope in response_presets.resolve -- see build_messages.
    `opener` skips the tracker read: its section is `except_opener`, and the
    opener is composed inside an async generator on the event loop, where the
    read's lock waits would stall every other request."""
    # BEST-EFFORT, not `campaign_lock`: `post_chat` appends the player's post
    # before calling this and only wires the undo that would take it back off
    # afterwards, so a `StoreBusy` raised here would strand that post with no
    # reply and nothing able to remove it. Under contention it reads unlocked.
    with locks.best_effort_campaign_lock(cid):
        scene = scenes_read.read_scene(cid, sid)
        # The tracked state as of the transcript's tail, read beside the scene
        # so the two describe one moment. A reroll never comes through here:
        # it replays the prompt frozen for the response it replaces, which
        # already holds the state that stood before that post.
        tracker = _NO_TRACKER if opener else _tracker_read(cid, sid)
    # The prompt view (`store/regex`): copies, with every prompt-phase rule
    # applied, so the history, the world-info scan and the birthday check below
    # all read the text the model will be shown. No rules, plain copies.
    history = regex_view.view(scene["messages"], cid=cid, phase="prompt")
    # Kept whole for world-info activation, which numbers posts by their place
    # in the transcript even on an NPC call (see `posts` below).
    full_history = history
    # {{date}}/{{weekday}}/{{time}}, resolved ONCE per compose and handed to
    # every `expand_macros` call below, in `_render_sections` and in `_prepare`.
    # Resolved per call it re-read the scene file for every history message and
    # every section; resolved here it comes off the scene just read under the
    # lock, so the whole prompt names one moment even if the clock moves
    # mid-compose.
    dt_subs = macros._datetime_subs(cid, sid, scenes_read.histories(scene["meta"])["times"])
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
    # BEFORE the cast is narrowed to an assigned NPC, because what that NPC can
    # perceive of the others is the point of the section -- and filtered for
    # it here, by `view`, rather than by blanking afterwards: a narrator reads
    # every value, an NPC its own and what it was shown or told.
    tracker_viewer = actor_ref if actor_ref not in (None, tracker_view.NARRATOR) else None
    tracker_lines = _tracker_lines(cid, sid, tracker, tracker_viewer, excluded_refs)

    roster = actor.public_roster(cast)
    # Who is in the room, kept whole when an NPC call narrows `cast` below: the
    # owner gate reads the scene, not the speaker (spec §8.1), so lore gated on
    # another character present activates on this call too -- and what this
    # call may then SEE of it is `actor.knows`'s question, asked afterwards.
    scene_cast = cast
    public_player_names = [a["name"] for a in cast if a["role"] == "player"]
    response_actor = None
    if actor_ref is not None:
        if actor_ref == "grimoire":
            response_actor = {"ref": "grimoire", "name": "Grimoire"}
        else:
            selected = [a for a in cast if actor.ref(a) == actor_ref and a["role"] == "npc"]
            if not selected:
                raise ValueError("Assigned actor is not a present NPC")
            response_actor = {"ref": actor_ref, "name": selected[0]["name"]}
            history = actor.observed_history(cid, sid, actor_ref, history)
            cast = selected
    actor_scoped = actor_ref is not None and actor_ref != "grimoire"
    # The CONTENT inputs read only posts in context; a hidden post stays in
    # `history` because the index-based steps above (`pins.active`,
    # `actor.observed_history`) count it, and `_project_history` drops it itself.
    visible = scenes_serialize.without_excluded(history)
    # Author's notes (play controls V). The cadence counts the FULL transcript
    # -- not this actor's observed slice, and hidden posts included -- so every
    # call of one turn agrees on it; an opener is turn 0 whatever the scene
    # holds. Scene notes are keyed by identity, which follows the scene
    # through a rename. A bad notes file reads as no notes.
    note_turn = 0 if opener else authors_note.note_turn(scene["messages"])
    applied_notes, skipped_notes = authors_note.applicable(
        authors_notes.read(cid), scenes_identity.scene_identity(cid, sid),
        actor_ref if actor_scoped else None,
        response_actor["name"] if actor_scoped and response_actor else "", note_turn)

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
    player_names: list[str] = public_player_names if actor_scoped else []
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

    # Macros are expanded BEFORE the caps, not after, because the caps are
    # documented as the longest text the prompt sees and `{{user}}` is eight
    # characters that become the joined player names. Capping first left the
    # sent text free to exceed the ceiling by however much that expansion
    # added. `_render_sections` expands again on the way out; that pass finds
    # nothing left in these fields, since `expand_macros` leaves no tokens
    # behind for the ones it resolves.
    #
    # It does widen the generator/judge gap by one step: the judge reads the
    # stored anchor, so an anchor containing `{{user}}` is capped from
    # different text. That divergence is already documented in
    # `voice_anchors.py` -- substitution is on its list -- and a cap that does
    # not bound what is sent is the worse of the two.
    def _expanded(text: str) -> str:
        return macros.expand_macros(text, subs, cid, sid, datetime_subs=dt_subs) if text else text

    # ONE resolved structure for every section that names a character:
    # `character_descriptions`, `voice_policy`, `voice_anchors`,
    # `voice_examples`. Built here rather than per-template because
    # disambiguation and nameless handling must not be able to diverge between
    # them, and because a template resolving an anchor would be doing store IO
    # from a render.
    #
    # Nothing is filtered out HERE. Filtering is each template's business, per
    # block: `named_npc_count` is what the voice policy renders on, and
    # dropping the anchorless here would switch that policy off in exactly the
    # case it exists for -- a cast with no anchors yet.
    #
    # "Here" is doing work: a present NPC whose card or locked version cannot be
    # read already dropped out of `npc_cards` above, and cannot contribute a
    # block it has no data for. That is the pre-existing filter, not a new one.
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
    # strict=True is doing real work: `npc_ids` is appended only after its card
    # read succeeds, so the two lists staying aligned is an invariant rather
    # than an assumption, and a silent truncation here would attach one
    # character's anchor to another's name.
    for shown, card, char_id in zip(cast_data.voice_safe_names(raw_names, player_names), npc_cards, npc_ids,
                                    strict=True):
        parts = [_str(card, "description"), _str(card, "personality"),
                 _str(card, "scenario")]
        anchor = overlay.voice_anchor_record(cid, char_id)["text"]
        cast_blocks.append({
            "name": shown,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(_expanded(anchor)),
            "example": actor.select_examples(_expanded(_str(card, "mes_example")),
                                              voice_anchors.VOICE_EXAMPLE_CAP,
                                              "\n".join(m["content"] for m in visible[-4:])),
        })
    # A COUNT, not a length: reading `len(cast_blocks)` at render time would let
    # a per-block filter move the policy's render condition.
    named_npc_count = sum(1 for b in cast_blocks if b["name"])
    # Who actually PUT something in the voice sections, for the pin rule: a pin
    # on an actor that selected nothing protects nothing (`_pinned_sections`).
    voiced_ids = frozenset(cid_ for cid_, b in zip(npc_ids, cast_blocks, strict=True)
                           if b["name"] and (b["anchor"] or b["example"]))

    depth = config.scan_depth()
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in visible[-depth:]) if depth else ""
    if wi_seed:  # opener: the prompt stands in for the (absent) recent history
        recent_text = (recent_text + "\n" + wi_seed).strip()
    # World info reads the same messages per post rather than joined, each at
    # its TRANSCRIPT index -- the unit a timed entry counts and an inspector
    # reason cites. An NPC call reads only what that NPC observed, and
    # `observed_history` returns elements of the list it was handed, so the
    # filter is by identity and keeps each post's own index rather than
    # renumbering the observed tail from 0. A post hidden from context is not
    # read: its words must not wake an entry the prompt then carries.
    observed = {id(m) for m in visible}
    posts = [(i, m["content"]) for i, m in enumerate(full_history) if id(m) in observed]
    # Birthday names belong to the current question. World-info activation
    # deliberately scans several turns, but an earlier name must not widen a
    # direct question about somebody else.
    birthday_text = wi_seed or next((m["content"] for m in reversed(visible)
                                     if m["role"] == "user"), "")

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
            owners = [v.strip() for v in loc["meta"].get("owners", "").split(",") if v.strip()]
            visible_setting = not actor_scoped or actor.knows(
                {"secrecy": level, "owners": owners,
                 "known_by": loc["meta"].get("known_by", "")}, actor_ref)
            if level != entities.GM_ONLY and visible_setting:
                current_setting = loc["body"].strip()
                current_setting_secret = level == entities.SECRET
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    # The base present set and why each ref is in it (spec §7.3); the engine
    # grows it structurally and by activation. The whole scene's, on an NPC
    # call as on the narrator's (see `scene_cast`).
    present: dict[str, dict] = {f"{a['kind']}:{a['id']}": {"type": "cast", "via": None}
                                for a in scene_cast}
    if current_loc and not loc_excluded:
        present[f"locations:{current_loc}"] = {"type": "current_location", "via": None}

    cfg = config.read_config()
    campaign_meta = campaigns_read.read_campaign(cid)["meta"]
    # One per-field cascade resolves BOTH the prose style and the length budget
    # over turn -> scene -> campaign -> global. It subsumes the old style-only
    # resolver: with no response presets set anywhere (every pre-existing
    # install) it walks the same style_id keys in the same order, so the
    # migration is a no-op.
    budget = response_presets.resolve(turn=turn or {}, scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    targets = response_targets.resolve(turn=turn or {}, scene_meta=scene["meta"],
                                       campaign_meta=campaign_meta, config=cfg)
    phase = "opening" if opening_narrator else "continuation"
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except (styles.StyleNotFound, OSError, UnicodeDecodeError):
        # resolve() already skips ids that don't exist; this also covers a file
        # that exists but can't be read, which must not break generation either.
        resolved_style = None
    activated_wi, recalled_wi, wi_result, wi_names = world_state._world_info(
        cid, posts, wi_seed, exclude=exclude, present=present, pinned_refs=pinned_refs,
        excluded_refs=excluded_refs, scan_depth=depth,
        recursion_depth=config.lore_recursion_depth(cfg),
        current_location=current_loc if not loc_excluded else None, recall_text=recent_text,
        actor_ref=actor_ref if actor_scoped else None)
    # A no-op since `_world_info` filters its candidates for the actor; kept so
    # what reaches an NPC's prompt does not rest on that one call site alone.
    if actor_scoped:
        activated_wi = actor.known_entries(activated_wi, actor_ref)
        recalled_wi = actor.known_entries(recalled_wi, actor_ref)
    wi_public, wi_secret = world_state.secrecy_split(activated_wi)
    # The same entries again as the engine's hits, for what renders World info
    # per entry (`_render_sections`) and the inspector rows: priority, match age
    # and the reason each one is in. Matched by identity, so the actor filter
    # above is honoured here too. `names` resolves every ref a reason can name
    # -- a record listed this turn, activated or not, or someone in the room.
    wi_ids = {id(e) for e in activated_wi}
    recalled_ids = {id(e) for e in recalled_wi}
    lore = {"world_info": [h for h in wi_result.keyword if id(h.entry) in wi_ids],
            "recalled": [h for h in wi_result.recalled if id(h.entry) in recalled_ids],
            "held_back": list(wi_result.held_back),
            "names": {**wi_names, **{f"{m['kind']}:{m['id']}": str(m.get("name") or m["id"])
                                     for m in scene_cast}}}
    recalled_public, recalled_secret = world_state.secrecy_split(recalled_wi)
    mech = mechanics._mechanics(cid, sid, cast, recent_text)
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "response_actor": response_actor,
        "perception_rider": config.perception_rider(),
        "response_roster": roster,
        "response_candidates": [{"ref": e["ref"], "name": e["name"]}
                                for e in (eligible_speakers or [])],
        # A candidate marked `responded` has already spoken this round, which
        # an automatic chain offers on purpose: naming them begins the next
        # round (`routes.character_turns._compose`). The handoff text says so.
        "response_repeat": any(e.get("responded") for e in (eligible_speakers or [])),
        "global_system_prompt": cfg.get("system_prompt", ""),
        # The campaign's world as its author describes it (#38), read live
        # through the world like every record the campaign inherits. Public
        # framing, so not on the actor-scoped blanking list below.
        "world_overview": worlds_read.profile_of(campaigns_read.world_root_of(cid)),
        "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
        "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
        "budget": targets[phase],
        "npc_cards": npc_cards,
        "cast_blocks": cast_blocks,
        "named_npc_count": named_npc_count,
        # The POV filter (#116) resolves the present cast's names itself, from
        # the cast record: `npc_names`/`player_names` here are one name each and
        # the wrong one for it (see `world_state._actor_aliases`).
        "states": world_state._character_states(aroot, cid, cast, pcless),
        # Already filtered for this prompt's reader (`_tracker_lines`), so the
        # actor-scoped blanking below has nothing to take from it.
        "tracker_lines": tracker_lines,
        "tracker_narrator": tracker_viewer is None,
        # Derived on every pass and never stored -- see speaker.py. Off by
        # default because it adds tokens to every group turn, and `None`
        # (the toggle off, or fewer than two NPCs) renders no section at all.
        # `history`, not `sub_history`: the raw messages still carry the
        # `speaker` stamp that `_project_history` folds into the text.
        #
        # One NPC at most when actor-scoped, so `nominate` would answer None
        # anyway -- and the blanking below says so regardless.
        "speaker": (speaker.nominate(npc_names, visible, pending=wi_seed)
                    if not actor_scoped and config.speaker_turn_taking() else None),
        "players": players, "ref_names": ref_names, "refs": refs,
        # The recap, archive, ledgers, calendar, relationship graph, group
        # state, off-scene cast and art catalogue are campaign-wide. Birthday
        # metadata is retrieved for the current question in either voice.
        **_campaign_view(cid, sid, croot, cast, recent_text, birthday_text,
                         full_recap, activated_wi,
                         recalled_wi, current_loc if not loc_excluded else None,
                         actor_scoped=actor_scoped, excluded_refs=excluded_refs),
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
        "player_names": player_names,
        "mechanics_rules": mech["mechanics_rules"], "mechanics_sheets": mech["mechanics_sheets"],
        "mechanics_checks": mech["mechanics_checks"],
    }

    if actor_scoped:
        # These fields have no actor attribution. In particular a recap or a
        # group marked public does not make its private campaign state public.
        #
        # THE CONTRACT, and it stays even though `_campaign_view` no longer
        # gathers most of these for an actor-scoped compose: that is an
        # optimisation, and this is what an NPC's prompt is allowed to hold.
        for key in ("story_entries", "archive_entries", "plot_lines", "commitment_lines",
                    "group_states", "secret_group_states", "offscene_active", "offscene_known",
                    "players", "refs", "ref_names", "available_art"):
            data[key] = []
        data["relationship_lines"] = actor.own_relationships(cid, actor_ref, roster)
        data["speaker"] = None
        data["today"] = None  # scheduled events have no knowledge attribution
        data["mechanics_sheets"] = [s for s in data["mechanics_sheets"] if s.get("ref") == actor_ref]
        data["mechanics_checks"] = [c for c in data["mechanics_checks"] if c.get("ref") == actor_ref]

    # One assigned speaker per call makes whole-turn block limits irrelevant.
    # Measure only that speaker's recent prose.
    drift = (length_drift.measure_contributions(
        visible, response_actor["name"], targets["continuation"])
        if response_actor else None)
    length_correction = (prompts.render("scene/length_correction.j2",
                                        drift=drift,
                                        budget=targets["continuation"])
                         if drift else "")

    voice_notes = cast_data._voice_notes(cid, croot, cast)
    voice_correction = (prompts.render("scene/voice_correction.j2", voice_notes=voice_notes)
                        if voice_notes else "")

    post_history = prompts.render("scene/post_history.j2", npc_cards=npc_cards,
                                  voice_correction=voice_correction,
                                  length_correction=length_correction)
    post_history = _expanded(post_history)

    # The notes go INTO the projected history, each its own system message at
    # its depth (`authors_note.inject`); their positions travel beside it in
    # `notes`, never on the wire. An opener sends no history, so its notes ride
    # in `before_post` instead (`compose_opener`).
    in_history = [] if opener else applied_notes
    if images > 0:
        # The newest `images` pictures stay as references (#377): projected as
        # sentinels, expanded like any other text, then split into parts.
        image_table: dict[int, dict] = {}
        nonce = ""

        def project(messages: list[dict]) -> list[dict]:
            nonlocal nonce
            projected, table, nonce = story._project_history_refs(messages, images=images,
                                                                  cid=cid)
            image_table.update(table)
            return projected

        projected, positions = authors_note.inject(history, in_history, project)
        sub_history = _split_refs([{"role": m["role"], "content": _expanded(m["content"])}
                                   for m in projected], image_table, nonce)
    else:
        projected, positions = authors_note.inject(history, in_history)
        sub_history = [{"role": m["role"], "content": _expanded(m["content"])}
                       for m in projected]
    notes = positions + [{**n, "index": None, "status": "skipped"} for n in skipped_notes]
    opener_notes = ([(n["level"], _expanded(authors_note.render(n))) for n in applied_notes]
                    if opener else [])
    return {"data": data, "subs": subs, "datetime_subs": dt_subs, "history": sub_history,
            "notes": notes, "opener_notes": opener_notes,
            "post_history": post_history, "npc_names": npc_names, "wi_result": wi_result,
            "lore": lore,
            "response_settings": {"style_id": budget["style_id"], "phase": phase,
                                  **targets[phase]},
            "pinned_sections": _pinned_sections(pinned_refs, cast, activated_wi,
                                                current_loc if not loc_excluded else None, voiced_ids)}


def _parts_of(text: str, refs: dict[int, dict], sentinel) -> list:
    """`text` split on this composition's sentinels into text and reference
    parts. Removing the sentinels leaves exactly the `images=0` string, so the
    text parts concatenate to it (`content_parts`' rule)."""
    parts: list = []
    pos = 0
    for m in sentinel.finditer(text):
        if m.start() > pos:
            parts.append({"type": "text", "text": text[pos:m.start()]})
        ref = refs.get(int(m.group(1)))
        if ref is not None:
            parts.append(content_parts.ref(ref["url"], ref["alt"], ref["role"] != "user"))
        pos = m.end()
    if pos < len(text) or not parts:
        parts.append({"type": "text", "text": text[pos:]})
    return parts


def _split_refs(history: list[dict], refs: dict[int, dict], nonce: str) -> list[dict]:
    """Projected history with sentinels -> messages with image references, in
    USER messages only (#377).

    The providers that read images take them in user content alone, so a
    picture from an assistant post is CARRIED: moved to the start of the next
    user message, or -- when none follows in the window -- onto a carrier, a
    user message appended after the history that holds nothing else
    (`content_parts.CARRIER`). A message left with no reference is its plain
    string again, which is what keeps everything around it unchanged.
    """
    sentinel = re.compile("⟦img:" + re.escape(nonce) + r":(\d+)⟧")
    out: list[dict] = []
    carry: list = []
    for m in history:
        parts = _parts_of(m["content"], refs, sentinel)
        found = content_parts.image_refs(parts)
        if m["role"] == "user":
            parts = carry + parts
            carry = []
        else:
            carry += found
            parts = [p for p in parts if p not in found]
        out.append({**m, "content": content_parts.collapse(parts)})
    if carry:
        out.append({"role": "user", "content": carry, content_parts.CARRIER: True})
    return out


#: What `_tracker_read` answers when there is no state to show.
_NO_TRACKER: tuple[dict, list, dict] = ({}, [], {})


def _tracker_read(cid: str, sid: str) -> tuple[dict, list[dict], dict[str, str]]:
    """The scene's tracked state, its fields and its cast, as of the tail --
    or nothing, when the tracker is off for the campaign or cannot be read.

    Fail-soft, unlike the walk itself. `walk` raises on a malformed response
    ledger because a prune acting on a misread would discard records; a prompt
    acting on one would only lose this section, and that is the right way to be
    wrong -- a turn must not fail because the bookkeeping beside it did.
    """
    try:
        if not tracker_settings.enabled(cid):
            return _NO_TRACKER
        _key, snapshot = tracker_walk.current(cid, sid)
        if not snapshot:
            return _NO_TRACKER
        # The current cast only: the section describes who is in the scene
        # now, and a snapshot can still hold someone present who left after
        # the last tracked post.
        return (snapshot, tracker_fields.effective(cid, sid),
                tracker_walk.roster(cid, sid, departed=False))
    except Exception:
        # Logged with the traceback rather than marked `noqa`: BLE001 exempts a
        # handler that keeps the failure for whoever reads the log.
        log.warning("scene state: tracker read failed for %s/%s; composing without it",
                    cid, sid, exc_info=True)
        return _NO_TRACKER


def _tracker_lines(cid: str, sid: str, tracker: tuple[dict, list[dict], dict[str, str]],
                   viewer: str | None, excluded_refs: frozenset) -> list[dict]:
    """`view.lines_for` over `_tracker_read`'s answer, for `viewer` (an NPC
    ref, or `None` for the narrator).

    The roster is the FULL scene cast, not the actor-narrowed one, less what the
    reader excluded: an exclude takes a character's state out of the prompt
    with everything else about them being on stage. Lines with no value this
    reader may see are kept here and skipped by the template.
    """
    snapshot, fields, roster = tracker
    if not snapshot:
        return []
    try:
        shown = {ref: name for ref, name in roster.items() if ref not in excluded_refs}
        return tracker_view.lines_for(snapshot, fields, viewer, shown)
    except Exception:
        log.warning("scene state: a malformed snapshot for %s/%s; composing without it",
                    cid, sid, exc_info=True)
        return []


def _campaign_view(cid: str, sid: str, croot, cast: list[dict], recent_text: str,
                   birthday_text: str,
                   full_recap: int, activated_wi: list[dict], recalled_wi: list[dict],
                   art_loc: str | None, *, actor_scoped: bool,
                   excluded_refs: frozenset[str]) -> dict:
    """The template data only a campaign-wide voice is shown.

    Except `birthdates`, every key here is one `_assemble` blanks for an actor-scoped compose -- an
    assigned NPC's reply, which is every response of an automatic round -- so
    for one it is not gathered at all: blanks come back, and the blanking block
    blanks them again as the contract. It used to be gathered and thrown away,
    between one speaker finishing and the next starting: the off-scene
    directory walks the world's whole roster, `art.catalogue` walks asset
    sidecars and may make an embeddings call, and the rest read the chronicle,
    both ledgers, group state, the relationship graph and the calendar.

    Birthday metadata is a character field and can answer a direct question
    in either voice. It uses the latest question, not the wider activation
    window. Nothing here draws from the macro RNG or writes; the other skipped
    producers remain byte-for-byte equivalent under `test_actor_scoped_skip.py`.
    """
    if actor_scoped:
        return {"offscene_active": [], "offscene_known": [], "available_art": [],
                "story_entries": [], "archive_entries": [], "plot_lines": [],
                "commitment_lines": [], "group_states": [], "secret_group_states": [],
                "relationship_lines": [], "today": None,
                "birthdates": birthdays.relevant(cid, birthday_text, excluded_refs)}
    offscene_active, offscene_known = cast_data._cast_directory_data(croot, cid, sid)
    return {
        "offscene_active": offscene_active, "offscene_known": offscene_known,
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
        "available_art": (art.catalogue(cid, cast, art_loc, activated_wi + recalled_wi, recent_text)
                          if _section_on("available_art") else []),
        "story_entries": story._story_entries(cid, depth=full_recap or None, full=bool(full_recap)),
        # The archive excludes what the recap already shows, and (via `before`)
        # this scene and any scene after it: a scene absorbed earlier still has
        # a chronicle record, so without that bound, continuing an old scene
        # could recall the present -- or the future -- as a past event.
        "archive_entries": archive._archive_entries(
            cid, recent_text, story._recap_ids(cid, full_recap or None), before=sid),
        "plot_lines": effective.render_threads(cid, with_id=False),
        "commitment_lines": effective.render_commitments(cid, with_id=False),
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
        # The whole graph among those present. An actor-scoped compose gets its
        # own outgoing feelings instead, from the blanking block.
        "relationship_lines": story._relationship_lines(cid, cast),
        "today": world_state._today_data(cid, sid, croot),
        "birthdates": birthdays.relevant(cid, birthday_text, excluded_refs),
    }


#: Which sections a pinned cast member holds up, BY `Section.id`. Not by label:
#: from #29 the label is the reader's to edit and two sections may share one, so
#: a protection keyed on the label could hold up the wrong section or, after a
#: rename, none at all — silently, since a pin that protects nothing looks
#: exactly like a pin whose content did not activate.
#:
#: Everything else a pinned character feeds is already `lock-in` (their card,
#: their persona), so naming those here would say nothing; this one is the
#: droppable claim about that character, and a pin on someone is a request to
#: keep the model told who they currently are.
_CAST_SECTIONS = ("character_state", "tracker_state")

#: The voice sections, held up only by a pinned NPC. Per-character content like
#: the one above -- a reader who pinned a character and then watched the packer
#: drop that character's anchor would have been told their pin meant something
#: it did not -- but NPC-only, which `_CAST_SECTIONS` is not. `voice_policy` is
#: absent deliberately: it is LOCK_IN, so no pin can make it any safer.
_VOICE_CAST_SECTIONS = ("voice_anchors", "voice_examples")

#: What a pinned world-info entry holds up: the section its body renders into,
#: plus — for a group — the campaign state that activation pulls in beside it.
_WORLD_INFO_SECTION = "world_info"
_GROUP_STATE_SECTION = "group_state"
#: A pinned location that IS the current setting renders there, not in World
#: info (see `world_state._world_info`), so that is the section it protects.
_SETTING_SECTION = "current_setting"


def _section_on(section_id: str) -> bool:
    """Will `section_id` render at all, under the reader's prompt layout?

    Asked in `_campaign_view` only for data that is expensive to gather -- see
    the `available_art` key. `layout.apply` is what `_render_sections` will consult
    a moment later, so this cannot disagree with it; it costs one `read_config`
    and at most one small file read, which is what that function already
    promises per assemble pass.

    Never raises: `read_layout` answers every malformed file with "no layout",
    and a preference must not be able to take a generation down.
    """
    return any(sec.id == section_id for sec in layout.apply(SECTIONS))


def _pinned_sections(pinned_refs: frozenset, cast: list[dict], activated_wi: list[dict],
                     current_loc: str | None, voiced_ids: frozenset = frozenset()) -> frozenset:
    """The section ids a pin is holding up, for this assembly (#129).

    A pin is a promise about CONTENT, and the packer drops SECTIONS — so the
    promise has to be translated into the sections the pinned content actually
    landed in, once, here, where both are in hand. Two consequences worth
    stating: a pin protects the whole section it lands in, neighbours included
    (sections are dropped whole, so there is no finer unit to protect), and a
    pin on something that selected nothing this turn protects nothing, which is
    right — there is no content of the reader's in the prompt to defend.

    World info is the exception to the first: it sheds an entry at a time
    (spec §6), so the packer protects the pinned entry itself and still sheds
    its neighbours. The flag still matters there -- it keeps the section from
    ever being dropped whole, and the inspector shows it.
    """
    if not pinned_refs:
        return frozenset()
    out = set()
    pinned_cast = [a for a in cast if f"{a['kind']}:{a['id']}" in pinned_refs]
    if pinned_cast:
        out.update(_CAST_SECTIONS)
    # The voice sections carry NPC data ONLY, so a pinned PLAYER must not hold
    # them up. Sections are dropped whole, so pinning your own character would
    # otherwise make every NPC's anchor and examples undroppable -- and on a
    # large cast that is enough to push a budgeted prompt over on its own, for
    # a pin the reader made about themselves.
    # ...and only when that NPC actually PUT something in them. This function's
    # own rule is that a pin on something which selected nothing protects
    # nothing, and sections drop whole -- so pinning an anchorless NPC would
    # otherwise make every OTHER character's anchors and examples undroppable,
    # on the strength of content the pinned actor did not contribute.
    if any(a["role"] == "npc" and a["id"] in voiced_ids for a in pinned_cast):
        out.update(_VOICE_CAST_SECTIONS)
    for e in activated_wi:
        if f"{e['kind']}:{e['id']}" not in pinned_refs:
            continue
        out.add(_WORLD_INFO_SECTION)
        if e["kind"] == "groups":
            out.add(_GROUP_STATE_SECTION)
    if current_loc and f"locations:{current_loc}" in pinned_refs:
        out.add(_SETTING_SECTION)
    return frozenset(out)


#: The off-scene cast directory's shared heading: the line that says these
#: characters are NOT present. Both tiers name it and `_render_sections` emits
#: it once per contiguous run of them — one copy in the catalog's order, where
#: they are adjacent, and one per half a layout has separated. NOT "exactly one
#: of them emits it": collapsing it back to one per message is how the later
#: half ends up under an unrelated section's `# Heading` (#423).
#:
#: No leading underscore, and that is the convention rather than an oversight:
#: `templates/README.md` reserves `_` for macro libraries, and this file is
#: rendered as prompt text like `history_line.j2` or `post_history.j2`.
_OFF_SCENE_CAST_HEADING = "scene/off_scene_cast_heading.j2"


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
    #: Rendered on every turn EXCEPT the opener. Only the scene state wants
    #: this: the opener is streamed unpersisted into a box the user reads and
    #: adopts by hand, for a scene with no tracked posts yet -- so whatever the
    #: tracker last recorded belongs to some earlier moment, and an opener
    #: draft is one of the side calls the tracker's state is not shown to.
    except_opener: bool = False
    #: A heading this section SHARES with every other section naming the same
    #: template. `_render_sections` opens each contiguous RUN of them with it —
    #: one copy while they are adjacent, another for a half the layout moved
    #: away, because what it frames is a block and a split layout has two, and
    #: `_dedupe_runs` takes the second back off when packing closes the gap. Not
    #: a section title: every other section opens with its own `# Heading`
    #: inside its own template, and only a block that was split into several
    #: sections for the token breakdown's sake needs this.
    #:
    #: It is a property of the SECTION rather than something a template works
    #: out, because "am I first?" is a question about the merged, enabled
    #: layout and a template can only see the data (#423). The off-scene cast
    #: directory's tier 3 used to answer it by testing whether tier 2 had
    #: content, which stopped being the same question the moment `layout.py`
    #: let a reader disable or reorder either half — and the heading it lost is
    #: the line saying the cast is not present.
    heading: str = ""


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
    Section("model_guidance", "Model guidance", "scene/sections/model_guidance.j2", pack.LOCK_IN),
    Section("card_system_prompts", "System prompt",
            "scene/sections/card_system_prompts.j2", pack.LOCK_IN),
    # What the world is (#38): genre, tone, themes and the author's description,
    # off `world.md`. BACKGROUND -- it frames, and the cards and world info
    # already carry most of it piecemeal, so it gives way before anything about
    # the scene in hand. Renders nothing for a world with no profile, so a
    # campaign whose world never had one sends the prompt it always sent.
    Section("world_overview", "World overview",
            "scene/sections/world_overview.j2", pack.BACKGROUND),
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
    # What each present character looks like and is doing right now, as the
    # scene tracker recorded it, filtered for the reader (`_tracker_lines`).
    # Same tier and pin rule as the standing state above, which is the same
    # kind of claim on a shorter clock. Not in the opener: a new scene has no
    # tracked posts, and the opener is a draft the reader adopts by hand.
    Section("tracker_state", "Scene state", "scene/sections/tracker_state.j2",
            pack.SPOTLIGHT, except_opener=True),
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
    Section("birthdates", "Birthdates", "scene/sections/birthdates.j2", pack.RECALLED),
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
    # needs bounding. Adjacent and in this order by DEFAULT, so system.j2 joins
    # them back into the single block they were split out of — but only by
    # default: `layout.py` lets a reader reorder or disable either half, which
    # is why the shared heading is `Section.heading` (emitted by
    # `_render_sections` on whichever half actually rendered first) rather than
    # something the templates work out between themselves (#423).
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
    # Which half is exposed follows the LAYOUT, because the heading rides on
    # whichever renders first: reorder the two and it is tier 3 that can be
    # dropped carrying the heading. Same defect, same cost, and pinned by
    # `test_a_reordered_layout_can_still_drop_the_half_holding_the_heading`.
    # #423 moved the heading's owner from data to the render path; it did not
    # move it past the packer, which runs after.
    #
    # Accepted rather than solved, and the options were weighed: dropping the
    # two together needs a section-grouping notion `pack` does not have, and
    # giving tier 3 its own lower tier means adding one to `DROP_ORDER` --
    # defensible (a directory of absent characters is worth less than the
    # recap) but a change to the packing model this issue did not ask for.
    # Reassigning the heading to a survivor is the third option and has the
    # same shape: done after `pack.pack` it adds tokens to a total already
    # measured against the ceiling, so to be honest it has to happen inside the
    # drop loop -- which is the grouping notion again.
    # Revisit if the unframed list turns out to cost anything in practice.
    Section("off_scene_cast_active", "Off-scene cast · active elsewhere",
            "scene/sections/off_scene_cast_active.j2", pack.BACKGROUND,
            heading=_OFF_SCENE_CAST_HEADING),
    Section("off_scene_cast_known", "Off-scene cast · known to exist",
            "scene/sections/off_scene_cast_known.j2", pack.BACKGROUND,
            heading=_OFF_SCENE_CAST_HEADING),
    Section("mechanics_response_format", "Mechanics response format",
            "scene/sections/mechanics_response_format.j2", pack.LOCK_IN),
    Section("response_format", "Response format",
            "scene/sections/response_format.j2", pack.LOCK_IN),
    Section("response_budget", "Response budget",
            "scene/sections/response_budget.j2", pack.LOCK_IN),
]

#: The two sections that pick a variant file from the assembled data. Everything
#: else in `SECTIONS` names its template outright.
_VARIANTS = {
    "scene/opener_instruction": lambda d: ("adapt_" if d.get("opener_adapt") else "")
                                          + ("offscreen" if d["pcless"] else "standard"),
    "scene/sections/story_so_far": lambda d: "full" if d["story_full"] else "compact",
}


def _section_template(section: Section, data: dict) -> str:
    pick = _VARIANTS.get(section.template)
    return f"{section.template}/{pick(data)}.j2" if pick else section.template


#: The section semantic recall renders into. Its rows carry entries and reasons
#: like World info's, but it never sheds: it drops whole, in its own tier.
_RECALLED_SECTION = "recalled_lore"


def _is_secret(hit: activation.Hit) -> bool:
    """`world_state.secrecy_split`'s rule, for one hit."""
    return entities.normalize_secrecy(hit.entry.get("secrecy")) == entities.SECRET


def _template_order(hits: list[activation.Hit]) -> list[activation.Hit]:
    """Hits in the order a world-info template prints them: every public entry,
    then every secret one, each half in activation order."""
    return ([h for h in hits if not _is_secret(h)]
            + [h for h in hits if _is_secret(h)])


def _reason_refs(reason: dict) -> list[str]:
    """Every ref a reason names: the owner that opened its gate, how that owner
    is present, and the entry that pulled it in."""
    presence = reason.get("owner_presence")
    found = [reason.get("owner"), reason.get("via"),
             presence.get("via") if isinstance(presence, dict) else None]
    return [r for r in found if isinstance(r, str) and r]


def _reason_names(hits, catalog: dict[str, str]) -> dict[str, str]:
    """A display name for every ref `hits`' reasons name; a ref nothing
    resolves (a record deleted since, a hand-edited owner) names itself."""
    return {ref: catalog.get(ref, ref) for h in hits for ref in _reason_refs(h.reason)}


def _entry_name(entry: dict) -> str:
    return str(entry.get("name") or entry.get("id") or "")


def _lore_detail(hits: list[activation.Hit], catalog: dict[str, str]) -> dict:
    """What an inspector row says about the entries in its section (spec §10),
    JSON-safe and never part of a prompt: `_breakdown` copies it onto the row
    and nothing renders it."""
    entries = []
    for h in hits:
        c = activation.controls(h.entry)
        entries.append({"ref": h.ref, "name": _entry_name(h.entry), "kind": h.entry.get("kind"),
                        "secrecy": entities.normalize_secrecy(h.entry.get("secrecy")),
                        "priority": c.priority, "keep": c.keep, "level": h.level,
                        "reason": deepcopy(h.reason)})
    return {"entries": entries, "names": _reason_names(hits, catalog)}


#: Stands in for one entry body while World info's template is rendered, so
#: the fixed text around the bodies can be told apart from them. NUL never
#: appears in a template, and the slot holds no `{{`, so it is not a macro.
_BODY_SLOT = re.compile("\x00(\\d+)\x00")


#: A run of World info's fixed text, by occurrence: (text, slot before, slot after).
_RunKey = tuple[str, str | None, str | None]


def _recall_run(runs: dict[_RunKey, str], suffixes: set[str], key: _RunKey, expand) -> str:
    """The expansion a re-rendered run of World info's fixed text keeps, by
    occurrence: `key` is (text, slot before, slot after). A run whose neighbour
    was shed takes the draw of the occurrence that shares its text and the slot
    it still has -- before for a suffix, after for anything else (see
    `_world_info_section`); text never seen is expanded. Remembered either way,
    so the next re-render is not a new draw."""
    if key not in runs:
        core, before, after = key
        sides = ((1, before), (2, after)) if core in suffixes else ((2, after), (1, before))
        found = next((got for side, ref in sides if ref is not None
                      for k, got in runs.items() if k[0] == core and k[side] == ref), None)
        runs[key] = expand(core) if found is None else found
    return runs[key]


def _held_back_only(section: Section, pinned, extra: dict) -> dict | None:
    """A text-less World info row for an empty section that still held entries
    back -- the turn where cooldown suppresses the only relevant entry is the
    turn the reader most needs to see why. It puts nothing in the prompt
    (`_compose_system` joins only what has some) and, like any section that
    rendered empty, breaks no heading run."""
    if not (extra.get("lore") or {}).get("held_back"):
        return None
    return {"id": section.id, "label": section.label, "text": "", "tier": section.tier,
            "pinned": section.id in pinned, "heading": section.heading, "heading_text": "",
            **extra}


def _world_info_section(section: Section, data: dict, lore: dict, head: str,
                        expand) -> tuple[str, dict]:
    """World info's text, plus what the packer needs to shed it an entry at a
    time and what its inspector row reports.

    The template is rendered with a slot in place of each body, and the result
    is expanded piece by piece in DOCUMENT order -- a run of the template's own
    text, then the body in the next slot, then the next run -- which is the
    left-to-right order expanding the whole rendered section always drew
    `{{random}}` in. So the unbounded section is the section-level expansion,
    the template's own text (the secret heading a reader may have edited)
    included, and a body that expands to nothing still leaves the gap it
    always left. Byte for byte, that holds for `{{random}}` under a seeded
    `random`, and only that far: the whole-text expansion drew every
    `{{random}}` before any `{{roll}}` and piece by piece they interleave,
    which is harmless only because a `{{roll}}` seeds itself from the OS
    (`dice.roll`) rather than from `random` -- so no seed reproduces a roll,
    whole or piecewise. A macro a hand-edited template splits across a slot's
    edge would also expand whole and not piecewise.

    Every expansion is remembered: a body by its entry's ref, a run of fixed
    text by OCCURRENCE -- its text less its edge whitespace (removing an entry
    only changes the joins around a run) together with the slots either side of
    it. The same fixed text twice is two draws, so the text alone cannot name
    one: a template that wraps every body in `{{random}}` would hand every
    re-rendered occurrence the first one's. `render(kept)` re-renders the slots
    for the kept entries and reuses those, so shedding one entry cannot re-draw
    another's macros or the template's, nor give one occurrence another's.

    A shed entry takes its slot out, and the runs either side of it close up
    into one, so a surviving run can find a neighbour gone. Which of the two
    draws it keeps depends on whom the text belonged to: text a template puts
    AHEAD of each body belongs to the body after it, text put BEHIND each body
    to the body before. The full render says which -- the leading edge of a
    prefixed list is that text, the trailing edge of a suffixed one -- so a
    run whose text closes the full render keeps the occurrence that shares its
    slot before, and any other the one that shares its slot after. Only text
    the full render never produced -- possible only in a hand-edited template
    -- is expanded, once.

    `head` is the section's already-expanded shared heading. World info declares
    none in the catalog, so it is "" today; `render` closes over it so a heading
    added later would still lead every re-render."""
    hits = _template_order(lore["world_info"])
    # Only a non-empty body takes a slot, as the template's `select` keeps only
    # those -- a raw-empty body never rendered anything.
    slotted = [i for i, h in enumerate(hits) if h.entry.get("body")]
    template = _section_template(section, data)
    bodies: dict[str, str] = {}
    runs: dict[_RunKey, str] = {}

    def skeleton(kept: frozenset) -> list[str]:
        shown = [i for i in slotted if hits[i].ref in kept]
        rendered = prompts.render(template, **{
            **data,
            "world_info_bodies": [f"\x00{i}\x00" for i in shown if not _is_secret(hits[i])],
            "secret_world_info_bodies": [f"\x00{i}\x00" for i in shown if _is_secret(hits[i])],
        }).strip()
        return _BODY_SLOT.split(rendered)

    def finish(body: str) -> str:
        body = body.strip()
        return head + "\n\n" + body if (head and body) else body

    def neighbours(pieces: list[str], n: int) -> tuple[str | None, str | None]:
        """The refs of the slots either side of run `n` (None at an edge)."""
        before = hits[int(pieces[n - 1])].ref if n else None
        after = hits[int(pieces[n + 1])].ref if n + 1 < len(pieces) else None
        return before, after

    def run(piece: str, got: str) -> str:
        # Keyed on the run WITHOUT its edge whitespace: removing an entry only
        # adds or drops the joins around a run (and the outer strip), and
        # whitespace holds no macro, so the run itself is the same text.
        core = piece.strip()
        lead = piece[:len(piece) - len(piece.lstrip())]
        return lead + got + piece[len(lead) + len(core):]

    # The first render draws, in document order, every occurrence afresh -- the
    # same fixed text twice is two expansions, as it was in the joined section.
    out = []
    pieces = skeleton(frozenset(h.ref for h in hits))
    for n, piece in enumerate(pieces):
        if n % 2:
            hit = hits[int(piece)]
            out.append(bodies.setdefault(hit.ref, expand(hit.entry["body"])))
        else:
            core = piece.strip()
            runs[(core, *neighbours(pieces, n))] = got = expand(core)
            out.append(run(piece, got))
    text = finish("".join(out))
    # Text that ends the full render but does not start it: each body's suffix.
    suffixes = {pieces[-1].strip()} - {pieces[0].strip()}

    def render(kept: frozenset) -> str:
        pieces = skeleton(kept)
        return finish("".join(
            bodies[hits[int(piece)].ref] if n % 2
            else run(piece, _recall_run(runs, suffixes, (piece.strip(), *neighbours(pieces, n)),
                                        expand))
            for n, piece in enumerate(pieces)))

    units = []
    for pos, h in enumerate(hits):
        c = activation.controls(h.entry)
        units.append({"ref": h.ref, "priority": c.priority, "keep": c.keep,
                      "pinned": h.reason.get("type") == "pinned",
                      "direct": h.direct, "age": h.age, "pos": pos})
    detail = _lore_detail(hits, lore["names"])
    detail["held_back"] = [{"ref": h.ref, "name": _entry_name(h.entry),
                            "reason": deepcopy(h.reason)} for h in lore["held_back"]]
    return text, {"shed": {"units": units, "render": render}, "lore": detail}


def _expanded_section(section: Section, data: dict, body: str, head: str, lore: dict | None,
                      expand) -> tuple[str, dict]:
    """One rendered section's text after macro expansion, with `head` (already
    expanded) on the front, and any keys it adds to its section dict.

    World info is expanded piece by piece instead, so the packer can shed one
    entry at a time (spec §6) without re-drawing anything: see
    `_world_info_section`. Recalled lore is expanded whole, as
    every other section is, and only gains its inspector detail."""
    if lore is not None and section.id == _WORLD_INFO_SECTION:
        return _world_info_section(section, data, lore, head, expand)
    body = expand(body).strip()
    text = head + "\n\n" + body if (head and body) else body
    if lore is not None and section.id == _RECALLED_SECTION:
        return text, {"lore": _lore_detail(_template_order(lore["recalled"]), lore["names"])}
    return text, {}


def _render_sections(a: dict, cid: str, sid: str, opener: bool = False,
                     keep_guidance_slot: bool = False) -> list[dict]:
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
    data = {"model_guidance": "", **a["data"], "opener": opener}
    pinned = a.get("pinned_sections") or frozenset()
    # Resolved once by `_assemble`; `None` (a hand-built `a`) resolves per call.
    dt_subs = a.get("datetime_subs")
    # The activated entries as hits (`_assemble`). Absent from a hand-built `a`,
    # which then renders World info whole, as every section renders.
    lore = a.get("lore")

    def expand(text: str) -> str:
        return macros.expand_macros(text, a["subs"], cid, sid, datetime_subs=dt_subs)

    out = []
    #: The heading of the last section actually EMITTED, which is what makes
    #: the rule below about contiguous runs rather than the whole message.
    last_heading = ""
    for section in layout.apply(SECTIONS):
        if section.pcless_only and not data["pcless"]:
            continue
        if section.opener_only and not opener:
            continue
        if section.except_opener and opener:
            continue
        # Keep the enabled layout position without rendering a profile yet.
        # Empty guidance must not split a shared-heading run; a selected profile
        # can split it later using the already frozen heading text.
        if section.id == "model_guidance" and keep_guidance_slot:
            out.append({"id": section.id, "label": section.label, "text": "",
                        "tier": section.tier, "pinned": False,
                        "heading": "", "heading_text": ""})
            continue
        body = prompts.render(_section_template(section, data), **data).strip()
        # A shared heading (`Section.heading`) opens each contiguous RUN of the
        # sections that name it, and this is the only place that can tell: it
        # holds the reader's merged layout and the rendered text at once, so
        # "which of these comes first", "which of these has anything to say"
        # and "are they still next to each other" are all answerable here and
        # nowhere else.
        #
        # Per run, not once per message, because the heading frames a BLOCK. A
        # layout that puts another section between the two halves has made two
        # blocks, and that section's own `# Heading` closes the first -- so a
        # suppressed second copy leaves the later half reading as part of
        # whatever came between, which is the unframed list #423 is about.
        # The catalog keeps them adjacent, so the default prompt has one run
        # and is unchanged.
        #
        # A section that rendered EMPTY does not break a run: it puts nothing
        # between the halves, so `last_heading` moves only when something is
        # actually appended.
        #
        # The heading is expanded FIRST and separately, and both halves of that
        # matter. Separately, because the exact string that goes on the front
        # has to be the exact string `_dedupe_runs` can take back off, and
        # expanding the joined text leaves no handle on where the prefix ends.
        # First, because `{{random:...}}` and `{{roll:...}}` are draws off a
        # shared RNG, so expansion order is the order the draws land in: the
        # heading is printed above the body, and expanding the body first would
        # hand the body's position the heading's draw. Templates are editable
        # on disk, so a heading with a macro in it is a thing a reader can
        # have. Each half is still expanded exactly once, in the order it is
        # read.
        head = ""
        if body and section.heading and section.heading != last_heading:
            head = expand(prompts.render(section.heading, **data)).strip()
        text, extra = _expanded_section(section, data, body, head, lore, expand)
        if not text:
            # `last_heading` stays put: an empty section breaks no run.
            out.extend(r for r in (_held_back_only(section, pinned, extra),) if r)
            continue
        out.append({"id": section.id, "label": section.label,
                    "text": text, "tier": section.tier,
                    "pinned": section.id in pinned,
                    "heading": section.heading, "heading_text": head, **extra})
        last_heading = section.heading
    # Openers have their own final actor instruction and no handoff protocol.
    # The turn contract asks for a fenced handoff, which is invalid in a draft.
    if not opener:
        actor.add_contract(out, data)
    return out


def _dedupe_runs(sections: list[dict]) -> None:
    """Strip a shared heading that PACKING made adjacent to its own copy.

    Runs are decided while sections render, and the packer edits the list
    afterwards -- so a layout that put a droppable section between two members
    of a group renders two correctly-framed runs, and dropping that section
    closes them up into one block carrying its heading twice.

    In place, on the packed sections, so `_system_text` and `_breakdown` read
    the same strings: an inspector row still showing a heading the prompt does
    not have would be describing a different message from the one that went
    out, which is the split `_render_sections` exists to prevent.

    Safe in the direction packing cares about, which is why this can live after
    `pack.pack` while the mirror-image problem (reassigning a heading to a
    survivor when its carrier was dropped) cannot: `pack` measured the prompt
    WITH both copies, so removing one only ever lowers the total. The cost is a
    fit computed one heading pessimistically; adding tokens after the fit would
    be a ceiling already spent.
    """
    last = ""
    for s in sections:
        if s.get("dropped") or not s["text"]:
            continue   # sent nothing, so it closes no run (a held-back-only row)
        head = s.get("heading") or ""
        dupe = s.get("heading_text")
        if head and head == last and dupe and s["text"].startswith(dupe):
            s["text"] = s["text"][len(dupe):].lstrip("\n")
            s["heading_text"] = ""
        # Set even when stripped: the section is still in the group, so a THIRD
        # member closing up behind it must dedupe against the same heading.
        last = head


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
    packed = pack.pack(_render_sections(a, cid, sid, opener=opener),
                       [] if opener else a["history"], reserved, budget,
                       compose=_compose_system,
                       notes=frozenset() if opener else frozenset(
                           n["index"] for n in a.get("notes", ()) if n["index"] is not None))
    # After the fit, never before: what the packer DROPPED is what decides
    # whether two runs of a shared heading became one.
    _dedupe_runs(packed["sections"])
    return {**packed, "budget": budget}


def _profile_sections(base: list[dict], guidance: str) -> list[dict]:
    """Select guidance in the frozen layout, retaining shared-heading frames.

    The ordinary render's heading copies describe the layout WITHOUT guidance.
    Inserting it can split an off-scene-cast run. Give the newly separated half
    its frozen heading before packing, so the added tokens participate in fit.
    """
    headings = {s["heading"]: s["heading_text"] for s in base if s["heading_text"]}
    sections = []
    last = ""
    for original in base:
        section = dict(original)
        if section["id"] == "model_guidance":
            section["text"] = guidance
        if not section["text"]:
            # A held-back-only World info row: nothing to frame, opens no run.
            if (section.get("lore") or {}).get("held_back"):
                sections.append(section)
            continue
        heading = section["heading"]
        frozen_heading = headings.get(heading, "")
        if frozen_heading and heading != last and not section["heading_text"]:
            section["heading_text"] = frozen_heading
            section["text"] = frozen_heading + "\n\n" + section["text"]
        sections.append(section)
        last = heading
    return sections


def _prepare(a: dict, cid: str, sid: str, *, model: str, describe: bool,
             opener: bool = False, before_post: tuple[dict, ...] = (),
             after_post: tuple[dict, ...] = (),
             extra: tuple[tuple[str, str], ...] = ()) -> tuple[model_guidance.PreparedMessages, dict | None]:
    """Freeze one generation, including each available profile's packed variant.

    A fallback is the same scene evidence with different model advice. Reusing
    `_assemble` or the render pass here would reroll macros and read a newer
    scene; swapping strings after packing would make the token ceiling a lie.
    Capture the rendered inputs first, then reuse the existing packer and
    breakdown over independent section dictionaries. Pack the finite profile
    set now: a compiled Jinja template can still load includes dynamically,
    so retaining the template alone would not freeze a later fallback. This
    costs one packing pass per distinct profile text plus the empty profile;
    the unbounded, undescribed path still does no token counting.
    """
    base = _render_sections(a, cid, sid, opener=opener, keep_guidance_slot=True)
    profiles = {}
    if any(s["id"] == "model_guidance" for s in base):
        data = {**a["data"], "opener": opener}
        for name, text in model_guidance.freeze_profiles(**data).items():
            wrapped = prompts.render("scene/sections/model_guidance.j2",
                                     **{**data, "model_guidance": text})
            profiles[name] = macros.expand_macros(wrapped, a["subs"], cid, sid,
                                                  datetime_subs=a.get("datetime_subs")).strip()
    compose = _compose_system
    history = deepcopy([] if opener else a["history"])
    notes = deepcopy([] if opener else a.get("notes", []))
    # Which history messages are author's notes: the packer keeps them off its
    # floor, and the breakdown reports them in a row of their own.
    note_index = frozenset(n["index"] for n in notes if n["index"] is not None)
    post_history = a["post_history"]
    before_post, after_post = deepcopy(before_post), deepcopy(after_post)
    budget = pack.budget_tokens()
    count = _token_memo()
    reserved = (count(post_history)
                + sum(count(text) for _label, text in extra)) if budget > 0 else 0

    def variant(guidance):
        sections = _profile_sections(base, guidance)
        packed = pack.pack(sections, history, reserved, budget, compose=compose, count=count,
                           notes=note_index)
        _dedupe_runs(packed["sections"])
        packed["budget"] = budget
        system = compose([s["text"] for s in packed["sections"] if not s["dropped"]])
        messages = [{"role": "system", "content": system}] if system or opener else []
        messages += _merged_carrier(packed["history"], before_post)
        if post_history:
            messages.append({"role": "system", "content": post_history})
        messages += after_post
        detail = (_breakdown({"post_history": post_history, "notes": notes}, packed,
                             list(extra), count=count)
                  if describe else None)
        return messages, detail

    # Aliases and profiles with identical text share the same packing work.
    # Selection and prompt-record notification stay lazy: only a dispatched
    # fallback is observed, even though every possible payload is ready.
    frozen = {text: variant(text) for text in dict.fromkeys(["", *profiles.values()])}

    def select(selected_model):
        return frozen[model_guidance.guidance_for(selected_model, profiles)]

    prepared = model_guidance.PreparedMessages(model, select, profiles={
        "": frozen[""], **{name: frozen[text] for name, text in profiles.items()}},
        campaign=cid)
    prepared.settings = deepcopy(a.get("response_settings"))
    return prepared, prepared.breakdown


def _merged_carrier(history: list[dict], before_post: tuple[dict, ...]) -> list[dict]:
    """`history + before_post`, with a trailing carrier folded into a user
    message the prompt appends right after it (a director note, #377), so the
    carrier never sits beside another user message. New list, new dicts: the
    packed history and `before_post` are shared by every guidance variant."""
    tail = history[-1] if history else None
    first = before_post[0] if before_post else None
    if not (tail and tail.get(content_parts.CARRIER) and first and first["role"] == "user"):
        return [*history, *before_post]
    content = first["content"]
    note = {**first, "content": [*tail["content"],
                                 *([{"type": "text", "text": content}]
                                   if isinstance(content, str) else content)]}
    return [*history[:-1], note, *before_post[1:]]


def _token_memo():
    """`tokens.count_tokens`, memoized for one `_prepare`.

    `_prepare` packs and describes one variant per distinct model profile, and
    the variants differ only in the guidance section: the history, the post-
    history block, the appended messages and every other section are the same
    strings each time, and within one variant the packer and `_breakdown`
    measure the same sections and history twice over. Counted afresh they were
    re-encoded at every one of those -- the whole transcript, several times per
    turn. Only a composed system message is new per variant, and it is counted
    once either way. Per call rather than process-wide, so it holds only strings
    this compose already holds and needs no eviction story; and the tokenizer
    is looked up on each miss, so a test patching `count_tokens` still sees
    every distinct string.
    """
    seen: dict[str, int] = {}
    used: set[str] = set()

    def count(text: str) -> int:
        n = seen.get(text)
        if n is None:
            n = seen[text] = tokens.count_tokens(text)
            if text and (which := tokens.last_counter()):
                used.add(which)
        return n

    def counted_with() -> str:
        """Which counter produced this compose's numbers: one name, `MIXED`
        when an encode fell back on some strings, "" when nothing was counted
        (or a test replaced `count_tokens`, which records no counter)."""
        if len(used) > 1:
            return tokens.MIXED
        return next(iter(used), "")

    count.counted_with = counted_with
    return count


def _compose_system(texts: list[str]) -> str:
    """The system message as it will be sent, from section texts."""
    # Only sections with text: a held-back-only World info row is in the list
    # for the inspector and has nothing to say to the model.
    return prompts.render("scene/system.j2", sections=[t for t in texts if t]).strip()


def _system_text(packed_sections: list[dict]) -> str:
    """Join the sections that survived packing into the system message."""
    return _compose_system([s["text"] for s in packed_sections if not s["dropped"]])


#: One message a caller wants appended after the system message, as
#: ``(inspector label, role, content)``. See `compose_turn`.
Appended = tuple[str, str, str]


def compose_turn(cid: str, sid: str, turn: dict | None = None,
                 appended: tuple[Appended, ...] = (),
                 describe: bool = True, model: str = "", actor_ref: str | None = None,
                 eligible_speakers: list[dict] | None = None,
                 images: int = 0) -> tuple[model_guidance.PreparedMessages, dict | None]:
    """One turn's messages, and the breakdown describing them.

    `images` is how many of the newest post images to keep as references for a
    route that reads them (#377, `store.post_images.images_for`); 0 composes
    today's text-only prompt, byte for byte.

    `describe=False` returns `None` for the breakdown and skips building it.
    That is not a micro-optimisation: on the DEFAULT unbounded budget `pack`
    skips tokenising entirely and deliberately says so, while `_breakdown`
    counts every section, every history message and the composed system prompt.
    Building one for a caller that will throw it away -- `build_messages`, or
    any route while `prompt_log_depth` is 0 -- would put a full tokenizer pass
    on every turn of a feature the user has turned off.

    BOTH out of a single `_assemble` + `_prepare` pass, which is what lets a
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
    a = _assemble(cid, sid, turn=turn, actor_ref=actor_ref, eligible_speakers=eligible_speakers,
                  images=images)
    return _prepare(a, cid, sid, model=model, describe=describe,
                    after_post=tuple({"role": role, "content": content}
                                     for _label, role, content in appended),
                    extra=tuple((label, content) for label, _role, content in appended))



def build_messages(cid: str, sid: str, turn: dict | None = None,
                   appended: tuple[Appended, ...] = (), model: str = "",
                   images: int = 0) -> list[dict]:
    """`compose_turn` without the breakdown — see there.

    The old `reserve=` parameter is gone. It charged the budget for a message
    the caller then appended itself, which left the two halves free to drift
    and gave `compose_turn` no way to report the appended block; `appended`
    does all three jobs at once.
    """
    return compose_turn(cid, sid, turn=turn, appended=appended, describe=False, model=model,
                        images=images)[0]


def compose_director_turn(cid: str, sid: str, note: str, turn: dict | None = None,
                          describe: bool = True, model: str = "", actor_ref: str | None = None,
                          eligible_speakers: list[dict] | None = None,
                          appended: tuple[Appended, ...] = (),
                          images: int = 0) -> tuple[model_guidance.PreparedMessages, dict | None]:
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
    a = _assemble(cid, sid, wi_seed=note, turn=turn, actor_ref=actor_ref,
                  eligible_speakers=eligible_speakers, images=images)
    # expanded up front so the note's tokens are reserved before packing: it is
    # a mandatory message, so the budget has to know about it
    note_text = macros.expand_macros(note, a["subs"], cid, sid, datetime_subs=a["datetime_subs"])
    return _prepare(a, cid, sid, model=model, describe=describe,
                    before_post=({"role": "user", "content": note_text},),
                    after_post=tuple({"role": role, "content": content}
                                     for _label, role, content in appended),
                    extra=(("Director note", note_text),
                           *((label, content) for label, _role, content in appended)))



def build_director_messages(cid: str, sid: str, note: str, turn: dict | None = None,
                            model: str = "", images: int = 0) -> list[dict]:
    """`compose_director_turn` without the breakdown — see there."""
    return compose_director_turn(cid, sid, note, turn=turn, describe=False, model=model,
                                 images=images)[0]


def _history_rows(p: dict, count, notes: frozenset[int] = frozenset(),
                  notes_trimmed: int = 0) -> list[dict]:
    """The history's inspector rows: the conversation, then -- when the kept
    history carries image references (#377) -- an Images row.

    The Images row is a SPLIT of the history's cost, never an addition beside
    it: `pack.message_cost` already charges each reference `IMAGE_TOKENS`, and
    `_breakdown`'s total is built from that, so the conversation row reports
    the text alone and the two rows sum to what the packer charged. Kept as a
    row of its own, in the `history` tier, so the budget bar draws the pictures
    in the Conversation bucket rather than leaving them out of a prompt whose
    whole size it claims to show. A carrier has no text and adds no blank line
    to the joined conversation.

    `notes` are the packed-history indices of author's-note messages, which
    the Author's notes row reports instead (`_notes_row`): the conversation
    row neither shows nor counts them, and its `trimmed` counts only the
    conversation messages the trim took (`notes_trimmed` were notes).
    """
    kept = [m for i, m in enumerate(p["history"]) if i not in notes]
    texts = [content_parts.text_of(m["content"]) for m in kept
             if not m.get(content_parts.CARRIER)]
    refs = [r for m in kept for r in content_parts.image_refs(m["content"])]
    rows = []
    hist = "\n\n".join(texts)
    if hist:
        # Displayed joined (one readable block), accounted per message with the
        # same per-message framing allowance the packer charges.
        rows.append({"id": "history", "label": "Conversation history", "text": hist,
                     "tier": pack.HISTORY, "dropped": False, "pinned": False,
                     "trimmed": p["history_trimmed"] - notes_trimmed,
                     "tokens": sum(pack.message_cost(content_parts.text_of(m["content"]), count)
                                   for m in kept if not m.get(content_parts.CARRIER))})
    if refs:
        rows.append({"id": "history_images", "label": f"Images ({len(refs)})",
                     "text": "\n".join(f"{r.get('alt', '')} — {r.get('url', '')}" for r in refs),
                     "tier": pack.HISTORY, "dropped": False, "pinned": False, "trimmed": 0,
                     "tokens": (len(refs) * pack.IMAGE_TOKENS
                                + sum(pack.MESSAGE_OVERHEAD for m in kept
                                      if m.get(content_parts.CARRIER)))})
    return rows


def _lore_row(section: dict) -> dict:
    """The entry-level keys of a World info or Recalled lore row: each entry
    with whether the packer shed it, the names its reasons need, and (World
    info) what was held back. Explicit keys, so the section's `shed` -- a
    function -- can never reach a row, a capture or JSON."""
    detail = section.get("lore")
    if not detail:
        return {}
    shed = set(section.get("shed_refs") or ())
    row = {"entries": [{**e, "reason": deepcopy(e["reason"]), "shed": e["ref"] in shed}
                       for e in detail["entries"]],
           "names": dict(detail["names"])}
    if "held_back" in detail:
        row["held_back"] = deepcopy(detail["held_back"])
    return row


def _notes_row(notes: list[dict], p: dict, count) -> tuple[dict, frozenset[int]]:
    """The Author's notes row (play controls V), and the packed-history indices
    of the notes it reports.

    `notes` are `_assemble`'s positions (indices into the history BEFORE
    packing) plus the notes this turn's cadence skipped. The packer only trims
    from the front, so a note at index `i` is at `i - history_trimmed` when it
    survived, and was trimmed when `i` fell inside the cut. Each note is listed
    as applied (with the text that went out), trimmed, or skipped with its
    cadence, which is how the reader sees why a configured note is absent.
    Tier `history`: the notes are sent inside the conversation and the packer
    trims them with it, so a lock-in label would describe a protection they do
    not have.
    """
    cut = p["history_trimmed"]
    kept: set[int] = set()
    entries: list[dict] = []
    blocks: list[str] = []
    labels = {"campaign": "Campaign", "scene": "Scene"}
    for n in notes:
        label = labels.get(n["level"]) or f"Character: {n['name']}"
        entry = {"level": n["level"], "name": n["name"], "depth": n["depth"],
                 "every": n["every"], "status": n["status"], "tokens": 0}
        if n["status"] == "skipped":
            blocks.append(f"[{label} · skipped (every {n['every']})]")
        elif n["index"] < cut:
            entry["status"] = "trimmed"
            blocks.append(f"[{label} · depth {n['depth']} · trimmed]")
        else:
            at = n["index"] - cut
            kept.add(at)
            text = content_parts.text_of(p["history"][at]["content"])
            entry["tokens"] = pack.message_cost(text, count)
            blocks.append(f"[{label} · depth {n['depth']}]\n{text}")
        entries.append(entry)
    row = {"id": "authors_note", "label": "Author's notes", "tier": pack.HISTORY,
           "dropped": False, "pinned": False,
           "trimmed": sum(1 for e in entries if e["status"] == "trimmed"),
           "tokens": sum(e["tokens"] for e in entries), "text": "\n\n".join(blocks),
           "notes": entries}
    return row, frozenset(kept)


def _breakdown(a: dict, p: dict, extra: list[tuple[str, str]] | None = None,
               count=None) -> dict:
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

    `count` is the tokenizer, `tokens.count_tokens` by default; `_prepare`
    passes the memoized one it packed with (`_token_memo`).
    """
    extra = extra or []
    if count is None:
        count = tokens.count_tokens
    rows = [{"id": s["id"], "label": s["label"], "text": s["text"], "tier": s["tier"],
             "dropped": s["dropped"], "trimmed": 0, "pinned": bool(s.get("pinned")),
             "tokens": count(s["text"]), **_lore_row(s)}
            for s in p["sections"]]

    # Every history message, notes included: they are sent, so they cost.
    hist_tokens = sum(pack.message_cost(m["content"], count) for m in p["history"])
    notes = a.get("notes") or []
    if notes:
        # Only when a note is configured, so a campaign with none describes
        # its turns exactly as it did before notes existed.
        note_row, note_index = _notes_row(notes, p, count)
        history_rows = _history_rows(p, count, note_index, p.get("notes_trimmed", 0))
        at = next((n + 1 for n, r in enumerate(history_rows) if r["id"] == "history"), 0)
        rows += [*history_rows[:at], note_row, *history_rows[at:]]
    else:
        rows += _history_rows(p, count)
    if a["post_history"]:
        rows.append({"id": "post_history", "label": "Post-history instructions",
                     "text": a["post_history"], "pinned": False,
                     "tier": pack.LOCK_IN, "dropped": False, "trimmed": 0,
                     "tokens": count(a["post_history"])})
    # `lock-in`, and not merely as a label: `_packed` reserved these, so the
    # packer could not drop them even had it wanted to. Reporting them under any
    # droppable tier would describe a choice the packer never had.
    #: Appended rows are numbered rather than named after their label: two of
    #: them can carry the same one (an opener sends a prompt and shape rules
    #: every time), and the inspector keys its rows on `id`.
    extra_tokens = [count(text) for _label, text in extra]
    rows += [{"id": f"appended_{n}", "label": label, "text": text, "tier": pack.LOCK_IN,
              "dropped": False, "trimmed": 0, "pinned": False, "tokens": cost}
             for n, ((label, text), cost) in enumerate(zip(extra, extra_tokens))]

    kept = [s["text"] for s in p["sections"] if not s["dropped"]]
    total = (count(_compose_system(kept)) + hist_tokens
             + count(a["post_history"]) + sum(extra_tokens))
    # Trimmed history messages are gone from `rows` entirely -- they are not a
    # section that can be shown struck through -- so their cost has to be added
    # here, or a pack that fit by trimming history alone reports nothing
    # dropped and the inspector stays silent about the cut it just made.
    out = {"sections": rows, "total_tokens": total,
           "dropped_tokens": (sum(r["tokens"] for r in rows if r["dropped"])
                              + p["history_trimmed_tokens"]
                              + p.get("images_dropped_tokens", 0)),
           "budget_tokens": p["budget"]}
    # Which counter made these numbers, taken from the pass that made them
    # (`_token_memo`) -- `tokens.counting` turns it into the inspector's label.
    # Absent when `count` is a bare function that cannot say.
    which = getattr(count, "counted_with", None)
    if which is not None and which():
        out["counted_with"] = which()
    return out


def context_breakdown(cid: str, sid: str, model: str = "", images: int = 0) -> dict:
    """The LIVE inspector view: compose the turn as it would be sent right now
    and describe it. Runs the same render and pack `compose_turn` runs.

    The live view has no appended blocks — those belong to a specific turn
    (regenerate guidance, a roll result), and this composes a hypothetical one.
    """
    _messages, detail = compose_turn(cid, sid, model=model, images=images)
    assert detail is not None  # describe=True always builds the inspector
    return detail


def context_sections(cid: str, sid: str, model: str = "", images: int = 0) -> list[dict]:
    """Just the rows of `context_breakdown` — see there."""
    return context_breakdown(cid, sid, model=model, images=images)["sections"]
