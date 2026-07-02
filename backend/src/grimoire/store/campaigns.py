"""Campaign meta CRUD, copy-on-create from a world, and sync.md manifest IO."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import calendars, entities, worlds
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


def create_campaign(name: str, world_id: str, region: str | None = None) -> str:
    ensure_home()
    if not worlds.world_meta_path(world_id).exists():
        raise worlds.WorldNotFound(world_id)
    cid = uniquify(slugify(name), lambda c: campaign_root(c).exists())
    root = campaign_root(cid)
    root.mkdir(parents=True)
    (root / "scenes").mkdir()
    now = now_iso()
    campaign_meta_path(cid).write_text(
        dump_frontmatter({"name": name, "world": world_id, "created": now, "updated": now}, ""),
        encoding="utf-8",
    )
    # copy-on-create: deep-copy world entities + record base hashes
    wroot = worlds.world_root(world_id)
    manifest: dict[str, str] = {}
    for kind, eid in entities.all_refs(wroot):
        src = wroot / kind / f"{eid}.md"
        dst_dir = root / kind
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{eid}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        assets_dir = wroot / kind / eid / "assets"
        if assets_dir.exists():  # entity images (primary/gallery) travel with the copy
            shutil.copytree(assets_dir, root / kind / eid / "assets", dirs_exist_ok=True)
        manifest[f"{kind}/{eid}"] = entities.entity_hash(wroot, kind, eid) or ""
    write_manifest(cid, manifest)
    calendars.copy_calendar(wroot, root)
    if region is not None:
        cfg = calendars.read_calendar(root)
        cfg["primary"]["region"] = region
        calendars.write_calendar(root, cfg)
    return cid


def read_campaign(cid: str) -> dict:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "body": body}


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
