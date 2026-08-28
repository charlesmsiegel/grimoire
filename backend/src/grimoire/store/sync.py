"""The push/sync engine: per-campaign incoming changes + accept/reject, and
the three explicit moves that run the other way.

Compares three content hashes per ref (kind, id):
  world = world entity's current hash (or None)
  base  = campaign sync.md[ref]        (or None)
  mine  = campaign entity's current hash (or None)
An incoming change exists iff world is not None and world != base.

`incoming`/`accept`/`reject` only ever advance the campaign. `promote`, `push`
and `demote` (#52, #53, #60) move a record the other way, and each of them is
something a user asked for by name -- never automatic, because all three relax
the rule that library drift becomes a campaign-local override and never a
library edit (#113).

Design: docs/superpowers/specs/2026-08-21-library-promote-demote-design.md
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import atomic, characters, entities, greetings, locks, overlay, pcs, revision
from .appearances import paths as appearances_paths
from .appearances import versions as appearances_versions
from .campaigns import lifecycle as campaigns_lifecycle
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .frontmatter import parse_frontmatter
from .paths import safe_id
from .worlds import lifecycle as worlds_lifecycle
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)


def _ref_str(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    if kind == "greetings":
        g = greetings.read_greeting(root, eid)
        return {"name": g["meta"].get("name", eid), "body": g["body"]}
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"]}


def incoming(cid: str) -> list[dict]:
    wroot = campaigns_read.world_root_of(cid)  # raises CampaignNotFound if the campaign is missing
    croot = campaigns_paths.campaign_root(cid)
    # read campaign.md / sync.md / appearances.json once and thread them through
    # the passes -- each used to re-read all three per pass
    manifest = campaigns_paths.read_manifest(cid)
    locked = appearances_paths.record(cid)
    # A detached record shares only a slug with whatever the world now holds
    # under its id, so nothing there is an update to it (#225). Dropping the
    # manifest ref covers the entity and unpicked-actor passes; a version lock
    # keeps its base in appearances.json, so that pass has to be told.
    gone = overlay.detached(cid)

    refs: set[str] = set(manifest) - gone

    out: list[dict] = []
    for ref in sorted(refs):
        kind, _, eid = ref.partition("/")
        if kind not in entities.SYNCED_KINDS:
            continue  # actor refs + plotmap are handled by their own passes
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        base_h = manifest.get(ref)
        if world_h is None or world_h == base_h:
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = entities.entity_hash(croot, kind, eid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == base_h else "conflict"
        out.append({"ref": {"kind": kind, "id": eid}, "status": status,
                    "world": _entity_blob(wroot, kind, eid),
                    "mine": _entity_blob(croot, kind, eid)})
    items = (out + _plotmap_incoming(wroot, croot, manifest)
             + _actor_incoming(wroot, croot, locked, gone)
             + _unpicked_incoming(wroot, croot, manifest, locked, gone))
    # LAST, over the combined list, so a pin means the same thing for every
    # pass: a pinned ref is simply not offered (#71). The composition view is
    # where a pinned ref still reports the state this filter is hiding.
    pins = pinned_refs(cid)
    if not pins:
        return items
    return [i for i in items if _ref_str(i["ref"]["kind"], i["ref"]["id"]) not in pins]


def _plotmap_blob(root: Path) -> dict:
    p = root / "plotmap.json"
    return {"name": "Plot map", "body": p.read_text(encoding="utf-8") if p.exists() else ""}


def _plotmap_incoming(wroot: Path, croot: Path, manifest: dict) -> list[dict]:
    if "plotmap" not in manifest or not (croot / "plotmap.json").exists():
        return []
    world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
    base = manifest.get("plotmap")
    if world_h is None or world_h == base:
        return []
    mine_h = greetings.plotmap_hash(croot)
    status = "update" if mine_h == base else "conflict"
    return [{"ref": {"kind": "plotmap", "id": "plotmap"}, "status": status,
            "world": _plotmap_blob(wroot), "mine": _plotmap_blob(croot)}]


def _actor_blob(root: Path, kind: str, actor_id: str, vid: str) -> dict:
    if kind == "characters":
        card = characters.read_card(root, actor_id, vid)
        return {"name": card["data"].get("name", actor_id), "version": vid, "card": card}
    persona = pcs.read_persona(root, actor_id, vid)
    return {"name": persona.get("name", actor_id), "version": vid, "persona": persona}


def _actor_incoming(wroot: Path, croot: Path, locked: dict, detached: set[str]) -> list[dict]:
    out: list[dict] = []
    for ref, rec in sorted(locked.items()):
        if ref in detached:
            continue  # the lock's base outlived its world actor; see overlay.detached
        kind, actor_id = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances_versions.actor_hash(wroot, kind, actor_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue  # world unchanged (or locked version deleted, which we skip)
        mine_h = appearances_versions.actor_hash(croot, kind, actor_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": kind, "id": actor_id}, "status": status,
                "world": _actor_blob(wroot, kind, actor_id, vid)}
        if mine_h is not None:
            item["mine"] = _actor_blob(croot, kind, actor_id, vid)
        out.append(item)
    return out


def _dir_hash(root: Path, kind: str, actor_id: str) -> str | None:
    return characters.dir_hash(root, actor_id) if kind == "characters" else pcs.dir_hash(root, actor_id)


def _actor_summary_blob(root: Path, kind: str, actor_id: str) -> dict:
    detail = (characters.read_character(root, actor_id) if kind == "characters"
              else pcs.read_pc(root, actor_id))
    versions = ", ".join(v["id"] for v in detail["versions"])
    return {"name": detail["meta"].get("name", actor_id), "body": f"versions: {versions}"}


def _unpicked_incoming(wroot: Path, croot: Path, manifest: dict, locked: dict,
                       detached: set[str]) -> list[dict]:
    """Whole-actor diffs for materialized actors with no version lock: one item per
    changed actor; accept dematerializes (revert to inherited), reject advances the base."""
    refs = {r for r in manifest
            if r.partition("/")[0] in appearances_paths.ACTOR_KINDS and r not in detached}
    out: list[dict] = []
    for ref in sorted(refs):
        if ref in locked:
            continue  # the per-locked-version pass owns this actor
        kind, _, aid = ref.partition("/")
        world_h = _dir_hash(wroot, kind, aid) if wroot.exists() else None
        if world_h is None or world_h == manifest.get(ref):
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = _dir_hash(croot, kind, aid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == manifest.get(ref) else "conflict"
        out.append({"ref": {"kind": kind, "id": aid}, "status": status,
                    "world": _actor_summary_blob(wroot, kind, aid),
                    "mine": _actor_summary_blob(croot, kind, aid)})
    return out


def _advance_actor(cid: str, kind: str, actor_id: str, *, copy: bool) -> bool:
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    rec = appearances_paths.record(cid).get(f"{kind}/{actor_id}")
    if rec is None:
        return False
    vid = rec["version"]
    world_h = appearances_versions.actor_hash(wroot, kind, actor_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False  # not pending
    if copy:
        ext = "json" if kind == "characters" else "md"
        src = wroot / kind / actor_id / f"{vid}.{ext}"
        dst = croot / kind / actor_id / f"{vid}.{ext}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(dst, src.read_text(encoding="utf-8"))
    appearances_versions.set_base(cid, kind, actor_id, world_h)
    return True


def _advance(cid: str, refs: list[dict], *, copy: bool) -> None:
    # Migrate first, so accept only ever runs against the overlay layout (#270).
    # Accepting drops a copy and its manifest ref, and to a campaign the
    # migration has not reached a ref whose copy is gone is a record the user
    # deleted -- so an interruption between the two writes would have it
    # tombstone an inherited record. Ordering the writes the other way round
    # there is not enough on its own: the stranded copy is then a record that
    # reads correctly and never syncs again, and no rule can tell it from one
    # the campaign owns. Getting the migration out of the way first leaves
    # neither. Nothing is lost when it cannot run (the world dir is missing):
    # every `world_h` below is then None and this function does nothing at all.
    # Cheap besides -- a migrated campaign costs one campaign.md read -- and the
    # sync routes already call it, so this only closes the gap between theirs
    # and ours.
    campaigns_lifecycle.ensure_campaign_slim(cid)
    # `incoming` filters detached refs, but accept/reject take theirs from the
    # request body -- a stale one submitted after the slug was recreated would
    # dematerialize the very copy detaching preserved (Codex review).
    gone = overlay.detached(cid)
    refs = [r for r in refs if _ref_str(r["kind"], r["id"]) not in gone]
    # Pinned refs are dropped for the same reason detached ones are: `incoming`
    # no longer offers them, but accept/reject take their refs from the request
    # body, and a stale submission must not advance a base hash the pin exists
    # to hold still (#71). Unpinning restores the offer untouched.
    pins = pinned_refs(cid)
    refs = [r for r in refs if _ref_str(r["kind"], r["id"]) not in pins]
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    manifest = campaigns_paths.read_manifest(cid)
    manifest_changed = False  # loc/lore manifest write
    touched = False           # any ref advanced → bump campaign.updated
    for ref in refs:
        kind, eid = ref["kind"], ref["id"]
        if kind == "plotmap":
            world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
            pending = ("plotmap" in manifest and world_h is not None
                       and manifest["plotmap"] != world_h)
            if not pending:
                continue
            if copy:   # take world: drop our copy, revert to inherited
                (croot / "plotmap.json").unlink(missing_ok=True)
                manifest.pop("plotmap", None)
            else:
                manifest["plotmap"] = world_h
            manifest_changed = touched = True
            continue
        if kind in appearances_paths.ACTOR_KINDS:
            if appearances_paths.record(cid).get(_ref_str(kind, eid)) is not None:
                if _advance_actor(cid, kind, eid, copy=copy):   # locked flow: unchanged
                    touched = True
                continue
            world_h = _dir_hash(wroot, kind, eid) if wroot.exists() else None
            if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
                continue
            if copy:
                overlay.dematerialize_actor(cid, kind, eid)
                manifest.pop(_ref_str(kind, eid), None)
            else:
                manifest[_ref_str(kind, eid)] = world_h
            manifest_changed = touched = True
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
            continue
        if copy:
            (croot / kind / f"{eid}.md").unlink(missing_ok=True)
            manifest.pop(_ref_str(kind, eid), None)
        else:
            manifest[_ref_str(kind, eid)] = world_h
        manifest_changed = touched = True
    if manifest_changed:
        campaigns_paths.write_manifest(cid, manifest)
    if touched:
        campaigns_read.touch(cid)


def accept(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=True)


def reject(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=False)


# ---------------------------------------------------------------------------
# Per-ref pins and the composition overview (#71)
# ---------------------------------------------------------------------------
#
# A pin freezes one ref against the sync engine: the campaign stops being
# offered world updates for it without rejecting each one (a reject advances
# the base; a pin holds it still, so unpinning restores exactly the offer that
# was waiting). Stored at <campaign>/sync_pins.json as {"<kind>/<id>": true} --
# a sidecar rather than a value in sync.md, which stays pure hashes. Not to be
# confused with store/pins.py (#129), which pins records into the *prompt*;
# this pins a ref against the *library*.


def _pins_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "sync_pins.json"


def pinned_refs(cid: str) -> set[str]:
    """Every pinned ref, as "<kind>/<id>" strings.

    A damaged or malformed file reads as no pins at all, deliberately: the
    fail-open direction here is "updates are offered again", which loses a
    convenience, where fail-closed would silently freeze refs nobody chose to.
    """
    p = _pins_path(cid)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {ref for ref, v in data.items() if v is True}


def set_pin(cid: str, kind: str, eid: str, pin: bool) -> set[str]:
    """Pin or unpin one ref, returning the pinned set as it now stands.

    A pure filter over the engine: nothing here touches sync.md or a base
    hash, which is what keeps accept/reject working unchanged after an unpin.
    """
    ref = _ref_str(kind, eid)
    with locks.campaign_lock(cid):
        pins = dict.fromkeys(pinned_refs(cid), True)
        if pin:
            pins[ref] = True
        else:
            pins.pop(ref, None)
        atomic.write_text(_pins_path(cid), json.dumps(pins, indent=2, sort_keys=True) + "\n")
    return set(pins)


def _composition_state(world_h: str | None, base: str | None, mine_h: str) -> str:
    """One state per ref, from the same three hashes the engine compares.

    A world hash of None is a world-side deletion (or a missing world), and it
    reads as "the world has not moved" here for the same reason `incoming`
    skips it: there is nothing to take, so the campaign's copy simply stands.
    """
    world_moved = world_h is not None and world_h != base
    mine_moved = mine_h != base
    if world_moved and mine_moved:
        return "conflict"
    if world_moved:
        return "update"
    if mine_moved:
        return "diverged"
    return "insync"


def _composition_row(kind: str, eid: str, name: str, state: str, pins: set[str],
                     lock: dict | None = None) -> dict:
    return {"ref": {"kind": kind, "id": eid}, "name": name, "state": state,
            "pinned": _ref_str(kind, eid) in pins, "lock": lock}


def composition(cid: str) -> list[dict]:
    """Every ref this campaign holds against the library, each with its state.

    The join that `incoming` and `diverged` are two projections of (#71): one row
    per ref from sync.md plus one per version-locked actor, with the state
    derived from the world/base/mine hash comparison -- including the two rows
    neither endpoint can report: a materialized ref with nothing pending
    (`insync`), and an actor edited here but never version-locked (`diverged`
    via the whole-dir hash). States and blob-less shape are deliberate: this is
    the overview, and the review panel still owns the diffs it acts on.

    Inherited records -- world records this campaign reads through the overlay
    without holding a copy -- are not rows: they are the world's, not part of
    what the campaign holds, and enumerating a whole library here would price
    the overview by the world's size instead of the campaign's.
    """
    wroot = campaigns_read.world_root_of(cid)
    croot = campaigns_paths.campaign_root(cid)
    manifest = campaigns_paths.read_manifest(cid)
    locked = appearances_paths.record(cid)
    gone = overlay.detached(cid)
    pins = pinned_refs(cid)
    return (_manifest_rows(cid, wroot, croot, manifest, locked, gone, pins)
            + _locked_rows(cid, wroot, croot, locked, gone, pins))


def _manifest_row(cid: str, wroot: Path, croot: Path, manifest: dict,
                  locked: dict, pins: set[str], ref: str) -> dict | None:
    """One manifest ref's row, or None where `incoming` would skip it too: a
    copy gone since materialization, an actor the locked pass owns, a kind the
    engine does not sync."""
    world_there = wroot.exists()
    kind, _, eid = ref.partition("/")
    if kind == "plotmap":
        mine_h = greetings.plotmap_hash(croot)
        if mine_h is None:
            return None
        world_h = greetings.plotmap_hash(wroot) if world_there else None
        return _composition_row(
            "plotmap", "plotmap", "Plot map",
            _composition_state(world_h, manifest[ref], mine_h), pins)
    if kind in appearances_paths.ACTOR_KINDS:
        if ref in locked:
            return None
        mine_h = _dir_hash(croot, kind, eid)
        if mine_h is None:
            return None
        world_h = _dir_hash(wroot, kind, eid) if world_there else None
        return _composition_row(
            kind, eid, _actor_name_or_id(croot, kind, eid),
            _composition_state(world_h, manifest[ref], mine_h), pins)
    if kind not in entities.SYNCED_KINDS:
        return None
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        return None
    world_h = entities.entity_hash(wroot, kind, eid) if world_there else None
    return _composition_row(
        kind, eid, _blob_name(text, eid),
        _composition_state(world_h, manifest[ref], entities.content_hash(text)), pins)


def _manifest_rows(cid: str, wroot: Path, croot: Path, manifest: dict,
                   locked: dict, gone: set[str], pins: set[str]) -> list[dict]:
    rows = (_manifest_row(cid, wroot, croot, manifest, locked, pins, ref)
            for ref in sorted(set(manifest) - gone))
    return [r for r in rows if r is not None]


def _locked_rows(cid: str, wroot: Path, croot: Path, locked: dict,
                 gone: set[str], pins: set[str]) -> list[dict]:
    aroot = appearances_paths.locked_actor_root(cid)
    rows: list[dict] = []
    for ref, rec in sorted(locked.items()):
        if ref in gone:
            continue
        kind, aid = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances_versions.actor_hash(wroot, kind, aid, vid)
        mine_h = appearances_versions.actor_hash(croot, kind, aid, vid)
        if mine_h is None:
            continue  # locked copy gone from disk: nothing to report a state on
        rows.append(_composition_row(
            kind, aid, _actor_name_or_id(aroot, kind, aid),
            _composition_state(world_h, rec["base"], mine_h), pins,
            lock={"version": vid, "role": rec["role"], "scenes": rec["scenes"]}))
    return rows


def _actor_name_or_id(root: Path, kind: str, actor_id: str) -> str:
    """Display name off the container meta, or the id when it cannot be read.

    Fallback rather than raise, for `appearances.cast._actor_name`'s reason:
    this runs over every actor the campaign holds, and one malformed record
    must not take out the whole overview.
    """
    try:
        detail = (characters.read_character(root, actor_id) if kind == "characters"
                  else pcs.read_pc(root, actor_id))
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return actor_id
    return str(detail["meta"].get("name", actor_id)) or actor_id


def is_composition_ref(cid: str, kind: str, eid: str) -> bool:
    """Whether a ref is currently part of this campaign's composition -- in the
    sync manifest or the appearance record. What the pin route refuses on: a
    pin on a ref the engine never consults would sit in the file meaning
    nothing, and the 404 says so while somebody is there to read it.

    The plot map's manifest key is the bare "plotmap" while its ref -- and so
    its pin -- is spelled "plotmap/plotmap" like every other; this is the one
    place the two spellings meet."""
    if kind == "plotmap":
        return "plotmap" in campaigns_paths.read_manifest(cid)
    ref = _ref_str(kind, eid)
    return (ref in campaigns_paths.read_manifest(cid)
            or ref in appearances_paths.record(cid))


# ---------------------------------------------------------------------------
# Campaign → world: promote, push, demote (#52, #53, #60)
# ---------------------------------------------------------------------------

class LibraryMoveError(Exception):
    """Base for every refusal below, so a route can map the family in one place."""


class PromoteConflictError(LibraryMoveError):
    """The world already holds that id, so promoting would overwrite a stranger."""


class PushConflictError(LibraryMoveError):
    """The world moved since the campaign's base — the mirror of a pull conflict."""


class NotDivergedError(LibraryMoveError):
    """Nothing to push: the campaign has no copy, or its copy matches the world."""


class NotInLibraryError(LibraryMoveError):
    """Nothing to push *to*: the record is campaign-local. That is a promote."""


class NotPushableError(LibraryMoveError):
    """A kind this operation deliberately does not carry."""


class NotDemotableError(LibraryMoveError):
    """A kind demote deliberately does not carry."""


class DanglingReferenceError(LibraryMoveError):
    """Promoting would publish a library record pointing at campaign-local content."""


class UnknownTargetError(LibraryMoveError):
    """A demote named a campaign that is not a dependent of this record."""


#: The sidecars a promoted actor carries. Both are world-level identity by the
#: overlay's own rule (they resolve per file, from the world), which is exactly
#: what makes them part of the record being promoted. Everything else in an
#: actor directory -- dossier.md, state.md, voice_drift.md -- is campaign-local
#: by definition and stays behind.
ACTOR_WORLD_SIDECARS: tuple[str, ...] = ("tagline.md", "voice_anchor.md")


def _flat_kind(kind: str) -> None:
    if kind not in entities.SYNCED_KINDS:
        raise entities.UnknownKind(kind)


def _safe_ref(kind: str, eid: str) -> None:
    """Refuse an id before it is ever joined onto a world root.

    `kind` and `eid` reach here as path parameters, and a path parameter can
    carry an encoded slash. Everything below writes to the WORLD, which no
    campaign resolver guards, so the check belongs at the top of each move
    rather than in whatever happens to run first: the campaign-side readers do
    refuse an unsafe id today, and relying on that would make these functions
    safe only for as long as the order of their own checks never changes."""
    if not safe_id(eid):
        raise entities.EntityNotFound(_ref_str(kind, eid))


def _require_world(wroot: Path) -> Path:
    """The world root, once it is known to be a world that still exists.

    `campaigns.read.world_root_of` answers with a path whether or not anything
    is there — a world deleted before the guard against that existed, or a
    campaign that records no world at all. Every move below then does
    `mkdir(parents=True)` on the way to its write, which would REBUILD that
    directory around the record it is writing: a tree with no world.md, which
    nothing lists as a world and no route can reach, holding the only copy of a
    record the campaign has just recorded a sync base for."""
    if not worlds_paths.names_its_directory(wroot) or not (wroot / "world.md").exists():
        raise worlds_paths.WorldNotFound(wroot.name)
    return wroot


def _world_file(wroot: Path, kind: str, eid: str) -> Path:
    return wroot / kind / f"{eid}.md"


def _world_holds_flat(wroot: Path, kind: str, eid: str) -> bool:
    """Is that id claimed in the world? The record DIRECTORY counts, exactly as
    it does for `overlay.create_entity`'s uniquify: it holds the assets a
    promoted record would silently adopt."""
    return _world_file(wroot, kind, eid).exists() or (wroot / kind / eid).is_dir()


def _actor_meta_name(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _world_holds_actor(wroot: Path, kind: str, aid: str) -> bool:
    """Is there an ACTOR under that id in the world?

    Keyed on the meta file rather than on the directory, and that is the whole
    of an interrupted promotion's retry. The directory is created before the
    version files and the meta is written last, so a kill in between leaves a
    directory holding no actor -- `overlay.actor_root`, `_record_present` and
    every reader agree that is not one. Asking `is_dir()` here would call that
    residue an existing library actor and refuse every retry with
    `PromoteConflictError`, permanently, which is exactly the stuck state the
    base-first ordering exists to avoid (Codex review).
    """
    return (wroot / kind / aid / _actor_meta_name(kind)).exists()


@contextmanager
def _reattaching_on_failure(cid: str, ref: str):
    """Undetach `ref`, and put the detachment back if the promotion does not land.

    Detaching has to come off BEFORE the copy — while it is on, the sync engine
    skips the ref, so a crash between the two writes would leave a promoted
    record that never syncs again, silently.

    But an *exception* can unwind where a crash cannot, and leaving the
    detachment off after a failed promotion is the #225 bug through its own
    door: the campaign is attached to a world record that was never written, so
    whatever claims that slug next supplies this campaign's record with its
    updates, its images and its sidecars by coincidence of id (Codex review).

    Only on the way out through an exception. A kill still lands on the
    documented residue, which is the harmless one and self-heals on retry.
    """
    was_detached = ref in overlay.detached(cid)
    try:
        # Inside the try, not before it. `undetach` is itself two writes -- the
        # marker, then the asset tombstones the detachment kept -- so a failure
        # in the second one used to escape this handler entirely, leaving the
        # campaign attached with no world record written (Codex review).
        overlay.undetach(cid, ref)
        yield
    except BaseException:
        if was_detached:
            try:
                overlay.add_detached(cid, ref)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                log.warning("could not restore the detachment on %s after a failed "
                            "promotion; a world record reusing that id would be "
                            "inherited by campaign %s", ref, cid)
        raise


def _copy_extras(what: str, copy) -> None:
    """The part of a promotion that runs AFTER the record has committed.

    A record's assets and world-level sidecars are filed beside it rather than
    in it, so they cannot be part of the single write the sync base describes.
    By the time they are copied the promotion is already complete and correct
    -- the world holds the record, the base describes it -- so a failure here
    must not be reported as a failed promote: the caller would retry, and the
    retry now refuses as a collision with the record it just made.

    Logged rather than swallowed, because a promoted record whose picture did
    not come with it is a thing the user can see and cannot otherwise explain.
    """
    try:
        copy()
    except (OSError, ValueError) as exc:
        # ValueError covers UnicodeDecodeError: a sidecar this store never wrote
        # (hand-placed, or written in another encoding) would otherwise escape a
        # handler that exists precisely so a committed promotion is not reported
        log.warning("promoted the record but could not copy its %s (%s) -- "
                    "the promotion stands; the files are still campaign-side", what, exc)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a record's asset tree, file by file and atomically.

    Not `shutil.copytree`, for the reason `sheets/tally.py` gives at the
    identical copy in the other direction: a partial copy must never appear
    under a real name. The destination here is a LIVE world record's asset
    directory, which every campaign of that world reads through the overlay the
    instant a file lands, so a torn image is served rather than merely stored.
    """
    if not src.is_dir():
        return
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(target, p.read_bytes())


def _put_base(cid: str, ref: str, base: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    manifest[ref] = base
    campaigns_paths.write_manifest(cid, manifest)


def _touch_world(wroot: Path) -> None:
    """Stamp the world as edited, without letting the stamp fail the edit.

    A library edit really is a change to the world (#53 asks for this, and #55
    reads it), but the record it describes has already landed by the time we
    get here -- reporting a 500 for a sort key would undo nothing and lose the
    user's work.

    The id comes back out of the directory name because the campaign-scoped
    callers only ever hold a root: `campaigns.read.world_root_of` resolves a
    campaign.md reference straight to a path. `canonical_id` is the same
    normalization `world_root` applied on the way in, so the round trip holds
    for every id that named a directory in the first place."""
    try:
        worlds_lifecycle.touch(worlds_paths.canonical_id(wroot.name))
    except Exception as exc:   # noqa: BLE001 - a sort key never fails a write
        log.warning("could not stamp world %s as updated (%s)", wroot, exc)


# ---- promote --------------------------------------------------------------

def promote(cid: str, kind: str, eid: str) -> None:
    """Copy a campaign-local record up into the campaign's world, and record the
    sync base that makes the two one record from here on.

    The precondition is that **the world holds no record under that id** —
    deliberately a statement about the world rather than about the manifest
    ref, so the residue of a crashed promote (a base whose world record never
    landed) retries cleanly instead of being refused as already-materialized.
    See the design doc for why that residue is the one chosen.
    """
    campaigns_lifecycle.ensure_campaign_slim(cid)   # only ever run against the overlay layout
    _safe_ref(kind, eid)
    wroot = _require_world(campaigns_read.world_root_of(cid))
    # sync.md is rewritten WHOLE, so two library moves on the same campaign can
    # each read it and each republish it, and the later write drops the other's
    # ref -- leaving a promoted record no manifest names, which no world edit
    # ever reaches again (Codex review). One lock, held across the read and the
    # write, and the same one demote takes.
    with locks.campaign_lock(cid):
        if kind in appearances_paths.ACTOR_KINDS:
            _promote_actor(cid, kind, eid, wroot)
        else:
            _flat_kind(kind)
            _promote_flat(cid, kind, eid, wroot)
    _touch_world(wroot)


def _promote_flat(cid: str, kind: str, eid: str, wroot: Path) -> None:
    # Collision before existence, on purpose. A record the campaign merely
    # inherits has no campaign-side file either, and answering *that* with "not
    # found" would be a lie about a record the user is looking at: it exists,
    # it is simply already library content. The 409 says so.
    if _world_holds_flat(wroot, kind, eid):
        raise PromoteConflictError(f"the library already has {kind}/{eid}")
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        raise entities.EntityNotFound(_ref_str(kind, eid))
    if kind == "greetings":
        _require_world_character(cid, wroot, text, eid)
    ref = _ref_str(kind, eid)
    dst = _world_file(wroot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Detaching comes off before the copy and goes back on if the copy fails --
    # see `_reattaching_on_failure` for both halves of why (#225, #247).
    # The base is hashed from the bytes we are writing, never re-read from the
    # destination: it has to describe the content that actually moved, even if
    # another writer touches that path in between (the argument
    # `_materialize_flat` makes in the opposite direction).
    with (_reattaching_on_failure(cid, ref),
          overlay.recorded_base(cid, ref, entities.content_hash(text), dst)):
        atomic.write_text(dst, text)
    # Assets only. The rest of a record directory (a group's state.md) is
    # campaign-local state that has no business in the library.
    _copy_extras("images", lambda: _copy_tree(
        overlay.record_dir(cid, kind, eid) / "assets", wroot / kind / eid / "assets"))


def _require_world_character(cid: str, wroot: Path, text: str, gid: str) -> None:
    """A greeting names the character it belongs to, so the library needs that
    character too -- otherwise promoting publishes a greeting pointing at
    nothing, and the campaign that promoted it is the only place it reads.

    A *detached* character fails this for the opposite reason: the world does
    hold that slug, but detachment is the statement that whatever holds it is a
    stranger to this campaign's character of the same id (#225). Promoting the
    greeting would file it against that stranger -- which is worse than a
    dangling reference, because it reads as working."""
    meta, _ = parse_frontmatter(text)
    detached_here = overlay.detached(cid)
    char = str(meta.get("character", "")).strip()
    if char:
        _require_world_actor(wroot, detached_here, char, gid, "belongs to")
        # The VERSION too, not just the actor. `start_from_greeting` seats the
        # owner at `g["version"]`, so a greeting pinned to a campaign-only
        # version of an otherwise-shared character publishes fine and then
        # raises in every sibling campaign (Codex review).
        vid = str(meta.get("version", "")).strip()
        if (vid and safe_id(vid)
                and appearances_versions.actor_hash(wroot, "characters", char, vid) is None):
            raise DanglingReferenceError(
                f"{gid} is pinned to {char}'s {vid} version, which is not in the library")
    # `present` names the rest of the cast the greeting opens with, and
    # `playing.start_from_greeting` reads every one of them. A campaign-local
    # name there publishes a greeting that raises the moment anyone starts it
    # -- in a sibling campaign, where nothing explains why (Codex review).
    for name in str(meta.get("present", "")).split(","):
        if not name:
            continue
        # `name`, not `name.strip()`. `greetings._tags_list` does not strip, so
        # a hand-edited `present: mara, winifred` is read as the literal id
        # " winifred" -- validating the stripped form passes a greeting that
        # then fails to seat its cast in every campaign (Codex review).
        _require_world_actor(wroot, detached_here, name, gid, "opens with")


def _require_world_actor(wroot: Path, detached_here: set[str], char: str,
                         gid: str, role: str) -> None:
    if not safe_id(char):
        return   # hand-edited: nothing this check can say about it
    if not (wroot / "characters" / char / "character.md").exists():
        raise DanglingReferenceError(
            f"{gid} {role} {char}, which is not in the library yet — promote it first")
    if _ref_str("characters", char) in detached_here:
        raise DanglingReferenceError(
            f"{gid} {role} this campaign's own {char}, which is not the "
            f"library's {char} — promoting it would file the greeting against a stranger")


def _promote_actor(cid: str, kind: str, aid: str, wroot: Path) -> None:
    if _world_holds_actor(wroot, kind, aid):     # see `_promote_flat` on the order
        raise PromoteConflictError(f"the library already has {kind}/{aid}")
    taken = overlay.actor_snapshot(cid, kind, aid)
    if taken is None:
        raise _actor_missing(kind, aid)
    base, files = taken
    meta_name = _actor_meta_name(kind)
    ref = _ref_str(kind, aid)
    dst = wroot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    # No meta means no actor, so EVERYTHING here is residue -- from an
    # interrupted attempt, or from a delete that could not finish removing the
    # directory. The whole tree goes, not just the version files: `assets/`,
    # tagline.md and voice_anchor.md are only ever overwritten where this
    # campaign supplies one, so a slot it leaves empty would keep the dead
    # actor's face and voice and hand them to the promoted one -- and through
    # the overlay, to every campaign of this world (#225, Codex review).
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    # The meta file is the commit point every reader keys an actor on, so it is
    # written last and is what `recorded_base` watches: version files landing
    # without it leave no actor at all — the harmless residue.
    with (_reattaching_on_failure(cid, ref),
          overlay.recorded_base(cid, ref, base, dst / meta_name)):
        for name, text in files:
            if name != meta_name:
                atomic.write_text(dst / name, text)
        atomic.write_text(dst / meta_name, dict(files)[meta_name])
    _advance_appearance_base(cid, kind, aid, wroot)
    src_dir = overlay.record_dir(cid, kind, aid)
    _copy_extras("sidecars", lambda: _copy_sidecars(src_dir, dst))
    _copy_extras("images", lambda: _copy_tree(src_dir / "assets", dst / "assets"))


def _advance_appearance_base(cid: str, kind: str, aid: str, wroot: Path) -> None:
    """Point a locked actor's appearance base at the world version just written.

    The flagship #60 case runs straight through this: an NPC who walks on
    mid-scene is created campaign-local and then CAST, and casting locks a
    version in appearances.json with an empty base -- there was no world actor
    to hash. `_actor_incoming` reads that record in preference to sync.md, so
    without this the promotion that just succeeded reports itself as a
    *conflict* to the very campaign that made it (Codex review).

    The manifest ref `recorded_base` wrote stays and is inert: the unpicked
    pass skips any ref that also appears in `locked`.

    Best-effort, and loudly so. The actor is already in the library by now, so
    raising would report a failed promotion for one that landed and send the
    retry into a collision. A stale base here is visible and recoverable --
    it surfaces as a conflict the user can reject, which advances it.
    """
    rec = appearances_paths.record(cid).get(_ref_str(kind, aid))
    if rec is None:
        return          # not locked: sync.md's whole-actor base is the only one
    try:
        world_h = appearances_versions.actor_hash(wroot, kind, aid, rec["version"])
        if world_h is not None:
            appearances_versions.set_base(cid, kind, aid, world_h)
    except (OSError, ValueError) as exc:
        log.warning("promoted %s/%s but could not advance its appearance base (%s) -- "
                    "the campaign will offer it as a conflict until that is rejected",
                    kind, aid, exc)


def _copy_sidecars(src_dir: Path, dst: Path) -> None:
    for sidecar in ACTOR_WORLD_SIDECARS:
        if (src_dir / sidecar).exists():
            atomic.write_text(dst / sidecar, (src_dir / sidecar).read_text(encoding="utf-8"))


def _actor_missing(kind: str, aid: str) -> Exception:
    return (characters.CharacterNotFound(aid) if kind == "characters"
            else pcs.PCNotFound(aid))


# ---- push -----------------------------------------------------------------

def diverged(cid: str) -> list[dict]:
    """Every materialized flat record whose copy no longer matches its base —
    the inverse of `incoming`, and the set `push` can act on.

    Detached refs are excluded: their base is gone and the world record of that
    id is a stranger, so there is nothing they could be pushed *back* into."""
    manifest = campaigns_paths.read_manifest(cid)
    gone = overlay.detached(cid)
    out: list[dict] = []
    for ref in sorted(set(manifest) - gone):
        kind, _, eid = ref.partition("/")
        if kind not in entities.SYNCED_KINDS:
            continue     # actors carry their base elsewhere; see `push`
        text = overlay.record_text(cid, kind, eid)
        if text is None or entities.content_hash(text) == manifest.get(ref):
            continue
        out.append({"ref": {"kind": kind, "id": eid},
                    "name": _blob_name(text, eid)})
    return out


def _blob_name(text: str, eid: str) -> str:
    meta, _ = parse_frontmatter(text)
    return str(meta.get("name", eid)) or eid


def library_status(cid: str, kind: str, eid: str) -> dict:
    """Where one campaign record stands relative to the library, in the three
    facts an editor needs to decide which action to offer.

    Computed here rather than pieced together client-side out of a world
    listing and a `diverged` sweep: which of promote and push applies is the
    same rule those two functions enforce, and a second copy of it in the
    frontend would drift into offering the button that 409s.
    """
    _safe_ref(kind, eid)
    # This is a read, so a campaign whose world is gone is answered rather than
    # refused -- but `world_alive` still gates both actions, because a move into
    # a world that is not there is exactly what `_require_world` refuses. An
    # editor offering a button for that call would be the drift this function
    # exists to prevent.
    wroot = campaigns_read.world_root_of(cid)
    world_alive = worlds_paths.names_its_directory(wroot) and (wroot / "world.md").exists()
    ref = _ref_str(kind, eid)
    actor = kind in appearances_paths.ACTOR_KINDS
    text: str | None = None
    actor_diverged = False
    if actor:
        mine = overlay.actor_snapshot(cid, kind, eid)
        has_own = mine is not None
        in_world = _world_holds_actor(wroot, kind, eid)
        # Compared even though actors cannot be pushed. Reporting a materialized
        # and edited actor as `diverged: False` made the panel say "in sync with
        # the library" about a card this campaign had rewritten -- a claim about
        # two records, and a false one (Codex review). Push stays unavailable;
        # what changes is that the editor stops asserting the opposite.
        if has_own and in_world:
            actor_diverged = mine[0] != campaigns_paths.read_manifest(cid).get(ref)
    else:
        _flat_kind(kind)
        text = overlay.record_text(cid, kind, eid)
        has_own = text is not None
        in_world = _world_holds_flat(wroot, kind, eid)
    detached_here = ref in overlay.detached(cid)
    diverged_here = False
    if text is not None and in_world and not detached_here:
        base = campaigns_paths.read_manifest(cid).get(ref)
        diverged_here = base is None or entities.content_hash(text) != base
    return {
        "in_library": in_world and not detached_here,
        "diverged": (diverged_here or actor_diverged) and not detached_here,
        # Exactly `promote`'s precondition, so the button is never offered for a
        # call that would refuse -- including the detached record whose slug the
        # library has since handed to somebody else.
        "can_promote": has_own and not in_world and world_alive,
        # Actors are deliberately absent from push (#53 option B), and `text` is
        # None for them, so `diverged_here` already says no.
        "can_push": diverged_here and world_alive,
    }


def push(cid: str, kind: str, eid: str, *, force: bool = False) -> None:
    """Save a campaign's override of a library record back into the library.

    Clears the override by construction: the world file becomes the campaign's
    bytes and the base advances to match, so `mine == world == base` holds and
    `incoming` has nothing to say about the ref.

    Refuses when the world has moved since the campaign's base — that is a push
    conflict, the mirror of the pull conflict `incoming` reports — unless the
    caller has seen it and forced past it.
    """
    if kind in appearances_paths.ACTOR_KINDS:
        # A locked actor's base lives in appearances.json and pushing one means
        # minting a NEW world version (#53 option B) rather than overwriting the
        # current one. Saying so beats doing the wrong one of those silently.
        raise NotPushableError(
            f"{kind} cannot be saved back to the library yet — "
            "promote an emergent one, or edit the library copy directly")
    _flat_kind(kind)
    _safe_ref(kind, eid)
    campaigns_lifecycle.ensure_campaign_slim(cid)
    wroot = _require_world(campaigns_read.world_root_of(cid))
    with locks.campaign_lock(cid):      # see `promote` on why the manifest needs it
        _push_locked(cid, kind, eid, wroot, force=force)


def _push_locked(cid: str, kind: str, eid: str, wroot: Path, *, force: bool) -> None:
    ref = _ref_str(kind, eid)
    text = overlay.record_text(cid, kind, eid)
    if text is None:
        raise NotDivergedError(f"{ref} is still inherited from the library — there is nothing to save")
    # The record FILE, not `_world_holds_flat`'s wider "is this id claimed?".
    # Directory occupancy is right for blocking a promote -- a leftover assets
    # directory is content a promotion would adopt -- but for push it would
    # report a record whose world file is gone as a conflict, and the forced
    # retry then recreates it around the dead record's images (Codex review).
    if not _world_file(wroot, kind, eid).exists():
        raise NotInLibraryError(f"{ref} is campaign-local; promote it into the library instead")
    if ref in overlay.detached(cid):
        # Its world original was deleted and something else took the slug. The
        # two records share nothing but an id, and overwriting a stranger is
        # the exact harm detaching exists to prevent (#225).
        raise NotInLibraryError(
            f"{ref} no longer belongs to the library record of that name")
    if kind == "greetings":
        # The same referential check promote runs. A campaign can edit an
        # inherited greeting's cast to name an emergent character, and pushing
        # those bytes publishes a greeting every sibling campaign inherits and
        # none of them can start (Codex review).
        _require_world_character(cid, wroot, text, eid)
    base = campaigns_paths.read_manifest(cid).get(ref)
    world_h = entities.entity_hash(wroot, kind, eid)
    mine = entities.content_hash(text)
    if mine == world_h:
        # The two sides already agree, so there is nothing to write and nothing
        # to conflict over -- whatever the base says. Checked BEFORE the
        # conflict tests on purpose: this is exactly the residue push's own
        # write ordering chooses (world written, base not), and answering a
        # retry of it with "changed in the library" would be a conflict message
        # about two identical files, with no way out but a force that overwrites
        # the world with what it already holds.
        if base == mine:
            raise NotDivergedError(f"{ref} already matches the library")
        _put_base(cid, ref, mine)    # clearing the override IS the whole ask
        # Stamped here as well: the interrupted push that left this state failed
        # before its own touch, so without this the world keeps its pre-edit
        # `updated` forever even though the edit landed (Codex review).
        _touch_world(wroot)
        return
    if base is None and not force:
        # A copy with no base: nothing proves the two share an ancestor, so
        # this is a conflict rather than a clean save.
        raise PushConflictError(f"{ref} has no recorded common version with the library")
    if base is not None and world_h != base and not force:
        raise PushConflictError(f"{ref} changed in the library since this campaign copied it")
    # World first, base second. The residue of a crash between them is a
    # conflict whose two sides happen to be identical -- noisy, and rejecting it
    # advances the base and clears it. The other order records a base for
    # content the world does not have, and sync then offers to overwrite this
    # campaign's edit with the library's older text.
    # Re-read immediately before publishing. Two campaigns pushing the same
    # world record hold DIFFERENT campaign locks, so nothing above excludes
    # them from each other and the later write would silently replace a push
    # that had already returned success (Codex review). This narrows that to
    # the gap between the check and the write rather than closing it: closing
    # it needs a world-scoped lock, and this store has none for anything --
    # world create, rename and delete are all unserialized today, so inventing
    # one here would add a second lock domain, and a new ordering pair to
    # deadlock on, for one call site. Named in the design doc as a known gap.
    if not force and entities.entity_hash(wroot, kind, eid) != world_h:
        raise PushConflictError(f"{ref} changed in the library while this save was being made")
    atomic.write_text(_world_file(wroot, kind, eid), text)
    _put_base(cid, ref, mine)   # the bytes we wrote, not a re-read of where they went
    _touch_world(wroot)


# ---- demote ---------------------------------------------------------------

def dependents(wid: str, kind: str, eid: str) -> list[dict]:
    """The campaigns that would notice this library record going away.

    Every campaign of the world, not only the ones holding a manifest ref: under
    the overlay a campaign with no copy is the one depending on the world record
    *most*, since it has nothing else. Two are excluded, and neither is a
    dependent in any sense -- one that tombstoned the record (it deliberately
    does not have it) and one whose ref is detached (its record is its own, and
    this world record is a stranger sharing the slug).
    """
    wroot = _require_world_record(wid, kind, eid)
    ref = _ref_str(kind, eid)
    out: list[dict] = []
    for cid in overlay.dependent_campaigns(wroot):
        if ref in overlay.deleted(cid) or ref in overlay.detached(cid):
            continue
        try:
            name = campaigns_read.read_campaign(cid)["meta"].get("name", cid)
        except campaigns_paths.CampaignNotFound:
            continue    # deleted between the enumeration and its turn
        out.append({"id": cid, "name": name,
                    "has_copy": overlay.record_text(cid, kind, eid) is not None})
    return sorted(out, key=lambda d: d["id"])


def _require_world_record(wid: str, kind: str, eid: str) -> Path:
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    wroot = worlds_paths.world_root(wid)
    if kind in appearances_paths.ACTOR_KINDS:
        raise NotDemotableError(f"{kind} cannot be demoted — see #52's v1 boundary")
    _flat_kind(kind)
    _safe_ref(kind, eid)
    if not _world_file(wroot, kind, eid).exists():
        raise entities.EntityNotFound(_ref_str(kind, eid))
    return wroot


def demote(wid: str, kind: str, eid: str, *, copy_down: bool = True,
           target: str | None = None) -> dict:
    """Take a record out of the library, optionally leaving each dependent
    campaign holding its own copy.

    The delete itself is the world route's ordinary one, followed by
    `overlay.forget_world_record` -- the sweep that already knows how to detach
    the campaigns holding copies and drop their now-meaningless bases. This
    function's own work is the copy-down that happens *first*, so that a
    campaign which would otherwise simply lose the record keeps it.

    Copy-down before delete, and aborting if any of it fails: a partial
    copy-down leaves campaigns holding copies they would have been given anyway
    and the library record still standing, which is the state before the call.
    Deleting first and copying after would lose the record outright for every
    campaign the copy never reached.

    `target` narrows who gets the copy — it does NOT narrow the delete, which
    is the whole operation. Every other dependent loses the record exactly as
    `copy_down=False` would have them lose it. That is destructive by request
    rather than by accident (a target naming no dependent is refused below),
    and the app's own demote UI does not offer it: `DemotePanel` always demotes
    to every dependent.
    """
    wroot = _require_world_record(wid, kind, eid)
    every = dependents(wid, kind, eid)
    deps = every
    if target is not None:
        # Refused, not filtered to nothing. The delete below runs whatever
        # `target` matched, so a typo used to mean "copy this down nowhere,
        # then take it away from every campaign" -- the most destructive
        # reading of the request, chosen silently.
        if not any(d["id"] == target for d in every):
            raise UnknownTargetError(
                f"{target} is not a campaign that depends on {kind}/{eid}")
        deps = [d for d in every if d["id"] == target]
    copied: list[str] = []
    # `every`, not `deps`: the copy-down may be narrowed to one campaign, but
    # the sweep below rewrites sync.md and detached.json in ALL of them, and a
    # lock has to cover every file this call touches. One demotion mutating
    # several campaigns is exactly the shape `hold_all` exists for -- it is the
    # only sanctioned way to hold more than one campaign lock, and it sorts, so
    # this cannot be the holder that wedges against another (#267, Codex review).
    with locks.hold_all([d["id"] for d in every]):
        # Every dependent's write token (#409), inside the hold that covers the
        # writes and in a `finally` so the failure path reaches it too. This is
        # a WORLD route, so nothing in `/api/campaigns/...` stamps these
        # campaigns -- and the sweep below rewrites sync.md and detached.json in
        # all of them, whether or not they took a copy. A caller holding one of
        # their tokens would otherwise confirm an operation priced before a
        # record it depends on was demoted into it.
        #
        # The `finally` is what a success-only stamp got wrong: the copy-down
        # writes one dependent at a time, so a raise part-way through leaves the
        # earlier ones written, and a world route's 5xx reaches no middleware
        # that could repair it (Codex review). Which ones were reached is not
        # knowable here without threading a record through every helper, so all
        # of them are stamped -- the ones that were not written pay a re-price,
        # which is the direction this value is deliberately wrong in.
        # `revision.bump` never raises, so this cannot displace the error that
        # got here.
        try:
            if copy_down:
                for dep in deps:
                    # The art goes down for EVERY dependent in scope, including the
                    # ones that already hold their own text. Assets overlay per
                    # file, so a campaign that diverged on wording is still reading
                    # the world's pictures -- and the delete below takes the world's
                    # record directory with it (`overlay.forget_world_record`).
                    overlay.copy_record_dir_down(dep["id"], kind, eid)
                    if kind == "greetings":
                        # The greeting's edges live in the plot map, not in the
                        # greeting -- and `delete_greeting` strips them from the
                        # world's. A campaign still inheriting that map would watch
                        # the record it was just given lose its prerequisites and
                        # unlock itself, which is the opposite of keeping it
                        # (Codex review). Its own map, taken before the delete.
                        overlay.materialize_plotmap(dep["id"])
                    if dep["has_copy"]:
                        continue     # already its own; materializing is a no-op anyway
                    overlay.materialize_entity(dep["id"], kind, eid)
                    copied.append(dep["id"])
            if kind == "greetings":
                greetings.delete_greeting(wroot, eid)   # also unwires the world's plot map
            else:
                entities.delete_entity(wroot, kind, eid)
            overlay.forget_world_record(wroot, kind, eid)
        finally:
            for dep in every:
                revision.bump(dep["id"])
    _touch_world(wroot)
    return {"copied_down": sorted(copied),
            "dependents": [d["id"] for d in deps]}


def campaigns_for_world(wid: str) -> list[dict]:
    out: list[dict] = []
    for c in campaigns_read.list_campaigns():
        if c.get("world") != wid:
            continue
        counts = {"new": 0, "update": 0, "conflict": 0}
        for p in incoming(c["id"]):
            counts[p["status"]] += 1
        out.append({"id": c["id"], "name": c["name"], "pending": counts})
    return out
