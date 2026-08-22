"""Schema-level judgment about a sheet's values: defaults, the expression
scope, derived evaluation, instance validation, and the canonical form of a
mutable field value. Nothing here touches the filesystem."""

from __future__ import annotations

from .. import expressions
from ..modules import fields as modules_fields
from ..modules import validate as modules_validate
from . import paths
from .paths import SheetError


def _int_or(value, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def default_fields(sheets_def: dict, type_id: str) -> dict:
    """Schema-default value map for a sheet type (spec: Decisions table)."""
    out: dict = {}
    for f in modules_fields.assembled_fields(sheets_def, type_id):
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
    for f in modules_fields.assembled_fields(sheets_def, type_id):
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
    if st.get("kind") != paths.sheet_kind(file_kind):
        return [f"sheet type {sheet_type!r} targets kind {st.get('kind')!r}, "
                f"not {paths.sheet_kind(file_kind)!r}"]
    return modules_validate.validate_sheet_values(sheets_def, sheet_type, fields)


def creation_pending(sheets_def: dict, type_id: str, fields: dict) -> list[str]:
    """The creation pools a sheet has never been through, or ``[]``.

    A sheet type's ``creation`` block prices a set of fields against a budget,
    and ``creation.write_creation`` is the only writer that spends it. Every
    other way a sheet comes into being -- ``write`` with ``fields=None``, a
    world starting sheet copied by ``seed``, a bulk create -- writes the schema
    defaults and consults no pool at all. That sheet is creation-incomplete the
    moment it exists, and #201 turns on saying so rather than skipping it.

    The test is "the values are still exactly this type's schema defaults",
    which is a statement about the state the sheet was CREATED in -- exactly
    what the issue asks the view to flag -- and it is what keeps this honest.

    The obvious implementation, and the one this replaces, was arithmetic:
    price the stored values against the pools and report ``budget - spent``.
    That question has no answer for a sheet anyone has touched, and it produced
    confident wrong ones. Two, both against shipped builtin modules:

    - ``pool-basic``'s ``medium`` advanced one dot of ``wits`` through
      ``advancement.advance`` -- a rules-legal raise bought with experience,
      not creation points -- read as ``attributes: -2``, permanently, and the
      roster badged that valid sheet as over budget.
    - ``d20-basic``'s ``adept`` prices attributes from ``min: 1`` while the
      schema defaults them to 10, so a *correctly* bulk-created sheet came out
      at ``attributes: -48`` and the bulk create's own success report announced
      each sheet it had just written as 48 over budget.

    Both are the same mistake: current values are not a record of what was
    spent at creation, and no arithmetic over them can recover one. So the
    answer is not a number. It is the names of the pools nobody ran, for a
    sheet that is still untouched, and nothing at all for a sheet that is not.

    The error this can still make is the safe one -- a sheet edited once by
    hand, but never taken through creation, stops being flagged. Silence about
    a sheet somebody has worked on beats crying wolf over every valid one.
    """
    st = sheets_def.get("sheet_types", {}).get(type_id)
    if not isinstance(st, dict):
        return []
    creation = st.get("creation")
    defined = creation.get("pools") if isinstance(creation, dict) else None
    if not isinstance(defined, dict) or not defined:
        return []
    if fields != default_fields(sheets_def, type_id):
        return []
    return sorted(pid for pid in defined if isinstance(pid, str))


def instance_errors(pack: dict, file_kind: str, sheet_type, fields: dict) -> list[str]:
    """The full read-time judgment for a stored sheet against an arbitrary
    pack dict — sheet-type/kind/value validation PLUS derived evaluation
    against the stored values (impact scans must judge exactly as reads do)."""
    sheets_def = pack["sheets"] if isinstance(pack.get("sheets"), dict) else {}
    errors = _validate_instance(sheets_def, file_kind, sheet_type, fields)
    if isinstance(sheet_type, str):
        _compute_derived(sheets_def, sheet_type, fields, errors)
    return errors


# ---- set_field (mechanics Phase 5, Task 5): the schema half of the per-field
# strict-CAS apply, whose lock/IO half lives in writer.py ----

MUTABLE_TYPES = ("resource", "track", "list")


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
