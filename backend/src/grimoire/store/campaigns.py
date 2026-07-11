"""Campaign meta CRUD, copy-on-create from a world, and sync.md manifest IO."""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from . import assets, calendars, characters, entities, greetings, pcs, worlds
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify


class CampaignNotFound(Exception):
    pass


def _campaigns_dir() -> Path:
    return home() / "campaigns"


def campaign_root(cid: str) -> Path:
    return _campaigns_dir() / cid


def campaign_meta_path(cid: str) -> Path:
    return campaign_root(cid) / "campaign.md"


def _manifest_path(cid: str) -> Path:
    return campaign_root(cid) / "sync.md"


def read_manifest(cid: str) -> dict[str, str]:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def write_manifest(cid: str, manifest: dict[str, str]) -> None:
    _manifest_path(cid).write_text(dump_frontmatter(manifest, ""), encoding="utf-8")


def list_campaigns() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = _campaigns_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            if not d.is_dir() or not mp.exists():
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


def create_campaign(name: str, world_id: str, region: str | None = None,
                     calendar: str | None = None) -> str:
    ensure_home()
    if not worlds.world_meta_path(world_id).exists():
        raise worlds.WorldNotFound(world_id)
    if calendar is not None:
        calendars.get_provider({"provider": calendar})  # unknown id -> CalendarError before anything is created
    cid = uniquify(slugify(name), lambda c: campaign_root(c).exists())
    root = campaign_root(cid)
    root.mkdir(parents=True)
    (root / "scenes").mkdir()
    now = now_iso()
    campaign_meta_path(cid).write_text(
        dump_frontmatter({"name": name, "world": world_id, "created": now, "updated": now,
                          "world_copy": "overlay"}, ""),
        encoding="utf-8",
    )
    # copy-on-write: nothing is copied up front; records materialize on divergence
    # (store/overlay.py) and sync.md tracks bases for materialized records only
    write_manifest(cid, {})
    calendars.copy_calendar(worlds.world_root(world_id), root)
    if region is not None or calendar is not None:
        cfg = calendars.read_calendar(root)
        if calendar is not None:
            cfg["primary"]["provider"] = calendar
            cfg["confirmed"] = True   # an explicit wizard choice
        if region is not None:
            cfg["primary"]["region"] = region
        calendars.validate_calendar(cfg)   # unknown provider -> CalendarError
        calendars.write_calendar(root, cfg)
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
    wroot = worlds.world_root(meta.get("world", ""))
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
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


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
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def touch(cid: str) -> None:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def delete_campaign(cid: str) -> None:
    root = campaign_root(cid)
    if not campaign_meta_path(cid).exists():
        raise CampaignNotFound(cid)
    shutil.rmtree(root)
