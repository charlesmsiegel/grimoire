"""The section writers: manifest, groups, sheet types, checks, rules, content,
layout and theme. Each one hands a ``mutate`` closure to ``migrate._apply``,
which stages, validates and publishes it.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic
from ..frontmatter import dump_frontmatter
from ..modules import fields as modules_fields, pack as modules_pack
from ..paths import safe_id
from .layout import _prune_layout
from .migrate import _apply
from .packfile import _read_json, _read_sheets, _write_json
from .scope import _field_keys, _group_scope

# ---- section writers ----


def set_manifest(mid: str, *, name: str, description: str, version: str,
                 dice: str, notes: str, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        meta = {"name": name}
        if description:
            meta["description"] = description
        if version:
            meta["version"] = version
        if dice:
            meta["dice"] = dice
        atomic.write_text(root / "module.md", dump_frontmatter(meta, notes))
    return _apply(mid, mutate, dry_run=dry_run)


def upsert_group(mid: str, gid: str, group: dict, *, dry_run: bool = False) -> dict:
    live_root, _src = modules_pack.pack_root(mid)
    affected = _group_scope(_read_sheets(live_root), gid)  # BEFORE mutation (P2-3)
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["groups"].get(gid)
        data["groups"][gid] = group
        _write_json(root, "sheets.json", data)
        if isinstance(old, dict):  # prune layout refs to removed keys
            removed = _field_keys(old) - _field_keys(group if isinstance(group, dict) else {})
            if removed:
                _prune_layout(root, in_scope=_group_scope(data, gid), names=removed)
    return _apply(mid, mutate, dry_run=dry_run, impact=True, sample=True,
                 affected_types=affected)


def delete_group(mid: str, gid: str, *, dry_run: bool = False) -> dict:
    live_root, _src = modules_pack.pack_root(mid)
    affected = _group_scope(_read_sheets(live_root), gid)  # BEFORE mutation (P2-3)
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        scope = _group_scope(data, gid)
        old = data["groups"].pop(gid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope=scope, group=gid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run, impact=True, affected_types=affected)


def upsert_sheet_type(mid: str, tid: str, sheet_type: dict, *,
                      dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].get(tid)
        data["sheet_types"][tid] = sheet_type
        _write_json(root, "sheets.json", data)
        if isinstance(old, dict):
            removed = _field_keys(old) - _field_keys(
                sheet_type if isinstance(sheet_type, dict) else {})
            if removed:
                _prune_layout(root, in_scope={tid}, names=removed)
    return _apply(mid, mutate, dry_run=dry_run, impact=True, sample=True,
                 affected_types={tid})


def delete_sheet_type(mid: str, tid: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].pop(tid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope={tid}, drop_type=tid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run, impact=True, affected_types={tid})


# ---- check, rule, content writers ----


def upsert_check(mid: str, check_id: str, check: dict, *, dry_run: bool = False) -> dict:
    if not isinstance(check_id, str) or not check_id or check_id == "_defaults":
        return {"ok": False, "errors": [f"bad check id {check_id!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        data[check_id] = check
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run)


def delete_check(mid: str, check_id: str, *, dry_run: bool = False,
                 pre_swap=None) -> dict:
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        data.pop(check_id, None)
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run, pre_swap=pre_swap)


def set_check_defaults(mid: str, defaults: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        if defaults:
            data["_defaults"] = defaults
        else:
            data.pop("_defaults", None)
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run)


def _rule_meta(flags: dict) -> dict:
    meta: dict = {}
    if flags.get("always"):
        meta["always"] = "true"
    if flags.get("on_roll"):
        meta["on_roll"] = "true"
    if flags.get("keys"):
        meta["keys"] = ", ".join(flags["keys"])
    if flags.get("sheet_types"):
        meta["sheet_types"] = ", ".join(flags["sheet_types"])
    return meta


def upsert_rule(mid: str, slug: str, flags: dict, body: str, *,
                dry_run: bool = False) -> dict:
    if not modules_pack._safe_mid(slug if isinstance(slug, str) else ""):
        return {"ok": False, "errors": [f"bad rules slug {slug!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        (root / "rules").mkdir(exist_ok=True)
        atomic.write_text(root / "rules" / f"{slug}.md", dump_frontmatter(_rule_meta(flags or {}), body))
    return _apply(mid, mutate, dry_run=dry_run)


def delete_rule(mid: str, slug: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        p = root / "rules" / f"{slug}.md"
        if p.exists():
            p.unlink()
    return _apply(mid, mutate, dry_run=dry_run)


def upsert_content(mid: str, kind: str, content_id: str, *, name: str,
                   body: str, keys: str, fields: dict, sheet: dict | None,
                   dry_run: bool = False) -> dict:
    if kind not in modules_fields.CONTENT_KINDS:
        return {"ok": False, "errors": [f"unknown content kind {kind!r}"], "display_errors": []}
    if not safe_id(content_id):
        return {"ok": False, "errors": [f"bad content id {content_id!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        d = root / "content" / kind
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": name or content_id}
        if keys:
            meta["keys"] = keys
        for k, v in (fields or {}).items():
            if k not in ("name", "keys") and isinstance(v, str):
                meta[k] = v
        atomic.write_text(d / f"{content_id}.md", dump_frontmatter(meta, body))
        sidecar = d / f"{content_id}.sheet.json"
        if sheet:
            _write_json(root, f"content/{kind}/{content_id}.sheet.json",
                        {"sheet_type": sheet.get("sheet_type"),
                         "fields": sheet.get("fields", {})})
        elif sidecar.exists():
            sidecar.unlink()
    return _apply(mid, mutate, dry_run=dry_run)


def delete_content(mid: str, kind: str, content_id: str, *, dry_run: bool = False) -> dict:
    if kind not in modules_fields.CONTENT_KINDS or not safe_id(content_id):
        return {"ok": False, "errors": [f"unknown content {kind}/{content_id}"], "display_errors": []}
    def mutate(root: Path) -> None:
        d = root / "content" / kind
        for p in (d / f"{content_id}.md", d / f"{content_id}.sheet.json"):
            if p.exists():
                p.unlink()
    return _apply(mid, mutate, dry_run=dry_run, impact=True)


def set_layout(mid: str, layout: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "layout.json", layout if isinstance(layout, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)


def set_theme(mid: str, theme: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "theme.json", theme if isinstance(theme, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)
