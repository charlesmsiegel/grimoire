"""One-pass source readers with actor visibility and campaign-local state."""

from __future__ import annotations

import logging

from .... import prompts
from ... import birthdays, characters, entities, length_drift, overlay, pcs, styles, voice_anchors
from ...appearances import versions as appearances_versions
from ...continuity import effective
from ...scenes import identity as scenes_identity
from ...tracker import fields as tracker_fields
from ...tracker import settings as tracker_settings
from ...tracker import view as tracker_view
from ...tracker import walk as tracker_walk
from .. import activation, actor, archive, art, epistemic, story, world_state
from .. import cast as cast_data
from .. import history as history_section
from . import _catalog

log = logging.getLogger('grimoire.store.context.assemble')


def _scan_window(visible: list[dict], depth: int, seed: str) -> str:
    """The last `depth` posts in context as one text, with this turn's
    unsent `seed` after them: what semantic recall, the mechanics rules, the
    archive and the art catalogue are matched against. World info reads the
    same posts one by one instead (`posts` in `_assemble`)."""
    # depth 0 => no scan window (history[-0:] would be the WHOLE list, so guard it)
    recent_text = "\n".join(m["content"] for m in visible[-depth:]) if depth else ""
    if seed:  # opener: the prompt stands in for the (absent) recent history
        recent_text = (recent_text + "\n" + seed).strip()
    return recent_text


def _assigned_npc(cast: list[dict], actor_ref: str) -> list[dict]:
    """The cast an NPC's own call keeps: the present NPC `actor_ref` names.
    Raises when it names nobody on stage, or nobody who is an NPC."""
    selected = [a for a in cast if actor.ref(a) == actor_ref and a["role"] == "npc"]
    if not selected:
        raise ValueError("Assigned actor is not a present NPC")
    return selected


def _npc_cards(cid: str, aroot, cast: list[dict]) -> tuple[list[dict], list[str]]:
    """Each present NPC's card at its locked version, and beside it that NPC's
    id. A card or version that cannot be read drops the NPC from both lists,
    which is what keeps them aligned for the `zip`s in `_assemble`."""
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
        # for the `zip`s that pair them -- an unreadable card drops out of both.
        npc_ids.append(a["id"])
    return npc_cards, npc_ids


def _players(cid: str, aroot, cast: list[dict]) -> tuple[list[dict], list[str]]:
    """Each present player's persona (a PC) or card (a character played as
    one) at its locked version, tagged with its kind, and the names they go
    by. One that cannot be read is left out of both."""
    players: list[dict] = []
    names: list[str] = []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(aroot, a["id"], vid)
                players.append({"kind": "pcs", **p})
                names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(aroot, a["id"], vid)["data"]
                players.append({"kind": "characters", **data})
                names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound, characters.VersionNotFound):
            continue
    return players, names


def _card_text(card: dict, key: str) -> str:
    """A card field as stripped text, or "" when it is not a string.

    A card is hand-editable and importable, so every one of these fields can
    arrive as a non-string -- `test_a_malformed_card_name_costs_only_its_own_actor`
    hand-edits `data.name` to a LIST. Reading through this keeps a malformed
    field costing only its own block rather than raising out of the whole
    assembly, which is the same per-actor failure policy `_character_states`
    already applies.
    """
    v = card.get(key)
    return v.strip() if isinstance(v, str) else ""


def _cast_blocks(cid: str, npc_cards: list[dict], npc_ids: list[str], *,
                 names: list[str], recent: str, expand) -> tuple[list[dict], list[str]]:
    """ONE resolved structure for every section that names a character:
    `character_descriptions`, `voice_policy`, `voice_anchors`,
    `voice_examples` -- each NPC's shown name (`names`, already disambiguated
    against the players), description, effective voice anchor and the example
    dialogue chosen against `recent`, plus beside them each block's identity
    for source capture. The identities are kept out of the rows, which custom
    templates may serialise whole (#131).

    Built here rather than per-template because disambiguation and nameless
    handling must not be able to diverge between them, and because a template
    resolving an anchor would be doing store IO from a render.

    Nothing is filtered out HERE. Filtering is each template's business, per
    block: `named_npc_count` is what the voice policy renders on, and dropping
    the anchorless here would switch that policy off in exactly the case it
    exists for -- a cast with no anchors yet.

    "Here" is doing work: a present NPC whose card or locked version cannot be
    read already dropped out of `npc_cards` (`_npc_cards`), and cannot
    contribute a block it has no data for. That is the pre-existing filter, not
    a new one.
    """
    blocks = []
    block_refs = []
    # strict=True is doing real work: `npc_ids` is appended only after its card
    # read succeeds, so the two lists staying aligned is an invariant rather
    # than an assumption, and a silent truncation here would attach one
    # character's anchor to another's name.
    for shown, card, char_id in zip(names, npc_cards, npc_ids, strict=True):
        parts = [_card_text(card, "description"), _card_text(card, "personality"),
                 _card_text(card, "scenario")]
        anchor = overlay.voice_anchor_record(cid, char_id)["text"]
        block_refs.append(f"characters:{char_id}")
        blocks.append({
            "name": shown,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(expand(anchor)),
            "example": actor.select_examples(expand(_card_text(card, "mes_example")),
                                              voice_anchors.VOICE_EXAMPLE_CAP, recent),
        })
    return blocks, block_refs


def _current_setting(cid: str, loc: str | None,
                     actor_ref: str | None) -> tuple[str, bool, frozenset]:
    """What renders as the Current setting: the scene's location's body, and
    whether it is secret -- plus the location world info must then hold back,
    which is the location itself once it has been read, rendered or not.

    `loc` is None when the scene is nowhere or the reader excluded where it
    is. A location deleted since the scene moved there omits the block and
    holds nothing back. `actor_ref` names an NPC's own call, which sees the
    setting only when it `actor.knows` the location.
    """
    if not loc:
        return "", False, frozenset()
    try:
        record = overlay.read_entity(cid, "locations", loc)
    except entities.EntityNotFound:
        return "", False, frozenset()  # referenced location was deleted — omit the setting block
    # The current location does NOT pass through `activate` -- it is
    # excluded from world info precisely so it can render as the
    # setting instead -- so the secrecy gate has to be applied here as
    # well, or "gm-only never enters the prompt" is false for the one
    # location the scene is actually standing in. Suppressing the block
    # leaves the model to invent a setting, which is the right way to
    # be wrong: the alternative is overriding the level the user set
    # because we decided they needed the description more.
    level = entities.normalize_secrecy(record["meta"].get("secrecy"))
    owners = [v.strip() for v in record["meta"].get("owners", "").split(",") if v.strip()]
    visible = actor_ref is None or actor.knows(
        {"secrecy": level, "owners": owners,
         "known_by": record["meta"].get("known_by", "")}, actor_ref)
    if level == entities.GM_ONLY or not visible:
        return "", False, frozenset({loc})
    return record["body"].strip(), level == entities.SECRET, frozenset({loc})


def _prose_style(style_id: str) -> tuple[str, str]:
    """The resolved prose style's name and body, or two blanks for none.

    resolve() already skips ids that don't exist; this also covers a file
    that exists but can't be read, which must not break generation either.
    """
    if not style_id:
        return "", ""
    try:
        style = styles.read_style(style_id)
    except (styles.StyleNotFound, OSError, UnicodeDecodeError):
        return "", ""
    return style["meta"]["name"], style["body"].strip()


def _lore(result: activation.Result, activated: list[dict], recalled: list[dict],
          names: dict[str, str]) -> dict:
    """The activated entries again as the engine's hits, for what renders
    World info per entry (`_render_sections`) and the inspector rows:
    priority, match age and the reason each one is in. Matched by identity, so
    an actor filter applied to `activated` and `recalled` is honoured here too.
    `names` resolves every ref a reason can name -- a record listed this turn,
    activated or not, or someone in the room."""
    activated_ids = {id(e) for e in activated}
    recalled_ids = {id(e) for e in recalled}
    return {"world_info": [h for h in result.keyword if id(h.entry) in activated_ids],
            "recalled": [h for h in result.recalled if id(h.entry) in recalled_ids],
            "held_back": list(result.held_back),
            "names": names}


def _history_parts(view: epistemic.EpistemicView, scene_cast: list[dict], actor_name: str,
                   *, actor_scoped: bool) -> dict:
    """The view's sections this reader may be shown: the narrator's two, or an
    NPC's own two, each named for the people in the room."""
    allowed = (history_section.ACTOR_SECTIONS if actor_scoped
               else history_section.NARRATOR_SECTIONS)
    return {k: v for k, v in history_section.parts(
        view, {actor.ref(a): a["name"] for a in scene_cast}, actor_name).items()
        if k in allowed}


#: The template data an NPC's own prompt never holds. See `_scope_to_actor`.
_NARRATOR_ONLY_KEYS = ("story_entries", "archive_entries", "plot_lines", "commitment_lines",
                       "group_states", "secret_group_states", "offscene_active", "offscene_known",
                       "players", "refs", "ref_names", "available_art", "history_evidence",
                       "history_withheld", "history_known", "history_suspected")


def _scope_to_actor(data: dict, cid: str, actor_ref: str, roster: list[dict],
                    history_parts: dict) -> None:
    """Take out of `data`, in place, what an assigned NPC's prompt may not hold.

    These fields have no actor attribution. In particular a recap or a group
    marked public does not make its private campaign state public.

    THE CONTRACT, and it stays even though `_campaign_view` no longer gathers
    most of these for an actor-scoped compose: that is an optimisation, and
    this is what an NPC's prompt is allowed to hold.
    """
    for key in _NARRATOR_ONLY_KEYS:
        data[key] = []
    # An NPC's history is set again from the view's visible slices alone:
    # whatever populated the keys above, nothing else reaches it.
    for section_id in history_section.ACTOR_SECTIONS:
        if section_id in history_parts:
            data[history_section.DATA_KEYS[section_id]] = list(history_parts[section_id].items)
    data["relationship_lines"] = actor.own_relationships(cid, actor_ref, roster)
    data["speaker"] = None
    data["today"] = None  # scheduled events have no knowledge attribution
    data["mechanics_sheets"] = [s for s in data["mechanics_sheets"] if s.get("ref") == actor_ref]
    data["mechanics_checks"] = [c for c in data["mechanics_checks"] if c.get("ref") == actor_ref]


def _post_history(cid: str, croot, cast: list[dict], npc_cards: list[dict],
                  response_actor: dict | None, *, visible: list[dict], budget: dict) -> str:
    """The post-history block, before macro expansion: the cards' own
    post-history instructions and the length and voice correctives.

    One assigned speaker per call makes whole-turn block limits irrelevant, so
    the length corrective measures only that speaker's recent prose against
    the continuation `budget`, and a call with no assigned speaker gets none.
    """
    drift = (length_drift.measure_contributions(visible, response_actor["name"], budget)
             if response_actor else None)
    length_correction = (prompts.render("scene/length_correction.j2", drift=drift, budget=budget)
                         if drift else "")
    voice_notes = cast_data._voice_notes(cid, croot, cast)
    voice_correction = (prompts.render("scene/voice_correction.j2", voice_notes=voice_notes)
                        if voice_notes else "")
    return prompts.render("scene/post_history.j2", npc_cards=npc_cards,
                          voice_correction=voice_correction,
                          length_correction=length_correction)


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


def _campaign_view(cid: str, sid: str, *, croot, cast: list[dict], recent_text: str,
                   birthday_text: str,
                   full_recap: int, activated_wi: list[dict], recalled_wi: list[dict],
                   art_loc: str | None, actor_scoped: bool,
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
        return {"offscene_active": [], "offscene_active_refs": [], "offscene_known": [], "available_art": [],
                "story_entries": [], "archive_entries": [], "history_evidence": [],
                "history_withheld": [], "history_known": [], "history_suspected": [], "plot_lines": [],
                "commitment_lines": [], "group_states": [], "secret_group_states": [],
                "relationship_lines": [], "today": None,
                "birthdates": birthdays.relevant(cid, birthday_text, excluded_refs)}
    offscene_active, offscene_known = cast_data._cast_directory_data(croot, cid, sid)
    # Identities ride beside the rows, which custom templates may serialise whole.
    offscene_active_refs = [a.pop("ref", "") for a in offscene_active]
    return {
        "offscene_active": offscene_active, "offscene_active_refs": offscene_active_refs,
        "offscene_known": offscene_known,
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
        "available_art": (art.catalogue(cid, cast, art_loc, activated_wi + recalled_wi, recent_text,
                                        scene=sid)
                          if _catalog._section_on("available_art") else []),
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


def _check_view(view: epistemic.EpistemicView, cid: str, sid: str,
                actor_ref: str | None) -> bool:
    """Refuse a history view built for another compose (spec 11 §6.4): another
    campaign, scene, scene identity or speaker -- narrator matching narrator.
    A mismatch is a programming error, raised before anything renders. A scene
    header that cannot be read just now (a synced folder mid-write) proves
    nothing either way, so it costs the turn its history, never the turn:
    False, and the compose goes on with none."""
    p = view.perspective
    actor_ = None if actor_ref in (None, "grimoire") else actor_ref
    if (p.cid, p.sid, p.actor) != (cid, sid, actor_):
        raise ValueError("history view was built for another compose")
    try:
        identity = scenes_identity.scene_identity_strict(cid, sid)
    except OSError:
        log.warning("History dropped from a compose: the scene header could not be read")
        return False
    if p.identity != identity:
        raise ValueError("history view was built for another compose")
    return True
