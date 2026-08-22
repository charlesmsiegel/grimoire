"""Whole-sheet and per-field writes: the CAS prelude, the validated write, and
the campaign/world mutators that take the campaign lock.

Named ``writer`` and not ``write``: ``write`` is a public function of this
package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function, leaving ``from ..sheets import write``
bound to the function rather than the module.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import locks
from ..modules import binding as modules_binding
from ..modules import fields as modules_fields
from ..modules import pack as modules_pack
from ..modules import validate as modules_validate
from ..paths import safe_id
from . import paths, schema
from .paths import FILE_KINDS, SheetConflict, SheetError
from .schema import MUTABLE_TYPES


def _validate_write_target(mid: str, file_kind: str, eid: str, sheet_type: str) -> dict:
    """Shared prelude for every checked sheet write: validates file_kind/eid/
    sheet_type and returns the resolved sheets definition. Raises SheetError."""
    if file_kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {file_kind!r}")
    if not safe_id(eid):
        raise SheetError(f"bad entity id {eid!r}")
    if not isinstance(sheet_type, str) or not sheet_type:
        raise SheetError("sheet_type must be a non-empty string")
    sheets_def = modules_pack.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        raise SheetError(f"unknown sheet type {sheet_type!r}")
    if st.get("kind") != paths.sheet_kind(file_kind):
        raise SheetError(
            f"sheet type {sheet_type!r} targets {st.get('kind')!r}, "
            f"not {paths.sheet_kind(file_kind)!r}")
    return sheets_def


def _checked_write(path: Path, mid: str, file_kind: str, eid: str,
                   sheet_type: str, fields: dict | None) -> None:
    sheets_def = _validate_write_target(mid, file_kind, eid, sheet_type)
    if fields is None:
        fields = schema.default_fields(sheets_def, sheet_type)
    else:
        if not isinstance(fields, dict):
            raise SheetError("fields must be an object")
        errs = modules_validate.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
    paths._atomic_write_json(path, paths._sheet_doc(
        sheet_type, fields, paths._next_gen(path, sheet_type),
        # A whole-sheet value write is not a creation step, and it is not an
        # undoing of one either: an edit to a created sheet leaves it created.
        creation=paths._creation_mark(path, sheet_type)))


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
            "fields": data.get("fields") if isinstance(data.get("fields"), dict) else {},
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
    with locks.campaign_lock(cid):
        # resolve INSIDE the lock: rebinds publish under this same lock, so a
        # writer can never resolve module A, lose the CPU to a rebind to B,
        # and then validate/write under A after B is visible.
        mid = modules_binding.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        path = paths._campaign_path(cid, kind, eid)
        _check_expected(path, expected)
        _checked_write(path, mid, kind, eid, sheet_type, fields)
    # Sheets live outside campaign.md and outside every scene, so the campaign's


def delete(cid: str, kind: str, eid: str, *, expected_gen: str | None) -> bool:
    """Delete a campaign sheet. ``expected_gen`` is mandatory CAS: the
    caller's last-read gen (None matches a legacy file with no gen minted
    yet). A missing file is False, never a conflict."""
    if kind not in FILE_KINDS or not safe_id(eid):
        return False
    with locks.campaign_lock(cid):
        p = paths._campaign_path(cid, kind, eid)
        stored = _stored_snapshot(p)
        if stored is None:
            return False
        if stored["gen"] != expected_gen:
            raise SheetConflict("the sheet changed since it was loaded")
        p.unlink()
    return True


def write_world(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                fields: dict | None = None, *, expected: dict | None) -> None:
    """``expected`` is mandatory whole-sheet CAS -- see write()."""
    if not safe_id(mid):
        raise SheetError(f"bad module id {mid!r}")
    modules_pack.pack_root(mid)  # raises ModuleNotFound
    path = paths._world_path(wid, mid, kind, eid)
    _check_expected(path, expected)
    _checked_write(path, mid, kind, eid, sheet_type, fields)


# ---- set_field (mechanics Phase 5, Task 5): per-field strict-CAS apply ----


def set_field_locked(mid: str, cid: str, kind: str, eid: str,
                     field_key: str, value, expect) -> None:
    """Body of set_field; caller holds locks.campaign_lock(cid) and resolved mid once."""
    if kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {kind!r}")
    if not safe_id(eid):
        raise SheetError(f"bad entity id {eid!r}")
    path = paths._campaign_path(cid, kind, eid)
    stored = _stored_snapshot(path)
    if stored is None:
        raise SheetError("no sheet exists for this entity")
    sheets_def = modules_pack.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(stored["sheet_type"]) \
        if isinstance(stored["sheet_type"], str) else None
    if not isinstance(st, dict):
        raise SheetError("sheet has no valid sheet type")
    fdefs = {f["key"]: f for f in modules_fields.assembled_fields(sheets_def, stored["sheet_type"])
             if isinstance(f, dict) and isinstance(f.get("key"), str)}
    fdef = fdefs.get(field_key)
    if fdef is None or fdef.get("type") not in MUTABLE_TYPES:
        raise SheetError(f"{field_key!r} is not a mutable field of this sheet")
    merged = {**schema.default_fields(sheets_def, stored["sheet_type"]), **stored["fields"]}
    live = merged.get(field_key)
    new = schema.canonical_field_value(fdef, value, live)
    want = schema.canonical_field_value(fdef, expect, live) if expect is not None else None
    if live != want:
        raise SheetConflict(
            f"{field_key!r} is {live!r}, expected {want!r} -- "
            "already applied or independently changed")
    new_fields = {**stored["fields"], field_key: new}
    errs = modules_validate.validate_sheet_values(sheets_def, stored["sheet_type"], new_fields)
    if errs:
        raise SheetError("; ".join(errs))
    paths._atomic_write_json(path, paths._sheet_doc(
        stored["sheet_type"], new_fields, stored["gen"],
        creation=paths._creation_mark(path, stored["sheet_type"])))


def set_field(cid: str, kind: str, eid: str, field_key: str, value, expect) -> None:
    """Per-field strict-CAS apply: raises SheetConflict when the live value
    doesn't equal the canonicalized ``expect`` -- including when it already
    equals the canonicalized ``value`` (a duplicate/independent apply must be
    reported, not silently accepted as a no-op)."""
    with locks.campaign_lock(cid):
        # resolve INSIDE the lock -- see write()'s rebind-serialization note.
        mid = modules_binding.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        set_field_locked(mid, cid, kind, eid, field_key, value, expect)
