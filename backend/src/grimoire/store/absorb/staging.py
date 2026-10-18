"""Actor and entity proposal builders, using overlay-aware stored records.

Each section retains its own omission, existence and no-op rules. The caller
supplies the one common citation/identity stamp, so building a record proposal
does not create a second interpretation of review policy.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .. import characters, entities, groupstate, overlay, pcs, playstate, relationships
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..paths import slugify
from . import membership, parse

Stamp = Callable[..., dict]
_CARD_FIELDS = ("description", "personality", "scenario")
_NEW_RECORD_NOUNS = {"locations": "location", "lore": "lore entry", "items": "item",
                     "groups": "group", "creatures": "creature"}

#: Every world record a `lore_edits` append may land on, in the order a BARE id
#: is tried. All five of `entities.ENTITY_KINDS` (#224): a body append is the
#: only edit that can evolve a record of any kind, and resolving just the two
#: left items, groups and creatures unevolvable — a group could only ever move
#: through its campaign-side state, and an item or creature was static from the
#: day it was created.
#:
#: A fixed order rather than ENTITY_KINDS' own, because ids are per-kind
#: directories: `lore/the-ledger` and `items/the-ledger` are two records, and a
#: bare id has to pick one. Lore and locations lead in the order they were tried
#: before the other three joined them, so an append that has been landing on a
#: lore entry cannot start landing on a same-slugged location instead. The pick
#: is not hidden from the reviewer either way — the staged label names the kind
#: it resolved to.
#:
#: A model that means one of the others says so: `<kind>/<id>` (or `<kind>:<id>`,
#: the form `group_state_edits` already accepts) resolves in that kind alone.
#: `test_absorb_store.py` holds this tuple to ENTITY_KINDS, so a sixth kind
#: cannot join the store and quietly arrive unevolvable the way these three did.
APPEND_KINDS: tuple[str, ...] = ("lore", "locations", "items", "groups", "creatures")




def _char_name(cid: str, char_id: str) -> str:
    """Overlay-aware: a thin campaign's NPC is usually still inherited (never
    materialized croot-side), so the display name must resolve across the union."""
    try:
        return characters.read_character(overlay.char_root(cid, char_id), char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_exists(cid: str, token: str) -> bool:
    """Overlay-aware: a thin campaign's cast is mostly inherited (never
    materialized croot-side), so existence must be checked across the union,
    not just the campaign's own copy."""
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(overlay.pc_root(cid, aid), aid)
        elif kind == "characters":
            characters.read_character(overlay.char_root(cid, aid), aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False


def _entity_target(cid: str, raw_id: str) -> tuple[str, str, dict] | None:
    """`(kind, eid, record)` for a `lore_edits` id, or None when nothing answers.

    A `<kind>/<id>`-qualified id is resolved in that kind ONLY. Falling back to
    the bare scan when the named kind has no such record would answer a
    question the model did not ask: it named a kind because the id alone is
    ambiguous, so a miss there means the record it meant does not exist, not
    that some other kind's same-slugged record will do.

    The record comes back with the pair rather than being re-read by the
    caller, because resolving is not free: a bare id that misses is up to five
    overlay reads, and each croot miss reads the campaign's tombstone list
    before falling through to the world.
    """
    kind, sep, rest = raw_id.partition("/")
    if not sep:
        kind, sep, rest = raw_id.partition(":")
    if sep and rest and kind in APPEND_KINDS:
        candidates: tuple[tuple[str, str], ...] = ((kind, rest),)
    else:
        candidates = tuple((k, raw_id) for k in APPEND_KINDS)
    for k, eid in candidates:
        try:
            return k, eid, overlay.read_entity(cid, k, eid)
        except entities.EntityNotFound:
            continue
    return None


def _pc_name(cid: str, pid: str) -> str | None:
    """The persona name at the version the campaign locked, or None when the PC
    is not in the appearance record or its persona will not read -- which is
    what a state edit for a PC requires, the way a character's requires its
    card."""
    vid = appearances_versions.locked_version(cid, "pcs", pid)
    if vid is None:
        return None
    try:
        persona = pcs.read_persona(overlay.pc_root(cid, pid), pid, vid)
    except (pcs.PCNotFound, pcs.PCVersionNotFound):
        return None
    name = persona.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else pid


def _character_state_edit(cid: str, kind: str, char_id: str, before: str, after: str,
                          name: str | None = None) -> dict:
    """A `character_state` row for a character or, with `kind="pcs"`, a player
    character. The id keeps its old spelling for characters, so a review staged
    before PCs had state still names the same row."""
    eid = f"character_state:{char_id}" if kind == "characters" else f"character_state:pcs:{char_id}"
    label = name or (_char_name(cid, char_id) if kind == "characters" else char_id)
    return {"id": eid, "kind": "character_state",
            "target": {"kind": kind, "id": char_id},
            "label": f"{label} — current state",
            "field": "current_state",
            "before": before, "after": after, "authored": False}


def _state_actor(cid: str, raw_id: str) -> tuple[str, str, str | None] | None:
    """Resolve a state subject without materializing an inherited actor."""
    if not raw_id:
        return None
    # The model echoes ids from the "Present: <kind>/<id>, ..." context line (or,
    # less reliably, a bare id) — strip any "characters/", "characters:", "pcs/" or
    # "pcs:" prefix so every form resolves. A bare id is a character's. A PC keeps
    # its current state only (Knows/Suspects stay non-player), filed under pcs/ so
    # it is never misfiled under characters/ with the PC's id as a character slug.
    kind, sep, rest = raw_id.partition("/")
    if not sep:
        kind, _, rest = raw_id.partition(":")
    if kind not in ("characters", "pcs"):
        kind, rest = "characters", raw_id
    char_id = rest
    name: str | None = None
    if kind == "pcs":
        name = _pc_name(cid, char_id)
        if name is None:
            return None
    else:
        try:
            # overlay-aware: a thin campaign's NPC is usually still inherited
            # (never appeared/materialized), and a state edit for it must not
            # be silently dropped just because croot lacks the character dir
            characters.read_character(overlay.char_root(cid, char_id), char_id)
        except characters.CharacterNotFound:
            return None
    return kind, char_id, name


def character_states(cid: str, croot: Path, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage NPC knowledge and PC state without losing omit versus clear."""
    out: list[dict] = []
    for e in parsed.get("character_state_edits", []):
        actor = _state_actor(cid, e.get("id", ""))
        if actor is None:
            continue
        kind, char_id, name = actor
        st = playstate.read_state(croot, char_id, kind)
        if kind == "pcs":
            cur_knows = cur_suspects = knows = suspects = ""
        else:
            cur_knows = st["knows"] if st else ""
            cur_suspects = st["suspects"] if st else ""
            # Keep-on-omit: an omitted knows/suspects preserves the stored value; an
            # explicit "" clears it. Prevents an absorb that only touches
            # current_state from silently erasing established knowledge.
            knows = e.get("knows", cur_knows)
            suspects = e.get("suspects", cur_suspects)
        after = playstate.compose_body(e.get("current_state", st["current_state"] if st else ""), knows, suspects)
        before = playstate.compose_body(st["current_state"], cur_knows, cur_suspects) if st else ""
        # Explicit empty strings clear even the last standing field (spec07).
        # Raw parsing removes null/omitted state fields before this boundary,
        # so a generated nullable contract cannot erase a snapshot by silence.
        clears = any(field in e for field in ("current_state", "knows", "suspects")) and bool(before)
        if not after and not clears:
            continue
        if before == after:
            continue
        out.append(stamp(_character_state_edit(cid, kind, char_id, before, after, name),
                           e, f"{kind}:{char_id}"))

    return out


def group_states(cid: str, croot: Path, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage each group state against its overlay-aware record."""
    out: list[dict] = []
    for e in parsed.get("group_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        gid = rest if kind == "groups" else raw_id
        try:
            name = overlay.read_entity(cid, "groups", gid)["meta"].get("name", gid)
        except entities.EntityNotFound:
            continue
        st = groupstate.read_state(croot, gid)
        cur = {k: (st[k] if st else "") for k in groupstate.FIELDS}
        new = {k: (e[k] if k in e else cur[k]) for k in groupstate.FIELDS}
        after = groupstate.compose_body(new)
        before = groupstate.compose_body(cur) if st else ""
        if before == after:
            continue
        out.append(stamp({"id": f"group_state:{gid}", "kind": "group_state",
                            "target": {"kind": "groups", "id": gid},
                            "label": f"{name} — group state", "field": "group_state",
                            "before": before, "after": after, "authored": False}, e))

    return out


def memberships(cid: str, sid: str, parsed: dict, membership_capture: membership.Capture | None, stamp: Stamp) -> list[dict]:
    """Stage only captured, present actors against their leased group snapshot."""
    out: list[dict] = []
    if membership_capture is not None:
        # Spec 07 section 9.3. Every drop is tolerated, not an error: a group
        # this review did not capture (or that is gm-only), an actor that is not
        # a referenceable, existing character or PC, a no-op, a repeat.
        seen_memberships = set()
        # Only an actor on the scene's Present line can be proposed (9.7): the
        # transcript can give evidence about nobody else.
        present = {f"{a['kind']}:{a['id']}" for a in appearances_cast.scene_cast(cid, sid)}
        for row in parsed.get("membership_changes", []):
            ref = membership.group_ref(row.get("group"))
            actor = membership.actor_ref(row.get("actor"))
            if ref is None or ref not in membership_capture.leases or actor is None:
                continue
            if (actor not in membership_capture.actors or actor not in present
                    or (ref, actor) in seen_memberships):
                continue
            seen_memberships.add((ref, actor))
            roster = membership_capture.snapshot.index.groups[ref]
            before = membership.PRESENT if actor in roster.members else membership.ABSENT
            after = membership.PRESENT if row["change"] == "join" else membership.ABSENT
            if before == after:
                continue
            gid = ref.partition(":")[2]
            verb = "joins" if row["change"] == "join" else "leaves"
            out.append(stamp({"id": f"membership:{gid}:{actor}", "kind": "membership",
                "target": {"kind": "groups", "id": gid},
                "label": f"{roster.name} — {relationships.actor_name(cid, actor)} {verb}",
                "field": f"members:{actor}",
                "before": before, "after": after, "authored": False,
                "payload": {"group": gid, "actor": actor, "change": row["change"],
                            "group_lifecycle_token": membership_capture.leases[ref].token}}, row, actor))

    return out


def lore_appends(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Resolve body appends in the declared record-kind order."""
    out: list[dict] = []
    for e in parsed.get("lore_edits", []):
        raw_id, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not raw_id or not append:
            continue
        target = _entity_target(cid, raw_id)
        if not target:
            continue
        kind, eid, ent = target
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        # The staged id carries the KIND as well as the record's, because two
        # kinds can hold the same slug and a qualified id can reach both of them
        # in one absorb. The reviewer's panel keys a row by this id (its React
        # key, and the lookup that hangs a contradiction warning on it), so two
        # rows sharing one would hand one record's warning to the other's row.
        out.append(stamp({"id": f"lore:{kind}/{eid}", "kind": "lore",
                            "target": {"kind": kind, "id": eid},
                            "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                            "before": before, "after": after, "authored": False}, e))

    return out


def authored(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage the locked card field with its actor as citation subject."""
    out: list[dict] = []
    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances_versions.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            # locked_version returned a version, so the actor is in the appearance
            # record and its card is materialized campaign-side
            before = characters.read_card(appearances_paths.locked_actor_root(cid),
                                          char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append(stamp({"id": f"authored:{char_id}:{field}", "kind": "authored",
                            "target": {"kind": "characters", "id": char_id},
                            "label": f"{_char_name(cid, char_id)} — {field} (card edit)",
                            "field": field, "before": before, "after": text, "authored": True},
                           e, f"characters:{char_id}"))

    return out


def feelings(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage directed feelings with only their owner as citation subject."""
    out: list[dict] = []
    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(cid, frm) or not _actor_exists(cid, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        # `frm` alone is the subject: the feeling is the FROM side's, so the TO
        # side describing it is a third party's read of somebody else's heart.
        out.append(stamp({"id": f"feeling:{relationships.feeling_key(frm, to)}",
                            "kind": "relationship",
                            "target": {"kind": "relationships",
                                       "id": relationships.feeling_key(frm, to)},
                            "label": f"{relationships.actor_name(cid, frm)} → {relationships.actor_name(cid, to)}",
                            "field": "feeling", "before": before, "after": after,
                            "authored": False, "payload": payload}, e, frm))

    return out


def bonds(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage shared bonds with both actors as citation subjects."""
    out: list[dict] = []
    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(cid, a_tok) or not _actor_exists(cid, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        # Both ends are subjects, unlike a feeling: a bond is the pair's shared
        # relationship type, so either of them naming it is first-hand.
        out.append(stamp({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                            "target": {"kind": "relationships",
                                       "id": relationships.bond_key(a_tok, b_tok)},
                            "label": f"{relationships.actor_name(cid, a_tok)} & {relationships.actor_name(cid, b_tok)}",
                            "field": "bond", "before": before, "after": typ, "authored": False,
                            "payload": {"a": a_tok, "b": b_tok, "type": typ}},
                           e, a_tok, b_tok))

    return out


def new_characters(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage emergent characters without duplicating a campaign roster record."""
    out: list[dict] = []
    # Names only, so the roster: the full listing's per-character image scan
    # would be read here for nothing.
    existing_char_names = {c["name"].strip().lower() for c in overlay.character_roster(cid)}
    for e in parsed.get("new_characters", []):
        name = (e.get("name", "") or "").strip()
        description = (e.get("description", "") or "").strip()
        if not name or not description:
            continue
        if name.lower() in existing_char_names:
            continue
        candidate_id = slugify(name)
        try:
            characters.read_character(overlay.char_root(cid, candidate_id), candidate_id)
            continue  # id already taken -- treat as the same character
        except characters.CharacterNotFound:
            pass
        # The reviewed description is the W++ block plus the generated history, so the
        # staged diff shows the full text that lands in the card's description field.
        history = (e.get("history", "") or "").strip()
        after = f"{description}\n\n{history}" if history else description
        # No subject: the person has no record yet, so nothing they said in the
        # scene can be first-hand ABOUT a record. Their own lines still
        # corroborate the citation, which is what separates a proposal drawn
        # from dialogue from one drawn from nowhere.
        out.append(stamp({"id": f"new_character:{candidate_id}", "kind": "new_character",
                            "target": {"kind": "characters", "id": ""},
                            "label": f"New character — {name}", "field": "description",
                            "before": "", "after": after, "authored": False,
                            "payload": {"name": name, "sd_prompt": e.get("sd_prompt", ""),
                                        "personality": e.get("personality", ""),
                                        "mes_example": e.get("mes_example", ""),
                                        "evidence": e.get("evidence", ""),
                                        "confidence": parse._confidence(e.get("confidence", "")),
                                        "open_questions": e.get("open_questions", "")}}, e))

    return out


def _new_entity(cid: str, e: dict, kind: str, prefix: str, existing: set[str]) -> dict | None:
    """Build one new entity only when its name and id are both available."""
    label_noun = _NEW_RECORD_NOUNS[kind]
    name = (e.get("name", "") or "").strip()
    body = (e.get("body", "") or "").strip()
    if not name or not body:
        return None
    if name.lower() in existing:
        return None
    candidate_id = slugify(name)
    try:
        overlay.read_entity(cid, kind, candidate_id)
        return None
    except entities.EntityNotFound:
        pass
    payload = {"name": name, "keys": e.get("keys", "")}
    if prefix == "new_lore":
        payload["kind"] = kind
    if kind == "locations":
        payload["sd_prompt"] = e.get("sd_prompt", "")
        payload["current_setting"] = e.get("current_setting", False)
    # The dedupe is per kind, so an item and a lore entry may share a
    # name -- and then a slug. The staged id carries the kind for every
    # kind but `lore`, which keeps the bare shape reviews already hold.
    staged_id = (f"{prefix}:{kind}/{candidate_id}"
                 if prefix == "new_lore" and kind != "lore"
                 else f"{prefix}:{candidate_id}")
    return {"id": staged_id, "kind": prefix,
            "target": {"kind": kind, "id": ""},
            "label": f"New {label_noun} — {name}", "field": "body",
            "before": "", "after": body, "authored": False,
            "payload": payload}


def new_entities(cid: str, parsed: dict, stamp: Stamp) -> list[dict]:
    """Stage new locations and per-kind records against existing names and ids."""
    out: list[dict] = []
    for section_kind, parsed_key, prefix in (
        ("locations", "new_locations", "new_location"),
        ("lore", "new_lore", "new_lore"),
    ):
        # Only `new_lore` carries a kind of its own, and it is per ROW; the
        # dedupe set below is therefore per kind, built the first time a row
        # of that kind turns up.
        existing: dict[str, set[str]] = {}
        for e in parsed.get(parsed_key, []):
            kind = section_kind
            if prefix == "new_lore":
                kind = parse.new_lore_kind(e.get("kind"))
            if kind not in existing:
                existing[kind] = {ent["name"].strip().lower()
                                  for ent in overlay.list_entities(cid, kind)}
            edit = _new_entity(cid, e, kind, prefix, existing[kind])
            if edit is not None:
                out.append(stamp(edit, e))

    return out
