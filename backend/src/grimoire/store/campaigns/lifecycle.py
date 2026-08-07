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

#: Campaign.md key marking the migration as decided but not finished deleting.
#: Deliberately NOT a third `world_copy` value: that field's only cross-version
#: contract is `== "overlay"`, so an unknown value reads as "unmigrated" to any
#: build that predates it, and the store is meant to be shared across devices
#: and platforms (CLAUDE.md, `store.home()` on a synced folder). Such a build
#: would rerun the whole old migration over a tree this one has already
#: half-pruned -- the exact double attribution `_finish` exists to prevent.
#: Stamped alongside `world_copy: overlay`, an older build simply skips, and the
#: worst it leaves behind is a few unpruned duplicate files (Codex review).
PRUNING_KEY = "slim_pruning"


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
    the tombstones, the next run repeats them (`add_deleted` is a union), and
    every interruption around the prune leaves a ref carrying the reserved base
    — a value the loop attributes to nobody and re-derives the same verdict for.
    """
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if meta.get("world_copy") == "overlay" and not meta.get(PRUNING_KEY):
        return
    root = paths.campaign_root(cid)
    wroot = read.world_root_of(cid)
    if not wroot.exists():
        return
    if meta.get(PRUNING_KEY):
        _finish(mp, meta, body, root, wroot)   # decisions all made; only deleting left
        return

    locked = set(appearances_paths.record(cid))
    manifest = paths.read_manifest(cid)
    copied = set(manifest)   # every record the full copy tracked, before the loop prunes it
    deleted_by_user: list[str] = []   # refs to tombstone: the copy is gone, the world's is not
    redundant: list[str] = []         # refs whose copy the world still holds, identical
    for ref, base in sorted(manifest.items()):
        kind, _, eid = ref.partition("/")
        # A ref the fork recorded with no hash at all copied nothing, so there
        # has never been a copy here for the user to have deleted -- both
        # historical writers stored `<hash of the world's> or ""`, and a plot
        # map is simply absent from most worlds, so `plotmap: ''` sits in every
        # campaign forked before its world had one. Reading that as a deletion
        # tombstones the world's plot map out of the campaign the moment the
        # world gains one, which is #270's own symptom (Codex review).
        attributable = bool(base)

        def spent(mine_h: str | None, world_h: str | None) -> bool:
            """The copy adds nothing the world does not already hold. A falsy
            base counts: it is what the prune below reserves before unlinking,
            so a run interrupted mid-prune re-derives the same verdict and
            finishes the job (and it is what a materialization reserves, whose
            unredeemed copy is the world's own bytes either way)."""
            return mine_h is not None and mine_h == world_h and (base == mine_h or not base)

        if ref == "plotmap":
            if not (root / "plotmap.json").exists():
                if attributable and (wroot / "plotmap.json").exists():
                    deleted_by_user.append(ref)   # keep the user's deletion deleted
                manifest.pop(ref)
            elif spent(greetings.plotmap_hash(root), greetings.plotmap_hash(wroot)):
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
                if attributable and dh(wroot, eid) is not None:
                    deleted_by_user.append(ref)   # keep the user's deletion deleted
                manifest.pop(ref)
            elif spent(mine_h, dh(wroot, eid)):
                redundant.append(ref)
                manifest.pop(ref)
            continue
        if not (root / kind / f"{eid}.md").exists():
            if attributable and (wroot / kind / f"{eid}.md").exists():
                deleted_by_user.append(ref)   # keep the user's deletion deleted
            manifest.pop(ref)
        elif spent(entities.entity_hash(root, kind, eid), entities.entity_hash(wroot, kind, eid)):
            redundant.append(ref)
            manifest.pop(ref)
    for ref in deleted_by_user:
        overlay.add_deleted(cid, ref)
    _tombstone_deleted_copied_assets(cid, root, wroot, copied)
    # Reserve, unlink, drop -- the same two-step `overlay._recorded_base` uses,
    # run backwards. Dropping the ref outright and then unlinking would leave an
    # interrupted prune as a copy the manifest does not name, and nothing on
    # disk tells that apart from a record the campaign owns: ids are
    # `slugify(name)` and each root uniquifies against itself alone, so a
    # campaign-local record whose slug the world later reuses with identical
    # content looks exactly like residue. Deleting it there reads as harmless --
    # the content is the same -- but the record has silently become inherited,
    # so the next world edit rewrites it and a world deletion removes it
    # (Codex review). Reserving instead makes the prune say so itself: a falsy
    # base is not attributable to any user (see the loop above), and every
    # interruption point below re-derives the same verdict on the next run.
    for ref in redundant:
        manifest[ref] = overlay.RESERVED_BASE
    paths.write_manifest(cid, manifest)
    for ref in redundant:
        overlay.dematerialize(cid, ref)
    for ref in redundant:
        manifest.pop(ref, None)
    paths.write_manifest(cid, manifest)
    _finish(mp, meta, body, root, wroot)


def _finish(mp: Path, meta: dict, body: str, root: Path, wroot: Path) -> None:
    """Prune byte-identical asset/sidecar copies, then stamp the migration done.

    Split out and fenced behind its own stamp because it is the one phase whose
    input a previous run can already have consumed.
    `_tombstone_deleted_copied_assets` reads a world asset missing from the
    campaign tree as one the user deleted, and `_prune_duplicate_files` deletes
    exactly such copies — so an interruption between them left the next run
    deriving asset deletions from a tree the last one had already pruned, and
    tombstoning live world images out of the campaign permanently. #270's own
    failure, one layer down, and reachable for any record that survives the
    classify loop while carrying a duplicate asset (Codex review).

    Reordering the two cannot fix it: `_prune_duplicate_files` runs last on
    purpose, so byte-identical copies are still present when the attribution
    happens and are not mistaken for deletions. What fixes it is not attributing
    twice -- `PRUNING_KEY` on disk means every decision is durable, so a resumed
    run re-enters here and attributes nothing."""
    if not meta.get(PRUNING_KEY):
        meta["world_copy"] = "overlay"
        meta[PRUNING_KEY] = True
        atomic.write_text(mp, dump_frontmatter(meta, body))
    _prune_duplicate_files(root, wroot)
    meta.pop(PRUNING_KEY, None)
    atomic.write_text(mp, dump_frontmatter(meta, body))


def _tombstone_deleted_copied_assets(cid: str, root: Path, wroot: Path, copied: set[str]) -> None:
    """A pre-overlay full copy held every world asset, so a world asset now
    missing from the campaign tree was deleted by the user before migration.
    Tombstone it, or the overlay would resurface the world copy once world_copy
    flips to overlay. Runs before _prune_duplicate_files so byte-identical
    copies are still present and not mistaken for deletions. Whole-deleted
    records already carry a <base>/<aid> tombstone and are skipped.

    A manifest ref is NOT enough to conclude the fork copied the record. Every
    overlay materializer records one, and none of them copies assets — they
    overlay per file — so on a campaign the migration has not reached yet, an
    ordinary edit to an inherited record was enough to make this tombstone every
    image that record has. No crash needed, and permanent: `deleted.json` is
    union-only and this runs once. The reserved-base residues `_recorded_base`
    leaves behind hit it the same way (Codex review).

    So it asks the campaign's own asset directory first. The fork copied whole
    record directories, creating `<kind>/<aid>/assets/<vid>/`, and no
    materializer creates one; `assets.delete_image` unlinks files without
    removing the directory, so the directory survives the user deleting every
    image in the version — exactly the case this function exists to preserve.

    That NARROWS the inference; it does not make it sound, and the difference is
    worth stating because the tests cannot. `put_image`, `write_focus`,
    `promote_image` and `image_subjects.copy_to_character` all write to the
    campaign root and all `mkdir(parents=True)`, so a user who uploads an image,
    sets an avatar crop, or promotes one — on a campaign whose migration is
    deferred because its world folder has not synced yet — creates the same
    directory the fork would have. What the world added afterwards is then read
    as something that user deleted. Assets the world adds to an
    already-copied version have never been distinguishable either.

    Nothing on disk separates "the user deleted this world image" from "this
    image was never copied here", and the error is one-way: a false tombstone
    hides inherited art permanently, while a missed one shows a deleted image
    again, which the user can delete once more. Left as it is because it is
    pre-overlay asset attribution, not #270's manifest-ref attribution, and
    narrowing it further is a change to make deliberately rather than in
    passing (Codex review)."""
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
            if not (root / kind / aid / "assets" / vid).is_dir():
                continue   # the fork never copied this version's assets here
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
    # This *is* a campaign-metadata write -- it rewrites campaign.md -- so it
    # advances the field that says when the metadata last changed, the way
    # rename_campaign does. Leaving it out made `updated` untrue about its own
    # file, not merely incomplete about the campaign.
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_campaign(cid: str) -> None:
    root = paths.campaign_root(cid)
    # same canonical-name requirement as delete_world: an rmtree must not
    # run for a spelling the store does not actually use (#259 review)
    if not paths.campaign_meta_path(cid).exists() or not worlds_paths.names_its_directory(root):
        raise paths.CampaignNotFound(cid)
    shutil.rmtree(root)
