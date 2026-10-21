"""Capture one scene moment and its resolved authorial and actor inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ... import (
    authors_notes,
    campaign_settings,
    config,
    default_prompts,
    locks,
    pins,
    response_presets,
    response_targets,
)
from ...appearances import cast as appearances_cast
from ...appearances import paths as appearances_paths
from ...campaigns import paths as campaigns_paths
from ...campaigns import read as campaigns_read
from ...history import settings as history_settings
from ...regex import view as regex_view
from ...scenes import focus as scenes_focus
from ...scenes import identity as scenes_identity
from ...scenes import read as scenes_read
from ...scenes import serialize as scenes_serialize
from ...tracker import view as tracker_view
from ...worlds import read as worlds_read
from .. import actor, authors_note, epistemic, macros, mechanics, speaker, world_state

# Module objects, not names: `_assemble` binds a local `cast` (hence the alias),
# and `cast._drift_roster` has to stay patchable from the test that counts it.
from .. import cast as cast_data
from .. import history as history_section
from . import _catalog, _history, _inputs


def _assemble(cid: str, sid: str, *, wi_seed: str = "", full_recap: int = 0,
              turn: dict | None = None, actor_ref: str | None = None,
              eligible_speakers: list[dict] | None = None,
              successor_by_decision: bool = False,
              opening_narrator: bool = False, opener: bool = False,
              images: int = 0, history_view: epistemic.EpistemicView | None = None) -> dict:
    """One pass gathering the template data + projected history + post-history.
    build_* render templates/scene/system.j2 from data; context_sections renders
    the per-section templates for the token breakdown. `wi_seed` folds extra text
    (the opener prompt) into the world-info activation window; `full_recap` (> 0)
    selects the full story-so-far variant over the compact recap. `turn` is a
    one-shot, unpersisted override (e.g. a per-turn response-length chip) that
    outranks every stored scope in response_presets.resolve -- see build_messages.
    `opener` skips the tracker read: its section is `except_opener`, so the
    read and its lock waits would buy the opener nothing. (The opener composes
    in a worker, `routes.greetings._compose_opener_call`, never on the event
    loop.)"""
    if history_view is not None and not _inputs._check_view(history_view, cid, sid, actor_ref):
        history_view = None
    scene, standing, focus_field, tracker = _read_moment(cid, sid, opener=opener)
    # The prompt view (`store/regex`): copies, with every prompt-phase rule
    # applied, so the history, the world-info scan and the birthday check below
    # all read the text the model will be shown. No rules, plain copies.
    # Kept whole for world-info activation, which numbers posts by their place
    # in the transcript even on an NPC call (see `posts` below).
    full_history = regex_view.view(scene["messages"], cid=cid, phase="prompt")
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
    rules = pins.active(cid, sid, len(full_history))
    pinned_refs, excluded_refs = rules["pinned"], rules["excluded"]
    # An excluded actor is removed HERE, before anything reads the cast, so
    # every consequence of being on stage goes with them: their card, their
    # state, their voice notes, their sheet -- and their ref in `present`, which
    # is what keeps an excluded character's owned lore from standing in a prompt
    # they are no longer in. The appearance record is untouched: an exclude is a
    # context rule, not a departure, and lifting it puts them straight back.
    # Who is in the room, kept whole when an NPC call narrows the cast: the
    # owner gate reads the scene, not the speaker (spec §8.1), so lore gated on
    # another character present activates on this call too -- and what this
    # call may then SEE of it is `actor.knows`'s question, asked afterwards.
    scene_cast = [a for a in appearances_cast.scene_cast(cid, sid)
                  if f"{a['kind']}:{a['id']}" not in excluded_refs]
    # BEFORE the cast is narrowed to an assigned NPC, because what that NPC can
    # perceive of the others is the point of the section -- and filtered for
    # it here, by `view`, rather than by blanking afterwards: a narrator reads
    # every value, an NPC its own and what it was shown or told.
    tracker_viewer = actor_ref if actor_ref not in (None, tracker_view.NARRATOR) else None
    tracker_lines = _inputs._tracker_lines(cid, sid, tracker, tracker_viewer, excluded_refs)

    roster = actor.public_roster(scene_cast)
    public_player_names = [a["name"] for a in scene_cast if a["role"] == "player"]
    reader = _reader(cid, sid, actor_ref, scene_cast, full_history)
    cast, history, npc_ref = reader.cast, reader.history, reader.npc_ref
    actor_scoped, actor_name = reader.actor_scoped, reader.actor_name
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
        npc_ref, actor_name, note_turn)

    pcless = scene["meta"].get("pcless") == "true"
    people = _people(cid, aroot, reader, public_player_names, pcless=pcless)
    # {{char}} is not resolved here (#137): baked at creation time instead, see
    # scene_substitutions.
    subs = {"{{user}}": ", ".join(people.player_names or people.ref_names)}

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

    voices = _voices(cid, people, visible, _expanded)

    cfg = campaign_settings.effective_config(cid)
    depth = config._whole(cfg.get("context_scan_depth"), config.DEFAULT_SCAN_DEPTH)
    recent_text = _inputs._scan_window(visible, depth, wi_seed)
    # Birthday names belong to the current question. World-info activation
    # deliberately scans several turns, but an earlier name must not widen a
    # direct question about somebody else.
    birthday_text = wi_seed or _last_question(visible)
    setting = _setting(cid, sid, excluded_refs, npc_ref, scene_cast)

    campaign_meta = campaigns_read.read_campaign(cid)["meta"]
    # One per-field cascade resolves BOTH the prose style and the length budget
    # over turn -> scene -> campaign -> global. It subsumes the old style-only
    # resolver: with no response presets set anywhere (every pre-existing
    # install) it walks the same style_id keys in the same order, so the
    # migration is a no-op.
    turn = turn or {}
    budget = response_presets.resolve(turn=turn, scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    targets = response_targets.resolve(turn=turn, scene_meta=scene["meta"],
                                       campaign_meta=campaign_meta, config=cfg)
    phase = "opening" if opening_narrator else "continuation"
    style_name, style_body = _inputs._prose_style(budget["style_id"])
    activated_wi, recalled_wi, wi_result, wi_names = world_state._world_info(
        cid, _scan_posts(full_history, visible), wi_seed, exclude=setting.exclude,
        present=setting.present, pinned_refs=pinned_refs,
        excluded_refs=excluded_refs, scan_depth=depth,
        recursion_depth=config.lore_recursion_depth(cfg),
        current_location=setting.location, recall_text=recent_text,
        actor_ref=npc_ref, sid=sid)
    # A no-op since `_world_info` filters its candidates for the actor; kept so
    # what reaches an NPC's prompt does not rest on that one call site alone.
    if actor_scoped:
        activated_wi = actor.known_entries(activated_wi, actor_ref)
        recalled_wi = actor.known_entries(recalled_wi, actor_ref)
    wi_public, wi_secret = world_state.secrecy_split(activated_wi)
    lore = _inputs._lore(wi_result, activated_wi, recalled_wi,
                 {**wi_names, **{f"{m['kind']}:{m['id']}": str(m.get("name") or m["id"])
                                 for m in scene_cast}})
    recalled_public, recalled_secret = world_state.secrecy_split(recalled_wi)
    mech = mechanics._mechanics(cid, sid, cast, recent_text)
    candidates = eligible_speakers or []
    data = {
        "opener": False, "pcless": pcless, "story_full": bool(full_recap),
        "response_actor": reader.response_actor,
        "response_successor_by_decision": successor_by_decision,
        "perception_rider": config.perception_rider(),
        "response_roster": roster,
        "response_candidates": [{"ref": e["ref"], "name": e["name"]} for e in candidates],
        # A candidate marked `responded` has already spoken this round, which
        # an automatic chain offers on purpose: naming them begins the next
        # round (`routes.character_turns._compose`). The handoff text says so.
        "response_repeat": any(e.get("responded") for e in candidates),
        "global_system_prompt": cfg.get("system_prompt", ""),
        "world_default_prompt": standing["world"]["text"],
        "campaign_default_prompt": standing["text"],
        "scene_focus": focus_field["text"],
        # The campaign's world as its author describes it (#38), read live
        # through the world like every record the campaign inherits. Public
        # framing, so not on the actor-scoped blanking list below.
        "world_overview": worlds_read.profile_of(campaigns_read.world_root_of(cid)),
        "prose_style_name": style_name,
        "prose_style_body": style_body,
        "budget": targets[phase],
        "npc_cards": people.npc_cards,
        "cast_blocks": voices.blocks,
        "cast_block_refs": voices.block_refs,
        "named_npc_count": voices.named_npc_count,
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
        "speaker": (speaker.nominate(people.npc_names, visible, pending=wi_seed)
                    if not actor_scoped and config.speaker_turn_taking() else None),
        "players": people.players, "ref_names": people.ref_names, "refs": people.refs,
        # The recap, archive, ledgers, calendar, relationship graph, group
        # state, off-scene cast and art catalogue are campaign-wide. Birthday
        # metadata is retrieved for the current question in either voice.
        **_inputs._campaign_view(cid, sid, croot=croot, cast=cast, recent_text=recent_text,
                         birthday_text=birthday_text, full_recap=full_recap,
                         activated_wi=activated_wi, recalled_wi=recalled_wi,
                         art_loc=setting.location, actor_scoped=actor_scoped,
                         excluded_refs=excluded_refs),
        "weather": world_state._weather_data(cid, sid),
        "current_setting": setting.text,
        "current_setting_secret": setting.secret,
        # Split by secrecy (#49): the secret halves render under a heading that
        # tells the model to keep them out of the mouths of characters who have
        # not learned them. GM-only entries are already gone -- `activate`
        # dropped them before anything here could see them.
        "world_info_bodies": wi_public, "secret_world_info_bodies": wi_secret,
        "recalled_lore_bodies": recalled_public,
        "secret_recalled_lore_bodies": recalled_secret,
        "player_names": people.player_names,
        "mechanics_rules": mech["mechanics_rules"], "mechanics_sheets": mech["mechanics_sheets"],
        "mechanics_checks": mech["mechanics_checks"],
    }

    # Recalled history (09, classified by 11): the view's own sections, the
    # narrator's two or the actor's two -- `_check_view` has refused a view
    # for anyone else. Off is no section at all.
    if not _history_on(history_view, cfg):
        history_view = None
    history_parts = (_inputs._history_parts(history_view, scene_cast, actor_name, actor_scoped=actor_scoped)
                     if history_view is not None else {})
    data.update(_history_data(history_parts))
    data["history_actor_name"] = actor_name

    if npc_ref is not None:
        _inputs._scope_to_actor(data, cid, npc_ref, roster, history_parts)

    post_history = _expanded(_inputs._post_history(cid, croot, cast, people.npc_cards,
                                           reader.response_actor,
                                           visible=visible, budget=targets["continuation"]))
    # The notes go INTO the projected history, each its own system message at
    # its depth (`authors_note.inject`); their positions travel beside it in
    # `notes`, never on the wire. An opener sends no history, so its notes ride
    # in `before_post` instead (`compose_opener`).
    projected_notes, opener_applied = ([], applied_notes) if opener else (applied_notes, [])
    sub_history, positions = _history._projected(cid, history, projected_notes,
                                        images=images, expand=_expanded)
    notes = positions + [{**n, "index": None, "status": "skipped"} for n in skipped_notes]
    opener_notes = [(n["level"], _expanded(authors_note.render(n))) for n in opener_applied]
    return {"data": data, "config": cfg, "subs": subs, "datetime_subs": dt_subs, "history": sub_history,
            "notes": notes, "opener_notes": opener_notes,
            "post_history": post_history, "npc_names": people.npc_names, "wi_result": wi_result,
            "lore": lore,
            # On, the view reaches its sections even with nothing selected, so
            # the first one's row can say why (empty, exhausted, failed); off,
            # none does.
            "history_view": history_view,
            "history_parts": history_parts,
            # The archive and recap are the narrator's; an NPC omits nothing.
            "history_omissions": ({} if actor_scoped
                                  else _omissions(cid, sid, recent_text, full_recap, history_parts)),
            "prompt_field_statuses": {"world_default_prompt": standing["world"]["status"],
                                      "campaign_default_prompt": standing["status"],
                                      "scene_focus": focus_field["status"]},
            "response_settings": {"style_id": budget["style_id"], "phase": phase,
                                  **targets[phase]},
            "pinned_sections": _catalog._pinned_sections(pinned_refs, cast, activated_wi,
                                                setting.location, voices.voiced_ids)}


def _read_moment(cid: str, sid: str, *, opener: bool) -> tuple[dict, dict, dict, tuple]:
    """The scene, its standing prompts, its focus and its tracked state, read
    together as one moment: `(scene, standing, focus, tracker)`."""
    # BEST-EFFORT, not `campaign_lock`: `post_chat` appends the player's post
    # before calling this and only wires the undo that would take it back off
    # afterwards, so a `StoreBusy` raised here would strand that post with no
    # reply and nothing able to remove it. Under contention it reads unlocked.
    with locks.best_effort_campaign_lock(cid):
        scene = scenes_read.read_scene(cid, sid)
        # Optional authorial fields are one fresh snapshot, shared by all
        # prepared model variants. Never consult them on historical replay.
        standing = default_prompts.resolve(cid)
        focus_field = scenes_focus.read(cid, sid)
        # The tracked state as of the transcript's tail, read beside the scene
        # so the two describe one moment. A reroll never comes through here:
        # it replays the prompt frozen for the response it replaces, which
        # already holds the state that stood before that post.
        tracker = _inputs._NO_TRACKER if opener else _inputs._tracker_read(cid, sid)
    return scene, standing, focus_field, tracker


@dataclass(frozen=True)
class _Reader:
    """Whom this prompt is written for: the narrator, or one NPC's own call."""

    cast: list[dict]                # narrowed to the assigned NPC on an NPC's call
    history: list[dict]             # narrowed to what that NPC observed
    response_actor: dict | None
    npc_ref: str | None             # the NPC whose own call this is, or None for the narrator's
    actor_name: str

    @property
    def actor_scoped(self) -> bool:
        return self.npc_ref is not None


def _reader(cid: str, sid: str, actor_ref: str | None, cast: list[dict],
            history: list[dict]) -> _Reader:
    """The reader `actor_ref` names (``"grimoire"`` the narrator, None no one
    in particular) and the cast and transcript as that reader holds them."""
    if actor_ref == "grimoire":
        return _Reader(cast, history, {"ref": "grimoire", "name": "Grimoire"}, None, "")
    if actor_ref is None:
        return _Reader(cast, history, None, None, "")
    cast = _inputs._assigned_npc(cast, actor_ref)
    name = cast[0]["name"]
    return _Reader(cast, actor.observed_history(cid, sid, actor_ref, history),
                   {"ref": actor_ref, "name": name}, actor_ref, name)


@dataclass(frozen=True)
class _People:
    """The cards and names a reader's prompt is written with."""

    npc_cards: list[dict]
    npc_ids: list[str]
    npc_names: list[str]
    players: list[dict]
    player_names: list[str]
    refs: list[dict]                # a pcless scene's offscreen reference cast
    ref_names: list[str]


def _people(cid: str, aroot, reader: _Reader, public_player_names: list[str], *,
            pcless: bool) -> _People:
    npc_cards, npc_ids = _inputs._npc_cards(cid, aroot, reader.cast)
    players, player_names = _inputs._players(cid, aroot, reader.cast)
    if reader.actor_scoped:
        # An NPC's own call keeps only that NPC in `cast`, so it read no player
        # card above -- but `{{user}}` still names the players in the room.
        player_names = [*public_player_names, *player_names]
    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    refs, ref_names = cast_data._campaign_player_refs(cid, aroot) if pcless else ([], [])
    return _People(npc_cards, npc_ids, npc_names, players, player_names, refs, ref_names)


@dataclass(frozen=True)
class _Voices:
    """The resolved per-character blocks every voice section renders from."""

    blocks: list[dict]
    block_refs: list[str]
    # A COUNT, not a length: reading `len(cast_blocks)` at render time would
    # let a per-block filter move the policy's render condition.
    named_npc_count: int
    # Who actually PUT something in the voice sections, for the pin rule: a
    # pin on an actor that selected nothing protects nothing (`_pinned_sections`).
    voiced_ids: frozenset[str]


def _voices(cid: str, people: _People, visible: list[dict], expand) -> _Voices:
    blocks, block_refs = _inputs._cast_blocks(
        cid, people.npc_cards, people.npc_ids, names=cast_data.voice_safe_names(
            [_inputs._card_text(d, "name") for d in people.npc_cards], people.player_names),
        recent="\n".join(m["content"] for m in visible[-4:]), expand=expand)
    return _Voices(blocks, block_refs,
                   named_npc_count=sum(1 for b in blocks if b["name"]),
                   voiced_ids=frozenset(cid_ for cid_, b in zip(people.npc_ids, blocks, strict=True)
                                        if b["name"] and (b["anchor"] or b["example"])))


def _scan_posts(full_history: list[dict], visible: list[dict]) -> list[tuple[int, str]]:
    """The posts world info reads, as `(transcript index, text)`.

    World info reads the same messages per post rather than joined, each at
    its TRANSCRIPT index -- the unit a timed entry counts and an inspector
    reason cites. An NPC call reads only what that NPC observed, and
    `observed_history` returns elements of the list it was handed, so the
    filter is by identity and keeps each post's own index rather than
    renumbering the observed tail from 0. A post hidden from context is not
    read: its words must not wake an entry the prompt then carries.
    """
    observed = {id(m) for m in visible}
    return [(i, m["content"]) for i, m in enumerate(full_history) if id(m) in observed]


def _last_question(visible: list[dict]) -> str:
    """The newest player post's text, or ""."""
    return next((m["content"] for m in reversed(visible) if m["role"] == "user"), "")


class _Setting(NamedTuple):
    """The scene's location as this reader's prompt describes it."""

    location: str | None            # None when nowhere, or excluded
    text: str
    secret: bool
    exclude: frozenset              # what world info must hold back (`_current_setting`)
    present: dict[str, dict]        # the base present set and why each ref is in it (spec §7.3)


def _setting(cid: str, sid: str, excluded_refs, npc_ref: str | None,
             scene_cast: list[dict]) -> _Setting:
    history_ids = scenes_read.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    # An excluded location is still where the scene IS -- the transcript says so
    # and moving is the reader's other lever -- but it stops being described:
    # neither the setting block nor world info renders it, and it unlocks
    # nothing it owns. That is the only reading of "keep this out of the prompt"
    # that the prompt can actually honour.
    setting_loc = (None if current_loc and f"locations:{current_loc}" in excluded_refs
                   else current_loc)
    text, secret, exclude = _inputs._current_setting(cid, setting_loc, npc_ref)
    # The engine grows the present set structurally and by activation. The
    # whole scene's, on an NPC call as on the narrator's (see `scene_cast`).
    present: dict[str, dict] = {f"{a['kind']}:{a['id']}": {"type": "cast", "via": None}
                                for a in scene_cast}
    if setting_loc:
        present[f"locations:{setting_loc}"] = {"type": "current_location", "via": None}
    return _Setting(setting_loc, text, secret, exclude, present)


def _history_on(view: epistemic.EpistemicView | None, cfg: dict) -> bool:
    return (view is not None and history_settings.read(cfg) is not None
            and _catalog._section_on("history_recall"))


def _history_data(parts: dict) -> dict[str, list]:
    """Each recalled-history section's template list; empty for a section
    this reader is not shown."""
    return {key: list(parts[section_id].items) if section_id in parts else []
            for section_id, key in history_section.DATA_KEYS.items()}


def _omissions(cid: str, sid: str, recent_text: str, full_recap: int, parts: dict) -> dict:
    """What the narrator's recap and archive leave to the recalled history."""
    if not any(p.items for p in parts.values()):
        return {}
    return history_section.summary_omissions(cid, sid, recent_text, full_recap)
