"""Mechanics module packs (#160): loading, validation, registry, binding.

A module is a declarative data pack -- JSON + markdown, no code plugins
(deliberately unlike calendars' Python-plugin model: sharing a module never
runs untrusted code). Built-ins ship in ``builtin_modules/`` inside this
package; user modules live in ``<GRIMOIRE_HOME>/modules/``.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import dice, expressions
from .frontmatter import parse_frontmatter
from .paths import home

class ModuleError(Exception):
    """Invalid module operation (e.g. deleting a built-in)."""


class ModuleNotFound(Exception):
    pass


FIELD_TYPES = ("number", "dots", "track", "resource", "text", "list")
SHEET_KINDS = ("characters", "items", "locations", "creatures", "groups", "lore")
CONTENT_KINDS = ("locations", "lore", "items", "groups", "creatures")

DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin_modules"


def builtin_dir() -> Path:
    """Built-in packs; GRIMOIRE_MODULES overrides for non-checkout layouts
    (same pattern as prompts.templates_dir())."""
    env = os.environ.get("GRIMOIRE_MODULES")
    return Path(env) if env else DEFAULT_BUILTIN_DIR


def user_dir() -> Path:
    return home() / "modules"


def _safe_mid(mid: str) -> bool:
    return bool(mid) and mid not in (".", "..") and "/" not in mid and "\\" not in mid


def pack_root(mid: str) -> tuple[Path, str]:
    """(root, source) for a module id; user library shadows built-ins."""
    if not _safe_mid(mid):
        raise ModuleNotFound(mid)
    u = user_dir() / mid
    if (u / "module.md").exists():
        return u, "user"
    b = builtin_dir() / mid
    if (b / "module.md").exists():
        return b, "builtin"
    raise ModuleNotFound(mid)


# ---- validation helpers ----

def _validate_manifest(meta: dict, errors: list[str]) -> None:
    if not meta.get("name"):
        errors.append("module.md: manifest requires a name")
    d = meta.get("dice")
    if d:
        try:
            dice.parse(d)
        except dice.DiceError as e:
            errors.append(f"module.md: bad dice default: {e}")


def _as_list(value, where, what, errors) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{where}: {what} must be a list")
        return []
    return value


def _as_dict(value, where, what, errors) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{where}: {what} must be an object")
        return {}
    return value


def _validate_field(field: dict, where: str, errors: list[str]) -> None:
    if not isinstance(field, dict):
        errors.append(f"{where}: field must be an object")
        return
    key = field.get("key")
    if not key or not isinstance(key, str):
        errors.append(f"{where}: field missing key")
        return
    ftype = field.get("type")
    if ftype not in FIELD_TYPES:
        errors.append(f"{where}.{key}: unknown field type {ftype!r}")
        return
    if ftype in ("dots", "track", "resource"):
        m = field.get("max")
        if not isinstance(m, int) or isinstance(m, bool):
            errors.append(f"{where}.{key}: {ftype} requires an integer max")


def numeric_names(fields: list[dict]) -> set[str]:
    """Expression-addressable names for a field list. resource contributes
    ``key`` (current) and ``key_max``; text/list are not addressable."""
    out: set[str] = set()
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
    st = sheets.get("sheet_types", {}).get(type_id, {})
    fields: list[dict] = []
    for gid in st.get("groups", []):
        g = sheets.get("groups", {}).get(gid)
        if isinstance(g, dict):
            fields.extend([f for f in g.get("fields", []) if isinstance(f, dict)])
    fields.extend([f for f in st.get("fields", []) if isinstance(f, dict)])
    return fields


def _validate_derived(derived: dict, scope: set[str], where: str,
                      errors: list[str]) -> set[str]:
    """Validate a derived map against a name scope; returns derived names."""
    out: set[str] = set()
    for name, expr in derived.items():
        if name in scope:
            errors.append(f"{where}.{name}: derived name collides with a field")
            continue
        if not isinstance(expr, str):
            errors.append(f"{where}.{name}: derived expression must be a string")
            continue
        try:
            unknown = expressions.names(expr) - scope
        except expressions.ExpressionError as e:
            errors.append(f"{where}.{name}: {e}")
            continue
        if unknown:
            errors.append(f"{where}.{name}: unknown names {sorted(unknown)}")
        out.add(name)
    return out


def _validate_sheets(sheets: dict, errors: list[str]) -> None:
    groups = sheets.get("groups", {})
    for gid, group in groups.items():
        if not isinstance(group, dict):
            errors.append(f"groups.{gid}: must be an object")
            continue
        seen: set[str] = set()
        fields = _as_list(group.get("fields"), f"groups.{gid}", "fields", errors)
        for f in fields:
            _validate_field(f, f"groups.{gid}", errors)
            if not isinstance(f, dict):
                continue
            k = f.get("key")
            if not isinstance(k, str):
                continue
            if k in seen:
                errors.append(f"groups.{gid}.{k}: duplicate field key")
            seen.add(k)
        gscope = numeric_names(fields)
        derived = _as_dict(group.get("derived"), f"groups.{gid}", "derived", errors)
        _validate_derived(derived, gscope, f"groups.{gid}", errors)
    for tid, st in sheets.get("sheet_types", {}).items():
        where = f"sheet_types.{tid}"
        if not isinstance(st, dict):
            errors.append(f"{where}: must be an object")
            continue
        if st.get("kind") not in SHEET_KINDS:
            errors.append(f"{where}: unknown kind {st.get('kind')!r}")
        st_groups = _as_list(st.get("groups"), where, "groups", errors)
        for gid in st_groups:
            if gid not in groups:
                errors.append(f"{where}: unknown group ref {gid!r}")
        st_fields = _as_list(st.get("fields"), where, "fields", errors)
        for f in st_fields:
            _validate_field(f, where, errors)
        fields = assembled_fields(sheets, tid)
        keys = [f.get("key") for f in fields if isinstance(f.get("key"), str)]
        for k in {k for k in keys if keys.count(k) > 1}:
            errors.append(f"{where}.{k}: duplicate field key across groups")
        scope = numeric_names(fields)
        for gid in st_groups:
            g = groups.get(gid)
            if isinstance(g, dict) and isinstance(g.get("derived", {}), dict):
                scope |= set(g.get("derived", {}))
        st_derived = _as_dict(st.get("derived"), where, "derived", errors)
        _validate_derived(st_derived, scope, where, errors)


def load_pack(mid: str) -> dict:
    root, source = pack_root(mid)
    errors: list[str] = []
    meta, _body = parse_frontmatter((root / "module.md").read_text(encoding="utf-8"))
    _validate_manifest(meta, errors)
    sheets: dict = {"groups": {}, "sheet_types": {}}
    sp = root / "sheets.json"
    if not sp.exists():
        errors.append("sheets.json: missing")
    else:
        try:
            sheets = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"sheets.json: {e}")
            sheets = {"groups": {}, "sheet_types": {}}
        else:
            if not isinstance(sheets, dict) or not isinstance(sheets.get("groups", {}), dict) \
                    or not isinstance(sheets.get("sheet_types", {}), dict):
                errors.append("sheets.json: must be an object with 'groups' and 'sheet_types' maps")
                sheets = {"groups": {}, "sheet_types": {}}
            else:
                _validate_sheets(sheets, errors)
    pack = {
        "id": mid,
        "source": source,
        "manifest": {"id": mid, **meta},
        "sheets": sheets,
        "checks": {},
        "rules": [],
        "content": [],
        "errors": errors,
    }
    return pack
