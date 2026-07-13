# Mechanics Phase 5 — Narrated-Event Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a campaign resolves to a mechanics module, end-scene absorb also audits the transcript against sheets + the roll log, surfacing contradiction warnings and proposing per-field sheet deltas through the existing StagedEdit review flow.

**Architecture:** A second focused LLM call (`store/audit.py` + `templates/audit/`) runs after the prose absorb call, gated by scene-start sheet baselines (`sheet_baselines.json`, validity = module id + schema stamp + per-sheet `gen` nonce + type). All campaign sheet writers move onto one per-campaign lock with mandatory CAS (`expected` snapshot incl. `gen`; `expected_gen` for delete); the absorb apply path re-authorizes inside that lock via `audit.apply_delta` → `sheets.set_field` (strict per-field CAS). Every audit degradation and every failed sheet edit is user-visible (`mechanics.status` / `dropped` / `sheet_failures`).

**Tech Stack:** FastAPI backend (pytest), Vite/React frontend (vitest), jinja2 prompts, pure-stdlib stores.

**Spec:** `docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md` (read it before starting any task; its Decisions table is the contract).

## Global Constraints

- Privacy rule: only invented names in fixtures/tests/docs (Seraphine, Mara, Winifred, Realm, Saltmarch...). Never a real world/campaign/character name.
- pydantic stays v1/v2-agnostic: plain `BaseModel` fields only, no `Field`/validators/`ConfigDict`; dump via `routes._dump`.
- Backend must not assume repo layout or desktop `~`: filesystem via `store.paths`, templates via `prompts.templates_dir()`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (repo root). Frontend: `npx vitest run` and `npx tsc -b` **from `frontend/`**.
- Base deps must stay Android-installable; this phase adds no dependencies (hashlib/uuid/threading are stdlib).
- Lock ordering (never reversed): **sheet lock → baseline lock**. The world-rebind route is the only path allowed to hold sheet locks of more than one campaign, acquired in sorted-cid order.
- Mutable field types (the only delta-eligible ones): `resource`, `track`, `list`. Static: `number`, `dots`, `text`. Absorb never changes a resource's `max`.
- New routes register **before** generic `/campaigns/{cid}/{kind}` catch-alls (house rule; see existing `# also registered before...` comments in `routes.py`).

---

### Task 1: Sheet generation nonce (`gen`)

Every sheet file carries `"gen": "<uuid4 hex>"` — minted on creation and on type-changing writes, preserved by value writes, surfaced by reads. This is the sheet-identity token every later task compares.

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py` (`_checked_write`, `_read_path`, `_checked_creation_write`, `advance`)
- Test: `backend/tests/test_sheets_store.py` (append)

**Interfaces:**
- Produces: `sheets.read(...)`/`read_world(...)` results gain `"gen": str | None`. `_checked_write`/`_checked_creation_write` write `{"sheet_type", "fields", "gen"}`. Helper `_next_gen(path: Path, sheet_type: str) -> str` (module-private).
- Consumes: existing `_checked_write(path, mid, file_kind, eid, sheet_type, fields)` and `_read_path`.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_sheets_store.py`; reuse that file's existing fixtures for a module-bound campaign — it already builds one for write/read tests; the snippets below assume a fixture `cid` bound to the `d20-basic` reference pack and an existing character `mara` with sheet type `adventurer`; adapt names to the file's actual fixtures):

```python
def test_gen_minted_on_create(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None) \
        if "expected" in sheets.write.__code__.co_varnames else \
        sheets.write(cid, "characters", "mara", "adventurer", None)
    s = sheets.read(cid, "characters", "mara")
    assert isinstance(s["gen"], str) and len(s["gen"]) == 32


def test_gen_preserved_on_same_type_write(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    fields = sheets.read(cid, "characters", "mara")["fields"]
    sheets.write(cid, "characters", "mara", "adventurer", {**fields, "athletics": 3})
    assert sheets.read(cid, "characters", "mara")["gen"] == g1


def test_gen_minted_on_type_change(cid):
    # d20-basic must have (or the test adds to a scratch pack) a second
    # characters-kind sheet type; if the reference pack has only one, create
    # the sheet, hand-edit the stored file's sheet_type to a fake old value,
    # then write with the real type and assert the gen changed.
    sheets.write(cid, "characters", "mara", "adventurer", None)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    p = sheets._campaign_path(cid, "characters", "mara")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["sheet_type"] = "legacy-type"
    p.write_text(json.dumps(data), encoding="utf-8")
    sheets.write(cid, "characters", "mara", "adventurer",
                 sheets.read(cid, "characters", "mara")["fields"])
    assert sheets.read(cid, "characters", "mara")["gen"] != g1


def test_legacy_file_without_gen_reads_none_and_gains_one_on_write(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None)
    p = sheets._campaign_path(cid, "characters", "mara")
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["gen"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert sheets.read(cid, "characters", "mara")["gen"] is None
    sheets.write(cid, "characters", "mara", "adventurer", data["fields"])
    assert isinstance(sheets.read(cid, "characters", "mara")["gen"], str)


def test_advance_preserves_gen(cid_with_advancement):
    # reuse the existing advancement fixture from the Phase 7 tests in this file
    cid, kind, eid = cid_with_advancement
    g1 = sheets.read(cid, kind, eid)["gen"]
    sheets.advance(cid, kind, eid, "athletics")
    assert sheets.read(cid, kind, eid)["gen"] == g1
```

Note: until Task 3 lands, `sheets.write` has no `expected` parameter — write the Task 1 tests against the current signature (the first test's conditional shows the intent; simply call the current signature). Task 3 updates these call sites.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q -k gen`
Expected: FAIL — `KeyError: 'gen'` (read result has no such key).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/sheets.py`, add near the top (after the imports; add `import uuid`):

```python
def _next_gen(path: Path, sheet_type: str) -> str:
    """Sheet identity nonce: preserved across same-type value writes, minted
    on creation and on type changes (a type change is logically a new sheet).
    Legacy files without a gen mint one on their next whole-sheet write."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = {}
        if isinstance(data, dict) and data.get("sheet_type") == sheet_type \
                and isinstance(data.get("gen"), str) and data["gen"]:
            return data["gen"]
    return uuid.uuid4().hex
```

In `_checked_write`, change the final write to:

```python
    _atomic_write_json(path, {"sheet_type": sheet_type, "fields": fields,
                              "gen": _next_gen(path, sheet_type)})
```

Same change at the end of `_checked_creation_write`. In `advance`, the final
`_atomic_write_json` call preserves the loaded gen:

```python
        _atomic_write_json(path, {"sheet_type": sheet_type, "fields": new_fields,
                                  "gen": data.get("gen")})
```

In `_read_path`, surface it — every returned dict gains `"gen"`:

```python
    # unreadable / non-dict branches:
    return {"sheet_type": None, "fields": {}, "derived": {}, "gen": None,
            "errors": [...]}          # (both early-return branches)
    # main return:
    return {"sheet_type": sheet_type, "fields": fields, "gen": data.get("gen"),
            "derived": derived, "errors": errors}
    # and the mid-is-None branch likewise gains "gen": data.get("gen")
```

- [ ] **Step 4: Run the sheet-store tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q`
Expected: PASS (including pre-existing tests — `gen` is additive; if any pre-existing test asserts an exact read dict, extend its expectation with `"gen": ANY`).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "feat(sheets): generation nonce on every sheet file (mechanics P5)"
```

---

### Task 2: One lock for every campaign-sheet mutator

`write`, `write_creation`, `delete` join `advance` under the per-campaign lock; the lock accessor goes public as `lock_for` for later tasks.

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py` (append)

**Interfaces:**
- Produces: `sheets.lock_for(cid) -> threading.Lock` (public; the old private `_lock_for` is renamed — update `advance`). All campaign mutators run their read-validate-write inside `with lock_for(cid):`. **Use `threading.RLock`** so `audit.apply_delta` (Task 8) can hold it around a `set_field` call without deadlock.
- Consumes: Task 1's write helpers.

- [ ] **Step 1: Write the failing test**

```python
def test_editor_write_serializes_with_advance(cid_with_advancement):
    """The pre-existing hole: a whole-sheet write interleaving advance's
    read-modify-write could lose the advancement. Serialized, both survive."""
    import threading
    cid, kind, eid = cid_with_advancement
    base_fields = sheets.read(cid, kind, eid)["fields"]
    errs = []

    def do_advance():
        try:
            sheets.advance(cid, kind, eid, "athletics")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    def do_write():
        try:
            sheets.write(cid, kind, eid, sheets.read(cid, kind, eid)["sheet_type"],
                         {**base_fields, "stealth": 2})
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=do_advance), threading.Thread(target=do_write)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Serialization guarantee is about atomicity, not order: whichever ran
    # second operated on the first's committed state, so neither raced a torn
    # read-modify-write. (Same-field last-write-wins between these two writers
    # is resolved by CAS in Task 3; this test only proves lock coverage —
    # no exception from a torn file, file parses cleanly.)
    s = sheets.read(cid, kind, eid)
    assert s["errors"] == [] and not errs
```

Also add a direct check that `lock_for` exists and is reentrant:

```python
def test_lock_for_public_and_reentrant(cid):
    lock = sheets.lock_for(cid)
    with lock:
        with lock:  # RLock: no deadlock
            pass
    assert sheets.lock_for(cid) is lock


def test_write_resolves_module_inside_the_lock(cid, monkeypatch):
    """Rebind serialization invariant: no campaign mutator may call
    modules.resolve outside lock_for(cid) — otherwise a writer could resolve
    module A, lose the CPU to a rebind publishing B under the lock, then
    write under A after B is visible."""
    from grimoire.store import modules as modules_mod
    real = modules_mod.resolve
    seen = []

    def spy(c):
        seen.append(sheets.lock_for(c)._is_owned())  # RLock: owned by us?
        return real(c)

    monkeypatch.setattr(modules_mod, "resolve", spy)
    sheets.write(cid, "characters", "mara", "adventurer", None)
    assert seen and all(seen)
```

(Keep this spy test green through Tasks 3 and 5 — it pins the ordering for
`write`; add the same one-liner assertion for `set_field` in Task 5's tests.)

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q -k "lock_for or serializes"`
Expected: FAIL — `AttributeError: module ... has no attribute 'lock_for'`.

- [ ] **Step 3: Implement**

In `sheets.py`: change the registry to RLocks and make the accessor public:

```python
_campaign_locks: dict[str, threading.RLock] = {}


def lock_for(cid: str) -> threading.RLock:
    """Get-or-create the per-campaign sheet lock atomically. Public: every
    campaign-sheet mutator, audit.capture_baseline, audit.apply_delta, and
    the module-rebind routes serialize on this. RLock so apply_delta can
    compose set_field under an already-held lock."""
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.RLock())
```

Update `advance` to `lock = lock_for(cid)`. Wrap the bodies of `write`, `write_creation` (`_assert_campaign_entity_exists` + `_checked_creation_write`), and `delete` in `with lock_for(cid):` — everything from the first read to the final write/unlink stays inside. `write` becomes:

```python
def write(cid: str, kind: str, eid: str, sheet_type: str,
          fields: dict | None = None) -> None:
    with lock_for(cid):
        # resolve INSIDE the lock: rebinds publish under this same lock, so a
        # writer can never resolve module A, lose the CPU to a rebind to B,
        # and then validate/write under A after B is visible.
        mid = modules.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _checked_write(_campaign_path(cid, kind, eid), mid, kind, eid,
                       sheet_type, fields)
```

(`delete` keeps returning `bool`; world writes are untouched. The same
lock-then-resolve ordering applies to `write_creation` here and to the Task 3
and Task 5 versions of `write`/`write_creation`/`set_field` — **no campaign
mutator ever calls `modules.resolve` outside `lock_for(cid)`**.)

- [ ] **Step 4: Run the sheet-store tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/tests/test_sheets_store.py
git commit -m "fix(sheets): every campaign-sheet mutator serializes on the campaign lock"
```

---

### Task 3: Mandatory whole-sheet CAS (`expected`) + `SheetConflict`

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`, `backend/src/grimoire/routes.py` (SheetBody, sheet PUT, creation PUT, instantiate route)
- Test: `backend/tests/test_sheets_store.py`, `backend/tests/test_routes.py` (append)

**Interfaces:**
- Produces: `class SheetConflict(SheetError)`; `sheets.write(cid, kind, eid, sheet_type, fields=None, *, expected: dict | None)` and `sheets.write_creation(cid, kind, eid, sheet_type, spends, *, expected: dict | None)` — `expected` is the caller's last-read `{"sheet_type", "fields", "gen"}` snapshot; `None` asserts no sheet exists. Route: `SheetBody` gains `expected: dict | None = None` (omitted == null == creation assertion; fails closed); PUT maps `SheetConflict` → 409 `{"detail": "..."}`.
- Consumes: Task 1 `gen`, Task 2 `lock_for`.

- [ ] **Step 1: Write the failing store tests**

```python
def _snapshot(cid, kind, eid):
    s = sheets.read(cid, kind, eid)
    return {"sheet_type": s["sheet_type"], "fields": s["fields"], "gen": s["gen"]}


def test_cas_none_expected_creates_then_conflicts(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    with pytest.raises(sheets.SheetConflict):
        sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)


def test_cas_matching_snapshot_writes(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "adventurer",
                 {**snap["fields"], "athletics": 3}, expected=snap)
    assert sheets.read(cid, "characters", "mara")["fields"]["athletics"] == 3


def test_cas_stale_fields_conflict(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "adventurer",
                 {**snap["fields"], "athletics": 3}, expected=snap)
    with pytest.raises(sheets.SheetConflict):  # snap is now stale
        sheets.write(cid, "characters", "mara", "adventurer",
                     {**snap["fields"], "athletics": 1}, expected=snap)


def test_cas_gen_mismatch_with_identical_content_conflicts(cid):
    """ABA: delete + recreate with identical type/default fields must still
    409 a stale editor whose snapshot matches by value."""
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.delete(cid, "characters", "mara", expected_gen=snap["gen"]) \
        if "expected_gen" in sheets.delete.__code__.co_varnames else \
        sheets.delete(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    live = _snapshot(cid, "characters", "mara")
    assert live["sheet_type"] == snap["sheet_type"] and live["fields"] == snap["fields"]
    with pytest.raises(sheets.SheetConflict):
        sheets.write(cid, "characters", "mara", "adventurer",
                     snap["fields"], expected=snap)
```

(Until Task 4 lands, call the current `delete` signature — the conditional above shows the intent; Task 4 updates the call site.)

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -q -k cas`
Expected: FAIL — `TypeError: write() got an unexpected keyword argument 'expected'`.

- [ ] **Step 3: Implement the store side**

In `sheets.py`:

```python
class SheetConflict(SheetError):
    """CAS rejection: the sheet changed since the caller last read it."""


def _stored_snapshot(path: Path) -> dict | None:
    """{"sheet_type", "fields", "gen"} as stored, or None when no file. An
    unreadable file yields an all-None snapshot (matches nothing sane)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"sheet_type": data.get("sheet_type"),
            "fields": data.get("fields") if isinstance(data.get("fields"), dict) else {},
            "gen": data.get("gen")}


def _check_expected(path: Path, expected: dict | None) -> None:
    """Mandatory whole-sheet CAS. expected=None asserts creation; otherwise
    sheet_type AND fields AND gen must all match the stored snapshot."""
    stored = _stored_snapshot(path)
    if expected is None:
        if stored is not None:
            raise SheetConflict("a sheet already exists for this entity")
        return
    if stored is None:
        raise SheetConflict("no sheet exists for this entity")
    if not isinstance(expected, dict):
        raise SheetError("expected must be an object or null")
    if (stored["sheet_type"] != expected.get("sheet_type")
            or stored["fields"] != expected.get("fields")
            or stored["gen"] != expected.get("gen")):
        raise SheetConflict("the sheet changed since it was loaded")
```

`write` and `write_creation` gain the required kw-only param and call
`_check_expected` inside the lock, before `_checked_write`/`_checked_creation_write`:

```python
def write(cid, kind, eid, sheet_type, fields=None, *, expected):
    with lock_for(cid):
        mid = modules.resolve(cid)      # inside the lock (rebind serialization)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        path = _campaign_path(cid, kind, eid)
        _check_expected(path, expected)
        _checked_write(path, mid, kind, eid, sheet_type, fields)
```

(`write_creation` mirrors this around its existing body. World writes keep their old signatures.)

**Fix every campaign caller in the same task** (grep `sheets.write(` / `sheets.write_creation(` under `backend/src` and `backend/tests`):
- `routes.py:2903` (sheet PUT) → `store.sheets.write(cid, kind, eid, body.sheet_type, body.fields, expected=body.expected)` and catch `SheetConflict` **before** `SheetError`, mapping to `HTTPException(status_code=409, detail=str(e))`.
- `routes.py:2919` (creation PUT) → `expected=body.expected` on a new `SheetCreationBody.expected: dict | None = None`, same 409 mapping.
- `routes.py:2955` (instantiate) → `expected=None` (entity created this request; the existing rollback already handles the raise).
- `sheets.seed` is untouched (raw file copy — exempt per spec).
- Existing tests calling `sheets.write(...)`: add `expected=None` for creations, or a fresh `_snapshot(...)` for updates.

Route model additions:

```python
class SheetBody(BaseModel):
    sheet_type: str
    fields: dict | None = None
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetCreationBody(BaseModel):
    sheet_type: str
    spends: dict[str, dict[str, int]] = {}
    expected: dict | None = None
```

- [ ] **Step 4: Write + run the route tests**

Append to `backend/tests/test_routes.py` (follow the file's existing client fixture):

```python
def test_sheet_put_cas(client, module_campaign):
    cid = module_campaign  # reuse/adapt this file's Phase 3 sheet-route fixture
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "adventurer", "fields": None, "expected": None})
    assert r.status_code == 200
    sheet = client.get(f"/api/campaigns/{cid}/sheets/characters/mara").json()["sheet"]
    snap = {"sheet_type": sheet["sheet_type"], "fields": sheet["fields"], "gen": sheet["gen"]}
    # stale creation assertion → 409
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "adventurer", "fields": None, "expected": None})
    assert r.status_code == 409
    # matching snapshot → 200; reusing it afterwards → 409
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "adventurer", "fields": sheet["fields"], "expected": snap})
    assert r.status_code == 200
    r = client.put(f"/api/campaigns/{cid}/sheets/characters/mara",
                   json={"sheet_type": "adventurer", "fields": sheet["fields"], "expected": snap})
    assert r.status_code == 409


def test_instantiate_still_creates_sheeted_content(client, module_campaign):
    # regression for the expected=None server-side call + rollback path:
    # instantiate a piece of module content that carries a sheet_type and
    # assert both entity and sheet exist (reuse the Phase 7 instantiate test's
    # fixture content id).
    ...  # adapt the existing Phase 7 instantiate test body; assertions unchanged
```

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "sheet_put_cas or instantiate"`
Expected: PASS.

- [ ] **Step 5: Full backend run, then commit**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` — expect PASS (this task touches many call sites; fix any missed `expected=` call sites the run surfaces).

```bash
git add backend/src/grimoire/store/sheets.py backend/src/grimoire/routes.py backend/tests
git commit -m "feat(sheets): mandatory whole-sheet CAS with gen; SheetConflict -> 409"
```

---

### Task 4: Delete CAS (`expected_gen`)

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py` (`delete`), `backend/src/grimoire/routes.py` (DELETE route)
- Test: `backend/tests/test_sheets_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `sheets.delete(cid, kind, eid, *, expected_gen: str | None) -> bool` — stored gen ≠ `expected_gen` → `SheetConflict`; missing file → `False`; legacy `gen: null` matches `expected_gen=None`. Route: `DELETE /api/campaigns/{cid}/sheets/{kind}/{eid}?gen=<gen>` (absent query param ⇒ `None`), 409 on conflict.
- Consumes: Tasks 1–3.

- [ ] **Step 1: Failing tests**

```python
def test_delete_cas(cid):
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    g = sheets.read(cid, "characters", "mara")["gen"]
    with pytest.raises(sheets.SheetConflict):
        sheets.delete(cid, "characters", "mara", expected_gen="stale" + g[:28])
    assert sheets.read(cid, "characters", "mara") is not None
    assert sheets.delete(cid, "characters", "mara", expected_gen=g) is True
    assert sheets.delete(cid, "characters", "mara", expected_gen=g) is False  # nothing left
```

Route test: create via PUT, DELETE without `gen` → 409, DELETE with the read gen → `{"ok": true}`.

- [ ] **Step 2: Run to verify failure** — `TypeError: delete() got an unexpected keyword argument`.

- [ ] **Step 3: Implement**

```python
def delete(cid: str, kind: str, eid: str, *, expected_gen: str | None) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid):
        return False
    with lock_for(cid):
        p = _campaign_path(cid, kind, eid)
        stored = _stored_snapshot(p)
        if stored is None:
            return False
        if stored["gen"] != expected_gen:
            raise SheetConflict("the sheet changed since it was loaded")
        p.unlink()
        return True
```

Route:

```python
@router.delete("/campaigns/{cid}/sheets/{kind}/{eid}")
def delete_campaign_sheet(cid: str, kind: str, eid: str, gen: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return {"ok": store.sheets.delete(cid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
```

Update any existing test/store callers of `delete` (grep `sheets.delete(`).

- [ ] **Step 4: Run** `... -m pytest backend -q` — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(sheets): delete requires expected_gen CAS"`

---

### Task 5: `sheets.set_field` — the per-field strict-CAS apply primitive

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py`
- Test: `backend/tests/test_sheets_store.py`

**Interfaces:**
- Produces:
  - `sheets.canonical_field_value(fdef: dict, value, live) -> int | list | dict` — raises `SheetError` on shape mismatch; resources → `{"current": int, "max": <live max>}` (proposed `max` ignored), tracks → int, lists → list.
  - `sheets.set_field(cid, kind, eid, field_key, value, expect) -> None` — public, takes `lock_for(cid)`, delegates to `_set_field_locked`.
  - `sheets._set_field_locked(mid, cid, kind, eid, field_key, value, expect) -> None` — caller must hold the lock; the module id is resolved **once by the caller** and passed in (rebind-consistency; see Task 8).
  - `SheetConflict` message for the equal-value case contains `"already applied or independently changed"`.
- Consumes: Tasks 1–2; `modules.assembled_fields`, `modules.validate_sheet_values`, `expressions` untouched.

- [ ] **Step 1: Failing tests** (representative set — implement all):

```python
def _setup_sheet(cid):  # adventurer with a resource 'hp', track/list per pack
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    return sheets.read(cid, "characters", "mara")


def test_set_field_resource_happy_path(cid):
    s = _setup_sheet(cid)
    live = s["fields"]["hp"]                      # {"current": C, "max": M}
    sheets.set_field(cid, "characters", "mara", "hp",
                     {"current": live["current"] - 2}, expect=live)
    got = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    assert got == {"current": live["current"] - 2, "max": live["max"]}


def test_set_field_max_tamper_ignored(cid):
    s = _setup_sheet(cid)
    live = s["fields"]["hp"]
    sheets.set_field(cid, "characters", "mara", "hp",
                     {"current": 1, "max": 999}, expect=live)
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"]["max"] == live["max"]


def test_set_field_static_field_rejected_at_write_boundary(cid):
    s = _setup_sheet(cid)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "characters", "mara", "athletics",  # number: static
                         s["fields"].get("athletics", 0) + 1,
                         expect=s["fields"].get("athletics", 0))


def test_set_field_unknown_field_rejected(cid):
    _setup_sheet(cid)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "characters", "mara", "nonesuch", 1, expect=0)


def test_set_field_conflict_on_stale_expect(cid):
    s = _setup_sheet(cid)
    live = s["fields"]["hp"]
    sheets.set_field(cid, "characters", "mara", "hp",
                     {"current": live["current"] - 1}, expect=live)
    with pytest.raises(sheets.SheetConflict):
        sheets.set_field(cid, "characters", "mara", "hp",
                         {"current": live["current"] - 2}, expect=live)


def test_set_field_conflict_even_when_live_equals_value(cid):
    """Duplicate save / independent same-value mutation must be REPORTED."""
    s = _setup_sheet(cid)
    live = s["fields"]["hp"]
    target = {"current": live["current"] - 2}
    sheets.set_field(cid, "characters", "mara", "hp", target, expect=live)
    with pytest.raises(sheets.SheetConflict) as ei:
        sheets.set_field(cid, "characters", "mara", "hp", target, expect=live)
    assert "already applied or independently changed" in str(ei.value)


def test_set_field_single_field_isolation(cid):
    """An unrelated field changed between materialize and apply survives."""
    s = _setup_sheet(cid)
    live_hp = s["fields"]["hp"]
    snap = {"sheet_type": s["sheet_type"], "fields": s["fields"], "gen": s["gen"]}
    sheets.write(cid, "characters", "mara", s["sheet_type"],
                 {**s["fields"], "athletics": 4}, expected=snap)
    sheets.set_field(cid, "characters", "mara", "hp",
                     {"current": live_hp["current"] - 1}, expect=live_hp)
    got = sheets.read(cid, "characters", "mara")["fields"]
    assert got["athletics"] == 4 and got["hp"]["current"] == live_hp["current"] - 1


def test_set_field_race_vs_advance(cid_with_advancement):
    """Threaded: both complete under the lock, neither write lost."""
    import threading
    cid, kind, eid = cid_with_advancement
    s = sheets.read(cid, kind, eid)
    live_pool = s["fields"]["xp"]  # the advancement pool resource
    results = []
    t1 = threading.Thread(target=lambda: results.append(
        sheets.advance(cid, kind, eid, "athletics")))
    t2 = threading.Thread(target=lambda: _try(results, lambda: sheets.set_field(
        cid, kind, eid, "hp", {"current": 1}, expect=s["fields"]["hp"])))
    t1.start(); t2.start(); t1.join(); t2.join()
    got = sheets.read(cid, kind, eid)["fields"]
    assert got["hp"]["current"] == 1            # set_field landed
    assert got["athletics"] == s["fields"]["athletics"] + 1  # advance landed
```

(`_try(results, fn)` = tiny helper appending an exception instead of raising; the advancement-pool field names must match the fixture pack — adapt.)

- [ ] **Step 2: Run to verify failure** — `AttributeError: ... no attribute 'set_field'`.

- [ ] **Step 3: Implement** in `sheets.py`:

```python
_MUTABLE_TYPES = ("resource", "track", "list")


def canonical_field_value(fdef: dict, value, live):
    """Canonical form of a proposed mutable-field value. Resources adopt the
    LIVE max (absorb never changes max); shape mismatches raise SheetError."""
    t = fdef.get("type")
    if t == "resource":
        cur = value.get("current") if isinstance(value, dict) else value
        if not isinstance(cur, int) or isinstance(cur, bool):
            raise SheetError(f"{fdef.get('key')!r}: resource value needs an integer 'current'")
        live_max = live.get("max") if isinstance(live, dict) else None
        if not isinstance(live_max, int) or isinstance(live_max, bool):
            live_max = _int_or(fdef.get("max"), 0)
        return {"current": cur, "max": live_max}
    if t == "track":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SheetError(f"{fdef.get('key')!r}: expected an integer")
        return value
    if t == "list":
        if not isinstance(value, list):
            raise SheetError(f"{fdef.get('key')!r}: expected a list")
        return value
    raise SheetError(f"{fdef.get('key')!r} is not a mutable field")


def _set_field_locked(mid: str, cid: str, kind: str, eid: str,
                      field_key: str, value, expect) -> None:
    """Body of set_field; caller holds lock_for(cid) and resolved mid once."""
    path = _campaign_path(cid, kind, eid)
    stored = _stored_snapshot(path)
    if stored is None:
        raise SheetError("no sheet exists for this entity")
    sheets_def = modules.load_pack(mid)["sheets"]
    st = sheets_def.get("sheet_types", {}).get(stored["sheet_type"]) \
        if isinstance(stored["sheet_type"], str) else None
    if not isinstance(st, dict):
        raise SheetError("sheet has no valid sheet type")
    fdefs = {f["key"]: f for f in modules.assembled_fields(sheets_def, stored["sheet_type"])
             if isinstance(f, dict) and isinstance(f.get("key"), str)}
    fdef = fdefs.get(field_key)
    if fdef is None or fdef.get("type") not in _MUTABLE_TYPES:
        raise SheetError(f"{field_key!r} is not a mutable field of this sheet")
    merged = {**default_fields(sheets_def, stored["sheet_type"]), **stored["fields"]}
    live = merged.get(field_key)
    new = canonical_field_value(fdef, value, live)
    want = canonical_field_value(fdef, expect, live) if expect is not None else None
    if live != want:
        raise SheetConflict(
            f"{field_key!r} is {live!r}, expected {want!r} — "
            "already applied or independently changed")
    new_fields = {**stored["fields"], field_key: new}
    errs = modules.validate_sheet_values(sheets_def, stored["sheet_type"], new_fields)
    if errs:
        raise SheetError("; ".join(errs))
    _atomic_write_json(path, {"sheet_type": stored["sheet_type"],
                              "fields": new_fields, "gen": stored["gen"]})


def set_field(cid: str, kind: str, eid: str, field_key: str, value, expect) -> None:
    with lock_for(cid):
        mid = modules.resolve(cid)      # inside the lock (rebind serialization)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        _set_field_locked(mid, cid, kind, eid, field_key, value, expect)
```

Note the canonical comparison: `expect` is canonicalized against the same live
field, so a `{"current": n}`-only expect compares correctly against a stored
`{"current", "max"}` — the spec's "no place where a current-only value meets a
current+max value".

- [ ] **Step 4: Run** the new tests + full sheet-store file — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(sheets): set_field per-field strict CAS with write-boundary mutability"`

---

### Task 6: Scene-start baselines (`store/audit.py` part 1) + hooks

**Files:**
- Create: `backend/src/grimoire/store/audit.py`
- Modify: `backend/src/grimoire/store/scenes.py` (`create_scene`), `backend/src/grimoire/store/scene_refs.py`, `backend/src/grimoire/routes.py` (`put_campaign_module`, `put_world_module`)
- Test: Create `backend/tests/test_audit_store.py`; append to `backend/tests/test_routes.py`

**Interfaces:**
- Produces (in `audit.py`):
  - `capture_baseline(cid: str, sid: str) -> None` — no-op when `modules.resolve(cid)` is None; never raises (any failure is swallowed — scene creation must not break).
  - `read_baselines(cid) -> dict` (whole file, never-raise), `baseline_field(cid, sid, kind, eid, field_key)` → baseline value or `None`.
  - `baseline_entry_valid(cid, sid, kind, eid, mid, sheet) -> bool` — the shared validity predicate (module id, schema stamp, entity present, `sheet_type` + `gen` match the passed live sheet dict).
  - `clear_baselines(cid) -> None`, `repoint_scenes(cid, mapping) -> None`.
  - `schema_stamp(mid) -> dict` → `{"hash": sha256hex, "mtime": int}` over `load_pack(mid)["sheets"]` (canonical dump) and `pack_root(mid)[0]/"sheets.json"` `st_mtime_ns` (0 when the file is absent).
- Consumes: `sheets.lock_for`, `sheets.list_refs`, `campaigns.campaign_root`, `modules.resolve/load_pack/pack_root`, `scenes.create_scene`, `scene_refs.repoint`.

- [ ] **Step 1: Failing tests** (`backend/tests/test_audit_store.py`; build a module-bound campaign + sheeted character the same way `test_sheets_store.py` does):

```python
def test_capture_and_read(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")          # hook fires here
    data = audit.read_baselines(cid)
    assert sid in data and "characters--mara" in data[sid]["sheets"]
    entry = data[sid]["sheets"]["characters--mara"]
    assert entry["sheet_type"] and entry["gen"] and isinstance(entry["fields"], dict)
    assert data[sid]["module"] and data[sid]["schema"]["hash"]


def test_capture_noop_without_module(plain_campaign):
    sid = scenes.create_scene(plain_campaign, "Landing")
    assert audit.read_baselines(plain_campaign) == {}


def test_baseline_field_validity_matrix(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is not None
    assert audit.baseline_field(cid, "no-such-scene", "characters", "mara", "hp") is None
    assert audit.baseline_field(cid, sid, "characters", "nobody", "hp") is None
    assert audit.baseline_field(cid, sid, "characters", "mara", "nonesuch") is None
    # gen mismatch: delete + recreate -> report-only
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is None


def test_baseline_invalid_after_pack_mtime_change(cid_with_sheet, user_pack_path):
    """A->B->A content reversion: hash restored but mtime moved -> invalid."""
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    p = user_pack_path / "sheets.json"       # the campaign's module lives in the
    original = p.read_text(encoding="utf-8")  # user library (GRIMOIRE_HOME/modules)
    p.write_text(original + " ", encoding="utf-8")   # B
    p.write_text(original, encoding="utf-8")          # back to A, mtime moved
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is None


def test_clear_and_repoint(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    audit.repoint_scenes(cid, {sid: "renamed"})
    assert "renamed" in audit.read_baselines(cid)
    audit.clear_baselines(cid)
    assert audit.read_baselines(cid) == {}


def test_concurrent_captures_both_land(cid_with_sheet):
    import threading
    cid = cid_with_sheet
    s1 = scenes.create_scene(cid, "One")   # capture already ran inside; to race
    s2 = scenes.create_scene(cid, "Two")   # captures directly:
    audit.clear_baselines(cid)
    t1 = threading.Thread(target=lambda: audit.capture_baseline(cid, s1))
    t2 = threading.Thread(target=lambda: audit.capture_baseline(cid, s2))
    t1.start(); t2.start(); t1.join(); t2.join()
    data = audit.read_baselines(cid)
    assert s1 in data and s2 in data
```

Route tests (append to `test_routes.py`): after creating a scene on a module-bound campaign, `PUT /api/campaigns/{cid}/module` with a different (valid) module or `"none"` empties `audit.read_baselines(cid)`; `PUT /api/worlds/{wid}/module` empties baselines of that world's non-overridden campaigns and leaves an overridden campaign's baselines intact.

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError`/`ImportError: cannot import name 'audit'`.

- [ ] **Step 3: Implement** `backend/src/grimoire/store/audit.py` (part 1 — baselines only; the LLM half arrives in Task 7):

```python
"""Narrated-event validation (mechanics Phase 5, roadmap #826).

Part 1: scene-start sheet baselines at <campaign>/sheet_baselines.json --
{"<sid>": {"module", "schema": {"hash","mtime"}, "sheets": {"kind--eid":
{"sheet_type","gen","fields"}}}}. Validity = module id + schema stamp +
per-sheet gen + type; no cross-store invalidation hooks (gen self-invalidates).
Lock ordering: sheet lock -> baseline lock, never reversed.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from . import campaigns, modules, sheets

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(cid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.Lock())


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "sheet_baselines.json"


def read_baselines(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    p = _path(cid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def schema_stamp(mid: str) -> dict:
    """Content hash + sheets.json mtime: an in-place pack edit changes the
    hash; an A->B->A reversion restores the hash but not the mtime."""
    sheets_def = modules.load_pack(mid)["sheets"]
    digest = hashlib.sha256(
        json.dumps(sheets_def, sort_keys=True).encode("utf-8")).hexdigest()
    try:
        mtime = (modules.pack_root(mid)[0] / "sheets.json").stat().st_mtime_ns
    except OSError:
        mtime = 0
    return {"hash": digest, "mtime": mtime}


def capture_baseline(cid: str, sid: str) -> None:
    """Snapshot every campaign sheet at scene creation. Never raises -- a
    capture failure must not fail scene creation."""
    try:
        mid = modules.resolve(cid)
        if mid is None:
            return
        with sheets.lock_for(cid):          # consistent multi-file snapshot
            snap: dict = {}
            croot = campaigns.campaign_root(cid)
            for kind, eid in sheets.list_refs(cid):
                try:
                    raw = json.loads((croot / "sheets" / f"{kind}--{eid}.json")
                                     .read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(raw, dict):
                    snap[f"{kind}--{eid}"] = {
                        "sheet_type": raw.get("sheet_type"), "gen": raw.get("gen"),
                        "fields": raw.get("fields") if isinstance(raw.get("fields"), dict) else {}}
            entry = {"module": mid, "schema": schema_stamp(mid), "sheets": snap}
            with _lock(cid):                # sheet lock -> baseline lock
                data = read_baselines(cid)
                data[sid] = entry
                _write(cid, data)
    except Exception:  # noqa: BLE001 -- never fail the caller
        return


def baseline_entry_valid(cid: str, sid: str, kind: str, eid: str,
                         mid: str, sheet: dict) -> bool:
    """Shared validity predicate: scene entry exists, module + schema stamp
    match, entity entry exists, and its sheet_type AND gen equal the live
    sheet's. `sheet` is a sheets.read() result (must be non-None)."""
    scene = read_baselines(cid).get(sid)
    if not isinstance(scene, dict) or scene.get("module") != mid:
        return False
    if scene.get("schema") != schema_stamp(mid):
        return False
    entry = scene.get("sheets", {}).get(f"{kind}--{eid}")
    if not isinstance(entry, dict):
        return False
    return (entry.get("sheet_type") == sheet["sheet_type"]
            and entry.get("gen") == sheet["gen"])


def baseline_field(cid: str, sid: str, kind: str, eid: str, field_key: str):
    """The scene-start value for a field, or None when no valid baseline
    covers it (report-only)."""
    mid = modules.resolve(cid)
    if mid is None:
        return None
    sheet = sheets.read(cid, kind, eid)
    if sheet is None or sheet["errors"]:
        return None
    if not baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
        return None
    entry = read_baselines(cid)[sid]["sheets"][f"{kind}--{eid}"]
    fields = {**sheets.default_fields(modules.load_pack(mid)["sheets"],
                                      entry["sheet_type"]), **entry["fields"]}
    return fields.get(field_key)


def clear_baselines(cid: str) -> None:
    with _lock(cid):
        _write(cid, {})


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    with _lock(cid):
        data = read_baselines(cid)
        hit = False
        for old, new in mapping.items():
            if old in data:
                data[new] = data.pop(old)
                hit = True
        if hit:
            _write(cid, data)
```

Hooks:

1. `scenes.py::create_scene` — after the scene file is written, before `return sid`:

```python
    from . import audit  # lazy: audit imports campaigns/sheets, scenes must not cycle
    audit.capture_baseline(cid, sid)
    return sid
```

2. `scene_refs.py` — add `audit` to the docstring ("six stores") and the tuple:

```python
from . import appearances, audit, changes, chronicle, plot, rolls
    for mod in (appearances, audit, changes, chronicle, plot, rolls):
```

3. `routes.py::put_campaign_module` — publish under the campaign's sheet lock:

```python
    try:
        with store.sheets.lock_for(cid):
            store.modules.set_campaign_module(cid, body.module.strip())
            store.audit.clear_baselines(cid)
    except store.campaigns.CampaignNotFound: ...  # unchanged handlers
```

4. `routes.py::put_world_module` — all affected campaign locks, sorted, then publish:

```python
    import contextlib
    affected = []
    for c in store.campaigns.list_campaigns():
        meta = c if isinstance(c, dict) else {}
        if meta.get("world") != wid:
            continue
        setting = (meta.get("module") or "").strip()
        if not setting:                      # no per-campaign override
            affected.append(meta["id"])
    with contextlib.ExitStack() as stack:
        for c in sorted(affected):           # sole multi-lock holder; sorted order
            stack.enter_context(store.sheets.lock_for(c))
        store.modules.set_world_module(wid, body.module.strip())
        for c in affected:
            store.audit.clear_baselines(c)
```

(Adapt the `list_campaigns` iteration to its actual return shape — check `store/campaigns.py`; the campaign meta must expose `world` and `module`. Keep the existing exception handlers around the whole block.)

- [ ] **Step 4: Run** `... -m pytest backend/tests/test_audit_store.py backend/tests/test_routes.py -q` — PASS; then the full backend suite.
- [ ] **Step 5: Commit** — `git commit -m "feat(audit): scene-start sheet baselines with gen/schema validity; rebind clears"`

---

### Task 7: Audit prompt / parse / materialize (`audit.py` part 2 + templates)

**Files:**
- Modify: `backend/src/grimoire/store/audit.py`
- Create: `templates/audit/system.j2`, `templates/audit/user.j2`
- Test: `backend/tests/test_audit_store.py`

**Interfaces:**
- Produces:
  - `class AuditParseError(Exception)` (carries a reason string).
  - `sheet_scope(cid, sid) -> list[tuple[str, str, str]]` — `(kind, eid, name)` for present cast + current location (unsheeted entries included; callers filter).
  - `sheet_blocks(cid, sid) -> tuple[list[str], list[dict]]` — rendered blocks + `[{"id","reason"}]` invalid-sheet exclusions.
  - `roll_lines(cid, sid) -> list[str]`.
  - `build_prompt(transcript, blocks, rolls) -> list[dict]` (system + user message, jinja via `prompts.render`).
  - `parse_output(text) -> dict` → `{"warnings": [str], "sheet_deltas": [dict], "dropped": [dict]}`; raises `AuditParseError` on structural failure.
  - `render_value(fdef, value) -> str` — `essence 6/10` for resources, `str(int)`, lists newline-joined.
  - `materialize(cid, sid, parsed) -> tuple[list[dict], list[dict]]` — `(edits, dropped)`; StagedEdit shape exactly:
    `{"id": "sheet:{kind}:{eid}:{field}", "kind": "sheet", "target": {"kind","id"}, "label", "field", "before", "after", "authored": False, "payload": {"field", "value", "expect", "note"}}`.
- Consumes: Task 5 `sheets.canonical_field_value`; Task 6 `baseline_field`; `appearances.scene_cast`, `scenes.get_location_history`, `overlay.read_entity`, `rolls.read`, `prompts.render`, `modules.assembled_fields/validate_sheet_values/load_pack`.

- [ ] **Step 1: Failing tests** (representative — implement the full matrix from the spec's Testing section):

```python
def test_parse_output_fail_closed():
    for bad in ("no json here", "{}", '{"warnings": null, "sheet_deltas": []}',
                '{"warnings": [], "sheet_deltas": {}}', '{"warnings": []}'):
        with pytest.raises(audit.AuditParseError):
            audit.parse_output(bad)


def test_parse_output_item_tolerance():
    out = audit.parse_output(
        '{"warnings": ["w1", 42], "sheet_deltas": '
        '[{"id": "characters:mara", "field": "hp", "value": {"current": 3}}, "junk"]}')
    assert out["warnings"] == ["w1"]
    assert len(out["sheet_deltas"]) == 1
    assert len(out["dropped"]) == 2          # the 42 and the "junk"


def test_materialize_happy_resource(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast       # fixture: scene + present mara + baseline
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": live["current"] - 2}, "note": "took a hit"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    e = edits[0]
    assert e["kind"] == "sheet" and e["id"] == "sheet:characters:mara:hp"
    assert e["payload"]["expect"] == live
    assert e["payload"]["value"] == {"current": live["current"] - 2, "max": live["max"]}
    assert e["before"].startswith("hp ") and e["after"].startswith("hp ")


def test_materialize_gates(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    cases = [
        ({"id": "characters:nobody", "field": "hp", "value": {"current": 1}}, "unknown"),
        ({"id": "characters:mara", "field": "athletics", "value": 3}, "static"),
        ({"id": "characters:mara", "field": "nonesuch", "value": 1}, "unknown field"),
        ({"id": "characters:mara", "field": "hp", "value": {"current": "lots"}}, "bad value"),
    ]
    for delta, _why in cases:
        edits, dropped = audit.materialize(
            cid, sid, {"warnings": [], "dropped": [], "sheet_deltas": [dict(delta, note="")]})
        assert edits == [] and len(dropped) == 1


def test_materialize_noop_dropped_silently(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"]}, "note": ""}]})
    assert edits == [] and dropped == []      # agreement, not loss


def test_materialize_suppresses_baseline_less_entity(scene_with_sheeted_cast):
    """THE regression: no valid baseline -> zero StagedEdits, whatever the model says."""
    cid, sid = scene_with_sheeted_cast
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)  # new gen
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 1}, "note": ""}]})
    assert edits == [] and dropped and "baseline" in dropped[0]["reason"]


def test_sheet_blocks_marks_and_excludes(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    blocks, excluded = audit.sheet_blocks(cid, sid)
    assert any("characters:mara" in b for b in blocks)
    assert any("start" in b or "->" in b for b in blocks)   # start -> current markers
    # FULL blocks: text fields present and static, never delta-eligible
    # (pick a text field the fixture pack defines; adapt the key)
    mara_block = next(b for b in blocks if "characters:mara" in b)
    assert "[static]" in mara_block
    assert excluded == []
    # corrupt the sheet -> excluded, not silently missing
    p = sheets._campaign_path(cid, "characters", "mara")
    p.write_text("{not json", encoding="utf-8")
    blocks, excluded = audit.sheet_blocks(cid, sid)
    assert all("characters:mara" not in b for b in blocks)
    assert excluded and excluded[0]["id"] == "characters:mara"
```

- [ ] **Step 2: Run to verify failure** — missing attributes.

- [ ] **Step 3: Implement.** Add to `audit.py` (imports grow: `from .. import prompts`, `from . import appearances, entities, overlay, rolls, scenes`):

```python
class AuditParseError(Exception):
    """The audit reply violated the output schema (fail closed, not clean)."""


def sheet_scope(cid: str, sid: str) -> list[tuple[str, str, str]]:
    """(kind, eid, name) for present cast + the current location -- the same
    scope Phase 4's mechanics_sheets context section uses (context._mechanics)."""
    out = [(a["kind"], a["id"], a.get("name", a["id"]))
           for a in appearances.scene_cast(cid, sid)]
    history = scenes.get_location_history(cid, sid)
    if history:
        loc = history[-1]
        try:
            name = overlay.read_entity(cid, "locations", loc)["meta"].get("name", loc)
            out.append(("locations", loc, name))
        except entities.EntityNotFound:
            pass
    return out


def _field_label(fdef: dict) -> str:
    return fdef.get("label") or fdef.get("key", "")


def render_value(fdef: dict, value) -> str:
    key = fdef.get("key", "")
    if fdef.get("type") == "resource" and isinstance(value, dict):
        return f"{key} {value.get('current', 0)}/{value.get('max', 0)}"
    if fdef.get("type") == "list" and isinstance(value, list):
        return f"{key}:\n" + "\n".join(f"- {v}" for v in value) if value else f"{key}: (empty)"
    return f"{key} {value}"


def sheet_blocks(cid: str, sid: str) -> tuple[list[str], list[dict]]:
    mid = modules.resolve(cid)
    if mid is None:
        return [], []
    sheets_def = modules.load_pack(mid)["sheets"]
    blocks, excluded = [], []
    for kind, eid, name in sheet_scope(cid, sid):
        sheet = sheets.read(cid, kind, eid)
        if sheet is None:
            continue                                   # unsheeted: not in scope
        ref = f"{kind}:{eid}"
        if sheet["errors"]:
            excluded.append({"id": ref,
                             "reason": "sheet invalid: " + "; ".join(sheet["errors"])})
            continue
        type_id = sheet["sheet_type"]
        merged = {**sheets.default_fields(sheets_def, type_id), **sheet["fields"]}
        lines = [f"{ref} — {type_id} ({name})"]
        has_baseline = False
        for f in modules.assembled_fields(sheets_def, type_id):
            key = f.get("key")
            if not isinstance(key, str) or key not in merged:
                continue
            if f.get("type") in sheets._MUTABLE_TYPES:
                start = baseline_field(cid, sid, kind, eid, key)
                if start is None:
                    lines.append(f"  {render_value(f, merged[key])}  "
                                 "[mutable — no scene baseline, report only]")
                else:
                    has_baseline = True
                    lines.append(f"  start {render_value(f, start)} -> now "
                                 f"{render_value(f, merged[key])}  [mutable]")
            else:
                # spec: FULL blocks — text fields included, marked static, so
                # contradictions involving text-valued mechanics stay visible
                lines.append(f"  {render_value(f, merged[key])}  [static]")
        del has_baseline  # marker only lives in the lines
        blocks.append("\n".join(lines))
    return blocks, excluded


def roll_lines(cid: str, sid: str) -> list[str]:
    out = []
    for entry in rolls.read(cid):
        if entry.get("scene") != sid:
            continue
        r = entry.get("result", {})
        tier = r.get("tier") or {}
        bits = [entry.get("label") or r.get("notation", ""), str(r.get("notation", ""))]
        if "successes" in r:
            bits.append(f"{r['successes']} successes")
        elif "total" in r:
            bits.append(f"total {r['total']}")
        if isinstance(tier, dict) and tier.get("label"):
            bits.append(tier["label"])
        out.append("- " + " · ".join(b for b in bits if b))
    return out


def build_prompt(transcript: str, blocks: list[str], roll_lines_: list[str]) -> list[dict]:
    return [{"role": "system", "content": prompts.render("audit/system.j2")},
            {"role": "user", "content": prompts.render(
                "audit/user.j2", sheet_blocks=blocks, roll_lines=roll_lines_,
                transcript=transcript)}]


def parse_output(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise AuditParseError("no JSON object in the audit reply")
    if not isinstance(obj, dict):
        raise AuditParseError("audit reply is not a JSON object")
    if not isinstance(obj.get("warnings"), list) or not isinstance(obj.get("sheet_deltas"), list):
        raise AuditParseError(
            "audit reply must carry 'warnings' and 'sheet_deltas' arrays")
    warnings, deltas, dropped = [], [], []
    for w in obj["warnings"]:
        if isinstance(w, str) and w.strip():
            warnings.append(w.strip())
        else:
            dropped.append({"id": "", "reason": f"malformed warning: {w!r}"})
    for d in obj["sheet_deltas"]:
        if isinstance(d, dict) and isinstance(d.get("id"), str) and isinstance(d.get("field"), str):
            deltas.append({"id": d["id"].strip(), "field": d["field"].strip(),
                           "value": d.get("value"),
                           "note": str(d.get("note") or "").strip()})
        else:
            dropped.append({"id": "", "reason": f"malformed delta: {d!r}"})
    return {"warnings": warnings, "sheet_deltas": deltas, "dropped": dropped}


def materialize(cid: str, sid: str, parsed: dict) -> tuple[list[dict], list[dict]]:
    """Deterministic gate over parsed sheet_deltas -> (StagedEdits, dropped).
    Mirrored inside the apply lock by apply_delta; set_field is the boundary."""
    mid = modules.resolve(cid)
    edits: list[dict] = []
    dropped: list[dict] = list(parsed.get("dropped", []))
    if mid is None:
        return edits, dropped
    sheets_def = modules.load_pack(mid)["sheets"]
    scope = {(k, e): name for k, e, name in sheet_scope(cid, sid)}
    for d in parsed.get("sheet_deltas", []):
        ref, field_key = d["id"], d["field"]
        kind, sep, eid = ref.partition(":")
        drop = lambda why: dropped.append({"id": ref, "field": field_key, "reason": why})
        if not sep or (kind, eid) not in scope:
            drop("entity not in this scene's sheet scope"); continue
        sheet = sheets.read(cid, kind, eid)
        if sheet is None or sheet["errors"]:
            drop("entity has no readable sheet"); continue
        if baseline_field(cid, sid, kind, eid, field_key) is None and \
                not baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
            drop("no valid scene baseline for this entity"); continue
        fdefs = {f["key"]: f for f in modules.assembled_fields(sheets_def, sheet["sheet_type"])
                 if isinstance(f, dict) and isinstance(f.get("key"), str)}
        fdef = fdefs.get(field_key)
        if fdef is None or fdef.get("type") not in sheets._MUTABLE_TYPES:
            drop("not a mutable field of this sheet"); continue
        merged = {**sheets.default_fields(sheets_def, sheet["sheet_type"]), **sheet["fields"]}
        live = merged.get(field_key)
        try:
            value = sheets.canonical_field_value(fdef, d["value"], live)
        except sheets.SheetError as e:
            drop(str(e)); continue
        errs = modules.validate_sheet_values(
            sheets_def, sheet["sheet_type"], {**sheet["fields"], field_key: value})
        if errs:
            drop("; ".join(errs)); continue
        expect = sheets.canonical_field_value(fdef, live, live)
        if value == expect:
            continue                                     # benign no-op: agreement
        name = scope[(kind, eid)]
        edits.append({"id": f"sheet:{kind}:{eid}:{field_key}", "kind": "sheet",
                      "target": {"kind": kind, "id": eid},
                      "label": f"{name} — {_field_label(fdef)} (sheet)",
                      "field": field_key,
                      "before": render_value(fdef, expect),
                      "after": render_value(fdef, value),
                      "authored": False,
                      "payload": {"field": field_key, "value": value,
                                  "expect": expect, "note": d.get("note", "")}})
    return edits, dropped
```

Note on the baseline gate: `baseline_field` returning `None` for a *specific
field* that legitimately isn't in the baseline (e.g. added by schema change —
impossible when the stamp matches, so in practice it means "invalid baseline")
is folded with `baseline_entry_valid` as shown; the pairing keeps the drop
reason honest.

Templates — `templates/audit/system.j2`:

```
You are auditing a completed role-play scene against its game-mechanics records. You receive the scene transcript, the mechanical roll log for the scene, and a compact sheet block for each present character and the scene's location. Each sheet line marked [mutable] shows the field at scene start and now ("start X -> now Y"); lines marked [static] never change through play; lines marked "report only" have no reliable scene-start value. Reply with ONLY a JSON object, no prose around it, with keys: "warnings" (list of strings — narration that contradicts the mechanics: a claimed success or hit with no roll-log entry, a narrated resource spend or damage that the sheets do not reflect, a current value that matches neither the scene-start value nor anything narrated; [] if none) and "sheet_deltas" (list of {"id","field","value","note"} — one entry per mutable field whose correct end-of-scene value differs from its "now" value; "id" is the exact kind:id header of the sheet block; "value" is the complete new value ({"current": n} for resources, an integer for tracks, the full new list for lists); "note" is one sentence of justification tied to the transcript). Rules: propose a delta only when the narration clearly established the change and the "now" value does not already reflect it — an already-applied change gets NO delta; a partially-applied change gets the corrected end value; a "report only" field gets warnings at most, never a delta. Never dispute a logged roll. Never propose changes to [static] fields. Never change a resource's max. Award experience/advancement points only when the narration or log clearly grants them.
```

`templates/audit/user.j2`:

```
{#- Vars: sheet_blocks [str], roll_lines [str], transcript str -#}
Sheets:
{% for b in sheet_blocks %}{{ b }}

{% endfor -%}
{% if roll_lines %}Roll log:
{{ roll_lines | join("\n") }}

{% endif -%}
Transcript:
{{ transcript }}
```

If `scripts/verify_templates.py` enumerates template variables, register the two new templates there (check how the existing `absorb/*.j2` are declared and mirror it).

- [ ] **Step 4: Run** `... -m pytest backend/tests/test_audit_store.py -q` — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(audit): audit prompt, fail-closed parse, materialize with dropped tracking"`

---

### Task 8: Apply — `audit.apply_delta` + the `"sheet"` branch in `absorb.apply_edits`

**Files:**
- Modify: `backend/src/grimoire/store/audit.py`, `backend/src/grimoire/store/absorb.py`, `backend/src/grimoire/routes.py` (`put_chronicle`)
- Test: `backend/tests/test_audit_store.py`, `backend/tests/test_absorb_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - `audit.apply_delta(cid, sid, edit: dict) -> None` — one critical section under `sheets.lock_for(cid)`: resolve module once, recompute scope, re-check baseline validity against the in-lock live sheet, then `sheets._set_field_locked`. Raises `sheets.SheetConflict` / `sheets.SheetError`.
  - `absorb.apply_edits(cid, edits, sid=None) -> tuple[list[str], list[dict]]` — **signature change**: returns `(applied, sheet_failures)`; `sheet_failures` items are `{"id", "reason", "kind": "conflict"|"error"}`.
  - `PUT /campaigns/{cid}/scenes/{sid}/chronicle` response gains `"sheet_failures"`.
- Consumes: Tasks 5–7.

- [ ] **Step 1: Failing tests**

```python
# test_audit_store.py
def test_apply_delta_happy_and_conflict(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": live["current"] - 2}, "note": ""}]}
    edits, _ = audit.materialize(cid, sid, parsed)
    audit.apply_delta(cid, sid, edits[0])
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"]["current"] == live["current"] - 2
    with pytest.raises(sheets.SheetConflict):      # double-apply reports
        audit.apply_delta(cid, sid, edits[0])


def test_apply_delta_rejects_out_of_scope_and_baseline_less(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    # forge an edit for a sheeted entity that is NOT in the scene (winifred
    # exists + sheeted but never appeared) with a CORRECT expect:
    live = sheets.read(cid, "characters", "winifred")["fields"]["hp"]
    forged = {"id": "sheet:characters:winifred:hp", "kind": "sheet",
              "target": {"kind": "characters", "id": "winifred"}, "field": "hp",
              "label": "x", "before": "", "after": "", "authored": False,
              "payload": {"field": "hp", "value": {"current": 1, "max": live["max"]},
                          "expect": live, "note": ""}}
    with pytest.raises(sheets.SheetError):
        audit.apply_delta(cid, sid, forged)
    assert sheets.read(cid, "characters", "winifred")["fields"]["hp"] == live


def test_apply_delta_vs_delete_recreate_race(scene_with_sheeted_cast):
    """gen re-check inside the lock: recreated sheet is untouched."""
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    fresh = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    with pytest.raises(sheets.SheetError):
        audit.apply_delta(cid, sid, edits[0])
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"] == fresh


# test_absorb_store.py
def test_apply_edits_sheet_failures_reported(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == [edits[0]["id"]] and failures == []
    applied, failures = absorb.apply_edits(cid, edits, sid)   # replay
    assert applied == [] and failures[0]["kind"] == "conflict"
    assert failures[0]["id"] == edits[0]["id"]


def test_apply_edits_sheet_not_in_changes_json(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 1}, "note": ""}]})
    absorb.apply_edits(cid, edits, sid)
    assert all(not ref.startswith("sheet") for ref in changes.read(cid))
```

- [ ] **Step 2: Run to verify failure** — attribute/arity errors.

- [ ] **Step 3: Implement.**

`audit.py`:

```python
def apply_delta(cid: str, sid: str, edit: dict) -> None:
    """One critical section: authorize (scope + baseline vs the in-lock live
    sheet) then write (set_field's body). Module resolved exactly once."""
    payload = edit.get("payload", {})
    kind, eid = edit.get("target", {}).get("kind"), edit.get("target", {}).get("id")
    field_key = payload.get("field")
    if not (isinstance(kind, str) and isinstance(eid, str) and isinstance(field_key, str)):
        raise sheets.SheetError("malformed sheet edit")
    with sheets.lock_for(cid):
        mid = modules.resolve(cid)                       # once, inside the lock
        if mid is None:
            raise sheets.SheetError("no module resolved for this campaign")
        if (kind, eid) not in {(k, e) for k, e, _ in sheet_scope(cid, sid)}:
            raise sheets.SheetError("entity not in this scene's sheet scope")
        sheet = sheets.read(cid, kind, eid)
        if sheet is None or sheet["errors"]:
            raise sheets.SheetError("entity has no readable sheet")
        if not baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
            raise sheets.SheetError("no valid scene baseline for this entity")
        sheets._set_field_locked(mid, cid, kind, eid, field_key,
                                 payload.get("value"), payload.get("expect"))
```

`absorb.py::apply_edits` — new signature and the branch. At the top:
`applied: list[str] = []` gains `sheet_failures: list[dict] = []`; inside the
loop add **before** the generic `try` (sheet edits get their own error
contract, not the best-effort skip):

```python
    for e in edits:
        if e.get("kind") == "sheet":
            if not sid:
                sheet_failures.append({"id": e.get("id", ""), "kind": "error",
                                       "reason": "sheet edits need a scene id"})
                continue
            from . import audit  # lazy: audit imports absorb-adjacent stores
            try:
                audit.apply_delta(cid, sid, e)
                applied.append(e["id"])
            except sheets.SheetConflict as exc:
                sheet_failures.append({"id": e.get("id", ""), "kind": "conflict",
                                       "reason": str(exc)})
            except sheets.SheetError as exc:
                sheet_failures.append({"id": e.get("id", ""), "kind": "error",
                                       "reason": str(exc)})
            continue
        try:
            ...  # existing body unchanged
```

(`absorb.py` must import `sheets` — add to its `from . import (...)` list.)
Return `applied, sheet_failures`. Update the two existing callers: `routes.py::put_chronicle`:

```python
    applied, sheet_failures = store.absorb.apply_edits(cid, body.edits, sid)
    return {**record, "applied": applied, "sheet_failures": sheet_failures}
```

and any tests unpacking the old single return (grep `apply_edits(` in `backend/`).

- [ ] **Step 4: Run** the three test files, then the full backend suite — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(absorb): sheet edit kind applies via in-lock re-authorized CAS; failures reported"`

---

### Task 9: Routes — audit step in absorb + standalone retry endpoint

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`post_absorb`, new `post_audit`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - Route helper `async def _run_audit(cid, sid, client, cfg) -> tuple[list[dict], dict]` → `(edits, mechanics)`; `mechanics = {"status", "reason", "warnings", "dropped"}` per the spec's Routes section (statuses: `skipped` / `ok` / `degraded` / `failed`; all-invalid scope → `failed` with **no** LLM call; `AuditParseError`/`LLMError`/any exception → `failed`, absorb intact).
  - `POST /api/campaigns/{cid}/scenes/{sid}/absorb` response gains `"mechanics"`; its `edits` include the audit's.
  - `POST /api/campaigns/{cid}/scenes/{sid}/audit` → `{"mechanics": {...}, "edits": [...]}`; 400 when no module resolves. Registered **before** the generic `{kind}` catch-alls (place it next to `post_absorb`, which already is).
- Consumes: Tasks 6–8; existing `get_llm`/`LLMError` plumbing; `FakeOpenRouterComplete` test pattern (`test_routes.py:1568`).

- [ ] **Step 1: Failing tests** (append to `test_routes.py`; the scene fixture must have a **PC-only cast** so the dossier loop makes zero calls and LLM-call counting stays exact):

```python
AUDIT_OK = '{"warnings": ["Mara claimed a hit with no roll"], "sheet_deltas": []}'

def test_absorb_runs_audit_on_module_campaign(client, module_scene):
    cid, sid = module_scene
    fake = FakeOpenRouterComplete([ABSORB_JSON, AUDIT_OK])  # 2 sequential completes
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["mechanics"]["status"] == "ok"
    assert body["mechanics"]["warnings"] == ["Mara claimed a hit with no roll"]
    assert fake.calls == 2


def test_absorb_moduleless_skips_audit(client, plain_scene):
    cid, sid = plain_scene
    fake = FakeOpenRouterComplete([ABSORB_JSON])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["mechanics"]["status"] == "skipped" and fake.calls == 1


def test_absorb_audit_schema_failure_is_failed_not_clean(client, module_scene):
    cid, sid = module_scene
    for bad in ("{}", '{"warnings": null, "sheet_deltas": null}', "utter garbage"):
        client.app.dependency_overrides[routes.get_llm] = \
            lambda b=bad: FakeOpenRouterComplete([ABSORB_JSON, b])
        body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
        assert body["one_line"]                       # prose absorb intact
        assert body["mechanics"]["status"] == "failed"
        assert body["mechanics"]["reason"]


def test_absorb_dropped_delta_degrades(client, module_scene):
    cid, sid = module_scene
    bad_delta = ('{"warnings": [], "sheet_deltas": [{"id": "characters:mara", '
                 '"field": "athletics", "value": 5, "note": "static tamper"}]}')
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([ABSORB_JSON, bad_delta])
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["mechanics"]["status"] == "degraded"
    assert body["mechanics"]["dropped"]


def test_absorb_survives_audit_pipeline_crash(client, module_scene, monkeypatch):
    """Never-fail-absorb: an exception ANYWHERE in the audit pipeline
    (here: materialize) yields mechanics failed, absorb 200 + intact prose."""
    cid, sid = module_scene
    from grimoire.store import audit as audit_mod
    monkeypatch.setattr(audit_mod, "materialize",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([ABSORB_JSON, AUDIT_OK])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["one_line"] and body["mechanics"]["status"] == "failed"
    assert "boom" in body["mechanics"]["reason"]


def test_audit_retry_endpoint(client, module_scene):
    cid, sid = module_scene
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete([AUDIT_OK])
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/audit").json()
    assert body["mechanics"]["status"] == "ok" and body["edits"] == []


def test_chronicle_put_applies_sheet_edit_and_reports_conflicts(client, module_scene_with_delta):
    cid, sid, sheet_edit = module_scene_with_delta   # a materialized sheet StagedEdit
    save = {"one_line": "x", "summary": "y", "keywords": [], "timeline_events": [],
            "edits": [sheet_edit]}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save).json()
    assert r["applied"] == [sheet_edit["id"]] and r["sheet_failures"] == []
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save).json()
    assert r["applied"] == [] and r["sheet_failures"][0]["kind"] == "conflict"
```

(`ABSORB_JSON` = a minimal valid absorb reply, reuse the constant/pattern the file's existing absorb tests use. `FakeOpenRouterComplete` may need a `calls` counter — extend the existing class in place if it lacks one.)

- [ ] **Step 2: Run to verify failure** — `KeyError: 'mechanics'` etc.

- [ ] **Step 3: Implement** in `routes.py`:

```python
async def _run_audit(cid: str, sid: str, client: LLMClient, cfg: dict):
    """(edits, mechanics) for the scene audit. Never raises; every failure is
    an explicit mechanics status (spec: audit visibility)."""
    mech = {"status": "skipped", "reason": None, "warnings": [], "dropped": []}
    excluded: list = []
    try:
        if store.modules.resolve(cid) is None:
            mech["reason"] = "no module"
            return [], mech
        # ONE failure boundary around the ENTIRE audit pipeline (spec:
        # never-fail-absorb) — sheet_blocks, read_scene, transcript,
        # roll_lines, build_prompt, complete, parse AND materialize. Any
        # exception anywhere here is a failed audit, never a 500 absorb.
        blocks, excluded = store.audit.sheet_blocks(cid, sid)
        if not blocks and not excluded:
            mech["reason"] = "no sheeted scope"
            return [], mech
        if not blocks:
            return [], {**mech, "status": "failed",
                        "reason": "all scoped sheets invalid",
                        "dropped": excluded}
        scene = store.scenes.read_scene(cid, sid)
        transcript = store.chronicle.transcript_text(scene["messages"])
        messages = store.audit.build_prompt(transcript, blocks,
                                            store.audit.roll_lines(cid, sid))
        text = await client.complete(messages, cfg)
        parsed = store.audit.parse_output(text)
        edits, dropped = store.audit.materialize(cid, sid, parsed)
    except store.audit.AuditParseError as exc:
        return [], {**mech, "status": "failed", "reason": str(exc), "dropped": excluded}
    except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
        return [], {**mech, "status": "failed", "reason": f"audit failed: {exc}",
                    "dropped": excluded}
    dropped = excluded + dropped
    status = "degraded" if dropped else "ok"
    reason = ("some sheets could not be audited" if excluded else
              "some findings could not be validated") if dropped else None
    return edits, {"status": status, "reason": reason,
                   "warnings": parsed["warnings"], "dropped": dropped}
```

In `post_absorb`, after the dossier loop and before the return:

```python
    audit_edits, mechanics = await _run_audit(cid, sid, client, cfg)
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
            **facts, "edits": edits + audit_edits, "mechanics": mechanics}
```

New endpoint (place directly under `post_absorb`):

```python
@router.post("/campaigns/{cid}/scenes/{sid}/audit")
async def post_audit(cid: str, sid: str, client: LLMClient = Depends(get_llm)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if store.modules.resolve(cid) is None:
        raise HTTPException(status_code=400, detail="no module resolved")
    edits, mechanics = await _run_audit(cid, sid, client, cfg)
    return {"mechanics": mechanics, "edits": edits}
```

- [ ] **Step 4: Run** `... -m pytest backend/tests/test_routes.py -q`, then full backend — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(routes): absorb mechanics audit step + standalone retry endpoint"`

---

### Task 10: Frontend — types, absorb panel, sheet CAS plumbing

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/routes/CampaignView.tsx`, `frontend/src/components/SheetEditor.tsx`, `frontend/src/components/CreationWizard.tsx`, `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`, `frontend/src/components/SheetEditor.test.tsx`

**Interfaces:**
- Produces (client.ts):
  - `Sheet` gains `gen: string | null`.
  - `putSheet` body gains `expected: { sheet_type: string | null; fields: Record<string, unknown>; gen: string | null } | null`.
  - `deleteSheet` gains a `gen: string | null` arg → appends `?gen=` for campaign scope.
  - `type MechanicsDrop = { id: string; field?: string; reason: string }`; `type Mechanics = { status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null; warnings: string[]; dropped: MechanicsDrop[] }`.
  - `SceneAbsorb` gains `mechanics: Mechanics`; `StagedEdit` union (or its `kind` field) accepts `"sheet"` with `payload.note`.
  - `saveChronicle` return type gains `sheet_failures: { id: string; reason: string; kind: "conflict" | "error" }[]`.
  - `retryAudit: (cid, sid) => request<{ mechanics: Mechanics; edits: StagedEdit[] }>("POST", `/api/campaigns/${cid}/scenes/${sid}/audit`)`.
- Consumes: Task 9 response shapes.

- [ ] **Step 1: Failing tests.** In `CampaignView.test.tsx`, follow the file's existing absorb-panel test pattern (mocked `api`) and add:

```tsx
it("renders mechanics warnings and a clean hint", async () => {
  // absorbScene resolves with mechanics {status:"ok", warnings:["Mara claimed a hit with no roll"], ...}
  // -> the panel shows the ⚠ list; with warnings: [] it shows "mechanics audited clean"
});

it("degraded/failed mechanics show a retry that replaces sheet rows", async () => {
  // absorbScene -> status "failed", reason "boom"; click "Retry validation";
  // api.retryAudit resolves {mechanics: ok, edits: [sheetEdit]};
  // assert the notice is gone and the sheet row appears
});

it("sheet edits render read-only with the note and survive save", async () => {
  // absorbScene returns edits: [sheetEdit {kind:"sheet", before:"hp 6/10", after:"hp 4/10",
  //   payload:{note:"took a hit"}}] -> row shows before/after text, NO textarea for it;
  // saveChronicle resolves {applied:[id], sheet_failures:[]} -> no failure notice
});

it("sheet_failures from save render a notice", async () => {
  // saveChronicle resolves {applied: [], sheet_failures:[{id, reason:"changed", kind:"conflict"}]}
  // -> "1 sheet change did not apply" notice listing the label/reason
});
```

In `SheetEditor.test.tsx`: saving sends `expected` (the loaded `{sheet_type, fields, gen}` snapshot, or `null` when creating); a 409 from `putSheet` triggers a reload (`getSheet` called again) and shows a "changed elsewhere" notice; delete passes the loaded `gen`.

Write these as real tests against the components' actual render output (mirror the style of the neighbouring tests in each file — they mock `api` with `vi.mock`).

- [ ] **Step 2: Run to verify failure** — `npx vitest run` (from `frontend/`) fails on missing UI/API surface.

- [ ] **Step 3: Implement.**

`client.ts` — add the types/functions from the Interfaces block. `deleteSheet` for campaign scope becomes:

```ts
deleteSheet: (scope: EntityScope, mid: string, kind: string, eid: string, gen: string | null) =>
  request<{ ok: boolean }>(
    "DELETE",
    scope.kind === "campaign"
      ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`
      : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`),
```

`CampaignView.tsx` — in the absorb panel (`{absorb && ...}` block):
- Above the edits list render the mechanics section:

```tsx
{absorb.mechanics.status === "ok" && absorb.mechanics.warnings.length === 0 && (
  <p className="field-hint">mechanics audited clean</p>)}
{absorb.mechanics.warnings.length > 0 && (
  <ul className="mechanics-warnings">
    {absorb.mechanics.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
  </ul>)}
{(absorb.mechanics.status === "failed" || absorb.mechanics.status === "degraded") && (
  <div className="mechanics-notice">
    <p>{absorb.mechanics.status === "failed"
        ? `Mechanics validation failed: ${absorb.mechanics.reason}`
        : "Some mechanics findings could not be validated"}</p>
    {absorb.mechanics.dropped.map((d, i) => (
      <p className="field-hint" key={i}>{d.id} {d.field ?? ""}: {d.reason}</p>))}
    <button onClick={retryAudit}>Retry validation</button>
  </div>)}
```

- `retryAudit` handler: `api.retryAudit(cid, activeId)` → replace `absorb.mechanics`, drop all `kind === "sheet"` rows from `editRows`, append the fresh ones (approved: true).
- Sheet edit rows render read-only: in the edits map, when `e.kind === "sheet"` render `before`/`after` as plain `<div className="absorb-before/absorb-after">` text (never the textarea path other kinds use) plus `e.payload?.note` as a `.field-hint`.
- `saveAbsorb`: capture the response; when `res.sheet_failures.length > 0` set a `sheetFailures` state rendered as a dismissible notice ("N sheet change(s) did not apply" + per-row label/reason) instead of silently clearing; still close the panel.

`SheetEditor.tsx` — keep the loaded snapshot in state when a sheet is fetched (`{sheet_type, fields, gen}` or `null` if none existed); pass it as `expected` on save and `gen` on delete; on a 409 error from `putSheet`/`deleteSheet` (the api layer throws with status — follow how other components detect statuses) re-fetch the sheet, replace the form state, and show "This sheet changed elsewhere — reloaded." `CreationWizard.tsx` likewise passes `expected` (normally `null` — creation) through `putSheetCreation`'s body.

`index.css` — minimal styles for `.mechanics-warnings`, `.mechanics-notice` consistent with the existing `.absorb-*` classes.

- [ ] **Step 4: Run** from `frontend/`: `npx vitest run` and `npx tsc -b` — PASS (tsc will also surface every call site of the changed api signatures — fix them all).
- [ ] **Step 5: Commit** — `git commit -m "feat(frontend): mechanics audit panel, read-only sheet rows, sheet CAS plumbing"`

---

### Task 11: Full verification + milestone check

**Files:** none new (fixes only if something surfaces).

- [ ] **Step 1: Full backend suite** — `backend/.venv/Scripts/python.exe -m pytest backend -q` → all pass.
- [ ] **Step 2: Full frontend** — from `frontend/`: `npx vitest run` && `npx tsc -b` → all pass.
- [ ] **Step 3: Template contract** — run `backend/.venv/Scripts/python.exe scripts/verify_templates.py` if it exists and covers absorb templates; register `audit/system.j2` + `audit/user.j2` if it enumerates variables (note: memory flags a pre-existing bug in this script — do not fix unrelated failures, only wire in the new templates).
- [ ] **Step 4: Milestone check with the `verify` skill** (mocked OpenRouter, isolated store): script a module-bound campaign scene with one logged roll and narrated damage; absorb → panel shows warning + read-only delta row; save → sheet changed; re-absorb → no second delta; a scripted garbage audit reply → degraded/failed notice, Retry recovers. (Launch via the repo's `verify` skill; drive with Playwright per that skill's docs.)
- [ ] **Step 5: Commit any fixes**, then run the CLAUDE.md **implementation gates**: `/codex:review` against the diff, then `/codex:adversarial-review` against the diff + the spec (does the diff implement the spec?). Address findings before `superpowers:finishing-a-development-branch`.

---

## Self-Review Notes (kept for the executor)

- **Spec coverage check** (spec section → task): baselines/validity/locking/rebind → 6; gen nonce → 1; write discipline lock/CAS/delete → 2/3/4; set_field → 5; audit prompt/parse/materialize/canonical/dropped → 7; apply_delta/apply_edits/sheet_failures → 8; routes mechanics statuses + retry endpoint → 9; frontend (warnings, degraded+retry, read-only rows, failures notice, editor CAS) → 10; milestone verify → 11. Out-of-scope items (whole-save idempotency, item scope, typed row editing) intentionally have no task.
- `expected` omitted == null == creation assertion (spec amended 2026-07-12; pydantic-agnostic models cannot distinguish).
- The `test_routes.py` LLM-call-count tests need PC-only casts (dossier calls are per-NPC and would break exact counts).
- Fixture names (`cid`, `module_scene`, `scene_with_sheeted_cast`, pack field names like `hp`/`xp`/`athletics`) are placeholders for whatever the existing test fixtures actually provide — **reuse the existing fixtures/reference packs**; do not invent a parallel fixture stack. All names are invented per the privacy rule.
