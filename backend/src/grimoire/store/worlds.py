"""World meta CRUD. A world is a directory of entity kind-folders + world.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import characters, entities, pcs
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify


class WorldNotFound(Exception):
    pass


def _worlds_dir() -> Path:
    return home() / "worlds"


def world_root(wid: str) -> Path:
    return _worlds_dir() / wid


def world_meta_path(wid: str) -> Path:
    return world_root(wid) / "world.md"


def list_worlds() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = _worlds_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "world.md"
            if not d.is_dir() or not mp.exists():
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "counts": {**entities.entity_counts(d), "characters": characters.character_count(d),
                           "pcs": pcs.pc_count(d)},
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def create_world(name: str) -> str:
    ensure_home()
    wid = uniquify(slugify(name), lambda c: world_root(c).exists())
    world_root(wid).mkdir(parents=True)
    now = now_iso()
    world_meta_path(wid).write_text(
        dump_frontmatter({"name": name, "created": now, "updated": now}, ""),
        encoding="utf-8",
    )
    return wid


def read_world(wid: str) -> dict:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    root = world_root(wid)
    return {"meta": {"id": wid, **meta}, "body": body,
            "counts": {**entities.entity_counts(root), "characters": characters.character_count(root),
                       "pcs": pcs.pc_count(root)}}


def rename_world(wid: str, name: str) -> None:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def delete_world(wid: str) -> None:
    root = world_root(wid)
    if not world_meta_path(wid).exists():
        raise WorldNotFound(wid)
    shutil.rmtree(root)
