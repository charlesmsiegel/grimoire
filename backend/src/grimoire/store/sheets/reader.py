"""Reading stored sheets and enumerating them.

Named ``reader`` and not ``read``: ``read`` is a public function of this
package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function, leaving ``from ..sheets import read``
bound to the function rather than the module.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..modules import binding as modules_binding
from ..modules import pack as modules_pack
from ..paths import safe_id
from ..worlds import paths as worlds_paths
from . import paths, schema
from .paths import FILE_KINDS


def _read_path(path: Path, file_kind: str, mid: str | None,
               pack: dict | None = None) -> dict | None:
    """``pack`` is the caller's already-loaded pack for ``mid``, for a sweep
    reading many sheets against one module.

    Not an optimization looking for a problem: ``load_pack`` re-reads and
    re-validates the whole pack from disk every call with no memo, and
    ``read`` below pays for TWO of them per sheet -- one here and one inside
    ``modules_binding.resolve``. Per cast member, that is what a coverage sweep
    over a few hundred characters costs. Passing a pack that is not ``mid``'s
    is the caller's bug; every caller here loads it two lines earlier."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {"sheet_type": None, "fields": {}, "derived": {}, "gen": None,
                "errors": [f"unreadable sheet file: {e}"]}
    if not isinstance(data, dict):
        return {"sheet_type": None, "fields": {}, "derived": {}, "gen": None,
                "errors": ["sheet file must be an object"]}
    sheet_type = data.get("sheet_type")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if mid is None:
        return {"sheet_type": sheet_type, "fields": fields, "derived": {},
                "gen": data.get("gen"), "errors": ["no module resolved"]}
    if pack is None:
        pack = modules_pack.load_pack(mid)
    errors = schema.instance_errors(pack, file_kind, sheet_type, fields)
    derived: dict = {}
    if isinstance(sheet_type, str):
        derived = schema._compute_derived(pack["sheets"], sheet_type, fields, [])
    return {"sheet_type": sheet_type, "fields": fields,
            "derived": derived, "gen": data.get("gen"), "errors": errors}


def read(cid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not safe_id(eid):
        return None
    mid = modules_binding.resolve(cid)
    return _read_path(paths._campaign_path(cid, kind, eid), kind, mid)


def read_world(wid: str, mid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not safe_id(eid) or not safe_id(mid):
        return None
    try:
        modules_pack.pack_root(mid)
    except modules_pack.ModuleNotFound:
        return None
    return _read_path(paths._world_path(wid, mid, kind, eid), kind, mid)


def list_refs(cid: str) -> list[tuple[str, str]]:
    d = paths._campaign_dir(cid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and safe_id(eid):
            out.append((kind, eid))
    return out


def world_list_refs(wid: str, mid: str) -> list[tuple[str, str]]:
    d = paths._world_dir(wid, mid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and safe_id(eid):
            out.append((kind, eid))
    return out


def world_sheet_modules(wid: str) -> list[str]:
    d = worlds_paths.world_root(wid) / "sheets"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and safe_id(p.name))
