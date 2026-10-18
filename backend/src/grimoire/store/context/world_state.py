"""The world's own state, as the prompt sees it: world-info activation plus the
today / weather / character-state / group-state blocks. The character-state
block's POV filter -- which of an NPC's suspicions name someone on stage --
is `suspects`.

World-info selection is `activation.run`'s: `_world_info` gathers the entries
and calls it with the scene's posts, seed and present set. `activate` is a thin
one-window wrapper over the same engine for callers that hold only joined text.
Everything else here gathers the data one section renders from.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence

from .. import (
    calendars,
    characters,
    entities,
    entity_schema,
    events,
    group_refs,
    groupstate,
    lore_fields,
    membership,
    overlay,
    pcs,
    playstate,
    weather,
)
from ..appearances import versions as appearances_versions
from ..scenes import read as scenes_read
from . import activation, semantic, suspects

# Aliased because `_actor_aliases` and `_character_states` below bind `actor`.
from . import actor as actor_scope

# Aliased to match `assemble.py` and `macros.py`, and because `_character_states`
# below takes a parameter named `cast`.
from . import cast as cast_data

#: The ref fields §7.1's structural presence reads, on whichever kind declares
#: them (`entity_schema.FIELDS`); `activation` applies each to its own kind.
_STRUCTURAL_FIELDS = ("holder", "leader", "members", "headquarters", "habitat")


def keyword_hit(keys, text: str) -> bool:
    """Whole-word, case-insensitive: does any key appear in `text`?

    Factored out of `activate` so archive retrieval (`archive._archive_entries`)
    selects by exactly these semantics rather than a lookalike that drifts from
    them -- "pact" must keep not matching the key "pac" on both sides of the
    seam.
    """
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keys)


def _ref(e: dict) -> str:
    """An entry's pin ref (`"<kind>:<id>"`), as `store/pins.py` spells it.

    `.get` on both halves: `activate` is a pure function over whatever dicts it
    is handed, and a caller that supplies neither (several tests, and any future
    strategy assembling entries of its own) gets a ref that matches no rule
    rather than a KeyError.
    """
    return f"{e.get('kind')}:{e.get('id')}"


def activate(entries: list[dict], recent_text: str, present: frozenset = frozenset(),
             recall: Callable[[list[dict], str], list[dict]] | None = None,
             pinned_refs: frozenset = frozenset(),
             excluded_refs: frozenset = frozenset()) -> list[dict]:
    """Select world-info entries against one window of joined text: `activation.run`
    with `recent_text` as its only post, scan depth 1 and no recursion. Owned entries
    (owners non-empty) are silent unless an owner is in `present`, or is made present by
    an item, group or creature that activates; then keyless = always-on, keyed = the
    entry's key rule against `recent_text`. Unowned entries need only their keys.

    `secrecy: gm-only` (#49) is dropped here, before any other rule and before
    `recall` ever sees the entry: this gate -- `activation.run`, which this
    function wraps and `_world_info` calls directly -- is THE one every
    world-info entry passes through, so dropping it there is what makes "never enters the
    prompt" true of the keyword path, the always-on path and the similarity
    path at once, rather than of whichever one someone remembered. `secret`
    entries are selected exactly like public ones — the difference is entirely
    in how they render (see `secrecy_split`).

    `pinned_refs` / `excluded_refs` are the reader's own overrides (#129,
    resolved by `pins.active`), checked next — ahead of the owner gate, the
    keyword rule and `recall` alike. That ordering is the feature: those are
    guesses about what the scene needs, and a pin is the reader saying they
    already know.

    **A pin does not beat `gm-only`,** and the difference from the gates it does
    beat is the whole reason the order above is what it is. The owner gate and
    the keyword rule are CONDITIONAL — "not unless her owner is here", "not
    unless someone says the word" — and a pin is the reader answering the
    condition. `gm-only` is not a condition; it is the entry saying it is not
    for the model at all, which is a property of the record rather than of this
    turn. A pin that could override it would make pinning a way to leak the GM's
    own notes into the prompt, which is precisely what #49 exists to prevent.
    Pinning one is therefore inert, exactly like pinning a record the campaign
    has since deleted.

    A pin DOES beat the owner gate, which is the one override that costs
    something: an entry owned by a character who is not in the scene stays out
    so absent people's lore cannot leak into it. A pin names that entry
    explicitly, by a reader looking at their own campaign, so it goes in — but
    nothing else opens that gate, and an owned entry the reader has not named is
    as silent as it ever was.

    An excluded entry is dropped outright rather than merely failing the keyword
    rule: `recall` only ever sees what the keyword rule REJECTED, so leaving it
    in `missed` would let the second stage put back exactly what the reader
    asked to remove.

    `recall` is a second-stage retrieval strategy with `semantic.recall`'s
    signature -- a test's, since production does not come through here:
    `_world_info` hands the engine a lambda over `semantic.recall_scored` that
    adds `campaign=cid`, so the embed row is charged to the campaign. It is
    handed the entries the keyword rule *rejected*, and only after the owner
    gate has already admitted them: an entry whose owner is absent
    never reaches it, so no similarity score can leak owned lore. Its hits are
    appended, so this function's result is a superset of what the keyword rule
    alone returns, in the same order. Defaulting it to None keeps the plain
    three-argument call exactly as pure and as offline as it has always been.
    """
    # A thin wrapper over the engine (`activation.run`): the one window is
    # `recent_text`, handed over as a single post, so the engine's bitmap is
    # the joined-text rule exactly. An entry that carries no
    # `controls` is parsed for them off its own keys, so a caller's plain dict
    # behaves as a record with the same frontmatter would.
    #
    # `recall` keeps its old shape (entries in, entries out): the engine is
    # given a stand-in that only records what it offered, and the caller's
    # strategy is then handed exactly those entries, so its answer is appended
    # verbatim -- whatever it returns -- as it always was.
    given = [e if isinstance(e.get("controls"), lore_fields.Controls)
             else {**e, "controls": lore_fields.parse(e)} for e in entries]
    original = {id(g): e for g, e in zip(given, entries, strict=True)}
    missed: list[dict] = []

    def offered(candidates: list[dict], _text: str) -> list[tuple[dict, float]]:
        missed.extend(candidates)
        return []

    result = activation.run(given, [(0, recent_text)], "", {ref: {} for ref in present},
                            scan_depth=1, recursion_depth=0, current_location=None,
                            pinned_refs=pinned_refs, excluded_refs=excluded_refs,
                            recall=offered if recall is not None else None,
                            recall_text=recent_text)
    out = [original[id(h.entry)] for h in result.keyword]
    if recall is not None and missed:
        out.extend(recall([original[id(e)] for e in missed], recent_text))
    return out


def secrecy_split(entries: list[dict]) -> tuple[list[str], list[str]]:
    """`(public bodies, secret bodies)` for a list of activated entries.

    The two lists render as two blocks of one section rather than two sections:
    a secret is exactly as relevant to the turn as the public entry beside it,
    so it must live or die with that entry when the packer trims — a separate
    section could drop the secrets alone and leave the model narrating the
    scene they were the twist in.
    """
    public: list[str] = []
    secret: list[str] = []
    for e in entries:
        target = (secret if entities.normalize_secrecy(e.get("secrecy")) == entities.SECRET
                  else public)
        target.append(e["body"])
    return public, secret


def _world_info(cid: str, posts: Sequence[tuple[int, str]], seed: str, *,
                exclude: frozenset = frozenset(), present: Mapping[str, dict] | None = None,
                pinned_refs: frozenset = frozenset(), excluded_refs: frozenset = frozenset(),
                scan_depth: int, recursion_depth: int = 0, current_location: str | None = None,
                recall_text: str = "", actor_ref: str | None = None, sid: str = "",
                ) -> tuple[list[dict], list[dict], activation.Result, dict[str, str]]:
    """Activated lore/location/item/group/creature entries as
    {"body", "kind", "id", ...} dicts — _assemble renders the bodies and uses
    the refs (e.g. activated groups pull their campaign state into context).

    Returns ``(keyword, recalled, result, names)``: what the keys (and pins)
    selected, what semantic recall added on top, the engine's whole answer --
    every hit with its reason, what was held back, and the present set it grew
    -- for the inspector and the packer, and the display name of every record
    listed, activated or not, so a reason naming an item that never activated
    can still name it. Reasons never reach a prompt: the first two are the
    entries themselves. The two lists render as separate sections in separate
    packer tiers -- see the comment at the return statement.

    `posts` are the transcript's own `(index, text)` pairs, director notes
    included and posts hidden from context left out, and `seed` is this
    turn's un-persisted input (an opener's prompt, a director's note);
    `activation.run` reads them per entry window. `present` is the base present
    set, ref -> reason, built by the caller; the engine grows it structurally
    and by activation. `recall_text` is the joined scan
    window the similarity query has always used, and `sid` the scene whose
    turn it serves, which recall's embed row is charged to beside `cid`.

    Two different exclusions meet here, deliberately spelled apart. `exclude` is
    the CURRENT LOCATION, held back because the Current setting section already
    renders it; `excluded_refs` is the reader's own rule (#129), which applies to
    every kind. `pinned_refs` is its opposite, and both are enforced in
    `activation.run` — the skips in `_candidates` are only there to save
    reading a file whose body is about to be thrown away.

    `actor_ref` names an NPC's own call (spec §8.1): the candidates are then
    only what that actor `actor.knows`, so an entry it cannot know neither
    activates, pulls by recursion, confers presence by activating nor takes a
    recall slot. What is in the room stays the whole scene's: `present` is
    the scene's, and structural presence still reads every entry, so an item
    someone present holds is present -- and opens the gate on its public
    lore -- whether or not this NPC knows of the item. None, or the
    narrator, filters nothing."""
    entries, names = _candidates(cid, exclude=exclude, pinned_refs=pinned_refs,
                                 excluded_refs=excluded_refs)
    # On an NPC's call only what it knows is a candidate; the room is still
    # the scene's, so structural presence reads every entry (§8.1).
    candidates = (entries if actor_ref in (None, "grimoire")
                  else actor_scope.known_entries(entries, actor_ref))
    # The only production caller that supplies a second stage, so the strategy
    # is chosen in one visible place and `activation.run` stays a pure function
    # of its arguments. The attribute is resolved off the module on every call
    # (inside the lambda, not when it is built), which is what keeps
    # `semantic.recall_scored` patchable from a test. The lambda hands recall
    # the campaign and the scene its embed row is charged to: a turn's recall
    # is part of what playing that scene cost, and a row without the scene
    # would leave the scene's own totals short of it.
    #
    # The split is reported rather than merged because the two halves are
    # packed differently: keyword hits are `spotlight`, recalled ones are
    # `archive` (see assemble.SECTIONS). Merging them let a recall grow the
    # World info section until the packer dropped the whole thing, keyword
    # hits included -- so enabling recall could REMOVE lore, which is exactly
    # what this layer promises never to do.
    result = activation.run(candidates, posts, seed, present or {}, scan_depth=scan_depth,
                            recursion_depth=recursion_depth,
                            current_location=current_location, pinned_refs=pinned_refs,
                            excluded_refs=excluded_refs,
                            recall=lambda c, t: semantic.recall_scored(c, t, campaign=cid,
                                                                      scene=sid),
                            recall_text=recall_text, scene_entries=entries)
    return ([h.entry for h in result.keyword], [h.entry for h in result.recalled], result,
            names)


#: The record kinds world info reads, in the order their entries are offered.
_WORLD_INFO_KINDS = ("lore", "locations", "items", "groups", "creatures")


def _candidates(cid: str, *, exclude: frozenset, pinned_refs: frozenset,
                excluded_refs: frozenset) -> tuple[list[dict], dict[str, str]]:
    """Every record the campaign could put in world info, as the entry dicts
    `activation.run` reads, and the display name of every record listed that
    the reader has not excluded -- the current location and a keyless one
    included, so a reason naming one can still name it.

    `exclude` (the current location) and `excluded_refs` (the reader's rule)
    are skipped only to save reading a file whose body is about to be thrown
    away; `activation.run` enforces both. See `_world_info`.
    """
    entries = []
    names: dict[str, str] = {}
    groups = _group_snapshot(cid)
    group_records = {record.id: record for record in groups.records}
    for kind, meta in _listed(cid, groups):
        ref = f"{kind}:{meta['id']}"
        if ref in excluded_refs:
            continue
        names[ref] = str(meta.get("name") or meta["id"])
        if kind == "locations" and meta["id"] in exclude:
            # Held back even when pinned: `exclude` is the CURRENT location,
            # which the Current setting section is already rendering, so
            # honouring the pin here would print its body twice. What the
            # pin buys it is protection from the packer, in `_assemble`.
            continue
        record: dict = ({"meta": group_records[meta["id"]].meta, "body": group_records[meta["id"]].body}
                        if kind == "groups" else overlay.read_entity(cid, kind, meta["id"]))
        keys = [k.strip() for k in record["meta"].get("keys", "").split(",") if k.strip()]
        if kind == "locations" and not keys and ref not in pinned_refs:
            # A keyless location surfaces only as the current setting, never
            # always-on -- unless the reader pinned it, which is a request
            # for this location in the prompt whatever the scene is doing.
            continue
        entries.append(_entry(kind, meta["id"], record, keys,
                              groups.index.groups.get(ref) if kind == "groups" else None))
    return entries, names


def _listed(cid: str, groups: membership.GroupSnapshot) -> Iterator[tuple[str, dict]]:
    """`(kind, meta)` for every record of the world-info kinds, kind by kind.

    Groups come off `groups`, the snapshot their bodies are read from too;
    every other kind is listed through the overlay as its turn comes, so the
    listings and the reads interleave exactly as one loop per kind would.
    """
    for kind in _WORLD_INFO_KINDS:
        if kind == "groups":
            yield from ((kind, {**record.meta, "id": record.id}) for record in groups.records)
        else:
            yield from ((kind, meta) for meta in overlay.list_entities(cid, kind))


def _group_snapshot(cid: str) -> membership.GroupSnapshot:
    """The campaign's groups and their membership, read as one version.

    Membership and visibility must describe the SAME group version. Reading
    the inverse and then an entity body could combine old GM-only affiliates
    with a newly public empty roster and grant presence neither version had.
    A campaign that sees no group at all reads no identity ledger, so it
    composes exactly as before membership existed (spec 07 section 11.1),
    even with a damaged `deleted.json`/`detached.json` beside it.
    """
    try:
        return membership.campaign_snapshot(cid)
    except group_refs.GroupIdentityConflict:
        if overlay.list_entities(cid, "groups"):
            raise
        return membership.GroupSnapshot(index=membership.index_from_rows(()), records=())


def _entry(kind: str, eid: str, record: dict, keys: list[str], roster=None) -> dict:
    """One record as a world-info entry: its body, key rule, owners, secrecy,
    activation controls and the structural refs §7.1's presence reads. A
    group's leader and members are its `roster` in the membership index, not
    its own frontmatter."""
    meta = record["meta"]
    entry = {"body": record["body"].strip(), "keys": keys,
             "owners": [o.strip() for o in meta.get("owners", "").split(",") if o.strip()],
             "secrecy": entities.normalize_secrecy(meta.get("secrecy")),
             "kind": kind, "id": eid,
             "name": meta.get("name", eid),
             "controls": lore_fields.parse(meta),
             "refs": {f: entity_schema.parse_refs(meta.get(f)) for f in _STRUCTURAL_FIELDS}}
    if kind == "groups":
        entry["refs"]["leader"] = [roster.leader] if roster is not None and roster.leader else []
        entry["refs"]["members"] = list(roster.members) if roster is not None else []
    return entry


def _today_data(cid: str, sid: str, croot) -> dict | None:
    history = scenes_read.get_time_history(cid, sid)
    if not history:
        return None
    cfg = calendars.read_calendar(croot)
    try:
        facts = calendars.today_facts(cfg, history[-1])
    except calendars.CalendarError:
        return None  # garbled date — omit, don't crash
    # The campaign's own scheduled events (#101) beside the calendar's holidays.
    # Two sources, one section: a holiday recurs and belongs to the world's
    # calendar, an event happens once and belongs to this campaign, but to the
    # model reading this block they are both "what today is". `events.day_facts`
    # is where the campaign half lives — `today_facts` takes a calendar config
    # and has no business reading a campaign — and `events.sooner` is the merge
    # rule the two callers of it share, so the prompt and the suggestion
    # snapshot cannot disagree about what is next.
    scheduled = events.day_facts(cid, croot, history[-1])
    return {"friendly": facts["friendly"], "weekday": facts["weekday"],
            "secondary_friendly": facts["secondary_friendly"],
            "holidays_today": facts["holidays_today"],
            "events_today": scheduled["events_today"],
            "upcoming": events.sooner(facts["upcoming"], scheduled["upcoming"]),
            "cast": cast_data.cast_datetime_facts(cid, sid, history[-1])}


def _weather_data(cid: str, sid: str) -> dict | None:
    """The sky at the scene's current location and moment, or None.

    Tolerant by construction — `current_weather` returns None rather than
    raising for a missing location, a missing moment, or a stored moment the
    campaign's calendar can no longer parse.
    """
    locations = scenes_read.get_location_history(cid, sid)
    moments = scenes_read.get_time_history(cid, sid)
    got = weather.current_weather(cid, locations[-1] if locations else None,
                                  moments[-1] if moments else None)
    if not got:
        return None
    out = {k: got[k] for k in ("condition", "temperature", "wind")}
    # Authored notes ride along: the model gets "the Wintertide storm" rather
    # than only `storm`, which is why a note is stored at all.
    out["notes"] = got.get("notes") or []
    return out


def _npc_name(aroot, char_id: str) -> str:
    try:
        return characters.read_character(aroot, char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_aliases(aroot, cid: str, actor: dict) -> set[str]:
    """Every name a present actor is referred to by — the character's own meta
    name AND the name on the card/persona version locked into this scene.

    Both, because the two can differ and each is the one some part of the app
    uses. The meta name labels the `# Character state` block; the locked card's
    `data.name` is what the character-description section, the transcript and
    the cast UI show, and therefore what another NPC's stored `suspects` is
    likely to call them. Matching on either one alone leaves a hole in the
    opposite direction: with only the meta name, a suspicion written as "The
    Woman on the Pier is hiding the ledger" sails past a filter looking for
    "Seraphine"; with only the card name, an NPC's own interiority reads as
    being about somebody else and is withheld for nothing.

    Failure is per actor and silent: an actor whose card cannot be read
    contributes the names that could be resolved. Fewer aliases can only hide
    less, never leak more — the same one-directional bias the rest of this
    filter is built on — and the alternative is one unreadable card emptying the
    whole block.
    """
    kind, aid = actor["kind"], actor["id"]
    # A LIST, not a set, and the filtering happens once at the end. A card is
    # hand-editable and importable, so `data.name` can arrive as a list or an
    # object -- and `set.add` of an unhashable value raises TypeError, which
    # escaped this function into `_character_states`' outer catch and emptied
    # the state block for EVERY actor in the scene. This function's failure
    # policy is per actor (below); collecting first and validating after is what
    # makes that true for a malformed name as well as for an unreadable card.
    names: list = []
    vid = appearances_versions.locked_version(cid, kind, aid)
    if kind == "characters":
        names.append(_npc_name(aroot, aid))
        if vid:
            with contextlib.suppress(characters.CharacterNotFound, characters.VersionNotFound):
                names.append(characters.read_card(aroot, aid, vid)["data"].get("name", ""))
    elif kind == "pcs":
        # BOTH names here too, for the reason the character branch above takes
        # both: a PC's container name and its locked persona name can differ,
        # and taking only the persona left a suspicion written against the
        # canonical PC name unmatched. The asymmetry was an oversight, not a
        # judgement about PCs.
        with contextlib.suppress(pcs.PCNotFound):
            names.append(pcs.read_pc(aroot, aid)["meta"].get("name", ""))
        if vid:
            with contextlib.suppress(pcs.PCNotFound, pcs.PCVersionNotFound):
                names.append(pcs.read_persona(aroot, aid, vid).get("name", ""))
    return {n.strip() for n in names if isinstance(n, str) and n.strip()}


def _character_states(aroot, cid: str, cast, pcless: bool) -> list[dict]:
    """`aroot` is an `appearances.locked_actor_root` — `cast` comes from the
    appearance record, so both the campaign-local state.md and the actor's
    campaign-side copy are found under it.

    `pcless` drives the POV filter (#116). A pcless scene is the director's own
    view — there is no player whose knowledge the narration has to respect, and
    the whole point of an offscreen turn is to move NPCs by what they privately
    believe — so it gets full disclosure. A scene with a player in it does not:
    each present NPC's `suspects` is filtered to what is not about another
    present actor. `current_state` and `knows` are never filtered; they are the
    tier the narration is allowed to treat as true.

    Every present actor's names are resolved HERE, from the cast, rather than
    handed in by `assemble`. Its `npc_names` / `player_names` are one name each
    and the wrong one for this: they are the locked card's `data.name`, while
    the block is labelled with the character's meta name. Passing either list
    made the filter and the label disagree — see `_actor_aliases`, which
    resolves both names for each actor so neither hole is open.

    This is the coarse half of #116. Filtering by mentioned name is a heuristic
    standing in for an audience marked on the entry itself, and true
    player-facing POV needs a scene to record whose eyes it is behind (#86) and
    knowledge to be stored per-entry rather than as prose (#122). Both are
    prerequisites this deliberately does not invent a field for.
    """
    try:
        aliases = [(a, _actor_aliases(aroot, cid, a)) for a in cast]
        out = []
        for actor, own in aliases:
            if actor["role"] != "npc" or actor["kind"] != "characters":
                continue
            char_id = actor["id"]
            st = playstate.read_state(aroot, char_id)
            if not st:
                continue
            name = _npc_name(aroot, char_id)
            if not pcless:
                # Every FORM of every OTHER present actor, minus this one's
                # own — a character suspecting something about herself is her
                # own interiority, under whichever name she is written down by.
                #
                # Forms, not names, because the collision is between the
                # DERIVED ones: an owner called `Mara Chen` beside an actor
                # called `Mara Vance` shares the short alias `Mara`, and
                # subtracting stored names alone left it in `others` — so her
                # own "Mara Chen fears she made a mistake" matched the other
                # actor and was withheld, which is precisely the exception this
                # subtraction exists to make. `Mara Vance` in full still
                # withholds, because only the ambiguous form is dropped.
                #
                # Case-FOLDED, because that is the comparison `suspects._mentions` makes:
                # a multi-word form matches under IGNORECASE and a one-word form
                # matches any spelling that is not all lower case, so `MARA` and
                # `Mara` are one form to the matcher and have to be one form
                # here too. Subtracting exact strings left the other actor's
                # `Mara` in `others` for an owner written `MARA CHEN`, and the
                # protection silently did not apply to the pair it exists for.
                own_forms = {f.casefold() for f in suspects.forms(own)}
                others = {f for f in set().union(*(suspects.forms(names)
                                                   for other, names in aliases
                                                   if other is not actor), set())
                          if f.casefold() not in own_forms}
                st = {**st, "suspects": suspects.visible(st["suspects"], others)}
            if st["current_state"] or st["knows"] or st["suspects"]:
                out.append({"name": name, **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []


def _group_states(cid: str, croot, activated: list[dict],
                  secrecy: str = entities.PUBLIC) -> list[dict]:
    """State for each activated group at `secrecy` that has a state.md — same
    failure policy as _character_states: a garbled file omits the block, never
    crashes.

    Filtered by level rather than returning everything, because a group's state
    is the half of it worth hiding: `groupstate.FIELDS` ends in `secrets`. A
    secret group whose state rendered in the plain block would have its
    description labelled and its secrets published — the feature inverted. The
    caller asks twice and the section renders the two answers as two blocks
    (see `sections/group_state.j2`); `gm-only` groups are not in `activated` at
    all, so their state is already gone with them.
    """
    try:
        out = []
        for e in activated:
            if e["kind"] != "groups" or entities.normalize_secrecy(e.get("secrecy")) != secrecy:
                continue
            st = groupstate.read_state(croot, e["id"])
            if st and any(st[k] for k in groupstate.FIELDS):
                out.append({"name": e["name"], **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []
