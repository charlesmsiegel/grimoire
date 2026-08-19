"""Renaming a group, field, derived value, sheet type, check, rules doc or
content entry -- one 229-line entry point plus its helper cluster.

Named ``renaming`` and not ``rename``: ``rename`` is a public function of this
package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function, leaving ``from . import rename`` bound to
the function rather than the module.

``check_proposal_guard`` lives here rather than with the other section writers
in ``edits``: ``rename`` is its only in-package caller, and leaving it there
would make this file import ``edits``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import atomic, proposals
from ..campaigns import read as campaigns_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..modules import binding as modules_binding
from ..modules import fields as modules_fields
from ..modules import pack as modules_pack
from .layout import _layout_name_edit, _specialize_layout
from .migrate import _apply, _sheet_files
from .packfile import _read_json, _read_sheets, _write_json
from .scope import _RenameCollision

_RENAME_KINDS = ("group", "field", "derived", "sheet_type", "check", "rule", "content")


def _rewrite_expr(expr: str, old: str, new: str) -> str:
    """Word-boundary text replacement. Safe: the expression language has no
    strings/attributes/comments, so \\b<old>\\b can only match a Name; the
    staged validation re-parses everything afterwards regardless."""
    return re.sub(rf"\b{re.escape(old)}\b", new, expr)


def _rewrite_exprs(expr: str, old: str, new: str, resource: bool) -> str:
    out = _rewrite_expr(expr, old, new)
    if resource:
        out = _rewrite_expr(out, f"{old}_max", f"{new}_max")
    return out


def _rewrite_placeholders(roll: str, old: str, new: str, resource: bool) -> str:
    return re.sub(r"\{([^{}]+)\}",
                  lambda m: "{" + _rewrite_exprs(m.group(1), old, new, resource) + "}",
                  roll)


def _rename_map_key(d: dict, old: str, new: str) -> None:
    """Move a map key, refusing to overwrite an existing destination (codex
    plan review: d[new] = d.pop(old) silently destroys a same-named valid
    definition and leaves nothing for staged validation to catch)."""
    if isinstance(d, dict) and old in d:
        if new in d:
            raise _RenameCollision(f"{new!r} already exists")
        d[new] = d.pop(old)


def _composing_tids(sheets_json: dict, owner: dict) -> set[str]:
    if "sheet_type" in owner:
        return {owner["sheet_type"]}
    gid = owner.get("group")
    out = set()
    for tid, st in (sheets_json.get("sheet_types") or {}).items():
        if isinstance(st, dict) and gid in (st.get("groups") or []):
            out.add(tid)
    return out


def check_proposal_guard(mid: str, check_id: str):
    """pre_swap callback: block while any campaign bound to this module has
    a non-terminal proposal referencing the check (spec: check rename row)."""
    def guard(_pack: dict) -> list[str]:
        blockers: list[str] = []
        for c in campaigns_read.list_campaigns():
            cid = c["id"]
            if modules_binding.resolve(cid) != mid:
                continue
            for sid, rec in proposals._read(cid).items():
                if not isinstance(rec, dict) or rec.get("status") not in proposals.NON_TERMINAL:
                    continue
                payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                res = rec.get("resolution") if isinstance(rec.get("resolution"), dict) else {}
                if payload.get("check") == check_id or res.get("check") == check_id:
                    blockers.append(
                        f"check {check_id!r} has a live roll proposal in campaign "
                        f"{cid!r}, scene {sid!r} — resolve or discard it first")
        return blockers
    return guard


_SAFE_KEY = re.compile(r"[a-z0-9][a-z0-9._-]*\Z", re.IGNORECASE)


def rename(mid: str, kind: str, address: dict, to: str, *,
           dry_run: bool = False) -> dict:
    if kind not in _RENAME_KINDS:
        return {"ok": False, "errors": [f"unknown rename kind {kind!r}"], "display_errors": []}
    old = address.get("from")
    # Codex plan review round 2: rule/content ids interpolate into paths —
    # a '../'-laden 'from' could move ANOTHER live module's file into
    # staging (then delete it in cleanup), and a colliding destination file
    # would be silently overwritten on POSIX. Both names must be safe keys,
    # for every kind (field/derived keys are never paths, but a uniform
    # gate is cheaper than remembering which kinds touch the filesystem).
    if not isinstance(old, str) or not _SAFE_KEY.match(old) \
            or not isinstance(to, str) or not _SAFE_KEY.match(to):
        return {"ok": False, "errors": ["rename needs safe 'from' and 'to' keys"],
                "display_errors": []}
    if old == to:
        return {"ok": False, "errors": ["'from' and 'to' are the same"], "display_errors": []}
    # source-exists + destination-free preflight per namespace: the mutate
    # step's _rename_map_key covers map-backed kinds; file-backed kinds
    # (rule, content) check here because a filesystem rename onto an
    # existing path must never happen at all.
    live_root, _src = modules_pack.pack_root(mid)
    if kind == "rule":
        if not (live_root / "rules" / f"{old}.md").exists():
            return {"ok": False, "errors": [f"unknown rules doc {old!r}"], "display_errors": []}
        if (live_root / "rules" / f"{to}.md").exists():
            return {"ok": False, "errors": [f"rules doc {to!r} already exists"], "display_errors": []}
    if kind == "content":
        ckind = address.get("kind")
        if ckind not in modules_fields.CONTENT_KINDS:
            return {"ok": False, "errors": [f"unknown content kind {ckind!r}"], "display_errors": []}
        if not (live_root / "content" / ckind / f"{old}.md").exists():
            return {"ok": False, "errors": [f"unknown content {ckind}/{old}"], "display_errors": []}
        if (live_root / "content" / ckind / f"{to}.md").exists():
            return {"ok": False, "errors": [f"content {ckind}/{to} already exists"], "display_errors": []}

    migration = None
    pre_swap = None
    if kind == "check":
        pre_swap = check_proposal_guard(mid, old)
    if kind == "field":
        owner = {k: address[k] for k in ("group", "sheet_type") if k in address}
        in_scope = _composing_tids(_read_sheets(live_root), owner)
        migration = {"op": "field", "from": old, "to": to, "owner": owner,
                     "sheet_types": sorted(in_scope)}

        def both_keys_guard(_pack: dict) -> list[str]:
            blockers = []
            for p, _cid in _sheet_files(mid):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                fields = data.get("fields") if isinstance(data, dict) \
                    and isinstance(data.get("fields"), dict) else {}
                if data.get("sheet_type") in migration["sheet_types"] \
                        and old in fields and to in fields:
                    blockers.append(
                        f"{p.name}: holds both {old!r} and {to!r} — resolve the "
                        "orphaned value first")
            return blockers
        pre_swap = both_keys_guard
    elif kind == "sheet_type":
        migration = {"op": "sheet_type", "from": old, "to": to}
    elif kind == "content":
        migration = {"op": "content", "kind": address.get("kind"), "from": old, "to": to}

    def mutate(root: Path) -> None:
        sheets_json = _read_sheets(root)
        checks_json = _read_json(root, "checks.json")
        layout_json = _read_json(root, "layout.json")

        if kind == "group":
            _rename_map_key(sheets_json.get("groups", {}), old, to)
            for st in sheets_json.get("sheet_types", {}).values():
                if isinstance(st, dict) and isinstance(st.get("groups"), list):
                    st["groups"] = [to if g == old else g for g in st["groups"]]
                creation = st.get("creation") if isinstance(st, dict) else None
                if isinstance(creation, dict) and isinstance(creation.get("pools"), dict):
                    _rename_map_key(creation["pools"], old, to)
            for check in checks_json.values():
                if isinstance(check, dict) and isinstance(check.get("requires"), list):
                    check["requires"] = [to if g == old else g for g in check["requires"]]
            if layout_json:
                all_tids = set(sheets_json.get("sheet_types", {}))
                layout_json = _specialize_layout(
                    layout_json, all_tids, _layout_name_edit(old, to, "group"))
                _write_json(root, "layout.json", layout_json)

        elif kind in ("field", "derived"):
            owner = {k: address[k] for k in ("group", "sheet_type") if k in address}
            if not owner:
                raise modules_pack.ModuleError("field/derived rename needs an owner")
            in_scope = _composing_tids(sheets_json, owner)
            groups = sheets_json.get("groups", {})
            types = sheets_json.get("sheet_types", {})
            owner_container = (groups.get(owner.get("group"))
                               if "group" in owner else types.get(owner.get("sheet_type")))
            resource = False
            if kind == "field" and isinstance(owner_container, dict):
                for f in owner_container.get("fields", []) or []:
                    if isinstance(f, dict) and f.get("key") == old:
                        resource = f.get("type") == "resource"
                        f["key"] = to
            if kind == "derived" and isinstance(owner_container, dict):
                _rename_map_key(owner_container.get("derived") or {}, old, to)
            # scope-bound expression rewrites
            if "group" in owner and isinstance(owner_container, dict):
                d = owner_container.get("derived")
                if isinstance(d, dict):
                    for name in list(d):
                        if isinstance(d[name], str):
                            d[name] = _rewrite_exprs(d[name], old, to, resource)
            for tid in in_scope:
                st = types.get(tid)
                if not isinstance(st, dict):
                    continue
                d = st.get("derived")
                if isinstance(d, dict):
                    for name in list(d):
                        if isinstance(d[name], str):
                            d[name] = _rewrite_exprs(d[name], old, to, resource)
                adv = st.get("advancement")
                if isinstance(adv, dict):
                    if adv.get("pool") == old:
                        adv["pool"] = to
                    costs = adv.get("costs")
                    if isinstance(costs, dict):
                        _rename_map_key(costs, old, to)
                        for name in list(costs):
                            if isinstance(costs[name], str):
                                costs[name] = _rewrite_exprs(costs[name], old, to, resource)
                creation = st.get("creation")
                if isinstance(creation, dict) and "group" in owner:
                    pool = (creation.get("pools") or {}).get(owner["group"])
                    if isinstance(pool, dict) and isinstance(pool.get("costs"), dict):
                        _rename_map_key(pool["costs"], old, to)
            if "group" in owner:
                gid = owner["group"]
                for check in checks_json.values():
                    if isinstance(check, dict) and gid in (check.get("requires") or []):
                        if isinstance(check.get("roll"), str):
                            check["roll"] = _rewrite_placeholders(check["roll"], old, to, resource)
            # content sidecars of composing types (pack files: staged rewrite)
            if kind == "field":
                for sc in sorted((root / "content").rglob("*.sheet.json")) \
                        if (root / "content").is_dir() else []:
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and stat.get("sheet_type") in in_scope \
                            and isinstance(stat.get("fields"), dict):
                        _rename_map_key(stat["fields"], old, to)
                        atomic.write_text(sc, json.dumps(stat, indent=2) + "\n")
            if layout_json:
                layout_json = _specialize_layout(
                    layout_json, in_scope, _layout_name_edit(old, to, "name"))
                _write_json(root, "layout.json", layout_json)

        elif kind == "sheet_type":
            _rename_map_key(sheets_json.get("sheet_types", {}), old, to)
            rd = root / "rules"
            if rd.is_dir():
                for p in sorted(rd.glob("*.md")):
                    text = p.read_text(encoding="utf-8")
                    meta, body = parse_frontmatter(text)
                    flags = [v.strip() for v in (meta.get("sheet_types") or "").split(",") if v.strip()]
                    if old in flags:
                        meta["sheet_types"] = ", ".join(to if f == old else f for f in flags)
                        atomic.write_text(p, dump_frontmatter(meta, body))
            if isinstance(layout_json.get("sheet_types"), dict):
                _rename_map_key(layout_json["sheet_types"], old, to)
                _write_json(root, "layout.json", layout_json)
            cd = root / "content"
            if cd.is_dir():
                for sc in sorted(cd.rglob("*.sheet.json")):
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and stat.get("sheet_type") == old:
                        stat["sheet_type"] = to
                        atomic.write_text(sc, json.dumps(stat, indent=2) + "\n")

        elif kind == "check":
            _rename_map_key(checks_json, old, to)

        elif kind == "rule":
            src, dst = root / "rules" / f"{old}.md", root / "rules" / f"{to}.md"
            if src.exists():
                src.rename(dst)
            for check in checks_json.values():
                if isinstance(check, dict) and isinstance(check.get("rules"), list):
                    check["rules"] = [to if r == old else r for r in check["rules"]]

        elif kind == "content":
            ckind = address.get("kind")
            d = root / "content" / str(ckind)
            if (d / f"{old}.md").exists():
                (d / f"{old}.md").rename(d / f"{to}.md")
            if (d / f"{old}.sheet.json").exists():
                (d / f"{old}.sheet.json").rename(d / f"{to}.sheet.json")
            marker, repl = f"{ckind}:module:{old}", f"{ckind}:module:{to}"
            cd = root / "content"
            if cd.is_dir():
                for sc in sorted(cd.rglob("*.sheet.json")):
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and isinstance(stat.get("fields"), dict):
                        changed = False
                        for k, v in stat["fields"].items():
                            if isinstance(v, list):
                                nv = [repl if e == marker else e for e in v]
                                if nv != v:
                                    stat["fields"][k] = nv
                                    changed = True
                        if changed:
                            atomic.write_text(sc, json.dumps(stat, indent=2) + "\n")

        _write_json(root, "sheets.json", sheets_json)
        if checks_json or (root / "checks.json").exists():
            _write_json(root, "checks.json", checks_json)

    return _apply(mid, mutate, dry_run=dry_run, migration=migration, pre_swap=pre_swap,
                  impact=True)
