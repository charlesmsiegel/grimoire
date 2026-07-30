"""Character-creation writes: pool spends priced against a sheet type's
``creation`` block, plus the world-sheet delete that pairs with them.

``delete_world`` here deletes a *world's sheet* for one entity; it is unrelated
to ``worlds.delete_world``, which deletes the world.
"""

from __future__ import annotations

from pathlib import Path

from .. import characters, entities, locks, overlay, pcs
from ..modules import (binding as modules_binding, pack as modules_pack,
                       validate as modules_validate)
from ..paths import safe_id
from ..worlds import paths as worlds_paths
from . import paths, schema, writer
# Aliased: `_checked_creation_write` has a local named `pools` (the sheet
# type's creation pools), which would shadow the submodule.
from . import pools as pools_mod
from .paths import FILE_KINDS, SheetConflict, SheetError


def _assert_world_entity_exists(wid: str, kind: str, eid: str) -> None:
    """Raises the underlying store's NotFound exception if eid doesn't exist.
    Skips silently for a kind outside FILE_KINDS -- the write path's own
    kind validation (_validate_write_target) already rejects that case."""
    if kind not in FILE_KINDS:
        return
    root = worlds_paths.world_root(wid)
    if kind == "characters":
        characters.read_character(root, eid)
    elif kind == "pcs":
        pcs.read_pc(root, eid)
    else:
        entities.read_entity(root, kind, eid)


def _assert_campaign_entity_exists(cid: str, kind: str, eid: str) -> None:
    if kind not in FILE_KINDS:
        return
    if kind == "characters":
        overlay.read_character(cid, eid)
    elif kind == "pcs":
        pcs.read_pc(overlay.pc_root(cid, eid), eid)
    else:
        overlay.read_entity(cid, kind, eid)


def _checked_creation_write(path: Path, mid: str, file_kind: str, eid: str,
                            sheet_type: str, spends: dict) -> None:
    sheets_def = writer._validate_write_target(mid, file_kind, eid, sheet_type)
    if not isinstance(spends, dict):
        raise SheetError("spends must be an object")
    st = sheets_def["sheet_types"][sheet_type]
    pools = st.get("creation", {}).get("pools", {}) if isinstance(st.get("creation"), dict) else {}
    for pool_id in spends:
        if pool_id not in pools:
            raise SheetError(f"unknown pool {pool_id!r}")
    fields = schema.default_fields(sheets_def, sheet_type)
    for pool_id, pool in pools.items():
        if not isinstance(pool, dict):
            continue
        costs = pool.get("costs", {})
        group_fields = pools_mod._pool_group_fields(sheets_def, pool_id)
        pool_spends = spends.get(pool_id, {})
        if not isinstance(pool_spends, dict):
            raise SheetError(f"spends[{pool_id!r}] must be an object")
        for extra in set(pool_spends) - set(costs):
            raise SheetError(f"{extra!r} is not a costed field of pool {pool_id!r}")
        total = 0
        for field_key, cost in costs.items():
            fdef = group_fields.get(field_key, {})
            floor = pools_mod._pool_floor(fdef)
            value = pool_spends.get(field_key, floor)
            if not isinstance(value, int) or isinstance(value, bool):
                raise SheetError(f"{field_key!r}: expected an integer")
            fmax = fdef.get("max")
            hi = fmax if isinstance(fmax, int) and not isinstance(fmax, bool) else floor
            if not floor <= value <= hi:
                raise SheetError(f"{field_key!r}: outside {floor}..{hi}")
            total += (value - floor) * cost
            fields[field_key] = value
        budget = pools_mod._pool_budget(pool)
        if total > budget:
            raise SheetError(f"pool {pool_id!r}: spent {total}, budget {budget}")
    errs = modules_validate.validate_sheet_values(sheets_def, sheet_type, fields)
    if errs:
        raise SheetError("; ".join(errs))
    paths._atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields,
                                    "gen": paths._next_gen(path, sheet_type)})


def write_creation(cid: str, kind: str, eid: str, sheet_type: str,
                   spends: dict[str, dict[str, int]], *, expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    with locks.campaign_lock(cid):
        # resolve INSIDE the lock -- see write()'s rebind-serialization note.
        mid = modules_binding.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _assert_campaign_entity_exists(cid, kind, eid)
        path = paths._campaign_path(cid, kind, eid)
        writer._check_expected(path, expected)
        _checked_creation_write(path, mid, kind, eid, sheet_type, spends)


def write_world_creation(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                         spends: dict[str, dict[str, int]], *,
                         expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    modules_pack.pack_root(mid)  # raises ModuleNotFound
    _assert_world_entity_exists(wid, kind, eid)
    path = paths._world_path(wid, mid, kind, eid)
    writer._check_expected(path, expected)
    _checked_creation_write(path, mid, kind, eid, sheet_type, spends)


def delete_world(wid: str, mid: str, kind: str, eid: str, *,
                 expected_gen: str | None) -> bool:
    """``expected_gen`` is mandatory CAS -- see delete()."""
    if kind not in FILE_KINDS or not safe_id(eid) or not safe_id(mid):
        return False
    p = paths._world_path(wid, mid, kind, eid)
    stored = writer._stored_snapshot(p)
    if stored is None:
        return False
    if stored["gen"] != expected_gen:
        raise SheetConflict("the sheet changed since it was loaded")
    p.unlink()
    return True
