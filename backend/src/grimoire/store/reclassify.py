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
  campaign-side (`overlay.hide_inherited`) or it would show through under the
  old kind beside the new one, and its sync base is dropped: there is no world
  record at the new ref for a base to be about.

**What a reclassify does not reach**, each for a reason rather than an oversight:

- **A scene's `location_history`.** It stores bare location ids, with no kind
  beside them, so a location leaving `locations` cannot be followed there --
  there is no ref to rewrite, only a record of where the play went. It is left
  as the play left it. `context.assemble` already renders no setting block for a
  location id that does not resolve, which is the behaviour a deleted location
  has always had.
- **`owners:` in a campaign's copy, on a WORLD-scope move.** World scope
  rewrites the world's own records; a campaign that materialized its own copy of
  one keeps its own text, and the world's rewritten version reaches it as an
  ordinary sync update. Rewriting a campaign's copy under it would manufacture a
  conflict on a record its owner never touched. (Campaign scope is the other
  way round and rewrites everything it can see, materializing as it goes --
  `overlay.rewrite_owner_refs` says why.)
- **The freed slug.** After `lore/tidewatch` becomes `locations/tidewatch`, the
  next lore entry named Tidewatch takes `lore/tidewatch` back, exactly as it
  does after a delete -- `entities.create_entity` hands out ids by slug against
  what exists *now*. Every ref this module can see has been repointed by then,
  so what it inherits is the same residue a delete leaves (#225), no more.
- **Actors.** Characters are a folder plus a V3 card per version, not a flat
  record, so lore -> character is a conversion rather than a move and has no id
  continuity to preserve. It is the issue's Option B and is not built here.
"""

from __future__ import annotations

import logging

from . import entities, locks, overlay, record_refs, sheets
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


def campaign_entity(cid: str, kind: str, eid: str, new_kind: str) -> str:
    """Reclassify this campaign's copy of `kind`/`eid`. Returns its new id.

    Raises `entities.EntityNotFound` when the campaign cannot see the record at
    all (tombstoned included), `entities.UnknownKind` for a kind that is not a
    generic entity kind, and `entities.SameKindError` when the two are the same.
    """
    with locks.campaign_lock(cid):
        new_eid = overlay.reclassify_entity(cid, kind, eid, new_kind)
        old, new = _ref(kind, eid), _ref(new_kind, new_eid)
        # Before the tombstone: `hide_inherited` asks whether the world's record
        # at the old ref would show through, and a stale base there is not part
        # of that question -- but a tombstone written first would make the ref
        # look already-hidden to anything reading the ledgers in between.
        overlay.repoint_record(cid, old, new, keep_base=False)
        overlay.hide_inherited(cid, kind, eid)
        overlay.rewrite_owner_refs(cid, _owner_ref(kind, eid),
                                   _owner_ref(new_kind, new_eid))
        record_refs.repoint(cid, {old: new})
    # Outside the hold: nothing below reads campaign state, and a `return`
    # inside a `with` reads to the type checker as a path that may not run.
    return new_eid


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
        old, new = _ref(kind, eid), _ref(new_kind, new_eid)
        entities.rewrite_owner_refs(wroot, _owner_ref(kind, eid),
                                    _owner_ref(new_kind, new_eid))
        sheets.repoint_world_records(wid, {old: new})
        swept = []
        for cid in cids:
            try:
                if _follow_in_campaign(cid, wroot, kind, eid, new_kind, new_eid):
                    swept.append(cid)
            except (OSError, ValueError) as exc:
                # Per campaign, not per sweep, and for `overlay.forget_world_record`'s
                # reason: the world record has already moved by the time we get
                # here, so one campaign with an unreadable ledger must not cost
                # every later dependent its repoint, nor 500 a move that happened.
                log.warning("could not follow %s -> %s into campaign %s (%s) -- its refs "
                            "still name the old kind", old, new, cid, exc)
        return {"id": new_eid, "campaigns": swept}


def _follow_in_campaign(cid: str, wroot, kind: str, eid: str,
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
    old, new = _ref(kind, eid), _ref(new_kind, new_eid)
    landed = new
    if overlay.has_own_copy(cid, kind, eid):
        got = overlay.reclassify_entity(cid, kind, eid, new_kind, prefer=new_eid)
        if got != new_eid:
            # The campaign already had a record of its own at the world's new
            # id, so its copy could not follow. It is no longer a copy *of*
            # anything -- keeping a base would have `sync.incoming` compare it
            # against a stranger -- so it becomes campaign-local, which is what
            # `keep_base=False` writes.
            log.warning("campaign %s could not move its copy of %s to %s (taken); it is "
                        "now the campaign-local record %s", cid, old, new, _ref(new_kind, got))
            landed = _ref(new_kind, got)
    overlay.repoint_record(cid, old, landed, keep_base=landed == new)
    record_refs.repoint(cid, {old: landed})
    return True
