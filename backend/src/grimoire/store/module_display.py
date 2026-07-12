"""Phase 6 display files: ``layout.json`` + ``theme.json`` validation and
fragment splicing.

Cosmetic-only by design: every problem lands in the structured
``display_errors`` list, never in ``pack["errors"]`` -- ``resolve()``
refuses packs with ``errors``, and a display typo must never switch off
mechanics for campaigns bound to the module. Same never-raise posture as
``modules.load_pack``.

Spec: docs/superpowers/specs/2026-07-12-mechanics-phase6-sheet-display-design.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

NODE_FORMS = ("row", "column", "group", "fields", "derived", "use")
MAX_DEPTH = 32
MAX_NODES = 1000

COLOR_KEYS = ("bg", "ink", "muted", "accent", "rule")
FONT_KEYS = ("display", "body")
FONT_STACKS = ("display", "body", "mono", "serif", "sans")
DOT_SHAPES = ("circle", "square", "diamond")
CORNER_STYLES = ("sharp", "rounded")
_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")


def _entry(source: str, sheet_type: str | None, message: str) -> dict:
    return {"source": source, "sheet_type": sheet_type, "message": message}


def _read_json(root: Path, name: str, source: str, errors: list[dict]):
    p = root / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
        # RecursionError: pathologically deep JSON blows the parser stack
        # before our own depth cap can see the tree.
        errors.append(_entry(source, None, f"{name}: {e.__class__.__name__}: {e}"))
        return None


class _LayoutError(Exception):
    """Internal: aborts one tree's expansion; caught in load_display."""


def _type_scope(sheets: dict, tid: str) -> dict:
    """Names a layout may reference for a sheet type: its group ids (with
    their field keys, for duplicate detection), assembled field keys, and
    reachable derived names."""
    from .modules import assembled_fields  # deferred: modules imports us

    st = sheets.get("sheet_types", {}).get(tid)
    if not isinstance(st, dict):
        st = {}
    groups = sheets.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}
    gids = [g for g in (st.get("groups") or []) if isinstance(g, str)]
    group_fields: dict[str, list[str]] = {}
    derived: set[str] = set()
    for gid in gids:
        g = groups.get(gid)
        if not isinstance(g, dict):
            continue
        gf = g.get("fields") if isinstance(g.get("fields"), list) else []
        group_fields[gid] = [f["key"] for f in gf
                             if isinstance(f, dict) and isinstance(f.get("key"), str)]
        if isinstance(g.get("derived"), dict):
            derived |= set(g["derived"])
    if isinstance(st.get("derived"), dict):
        derived |= set(st["derived"])
    fields = {f["key"] for f in assembled_fields(sheets, tid)
              if isinstance(f.get("key"), str)}
    return {"group_fields": group_fields, "fields": fields, "derived": derived}


class _Expander:
    """Validates and splices one tree. ``scope=None`` = structural pass only
    (used for standalone fragment checks; no ref/duplicate checks)."""

    def __init__(self, fragments: dict, scope: dict | None,
                 bad_fragments: set[str] | None = None):
        self.fragments = fragments
        self.scope = scope
        self.bad_fragments = bad_fragments or set()
        self.placed_fields: set[str] = set()
        self.placed_derived: set[str] = set()
        self.nodes = 0
        self.stack: list[str] = []

    def _str_list(self, value, where: str) -> list[str]:
        if not isinstance(value, list) or not all(
                isinstance(v, str) and v for v in value):
            raise _LayoutError(f"{where}: must be an array of names")
        return value

    def _place_fields(self, keys: list[str], where: str) -> None:
        for k in keys:
            if k in self.placed_fields:
                raise _LayoutError(f"{where}: field {k!r} placed more than once")
            self.placed_fields.add(k)

    def expand(self, node, path: str, depth: int) -> dict:
        if depth > MAX_DEPTH:
            raise _LayoutError(f"{path}: exceeds depth cap {MAX_DEPTH}")
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _LayoutError(f"{path}: exceeds node cap {MAX_NODES}")
        if not isinstance(node, dict):
            raise _LayoutError(f"{path}: node must be an object")
        forms = [k for k in NODE_FORMS if k in node]
        if len(forms) != 1:
            raise _LayoutError(
                f"{path}: node needs exactly one of {', '.join(NODE_FORMS)}")
        form = forms[0]
        extras = set(node) - {form, "title", "grid"}
        if extras:
            raise _LayoutError(f"{path}: unknown keys {sorted(extras)}")
        if "title" in node and not isinstance(node["title"], str):
            raise _LayoutError(f"{path}: title must be a string")
        if "grid" in node:
            if form not in ("group", "fields"):
                raise _LayoutError(f"{path}: grid only applies to group/fields")
            if not isinstance(node["grid"], bool):
                raise _LayoutError(f"{path}: grid must be a boolean")
        value = node[form]
        if form in ("row", "column"):
            if not isinstance(value, list):
                raise _LayoutError(f"{path}.{form}: must be an array of nodes")
            out: dict = {form: [self.expand(c, f"{path}.{form}[{i}]", depth + 1)
                                for i, c in enumerate(value)]}
        elif form == "use":
            if not isinstance(value, str) or not value:
                raise _LayoutError(f"{path}.use: must be a fragment id")
            if value in self.bad_fragments:
                raise _LayoutError(f"{path}.use: fragment {value!r} is invalid")
            if value in self.stack:
                raise _LayoutError(f"{path}.use: fragment cycle through {value!r}")
            frag = self.fragments.get(value)
            if frag is None:
                raise _LayoutError(f"{path}.use: unknown fragment {value!r}")
            self.stack.append(value)
            out = self.expand(frag, f"{path}.use({value})", depth + 1)
            self.stack.pop()
        elif form == "group":
            if not isinstance(value, str) or not value:
                raise _LayoutError(f"{path}.group: must be a group id")
            if self.scope is not None:
                if value not in self.scope["group_fields"]:
                    raise _LayoutError(
                        f"{path}.group: {value!r} is not one of this sheet type's groups")
                self._place_fields(self.scope["group_fields"][value], f"{path}.group")
            out = {form: value}
        elif form == "fields":
            keys = self._str_list(value, f"{path}.fields")
            if self.scope is not None:
                unknown = [k for k in keys if k not in self.scope["fields"]]
                if unknown:
                    raise _LayoutError(f"{path}.fields: unknown keys {unknown}")
                self._place_fields(keys, f"{path}.fields")
            out = {form: keys}
        else:  # derived
            names = self._str_list(value, f"{path}.derived")
            if self.scope is not None:
                unknown = [n for n in names if n not in self.scope["derived"]]
                if unknown:
                    raise _LayoutError(f"{path}.derived: unknown names {unknown}")
                for n in names:
                    if n in self.placed_derived:
                        raise _LayoutError(
                            f"{path}.derived: {n!r} placed more than once")
                    self.placed_derived.add(n)
            out = {form: names}
        if "title" in node:
            out["title"] = node["title"]
        if "grid" in node:
            out["grid"] = node["grid"]
        return out


def _load_layout(root: Path, sheets: dict, errors: list[dict]) -> dict:
    layout: dict = {"sheet_types": {}}
    raw = _read_json(root, "layout.json", "layout", errors)
    if raw is None:
        return layout
    if not isinstance(raw, dict):
        errors.append(_entry("layout", None, "layout.json: must be an object"))
        return layout
    extras = set(raw) - {"fragments", "sheet_types"}
    if extras:
        errors.append(_entry("layout", None,
                             f"layout.json: unknown keys {sorted(extras)}"))
    fragments = raw.get("fragments", {})
    if not isinstance(fragments, dict):
        errors.append(_entry("layout", None, "layout.json: fragments must be an object"))
        fragments = {}
    trees = raw.get("sheet_types", {})
    if not isinstance(trees, dict):
        errors.append(_entry("layout", None, "layout.json: sheet_types must be an object"))
        trees = {}
    # Structural pass over every fragment: an unused-but-broken fragment is
    # reported once (sheet_type None) and drops nothing by itself.
    bad_fragments: set[str] = set()
    for fid, frag in fragments.items():
        ex = _Expander(fragments, None)
        ex.stack.append(str(fid))
        try:
            ex.expand(frag, f"fragments.{fid}", 0)
        except _LayoutError as e:
            bad_fragments.add(fid)
            errors.append(_entry("layout", None, str(e)))
    known = sheets.get("sheet_types", {})
    if not isinstance(known, dict):
        known = {}
    for tid, tree in trees.items():
        if tid not in known:
            errors.append(_entry("layout", None,
                                 f"sheet_types.{tid}: unknown sheet type"))
            continue
        ex = _Expander(fragments, _type_scope(sheets, tid), bad_fragments)
        try:
            layout["sheet_types"][tid] = ex.expand(tree, f"sheet_types.{tid}", 0)
        except _LayoutError as e:
            errors.append(_entry("layout", tid, str(e)))
    return layout


def load_display(root: Path, sheets: dict) -> tuple[dict, dict, list[dict]]:
    """(layout, theme, display_errors) for a pack root. Never raises."""
    errors: list[dict] = []
    layout = _load_layout(root, sheets, errors)
    theme: dict = {}  # Task 2
    return layout, theme, errors
