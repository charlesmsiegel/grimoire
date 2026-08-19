"""Pack location and loading: where a module id resolves to on disk, and the
never-raising ``load_pack`` that turns a pack root into a validated dict.

``_load_rules`` and ``_load_content`` live here rather than beside
``read_content``: ``load_pack_at`` calls them, while ``content.read_content``
calls ``pack_root`` -- the other placement would put ``pack`` and ``content``
in a cycle.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..frontmatter import parse_frontmatter
from ..paths import home, safe_id
from . import display
from .fields import CONTENT_KINDS
from .validate import (
    _validate_checks,
    _validate_manifest,
    _validate_outcomes,
    _validate_sheets,
    validate_sheet_values,
)


class ModuleError(Exception):
    """Invalid module operation (e.g. deleting a built-in)."""


class ModuleNotFound(Exception):
    pass


class ContentNotFound(Exception):
    pass


# .parent.parent, not .parent: this file sits one level deeper than the
# `modules.py` it was split out of, and `builtin_modules/` stays next to the
# package.
DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "builtin_modules"  # paths-ok: package-relative, so the built-in packs ship inside the wheel


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
            if not safe_id(p.stem):
                # read_content would refuse this id, so listing it would
                # advertise content whose detail route 404s (#259 review).
                # Reported rather than silently dropped: unlike a user store,
                # a module pack is authored, so an unusable id is an error in it.
                errors.append(f"content/{kind}/{p.stem}: unusable id")
                continue
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


def load_pack(mid: str) -> dict:
    root, source = pack_root(mid)
    return load_pack_at(root, mid, source)


def load_pack_at(root: Path, mid: str, source: str = "user") -> dict:
    """load_pack against an explicit root — the staging validator uses this
    so a staged edit is judged by the identical code path resolve() trusts."""
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
                defaults = checks.get("_defaults")
                if defaults is not None:
                    if not isinstance(defaults, dict):
                        errors.append("checks.json: _defaults must be an object")
                    else:
                        d = defaults.get("difficulty")
                        if d is not None and (not isinstance(d, int) or isinstance(d, bool)):
                            errors.append("checks.json: _defaults.difficulty must be an integer")
                        if "outcomes" in defaults:
                            _validate_outcomes(defaults["outcomes"], "checks.json: _defaults", errors)
                _validate_checks(checks, sheets, {r["id"] for r in rules}, errors)
    content = _load_content(root, sheets, errors)
    layout, theme, display_errors = display.load_display(root, sheets)
    layout_source: dict = {}
    lp = root / "layout.json"
    if lp.exists():
        try:
            raw = json.loads(lp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError):
            raw = {}
        if isinstance(raw, dict):
            layout_source = raw
    pack = {
        "id": mid,
        "source": source,
        "manifest": {**meta, "id": mid, "notes": _body},
        "sheets": sheets,
        "checks": checks,
        "rules": rules,
        "content": content,
        "layout": layout,
        "layout_source": layout_source,
        "theme": theme,
        "display_errors": display_errors,
        "errors": errors,
    }
    return pack


# ---- registry: list ----


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
            "display_ok": not pack["display_errors"],
        }
    return out


def list_modules() -> list[dict]:
    """List all modules (builtin + user), with user shadowing builtin, sorted by name."""
    merged = _scan(builtin_dir())
    merged.update(_scan(user_dir()))
    return sorted(merged.values(), key=lambda m: str(m["name"]).lower())
