"""Sheet instances for sheetable entities (#161, mechanics Phase 3).

Campaign sheets live at ``<campaign>/sheets/<kind>--<id>.json``; world
starting sheets at ``<world>/sheets/<mid>/<kind>--<id>.json``. File shape:
``{"sheet_type": ..., "fields": {...}}``. Derived values are computed on
read, never stored. Sheets are campaign-owned mutable state: copied at
create (``seed``), never overlay-read. ``read``/``coverage`` never raise on
malformed sheet content; writes validate strictly and raise ``SheetError``.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase3-sheets-design.md.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from . import campaigns, characters, entities, expressions, modules, overlay, pcs, shapes, worlds


class SheetError(Exception):
    """Rejected sheet write (no module, bad kind/type/values)."""


class SheetConflict(SheetError):
    """CAS rejection: the sheet changed since the caller last read it."""


FILE_KINDS: tuple[str, ...] = ("characters", "pcs") + entities.ENTITY_KINDS


def _next_gen(path: Path, sheet_type: str) -> str:
    """Sheet identity nonce: preserved across same-type value writes, minted
    on creation and on type changes (a type change is logically a new sheet).
    Legacy files without a gen mint one on their next whole-sheet write."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = {}
        if isinstance(data, dict) and data.get("sheet_type") == sheet_type \
                and isinstance(data.get("gen"), str) and data["gen"]:
            return data["gen"]
    return uuid.uuid4().hex


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via a same-directory temp file + os.replace, so a crash
    mid-write can never leave a half-written sheet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def sheet_kind(kind: str) -> str:
    """Module sheet-type kind for a file kind (pcs share characters types)."""
    return "characters" if kind == "pcs" else kind


def _safe_part(part: str) -> bool:
    if not isinstance(part, str):
        return False
    return (bool(part) and part not in (".", "..") and "/" not in part
            and "\\" not in part and ":" not in part)


def _campaign_dir(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "sheets"


def _campaign_path(cid: str, kind: str, eid: str) -> Path:
    return _campaign_dir(cid) / f"{kind}--{eid}.json"


def _int_or(value, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def default_fields(sheets_def: dict, type_id: str) -> dict:
    """Schema-default value map for a sheet type (spec: Decisions table)."""
    out: dict = {}
    for f in modules.assembled_fields(sheets_def, type_id):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        t = f.get("type")
        if t in ("number", "dots", "track"):
            out[key] = _int_or(f.get("default"), 0)
        elif t == "resource":
            mx = _int_or(f.get("max"), 0)
            out[key] = {"current": _int_or(f.get("default"), mx), "max": mx}
        elif t == "text":
            out[key] = ""
        elif t == "list":
            out[key] = []
    return out


def _numeric_scope(sheets_def: dict, type_id: str, fields: dict) -> dict:
    """Expression scope: schema defaults overlaid with stored values."""
    merged = {**default_fields(sheets_def, type_id), **fields}
    scope: dict = {}
    for f in modules.assembled_fields(sheets_def, type_id):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        t = f.get("type")
        v = merged.get(key)
        if t in ("number", "dots", "track"):
            if isinstance(v, int) and not isinstance(v, bool):
                scope[key] = v
        elif t == "resource" and isinstance(v, dict):
            cur, mx = v.get("current"), v.get("max")
            if isinstance(cur, int) and not isinstance(cur, bool):
                scope[key] = cur
            if isinstance(mx, int) and not isinstance(mx, bool):
                scope[key + "_max"] = mx
    return scope


def _compute_derived(sheets_def: dict, type_id: str, fields: dict,
                     errors: list[str]) -> dict:
    """Group-level derived first (feeding the scope), then type-level."""
    st = sheets_def.get("sheet_types", {}).get(type_id)
    if not isinstance(st, dict):
        return {}
    scope = _numeric_scope(sheets_def, type_id, fields)
    out: dict = {}

    def run(derived: dict) -> None:
        if not isinstance(derived, dict):
            return
        for name, expr in derived.items():
            if not isinstance(expr, str):
                continue  # pack validation already flags these
            try:
                value = expressions.evaluate(expr, scope)
            except expressions.ExpressionError as e:
                errors.append(f"derived.{name}: {e}")
                continue
            out[name] = value
            scope[name] = value

    groups = shapes.list_at(st, "groups")
    for gid in groups:
        g = sheets_def.get("groups", {}).get(gid) if isinstance(gid, str) else None
        if isinstance(g, dict):
            run(g.get("derived", {}))
    run(st.get("derived", {}))
    return out


def expression_scope(sheet: dict, sheets_def: dict) -> dict:
    """Numeric scope + derived for an already-read sheet (#162: checks.py's
    check-formula and derived-tier evaluation reuse this instead of
    duplicating the numeric/derived assembly)."""
    scope = _numeric_scope(sheets_def, sheet["sheet_type"], sheet["fields"])
    scope.update({k: v for k, v in sheet["derived"].items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
    return scope


def _validate_instance(sheets_def: dict, file_kind: str, sheet_type,
                       fields: dict) -> list[str]:
    """Errors for a stored sheet against a module's sheets definition."""
    if not isinstance(sheet_type, str) or not sheet_type:
        return ["sheet has no sheet_type"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        return [f"unknown sheet type {sheet_type!r}"]
    if st.get("kind") != sheet_kind(file_kind):
        return [f"sheet type {sheet_type!r} targets kind {st.get('kind')!r}, "
                f"not {sheet_kind(file_kind)!r}"]
    return modules.validate_sheet_values(sheets_def, sheet_type, fields)


def instance_errors(pack: dict, file_kind: str, sheet_type, fields: dict) -> list[str]:
    """The full read-time judgment for a stored sheet against an arbitrary
    pack dict — sheet-type/kind/value validation PLUS derived evaluation
    against the stored values (impact scans must judge exactly as reads do)."""
    sheets_def = shapes.dict_at(pack, "sheets")
    errors = _validate_instance(sheets_def, file_kind, sheet_type, fields)
    if isinstance(sheet_type, str):
        _compute_derived(sheets_def, sheet_type, fields, errors)
    return errors


def _read_path(path: Path, file_kind: str, mid: str | None) -> dict | None:
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
    fields = shapes.dict_at(data, "fields")
    if mid is None:
        return {"sheet_type": sheet_type, "fields": fields, "derived": {},
                "gen": data.get("gen"), "errors": ["no module resolved"]}
    pack = modules.load_pack(mid)
    errors = instance_errors(pack, file_kind, sheet_type, fields)
    derived: dict = {}
    if isinstance(sheet_type, str):
        derived = _compute_derived(pack["sheets"], sheet_type, fields, [])
    return {"sheet_type": sheet_type, "fields": fields,
            "derived": derived, "gen": data.get("gen"), "errors": errors}


def read(cid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return None
    mid = modules.resolve(cid)
    return _read_path(_campaign_path(cid, kind, eid), kind, mid)


def _validate_write_target(mid: str, file_kind: str, eid: str, sheet_type: str) -> dict:
    """Shared prelude for every checked sheet write: validates file_kind/eid/
    sheet_type and returns the resolved sheets definition. Raises SheetError."""
    if file_kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {file_kind!r}")
    if not _safe_part(eid):
        raise SheetError(f"bad entity id {eid!r}")
    if not isinstance(sheet_type, str) or not sheet_type:
        raise SheetError("sheet_type must be a non-empty string")
    sheets_def = modules.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        raise SheetError(f"unknown sheet type {sheet_type!r}")
    if st.get("kind") != sheet_kind(file_kind):
        raise SheetError(
            f"sheet type {sheet_type!r} targets {st.get('kind')!r}, "
            f"not {sheet_kind(file_kind)!r}")
    return sheets_def


def _checked_write(path: Path, mid: str, file_kind: str, eid: str,
                   sheet_type: str, fields: dict | None) -> None:
    sheets_def = _validate_write_target(mid, file_kind, eid, sheet_type)
    if fields is None:
        fields = default_fields(sheets_def, sheet_type)
    else:
        if not isinstance(fields, dict):
            raise SheetError("fields must be an object")
        errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields,
                              "gen": _next_gen(path, sheet_type)})


def _stored_snapshot(path: Path) -> dict | None:
    """{"sheet_type", "fields", "gen"} as stored, or None when no file. An
    unreadable file yields an all-None snapshot (matches nothing sane)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"sheet_type": data.get("sheet_type"),
            "fields": shapes.dict_at(data, "fields"),
            "gen": data.get("gen")}


def _check_expected(path: Path, expected: dict | None) -> None:
    """Mandatory whole-sheet CAS. expected=None asserts creation; otherwise
    sheet_type AND fields AND gen must all match the stored snapshot."""
    stored = _stored_snapshot(path)
    if expected is None:
        if stored is not None:
            raise SheetConflict("a sheet already exists for this entity")
        return
    if stored is None:
        raise SheetConflict("no sheet exists for this entity")
    if not isinstance(expected, dict):
        raise SheetError("expected must be an object or null")
    if (stored["sheet_type"] != expected.get("sheet_type")
            or stored["fields"] != expected.get("fields")
            or stored["gen"] != expected.get("gen")):
        raise SheetConflict("the sheet changed since it was loaded")


def write(cid: str, kind: str, eid: str, sheet_type: str,
          fields: dict | None = None, *, expected: dict | None) -> None:
    """Create or replace a campaign sheet. A different ``sheet_type`` than
    the stored one is a type change: the caller must pass a clean payload
    containing only keys that exist in the new type's assembled field set --
    an unknown key is rejected with SheetError, never silently dropped.
    ``expected`` is mandatory whole-sheet CAS: the caller's last-read
    {"sheet_type", "fields", "gen"} snapshot, or None to assert no sheet
    exists yet -- raises SheetConflict on mismatch."""
    with lock_for(cid):
        # resolve INSIDE the lock: rebinds publish under this same lock, so a
        # writer can never resolve module A, lose the CPU to a rebind to B,
        # and then validate/write under A after B is visible.
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        path = _campaign_path(cid, kind, eid)
        _check_expected(path, expected)
        _checked_write(path, mid, kind, eid, sheet_type, fields)


def delete(cid: str, kind: str, eid: str, *, expected_gen: str | None) -> bool:
    """Delete a campaign sheet. ``expected_gen`` is mandatory CAS: the
    caller's last-read gen (None matches a legacy file with no gen minted
    yet). A missing file is False, never a conflict."""
    if kind not in FILE_KINDS or not _safe_part(eid):
        return False
    with lock_for(cid):
        p = _campaign_path(cid, kind, eid)
        stored = _stored_snapshot(p)
        if stored is None:
            return False
        if stored["gen"] != expected_gen:
            raise SheetConflict("the sheet changed since it was loaded")
        p.unlink()
        return True


def list_refs(cid: str) -> list[tuple[str, str]]:
    d = _campaign_dir(cid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and _safe_part(eid):
            out.append((kind, eid))
    return out


def _world_dir(wid: str, mid: str) -> Path:
    return worlds.world_root(wid) / "sheets" / mid


def _world_path(wid: str, mid: str, kind: str, eid: str) -> Path:
    return _world_dir(wid, mid) / f"{kind}--{eid}.json"


def read_world(wid: str, mid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return None
    try:
        modules.pack_root(mid)
    except modules.ModuleNotFound:
        return None
    return _read_path(_world_path(wid, mid, kind, eid), kind, mid)


def write_world(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                fields: dict | None = None, *, expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    if not _safe_part(mid):
        raise SheetError(f"bad module id {mid!r}")
    modules.pack_root(mid)  # raises ModuleNotFound
    path = _world_path(wid, mid, kind, eid)
    _check_expected(path, expected)
    _checked_write(path, mid, kind, eid, sheet_type, fields)


def _pool_floor(field: dict) -> int:
    if field.get("type") == "number":
        m = field.get("min")
        return m if isinstance(m, int) and not isinstance(m, bool) else 0
    return 0  # dots/track floor is always 0


def _pool_group_fields(sheets_def: dict, pool_id: str) -> dict[str, dict]:
    group = sheets_def.get("groups", {}).get(pool_id, {})
    fields = group.get("fields", []) if isinstance(group, dict) else []
    if not isinstance(fields, list):
        return {}
    return {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}


def _pool_budget(pool: dict) -> int:
    raw = pool.get("budget", 0)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return expressions.evaluate(str(raw), {})


def _assert_world_entity_exists(wid: str, kind: str, eid: str) -> None:
    """Raises the underlying store's NotFound exception if eid doesn't exist.
    Skips silently for a kind outside FILE_KINDS -- the write path's own
    kind validation (_validate_write_target) already rejects that case."""
    if kind not in FILE_KINDS:
        return
    root = worlds.world_root(wid)
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
    sheets_def = _validate_write_target(mid, file_kind, eid, sheet_type)
    if not isinstance(spends, dict):
        raise SheetError("spends must be an object")
    st = sheets_def["sheet_types"][sheet_type]
    pools = st.get("creation", {}).get("pools", {}) if isinstance(st.get("creation"), dict) else {}
    for pool_id in spends:
        if pool_id not in pools:
            raise SheetError(f"unknown pool {pool_id!r}")
    fields = default_fields(sheets_def, sheet_type)
    for pool_id, pool in pools.items():
        if not isinstance(pool, dict):
            continue
        costs = pool.get("costs", {})
        group_fields = _pool_group_fields(sheets_def, pool_id)
        pool_spends = spends.get(pool_id, {})
        if not isinstance(pool_spends, dict):
            raise SheetError(f"spends[{pool_id!r}] must be an object")
        for extra in set(pool_spends) - set(costs):
            raise SheetError(f"{extra!r} is not a costed field of pool {pool_id!r}")
        total = 0
        for field_key, cost in costs.items():
            fdef = group_fields.get(field_key, {})
            floor = _pool_floor(fdef)
            value = pool_spends.get(field_key, floor)
            if not isinstance(value, int) or isinstance(value, bool):
                raise SheetError(f"{field_key!r}: expected an integer")
            fmax = fdef.get("max")
            hi = fmax if isinstance(fmax, int) and not isinstance(fmax, bool) else floor
            if not floor <= value <= hi:
                raise SheetError(f"{field_key!r}: outside {floor}..{hi}")
            total += (value - floor) * cost
            fields[field_key] = value
        budget = _pool_budget(pool)
        if total > budget:
            raise SheetError(f"pool {pool_id!r}: spent {total}, budget {budget}")
    errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
    if errs:
        raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields,
                              "gen": _next_gen(path, sheet_type)})


def write_creation(cid: str, kind: str, eid: str, sheet_type: str,
                   spends: dict[str, dict[str, int]], *, expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    with lock_for(cid):
        # resolve INSIDE the lock -- see write()'s rebind-serialization note.
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _assert_campaign_entity_exists(cid, kind, eid)
        path = _campaign_path(cid, kind, eid)
        _check_expected(path, expected)
        _checked_creation_write(path, mid, kind, eid, sheet_type, spends)


def write_world_creation(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                         spends: dict[str, dict[str, int]], *,
                         expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    modules.pack_root(mid)  # raises ModuleNotFound
    _assert_world_entity_exists(wid, kind, eid)
    path = _world_path(wid, mid, kind, eid)
    _check_expected(path, expected)
    _checked_creation_write(path, mid, kind, eid, sheet_type, spends)


def delete_world(wid: str, mid: str, kind: str, eid: str, *,
                 expected_gen: str | None) -> bool:
    """``expected_gen`` is mandatory CAS -- see delete()."""
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return False
    p = _world_path(wid, mid, kind, eid)
    stored = _stored_snapshot(p)
    if stored is None:
        return False
    if stored["gen"] != expected_gen:
        raise SheetConflict("the sheet changed since it was loaded")
    p.unlink()
    return True


def world_list_refs(wid: str, mid: str) -> list[tuple[str, str]]:
    d = _world_dir(wid, mid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and _safe_part(eid):
            out.append((kind, eid))
    return out


def world_sheet_modules(wid: str) -> list[str]:
    d = worlds.world_root(wid) / "sheets"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and _safe_part(p.name))


def seed(cid: str) -> int:
    """Copy world starting sheets for the campaign's resolved module.
    Called once from create_campaign; changing the module later never
    re-seeds (spec)."""
    mid = modules.resolve(cid)
    if mid is None:
        return 0
    meta = campaigns.read_campaign(cid)["meta"]
    src = worlds.world_root(meta.get("world", "")) / "sheets" / mid
    if not src.is_dir():
        return 0
    dst = _campaign_dir(cid)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("*.json")):
        shutil.copy2(p, dst / p.name)
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
    mid = modules.resolve(cid)
    if mid is None:
        return {}
    kinds = _type_kinds(modules.load_pack(mid)["sheets"])
    out: dict = {}
    for kind in FILE_KINDS:
        if sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in overlay.list_characters(cid)]
        elif kind == "pcs":
            ids = [p["id"] for p in overlay.list_pcs(cid)]
        else:
            ids = [e["id"] for e in overlay.list_entities(cid, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: read(cid, k, eid))
    return out


def world_coverage(wid: str, mid: str) -> dict:
    try:
        modules.pack_root(mid)
    except modules.ModuleNotFound:
        return {}
    pack = modules.load_pack(mid)
    if pack["errors"]:
        return {}
    kinds = _type_kinds(pack["sheets"])
    root = worlds.world_root(wid)
    out: dict = {}
    for kind in FILE_KINDS:
        if sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in characters.list_characters(root)]
        elif kind == "pcs":
            ids = [p["id"] for p in pcs.list_pcs(root)]
        else:
            ids = [e["id"] for e in entities.list_entities(root, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: read_world(wid, mid, k, eid))
    return out


# ---- per-campaign sheet lock (#164, Phase 5): every sheet mutator serializes here ----

_registry_guard = threading.Lock()
_campaign_locks: dict[str, threading.RLock] = {}


def lock_for(cid: str) -> threading.RLock:
    """Get-or-create the per-campaign sheet lock atomically -- a plain
    `if cid not in _campaign_locks: ...` is a check-then-act race that can
    hand two concurrent first-ever callers different Lock objects. Public:
    every campaign-sheet mutator (write, write_creation, delete, advance),
    audit.capture_baseline, audit.apply_delta, and the module-rebind routes
    serialize on this. RLock so apply_delta can compose set_field under an
    already-held lock."""
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.RLock())


# ---- advancement (#164, Phase 7): single resource pool, formula-priced raises ----


def _advancement_cost(sheets_def: dict, type_id: str, field_key: str,
                      fields: dict, new: int) -> int:
    """Evaluate an advancement cost against a tentative post-raise scope:
    the raised field is set to `new` before recomputing derived values, so a
    cost formula referencing a derived name sees the post-raise state."""
    tentative = {**fields, field_key: new}
    scope = _numeric_scope(sheets_def, type_id, tentative)
    derived_errors: list[str] = []
    derived = _compute_derived(sheets_def, type_id, tentative, derived_errors)
    st = sheets_def.get("sheet_types", {}).get(type_id, {})
    adv = st.get("advancement", {}) if isinstance(st, dict) else {}
    expr = adv.get("costs", {}).get(field_key) if isinstance(adv, dict) else None
    if not isinstance(expr, str):
        raise SheetError(f"{field_key!r} is not advancement-eligible")
    try:
        cost = expressions.evaluate(expr, {**scope, **derived, "new": new})
    except expressions.ExpressionError as e:
        raise SheetError(f"advancement cost for {field_key!r}: {e}")
    if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
        raise SheetError(f"advancement cost for {field_key!r} must be a positive integer, got {cost!r}")
    return cost


def advance(cid: str, kind: str, eid: str, field_key: str) -> dict:
    with lock_for(cid):
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _assert_campaign_entity_exists(cid, kind, eid)
        path = _campaign_path(cid, kind, eid)
        if not path.exists():
            raise SheetError("no sheet exists for this entity")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            raise SheetError(f"unreadable sheet file: {e}")
        if not isinstance(data, dict):
            data = {}
        sheet_type = data.get("sheet_type")
        fields = shapes.dict_at(data, "fields")
        sheets_def = modules.load_pack(mid)["sheets"]
        st = sheets_def.get("sheet_types", {}).get(sheet_type) if isinstance(sheet_type, str) else None
        if not isinstance(st, dict):
            raise SheetError("sheet has no valid sheet type")
        adv = st.get("advancement")
        if not isinstance(adv, dict):
            raise SheetError("this sheet type has no advancement rules")
        pool_key = adv.get("pool")
        costs = adv.get("costs", {})
        if field_key not in costs:
            raise SheetError(f"{field_key!r} is not advancement-eligible")
        field_defs = {f["key"]: f for f in modules.assembled_fields(sheets_def, sheet_type)
                      if isinstance(f, dict) and isinstance(f.get("key"), str)}
        fdef = field_defs.get(field_key, {})
        current = fields.get(field_key, 0)
        current = current if isinstance(current, int) and not isinstance(current, bool) else 0
        fmax = fdef.get("max")
        if isinstance(fmax, int) and not isinstance(fmax, bool) and current >= fmax:
            raise SheetError(f"{field_key!r} is already at its maximum ({fmax})")
        new = current + 1
        cost = _advancement_cost(sheets_def, sheet_type, field_key, fields, new)
        pool_val = fields.get(pool_key)
        balance = pool_val.get("current", 0) if isinstance(pool_val, dict) else 0
        if balance < cost:
            raise SheetError(f"needs {cost} {pool_key}, have {balance}")
        pool_max = pool_val.get("max", balance) if isinstance(pool_val, dict) else 0
        new_fields = {**fields, field_key: new,
                      pool_key: {"current": balance - cost, "max": pool_max}}
        _atomic_write_json(path, {"sheet_type": sheet_type, "fields": new_fields,
                                  "gen": data.get("gen")})
        return _read_path(path, kind, mid)


# ---- set_field (mechanics Phase 5, Task 5): per-field strict-CAS apply ----

_MUTABLE_TYPES = ("resource", "track", "list")


def canonical_field_value(fdef: dict, value, live):
    """Canonical form of a proposed mutable-field value. Resources adopt the
    LIVE max (absorb/set_field never change max, only ``write`` can); shape
    mismatches raise SheetError."""
    t = fdef.get("type")
    if t == "resource":
        cur = value.get("current") if isinstance(value, dict) else value
        if not isinstance(cur, int) or isinstance(cur, bool):
            raise SheetError(f"{fdef.get('key')!r}: resource value needs an integer 'current'")
        live_max = live.get("max") if isinstance(live, dict) else None
        if not isinstance(live_max, int) or isinstance(live_max, bool):
            live_max = _int_or(fdef.get("max"), 0)
        return {"current": cur, "max": live_max}
    if t == "track":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SheetError(f"{fdef.get('key')!r}: expected an integer")
        return value
    if t == "list":
        if not isinstance(value, list):
            raise SheetError(f"{fdef.get('key')!r}: expected a list")
        return value
    raise SheetError(f"{fdef.get('key')!r} is not a mutable field")


def _set_field_locked(mid: str, cid: str, kind: str, eid: str,
                      field_key: str, value, expect) -> None:
    """Body of set_field; caller holds lock_for(cid) and resolved mid once."""
    if kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {kind!r}")
    if not _safe_part(eid):
        raise SheetError(f"bad entity id {eid!r}")
    path = _campaign_path(cid, kind, eid)
    stored = _stored_snapshot(path)
    if stored is None:
        raise SheetError("no sheet exists for this entity")
    sheets_def = modules.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(stored["sheet_type"]) \
        if isinstance(stored["sheet_type"], str) else None
    if not isinstance(st, dict):
        raise SheetError("sheet has no valid sheet type")
    fdefs = {f["key"]: f for f in modules.assembled_fields(sheets_def, stored["sheet_type"])
             if isinstance(f, dict) and isinstance(f.get("key"), str)}
    fdef = fdefs.get(field_key)
    if fdef is None or fdef.get("type") not in _MUTABLE_TYPES:
        raise SheetError(f"{field_key!r} is not a mutable field of this sheet")
    merged = {**default_fields(sheets_def, stored["sheet_type"]), **stored["fields"]}
    live = merged.get(field_key)
    new = canonical_field_value(fdef, value, live)
    want = canonical_field_value(fdef, expect, live) if expect is not None else None
    if live != want:
        raise SheetConflict(
            f"{field_key!r} is {live!r}, expected {want!r} -- "
            "already applied or independently changed")
    new_fields = {**stored["fields"], field_key: new}
    errs = modules.validate_sheet_values(sheets_def, stored["sheet_type"], new_fields)
    if errs:
        raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": stored["sheet_type"],
                              "fields": new_fields, "gen": stored["gen"]})


def set_field(cid: str, kind: str, eid: str, field_key: str, value, expect) -> None:
    """Per-field strict-CAS apply: raises SheetConflict when the live value
    doesn't equal the canonicalized ``expect`` -- including when it already
    equals the canonicalized ``value`` (a duplicate/independent apply must be
    reported, not silently accepted as a no-op)."""
    with lock_for(cid):
        # resolve INSIDE the lock -- see write()'s rebind-serialization note.
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _set_field_locked(mid, cid, kind, eid, field_key, value, expect)
