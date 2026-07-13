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


_MISSING = object()


def _read_json(root: Path, name: str, source: str, errors: list[dict],
               sheet_type: str | None = None):
    """Returns ``_MISSING`` when the file is absent or fails to parse (the
    parse-failure case already appends its own error entry, so callers just
    need to tell "no file" apart from "file, possibly JSON ``null``"), or the
    parsed JSON value otherwise -- including ``None`` for a file that
    literally contains ``null``, which callers must reject as malformed."""
    p = root / name
    if not p.exists():
        return _MISSING
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
        # RecursionError: pathologically deep JSON blows the parser stack
        # before our own depth cap can see the tree.
        errors.append(_entry(source, sheet_type, f"{name}: {e.__class__.__name__}: {e}"))
        return _MISSING


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
    return {"group_fields": group_fields, "fields": fields,
            "derived": derived, "group_desc": "this sheet type's groups"}


def _union_scope(sheets: dict) -> dict:
    """Best-effort scope for standalone fragment checks: every group defined
    in sheets.json, the union of all sheet types' assembled field keys, and
    every group-/type-level derived name. Any name valid for at least one
    sheet type passes; a name that exists nowhere errors."""
    from .modules import assembled_fields  # deferred: modules imports us

    groups = sheets.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}
    group_fields: dict[str, list[str]] = {}
    derived: set[str] = set()
    for gid, g in groups.items():
        if not isinstance(g, dict):
            continue
        gf = g.get("fields") if isinstance(g.get("fields"), list) else []
        group_fields[gid] = [f["key"] for f in gf
                             if isinstance(f, dict) and isinstance(f.get("key"), str)]
        if isinstance(g.get("derived"), dict):
            derived |= set(g["derived"])
    fields: set[str] = set()
    sheet_types = sheets.get("sheet_types", {})
    if not isinstance(sheet_types, dict):
        sheet_types = {}
    for tid, st in sheet_types.items():
        if isinstance(st, dict) and isinstance(st.get("derived"), dict):
            derived |= set(st["derived"])
        fields |= {f["key"] for f in assembled_fields(sheets, tid)
                   if isinstance(f.get("key"), str)}
    return {"group_fields": group_fields, "fields": fields,
            "derived": derived, "group_desc": "the groups defined in sheets.json"}


class _Expander:
    """Validates and splices one tree. The standalone fragment pass uses the
    union scope with ``check_placement=False``: refs are best-effort checked
    against every sheet type at once, but duplicate-placement is a per-type
    property (groups sharing a field key across types would false-positive),
    so it is only enforced during per-type expansion. ``scope=None`` still
    means a purely structural pass (no ref/duplicate checks)."""

    def __init__(self, fragments: dict, scope: dict | None,
                 bad_fragments: set[str] | None = None,
                 check_placement: bool = True):
        self.fragments = fragments
        self.scope = scope
        self.check_placement = check_placement
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
        if not self.check_placement:
            return
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
                        f"{path}.group: {value!r} is not one of "
                        f"{self.scope['group_desc']}")
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
                if self.check_placement:
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


def _load_theme(root: Path, errors: list[dict]) -> dict:
    raw = _read_json(root, "theme.json", "theme", errors)
    if raw is _MISSING:
        return {}
    if not isinstance(raw, dict):
        errors.append(_entry("theme", None, "theme.json: must be an object"))
        return {}
    theme: dict = {}
    for key, value in raw.items():
        if key == "colors":
            if not isinstance(value, dict):
                errors.append(_entry("theme", None, "theme.json: colors must be an object"))
                continue
            colors: dict = {}
            for ck, cv in value.items():
                if ck not in COLOR_KEYS:
                    errors.append(_entry("theme", None,
                                         f"theme.json: unknown color {ck!r}"))
                elif not isinstance(cv, str) or not _HEX.match(cv):
                    errors.append(_entry("theme", None,
                                         f"theme.json: {ck} must be a hex color"))
                else:
                    colors[ck] = cv
            if ("bg" in colors) != ("ink" in colors):
                errors.append(_entry("theme", None,
                                     "theme.json: bg and ink must be set together"))
                colors.pop("bg", None)
                colors.pop("ink", None)
            if colors:
                theme["colors"] = colors
        elif key == "fonts":
            if not isinstance(value, dict):
                errors.append(_entry("theme", None, "theme.json: fonts must be an object"))
                continue
            fonts: dict = {}
            for fk, fv in value.items():
                if fk not in FONT_KEYS:
                    errors.append(_entry("theme", None,
                                         f"theme.json: unknown font slot {fk!r}"))
                elif fv not in FONT_STACKS:
                    errors.append(_entry("theme", None,
                                         f"theme.json: unknown font {fv!r}"))
                else:
                    fonts[fk] = fv
            if fonts:
                theme["fonts"] = fonts
        elif key == "dots":
            if value in DOT_SHAPES:
                theme["dots"] = value
            else:
                errors.append(_entry("theme", None,
                                     f"theme.json: unknown dots shape {value!r}"))
        elif key == "corners":
            if value in CORNER_STYLES:
                theme["corners"] = value
            else:
                errors.append(_entry("theme", None,
                                     f"theme.json: unknown corners style {value!r}"))
        else:
            errors.append(_entry("theme", None, f"theme.json: unknown key {key!r}"))
    return theme


def _load_layout(root: Path, sheets: dict, errors: list[dict]) -> dict:
    layout: dict = {"sheet_types": {}}
    raw = _read_json(root, "layout.json", "layout", errors, sheet_type="*")
    if raw is _MISSING:
        return layout
    if not isinstance(raw, dict):
        errors.append(_entry("layout", "*", "layout.json: must be an object"))
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
        errors.append(_entry("layout", "*", "layout.json: sheet_types must be an object"))
        trees = {}
    # Standalone pass over every fragment: an unused-but-broken fragment is
    # reported once (sheet_type None) and drops nothing by itself. Refs are
    # checked against the union scope (any name valid for at least one sheet
    # type passes); per-type expansion re-validates against the actual scope.
    bad_fragments: set[str] = set()
    union = _union_scope(sheets) if fragments else None
    for fid, frag in fragments.items():
        ex = _Expander(fragments, union, check_placement=False)
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
    """(layout, theme, display_errors) for a pack root. Never raises: display
    files are cosmetic, so even an unforeseen exception must degrade to
    "no display files + an error entry", never break load_pack/resolve()."""
    errors: list[dict] = []
    try:
        layout = _load_layout(root, sheets, errors)
        theme = _load_theme(root, errors)
        if (root / "theme.css").exists():
            errors.append(_entry("theme", None,
                                 "theme.css is not supported — use theme.json"))
    except Exception as e:  # containment boundary, deliberately broad
        errors.append(_entry("layout", "*",
                             f"display files: {e.__class__.__name__}: {e}"))
        return {"sheet_types": {}}, {}, errors
    return layout, theme, errors
