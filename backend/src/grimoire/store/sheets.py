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
import shutil
from pathlib import Path

from . import campaigns, entities, expressions, modules, worlds


class SheetError(Exception):
    """Rejected sheet write (no module, bad kind/type/values)."""


FILE_KINDS: tuple[str, ...] = ("characters", "pcs") + entities.ENTITY_KINDS


def sheet_kind(kind: str) -> str:
    """Module sheet-type kind for a file kind (pcs share characters types)."""
    return "characters" if kind == "pcs" else kind


def _safe_part(part: str) -> bool:
    if not isinstance(part, str):
        return False
    return bool(part) and part not in (".", "..") and "/" not in part and "\\" not in part


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

    groups = st.get("groups") if isinstance(st.get("groups"), list) else []
    for gid in groups:
        g = sheets_def.get("groups", {}).get(gid) if isinstance(gid, str) else None
        if isinstance(g, dict):
            run(g.get("derived", {}))
    run(st.get("derived", {}))
    return out


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


def _read_path(path: Path, file_kind: str, mid: str | None) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {"sheet_type": None, "fields": {}, "derived": {},
                "errors": [f"unreadable sheet file: {e}"]}
    if not isinstance(data, dict):
        return {"sheet_type": None, "fields": {}, "derived": {},
                "errors": ["sheet file must be an object"]}
    sheet_type = data.get("sheet_type")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if mid is None:
        return {"sheet_type": sheet_type, "fields": fields, "derived": {},
                "errors": ["no module resolved"]}
    sheets_def = modules.load_pack(mid)["sheets"]
    errors = _validate_instance(sheets_def, file_kind, sheet_type, fields)
    derived: dict = {}
    if isinstance(sheet_type, str):
        derived = _compute_derived(sheets_def, sheet_type, fields, errors)
    return {"sheet_type": sheet_type, "fields": fields,
            "derived": derived, "errors": errors}


def read(cid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return None
    try:
        mid = modules.resolve(cid)
    except campaigns.CampaignNotFound:
        raise
    return _read_path(_campaign_path(cid, kind, eid), kind, mid)


def _checked_write(path: Path, mid: str, file_kind: str, eid: str,
                   sheet_type: str, fields: dict | None) -> None:
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
    if fields is None:
        fields = default_fields(sheets_def, sheet_type)
    else:
        if not isinstance(fields, dict):
            raise SheetError("fields must be an object")
        allowed = {f.get("key") for f in modules.assembled_fields(sheets_def, sheet_type)
                   if isinstance(f, dict)}
        fields = {k: v for k, v in fields.items() if k in allowed}
        errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sheet_type": sheet_type, "fields": fields},
                               indent=2), encoding="utf-8")


def write(cid: str, kind: str, eid: str, sheet_type: str,
          fields: dict | None = None) -> None:
    """Create or replace a campaign sheet. A different ``sheet_type`` than
    the stored one is a type change: values whose keys exist in the new
    type's assembled field set are kept (caller passes them), others are
    filtered out here."""
    mid = modules.resolve(cid)
    if mid is None:
        raise SheetError("no module resolved for this campaign")
    _checked_write(_campaign_path(cid, kind, eid), mid, kind, eid,
                   sheet_type, fields)


def delete(cid: str, kind: str, eid: str) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return False
    p = _campaign_path(cid, kind, eid)
    if not p.exists():
        return False
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
                fields: dict | None = None) -> None:
    modules.pack_root(mid)  # raises ModuleNotFound
    _checked_write(_world_path(wid, mid, kind, eid), mid, kind, eid,
                   sheet_type, fields)


def delete_world(wid: str, mid: str, kind: str, eid: str) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return False
    p = _world_path(wid, mid, kind, eid)
    if not p.exists():
        return False
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
