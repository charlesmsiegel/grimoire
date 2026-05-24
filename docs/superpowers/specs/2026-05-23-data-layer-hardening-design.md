# Data Layer Hardening

Date: 2026-05-23
Status: Approved
PR: 8 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 6 (Large service splits)

## Problem

Services bypass `StateStore` and reach directly into `self.store.db.fetchall()` for ad-hoc queries. In-memory caches across services have no coordinated invalidation. The `_TABLE_COLUMNS` whitelist in `delta_log.py` is hand-maintained and can silently skip new columns. JSON serialization in `_coerce_for_column` can double-encode values.

## Solution

Consolidate database access through explicit repository methods, add event-driven cache invalidation, fix JSON encoding, and validate the column whitelist on startup.

## Detailed Design

### Step 1: Fix JSON Double-Encoding

**File:** `backend/src/grimoire/state_store/delta_log.py`, `_coerce_for_column` (~line 414)

Current code calls `json.dumps()` on values that may already be JSON strings. Add a type guard:

```python
def _coerce_for_column(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value  # Already serialized
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value
```

### Step 2: Validate _TABLE_COLUMNS on Startup

**File:** `backend/src/grimoire/state_store/delta_log.py`

Add a validation function called during `StateStore` initialization:

```python
async def validate_table_columns(db: Database) -> list[str]:
    """Compare _TABLE_COLUMNS against actual schema. Return warnings."""
    warnings = []
    for table, columns in _TABLE_COLUMNS.items():
        actual = {row["name"] for row in await db.fetchall(f"PRAGMA table_info({table})")}
        declared = set(columns)
        missing = actual - declared - {"rowid"}
        if missing:
            warnings.append(f"{table}: columns {missing} exist in DB but not in _TABLE_COLUMNS")
    return warnings
```

Log warnings on startup. This is diagnostic, not blocking -- missing columns won't break existing code, but the warning alerts developers to update the whitelist.

### Step 3: Add Repository Methods for Common Queries

Services currently write raw SQL like `await self.store.db.fetchall("SELECT * FROM library_index WHERE kind = ? ...", (kind,))`. These should become named methods on `StateStore` or its collaborators (from PR 6):

**High-priority methods to add:**

| Method | Replaces | Used By |
|--------|----------|---------|
| `list_library_by_kind(kind)` | Raw SQL in LibraryService | library, world, characters |
| `get_campaign_row(campaign_id)` | Raw SQL in campaigns router | campaigns, orchestrator |
| `list_campaign_entities(campaign_id, kind)` | Raw SQL in characters, extras | characters, extras, entities |
| `get_entity_state(campaign_id, entity_id)` | Raw SQL in transient_state | transient_state, characters |
| `count_deltas(campaign_id)` | Raw SQL in observability | observability |

This does not create a full ORM. It adds named query methods for the most common patterns, reducing raw SQL duplication and creating a single place to add query logging or caching.

### Step 4: Event-Driven Cache Invalidation

**Current caches:**
- `CharactersService._active_pc` (OrderedDict, max 256)
- `CharactersService._view_cache` (OrderedDict)
- `ImageGenService._cache` (OrderedDict)

**New approach:** When `StateStore` writes a library entity, it emits an `entity_changed` event via the event bus:

```python
# In StateStore.write_library_file():
await self._bus.emit(Event(
    type="library_entity_changed",
    payload={"ref": ref, "kind": kind},
))
```

Services with caches subscribe:

```python
# In CharactersService.__init__():
if event_bus is not None:
    event_bus.subscribe("library_entity_changed", self._on_entity_changed)

async def _on_entity_changed(self, event: Event) -> None:
    ref = event.payload.get("ref")
    if ref:
        self._view_cache_invalidate(ref)
```

This keeps caches correct when entities are modified through any code path, not just through the owning service's methods.

### Step 5: Add Delta Retention Policy

**File:** `backend/src/grimoire/state_store/retention.py`

The `RetentionSweeper` already exists and runs periodically. Extend its config to include delta retention:

```python
@dataclass
class RetentionConfig:
    # Existing fields...
    delta_max_age_days: int = 180  # Keep deltas for 6 months
    delta_max_rows: int = 500_000  # Hard cap per campaign
```

The sweeper deletes deltas older than `delta_max_age_days` OR exceeding `delta_max_rows` per campaign, keeping the most recent rows. Reversed deltas (where `reversed_at IS NOT NULL`) are swept first.

## Scope

### In scope
- Fix JSON double-encoding in `_coerce_for_column`
- Add startup validation for `_TABLE_COLUMNS`
- Add 5 named repository methods to StateStore
- Add event-driven cache invalidation for CharactersService and ImageGenService
- Add delta retention policy to RetentionSweeper

### Not in scope
- Full repository/DAO layer extraction
- Changing the SQLite schema
- Adding database migrations
- Cache TTL or distributed caching
- Changing the delta log format

## Verification

1. `pytest` full suite passes.
2. No raw `fetchall`/`fetchone` calls outside StateStore and its collaborators for the 5 identified query patterns.
3. `_coerce_for_column` has a test for string, dict, list, bool, and None inputs.
4. Startup logs show `_TABLE_COLUMNS` validation ran with zero warnings (on current schema).
5. Cache invalidation test: modify a library entity through StateStore, verify CharactersService cache is cleared.
