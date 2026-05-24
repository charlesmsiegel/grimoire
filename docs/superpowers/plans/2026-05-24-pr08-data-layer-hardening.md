# PR 8: Data Layer Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix JSON double-encoding, validate `_TABLE_COLUMNS` on startup, add repository methods for common queries, add event-driven cache invalidation, and add delta retention policy.

**Architecture:** Fixes go directly into existing modules. Repository methods are added to `StateStore` (or its PR 6 collaborators if that PR landed first). Cache invalidation uses the existing `EventBus` — `StateStore` emits `library_entity_changed` events, caching services subscribe and invalidate.

**Tech Stack:** Python 3.12+, aiosqlite, EventBus

---

### Task 1: Fix JSON double-encoding in _coerce_for_column

**Files:**
- Modify: `backend/src/grimoire/state_store/delta_log.py`
- Test: `backend/tests/state_store/test_delta_log.py`

- [ ] **Step 1: Write test for the double-encoding bug**

```python
def test_coerce_for_column_does_not_double_encode_strings():
    from grimoire.state_store.delta_log import _coerce_for_column
    already_json = '{"key": "value"}'
    result = _coerce_for_column(already_json)
    assert result == already_json  # should NOT be '"{\\"key\\": \\"value\\"}"'


def test_coerce_for_column_serializes_dicts():
    from grimoire.state_store.delta_log import _coerce_for_column
    result = _coerce_for_column({"key": "value"})
    assert isinstance(result, str)
    assert '"key"' in result


def test_coerce_for_column_converts_bools():
    from grimoire.state_store.delta_log import _coerce_for_column
    assert _coerce_for_column(True) == 1
    assert _coerce_for_column(False) == 0
```

- [ ] **Step 2: Run to verify the string test fails**

Run: `cd backend && uv run pytest tests/state_store/test_delta_log.py -k coerce -v`
Expected: The string test fails (current code double-encodes)

- [ ] **Step 3: Fix _coerce_for_column**

Add a `str` guard before the `json.dumps` call:

```python
def _coerce_for_column(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/state_store/test_delta_log.py -k coerce -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/state_store/delta_log.py backend/tests/state_store/test_delta_log.py
git commit -m "fix(state_store): prevent JSON double-encoding in _coerce_for_column"
```

---

### Task 2: Validate _TABLE_COLUMNS on startup

**Files:**
- Modify: `backend/src/grimoire/state_store/delta_log.py` (add validation function)
- Modify: `backend/src/grimoire/state_store/store.py` (call on init)

- [ ] **Step 1: Add validation function**

```python
async def validate_table_columns(db: Database) -> list[str]:
    """Compare _TABLE_COLUMNS against actual schema. Return warnings."""
    warnings = []
    for table, columns in _TABLE_COLUMNS.items():
        rows = await db.fetchall(f"PRAGMA table_info({table})")
        actual = {row["name"] for row in rows}
        declared = set(columns)
        missing = actual - declared - {"rowid"}
        if missing:
            warnings.append(
                f"{table}: columns {missing} exist in DB but not in _TABLE_COLUMNS"
            )
    return warnings
```

- [ ] **Step 2: Call from StateStore initialization**

After the store is constructed, call the validator and log warnings:

```python
warnings = await validate_table_columns(self.db)
for w in warnings:
    logger.warning("schema drift: %s", w)
```

- [ ] **Step 3: Run tests, commit**

```
git commit -m "feat(state_store): validate _TABLE_COLUMNS against DB schema on startup"
```

---

### Task 3: Add repository methods for common queries

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py`

- [ ] **Step 1: Add named query methods**

```python
async def list_library_by_kind(self, kind: str) -> list[dict]:
    return [dict(r) for r in await self.db.fetchall(
        "SELECT * FROM library_index WHERE kind = ? ORDER BY name", (kind,)
    )]

async def get_campaign_row(self, campaign_id: str) -> dict | None:
    row = await self.db.fetchone(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
    )
    return dict(row) if row else None

async def list_campaign_entities(self, campaign_id: str, kind: str) -> list[dict]:
    return [dict(r) for r in await self.db.fetchall(
        "SELECT * FROM campaign_entities WHERE campaign_id = ? AND kind = ?",
        (campaign_id, kind),
    )]

async def get_entity_state(self, campaign_id: str, entity_id: str) -> dict | None:
    row = await self.db.fetchone(
        "SELECT * FROM entity_state WHERE campaign_id = ? AND entity_id = ?",
        (campaign_id, entity_id),
    )
    return dict(row) if row else None

async def count_deltas(self, campaign_id: str) -> int:
    row = await self.db.fetchone(
        "SELECT COUNT(*) as cnt FROM deltas WHERE campaign_id = ?", (campaign_id,)
    )
    return row["cnt"] if row else 0
```

Read the actual table and column names from `storage/migrations.py` to confirm the SQL is correct. The above are templates.

- [ ] **Step 2: Update call sites**

Grep for `self.store.db.fetchall` and `self.store.db.fetchone` across services. Replace the 5 most common patterns with calls to the new repository methods.

- [ ] **Step 3: Run tests, commit**

```
git commit -m "feat(state_store): add named repository methods for common queries"
```

---

### Task 4: Add event-driven cache invalidation

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py` (emit entity_changed events)
- Modify: `backend/src/grimoire/characters/service.py` (subscribe and invalidate)
- Modify: `backend/src/grimoire/imagegen/service.py` (subscribe and invalidate)

- [ ] **Step 1: Emit library_entity_changed from StateStore**

In `StateStore.write_library_file()`, after the write succeeds:

```python
if self._bus is not None:
    await self._bus.emit(Event(
        type="library_entity_changed",
        payload={"ref": ref, "kind": kind},
    ))
```

- [ ] **Step 2: Subscribe in CharactersService**

```python
# In CharactersService.__init__, if event_bus is provided:
if event_bus is not None:
    event_bus.subscribe("library_entity_changed", self._on_entity_changed)

async def _on_entity_changed(self, event: Event) -> None:
    ref = event.payload.get("ref")
    if ref:
        self._view_cache_invalidate(ref)
```

- [ ] **Step 3: Subscribe in ImageGenService**

Same pattern — clear cache entries related to the changed entity.

- [ ] **Step 4: Write test**

```python
async def test_cache_invalidation_on_entity_change():
    # Setup: create CharactersService with event_bus
    # Write a library entity through StateStore
    # Verify CharactersService cache was invalidated
```

- [ ] **Step 5: Run tests, commit**

```
git commit -m "feat: add event-driven cache invalidation for library entity changes"
```

---

### Task 5: Add delta retention policy

**Files:**
- Modify: `backend/src/grimoire/state_store/retention.py`

- [ ] **Step 1: Extend RetentionConfig**

Add fields:
```python
delta_max_age_days: int = 180
delta_max_rows: int = 500_000
```

- [ ] **Step 2: Add delta sweep logic to the retention sweeper**

```python
async def _sweep_deltas(self) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=self._config.delta_max_age_days)).isoformat()
    # Delete reversed deltas older than cutoff first
    result = await self._db.execute(
        "DELETE FROM deltas WHERE reversed_at IS NOT NULL AND applied_at < ?",
        (cutoff,),
    )
    deleted = result.rowcount if result else 0
    # Then enforce per-campaign row cap
    # ...
    return deleted
```

Read the actual retention sweeper code to understand the existing pattern and follow it.

- [ ] **Step 3: Run tests, commit**

```
git commit -m "feat(state_store): add delta retention policy to sweeper"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 2: Verify startup logs show schema validation**

Start the app briefly and check logs for `_TABLE_COLUMNS` validation output.
