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
import shutil
from pathlib import Path

from . import dice, expressions
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, slugify, uniquify

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


_MID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _safe_mid(mid: str) -> bool:
    return bool(mid) and bool(_MID_RE.fullmatch(mid))


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
    if key in expressions._FUNCS:
        errors.append(f"{where}.{key}: reserved key (expression function name)")
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


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _validate_checks(checks: dict, sheets: dict, rule_ids: set[str],
                     errors: list[str]) -> None:
    groups = sheets.get("groups", {})
    for cid, check in checks.items():
        if not isinstance(check, dict):
            errors.append(f"checks.{cid}: must be an object")
            continue
        where = f"checks.{cid}"
        if not check.get("label"):
            errors.append(f"{where}: missing label")
        scope: set[str] = set()
        requires = _as_list(check.get("requires"), where, "requires", errors)
        for gid in requires:
            if not isinstance(gid, str):
                errors.append(f"{where}: requires entries must be strings")
                continue
            g = groups.get(gid)
            if not isinstance(g, dict):
                errors.append(f"{where}: unknown required group {gid!r}")
                continue
            scope |= numeric_names(g.get("fields", []))
            derived = g.get("derived", {})
            if isinstance(derived, dict):
                scope |= set(derived)
        roll = check.get("roll", "")
        if not isinstance(roll, str):
            errors.append(f"{where}: roll must be a string")
        else:
            exprs = _PLACEHOLDER.findall(roll)
            for expr in exprs:
                try:
                    unknown = expressions.names(expr) - scope
                except expressions.ExpressionError as e:
                    errors.append(f"{where}: {e}")
                    continue
                if unknown:
                    errors.append(f"{where}: unknown names {sorted(unknown)}")
            template = _PLACEHOLDER.sub("3", roll)
            try:
                dice.parse(template)
            except dice.DiceError as e:
                errors.append(f"{where}: roll is not dice notation: {e}")
        rule_refs = _as_list(check.get("rules"), where, "rules", errors)
        for rid in rule_refs:
            if not isinstance(rid, str):
                errors.append(f"{where}: rules entries must be strings")
                continue
            if rid not in rule_ids:
                errors.append(f"{where}: unknown rules doc {rid!r}")
        if "outcomes" in check and not isinstance(check["outcomes"], list):
            errors.append(f"{where}: outcomes must be a list")


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _load_rules(root: Path, sheets: dict, errors: list[str]) -> list[dict]:
    out: list[dict] = []
    rd = root / "rules"
    if not rd.is_dir():
        return out
    type_ids = set(sheets.get("sheet_types", {}))
    for p in sorted(rd.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            errors.append(f"rules/{p.stem}: {e}")
            continue
        meta, _ = parse_frontmatter(text)
        doc = {
            "id": p.stem,
            "keys": _split_csv(meta.get("keys", "")),
            "always": meta.get("always", "") == "true",
            "on_roll": meta.get("on_roll", "") == "true",
            "sheet_types": _split_csv(meta.get("sheet_types", "")),
        }
        for t in doc["sheet_types"]:
            if t not in type_ids:
                errors.append(f"rules/{p.stem}: unknown sheet type {t!r}")
        out.append(doc)
    return out


def _load_content(root: Path, sheets: dict, errors: list[str]) -> list[dict]:
    out: list[dict] = []
    cd = root / "content"
    if not cd.is_dir():
        return out
    type_defs = sheets.get("sheet_types", {})
    for kind_dir in sorted(p for p in cd.iterdir() if p.is_dir()):
        kind = kind_dir.name
        if kind not in CONTENT_KINDS:
            errors.append(f"content/{kind}: unknown kind")
            continue
        for p in sorted(kind_dir.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as e:
                errors.append(f"content/{kind}/{p.stem}: {e}")
                continue
            meta, _ = parse_frontmatter(text)
            entry = {"kind": kind, "id": p.stem,
                     "name": meta.get("name", p.stem), "sheet_type": None}
            sidecar = kind_dir / f"{p.stem}.sheet.json"
            if sidecar.exists():
                where = f"content/{kind}/{p.stem}.sheet.json"
                try:
                    stat = json.loads(sidecar.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                    errors.append(f"{where}: {e}")
                    stat = {}
                else:
                    if not isinstance(stat, dict):
                        errors.append(f"{where}: must be an object")
                        stat = {}
                tid = stat.get("sheet_type")
                td = type_defs.get(tid) if isinstance(tid, str) else None
                if not isinstance(td, dict):
                    errors.append(f"{where}: unknown sheet type {tid!r}")
                elif td.get("kind") != kind:
                    errors.append(
                        f"{where}: sheet type {tid!r} targets kind "
                        f"{td.get('kind')!r}, not {kind!r}")
                else:
                    entry["sheet_type"] = tid
                    for e in validate_sheet_values(sheets, tid,
                                                   stat.get("fields", {})):
                        errors.append(f"{where}: {e}")
            out.append(entry)
    return out


def validate_sheet_values(sheets: dict, type_id: str, values: dict) -> list[str]:
    """Validate a sheet's field-value map against a sheet type. Reused by
    campaign sheets in Phase 3."""
    if not isinstance(values, dict):
        return ["fields must be an object"]
    errors: list[str] = []
    fields = {
        f["key"]: f
        for f in assembled_fields(sheets, type_id)
        if isinstance(f.get("key"), str)
    }
    for key, value in values.items():
        f = fields.get(key)
        if f is None:
            errors.append(f"{key}: not a field of sheet type {type_id!r}")
            continue
        t = f.get("type")
        if t == "resource":
            if (not isinstance(value, dict)
                    or not isinstance(value.get("current"), int) or isinstance(value.get("current"), bool)
                    or not isinstance(value.get("max"), int) or isinstance(value.get("max"), bool)):
                errors.append(f"{key}: resource needs a current/max pair")
        elif t in ("number", "dots", "track"):
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{key}: expected an integer")
            else:
                fmax, fmin = f.get("max"), f.get("min")
                fmax_ok = isinstance(fmax, int) and not isinstance(fmax, bool)
                fmin_ok = isinstance(fmin, int) and not isinstance(fmin, bool)
                if t in ("dots", "track"):
                    if not fmax_ok or not 0 <= value <= fmax:
                        errors.append(f"{key}: outside 0..max")
                elif t == "number" and (
                        (fmin_ok and value < fmin)
                        or (fmax_ok and value > fmax)):
                    errors.append(f"{key}: outside min/max")
        elif t == "text":
            if not isinstance(value, str):
                errors.append(f"{key}: expected a string")
        elif t == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{key}: expected a list of strings")
    return errors


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
            if not isinstance(gid, str):
                errors.append(f"{where}: group ref must be a string")
            elif gid not in groups:
                errors.append(f"{where}: unknown group ref {gid!r}")
        st_fields = _as_list(st.get("fields"), where, "fields", errors)
        for f in st_fields:
            _validate_field(f, where, errors)
        fields = assembled_fields(sheets, tid)
        resource_max = {
            f["key"] + "_max"
            for f in fields
            if isinstance(f, dict) and isinstance(f.get("key"), str)
            and f.get("type") == "resource"
        }
        for f in fields:
            if not isinstance(f, dict):
                continue
            k = f.get("key")
            if isinstance(k, str) and k in resource_max:
                errors.append(
                    f"{where}.{k}: collides with a resource field's implicit _max name")
        keys = [f.get("key") for f in fields if isinstance(f.get("key"), str)]
        for k in {k for k in keys if keys.count(k) > 1}:
            errors.append(f"{where}.{k}: duplicate field key across groups")
        scope = numeric_names(fields)
        for gid in st_groups:
            g = groups.get(gid) if isinstance(gid, str) else None
            if isinstance(g, dict) and isinstance(g.get("derived", {}), dict):
                scope |= set(g.get("derived", {}))
        st_derived = _as_dict(st.get("derived"), where, "derived", errors)
        _validate_derived(st_derived, scope, where, errors)


def load_pack(mid: str) -> dict:
    root, source = pack_root(mid)
    errors: list[str] = []
    try:
        module_text = (root / "module.md").read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        errors.append(f"module.md: {e}")
        meta, _body = {}, ""
    else:
        meta, _body = parse_frontmatter(module_text)
    _validate_manifest(meta, errors)
    sheets: dict = {"groups": {}, "sheet_types": {}}
    sp = root / "sheets.json"
    if not sp.exists():
        errors.append("sheets.json: missing")
    else:
        try:
            sheets = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            errors.append(f"sheets.json: {e}")
            sheets = {"groups": {}, "sheet_types": {}}
        else:
            if not isinstance(sheets, dict) or not isinstance(sheets.get("groups", {}), dict) \
                    or not isinstance(sheets.get("sheet_types", {}), dict):
                errors.append("sheets.json: must be an object with 'groups' and 'sheet_types' maps")
                sheets = {"groups": {}, "sheet_types": {}}
            else:
                _validate_sheets(sheets, errors)
    rules = _load_rules(root, sheets, errors)
    checks: dict = {}
    cp = root / "checks.json"
    if cp.exists():
        try:
            checks = json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            errors.append(f"checks.json: {e}")
            checks = {}
        else:
            if not isinstance(checks, dict):
                errors.append("checks.json: must be an object of check definitions")
                checks = {}
            else:
                _validate_checks(checks, sheets, {r["id"] for r in rules}, errors)
    content = _load_content(root, sheets, errors)
    pack = {
        "id": mid,
        "source": source,
        "manifest": {**meta, "id": mid},
        "sheets": sheets,
        "checks": checks,
        "rules": rules,
        "content": content,
        "errors": errors,
    }
    return pack


# ---- registry: list, scaffold, delete ----


def _scan(d: Path) -> dict[str, dict]:
    """Scan a directory for module packs and return metadata dict by id."""
    out: dict[str, dict] = {}
    if not d.is_dir():
        return out
    for p in sorted(q for q in d.iterdir() if (q / "module.md").exists() and _safe_mid(q.name)):
        pack = load_pack(p.name)
        m = pack["manifest"]
        out[p.name] = {
            "id": p.name,
            "name": m.get("name", p.name),
            "description": m.get("description", ""),
            "version": m.get("version", ""),
            "source": pack["source"],
            "valid": not pack["errors"],
        }
    return out


def list_modules() -> list[dict]:
    """List all modules (builtin + user), with user shadowing builtin, sorted by name."""
    merged = _scan(builtin_dir())
    merged.update(_scan(user_dir()))
    return sorted(merged.values(), key=lambda m: str(m["name"]).lower())


def create_module(name: str) -> str:
    """Scaffold a minimal valid module pack in user_dir(), return its id."""
    # Normalize: collapse newlines/whitespace, then default to "Untitled"
    name = " ".join(name.split())
    name = name or "Untitled"
    mid = uniquify(slugify(name), lambda i: i == "none" or (user_dir() / i).exists()
                   or (builtin_dir() / i / "module.md").exists())
    d = user_dir() / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text(
        dump_frontmatter({"name": name, "description": "", "version": "0.1"}, ""),
        encoding="utf-8")
    (d / "sheets.json").write_text(
        '{\n  "groups": {},\n  "sheet_types": {}\n}\n', encoding="utf-8")
    return mid


def delete_module(mid: str) -> None:
    """Delete a user module. Raises ModuleError if builtin, ModuleNotFound if absent."""
    root, source = pack_root(mid)
    if source != "user":
        raise ModuleError("built-in modules cannot be deleted")
    shutil.rmtree(root)


# ---- binding: world/campaign module: keys + resolve() ----


def _write_key(meta_path, key: str, value: str) -> None:
    text = meta_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if value:
        meta[key] = value
    else:
        meta.pop(key, None)
    meta_path.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def set_world_module(wid: str, mid: str) -> None:
    from . import worlds
    worlds.read_world(wid)  # raises WorldNotFound
    if mid == "none":
        raise ModuleError("'none' is reserved")
    if mid:
        pack_root(mid)  # raises ModuleNotFound
    _write_key(worlds.world_meta_path(wid), "module", mid)


def set_campaign_module(cid: str, value: str) -> None:
    """value: "" -> inherit world default, "none" -> mechanics off, else mid."""
    from . import campaigns
    campaigns.read_campaign(cid)  # raises CampaignNotFound
    if value and value != "none":
        pack_root(value)
    _write_key(campaigns.campaign_meta_path(cid), "module", value)


def resolve(cid: str) -> str | None:
    """The module id governing a campaign, or None (= zero mechanics).
    Campaign tri-state ("", "none", mid) over world default; a binding to a
    missing or invalid module falls through to None."""
    from . import campaigns, worlds
    meta = campaigns.read_campaign(cid)["meta"]
    setting = (meta.get("module") or "").strip()
    if setting == "none":
        return None
    mid = setting
    if not mid:
        try:
            wmeta = worlds.read_world(meta.get("world", ""))["meta"]
        except worlds.WorldNotFound:
            return None
        mid = (wmeta.get("module") or "").strip()
    if not mid:
        return None
    try:
        pack = load_pack(mid)
    except ModuleNotFound:
        return None
    return None if pack["errors"] else mid
