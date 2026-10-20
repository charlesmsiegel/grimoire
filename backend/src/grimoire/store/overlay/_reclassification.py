"""Campaign record moves and scoped reference rewrites without changing world records."""

from __future__ import annotations

from .. import (
    commits,
    entities,
    entity_schema,
    group_proposals,
    journal,
    knowledge,
    locks,
    revision,
)
from . import _copying, _entities, _ledgers, _record_paths


def reclassify_entity(cid: str, kind: str, eid: str, new_kind: str,
                      prefer: str | None = None) -> str:
    """Move this campaign's copy of `kind`/`eid` to `new_kind`; return its id.

    Materializes first, because a campaign that reclassifies an inherited record
    is disagreeing with its world about what that record *is* -- and there is no
    way to say that without holding a copy. `EntityNotFound` for a record this
    campaign cannot see at all, tombstoned ones included, exactly as a read of
    it would raise.

    An unknown `new_kind` and a `new_kind` equal to `kind` are both refused
    here rather than one call later, so neither leaves a materialized copy
    behind -- see the comment on the checks.

    `prefer` is the id the caller needs this copy to land on, and it is passed
    straight through: a world-side reclassify has already moved the world
    record, and a campaign copy that took a different id would stop being a copy
    *of* it. Occupied campaign-side, the copy lands elsewhere and the caller is
    told by the id it gets back rather than being handed a silently forked pair.
    """
    # Both refusals BEFORE the materialization, not inside the move that
    # follows it. `entities.reclassify` raises these too, but by then this has
    # already copied a world record into the campaign -- so a request that
    # cannot be satisfied would fork the record off its world as a parting gift
    # and then report failure. Validate, then mutate.
    if new_kind not in entities.ENTITY_KINDS:
        raise entities.UnknownKind(new_kind)
    if kind == new_kind:
        raise entities.SameKindError(_record_paths.flat_ref(kind, eid))
    with locks.campaign_lock(cid):
        _, gone = _ledgers.require_identity_ledgers(cid)
        croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
        journal.require_readable(cid)
        if kind == "groups":
            knowledge.require_readable(cid)
        _copying.require_group_materialization(cid, kind, eid)
        def taken(c: str) -> bool:
            # the destination's namespace as this campaign sees it: what the world
            # holds there (which the campaign inherits), plus what it has tombstoned
            # (which `create_entity` already counts, for #225's reason)
            return (_record_paths._flat_path(wroot, new_kind, c).exists()
                    or _record_paths._record_dir(wroot, new_kind, c).is_dir()
                    or _record_paths.flat_ref(new_kind, c) in gone)

        with revision.stamp_on_failure(cid):
            group_proposals.require_readable(croot)
            commits.require_readable(cid)
            _copying.materialize_entity(cid, kind, eid)
            if kind == "groups":
                group_proposals.retire(cid, croot, group_proposals.matching_refs(croot, croot, eid))
            commit_move = commits.begin_membership_relocation(cid, kind, eid, new_kind, root=croot)
            token = journal.begin_membership_relocation(cid, kind, eid, new_kind)
            landed = entities.reclassify(croot, kind, eid, new_kind, taken=taken, prefer=prefer)
            if token is not None:
                journal.complete_membership_relocation(cid, token, new_kind, landed)
            if commit_move is not None:
                commits.complete_membership_relocation(cid, commit_move, new_kind, landed)
            if kind == "groups":
                # The group's id is free once it moves, and a new group of the
                # same name would take it -- and every grant made to this one
                # (spec 11 §7.5), so they go as on a delete.
                knowledge.forget_audience(cid, f"groups:{eid}")
            return landed


def rewrite_owner_refs(cid: str, old: str, new: str) -> list[tuple[str, str]]:
    """`entities.rewrite_owner_refs` through the overlay: repoint every
    `owners:` entry this campaign can SEE, not merely the copies it holds.

    The distinction is the whole reason this is here rather than a call on the
    campaign root. A campaign reclassifying an inherited record usually holds no
    copy of the records that named it as an owner -- they are still the world's
    -- so a croot-only sweep would rewrite nothing at all and leave every one of
    them pointing at a kind that, in this campaign, the record no longer has.

    Rewriting one materializes it, which is a real consequence and the correct
    one: the campaign has made a campaign-local decision about what a record is,
    and an entry gated on that record is part of what the decision changes. It
    is bounded to entries that actually name it, which is normally none.
    """
    touched: list[tuple[str, str]] = []
    for kind in entities.ENTITY_KINDS:
        # `owners` off the LISTING: `list_entities` has already parsed each
        # record's frontmatter, so reading every record again to find a line it
        # is holding would double the cost of a sweep that normally rewrites
        # nothing.
        for meta in _entities.list_entities(cid, kind):
            rewritten = entities.repointed(
                entities.owner_refs(meta.get("owners", "")), old, new)
            if rewritten is None:
                continue
            _entities.update_entity(cid, kind, meta["id"], owners=", ".join(rewritten))
            touched.append((kind, meta["id"]))
    return touched


def rewrite_ref_fields(cid: str, old: str, new: str) -> list[tuple[str, str]]:
    """`entities.rewrite_ref_fields` through the overlay, for the reason above:
    the records whose `holder` / `leader` / `headquarters` / `habitat` name a
    reclassified record are usually still the world's, so a croot-only sweep
    would rewrite none of them.

    Only a reclassify calls this. A delete leaves refs dangling on purpose --
    and the tombstone case is exactly why: `add_deleted` hides the world's
    record from this campaign without touching it, so there is no edit here
    that would not be one campaign rewriting a record every other campaign
    shares. `entity_schema`'s module docstring carries the rest.

    Fields whose declared `kinds` do not include the destination are skipped,
    for the reason `entities.rewrite_ref_fields` gives at length: repointing
    one stores a value the save boundary then rejects, which would leave the
    referring record unsaveable.
    """
    touched: list[tuple[str, str]] = []
    new_kind = new.split(":", 1)[0]
    for kind in entities.ENTITY_KINDS:
        specs = [s for s in entity_schema.ref_fields(kind) if new_kind in s["kinds"]]
        if not specs:
            continue
        for meta in _entities.list_entities(cid, kind):
            fields = {}
            for spec in specs:
                rewritten = entities.repointed(
                    entity_schema.parse_refs(meta.get(spec["key"], "")), old, new)
                if rewritten is not None:
                    fields[spec["key"]] = ", ".join(rewritten)
            if fields:
                _entities.update_entity(cid, kind, meta["id"], fields=fields)
                touched.append((kind, meta["id"]))
    return touched
