"""The section writers: manifest, groups, sheet types, checks, rules, content,
layout and theme. Each one hands a ``mutate`` closure to ``migrate._apply``,
which stages, validates and publishes it.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic
from ..frontmatter import dump_frontmatter
from ..modules import fields as modules_fields
from ..modules import pack as modules_pack
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


def _provided_names(data: dict, st: dict) -> set[str]:
    """Every name a sheet type's layout may reference: its own fields and
    derived, plus those of each group it composes."""
    names = _field_keys(st)
    groups = data.get("groups", {})
    for gid in st.get("groups") or []:
        g = groups.get(gid) if isinstance(gid, str) and isinstance(groups, dict) else None
        if isinstance(g, dict):
            names |= _field_keys(g)
    return names


def upsert_sheet_type(mid: str, tid: str, sheet_type: dict, *,
                      dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].get(tid)
        data["sheet_types"][tid] = sheet_type
        _write_json(root, "sheets.json", data)
        if isinstance(old, dict):
            new = sheet_type if isinstance(sheet_type, dict) else {}
            # Names are judged over the ASSEMBLED set, not the type's own
            # fields: dropping a group from `groups` takes that group's names
            # out of this type's layout scope just as deleting an own field
            # does, and the `{"group": gid}` node itself goes with them. Both
            # are scoped to this type -- another type still composing the
            # group keeps its node (#227 T3).
            removed = _provided_names(data, old) - _provided_names(data, new)
            dropped = {g for g in (old.get("groups") or []) if isinstance(g, str)} \
                - {g for g in (new.get("groups") or []) if isinstance(g, str)}
            if removed or dropped:
                _prune_layout(root, in_scope={tid}, names=removed, groups=dropped)
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
    # Frontmatter carries string scalars only (`dump_frontmatter` would
    # stringify anything else and the reader would hand back that spelling),
    # so a non-string value cannot be stored faithfully. Refused rather than
    # dropped: the payload came from a form or a script, and a save that
    # reports success while losing a field is the worse of the two answers.
    bad = sorted(k for k, v in (fields or {}).items()
                 if k not in ("name", "keys") and not isinstance(v, str))
    if bad:
        return {"ok": False, "display_errors": [],
                "errors": [f"metadata value for {k!r} must be a string" for k in bad]}
    def mutate(root: Path) -> None:
        d = root / "content" / kind
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": name or content_id}
        if keys:
            meta["keys"] = keys
        for k, v in (fields or {}).items():
            if k not in ("name", "keys"):
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
