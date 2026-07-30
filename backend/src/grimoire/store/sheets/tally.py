"""Coverage tallies (how many of a campaign's/world's entities have a valid
sheet) and ``seed``, the one-time copy of a world's starting sheets.

Named ``tally`` and not ``coverage``: ``coverage`` is a public function of
this package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function.
"""

from __future__ import annotations

from .. import atomic, characters, entities, locks, overlay, pcs
from ..campaigns import read as campaigns_read
from ..modules import binding as modules_binding, pack as modules_pack
from ..worlds import paths as worlds_paths
from . import paths
# Aliased: `_tally` takes a parameter named `reader`, which would shadow the
# submodule inside it.
from . import reader as sheet_reader
from .paths import FILE_KINDS


def seed(cid: str) -> int:
    """Copy world starting sheets for the campaign's resolved module.
    Called once from create_campaign; changing the module later never
    re-seeds (spec).

    Takes the campaign lock even though it runs during creation. "Nobody else
    has this cid yet" is true only *in process*: ``create_campaign`` publishes
    ``campaign.md`` several operations before it gets here, and
    ``list_campaigns`` reports any directory holding that file — so a second
    grimoire process (the case ``campaign_lock`` exists for, #234) can discover
    the campaign mid-creation and write a sheet this copy would then overwrite.
    """
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            return 0
        src = campaigns_read.world_root_of(cid) / "sheets" / mid
        if not src.is_dir():
            return 0
        dst = paths._campaign_dir(cid)
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in sorted(src.glob("*.json")):
            # through the helper, not shutil.copy2: a partial copy must never
            # appear under a real sheet name
            atomic.write_bytes(dst / p.name, p.read_bytes())
            n += 1
        return n


def _type_kinds(sheets_def: dict) -> set[str]:
    return {st.get("kind") for st in sheets_def.get("sheet_types", {}).values()
            if isinstance(st, dict)}


def _tally(ids: list[str], reader) -> dict:
    sheeted = invalid = 0
    for eid in ids:
        s = reader(eid)
        if s is None:
            continue
        sheeted += 1
        if s["errors"]:
            invalid += 1
    return {"total": len(ids), "sheeted": sheeted, "invalid": invalid}


def coverage(cid: str) -> dict:
    mid = modules_binding.resolve(cid)
    if mid is None:
        return {}
    kinds = _type_kinds(modules_pack.load_pack(mid)["sheets"])
    out: dict = {}
    for kind in FILE_KINDS:
        if paths.sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in overlay.list_characters(cid)]
        elif kind == "pcs":
            ids = [p["id"] for p in overlay.list_pcs(cid)]
        else:
            ids = [e["id"] for e in overlay.list_entities(cid, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: sheet_reader.read(cid, k, eid))
    return out


def world_coverage(wid: str, mid: str) -> dict:
    try:
        modules_pack.pack_root(mid)
    except modules_pack.ModuleNotFound:
        return {}
    pack = modules_pack.load_pack(mid)
    if pack["errors"]:
        return {}
    kinds = _type_kinds(pack["sheets"])
    root = worlds_paths.world_root(wid)
    out: dict = {}
    for kind in FILE_KINDS:
        if paths.sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in characters.list_characters(root)]
        elif kind == "pcs":
            ids = [p["id"] for p in pcs.list_pcs(root)]
        else:
            ids = [e["id"] for e in entities.list_entities(root, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: sheet_reader.read_world(wid, mid, k, eid))
    return out
