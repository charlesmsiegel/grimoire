"""Cast-derived data: the campaign's offscreen player references, the two-tier
off-scene character directory, the drift-measurement roster, and per-actor
calendar facts.

Four of these five start from the appearance record -- the campaign roster or
the scene cast -- and resolve the actors it names campaign-side. `_char_name`
is the shared name lookup the directory builds on; it takes a root and reads no
appearance record of its own.
"""

from __future__ import annotations

import logging

from .. import calendars, characters, config, dossiers, overlay, pcs, voice_drift
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..appearances import transitions as appearances_transitions
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..scenes import serialize as scenes_serialize

log = logging.getLogger(__name__)

#: Omitted tier-3 names spelled out in the cap's log line before it summarises
#: the rest as a count. See `_scope_known`.
_LOGGED_DROPS = 12


def _campaign_player_refs(cid: str, aroot) -> tuple[list[dict], list[str]]:
    """(persona data dicts, names) of every campaign-level player actor, seated in
    the scene or not — the offscreen reference cast. Each dict carries "kind" so
    the persona-block templates pick the right format.

    `aroot` is an `appearances.locked_actor_root`: the roster is the appearance
    record, so every actor here has a campaign-side copy."""
    refs: list[dict] = []
    names: list[str] = []
    for a in appearances_cast.roster(cid):
        if a["role"] != "player":
            continue
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(aroot, a["id"], a["version"])
                refs.append({"kind": "pcs", **p})
                names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(aroot, a["id"], a["version"])["data"]
                refs.append({"kind": "characters", **data})
                names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    return refs, names


def _char_name(root, cid: str) -> str:
    try:
        return characters.read_character(root, cid)["meta"]["name"]
    except characters.CharacterNotFound:
        return cid


def _known_limit() -> int:
    """Characters tier 3 may name; 0 = no ceiling. A hand-edited config.md
    holding nonsense falls back to the DEFAULT rather than to unbounded, unlike
    `pack.budget_tokens`: there, falling back to "no ceiling" restores the
    behaviour every install had before budgets existed, while here it restores
    the unbounded listing this setting exists to bound."""
    try:
        return max(int(config.read_config().get("offscene_known_limit",
                                                config.DEFAULT_OFFSCENE_KNOWN_LIMIT)), 0)
    except (TypeError, ValueError):
        return int(config.DEFAULT_OFFSCENE_KNOWN_LIMIT)


def _scope_known(cid: str, sid: str, known: list[dict], limit: int) -> list[dict]:
    """Tier 3, cut to `limit` entries — relevance decides who survives, the
    directory's own order decides how they read.

    Tier 3 is every character the campaign can see that has a tagline and has
    never been cast, so it grows with the WORLD rather than with the campaign
    and had no bound at all (#3). A flat alphabetical cut would be the cheapest
    bound and the wrong one: it drops the character this scene is about because
    their id sorts late.

    So the ceiling is spent on relevance first. `appearances.suggestions` is
    the signal already in the codebase for "the in-scene cast's cards name this
    character", which is the same question asked one panel over — reused rather
    than re-derived so the directory and the suggestion rail cannot disagree
    about who is relevant to a scene.

    Two things this deliberately does NOT do:

    - reorder the survivors. Selection is by relevance; rendering stays in the
      directory's natural (id) order, so a store under the ceiling renders
      byte-identically to before this existed and only a store OVER it sees any
      change at all.
    - drop anyone quietly. The omitted names go to the log, because a directory
      that is silently partial is indistinguishable to the reader from a world
      that is smaller than it is.

    And one thing it deliberately does not tell the MODEL. The section is
    headed "Other characters in this world" and, past the ceiling, that is no
    longer the whole of them -- so the obvious move is a trailing "…and N
    others". It is not made, on purpose: the line beneath the heading asks the
    model to introduce these people only if the story calls for it, and telling
    it that hundreds more exist that it cannot see invites exactly the invented
    cast that instruction is there to prevent. The omission is surfaced to the
    USER (this log, and the section's own token row in the inspector), who can
    raise the ceiling; it is not surfaced to the model, which can only guess.
    """
    try:
        mentioned = {s["character"] for s in appearances_transitions.suggestions(cid, sid)}
    except Exception:
        # Blind on purpose, and carrying no `noqa`: BLE001 exempts a handler
        # that logs with `exc_info`, which is exactly the bargain here -- the
        # failure is swallowed for the caller and kept in full for whoever
        # reads the log.
        #
        # The relevance signal reads one card per in-scene actor, and this runs
        # on the generation hot path. An unreadable card must cost the ceiling
        # its ordering, not cost the turn its prompt: fall back to the natural
        # order, still capped. The bound is the feature; the ranking is the
        # refinement.
        log.warning("off-scene cast: relevance scan failed for %s/%s; "
                    "capping tier 3 in directory order", cid, sid, exc_info=True)
        mentioned = set()
    # Stable by construction: `sorted` is stable and the tiebreak is the index,
    # so within each of the two groups the directory's own order survives.
    keep = set(sorted(range(len(known)),
                      key=lambda i: (known[i]["id"] not in mentioned, i))[:limit])
    dropped = [k["name"] for i, k in enumerate(known) if i not in keep]
    # Named, but not all of them: this runs on every generated turn, and the
    # worlds that trip the ceiling are exactly the ones with hundreds to name.
    # The count is the number that matters and is always exact; the names are a
    # readable sample, built small rather than joined in full and then thrown at
    # a handler that may not even be enabled.
    shown = ", ".join(dropped[:_LOGGED_DROPS])
    if len(dropped) > _LOGGED_DROPS:
        shown += f", … and {len(dropped) - _LOGGED_DROPS} more"
    log.info("off-scene cast: %s/%s tier 3 capped to %d of %d; omitted %s",
             cid, sid, limit, len(known), shown)
    return [k for i, k in enumerate(known) if i in keep]


def _cast_directory_data(croot, cid: str, sid: str) -> tuple[list[dict], list[dict]]:
    """Off-scene cast data for the two-tier directory (the template renders the text):
    campaign-active characters (dossier paragraph) and every other world character
    (tagline + available versions), the latter bounded by `_known_limit`."""
    present = {a["id"] for a in appearances_cast.scene_cast(cid, sid) if a["kind"] == "characters"}
    roster = appearances_cast.roster(cid)
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
        # ONE read for both the name and the version list, off one resolved
        # root. This used to be two `read_character` calls on the same file
        # (`_char_name` makes its own) plus three `char_root` resolutions, per
        # candidate, on every generated turn -- and tier 3 iterates the whole
        # world, which is the very growth this section is being bounded for.
        # Deliberately unguarded, exactly as before: the old pair read versions
        # FIRST and unguarded, so a character that vanished between the listing
        # and this read raised then and raises now. `_char_name`'s
        # CharacterNotFound fallback was unreachable here for that reason, and
        # this is a read-once refactor -- turning that raise into a degraded
        # entry would be a different change, with its own test.
        record = characters.read_character(overlay.char_root(cid, char_id), char_id)
        known.append({"id": char_id, "name": record["meta"]["name"],
                      "tagline": tag,
                      "versions": [v["id"] for v in record["versions"]]})
    # Asked only when it can bite: `_scope_known` reads a card per in-scene
    # actor to rank the tail, and a campaign inside the ceiling has no tail.
    limit = _known_limit()
    if limit and len(known) > limit:
        known = _scope_known(cid, sid, known, limit)
    return active, known


def _voice_notes(cid: str, croot, cast: list[dict]) -> list[dict]:
    """Unresolved voice-drift correctives for the NPCs actually on screen (#59).

    Present-cast only, and NPC-only: the corrective is an instruction about the
    voice the model is about to write, so a flag on a character who is not in
    this scene has nothing to correct, and a player character's voice is the
    user's to drift.

    A flag is honoured only while the anchor that produced it is STILL THE
    CURRENT ONE. Two things follow from that, and they are the same rule:

    - No anchor at all -> silence. Removing the anchor is the documented way to
      opt a character out, and absorb stops judging them the moment it goes, so
      the flag would otherwise have no path back to cleared.
    - A DIFFERENT anchor -> silence. absorb's apply-time fingerprint only covers
      the pending-review window; a committed flag outlives it, so without this
      a note would go on citing a standard the user has since replaced (and a
      removed-then-restored anchor would resurrect it) until some later absorb
      happened to clear it.

    Both are read second, and only for a flagged character, so the common case
    (no flag) costs nothing on the generation hot path.

    Ordered by the cast, not by the flag store, so the note list reads in the
    same order as the cards above it.

    Named by `scene_cast`'s `name` -- the LOCKED VERSION's card name -- and not
    by the character container's, which can differ. This corrective is read by
    the same model that is holding the NPC cards and the transcript, and both of
    those identify the character by the card name. Addressing it to any other
    string invites the model to ignore it, or in a multi-NPC scene to apply it
    to the wrong character.
    """
    # A corrective addresses the model by NAME, so it needs that name to identify
    # exactly one actor on screen. The absorb-time clash guard cannot cover this:
    # a flag committed while the name was unique is consumed by every later
    # generation, and a same-named actor joining afterwards (or a locked card
    # renamed into a collision) makes the standing note ambiguous with no absorb
    # in between to re-examine it. Suppress rather than guess -- an instruction
    # the model applies to the wrong character is worse than none.
    #
    # Plus the reserved labels absorb seeds: these are what the transcript calls
    # the user's lines and unstamped narration, so a corrective addressed to a
    # character wearing one can be applied to the player instead. Seeded here
    # too because a rename AFTER the flag was committed never passes through
    # absorb's guard again.
    present = [a["name"] for a in cast if isinstance(a.get("name"), str) and a["name"].strip()]
    present += ["You", "Grimoire"]

    # Cards come off the APPEARANCE-RECORD root, not the bare campaign root the
    # flags use. Same directory, but only one of those reads is legitimate
    # campaign-side: `cast` is a `scene_cast` result, so every actor in it is
    # locked and its card was copied into the campaign tree -- the documented
    # exception `locked_actor_root` names. A raw-croot card read is the mistake
    # the overlay exists to prevent, and the guard flags it as such.
    aroot = appearances_paths.locked_actor_root(cid)
    out: list[dict] = []
    for a in cast:
        if a["kind"] != "characters" or a["role"] != "npc":
            continue
        # The RAW card name, like the judge reads (routes/scenes.py). `a["name"]`
        # substitutes the actor id for a card carrying no usable one, and a slug
        # is not a name the model can match to anything: the NPC card in front of
        # it has no `name` at all, so a corrective addressed to the slug is an
        # instruction about nobody. Suppress rather than address a stranger.
        try:
            vid = appearances_versions.locked_version(cid, "characters", a["id"])
            data = characters.read_card(aroot, a["id"], vid).get("data")
        except Exception:  # noqa: BLE001 -- an unreadable card owes no corrective
            continue
        name = data.get("name") if isinstance(data, dict) else None
        # `scenes.confusable` rather than a whole-name comparison, for the same
        # reason absorb uses it: "Winifred Vance" and "Winifred Vale" are
        # distinct strings, but neither owns the label "Winifred" -- and the
        # model reading this corrective is holding both their cards.
        if not isinstance(name, str) or scenes_serialize.confusable(name, present):
            continue
        # ONE read for note and provenance: the flag is replaced atomically, so
        # reading them separately can straddle a chronicle save and validate a
        # stale note against the fresh fingerprint (see voice_drift.read_record).
        flag = voice_drift.read_record(croot, a["id"])
        if not flag["note"]:
            continue
        record = overlay.voice_anchor_record(cid, a["id"])
        if not record["text"]:
            continue
        # "" is "provenance not recorded" (a flag predating the field), which
        # counts as valid -- invalidating on it would retire real user data on
        # upgrade.
        if flag["anchor"] and not voice_drift.fingerprint_matches(
                flag["anchor"], record["text"], record["id"]):
            continue
        out.append({"name": name, "note": flag["note"]})
    return out


def _drift_roster(cid: str, npc_names: list[str], player_names: list[str]) -> list[str]:
    """Names drift measurement canonicalizes speaker labels against.

    The present cast plus the CAMPAIGN roster — actors that have appeared in this
    campaign, which `appearances.roster_names` keeps after they leave a scene.
    The window reaches back three turns, so a departed character still has blocks
    in it; dropping their name would split "Winifred" and "Winifred Vance" into
    two speakers on an ordinary departure, inventing a `speakers` violation while
    hiding the real `blocks_per_speaker` one.

    Deliberately NOT every character the campaign can see. Pulling in inherited
    world characters who were never in this campaign's history adds names that
    cannot appear in the measured turns but can still collide: an unrelated
    "Winifred Vale" elsewhere in the world would make the label "Winifred"
    ambiguous and break canonicalization for the Winifred actually on screen.

    Deduplicated, and that is load-bearing: scenes.match_name resolves an exact
    match only when it is UNIQUE, so a name appearing twice in this list would
    make it return None and silently disable canonicalization for that character.
    """
    names = list(npc_names) + list(player_names) + appearances_cast.roster_names(cid)
    return list(dict.fromkeys(n for n in names if n))


def cast_datetime_facts(cid: str, sid: str, native: str) -> list[dict]:
    """Age / birthday-today for each in-scene actor that has a birthdate. Others skipped."""
    croot = campaigns_paths.campaign_root(cid)          # calendar.json is campaign-local
    aroot = appearances_paths.locked_actor_root(cid)    # cast actors are locked, so campaign-side
    cfg = calendars.read_calendar(croot)
    provider = calendars.get_provider(cfg["primary"])
    out: list[dict] = []
    for a in appearances_cast.scene_cast(cid, sid):
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                persona = pcs.read_persona(aroot, a["id"], vid)
                birth, name = persona.get("birthdate", ""), persona.get("name", a["id"])
            else:
                meta = characters.read_character(aroot, a["id"])["meta"]
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


def voice_safe_names(names: list[str], players=()) -> list[str]:
    """Display names for the cast blocks, blanked where they cannot attribute.

    `names` arrive stripped, `""` for a card with no name. A name shared
    EXACTLY (case-folded) with another present card is blanked too, which
    suppresses that character's anchor and example blocks -- the voice
    templates skip nameless entries -- while their description still renders,
    headerless, exactly as it did before any of this.

    Why blank rather than disambiguate. An earlier revision invented labels
    here (`Winifred #1`, `Winifred #2`) and that is worse than it looks: the
    transcript identifies speakers by card name and nothing else, so a model
    copying a heading into its `**<Name>:**` marker would persist `Winifred #1`
    into the scene -- and a transcript is the one artifact in this app that
    cannot be regenerated. The ordinals bought nothing to set against that,
    because `match_name("Winifred", ["Winifred", "Winifred"])` is already
    `None`: the duplicate case was never routable, and an alias would have made
    an unroutable label a synthetic one as well.

    It also matches how the rest of the codebase treats this case. Two present
    NPCs wearing one name cannot be told apart in the prose, so `_voice_notes`
    suppresses a corrective addressed to them and the absorb stage reports the
    clash instead of judging it. Saying nothing beats saying it under a name
    that means both of them.

    A name the PLAYER's would swallow is blanked too, and that case is worse
    than the duplicate one rather than merely similar. `split_reply` routes a
    block whose label resolves to a player straight to the narrator -- "never
    store a forged player line" -- so an NPC sharing the player's name, given a
    heading the model then echoed, would have its dialogue persisted as
    unattributed narration with the speaker gone. The test is
    `serialize.match_name` against the present players, which is exactly the
    predicate `_speaker_and_role` applies when deciding that, so this cannot
    disagree with the persistence path about who a label names.

    So is a name the SERIALIZER cannot write back, which is the same failure
    once more and wider than a reserved-label check catches. `label_preserved`
    is the predicate for it, and its docstring names this caller: a label
    holding `*` or a newline, longer than 64 characters, or colliding with a
    reserved label in its sub-speaker form -- `You (Mara)` is stored and read
    back as plain "Mara" with the USER role, filing an NPC's dialogue under the
    player. Asking `n in RESERVED_LABELS` caught only the bare form and missed
    every one of those.

    EXACT duplication for the NPC-vs-NPC case, deliberately NOT
    `serialize.confusable` -- which is the
    wrong tool here for the second distinct reason. It answers whether some
    label that could name this actor is ambiguous, so it is `True` for
    "Winifred Vance" beside "Winifred Vale". But those are the headings that
    actually render, and `match_name` resolves each of them exactly; only the
    bare "Winifred" is ambiguous, and nothing writes that. Blanking them would
    cost two distinguishable characters their voices to prevent nothing.
    """
    folded = [n.casefold() for n in names]
    out = []
    for i, n in enumerate(names):
        if not n or folded.count(folded[i]) > 1:
            out.append("")                      # nameless, or shared with another NPC
        elif scenes_serialize.match_name(n, players):
            out.append("")                      # the player's label would swallow it
        elif not scenes_serialize.label_preserved(n):
            out.append("")                      # the transcript cannot carry it back
        else:
            out.append(n)
    return out
