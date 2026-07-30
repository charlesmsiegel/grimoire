"""Campaign meta CRUD, copy-on-create from a world, and sync.md manifest IO."""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from . import (assets, atomic, calendars, characters, entities, greetings, locks, pcs,
               worlds)
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, safe_id, slugify, uniquify


class CampaignNotFound(Exception):
    pass


def _campaigns_dir() -> Path:
    return home() / "campaigns"


def campaign_root(cid: str) -> Path:
    """The campaign's own directory — nothing it inherits from its world.

    Correct for campaign-local state (scenes, sheets, proposals, chronicle,
    playstate, calendar.json, the climate default, ...) and for writes, which is how a
    record materializes. It is *not* a place to read a record the campaign
    inherits: `overlay.INHERITED_KINDS` / `INHERITED_FILES` say which those are,
    and `store/overlay.py` is the only thing that resolves them. Reading one
    here misses everything still live-inherited from the world, and misses the
    campaign's tombstones — silently, which is why `tests/test_overlay_guard.py`
    checks for it (#248).

    Raises CampaignNotFound for an id that doesn't name a child of the
    campaigns dir. The guard lives here rather than in the router so a caller
    that isn't an HTTP path parameter gets it too (#240).
    """
    if not safe_id(cid):
        raise CampaignNotFound(cid)
    return _campaigns_dir() / cid


def campaign_meta_path(cid: str) -> Path:
    return campaign_root(cid) / "campaign.md"


# A campaign may record no world at all, and every world-side read still wants
# a path it can treat as empty. That path has to be one nothing can occupy: any
# sentinel *directory* is one a restored or hand-managed store may already
# contain, and then a world-less campaign inherits whatever is inside it. So
# absence resolves below the campaign's own campaign.md -- a regular file, so
# the filesystem itself guarantees no child of it can ever exist.
_NO_WORLD = "(no world)"


def world_root_of(cid: str) -> Path:
    """The root of the campaign's world, or an unoccupiable path if it has none.

    A stored `world` the guard refuses to resolve — a restored or hand-edited
    campaign can carry one — counts as "no world" rather than raising: a world
    directory that has been deleted already reads as inheriting nothing, and a
    reference that cannot name one is no different. Raises CampaignNotFound
    for a campaign that isn't there. Callers holding a world id they know is
    set should use `worlds.world_root` directly.
    """
    wid = read_campaign(cid)["meta"].get("world", "")
    try:
        return worlds.world_root(wid)
    except worlds.WorldNotFound:
        return campaign_meta_path(cid) / _NO_WORLD


def campaign_exists(cid: str) -> bool:
    """Existence check that survives an id `campaign_root` refuses to resolve.

    Callers testing "is there such a campaign?" want False for an unusable id,
    not an exception -- see worlds.world_exists.
    """
    try:
        return campaign_meta_path(cid).exists()
    except CampaignNotFound:
        return False


def _manifest_path(cid: str) -> Path:
    return campaign_root(cid) / "sync.md"


def read_manifest(cid: str) -> dict[str, str]:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def write_manifest(cid: str, manifest: dict[str, str]) -> None:
    atomic.write_text(_manifest_path(cid), dump_frontmatter(manifest, ""))


def list_campaigns() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = _campaigns_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            # see worlds.list_worlds: enumeration agrees with the resolvers, so a
            # stray directory can't abort a listing -- or the startup migration
            if not d.is_dir() or not mp.exists() or not safe_id(d.name):
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "world": meta.get("world", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def world_refs() -> list[tuple[str, str | None]]:
    """(campaign name, referenced world id) for *every* campaign on disk.

    Deliberately unfiltered, unlike `list_campaigns`. This backs
    `worlds.delete_world`'s in-use check, and a campaign that is unusable as an
    id still pins the world it references: filtering it out of the check is
    what would make that world deletable out from under it (#259 review).
    Enumeration may hide a record from the UI; it must never hide it from a
    referential-integrity check.

    A world id of ``None`` means "this campaign's reference could not be read".
    Undecodable bytes in the *body* must not cost us a reference sitting in
    perfectly good frontmatter, so the read is retried lossily first; only a
    file that cannot be read at all yields ``None``. Callers must treat that as
    "may reference anything" -- skipping it is how "we could not tell" turns
    into "nothing uses this world", which deletes it (#259 review).
    """
    out: list[tuple[str, str | None]] = []
    base = _campaigns_dir()
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        mp = d / "campaign.md"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            text = mp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:   # frontmatter survives a bad byte in the body
                text = mp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                out.append((d.name, None))
                continue
        except OSError:
            out.append((d.name, None))
            continue
        meta, _ = parse_frontmatter(text)
        out.append((meta.get("name", d.name), meta.get("world", "")))
    return out


def create_campaign(name: str, world_id: str, region: str | None = None,
                     calendar: str | None = None, module: str | None = None,
                     climate: str | None = None) -> str:
    ensure_home()
    if not worlds.world_exists(world_id):
        raise worlds.WorldNotFound(world_id)
    # `world_exists` resolves case-insensitively where the filesystem does, so
    # the caller's spelling may not be the one on disk. Store the canonical one
    # or the reference is invisible to a later string comparison (#259 review).
    world_id = worlds.canonical_id(world_id)
    if calendar is not None:
        calendars.get_provider({"provider": calendar})  # unknown id -> CalendarError before anything is created
    from . import campaign_climate, climates
    wanted_climate = climate or climates.FALLBACK_ID
    campaign_climate.check_default(wanted_climate)  # unknown id -> fail before anything is created
    if module and module != "none":  # "none" = explicitly mechanics-free, always legal
        from . import modules
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
    cfg = calendars.read_calendar(worlds.world_root(world_id))
    if calendar is not None:
        cfg["primary"]["provider"] = calendar
        cfg["confirmed"] = True          # an explicit wizard choice
    if region is not None:
        cfg["primary"]["region"] = region
    if region is not None or calendar is not None:
        calendars.validate_calendar(cfg)   # unknown provider -> CalendarError
    cid = uniquify(slugify(name), lambda c: campaign_root(c).exists())
    root = campaign_root(cid)
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
        atomic.write_text(campaign_meta_path(cid), dump_frontmatter(
            {"name": name, "world": world_id, "created": now, "updated": now,
             "world_copy": "overlay",
             **({"module": module} if module else {})}, ""))
        # copy-on-write: nothing is copied up front; records materialize on divergence
        # (store/overlay.py) and sync.md tracks bases for materialized records only
        write_manifest(cid, {})
        calendars.write_calendar(root, cfg)
        campaign_climate.write_default(cid, wanted_climate)
        from . import sheets
        sheets.seed(cid)                 # reentrant: takes this same lock again
    return cid


def read_campaign(cid: str) -> dict:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "body": body}


def ensure_campaign_slim(cid: str) -> None:
    """One-time lazy migration of a full-copy campaign to the overlay layout.
    Deletes campaign files that are provably redundant — flat/actor content
    whose hash equals both the recorded sync base and the current world hash,
    plus byte-identical asset/sidecar copies — tombstones refs whose copy the
    user had deleted, and stamps world_copy: overlay. Skips (unmarked) while
    the world dir is missing so a late-syncing store slims on a later access.
    Locked actors keep their cards (the lock invariant needs them); diverged
    records and campaign-local files are never touched."""
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if meta.get("world_copy") == "overlay":
        return
    root = campaign_root(cid)
    wroot = world_root_of(cid)
    if not wroot.exists():
        return
    from . import appearances, overlay  # campaigns is imported by these

    locked = set(appearances.record(cid))
    manifest = read_manifest(cid)
    copied = set(manifest)   # every record the full copy tracked, before the loop prunes it
    for ref, base in sorted(list(manifest.items())):
        kind, _, eid = ref.partition("/")
        if ref == "plotmap":
            p = root / "plotmap.json"
            if not p.exists():
                if (wroot / "plotmap.json").exists():
                    overlay.add_deleted(cid, "plotmap")   # keep the user's deletion deleted
                manifest.pop(ref)
            elif greetings.plotmap_hash(root) == base == greetings.plotmap_hash(wroot):
                p.unlink()
                manifest.pop(ref)
            continue
        if kind in appearances.ACTOR_KINDS:
            if ref in locked:
                manifest.pop(ref)   # a lock owns its base in appearances.json
                continue
            dh = characters.dir_hash if kind == "characters" else pcs.dir_hash
            mine_h = dh(root, eid)
            if mine_h is None:
                if dh(wroot, eid) is not None:
                    overlay.add_deleted(cid, ref)   # keep the user's deletion deleted
                manifest.pop(ref)
            elif mine_h == base == dh(wroot, eid):
                overlay.dematerialize_actor(cid, kind, eid)
                manifest.pop(ref)
            continue
        p = root / kind / f"{eid}.md"
        if not p.exists():
            if (wroot / kind / f"{eid}.md").exists():
                overlay.add_deleted(cid, ref)   # keep the user's deletion deleted
            manifest.pop(ref)
        elif entities.entity_hash(root, kind, eid) == base == entities.entity_hash(wroot, kind, eid):
            p.unlink()
            manifest.pop(ref)
    write_manifest(cid, manifest)
    _tombstone_deleted_copied_assets(cid, root, wroot, copied)
    _prune_duplicate_files(root, wroot)
    meta["world_copy"] = "overlay"
    atomic.write_text(mp, dump_frontmatter(meta, body))


def _tombstone_deleted_copied_assets(cid: str, root: Path, wroot: Path, copied: set[str]) -> None:
    """A pre-overlay full copy held every world asset, so a world asset now
    missing from the campaign tree was deleted by the user before migration.
    Tombstone it, or the overlay would resurface the world copy once world_copy
    flips to overlay. Runs before _prune_duplicate_files so byte-identical
    copies are still present and not mistaken for deletions. Only records the
    full copy tracked (`copied`) are considered — world records/assets added
    after the fork stay live-inherited; whole-deleted records already carry a
    <base>/<aid> tombstone and are skipped."""
    from . import overlay  # campaigns is imported by overlay
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
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def set_campaign_response(cid: str, fields: dict) -> None:
    """Campaign-scope response settings; same semantics as scenes.set_response."""
    from . import scenes  # lazy: scenes imports campaigns, so avoid a module cycle

    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    for key in scenes.RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    atomic.write_text(mp, dump_frontmatter(meta, body))


def touch(cid: str) -> None:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_campaign(cid: str) -> None:
    root = campaign_root(cid)
    # same canonical-name requirement as delete_world: an rmtree must not
    # run for a spelling the store does not actually use (#259 review)
    if not campaign_meta_path(cid).exists() or not worlds.names_its_directory(root):
        raise CampaignNotFound(cid)
    shutil.rmtree(root)
