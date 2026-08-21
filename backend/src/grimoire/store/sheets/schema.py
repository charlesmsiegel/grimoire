"""Schema-level judgment about a sheet's values: defaults, the expression
scope, derived evaluation, instance validation, and the canonical form of a
mutable field value. Nothing here touches the filesystem."""

from __future__ import annotations

from .. import expressions
from ..modules import fields as modules_fields
from ..modules import validate as modules_validate
from . import paths, pools
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


def _pool_spent(sheets_def: dict, pool_id: str, costs: dict, merged: dict) -> int:
    """What a value map spends against one creation pool, priced from its
    fields' floors -- the same arithmetic ``creation._checked_creation_write``
    charges a spend at, run over values that are already stored."""
    group_fields = pools._pool_group_fields(sheets_def, pool_id)
    spent = 0
    for field_key, cost in costs.items():
        value = merged.get(field_key)
        if (not isinstance(cost, int) or isinstance(cost, bool)
                or not isinstance(value, int) or isinstance(value, bool)):
            continue   # unjudgeable; see unspent_pools' docstring
        spent += (value - pools._pool_floor(group_fields.get(field_key, {}))) * cost
    return spent


def unspent_pools(sheets_def: dict, type_id: str, fields: dict) -> dict[str, int]:
    """Creation pools a sheet does not balance: ``{pool_id: budget - spent}``.

    A sheet type's ``creation`` block prices a set of fields against a budget,
    and ``creation.write_creation`` is the only writer that spends it. Every
    other way a sheet comes into being -- ``write`` with ``fields=None``, a
    world starting sheet copied by ``seed``, a bulk create -- writes the schema
    defaults and consults no pool at all, so the sheet is *creation-incomplete*
    the moment it exists and nothing on it says so.

    That is the gap this closes, and the reason it is computed rather than
    stored: like ``derived``, it is a judgment about the values that are there,
    so it cannot go stale behind an edit made anywhere else.

    Only pools that do not balance are listed. A positive value is points still
    to spend; a NEGATIVE one is a sheet already over its budget, which a module
    whose schema defaults sit above its pool floors produces by construction --
    reporting only the underspend would quietly call that one complete.

    Never raises. A budget expression that does not evaluate, a non-integer
    cost, a field whose stored value is not an integer: each makes its pool (or
    that one field) unjudgeable, and pack validation already reports the first
    two as pack errors. Guessing here would put a number on the screen that no
    rule produced.
    """
    st = sheets_def.get("sheet_types", {}).get(type_id)
    if not isinstance(st, dict):
        return {}
    creation = st.get("creation")
    defined = creation.get("pools") if isinstance(creation, dict) else None
    if not isinstance(defined, dict):
        return {}
    merged = {**default_fields(sheets_def, type_id), **fields}
    out: dict[str, int] = {}
    for pool_id, pool in defined.items():
        if not isinstance(pool, dict) or not isinstance(pool.get("costs"), dict):
            continue
        try:
            budget = pools._pool_budget(pool)
        except expressions.ExpressionError:
            continue
        spent = _pool_spent(sheets_def, pool_id, pool["costs"], merged)
        if budget != spent:
            out[pool_id] = budget - spent
    return out


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
