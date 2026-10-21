"""Flat-record resolution, membership capture and campaign-local CRUD."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import (
    commits,
    entities,
    group_inventory,
    group_refs,
    journal,
    knowledge,
    locks,
    revision,
    statcache,
)
from ..campaigns import read as campaigns_read
from ..paths import safe_id
from . import _copying, _identity, _ledgers, _record_paths, _transitions, _views


def list_entities(cid: str, kind: str, *, v: _views.View | None = None) -> list[dict]:
    v = _views._view(cid, v)
    mine = entities.list_entities(v.croot, kind)
    have = {e["id"] for e in mine}
    inherited = [e for e in entities.list_entities(v.wroot, kind)
                 if e["id"] not in have and _record_paths.flat_ref(kind, e["id"]) not in v.gone]
    return sorted(mine + inherited, key=lambda e: e["id"])


def group_capture(cid: str) -> group_inventory.GroupCapture:
    """Fresh, strict membership inputs; never attach new stamps to an old View.

    Campaign metadata and both ledgers are stamped before world resolution or
    ledger loading. The frozen sets and selected roots then belong to this sweep.
    Ordinary entity listings retain their fail-soft display contract.
    """
    v = _views.view(cid)
    # Preserve the logical store container before following a campaign symlink.
    # A pointer switch after this point cannot redirect either binding or bytes.
    store_root = v.croot.parent.parent
    v._croot = v.croot.resolve()
    root_stamp = statcache.stamp(v.croot)
    meta_stamps = group_inventory.pre_stamps(v.croot / "campaign.md", v.croot)
    gone_path, off_path = v.croot / "deleted.json", v.croot / "detached.json"
    gone_stamps = group_inventory.pre_stamps(gone_path, v.croot)
    off_stamps = group_inventory.pre_stamps(off_path, v.croot)
    v._wroot = campaigns_read.world_root_from(v.croot, store_root=store_root).resolve()
    v._gone = frozenset(_ledgers._strict_ledger(gone_path, visibility=True))
    v._off = frozenset(_ledgers._strict_ledger(off_path, visibility=False))
    mine, mine_stamps = group_inventory.collect(v.croot, "campaign")
    theirs, their_stamps = group_inventory.collect(v.wroot, "world")
    # A native alias may resolve to an owned file with a differently spelled
    # stem. That file wins just as it does in the one-group editor; no casefold
    # may collapse two real files in a case-sensitive collection.
    owned_ids = {source.id for source in mine}
    inherited = tuple(source for source in theirs
        if source.id not in owned_ids and not _record_paths._flat_path(v.croot, "groups", source.id).exists()
        and not _identity.record_is_marked(cid, "groups", source.id, set(v.gone), roots=(v.wroot, v.wroot)))
    sources = tuple(sorted((*mine, *inherited), key=lambda source: source.id))
    ledger_digest = hashlib.sha256(json.dumps(
        (sorted(v.gone), sorted(v.off)), ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    stamps = group_inventory.combined_stamps(
        None if root_stamp is None else (root_stamp,), meta_stamps, gone_stamps,
        off_stamps, mine_stamps, their_stamps)
    return group_inventory.GroupCapture(
        sources=sources, stamps=stamps, roots=(v.croot, v.wroot), deleted=v.gone,
        detached=v.off, ledger_digest=ledger_digest,
        groups_digest=group_inventory.content_digest(sources), store_root=store_root.resolve())


def group_rows(capture: group_inventory.GroupCapture, *, include_body: bool = False
               ) -> tuple[list[dict], tuple[str, ...], tuple[str, ...]]:
    """Effective metadata plus the native proofs needed to validate a memo hit."""
    rows = group_inventory.rows(capture.sources, include_body=include_body)
    raw_refs = tuple(sorted({actor for source, row in zip(capture.sources, rows, strict=True)
        if source.layer == "world" for _, actor in group_refs.group_slots(row)}))
    severed = _identity.actor_detachments(capture.roots[0], capture.roots[1], raw_refs, set(capture.detached))
    for source, row in zip(capture.sources, rows, strict=True):
        if source.layer == "world":
            members, leader = group_refs.effective_refs(row, inherited=True, detached=severed)
            row["members"] = ", ".join(members)
            row["leader"] = leader or ""
    return rows, raw_refs, tuple(sorted(severed))


def group_listing(cid: str) -> tuple[list[dict], tuple[statcache.Stamp, ...] | None,
                                    tuple[Path, Path], str, str]:
    capture = group_capture(cid)
    rows, _, _ = group_rows(capture)
    return rows, capture.stamps, (capture.roots[0], capture.roots[1]), capture.ledger_digest, capture.groups_digest


def read_entity(cid: str, kind: str, eid: str, *, v: _views.View | None = None) -> dict:
    v = _views._view(cid, v)
    try:
        return entities.read_entity(v.croot, kind, eid)
    except entities.EntityNotFound:
        if _record_paths.flat_ref(kind, eid) in v.gone:
            raise
        return entities.read_entity(v.wroot, kind, eid)


def entity_source(cid: str, kind: str, eid: str, *, v: _views.View | None = None) -> Path | None:
    """The file `read_entity` reads for this campaign's view of `kind/eid`:
    the campaign's own copy, else the world's unless the campaign deleted it;
    None when neither exists. One or two stats, no read. Spec 05's vector rows
    name it, so a warm-up rebuilds an entry from the file the campaign reads."""
    v = _views._view(cid, v)
    try:
        return entities.require_entity(v.croot, kind, eid)
    except entities.EntityNotFound:
        if _record_paths.flat_ref(kind, eid) in v.gone:
            return None
    try:
        return entities.require_entity(v.wroot, kind, eid)
    except entities.EntityNotFound:
        return None


def read_entity_rev(cid: str, kind: str, eid: str) -> dict:
    """`read_entity` carrying the rev of whichever layer answered (#35)."""
    try:
        return entities.read_entity_rev(_record_paths.croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _record_paths.flat_ref(kind, eid) in _ledgers.deleted(cid):
            raise
        return entities.read_entity_rev(_record_paths.wroot_of(cid), kind, eid)


def read_group_membership(cid: str, eid: str, *, v: _views.View | None = None
                          ) -> tuple[dict, group_refs.GroupMembershipContext]:
    """The ordinary group payload and membership context from one source read.

    Owned diagnostics are empty, including authored dangling refs; they do not
    need readable identity ledgers. Inherited diagnostics use one complete
    ledger snapshot and the selected World root, never a second group read.
    """
    v = _views._view(cid, v)
    try:
        record = entities.read_entity_rev(v.croot, "groups", eid)
    except entities.EntityNotFound:
        severed, gone = _ledgers.require_identity_ledgers(cid)
        if _identity.record_is_marked(cid, "groups", eid, gone, roots=(v.croot, v.wroot)):
            raise
        wroot = v.wroot
        record = entities.read_entity_rev(wroot, "groups", eid)
        meta = record["meta"]
        raw_members, raw_leader = group_refs.parse_members(meta), group_refs.parse_leader(meta)
        severed = _identity.actor_detachments(v.croot, wroot,
            (actor for _, actor in group_refs.group_slots(meta)), severed)
        members, leader = group_refs.effective_refs(meta, inherited=True, detached=severed)
        unresolved = _identity._unresolved_group_slots(v.croot, wroot, meta, severed)
        context = group_refs.GroupMembershipContext(
            layer="world", members=members, leader=leader,
            filtered_members=tuple(r for r in raw_members if r not in members),
            filtered_leader=raw_leader if raw_leader != leader else None,
            unresolved_members=tuple(r for r in raw_members if ("members", r) in unresolved),
            unresolved_leader=raw_leader if ("leader", raw_leader) in unresolved else None,
            unresolved_leaders=tuple(r for r in group_refs.leader_tokens(meta)
                                     if ("leader", r) in unresolved))
    else:
        context = group_refs.GroupMembershipContext(
            layer="campaign", members=group_refs.parse_members(record["meta"]),
            leader=group_refs.parse_leader(record["meta"]), filtered_members=(),
            filtered_leader=None, unresolved_members=(), unresolved_leader=None)
    return record, context



def entity_rev(cid: str, kind: str, eid: str) -> str | None:
    """The rev `read_entity_rev` would return now, without reading the record.

    Resolved through the overlay for the same reason the read is: an inherited
    record's rev describes the *world* file, and a save against it materializes
    from that file. So a world-side edit arriving between the campaign editor's
    read and its save is a stale write like any other -- the copy it would make
    is not the text the user was shown.
    """
    h = entities.entity_hash(_record_paths.croot_of(cid), kind, eid)
    if h is not None:
        return h
    if _record_paths.flat_ref(kind, eid) in _ledgers.deleted(cid):
        return None
    return entities.entity_hash(_record_paths.wroot_of(cid), kind, eid)


def entity_root(cid: str, kind: str, eid: str) -> Path:
    """Root whose `<kind>/<eid>.md` is the record this campaign reads.

    The flat-record twin of `actor_root`, and it resolves the way `read_entity`
    does rather than the way `_inherits_world` does: a *detached* record is one
    the campaign holds itself, so its own copy answers before the question of
    inheritance comes up at all. A tombstoned record resolves to the campaign,
    where there is no file, so the caller's read raises its usual NotFound.
    """
    croot = _record_paths.croot_of(cid)
    # `kind` and `eid` are path parameters, and a path parameter can carry an
    # encoded slash: the resolvers refuse those, so this must not build a path
    # out of one and stat it. Refusing here and letting the caller's
    # `require_entity` raise keeps both answers the same 404.
    if kind in entities.ENTITY_KINDS and safe_id(eid) and _record_paths._flat_path(croot, kind, eid).exists():
        return croot
    if _record_paths.flat_ref(kind, eid) in _ledgers.deleted(cid):
        return croot
    return _record_paths.wroot_of(cid)


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "", sd_prompt: str = "", fields: dict | None = None,
                  secrecy: str = "") -> str:
    wroot, gone = _record_paths.wroot_of(cid), _ledgers.deleted(cid)

    def taken(eid: str) -> bool:
        # the world's record DIRECTORY counts: a sweep that could not remove it
        # leaves assets this campaign would inherit through the overlay the
        # moment it claimed the slug (Codex review)
        return (_record_paths._flat_path(wroot, kind, eid).exists() or _record_paths._record_dir(wroot, kind, eid).is_dir()
                or _record_paths.flat_ref(kind, eid) in gone)

    eid = entities.create_entity(_record_paths.croot_of(cid), kind, name, body, keys=keys,
                                 owners=owners, sd_prompt=sd_prompt, taken=taken, fields=fields,
                                 secrecy=secrecy)
    # Campaign-scoped entity work is work on the campaign, but it writes only
    # overlay records -- neither campaign.md nor any scene -- so the derived
    # activity high-water mark would not see an evening of lore or cast
    # editing at all. Stamped after the write, and never fatal to it.
    return eid


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None, fields: dict | None = None,
                  secrecy: str | None = None,
                  reviewed_slots: frozenset[tuple[str, str]] = frozenset()) -> None:
    if kind not in entities.ENTITY_KINDS:
        raise entities.UnknownKind(kind)
    if not safe_id(eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")
    with locks.campaign_lock(cid):
        croot = _record_paths.croot_of(cid)
        if _record_paths._flat_path(croot, kind, eid).exists():
            # Already-owned refs are authored; an ordinary edit needs no World
            # identity inference. Lifecycle and membership journal owners keep
            # their own stricter preflights (spec 07).
            with revision.stamp_on_failure(cid):
                entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys,
                                       owners=owners, fields=fields, secrecy=secrecy)
            return
        if not _copying._materialize_flat(cid, kind, eid,
                prepare=lambda text: entities.updated_text(text, name=name, body=body,
                    keys=keys, owners=owners, fields=fields, secrecy=secrecy),
                reviewed_slots=reviewed_slots):
            raise entities.EntityNotFound(f"{kind}/{eid}")


def would_inherit(cid: str, kind: str, eid: str) -> bool:
    """Would this campaign read the world's `<kind>/<eid>` if it held none of
    its own? What `delete_entity` turns on, and what a campaign-side reclassify
    has to ask before it moves anything: moving the campaign's copy out of a
    kind leaves the world's copy free to show through, and the record would then
    be listed twice -- inherited under its old kind, and under its new one.

    A predicate rather than a decide-and-tombstone, because the *answer* has to
    be taken before `repoint_record` runs and the tombstone written after. Both
    read `detached.json`, and repointing moves this ref's entry in it: asked
    afterwards, a detached record reads as freshly inheriting and gets a
    tombstone -- which permanently hides whatever stranger now holds that id in
    the world, the exact outcome `_inherits_world` exists to prevent.
    """
    return (_identity._inherits_world(cid, _record_paths.flat_ref(kind, eid))
            and _record_paths._flat_path(_record_paths.wroot_of(cid), kind, eid).exists())


def delete_entity(cid: str, kind: str, eid: str) -> None:
    with locks.campaign_lock(cid):
        _ledgers.require_identity_ledgers(cid)
        journal.require_readable(cid)
        if kind == "groups":
            knowledge.require_readable(cid)
        eid = _identity._canonical_record_id(cid, kind, eid)
        ref = _record_paths.flat_ref(kind, eid)
        source_refs = _identity.capture_record_refs(cid, ref)
        in_world = would_inherit(cid, kind, eid)
        with revision.stamp_on_failure(cid):
            _transitions._retire_owned_reviews(cid, kind, eid)
            commits.retire_membership_targets(cid, kind, eid, root=_record_paths.wroot_of(cid), world=str(_record_paths.wroot_of(cid).resolve()))
            journal.retire_membership_targets(cid, kind, eid)
            try:
                entities.delete_entity(_record_paths.croot_of(cid), kind, eid)
                _transitions._drop_manifest_ref(cid, ref, source_refs=source_refs)
            except entities.EntityNotFound:
                if not in_world:
                    raise
            if in_world:
                _ledgers.add_deleted(cid, ref)   # keep the world's copy from showing through
            _record_paths._drop_record_dir(_record_paths.croot_of(cid), kind, eid)
            _transitions._undetach(cid, ref, source_refs=source_refs)
            if kind == "groups":
                # A group recreated under this id must not inherit what the
                # user said the deleted one knew (spec 11 §7.5).
                knowledge.forget_audience(cid, f"groups:{eid}")


def require_owned_entity(cid: str, kind: str, eid: str) -> Path:
    """Resolve an existing owned entity only, with no inherited fallback.

    Membership Undo seals an owned original; resolving a World replacement
    after its copy disappears would grant that replacement the offer. Keep the
    raw-root lookup here, where campaign record resolution is owned (spec 07).
    """
    return entities.require_entity(_record_paths.croot_of(cid), kind, eid)
