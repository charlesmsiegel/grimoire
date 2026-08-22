"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (every kind in INHERITED_KINDS that is not an actor -- currently
  locations, lore, items, groups, creatures, greetings -- plus plotmap.json):
  campaign file wins; else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md, voice_anchor.md) and
  assets still overlay per file.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.
- <campaign>/detached.json (same shape) holds the third state: a record that
  shares only a slug with whatever the world files under that id. Two ways in,
  and they are the same fact from either end -- the campaign copy whose world
  original was DELETED, and the record the campaign CREATED for itself, whose
  id named nothing in the world when it was allocated. Records, sidecars and
  assets all stop resolving through for it -- see `detached()`.

Which records inherit, and which do not, is the whole content of the rule, so
it is declared below as data (INHERITED_KINDS / INHERITED_FILES) rather than
only in prose: `tests/test_overlay_guard.py` reads those names to check that
nothing outside this module resolves an inheritable record off a raw campaign
root. Everything else under <campaign> is campaign-local by definition and is
read directly: campaign.md, sync.md, deleted.json, detached.json, appearances.json,
calendar.json, changes.json, journal.json, chronicle.json, timeline.md, sheet_baselines.json,
the climate default (store/campaign_climate.py owns that filename), scenes/,
sheets/, proposals/, and the per-actor sidecars filed inside an actor dir
(dossier.md, state.md, voice_drift.md). tagline.md and voice_anchor.md are the
exceptions among sidecars -- both are world-level identity, so both overlay per
file, via `tagline()` / `voice_anchor()` below.

Campaign-local, though, does not mean campaign-lifetime: those sidecars are
filed under a record id, and an id outlives the record it named. Deleting a
record frees its slug, so the next create hands the same id back -- which is
what `forget_world_record` (at the bottom) exists to get in front of.
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import (
    assets,
    atomic,
    cards,
    characters,
    entities,
    failsoft,
    greetings,
    image_descriptions,
    locks,
    pcs,
    taglines,
    voice_anchors,
)
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .paths import natural_key, safe_id
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)

#: Record kinds a campaign inherits from its world. A `<campaign>/<kind>/...`
#: read for one of these is only correct through this module (or after the
#: reader has materialized the record itself).
INHERITED_KINDS: tuple[str, ...] = entities.SYNCED_KINDS + appearances_paths.ACTOR_KINDS

#: Campaign-root files that resolve through to the world the same way.
INHERITED_FILES: tuple[str, ...] = ("plotmap.json",)

#: The sync base a materialization reserves before its copy lands, while the
#: slim migration is still pending — see `_recorded_base`. Deliberately the
#: value the pre-overlay fork wrote for anything it did not copy, so the
#: migration's existing rule for that (`lifecycle.ensure_campaign_slim`, which
#: attributes no deletion to a ref carrying it) covers this too.
RESERVED_BASE = ""


def croot_of(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist — the world was deleted
    before the guard against that existed. Or, if the campaign records no
    world, `campaigns.world_root_of` yields a path nothing can occupy.
    Either way, nothing here raises for it — every resolver below just reads
    a path that doesn't hold the expected records as holding none."""
    return campaigns_read.world_root_of(cid)


# ---- tombstones ----

def _refs_in(refs) -> set[str]:
    """The string entries of a ledger, and only those.

    `failsoft.read_json` checks the OUTER type, so `[1]` reads as a perfectly
    good list and every `ref.startswith(...)` downstream then raises
    `AttributeError` -- a 500 out of a read whose entire contract is to fail
    soft (Codex review). A non-string entry is an entry nothing can match, and
    dropping just that one keeps the rest of a hand-edited ledger working."""
    return {r for r in (refs or []) if isinstance(r, str)}


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
    atomic.write_text(_deleted_path(cid), json.dumps(sorted(deleted(cid) | {ref}), indent=2) + "\n")


def _drop_deleted(cid: str, refs: set[str]) -> None:
    keep = deleted(cid) - refs
    if keep != deleted(cid):
        atomic.write_text(_deleted_path(cid), json.dumps(sorted(keep), indent=2) + "\n")


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
    """Mark `ref` detached. UNDER `campaign_lock`, unlike its two siblings.

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

    So the asymmetry is real and deliberate: `detached.json` is serialized;
    `deleted.json` and the manifest keep their documented gap. Stated here
    rather than left to be discovered, and it does NOT take `overlay` out of
    `locks.UNREVIEWED` -- that review is still owed, and this is one file of
    the three it has to settle.

    Reentrant, so `create_character`, which holds the lock across the create
    and this mark to make the pair atomic, pays nothing for it."""
    with locks.campaign_lock(cid):
        atomic.write_text(_detached_path(cid),
                          json.dumps(sorted(detached(cid) | {ref}), indent=2) + "\n")


def _undetach(cid: str, ref: str) -> None:
    """Drop a detachment when the record it describes goes.

    Detachment is a statement about a *record*, not about an id: it says this
    campaign's copy is its own. Delete that copy and the statement has no
    subject -- but it would keep suppressing the per-file resolvers, so a later
    world record of the same slug would list in the campaign with its images
    and sidecars hidden (Codex review).

    UNDER `campaign_lock`, for `add_detached`'s reason: this rewrites the whole
    ledger to remove one ref, so an unserialized run of it drops whatever
    markers landed since it read -- including an actor marker it has nothing to
    do with, since entity and greeting deletes come through here too. Locking
    only the writers that ADD would leave the file racing the ones that
    subtract."""
    with locks.campaign_lock(cid):
        keep = detached(cid) - {ref}
        if keep != detached(cid):
            atomic.write_text(_detached_path(cid), json.dumps(sorted(keep), indent=2) + "\n")
    # Images deleted while detached tombstoned SLOTS of a record that is now
    # gone. Reattaching without clearing them inherits the world's replacement
    # with its avatar still hidden (Codex review) -- the same reasoning as the
    # sweep's own clearing, at the other end of a detachment.
    _drop_deleted(cid, {r for r in deleted(cid) if r.startswith(f"assets/{ref}/")})


# ---- flat records (locations / lore; greetings + plotmap join in Task 2) ----

def _flat_ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _inherits_world(cid: str, ref: str) -> bool:
    """Would this campaign read the world's record of that id, if it had none?

    False once the ref is tombstoned, and false once it is detached. The second
    is what keeps a delete honest: a tombstone exists to stop the world's copy
    showing through, and a detached record has no world copy to stop -- whatever
    holds the id now is a stranger. Tombstoning on its way out would hide that
    stranger from the campaign permanently, which deleting an un-detached
    campaign-local record never does (Codex review)."""
    return ref not in deleted(cid) and ref not in detached(cid)


def _flat_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


@contextmanager
def _recorded_base(cid: str, ref: str, base: str, commit: Path):
    """Record `ref`'s sync base in sync.md, then make the copy it describes.
    `commit` is the copy's last write — the file whose existence *is* the
    materialization.

    Base first, copy second, because the two writes cannot be made one and a
    crash between them has to land on the harmless side (#247):

    - *base, no copy* — the record is still un-materialized, so it reads
      through to the world live and sync skips it (`mine_h is None`). The next
      materialization overwrites the base. Self-healing.
    - *copy, no base* — permanent silent divergence. Every ref the sync engine
      considers comes from the manifest, so that record never sees a world
      edit again, and nothing ever notices.

    The copy's own last write is therefore the commit point: for an actor that
    is character.md / pc.md, which is what `actor_root` keys on — version files
    landing without it leave the actor inherited, exactly as if the copy had
    not begun.

    All of which describes a campaign the slim migration has already reached.
    Before it, sync.md is that migration's inventory of the pre-overlay full
    copy, and *base, no copy* is the one thing it cannot survive: it reads as a
    record the user deleted and tombstones it (#270).

    Neither ordering is safe there, so the base is RESERVED before the copy and
    recorded after it. The reservation is the empty hash — the value the fork
    itself wrote for anything it did not copy — and `ensure_campaign_slim`
    already refuses to attribute one to a user, because nothing was ever copied
    for the user to have deleted. That makes both residues survivable:

    - *reserved, no copy* — the migration drops the ref, leaving the record
      inherited. Same self-healing shape as *base, no copy* past the migration.
    - *reserved, copy* — the migration keeps the record and its reservation, so
      it stays visible to sync, which offers it as a conflict to resolve. Noisy,
      and the point: swapping the writes instead would leave a copy no ref
      names, and once the world moved on the sweep could no longer recognize it
      as residue — permanent silent divergence, the very thing above (Codex
      review).

    An exception, unlike a crash, can unwind, so undo the base then. It restores
    what it displaced rather than dropping the ref: an earlier interrupted
    attempt may have left a base there.

    But undo only while `commit` is absent. An asynchronous exception —
    KeyboardInterrupt, a worker shutdown — can arrive after the commit write
    returned, and an unconditional undo would then strip the base off a copy
    that did land: the very state this ordering exists to prevent. A concurrent
    materialization of the same ref that finished while ours failed is the same
    case (Codex review).
    """
    reserving = campaigns_read.slim_pending(cid)
    manifest = campaigns_paths.read_manifest(cid)
    previous = manifest.get(ref)
    manifest[ref] = RESERVED_BASE if reserving else base
    campaigns_paths.write_manifest(cid, manifest)
    try:
        yield
    except BaseException:
        if not commit.exists():
            manifest = campaigns_paths.read_manifest(cid)
            if previous is None:
                manifest.pop(ref, None)
            else:
                manifest[ref] = previous
            try:
                campaigns_paths.write_manifest(cid, manifest)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                pass   # the copy's failure is the one worth raising
        elif reserving:
            # The copy landed and the exception arrived after it, so the
            # reservation now describes a real copy and has to be redeemed --
            # the same reasoning as leaving the base alone above.
            try:
                _put_base(cid, ref, base)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                pass
        raise
    if reserving:
        _put_base(cid, ref, base)


@contextmanager
def recorded_base(cid: str, ref: str, base: str, commit: Path):
    """`_recorded_base` for a copy that lands somewhere other than this campaign.

    Same contract, same crash ordering, one difference in what `commit` names:
    for a materialization it is the campaign file being written, and for a
    *promotion* (`sync.promote`) it is the world file. The reasoning survives
    the swap unchanged, because it only ever asks "did the copy this base
    describes actually land?" -- and the answer is read off `commit` either way.

    Public because the promoting caller lives in `store/sync.py`: base-hash
    discipline is the one part of moving a record that must not be re-derived
    at a second call site, which is the whole argument #52 makes for doing the
    move server-side at all.
    """
    with _recorded_base(cid, ref, base, commit):
        yield


def record_text(cid: str, kind: str, eid: str) -> str | None:
    """The bytes of the campaign's OWN copy of a flat record, or None when it
    has none and reads the world's. Deliberately unresolved: a promotion copies
    what this campaign holds, and inheriting content back up to the world it
    came from is the one thing it must never do."""
    if not safe_id(eid):
        return None
    p = _flat_path(croot_of(cid), kind, eid)
    return p.read_text(encoding="utf-8") if p.exists() else None


def actor_snapshot(cid: str, kind: str, aid: str):
    """`characters.snapshot` / `pcs.snapshot` of the campaign's own actor dir,
    or None when the actor is still inherited. Unresolved for the same reason
    as `record_text`."""
    if not safe_id(aid):
        return None
    snap = characters.snapshot if kind == "characters" else pcs.snapshot
    return snap(croot_of(cid), aid)


def record_dir(cid: str, kind: str, rid: str) -> Path:
    """The campaign's `<kind>/<rid>/` -- assets and the record's sidecars."""
    return _record_dir(croot_of(cid), kind, rid)


def undetach(cid: str, ref: str) -> None:
    """Drop a detachment because the record has a world ancestor again.

    The inverse of what `forget_world_record` does on a world-side delete, and
    the only thing that legitimately reverses it: `detached` means "mine, and
    whatever holds that id in the world is a stranger", which stops being true
    the moment this campaign's own copy becomes that world record (#52)."""
    _undetach(cid, ref)


def dependent_campaigns(wroot: Path) -> list[str]:
    """Ids of the campaigns that inherit from `wroot`. See `_dependent_campaigns`."""
    return _dependent_campaigns(wroot)


def copy_record_dir_down(cid: str, kind: str, rid: str) -> None:
    """Give this campaign its own copy of everything filed beside a world
    record — its `assets/`, and for a greeting its localized images.

    `_materialize_flat` deliberately does not do this: assets overlay per file,
    so a campaign that merely diverged on TEXT should keep reading the world's
    pictures rather than forking them. That reasoning ends the moment the world
    record is about to be deleted (`sync.demote`), because the files it points
    at go with it — leaving the campaigns holding the demoted record with its
    text and none of its art, permanently and with nothing to say so.

    Two rules, both the overlay's own, and both the reason this lives here
    rather than in the caller:

    - **A file the campaign already has wins**, and is not overwritten. That is
      the whole per-file overlay rule, and `image_root` checks the campaign's
      own file *before* anything else, so copying over one would replace a
      picture this campaign chose with the world's.
    - **A tombstoned asset stays gone.** `image_root` checks the campaign file
      first and the tombstone second, so a blind copy would hand back exactly
      the image the user deleted here — the deletion undone by an operation
      aimed at a different record entirely.
    """
    if kind not in INHERITED_KINDS or not safe_id(rid):
        return
    src = _record_dir(wroot_of(cid), kind, rid)
    if not src.is_dir():
        return
    dst = _record_dir(croot_of(cid), kind, rid)
    gone = deleted(cid)
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if _tombstoned_asset(kind, rid, rel, gone):
            continue
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Through the helper, not `shutil.copy2`, for the reason
        # `sheets/tally.py` gives at the identical world->campaign copy: a
        # partial copy must never appear under a real name. Here that name is
        # an image slot the overlay resolves campaign-first, so a truncated
        # file would not merely be broken -- it would SHADOW the world's intact
        # one for as long as it sat there.
        atomic.write_bytes(target, p.read_bytes())


def _tombstoned_asset(kind: str, rid: str, rel: Path, gone: set[str]) -> bool:
    """Does a per-asset tombstone hide `rel`? `assets/<vid>/<name>.<ext>` is the
    layout `assets._dir` writes, and `_asset_ref` keys the tombstone on
    (base, id, vid, name) — the extension is not part of it, because deleting
    an image and uploading a different format of it is the same slot."""
    parts = rel.parts
    if len(parts) != 3 or parts[0] != "assets":
        return False
    return _asset_ref(kind, rid, parts[1], Path(parts[2]).stem) in gone


def _put_base(cid: str, ref: str, base: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    manifest[ref] = base
    campaigns_paths.write_manifest(cid, manifest)


def _materialize_flat(cid: str, kind: str, eid: str) -> bool:
    """Copy an inherited flat record into the campaign and record its sync
    base. True if the campaign file exists afterwards. Assets are never
    copied — they overlay per file. Tombstoned records don't materialize."""
    croot = croot_of(cid)
    if _flat_path(croot, kind, eid).exists():
        return True
    wroot = wroot_of(cid)
    src = _flat_path(wroot, kind, eid)
    if not src.exists() or _flat_ref(kind, eid) in deleted(cid):
        return False
    dst = _flat_path(croot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    # hashed from `text`, not re-read from the world: a world edit between the
    # two would otherwise record a base for content the campaign never got, and
    # sync would see world == base and skip the record forever (Codex review)
    with _recorded_base(cid, _flat_ref(kind, eid), entities.content_hash(text), dst):
        atomic.write_text(dst, text)
    return True


def dematerialize(cid: str, ref: str) -> None:
    """Remove the campaign copy of `ref` so the record reverts to inherited —
    the ref-level inverse of materialization, over all three shapes sync.md
    names. The manifest is the caller's: drop the ref before calling this while
    `campaigns.read.slim_pending` holds, and after it otherwise (#247, #270)."""
    kind, _, eid = ref.partition("/")
    if ref == "plotmap":
        (croot_of(cid) / "plotmap.json").unlink(missing_ok=True)
    elif kind in appearances_paths.ACTOR_KINDS:
        dematerialize_actor(cid, kind, eid)
    else:
        _flat_path(croot_of(cid), kind, eid).unlink(missing_ok=True)


def _drop_manifest_ref(cid: str, ref: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    if manifest.pop(ref, None) is not None:
        campaigns_paths.write_manifest(cid, manifest)


def materialize_entity(cid: str, kind: str, eid: str) -> None:
    if not _materialize_flat(cid, kind, eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")


def list_entities(cid: str, kind: str) -> list[dict]:
    mine = entities.list_entities(croot_of(cid), kind)
    have = {e["id"] for e in mine}
    gone = deleted(cid)
    inherited = [e for e in entities.list_entities(wroot_of(cid), kind)
                 if e["id"] not in have and _flat_ref(kind, e["id"]) not in gone]
    return sorted(mine + inherited, key=lambda e: e["id"])


def read_entity(cid: str, kind: str, eid: str) -> dict:
    try:
        return entities.read_entity(croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _flat_ref(kind, eid) in deleted(cid):
            raise
        return entities.read_entity(wroot_of(cid), kind, eid)


def read_entity_rev(cid: str, kind: str, eid: str) -> dict:
    """`read_entity` carrying the rev of whichever layer answered (#35)."""
    try:
        return entities.read_entity_rev(croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _flat_ref(kind, eid) in deleted(cid):
            raise
        return entities.read_entity_rev(wroot_of(cid), kind, eid)


def entity_rev(cid: str, kind: str, eid: str) -> str | None:
    """The rev `read_entity_rev` would return now, without reading the record.

    Resolved through the overlay for the same reason the read is: an inherited
    record's rev describes the *world* file, and a save against it materializes
    from that file. So a world-side edit arriving between the campaign editor's
    read and its save is a stale write like any other -- the copy it would make
    is not the text the user was shown.
    """
    h = entities.entity_hash(croot_of(cid), kind, eid)
    if h is not None:
        return h
    if _flat_ref(kind, eid) in deleted(cid):
        return None
    return entities.entity_hash(wroot_of(cid), kind, eid)


def entity_root(cid: str, kind: str, eid: str) -> Path:
    """Root whose `<kind>/<eid>.md` is the record this campaign reads.

    The flat-record twin of `actor_root`, and it resolves the way `read_entity`
    does rather than the way `_inherits_world` does: a *detached* record is one
    the campaign holds itself, so its own copy answers before the question of
    inheritance comes up at all. A tombstoned record resolves to the campaign,
    where there is no file, so the caller's read raises its usual NotFound.
    """
    croot = croot_of(cid)
    # `kind` and `eid` are path parameters, and a path parameter can carry an
    # encoded slash: the resolvers refuse those, so this must not build a path
    # out of one and stat it. Refusing here and letting the caller's
    # `require_entity` raise keeps both answers the same 404.
    if kind in entities.ENTITY_KINDS and safe_id(eid) and _flat_path(croot, kind, eid).exists():
        return croot
    if _flat_ref(kind, eid) in deleted(cid):
        return croot
    return wroot_of(cid)


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "", sd_prompt: str = "", fields: dict | None = None,
                  secrecy: str = "") -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(eid: str) -> bool:
        # the world's record DIRECTORY counts: a sweep that could not remove it
        # leaves assets this campaign would inherit through the overlay the
        # moment it claimed the slug (Codex review)
        return (_flat_path(wroot, kind, eid).exists() or _record_dir(wroot, kind, eid).is_dir()
                or _flat_ref(kind, eid) in gone)

    eid = entities.create_entity(croot_of(cid), kind, name, body, keys, owners,
                                 sd_prompt=sd_prompt, taken=taken, fields=fields,
                                 secrecy=secrecy)
    # Campaign-scoped entity work is work on the campaign, but it writes only
    # overlay records -- neither campaign.md nor any scene -- so the derived
    # activity high-water mark would not see an evening of lore or cast
    # editing at all. Stamped after the write, and never fatal to it.
    return eid


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None, fields: dict | None = None,
                  secrecy: str | None = None) -> None:
    croot = croot_of(cid)
    if not _flat_path(croot, kind, eid).exists():
        materialize_entity(cid, kind, eid)
    entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys, owners=owners,
                           fields=fields, secrecy=secrecy)


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
    return (_inherits_world(cid, _flat_ref(kind, eid))
            and _flat_path(wroot_of(cid), kind, eid).exists())


def delete_entity(cid: str, kind: str, eid: str) -> None:
    ref = _flat_ref(kind, eid)
    in_world = would_inherit(cid, kind, eid)
    try:
        entities.delete_entity(croot_of(cid), kind, eid)
        _drop_manifest_ref(cid, ref)
    except entities.EntityNotFound:
        if not in_world:
            raise
    if in_world:
        add_deleted(cid, ref)   # keep the world's copy from showing through
    _drop_record_dir(croot_of(cid), kind, eid)
    _undetach(cid, ref)


def has_own_copy(cid: str, kind: str, eid: str) -> bool:
    """Does this campaign hold its own file for `<kind>/<eid>`, rather than
    reading the world's? The question a world-side sweep has to ask before it
    touches a campaign: a campaign that never materialized the record has
    nothing to move, and materializing one now would fork it off the world for
    a change it agreed with."""
    return kind in entities.ENTITY_KINDS and safe_id(eid) and _flat_path(croot_of(cid), kind, eid).exists()


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
        raise entities.SameKindError(_flat_ref(kind, eid))
    croot, wroot = croot_of(cid), wroot_of(cid)
    materialize_entity(cid, kind, eid)

    def taken(c: str) -> bool:
        # the destination's namespace as this campaign sees it: what the world
        # holds there (which the campaign inherits), plus what it has tombstoned
        # (which `create_entity` already counts, for #225's reason)
        return (_flat_path(wroot, new_kind, c).exists()
                or _record_dir(wroot, new_kind, c).is_dir()
                or _flat_ref(new_kind, c) in deleted(cid))

    return entities.reclassify(croot, kind, eid, new_kind, taken=taken, prefer=prefer)


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
        for meta in list_entities(cid, kind):
            refs = entities.owner_refs(meta.get("owners", ""))
            if old not in refs:
                continue
            rewritten: list[str] = []
            for ref in refs:
                candidate = new if ref == old else ref
                if candidate not in rewritten:
                    rewritten.append(candidate)
            update_entity(cid, kind, meta["id"], owners=", ".join(rewritten))
            touched.append((kind, meta["id"]))
    return touched


def repoint_record(cid: str, old_ref: str, new_ref: str, *, keep_base: bool) -> None:
    """Follow a reclassified record through the three ledgers that say what this
    campaign's copy *is* relative to its world: `sync.md`, `deleted.json` and
    `detached.json` (#119).

    They stay here rather than joining `record_refs`' fan-out because each is a
    statement about inheritance, and only the caller knows whether the world
    moved too -- which is exactly what `keep_base` says. **True** (a world-side
    reclassify) carries the sync base over to the new key: the world file's
    bytes did not change, only its kind directory, so the hash the base records
    still describes it and the campaign's copy is still in sync or still
    diverged, unchanged. **False** (a campaign-side one) drops it: the world
    record is still filed under the old kind, so there is no longer a world
    record at this ref for a base to be about, and a base left standing would
    have `sync.incoming` compare this record against whatever claims the old
    slug next.

    Both tombstone shapes move: the whole-record one, and the per-asset
    `assets/<kind>/<id>/<version>/<name>` slots, which hide by slot and would
    otherwise blank an image of the record under its new kind while un-hiding
    the one it was hidden under. A detachment moves for the same reason -- it is
    a statement about a record, and the record is still here.
    """
    if old_ref == new_ref:
        return
    manifest = campaigns_paths.read_manifest(cid)
    base = manifest.pop(old_ref, None)
    if base is not None:
        if keep_base:
            manifest[new_ref] = base
        campaigns_paths.write_manifest(cid, manifest)
    old_assets, new_assets = f"assets/{old_ref}/", f"assets/{new_ref}/"
    gone = deleted(cid)
    moved = {r for r in gone if r == old_ref or r.startswith(old_assets)}
    if moved:
        kept = (gone - moved) | {new_ref if r == old_ref else new_assets + r[len(old_assets):]
                                 for r in moved}
        atomic.write_text(_deleted_path(cid), json.dumps(sorted(kept), indent=2) + "\n")
    severed = detached(cid)
    if old_ref in severed:
        atomic.write_text(_detached_path(cid),
                          json.dumps(sorted((severed - {old_ref}) | {new_ref}), indent=2) + "\n")


# ---- greetings + plot map ----

def list_greetings(cid: str) -> list[dict]:
    mine = greetings.list_greetings(croot_of(cid))
    have = {g["id"] for g in mine}
    gone = deleted(cid)
    inherited = [g for g in greetings.list_greetings(wroot_of(cid))
                 if g["id"] not in have and _flat_ref("greetings", g["id"]) not in gone]
    out = mine + inherited
    out.sort(key=lambda m: natural_key(m["name"]))
    return out


def read_greeting(cid: str, gid: str) -> dict:
    try:
        return greetings.read_greeting(croot_of(cid), gid)
    except greetings.GreetingNotFound:
        if _flat_ref("greetings", gid) in deleted(cid):
            raise
        return greetings.read_greeting(wroot_of(cid), gid)


def read_greeting_rev(cid: str, gid: str) -> dict:
    """`read_greeting` carrying the rev of whichever layer answered (#35).

    Its write-side counterpart is `entity_rev(cid, "greetings", gid)`: greetings
    are a flat `<root>/greetings/<id>.md` record with the same overlay layering
    as any entity, so the resolution rule is one rule, not two.
    """
    try:
        return greetings.read_greeting_rev(croot_of(cid), gid)
    except greetings.GreetingNotFound:
        if _flat_ref("greetings", gid) in deleted(cid):
            raise
        return greetings.read_greeting_rev(wroot_of(cid), gid)


def create_greeting(cid: str, name: str, character: str, version: str, body: str = "",
                    requires_tags=None, predecessor_join: str = "all",
                    present=None, pcless: bool = False, location: str = "") -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(gid: str) -> bool:
        return (_flat_path(wroot, "greetings", gid).exists()
                or _record_dir(wroot, "greetings", gid).is_dir()
                or _flat_ref("greetings", gid) in gone)

    # #137: bake {{char}} here, not inside greetings.create_greeting -- a thin
    # campaign's character commonly still lives only in the world, and
    # char_root (not croot_of) is the resolver that finds it there.
    body = cards.bake_char_token(body, greetings.char_name(char_root(cid, character), character, version))
    gid = greetings.create_greeting(croot_of(cid), name, character, version, body,
                                    requires_tags, predecessor_join, present=present,
                                    pcless=pcless, location=location, taken=taken)
    return gid


def update_greeting(cid: str, gid: str, **kwargs) -> None:
    if not _materialize_flat(cid, "greetings", gid):
        raise greetings.GreetingNotFound(gid)
    if kwargs.get("body") is not None:
        meta = greetings.read_greeting(croot_of(cid), gid)["meta"]
        character = meta.get("character", "")
        name = greetings.char_name(char_root(cid, character), character, meta.get("version", ""))
        kwargs["body"] = cards.bake_char_token(kwargs["body"], name)
    greetings.update_greeting(croot_of(cid), gid, **kwargs)


def delete_greeting(cid: str, gid: str) -> None:
    ref = _flat_ref("greetings", gid)
    in_world = _inherits_world(cid, ref) and _flat_path(wroot_of(cid), "greetings", gid).exists()
    if in_world:
        materialize_plotmap(cid)   # edge cleanup must land campaign-side
    try:
        greetings.delete_greeting(croot_of(cid), gid)
        _drop_manifest_ref(cid, ref)
    except greetings.GreetingNotFound:
        if not in_world:
            raise
        greetings.remove_from_plotmap(croot_of(cid), gid)
    if in_world:
        add_deleted(cid, ref)
    _drop_record_dir(croot_of(cid), "greetings", gid)
    _undetach(cid, ref)


def read_plotmap(cid: str) -> dict:
    croot = croot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        # The campaign's OWN map. Its nodes and edges name the campaign's own
        # greetings, detached or not, and filtering them would delete plot
        # relationships the campaign authored (Codex review).
        return greetings.read_plotmap(croot)
    return _without_detached(cid, greetings.read_plotmap(wroot_of(cid)))


def _without_detached(cid: str, plotmap: dict) -> dict:
    """Drop detached greetings from a plot map, node and inbound edges alike.

    Only ever applied to an INHERITED map. A campaign that owns a greeting copy
    but no plot map of its own reads the WORLD's, and the world is where the
    recreated slug's edges are. Materializing a campaign map to clean would fork
    it off the world's over an unrelated record's delete, and would fix only the
    campaigns that had already been swept (Codex review)."""
    gone = {r.partition("/")[2] for r in detached(cid) if r.startswith("greetings/")}
    if not gone:
        return plotmap
    return {gid: {k: [x for x in v if x not in gone] if isinstance(v, list) else v
                  for k, v in edges.items()}
            for gid, edges in plotmap.items() if gid not in gone}


def materialize_plotmap(cid: str) -> None:
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        return
    src = wroot / "plotmap.json"
    if not src.exists():
        return   # nothing to copy; set_edges will create a fresh campaign map
    text = src.read_text(encoding="utf-8")
    dst = croot / "plotmap.json"
    with _recorded_base(cid, "plotmap", greetings.plotmap_content_hash(text), dst):
        atomic.write_text(dst, text)


def set_edges(cid: str, gid: str, leads_to=None, excludes=None) -> None:
    materialize_plotmap(cid)
    greetings.set_edges(croot_of(cid), gid, leads_to, excludes)


# ---- actors (characters / pcs): whole-dir resolution keyed on the container meta ----

def _actor_meta(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _actor_not_found(kind: str, aid: str) -> Exception:
    return characters.CharacterNotFound(aid) if kind == "characters" else pcs.PCNotFound(aid)


def actor_root(cid: str, kind: str, aid: str) -> Path:
    """Root whose <kind>/<aid> dir is authoritative for meta + version files.
    Tombstoned actors resolve to the campaign, where the caller's read raises
    its usual NotFound."""
    croot = croot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return croot
    if _flat_ref(kind, aid) in deleted(cid):
        return croot
    return wroot_of(cid)


def char_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "characters", aid)


def pc_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "pcs", aid)


def materialize_actor(cid: str, kind: str, aid: str) -> None:
    """Copy meta + every version file (never assets or sidecars) from the world
    and record the whole-actor sync base. No-op when already materialized."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return
    # one read of the world actor: the base and the bytes it covers, so the two
    # cannot disagree even if the world moves mid-copy (Codex review)
    taken = (characters.snapshot if kind == "characters" else pcs.snapshot)(wroot, aid)
    if taken is None or _flat_ref(kind, aid) in deleted(cid):
        raise _actor_not_found(kind, aid)
    base, files = taken
    meta_name = _actor_meta(kind)
    dst = croot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    # No meta here means no materialized actor, so any version file in the
    # campaign dir is residue from an interrupted copy. Drop it: overwriting
    # only what the world still has would resurrect a version purged in
    # between, and the base we are about to record excludes it (Codex review).
    ext = "json" if kind == "characters" else "md"
    for p in dst.glob(f"*.{ext}"):
        if p.name != meta_name:
            p.unlink()
    with _recorded_base(cid, _flat_ref(kind, aid), base, dst / meta_name):
        # the meta is the single commit point, so it goes last -- `snapshot`
        # puts it first, and for a PC it is an *.md sibling of the versions
        for name, text in files:
            if name != meta_name:
                atomic.write_text(dst / name, text)
        atomic.write_text(dst / meta_name, dict(files)[meta_name])


def ensure_actor_writable(cid: str, kind: str, aid: str) -> Path:
    """Materialize an inherited actor and return the campaign root writes target.

    Stamps campaign activity, because this is the chokepoint every
    campaign-scoped actor write already passes through to obtain a writable
    root -- character default-version, version create/update/delete, and the
    PC routes. Putting it here rather than in each of those eight routes is
    deliberate: an enumerated list of mutators is what left `activity` claiming
    more than it delivered three times over, and the same lesson is already
    written down for the campaign lock domain in locks.py.

    The trade is that this runs *before* the caller's write, so an edit that
    then 404s still records activity. That is the acceptable direction: asking
    to make an actor writable is itself campaign work -- on the first call it
    materializes files that were not there -- and an over-eager ordering hint
    costs a row's position in a list, where a missed one loses work the user
    can see they did.
    """
    croot = croot_of(cid)
    if not (croot / kind / aid / _actor_meta(kind)).exists():
        materialize_actor(cid, kind, aid)
    return croot


def dematerialize_actor(cid: str, kind: str, aid: str) -> None:
    """Remove meta + version files so the actor reverts to inherited. Sidecars
    (tagline/dossier/state) and assets stay — they overlay per file. PCs carry
    no sidecar .md files, so all *.md go.

    The meta file goes FIRST, and that ordering is the whole safety of this
    function. It is the commit point `actor_root` keys on, so while it is there
    the campaign claims to have materialized this actor. Unlinking the versions
    first and it last meant a kill between the two left a directory that still
    said "materialized" and held no version to read: `read_character` raises,
    while `list_characters` skips version-less dirs so the world's copy shows
    through the overlay and the actor stays in the roster. A character that
    lists but cannot be opened, edited or re-materialized — and nothing repairs
    it, because the migration only revisits refs sync.md still holds and this
    one's ref is already gone (Codex review, #270).

    Meta-first inverts that into a state the store already understands: version
    files with no meta read as inherited, exactly as an interrupted *copy*
    leaves them, and `materialize_actor` purges them on the next copy."""
    d = croot_of(cid) / kind / aid
    if not d.exists():
        return
    meta = _actor_meta(kind)
    rest = (list(d.glob("*.json")) if kind == "characters"
            else [p for p in d.glob("*.md") if p.name != meta])
    for p in [d / meta, *rest]:
        if p.exists():
            p.unlink()
    if not any(d.iterdir()):
        d.rmdir()


def _patch_char_item(cid: str, item: dict) -> dict:
    images = list_images(cid, item["id"], item["default_version"])
    names = [i["name"] for i in images]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            # From the union, like every other field here: a campaign row whose
            # avatar lives world-side would otherwise carry the campaign root's
            # token for a file it does not have, and `?v=` caches immutable.
            "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR), None),
            "avatar_focus": read_focus(cid, item["id"], item["default_version"]),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
            "localized_count": sum(1 for n in names if n.startswith("embed-")),
            "tagline": tagline(cid, item["id"])}


def list_characters(cid: str) -> list[dict]:
    mine = characters.list_characters(croot_of(cid))
    # dossier/state-only dirs have no character.md and are filtered by
    # characters.list_characters itself (it requires the meta file)
    have = {c["id"] for c in mine}
    gone = deleted(cid)
    inherited = [c for c in characters.list_characters(wroot_of(cid))
                 if c["id"] not in have and _flat_ref("characters", c["id"]) not in gone]
    return sorted([_patch_char_item(cid, c) for c in mine + inherited], key=lambda c: c["id"])


def _patch_pc_item(cid: str, item: dict) -> dict:
    """The PC counterpart of `_patch_char_item`: `pcs.list_pcs` computed these
    against one root, but a thin campaign's PC can hold its images world-side,
    so the derived fields have to come from the overlay union (#219).

    That does mean the store-level scan is always discarded here -- two asset
    scans per row for one answer. It is kept rather than parameterised away
    because `pcs.list_pcs` is also the world route's answer, where the values
    it computes are the ones served; `_patch_char_item` has carried the same
    cost since characters got theirs."""
    names = [i["name"] for i in list_images(cid, item["id"], item["default_version"],
                                            pcs.ASSET_BASE)]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            "avatar_focus": read_focus(cid, item["id"], item["default_version"],
                                       pcs.ASSET_BASE),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_"))}


def list_pcs(cid: str) -> list[dict]:
    mine = pcs.list_pcs(croot_of(cid))
    have = {p["id"] for p in mine}
    gone = deleted(cid)
    inherited = [p for p in pcs.list_pcs(wroot_of(cid))
                 if p["id"] not in have and _flat_ref("pcs", p["id"]) not in gone]
    return sorted([_patch_pc_item(cid, p) for p in mine + inherited], key=lambda p: p["id"])


def character_refs(cid: str) -> list[str]:
    return [c["id"] for c in list_characters(cid)]


def _mark_campaign_owned(cid: str, kind: str, aid: str) -> None:
    """Record a campaign-created actor as the campaign's own outright (#225).

    The `taken` guards below allocate the id against the world *as it stands*,
    so a campaign-created id names no world record at birth -- which is exactly
    what makes a world record claiming that id **later** a stranger. That is
    the same position a spared copy is in once its world original is deleted,
    and `detached` is the word this module already has for it; only the end the
    collision arrives from is different.

    Without this, a campaign's invented Winifred took a later world Winifred's
    avatar, tagline, voice anchor and sync updates by coincidence of slug --
    every item `detached`'s own docstring lists -- and
    `appearances.actor_source` read her as that stranger's record diverged
    rather than as the campaign's own (Codex review on #99).

    A no-op until such a collision happens: with nothing under the id in the
    world there is nothing for the per-file resolvers to inherit and nothing
    for sync to offer. That is what keeps the blast radius to the bug.

    After the create, never before. A marker orphaned by a failed create would
    suppress inheritance for whatever holds that id next -- failure *adding*
    content, the one direction a user cannot spot by looking, which is the
    argument `detached` itself makes. Crashing between the two leaves an
    unmarked campaign actor: what every one of them was until now.

    Callers hold `campaign_lock` across both halves, which is what makes "after
    the create" a window rather than a race. `add_detached` is an unserialized
    read-modify-replace, so two concurrent creates in one campaign would each
    read the same ledger and the later write would drop the earlier marker --
    silently un-fixing one actor (Codex review). The lock also closes the older
    hazard underneath it: `uniquify` allocates against the filesystem, so those
    same two creates could hand out one id twice.

    Which is the boundary: this marks actors created from here on, and there is
    deliberately no startup backfill for the ones already on disk. The rule a
    backfill would need -- "the world has no actor under this id, so detach" --
    cannot tell a campaign-created actor from a store whose `worlds/` has not
    finished syncing, and `store.home()` is pointed at a synced folder on
    purpose (CLAUDE.md). Detaching is one-way, so guessing wrong there would
    permanently cut a campaign off from its own library to pre-empt a
    collision that needs the user to name a world character after one their
    campaign invented. `appearances.actor_source` still reads an unmarked
    campaign actor correctly until such a collision, through the world-side
    existence check it keeps as a fallback for exactly this data.

    One consequence to carry forward: promoting a campaign-made character into
    the library (#60) now has to CLEAR this marker as well as advance the sync
    base. A promoted character does have a world record behind her, and leaving
    her detached would cut her off from the very library she was just added to.
    """
    add_detached(cid, _flat_ref(kind, aid))


def create_character(cid: str, name: str, version_name: str = "default",
                     card: dict | None = None) -> tuple[str, str]:
    """Create a character this campaign owns. UNDER `campaign_lock` -- see
    `_mark_campaign_owned`, and `set_description` above for why new code in
    this module takes it rather than riding `locks.UNREVIEWED`."""
    with locks.campaign_lock(cid):
        # Inside the hold, all of it: the id is allocated against what `taken`
        # sees, so reading the world dir and the tombstones outside the lock
        # would be the same check-then-act the lock is here to close.
        wroot, gone = wroot_of(cid), deleted(cid)

        def taken(aid: str) -> bool:
            return ((wroot / "characters" / aid).is_dir()
                    or _flat_ref("characters", aid) in gone)

        made = characters.create_character(croot_of(cid), name, version_name, card, taken=taken)
        _mark_campaign_owned(cid, "characters", made[0])
    return made


def delete_actor(cid: str, kind: str, aid: str) -> None:
    """Delete an actor campaign-side — the actor twin of `delete_entity`.

    The campaign-scoped create routes have existed for PCs for a while and for
    characters since #60, and neither had a delete: `deleteCharacter` targets
    the world, and version-delete refuses the last one, so an NPC invented by
    mistake could not be removed at all (Codex review).

    Same three cases `delete_entity` distinguishes, for the same reasons:

    - **Inherited from the world** — nothing to unlink, so the tombstone IS the
      delete; it keeps the world's actor from showing back through.
    - **A campaign copy of a world actor** — the copy goes and the tombstone
      goes on, so the world's does not resurface.
    - **Campaign-local** (the emergent NPC this exists for) — the directory
      goes and no tombstone is written, because there is nothing to hide.

    The appearance record goes with it either way. It holds a version lock and
    a per-version sync base for an actor that no longer exists here, and
    `_actor_incoming` reads that record in preference to sync.md -- left
    behind, it offers updates for a deleted actor forever.
    """
    ref = _flat_ref(kind, aid)
    in_world = _inherits_world(cid, ref) and _record_present(wroot_of(cid), kind, aid)
    mine = _record_dir(croot_of(cid), kind, aid)
    if not _record_present(croot_of(cid), kind, aid) and not in_world:
        raise _actor_not_found(kind, aid)
    if mine.is_dir():
        shutil.rmtree(mine, ignore_errors=True)
    _drop_manifest_ref(cid, ref)
    appearances_paths.forget(cid, kind, aid)
    if in_world:
        add_deleted(cid, ref)
    _undetach(cid, ref)


def create_pc(cid: str, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    """The PC counterpart of `create_character`, lock and all."""
    with locks.campaign_lock(cid):
        wroot, gone = wroot_of(cid), deleted(cid)

        def taken(pid: str) -> bool:
            return (wroot / "pcs" / pid).is_dir() or _flat_ref("pcs", pid) in gone

        made = pcs.create_pc(croot_of(cid), name, tags, version_name, persona, taken=taken)
        _mark_campaign_owned(cid, "pcs", made[0])
    return made


# ---- assets: per-file union, campaign wins ----

def _asset_ref(base: str, aid: str, vid: str, name: str) -> str:
    return f"assets/{base}/{aid}/{vid}/{name}"


def list_images(cid: str, aid: str, vid: str, base: str = "characters") -> list[dict]:
    mine = assets.list_images(croot_of(cid), aid, vid, base)
    if _flat_ref(base, aid) in detached(cid):
        return sorted(mine, key=lambda i: i["name"])   # the world's id-mate is a stranger
    have = {i["name"] for i in mine}
    gone = deleted(cid)
    inherited = [i for i in assets.list_images(wroot_of(cid), aid, vid, base)
                 if i["name"] not in have and _asset_ref(base, aid, vid, i["name"]) not in gone]
    return sorted(mine + inherited, key=lambda i: i["name"])


def image_root(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> Path:
    croot = croot_of(cid)
    if assets.image_path(croot, aid, vid, name, base) is not None:
        return croot
    gone = deleted(cid)
    # A per-asset tombstone or a whole-record tombstone (the record was deleted
    # campaign-side; only its <base>/<aid> ref is written) both hide the image:
    # return croot so the serve route 404s instead of falling through to the world.
    if (_asset_ref(base, aid, vid, name) in gone or _flat_ref(base, aid) in gone
            or _flat_ref(base, aid) in detached(cid)):
        return croot
    return wroot_of(cid)


def read_focus(cid: str, aid: str, vid: str, base: str = "characters") -> int | None:
    croot = croot_of(cid)
    focus_file = croot / base / aid / "assets" / vid / assets.FOCUS_FILE
    if (assets.image_path(croot, aid, vid, assets.AVATAR, base) is not None
            or focus_file.exists()
            or _asset_ref(base, aid, vid, assets.AVATAR) in deleted(cid)
            or _flat_ref(base, aid) in detached(cid)):
        return assets.read_focus(croot, aid, vid, base)
    return assets.read_focus(wroot_of(cid), aid, vid, base)


def read_description(cid: str, aid: str, vid: str, name: str,
                     base: str = "characters") -> str:
    """One image's description, resolved campaign-first.

    The rule is per IMAGE, where `read_focus`' is per folder, because that is
    the granularity of the thing: a description is a claim about particular
    bytes.

    - **The campaign holds the image.** Its own sidecar answers, and there is
      NO fallback to the world's. A campaign-side `gallery_1` is different art
      from the world's `gallery_1`, so inheriting the world's sentence about it
      would caption one picture with a description of another -- which, since
      the caption becomes alt text in a transcript, is worse than saying
      nothing.
    - **The image is inherited.** The campaign's sidecar answers if it has a
      key (an author may describe inherited art without diverging the art
      itself), and the world's otherwise.
    - **Tombstoned or detached.** Campaign-side only, matching `read_focus` and
      `image_root`: the world's id-mate is a stranger.

    Reads pass the overlay-resolved union as `names`, or a description written
    campaign-side for an image whose bytes are still inherited would be
    filtered out as naming nothing.
    """
    croot = croot_of(cid)
    union = {i["name"] for i in list_images(cid, aid, vid, base)}
    mine = image_descriptions.read_all(croot, aid, vid, base, names=union)
    if (assets.image_path(croot, aid, vid, name, base) is not None
            or _asset_ref(base, aid, vid, name) in deleted(cid)
            or _flat_ref(base, aid) in detached(cid)):
        return mine.get(name, "")
    if name in mine:            # typed read: a malformed entry is not an answer
        return mine[name]
    return image_descriptions.read(wroot_of(cid), aid, vid, name, base)


def read_descriptions(cid: str, aid: str, vid: str, base: str = "characters") -> dict[str, str]:
    """Every visible image's description for one version, by `read_description`'s
    per-image rule — but resolving the whole version in one pass.

    Written as a sweep rather than a loop over `read_description` because the
    loop was quadratic and it showed: each call re-listed the version's images
    and re-read both sidecars, so a thirty-image character cost ~44ms of pure
    directory scanning. This runs once per cast member per TURN, on the
    synchronous path that blocks the event loop, so that was the difference
    between a rounding error and a fifth of a second of stat() calls per reply.

    One entry per image the overlay lists; an image nobody has reviewed is
    ABSENT rather than empty, so a caller can still tell "not reviewed" from
    "reviewed, nothing to say".
    """
    croot, wroot = croot_of(cid), wroot_of(cid)
    images = list_images(cid, aid, vid, base)
    union = {i["name"] for i in images}
    mine_images = {i["name"] for i in assets.list_images(croot, aid, vid, base)}
    mine = image_descriptions.read_all(croot, aid, vid, base, names=union)
    # From the TYPED read, not `raw_keys`. A campaign sidecar holding a non-string
    # value for an inherited image is dropped by `read_all` but still has a raw
    # key -- and treating that as "the campaign has spoken" turned the malformed
    # entry into `""`, masking the world's perfectly good description and marking
    # the image reviewed-empty. `read_description` never agreed with that: it
    # falls through to the world for the same image.
    mine_keys = set(mine)
    gone, detached_record = deleted(cid), _flat_ref(base, aid) in detached(cid)
    theirs: dict[str, str] = {}
    # Only read the world side if some image might fall through to it -- a fully
    # diverged version never touches it.
    if not detached_record and not union <= mine_images:
        theirs = image_descriptions.read_all(wroot, aid, vid, base, names=union)

    out: dict[str, str] = {}
    for name in sorted(union):
        campaign_side = (name in mine_images or detached_record
                         or _asset_ref(base, aid, vid, name) in gone)
        if name in mine_keys:
            out[name] = mine.get(name, "")
        elif not campaign_side and name in theirs:
            out[name] = theirs[name]
    return out


def set_description(cid: str, aid: str, vid: str, name: str, text: str,
                    base: str = "characters") -> None:
    """Write one image's description campaign-side.

    The write always lands in the campaign, exactly as `put_campaign_avatar_focus`
    lands a focus there, and `read_description` then treats the campaign as
    authoritative for that image going forward. The existence gate is the
    overlay UNION, not this campaign's own directory: a thin campaign reaches
    most of its art through the world, and describing an inherited picture must
    not require diverging the picture.

    UNDER `campaign_lock`, unlike almost everything else in this module.
    `overlay` sits in `locks.UNREVIEWED` — the frozen backlog — so the guard
    does not require it, but riding that grandfather would be taking a licence
    granted to code that predates the lock domain and spending it on new code.
    `store.covers` and `store.campaign_images` are the precedent for what a new
    campaign-scoped mutator does, and both take it.

    It earns it on the merits too: a description sidecar is read-modify-written
    whole, so two unlocked writers lose one of the two — and what is lost here
    is a sentence somebody sat and wrote, not a derived value that regenerates.
    Two browser tabs, or the describe queue saving while an editor saves, is all
    it takes. Reentrant, so a caller already holding it pays nothing.

    The WORLD-side write (`image_descriptions.set_description` straight onto a
    world root) is still unlocked, and that is not an oversight this closes:
    worlds have no lock domain at all, and `focus.json` and `subjects.json` race
    there in exactly the same way. Naming it here rather than leaving the
    asymmetry to be discovered.
    """
    with locks.campaign_lock(cid):
        # The union is resolved INSIDE the lock, and `set_in` re-checks it inside
        # the sidecar lock as well. Computed outside, it was a check-then-act:
        # a slot could be promoted away between the check and the write.
        image_descriptions.set_description(
            croot_of(cid), aid, vid, name, text, base,
            names={i["name"] for i in list_images(cid, aid, vid, base)})


def delete_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    assets.delete_image(croot_of(cid), aid, vid, name, base)   # no-op when absent
    if assets.image_path(wroot_of(cid), aid, vid, name, base) is not None:
        add_deleted(cid, _asset_ref(base, aid, vid, name))


def promote_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    """Copy-up the named image and the current avatar, then swap campaign-side.

    UNDER `campaign_lock`, for `set_description`'s reason and then one of its
    own. The sidecar lock serializes each *write* to the file, which is not
    enough here: this reads the resolved descriptions and writes them back a
    few statements later, so a save landing in that gap was read past and then
    overwritten with the snapshot -- losing text somebody had just written (PR
    review). A read-modify-write needs the lock the other writer takes, and for
    campaign-scoped state that is this one.
    """
    croot, wroot = croot_of(cid), wroot_of(cid)
    with locks.campaign_lock(cid):
        inherits = _flat_ref(base, aid) not in detached(cid)
        # Resolved BEFORE anything moves. Copying the bytes up is what makes
        # this campaign hold the picture, and from that moment
        # `read_description` stops falling through to the world for it --
        # deliberately, since a campaign-side image is normally different art.
        # Here it is the SAME art, so a description not carried up with the
        # bytes is one this campaign silently loses the instant somebody
        # promotes the picture (PR review).
        resolved = read_descriptions(cid, aid, vid, base)
        union = {i["name"] for i in list_images(cid, aid, vid, base)}
        for n in (name, assets.AVATAR):
            if not (inherits and assets.image_path(croot, aid, vid, n, base) is None
                    and _asset_ref(base, aid, vid, n) not in deleted(cid)):
                continue
            src = assets.image_path(wroot, aid, vid, n, base)
            if src is None:
                continue
            # The DESCRIPTION first, then the bytes. Describing an image whose
            # bytes are still inherited is an ordinary state of this store, so
            # a failure between the two leaves something coherent; the other
            # order leaves the picture campaign-side with the world's sentence
            # masked, and a retry cannot even see that it has to fix it --
            # `image_path` is non-null by then, so the carry-up is skipped
            # forever (PR review).
            #
            # Key presence, not truthiness: `""` is "reviewed, nothing to say"
            # and has to travel too, or the promoted image walks back into the
            # describe queue somebody has already answered for. `names` is the
            # overlay union, since the bytes are not campaign-side yet.
            if n in resolved:
                image_descriptions.set_description(croot, aid, vid, n, resolved[n],
                                                   base, names=union)
            assets.put_image(croot, aid, vid, n, src.read_bytes(),
                             src.suffix.lstrip("."), base)
        assets.promote_image(croot, aid, vid, name, base)
        # When there was no avatar to swap into the promoted slot, the swap
        # leaves no campaign file at `name`, so the inherited image there would
        # still show through the overlay next to the new avatar. Tombstone it so
        # promotion moves the image out of the gallery instead of duplicating it.
        if (inherits and assets.image_path(croot, aid, vid, name, base) is None
                and assets.image_path(wroot, aid, vid, name, base) is not None):
            add_deleted(cid, _asset_ref(base, aid, vid, name))


# ---- payload patching: asset-derived fields come from the union ----

def read_character(cid: str, char_id: str) -> dict:
    detail = characters.read_character(char_root(cid, char_id), char_id)
    for v in detail["versions"]:
        images = list_images(cid, char_id, v["id"])
        v["images"] = [i["name"] for i in images]
        v["image_v"] = {i["name"]: i["v"] for i in images}
        v["avatar_focus"] = read_focus(cid, char_id, v["id"])
        # Re-derived per image, not per folder: `read_descriptions` refuses to
        # caption a campaign-side picture with the world's sentence about a
        # different one. See its docstring.
        v["image_descriptions"] = read_descriptions(cid, char_id, v["id"])
    return detail


def read_pc(cid: str, pid: str) -> dict:
    """`pcs.read_pc` with its image fields re-derived from the union.

    The persona files resolve whole-directory through `pc_root`, but assets
    overlay per file, so a materialized PC can still be showing an avatar that
    only the world has. Reading the detail off one root would hide it -- the
    same reason `read_character` exists rather than callers using
    `characters.read_character` directly (#219)."""
    detail = pcs.read_pc(pc_root(cid, pid), pid)
    for v in detail["versions"]:
        v["images"] = [i["name"] for i in list_images(cid, pid, v["id"], pcs.ASSET_BASE)]
        v["avatar_focus"] = read_focus(cid, pid, v["id"], pcs.ASSET_BASE)
        v["image_descriptions"] = read_descriptions(cid, pid, v["id"], pcs.ASSET_BASE)
    return detail


def tagline(cid: str, char_id: str) -> str:
    mine = taglines.read(croot_of(cid), char_id)
    if mine or _flat_ref("characters", char_id) in detached(cid):
        return mine
    return taglines.read(wroot_of(cid), char_id)


def set_voice_anchor(cid: str, char_id: str, text: str) -> None:
    """Write the campaign's own anchor for `char_id`; blank opts the character
    out of voice checks in this campaign.

    Campaign-side by definition: this IS the per-file divergence, the same shape
    as any other campaign edit to an inherited record, and it lives here rather
    than at the call site so nothing outside this module hands `voice_anchors` a
    raw campaign root.

    Blank is the one place the anchor overlay does NOT behave like tagline's.
    Deleting the campaign copy would let the world's anchor show through again,
    so a user who cleared the field would still be judged -- against the very
    text they just erased -- while the editor told them clearing skips the
    checks. Blankness is this feature's opt-out switch, so it has to survive
    resolution: a blank save writes a TOMBSTONE.

    The one exception is a blank that erased nothing -- no world anchor, no
    campaign anchor, no tombstone already. There is no decision in that save to
    record, and a tombstone would turn it into a standing promise to ignore an
    anchor the world might add later. Anything else -- an inherited anchor, the
    campaign's own, or an opt-out already made -- is a decision, and once made
    only the user typing an anchor back in may end it. Not the world removing
    its anchor, and not a later save that changed nothing.

    The nonce `voice_anchors.write` preserves is read from the CAMPAIGN copy, so
    a campaign that overrides the world's anchor mints its own identity. That is
    correct rather than incidental: an override is a different standard, and
    findings judged against the world's should stop applying here.
    """
    croot = croot_of(cid)
    inherited = ("" if _flat_ref("characters", char_id) in detached(cid)
                 else voice_anchors.read(wroot_of(cid), char_id))
    mine = voice_anchors.read_record(croot, char_id)
    if not text.strip():
        # Tombstone unless this blank erased nothing at all. Each of the three
        # is an opt-out the resolver would otherwise undo:
        #   inherited        -- the world's anchor would show through again
        #   mine["text"]     -- clearing an override IS the opt-out; deleting it
        #                       re-exposes the world's, or lets a world anchor
        #                       created later start judging a character whose
        #                       owner just erased one
        #   mine["disabled"] -- an opt-out already made, which must outlive the
        #                       world anchor being removed and restored
        if inherited or mine["text"] or mine["disabled"]:
            voice_anchors.disable(croot, char_id)
            return
        voice_anchors.write(croot, char_id, text)   # nothing to erase: just delete
        return
    if not mine["text"] and not mine["disabled"] and text.split() == inherited.split():
        # Still inheriting, and the submitted text IS the inherited text: the
        # editor shows the resolved anchor, so re-saving an untouched form
        # lands here. Materializing a copy would mint a new nonce -- silently
        # suppressing every committed flag fingerprinted against the identical
        # world anchor -- and detach the campaign from later world edits, all
        # for a save that changed nothing. Only a real divergence diverges.
        #
        # `.split()`, not `.strip()`, so "the same anchor" means here exactly
        # what it means to `voice_drift.anchor_fingerprint` -- which normalizes
        # whitespace THROUGHOUT because rewrapping a line is presentation, not a
        # new standard. With `.strip()` the two disagreed, and the disagreement
        # was the bug: rewrapping an inherited anchor materialized a campaign
        # copy whose nonce suppressed every flag judged against text the
        # fingerprint still considered identical. The rewrap itself is not
        # persisted, which is the same outcome as re-saving an untouched form
        # and for the same reason: by the only definition of anchor identity
        # this feature has, nothing changed.
        #
        # Deliberately not extended to a standing TOMBSTONE: re-entering the
        # world's words there is a decision to be judged again, and it gets a
        # fresh identity like any other opt-back-in.
        return
    voice_anchors.write(croot, char_id, text)


def voice_anchor_record(cid: str, char_id: str) -> dict:
    """The winning anchor's {"text", "id"} — same per-file overlay as
    `voice_anchor`, but carrying the nonce a fingerprint needs. Resolved as one
    record so text and identity always come from the SAME file: reading the
    text campaign-side and the nonce world-side would fingerprint an anchor that
    exists nowhere."""
    mine = voice_anchors.read_record(croot_of(cid), char_id)
    if mine["disabled"] or _flat_ref("characters", char_id) in detached(cid):
        return mine   # campaign opt-out, or a world id-mate that is a stranger
    return mine if mine["text"] else voice_anchors.read_record(wroot_of(cid), char_id)


def voice_anchor(cid: str, char_id: str) -> str:
    """The character's voice anchor as this campaign sees it.

    Same per-file overlay as tagline, with one difference tagline has no need
    for: a campaign can hold a TOMBSTONE saying it wants no anchor here, and
    that beats the world's rather than falling back to it (see
    `set_voice_anchor`). The anchor is world-level (a voice is a library
    property), but a campaign that has materialized its own copy of the
    character is entitled to its own reference text -- or to none."""
    return voice_anchor_record(cid, char_id)["text"]


# ---- world-side deletes: nothing outlives the record it was filed beside ----

def _record_dir(root: Path, kind: str, rid: str) -> Path:
    """`<root>/<kind>/<rid>/` — the directory a record's *neighbours* live in.

    Flat records are a file (`<kind>/<rid>.md`) with a sibling directory; actors
    are the directory. Either way this holds everything filed under the record's
    id rather than inside it: an actor's campaign-local sidecars (state.md,
    dossier.md, voice_drift.md), a group's state.md, and any kind's `assets/`.
    """
    return root / kind / rid


def _drop_record_dir(root: Path, kind: str, rid: str) -> None:
    """Remove `_record_dir`, if there is one.

    The id is the only thing tying those files to their record, and ids are
    handed out by slug (`entities.create_entity` / `characters.create_character`
    uniquify against what exists *now*), so a record deleted and recreated under
    the same name gets the same id back. Anything left behind is then adopted by
    a new, unrelated record — for a group's state.md that means a dead group's
    Secrets in a live scene's context (#225).

    A failure to remove is logged rather than raised: the record itself is
    already gone by the time this runs, and answering a delete that succeeded
    with a 500 helps nobody. `rmtree` stops at its first error, so what survives
    is a *part* of the directory rather than all of it — still strictly less
    than the pre-#225 leftovers, but the warning says which path to look at
    because "some of the dead record's sidecars" is not a state to leave a user
    guessing about.
    """
    if kind not in INHERITED_KINDS or not safe_id(rid):
        return
    d = _record_dir(root, kind, rid)
    if not d.is_dir():
        return
    try:
        shutil.rmtree(d)
    except OSError as exc:
        log.warning("could not remove %s (%s) -- a record recreated under the id "
                    "%r will inherit what is still in it", d, exc, rid)


def dependent_campaigns(wroot: Path) -> list[str]:
    """Ids of the campaigns that inherit from `wroot`.

    `world_refs`, not `list_campaigns`, because it reads each campaign.md
    independently: `list_campaigns` raises out of its own loop on the first
    undecodable one, and a single corrupt campaign — of *any* world — must not
    cost every healthy dependent its sweep (Codex review).

    That tolerance points the other way from `worlds.delete_world`'s in-use
    check, which shares this enumeration. There, a campaign whose reference
    could not be read counts as a user, because "we could not tell" must not
    become "nothing uses this world". Here it is skipped for the same reason
    read the other way: we would be deleting that campaign's state on a guess
    that it depends on this world at all, and a sweep that does not happen only
    leaves the pre-#225 behaviour.
    """
    out = []
    for cid, _name, w in campaigns_read.world_refs():
        if w is None:
            log.warning("cannot read which world campaign %s belongs to -- leaving its "
                        "state alone; a record recreated in %s may inherit it", cid, wroot)
            continue
        if worlds_paths.references_world(w, wroot) and campaigns_paths.campaign_exists(cid):
            out.append(cid)
    return out


def forget_world_record(wroot: Path, kind: str, rid: str) -> None:
    """Sweep what a world-side delete of `<kind>/<rid>` leaves reachable only
    by its id. Call it *after* the delete, from every world route that removes
    an inheritable record.

    Two places keep such leftovers:

    - the world's own `_record_dir` — `entities.delete_entity` and
      `greetings.delete_greeting` unlink the `.md` and nothing else, so the
      record's images survive it — the actor deletes already `rmtree` the
      directory, because for an actor the directory *is* the record;
    - each dependent campaign's `_record_dir`, holding state the campaign filed
      against a record it only ever *inherited*. That state is campaign-local by
      definition, so no sync ever removes it, and a world-side delete leaves it
      unreachable but intact — until the next same-name create hands the id
      back and it re-attaches to a record it knows nothing about (#225).

    A campaign that materialized its own copy keeps it: its record did not go
    anywhere, so its state is still the state of something it has. Deleting a
    world record has never removed a campaign's copy of it (`sync.incoming`
    skips world-side deletions), and this is not the change that starts.

    But that copy is no longer a copy *of* anything, and its `sync.md` base
    still says otherwise — a base is the claim "this world record and mine share
    an ancestor". Left standing, it is the same bug through a different door: a
    recreated slug arrives as an `update`, and accepting it overwrites the
    campaign's record with an unrelated one while its state.md stays put (Codex
    review). So the base goes, which is exactly what makes a record
    campaign-local — the state a campaign-side create leaves it in.

    Two limits, both deliberate. What this does NOT reach is campaign-local
    state keyed by the record id from *outside* the record's directory:
    `sheets/`, `relationships.json`, `appearances.json`, and the `deleted.json`
    tombstone that now hides a recreated record rather than adopting it. Each is
    a separate store with its own semantics, and sweeping them is the dependents
    design #52 carries.

    And being driven by the delete, this only sees campaigns that exist when the
    delete happens: state restored from a backup taken before it, or carried in
    from a store that never saw it, re-attaches exactly as it used to. Closing
    that needs identity rather than an event -- #225's other suggestion, a
    creation nonce stamped into the record and its state -- which is a store
    format change, and a much larger one than the bug in front of us.

    The enumeration is best-effort for the same reason the removal is: a store
    holding one campaign nobody can read must not make a world record
    undeletable, and the delete has already happened by the time we get here.
    """
    if _record_present(wroot, kind, rid):
        # Someone took the slug back between the delete and here. Everything
        # below is aimed at a record that exists again, and cannot tell its
        # state from the dead one's: sweeping would delete state written for
        # the NEW record, and detach a campaign that just materialized it.
        # Standing down leaves the pre-#225 behaviour, which is the harmless
        # side of a race this module cannot serialize (Codex review).
        log.warning("%s/%s was recreated before its delete could be swept -- "
                    "dependent campaigns keep what they filed against the old "
                    "record, and it may re-attach to the new one", kind, rid)
        return
    _drop_record_dir(wroot, kind, rid)
    try:
        cids = dependent_campaigns(wroot)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("could not enumerate the campaigns of %s (%s) -- state they "
                    "filed against %s/%s stays, and a record recreated under that "
                    "id will inherit it", wroot, exc, kind, rid)
        return
    for cid in cids:
        try:
            _forget_in_campaign(cid, kind, rid, wroot)
        except (OSError, ValueError, locks.StoreBusy) as exc:
            # Per campaign, not per sweep: an unreadable sync.md used to raise
            # through the loop, so one damaged campaign cost every LATER
            # dependent its cleanup -- and the route 500'd on a delete that had
            # already happened, whose retry 404s without sweeping (Codex review).
            # ValueError, not UnicodeDecodeError: a malformed plotmap.json
            # reaches `json.loads` here and raises JSONDecodeError, which is a
            # ValueError and not a decode error (Codex review). The wider catch
            # covers both, since both mean "this campaign's file is garbage".
            #
            # StoreBusy joins them now that `add_detached`/`_undetach` take the
            # campaign lock: a campaign busy for LOCK_TIMEOUT would otherwise
            # abort the sweep for every campaign after it and 500 a delete that
            # has already happened -- reintroducing, by a new route, exactly the
            # failure the paragraph above records fixing. Skipping one campaign
            # leaves it at the pre-#225 behaviour, which `_dependent_campaigns`
            # already names as the acceptable direction for a sweep that cannot
            # run.
            log.warning("could not finish sweeping campaign %s after %s/%s was deleted "
                        "(%s) -- a record recreated under that id may inherit its state",
                        cid, kind, rid, exc)


def _forget_in_campaign(cid: str, kind: str, rid: str, wroot: Path) -> None:
    # Campaign ids are reusable, and the sweep mutates one campaign at a time:
    # between the enumeration and this call the campaign could have been deleted
    # and its slug taken by a new one on a different world, which never depended
    # on `wroot` at all (Codex review). Re-asking is cheap; being wrong deletes
    # a stranger's state.
    try:
        world = campaigns_read.read_campaign(cid)["meta"].get("world", "")
    except campaigns_paths.CampaignNotFound:
        return   # deleted between the enumeration and its turn: nothing to sweep
    if not worlds_paths.references_world(world, wroot):
        return
    croot, ref = croot_of(cid), _flat_ref(kind, rid)
    if _record_present(croot, kind, rid):
        # The campaign owns a copy. It keeps it -- and stops sharing an identity
        # with whatever claims the slug next, in BOTH directions the id reaches:
        # the per-file overlays `detached` governs, and the sync base.
        #
        # Marker first, base second, the same ordering argument `_recorded_base`
        # makes for #247: the two writes cannot be made one, so a crash between
        # them has to land on the harmless side. *Marker, no base drop* -- sync
        # already ignores a detached ref, so the stale base is inert. *Base
        # dropped, no marker* -- the record looks campaign-local to sync while
        # every per-file resolver still reads the world, so the next slug owner
        # supplies its images, tagline and voice anchor with nothing to notice
        # (Codex review).
        add_detached(cid, ref)
        _drop_manifest_ref(cid, ref)
        return
    _drop_record_dir(croot, kind, rid)
    # Per-asset tombstones outlive the record they hid, and they hide by slot:
    # `assets/characters/mara/default/avatar` would blank the NEXT Mara's avatar
    # in this campaign, for a deletion aimed at a record that is gone (Codex
    # review). The whole-record tombstone keeps its meaning and stays.
    _drop_deleted(cid, {r for r in deleted(cid) if r.startswith(f"assets/{kind}/{rid}/")})
    if kind == "greetings":
        _drop_plotmap_edges(cid, croot, rid)


def _record_present(root: Path, kind: str, rid: str) -> bool:
    """Is there a record of this kind and id here *now*?

    Asked again after the delete, because the sweep drops a whole directory and
    these handlers hold no world-level lock: a concurrent create of the same
    name takes the id back the instant the delete frees it, and publishes its
    own `<kind>/<rid>/` for the sweep to remove -- reporting success for a
    record that no longer exists (Codex review).

    This narrows that window to the gap between the check and the `rmtree`
    rather than closing it; closing it needs a world-record lock, which this
    module does not have for anything (`locks.UNREVIEWED`). It costs nothing
    and removes the destructive outcome for every interleaving but one.
    """
    if kind in ("characters", "pcs"):
        return (root / kind / rid / _actor_meta(kind)).exists()
    return _flat_path(root, kind, rid).exists()


def _drop_plotmap_edges(cid: str, croot: Path, gid: str) -> None:
    """Unwire a deleted greeting from a campaign's OWN plot map.

    `greetings.delete_greeting` unwires it from the world's, and a campaign
    without a plotmap.json reads that one — but a campaign that materialized its
    map keeps a private copy `read_plotmap` prefers, and the deleted greeting's
    node and every `leads_to`/`excludes` pointing at it survive there. Recreate
    the slug and those edges are the new greeting's (Codex review).

    Only for a campaign that already has its own map: materializing one here
    would fork a campaign off the world's plot map as a side effect of a delete
    it had nothing to do with.
    """
    if not (croot / "plotmap.json").exists():
        return
    greetings.remove_from_plotmap(croot, gid)
    # A map that only ever matched the world's still matches it after both got
    # the same edit -- but sync.md holds the pre-delete hash, so the campaign
    # would be walked through a "conflict" whose two sides are identical (Codex
    # review). Advance the base to what both now are.
    wroot = wroot_of(cid)
    manifest = campaigns_paths.read_manifest(cid)
    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
    if "plotmap" in manifest and world_h is not None and greetings.plotmap_hash(croot) == world_h:
        manifest["plotmap"] = world_h
        campaigns_paths.write_manifest(cid, manifest)
