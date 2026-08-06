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
- <campaign>/detached.json (same shape) holds the third state: a campaign copy
  whose world original was DELETED, so it shares only a slug with whatever
  claims that id next. Records, sidecars and assets all stop resolving through
  for it -- see `detached()`.

Which records inherit, and which do not, is the whole content of the rule, so
it is declared below as data (INHERITED_KINDS / INHERITED_FILES) rather than
only in prose: `tests/test_overlay_guard.py` reads those names to check that
nothing outside this module resolves an inheritable record off a raw campaign
root. Everything else under <campaign> is campaign-local by definition and is
read directly: campaign.md, sync.md, deleted.json, detached.json, appearances.json,
calendar.json, changes.json, chronicle.json, timeline.md, sheet_baselines.json,
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

from . import (assets, atomic, cards, characters, entities, failsoft, greetings,
               pcs, taglines, voice_anchors)
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths, read as campaigns_read
from .paths import natural_key, safe_id
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)

#: Record kinds a campaign inherits from its world. A `<campaign>/<kind>/...`
#: read for one of these is only correct through this module (or after the
#: reader has materialized the record itself).
INHERITED_KINDS: tuple[str, ...] = entities.SYNCED_KINDS + appearances_paths.ACTOR_KINDS

#: Campaign-root files that resolve through to the world the same way.
INHERITED_FILES: tuple[str, ...] = ("plotmap.json",)


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
    under that id in the world applies to it** — which is what a campaign copy
    becomes when the world record it was copied from is deleted. The id is then
    free, and the next world record to claim it is a stranger that would
    otherwise supply this campaign's record with its sync updates, its avatar,
    its tagline and its voice anchor, all by coincidence of slug (#225).

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
    """Mark `ref` detached.

    Read-modify-write, and deliberately unserialized: two concurrent world
    deletes whose campaigns both own a copy can each read this file and each
    replace it, losing one marker (Codex review). That is true, and it is the
    same shape as `add_deleted` beside it and `campaigns.paths.write_manifest`
    below it -- the latter is named in `locks.OUTSIDE_DOMAIN` as exactly this
    known gap, and `overlay` sits in `locks.UNREVIEWED` because nothing here
    serializes. Giving this one file a lock its two siblings do not have would
    not make the campaign ledger safe; it would only make the gap harder to
    find. Closing it is the review that takes `overlay` out of that backlog."""
    atomic.write_text(_detached_path(cid),
                      json.dumps(sorted(detached(cid) | {ref}), indent=2) + "\n")


def _undetach(cid: str, ref: str) -> None:
    """Drop a detachment when the record it describes goes.

    Detachment is a statement about a *record*, not about an id: it says this
    campaign's copy is its own. Delete that copy and the statement has no
    subject -- but it would keep suppressing the per-file resolvers, so a later
    world record of the same slug would list in the campaign with its images
    and sidecars hidden (Codex review)."""
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
    Before it, sync.md is the pre-overlay full copy's inventory and the same
    residue reads as a record the user deleted, which the migration tombstones
    (#270) — so there the two writes swap and the copy commits first. See
    `campaigns.read.slim_pending`: what an interruption leaves there is a copy
    the manifest does not name, which the migration sweeps.

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
    if campaigns_read.slim_pending(cid):
        # Copy first, base second. Nothing to undo on the way out: no base was
        # written, and the copy's own last write is atomic, so a failed body
        # leaves the record inherited exactly as it found it.
        yield
        manifest = campaigns_paths.read_manifest(cid)
        manifest[ref] = base
        campaigns_paths.write_manifest(cid, manifest)
        return
    manifest = campaigns_paths.read_manifest(cid)
    previous = manifest.get(ref)
    manifest[ref] = base
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
        raise


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


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "", sd_prompt: str = "", fields: dict | None = None) -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(eid: str) -> bool:
        # the world's record DIRECTORY counts: a sweep that could not remove it
        # leaves assets this campaign would inherit through the overlay the
        # moment it claimed the slug (Codex review)
        return (_flat_path(wroot, kind, eid).exists() or _record_dir(wroot, kind, eid).is_dir()
                or _flat_ref(kind, eid) in gone)

    return entities.create_entity(croot_of(cid), kind, name, body, keys, owners,
                                  sd_prompt=sd_prompt, taken=taken, fields=fields)


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None, fields: dict | None = None) -> None:
    croot = croot_of(cid)
    if not _flat_path(croot, kind, eid).exists():
        materialize_entity(cid, kind, eid)
    entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys, owners=owners, fields=fields)


def delete_entity(cid: str, kind: str, eid: str) -> None:
    ref = _flat_ref(kind, eid)
    in_world = _inherits_world(cid, ref) and _flat_path(wroot_of(cid), kind, eid).exists()
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


def create_greeting(cid: str, name: str, character: str, version: str, body: str = "",
                    requires_tags=None, predecessor_join: str = "all",
                    present=None, pcless: bool = False) -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(gid: str) -> bool:
        return (_flat_path(wroot, "greetings", gid).exists()
                or _record_dir(wroot, "greetings", gid).is_dir()
                or _flat_ref("greetings", gid) in gone)

    # #137: bake {{char}} here, not inside greetings.create_greeting -- a thin
    # campaign's character commonly still lives only in the world, and
    # char_root (not croot_of) is the resolver that finds it there.
    body = cards.bake_char_token(body, greetings.char_name(char_root(cid, character), character, version))
    return greetings.create_greeting(croot_of(cid), name, character, version, body,
                                     requires_tags, predecessor_join, present=present,
                                     pcless=pcless, taken=taken)


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
    """Materialize an inherited actor and return the campaign root writes target."""
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
    names = [i["name"] for i in list_images(cid, item["id"], item["default_version"])]
    return {**item,
            "has_avatar": assets.AVATAR in names,
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


def list_pcs(cid: str) -> list[dict]:
    mine = pcs.list_pcs(croot_of(cid))
    have = {p["id"] for p in mine}
    gone = deleted(cid)
    inherited = [p for p in pcs.list_pcs(wroot_of(cid))
                 if p["id"] not in have and _flat_ref("pcs", p["id"]) not in gone]
    return sorted(mine + inherited, key=lambda p: p["id"])


def character_refs(cid: str) -> list[str]:
    return [c["id"] for c in list_characters(cid)]


def create_character(cid: str, name: str, version_name: str = "default",
                     card: dict | None = None) -> tuple[str, str]:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(aid: str) -> bool:
        return ((wroot / "characters" / aid).is_dir()
                or _flat_ref("characters", aid) in gone)

    return characters.create_character(croot_of(cid), name, version_name, card, taken=taken)


def create_pc(cid: str, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(pid: str) -> bool:
        return (wroot / "pcs" / pid).is_dir() or _flat_ref("pcs", pid) in gone

    return pcs.create_pc(croot_of(cid), name, tags, version_name, persona, taken=taken)


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


def delete_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    assets.delete_image(croot_of(cid), aid, vid, name, base)   # no-op when absent
    if assets.image_path(wroot_of(cid), aid, vid, name, base) is not None:
        add_deleted(cid, _asset_ref(base, aid, vid, name))


def promote_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    """Copy-up the named image and the current avatar, then swap campaign-side."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    inherits = _flat_ref(base, aid) not in detached(cid)
    for n in (name, assets.AVATAR):
        if (inherits and assets.image_path(croot, aid, vid, n, base) is None
                and _asset_ref(base, aid, vid, n) not in deleted(cid)):
            src = assets.image_path(wroot, aid, vid, n, base)
            if src is not None:
                assets.put_image(croot, aid, vid, n, src.read_bytes(),
                                 src.suffix.lstrip("."), base)
    assets.promote_image(croot, aid, vid, name, base)
    # When there was no avatar to swap into the promoted slot, the swap leaves
    # no campaign file at `name`, so the inherited image there would still show
    # through the overlay next to the new avatar. Tombstone it so promotion
    # moves the image out of the gallery instead of duplicating it.
    if (inherits and assets.image_path(croot, aid, vid, name, base) is None
            and assets.image_path(wroot, aid, vid, name, base) is not None):
        add_deleted(cid, _asset_ref(base, aid, vid, name))


# ---- payload patching: asset-derived fields come from the union ----

def read_character(cid: str, char_id: str) -> dict:
    detail = characters.read_character(char_root(cid, char_id), char_id)
    for v in detail["versions"]:
        v["images"] = [i["name"] for i in list_images(cid, char_id, v["id"])]
        v["avatar_focus"] = read_focus(cid, char_id, v["id"])
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


def _dependent_campaigns(wroot: Path) -> list[str]:
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
        cids = _dependent_campaigns(wroot)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("could not enumerate the campaigns of %s (%s) -- state they "
                    "filed against %s/%s stays, and a record recreated under that "
                    "id will inherit it", wroot, exc, kind, rid)
        return
    for cid in cids:
        try:
            _forget_in_campaign(cid, kind, rid, wroot)
        except (OSError, ValueError) as exc:
            # Per campaign, not per sweep: an unreadable sync.md used to raise
            # through the loop, so one damaged campaign cost every LATER
            # dependent its cleanup -- and the route 500'd on a delete that had
            # already happened, whose retry 404s without sweeping (Codex review).
            # ValueError, not UnicodeDecodeError: a malformed plotmap.json
            # reaches `json.loads` here and raises JSONDecodeError, which is a
            # ValueError and not a decode error (Codex review). The wider catch
            # covers both, since both mean "this campaign's file is garbage".
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
