"""Binding: the world/campaign ``module`` keys, and resolve().

The only part of this package that reaches sideways into ``campaigns`` and
``worlds``, which is why it is imported last by ``__init__``.
"""

from __future__ import annotations

from .. import atomic
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..worlds import paths as worlds_paths
from ..worlds import read as worlds_read

# Through the module object, not by value: `load_pack` is a fault-injection
# target (test_audit_store.py), and in the flat module `resolve` reached it as
# a plain global -- i.e. the same binding the patch replaces. Aliased because
# `resolve` already has a local named `pack`. Exceptions come across by value:
# `except` needs the class, and rebinding one is not a thing tests do.
from . import pack as pack_mod
from .pack import ModuleError, ModuleNotFound


def _write_key(meta_path, key: str, value: str) -> None:
    text = meta_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if value:
        meta[key] = value
    else:
        meta.pop(key, None)
    atomic.write_text(meta_path, dump_frontmatter(meta, body))


def set_world_module(wid: str, mid: str) -> None:
    worlds_read.read_world(wid)  # raises WorldNotFound
    if mid == "none":
        raise ModuleError("'none' is reserved")
    if mid:
        pack_mod.pack_root(mid)  # raises ModuleNotFound
    _write_key(worlds_paths.world_meta_path(wid), "module", mid)


def set_campaign_module(cid: str, value: str) -> None:
    """value: "" -> inherit world default, "none" -> mechanics off, else mid."""
    campaigns_read.read_campaign(cid)  # raises CampaignNotFound
    if value and value != "none":
        pack_mod.pack_root(value)
    _write_key(campaigns_paths.campaign_meta_path(cid), "module", value)


def resolve(cid: str) -> str | None:
    """The module id governing a campaign, or None (= zero mechanics).
    Campaign tri-state ("", "none", mid) over world default; a binding to a
    missing or invalid module falls through to None."""
    meta = campaigns_read.read_campaign(cid)["meta"]
    setting = (meta.get("module") or "").strip()
    if setting == "none":
        return None
    mid = setting
    if not mid:
        try:
            wmeta = worlds_read.read_world(meta.get("world", ""))["meta"]
        except worlds_paths.WorldNotFound:
            return None
        mid = (wmeta.get("module") or "").strip()
    if not mid:
        return None
    try:
        pack = pack_mod.load_pack(mid)
    except ModuleNotFound:
        return None
    return None if pack["errors"] else mid
