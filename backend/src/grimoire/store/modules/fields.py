"""Sheet-type field assembly: the leaf both ``pack`` and ``display`` read.

``assembled_fields`` is pure sheets.json arithmetic and depends on nothing
else in this package, which is what lets ``display.py`` import it at module
scope instead of deferring the import back into ``modules``.
"""

from __future__ import annotations

CONTENT_KINDS = ("locations", "lore", "items", "groups", "creatures")


def numeric_names(fields: list[dict]) -> set[str]:
    """Expression-addressable names for a field list. resource contributes
    ``key`` (current) and ``key_max``; text/list are not addressable."""
    out: set[str] = set()
    if not isinstance(fields, list):
        return out
    for f in fields:
        if not isinstance(f, dict):
            continue
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        t = f.get("type")
        if t in ("number", "dots", "track"):
            out.add(key)
        elif t == "resource":
            out.add(key)
            out.add(key + "_max")
    return out


def assembled_fields(sheets: dict, type_id: str) -> list[dict]:
    """Group fields (in group order) then own fields for a sheet type."""
    sheet_types = sheets.get("sheet_types", {})
    st = sheet_types.get(type_id, {}) if isinstance(sheet_types, dict) else {}
    if not isinstance(st, dict):
        st = {}
    groups = sheets.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}
    fields: list[dict] = []
    st_groups = st.get("groups", [])
    if not isinstance(st_groups, list):
        st_groups = []
    for gid in st_groups:
        g = groups.get(gid) if isinstance(gid, str) else None
        if isinstance(g, dict):
            gfields = g.get("fields", [])
            if isinstance(gfields, list):
                fields.extend([f for f in gfields if isinstance(f, dict)])
    st_fields = st.get("fields", [])
    if isinstance(st_fields, list):
        fields.extend([f for f in st_fields if isinstance(f, dict)])
    return fields


def _pool_group_fields(group: dict) -> dict[str, dict]:
    fields = group.get("fields", []) if isinstance(group, dict) else []
    if not isinstance(fields, list):
        return {}
    return {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}
