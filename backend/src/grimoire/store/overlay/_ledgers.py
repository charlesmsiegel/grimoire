"""Strict mutation preflights and fail-soft display reads of visibility and detachment ledgers."""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic, failsoft, group_refs, locks
from ..campaigns import paths as campaigns_paths
from ..paths import safe_id
from . import _record_paths

# ---- tombstones ----

def _refs_in(refs) -> set[str]:
    """The string entries of a ledger, and only those.

    `failsoft.read_json` checks the OUTER type, so `[1]` reads as a perfectly
    good list and every `ref.startswith(...)` downstream then raises
    `AttributeError` -- a 500 out of a read whose entire contract is to fail
    soft (Codex review). A non-string entry is an entry nothing can match, and
    dropping just that one keeps the rest of a hand-edited ledger working."""
    return {r for r in (refs or []) if isinstance(r, str)}


def _valid_ledger_ref(ref: object, *, visibility: bool) -> bool:
    """The existing record/asset/library spellings, without actor-ref restrictions.

    A ledger names filesystem records, including ids a comma-separated actor
    field cannot carry. Safe ids therefore apply here, not referenceable ids.
    """
    if not isinstance(ref, str):
        return False
    parts = ref.split("/")
    if len(parts) == 2:
        return ((parts[0] in _record_paths.INHERITED_KINDS and safe_id(parts[1]))
                or parts == ["plotmap", "plotmap"])
    if not visibility:
        return False
    if ref == "plotmap":
        return True
    if len(parts) == 3 and parts[:2] == ["assets", "library"]:
        return safe_id(parts[2])
    return (len(parts) == 5 and parts[0] == "assets"
            and parts[1] in _record_paths.INHERITED_KINDS and all(safe_id(part) for part in parts[2:]))


def _strict_ledger(path: Path, *, visibility: bool) -> set[str]:
    """An empty ledger means actual absence, never unreadable identity (spec 07).

    Ordinary overlay display remains fail-soft. Mutation and membership identity
    boundaries cannot use that fallback: overwriting an empty fallback destroys
    markers for unrelated records and can bind a future stranger by slug.
    """
    try:
        refs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(refs, list) or not all(
                _valid_ledger_ref(ref, visibility=visibility) for ref in refs):
            raise ValueError("invalid identity ledger structure or reference")
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as exc:
        raise group_refs.GroupIdentityConflict(
            f"Cannot verify group identity: {path.name} is unreadable or invalid. "
            "Repair that ledger before editing or changing linked records.") from exc
    return set(refs)


def detached_strict(cid: str) -> set[str]:
    """Complete validated detachments for an identity read or mutation."""
    return _strict_ledger(_detached_path(cid), visibility=False)


def deleted_strict(cid: str) -> set[str]:
    """Complete validated tombstones, preserving record/asset/library markers."""
    return _strict_ledger(_deleted_path(cid), visibility=True)


def require_identity_ledgers(cid: str) -> tuple[set[str], set[str]]:
    """Preflight both before associated effects; caller holds the campaign lock."""
    return detached_strict(cid), deleted_strict(cid)


def _deleted_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "deleted.json"


def deleted(cid: str) -> set[str]:
    """The campaign's tombstoned refs, empty when it has none.

    A corrupt file reads as empty too, because a campaign that cannot be opened
    at all is the worse failure -- but unlike the store's other fail-soft reads,
    this one degrades toward *more* content: "nothing was deleted" is how every
    record the user deleted campaign-side comes back, inherited from the world.
    That is the one direction of failure a user cannot spot by looking, so
    `failsoft` logs it (see that module for why only two reads do).
    """
    refs = failsoft.read_json(
        _deleted_path(cid), list,
        f"campaign {cid} reads as having no deletions, so records deleted here "
        "will reappear, inherited from the world")
    return _refs_in(refs)


def add_deleted(cid: str, ref: str) -> None:
    """Tombstone one ref, UNDER `campaign_lock`.

    `deleted.json` is rewritten whole, so two unlocked writers lose one of the
    two -- and what a lost tombstone does is resurrect a record or an image the
    user deleted, which `deleted()` above names as the one direction of failure
    a user cannot spot by looking. Reentrant, so the callers already inside a
    hold pay nothing. `add_detached` is the precedent: same file, same shape,
    already locked.
    """
    with locks.campaign_lock(cid):
        gone = deleted_strict(cid)
        if not _valid_ledger_ref(ref, visibility=True):
            raise group_refs.GroupIdentityConflict("Cannot record an invalid visibility reference.")
        atomic.write_text(_deleted_path(cid),
                          json.dumps(sorted(gone | {ref}), indent=2) + "\n")


def _drop_deleted(cid: str, refs: set[str]) -> None:
    """Untombstone, under the same lock and for the same reason -- it is the
    same whole-file rewrite read backwards."""
    with locks.campaign_lock(cid):
        gone = deleted_strict(cid)
        keep = gone - refs
        if keep != gone:
            atomic.write_text(_deleted_path(cid),
                              json.dumps(sorted(keep), indent=2) + "\n")


def drop_library_tombstone(cid: str, name: str) -> None:
    """Un-hide one inherited library image.

    The public door onto `_drop_deleted` for this ref shape, needed by two
    callers with no other way in: the campaign's Restore, and
    `world_images.delete_image`'s sweep of the campaigns that hid an image the
    world no longer has. `forget_world_record` is the record-shaped equivalent
    and cannot serve, since a library image has no record.
    """
    _drop_deleted(cid, {_record_paths.library_ref(name)})


# ---- detached refs ----

def _detached_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "detached.json"


def detached(cid: str) -> set[str]:
    """Refs the campaign owns outright, sharing only a slug with the world.

    The third state a record can be in here, after "campaign copy" and
    "tombstone". A tombstone says *absent*; this says **mine, and nothing filed
    under that id in the world applies to it**. The id is free, and the next
    world record to claim it is a stranger that would otherwise supply this
    campaign's record with its sync updates, its avatar, its tagline and its
    voice anchor, all by coincidence of slug (#225).

    A record arrives here two ways, and they are one fact read from either end:
    the copy whose world original was **deleted** (`forget_world_record`), and
    the record the campaign **created** for itself, whose id named nothing in
    the world when `uniquify` allocated it (`_mark_campaign_owned`). Neither
    has a world record behind it; both would otherwise adopt the next one to
    take the slug.

    Detaching is one-way and per record: the campaign copy already exists, so
    there is nothing to fall through *to* that could be right.

    Fail-soft in the same direction as `deleted`, and warned about for the same
    reason: reading empty means "still attached", so a corrupt file quietly
    resumes inheriting from an unrelated record. Failure *adds* content, which
    is the one direction a user cannot spot by looking.
    """
    refs = failsoft.read_json(
        _detached_path(cid), list,
        f"campaign {cid} reads as having no detached records, so a record whose "
        "world original was deleted will inherit from whatever now holds its id")
    return _refs_in(refs)


def add_detached(cid: str, ref: str) -> None:
    """Mark `ref` detached under the shared campaign lock.

    Read-modify-replace, so two unserialized writers each read this file and
    each replace it, losing one marker. This used to be left open, and the
    argument was that `add_deleted` beside it and `campaigns.paths.write_manifest`
    below it have the identical shape -- the latter named in
    `locks.OUTSIDE_DOMAIN` as exactly that known gap -- so giving this one file
    a lock its siblings lack would not make the campaign ledger safe, only make
    the gap harder to find.

    What changed is the write rate. That argument was written when the only
    caller was the world-delete sweep, where losing a marker takes two deletes
    landing on one campaign at once. `_mark_campaign_owned` now writes here on
    every campaign actor creation, so the sweep races ordinary play, and a lost
    marker silently un-fixes one actor -- no error, nothing to see until a slug
    collision hands her a stranger's assets (Codex review, twice). A hazard
    that common is not one to leave standing on the grounds that its neighbours
    share it.

    Both detachment and visibility writers now serialize their complete
    read-modify-write, and use strict reads to preserve identity (spec 07).
    The manifest's other writers retain their documented residual. These
    changes do NOT claim that the whole overlay backlog has been reviewed;
    the remaining scope in `locks.UNREVIEWED` is still owed.

    Reentrant, so `create_character`, which holds the lock across the create
    and this mark to make the pair atomic, pays nothing for it."""
    with locks.campaign_lock(cid):
        severed = detached_strict(cid)
        if not _valid_ledger_ref(ref, visibility=False):
            raise group_refs.GroupIdentityConflict("Cannot record an invalid detachment reference.")
        atomic.write_text(_detached_path(cid),
                          json.dumps(sorted(severed | {ref}), indent=2) + "\n")
