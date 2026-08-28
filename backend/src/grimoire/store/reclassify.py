"""Reclassification (#119): a generic entity changes kind and keeps its id.

The store's five generic kinds -- locations, lore, items, groups, creatures --
are one shape (`<root>/<kind>/<id>.md` plus a sibling directory) differing only
in what the context builder does with them: keyless lore is always-on, a keyless
location surfaces only as the scene's current setting, an activated group pulls
its campaign state in. So "this is a place, not a rumour" is a correction the
data supports, and until now the only way to make it was to retype the record
under the other kind and lose everything filed against the old one.

**The id survives the move.** That is the invariant the whole design rests on:
a ref is `(kind, id)`, and if only the kind changes then every ledger keyed by
the pair can be rekeyed old-for-new instead of re-resolved. It survives *where
it can* -- a destination already holding that slug forces the `-2` suffix a
create would take -- so callers use the id these functions return.

Two scopes, and the difference is not cosmetic:

- **World scope** moves the record for everyone. Every campaign of that world
  is swept in the same breath: its copy of the record (if it materialized one)
  moves with it, its sync base carries over to the new ref unchanged (the file's
  bytes did not move, only its directory), and its ledgers are repointed. Skip
  that sweep and the issue's own failure lands: the old ref's world hash reads
  as `None`, which `sync.incoming` treats as a world-side deletion and skips,
  while the new ref arrives as a `new` record -- so the campaign accumulates a
  stale copy under the old kind *and* a duplicate under the new one.
- **Campaign scope** moves only this campaign's copy, materializing it first,
  because a campaign that reclassifies an inherited record is disagreeing with
  its world about what that record is -- and there is no way to say that without
  holding a copy. The world's record stays where it is, so it is tombstoned
  campaign-side -- `overlay.would_inherit` asks whether it would otherwise show
  through under the old kind beside the new one -- and its sync base is
  dropped: there is no world record at the new ref for a base to be about.

**What a reclassify does not reach**, each for a reason rather than an oversight:

- **Anything already written into a scene.** Two things live there and neither
  is a ledger key:

  `location_history` stores bare location ids, with no kind beside them, so a
  location leaving `locations` cannot be followed there -- there is no ref to
  rewrite, only a record of where the play went. `context.assemble` already
  renders no setting block for a location id that does not resolve, which is
  the behaviour a deleted location has always had.

  A post that carries a picture stores it as `![alt](/api/.../<kind>/<id>/...)`
  -- `context.art.resolve_handles` expands a handle to markdown at append time,
  so the kind is baked into the URL. Reclassify the record and that image stops
  loading in the post it was shown in, exactly as deleting it does.

  Both are left as the play left them. A transcript is the one thing in this
  store that cannot be regenerated, and the whole of `store/scenes` is
  serialized to protect it; sweeping every post of every scene to repair a
  cosmetic link is not a trade this makes. The alt text -- which is the part
  the model ever sees (`context.story`) -- is unaffected either way.
- **`owners:` and the ref-valued FIELDS in a campaign's copy, on a WORLD-scope
  move.** Both spell a ref the same way (`<kind>:<id>`) and both are swept in
  the same breath, world-side and campaign-side. World scope
  rewrites the world's own records; a campaign that materialized its own copy of
  one keeps its own text, and the world's rewritten version reaches it as an
  ordinary sync update. Rewriting a campaign's copy under it would manufacture a
  conflict on a record its owner never touched. (Campaign scope is the other
  way round and rewrites everything it can see, materializing as it goes --
  `overlay.rewrite_owner_refs` says why.)
- **A campaign-scope move BACK to a kind it has already left.** The first move
  tombstones the world's record under the old kind, so the second finds that
  slug taken and lands on `-2`. It is the conservative answer and it is
  deliberate: the tombstone says "the world's record at this ref is deleted
  here", and nothing on disk distinguishes the record this campaign hid on its
  way out from an unrelated one that happened to hold the id. Clearing it would
  be right for the first and would un-delete the second.
- **The slug a WORLD-scope move frees.** After the world's `lore/tidewatch`
  becomes `locations/tidewatch`, the next lore entry named Tidewatch takes
  `lore/tidewatch` back, exactly as it does after a delete --
  `entities.create_entity` hands out ids by slug against what exists *now*.
  Every ref named above has been repointed by then, so what the newcomer
  inherits is the same residue a delete leaves (#225), no more -- with the
  location history above as the one thing that still points its way.
  (Campaign scope frees nothing: the old ref is tombstoned, which is what the
  round-trip bullet is about.)
- **Actors.** Characters are a folder plus a V3 card per version, not a flat
  record, so lore -> character is a conversion rather than a move and has no id
  continuity to preserve. It is the issue's Option B and is not built here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import entities, locks, overlay, record_refs, revision, sheets
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)


def _ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _owner_ref(kind: str, eid: str) -> str:
    # `owners:` names a record with a COLON, matching the present-set
    # `context.assemble` builds; every ledger key uses a slash. Two spellings of
    # one ref, and this module is where they meet.
    return f"{kind}:{eid}"


def _sweep(fn, lost: str, old_ref: str, new_ref: str, cid: str = "") -> None:
    """Run one leg of a post-move sweep; log what stayed stale rather than raise.

    **A leg each**, and that is the point of the helper. The `owners:` rewrite,
    the ref-field rewrite and the ledger repoint are independent -- different
    files, and none needs the others to have succeeded -- but they used to
    share one `try`, so the first failure silently skipped the rest. That was
    invisible until `EntityNotFound` became a caught miss rather than a 500: a
    referring record vanishing mid-sweep then also cost the world sheet its
    repoint, leaving it keyed to a kind the record no longer has, reachable
    from nothing.

    Never raises, for the reason every call site shares: the record has already
    moved by the time any of this runs. A 500 here reports a reclassify that
    DID happen as having failed, and the retry it invites then 404s. Stale
    display refs are the smaller harm, and the warning names what to look at.

    `EntityNotFound` is a miss and not an error because each sweep reads a
    listing and then writes every record it named, with no lock over the gap --
    another process or a sync client deleting one in between is ordinary.
    """
    try:
        fn()
    except (OSError, ValueError, entities.EntityNotFound) as exc:
        log.warning("reclassified %s to %s%s but %s (%s)", old_ref, new_ref,
                    f" in campaign {cid}" if cid else "", lost, exc)


def campaign_entity(cid: str, kind: str, eid: str, new_kind: str) -> str:
    """Reclassify this campaign's copy of `kind`/`eid`. Returns its new id.

    Raises `entities.EntityNotFound` when the campaign cannot see the record at
    all (tombstoned included), `entities.UnknownKind` for a kind that is not a
    generic entity kind, and `entities.SameKindError` when the two are the same.
    """
    with locks.campaign_lock(cid):
        # Asked FIRST, and written last. Both this and `repoint_record` read
        # `detached.json`, and repointing moves this ref's entry in it -- so a
        # detached record asked afterwards reads as freshly inheriting and gets
        # a tombstone that permanently hides whatever stranger now holds its id
        # in the world. See `overlay.would_inherit`.
        shadowed = overlay.would_inherit(cid, kind, eid)
        new_eid = overlay.reclassify_entity(cid, kind, eid, new_kind)
        old_ref, new_ref = _ref(kind, eid), _ref(new_kind, new_eid)
        overlay.repoint_record(cid, old_ref, new_ref, keep_base=False)
        if shadowed:
            overlay.add_deleted(cid, old_ref)   # or the world's shows through
        _repoint_campaign_side(cid, kind, eid, new_kind, new_eid)
    # Outside the hold: nothing below reads campaign state, and a `return`
    # inside a `with` reads to the type checker as a path that may not run.
    return new_eid


def _repoint_campaign_side(cid: str, kind: str, eid: str,
                           new_kind: str, new_eid: str) -> None:
    """The owner refs and the ledgers, neither of which may take down a move
    that has already happened.

    By the time this runs the record is under its new kind and the three
    overlay ledgers agree. One record nobody can parse must not turn that into
    a 500 whose retry then 404s -- the same trade `overlay.forget_world_record`
    makes at the other end of a delete, and the one `world_entity` makes per
    dependent campaign. Stale display refs are the smaller harm, and the
    warning names what to go and look at.
    """
    old_ref, new_ref = _ref(kind, eid), _ref(new_kind, new_eid)
    old_owner, new_owner = _owner_ref(kind, eid), _owner_ref(new_kind, new_eid)
    _sweep(lambda: overlay.rewrite_owner_refs(cid, old_owner, new_owner),
           "an `owners:` line may still name the old kind", old_ref, new_ref, cid)
    _sweep(lambda: overlay.rewrite_ref_fields(cid, old_owner, new_owner),
           "a ref field may still name the old kind", old_ref, new_ref, cid)
    _sweep(lambda: record_refs.repoint(cid, {old_ref: new_ref}),
           "a pin, a citation or an undo entry may still name the old kind",
           old_ref, new_ref, cid)


def world_entity(wid: str, kind: str, eid: str, new_kind: str) -> dict:
    """Reclassify a world record and sweep every campaign that inherits it.

    Returns `{"id": <new id>, "campaigns": [<cid>, ...]}` -- the campaigns whose
    ledgers were repointed, so a caller can say what the move reached.

    The dependents are enumerated and locked *before* the world record moves, so
    no campaign observes the half-moved state: a turn assembling context in one
    of them would otherwise list the record under both kinds, or neither. A
    campaign created after the enumeration needs no sweep -- it has nothing
    filed against the old ref and materializes from the world as it now stands.
    """
    wroot = worlds_paths.world_root(wid)
    cids = overlay.dependent_campaigns(wroot)
    with locks.hold_all(cids):
        new_eid = entities.reclassify(wroot, kind, eid, new_kind)
        old_ref, new_ref = _ref(kind, eid), _ref(new_kind, new_eid)
        old_owner, new_owner = _owner_ref(kind, eid), _owner_ref(new_kind, new_eid)
        # Three legs, none of which may cost the other two -- see `_sweep`. And
        # none of which, together, may cost the campaign loop below: one
        # unreadable world record must not leave every dependent campaign with
        # a stale copy under the old kind AND a duplicate under the new one,
        # which is the failure this whole function exists to prevent.
        _sweep(lambda: entities.rewrite_owner_refs(wroot, old_owner, new_owner),
               "an `owners:` line may still name the old kind", old_ref, new_ref)
        _sweep(lambda: entities.rewrite_ref_fields(wroot, old_owner, new_owner),
               "a ref field may still name the old kind", old_ref, new_ref)
        _sweep(lambda: sheets.repoint_world_records(wid, {old_ref: new_ref}),
               "a sheet may still be keyed to the old kind", old_ref, new_ref)
        swept = []
        for cid in cids:
            try:
                if _follow_in_campaign(cid, wroot, kind, eid, new_kind, new_eid):
                    swept.append(cid)
                    # Inside the hold that covers the repoint (#409). A world
                    # route reaches this, so nothing in `/api/campaigns/...`
                    # stamps the campaigns it rewrites -- see `sync.demote`,
                    # which makes the same call for the same reason.
                    revision.bump(cid)
            except (OSError, ValueError, entities.EntityNotFound) as exc:
                # Per campaign, not per sweep, and for `overlay.forget_world_record`'s
                # reason: the world record has already moved by the time we get
                # here, so one campaign with an unreadable ledger must not cost
                # every later dependent its repoint, nor 500 a move that happened.
                # `EntityNotFound` is in the list because the copy this is about
                # to move can go between `has_own_copy` and the move itself --
                # nothing holds a world-record lock, and there is none to hold.
                log.warning("could not follow %s -> %s into campaign %s (%s) -- its refs "
                            "still name the old kind", old_ref, new_ref, cid, exc)
    return {"id": new_eid, "campaigns": swept}


def _follow_in_campaign(cid: str, wroot: Path, kind: str, eid: str,
                        new_kind: str, new_eid: str) -> bool:
    """Repoint one dependent campaign at the world record's new kind.

    Re-asks which world the campaign belongs to for the reason
    `overlay._forget_in_campaign` does: campaign ids are reusable, and between
    the enumeration and this call the slug could have been taken by a campaign
    on another world entirely.
    """
    try:
        world = campaigns_read.read_campaign(cid)["meta"].get("world", "")
    except campaigns_paths.CampaignNotFound:
        return False   # deleted between the enumeration and its turn
    if not worlds_paths.references_world(world, wroot):
        return False
    old_ref, new_ref = _ref(kind, eid), _ref(new_kind, new_eid)
    landed = new_ref
    if overlay.has_own_copy(cid, kind, eid):
        got = overlay.reclassify_entity(cid, kind, eid, new_kind, prefer=new_eid)
        if got != new_eid:
            # The campaign already had a record of its own at the world's new
            # id, so its copy could not follow. It is no longer a copy *of*
            # anything -- keeping a base would have `sync.incoming` compare it
            # against a stranger -- so it becomes campaign-local, which is what
            # `keep_base=False` writes.
            log.warning("campaign %s could not move its copy of %s to %s (taken); it is "
                        "now the campaign-local record %s",
                        cid, old_ref, new_ref, _ref(new_kind, got))
            landed = _ref(new_kind, got)
    overlay.repoint_record(cid, old_ref, landed, keep_base=landed == new_ref)
    record_refs.repoint(cid, {old_ref: landed})
    return True
