# Mechanics Phase 6 — Sheet Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pretty sheet rendering — a widget library per field type, per-sheet-type `layout.json` arrangement, and `theme.json` token theming — replacing the Phase-3 generic `label: value` sheet rendering.

**Architecture:** Backend gains `store/module_display.py` (layout/theme validation + fragment splicing, errors in a structured non-fatal `display_errors` channel) wired into `modules.load_pack`. Frontend gains `SheetWidgets.tsx` (one widget per field type, view/edit modes) and `SheetLayout.tsx` (one rendering path for layouted and unlayouted sheets); `SheetEditor` swaps its hand-rolled bodies for them and applies scoped theme CSS variables. Both built-in reference modules gain `layout.json` + `theme.json`.

**Tech Stack:** Python stdlib (no pydantic in store), FastAPI route untouched (pack flows through `GET /api/modules/{mid}` as-is), React + TypeScript, vitest + testing-library, pytest.

**Spec:** `docs/superpowers/specs/2026-07-12-mechanics-phase6-sheet-display-design.md` — binding; re-read it before starting.

## Global Constraints

- **Never-raise loader**: `load_pack` and everything it calls must never raise on malformed pack content. Display problems go in `display_errors` (structured entries `{"source": "layout"|"theme", "sheet_type": <tid>|None, "message": str}`), **never** in `errors` — `resolve()` refuses packs with `errors` and a cosmetic typo must not unbind mechanics.
- **Store code is pure stdlib** — no pydantic, no new deps (Android/Chaquopy).
- **Caps**: expanded layout tree per sheet type ≤ depth 32, ≤ 1000 nodes.
- **Privacy**: invented names only in fixtures/tests/docs (Seraphine, Mara, Winifred, Realm, Saltmarch and the existing pack names).
- **Worktree test commands** (run from worktree root, Git Bash): backend `PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend -q` (the venv's editable install points at the main checkout; PYTHONPATH shadows it — sanity-check with `python -c "import grimoire; print(grimoire.__file__)"`). Frontend: `cd frontend && npm install` once, then `npx vitest run` and `npx tsc -b` **from `frontend/`**.
- Existing suites must stay green after every task.

## File Structure

| File | Responsibility |
|---|---|
| `backend/src/grimoire/store/module_display.py` (new) | layout.json + theme.json validation, fragment splicing, theme.css detection → `(layout, theme, display_errors)` |
| `backend/src/grimoire/store/modules.py` (modify) | call `module_display.load_display` in `load_pack`; `display_ok` in `_scan` rows |
| `backend/tests/test_module_display.py` (new) | all layout/theme validation tests |
| `backend/tests/test_modules_store.py` (modify) | reference packs display-clean; `display_ok`; resolve unaffected |
| `backend/src/grimoire/store/builtin_modules/{d20-basic,pool-basic}/{layout.json,theme.json}` (new) | reference layouts/themes exercising the whole contract |
| `frontend/src/api/client.ts` (modify) | `LayoutNode`, `ModuleTheme`, `DisplayError` types; `ModuleSummary.display_ok`; `ModuleDetail.layout/theme/display_errors` |
| `frontend/src/components/SheetWidgets.tsx` (new) | per-field-type widgets (view/edit), derived badge, `FieldWidget` dispatcher |
| `frontend/src/components/SheetLayout.tsx` (new) | layout-tree renderer, `defaultLayout`, trailing Other/Derived sections, `assembledDefs` (the one flattening helper), `themeStyle` |
| `frontend/src/components/SheetEditor.tsx` (modify) | bodies → `SheetLayout`; theme vars + data attrs; dropped-layout hint |
| `frontend/src/components/SheetPanel.tsx` (modify) | import `assembledDefs` instead of local duplicate |
| `frontend/src/routes/ModulesView.tsx` (modify) | Display section; list-row "display issues" marker |
| `frontend/src/index.css` (modify) | widget/layout/theme styles; retire `.sheet-view`/`.sheet-row` |
| `.claude/skills/create-mechanics-module/SKILL.md` (modify) | layout/theme authoring step |

---

### Task 1: Backend layout validation + splicing (`module_display.py`)

**Files:**
- Create: `backend/src/grimoire/store/module_display.py`
- Test: `backend/tests/test_module_display.py`

**Interfaces:**
- Produces: `module_display.load_display(root: Path, sheets: dict) -> tuple[dict, dict, list[dict]]` returning `(layout, theme, display_errors)` where `layout = {"sheet_types": {tid: <spliced tree>}}` (only valid trees, no `use` nodes), `theme = {}` until Task 2, `display_errors = [{"source", "sheet_type", "message"}, ...]`.
- Consumes: `modules.assembled_fields(sheets, tid)` via **deferred import** (modules.py will import module_display at top level in Task 3 — a top-level back-import would be circular).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_module_display.py`:

```python
import json

from grimoire.store import module_display

# Two groups + two character types so per-type granularity is testable.
SHEETS = {
    "groups": {
        "attributes": {
            "label": "Attributes",
            "fields": [
                {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
                {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1},
            ],
            "derived": {"reflex": "min(vigor, wits)"},
        },
        "abilities": {
            "label": "Abilities",
            "fields": [{"key": "brawl", "label": "Brawl", "type": "dots", "max": 5}],
        },
    },
    "sheet_types": {
        "warden": {
            "label": "Warden", "kind": "characters",
            "groups": ["attributes", "abilities"],
            "fields": [{"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
            "derived": {"surge": "reflex + essence_max - essence"},
        },
        "adept": {
            "label": "Adept", "kind": "characters",
            "groups": ["attributes"],
            "fields": [{"key": "focus", "label": "Focus", "type": "number", "default": 0}],
        },
    },
}

GOOD_LAYOUT = {
    "fragments": {
        "attr-block": {"group": "attributes", "grid": True, "title": "Attributes"},
    },
    "sheet_types": {
        "warden": {"column": [
            {"use": "attr-block"},
            {"row": [
                {"group": "abilities", "title": "Abilities"},
                {"column": [{"fields": ["essence"]}, {"derived": ["reflex", "surge"]}], "title": "Power"},
            ]},
        ]},
        "adept": {"column": [{"use": "attr-block"}, {"fields": ["focus"]}]},
    },
}


def write_layout(tmp_path, layout):
    (tmp_path / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    return tmp_path


def load(tmp_path):
    return module_display.load_display(tmp_path, SHEETS)


def layout_errors(tmp_path, layout):
    layout_out, _theme, errors = load(write_layout(tmp_path, layout))
    return layout_out, [e for e in errors if e["source"] == "layout"]


def test_no_display_files(tmp_path):
    layout, theme, errors = load(tmp_path)
    assert layout == {"sheet_types": {}}
    assert theme == {}
    assert errors == []


def test_good_layout_splices_fragments(tmp_path):
    layout, errors = layout_errors(tmp_path, GOOD_LAYOUT)
    assert errors == []
    assert set(layout["sheet_types"]) == {"warden", "adept"}
    warden = layout["sheet_types"]["warden"]
    spliced = warden["column"][0]
    assert spliced == {"group": "attributes", "grid": True, "title": "Attributes"}
    assert "use" not in json.dumps(layout)


def test_use_title_overrides_fragment_title(tmp_path):
    lay = {"fragments": {"f": {"group": "attributes", "title": "Original"}},
           "sheet_types": {"adept": {"column": [{"use": "f", "title": "Override"}]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert errors == []
    assert layout["sheet_types"]["adept"]["column"][0]["title"] == "Override"


def test_unparseable_layout_is_file_level_error(tmp_path):
    (tmp_path / "layout.json").write_text("{nope", encoding="utf-8")
    layout, _theme, errors = load(tmp_path)
    assert layout == {"sheet_types": {}}
    assert errors and errors[0]["source"] == "layout" and errors[0]["sheet_type"] is None


def test_non_object_root(tmp_path):
    layout, errors = layout_errors(tmp_path, ["not", "an", "object"])
    assert layout["sheet_types"] == {} and errors[0]["sheet_type"] is None


def test_unknown_root_key(tmp_path):
    lay = dict(GOOD_LAYOUT, extra=1)
    layout, errors = layout_errors(tmp_path, lay)
    assert set(layout["sheet_types"]) == {"warden", "adept"}  # trees still load
    assert any("extra" in e["message"] and e["sheet_type"] is None for e in errors)


def test_unknown_sheet_type_key(tmp_path):
    lay = {"sheet_types": {"ghost": {"fields": []}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert layout["sheet_types"] == {}
    assert any("ghost" in e["message"] and e["sheet_type"] is None for e in errors)


def bad_tree_case(tmp_path, tree, needle):
    """One bad warden tree: warden dropped with a warden-tagged error; adept survives."""
    lay = {"sheet_types": {"warden": tree,
                           "adept": {"fields": ["focus"]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "warden" not in layout["sheet_types"]
    assert "adept" in layout["sheet_types"]
    assert any(needle in e["message"] and e["sheet_type"] == "warden" for e in errors)


def test_node_not_object(tmp_path):
    bad_tree_case(tmp_path, {"column": ["x"]}, "object")


def test_node_needs_exactly_one_form(tmp_path):
    bad_tree_case(tmp_path, {"row": [], "column": []}, "exactly one")
    bad_tree_case(tmp_path, {"title": "no form"}, "exactly one")


def test_unknown_node_key(tmp_path):
    bad_tree_case(tmp_path, {"group": "attributes", "colour": "red"}, "colour")


def test_wrong_value_types(tmp_path):
    bad_tree_case(tmp_path, {"row": "x"}, "array")
    bad_tree_case(tmp_path, {"fields": [1]}, "fields")
    bad_tree_case(tmp_path, {"group": 7}, "group")
    bad_tree_case(tmp_path, {"group": "attributes", "grid": "yes"}, "boolean")
    bad_tree_case(tmp_path, {"group": "attributes", "title": {}}, "title")


def test_grid_only_on_group_or_fields(tmp_path):
    bad_tree_case(tmp_path, {"row": [], "grid": True}, "grid")


def test_unknown_refs(tmp_path):
    bad_tree_case(tmp_path, {"group": "ghost"}, "ghost")
    bad_tree_case(tmp_path, {"fields": ["ghost"]}, "ghost")
    bad_tree_case(tmp_path, {"derived": ["ghost"]}, "ghost")
    bad_tree_case(tmp_path, {"use": "ghost"}, "ghost")


def test_group_ref_must_be_in_sheet_types_groups(tmp_path):
    # abilities exists in sheets.json but adept does not include it
    lay = {"sheet_types": {"adept": {"group": "abilities"}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any(e["sheet_type"] == "adept" for e in errors)


def test_duplicate_placement(tmp_path):
    bad_tree_case(tmp_path, {"column": [{"group": "attributes"}, {"fields": ["vigor"]}]},
                  "once")
    bad_tree_case(tmp_path, {"column": [{"derived": ["reflex"]}, {"derived": ["reflex"]}]},
                  "once")


def test_fragment_cycle(tmp_path):
    lay = {"fragments": {"a": {"column": [{"use": "b"}]}, "b": {"column": [{"use": "a"}]}},
           "sheet_types": {"adept": {"use": "a"}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("cycle" in e["message"] for e in errors)


def test_unused_broken_fragment_reported_but_drops_nothing(tmp_path):
    lay = dict(GOOD_LAYOUT, fragments=dict(GOOD_LAYOUT["fragments"], broken={"row": "x"}))
    layout, errors = layout_errors(tmp_path, lay)
    assert set(layout["sheet_types"]) == {"warden", "adept"}
    assert any("broken" in e["message"] and e["sheet_type"] is None for e in errors)


def test_used_broken_fragment_drops_user(tmp_path):
    lay = {"fragments": {"bad": {"row": "x"}},
           "sheet_types": {"adept": {"use": "bad"}, "warden": GOOD_LAYOUT["sheet_types"]["warden"]}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any(e["sheet_type"] == "adept" and "invalid" in e["message"] for e in errors)


def test_depth_cap(tmp_path):
    tree = {"fields": ["focus"]}
    for _ in range(40):
        tree = {"column": [tree]}
    lay = {"sheet_types": {"adept": tree}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("depth" in e["message"] for e in errors)


def test_node_cap(tmp_path):
    lay = {"sheet_types": {"adept": {"row": [{"derived": []} for _ in range(1100)]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("node cap" in e["message"] for e in errors)


def test_pathologically_deep_json_never_raises(tmp_path):
    # deep enough to blow the JSON parser's recursion limit before our caps
    (tmp_path / "layout.json").write_text("[" * 100000 + "]" * 100000,
                                          encoding="utf-8")
    layout, _theme, errors = load(tmp_path)  # must not raise
    assert layout == {"sheet_types": {}}
    assert errors and errors[0]["source"] == "layout"
```

Note `test_duplicate_placement`'s second case: an empty `derived` list is fine, but placing `reflex` twice is not; and `{"derived": []}` nodes in `test_node_cap` are individually valid so only the cap trips.

- [ ] **Step 2: Run tests to verify they fail**

Run (worktree root, Git Bash):
```bash
PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_display.py -q
```
Expected: collection error — `module_display` does not exist.

- [ ] **Step 3: Implement `module_display.py`**

Create `backend/src/grimoire/store/module_display.py`:

```python
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
```

- [ ] **Step 4: Run tests — layout tests pass**

```bash
PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_display.py -q
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_display.py backend/tests/test_module_display.py
git commit -m "feat(modules): layout.json validation + fragment splicing (#165)"
```

---

### Task 2: Backend theme validation + theme.css detection

**Files:**
- Modify: `backend/src/grimoire/store/module_display.py`
- Test: `backend/tests/test_module_display.py`

**Interfaces:**
- Produces: `load_display` now returns validated `theme` dict (keys `colors`/`fonts`/`dots`/`corners`, dropped entries removed) and appends theme + theme.css `display_errors` (`source: "theme"`, `sheet_type: None`).

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_module_display.py`)

```python
GOOD_THEME = {
    "colors": {"bg": "#191521", "ink": "#d8d2c4", "accent": "#8a2a3b"},
    "fonts": {"display": "display", "body": "serif"},
    "dots": "diamond",
    "corners": "sharp",
}


def write_theme(tmp_path, theme):
    text = theme if isinstance(theme, str) else json.dumps(theme)
    (tmp_path / "theme.json").write_text(text, encoding="utf-8")
    return tmp_path


def theme_of(tmp_path, theme):
    _layout, out, errors = load(write_theme(tmp_path, theme))
    return out, [e for e in errors if e["source"] == "theme"]


def test_good_theme(tmp_path):
    theme, errors = theme_of(tmp_path, GOOD_THEME)
    assert errors == []
    assert theme == GOOD_THEME


def test_theme_unparseable(tmp_path):
    theme, errors = theme_of(tmp_path, "{nope")
    assert theme == {} and errors and errors[0]["sheet_type"] is None


def test_theme_not_object(tmp_path):
    theme, errors = theme_of(tmp_path, ["x"])
    assert theme == {} and errors


def test_theme_unknown_key_dropped(tmp_path):
    theme, errors = theme_of(tmp_path, dict(GOOD_THEME, sparkle=True))
    assert "sparkle" not in theme
    assert any("sparkle" in e["message"] for e in errors)


def test_theme_bad_hex(tmp_path):
    theme, errors = theme_of(tmp_path, {"colors": {"accent": "url(evil)"}})
    assert theme == {}
    assert any("hex" in e["message"] for e in errors)


def test_theme_bg_without_ink_drops_both(tmp_path):
    theme, errors = theme_of(tmp_path, {"colors": {"bg": "#fff", "accent": "#8a2a3b"}})
    assert theme == {"colors": {"accent": "#8a2a3b"}}
    assert any("together" in e["message"] for e in errors)


def test_theme_unknown_font(tmp_path):
    theme, errors = theme_of(tmp_path, {"fonts": {"body": "comic-sans"}})
    assert theme == {}
    assert any("comic-sans" in e["message"] for e in errors)


def test_theme_unknown_enum_values(tmp_path):
    theme, errors = theme_of(tmp_path, {"dots": "star", "corners": "bevelled"})
    assert theme == {}
    assert len(errors) == 2


def test_theme_css_detected(tmp_path):
    (tmp_path / "theme.css").write_text(".x{}", encoding="utf-8")
    _layout, theme, errors = load(tmp_path)
    assert theme == {}
    assert any("theme.css" in e["message"] and e["source"] == "theme" for e in errors)
```

- [ ] **Step 2: Run to verify the new tests fail**

Same command as Task 1 Step 4. Expected: the new theme tests FAIL (theme is always `{}` with no errors), layout tests still pass.

- [ ] **Step 3: Implement theme validation**

In `module_display.py`, add `_load_theme` and wire it into `load_display`:

```python
def _load_theme(root: Path, errors: list[dict]) -> dict:
    raw = _read_json(root, "theme.json", "theme", errors)
    if raw is None:
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
```

and replace the `load_display` body:

```python
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
        errors.append(_entry("layout", None,
                             f"display files: {e.__class__.__name__}: {e}"))
        return {"sheet_types": {}}, {}, errors
    return layout, theme, errors
```

- [ ] **Step 4: Run tests — all pass**

Same command. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_display.py backend/tests/test_module_display.py
git commit -m "feat(modules): theme.json token validation + theme.css detection (#165)"
```

---

### Task 3: Wire display loading into `load_pack` + `_scan`

**Files:**
- Modify: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: `load_pack(mid)` result gains `"layout"`, `"theme"`, `"display_errors"` keys; `list_modules()` rows gain `"display_ok": bool`. `resolve()` behavior unchanged by display errors.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_modules_store.py`)

```python
def test_pack_display_keys_default_empty(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    pack = modules.load_pack("testmod")
    assert pack["layout"] == {"sheet_types": {}}
    assert pack["theme"] == {}
    assert pack["display_errors"] == []


def test_display_errors_do_not_invalidate(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "layout.json").write_text("{broken", encoding="utf-8")
    (d / "theme.css").write_text(".x{}", encoding="utf-8")
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []          # mechanics untouched
    assert len(pack["display_errors"]) == 2
    rows = {m["id"]: m for m in modules.list_modules()}
    assert rows["testmod"]["valid"] is True
    assert rows["testmod"]["display_ok"] is False


def test_display_ok_true_for_clean_pack(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    rows = {m["id"]: m for m in modules.list_modules()}
    assert rows["testmod"]["display_ok"] is True


def test_load_pack_survives_pathological_display_files(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "layout.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    pack = modules.load_pack("testmod")  # must not raise
    assert pack["errors"] == []
    assert pack["layout"] == {"sheet_types": {}}
    assert pack["display_errors"]


def test_resolve_ignores_display_errors(monkeypatch, tmp_path):
    from grimoire.store import worlds, campaigns
    home = _home(monkeypatch, tmp_path)
    d = make_pack(home)
    (d / "layout.json").write_text("{broken", encoding="utf-8")
    wid = worlds.create_world("Realm")["id"]
    cid = campaigns.create_campaign(wid, "Saltmarch Run")["id"]
    modules.set_campaign_module(cid, "testmod")
    assert modules.resolve(cid) == "testmod"
```

(If `create_world`/`create_campaign` signatures differ, mirror the calls used elsewhere in this test file / `backend/tests/test_sheets_store.py` — the assertion that matters is `resolve(cid) == "testmod"` despite `display_errors`.)

- [ ] **Step 2: Run to verify they fail**

```bash
PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q
```
Expected: the four new tests FAIL (KeyError `layout` / `display_ok`).

- [ ] **Step 3: Implement**

In `modules.py`:

1. Import: change `from . import dice, expressions` to `from . import dice, expressions, module_display`.
2. In `load_pack`, after `content = _load_content(root, sheets, errors)`:

```python
    layout, theme, display_errors = module_display.load_display(root, sheets)
```

and extend the returned dict:

```python
    pack = {
        "id": mid,
        "source": source,
        "manifest": {**meta, "id": mid},
        "sheets": sheets,
        "checks": checks,
        "rules": rules,
        "content": content,
        "layout": layout,
        "theme": theme,
        "display_errors": display_errors,
        "errors": errors,
    }
```

3. In `_scan`, add to the row dict:

```python
            "valid": not pack["errors"],
            "display_ok": not pack["display_errors"],
```

- [ ] **Step 4: Run the full backend suite**

```bash
PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend -q
```
Expected: all PASS (route tests dump the new keys transparently).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): load_pack carries layout/theme/display_errors; display_ok in registry (#165)"
```

---

### Task 4: Reference-module layouts + themes

**Files:**
- Create: `backend/src/grimoire/store/builtin_modules/d20-basic/layout.json`, `.../d20-basic/theme.json`, `.../pool-basic/layout.json`, `.../pool-basic/theme.json`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: both built-ins ship display files that are the contract's fixtures; between them they exercise every node form (`row`, `column`, `group`, `fields`, `derived`, `use`), `title`, `grid`, and every theme key.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_modules_store.py`)

```python
def test_builtin_packs_display_clean(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for mid in ("d20-basic", "pool-basic"):
        pack = modules.load_pack(mid)
        assert pack["errors"] == [], (mid, pack["errors"])
        assert pack["display_errors"] == [], (mid, pack["display_errors"])
        assert pack["layout"]["sheet_types"], mid       # every builtin ships layouts
        assert pack["theme"], mid                        # and a theme
        assert "use" not in json.dumps(pack["layout"])   # spliced
```

(Add `import json` at the top of the file if not present — it is already imported.)

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `pack["layout"]["sheet_types"]` is empty for both.

- [ ] **Step 3: Author the display files**

`backend/src/grimoire/store/builtin_modules/d20-basic/layout.json`:

```json
{
  "fragments": {
    "core-stats": {
      "column": [
        {"group": "attributes", "grid": true, "title": "Attributes"},
        {"derived": ["str_mod", "dex_mod", "mind_mod"], "title": "Modifiers"}
      ]
    }
  },
  "sheet_types": {
    "warrior": {
      "column": [
        {"use": "core-stats"},
        {"row": [
          {"group": "skills", "title": "Skills"},
          {"column": [{"fields": ["hp", "gear"]}, {"derived": ["melee_bonus"]}], "title": "Combat"}
        ]}
      ]
    },
    "adept": {
      "column": [
        {"use": "core-stats"},
        {"row": [
          {"group": "skills", "title": "Skills"},
          {"column": [{"fields": ["hp", "spell_slots", "spells"]}, {"derived": ["spell_bonus"]}], "title": "Magic"}
        ]}
      ]
    },
    "wondrous-item": {
      "column": [{"fields": ["charges", "bonus", "quirk"], "title": "Item"}]
    }
  }
}
```

`backend/src/grimoire/store/builtin_modules/d20-basic/theme.json`:

```json
{
  "colors": {"bg": "#f3ecd9", "ink": "#2b2416", "muted": "#7a6f55", "accent": "#8a6d1f", "rule": "#2b2416"},
  "fonts": {"body": "serif"},
  "dots": "circle",
  "corners": "rounded"
}
```

`backend/src/grimoire/store/builtin_modules/pool-basic/layout.json`:

```json
{
  "fragments": {
    "traits": {
      "row": [
        {"group": "attributes", "title": "Attributes"},
        {"group": "abilities", "title": "Abilities"}
      ]
    }
  },
  "sheet_types": {
    "medium": {
      "column": [
        {"use": "traits"},
        {"row": [
          {"column": [{"fields": ["essence", "health"]}], "title": "Condition"},
          {"column": [{"derived": ["awareness", "sight_pool"]}, {"fields": ["quirk", "gear"]}], "title": "Gifts"}
        ]}
      ]
    },
    "shifter": {
      "column": [
        {"use": "traits"},
        {"row": [
          {"column": [{"fields": ["fury", "health"]}], "title": "Condition"},
          {"column": [{"derived": ["awareness", "claw_pool"]}, {"fields": ["quirk", "gear"]}], "title": "Gifts"}
        ]}
      ]
    },
    "talisman": {"column": [{"fields": ["power", "charges"], "title": "Talisman"}]},
    "haven": {"column": [{"fields": ["ward", "size"], "title": "Haven"}]}
  }
}
```

`backend/src/grimoire/store/builtin_modules/pool-basic/theme.json`:

```json
{
  "colors": {"bg": "#191521", "ink": "#d8d2c4", "muted": "#8d8496", "accent": "#8a2a3b", "rule": "#5a4a66"},
  "fonts": {"display": "display", "body": "serif"},
  "dots": "diamond",
  "corners": "sharp"
}
```

- [ ] **Step 4: Run the full backend suite** — all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/builtin_modules backend/tests/test_modules_store.py
git commit -m "feat(modules): reference layouts + themes for d20-basic and pool-basic (#165)"
```

---

### Task 5: Client types + `SheetWidgets.tsx` + widget CSS

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/components/SheetWidgets.tsx`
- Test: `frontend/src/components/SheetWidgets.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Produces (client.ts):

```ts
export type LayoutNode = {
  row?: LayoutNode[]; column?: LayoutNode[]; group?: string;
  fields?: string[]; derived?: string[]; title?: string; grid?: boolean;
};
export type ModuleTheme = {
  colors?: Partial<Record<"bg" | "ink" | "muted" | "accent" | "rule", string>>;
  fonts?: Partial<Record<"display" | "body", string>>;
  dots?: string; corners?: string;
};
export type DisplayError = { source: "layout" | "theme"; sheet_type: string | null; message: string };
```
  plus `display_ok?: boolean` on `ModuleSummary` and `layout?: { sheet_types: Record<string, LayoutNode> }; theme?: ModuleTheme; display_errors?: DisplayError[]` on `ModuleDetail` (optional so existing test fixtures stay valid; the backend always sends them).
- Produces (SheetWidgets.tsx): `export type WidgetMode = "view" | "edit"`; `export function FieldWidget({ def, value, mode, grid, onChange }: { def: ModuleField; value: unknown; mode: WidgetMode; grid?: boolean; onChange?: (v: unknown) => void })`; `export function DerivedBadge({ name, value }: { name: string; value: unknown })`; `export function isResource(v: unknown): v is { current: number; max: number }`.
- Click-to-set semantics (dots and track): pip *n* (1-based) sets value *n*; clicking the pip at the current value sets *n−1* (so 0 is reachable). Pips are `<button type="button">` with `aria-label` `` `${label} ${n}` `` in edit mode, plain `<span>`s in view mode.
- List edit keeps the raw-string draft discipline: the edit widget renders whatever string it is given and emits raw strings; joining/splitting stays in SheetEditor.

- [ ] **Step 1: Add the types to `client.ts`** (in the `// modules` section, before `ModuleSummary`), and extend `ModuleSummary`/`ModuleDetail` as above.

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/SheetWidgets.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { FieldWidget, DerivedBadge } from "./SheetWidgets";
import type { ModuleField } from "../api/client";

const dots: ModuleField = { key: "vigor", label: "Vigor", type: "dots", max: 5 };
const track: ModuleField = { key: "health", label: "Health", type: "track", max: 7 };
const res: ModuleField = { key: "essence", label: "Essence", type: "resource", max: 10 };
const num: ModuleField = { key: "strength", label: "Strength", type: "number", min: 1, max: 20 };
const list: ModuleField = { key: "gear", label: "Gear", type: "list" };

test("dots view renders max pips with value filled, no buttons", () => {
  const { container } = render(<FieldWidget def={dots} value={3} mode="view" />);
  expect(container.querySelectorAll(".pip").length).toBe(5);
  expect(container.querySelectorAll(".pip.on").length).toBe(3);
  expect(container.querySelectorAll("button").length).toBe(0);
});

test("dots edit: click sets value; clicking current decrements", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={dots} value={3} mode="edit" onChange={onChange} />);
  fireEvent.click(screen.getByLabelText("Vigor 5"));
  expect(onChange).toHaveBeenCalledWith(5);
  fireEvent.click(screen.getByLabelText("Vigor 3"));
  expect(onChange).toHaveBeenCalledWith(2);
});

test("track edit clicking box 1 at value 1 reaches 0", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={track} value={1} mode="edit" onChange={onChange} />);
  fireEvent.click(screen.getByLabelText("Health 1"));
  expect(onChange).toHaveBeenCalledWith(0);
});

test("resource view shows bar and current/max text", () => {
  const { container } = render(
    <FieldWidget def={res} value={{ current: 6, max: 10 }} mode="view" />);
  expect(screen.getByText("6 / 10")).toBeInTheDocument();
  const fill = container.querySelector(".resource-fill") as HTMLElement;
  expect(fill.style.width).toBe("60%");
});

test("resource edit exposes paired inputs", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={res} value={{ current: 6, max: 10 }} mode="edit" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("Essence current"), { target: { value: "4" } });
  expect(onChange).toHaveBeenCalledWith({ current: 4, max: 10 });
});

test("number renders stat cell in grid mode", () => {
  const { container } = render(<FieldWidget def={num} value={14} mode="view" grid />);
  expect(container.querySelector(".stat-cell")).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
});

test("number edit in grid mode is an input", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={num} value={14} mode="edit" grid onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("Strength"), { target: { value: "15" } });
  expect(onChange).toHaveBeenCalledWith(15);
});

test("list view renders bullets; edit emits raw string", () => {
  const { rerender } = render(<FieldWidget def={list} value={["rope", "lantern"]} mode="view" />);
  expect(screen.getByText("rope")).toBeInTheDocument();
  const onChange = vi.fn();
  rerender(<FieldWidget def={list} value={"rope\n"} mode="edit" onChange={onChange} />);
  const ta = screen.getByLabelText("Gear") as HTMLTextAreaElement;
  expect(ta.value).toBe("rope\n");
  fireEvent.change(ta, { target: { value: "rope\nlan" } });
  expect(onChange).toHaveBeenCalledWith("rope\nlan");
});

test("derived badge shows name and value, em-dash when undefined", () => {
  render(<DerivedBadge name="sight_pool" value={6} />);
  expect(screen.getByText("sight_pool")).toBeInTheDocument();
  expect(screen.getByText("6")).toBeInTheDocument();
  render(<DerivedBadge name="ghost" value={undefined} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
```

(If `getByLabelText("Gear")` fails because the existing `Field` component labels via its heading rather than `htmlFor`, match how `SheetEditor.test.tsx` addresses the gear textarea today and use that instead.)

- [ ] **Step 3: Run to verify they fail**

Run from `frontend/`: `npx vitest run src/components/SheetWidgets.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement `SheetWidgets.tsx`**

```tsx
import type { ModuleField } from "../api/client";
import { Field } from "./Field";

export type WidgetMode = "view" | "edit";

type WidgetProps = {
  def: ModuleField; value: unknown; mode: WidgetMode;
  grid?: boolean; onChange?: (v: unknown) => void;
};

export function isResource(v: unknown): v is { current: number; max: number } {
  return !!v && typeof v === "object" && "current" in (v as object) && "max" in (v as object);
}

const label = (f: ModuleField) => f.label ?? f.key;

/** dots + track share click-to-set: pip n sets value n; clicking the pip at
 *  the current value decrements to n-1 so 0 stays reachable. */
function Pips({ def, value, mode, shape, onChange }: WidgetProps & { shape: "dot" | "box" }) {
  const max = typeof def.max === "number" ? def.max : 5;
  const n = typeof value === "number" ? value : 0;
  const name = label(def);
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span className="pips">
        {Array.from({ length: max }, (_, i) =>
          mode === "edit" ? (
            <button key={i} type="button" className={`pip ${shape}${i < n ? " on" : ""}`}
                    aria-label={`${name} ${i + 1}`} aria-pressed={i < n}
                    onClick={() => onChange?.(i + 1 === n ? i : i + 1)} />
          ) : (
            <span key={i} className={`pip ${shape}${i < n ? " on" : ""}`} />
          ))}
      </span>
    </div>
  );
}

function Resource({ def, value, mode, onChange }: WidgetProps) {
  const rv = isResource(value) ? value : { current: 0, max: def.max ?? 0 };
  const pct = rv.max > 0 ? Math.max(0, Math.min(1, rv.current / rv.max)) * 100 : 0;
  const name = label(def);
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span className="resource">
        <span className="resource-bar"><span className="resource-fill" style={{ width: `${pct}%` }} /></span>
        {mode === "edit" ? (
          <span className="resource-inputs">
            <input type="number" aria-label={`${name} current`} min={0} value={rv.current}
                   onChange={(e) => onChange?.({ ...rv, current: Number(e.target.value) })} />
            <span>/</span>
            <input type="number" aria-label={`${name} max`} min={0} value={rv.max}
                   onChange={(e) => onChange?.({ ...rv, max: Number(e.target.value) })} />
          </span>
        ) : (
          <span className="resource-text">{rv.current} / {rv.max}</span>
        )}
      </span>
    </div>
  );
}

function NumberW({ def, value, mode, grid, onChange }: WidgetProps) {
  const n = typeof value === "number" ? value : 0;
  const name = label(def);
  if (grid) {
    return (
      <div className="stat-cell">
        {mode === "edit" ? (
          <input type="number" aria-label={name} min={def.min ?? 0} max={def.max} value={n}
                 onChange={(e) => onChange?.(Number(e.target.value))} />
        ) : (
          <span className="stat-value">{n}</span>
        )}
        <span className="stat-label">{name}</span>
      </div>
    );
  }
  if (mode === "edit") {
    return (
      <Field label={name}>
        <input type="number" min={def.min ?? 0} max={def.max} value={n}
               onChange={(e) => onChange?.(Number(e.target.value))} />
      </Field>
    );
  }
  return <div className="widget-row"><span className="widget-label">{name}</span><span>{n}</span></div>;
}

function TextW({ def, value, mode, onChange }: WidgetProps) {
  const name = label(def);
  if (mode === "edit") {
    return (
      <Field label={name}>
        <input type="text" value={typeof value === "string" ? value : ""}
               onChange={(e) => onChange?.(e.target.value)} />
      </Field>
    );
  }
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      <span>{typeof value === "string" && value ? value : "—"}</span>
    </div>
  );
}

/** Edit mode renders and emits the raw draft string; joining stored arrays /
 *  splitting back happens at SheetEditor's commit points, exactly as before. */
function ListW({ def, value, mode, onChange }: WidgetProps) {
  const name = label(def);
  if (mode === "edit") {
    const s = typeof value === "string" ? value
      : Array.isArray(value) ? (value as string[]).join("\n") : "";
    return (
      <Field label={name} hint="one per line">
        <textarea rows={3} value={s} onChange={(e) => onChange?.(e.target.value)} />
      </Field>
    );
  }
  const items = Array.isArray(value) ? (value as string[]) : [];
  return (
    <div className="widget-row">
      <span className="widget-label">{name}</span>
      {items.length > 0
        ? <ul className="widget-list">{items.map((v, i) => <li key={i}>{v}</li>)}</ul>
        : <span>—</span>}
    </div>
  );
}

export function DerivedBadge({ name, value }: { name: string; value: unknown }) {
  return (
    <span className="derived-badge">
      <span className="derived-name">{name}</span>
      <strong className="derived-value">{value === undefined ? "—" : String(value)}</strong>
    </span>
  );
}

export function FieldWidget(props: WidgetProps) {
  switch (props.def.type) {
    case "dots": return <Pips {...props} shape="dot" />;
    case "track": return <Pips {...props} shape="box" />;
    case "resource": return <Resource {...props} />;
    case "text": return <TextW {...props} />;
    case "list": return <ListW {...props} />;
    default: return <NumberW {...props} />;
  }
}
```

- [ ] **Step 5: Add widget CSS** (append to `frontend/src/index.css`, after the existing sheet-takeover block):

```css
/* ---- sheet display: widgets, layout, theme (#165) ----
   All --sheet-* vars are set inline on .sheet-takeover from the module's
   validated theme.json; every use falls back to app tokens so unthemed
   modules render exactly in the active app theme. */
.widget-row { display: flex; align-items: baseline; gap: 10px; font-size: 13px; padding: 2px 0; }
.widget-label {
  min-width: 110px; font-family: var(--fm); font-size: 11px;
  text-transform: uppercase; letter-spacing: .08em;
  color: var(--sheet-muted, var(--sheet-ink, var(--muted)));
}
.pips { display: inline-flex; gap: 4px; align-items: center; }
.pip {
  width: 12px; height: 12px; padding: 0; display: inline-block;
  border: 1.5px solid var(--sheet-rule, var(--sheet-ink, var(--rule)));
  background: transparent;
}
.pip.on { background: var(--sheet-accent, var(--sheet-ink, var(--accent))); }
button.pip { cursor: pointer; }
.pip.dot { border-radius: 50%; }
[data-dots="square"] .pip.dot { border-radius: 0; }
[data-dots="diamond"] .pip.dot { border-radius: 0; transform: rotate(45deg); width: 10px; height: 10px; }
.pip.box { border-radius: 2px; }
.resource { display: inline-flex; align-items: center; gap: 8px; flex: 1; }
.resource-bar {
  flex: 1; max-width: 220px; height: 10px; display: inline-block;
  border: 1.5px solid var(--sheet-rule, var(--sheet-ink, var(--rule)));
  background: transparent; /* never an app token — must sit on any themed bg */
}
.resource-fill { display: block; height: 100%; background: var(--sheet-accent, var(--accent)); }
.resource-text { font-family: var(--fm); font-size: 12px; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 8px; }
.stat-cell {
  border: 1.5px solid var(--sheet-rule, var(--sheet-ink, var(--rule)));
  border-radius: var(--sheet-radius, 0);
  padding: 8px 6px; text-align: center; display: flex; flex-direction: column; gap: 2px;
}
.stat-value { font-family: var(--sheet-fd, var(--fd)); font-size: 26px; line-height: 1; }
.stat-cell input { width: 100%; text-align: center; font-size: 18px; }
.stat-label {
  font-size: 10px; text-transform: uppercase; letter-spacing: .08em;
  color: var(--sheet-muted, var(--sheet-ink, var(--muted)));
}
.derived-badges { display: flex; flex-wrap: wrap; gap: 6px; }
.derived-badge {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; font-size: 12px;
  border: 1.5px solid var(--sheet-rule, var(--sheet-ink, var(--rule)));
  border-radius: var(--sheet-radius, 0);
}
.derived-name {
  color: var(--sheet-muted, var(--sheet-ink, var(--muted)));
  font-size: 10px; text-transform: uppercase; letter-spacing: .08em;
}
.widget-list { margin: 0; padding-left: 18px; font-size: 13px; }
```

- [ ] **Step 6: Run tests + typecheck**

From `frontend/`: `npx vitest run src/components/SheetWidgets.test.tsx` then `npx tsc -b`.
Expected: PASS, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/SheetWidgets.tsx frontend/src/components/SheetWidgets.test.tsx frontend/src/index.css
git commit -m "feat(frontend): sheet widget library + display types (#165)"
```

---

### Task 6: `SheetLayout.tsx` — tree renderer, default arrangement, theme helper

**Files:**
- Create: `frontend/src/components/SheetLayout.tsx`
- Test: `frontend/src/components/SheetLayout.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `FieldWidget`, `DerivedBadge`, `WidgetMode` from `./SheetWidgets`; `LayoutNode`, `ModuleDetail`, `ModuleField`, `ModuleTheme` from `../api/client`.
- Produces:
  - `export default function SheetLayout({ module, sheetType, mode, values, derived, onChange }: { module: ModuleDetail; sheetType: string; mode: WidgetMode; values: Record<string, unknown>; derived: Record<string, unknown>; onChange?: (key: string, v: unknown) => void })`
  - `export function assembledDefs(module: ModuleDetail, t: string | null): ModuleField[]` — THE flattening helper; Task 7 points SheetEditor/SheetPanel at it.
  - `export function defaultLayout(module: ModuleDetail, tid: string): LayoutNode`
  - `export function themeStyle(theme: ModuleTheme | undefined): CSSProperties` — maps validated tokens to `--sheet-*` inline vars (fonts: `display→var(--fd)`, `body→var(--fb)`, `mono→var(--fm)`, `serif→Georgia, 'Times New Roman', serif`, `sans→system-ui, sans-serif`).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/SheetLayout.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import SheetLayout, { defaultLayout, themeStyle } from "./SheetLayout";
import type { ModuleDetail } from "../api/client";

const MOD: ModuleDetail = {
  id: "pool-basic", source: "builtin",
  manifest: { id: "pool-basic", name: "Pool Basic" },
  sheets: {
    groups: {
      attributes: { label: "Attributes", fields: [
        { key: "vigor", label: "Vigor", type: "dots", max: 5 },
        { key: "wits", label: "Wits", type: "dots", max: 5 },
      ]},
    },
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: ["attributes"],
        fields: [{ key: "essence", label: "Essence", type: "resource", max: 10 },
                 { key: "gear", label: "Gear", type: "list" }],
        derived: { sight_pool: "wits" } },
    },
  },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {
    medium: { column: [
      { group: "attributes", title: "Attributes" },
      { row: [{ fields: ["essence"], title: "Power" },
              { derived: ["sight_pool"], title: "Gifts" }] },
    ]},
  }},
  theme: {}, display_errors: [],
};

const VALUES = { vigor: 3, wits: 2, essence: { current: 6, max: 10 }, gear: ["rope"] };
const DERIVED = { sight_pool: 2 };

test("renders layout tree with titles, widgets, and derived badges", () => {
  const { container } = render(
    <SheetLayout module={MOD} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument();
  expect(screen.getByText("Power")).toBeInTheDocument();
  expect(container.querySelectorAll(".pip").length).toBe(10);  // vigor + wits
  expect(screen.getByText("sight_pool")).toBeInTheDocument();
});

test("unplaced fields land in Other", () => {
  // layout places essence but not gear
  render(<SheetLayout module={MOD} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Other")).toBeInTheDocument();
  expect(screen.getByText("rope")).toBeInTheDocument();
});

test("no layout: default arrangement with widgets and trailing Derived", () => {
  const bare: ModuleDetail = { ...MOD, layout: { sheet_types: {} } };
  const { container } = render(
    <SheetLayout module={bare} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument(); // group title
  expect(screen.getByText("Details")).toBeInTheDocument();    // own fields
  expect(screen.getByText("Derived")).toBeInTheDocument();    // trailing derived
  expect(container.querySelectorAll(".pip").length).toBe(10);
  expect(screen.queryByText("Other")).toBeNull();             // everything placed
});

test("defaultLayout skips groups missing from the module", () => {
  const broken: ModuleDetail = { ...MOD, sheets: { ...MOD.sheets,
    sheet_types: { medium: { ...MOD.sheets.sheet_types.medium, groups: ["attributes", "ghost"] } } } };
  const node = defaultLayout(broken, "medium");
  expect(JSON.stringify(node)).not.toContain("ghost");
});

test("edit mode threads onChange through widgets", () => {
  const onChange = vi.fn();
  render(<SheetLayout module={MOD} sheetType="medium" mode="edit"
                      values={VALUES} derived={DERIVED} onChange={onChange} />);
  screen.getByLabelText("Vigor 4").click();
  expect(onChange).toHaveBeenCalledWith("vigor", 4);
});

test("themeStyle maps tokens to sheet vars", () => {
  expect(themeStyle({ colors: { bg: "#111", ink: "#eee" }, fonts: { body: "serif" } }))
    .toEqual({ "--sheet-bg": "#111", "--sheet-ink": "#eee",
               "--sheet-fb": "Georgia, 'Times New Roman', serif" });
  expect(themeStyle(undefined)).toEqual({});
});
```

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/components/SheetLayout.test.tsx` from `frontend/`; FAIL (module missing).

- [ ] **Step 3: Implement `SheetLayout.tsx`**

```tsx
import type { CSSProperties, ReactNode } from "react";
import type { LayoutNode, ModuleDetail, ModuleField, ModuleTheme } from "../api/client";
import { DerivedBadge, FieldWidget, type WidgetMode } from "./SheetWidgets";

/** Full field-def set (group fields + own fields) for a sheet type — the one
 *  flattening helper; SheetEditor and SheetPanel import it from here. */
export function assembledDefs(module: ModuleDetail, t: string | null): ModuleField[] {
  if (!t) return [];
  const st = module.sheets.sheet_types[t];
  if (!st) return [];
  return st.groups.flatMap((g) => module.sheets.groups[g]?.fields ?? []).concat(st.fields);
}

/** The Phase-3 arrangement (groups in order → own fields) as a layout tree —
 *  one rendering path whether or not the module ships a layout. Places no
 *  derived; the trailing sections pick those up. */
export function defaultLayout(module: ModuleDetail, tid: string): LayoutNode {
  const st = module.sheets.sheet_types[tid];
  const children: LayoutNode[] = (st?.groups ?? [])
    .filter((g) => module.sheets.groups[g])
    .map((g) => ({ group: g, title: module.sheets.groups[g].label ?? g }));
  const own = (st?.fields ?? []).map((f) => f.key);
  if (own.length > 0) children.push({ fields: own, title: "Details" });
  return { column: children };
}

const FONT_VALUES: Record<string, string> = {
  display: "var(--fd)", body: "var(--fb)", mono: "var(--fm)",
  serif: "Georgia, 'Times New Roman', serif", sans: "system-ui, sans-serif",
};

/** Validated theme.json tokens → scoped --sheet-* inline vars. */
export function themeStyle(theme: ModuleTheme | undefined): CSSProperties {
  const s: Record<string, string> = {};
  const c = theme?.colors ?? {};
  if (c.bg) s["--sheet-bg"] = c.bg;
  if (c.ink) s["--sheet-ink"] = c.ink;
  if (c.muted) s["--sheet-muted"] = c.muted;
  if (c.accent) s["--sheet-accent"] = c.accent;
  if (c.rule) s["--sheet-rule"] = c.rule;
  const f = theme?.fonts ?? {};
  if (f.display && FONT_VALUES[f.display]) s["--sheet-fd"] = FONT_VALUES[f.display];
  if (f.body && FONT_VALUES[f.body]) s["--sheet-fb"] = FONT_VALUES[f.body];
  return s as CSSProperties;
}

type Ctx = {
  defs: Map<string, ModuleField>;
  groupFields: (gid: string) => string[];
  values: Record<string, unknown>;
  derived: Record<string, unknown>;
  mode: WidgetMode;
  onChange?: (key: string, v: unknown) => void;
  placedFields: Set<string>;
  placedDerived: Set<string>;
};

function fieldSet(keys: string[], grid: boolean | undefined, ctx: Ctx): ReactNode {
  const widgets = keys.map((k) => {
    ctx.placedFields.add(k);
    const def = ctx.defs.get(k);
    if (!def) return null; // backend-validated; defensive only
    return (
      <FieldWidget key={k} def={def} value={ctx.values[k]} mode={ctx.mode} grid={grid}
                   onChange={ctx.onChange ? (v) => ctx.onChange!(k, v) : undefined} />
    );
  });
  return grid ? <div className="stat-grid">{widgets}</div> : <>{widgets}</>;
}

function badges(names: string[], ctx: Ctx): ReactNode {
  return (
    <div className="derived-badges">
      {names.map((n) => {
        ctx.placedDerived.add(n);
        return <DerivedBadge key={n} name={n} value={ctx.derived[n]} />;
      })}
    </div>
  );
}

function renderNode(node: LayoutNode, ctx: Ctx, key: number): ReactNode {
  let inner: ReactNode = null;
  if (node.row) inner = <div className="sheet-cols">{node.row.map((c, i) => renderNode(c, ctx, i))}</div>;
  else if (node.column) inner = <div className="sheet-stack">{node.column.map((c, i) => renderNode(c, ctx, i))}</div>;
  else if (node.group) inner = fieldSet(ctx.groupFields(node.group), node.grid, ctx);
  else if (node.fields) inner = fieldSet(node.fields, node.grid, ctx);
  else if (node.derived) inner = badges(node.derived, ctx);
  return node.title ? (
    <section className="sheet-panel" key={key}><h4>{node.title}</h4>{inner}</section>
  ) : (
    <div className="sheet-slot" key={key}>{inner}</div>
  );
}

export default function SheetLayout({ module, sheetType, mode, values, derived, onChange }: {
  module: ModuleDetail; sheetType: string; mode: WidgetMode;
  values: Record<string, unknown>; derived: Record<string, unknown>;
  onChange?: (key: string, v: unknown) => void;
}) {
  const tree = module.layout?.sheet_types?.[sheetType] ?? defaultLayout(module, sheetType);
  const ctx: Ctx = {
    defs: new Map(assembledDefs(module, sheetType).map((f) => [f.key, f])),
    groupFields: (gid) => (module.sheets.groups[gid]?.fields ?? []).map((f) => f.key),
    values, derived, mode, onChange,
    placedFields: new Set(), placedDerived: new Set(),
  };
  const body = renderNode(tree, ctx, 0); // eager: populates placed* sets
  const restFields = [...ctx.defs.keys()].filter((k) => !ctx.placedFields.has(k));
  const restDerived = Object.keys(derived).filter((n) => !ctx.placedDerived.has(n));
  return (
    <div className="sheet-arranged">
      {body}
      {restFields.length > 0 && (
        <section className="sheet-panel"><h4>Other</h4>{fieldSet(restFields, undefined, ctx)}</section>
      )}
      {restDerived.length > 0 && (
        <section className="sheet-panel"><h4>Derived</h4>{badges(restDerived, ctx)}</section>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add layout/theme CSS** (append to the Task-5 block in `index.css`):

```css
.sheet-arranged { display: flex; flex-direction: column; gap: 16px; }
.sheet-cols { display: flex; flex-wrap: wrap; gap: 16px; }
.sheet-cols > * { flex: 1 1 240px; min-width: 0; }
.sheet-stack { display: flex; flex-direction: column; gap: 12px; }
.sheet-panel {
  border: 1.5px solid var(--sheet-rule, var(--sheet-ink, var(--rule-soft)));
  border-radius: var(--sheet-radius, 0);
  padding: 10px 12px;
}
.sheet-panel > h4 {
  margin: 0 0 8px; font-family: var(--sheet-fd, var(--fd));
  text-transform: uppercase; font-size: 12px; letter-spacing: .1em;
  color: var(--sheet-muted, var(--sheet-ink, var(--muted)));
}
[data-corners="rounded"] { --sheet-radius: 8px; }
```

- [ ] **Step 5: Run tests + typecheck** — `npx vitest run src/components/SheetLayout.test.tsx` and `npx tsc -b` from `frontend/`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SheetLayout.tsx frontend/src/components/SheetLayout.test.tsx frontend/src/index.css
git commit -m "feat(frontend): sheet layout renderer, default arrangement, theme vars (#165)"
```

---

### Task 7: SheetEditor integration (layout in both modes, theme, hint)

**Files:**
- Modify: `frontend/src/components/SheetEditor.tsx`
- Modify: `frontend/src/components/SheetPanel.tsx`
- Modify: `frontend/src/components/SheetEditor.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `SheetLayout`, `assembledDefs`, `themeStyle` from `./SheetLayout`.
- Produces: SheetEditor renders `SheetLayout` in both modes; the takeover carries `style={themeStyle(module.theme)}`, `data-dots={module.theme?.dots ?? "circle"}`, `data-corners={module.theme?.corners ?? "sharp"}`; dropped-layout hint per the spec routing rule. Save/Cancel/type-change/delete/draft semantics unchanged.

- [ ] **Step 1: Update the tests**

In `SheetEditor.test.tsx`:

1. The existing "view shows groups and derived; edit saves fields" test edits vigor via `getByLabelText("Vigor")` (number input). Dots are now click-to-set pips — replace those two lines with:

```tsx
  fireEvent.click(screen.getByLabelText("Vigor 4"));
```

(the assertion `fields: expect.objectContaining({ vigor: 4 })` stays).

2. Add new tests:

```tsx
test("layout applies in view and edit; same panels both modes", () => {
  const laid: ModuleDetail = { ...MOD, layout: { sheet_types: {
    medium: { column: [{ group: "attributes", title: "Attributes" },
                       { fields: ["essence", "quirk", "gear"], title: "Power" }] } } } };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={laid}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.getByText("Power")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Edit"));
  expect(screen.getByText("Power")).toBeInTheDocument(); // same arrangement in edit
});

test("theme sets vars and data attributes on the takeover", () => {
  const themed: ModuleDetail = { ...MOD,
    theme: { colors: { bg: "#191521", ink: "#d8d2c4" }, dots: "diamond", corners: "sharp" } };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={themed}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  const takeover = screen.getByRole("dialog");
  expect(takeover.getAttribute("data-dots")).toBe("diamond");
  expect(takeover.style.getPropertyValue("--sheet-bg")).toBe("#191521");
});

test("unthemed module sets no sheet vars", () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.getByRole("dialog").style.getPropertyValue("--sheet-bg")).toBe("");
});

test("dropped-layout hint routing", () => {
  const base = { scope: { kind: "campaign", id: "run" } as const, kind: "characters",
                 eid: "mara", initial: SHEET, onClose: () => {}, onSaved: () => {} };
  const HINT = /layout for this sheet type is invalid/;
  // names the current type -> fires
  const dropped: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: "medium", message: "sheet_types.medium: bad" }] };
  const { unmount } = render(<SheetEditor {...base} module={dropped} />);
  expect(screen.getByText(HINT)).toBeInTheDocument();
  unmount();
  // file-level error, no surviving tree -> fires
  const global: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: null, message: "layout.json: must be an object" }] };
  const r2 = render(<SheetEditor {...base} module={global} />);
  expect(screen.getByText(HINT)).toBeInTheDocument();
  r2.unmount();
  // unused-fragment error but current type's layout survived -> does NOT fire
  const survived: ModuleDetail = { ...MOD,
    layout: { sheet_types: { medium: { column: [] } } },
    display_errors: [{ source: "layout", sheet_type: null, message: "fragments.broken: bad" }] };
  const r3 = render(<SheetEditor {...base} module={survived} />);
  expect(r3.queryByText(HINT)).toBeNull();
  r3.unmount();
  // another type's tree dropped, current type never had a layout -> does NOT fire
  const other: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: "shifter", message: "sheet_types.shifter: bad" }] };
  const r4 = render(<SheetEditor {...base} module={other} />);
  expect(r4.queryByText(HINT)).toBeNull();
});
```

(`r3.queryByText` etc.: destructure `queryByText` from each `render` result, or use `screen` with unmounts as shown for the first two.)

- [ ] **Step 2: Run to verify the new tests fail** — `npx vitest run src/components/SheetEditor.test.tsx`; the new tests FAIL, plus the reworked vigor test FAILS (pips don't exist yet in the editor).

- [ ] **Step 3: Rework `SheetEditor.tsx`**

1. Replace imports/helpers: delete local `fieldDefsOf`, `displayValue`, `widget`, `isResource`; add

```tsx
import SheetLayout, { assembledDefs, themeStyle } from "./SheetLayout";
```

`keysOf` becomes `const keysOf = (module: ModuleDetail, t: string) => assembledDefs(module, t).map((f) => f.key);` and every other `fieldDefsOf(...)` call site becomes `assembledDefs(...)` (`toEditDraft`, `normalizeForSave` keep their `defs: ModuleField[]` signatures).

2. Takeover container:

```tsx
      <div className="sheet-takeover" role="dialog" aria-label={typeDef?.label ?? "Sheet"}
           style={themeStyle(module.theme)}
           data-dots={module.theme?.dots ?? "circle"}
           data-corners={module.theme?.corners ?? "sharp"}>
```

3. Dropped-layout hint, computed above the return and rendered after the banners:

```tsx
  const layoutTree = sheetType ? module.layout?.sheet_types?.[sheetType] : undefined;
  const layoutDropped = !!sheetType && !layoutTree && (module.display_errors ?? []).some(
    (e) => e.source === "layout" && (e.sheet_type === sheetType || e.sheet_type === null));
```

```tsx
        {layoutDropped && (
          <div className="field-hint">
            This module's layout for this sheet type is invalid — using the default arrangement.
          </div>
        )}
```

4. Replace the view body (`<div className="sheet-view">…</div>`) with:

```tsx
          <SheetLayout module={module} sheetType={sheetType!} mode="view"
                       values={fields} derived={initial.derived} />
```

and the edit body (`<div className="form">…</div>`) with:

```tsx
          <SheetLayout module={module} sheetType={sheetType!} mode="edit"
                       values={draft} derived={initial.derived} onChange={setField} />
```

(`setField(key, value)` already has that shape.)

5. In `SheetPanel.tsx`, delete the local `sheetFields` helper and `isResource`; import them instead:

```tsx
import { assembledDefs } from "./SheetLayout";
import { isResource } from "./SheetWidgets";
```

(`sheetFields(module, typeId)` call sites become `assembledDefs(module, typeId)`.)

6. In `index.css`, retheme the takeover so the inline `--sheet-*` vars actually take effect — in the existing `.sheet-takeover` rule change `background: var(--surface); color: var(--ink);` to

```css
  background: var(--sheet-bg, var(--surface)); color: var(--sheet-ink, var(--ink));
  font-family: var(--sheet-fb, var(--fb));
```

and change `.sheet-takeover h3`'s `font-family: var(--fd);` to `font-family: var(--sheet-fd, var(--fd));`.

Then retheme the takeover's form controls so no descendant is left on raw app tokens under a themed module — change the `.sheet-takeover select` rule's `background: var(--surface); color: var(--ink); border: var(--rw2) solid var(--rule);` to

```css
  background: var(--sheet-bg, var(--surface)); color: var(--sheet-ink, var(--ink));
  border: var(--rw2) solid var(--sheet-rule, var(--rule));
```

and add alongside it:

```css
.sheet-takeover input, .sheet-takeover textarea {
  background: var(--sheet-bg, var(--surface)); color: var(--sheet-ink, var(--ink));
  border: var(--rw2) solid var(--sheet-rule, var(--rule));
}
```

(These are stylesheet rules; jsdom does not apply stylesheets, so vitest asserts the inline `--sheet-*` vars and data attributes on the container — the cascade itself is verified by eye in the end-state check.)

7. In `index.css`, delete the now-dead rules `.sheet-view { ... }` and `.sheet-row { ... }` (the `.resource-inputs` rules stay — the resource widget reuses them).

- [ ] **Step 4: Run the full frontend suite + typecheck**

From `frontend/`: `npx vitest run` and `npx tsc -b`.
Expected: all PASS — SheetPanel/editor-integration tests included. If an existing test selects dots via `getByLabelText("<label>")` expecting a number input, update it to the pip pattern from Step 1.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SheetEditor.tsx frontend/src/components/SheetPanel.tsx frontend/src/components/SheetEditor.test.tsx frontend/src/index.css
git commit -m "feat(frontend): SheetEditor renders layouts + themes; dropped-layout hint (#165)"
```

---

### Task 8: ModulesView display surfacing

**Files:**
- Modify: `frontend/src/routes/ModulesView.tsx`
- Test: `frontend/src/routes/ModulesView.test.tsx`

**Interfaces:**
- Consumes: `ModuleSummary.display_ok`, `ModuleDetail.layout/theme/display_errors` (Task 5 types).

- [ ] **Step 1: Write the failing tests** (append to `ModulesView.test.tsx`, following its existing mock pattern for `api.listModules`/`api.readModule`; the file's testing-library import does **not** currently include `screen` — extend it to `import { render, screen, fireEvent, ... } from "@testing-library/react"` or rewrite the queries in the file's existing `container`/`within` style)

```tsx
test("list row flags display issues; detail shows Display section", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Pool Basic", description: "", version: "1",
      source: "builtin", valid: true, display_ok: false },
  ]);
  (api.readModule as any).mockResolvedValue({
    ...DETAIL,
    layout: { sheet_types: { medium: { column: [] } } },
    theme: { dots: "diamond" },
    display_errors: [{ source: "layout", sheet_type: "haven", message: "sheet_types.haven: bad" }],
  });
  render(<ModulesView />);
  expect(await screen.findByText("display issues")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Pool Basic"));
  expect(await screen.findByText("Display")).toBeInTheDocument();
  expect(screen.getByText("medium layout")).toBeInTheDocument();
  expect(screen.getByText("theme")).toBeInTheDocument();
  expect(screen.getByText("sheet_types.haven: bad")).toBeInTheDocument();
});
```

(`DETAIL` = the file's existing detail fixture; reuse whatever it is named. If the file has no detail fixture, build a minimal `ModuleDetail` like the SheetEditor test's `MOD`.)

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/routes/ModulesView.test.tsx`.

- [ ] **Step 3: Implement**

1. List row (inside the row `<button>`, after `{m.name}`):

```tsx
            {m.display_ok === false && <span className="field-hint"> · display issues</span>}
```

2. Sidebar, after the Rules section and before Problems:

```tsx
              {(Object.keys(detail.layout?.sheet_types ?? {}).length > 0
                || Object.keys(detail.theme ?? {}).length > 0
                || (detail.display_errors ?? []).length > 0) && (
                <div className="side-section">
                  <h4>Display</h4>
                  <div className="chips">
                    {Object.keys(detail.layout?.sheet_types ?? {}).map((tid) => (
                      <span key={tid} className="chip on">{tid} layout</span>
                    ))}
                    {Object.keys(detail.theme ?? {}).length > 0 && (
                      <span className="chip on">theme</span>
                    )}
                  </div>
                  {(detail.display_errors ?? []).map((e, i) => (
                    <div key={i} className="field-hint">{e.message}</div>
                  ))}
                </div>
              )}
```

- [ ] **Step 4: Run the frontend suite + typecheck** — `npx vitest run` and `npx tsc -b` from `frontend/`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ModulesView.tsx frontend/src/routes/ModulesView.test.tsx
git commit -m "feat(frontend): module library surfaces layouts, themes, display errors (#165)"
```

---

### Task 9: Authoring-skill update + full verification

**Files:**
- Modify: `.claude/skills/create-mechanics-module/SKILL.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Add a display-files step to the skill**

After the `### 7. Optional: statted content` section, insert:

```markdown
### 7b. Optional: `layout.json` + `theme.json` — pretty rendering

Both cosmetic and optional; problems land in the pack's `display_errors`
(shown in the module library) and never invalidate the module.

`layout.json` arranges each sheet type's widgets. A node has exactly one of
`row` (array of nodes, horizontal), `column` (array, vertical),
`group: "<gid>"`, `fields: ["<key>", ...]`, `derived: ["<name>", ...]`, or
`use: "<fragment id>"`; optional `title` (panel heading) on any node and
`grid: true` (stat-grid cells) on `group`/`fields` nodes. Shared
`fragments` keep groups rendering identically across sheet types:

    {
      "fragments": {"traits": {"group": "attributes", "grid": true, "title": "Attributes"}},
      "sheet_types": {"warden": {"column": [{"use": "traits"}, {"fields": ["essence"], "title": "Power"}]}}
    }

Every field/derived may be placed at most once per sheet type; anything
unplaced renders in a trailing "Other" section, so partial layouts are fine.

`theme.json` is a token whitelist (never CSS — `theme.css` is rejected):
`colors` (`bg`+`ink` must be set together, plus `muted`/`accent`/`rule`,
hex only), `fonts` (`display`/`body`, each one of `display`, `body`,
`mono`, `serif`, `sans`), `dots` (`circle`/`square`/`diamond`), `corners`
(`sharp`/`rounded`):

    {"colors": {"bg": "#191521", "ink": "#d8d2c4"}, "fonts": {"body": "serif"}, "dots": "diamond"}

Validate as in step 8 and check `display_errors` is empty in the pack
payload (`/api/modules/<mid>`).
```

Also add to the `## Common mistakes` list:

```markdown
- Placing the same field in two layout nodes — each field/derived name may
  appear once per sheet type; the second placement is a display error.
- Setting `colors.bg` without `colors.ink` (or vice versa) — they only
  apply as a pair, so a lone one is dropped.
```

- [ ] **Step 2: Full verification**

```bash
PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend -q
cd frontend && npx vitest run && npx tsc -b
```
Expected: everything PASS.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/create-mechanics-module/SKILL.md
git commit -m "docs(skill): layout.json + theme.json authoring step (#165)"
```

---

## Spec-coverage checklist (self-review)

- Widgets per field type incl. view/edit + click-to-set semantics — Task 5.
- `layout.json` node model, total schema, fragments/splicing, caps, at-most-once, per-type drop granularity — Tasks 1, 6.
- `theme.json` whitelist, bg/ink pairing, fallback chain, data attributes, no authored CSS — Tasks 2, 5, 6, 7.
- `display_errors` structured channel, non-fatal, `display_ok`, theme.css detection — Tasks 2, 3.
- Layout in both editor modes, one rendering path, trailing Other/Derived, dropped-layout hint routing — Tasks 6, 7.
- Library surfacing (rows + Display section) — Task 8.
- Reference modules exercise every node form/theme key — Task 4.
- Authoring skill update — Task 9.
- Out of scope honored: no SheetPanel redesign (only the helper import moves), no print/export, no view-mode quick-adjust.
