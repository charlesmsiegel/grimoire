# Mechanics Phase 1 — Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the mechanics module data contract: pack loader/validator, expression evaluator, world/campaign binding with resolver, module routes + read-only library UI, two built-in reference modules, and the `create-mechanics-module` skill.

**Architecture:** Modules are declarative data packs (no code plugins). Built-ins live in-repo at `backend/src/grimoire/store/builtin_modules/<mid>/`; user modules at `<GRIMOIRE_HOME>/modules/<mid>/`. `store/modules.py` loads/validates packs and resolves the campaign binding (campaign tri-state overriding world default). `store/expressions.py` is a whitelisted-AST evaluator. Spec: `docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md`.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend). Pure stdlib for the new store modules.

## Global Constraints

- **Privacy:** invented names only in code/tests/fixtures/docs (reuse placeholders like Seraphine, Mara, Saltmarch; module fixture names are invented: `d20-basic`, `pool-basic`, sheet types like `warden`, content like "Lantern of Winnowing"). Never a real world/campaign/character name.
- **Android rules:** pydantic usage stays v1/v2-agnostic (plain `BaseModel` scalar fields, dump via `routes._dump`, no `Field`/validators/`ConfigDict`); new store modules are pure stdlib; filesystem access via `store.paths.home()` or the `GRIMOIRE_MODULES`-override pattern (mirror `prompts.templates_dir()`).
- **Frontmatter is string-scalar only** (`store/frontmatter.py`): list-ish values are comma-joined strings (like entity `keys`); booleans are `"true"`.
- **Run tests:** backend `backend/.venv/Scripts/python.exe -m pytest backend -q`; frontend **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend vitest run`).
- **Worktree:** execute on branch `mechanics-modules-phase1` in `.worktrees/mechanics-modules-phase1` (create via superpowers:using-git-worktrees). In the worktree, backend tests need `PYTHONPATH` to shadow the editable install (`PYTHONPATH=backend/src` relative to the worktree root), and `frontend/` needs its own `npm install` before vitest/tsc.
- **Route ordering:** campaign-scoped specific routes (`/campaigns/{cid}/module`) must be registered **before** the generic `/campaigns/{cid}/{kind}` entity routes (which otherwise swallow them). Same rule already applies to `/campaigns/{cid}/rolls` (routes.py comment near line 2161).
- Commit after every task with a conventional message; run the relevant tests before each commit.

## GitHub issues (for landing)

- **Resolves #160** (Pluggable per-campaign mechanics modules with `null` fall-through). Closing comment must note the settled approach *diverges from the issue body's Option A* (calendar-style code registry): modules are declarative data packs with a safe expression language — no code plugins — and binding is world-default + campaign tri-state override, per the 2026-07-12 spec.
- **Comment (keep open):** #161 (sheets → Phase 3 draft spec), #162 (pre-roll proposals → Phase 4 draft), #163 (narrated-event validation → Phase 5 draft), #164 (content/wizard → Phase 7 draft), #165 (widgets/theme → Phase 6 draft), #166 (authoring UI → Phase 8 draft; interim path is the `create-mechanics-module` skill), #221 (game-mechanical entity fields: addressed via sheets contract, not typed fields — re-scope), #201 (Mechanics view: module binding UI ships here; sheets/bulk-create in Phase 3), #222 (ref-valued fields: intersects Phase 7 draft).

## File structure

- Create: `backend/src/grimoire/store/expressions.py` — expression parse/validate/evaluate (no deps on other store modules).
- Create: `backend/src/grimoire/store/modules.py` — pack loading, validation, registry (builtin+user), scaffold/delete, binding setters, `resolve()`.
- Create: `backend/src/grimoire/store/builtin_modules/{d20-basic,pool-basic}/…` — reference packs (data only).
- Modify: `backend/src/grimoire/store/campaigns.py` (create gains `module` param), `backend/src/grimoire/routes.py` (models + endpoints).
- Create: `backend/tests/test_expressions.py`, `backend/tests/test_modules_store.py`; Modify: `backend/tests/test_routes.py`.
- Modify: `frontend/src/api/client.ts` (types + api fns), `frontend/src/App.tsx` (+ test), `frontend/src/routes/CampaignWizard.tsx` (+ test), `frontend/src/routes/CampaignView.tsx`, `frontend/src/routes/WorldView.tsx`.
- Create: `frontend/src/routes/ModulesView.tsx` (+ test), `frontend/src/components/MechanicsConfig.tsx` (+ test), `frontend/src/components/WorldMechanics.tsx` (+ test).
- Create: `.claude/skills/create-mechanics-module/SKILL.md`.

---

### Task 1: Expression evaluator (`store/expressions.py`)

**Files:**
- Create: `backend/src/grimoire/store/expressions.py`
- Test: `backend/tests/test_expressions.py`

**Interfaces:**
- Produces: `ExpressionError(ValueError)`; `parse(text: str) -> ast.Expression` (raises `ExpressionError`); `names(text: str) -> set[str]`; `evaluate(text: str, scope: dict[str, int | float]) -> int | float` (raises `ExpressionError` on unknown name at eval time).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_expressions.py
import pytest

from grimoire.store import expressions


@pytest.mark.parametrize(
    "text,scope,expected",
    [
        ("1 + 2 * 3", {}, 7),
        ("floor((strength - 10) / 2)", {"strength": 15}, 2),
        ("floor((strength - 10) / 2)", {"strength": 9}, -1),
        ("min(dex, 5) + max(brawl, 1)", {"dex": 7, "brawl": 0}, 6),
        ("ceil(essence / 2)", {"essence": 5}, 3),
        ("abs(0 - hp)", {"hp": 4}, 4),
        ("hp_max - hp", {"hp": 3, "hp_max": 10}, 7),
        ("10 // 3", {}, 3),
        ("-vigor + 2", {"vigor": 3}, -1),
        ("2 if wits > 3 else 1", {"wits": 5}, 2),
        ("2 if wits > 3 else 1", {"wits": 2}, 1),
        ("wits > 2 and brawl > 0", {"wits": 3, "brawl": 1}, True),
        ("not (wits > 2)", {"wits": 1}, True),
    ],
)
def test_evaluate(text, scope, expected):
    assert expressions.evaluate(text, scope) == expected


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",          # call to non-whitelisted name
        "a.b",                        # attribute access
        "a[0]",                       # subscript
        "[x for x in y]",             # comprehension
        "lambda: 1",                  # lambda
        "'s'",                        # string literal
        "f'{a}'",                     # f-string
        "pow(2, 3)",                  # non-whitelisted call
        "a ** 2",                     # power operator (not whitelisted)
        "a % 2",                      # modulo (not whitelisted)
        "(1,)",                       # tuple
        "{1: 2}",                     # dict
        "a := 1",                     # walrus
        "1; 2",                       # not a single expression
        "def f(): pass",              # statement
        "",                           # empty
    ],
)
def test_rejects_forbidden(text):
    with pytest.raises(expressions.ExpressionError):
        expressions.parse(text)


def test_names():
    assert expressions.names("dex + min(brawl, 5)") == {"dex", "brawl"}


def test_unknown_name_at_eval():
    with pytest.raises(expressions.ExpressionError, match="unknown name"):
        expressions.evaluate("dex + 1", {})


def test_parse_error_names_construct():
    with pytest.raises(expressions.ExpressionError, match="Attribute"):
        expressions.parse("a.b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_expressions.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (expressions does not exist).

- [ ] **Step 3: Write the implementation**

```python
# backend/src/grimoire/store/expressions.py
"""Safe expression evaluator for mechanics modules (#160).

A whitelisted subset of Python expressions parsed via ``ast`` -- never
``eval`` on raw text. Serves sheet derived fields, check roll formulas, and
(later) creation budgets. Pure stdlib: no filesystem, no pydantic.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md.
"""

from __future__ import annotations

import ast
import math


class ExpressionError(ValueError):
    """Unparseable, forbidden, or unevaluable expression."""


_FUNCS = {"min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "abs": abs}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.USub, ast.UAdd,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def parse(text: str) -> ast.Expression:
    """Parse ``text`` into a validated ast.Expression or raise ExpressionError."""
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError) as e:
        raise ExpressionError(f"unparseable expression {text!r}: {e}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"forbidden construct {type(node).__name__} in {text!r}"
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ExpressionError(f"non-numeric literal in {text!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExpressionError(f"forbidden call in {text!r}")
            if node.keywords:
                raise ExpressionError(f"keyword arguments not allowed in {text!r}")
    return tree


def names(text: str) -> set[str]:
    """Field names referenced by the expression (call names excluded)."""
    tree = parse(text)
    return {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id not in _FUNCS
    }


def _eval(node: ast.AST, scope: dict) -> int | float | bool:
    if isinstance(node, ast.Expression):
        return _eval(node.body, scope)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise ExpressionError(f"unknown name {node.id!r}")
        return scope[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, scope)
        return -v if isinstance(node.op, ast.USub) else (not v if isinstance(node.op, ast.Not) else +v)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, scope), _eval(node.right, scope)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        return a // b  # FloorDiv (only remaining allowed BinOp)
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, scope) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, scope)
            ok = (
                left == right if isinstance(op, ast.Eq)
                else left != right if isinstance(op, ast.NotEq)
                else left < right if isinstance(op, ast.Lt)
                else left <= right if isinstance(op, ast.LtE)
                else left > right if isinstance(op, ast.Gt)
                else left >= right
            )
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, scope) if _eval(node.test, scope) else _eval(node.orelse, scope)
    if isinstance(node, ast.Call):
        args = [_eval(a, scope) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ExpressionError(f"unhandled node {type(node).__name__}")  # unreachable


def evaluate(text: str, scope: dict[str, int | float]) -> int | float | bool:
    return _eval(parse(text), scope)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_expressions.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/expressions.py backend/tests/test_expressions.py
git commit -m "feat(expressions): whitelisted-AST safe expression evaluator (#160)"
```

---

### Task 2: Pack loading + sheets validation (`store/modules.py`, part 1)

**Files:**
- Create: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: `ModuleError(Exception)`, `ModuleNotFound(Exception)`; `FIELD_TYPES`, `SHEET_KINDS`; `builtin_dir() -> Path`, `user_dir() -> Path`; `pack_root(mid) -> Path` (raises `ModuleNotFound`); `load_pack(mid) -> dict` returning `{"id", "source", "manifest", "sheets", "checks", "rules", "content", "errors"}`; `assembled_fields(sheets: dict, type_id: str) -> list[dict]`; `numeric_names(fields: list[dict]) -> set[str]`.
- Consumes: `expressions.parse/names` (Task 1), `frontmatter.parse_frontmatter`, `paths.home`, `dice.parse`.

- [ ] **Step 1: Write the failing tests**

Tests build throwaway packs on disk under `tmp_path/"modules"/<mid>` with `GRIMOIRE_HOME` pointed at `tmp_path` (user-library location), via a helper:

```python
# backend/tests/test_modules_store.py
import json

import pytest

from grimoire.store import modules


GOOD_SHEETS = {
    "groups": {
        "attributes": {
            "label": "Attributes",
            "fields": [
                {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
                {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1},
            ],
            "derived": {"reflex": "min(vigor, wits)"},
        },
    },
    "sheet_types": {
        "warden": {
            "label": "Warden",
            "kind": "characters",
            "groups": ["attributes"],
            "fields": [{"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
            "derived": {"surge": "reflex + essence_max - essence"},
        },
    },
}


def make_pack(root, mid="testmod", sheets=None, manifest=None, checks=None,
              rules=None, content=None):
    d = root / "modules" / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text(
        manifest
        or "---\nname: Test Module\ndescription: fixture\nversion: 0.1\ndice: 1d20\n---\nnotes\n",
        encoding="utf-8",
    )
    (d / "sheets.json").write_text(
        json.dumps(sheets if sheets is not None else GOOD_SHEETS), encoding="utf-8"
    )
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    if rules:
        rd = d / "rules"
        rd.mkdir()
        for name, text in rules.items():
            (rd / f"{name}.md").write_text(text, encoding="utf-8")
    if content:
        for rel, text in content.items():
            p = d / "content" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return d


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_load_good_pack(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert pack["manifest"]["name"] == "Test Module"
    assert pack["source"] == "user"
    assert "warden" in pack["sheets"]["sheet_types"]


def test_missing_module(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleNotFound):
        modules.load_pack("nope")


def test_manifest_requires_name(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path), manifest="---\nversion: 1\n---\n")
    assert any("name" in e for e in modules.load_pack("testmod")["errors"])


def test_manifest_bad_dice(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              manifest="---\nname: X\ndice: 1dbanana\n---\n")
    assert any("dice" in e for e in modules.load_pack("testmod")["errors"])


def _sheets_error(monkeypatch, tmp_path, mutate):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    mutate(sheets)
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets)
    return modules.load_pack("testmod")["errors"]


def test_unknown_group_ref(monkeypatch, tmp_path):
    errs = _sheets_error(monkeypatch, tmp_path,
                         lambda s: s["sheet_types"]["warden"]["groups"].append("ghost"))
    assert any("ghost" in e for e in errs)


def test_unknown_kind(monkeypatch, tmp_path):
    errs = _sheets_error(monkeypatch, tmp_path,
                         lambda s: s["sheet_types"]["warden"].update(kind="vehicles"))
    assert any("vehicles" in e for e in errs)


def test_duplicate_field_key(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["fields"].append(
            {"key": "vigor", "type": "number"}))
    assert any("duplicate" in e for e in errs)


def test_derived_collides_with_field(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["derived"].update(vigor="1"))
    assert any("collide" in e for e in errs)


def test_bad_field_type(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "aura", "type": "sparkles"}))
    assert any("sparkles" in e for e in errs)


def test_dots_requires_max(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "aura", "type": "dots"}))
    assert any("max" in e for e in errs)


def test_derived_unparseable(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update(bad="a.b"))
    assert any("bad" in e for e in errs)


def test_derived_unknown_name(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update(bad="charm + 1"))
    assert any("charm" in e for e in errs)


def test_sheets_json_invalid_json(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "sheets.json").write_text("{nope", encoding="utf-8")
    assert any("sheets.json" in e for e in modules.load_pack("testmod")["errors"])


def test_assembled_fields_and_numeric_names():
    fields = modules.assembled_fields(GOOD_SHEETS, "warden")
    assert [f["key"] for f in fields] == ["vigor", "wits", "essence"]
    assert modules.numeric_names(fields) == {"vigor", "wits", "essence", "essence_max"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: FAIL — `ImportError` (modules does not exist).

- [ ] **Step 3: Write the implementation**

```python
# backend/src/grimoire/store/modules.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): pack loading + sheets.json validation (#160)"
```

---

### Task 3: Checks, rules, and content validation (`store/modules.py`, part 2)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py` (append)

**Interfaces:**
- Produces: `load_pack` now also fills `checks` (dict id→def), `rules` (list of `{"id", "keys": [..], "always": bool, "on_roll": bool, "sheet_types": [..]}`), `content` (list of `{"kind", "id", "name", "sheet_type": str|None}`), with corresponding `errors`. Also `validate_sheet_values(sheets: dict, type_id: str, values: dict) -> list[str]` (reused by Phase 3 for campaign sheets).
- Consumes: Task 2's helpers.

- [ ] **Step 1: Write the failing tests (append to test_modules_store.py)**

```python
GOOD_CHECKS = {
    "surge_check": {
        "label": "Surge",
        "roll": "{reflex + essence}d10 t6",
        "requires": ["attributes"],
        "rules": ["combat"],
    },
}
# NOTE: "essence" is a sheet-type field, not in group "attributes" — see
# test_check_names_must_come_from_required_groups below; use "vigor + wits"
# for the passing case.
GOOD_CHECKS["surge_check"]["roll"] = "{vigor + wits}d10 t6"

RULES = {
    "core": "---\nalways: true\n---\nCore rules.\n",
    "combat": "---\nkeys: fight, attack\non_roll: true\n---\nCombat.\n",
    "warden-arts": "---\nsheet_types: warden\n---\nWarden arts.\n",
}


def test_good_checks_and_rules(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path), checks=GOOD_CHECKS, rules=RULES)
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert "surge_check" in pack["checks"]
    by_id = {r["id"]: r for r in pack["rules"]}
    assert by_id["core"]["always"] is True
    assert by_id["combat"]["keys"] == ["fight", "attack"]
    assert by_id["combat"]["on_roll"] is True
    assert by_id["warden-arts"]["sheet_types"] == ["warden"]


def test_check_unknown_required_group(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": ["ghost"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_check_placeholder_unknown_name(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20 + {charm}", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("charm" in e for e in modules.load_pack("testmod")["errors"])


def test_check_names_must_come_from_required_groups(monkeypatch, tmp_path):
    # essence is a warden sheet-type field, not in group "attributes"
    checks = {"c": {"label": "C", "roll": "{essence}d10 t6", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("essence" in e for e in modules.load_pack("testmod")["errors"])


def test_check_template_must_parse_as_dice(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "banana + {vigor}", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("dice" in e.lower() for e in modules.load_pack("testmod")["errors"])


def test_check_unknown_rules_doc(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": [], "rules": ["ghost"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_rules_unknown_sheet_type(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              rules={"x": "---\nsheet_types: ghost\n---\nX.\n"})
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_content_with_stat_sidecar(monkeypatch, tmp_path):
    content = {
        "items/lantern.md": "---\nname: Lantern of Winnowing\n---\nA lantern.\n",
        "items/lantern.sheet.json": json.dumps(
            {"sheet_type": "warden", "fields": {"vigor": 3}}),
    }
    # warden targets characters, not items -> kind mismatch error
    make_pack(_home(monkeypatch, tmp_path), content=content)
    assert any("kind" in e for e in modules.load_pack("testmod")["errors"])


def test_content_bad_kind_dir(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              content={"vehicles/cart.md": "---\nname: Cart\n---\n"})
    assert any("vehicles" in e for e in modules.load_pack("testmod")["errors"])


def test_validate_sheet_values():
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden",
                                         {"vigor": 3, "essence": {"current": 4, "max": 10}})
    assert errs == []
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"ghost": 1})
    assert any("ghost" in e for e in errs)
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"vigor": 9})
    assert any("max" in e for e in errs)          # dots over max
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"essence": 4})
    assert any("current/max" in e for e in errs)  # resource needs a pair
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: new tests FAIL (checks/rules/content remain empty; `validate_sheet_values` missing). Task-2 tests still PASS.

- [ ] **Step 3: Implement (append to modules.py; call the new loaders inside `load_pack` before building `pack`)**

```python
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _validate_checks(checks: dict, sheets: dict, rule_ids: set[str],
                     errors: list[str]) -> None:
    groups = sheets.get("groups", {})
    for cid, check in checks.items():
        where = f"checks.{cid}"
        if not check.get("label"):
            errors.append(f"{where}: missing label")
        scope: set[str] = set()
        for gid in check.get("requires", []):
            if gid not in groups:
                errors.append(f"{where}: unknown required group {gid!r}")
                continue
            scope |= numeric_names(groups[gid].get("fields", []))
            scope |= set(groups[gid].get("derived", {}))
        roll = check.get("roll", "")
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
        for rid in check.get("rules", []):
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
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
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
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            entry = {"kind": kind, "id": p.stem,
                     "name": meta.get("name", p.stem), "sheet_type": None}
            sidecar = kind_dir / f"{p.stem}.sheet.json"
            if sidecar.exists():
                where = f"content/{kind}/{p.stem}.sheet.json"
                try:
                    stat = json.loads(sidecar.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    errors.append(f"{where}: {e}")
                    stat = {}
                tid = stat.get("sheet_type")
                if tid not in type_defs:
                    errors.append(f"{where}: unknown sheet type {tid!r}")
                elif type_defs[tid].get("kind") != kind:
                    errors.append(
                        f"{where}: sheet type {tid!r} targets kind "
                        f"{type_defs[tid].get('kind')!r}, not {kind!r}")
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
    errors: list[str] = []
    fields = {f["key"]: f for f in assembled_fields(sheets, type_id)}
    for key, value in values.items():
        f = fields.get(key)
        if f is None:
            errors.append(f"{key}: not a field of sheet type {type_id!r}")
            continue
        t = f["type"]
        if t == "resource":
            if (not isinstance(value, dict)
                    or not isinstance(value.get("current"), int)
                    or not isinstance(value.get("max"), int)):
                errors.append(f"{key}: resource needs a current/max pair")
        elif t in ("number", "dots", "track"):
            if not isinstance(value, int):
                errors.append(f"{key}: expected an integer")
            elif t in ("dots", "track") and not 0 <= value <= f["max"]:
                errors.append(f"{key}: outside 0..max")
            elif t == "number" and (
                    ("min" in f and value < f["min"])
                    or ("max" in f and value > f["max"])):
                errors.append(f"{key}: outside min/max")
        elif t == "text":
            if not isinstance(value, str):
                errors.append(f"{key}: expected a string")
        elif t == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{key}: expected a list of strings")
    return errors
```

And inside `load_pack`, after sheets validation, replace the `pack = {...}` block's placeholders:

```python
    rules = _load_rules(root, sheets, errors)
    checks: dict = {}
    cp = root / "checks.json"
    if cp.exists():
        try:
            checks = json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"checks.json: {e}")
            checks = {}
        else:
            _validate_checks(checks, sheets, {r["id"] for r in rules}, errors)
    content = _load_content(root, sheets, errors)
```

…and use `"checks": checks, "rules": rules, "content": content` in the returned dict.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): checks/rules/content validation + sheet-value validator (#160)"
```

---

### Task 4: Registry — list, scaffold, delete (`store/modules.py`, part 3)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py` (append)

**Interfaces:**
- Produces: `list_modules() -> list[dict]` (`{"id","name","description","version","source","valid"}`, user shadows builtin, sorted by name); `create_module(name: str) -> str` (scaffolds minimal valid pack in `user_dir()`, id = `uniquify(slugify(name))`); `delete_module(mid)` (user only; `ModuleError` on builtin, `ModuleNotFound` if absent).
- Consumes: `paths.slugify/uniquify`, `shutil.rmtree`.

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_list_create_delete(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("Homebrew Nights")
    assert mid == "homebrew-nights"
    listed = {m["id"]: m for m in modules.list_modules()}
    assert listed[mid]["source"] == "user"
    assert listed[mid]["valid"] is True
    # built-ins present alongside (d20-basic/pool-basic land in Task 5;
    # here just assert the user module lists)
    modules.delete_module(mid)
    assert mid not in {m["id"] for m in modules.list_modules()}
    with pytest.raises(modules.ModuleNotFound):
        modules.delete_module(mid)


def test_scaffold_is_valid(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("Fresh")
    assert modules.load_pack(mid)["errors"] == []


def test_delete_builtin_refused(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    # simulate a builtin by pointing GRIMOIRE_MODULES at a temp dir
    b = tmp_path / "builtins" / "stock"
    b.mkdir(parents=True)
    (b / "module.md").write_text("---\nname: Stock\n---\n", encoding="utf-8")
    (b / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_MODULES", str(tmp_path / "builtins"))
    assert modules.load_pack("stock")["source"] == "builtin"
    with pytest.raises(modules.ModuleError):
        modules.delete_module("stock")
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: new tests FAIL with `AttributeError` (functions missing).

- [ ] **Step 3: Implement (append)**

```python
import shutil

from .paths import slugify, uniquify


def _scan(d: Path, source: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not d.is_dir():
        return out
    for p in sorted(q for q in d.iterdir() if (q / "module.md").exists()):
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
    merged = _scan(builtin_dir(), "builtin")
    merged.update(_scan(user_dir(), "user"))
    return sorted(merged.values(), key=lambda m: m["name"].lower())


def create_module(name: str) -> str:
    mid = uniquify(slugify(name), lambda i: (user_dir() / i).exists()
                   or (builtin_dir() / i / "module.md").exists())
    d = user_dir() / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text(
        f"---\nname: {name}\ndescription: \nversion: 0.1\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(
        '{\n  "groups": {},\n  "sheet_types": {}\n}\n', encoding="utf-8")
    return mid


def delete_module(mid: str) -> None:
    root, source = pack_root(mid)
    if source != "user":
        raise ModuleError("built-in modules cannot be deleted")
    shutil.rmtree(root)
```

Check `uniquify`'s signature in `store/paths.py` (`uniquify(base_id, exists)`) and match its callable contract. Move the `import shutil` / `from .paths import slugify, uniquify` lines into the existing import block at the top of the file.

- [ ] **Step 4: Run tests** — all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): registry list/scaffold/delete with user-over-builtin merge (#160)"
```

---

### Task 5: Built-in reference modules (`d20-basic`, `pool-basic`)

**Files:**
- Create: `backend/src/grimoire/store/builtin_modules/d20-basic/{module.md,sheets.json,checks.json,rules/*.md,content/items/*.md,content/items/*.sheet.json}`
- Create: `backend/src/grimoire/store/builtin_modules/pool-basic/{…same shape…}`
- Test: `backend/tests/test_modules_store.py` (append)

Both packs must exercise: ≥2 character sheet types, ≥1 non-character sheet type, shared groups, group- and type-level derived, every rules activation flag, a check-linked rules doc, and ≥1 statted content entry. All names invented.

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_builtin_reference_modules_validate(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)  # built-ins resolve package-relative
    for mid in ("d20-basic", "pool-basic"):
        pack = modules.load_pack(mid)
        assert pack["errors"] == [], f"{mid}: {pack['errors']}"
        assert pack["source"] == "builtin"
        kinds = {t["kind"] for t in pack["sheets"]["sheet_types"].values()}
        char_types = [t for t in pack["sheets"]["sheet_types"].values()
                      if t["kind"] == "characters"]
        assert len(char_types) >= 2
        assert kinds - {"characters"}          # at least one non-character type
        assert pack["checks"]
        flags = {f for r in pack["rules"]
                 for f in ("always", "on_roll") if r[f]}
        assert flags == {"always", "on_roll"}
        assert any(r["keys"] for r in pack["rules"])
        assert any(r["sheet_types"] for r in pack["rules"])
        assert any(c["sheet_type"] for c in pack["content"])
    assert {m["id"] for m in modules.list_modules()} >= {"d20-basic", "pool-basic"}
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFound: d20-basic`).

- [ ] **Step 3: Write the packs**

`d20-basic/module.md`:
```markdown
---
name: Basic d20
description: Flat d20 + modifiers against a difficulty class.
version: 0.1
dice: 1d20
---
Reference module proving the flat-roll shape of the contract.
```

`d20-basic/sheets.json`:
```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "strength", "label": "Strength", "type": "number", "default": 10, "min": 1, "max": 20},
        {"key": "dexterity", "label": "Dexterity", "type": "number", "default": 10, "min": 1, "max": 20},
        {"key": "mind", "label": "Mind", "type": "number", "default": 10, "min": 1, "max": 20}
      ],
      "derived": {
        "str_mod": "floor((strength - 10) / 2)",
        "dex_mod": "floor((dexterity - 10) / 2)",
        "mind_mod": "floor((mind - 10) / 2)"
      }
    },
    "skills": {
      "label": "Skills",
      "fields": [
        {"key": "athletics", "label": "Athletics", "type": "number", "default": 0},
        {"key": "stealth", "label": "Stealth", "type": "number", "default": 0},
        {"key": "arcana", "label": "Arcana", "type": "number", "default": 0}
      ]
    }
  },
  "sheet_types": {
    "warrior": {
      "label": "Warrior",
      "kind": "characters",
      "groups": ["attributes", "skills"],
      "fields": [
        {"key": "hp", "label": "Hit Points", "type": "resource", "max": 12},
        {"key": "gear", "label": "Gear", "type": "list"}
      ],
      "derived": {"melee_bonus": "str_mod + 2"}
    },
    "adept": {
      "label": "Adept",
      "kind": "characters",
      "groups": ["attributes", "skills"],
      "fields": [
        {"key": "hp", "label": "Hit Points", "type": "resource", "max": 8},
        {"key": "spell_slots", "label": "Spell Slots", "type": "track", "max": 4}
      ],
      "derived": {"spell_bonus": "mind_mod + 2"}
    },
    "wondrous-item": {
      "label": "Wondrous Item",
      "kind": "items",
      "groups": [],
      "fields": [
        {"key": "charges", "label": "Charges", "type": "resource", "max": 3},
        {"key": "bonus", "label": "Bonus", "type": "number", "default": 1},
        {"key": "quirk", "label": "Quirk", "type": "text"}
      ]
    }
  }
}
```

`d20-basic/checks.json`:
```json
{
  "athletics": {
    "label": "Athletics",
    "roll": "1d20 + {athletics + str_mod}",
    "requires": ["attributes", "skills"],
    "rules": ["skill-checks"]
  },
  "stealth": {
    "label": "Stealth",
    "roll": "1d20 + {stealth + dex_mod}",
    "requires": ["attributes", "skills"]
  }
}
```

`d20-basic/rules/core.md`:
```markdown
---
always: true
---
Rolls are 1d20 plus a modifier against a difficulty class (DC). Meeting or
beating the DC succeeds.
```

`d20-basic/rules/skill-checks.md`:
```markdown
---
keys: climb, sneak, search
---
Skill checks add attribute modifier plus skill ranks. Typical DCs: easy 10,
hard 15, heroic 20.
```

`d20-basic/rules/crits.md`:
```markdown
---
on_roll: true
---
A natural 20 is a critical success; a natural 1 is a critical failure,
regardless of modifiers.
```

`d20-basic/rules/adept-magic.md`:
```markdown
---
sheet_types: adept
keys: spell, magic
---
Adepts cast by spending spell slots; a cast is Mind-based (spell_bonus vs DC).
```

`d20-basic/content/items/lantern-of-winnowing.md`:
```markdown
---
name: Lantern of Winnowing
---
A brass lantern whose light clings to whatever last lied to its bearer.
```

`d20-basic/content/items/lantern-of-winnowing.sheet.json`:
```json
{
  "sheet_type": "wondrous-item",
  "fields": {
    "charges": {"current": 3, "max": 3},
    "bonus": 1,
    "quirk": "hums faintly near falsehoods"
  }
}
```

`pool-basic/module.md`:
```markdown
---
name: Basic Pool
description: d10 dice pools against a target number, successes counted.
version: 0.1
dice: 5d10 t6
---
Reference module proving the dice-pool shape of the contract.
```

`pool-basic/sheets.json`:
```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
        {"key": "grace", "label": "Grace", "type": "dots", "max": 5, "default": 1},
        {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1}
      ]
    },
    "abilities": {
      "label": "Abilities",
      "fields": [
        {"key": "brawl", "label": "Brawl", "type": "dots", "max": 5, "default": 0},
        {"key": "shadowing", "label": "Shadowing", "type": "dots", "max": 5, "default": 0},
        {"key": "occult", "label": "Occult", "type": "dots", "max": 5, "default": 0}
      ]
    }
  },
  "sheet_types": {
    "medium": {
      "label": "Medium",
      "kind": "characters",
      "groups": ["attributes", "abilities"],
      "fields": [
        {"key": "essence", "label": "Essence", "type": "resource", "max": 10},
        {"key": "health", "label": "Health", "type": "track", "max": 7}
      ],
      "derived": {"sight_pool": "wits + occult"}
    },
    "shifter": {
      "label": "Shifter",
      "kind": "characters",
      "groups": ["attributes", "abilities"],
      "fields": [
        {"key": "fury", "label": "Fury", "type": "resource", "max": 5},
        {"key": "health", "label": "Health", "type": "track", "max": 7}
      ],
      "derived": {"claw_pool": "vigor + brawl"}
    },
    "talisman": {
      "label": "Talisman",
      "kind": "items",
      "groups": [],
      "fields": [
        {"key": "power", "label": "Power", "type": "dots", "max": 5, "default": 1},
        {"key": "charges", "label": "Charges", "type": "resource", "max": 10}
      ]
    },
    "haven": {
      "label": "Haven",
      "kind": "locations",
      "groups": [],
      "fields": [
        {"key": "ward", "label": "Ward", "type": "dots", "max": 5, "default": 0},
        {"key": "size", "label": "Size", "type": "number", "default": 1}
      ]
    }
  }
}
```

`pool-basic/checks.json`:
```json
{
  "brawl": {
    "label": "Vigor + Brawl",
    "roll": "{vigor + brawl}d10 t6",
    "requires": ["attributes", "abilities"],
    "rules": ["combat"]
  },
  "perception": {
    "label": "Wits + Occult",
    "roll": "{wits + occult}d10 t6",
    "requires": ["attributes", "abilities"]
  }
}
```

`pool-basic/rules/core.md`:
```markdown
---
always: true
---
Roll a pool of d10s; each die at or above the target number (default 6) is a
success. More successes mean a stronger result.
```

`pool-basic/rules/combat.md`:
```markdown
---
keys: fight, attack, brawl
---
Attacks roll Vigor + Brawl. Each success past the first adds a wound.
```

`pool-basic/rules/botches.md`:
```markdown
---
on_roll: true
---
Zero successes with at least one die showing 1 is a botch: the action fails
badly and complications follow.
```

`pool-basic/rules/shifter-gifts.md`:
```markdown
---
sheet_types: shifter
keys: shift, claws
---
Shifters spend Fury to change form; claws add lethal damage to Brawl attacks.
```

`pool-basic/content/items/moonwell-talisman.md`:
```markdown
---
name: Moonwell Talisman
---
A silver disc that stores moonlight for later spending.
```

`pool-basic/content/items/moonwell-talisman.sheet.json`:
```json
{
  "sheet_type": "talisman",
  "fields": {
    "power": 3,
    "charges": {"current": 10, "max": 10}
  }
}
```

- [ ] **Step 4: Run tests** — `test_builtin_reference_modules_validate` PASSES (whole file green).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/builtin_modules backend/tests/test_modules_store.py
git commit -m "feat(modules): d20-basic and pool-basic built-in reference packs (#160)"
```

---

### Task 6: Binding — world/campaign `module:` keys + `resolve()`

**Files:**
- Modify: `backend/src/grimoire/store/modules.py` (binding + resolve)
- Modify: `backend/src/grimoire/store/campaigns.py` (`create_campaign` gains `module` param)
- Test: `backend/tests/test_modules_store.py` (append)

**Interfaces:**
- Produces: `set_world_module(wid: str, mid: str) -> None` (`""` clears the key; raises `ModuleNotFound` for unknown mid, `worlds.WorldNotFound`); `set_campaign_module(cid: str, value: str) -> None` (`""` clears ⇒ inherit; `"none"` ⇒ off; `<mid>` validated); `resolve(cid: str) -> str | None`; `campaigns.create_campaign(name, world_id, region=None, calendar=None, module=None)`.
- Consumes: `campaigns.campaign_meta_path/read_campaign`, `worlds.world_meta_path/read_world`, frontmatter round-trip pattern (`parse_frontmatter` → mutate → `dump_frontmatter`).

- [ ] **Step 1: Write the failing tests (append)**

```python
from grimoire.store import campaigns, worlds


def _world_campaign(monkeypatch, tmp_path, **kw):
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, **kw)
    return wid, cid


def test_resolve_default_none(monkeypatch, tmp_path):
    _, cid = _world_campaign(monkeypatch, tmp_path)
    assert modules.resolve(cid) is None


def test_resolve_inherits_world_default(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    assert modules.resolve(cid) == "pool-basic"


def test_campaign_none_overrides_world(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    modules.set_campaign_module(cid, "none")
    assert modules.resolve(cid) is None


def test_campaign_module_overrides_world(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_campaign_module(cid, "d20-basic")
    assert modules.resolve(cid) == "d20-basic"


def test_clear_campaign_setting_reinherits(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    modules.set_campaign_module(cid, "d20-basic")
    modules.set_campaign_module(cid, "")
    assert modules.resolve(cid) == "pool-basic"


def test_set_unknown_module_rejected(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleNotFound):
        modules.set_campaign_module(cid, "ghost")
    with pytest.raises(modules.ModuleNotFound):
        modules.set_world_module(wid, "ghost")


def test_resolve_missing_module_falls_through(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_campaign_module(cid, "d20-basic")
    # simulate the module disappearing after binding
    monkeypatch.setenv("GRIMOIRE_MODULES", str(tmp_path / "empty"))
    assert modules.resolve(cid) is None


def test_create_campaign_with_module(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module="pool-basic")
    assert modules.resolve(cid) == "pool-basic"
    with pytest.raises(modules.ModuleNotFound):
        campaigns.create_campaign("Run2", wid, module="ghost")
```

- [ ] **Step 2: Run to verify the new tests fail** (`AttributeError: resolve`, `TypeError` on `module=` kwarg).

- [ ] **Step 3: Implement**

Append to `modules.py` (imports of `campaigns`/`worlds` go **inside** the functions — `campaigns.py` will import `modules`, so top-level imports would cycle):

```python
def _write_key(meta_path, key: str, value: str) -> None:
    from .frontmatter import dump_frontmatter
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
```

In `campaigns.py`, change `create_campaign`'s signature to
`def create_campaign(name: str, world_id: str, region: str | None = None, calendar: str | None = None, module: str | None = None) -> str:`
and, next to the existing calendar validation (before any files are created), add:

```python
    if module:
        from . import modules
        modules.pack_root(module)  # raises ModuleNotFound before creating anything
```

then include the key when writing `campaign.md` (the `dump_frontmatter({...})` call): add `**({"module": module} if module else {})` into the meta dict.

- [ ] **Step 4: Run the whole backend suite** (binding touches campaigns):

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/src/grimoire/store/campaigns.py backend/tests/test_modules_store.py
git commit -m "feat(modules): world-default + campaign tri-state binding and resolve() (#160)"
```

---

### Task 7: Routes — modules CRUD + binding endpoints

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Produces HTTP API: `GET /api/modules` → `list_modules()`; `POST /api/modules` body `{name}` → `{"id": mid}`; `GET /api/modules/{mid}` → full pack dict (as returned by `load_pack`); `DELETE /api/modules/{mid}` → `{"ok": true}` (400 on builtin, 404 missing); `GET /api/campaigns/{cid}/module` → `{"setting": str, "resolved": str|null, "source": "campaign"|"world"|null}`; `PUT /api/campaigns/{cid}/module` body `{module: str}` (`""`/`"none"`/mid; 404 unknown mid); `PUT /api/worlds/{wid}/module` body `{module: str}`. `POST /api/campaigns` (`NewCampaign`) gains optional `module`.
- Consumes: Task 4/6 store functions.

- [ ] **Step 1: Write the failing tests (append to test_routes.py, reusing its `client`, `_world`, `_campaign` helpers)**

```python
def test_modules_api(client):
    listed = client.get("/api/modules").json()
    assert {m["id"] for m in listed} >= {"d20-basic", "pool-basic"}
    detail = client.get("/api/modules/pool-basic").json()
    assert detail["manifest"]["name"] == "Basic Pool"
    assert "medium" in detail["sheets"]["sheet_types"]
    assert detail["errors"] == []
    assert client.get("/api/modules/ghost").status_code == 404

    created = client.post("/api/modules", json={"name": "Homebrew"}).json()
    assert created["id"] == "homebrew"
    assert client.delete("/api/modules/homebrew").json()["ok"] is True
    assert client.delete("/api/modules/pool-basic").status_code == 400


def test_campaign_module_binding_api(client):
    wid = _world(client)
    cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r == {"setting": "", "resolved": None, "source": None}

    assert client.put(f"/api/worlds/{wid}/module",
                      json={"module": "pool-basic"}).json()["ok"] is True
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] == "pool-basic" and r["source"] == "world"

    client.put(f"/api/campaigns/{cid}/module", json={"module": "none"})
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] is None and r["setting"] == "none"

    client.put(f"/api/campaigns/{cid}/module", json={"module": "d20-basic"})
    r = client.get(f"/api/campaigns/{cid}/module").json()
    assert r["resolved"] == "d20-basic" and r["source"] == "campaign"

    assert client.put(f"/api/campaigns/{cid}/module",
                      json={"module": "ghost"}).status_code == 404
    assert client.put(f"/api/worlds/{wid}/module",
                      json={"module": "ghost"}).status_code == 404


def test_create_campaign_with_module(client):
    _world(client)
    r = client.post("/api/campaigns",
                    json={"name": "Mechanical", "world": "w", "module": "pool-basic"})
    cid = r.json()["id"]
    assert client.get(f"/api/campaigns/{cid}/module").json()["resolved"] == "pool-basic"
    assert client.post(
        "/api/campaigns",
        json={"name": "Broken", "world": "w", "module": "ghost"}).status_code == 404
```

Check `_world(client)`'s actual returned id in `test_routes.py` (its helper creates world "W" → id `w`); adjust literals to match the existing helpers.

- [ ] **Step 2: Run to verify they fail** (404s from missing routes).

- [ ] **Step 3: Implement**

Models (in the `# ---- models ----` block):

```python
class ModuleCreate(BaseModel):
    name: str


class ModuleSetting(BaseModel):
    module: str = ""
```

Add `module: str | None = None` to the existing `NewCampaign` model and pass it through the existing `post_campaign` handler into `store.campaigns.create_campaign(...)`, mapping `store.modules.ModuleNotFound` → `HTTPException(404, "module not found")`.

Endpoints — module library (place with the other top-level resources):

```python
@router.get("/modules")
def get_modules():
    return store.modules.list_modules()


@router.post("/modules")
def post_module(body: ModuleCreate):
    return {"id": store.modules.create_module(body.name)}


@router.get("/modules/{mid}")
def get_module(mid: str):
    try:
        return store.modules.load_pack(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.delete("/modules/{mid}")
def delete_module(mid: str):
    try:
        store.modules.delete_module(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError:
        raise HTTPException(status_code=400, detail="built-in modules cannot be deleted")
    return {"ok": True}
```

Binding endpoints — **register before the generic `/campaigns/{cid}/{kind}` entity routes** (put them beside the `/campaigns/{cid}/rolls` block, which carries the same ordering comment):

```python
@router.get("/campaigns/{cid}/module")
def get_campaign_module(cid: str):
    try:
        meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    setting = (meta.get("module") or "").strip()
    resolved = store.modules.resolve(cid)
    source = None
    if resolved is not None:
        source = "campaign" if setting and setting != "none" else "world"
    return {"setting": setting, "resolved": resolved, "source": source}


@router.put("/campaigns/{cid}/module")
def put_campaign_module(cid: str, body: ModuleSetting):
    try:
        store.modules.set_campaign_module(cid, body.module.strip())
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"ok": True}


@router.put("/worlds/{wid}/module")
def put_world_module(wid: str, body: ModuleSetting):
    try:
        store.modules.set_world_module(wid, body.module.strip())
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"ok": True}
```

Also confirm `store/__init__.py` exposes `modules` (check how `dice`/`rolls` are imported there and mirror it).

- [ ] **Step 4: Run the whole backend suite** — all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/__init__.py backend/tests/test_routes.py
git commit -m "feat(routes): module library + world/campaign binding endpoints (#160)"
```

---

### Task 8: Frontend API client — module types + functions

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces (used by Tasks 9–12):

```ts
export type ModuleSummary = {
  id: string; name: string; description: string;
  version: string; source: "builtin" | "user"; valid: boolean;
};
export type ModuleField = {
  key: string; label?: string; type: string;
  max?: number; min?: number; default?: number;
};
export type ModuleSheetType = {
  label: string; kind: string; groups: string[];
  fields: ModuleField[]; derived?: Record<string, string>;
};
export type ModuleDetail = {
  id: string;
  source: "builtin" | "user";
  manifest: { id: string; name: string; description?: string; version?: string; dice?: string };
  sheets: { groups: Record<string, { label?: string; fields: ModuleField[]; derived?: Record<string, string> }>;
            sheet_types: Record<string, ModuleSheetType> };
  checks: Record<string, { label: string; roll: string; requires?: string[]; rules?: string[] }>;
  rules: { id: string; keys: string[]; always: boolean; on_roll: boolean; sheet_types: string[] }[];
  content: { kind: string; id: string; name: string; sheet_type: string | null }[];
  errors: string[];
};
export type CampaignModule = {
  setting: string; resolved: string | null; source: "campaign" | "world" | null;
};
```

and in the `api` object:

```ts
  listModules: () => request<ModuleSummary[]>("GET", "/api/modules"),
  readModule: (mid: string) => request<ModuleDetail>("GET", `/api/modules/${mid}`),
  getCampaignModule: (cid: string) =>
    request<CampaignModule>("GET", `/api/campaigns/${cid}/module`),
  setCampaignModule: (cid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/module`, { module }),
  setWorldModule: (wid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/module`, { module }),
```

- Modify `createCampaign` (client.ts line ~324) to accept a fifth optional arg `module?: string`, spread into the POST body like `region`/`calendar`. Add `module?: string` to `WorldMeta` and `CampaignMeta` types.

Steps: **Step 1** make the edits; **Step 2** run `npx tsc -b` from `frontend/` — expect clean; **Step 3** commit:

```bash
git add frontend/src/api/client.ts
git commit -m "feat(client): module library + binding API functions"
```

(No new tests — exercised by component tests in Tasks 9–12.)

---

### Task 9: ModulesView page + navigation

**Files:**
- Create: `frontend/src/routes/ModulesView.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/App.test.tsx`
- Test: `frontend/src/routes/ModulesView.test.tsx`

Read-only list/detail per the house pattern (GreetingEditor minus edit mode): `.editor` > `.editor-list` rail of module rows; `.editor-body` > `.detail-view` with `.detail-main` (name, description, per-sheet-type field listing) and `.detail-sidebar` (`.side-section` blocks: version/source, sheet types, checks, rules with activation hints, validation errors if any).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/ModulesView.test.tsx
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    listModules: vi.fn(),
    readModule: vi.fn(),
  },
}));
import { api } from "../api/client";
import ModulesView from "./ModulesView";

const POOL = {
  id: "pool-basic",
  source: "builtin",
  manifest: { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", dice: "5d10 t6" },
  sheets: {
    groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: ["attributes"], fields: [], derived: {} },
      talisman: { label: "Talisman", kind: "items", groups: [], fields: [{ key: "power", label: "Power", type: "dots", max: 5 }], derived: {} },
    },
  },
  checks: { brawl: { label: "Vigor + Brawl", roll: "{vigor}d10 t6", requires: ["attributes"] } },
  rules: [{ id: "core", keys: [], always: true, on_roll: false, sheet_types: [] }],
  content: [],
  errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue(POOL);
});

test("clicking a row shows the read-only module detail", async () => {
  const { container } = render(<ModulesView />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Basic Pool"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  const detail = await waitFor(() => container.querySelector(".detail-view") as HTMLElement);
  expect(within(detail).getByText("d10 pools.")).toBeInTheDocument();
  expect(within(detail).getByText("Medium")).toBeInTheDocument();
  expect(within(detail).getByText("Talisman")).toBeInTheDocument();
  expect(within(detail).getByText("Vigor + Brawl")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();   // read-only
  expect(within(detail).queryByText("Edit")).toBeNull();    // no edit affordance
});
```

- [ ] **Step 2: Run to verify it fails**

From `frontend/`: `npx vitest run src/routes/ModulesView.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `ModulesView.tsx`**

```tsx
// frontend/src/routes/ModulesView.tsx
import { useEffect, useState } from "react";
import { api, type ModuleDetail, type ModuleSummary } from "../api/client";

export default function ModulesView() {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listModules().then(setMods).catch((e) => setError(String(e)));
  }, []);

  const select = (mid: string) => {
    api.readModule(mid).then(setDetail).catch((e) => setError(String(e)));
  };

  return (
    <div className="editor">
      <div className="editor-list">
        {mods.map((m) => (
          <button
            key={m.id}
            className={"row" + (detail?.id === m.id ? " active" : "")}
            onClick={() => select(m.id)}
          >
            {m.name}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {detail ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{detail.manifest.name}</h3>
              {detail.manifest.description && <p>{detail.manifest.description}</p>}
              {Object.entries(detail.sheets.sheet_types).map(([tid, st]) => (
                <div key={tid} className="side-section">
                  <h4>
                    {st.label} <span className="field-hint">({st.kind})</span>
                  </h4>
                  <div className="chips">
                    {st.groups.map((g) => (
                      <span key={g} className="chip on">
                        {detail.sheets.groups[g]?.label ?? g}
                      </span>
                    ))}
                    {st.fields.map((f) => (
                      <span key={f.key} className="chip">
                        {f.label ?? f.key}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <aside className="detail-sidebar">
              <div className="side-section">
                <h4>Module</h4>
                <span className="chip on">{detail.source}</span>
                {detail.manifest.version && (
                  <span className="chip on">v{detail.manifest.version}</span>
                )}
                {detail.manifest.dice && (
                  <div className="field-hint">Dice: {detail.manifest.dice}</div>
                )}
              </div>
              {Object.keys(detail.checks).length > 0 && (
                <div className="side-section">
                  <h4>Checks</h4>
                  <div className="chips">
                    {Object.entries(detail.checks).map(([id, c]) => (
                      <span key={id} className="chip on">{c.label}</span>
                    ))}
                  </div>
                </div>
              )}
              {detail.rules.length > 0 && (
                <div className="side-section">
                  <h4>Rules</h4>
                  {detail.rules.map((r) => (
                    <div key={r.id} className="field-hint">
                      {r.id}
                      {r.always ? " · always" : ""}
                      {r.on_roll ? " · on roll" : ""}
                      {r.keys.length ? ` · keys: ${r.keys.join(", ")}` : ""}
                      {r.sheet_types.length ? ` · types: ${r.sheet_types.join(", ")}` : ""}
                    </div>
                  ))}
                </div>
              )}
              {detail.errors.length > 0 && (
                <div className="side-section">
                  <h4>Problems</h4>
                  {detail.errors.map((e, i) => (
                    <div key={i} className="field-hint">{e}</div>
                  ))}
                </div>
              )}
            </aside>
          </div>
        ) : (
          <p className="field-hint">Select a module to view its contract.</p>
        )}
      </div>
    </div>
  );
}
```

In `App.tsx`: add `<NavLink to="/modules" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>Modules</NavLink>` in the topbar `<nav>` (after Worlds), import `ModulesView`, add `<Route path="/modules" element={<ModulesView />} />`. In `App.test.tsx`, extend the topbar assertion with `Modules`.

- [ ] **Step 4: Run** — from `frontend/`: `npx vitest run src/routes/ModulesView.test.tsx src/App.test.tsx` then `npx tsc -b`. All PASS/clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ModulesView.tsx frontend/src/routes/ModulesView.test.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(frontend): read-only Modules library page + nav (#160)"
```

---

### Task 10: Campaign wizard module picker

**Files:**
- Modify: `frontend/src/routes/CampaignWizard.tsx`, `frontend/src/routes/CampaignWizard.test.tsx`

- [ ] **Step 1: Write the failing test** (follow the existing mock pattern in `CampaignWizard.test.tsx`; add `listModules` to the client mock returning `[{ id: "pool-basic", name: "Basic Pool", … }]` and `createCampaign` spy):

```tsx
test("module picker defaults to inherit and passes the chosen module", async () => {
  // render wizard, wait for step 1
  const select = await screen.findByLabelText("Mechanics module");
  expect((select as HTMLSelectElement).value).toBe("");           // inherit default
  fireEvent.change(select, { target: { value: "pool-basic" } });
  // …fill name/world and drive the wizard to commit as the existing tests do…
  await waitFor(() =>
    expect(api.createCampaign).toHaveBeenCalledWith(
      expect.any(String), expect.any(String), undefined, expect.anything(), "pool-basic"));
});
```

Mirror the existing tests' exact setup for filling name/world and committing — copy the closest existing "creates a campaign" test and extend it.

- [ ] **Step 2: Run to verify it fails** (no such label).

- [ ] **Step 3: Implement** in `CampaignWizard.tsx`:
  - `const [modules, setModules] = useState<ModuleSummary[]>([]);` and `const [moduleId, setModuleId] = useState("");`
  - load in the existing first `useEffect`: `api.listModules().then(setModules).catch(() => setModules([]));`
  - after the World select in Step 1:

```tsx
<div className="field">
  <label htmlFor="wiz-module">Mechanics module</label>
  <select id="wiz-module" value={moduleId}
          onChange={(e) => setModuleId(e.target.value)}>
    <option value="">World default</option>
    <option value="none">None</option>
    {modules.map((m) => (
      <option key={m.id} value={m.id}>{m.name}</option>
    ))}
  </select>
</div>
```

  - in `commit()`: pass `moduleId || undefined` as the new fifth argument to `api.createCampaign(...)`. Note `"none"` is a legal value (campaign explicitly mechanics-free): `createCampaign` sends it verbatim; backend `create_campaign` writes it (the `pack_root` validation in Task 6 must skip `"none"` — it only validates truthy values ≠ `"none"`; confirm Task 6's `create_campaign` guard reads `if module and module != "none":`).

- [ ] **Step 4: Run** — from `frontend/`: `npx vitest run src/routes/CampaignWizard.test.tsx` and `npx tsc -b`. PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignWizard.tsx frontend/src/routes/CampaignWizard.test.tsx
git commit -m "feat(frontend): mechanics module picker in the campaign wizard (#160)"
```

**Consistency note for the Task 6 implementer:** the campaign-create guard must be `if module and module != "none":` and the frontmatter write `**({"module": module} if module else {})` (so `"none"` is persisted as an explicit off).

---

### Task 11: Campaign Mechanics panel

**Files:**
- Create: `frontend/src/components/MechanicsConfig.tsx`
- Test: `frontend/src/components/MechanicsConfig.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx`

Modeled on `components/CalendarConfig.tsx` (fetch → local state → Save). Surfaced from `CampaignView` the same way CalendarConfig is: add a `showMechanics` state, a subheader button (`sub-actions`, near the calendar toggle) labeled "Mechanics", and a `panel-slot` rendering `<MechanicsConfig cid={cid} />`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/MechanicsConfig.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    getCampaignModule: vi.fn(),
    setCampaignModule: vi.fn(),
    listModules: vi.fn(),
  },
}));
import { api } from "../api/client";
import MechanicsConfig from "./MechanicsConfig";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "", version: "0.1", source: "builtin", valid: true },
  ]);
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  (api.setCampaignModule as any).mockResolvedValue({ ok: true });
});

test("shows tri-state select and saves the choice", async () => {
  render(<MechanicsConfig cid="run" />);
  const select = (await screen.findByLabelText("Mechanics")) as HTMLSelectElement;
  expect(select.value).toBe("");                        // inherit
  expect(screen.getByText(/No mechanics/)).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.setCampaignModule).toHaveBeenCalledWith("run", "pool-basic"));
});

test("shows the resolved module and its source", async () => {
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "", resolved: "pool-basic", source: "world" });
  render(<MechanicsConfig cid="run" />);
  expect(await screen.findByText(/Basic Pool/)).toBeInTheDocument();
  expect(screen.getByText(/world default/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

```tsx
// frontend/src/components/MechanicsConfig.tsx
import { useEffect, useState } from "react";
import { api, type CampaignModule, type ModuleSummary } from "../api/client";

export default function MechanicsConfig({ cid }: { cid: string }) {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [state, setState] = useState<CampaignModule | null>(null);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);

  const load = () =>
    api.getCampaignModule(cid).then((s) => {
      setState(s);
      setValue(s.setting);
    });

  useEffect(() => {
    api.listModules().then(setMods).catch(() => setMods([]));
    load().catch(() => setState(null));
  }, [cid]);

  const save = async () => {
    await api.setCampaignModule(cid, value);
    setSaved(true);
    await load();
  };

  const name = (mid: string | null) =>
    mods.find((m) => m.id === mid)?.name ?? mid ?? "";

  return (
    <div className="side-section">
      <h4>Mechanics</h4>
      <label>
        Mechanics
        <select aria-label="Mechanics" value={value}
                onChange={(e) => { setValue(e.target.value); setSaved(false); }}>
          <option value="">World default</option>
          <option value="none">None</option>
          {mods.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </label>
      {state && (
        <div className="field-hint">
          {state.resolved
            ? `Playing with ${name(state.resolved)}` +
              (state.source === "world" ? " (world default)" : "")
            : "No mechanics — freeform play."}
        </div>
      )}
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
```

Wire into `CampaignView.tsx` beside the calendar panel (same toggle pattern; copy the `showCalendar` wiring for a `showMechanics` state and a "Mechanics" `sub-actions` button).

- [ ] **Step 4: Run** — from `frontend/`: `npx vitest run src/components/MechanicsConfig.test.tsx` and `npx tsc -b`. PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MechanicsConfig.tsx frontend/src/components/MechanicsConfig.test.tsx frontend/src/routes/CampaignView.tsx
git commit -m "feat(frontend): campaign Mechanics panel with tri-state module setting (#160)"
```

---

### Task 12: World default-module setting

**Files:**
- Create: `frontend/src/components/WorldMechanics.tsx`
- Test: `frontend/src/components/WorldMechanics.test.tsx`
- Modify: `frontend/src/components/WorldOverview.tsx` (render `<WorldMechanics wid={wid} />` as a new section)

Same shape as MechanicsConfig but two-state (`""` = none, `<mid>` = default) writing via `api.setWorldModule`; initial value from `api.getWorld(wid)`'s `meta.module`. Mirror the MechanicsConfig test (select defaults to `""` labeled "None"; choosing `pool-basic` + Save calls `setWorldModule("w", "pool-basic")`). Add `getWorld` to the test's client mock. Implementation is MechanicsConfig with: options `<option value="">None</option>` + modules; hint text `Campaigns on this world default to <name>.` when set.

Steps: failing test → verify fail → implement → `npx vitest run src/components/WorldMechanics.test.tsx` + `npx tsc -b` → commit:

```bash
git add frontend/src/components/WorldMechanics.tsx frontend/src/components/WorldMechanics.test.tsx frontend/src/components/WorldOverview.tsx
git commit -m "feat(frontend): world default mechanics module setting (#160)"
```

---

### Task 13: `create-mechanics-module` skill

**Files:**
- Create: `.claude/skills/create-mechanics-module/SKILL.md`

Follow the structure of `.claude/skills/ingest-campaign-log/SKILL.md` (read it first for frontmatter/layout conventions). Content requirements:

- Frontmatter: `name: create-mechanics-module`, `description:` "Use when authoring a new game-mechanics module (data pack) for grimoire — interviews for the system's shape, scaffolds the pack, and validates each step."
- Workflow (numbered): (1) interview — dice habit, attribute/ability structure, character types (splats/classes), object families (items/locations/etc.), mutable resources vs static ratings; (2) scaffold `<GRIMOIRE_HOME>/modules/<mid>/module.md` (or `store.modules.create_module`); (3) author `sheets.json` groups first, then sheet types per kind (composition: shared groups + own fields; `resource` for play-mutable pools, `track` for boxes, `dots` for ratings); (4) author `checks.json` (`{expression}` placeholders, `requires` groups, link rules docs); (5) author `rules/*.md` with activation flags (`always` core digest, `on_roll` dice interpretation, `keys` situational, `sheet_types` splat powers); (6) optional statted content (`content/<kind>/<id>.md` + `<id>.sheet.json`); (7) validate after every step with
  `backend/.venv/Scripts/python.exe -c "from grimoire.store import modules; print(modules.load_pack('<mid>')['errors'])"`
  (empty list = valid); (8) confirm the module appears on the Modules page.
- Include a minimal complete example pack (may reference `pool-basic` as the model) and the full field-type table and activation-flag table from the spec.
- Reference the spec path for format details.

Steps: write the file → verify `backend/.venv/Scripts/python.exe -c "..."` snippet works against `pool-basic` → commit:

```bash
git add .claude/skills/create-mechanics-module/SKILL.md
git commit -m "feat(skill): create-mechanics-module authoring walkthrough (#166)"
```

---

### Task 14: Full verification + docs

- [ ] **Step 1:** Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` (with worktree `PYTHONPATH` shadowing) — all green.
- [ ] **Step 2:** Frontend from `frontend/`: `npx vitest run` and `npx tsc -b` — all green.
- [ ] **Step 3:** Check `CLAUDE.md`'s working-notes/architecture claims still hold (no edits expected; modules are pure-stdlib and Android-safe). Confirm `pyproject.toml` needed no new deps.
- [ ] **Step 4:** End-state smoke per spec: in a temp `GRIMOIRE_HOME`, create world+campaign via TestClient, bind `pool-basic` through `PUT /api/campaigns/{cid}/module`, assert `GET .../module` resolves it — this is `test_campaign_module_binding_api`, already green; nothing manual required.
- [ ] **Step 5:** Commit any stragglers; the branch is ready for review/merge (rebase-merge to `main` per house convention) and the GitHub issue pass from the plan header.
