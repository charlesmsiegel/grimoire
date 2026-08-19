"""Advancement (#164, Phase 7): single resource pool, formula-priced raises.

Named ``advancement`` and not ``advance``: ``advance`` is a public function of
this package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function.
"""

from __future__ import annotations

import json

from .. import expressions, locks
from ..modules import binding as modules_binding
from ..modules import fields as modules_fields
from ..modules import pack as modules_pack
from . import creation, paths, reader, schema
from .paths import SheetError


def _advancement_cost(sheets_def: dict, type_id: str, field_key: str,
                      fields: dict, new: int) -> int:
    """Evaluate an advancement cost against a tentative post-raise scope:
    the raised field is set to `new` before recomputing derived values, so a
    cost formula referencing a derived name sees the post-raise state."""
    tentative = {**fields, field_key: new}
    scope = schema._numeric_scope(sheets_def, type_id, tentative)
    derived_errors: list[str] = []
    derived = schema._compute_derived(sheets_def, type_id, tentative, derived_errors)
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
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        creation._assert_campaign_entity_exists(cid, kind, eid)
        path = paths._campaign_path(cid, kind, eid)
        if not path.exists():
            raise SheetError("no sheet exists for this entity")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            raise SheetError(f"unreadable sheet file: {e}")
        sheet_type = data.get("sheet_type") if isinstance(data, dict) else None
        fields = data.get("fields") if isinstance(data, dict) and isinstance(data.get("fields"), dict) else {}
        sheets_def = modules_pack.load_pack(mid)["sheets"]
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
        field_defs = {f["key"]: f for f in modules_fields.assembled_fields(sheets_def, sheet_type)
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
        paths._atomic_write_json(path, {"sheet_type": sheet_type, "fields": new_fields,
                                        "gen": data.get("gen")})
        return reader._read_path(path, kind, mid)
