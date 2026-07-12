# Mechanics Phase 3 — Sheets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sheet instances for every sheetable entity: `store/sheets.py` (CRUD, validation, derived computation, seeding, coverage), reserved-key pack validation, routes, a kind-agnostic SheetPanel + takeover SheetEditor, coverage blocks, and fleshed reference packs.

**Architecture:** Campaign sheets at `<campaign>/sheets/<kind>--<id>.json`, world starting sheets at `<world>/sheets/<mid>/<kind>--<id>.json`; derived values computed on read via `store/expressions.py`; validation reuses `modules.validate_sheet_values`. Frontend: module context lifted into `WorldView` (campaign = resolved module, world = picker choice) and passed to editors as a prop — `SheetPanel` renders nothing without it, keeping existing editor tests inert. Spec: `docs/superpowers/specs/2026-07-12-mechanics-phase3-sheets-design.md`.

**Tech Stack:** FastAPI + pytest (backend, pure stdlib stores), Vite/React + vitest (frontend).

## Global Constraints

- **Privacy:** invented names only (Realm, Saltmarch, Mara, warden/medium/talisman-style fixtures).
- **Android rules:** `store/sheets.py` pure stdlib; route models plain pydantic `BaseModel` scalars dumped via `routes._dump`; no new deps.
- **Never-raise posture:** `sheets.read`/`coverage` never raise on malformed sheet-file content (accumulate `errors`); only domain exceptions for missing campaign/world/kind and rejected writes. Mirror `modules.load_pack` discipline; guard every value read from JSON.
- **Route ordering:** `/campaigns/{cid}/sheets` and `/worlds/{wid}/sheets` MUST be registered before the generic `{kind}` catch-alls (campaign generics at routes.py:2522+, world generics at routes.py:1331+; module routes at 2336 and 478 show the pattern).
- **PCs:** stored under file-kind `pcs`, validate against `characters` sheet types; `characters` and `pcs` are separate coverage rows.
- **Sheets are campaign-owned:** copied at create (`seed`), never overlay-read; no re-seed on later binding.
- **Run tests:** backend `backend/.venv/Scripts/python.exe -m pytest backend -q` (worktree: prefix `PYTHONPATH=backend/src`, Git Bash); frontend FROM `frontend/`: `npx vitest run`, `npx tsc -b`.
- **Worktree:** branch `mechanics-phase3-sheets` at `.worktrees/mechanics-phase3-sheets`; frontend needs its own `npm install`.
- Commit per task, conventional messages, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## GitHub issues (for landing)

- **Resolves #161** (character sheets). Closing comment: multi-kind sheets (not just characters), takeover editor, coverage indicators, world starting sheets keyed by module; derived capabilities via the expression engine.
- **Comment:** #201 (coverage blocks + SheetPanel land the "sheets" half; bulk-create still Phase 7), #163/#162 (sheet storage they depend on now exists).

## File structure

- Create: `backend/src/grimoire/store/sheets.py`, `backend/tests/test_sheets_store.py`
- Modify: `backend/src/grimoire/store/modules.py` (reserved keys), `backend/src/grimoire/store/campaigns.py` (seed call), `backend/src/grimoire/routes.py`, `backend/tests/test_modules_store.py`, `backend/tests/test_routes.py`, both `builtin_modules/*/sheets.json`
- Create: `frontend/src/components/SheetPanel.tsx` (+test), `frontend/src/components/SheetEditor.tsx` (+test)
- Modify: `frontend/src/api/client.ts`, `frontend/src/components/{EntityEditor,PCEditor,CharacterEditor,MechanicsConfig,WorldMechanics,WorldOverview}.tsx` (+ affected tests), `frontend/src/routes/WorldView.tsx` (+test), `frontend/src/index.css`

---

### Task 1: Reserved field keys (modules.py)

**Files:**
- Modify: `backend/src/grimoire/store/modules.py` (`_validate_field`, lines 99-114, and `_validate_sheets`)
- Test: `backend/tests/test_modules_store.py` (append)

**Interfaces:**
- Consumes: `expressions._FUNCS` (dict keys: min, max, floor, ceil, abs; `expressions` already imported at modules.py:18).
- Produces: two new load-time pack errors; no signature changes.

- [ ] **Step 1: Write the failing tests (append to test_modules_store.py)**

```python
def test_reserved_function_name_key_rejected(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "floor", "type": "number"}))
    assert any("reserved" in e for e in errs)


def test_resource_max_name_collision_rejected(monkeypatch, tmp_path):
    # GOOD_SHEETS' warden has resource "essence" -> implicit "essence_max"
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["fields"].append(
            {"key": "essence_max", "type": "number"}))
    assert any("essence_max" in e and "resource" in e for e in errs)


def test_builtin_packs_pass_reserved_key_rules(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for mid in ("d20-basic", "pool-basic"):
        assert modules.load_pack(mid)["errors"] == []
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q` (adjust venv path to `/c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` from a worktree)
Expected: first two FAIL (no such errors produced); third passes already.

- [ ] **Step 3: Implement**

In `_validate_field`, after the string-key check (`if not key or not isinstance(key, str): ...` block ends at line 106):

```python
    if key in expressions._FUNCS:
        errors.append(f"{where}.{key}: reserved key (expression function name)")
        return
```

In `_validate_sheets`, inside the per-sheet-type loop where `fields = assembled_fields(sheets, tid)` is already computed (before the duplicate-key check), add the `_max` collision check:

```python
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
```

- [ ] **Step 4: Run the module tests** — all PASS (built-ins still clean).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): reserved field keys - expression functions and resource _max collisions (#161)"
```

---

### Task 2: `store/sheets.py` core — campaign CRUD, defaults, derived

**Files:**
- Create: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py`

**Interfaces:**
- Consumes: `modules.resolve(cid) -> str|None`, `modules.load_pack(mid) -> dict` (key `"sheets"`), `modules.assembled_fields(sheets, tid) -> list[dict]`, `modules.validate_sheet_values(sheets, tid, values) -> list[str]`, `expressions.evaluate(text, scope)` raising `ExpressionError`, `campaigns.campaign_root(cid)`.
- Produces (used by Tasks 3-6): `SheetError(Exception)`; `FILE_KINDS: tuple` (= ENTITY_KINDS + characters + pcs); `sheet_kind(kind) -> str` (pcs→characters); `default_fields(sheets: dict, type_id: str) -> dict`; `read(cid, kind, eid) -> dict|None`; `write(cid, kind, eid, sheet_type: str, fields: dict|None) -> None`; `delete(cid, kind, eid) -> bool`; `list_refs(cid) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sheets_store.py
import json

import pytest

from grimoire.store import campaigns, modules, sheets, worlds


def _campaign(monkeypatch, tmp_path, module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    return wid, cid


def test_write_and_read_with_derived(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 2, "grace": 3, "wits": 4, "occult": 2,
                  "essence": {"current": 6, "max": 10}})
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium"
    assert s["errors"] == []
    assert s["derived"]["sight_pool"] == 6          # wits + occult
    assert s["derived"]["awareness"] == 7           # group derived: wits + grace


def test_read_missing_returns_none(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    assert sheets.read(cid, "characters", "nobody") is None


def test_write_defaults_when_fields_none(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    s = sheets.read(cid, "items", "moon-disc")
    assert s["fields"]["power"] == 1                       # schema default
    assert s["fields"]["charges"] == {"current": 10, "max": 10}  # default max
    assert s["errors"] == []


def test_pcs_validate_against_characters(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "pcs", "seraphine", "medium", None)
    assert sheets.read(cid, "pcs", "seraphine")["sheet_type"] == "medium"


def test_write_rejects_kind_mismatch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "talisman", None)  # items type


def test_write_rejects_unknown_type_and_bad_values(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "ghost", None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", {"vigor": 99})


def test_write_without_module_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", None)


def test_type_change_preserves_shared_drops_orphans(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    sheets.write(cid, "characters", "mara", "shifter",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "shifter"
    assert s["fields"]["vigor"] == 3         # shared via attributes group
    assert "essence" not in s["fields"]      # medium-only field dropped


def test_invalid_after_module_switch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    modules.set_campaign_module(cid, "d20-basic")
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"]                        # flagged, not deleted
    assert s["sheet_type"] == "medium"
    modules.set_campaign_module(cid, "none")
    assert any("module" in e for e in sheets.read(cid, "characters", "mara")["errors"])


def test_malformed_sheet_file_tolerated(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "sheets"
    d.mkdir(exist_ok=True)
    (d / "characters--mara.json").write_text("{nope", encoding="utf-8")
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] is None and s["fields"] == {} and s["errors"]


def test_delete_and_list_refs(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    assert sheets.list_refs(cid) == [("characters", "mara"), ("items", "moon-disc")]
    assert sheets.delete(cid, "items", "moon-disc") is True
    assert sheets.delete(cid, "items", "moon-disc") is False
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_bad_kind_and_eid_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "vehicles", "cart", "medium", None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "../escape", "medium", None)
    assert sheets.read(cid, "vehicles", "cart") is None
```

- [ ] **Step 2: Run to verify failure** (`ImportError: cannot import name 'sheets'`).

- [ ] **Step 3: Implement `backend/src/grimoire/store/sheets.py`**

```python
"""Sheet instances for sheetable entities (#161, mechanics Phase 3).

Campaign sheets live at ``<campaign>/sheets/<kind>--<id>.json``; world
starting sheets at ``<world>/sheets/<mid>/<kind>--<id>.json``. File shape:
``{"sheet_type": ..., "fields": {...}}``. Derived values are computed on
read, never stored. Sheets are campaign-owned mutable state: copied at
create (``seed``), never overlay-read. ``read``/``coverage`` never raise on
malformed sheet content; writes validate strictly and raise ``SheetError``.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase3-sheets-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, entities, expressions, modules, worlds


class SheetError(Exception):
    """Rejected sheet write (no module, bad kind/type/values)."""


FILE_KINDS: tuple[str, ...] = ("characters", "pcs") + entities.ENTITY_KINDS


def sheet_kind(kind: str) -> str:
    """Module sheet-type kind for a file kind (pcs share characters types)."""
    return "characters" if kind == "pcs" else kind


def _safe_part(part: str) -> bool:
    return bool(part) and part not in (".", "..") and "/" not in part and "\\" not in part


def _campaign_dir(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "sheets"


def _campaign_path(cid: str, kind: str, eid: str) -> Path:
    return _campaign_dir(cid) / f"{kind}--{eid}.json"


def _int_or(value, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def default_fields(sheets_def: dict, type_id: str) -> dict:
    """Schema-default value map for a sheet type (spec: Decisions table)."""
    out: dict = {}
    for f in modules.assembled_fields(sheets_def, type_id):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        t = f.get("type")
        if t in ("number", "dots", "track"):
            out[key] = _int_or(f.get("default"), 0)
        elif t == "resource":
            mx = _int_or(f.get("max"), 0)
            out[key] = {"current": _int_or(f.get("default"), mx), "max": mx}
        elif t == "text":
            out[key] = ""
        elif t == "list":
            out[key] = []
    return out


def _numeric_scope(sheets_def: dict, type_id: str, fields: dict) -> dict:
    """Expression scope: schema defaults overlaid with stored values."""
    merged = {**default_fields(sheets_def, type_id), **fields}
    scope: dict = {}
    for f in modules.assembled_fields(sheets_def, type_id):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        t = f.get("type")
        v = merged.get(key)
        if t in ("number", "dots", "track"):
            if isinstance(v, int) and not isinstance(v, bool):
                scope[key] = v
        elif t == "resource" and isinstance(v, dict):
            cur, mx = v.get("current"), v.get("max")
            if isinstance(cur, int) and not isinstance(cur, bool):
                scope[key] = cur
            if isinstance(mx, int) and not isinstance(mx, bool):
                scope[key + "_max"] = mx
    return scope


def _compute_derived(sheets_def: dict, type_id: str, fields: dict,
                     errors: list[str]) -> dict:
    """Group-level derived first (feeding the scope), then type-level."""
    st = sheets_def.get("sheet_types", {}).get(type_id)
    if not isinstance(st, dict):
        return {}
    scope = _numeric_scope(sheets_def, type_id, fields)
    out: dict = {}

    def run(derived: dict) -> None:
        if not isinstance(derived, dict):
            return
        for name, expr in derived.items():
            if not isinstance(expr, str):
                continue  # pack validation already flags these
            try:
                value = expressions.evaluate(expr, scope)
            except expressions.ExpressionError as e:
                errors.append(f"derived.{name}: {e}")
                continue
            out[name] = value
            scope[name] = value

    groups = st.get("groups") if isinstance(st.get("groups"), list) else []
    for gid in groups:
        g = sheets_def.get("groups", {}).get(gid) if isinstance(gid, str) else None
        if isinstance(g, dict):
            run(g.get("derived", {}))
    run(st.get("derived", {}))
    return out


def _validate_instance(sheets_def: dict, file_kind: str, sheet_type,
                       fields: dict) -> list[str]:
    """Errors for a stored sheet against a module's sheets definition."""
    if not isinstance(sheet_type, str) or not sheet_type:
        return ["sheet has no sheet_type"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        return [f"unknown sheet type {sheet_type!r}"]
    if st.get("kind") != sheet_kind(file_kind):
        return [f"sheet type {sheet_type!r} targets kind {st.get('kind')!r}, "
                f"not {sheet_kind(file_kind)!r}"]
    return modules.validate_sheet_values(sheets_def, sheet_type, fields)


def _read_path(path: Path, file_kind: str, mid: str | None) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {"sheet_type": None, "fields": {}, "derived": {},
                "errors": [f"unreadable sheet file: {e}"]}
    if not isinstance(data, dict):
        return {"sheet_type": None, "fields": {}, "derived": {},
                "errors": ["sheet file must be an object"]}
    sheet_type = data.get("sheet_type")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if mid is None:
        return {"sheet_type": sheet_type, "fields": fields, "derived": {},
                "errors": ["no module resolved"]}
    sheets_def = modules.load_pack(mid)["sheets"]
    errors = _validate_instance(sheets_def, file_kind, sheet_type, fields)
    derived: dict = {}
    if isinstance(sheet_type, str):
        derived = _compute_derived(sheets_def, sheet_type, fields, errors)
    return {"sheet_type": sheet_type, "fields": fields,
            "derived": derived, "errors": errors}


def read(cid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return None
    try:
        mid = modules.resolve(cid)
    except campaigns.CampaignNotFound:
        raise
    return _read_path(_campaign_path(cid, kind, eid), kind, mid)


def _checked_write(path: Path, mid: str, file_kind: str, eid: str,
                   sheet_type: str, fields: dict | None) -> None:
    if file_kind not in FILE_KINDS:
        raise SheetError(f"unknown sheet kind {file_kind!r}")
    if not _safe_part(eid):
        raise SheetError(f"bad entity id {eid!r}")
    sheets_def = modules.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(sheet_type)
    if not isinstance(st, dict):
        raise SheetError(f"unknown sheet type {sheet_type!r}")
    if st.get("kind") != sheet_kind(file_kind):
        raise SheetError(
            f"sheet type {sheet_type!r} targets {st.get('kind')!r}, "
            f"not {sheet_kind(file_kind)!r}")
    if fields is None:
        fields = default_fields(sheets_def, sheet_type)
    else:
        allowed = {f.get("key") for f in modules.assembled_fields(sheets_def, sheet_type)
                   if isinstance(f, dict)}
        fields = {k: v for k, v in fields.items() if k in allowed}
        errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sheet_type": sheet_type, "fields": fields},
                               indent=2), encoding="utf-8")


def write(cid: str, kind: str, eid: str, sheet_type: str,
          fields: dict | None = None) -> None:
    """Create or replace a campaign sheet. A different ``sheet_type`` than
    the stored one is a type change: values whose keys exist in the new
    type's assembled field set are kept (caller passes them), others are
    filtered out here."""
    mid = modules.resolve(cid)
    if mid is None:
        raise SheetError("no module resolved for this campaign")
    _checked_write(_campaign_path(cid, kind, eid), mid, kind, eid,
                   sheet_type, fields)


def delete(cid: str, kind: str, eid: str) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return False
    p = _campaign_path(cid, kind, eid)
    if not p.exists():
        return False
    p.unlink()
    return True


def list_refs(cid: str) -> list[tuple[str, str]]:
    d = _campaign_dir(cid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and _safe_part(eid):
            out.append((kind, eid))
    return out
```

Note `test_write_without_module_rejected` passes `module=None` at create — `resolve` then returns None and `write` raises before touching disk. `test_bad_kind_and_eid_rejected`'s write path raises `SheetError` from `_checked_write`'s kind guard **only after** resolve — move the kind/eid guards to the top of `write` if the test ordering demands it (the test uses a bound campaign, so either order passes; keep guards in `_checked_write`).

- [ ] **Step 4: Run** — all Task-2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): campaign sheet CRUD, defaults, derived computation (#161)"
```

---

### Task 3: World sheets + seeding

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`, `backend/src/grimoire/store/campaigns.py`
- Test: `backend/tests/test_sheets_store.py` (append)

**Interfaces:**
- Produces: `read_world(wid, mid, kind, eid) -> dict|None`; `write_world(wid, mid, kind, eid, sheet_type, fields=None)`; `delete_world(wid, mid, kind, eid) -> bool`; `world_list_refs(wid, mid) -> list[tuple[str,str]]`; `world_sheet_modules(wid) -> list[str]`; `seed(cid) -> int` (files copied).
- Consumes: `worlds.world_root`, `modules.pack_root` (existence check raising `ModuleNotFound`).

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_world_sheet_crud_keyed_by_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None)
    s = sheets.read_world(wid, "pool-basic", "characters", "mara")
    assert s["sheet_type"] == "medium" and s["errors"] == []
    assert sheets.world_sheet_modules(wid) == ["pool-basic"]
    assert sheets.world_list_refs(wid, "pool-basic") == [("characters", "mara")]
    assert sheets.read_world(wid, "d20-basic", "characters", "mara") is None
    assert sheets.delete_world(wid, "pool-basic", "characters", "mara") is True


def test_write_world_unknown_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(modules.ModuleNotFound):
        sheets.write_world(wid, "ghost", "characters", "mara", "medium", None)


def test_seed_on_create_matching_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium",
                       {"vigor": 3})
    sheets.write_world(wid, "d20-basic", "characters", "mara", "warrior", None)
    cid = campaigns.create_campaign("Run", wid, module="pool-basic")
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium" and s["fields"]["vigor"] == 3
    # only the matching module's sheets seeded
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_no_seed_without_module_and_no_reseed_on_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None)
    cid = campaigns.create_campaign("Run", wid)
    assert sheets.list_refs(cid) == []
    modules.set_campaign_module(cid, "pool-basic")   # later binding
    assert sheets.list_refs(cid) == []               # never re-seeds
```

- [ ] **Step 2: Run to verify failure** (`AttributeError: write_world`).

- [ ] **Step 3: Implement (append to sheets.py)**

```python
import shutil  # move to the import block at the top


def _world_dir(wid: str, mid: str) -> Path:
    return worlds.world_root(wid) / "sheets" / mid


def _world_path(wid: str, mid: str, kind: str, eid: str) -> Path:
    return _world_dir(wid, mid) / f"{kind}--{eid}.json"


def read_world(wid: str, mid: str, kind: str, eid: str) -> dict | None:
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return None
    try:
        modules.pack_root(mid)
    except modules.ModuleNotFound:
        return None
    return _read_path(_world_path(wid, mid, kind, eid), kind, mid)


def write_world(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                fields: dict | None = None) -> None:
    modules.pack_root(mid)  # raises ModuleNotFound
    _checked_write(_world_path(wid, mid, kind, eid), mid, kind, eid,
                   sheet_type, fields)


def delete_world(wid: str, mid: str, kind: str, eid: str) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return False
    p = _world_path(wid, mid, kind, eid)
    if not p.exists():
        return False
    p.unlink()
    return True


def world_list_refs(wid: str, mid: str) -> list[tuple[str, str]]:
    d = _world_dir(wid, mid)
    out: list[tuple[str, str]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        kind, sep, eid = p.stem.partition("--")
        if sep and kind in FILE_KINDS and _safe_part(eid):
            out.append((kind, eid))
    return out


def world_sheet_modules(wid: str) -> list[str]:
    d = worlds.world_root(wid) / "sheets"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and _safe_part(p.name))


def seed(cid: str) -> int:
    """Copy world starting sheets for the campaign's resolved module.
    Called once from create_campaign; changing the module later never
    re-seeds (spec)."""
    mid = modules.resolve(cid)
    if mid is None:
        return 0
    meta = campaigns.read_campaign(cid)["meta"]
    src = worlds.world_root(meta.get("world", "")) / "sheets" / mid
    if not src.is_dir():
        return 0
    dst = _campaign_dir(cid)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("*.json")):
        shutil.copy2(p, dst / p.name)
        n += 1
    return n
```

In `campaigns.py::create_campaign`, immediately after `calendars.copy_calendar(worlds.world_root(world_id), root)` (line ~91), add:

```python
    from . import sheets
    sheets.seed(cid)
```

(function-level import — campaigns must not import sheets at module top; sheets imports campaigns at top, which is fine because campaigns never does.)

- [ ] **Step 4: Run the whole sheets test file, then the full backend suite** — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/src/grimoire/store/campaigns.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): world starting sheets keyed by module + create-time seeding (#161)"
```

---

### Task 4: Coverage

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py` (append)

**Interfaces:**
- Produces: `coverage(cid) -> dict` and `world_coverage(wid, mid) -> dict`, both `{file_kind: {"total": int, "sheeted": int, "invalid": int}}` (only kinds the module has sheet types for; `{}` when no module / unknown module).
- Consumes: `overlay.list_characters(cid)`, `overlay.list_pcs(cid)`, `overlay.list_entities(cid, kind)` (campaign-visible, tombstone-aware); `characters.list_characters(root)`, `pcs.list_pcs(root)`, `entities.list_entities(root, kind)` (world scope).

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_campaign_coverage(monkeypatch, tmp_path):
    from grimoire.store import entities as ent, overlay
    wid, cid = _campaign(monkeypatch, tmp_path)          # pool-basic
    ent.create_entity(worlds.world_root(wid), "items", "Moon Disc")
    ent.create_entity(worlds.world_root(wid), "locations", "Old Chapel")
    overlay.create_entity(cid, "items", "Salt Knife")
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    cov = sheets.coverage(cid)
    assert cov["items"] == {"total": 2, "sheeted": 1, "invalid": 0}
    assert cov["locations"]["total"] == 1
    # pool-basic has no lore/groups/creatures sheet types -> absent rows
    assert "lore" not in cov and "creatures" not in cov
    assert "characters" in cov and "pcs" in cov          # separate rows


def test_coverage_counts_invalid(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import overlay
    overlay.create_entity(cid, "items", "Moon Disc")
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    modules.set_campaign_module(cid, "d20-basic")        # talisman now unknown
    cov = sheets.coverage(cid)
    assert cov["items"]["invalid"] == 1


def test_coverage_empty_without_module(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path, module=None)
    assert sheets.coverage(cid) == {}


def test_world_coverage(monkeypatch, tmp_path):
    from grimoire.store import entities as ent
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    ent.create_entity(worlds.world_root(wid), "items", "Moon Disc")
    sheets.write_world(wid, "pool-basic", "items", "moon-disc", "talisman", None)
    cov = sheets.world_coverage(wid, "pool-basic")
    assert cov["items"] == {"total": 1, "sheeted": 1, "invalid": 0}
    assert sheets.world_coverage(wid, "ghost") == {}
```

Check `entities.create_entity`'s exact signature (`create_entity(root, kind, name, ...) -> id`) and `overlay.create_entity(cid, kind, name, ...)` — adjust the calls' argument shape if they differ (the ids come from `slugify(name)`, hence `moon-disc`).

- [ ] **Step 2: Run to verify failure** (`AttributeError: coverage`).

- [ ] **Step 3: Implement (append to sheets.py; add `characters`, `overlay`, `pcs` to the top import: `from . import campaigns, characters, entities, expressions, modules, overlay, pcs, worlds`)**

```python
def _type_kinds(sheets_def: dict) -> set[str]:
    return {st.get("kind") for st in sheets_def.get("sheet_types", {}).values()
            if isinstance(st, dict)}


def _tally(ids: list[str], reader) -> dict:
    sheeted = invalid = 0
    for eid in ids:
        s = reader(eid)
        if s is None:
            continue
        sheeted += 1
        if s["errors"]:
            invalid += 1
    return {"total": len(ids), "sheeted": sheeted, "invalid": invalid}


def coverage(cid: str) -> dict:
    mid = modules.resolve(cid)
    if mid is None:
        return {}
    kinds = _type_kinds(modules.load_pack(mid)["sheets"])
    out: dict = {}
    for kind in FILE_KINDS:
        if sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in overlay.list_characters(cid)]
        elif kind == "pcs":
            ids = [p["id"] for p in overlay.list_pcs(cid)]
        else:
            ids = [e["id"] for e in overlay.list_entities(cid, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: read(cid, k, eid))
    return out


def world_coverage(wid: str, mid: str) -> dict:
    try:
        modules.pack_root(mid)
    except modules.ModuleNotFound:
        return {}
    pack = modules.load_pack(mid)
    if pack["errors"]:
        return {}
    kinds = _type_kinds(pack["sheets"])
    root = worlds.world_root(wid)
    out: dict = {}
    for kind in FILE_KINDS:
        if sheet_kind(kind) not in kinds:
            continue
        if kind == "characters":
            ids = [c["id"] for c in characters.list_characters(root)]
        elif kind == "pcs":
            ids = [p["id"] for p in pcs.list_pcs(root)]
        else:
            ids = [e["id"] for e in entities.list_entities(root, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: read_world(wid, mid, k, eid))
    return out
```

- [ ] **Step 4: Run the sheets file + full backend suite** — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): campaign and world coverage tallies (#161)"
```

---

### Task 5: Routes

**Files:**
- Modify: `backend/src/grimoire/routes.py`, `backend/src/grimoire/store/__init__.py` (expose `sheets` like `modules`)
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Produces HTTP API:
  - `GET /api/campaigns/{cid}/sheets` → `{"coverage": {...}, "refs": [["characters","mara"], ...]}`
  - `GET /api/campaigns/{cid}/sheets/{kind}/{eid}` → `{"sheet": {...}|null}`
  - `PUT  ...` body `{sheet_type, fields|null}` → `{"ok": true}`; 400 `SheetError`; 404 unknown campaign
  - `DELETE ...` → `{"ok": bool}` (ok=false when nothing existed)
  - `GET /api/worlds/{wid}/sheets` → `{"modules": [...], "default": "<mid or empty>"}`
  - `GET /api/worlds/{wid}/sheets/{mid}` → `{"coverage": {...}, "refs": [...]}` (404 unknown module)
  - `GET/PUT/DELETE /api/worlds/{wid}/sheets/{mid}/{kind}/{eid}` — same shapes; PUT 404 on unknown module.

- [ ] **Step 1: Write the failing tests (append to test_routes.py)**

```python
def test_campaign_sheet_routes(client):
    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/campaigns/{cid}/characters",
                       json={"name": "Mara", "card": {"description": "x"}}).json()["id"]

    base = f"/api/campaigns/{cid}/sheets/characters/{chid}"
    assert client.get(base).json()["sheet"] is None
    r = client.put(base, json={"sheet_type": "medium", "fields": None})
    assert r.json()["ok"] is True
    got = client.get(base).json()["sheet"]
    assert got["sheet_type"] == "medium" and got["errors"] == []
    assert "sight_pool" in got["derived"]

    idx = client.get(f"/api/campaigns/{cid}/sheets").json()
    assert idx["coverage"]["characters"]["sheeted"] == 1
    assert ["characters", chid] in idx["refs"]

    assert client.put(base, json={"sheet_type": "ghost"}).status_code == 400
    assert client.delete(base).json()["ok"] is True
    assert client.get("/api/campaigns/nope/sheets").status_code == 404


def test_campaign_sheet_routes_without_module(client):
    _, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/sheets").json()["coverage"] == {}
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "medium"})
    assert r.status_code == 400


def test_world_sheet_routes(client):
    wid = _world(client)
    idx = client.get(f"/api/worlds/{wid}/sheets").json()
    assert idx == {"modules": [], "default": ""}
    base = f"/api/worlds/{wid}/sheets/pool-basic/characters/mara"
    assert client.put(base, json={"sheet_type": "medium", "fields": None}).json()["ok"] is True
    assert client.get(base).json()["sheet"]["sheet_type"] == "medium"
    idx = client.get(f"/api/worlds/{wid}/sheets").json()
    assert idx["modules"] == ["pool-basic"]
    cov = client.get(f"/api/worlds/{wid}/sheets/pool-basic").json()
    assert ["characters", "mara"] in cov["refs"]
    assert client.get(f"/api/worlds/{wid}/sheets/ghost").status_code == 404
    assert client.put(f"/api/worlds/{wid}/sheets/ghost/characters/mara",
                      json={"sheet_type": "medium"}).status_code == 404
    assert client.delete(base).json()["ok"] is True
```

Check the character-create route's actual body shape in test_routes.py (search an existing `POST /campaigns/{cid}/characters` test) and mirror it — if characters are created differently (e.g. via world + overlay), create the character through whatever existing API pattern the file already uses; the sheet assertions are the point.

- [ ] **Step 2: Run to verify failure** (404s).

- [ ] **Step 3: Implement**

Model (in the `# ---- models ----` block):

```python
class SheetBody(BaseModel):
    sheet_type: str
    fields: dict | None = None
```

Expose in `backend/src/grimoire/store/__init__.py`: add `sheets` to the existing submodule import line that includes `modules`.

Campaign endpoints — **beside the `/campaigns/{cid}/module` block (routes.py ~2336), before the generic `/campaigns/{cid}/{kind}` routes**:

```python
@router.get("/campaigns/{cid}/sheets")
def get_campaign_sheets(cid: str):
    _campaign_root_or_404(cid)
    return {"coverage": store.sheets.coverage(cid),
            "refs": store.sheets.list_refs(cid)}


@router.get("/campaigns/{cid}/sheets/{kind}/{eid}")
def get_campaign_sheet(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.put("/campaigns/{cid}/sheets/{kind}/{eid}")
def put_campaign_sheet(cid: str, kind: str, eid: str, body: SheetBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write(cid, kind, eid, body.sheet_type, body.fields)
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/campaigns/{cid}/sheets/{kind}/{eid}")
def delete_campaign_sheet(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return {"ok": store.sheets.delete(cid, kind, eid)}
```

World endpoints — **beside `/worlds/{wid}/module` (routes.py ~478), before the generic `/worlds/{wid}/{kind}` routes (~1331)**:

```python
@router.get("/worlds/{wid}/sheets")
def get_world_sheets_index(wid: str):
    _world_root_or_404(wid)
    meta = store.worlds.read_world(wid)["meta"]
    return {"modules": store.sheets.world_sheet_modules(wid),
            "default": (meta.get("module") or "").strip()}


@router.get("/worlds/{wid}/sheets/{mid}")
def get_world_sheets(wid: str, mid: str):
    _world_root_or_404(wid)
    try:
        store.modules.pack_root(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"coverage": store.sheets.world_coverage(wid, mid),
            "refs": store.sheets.world_list_refs(wid, mid)}


@router.get("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def get_world_sheet(wid: str, mid: str, kind: str, eid: str):
    _world_root_or_404(wid)
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}


@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def put_world_sheet(wid: str, mid: str, kind: str, eid: str, body: SheetBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world(wid, mid, kind, eid, body.sheet_type, body.fields)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def delete_world_sheet(wid: str, mid: str, kind: str, eid: str):
    _world_root_or_404(wid)
    return {"ok": store.sheets.delete_world(wid, mid, kind, eid)}
```

- [ ] **Step 4: Run test_routes.py, then the full backend suite** — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/__init__.py backend/tests/test_routes.py
git commit -m "feat(routes): campaign and world sheet endpoints + coverage (#161)"
```

---

### Task 6: Reference pack fleshing

**Files:**
- Modify: `backend/src/grimoire/store/builtin_modules/pool-basic/sheets.json`, `backend/src/grimoire/store/builtin_modules/d20-basic/sheets.json`
- Test: `backend/tests/test_modules_store.py` (append one assertion test)

Additions (exact):
- `pool-basic` group `abilities` gains `{"key": "empathy", "label": "Empathy", "type": "dots", "max": 5, "default": 0}` and `{"key": "lore", "label": "Lore", "type": "dots", "max": 5, "default": 0}`.
- `pool-basic` sheet types `medium` and `shifter` each gain `{"key": "quirk", "label": "Quirk", "type": "text"}` and `{"key": "gear", "label": "Gear", "type": "list"}` in their `fields`.
- `d20-basic` sheet type `adept` gains `{"key": "spells", "label": "Spells", "type": "list"}`.

Test (append):

```python
def test_fleshed_reference_packs(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    pool = modules.load_pack("pool-basic")
    assert pool["errors"] == []
    medium = pool["sheets"]["sheet_types"]["medium"]
    keys = {f["key"] for f in medium["fields"]}
    assert {"quirk", "gear"} <= keys
    d20 = modules.load_pack("d20-basic")
    assert d20["errors"] == []
    assert any(f["key"] == "spells"
               for f in d20["sheets"]["sheet_types"]["adept"]["fields"])
```

Steps: failing test → edit the two JSON files → module tests green (including the Phase-1 builtin assertions) → full backend suite → commit:

```bash
git add backend/src/grimoire/store/builtin_modules backend/tests/test_modules_store.py
git commit -m "feat(modules): flesh reference packs so every widget type appears (#161)"
```

---

### Task 7: Frontend client — sheet types + functions

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces (produced, used by Tasks 8-12):**

```ts
export type Sheet = {
  sheet_type: string | null;
  fields: Record<string, unknown>;
  derived: Record<string, number | boolean>;
  errors: string[];
};
export type SheetCoverage = Record<string, { total: number; sheeted: number; invalid: number }>;
```

api functions (scope-aware ones branch on `scope.kind`, taking `mid` for the world side; model on `readEntity`'s `entityBase` style but sheets need the module segment only at world scope):

```ts
  getCampaignSheets: (cid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/campaigns/${cid}/sheets`),
  getWorldSheetsIndex: (wid: string) =>
    request<{ modules: string[]; default: string }>("GET", `/api/worlds/${wid}/sheets`),
  getWorldSheets: (wid: string, mid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/worlds/${wid}/sheets/${mid}`),
  getSheet: (scope: EntityScope, mid: string, kind: string, eid: string) =>
    request<{ sheet: Sheet | null }>(
      "GET",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`),
  putSheet: (scope: EntityScope, mid: string, kind: string, eid: string,
             body: { sheet_type: string; fields: Record<string, unknown> | null }) =>
    request<{ ok: boolean }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`,
      body),
  deleteSheet: (scope: EntityScope, mid: string, kind: string, eid: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`),
```

Steps: add types + functions → from `frontend/`: `npx tsc -b` clean, `npx vitest run` green (purely additive) → commit:

```bash
git add frontend/src/api/client.ts
git commit -m "feat(client): sheet CRUD and coverage API functions (#161)"
```

---

### Task 8: SheetEditor (takeover)

**Files:**
- Create: `frontend/src/components/SheetEditor.tsx`
- Test: `frontend/src/components/SheetEditor.test.tsx`
- Modify: `frontend/src/index.css` (takeover styles)

**Interfaces:**
- Produces: `export default function SheetEditor({ scope, module, kind, eid, initial, onClose, onSaved }: { scope: EntityScope; module: ModuleDetail; kind: string; eid: string; initial: Sheet; onClose: () => void; onSaved: () => void })`.
- Consumes: `api.putSheet`, `api.deleteSheet`, `ModuleDetail.sheets` (groups/sheet_types with `ModuleField`s), `Sheet` type.
- Behavior: renders a fixed overlay (`.sheet-takeover` + `.sheet-backdrop`) over the detail area. View mode: one section per group (group label + `label: value` rows) then own fields; derived values listed read-only with a `field-hint` marker; sheet `errors` render in a `.banner`. Edit mode: widgets per Global-Constraints table (`number`/`dots`/`track` → bounded number input; `resource` → paired current/max number inputs; `text` → text input; `list` → textarea one-per-line). Header `.form-actions`: Edit / Save / Cancel / Change type… / Delete sheet / Close.
- Type change: a select of the kind's other sheet types; on pick, compute dropped keys client-side — `keysOf(type) = module.sheets.sheet_types[t].groups.flatMap(g => module.sheets.groups[g]?.fields ?? []).concat(fields).map(f => f.key)` — confirm via `window.confirm` listing them, then `putSheet` with the new `sheet_type` and the current draft filtered to surviving keys, then `onSaved()`.

The `sheet_kind` mapping matters here: for `kind="pcs"` filter sheet types by `st.kind === "characters"`; add a tiny helper `const typeKind = (k: string) => (k === "pcs" ? "characters" : k);` and export it for SheetPanel's reuse.

Representative test (mock `api: { putSheet: vi.fn(), deleteSheet: vi.fn() }`; build a small `ModuleDetail` fixture with groups `attributes` (vigor dots max 5) and types `medium`/`shifter` for characters):

```tsx
test("view shows groups and derived; edit saves fields", async () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={onSaved} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument();
  expect(screen.getByText(/sight_pool/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Edit"));
  const vigor = screen.getByLabelText("Vigor") as HTMLInputElement;
  fireEvent.change(vigor, { target: { value: "4" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "medium", fields: expect.objectContaining({ vigor: 4 }) }));
});

test("change type confirms and filters orphans", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<SheetEditor ... initial={SHEET} ... />);
  fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "shifter" } });
  await waitFor(() => expect(api.putSheet).toHaveBeenCalled());
  const body = (api.putSheet as any).mock.calls[0][4];
  expect(body.sheet_type).toBe("shifter");
  expect(body.fields).not.toHaveProperty("essence");
});
```

CSS (append to index.css, matching existing variable usage — check how `.panel-slot`/`.detail-view` colors are declared and reuse those variables):

```css
.sheet-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 39; }
.sheet-takeover { position: fixed; inset: 6% 14%; overflow-y: auto; z-index: 40;
  border-radius: 8px; padding: 1rem 1.5rem; }
```

(Implementer: give `.sheet-takeover` the same background/border as `.editor-body` or the app's panel surface — read index.css and reuse its custom properties rather than hardcoding colors.)

Steps: failing test → implement → `npx vitest run src/components/SheetEditor.test.tsx` + `npx tsc -b` → commit:

```bash
git add frontend/src/components/SheetEditor.tsx frontend/src/components/SheetEditor.test.tsx frontend/src/index.css
git commit -m "feat(frontend): takeover sheet editor with typed widgets and type change (#161)"
```

---

### Task 9: SheetPanel

**Files:**
- Create: `frontend/src/components/SheetPanel.tsx`
- Test: `frontend/src/components/SheetPanel.test.tsx`

**Interfaces:**
- Produces: `export default function SheetPanel({ scope, module, kind, eid }: { scope: EntityScope; module: ModuleDetail | null; kind: string; eid: string })`.
- Consumes: `api.getSheet`, `api.putSheet`, `Sheet`, `ModuleDetail`, `SheetEditor` (Task 8), `typeKind` helper.
- Behavior: returns `null` when `module` is null or `module.sheets.sheet_types` has no type with `kind === typeKind(kind)`. Otherwise fetch `api.getSheet(scope, module.id, kind, eid)` on `[scope.kind, scope.id, module.id, kind, eid]`.
  - **Unsheeted** (`sheet: null`): `.side-section` "Sheet" with hint "No sheet", a type select (aria-label "Sheet type", auto-selected single option) and a Create button → `putSheet(..., { sheet_type, fields: null })` → refetch → open editor.
  - **Sheeted:** sheet-type chip (label from the module), summary rows: each `resource` field as `name current/max`, each derived as `name value` chips; Open sheet button toggling `<SheetEditor initial={sheet} …/>` with `onSaved` refetching.
  - **Invalid:** same + warning `field-hint` lines for each error.
  - Fetch/save failures set a `.field-hint` error (never unhandled rejections).

Test (mock `api: { getSheet: vi.fn(), putSheet: vi.fn() }`, plus mock `./SheetEditor` with `vi.mock("./SheetEditor", () => ({ default: () => <div data-testid="sheet-editor" /> }))`):

```tsx
test("renders nothing without module or matching type", () => {
  const { container, rerender } = render(
    <SheetPanel scope={CAMP} module={null} kind="characters" eid="mara" />);
  expect(container.firstChild).toBeNull();
  rerender(<SheetPanel scope={CAMP} module={MOD} kind="lore" eid="secret" />);
  expect(container.firstChild).toBeNull();   // MOD has no lore sheet types
  expect(api.getSheet).not.toHaveBeenCalled();
});

test("unsheeted: create with picked type then editor opens", async () => {
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.putSheet as any).mockResolvedValue({ ok: true });
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "medium" } });
  (api.getSheet as any).mockResolvedValue({ sheet: FRESH_SHEET });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    CAMP, "pool-basic", "characters", "mara", { sheet_type: "medium", fields: null }));
  expect(await screen.findByTestId("sheet-editor")).toBeInTheDocument();
});

test("sheeted: summary chips + open", async () => {
  (api.getSheet as any).mockResolvedValue({ sheet: SHEET });  // medium, essence 6/10, sight_pool 6
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  expect(await screen.findByText("Medium")).toBeInTheDocument();       // type chip
  expect(screen.getByText(/essence 6\/10/)).toBeInTheDocument();       // resource summary
  expect(screen.getByText(/sight_pool 6/)).toBeInTheDocument();        // derived chip
  fireEvent.click(screen.getByText("Open sheet"));
  expect(await screen.findByTestId("sheet-editor")).toBeInTheDocument();
});

test("invalid: error hints listed", async () => {
  (api.getSheet as any).mockResolvedValue({
    sheet: { ...SHEET, errors: ["unknown sheet type 'medium'"] } });
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  expect(await screen.findByText(/unknown sheet type/)).toBeInTheDocument();
  expect(screen.getByText("Open sheet")).toBeInTheDocument();          // repair path stays open
});
```

Steps: failing tests → implement → focused vitest + `npx tsc -b` → commit:

```bash
git add frontend/src/components/SheetPanel.tsx frontend/src/components/SheetPanel.test.tsx
git commit -m "feat(frontend): SheetPanel side-section with create/summary/invalid states (#161)"
```

---

### Task 10: Mount SheetPanel in the three editors

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx` (sidebar, after GroupStatePanel at ~line 338), `frontend/src/components/PCEditor.tsx` (sidebar, after Tags ~line 239), `frontend/src/components/CharacterEditor.tsx` (detail flow, after the Version block ~line 900)
- Test: extend `EntityEditor.test.tsx` with ONE campaign-scope test proving the mount (the others stay inert)

Each editor gains an optional prop `module?: ModuleDetail | null` (default `null` — **existing tests pass nothing and stay green**) and renders:

```tsx
// EntityEditor, inside .detail-sidebar:
{module && editing && (
  <SheetPanel scope={scope} module={module} kind={kind} eid={editing} />
)}
// PCEditor, inside .detail-sidebar:
{module && detail && (
  <SheetPanel scope={scope} module={module} kind="pcs" eid={detail.meta.id} />
)}
// CharacterEditor, in the .detail flow:
{module && detail && (
  <SheetPanel scope={scope} module={module} kind="characters" eid={detail.meta.id} />
)}
```

New EntityEditor test (mock `../api/client`'s api additionally with `getSheet: vi.fn().mockResolvedValue({ sheet: null })`; pass a minimal `module` fixture and `scope={{kind:"campaign",id:"run"}}`): clicking a row shows the detail with a "Sheet" side-section. Also add `getSheet`/`putSheet` as `vi.fn()` to the EntityEditor mock's api object (harmless for other tests since the panel only mounts when `module` is passed).

Steps: failing test → wire the three editors → full `npx vitest run` (all existing editor tests must stay green) + `npx tsc -b` → commit:

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/PCEditor.tsx frontend/src/components/CharacterEditor.tsx frontend/src/components/EntityEditor.test.tsx
git commit -m "feat(frontend): mount SheetPanel in entity, PC, and character detail views (#161)"
```

---

### Task 11: WorldView module context

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx`, `frontend/src/routes/WorldView.test.tsx`

**Interfaces:**
- Produces: WorldView state `moduleCtx: ModuleDetail | null` and `worldMid: string` + setter, threaded as `module={moduleCtx}` into the CharacterEditor/PCEditor/EntityEditor render calls (lines 117-133). **Do NOT touch WorldOverview in this task** — it doesn't accept the picker props yet; Task 12 changes WorldOverview's props and adds the `worldMid`/`onPickMid` threading in WorldView at the same time (keeping `tsc` green after every task).
- Behavior: in the existing data `useEffect` (~lines 40-51) extend:
  - campaign: `api.getCampaignModule(cid)` → if `resolved`, `api.readModule(resolved)` → `setModuleCtx`; else null. Failures → null.
  - world: `api.getWorldSheetsIndex(wid)` + `api.listModules()` → pick initial `worldMid`: index `default`, else first of index `modules`, else first installed module id, else `""`. A second effect on `worldMid`: `readModule(worldMid)` → `setModuleCtx` (null when `""`).

WorldView.test.tsx: extend the hand-authored mock with `getCampaignModule: vi.fn().mockResolvedValue({ setting: "", resolved: null, source: null })`, `readModule: vi.fn()`, `getWorldSheetsIndex: vi.fn().mockResolvedValue({ modules: [], default: "" })` (listModules already mocked). Add one test: campaign path with `getCampaignModule` resolving `pool-basic` and `readModule` returning a fixture → the characters tab's detail (drive an existing campaign-path test pattern) shows the Sheet section. Also add `getSheet: vi.fn().mockResolvedValue({ sheet: null })` for that test.

Steps: failing/extended tests → implement → full `npx vitest run` + `npx tsc -b` → commit:

```bash
git add frontend/src/routes/WorldView.tsx frontend/src/routes/WorldView.test.tsx
git commit -m "feat(frontend): lift module context into WorldView and thread to editors (#161)"
```

---

### Task 12: Coverage blocks + world module picker

**Files:**
- Modify: `frontend/src/components/MechanicsConfig.tsx` (+test), `frontend/src/components/WorldMechanics.tsx` (+test), `frontend/src/components/WorldOverview.tsx`, `frontend/src/routes/WorldView.tsx` (thread `worldMid`/`onPickMid` into the `<WorldOverview>` render call — deferred from Task 11)

**MechanicsConfig** (campaign): when `state.resolved`, fetch `api.getCampaignSheets(cid)` and render below the resolved hint:

```tsx
{coverage && Object.keys(coverage).length > 0 && (
  <div className="side-section">
    <h4>Sheets</h4>
    {Object.entries(coverage).map(([kind, c]) => (
      <div key={kind} className="field-hint">
        {KIND_LABELS[kind] ?? kind} {c.sheeted}/{c.total}
        {c.invalid > 0 ? ` · ${c.invalid} invalid` : ""}
      </div>
    ))}
  </div>
)}
```

with `const KIND_LABELS: Record<string, string> = { characters: "Characters", pcs: "PCs", locations: "Locations", lore: "Lore", items: "Items", groups: "Groups", creatures: "Creatures" };`. Extend MechanicsConfig.test.tsx mock with `getCampaignSheets: vi.fn().mockResolvedValue({ coverage: {}, refs: [] })` in `beforeEach`, plus one test asserting "Characters 1/3" renders from a fixture coverage.

**WorldMechanics** (world): new props `{ wid, worldMid, onPickMid }` (WorldOverview threads them from WorldView — update `WorldOverview` props accordingly: `{ wid, onNavigate, worldMid, onPickMid }` and pass through). Add below the default-module block: a "Starting sheets for:" select (aria-label "Starting sheets module", value `worldMid`, options from `listModules`) calling `onPickMid`, and when `worldMid` is truthy fetch `api.getWorldSheets(wid, worldMid)` and render the same coverage block. Extend WorldMechanics.test.tsx: mock adds `getWorldSheets`; tests for picker calling `onPickMid` and coverage rendering.

Steps: failing tests → implement all three files → full `npx vitest run` + `npx tsc -b` → commit:

```bash
git add frontend/src/components/MechanicsConfig.tsx frontend/src/components/MechanicsConfig.test.tsx frontend/src/components/WorldMechanics.tsx frontend/src/components/WorldMechanics.test.tsx frontend/src/components/WorldOverview.tsx
git commit -m "feat(frontend): sheet coverage blocks + world starting-sheet module picker (#161)"
```

---

### Task 13: Full verification

- [ ] Backend full suite (worktree PYTHONPATH prefix) — green.
- [ ] Frontend from `frontend/`: `npx vitest run` and `npx tsc -b` — green.
- [ ] End-state smoke via routes tests (already covered): campaign sheet flow and world-seeding path are `test_campaign_sheet_routes` and `test_seed_on_create_matching_module`.
- [ ] Update `.claude/skills/create-mechanics-module/SKILL.md` if it references "sheets land in Phase 3" as future — add one line noting sheet instances now exist (campaign + world starting sheets) and the reserved-key rules (function names, `_max` collisions) that packs must now satisfy.
- [ ] Commit any stragglers; branch ready for rebase-merge + the GitHub issue pass from the header.
