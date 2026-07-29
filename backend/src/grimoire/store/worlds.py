"""World meta CRUD. A world is a directory of entity kind-folders + world.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import atomic, characters, entities, greetings, pcs
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, safe_id, slugify, uniquify


class WorldNotFound(Exception):
    pass


class WorldInUse(Exception):
    def __init__(self, wid: str, names: list[str]):
        self.names = names
        super().__init__(f"world is used by campaigns: {', '.join(names)}")


def _worlds_dir() -> Path:
    return home() / "worlds"


# An id `slugify` can never emit (it produces only [a-z0-9-]), so this names a
# directory the store never creates -- see world_root_or_missing.
_NO_WORLD = ".none"


def world_root(wid: str) -> Path:
    """The world's directory.

    Raises WorldNotFound for an id that doesn't name a child of the worlds dir
    -- including "", which would otherwise resolve to the worlds dir itself.
    The guard lives here rather than in the router so a caller that isn't an
    HTTP path parameter (a body field, a CLI script, an importer) gets it too.
    """
    if not safe_id(wid):
        raise WorldNotFound(wid)
    return _worlds_dir() / wid


def world_root_or_missing(wid: str) -> Path:
    """The world root for a campaign's recorded world, which may be unset.

    An empty id means the campaign has no world at all, so this hands back a
    path that cannot exist and every world-side read finds nothing -- which is
    what these callers already wanted from `world_root("")`. What they got was
    the worlds parent dir, which exists and holds every world.
    """
    return world_root(wid or _NO_WORLD)


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
                           "pcs": pcs.pc_count(d), "greetings": greetings.greeting_count(d)},
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def create_world(name: str) -> str:
    ensure_home()
    wid = uniquify(slugify(name), lambda c: world_root(c).exists())
    world_root(wid).mkdir(parents=True)
    now = now_iso()
    atomic.write_text(world_meta_path(wid), dump_frontmatter({"name": name, "created": now, "updated": now}, ""))
    return wid


def read_world(wid: str) -> dict:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    root = world_root(wid)
    return {"meta": {"id": wid, **meta}, "body": body,
            "counts": {**entities.entity_counts(root), "characters": characters.character_count(root),
                       "pcs": pcs.pc_count(root), "greetings": greetings.greeting_count(root)}}


def world_name(wid: str) -> str | None:
    """Just the display name — no entity counts, one file read (for embedding
    in other payloads without read_world's directory sweeps)."""
    mp = world_meta_path(wid)
    if not mp.exists():
        return None
    meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return meta.get("name", wid)


def rename_world(wid: str, name: str) -> None:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_world(wid: str) -> None:
    root = world_root(wid)
    if not world_meta_path(wid).exists():
        raise WorldNotFound(wid)
    from . import campaigns  # function-level: campaigns imports worlds at module level
    used_by = [c["name"] for c in campaigns.list_campaigns() if c.get("world") == wid]
    if used_by:
        raise WorldInUse(wid, used_by)
    shutil.rmtree(root)
