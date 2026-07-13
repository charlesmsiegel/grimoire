# Mechanics Phase 7 — Content Browsers + Creation Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a mechanics module's `content/` browse alongside world/campaign
entities and be instantiated into them; let sheets carry `ref` fields
pointing at entities or uninstantiated module content; let a module declare
per-group point-buy budgets driving a creation wizard; let a module declare
a single XP-like advancement pool with formula-priced raises.

**Architecture:** Backend: three schema additions to `store/modules.py`
(`ref` field type, `creation.pools`, `advancement`) validated at pack-load
time exactly like existing `checks`/`derived`; a `read_content()` reader;
two new `store/sheets.py` write paths (`write_creation`/`write_world_creation`
enforcing pool budgets server-side, `advance()` serialized by a per-campaign
lock with an atomic temp-file+`os.replace` write) alongside the existing
`write()`; five new routes mirroring the existing world/campaign
entity-and-sheet route pairs exactly. Frontend: `module.content` (already
fetched, unused) merges into `EntityEditor`'s existing rail; a new
`CreationWizard` component wraps the existing sheet-type-picker flow with a
budget stepper; `SheetEditor` gains a `ref` widget and an advancement
button.

**Tech Stack:** FastAPI + pydantic (v1/v2-agnostic via `routes._dump`),
pytest (`GRIMOIRE_HOME`-isolated); React + TypeScript, vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-mechanics-phase7-content-creation-design.md` (codex-approved after 5 review rounds — every design decision below traces back to a row in its Decisions table).
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- `store/modules.py::load_pack` must **never raise** on malformed pack content — only `ModuleNotFound`/`ContentNotFound` for a missing module/content id are allowed to propagate.
- `store/sheets.py` reads never raise (malformed sheet → `errors` list); writes raise `SheetError` on rejection.
- pydantic models in `routes.py` stay v1/v2-agnostic: plain `BaseModel` fields only, no `Field`/validators/`ConfigDict`, dumped via `routes._dump` (not needed for this phase's models — none of them are echoed back verbatim, but keep the convention if that changes).
- `ref` fields may only target kinds in `entities.ENTITY_KINDS` (`locations`, `lore`, `items`, `groups`, `creatures`) — never `characters`/`pcs`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Run frontend tests from `frontend/`: `npx vitest run` (not `npx --prefix frontend vitest run` — that skips `vitest.config.ts` and breaks every mock-based test).
- Privacy: any names in test fixtures must be invented (reuse `Realm`/`Mara`/`Seraphine`/`Winifred`/`Saltmarch`-style placeholders already used in this codebase's tests) — never a real world/campaign/character name.

---

## File Structure

Backend:
- Modify `backend/src/grimoire/store/modules.py` — `ref` field type, `creation.pools` schema+validation, `advancement` block schema+validation, `read_content()`, `ContentNotFound`.
- Modify `backend/src/grimoire/store/sheets.py` — atomic write helper, `write_creation`/`write_world_creation`, `advance()` + per-campaign lock registry.
- Modify `backend/src/grimoire/routes.py` — 5 new routes + 2 new pydantic models.
- Modify `backend/src/grimoire/store/builtin_modules/pool-basic/sheets.json`, `backend/src/grimoire/store/builtin_modules/d20-basic/sheets.json` — reference fleshing.
- Modify `backend/tests/test_modules_store.py`, `backend/tests/test_sheets_store.py`, `backend/tests/test_routes.py`.

Frontend:
- Modify `frontend/src/api/client.ts` — types + new API functions.
- Modify `frontend/src/components/EntityEditor.tsx` — content-row merge, read-only content preview, Instantiate.
- Create `frontend/src/components/CreationWizard.tsx`.
- Modify `frontend/src/components/CharacterEditor.tsx`, `frontend/src/components/PCEditor.tsx` — wizard button, `onOpenRef` threading.
- Modify `frontend/src/components/SheetPanel.tsx` — thread `onOpenRef` through to `SheetEditor`.
- Modify `frontend/src/components/SheetEditor.tsx` — `ref` widget, advancement button.
- Modify `frontend/src/components/EntityEditor.test.tsx`; create `frontend/src/components/CreationWizard.test.tsx`; modify `frontend/src/components/SheetEditor.test.tsx`.

No new CSS — every new element reuses existing global classes (`chips`,
`chip`, `chip on`, `side-section`, `field-hint`, `banner`, `subtle`,
`primary`, `sheet-row`) already used throughout `EntityEditor.tsx` /
`SheetEditor.tsx` / `ModulesView.tsx`.

---

### Task 1: `ref` field type (modules.py)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py:30` (`FIELD_TYPES`), `:99-118` (`_validate_field`), `:324-366` (`validate_sheet_values`)
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: `"ref"` is a valid `FIELD_TYPES` entry; a `ref` field descriptor requires `ref_kind` (one of `entities.ENTITY_KINDS`); `ref` fields are excluded from `numeric_names()` (already true — that function only adds `number`/`dots`/`track`/`resource`, so no code change needed there, only a test asserting it); `modules.validate_sheet_values` gains a `ref` value-shape check (list of `"<ref_kind>:<id>"` or `"<ref_kind>:module:<id>"` strings) — this is the actual write-time trust boundary (`_checked_write`/`_checked_creation_write` both call `validate_sheet_values`), not just the descriptor check above. Codex adversarial review of the plan (round 1) flagged this as missing: without it, a stale client or direct API call can persist a malformed `ref` value (wrong shape, wrong `ref_kind` prefix, non-string entries) through the existing plain sheet `PUT`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_modules_store.py` (near the other `_sheets_error` tests, after `test_bool_max_rejected`):

```python
def test_ref_field_requires_ref_kind(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "known", "type": "ref"}))
    assert any("ref_kind" in e for e in errs)


def test_ref_field_bad_ref_kind(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "known", "type": "ref", "ref_kind": "characters"}))
    assert any("ref_kind" in e for e in errs)


def test_ref_field_valid(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "known", "type": "ref", "ref_kind": "lore"}))
    assert errs == []


def test_ref_field_not_numeric_addressable():
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["groups"]["attributes"]["fields"].append(
        {"key": "known", "type": "ref", "ref_kind": "lore"})
    fields = modules.assembled_fields(sheets, "warden")
    assert "known" not in modules.numeric_names(fields)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k ref_field -v`
Expected: FAIL — `test_ref_field_valid` fails because `type": "ref"` isn't a known field type yet (`"unknown field type 'ref'"` error); `test_ref_field_requires_ref_kind`/`test_ref_field_bad_ref_kind` fail because that error doesn't exist yet.

- [ ] **Step 3: Add `ref` to `FIELD_TYPES` and validate `ref_kind`**

In `backend/src/grimoire/store/modules.py`, change line 30:

```python
FIELD_TYPES = ("number", "dots", "track", "resource", "text", "list", "ref")
```

Add an import at the top (near the existing `from . import dice, expressions`):

```python
from . import dice, entities, expressions
```

In `_validate_field` (around line 99-118), after the existing `ftype not in FIELD_TYPES` check and before the `dots`/`track`/`resource` `max` check, add:

```python
    if ftype == "ref":
        ref_kind = field.get("ref_kind")
        if ref_kind not in entities.ENTITY_KINDS:
            errors.append(f"{where}.{key}: ref field requires ref_kind in {entities.ENTITY_KINDS}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k ref_field -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing tests for `validate_sheet_values`'s `ref` handling**

Add to `backend/tests/test_modules_store.py`. This needs a sheets fixture with a `ref` field, so build one from `GOOD_SHEETS`:

```python
def _sheets_with_ref():
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["sheet_types"]["warden"]["fields"].append(
        {"key": "known", "label": "Known", "type": "ref", "ref_kind": "lore"})
    return sheets


def test_validate_sheet_values_ref_entity_form():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(sheets, "warden", {"known": ["lore:fireball"]})
    assert errs == []


def test_validate_sheet_values_ref_module_form():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(sheets, "warden", {"known": ["lore:module:icebolt"]})
    assert errs == []


def test_validate_sheet_values_ref_mixed_forms():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(
        sheets, "warden", {"known": ["lore:fireball", "lore:module:icebolt"]})
    assert errs == []


def test_validate_sheet_values_ref_not_a_list():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(sheets, "warden", {"known": "lore:fireball"})
    assert any("known" in e for e in errs)


def test_validate_sheet_values_ref_wrong_kind_prefix():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(sheets, "warden", {"known": ["items:sword"]})
    assert any("known" in e for e in errs)


def test_validate_sheet_values_ref_bad_segment_count():
    sheets = _sheets_with_ref()
    for bad in ("fireball", "lore:module:extra:segment", "lore::"):
        errs = modules.validate_sheet_values(sheets, "warden", {"known": [bad]})
        assert any("known" in e for e in errs), bad


def test_validate_sheet_values_ref_non_string_entry():
    sheets = _sheets_with_ref()
    errs = modules.validate_sheet_values(sheets, "warden", {"known": [5]})
    assert any("known" in e for e in errs)
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k validate_sheet_values_ref -v`
Expected: FAIL — today `validate_sheet_values` has no `t == "ref"` branch, so it falls through every existing `elif` untouched and returns `errs == []` even for the malformed cases (the happy-path tests already pass by accident; the four rejection tests fail).

- [ ] **Step 7: Add the `ref` branch to `validate_sheet_values`**

In `backend/src/grimoire/store/modules.py`, in `validate_sheet_values` (around line 324-366), add a `ref` branch to the `elif` chain, right after the existing `elif t == "list":` branch:

```python
        elif t == "ref":
            ref_kind = f.get("ref_kind")
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{key}: expected a list of strings")
            else:
                for entry in value:
                    parts = entry.split(":")
                    valid_entity_form = len(parts) == 2 and parts[0] == ref_kind
                    valid_module_form = len(parts) == 3 and parts[0] == ref_kind and parts[1] == "module"
                    if not (valid_entity_form or valid_module_form):
                        errors.append(f"{key}: {entry!r} is not a valid ref for kind {ref_kind!r}")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k "ref_field or validate_sheet_values_ref" -v`
Expected: PASS (11 passed)

- [ ] **Step 9: Run the full modules test file to check for regressions**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all pass (the mutation-sweep test `test_load_pack_never_raises_mutation_sweep` in particular — a `ref` field type addition must not introduce a new raise path for junk `ref_kind` values, which the added `in entities.ENTITY_KINDS` check handles safely since `in` never raises on arbitrary junk; the new `validate_sheet_values` branch is equally junk-safe since it only does `isinstance`/`.split`/`==` checks, never raises on arbitrary input shapes).

- [ ] **Step 10: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): ref field type + write-time value validation for both address forms"
```

---

### Task 2: `creation.pools` schema (modules.py)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py` — new `_validate_creation` function called from `_validate_sheets`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Consumes: `numeric_names`, `expressions.evaluate`/`ExpressionError` (Task 1's imports already present)
- Produces: sheet types may carry `st["creation"]["pools"][pool_id] = {"budget": int|str, "costs": {field_key: int}}`; validated at `load_pack` time. Task 6 (`sheets.write_creation`) reads this shape directly off `load_pack(mid)["sheets"]["sheet_types"][type_id]["creation"]`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_modules_store.py`:

```python
def _with_creation(sheets, pools):
    sheets["sheet_types"]["warden"]["creation"] = {"pools": pools}
    return sheets


def test_creation_pool_unknown_group(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"ghost": {"budget": 10, "costs": {"vigor": 1}}}))
    assert any("ghost" in e for e in errs)


def test_creation_pool_cost_field_wrong_group(monkeypatch, tmp_path):
    # "essence" belongs to the warden sheet type's own fields, not the
    # "attributes" group -- a pool keyed to "attributes" can't cost it.
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"attributes": {"budget": 10, "costs": {"essence": 1}}}))
    assert any("essence" in e for e in errs)


def test_creation_pool_non_positive_cost(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"attributes": {"budget": 10, "costs": {"vigor": 0}}}))
    assert any("vigor" in e for e in errs)


def test_creation_pool_budget_references_field(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"attributes": {"budget": "vigor + 1", "costs": {"vigor": 1}}}))
    assert any("budget" in e for e in errs)


def test_creation_pool_valid(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"attributes": {"budget": 6, "costs": {"vigor": 1, "wits": 2}}}))
    assert errs == []


def test_creation_pool_valid_expression_budget(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_creation(s, {"attributes": {"budget": "3 + 3", "costs": {"vigor": 1}}}))
    assert errs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k creation_pool -v`
Expected: FAIL — none of the `creation` block errors are raised yet (`test_creation_pool_valid` fails too, because nothing reads/allows an unrecognized `creation` key today — actually it currently passes silently since `_validate_sheets` never inspects unknown keys; check with a quick run first, but the rejection-case tests definitely fail since no validation exists).

- [ ] **Step 3: Implement `_validate_creation`**

In `backend/src/grimoire/store/modules.py`, add a new function right after `_validate_derived` (around line 185):

```python
def _pool_group_fields(group: dict) -> dict[str, dict]:
    fields = group.get("fields", []) if isinstance(group, dict) else []
    if not isinstance(fields, list):
        return {}
    return {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}


def _validate_creation(st: dict, st_groups: list, groups: dict, where: str,
                       errors: list[str]) -> None:
    creation = st.get("creation")
    if creation is None:
        return
    if not isinstance(creation, dict):
        errors.append(f"{where}.creation: must be an object")
        return
    pools = _as_dict(creation.get("pools"), f"{where}.creation", "pools", errors)
    for pool_id, pool in pools.items():
        pwhere = f"{where}.creation.pools.{pool_id}"
        if not isinstance(pool, dict):
            errors.append(f"{pwhere}: must be an object")
            continue
        if pool_id not in st_groups:
            errors.append(f"{pwhere}: {pool_id!r} is not a group of this sheet type")
            continue
        group_fields = _pool_group_fields(groups.get(pool_id))
        budget = pool.get("budget", 0)
        if isinstance(budget, str):
            try:
                unknown = expressions.names(budget)
            except expressions.ExpressionError as e:
                errors.append(f"{pwhere}.budget: {e}")
                unknown = set()
            if unknown:
                errors.append(f"{pwhere}.budget: must not reference fields, found {sorted(unknown)}")
        elif not isinstance(budget, int) or isinstance(budget, bool):
            errors.append(f"{pwhere}.budget: must be an int or an expression string")
        costs = _as_dict(pool.get("costs"), pwhere, "costs", errors)
        for field_key, cost in costs.items():
            if field_key not in group_fields:
                errors.append(f"{pwhere}.costs.{field_key}: not a field of group {pool_id!r}")
                continue
            if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
                errors.append(f"{pwhere}.costs.{field_key}: must be a positive integer")
```

Call it from `_validate_sheets` (around line 428, right after the existing `_validate_derived(st_derived, scope, where, errors)` call for sheet types):

```python
        st_derived = _as_dict(st.get("derived"), where, "derived", errors)
        _validate_derived(st_derived, scope, where, errors)
        _validate_creation(st, st_groups, groups, where, errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k creation_pool -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full modules test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all pass, including the mutation-sweep test. If the sweep test's `count > 100` assertion needs the `creation` block added to `GOOD_SHEETS`'s mutation surface — it doesn't automatically, since `GOOD_SHEETS` doesn't declare a `creation` block by default. No sweep-test changes needed this task (Task 3 adds `advancement`, which also stays out of `GOOD_SHEETS` by default for the same reason: keeping the shared base fixture minimal).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): creation.pools schema — per-group budget/cost validation"
```

---

### Task 3: `advancement` block schema (modules.py)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py` — new `_validate_advancement` function called from `_validate_sheets`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Consumes: `assembled_fields`, `numeric_names`, `expressions.evaluate`/`names`/`ExpressionError`
- Produces: sheet types may carry `st["advancement"] = {"pool": field_key, "costs": {field_key: expr_str}}`, validated at load time. Task 7 (`sheets.advance`) reads this shape directly.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_modules_store.py`:

```python
def _with_advancement(sheets, pool, costs):
    sheets["sheet_types"]["warden"]["advancement"] = {"pool": pool, "costs": costs}
    return sheets


def test_advancement_pool_not_resource(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "vigor", {"vigor": "new * 2"}))
    assert any("pool" in e for e in errs)


def test_advancement_costs_key_not_raisable(monkeypatch, tmp_path):
    # "essence" is a resource field -- not raisable via advancement
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"essence": "new * 2"}))
    assert any("essence" in e for e in errs)


def test_advancement_cost_unknown_name(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"vigor": "charm * 2"}))
    assert any("charm" in e for e in errs)


def test_advancement_cost_non_positive_sample(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"vigor": "-new"}))
    assert any("positive" in e for e in errs)


def test_advancement_cost_bool_sample(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"vigor": "new > 0"}))
    assert any("positive" in e for e in errs)


def test_advancement_valid(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"vigor": "new * 3"}))
    assert errs == []


def test_advancement_cost_can_reference_derived(monkeypatch, tmp_path):
    # "reflex" is a group-derived name (min(vigor, wits)) in GOOD_SHEETS
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: _with_advancement(s, "essence", {"vigor": "reflex + new"}))
    assert errs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k advancement -v`
Expected: FAIL — no `advancement` validation exists yet.

- [ ] **Step 3: Implement `_validate_advancement`**

Add to `backend/src/grimoire/store/modules.py`, right after `_validate_creation`:

```python
_RAISABLE_TYPES = ("number", "dots")


def _validate_advancement(st: dict, fields: list[dict], scope: set[str],
                          where: str, errors: list[str]) -> None:
    adv = st.get("advancement")
    if adv is None:
        return
    if not isinstance(adv, dict):
        errors.append(f"{where}.advancement: must be an object")
        return
    field_defs = {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}
    pool = adv.get("pool")
    pool_field = field_defs.get(pool) if isinstance(pool, str) else None
    if not isinstance(pool_field, dict) or pool_field.get("type") != "resource":
        errors.append(f"{where}.advancement.pool: {pool!r} must be a resource field of this sheet type")
    costs = _as_dict(adv.get("costs"), f"{where}.advancement", "costs", errors)
    cost_scope = scope | {"new"}
    for field_key, expr in costs.items():
        fdef = field_defs.get(field_key)
        if not isinstance(fdef, dict) or fdef.get("type") not in _RAISABLE_TYPES:
            errors.append(f"{where}.advancement.costs.{field_key}: must target a number/dots field")
            continue
        if not isinstance(expr, str):
            errors.append(f"{where}.advancement.costs.{field_key}: expression must be a string")
            continue
        try:
            unknown = expressions.names(expr) - cost_scope
        except expressions.ExpressionError as e:
            errors.append(f"{where}.advancement.costs.{field_key}: {e}")
            continue
        if unknown:
            errors.append(f"{where}.advancement.costs.{field_key}: unknown names {sorted(unknown)}")
            continue
        sample = {name: 1 for name in cost_scope}
        try:
            result = expressions.evaluate(expr, sample)
        except expressions.ExpressionError as e:
            errors.append(f"{where}.advancement.costs.{field_key}: {e}")
            continue
        if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
            errors.append(
                f"{where}.advancement.costs.{field_key}: must evaluate to a positive "
                f"integer (sampled {result!r} at every name = 1)")
```

Call it from `_validate_sheets`, right after the `_validate_creation` call added in Task 2:

```python
        _validate_creation(st, st_groups, groups, where, errors)
        _validate_advancement(st, fields, scope, where, errors)
```

(`fields` here is the sheet type's `assembled_fields(sheets, tid)` result already computed a few lines above in `_validate_sheets`; `scope` is the `numeric_names(fields) | group-derived-names` set also already computed there — both are already in scope at that point in the existing function, no new computation needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k advancement -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Run the full modules test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): advancement schema — resource pool + positive-int-sampled cost formulas"
```

---

### Task 4: `read_content()` + `ContentNotFound` (modules.py)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: `modules.ContentNotFound` (exception); `modules.read_content(mid: str, kind: str, id: str) -> dict` returning `{"kind", "id", "name", "body", "keys", **extra frontmatter, "sheet_type", "fields"}`. Raises `ModuleNotFound` (bad `mid`) or `ContentNotFound` (bad `kind`/`id`). Consumed by Task 8's routes.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_modules_store.py`:

```python
def test_read_content_plain(monkeypatch, tmp_path):
    content = {"items/lantern.md": "---\nname: Lantern of Winnowing\nkeys: lantern, glow\n---\nA soft lantern.\n"}
    make_pack(_home(monkeypatch, tmp_path), content=content)
    entry = modules.read_content("testmod", "items", "lantern")
    assert entry["name"] == "Lantern of Winnowing"
    assert entry["body"] == "A soft lantern.\n"
    assert entry["keys"] == "lantern, glow"
    assert entry["sheet_type"] is None
    assert entry["fields"] == {}


def test_read_content_statted(monkeypatch, tmp_path):
    content = {
        "items/orb.md": "---\nname: Orb\n---\nAn orb.\n",
        "items/orb.sheet.json": json.dumps({"sheet_type": "talisman-like", "fields": {"power": 2}}),
    }
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["sheet_types"]["talisman-like"] = {
        "label": "Talisman-like", "kind": "items", "groups": [],
        "fields": [{"key": "power", "type": "dots", "max": 5}],
    }
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets, content=content)
    entry = modules.read_content("testmod", "items", "orb")
    assert entry["sheet_type"] == "talisman-like"
    assert entry["fields"] == {"power": 2}


def test_read_content_missing_module(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleNotFound):
        modules.read_content("ghost", "items", "lantern")


def test_read_content_missing_entry(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    with pytest.raises(modules.ContentNotFound):
        modules.read_content("testmod", "items", "nope")


def test_read_content_bad_kind_or_id(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    with pytest.raises(modules.ContentNotFound):
        modules.read_content("testmod", "characters", "mara")
    with pytest.raises(modules.ContentNotFound):
        modules.read_content("testmod", "items", "../escape")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k read_content -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.modules' has no attribute 'read_content'`.

- [ ] **Step 3: Implement `ContentNotFound` and `read_content`**

In `backend/src/grimoire/store/modules.py`, add the exception near the other exceptions (after `ModuleNotFound`, around line 27):

```python
class ContentNotFound(Exception):
    pass
```

Add `read_content` near `_load_content` (after it, around line 322):

```python
def _safe_id_like(value: str) -> bool:
    return isinstance(value, str) and bool(value) and value not in (".", "..") \
        and "/" not in value and "\\" not in value


def read_content(mid: str, kind: str, id: str) -> dict:
    root, _source = pack_root(mid)  # raises ModuleNotFound
    if kind not in CONTENT_KINDS or not _safe_id_like(id):
        raise ContentNotFound(f"{kind}/{id}")
    p = root / "content" / kind / f"{id}.md"
    if not p.exists():
        raise ContentNotFound(f"{kind}/{id}")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    out = {"kind": kind, "id": id, "name": meta.get("name", id), "body": body,
           "keys": meta.get("keys", ""), "sheet_type": None, "fields": {}}
    for k, v in meta.items():
        if k not in ("name", "keys"):
            out[k] = v
    sidecar = root / "content" / kind / f"{id}.sheet.json"
    if sidecar.exists():
        try:
            stat = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            stat = {}
        if isinstance(stat, dict):
            out["sheet_type"] = stat.get("sheet_type")
            out["fields"] = stat.get("fields", {}) if isinstance(stat.get("fields"), dict) else {}
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k read_content -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full modules test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): read_content() — full module content entry incl. body"
```

---

### Task 5: Atomic write helper + `_checked_write` refactor (sheets.py)

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py`

**Interfaces:**
- Produces: `_atomic_write_json(path: Path, data: dict) -> None` (temp-file-in-same-directory + `os.replace`); `_validate_write_target(mid, file_kind, eid, sheet_type) -> dict` (shared prelude, returns `sheets_def`, raises `SheetError`). Both consumed by Task 6/7's new write paths and by the refactored `_checked_write`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_sheets_store.py`:

```python
def test_atomic_write_leaves_no_tmp_file(tmp_path):
    p = tmp_path / "sub" / "sheet.json"
    sheets._atomic_write_json(p, {"sheet_type": "medium", "fields": {}})
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["sheet_type"] == "medium"
    leftovers = list((tmp_path / "sub").glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_failure_leaves_no_tmp_file(tmp_path, monkeypatch):
    p = tmp_path / "sheet.json"

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(sheets.os, "replace", boom)
    with pytest.raises(OSError):
        sheets._atomic_write_json(p, {"sheet_type": "medium", "fields": {}})
    assert not p.exists()
    assert list(tmp_path.glob("*.tmp")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k atomic_write -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.sheets' has no attribute '_atomic_write_json'`.

- [ ] **Step 3: Implement `_atomic_write_json` and refactor `_checked_write`**

In `backend/src/grimoire/store/sheets.py`, add imports at the top (`os` and `tempfile` alongside the existing `json`, `shutil`):

```python
import json
import os
import shutil
import tempfile
from pathlib import Path
```

Add the helper right after the module-level constants (after `FILE_KINDS`, around line 25):

```python
def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via a same-directory temp file + os.replace, so a crash
    mid-write can never leave a half-written sheet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
```

Add the shared validation prelude right before `_checked_write` (around line 172):

```python
def _validate_write_target(mid: str, file_kind: str, eid: str, sheet_type: str) -> dict:
    """Shared prelude for every checked sheet write: validates file_kind/eid/
    sheet_type and returns the resolved sheets definition. Raises SheetError."""
    if file_kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {file_kind!r}")
    if not _safe_part(eid):
        raise SheetError(f"bad entity id {eid!r}")
    if not isinstance(sheet_type, str) or not sheet_type:
        raise SheetError("sheet_type must be a non-empty string")
    sheets_def = modules.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        raise SheetError(f"unknown sheet type {sheet_type!r}")
    if st.get("kind") != sheet_kind(file_kind):
        raise SheetError(
            f"sheet type {sheet_type!r} targets {st.get('kind')!r}, "
            f"not {sheet_kind(file_kind)!r}")
    return sheets_def
```

Replace the body of `_checked_write` (currently lines ~172-201) with:

```python
def _checked_write(path: Path, mid: str, file_kind: str, eid: str,
                   sheet_type: str, fields: dict | None) -> None:
    sheets_def = _validate_write_target(mid, file_kind, eid, sheet_type)
    if fields is None:
        fields = default_fields(sheets_def, sheet_type)
    else:
        if not isinstance(fields, dict):
            raise SheetError("fields must be an object")
        allowed = {f.get("key") for f in modules.assembled_fields(sheets_def, sheet_type)
                   if isinstance(f, dict)}
        fields = {k: v for k, v in fields.items() if k in allowed}
        errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields})
```

(This is a pure refactor: the `if file_kind not in FILE_KINDS` ... `sheets_def = modules.load_pack(mid)["sheets"]` ... kind-mismatch prelude moves into `_validate_write_target`; the trailing `path.parent.mkdir(...)` + `path.write_text(...)` becomes the one `_atomic_write_json` call. Behavior for every existing caller is unchanged — same exceptions, same file shape, just written atomically.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k atomic_write -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full sheets test file to confirm the refactor didn't break anything**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q`
Expected: all pass (every existing `write`/`write_world` test exercises `_checked_write` through its public callers, so this is the regression check for the refactor).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "refactor(sheets): atomic temp-file+replace writes; extract shared write-target validation"
```

---

### Task 6: `write_creation` / `write_world_creation` (sheets.py)

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py`

**Interfaces:**
- Consumes: `_validate_write_target`, `_atomic_write_json`, `default_fields`, `modules.validate_sheet_values`, `expressions.evaluate` (Task 5's helpers + existing).
- Produces: `sheets.write_creation(cid: str, kind: str, eid: str, sheet_type: str, spends: dict[str, dict[str, int]]) -> None`; `sheets.write_world_creation(wid: str, mid: str, kind: str, eid: str, sheet_type: str, spends: dict) -> None`. Both raise `SheetError` on any pool/range violation. Consumed by Task 9's routes.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sheets_store.py`. First, a module fixture with a `creation` block — reuse the existing `_campaign` helper but bind to a small local pack via `monkeypatch` isn't needed; instead extend `pool-basic`'s `medium` type in-memory isn't possible (it's a real builtin pack read from disk), so these tests write a temporary user-library module with a `creation` block, mirroring `test_modules_store.py`'s `make_pack`:

```python
def _campaign_with_creation_module(monkeypatch, tmp_path):
    """A user-library module 'chargen' with one sheet type ('hero') that has
    two creation pools, for write_creation tests."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "chargen"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Chargen Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [
                    {"key": "strength", "type": "number", "min": 1, "max": 20, "default": 10},
                    {"key": "wits", "type": "dots", "max": 5, "default": 1},
                ],
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "hp", "type": "resource", "max": 10}],
                "creation": {"pools": {"attributes": {
                    "budget": 6, "costs": {"strength": 2, "wits": 1}}}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module="chargen")
    return wid, cid


def test_write_creation_happy_path(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero",
                          {"attributes": {"strength": 12, "wits": 3}})
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"] == []
    assert s["fields"]["strength"] == 12   # (12-1)*2 = 22 -- wait, see next test for the exact math
    assert s["fields"]["wits"] == 3


def test_write_creation_over_budget_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 20, "wits": 5}})


def test_write_creation_field_outside_range_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 999, "wits": 0}})


def test_write_creation_field_not_in_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"hp": 5}})


def test_write_creation_omitted_costed_field_uses_floor_not_default(monkeypatch, tmp_path):
    # strength's schema default is 10 (well above its floor of 1) but it's
    # omitted from spends -- must resolve to the pool floor (1), not the
    # schema default (10), or the budget-omission loophole reopens.
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero",
                          {"attributes": {"wits": 2}})
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1
    assert s["fields"]["wits"] == 2


def test_write_creation_unknown_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"ghost_pool": {"strength": 12}})


def test_write_creation_empty_spends_falls_through_to_defaults(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero", {})
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1   # floor, not schema default 10 -- see note below
    assert s["fields"]["hp"] == {"current": 10, "max": 10}  # non-costed field: schema default


def test_write_world_creation(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_world_creation(wid, "chargen", "characters", "mara", "hero",
                                {"attributes": {"strength": 14}})
    s = sheets.read_world(wid, "chargen", "characters", "mara")
    assert s["fields"]["strength"] == 14
```

Note on `test_write_creation_empty_spends_falls_through_to_defaults`: an
empty `spends` map still means every pool's `costs` fields resolve to
their **floor** (0/min), same as any other omitted costed field — per the
spec's fix (round-2 Codex finding), a costed field's schema `default` is
never consulted by `write_creation`. Only fields with **no** pool costing
them (like `hp` here) fall back to `sheets.default_fields`'s schema
defaults.

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k write_creation -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.sheets' has no attribute 'write_creation'`.

- [ ] **Step 3: Implement `write_creation`/`write_world_creation`**

Add to `backend/src/grimoire/store/sheets.py`, after `write`/`write_world` (after the existing `write_world` function, before `delete_world`):

```python
def _pool_floor(field: dict) -> int:
    if field.get("type") == "number":
        m = field.get("min")
        return m if isinstance(m, int) and not isinstance(m, bool) else 0
    return 0  # dots/track floor is always 0


def _pool_group_fields(sheets_def: dict, pool_id: str) -> dict[str, dict]:
    group = sheets_def.get("groups", {}).get(pool_id, {})
    fields = group.get("fields", []) if isinstance(group, dict) else []
    if not isinstance(fields, list):
        return {}
    return {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}


def _pool_budget(pool: dict) -> int:
    raw = pool.get("budget", 0)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return expressions.evaluate(str(raw), {})


def _checked_creation_write(path: Path, mid: str, file_kind: str, eid: str,
                            sheet_type: str, spends: dict) -> None:
    sheets_def = _validate_write_target(mid, file_kind, eid, sheet_type)
    if not isinstance(spends, dict):
        raise SheetError("spends must be an object")
    st = sheets_def["sheet_types"][sheet_type]
    pools = st.get("creation", {}).get("pools", {}) if isinstance(st.get("creation"), dict) else {}
    for pool_id in spends:
        if pool_id not in pools:
            raise SheetError(f"unknown pool {pool_id!r}")
    fields = default_fields(sheets_def, sheet_type)
    for pool_id, pool in pools.items():
        if not isinstance(pool, dict):
            continue
        costs = pool.get("costs", {})
        group_fields = _pool_group_fields(sheets_def, pool_id)
        pool_spends = spends.get(pool_id, {})
        if not isinstance(pool_spends, dict):
            raise SheetError(f"spends[{pool_id!r}] must be an object")
        for extra in set(pool_spends) - set(costs):
            raise SheetError(f"{extra!r} is not a costed field of pool {pool_id!r}")
        total = 0
        for field_key, cost in costs.items():
            fdef = group_fields.get(field_key, {})
            floor = _pool_floor(fdef)
            value = pool_spends.get(field_key, floor)
            if not isinstance(value, int) or isinstance(value, bool):
                raise SheetError(f"{field_key!r}: expected an integer")
            fmax = fdef.get("max")
            hi = fmax if isinstance(fmax, int) and not isinstance(fmax, bool) else floor
            if not floor <= value <= hi:
                raise SheetError(f"{field_key!r}: outside {floor}..{hi}")
            total += (value - floor) * cost
            fields[field_key] = value
        budget = _pool_budget(pool)
        if total > budget:
            raise SheetError(f"pool {pool_id!r}: spent {total}, budget {budget}")
    errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
    if errs:
        raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields})


def write_creation(cid: str, kind: str, eid: str, sheet_type: str,
                   spends: dict[str, dict[str, int]]) -> None:
    mid = modules.resolve(cid)
    if mid is None:
        raise SheetError("no module resolved for this campaign")
    _checked_creation_write(_campaign_path(cid, kind, eid), mid, kind, eid, sheet_type, spends)


def write_world_creation(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                         spends: dict[str, dict[str, int]]) -> None:
    modules.pack_root(mid)  # raises ModuleNotFound
    _checked_creation_write(_world_path(wid, mid, kind, eid), mid, kind, eid, sheet_type, spends)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k write_creation -v`
Expected: PASS. In `test_write_creation_happy_path`, the assertion
comment about "(12-1)*2 = 22" is a stray note left from drafting the
formula by hand — the test only asserts the *stored field value* (12),
not the spend total, so no code change is needed; delete that comment
line if it's confusing when you write the test file.

- [ ] **Step 5: Run the full sheets test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): write_creation/write_world_creation — server-enforced pool budgets"
```

---

### Task 7: `advance()` + per-campaign lock registry (sheets.py)

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py`

**Interfaces:**
- Produces: `sheets.advance(cid: str, kind: str, eid: str, field_key: str) -> dict` (same return shape as `sheets.read`). Raises `SheetError`. Consumed by Task 9's route.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sheets_store.py`:

```python
def _campaign_with_advancement_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "advtest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Advancement Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [{"key": "wits", "type": "dots", "max": 5, "default": 1}],
                "derived": {"double_wits": "wits * 2"},
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "xp", "type": "resource", "max": 999}],
                "advancement": {"pool": "xp", "costs": {
                    "wits": "new * 3", "double_wits": "no"}},
            },
        },
    }).replace('"double_wits": "no"', '')[:-1] + ('' if False else '') , encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module="advtest")
    sheets.write(cid, "characters", "mara", "hero", {"wits": 2, "xp": {"current": 20, "max": 999}})
    return cid


def test_advance_happy_path(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    s = sheets.advance(cid, "characters", "mara", "wits")
    assert s["fields"]["wits"] == 3
    assert s["fields"]["xp"]["current"] == 20 - 9   # new=3, cost = 3*3
    assert s["errors"] == []


def test_advance_insufficient_balance(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "poor", "hero", {"wits": 2, "xp": {"current": 1, "max": 999}})
    with pytest.raises(sheets.SheetError, match="needs 9"):
        sheets.advance(cid, "characters", "poor", "wits")


def test_advance_field_at_max(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "capped", "hero", {"wits": 5, "xp": {"current": 999, "max": 999}})
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "capped", "wits")


def test_advance_no_advancement_block(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)  # pool-basic has no advancement block yet
    sheets.write(cid, "characters", "mara", "medium", None)
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "mara", "vigor")


def test_advance_unknown_field(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "mara", "ghost")


def test_advance_recomputes_from_current_values(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.advance(cid, "characters", "mara", "wits")   # 2 -> 3, cost 9, xp 20 -> 11
    s = sheets.advance(cid, "characters", "mara", "wits")  # 3 -> 4, cost 12, xp 11 -> -1 should fail
    # second call should have failed with insufficient balance -- rewritten below
```

Replace that last stray test with a correct one (the draft above is
intentionally left half-written to show the reasoning — write the real
version):

```python
def test_advance_recomputes_from_current_values(monkeypatch, tmp_path):
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.advance(cid, "characters", "mara", "wits")   # 2 -> 3, cost 3*3=9, xp 20 -> 11
    with pytest.raises(sheets.SheetError, match="needs 12"):
        sheets.advance(cid, "characters", "mara", "wits")  # 3 -> 4 would cost 4*3=12, only have 11


def test_advance_concurrent_calls_only_one_succeeds(monkeypatch, tmp_path):
    import threading
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "duelist", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}})
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            sheets.advance(cid, "characters", "duelist", "wits")
            results.append("ok")
        except sheets.SheetError:
            results.append("rejected")

    t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["ok", "rejected"]
    s = sheets.read(cid, "characters", "duelist")
    assert s["fields"]["wits"] == 3
    assert s["fields"]["xp"]["current"] == 0


def test_advance_first_ever_call_cold_registry_race(monkeypatch, tmp_path):
    # exercises _lock_for's cold-registry path directly, not just contention
    # on an already-created lock
    import threading
    cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "first", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}})
    assert cid not in sheets._campaign_locks
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            sheets.advance(cid, "characters", "first", "wits")
            results.append("ok")
        except sheets.SheetError:
            results.append("rejected")

    t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["ok", "rejected"]
```

The `_campaign_with_advancement_module` helper above has a garbled line
building `sheets.json` (a leftover `.replace(...)` chain from drafting) —
write it cleanly instead:

```python
def _campaign_with_advancement_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "advtest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Advancement Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [{"key": "wits", "type": "dots", "max": 5, "default": 1}],
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "xp", "type": "resource", "max": 999}],
                "advancement": {"pool": "xp", "costs": {"wits": "new * 3"}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module="advtest")
    sheets.write(cid, "characters", "mara", "hero", {"wits": 2, "xp": {"current": 20, "max": 999}})
    return cid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k advance -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.sheets' has no attribute 'advance'`.

- [ ] **Step 3: Implement `advance()` and the lock registry**

Add `threading` to the imports at the top of `backend/src/grimoire/store/sheets.py`:

```python
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
```

Add near the bottom of the file (after `world_coverage`):

```python
# ---- advancement (#164, Phase 7): single resource pool, formula-priced raises ----

_registry_guard = threading.Lock()
_campaign_locks: dict[str, threading.Lock] = {}


def _lock_for(cid: str) -> threading.Lock:
    """Get-or-create the per-campaign lock atomically -- a plain
    `if cid not in _campaign_locks: ...` is a check-then-act race that can
    hand two concurrent first-ever callers different Lock objects."""
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.Lock())


def _advancement_cost(sheets_def: dict, type_id: str, field_key: str,
                      fields: dict, new: int) -> int:
    """Evaluate an advancement cost against a tentative post-raise scope:
    the raised field is set to `new` before recomputing derived values, so a
    cost formula referencing a derived name sees the post-raise state."""
    tentative = {**fields, field_key: new}
    scope = _numeric_scope(sheets_def, type_id, tentative)
    derived_errors: list[str] = []
    derived = _compute_derived(sheets_def, type_id, tentative, derived_errors)
    st = sheets_def.get("sheet_types", {}).get(type_id, {})
    adv = st.get("advancement", {}) if isinstance(st, dict) else {}
    expr = adv.get("costs", {}).get(field_key) if isinstance(adv, dict) else None
    if not isinstance(expr, str):
        raise SheetError(f"{field_key!r} is not advancement-eligible")
    try:
        cost = expressions.evaluate(expr, {**scope, **derived, "new": new})
    except expressions.ExpressionError as e:
        raise SheetError(f"advancement cost for {field_key!r}: {e}")
    if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
        raise SheetError(f"advancement cost for {field_key!r} must be a positive integer, got {cost!r}")
    return cost


def advance(cid: str, kind: str, eid: str, field_key: str) -> dict:
    lock = _lock_for(cid)
    with lock:
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        path = _campaign_path(cid, kind, eid)
        if not path.exists():
            raise SheetError("no sheet exists for this entity")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            raise SheetError(f"unreadable sheet file: {e}")
        sheet_type = data.get("sheet_type") if isinstance(data, dict) else None
        fields = data.get("fields") if isinstance(data, dict) and isinstance(data.get("fields"), dict) else {}
        sheets_def = modules.load_pack(mid)["sheets"]
        st = sheets_def.get("sheet_types", {}).get(sheet_type) if isinstance(sheet_type, str) else None
        if not isinstance(st, dict):
            raise SheetError("sheet has no valid sheet type")
        adv = st.get("advancement")
        if not isinstance(adv, dict):
            raise SheetError("this sheet type has no advancement rules")
        pool_key = adv.get("pool")
        costs = adv.get("costs", {})
        if field_key not in costs:
            raise SheetError(f"{field_key!r} is not advancement-eligible")
        field_defs = {f["key"]: f for f in modules.assembled_fields(sheets_def, sheet_type)
                      if isinstance(f, dict) and isinstance(f.get("key"), str)}
        fdef = field_defs.get(field_key, {})
        current = fields.get(field_key, 0)
        current = current if isinstance(current, int) and not isinstance(current, bool) else 0
        fmax = fdef.get("max")
        if isinstance(fmax, int) and not isinstance(fmax, bool) and current >= fmax:
            raise SheetError(f"{field_key!r} is already at its maximum ({fmax})")
        new = current + 1
        cost = _advancement_cost(sheets_def, sheet_type, field_key, fields, new)
        pool_val = fields.get(pool_key)
        balance = pool_val.get("current", 0) if isinstance(pool_val, dict) else 0
        if balance < cost:
            raise SheetError(f"needs {cost} {pool_key}, have {balance}")
        pool_max = pool_val.get("max", balance) if isinstance(pool_val, dict) else 0
        new_fields = {**fields, field_key: new,
                      pool_key: {"current": balance - cost, "max": pool_max}}
        _atomic_write_json(path, {"sheet_type": sheet_type, "fields": new_fields})
        return _read_path(path, kind, mid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k advance -v`
Expected: PASS (8 passed). The concurrency tests are inherently a little
timing-sensitive but should pass reliably: the lock genuinely serializes
the critical section, so the two threads' outcomes are deterministic
regardless of scheduling — one reads balance 9 and succeeds, the other
(whichever runs second) reads the now-debited balance 0 and is rejected.

- [ ] **Step 5: Run the full sheets test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): advance() -- lock-guarded, atomic, tentative-post-raise-scope XP spend"
```

---

### Task 8: Routes — content read + instantiate (world/campaign)

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.modules.read_content`, `store.modules.ContentNotFound`, `store.entities.create_entity`, `store.overlay.create_entity`, `store.entity_schema.field_keys`, `store.sheets.write_world`/`write`.
- Produces: `GET /api/modules/{mid}/content/{kind}/{id}`, `POST /api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}`, `POST /api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}` — all returning JSON, no request body for the POSTs.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py`, near `test_modules_api` (around line 2806). First, a small helper to seed a user-library module with content, mirroring the pattern already used for `_world`/`_campaign`:

```python
def _seed_content_module(client, tmp_path, mid="contentmod", statted=False):
    import json as _json
    home = tmp_path  # GRIMOIRE_HOME is already tmp_path via the client fixture
    d = home / "modules" / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Content Test\n---\n", encoding="utf-8")
    sheets_def = {"groups": {}, "sheet_types": {}}
    if statted:
        sheets_def["sheet_types"]["trinket"] = {
            "label": "Trinket", "kind": "items", "groups": [],
            "fields": [{"key": "power", "type": "dots", "max": 5}],
        }
    (d / "sheets.json").write_text(_json.dumps(sheets_def), encoding="utf-8")
    cd = d / "content" / "items"
    cd.mkdir(parents=True)
    (cd / "lantern.md").write_text(
        "---\nname: Lantern of Winnowing\nkeys: lantern\n---\nA soft lantern.\n", encoding="utf-8")
    if statted:
        (cd / "lantern.sheet.json").write_text(
            _json.dumps({"sheet_type": "trinket", "fields": {"power": 2}}), encoding="utf-8")
    return mid


def test_module_content_read(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)
    r = client.get(f"/api/modules/{mid}/content/items/lantern")
    assert r.status_code == 200
    assert r.json()["name"] == "Lantern of Winnowing"
    assert r.json()["body"] == "A soft lantern.\n"
    assert client.get(f"/api/modules/{mid}/content/items/nope").status_code == 404
    assert client.get("/api/modules/ghost/content/items/lantern").status_code == 404


def test_instantiate_into_world(client, tmp_path):
    mid = _seed_content_module(client, tmp_path, statted=True)
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 200
    eid = r.json()["id"]
    entity = client.get(f"/api/worlds/{wid}/items/{eid}").json()
    assert entity["meta"]["name"] == "Lantern of Winnowing"
    sheet = client.get(f"/api/worlds/{wid}/sheets/{mid}/items/{eid}").json()["sheet"]
    assert sheet["sheet_type"] == "trinket"
    assert sheet["fields"]["power"] == 2


def test_instantiate_into_campaign(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)  # not statted -- no sheet expected
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": mid})
    r = client.post(f"/api/campaigns/{cid}/items/instantiate/{mid}/lantern")
    assert r.status_code == 200
    eid = r.json()["id"]
    entity = client.get(f"/api/campaigns/{cid}/items/{eid}").json()
    assert entity["meta"]["name"] == "Lantern of Winnowing"
    assert client.get(f"/api/campaigns/{cid}/sheets/items/{eid}").json()["sheet"] is None


def test_instantiate_unknown_content_404(client, tmp_path):
    mid = _seed_content_module(client, tmp_path)
    wid = _world(client)
    assert client.post(f"/api/worlds/{wid}/items/instantiate/{mid}/ghost").status_code == 404
    assert client.post(f"/api/worlds/{wid}/items/instantiate/ghostmod/lantern").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "content_read or instantiate" -v`
Expected: FAIL — 404s on the new routes (not yet registered).

- [ ] **Step 3: Implement the routes**

In `backend/src/grimoire/routes.py`, add a small helper near `_check_fields` (around line 1230, since it needs `store.entity_schema` the same way):

```python
def _content_fields(kind: str, content: dict) -> dict:
    return {k: content[k] for k in store.entity_schema.field_keys(kind) if k in content}
```

Add the three new routes right after the existing `get_world_sheets_index`/`get_world_sheets`/`get_world_sheet`/`put_world_sheet`/`delete_world_sheet` block (after line 538, before the world-tags section), and mirror the campaign one after the existing campaign sheets block (after line 2441, before `get_scene_context`):

```python
# registered before the generic /worlds/{wid}/{kind} entity routes below --
# distinct path shape (extra segments), so no collision either way, but
# grouped with the other module/sheet routes for readability
@router.get("/modules/{mid}/content/{kind}/{id}")
def get_module_content(mid: str, kind: str, id: str):
    try:
        return store.modules.read_content(mid, kind, id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")


@router.post("/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}")
def post_world_instantiate(wid: str, kind: str, mid: str, content_id: str):
    root = _world_root_or_404(wid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.entities.create_entity(root, kind, content["name"], content["body"],
                                           content.get("keys", ""), "",
                                           fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write_world(wid, mid, kind, eid, content["sheet_type"], content.get("fields"))
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}
```

And the campaign equivalent, placed after `delete_campaign_sheet` (line 2441):

```python
@router.post("/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}")
def post_campaign_instantiate(cid: str, kind: str, mid: str, content_id: str):
    _campaign_root_or_404(cid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.overlay.create_entity(cid, kind, content["name"], content["body"],
                                          content.get("keys", ""), "",
                                          fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write(cid, kind, eid, content["sheet_type"], content.get("fields"))
        except store.sheets.SheetError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "content_read or instantiate" -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full routes test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): module content read + world/campaign instantiate"
```

---

### Task 9: Routes — creation write + advance

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.sheets.write_creation`/`write_world_creation`/`advance`.
- Produces: `PUT /api/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation`, `PUT /api/campaigns/{cid}/sheets/{kind}/{eid}/creation`, `POST /api/campaigns/{cid}/sheets/{kind}/{eid}/advance`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py`:

```python
def test_campaign_sheet_creation_route(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    base = f"/api/campaigns/{cid}/sheets/characters/{chid}/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 200
    assert r.json()["sheet"]["sheet_type"] == "medium"
    r = client.put(base, json={"sheet_type": "ghost", "spends": {}})
    assert r.status_code == 400


def test_world_sheet_creation_route(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/mara/creation"
    r = client.put(base, json={"sheet_type": "medium", "spends": {}})
    assert r.status_code == 200
    assert r.json()["sheet"]["sheet_type"] == "medium"
    assert client.put(f"/api/worlds/{wid}/sheets/ghost/characters/mara/creation",
                      json={"sheet_type": "medium", "spends": {}}).status_code == 404


def test_advance_route(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()["character"]
    client.put(f"/api/campaigns/{cid}/sheets/characters/{chid}", json={"sheet_type": "medium"})
    # pool-basic's "medium" has no advancement block yet (added in Task 10) -- expect 400
    r = client.post(f"/api/campaigns/{cid}/sheets/characters/{chid}/advance", json={"field": "wits"})
    assert r.status_code == 400
    assert client.post(f"/api/campaigns/nope/sheets/characters/{chid}/advance",
                       json={"field": "wits"}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "creation_route or advance_route" -v`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Implement the routes and models**

In `backend/src/grimoire/routes.py`, add two models near `SheetBody` (around line 98):

```python
class SheetCreationBody(BaseModel):
    sheet_type: str
    spends: dict[str, dict[str, int]] = {}


class SheetAdvanceBody(BaseModel):
    field: str
```

Add the world creation route right after `put_world_sheet` (after line 532, before `delete_world_sheet`):

```python
@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation")
def put_world_sheet_creation(wid: str, mid: str, kind: str, eid: str, body: SheetCreationBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world_creation(wid, mid, kind, eid, body.sheet_type, body.spends)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}
```

Add the campaign creation + advance routes right after `delete_campaign_sheet` (after line 2441, or right after Task 8's `post_campaign_instantiate` if that's where you left off):

```python
@router.put("/campaigns/{cid}/sheets/{kind}/{eid}/creation")
def put_campaign_sheet_creation(cid: str, kind: str, eid: str, body: SheetCreationBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write_creation(cid, kind, eid, body.sheet_type, body.spends)
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.post("/campaigns/{cid}/sheets/{kind}/{eid}/advance")
def post_sheet_advance(cid: str, kind: str, eid: str, body: SheetAdvanceBody):
    _campaign_root_or_404(cid)
    try:
        return {"sheet": store.sheets.advance(cid, kind, eid, body.field)}
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "creation_route or advance_route" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full routes test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: all pass.

- [ ] **Step 6: Run the entire backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass. This is the checkpoint before touching reference module fixtures (Task 10) and frontend (Tasks 11-15).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): sheet-creation write + advance endpoints"
```

---

### Task 10: Reference module fleshing (pool-basic + d20-basic)

**Files:**
- Modify: `backend/src/grimoire/store/builtin_modules/pool-basic/sheets.json`
- Modify: `backend/src/grimoire/store/builtin_modules/d20-basic/sheets.json`
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Consumes: Tasks 1-3's schema (ref/creation/advancement).
- Produces: both reference packs exercise every new schema piece and stay clean under `load_pack`. This is the first end-to-end proof the schema additions compose correctly on real (if small) fixtures, ahead of frontend work.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_modules_store.py`, extending `test_fleshed_reference_packs`:

```python
def test_phase7_reference_fleshing(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    pool = modules.load_pack("pool-basic")
    assert pool["errors"] == []
    medium = pool["sheets"]["sheet_types"]["medium"]
    ref_fields = [f for f in medium["fields"] if f["type"] == "ref"]
    assert ref_fields and ref_fields[0]["ref_kind"] == "creatures"
    assert "creation" in medium and "attributes" in medium["creation"]["pools"]
    assert "advancement" in medium and medium["advancement"]["pool"] == "xp"

    d20 = modules.load_pack("d20-basic")
    assert d20["errors"] == []
    adept = d20["sheets"]["sheet_types"]["adept"]
    ref_fields = [f for f in adept["fields"] if f["type"] == "ref"]
    assert ref_fields and ref_fields[0]["ref_kind"] == "lore"
    assert "creation" in adept and set(adept["creation"]["pools"]) >= {"attributes", "skills"}
    assert "advancement" in adept and adept["advancement"]["pool"] == "xp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k phase7_reference_fleshing -v`
Expected: FAIL — neither pack has `ref`/`creation`/`advancement` yet.

- [ ] **Step 3: Flesh `pool-basic`'s `medium` type**

Edit `backend/src/grimoire/store/builtin_modules/pool-basic/sheets.json` — change the `"medium"` sheet type entry to:

```json
    "medium": {
      "label": "Medium",
      "kind": "characters",
      "groups": ["attributes", "abilities"],
      "fields": [
        {"key": "essence", "label": "Essence", "type": "resource", "max": 10},
        {"key": "health", "label": "Health", "type": "track", "max": 7},
        {"key": "quirk", "label": "Quirk", "type": "text"},
        {"key": "gear", "label": "Gear", "type": "list"},
        {"key": "bound_spirits", "label": "Bound Spirits", "type": "ref", "ref_kind": "creatures"},
        {"key": "xp", "label": "Experience", "type": "resource", "max": 999}
      ],
      "derived": {"sight_pool": "wits + occult"},
      "creation": {
        "pools": {
          "attributes": {"budget": 6, "costs": {"vigor": 2, "grace": 2, "wits": 2}},
          "abilities": {"budget": 6, "costs": {"brawl": 1, "occult": 1, "empathy": 1}}
        }
      },
      "advancement": {
        "pool": "xp",
        "costs": {"wits": "new * 3", "occult": "new * 2"}
      }
    },
```

- [ ] **Step 4: Flesh `d20-basic`'s `adept` type**

Edit `backend/src/grimoire/store/builtin_modules/d20-basic/sheets.json` — change the `"adept"` sheet type entry to:

```json
    "adept": {
      "label": "Adept",
      "kind": "characters",
      "groups": ["attributes", "skills"],
      "fields": [
        {"key": "hp", "label": "Hit Points", "type": "resource", "max": 8},
        {"key": "spell_slots", "label": "Spell Slots", "type": "track", "max": 4},
        {"key": "spells", "label": "Spells", "type": "list"},
        {"key": "known_spells", "label": "Known Spells", "type": "ref", "ref_kind": "lore"},
        {"key": "xp", "label": "Experience", "type": "resource", "max": 999}
      ],
      "derived": {"spell_bonus": "mind_mod + 2"},
      "creation": {
        "pools": {
          "attributes": {"budget": 6, "costs": {"strength": 2, "dexterity": 2, "mind": 2}},
          "skills": {"budget": 6, "costs": {"athletics": 1, "stealth": 1, "arcana": 1}}
        }
      },
      "advancement": {
        "pool": "xp",
        "costs": {"arcana": "new * 3", "mind": "new * 5"}
      }
    },
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k phase7_reference_fleshing -v`
Expected: PASS

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass — including `test_builtin_reference_modules_validate` and `test_fleshed_reference_packs`, which read these same files and must keep passing unmodified (this task only adds fields/blocks, it doesn't remove or rename anything the earlier assertions depend on — `medium`'s `quirk`/`gear` and `adept`'s `spells` list field are untouched).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/builtin_modules/pool-basic/sheets.json \
        backend/src/grimoire/store/builtin_modules/d20-basic/sheets.json \
        backend/tests/test_modules_store.py
git commit -m "feat(builtin-modules): flesh pool-basic/d20-basic with ref/creation/advancement"
```

---

### Task 11: Frontend types + API functions (client.ts)

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: none (pure types + thin wrappers around `request()`; exercised indirectly by Tasks 12-15's component tests)

**Interfaces:**
- Produces: `ModuleField.ref_kind?: string`; `ModuleSheetType.creation?`/`advancement?`; `ModuleContentEntry` type; `api.readModuleContent`, `api.instantiateContent`, `api.putSheetCreation`, `api.advanceSheet`.

- [ ] **Step 1: Extend the `ModuleField`/`ModuleSheetType` types**

In `frontend/src/api/client.ts`, change (around line 266-273):

```typescript
export type ModuleField = {
  key: string; label?: string; type: string;
  max?: number; min?: number; default?: number;
  ref_kind?: string;
};
export type ModuleSheetType = {
  label: string; kind: string; groups: string[];
  fields: ModuleField[]; derived?: Record<string, string>;
  creation?: { pools: Record<string, { budget: number | string; costs: Record<string, number> }> };
  advancement?: { pool: string; costs: Record<string, string> };
};
```

Add a content-entry type near `ModuleDetail` (around line 274-284, right after it):

```typescript
export type ModuleContentEntry = {
  kind: string; id: string; name: string; body: string; keys: string;
  sheet_type: string | null; fields: Record<string, unknown>;
};
```

- [ ] **Step 2: Add the new API functions**

In `frontend/src/api/client.ts`, in the `api` object's "modules" section (near `readModule`, around line 664), add:

```typescript
  readModuleContent: (mid: string, kind: string, id: string) =>
    request<ModuleContentEntry>("GET", `/api/modules/${mid}/content/${kind}/${id}`),
  instantiateContent: (scope: EntityScope, kind: string, mid: string, contentId: string) =>
    request<{ id: string }>(
      "POST",
      `${entityBase(scope)}/${kind}/instantiate/${mid}/${contentId}`),
```

In the "sheets" section (near `putSheet`, around line 694), add:

```typescript
  putSheetCreation: (scope: EntityScope, mid: string, kind: string, eid: string,
                     body: { sheet_type: string; spends: Record<string, Record<string, number>> }) =>
    request<{ sheet: Sheet }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}/creation`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}/creation`,
      body),
  advanceSheet: (cid: string, kind: string, eid: string, field: string) =>
    request<{ sheet: Sheet }>("POST", `/api/campaigns/${cid}/sheets/${kind}/${eid}/advance`, { field }),
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: no new errors (this task only adds types/functions, nothing yet consumes them incorrectly).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(client): ref/creation/advancement types; content, instantiate, creation, advance API fns"
```

---

### Task 12: EntityEditor — merge module content, read-only preview, Instantiate

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx`
- Modify: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `api.readModuleContent`, `api.instantiateContent` (Task 11); `module.content` (already a prop, already fetched by `ModuleDetail`, previously unused here).
- Produces: template rows merged into the rail; a `contentPreview: ModuleContentEntry | null` view state; no new props on `EntityEditor` itself (it already receives `module`).

- [ ] **Step 1: Write the failing tests**

`EntityEditor.test.tsx`'s existing `vi.mock("../api/client", () => ({ ENTITY_FIELDS: {...}, api: {<literal>} }))` block (no `...actual` spread here, unlike `CharacterEditor.test.tsx`/`PCEditor.test.tsx`) needs `readModuleContent: vi.fn()` and `instantiateContent: vi.fn()` added to its `api` literal. Its `beforeEach` already resets mocks with `vi.clearAllMocks()` and sets defaults via `(api.fn as any).mockResolvedValue(...)` — follow that exact style, not `vi.mocked(...)`.

Add to `frontend/src/components/EntityEditor.test.tsx`:

```typescript
it("merges module content into the rail as templates and previews on click", async () => {
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  } as any;
  (api.listEntities as any).mockResolvedValue([{ id: "sword", name: "Sword" }]);
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  await screen.findByText("Sword");
  const templateRow = await screen.findByText("Lantern of Winnowing");
  fireEvent.click(templateRow);
  await screen.findByText("A soft lantern.");
  expect(screen.getByText("Instantiate")).toBeInTheDocument();
  expect(screen.queryByText("Edit")).not.toBeInTheDocument();
});

it("instantiate creates a real record and selects it", async () => {
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  } as any;
  (api.listEntities as any).mockResolvedValue([]);
  (api.instantiateContent as any).mockResolvedValue({ id: "lantern" });
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "lantern", name: "Lantern of Winnowing" }, body: "A soft lantern.",
  });
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("Lantern of Winnowing"));
  fireEvent.click(await screen.findByText("Instantiate"));
  await waitFor(() => expect(api.instantiateContent).toHaveBeenCalledWith(
    { kind: "world", id: "w1" }, "items", "testmod", "lantern"));
  await screen.findByText("Edit"); // back to a normal read-only view of the new record
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run EntityEditor -t "merges module content|instantiate creates"`
Expected: FAIL — no template rows render yet, `Instantiate` doesn't exist.

- [ ] **Step 3: Implement the content merge + preview + instantiate**

In `frontend/src/components/EntityEditor.tsx`, add state near the top of the component (after the existing `useState` declarations, e.g. after `images`):

```typescript
  const [contentPreview, setContentPreview] = useState<ModuleContentEntry | null>(null);
```

Add `ModuleContentEntry` to the import line (line 4):

```typescript
import { api, ENTITY_FIELDS, type EntityKind, type EntityScope, type EntitySummary, type ModuleContentEntry, type ModuleDetail } from "../api/client";
```

Add a helper to select a content row, near the existing `select` function:

```typescript
  async function selectContent(id: string) {
    if (!module) return;
    setError(null);
    const entry = await api.readModuleContent(module.id, kind, id);
    setContentPreview(entry);
    setEditing(null);
    setMode("view");
  }

  async function instantiate() {
    if (!module || !contentPreview) return;
    setError(null);
    try {
      const { id } = await api.instantiateContent(scope, kind, module.id, contentPreview.id);
      setContentPreview(null);
      await reload();
      await select(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

Clear `contentPreview` wherever the existing code clears `editing`/resets
the form — in `resetForm()`, add `setContentPreview(null);`, and in
`select(id)` (the existing entity-row select), add `setContentPreview(null);`
at the top.

In the rail (`.editor-list`), after the existing `{items.map(row)}` /
`{kind === "lore" ? groups.map(...) : items.map(row)}` block, add the
template rows:

```typescript
        {module?.content.filter((c) => c.kind === kind).map((c) => (
          <button key={`content-${c.id}`} className="row content-row"
                  onClick={() => selectContent(c.id)}>
            <span className="row-name">{c.name}</span>
            <span className="chip">template</span>
          </button>
        ))}
```

In `.editor-body`, before the existing `{mode === "view" && editing ? (...) : (...)}` conditional, add a branch for the content preview (content preview takes priority when set, same rhythm as the existing view/form split):

```typescript
        {contentPreview ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{contentPreview.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{contentPreview.body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="primary" onClick={instantiate}>Instantiate</button>
              </div>
              <div className="side-section">
                <h4>Module</h4>
                <span className="chip on">{module?.manifest.name}</span>
              </div>
            </aside>
          </div>
        ) : mode === "view" && editing ? (
          /* ...existing view block unchanged... */
```

(The closing of this new ternary branch is the existing `mode === "view" && editing ? (` block — just prepend the `contentPreview ? (...) :` arm in front of it and keep everything else in `.editor-body` exactly as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run EntityEditor -t "merges module content|instantiate creates"`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full EntityEditor test file**

Run (from `frontend/`): `npx vitest run EntityEditor`
Expected: all pass (confirms the new `contentPreview` branch didn't disturb the existing view/edit rhythm).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx
git commit -m "feat(entity-editor): merge module content into the rail; read-only preview + Instantiate"
```

---

### Task 13: `CreationWizard` component + "+ New with sheet…" wiring

**Files:**
- Create: `frontend/src/components/CreationWizard.tsx`
- Test: `frontend/src/components/CreationWizard.test.tsx`
- Modify: `frontend/src/components/EntityEditor.tsx` (wire the wizard trigger)
- Modify: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `api.putSheetCreation` (Task 11), `ModuleDetail`, `EntityScope`.
- Produces: `CreationWizard` component, props `{ scope, kind, module, createRecord: (name: string) => Promise<string>, onDone: (newId: string) => void, onCancel: () => void }`.
  **`createRecord` is injected, not hardcoded to `api.createEntity`** — `CharacterEditor`'s "+ New character" uses `api.createCharacter(wid, {name})` and `PCEditor`'s "+ New PC" branches between `api.createPC(wid, ...)` and `api.createCampaignPC(cid, ...)` by scope, neither of which is the generic `EntityCreate` shape `api.createEntity` takes. The wizard only owns the shared part (name capture, sheet-type pick, budget spend); each consumer supplies its own kind-correct creation call. The wizard's form step collects **name only, no Body field** — `newCharacter`/`newPC` are already name-only (a bare `window.prompt`), so this matches them exactly; `EntityEditor`'s own plain "+ New" form *does* also collect Body up front, so a wizard-created generic entity (item/location/etc.) starts with an empty body where the plain-create path wouldn't — an accepted, minor simplification (fixable via the normal Edit step afterward) in exchange for one shared wizard form instead of three kind-specific ones.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/CreationWizard.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    putSheetCreation: vi.fn(),
  },
}));
import { api } from "../api/client";
import CreationWizard from "./CreationWizard";

beforeEach(() => {
  vi.clearAllMocks();
});

const scope = { kind: "world" as const, id: "w1" };

const module = {
  id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
  sheets: {
    groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", type: "dots", max: 5 }] } },
    sheet_types: {
      hero: {
        label: "Hero", kind: "items", groups: ["attributes"], fields: [],
        creation: { pools: { attributes: { budget: 3, costs: { vigor: 1 } } } },
      },
      plain: { label: "Plain", kind: "items", groups: [], fields: [] },
    },
  },
  checks: {}, rules: [], content: [], errors: [],
} as any;

describe("CreationWizard", () => {
  it("creates the record via createRecord, picks a type, spends a pool, and calls onDone", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => expect(createRecord).toHaveBeenCalledWith("Sword"));

    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Next"));

    const input = screen.getByLabelText("Vigor");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "items", "sword", { sheet_type: "hero", spends: { attributes: { vigor: 3 } } }));
    expect(onDone).toHaveBeenCalledWith("sword");
  });

  it("a type with no creation block skips straight to a budget-free create call", async () => {
    const createRecord = vi.fn().mockResolvedValue("shield");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "plain", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Shield" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "plain" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "items", "shield", { sheet_type: "plain", spends: {} }));
    expect(onDone).toHaveBeenCalledWith("shield");
  });

  it("blocks spending over a pool's budget", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={vi.fn()} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Next"));
    const createButton = screen.getByText("Create") as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Vigor"), { target: { value: "10" } });
    expect(createButton.disabled).toBe(true);
  });

  it("kind='pcs' finds sheet types declared with kind='characters' (typeKind mapping)", async () => {
    const pcModule = {
      ...module,
      sheets: {
        groups: {},
        sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } },
      },
    };
    const createRecord = vi.fn().mockResolvedValue("elara");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="pcs" module={pcModule as any}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
    fireEvent.click(screen.getByText("Next"));
    const select = await screen.findByLabelText("Sheet type");
    expect(within(select).getByText("Hero")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "pcs", "elara", { sheet_type: "hero", spends: {} }));
    expect(onDone).toHaveBeenCalledWith("elara");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run CreationWizard`
Expected: FAIL — `Failed to resolve import "./CreationWizard"`.

- [ ] **Step 3: Implement `CreationWizard.tsx`**

Create `frontend/src/components/CreationWizard.tsx`:

```typescript
import { useState } from "react";
import { api, type EntityScope, type ModuleDetail } from "../api/client";
import { Field } from "./Field";
import { typeKind } from "./SheetEditor";

type Step = "form" | "type" | "budget";

export default function CreationWizard({ scope, kind, module, createRecord, onDone, onCancel }: {
  scope: EntityScope; kind: string; module: ModuleDetail;
  createRecord: (name: string) => Promise<string>;
  onDone: (id: string) => void; onCancel: () => void;
}) {
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [entityId, setEntityId] = useState<string | null>(null);
  const [sheetType, setSheetType] = useState("");
  const [spends, setSpends] = useState<Record<string, Record<string, number>>>({});
  const [error, setError] = useState<string | null>(null);

  // `kind` is the file/entity kind used for every API call (create, putSheetCreation) --
  // "pcs" is a real, distinct kind there. Sheet *types* are declared with the module
  // kind ("characters"), never "pcs" (mirrors backend sheets.sheet_kind()); typeKind()
  // is the same mapping SheetEditor already uses, reused here so PCEditor's
  // kind="pcs" wizard actually finds its "characters" sheet types instead of
  // filtering to an empty list (round-1 Codex finding on the plan).
  const types = Object.entries(module.sheets.sheet_types).filter(([, st]) => st.kind === typeKind(kind));
  const typeDef = sheetType ? module.sheets.sheet_types[sheetType] : undefined;
  const pools = typeDef?.creation?.pools ?? {};

  async function createEntity() {
    if (!name.trim()) return;
    setError(null);
    try {
      const id = await createRecord(name);
      setEntityId(id);
      setStep("type");
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  function pickType(tid: string) {
    setSheetType(tid);
    setSpends({});
    const def = module.sheets.sheet_types[tid];
    if (def?.creation) {
      setStep("budget");
    } else {
      void submit(tid, {});
    }
  }

  function setSpend(poolId: string, fieldKey: string, value: number) {
    setSpends({ ...spends, [poolId]: { ...(spends[poolId] ?? {}), [fieldKey]: value } });
  }

  function poolTotal(poolId: string): number {
    const pool = pools[poolId];
    if (!pool) return 0;
    let total = 0;
    for (const [fieldKey, cost] of Object.entries(pool.costs)) {
      const floor = 0; // budget floor is always 0 for the client-side preview
      const value = spends[poolId]?.[fieldKey] ?? floor;
      total += (value - floor) * cost;
    }
    return total;
  }

  const anyOverBudget = Object.keys(pools).some((pid) => {
    const budget = typeof pools[pid].budget === "number" ? pools[pid].budget as number : Infinity;
    return poolTotal(pid) > budget;
  });

  async function submit(tid: string, finalSpends: Record<string, Record<string, number>>) {
    if (!entityId) return;
    setError(null);
    try {
      await api.putSheetCreation(scope, module.id, kind, entityId, { sheet_type: tid, spends: finalSpends });
      onDone(entityId);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="form">
      <h3>New {kind} (with sheet)</h3>
      {error && <div className="banner">{error}</div>}
      {step === "form" && (
        <>
          <Field label="Name">
            <input aria-label="Name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={createEntity} disabled={!name.trim()}>Next</button>
          </div>
        </>
      )}
      {step === "type" && (
        <>
          <Field label="Sheet type">
            <select aria-label="Sheet type" value={sheetType} onChange={(e) => setSheetType(e.target.value)}>
              <option value="" disabled>Select type…</option>
              {types.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
            </select>
          </Field>
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={() => pickType(sheetType)} disabled={!sheetType}>Next</button>
          </div>
        </>
      )}
      {step === "budget" && typeDef && (
        <>
          {Object.entries(pools).map(([poolId, pool]) => (
            <div className="side-section" key={poolId}>
              <h4>{module.sheets.groups[poolId]?.label ?? poolId} — {poolTotal(poolId)} / {String(pool.budget)}</h4>
              {Object.keys(pool.costs).map((fieldKey) => {
                const fdef = module.sheets.groups[poolId]?.fields.find((f) => f.key === fieldKey);
                return (
                  <Field key={fieldKey} label={fdef?.label ?? fieldKey}>
                    <input aria-label={fdef?.label ?? fieldKey} type="number" min={0} max={fdef?.max}
                           value={spends[poolId]?.[fieldKey] ?? 0}
                           onChange={(e) => setSpend(poolId, fieldKey, Number(e.target.value))} />
                  </Field>
                );
              })}
            </div>
          ))}
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={() => submit(sheetType, spends)} disabled={anyOverBudget}>Create</button>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run CreationWizard`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire "+ New with sheet…" into `EntityEditor`**

In `frontend/src/components/EntityEditor.tsx`, add state near the top:

```typescript
  const [wizardOpen, setWizardOpen] = useState(false);
```

Import `CreationWizard`:

```typescript
import CreationWizard from "./CreationWizard";
```

In `.editor-list`, right after the existing `<button className="primary new" onClick={resetForm}>+ New {label}</button>`, add:

```typescript
        {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === kind) && (
          <button className="subtle" onClick={() => { setWizardOpen(true); setContentPreview(null); resetForm(); }}>
            + New {label} with sheet…
          </button>
        )}
```

In `.editor-body`, add the wizard as the first branch of the same conditional chain the content preview joined in Task 12 (wizard takes priority over both content preview and the normal view/edit split):

```typescript
        {wizardOpen && module ? (
          <CreationWizard scope={scope} kind={kind} module={module}
                          createRecord={(n) => api.createEntity(scope, kind, { name: n }).then((r) => r.id)}
                          onDone={async (id) => { setWizardOpen(false); await reload(); await select(id); }}
                          onCancel={() => setWizardOpen(false)} />
        ) : contentPreview ? (
          /* ...Task 12's content preview branch unchanged... */
```

- [ ] **Step 6: Add an EntityEditor test for the wizard trigger**

Add to `frontend/src/components/EntityEditor.test.tsx`:

```typescript
it("shows a wizard trigger only when the module has a sheet type for this kind, and opens the wizard", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  const trigger = await screen.findByText("+ New item with sheet…");
  fireEvent.click(trigger);
  await screen.findByText("New item (with sheet)");
});

it("hides the wizard trigger when the module has no sheet type for this kind", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  await screen.findByText("+ New item");
  expect(screen.queryByText("+ New item with sheet…")).not.toBeInTheDocument();
});
```

- [ ] **Step 7: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run EntityEditor`
Expected: all pass.

- [ ] **Step 8: Type-check and run the full frontend suite**

Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: no type errors; all tests pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/CreationWizard.tsx frontend/src/components/CreationWizard.test.tsx \
        frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx
git commit -m "feat(wizard): CreationWizard component; wire '+ New with sheet…' into EntityEditor"
```

---

### Task 14: Wire `CreationWizard` into `CharacterEditor` and `PCEditor`

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Modify: `frontend/src/components/PCEditor.tsx`
- Test: no new test files — this task is pure wiring of an already-tested component (Task 13); `CharacterEditor` gets a smoke assertion, `PCEditor` gets a full end-to-end flow test (see Step 1 — Codex's round-1 plan review specifically flagged the PC path as at risk of being dead-on-arrival, since `kind="pcs"` doesn't literally match any sheet type's declared `kind: "characters"`; Task 13's `typeKind()` fix inside `CreationWizard` is what makes it work, and this task's test is what proves it end-to-end rather than just asserting a button renders).

**Interfaces:**
- Consumes: `CreationWizard` (Task 13, now `typeKind`-aware for the entity-kind vs sheet-type-kind mismatch).

- [ ] **Step 1: Write the failing tests**

Both `CharacterEditor.test.tsx` and `PCEditor.test.tsx` mock `../api/client` with a factory that fully replaces the `api` export (`{...actual, api: {<literal>}}` — the `api` key is a full replacement, not a deep merge, so any method not listed in that literal is `undefined` at test time). Add `putSheetCreation: vi.fn()` to each file's existing mock `api` literal (`createCharacter`/`createPC`/`createCampaignPC` are already present in each).

Add to `frontend/src/components/CharacterEditor.test.tsx`:

```typescript
it("shows a wizard trigger when the module has a characters sheet type", async () => {
  (api.listCharacters as any).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<CharacterEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  await screen.findByText("+ New character with sheet…");
});
```

Add to `frontend/src/components/PCEditor.test.tsx` — this one goes further than a smoke test: it exercises the exact bug Codex's round-1 plan review caught (the wizard is opened with `kind="pcs"` but sheet types are declared `kind="characters"`; without `CreationWizard`'s `typeKind()` mapping from Task 13, the type-select would list nothing and the flow would be dead on arrival), driving the wizard end-to-end and asserting `putSheetCreation` is actually called with `kind="pcs"`:

```typescript
it("wizard trigger opens the wizard, finds the characters sheet type, and creates a PC sheet", async () => {
  (api.listPCs as any).mockResolvedValue([]);
  (api.createPC as any).mockResolvedValue({ pc: "elara" });
  (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
  (api.readPC as any).mockResolvedValue({
    meta: { id: "elara", name: "Elara", tags: [], default_version: "default" },
    versions: [{ id: "default", name: "default", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "" } }],
  });
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<PCEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New PC with sheet…"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
  fireEvent.click(screen.getByText("Next"));
  const select = await screen.findByLabelText("Sheet type");
  expect(within(select).getByText("Hero")).toBeInTheDocument();  // proves typeKind("pcs") -> "characters" found it
  fireEvent.change(select, { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
    { kind: "world", id: "w1" }, "testmod", "pcs", "elara", { sheet_type: "hero", spends: {} }));
});
```

(Add `within` to `PCEditor.test.tsx`'s `@testing-library/react` import alongside `render`, `screen`, `fireEvent`, `waitFor`.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run CharacterEditor PCEditor -t "wizard trigger"`
Expected: FAIL — no such button.

- [ ] **Step 3: Wire `CharacterEditor`**

In `frontend/src/components/CharacterEditor.tsx`, import `CreationWizard` and add `wizardOpen` state alongside the other `useState` declarations (near line 54):

```typescript
import CreationWizard from "./CreationWizard";
```
```typescript
  const [wizardOpen, setWizardOpen] = useState(false);
```

Right after the existing `<button className="primary" onClick={newCharacter}>+ New character</button>` (line 747), add:

```typescript
          {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
            <button className="subtle" onClick={() => setWizardOpen(true)}>+ New character with sheet…</button>
          )}
```

`CharacterEditor` already has an `openEdit(cid: string)` function (used by `newCharacter` itself, around line 335: `window.scrollTo(0, 0); await select(cid); setMode("edit");`) — this is exactly the "land on the new record, ready to edit" step the wizard needs after `onDone`. Find the JSX branch that switches on `mode` (`"grid"` vs the detail view) and add a wizard branch ahead of it, using the same priority pattern as Task 13's `EntityEditor` change:

```typescript
        {wizardOpen && module ? (
          <CreationWizard scope={scope} kind="characters" module={module}
                          createRecord={(n) => api.createCharacter(wid, { name: n }).then((r) => r.character)}
                          onDone={async (id) => { setWizardOpen(false); await reload(); await openEdit(id); }}
                          onCancel={() => setWizardOpen(false)} />
        ) : mode === "grid" ? (
          /* ...existing grid rendering unchanged... */
```

(`api.createCharacter` always targets `wid` — `newCharacter` does the same regardless of `scope`, since characters are always authored at world scope and appear in a campaign only via the overlay; the wizard's `createRecord` mirrors that exactly, not `scope`.)

- [ ] **Step 4: Wire `PCEditor`**

In `frontend/src/components/PCEditor.tsx`, the same pattern: import `CreationWizard`, add `wizardOpen` state, add the trigger button right after `<button className="primary new" onClick={newPC}>+ New PC</button>` (line 151):

```typescript
        {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
          <button className="subtle" onClick={() => setWizardOpen(true)}>+ New PC with sheet…</button>
        )}
```

And the same wizard-branch wiring in the detail body — `kind="pcs"` this time (PCs validate against `characters` sheet types per `sheets.sheet_kind`, but the entity kind threaded through `CreationWizard`/`api.putSheetCreation` is `"pcs"`, matching `SheetPanel`'s existing `kind="pcs"` usage in this file at line ~243). `PCEditor` already branches PC creation by scope exactly the way `newPC` does (`worldScope ? api.createPC(wid, ...) : api.createCampaignPC(scope.id, ...)`), and its own post-create navigation is `await select(pc); setMode("edit");` (no `openEdit`-style helper here, unlike `CharacterEditor` — `select`+`setMode` inline is this file's existing idiom, per `newPC`'s own body):

```typescript
        {wizardOpen && module ? (
          <CreationWizard scope={scope} kind="pcs" module={module}
                          createRecord={(n) => (worldScope
                            ? api.createPC(wid, { name: n }).then((r) => r.pc)
                            : api.createCampaignPC(scope.id, { name: n }).then((r) => r.pc))}
                          onDone={async (id) => {
                            setWizardOpen(false);
                            await reload();
                            await select(id);
                            setMode("edit");
                          }}
                          onCancel={() => setWizardOpen(false)} />
        ) : (
          /* ...existing view/edit rendering unchanged... */
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run CharacterEditor PCEditor -t "wizard trigger"`
Expected: PASS (2 passed)

- [ ] **Step 6: Run both full test files**

Run (from `frontend/`): `npx vitest run CharacterEditor PCEditor`
Expected: all pass.

- [ ] **Step 7: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx \
        frontend/src/components/PCEditor.tsx frontend/src/components/PCEditor.test.tsx
git commit -m "feat(wizard): wire CreationWizard into CharacterEditor and PCEditor"
```

---

### Task 15: `SheetEditor` — `ref` field widget + `onOpenRef` threading

**Files:**
- Modify: `frontend/src/components/SheetEditor.tsx`
- Modify: `frontend/src/components/SheetPanel.tsx`
- Modify: `frontend/src/components/EntityEditor.tsx`, `frontend/src/components/CharacterEditor.tsx`, `frontend/src/components/PCEditor.tsx` (thread an `onOpenRef` callback down)
- Test: `frontend/src/components/SheetEditor.test.tsx`

**Interfaces:**
- Consumes: `api.listEntities` (existing, for the "in your world/campaign" picker group), `api.readModuleContent` (Task 11, for the module-content preview modal).
- Produces: `SheetEditor` gains `onOpenRef?: (kind: string, id: string) => void` prop; `SheetPanel` gains the same and threads it through.

- [ ] **Step 1: Write the failing tests**

The existing `vi.mock("../api/client", () => ({ api: { putSheet: vi.fn(), deleteSheet: vi.fn() } }))` block needs `listEntities: vi.fn()` and `readModuleContent: vi.fn()` added. Use the file's real fixtures — `MOD` (id `"pool-basic"`, manifest name `"Pool Basic"`), the `scope={{ kind: "campaign", id: "run" }}` shape every existing test already renders with, and build a `REF_MOD` fixture as a variant of `MOD` (don't spread a bare `module` — that's not a fixture in this file, it's JS's reserved CommonJS global). Add near `MOD`/`SHEET`:

```typescript
const REF_MOD: ModuleDetail = {
  ...MOD,
  sheets: {
    groups: {},
    sheet_types: {
      warden: {
        label: "Warden", kind: "characters", groups: [],
        fields: [{ key: "known", label: "Known Spells", type: "ref", ref_kind: "lore" }],
      },
    },
  },
};
```

Add the tests:

```typescript
test("view mode renders entity-form ref chips that call onOpenRef", async () => {
  const onOpenRef = vi.fn();
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:fireball"] }, derived: {}, errors: [] };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} onOpenRef={onOpenRef} />);
  fireEvent.click(screen.getByText(/fireball/i));
  expect(onOpenRef).toHaveBeenCalledWith("lore", "fireball");
});

test("view mode renders module-content ref chips that open a preview instead", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "icebolt", name: "Icebolt", body: "A shard of ice.", keys: "", sheet_type: null, fields: {},
  });
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:module:icebolt"] }, derived: {}, errors: [] };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText(/icebolt/i));
  await screen.findByText("A shard of ice.");
});

test("edit mode offers a two-group checkbox picker for a ref field", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "fireball", name: "Fireball" }]);
  const initial: Sheet = { sheet_type: "warden", fields: { known: [] }, derived: {}, errors: [] };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  await screen.findByText("In your world/campaign");
  await screen.findByText("From Pool Basic");
  fireEvent.click(screen.getByLabelText("Fireball"));
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "warden", fields: { known: ["lore:fireball"] } }));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run SheetEditor -t "ref"`
Expected: FAIL — no `ref` case in `widget()`/`displayValue()` yet, so these fields render via the `default` (number) branch and crash or render nonsense.

- [ ] **Step 3: Implement the `ref` widget**

In `frontend/src/components/SheetEditor.tsx`, add imports:

```typescript
import { useEffect, useState, type ChangeEvent } from "react";
import { api, type EntityScope, type ModuleContentEntry, type ModuleDetail, type ModuleField, type Sheet } from "../api/client";
import { Field } from "./Field";
```

Add a small helper near the top (after `isResource`):

```typescript
function isModuleRef(ref: string): boolean {
  return ref.split(":").length === 3;
}
function refKindAndId(ref: string): { kind: string; id: string; isModule: boolean } {
  const parts = ref.split(":");
  return parts.length === 3
    ? { kind: parts[0], id: parts[2], isModule: true }
    : { kind: parts[0], id: parts[1], isModule: false };
}
```

Add a `RefFieldView` sub-component (renders chips, handles the module-content preview):

```typescript
function RefFieldView({ f, value, module, onOpenRef }: {
  f: ModuleField; value: unknown; module: ModuleDetail; onOpenRef?: (kind: string, id: string) => void;
}) {
  const [preview, setPreview] = useState<ModuleContentEntry | null>(null);
  const refs = Array.isArray(value) ? (value as string[]) : [];
  return (
    <div className="field" key={f.key}>
      <label>{f.label ?? f.key}</label>
      <div className="chips">
        {refs.map((ref) => {
          const { kind, id, isModule } = refKindAndId(ref);
          return (
            <button key={ref} className="chip owner-chip"
                    onClick={() => isModule ? api.readModuleContent(module.id, kind, id).then(setPreview)
                                            : onOpenRef?.(kind, id)}>
              {id}
            </button>
          );
        })}
        {refs.length === 0 && <span className="field-hint">none</span>}
      </div>
      {preview && (
        <div className="side-section">
          <h4>{preview.name}</h4>
          <p>{preview.body}</p>
          <button className="subtle" onClick={() => setPreview(null)}>Close</button>
        </div>
      )}
    </div>
  );
}
```

Add a `RefFieldEdit` sub-component (the two-group picker):

```typescript
function RefFieldEdit({ f, scope, module, value, onChange }: {
  f: ModuleField; scope: EntityScope; module: ModuleDetail;
  value: unknown; onChange: (v: string[]) => void;
}) {
  const [entities, setEntities] = useState<{ id: string; name: string }[]>([]);
  const refKind = f.ref_kind!;
  useEffect(() => {
    api.listEntities(scope, refKind as any).then(setEntities).catch(() => setEntities([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id, refKind]);
  const current = new Set(Array.isArray(value) ? (value as string[]) : []);
  const content = module.content.filter((c) => c.kind === refKind);

  function toggle(ref: string, checked: boolean) {
    const next = new Set(current);
    if (checked) next.add(ref); else next.delete(ref);
    onChange([...next]);
  }

  return (
    <Field label={f.label ?? f.key}>
      <div className="side-section">
        <h4>In your world/campaign</h4>
        <div className="chips owner-picker">
          {entities.map((e) => {
            const ref = `${refKind}:${e.id}`;
            return (
              <label key={ref} className="owner-option">
                <input type="checkbox" aria-label={e.name} checked={current.has(ref)}
                       onChange={(ev) => toggle(ref, ev.target.checked)} />
                {e.name}
              </label>
            );
          })}
          {entities.length === 0 && <span className="field-hint">None yet.</span>}
        </div>
      </div>
      <div className="side-section">
        <h4>From {module.manifest.name}</h4>
        <div className="chips owner-picker">
          {content.map((c) => {
            const ref = `${refKind}:module:${c.id}`;
            return (
              <label key={ref} className="owner-option">
                <input type="checkbox" aria-label={c.name} checked={current.has(ref)}
                       onChange={(ev) => toggle(ref, ev.target.checked)} />
                {c.name}
              </label>
            );
          })}
          {content.length === 0 && <span className="field-hint">None.</span>}
        </div>
      </div>
    </Field>
  );
}
```

Now wire these into the existing `widget()` and the view-mode rendering. `widget()` currently takes `(f, value, onChange)` — add a `ref` case at its top, but it needs `scope`/`module` which `widget()` doesn't currently receive. Change `widget`'s signature to take a context object instead of threading two more positional params through every call site:

```typescript
function widget(f: ModuleField, value: unknown, onChange: (v: unknown) => void,
                ctx: { scope: EntityScope; module: ModuleDetail }) {
  const label = fieldLabel(f);
  if (f.type === "ref") {
    return <RefFieldEdit key={f.key} f={f} scope={ctx.scope} module={ctx.module}
                         value={value} onChange={onChange as (v: string[]) => void} />;
  }
  switch (f.type) {
    /* ...existing cases unchanged... */
```

Update both call sites of `widget(...)` in the `mode === "edit"` JSX (the two `{groupIds.map(...)}`/`{ownFields.map(...)}` blocks near the bottom of the file) to pass the new fourth argument: `widget(f, draft[f.key], (v) => setField(f.key, v), { scope, module })`.

In the view-mode rendering (`.sheet-row` divs), add a `ref`-aware branch. Currently each row is `<div className="sheet-row" key={f.key}>{fieldLabel(f)}: {displayValue(f, fields[f.key])}</div>` inside two `.map` calls (group fields, own fields). Change both to:

```typescript
                  {g.fields.map((f) => f.type === "ref"
                    ? <RefFieldView key={f.key} f={f} value={fields[f.key]} module={module} onOpenRef={onOpenRef} />
                    : <div className="sheet-row" key={f.key}>{fieldLabel(f)}: {displayValue(f, fields[f.key])}</div>)}
```

(and the matching change for the `ownFields.map(...)` block right below it).

Add `onOpenRef` to the component's props:

```typescript
export default function SheetEditor({ scope, module, kind, eid, initial, onClose, onSaved, onOpenRef }:
  { scope: EntityScope; module: ModuleDetail; kind: string; eid: string; initial: Sheet;
    onClose: () => void; onSaved: () => void; onOpenRef?: (kind: string, id: string) => void }) {
```

- [ ] **Step 4: Thread `onOpenRef` through `SheetPanel`**

In `frontend/src/components/SheetPanel.tsx`, add the prop and pass it through to `SheetEditor`:

```typescript
export default function SheetPanel({ scope, module, kind, eid, onOpenRef }:
  { scope: EntityScope; module: ModuleDetail | null; kind: string; eid: string;
    onOpenRef?: (kind: string, id: string) => void }) {
```

And in the `editing && sheet` render branch:

```typescript
      <SheetEditor scope={scope} module={module} kind={kind} eid={eid} initial={sheet}
                   onClose={() => setEditing(false)}
                   onSaved={() => { refetch(); }} onOpenRef={onOpenRef} />
```

- [ ] **Step 5: Thread `onOpenRef` from `EntityEditor`/`CharacterEditor`/`PCEditor` into `SheetPanel`**

In `EntityEditor.tsx`, the existing `<SheetPanel scope={scope} module={module} kind={kind} eid={editing} />` call (line 342) gains `onOpenRef={onOpenOwner}` — `EntityEditor` already has an `onOpenOwner?: (ref: string) => void` prop taking a single `"kind:id"` string; adapt it: `onOpenRef={(kind, id) => onOpenOwner?.(`${kind}:${id}`)}`.

In `CharacterEditor.tsx` and `PCEditor.tsx`, their `SheetPanel` calls don't currently take an `onOpenOwner`-shaped prop to reuse, so leave `onOpenRef` unset for now (`ref` chip navigation from a character/PC sheet showing "not yet wired to open the target record" is an acceptable, explicitly-scoped gap — the chip still renders and the module-content half of the feature, which doesn't need `onOpenRef` at all, works fully in both places). Add a one-line comment at each call site noting this:

```typescript
                <SheetPanel scope={scope} module={module} kind="characters" eid={detail.meta.id} />
                {/* onOpenRef intentionally unset here: no cross-editor navigation target exists
                    yet from a character/PC sheet's ref chips (entity-form refs only; module-content
                    ref chips still preview correctly without it) */}
```

- [ ] **Step 6: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run SheetEditor -t "ref"`
Expected: PASS (3 passed)

- [ ] **Step 7: Run the full SheetEditor, SheetPanel, and EntityEditor test files**

Run (from `frontend/`): `npx vitest run SheetEditor SheetPanel EntityEditor`
Expected: all pass — this is the regression check for `widget()`'s new signature (every existing non-ref field call site was updated in Step 3) and the view-mode row rendering change.

- [ ] **Step 8: Type-check and run the full frontend suite**

Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: no errors; all tests pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/SheetEditor.tsx frontend/src/components/SheetEditor.test.tsx \
        frontend/src/components/SheetPanel.tsx frontend/src/components/EntityEditor.tsx \
        frontend/src/components/CharacterEditor.tsx frontend/src/components/PCEditor.tsx
git commit -m "feat(sheet-editor): ref field widget (entity + module-content address forms)"
```

---

### Task 16: `SheetEditor` — advancement "+" button

**Files:**
- Modify: `frontend/src/components/SheetEditor.tsx`
- Test: `frontend/src/components/SheetEditor.test.tsx`

**Interfaces:**
- Consumes: `api.advanceSheet` (Task 11).
- Produces: an "Advance" button per field listed in the sheet type's `advancement.costs`, visible in view mode, only when `sheetType`'s definition has an `advancement` block.

- [ ] **Step 1: Write the failing tests**

Add `advanceSheet: vi.fn()` to the mock `api` literal. Add an `ADV_MOD` fixture (variant of `MOD`, same pattern as `REF_MOD`):

```typescript
const ADV_MOD: ModuleDetail = {
  ...MOD,
  sheets: {
    groups: {},
    sheet_types: {
      warden: {
        label: "Warden", kind: "characters", groups: [],
        fields: [
          { key: "wits", label: "Wits", type: "dots", max: 5 },
          { key: "xp", label: "XP", type: "resource", max: 999 },
        ],
        advancement: { pool: "xp", costs: { wits: "new * 3" } },
      },
    },
  },
};
```

```typescript
test("shows an advance button for advancement-eligible fields and calls the API on click", async () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 20, max: 999 } }, derived: {}, errors: [],
  };
  (api.advanceSheet as any).mockResolvedValue({
    sheet: { sheet_type: "warden", fields: { wits: 3, xp: { current: 11, max: 999 } }, derived: {}, errors: [] },
  });
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByLabelText("Advance Wits"));
  await waitFor(() => expect(api.advanceSheet).toHaveBeenCalledWith("run", "characters", "mara", "wits"));
});

test("shows the SheetError message on a rejected advance", async () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 1, max: 999 } }, derived: {}, errors: [],
  };
  (api.advanceSheet as any).mockRejectedValue({ detail: "needs 6 xp, have 1" });
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByLabelText("Advance Wits"));
  await screen.findByText("needs 6 xp, have 1");
});

test("hides the advance button at world scope (starting sheets have no XP economy)", () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 20, max: 999 } }, derived: {}, errors: [],
  };
  render(<SheetEditor scope={{ kind: "world", id: "realm" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.queryByLabelText("Advance Wits")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run SheetEditor -t "advance"`
Expected: FAIL — no "Advance Wits" button exists yet.

- [ ] **Step 3: Implement the advance button**

In `frontend/src/components/SheetEditor.tsx`, add an `advance` handler in the component body (near `save`):

```typescript
  async function advanceField(key: string) {
    setError(null);
    try {
      const { sheet: fresh } = await api.advanceSheet(scope.id, kind, eid, key);
      setFields(fresh.fields);
      setDraft(fresh.fields);
      onSaved();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

In the view-mode row rendering (both the `g.fields.map(...)` and `ownFields.map(...)` blocks, right where Task 15 added the `ref`-aware branch), add an advancement-aware branch — a field is advancement-eligible when `typeDef?.advancement?.costs[f.key]` is set **and** `scope.kind === "campaign"` (advancement is a play-time XP spend against a live campaign sheet; a world-scope `SheetEditor` is editing a *starting* sheet template, which has no XP economy — the backend route is campaign-only too, so gating here avoids a button that would 404):

```typescript
                  {g.fields.map((f) => {
                    const advEligible = scope.kind === "campaign" && typeDef?.advancement?.costs[f.key];
                    if (f.type === "ref") {
                      return <RefFieldView key={f.key} f={f} value={fields[f.key]} module={module} onOpenRef={onOpenRef} />;
                    }
                    return (
                      <div className="sheet-row" key={f.key}>
                        {fieldLabel(f)}: {displayValue(f, fields[f.key])}
                        {advEligible && (
                          <button className="subtle" aria-label={`Advance ${fieldLabel(f)}`}
                                  onClick={() => advanceField(f.key)}>+</button>
                        )}
                      </div>
                    );
                  })}
```

(Apply the identical pattern to the `ownFields.map(...)` block.)

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run SheetEditor -t "advance"`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full SheetEditor test file, type-check, and run the full frontend suite**

Run (from `frontend/`): `npx vitest run SheetEditor && npx tsc -b && npx vitest run`
Expected: all pass, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SheetEditor.tsx frontend/src/components/SheetEditor.test.tsx
git commit -m "feat(sheet-editor): advancement + button, wired to POST .../advance"
```

---

## Final checkpoint

- [ ] **Run the entire backend and frontend suite one more time**

```bash
backend/.venv/Scripts/python.exe -m pytest backend -q
cd frontend && npx tsc -b && npx vitest run
```

Expected: everything passes. Then proceed to CLAUDE.md's `/codex:review`
diff gate before considering the branch done, and the final
`/codex:adversarial-review` against the diff + the originating spec.
