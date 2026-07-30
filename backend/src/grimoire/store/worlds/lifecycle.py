"""World create/rename/delete."""

from __future__ import annotations

import shutil

from .. import atomic
from ..campaigns import read as campaigns_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import ensure_home, now_iso, slugify, uniquify
from . import paths


class WorldInUse(Exception):
    def __init__(self, wid: str, names: list[str]):
        self.names = names
        super().__init__(f"world is used by campaigns: {', '.join(names)}")


def create_world(name: str) -> str:
    ensure_home()
    wid = uniquify(slugify(name), lambda c: paths.world_root(c).exists())
    paths.world_root(wid).mkdir(parents=True)
    now = now_iso()
    atomic.write_text(paths.world_meta_path(wid), dump_frontmatter({"name": name, "created": now, "updated": now}, ""))
    return wid


def rename_world(wid: str, name: str) -> None:
    mp = paths.world_meta_path(wid)
    if not mp.exists():
        raise paths.WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_world(wid: str) -> None:
    root = paths.world_root(wid)
    if not paths.world_meta_path(wid).exists() or not paths.names_its_directory(root):
        raise paths.WorldNotFound(wid)
    # world_refs, not list_campaigns: the in-use check has to see campaigns the
    # public listing hides, or hiding one makes its world deletable. A campaign
    # whose reference could not be read (w is None) counts as a user too --
    # deletion is irreversible, so "we could not tell" has to block it.
    used_by = [name for name, w in campaigns_read.world_refs()
               if w is None or paths.references_world(w, root)]
    if used_by:
        raise WorldInUse(wid, used_by)
    shutil.rmtree(root)
