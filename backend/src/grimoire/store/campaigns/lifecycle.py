"""Campaign create/delete/rename, copy-on-create from a world, and the
one-time migration of a full-copy campaign to the overlay layout."""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from .. import (assets, atomic, calendars, campaign_climate, characters, climates,
                entities, greetings, locks, modules, overlay, pcs, sheets)
from ..appearances import paths as appearances_paths
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import ensure_home, now_iso, slugify, uniquify
from ..scenes import write as scenes_write
from ..worlds import paths as worlds_paths
from . import paths, read


def create_campaign(name: str, world_id: str, region: str | None = None,
                     calendar: str | None = None, module: str | None = None,
                     climate: str | None = None) -> str:
    ensure_home()
    if not worlds_paths.world_exists(world_id):
        raise worlds_paths.WorldNotFound(world_id)
    # `world_exists` resolves case-insensitively where the filesystem does, so
    # the caller's spelling may not be the one on disk. Store the canonical one
    # or the reference is invisible to a later string comparison (#259 review).
    world_id = worlds_paths.canonical_id(world_id)
    if calendar is not None:
        calendars.get_provider({"provider": calendar})  # unknown id -> CalendarError before anything is created
    wanted_climate = climate or climates.FALLBACK_ID
    campaign_climate.check_default(wanted_climate)  # unknown id -> fail before anything is created
    if module and module != "none":  # "none" = explicitly mechanics-free, always legal
        modules.pack_root(module)  # raises ModuleNotFound before creating anything
    # The campaign's calendar is resolved, adjusted and VALIDATED here, before
    # the lock — not inside it. `validate_calendar` calls `get_provider`, which
    # imports every user-authored provider in `<home>/calendars/`, and then runs
    # that provider's own `validate_rule`. Nothing bounds how long hand-written
    # plugin code takes, so holding the campaign lock across it lets one bad
    # calendar stall every writer in the campaign — the rule
    # `test_calendar_plugin_code_never_runs_under_the_campaign_lock` already
    # pins for the two scene mutators that need a calendar. Reading from the
    # world root rather than re-reading the campaign copy is what makes the
    # hoist possible, and is equivalent: `copy_calendar` is exactly
    # `write_calendar(croot, read_calendar(wroot))`, and `read_calendar`
    # normalizes, so the round trip through the campaign copy returned this
    # same dict. A malformed holiday now also fails before the directory is
    # created rather than after, which is what the checks above already do.
    cfg = calendars.read_calendar(worlds_paths.world_root(world_id))
    if calendar is not None:
        cfg["primary"]["provider"] = calendar
        cfg["confirmed"] = True          # an explicit wizard choice
    if region is not None:
        cfg["primary"]["region"] = region
    if region is not None or calendar is not None:
        calendars.validate_calendar(cfg)   # unknown provider -> CalendarError
    cid = uniquify(slugify(name), lambda c: paths.campaign_root(c).exists())
    root = paths.campaign_root(cid)
    # The lock spans PUBLICATION, not just the writes after it. `campaign.md` is
    # what makes a directory a campaign to `list_campaigns`, so the moment it
    # lands another grimoire process can find this campaign and start writing to
    # it (#234 — the lock is cross-process). Serializing only the later steps
    # does not help: that process can take the lock, write a sheet and release it
    # inside the window, and `sheets.seed` would then overwrite a completed
    # write with the world defaults. Holding from before publication through the
    # last initializing write is what makes creation atomic to anyone watching.
    #
    # It spans the `mkdir` too, which is not about serialization: acquisition
    # can fail (`StoreBusy` on a timeout), and with the directory already
    # created that leaves an empty orphan behind. `uniquify` reads any existing
    # directory as occupied, so the next attempt at the same name would silently
    # become `<name>-2`. The lock file lives outside the campaign tree
    # (`proclock.lock_path`), so nothing here needs the directory to exist first.
    #
    # Everything inside is bounded: file writes this package owns. No plugin
    # code, no provider import — see the calendar block above.
    with locks.campaign_lock(cid):
        root.mkdir(parents=True)
        (root / "scenes").mkdir()
        now = now_iso()
        atomic.write_text(paths.campaign_meta_path(cid), dump_frontmatter(
            {"name": name, "world": world_id, "created": now, "updated": now,
             "world_copy": "overlay",
             **({"module": module} if module else {})}, ""))
        # copy-on-write: nothing is copied up front; records materialize on divergence
        # (store/overlay.py) and sync.md tracks bases for materialized records only
        paths.write_manifest(cid, {})
        calendars.write_calendar(root, cfg)
        campaign_climate.write_default(cid, wanted_climate)
        sheets.seed(cid)                 # reentrant: takes this same lock again
    return cid


def ensure_campaign_slim(cid: str) -> None:
    """One-time lazy migration of a full-copy campaign to the overlay layout.
    Deletes campaign files that are provably redundant — flat/actor content
    whose hash equals both the recorded sync base and the current world hash,
    plus byte-identical asset/sidecar copies — tombstones refs whose copy the
    user had deleted, and stamps world_copy: overlay. Skips (unmarked) while
    the world dir is missing so a late-syncing store slims on a later access.
    Locked actors keep their cards (the lock invariant needs them); diverged
    records and campaign-local files are never touched.

    **Decides everything before it deletes anything** (#270). The rule that
    reads a manifest ref whose copy is absent as a user deletion is only sound
    while the ref cannot outlive the copy, and this function used to break that
    itself: it unlinked redundant copies inside the loop and persisted the
    pruned manifest only afterwards, so an interruption anywhere in between —
    a crash, a kill, or just an OSError out of the manifest write — left every
    copy it had already pruned looking exactly like a record the user had
    deleted. The next run tombstoned them, permanently hiding records from a
    campaign that had done nothing but be interrupted mid-migration.

    So the loop below only classifies, and the writes that follow run in the
    one order whose every prefix is a state this function reads correctly:
    tombstones, then the manifest, then the copies it named. Interrupted after
    the tombstones, the next run repeats them (`add_deleted` is a union).
    Interrupted after the manifest, what is left is a copy the manifest no
    longer names — which the next run cannot mistake for a deletion, because
    the loop only ever looks at refs the manifest still holds, and which
    `_reclaim_untracked_copies` settles rather than leaving adrift.

    That sweep runs before the manifest write and deletes only copies nothing
    names, so it has no ref of its own to order against; the bases it hands
    back ride along on that same write.
    """
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if meta.get("world_copy") == "overlay":
        return
    root = paths.campaign_root(cid)
    wroot = read.world_root_of(cid)
    if not wroot.exists():
        return

    locked = set(appearances_paths.record(cid))
    manifest = paths.read_manifest(cid)
    copied = set(manifest)   # every record the full copy tracked, before the loop prunes it
    deleted_by_user: list[str] = []   # refs to tombstone: the copy is gone, the world's is not
    redundant: list[str] = []         # refs whose copy the world still holds, identical
    for ref, base in sorted(manifest.items()):
        kind, _, eid = ref.partition("/")
        if ref == "plotmap":
            if not (root / "plotmap.json").exists():
                if (wroot / "plotmap.json").exists():
                    deleted_by_user.append(ref)   # keep the user's deletion deleted
                manifest.pop(ref)
            elif greetings.plotmap_hash(root) == base == greetings.plotmap_hash(wroot):
                redundant.append(ref)
                manifest.pop(ref)
            continue
        if kind in appearances_paths.ACTOR_KINDS:
            if ref in locked:
                manifest.pop(ref)   # a lock owns its base in appearances.json
                continue
            dh = characters.dir_hash if kind == "characters" else pcs.dir_hash
            mine_h = dh(root, eid)
            if mine_h is None:
                if dh(wroot, eid) is not None:
                    deleted_by_user.append(ref)   # keep the user's deletion deleted
                manifest.pop(ref)
            elif mine_h == base == dh(wroot, eid):
                redundant.append(ref)
                manifest.pop(ref)
            continue
        if not (root / kind / f"{eid}.md").exists():
            if (wroot / kind / f"{eid}.md").exists():
                deleted_by_user.append(ref)   # keep the user's deletion deleted
            manifest.pop(ref)
        elif entities.entity_hash(root, kind, eid) == base == entities.entity_hash(wroot, kind, eid):
            redundant.append(ref)
            manifest.pop(ref)
    for ref in deleted_by_user:
        overlay.add_deleted(cid, ref)
    _tombstone_deleted_copied_assets(cid, root, wroot, copied)
    # Tracked means "named by the manifest ON DISK", which is still the one the
    # loop started from -- `copied`, not what the loop pruned down to. A ref the
    # loop dropped is still out there naming its copy until the write below, so
    # letting the sweep take that copy would drop it *before* the ref: the very
    # ordering this is here to keep. Locked refs count too -- they leave the
    # manifest holding their cards on purpose, and the sweep would take exactly
    # the files the lock invariant needs.
    manifest.update(_reclaim_untracked_copies(cid, root, wroot, copied | locked))
    paths.write_manifest(cid, manifest)
    for ref in redundant:
        overlay.dematerialize(cid, ref)
    _prune_duplicate_files(root, wroot)
    meta["world_copy"] = "overlay"
    atomic.write_text(mp, dump_frontmatter(meta, body))


def _reclaim_untracked_copies(cid: str, root: Path, wroot: Path, tracked: set[str]) -> dict[str, str]:
    """Settle the campaign copies sync.md does not name, returning the bases to
    record for the ones kept.

    In a full copy every copy had a ref, so an untracked one is residue: every
    writer drops the ref before the copy while `campaigns.read.slim_pending`
    holds (#270), and the copy is what an interrupted one leaves behind. Left
    as it is, it would survive the migration as a materialized record with no
    base — readable, but invisible to sync, so it would never see a world edit
    again and nothing would say so.

    Which residue it is decides what to do, and the world answers:

    - **The world holds it identically** — an interrupted materialization,
      whose copy was the world's own bytes. Drop it; the record goes back to
      being inherited, which is where the migration was taking it anyway.
    - **The world holds a different version** — an interrupted accept, which
      got as far as disowning the copy. Keep it and record the copy's own hash
      as its base: that is the truth (the copy is an unmodified materialization
      of the world version it was made from), and it puts the record back in
      front of sync, which offers the update the accept was interrupted taking.
    - **The world does not hold it at all** — campaign-local, not residue.
      Untouched, and no base: it has no world record to have a base against.
    """
    adopted: dict[str, str] = {}
    if "plotmap" not in tracked and (root / "plotmap.json").exists():
        mine_h, world_h = greetings.plotmap_hash(root), greetings.plotmap_hash(wroot)
        if mine_h is not None and mine_h == world_h:
            (root / "plotmap.json").unlink()
        elif mine_h is not None and world_h is not None:
            adopted["plotmap"] = mine_h
    for kind, eid in entities.synced_refs(root):
        ref = f"{kind}/{eid}"
        if ref in tracked:
            continue
        mine_h = entities.entity_hash(root, kind, eid)
        world_h = entities.entity_hash(wroot, kind, eid)
        if mine_h is not None and mine_h == world_h:
            (root / kind / f"{eid}.md").unlink()
        elif mine_h is not None and world_h is not None:
            adopted[ref] = mine_h
    for kind in appearances_paths.ACTOR_KINDS:
        dh = characters.dir_hash if kind == "characters" else pcs.dir_hash
        listed = characters.list_characters(root) if kind == "characters" else pcs.list_pcs(root)
        for item in listed:
            aid = item["id"]
            ref = f"{kind}/{aid}"
            if ref in tracked:
                continue
            mine_h, world_h = dh(root, aid), dh(wroot, aid)
            if mine_h is not None and mine_h == world_h:
                overlay.dematerialize_actor(cid, kind, aid)
            elif mine_h is not None and world_h is not None:
                adopted[ref] = mine_h
    return adopted


def _tombstone_deleted_copied_assets(cid: str, root: Path, wroot: Path, copied: set[str]) -> None:
    """A pre-overlay full copy held every world asset, so a world asset now
    missing from the campaign tree was deleted by the user before migration.
    Tombstone it, or the overlay would resurface the world copy once world_copy
    flips to overlay. Runs before _prune_duplicate_files so byte-identical
    copies are still present and not mistaken for deletions. Only records the
    full copy tracked (`copied`) are considered — world records/assets added
    after the fork stay live-inherited; whole-deleted records already carry a
    <base>/<aid> tombstone and are skipped."""
    gone = overlay.deleted(cid)
    for kind in ("characters", "pcs", "locations", "lore", "greetings"):
        wbase = wroot / kind
        if not wbase.exists():
            continue
        for wp in sorted(wbase.rglob("*")):
            if not wp.is_file() or not assets._norm_ext(wp.suffix):
                continue   # images only: focus.json / non-image sidecars overlay via files
            rel = wp.relative_to(wroot)
            parts = rel.parts
            if len(parts) != 5 or parts[2] != "assets":
                continue
            aid, vid, name = parts[1], parts[3], wp.stem
            if f"{kind}/{aid}" not in copied or f"{kind}/{aid}" in gone:
                continue
            if not (root / rel).exists():
                overlay.add_deleted(cid, f"assets/{kind}/{aid}/{vid}/{name}")


def _prune_duplicate_files(root: Path, wroot: Path) -> None:
    """Delete campaign files byte-identical to the same relative path in the
    world: asset files and actor sidecars (tagline.md; focus.json lives under
    assets/). The file-level overlay serves them from the world afterwards.
    Campaign-only or diverged files stay; emptied dirs are removed."""
    for kind in ("characters", "pcs", "locations", "lore", "greetings"):
        base = root / kind
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if "assets" not in rel.parts and p.name != "tagline.md":
                continue
            w = wroot / rel
            if w.exists() and filecmp.cmp(p, w, shallow=False):
                # A focus sidecar is not redundant while a divergent campaign
                # avatar sits beside it: overlay.read_focus treats that avatar
                # as authoritative and won't fall back to the world focus, so
                # dropping the sidecar would silently reset the crop to center.
                if p.name == assets.FOCUS_FILE and any(p.parent.glob(f"{assets.AVATAR}.*")):
                    continue
                p.unlink()
        for d in sorted((x for x in base.rglob("*") if x.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        if base.exists() and not any(base.iterdir()):
            base.rmdir()


def rename_campaign(cid: str, name: str) -> None:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def set_campaign_response(cid: str, fields: dict) -> None:
    """Campaign-scope response settings; same semantics as scenes.set_response."""
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    for key in scenes_write.RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_campaign(cid: str) -> None:
    root = paths.campaign_root(cid)
    # same canonical-name requirement as delete_world: an rmtree must not
    # run for a spelling the store does not actually use (#259 review)
    if not paths.campaign_meta_path(cid).exists() or not worlds_paths.names_its_directory(root):
        raise paths.CampaignNotFound(cid)
    shutil.rmtree(root)
