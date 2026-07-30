"""Creation-pool arithmetic: a costed field's floor, the fields a pool's group
contributes, and the pool's budget.

``_pool_group_fields`` is deliberately distinct from ``modules.fields``'
same-named private: that one assembles a sheet type's fields, this one reads a
single group's field list for pool costing.
"""

from __future__ import annotations

from .. import expressions


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
