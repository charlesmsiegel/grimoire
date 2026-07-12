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
import re
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


def _validate_field(field: dict, where: str, errors: list[str]) -> None:
    key = field.get("key")
    if not key or not isinstance(key, str):
        errors.append(f"{where}: field missing key")
        return
    ftype = field.get("type")
    if ftype not in FIELD_TYPES:
        errors.append(f"{where}.{key}: unknown field type {ftype!r}")
        return
    if ftype in ("dots", "track", "resource") and not isinstance(field.get("max"), int):
        errors.append(f"{where}.{key}: {ftype} requires an integer max")


def numeric_names(fields: list[dict]) -> set[str]:
    """Expression-addressable names for a field list. resource contributes
    ``key`` (current) and ``key_max``; text/list are not addressable."""
    out: set[str] = set()
    for f in fields:
        t = f.get("type")
        if t in ("number", "dots", "track"):
            out.add(f["key"])
        elif t == "resource":
            out.add(f["key"])
            out.add(f["key"] + "_max")
    return out


def assembled_fields(sheets: dict, type_id: str) -> list[dict]:
    """Group fields (in group order) then own fields for a sheet type."""
    st = sheets.get("sheet_types", {}).get(type_id, {})
    fields: list[dict] = []
    for gid in st.get("groups", []):
        fields.extend(sheets.get("groups", {}).get(gid, {}).get("fields", []))
    fields.extend(st.get("fields", []))
    return fields


def _validate_derived(derived: dict, scope: set[str], where: str,
                      errors: list[str]) -> set[str]:
    """Validate a derived map against a name scope; returns derived names."""
    out: set[str] = set()
    for name, expr in derived.items():
        if name in scope:
            errors.append(f"{where}.{name}: derived name collides with a field")
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
        seen: set[str] = set()
        for f in group.get("fields", []):
            _validate_field(f, f"groups.{gid}", errors)
            k = f.get("key")
            if k in seen:
                errors.append(f"groups.{gid}.{k}: duplicate field key")
            seen.add(k)
        gscope = numeric_names(group.get("fields", []))
        _validate_derived(group.get("derived", {}), gscope, f"groups.{gid}", errors)
    for tid, st in sheets.get("sheet_types", {}).items():
        where = f"sheet_types.{tid}"
        if st.get("kind") not in SHEET_KINDS:
            errors.append(f"{where}: unknown kind {st.get('kind')!r}")
        for gid in st.get("groups", []):
            if gid not in groups:
                errors.append(f"{where}: unknown group ref {gid!r}")
        for f in st.get("fields", []):
            _validate_field(f, where, errors)
        fields = assembled_fields(sheets, tid)
        keys = [f.get("key") for f in fields]
        for k in {k for k in keys if keys.count(k) > 1}:
            errors.append(f"{where}.{k}: duplicate field key across groups")
        scope = numeric_names(fields)
        for gid in st.get("groups", []):
            scope |= set(groups.get(gid, {}).get("derived", {}))
        _validate_derived(st.get("derived", {}), scope, where, errors)


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
