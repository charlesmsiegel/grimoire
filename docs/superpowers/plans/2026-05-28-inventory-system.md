# Inventory Management System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a toggleable, per-campaign inventory subsystem where extraction is the only fuzzy step and application is a deterministic, ordered state machine that records mutations as reversible file-write deltas.

**Architecture:** A new `inventory/` domain module. The extractor emits *typed* inventory operations; the orchestrator calls `InventoryService.apply()` after extraction. The service resolves item identity, runs a pure state machine, writes each holder's `inventory:` section into their existing overlay file (override YAML / emergent frontmatter / PC profile), mirrors a derived `inventory_holdings` SQLite table, records flagged ops, and emits events. A data-driven HUD widget + REST/WS API + a React flagged-ops review panel expose it.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, aiosqlite, pytest/pytest-asyncio; TypeScript, React 18, Zod, Vitest + React Testing Library.

**Reference spec:** `docs/superpowers/specs/2026-05-28-inventory-system-design.md`

---

## Key facts the implementer must know

- **No `branch_id`.** Branches were removed (migration `036_remove_branches.sql`). All campaign-scoped tables key on `campaign_id` only. Do **not** add `branch_id` columns.
- **`DeltaKind.INVENTORY_CHANGE` already exists** (`backend/src/grimoire/types/state.py:35`). Reuse it.
- **`Scope`** is imported from `grimoire.types.common` (used as `Scope.CAMPAIGN_SQLITE`, `Scope.CAMPAIGN_FILE`, etc.). Check the enum members in that file before using one.
- **Migrations** live in `backend/src/grimoire/storage/migrations/NNN_name.sql`, applied in gap-free monotonic order by `apply_migrations()`. The next number is **037**.
- **StateStore public API** (all `async`): `write_override(*, campaign_id, library_id, patch, source, turn_id=None) -> Path`; `get_override(campaign_id, library_id) -> dict | None`; `write_emergent(*, campaign_id, kind, entity_id, frontmatter, body, source, turn_id=None) -> Path`; `get_emergent(campaign_id, kind, entity_id) -> dict | None`; `resolve_entity(*, campaign_id, kind, asset_id, world_id=None) -> dict | None` (returns `{"source": ..., "frontmatter": ..., ...}` where `source` ∈ `campaign-emergent | campaign-override | library-live | library-snapshot | library-fallback`); `get_campaign_config(campaign_id) -> dict | None`.
- **DB access:** `store.db` is a `Database` with `await store.db.fetchone(sql, params)`, `await store.db.fetchall(sql, params)`, and a transaction context `async with store._txn() as conn:` then `await conn.execute(...)`. For inventory's derived rows we add dedicated StateStore methods (storage layer owns SQLite).
- **Test fixture:** `backend/tests/state_store/conftest.py` exposes an async `store` fixture (migrated temp DB + `StateStore`). Reuse it for storage tests.
- **ID helpers:** `from grimoire.util import new_id, slugify_id` — `slugify_id("Bag of Holding") -> "bag-of-holding"` (deterministic), `new_id("inv") -> "inv_<hex>"` (random; avoid in deterministic paths).
- **EventBus:** `Event(type=str, payload=dict)`; `bus.subscribe(type, handler)`, `await bus.emit(Event(...))`. Event type constants are plain strings in `backend/src/grimoire/events.py`.
- **HUD is data-driven:** a widget is a `HudWidget` descriptor in `backend/src/grimoire/hud/widgets.py` (`CORE_WIDGETS` list) pointing at a `read` endpoint, with `render_hint` selecting an existing React renderer and `refresh_on` listing event types. Adding a basic display widget needs no new React component — only a descriptor, a read endpoint, and an id added to the frontend `CORE_WIDGET_IDS` set.

---

## File Structure

**Create (backend):**
- `backend/src/grimoire/storage/migrations/037_inventory.sql` — derived tables
- `backend/src/grimoire/inventory/__init__.py` — exports
- `backend/src/grimoire/inventory/models.py` — Pydantic models + enums
- `backend/src/grimoire/inventory/state_machine.py` — pure resolution logic
- `backend/src/grimoire/inventory/resolver.py` — item-identity resolution
- `backend/src/grimoire/inventory/config.py` — `InventoryConfig`
- `backend/src/grimoire/inventory/events.py` — event-type constants
- `backend/src/grimoire/inventory/service.py` — apply pipeline + I/O
- `backend/src/grimoire/inventory/persistence.py` — overlay-section read/write + derived-table sync (uses StateStore)
- `backend/src/grimoire/api/campaigns/inventory.py` — REST router

**Create (frontend):**
- `frontend/src/api/inventory.ts` — API client + Zod schemas
- `frontend/src/routes/campaign/SideHud/widgets/InventoryFlagsPanel.tsx` — flagged-ops review component
- `frontend/src/routes/campaign/SideHud/__tests__/InventoryFlagsPanel.test.tsx`

**Create (tests):**
- `backend/tests/inventory/__init__.py`
- `backend/tests/inventory/test_state_machine.py`
- `backend/tests/inventory/test_resolver.py`
- `backend/tests/inventory/test_persistence.py`
- `backend/tests/inventory/test_service.py`
- `backend/tests/inventory/test_api.py`
- `backend/tests/integration/test_inventory_pipeline.py`

**Modify (backend):**
- `backend/src/grimoire/state_store/store.py` — add inventory derived-table methods
- `backend/src/grimoire/extractor/schema.py` — typed `inventory_change` schema
- `backend/src/grimoire/extractor/llm_strategy.py` — `_make_inventory_delta`
- `backend/src/grimoire/extractor/rule_based.py` — `_make_inventory_delta`
- `backend/src/grimoire/events.py` — add inventory event constants
- `backend/src/grimoire/api/stream.py` — forward inventory events
- `backend/src/grimoire/orchestrator/service.py` — constructor param + apply call
- `backend/src/grimoire/orchestrator/delta_applier.py` — route INVENTORY_CHANGE
- `backend/src/grimoire/bootstrap.py` — construct + wire InventoryService
- `backend/src/grimoire/watcher/watcher.py` — rebuild inventory_holdings from files
- `backend/src/grimoire/hud/widgets.py` — `core.inventory` descriptor
- `backend/src/grimoire/api/campaigns/__init__.py` (or wherever routers mount) — mount router
- Existing extractor tests: `test_rule_based.py`, `test_llm_strategy.py`, `test_service.py`, `integration/test_golden_extractor.py`

**Modify (frontend):**
- `frontend/src/routes/campaign/SideHud/SideHud.tsx` — add `core.inventory` to `CORE_WIDGET_IDS`, mount flags panel

**Modify (docs):**
- `CLAUDE.md`, `AGENTS.md` — module ownership table
- `README.md` — feature list

---

## Task 1: Migration — derived inventory tables

**Files:**
- Create: `backend/src/grimoire/storage/migrations/037_inventory.sql`
- Create: `backend/tests/inventory/__init__.py` (empty), `backend/tests/inventory/conftest.py`
- Test: `backend/tests/inventory/test_persistence.py`

- [ ] **Step 1: Create the test package marker and shared `store` fixture**

Create `backend/tests/inventory/__init__.py` with no content (empty file).

pytest fixtures do not cross sibling test directories, so `tests/inventory/`
needs its own `store` fixture (a migrated temp DB + `StateStore`). Create
`backend/tests/inventory/conftest.py` (copied from `tests/state_store/conftest.py`):

```python
"""Fixtures for the inventory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()
```

- [ ] **Step 2: Write the failing migration test**

Create `backend/tests/inventory/test_persistence.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_inventory_tables_exist(store):
    rows = await store.db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('inventory_holdings', 'inventory_flags')"
    )
    names = {r["name"] for r in rows}
    assert names == {"inventory_holdings", "inventory_flags"}


async def test_inventory_holdings_has_no_branch_id(store):
    cols = await store.db.fetchall("PRAGMA table_info(inventory_holdings)")
    names = {c["name"] for c in cols}
    assert "branch_id" not in names
    assert {"campaign_id", "holder_kind", "holder_id", "item_ref", "quantity"} <= names
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/inventory/test_persistence.py -v`
Expected: FAIL — no such table `inventory_holdings`.

- [ ] **Step 4: Write the migration**

Create `backend/src/grimoire/storage/migrations/037_inventory.sql`:

```sql
-- Inventory subsystem (#444). Derived from per-holder overlay `inventory:`
-- sections, which are the source of truth. Rebuilt from files by the watcher.
-- No branch_id: branches were removed in migration 036.

CREATE TABLE inventory_holdings (
  id           TEXT PRIMARY KEY,   -- campaign_id:holder_kind:holder_id:item_ref
  campaign_id  TEXT NOT NULL,
  holder_kind  TEXT NOT NULL,      -- 'character' | 'location'
  holder_id    TEXT NOT NULL,
  item_ref     TEXT NOT NULL,
  item_name    TEXT NOT NULL,
  quantity     INTEGER NOT NULL,
  fungible     INTEGER NOT NULL DEFAULT 0,
  equipped     INTEGER NOT NULL DEFAULT 0,
  provenance   TEXT,
  notes        TEXT
);
CREATE INDEX idx_inv_holder ON inventory_holdings(campaign_id, holder_kind, holder_id);
CREATE INDEX idx_inv_item   ON inventory_holdings(campaign_id, item_ref);

CREATE TABLE inventory_flags (
  id           TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL,
  turn_id      TEXT,
  op_json      TEXT NOT NULL,      -- the originating InventoryOperation as JSON
  flag_reason  TEXT NOT NULL,      -- low_confidence | reconciled_* | unresolved_*
  resolved     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_inv_flags_campaign ON inventory_flags(campaign_id, resolved);
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/inventory/test_persistence.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/storage/migrations/037_inventory.sql backend/tests/inventory/
git commit -m "feat(inventory): add derived inventory tables (#444)"
```

---

## Task 2: Models & enums

**Files:**
- Create: `backend/src/grimoire/inventory/__init__.py`, `backend/src/grimoire/inventory/models.py`
- Test: `backend/tests/inventory/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_models.py`:

```python
from grimoire.inventory.models import (
    FlagReason,
    FlaggedOp,
    InventoryAction,
    InventoryEntry,
    InventoryOperation,
)


def test_entry_defaults():
    e = InventoryEntry(item_ref="the-key", item_name="The Key")
    assert e.quantity == 1
    assert e.fungible is False
    assert e.equipped is False
    assert e.provenance is None


def test_operation_roundtrip_json():
    op = InventoryOperation(
        action=InventoryAction.TRANSFER,
        item="silver ring",
        holder="winifred",
        to="julian",
        quantity=1,
        confidence=0.9,
    )
    blob = op.model_dump_json()
    back = InventoryOperation.model_validate_json(blob)
    assert back.action is InventoryAction.TRANSFER
    assert back.to == "julian"


def test_flagged_op_reason_enum():
    op = InventoryOperation(action=InventoryAction.DROP, item="x", holder="h", confidence=0.1)
    flag = FlaggedOp(op=op, reason=FlagReason.LOW_CONFIDENCE)
    assert flag.reason is FlagReason.LOW_CONFIDENCE
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/inventory/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: grimoire.inventory`.

- [ ] **Step 3: Create the package init**

Create `backend/src/grimoire/inventory/__init__.py`:

```python
"""Deterministic inventory subsystem (#444)."""
```

- [ ] **Step 4: Write the models**

Create `backend/src/grimoire/inventory/models.py`:

```python
"""Inventory domain models: entries, operations, results, flags."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class InventoryAction(StrEnum):
    ACQUIRE = "acquire"
    DROP = "drop"
    TRANSFER = "transfer"
    CONSUME = "consume"
    ADJUST = "adjust"
    EQUIP = "equip"
    UNEQUIP = "unequip"


class HolderKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"


class FlagReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    RECONCILED_MISSING_ITEM = "reconciled_missing_item"
    RECONCILED_QUANTITY = "reconciled_quantity"
    RECONCILED_HOLDER = "reconciled_holder"
    UNRESOLVED_ITEM = "unresolved_item"
    UNRESOLVED_HOLDER = "unresolved_holder"


class InventoryEntry(BaseModel):
    """One held item in a holder's inventory section."""

    item_ref: str
    item_name: str
    quantity: int = 1
    fungible: bool = False
    equipped: bool = False
    provenance: str | None = None
    notes: str | None = None
    acquired_in_post: str | None = None


class InventoryOperation(BaseModel):
    """A typed, deterministic operation proposed by the extractor or the user."""

    action: InventoryAction
    item: str                       # natural-language item; resolved to item_ref later
    holder: str                     # acting/source holder ref
    to: str | None = None           # destination holder for transfer
    quantity: int | None = None     # default 1; signed delta for ADJUST
    equipped: bool | None = None
    provenance: str | None = None
    confidence: float = 1.0
    source: str = "extractor"       # 'extractor' | 'user' | 'mechanics:...'
    evidence: str = ""


class FlaggedOp(BaseModel):
    """An operation surfaced for review (low confidence or reconciled)."""

    op: InventoryOperation
    reason: FlagReason


class HolderChange(BaseModel):
    """Resolved net change to a single holder's entries after applying ops."""

    holder_kind: HolderKind
    holder_id: str
    entries: list[InventoryEntry] = Field(default_factory=list)


class OperationResult(BaseModel):
    """Outcome of running the state machine over a turn's operations."""

    changed_holders: list[HolderChange] = Field(default_factory=list)
    flags: list[FlaggedOp] = Field(default_factory=list)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && uv run pytest tests/inventory/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/inventory/__init__.py backend/src/grimoire/inventory/models.py backend/tests/inventory/test_models.py
git commit -m "feat(inventory): add domain models and enums (#444)"
```

---

## Task 3: Pure deterministic state machine

The state machine operates on **resolved** operations (item already resolved to `item_ref` + `fungible`, holders already resolved to `(kind, id)`). It is pure: no I/O, clock, or LLM. To keep it independent of resolution, it takes a small `ResolvedOp` value.

**Files:**
- Create: `backend/src/grimoire/inventory/state_machine.py`
- Test: `backend/tests/inventory/test_state_machine.py`

- [ ] **Step 1: Write failing tests (valid paths)**

Create `backend/tests/inventory/test_state_machine.py`:

```python
from grimoire.inventory.models import (
    HolderKind,
    InventoryAction,
    InventoryEntry,
)
from grimoire.inventory.state_machine import ResolvedOp, apply_op


def _op(action, *, item_ref="ring", fungible=False, holder=("character", "flo"),
        to=None, quantity=None, equipped=None, item_name="Ring"):
    return ResolvedOp(
        action=action,
        item_ref=item_ref,
        item_name=item_name,
        fungible=fungible,
        holder_kind=HolderKind(holder[0]),
        holder_id=holder[1],
        to_kind=HolderKind(to[0]) if to else None,
        to_id=to[1] if to else None,
        quantity=quantity,
        equipped=equipped,
    )


def _holdings(*entries):
    # mapping: (holder_kind, holder_id) -> {item_ref: InventoryEntry}
    h = {}
    for (hk, hid), entry in entries:
        h.setdefault((HolderKind(hk), hid), {})[entry.item_ref] = entry
    return h


def test_acquire_adds_entry():
    h = {}
    res = apply_op(h, _op(InventoryAction.ACQUIRE))
    entry = h[(HolderKind.CHARACTER, "flo")]["ring"]
    assert entry.quantity == 1
    assert res.flag is None


def test_acquire_stacks_fungible():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=100, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.ACQUIRE, item_ref="gold", fungible=True, quantity=20))
    assert h[(HolderKind.CHARACTER, "flo")]["gold"].quantity == 120


def test_transfer_moves_between_holders():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="ring", item_name="Ring", quantity=1)),
    )
    res = apply_op(
        h, _op(InventoryAction.TRANSFER, to=("character", "julian"), quantity=1)
    )
    assert "ring" not in h[(HolderKind.CHARACTER, "flo")]
    assert h[(HolderKind.CHARACTER, "julian")]["ring"].quantity == 1
    assert res.flag is None


def test_consume_default_one_removes_at_zero():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="potion", item_name="Potion", quantity=1, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.CONSUME, item_ref="potion", fungible=True))
    assert "potion" not in h[(HolderKind.CHARACTER, "flo")]


def test_adjust_applies_signed_delta():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=100, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.ADJUST, item_ref="gold", fungible=True, quantity=-30))
    assert h[(HolderKind.CHARACTER, "flo")]["gold"].quantity == 70


def test_equip_sets_flag():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="sword", item_name="Sword", quantity=1)),
    )
    apply_op(h, _op(InventoryAction.EQUIP, item_ref="sword"))
    assert h[(HolderKind.CHARACTER, "flo")]["sword"].equipped is True
```

- [ ] **Step 2: Write failing tests (conflict/reconcile paths)**

Append to `backend/tests/inventory/test_state_machine.py`:

```python
from grimoire.inventory.models import FlagReason


def test_drop_missing_item_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.DROP))
    # Reconciled: item granted then dropped -> holder ends with no entry.
    assert "ring" not in h.get((HolderKind.CHARACTER, "flo"), {})
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM


def test_transfer_missing_source_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.TRANSFER, to=("character", "julian"), quantity=1))
    assert h[(HolderKind.CHARACTER, "julian")]["ring"].quantity == 1
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM


def test_over_consume_clamps_and_flags():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=10, fungible=True)),
    )
    res = apply_op(h, _op(InventoryAction.CONSUME, item_ref="gold", fungible=True, quantity=50))
    assert "gold" not in h[(HolderKind.CHARACTER, "flo")]
    assert res.flag is FlagReason.RECONCILED_QUANTITY


def test_adjust_below_zero_clamps_and_flags():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=10, fungible=True)),
    )
    res = apply_op(h, _op(InventoryAction.ADJUST, item_ref="gold", fungible=True, quantity=-50))
    assert "gold" not in h[(HolderKind.CHARACTER, "flo")]
    assert res.flag is FlagReason.RECONCILED_QUANTITY


def test_equip_missing_item_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.EQUIP, item_ref="sword", item_name="Sword"))
    assert h[(HolderKind.CHARACTER, "flo")]["sword"].equipped is True
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_state_machine.py -v`
Expected: FAIL — `ModuleNotFoundError` / `apply_op` not defined.

- [ ] **Step 4: Implement the state machine**

Create `backend/src/grimoire/inventory/state_machine.py`:

```python
"""Pure, deterministic inventory operation resolution. No I/O.

The state machine mutates an in-memory holdings map:
    holdings[(HolderKind, holder_id)][item_ref] = InventoryEntry

Each ``apply_op`` returns a ``StepResult`` carrying an optional reconciliation
flag. Conflicts never raise — the prose is canon, so we reconcile state to
match the narrative and flag the discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import FlagReason, HolderKind, InventoryAction, InventoryEntry

Holdings = dict[tuple[HolderKind, str], dict[str, InventoryEntry]]


@dataclass(frozen=True)
class ResolvedOp:
    action: InventoryAction
    item_ref: str
    item_name: str
    fungible: bool
    holder_kind: HolderKind
    holder_id: str
    to_kind: HolderKind | None = None
    to_id: str | None = None
    quantity: int | None = None
    equipped: bool | None = None
    provenance: str | None = None
    acquired_in_post: str | None = None


@dataclass(frozen=True)
class StepResult:
    flag: FlagReason | None = None


def _bucket(holdings: Holdings, kind: HolderKind, hid: str) -> dict[str, InventoryEntry]:
    return holdings.setdefault((kind, hid), {})


def _grant(bucket: dict[str, InventoryEntry], op: ResolvedOp, qty: int) -> None:
    existing = bucket.get(op.item_ref)
    if existing is not None:
        existing.quantity += qty
        return
    bucket[op.item_ref] = InventoryEntry(
        item_ref=op.item_ref,
        item_name=op.item_name,
        quantity=qty,
        fungible=op.fungible,
        provenance=op.provenance,
        acquired_in_post=op.acquired_in_post,
    )


def _remove(bucket: dict[str, InventoryEntry], item_ref: str, qty: int) -> bool:
    """Remove qty; delete entry at <=0. Returns True if a shortfall was clamped."""
    entry = bucket.get(item_ref)
    if entry is None:
        return True  # nothing to remove — shortfall
    shortfall = qty > entry.quantity
    entry.quantity -= qty
    if entry.quantity <= 0:
        del bucket[item_ref]
    return shortfall


def apply_op(holdings: Holdings, op: ResolvedOp) -> StepResult:
    qty = op.quantity if op.quantity is not None else 1
    src = _bucket(holdings, op.holder_kind, op.holder_id)

    if op.action is InventoryAction.ACQUIRE:
        _grant(src, op, qty)
        return StepResult()

    if op.action is InventoryAction.DROP:
        missing = op.item_ref not in src
        _remove(src, op.item_ref, qty)
        return StepResult(FlagReason.RECONCILED_MISSING_ITEM if missing else None)

    if op.action is InventoryAction.TRANSFER:
        if op.to_kind is None or op.to_id is None:
            # Treat as a drop if no destination resolved.
            missing = op.item_ref not in src
            _remove(src, op.item_ref, qty)
            return StepResult(FlagReason.RECONCILED_HOLDER)
        missing = op.item_ref not in src
        _remove(src, op.item_ref, qty)
        dst = _bucket(holdings, op.to_kind, op.to_id)
        _grant(dst, op, qty)
        return StepResult(FlagReason.RECONCILED_MISSING_ITEM if missing else None)

    if op.action is InventoryAction.CONSUME:
        entry = src.get(op.item_ref)
        take = qty if op.quantity is not None else (entry.quantity if entry else 1)
        shortfall = _remove(src, op.item_ref, take)
        return StepResult(FlagReason.RECONCILED_QUANTITY if shortfall else None)

    if op.action is InventoryAction.ADJUST:
        entry = src.get(op.item_ref)
        if entry is None:
            if qty <= 0:
                return StepResult(FlagReason.RECONCILED_QUANTITY)
            _grant(src, op, qty)
            return StepResult()
        new_q = entry.quantity + qty
        if new_q <= 0:
            del src[op.item_ref]
            return StepResult(FlagReason.RECONCILED_QUANTITY if new_q < 0 else None)
        entry.quantity = new_q
        return StepResult()

    if op.action in (InventoryAction.EQUIP, InventoryAction.UNEQUIP):
        want = op.action is InventoryAction.EQUIP
        entry = src.get(op.item_ref)
        flag = None
        if entry is None:
            _grant(src, op, 1)
            entry = src[op.item_ref]
            flag = FlagReason.RECONCILED_MISSING_ITEM
        entry.equipped = want
        return StepResult(flag)

    return StepResult()
```

- [ ] **Step 5: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_state_machine.py -v`
Expected: PASS (all 11 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/inventory/state_machine.py backend/tests/inventory/test_state_machine.py
git commit -m "feat(inventory): pure deterministic state machine (#444)"
```

---

## Task 4: Config

**Files:**
- Create: `backend/src/grimoire/inventory/config.py`
- Test: `backend/tests/inventory/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_config.py`:

```python
from grimoire.inventory.config import InventoryConfig

DEFAULT_FUNGIBLES = {"gold", "silver", "coins", "arrows", "rations", "torches"}


def test_defaults_disabled():
    cfg = InventoryConfig.from_campaign_config(None)
    assert cfg.enabled is False
    assert cfg.flag_threshold == 0.6
    assert DEFAULT_FUNGIBLES <= cfg.fungible_resources


def test_reads_campaign_block():
    cfg = InventoryConfig.from_campaign_config(
        {"inventory": {"enabled": True, "flag_threshold": 0.8, "fungible_resources": ["mana"]}}
    )
    assert cfg.enabled is True
    assert cfg.flag_threshold == 0.8
    assert "mana" in cfg.fungible_resources
    assert "gold" in cfg.fungible_resources  # extends defaults, not replaces


def test_ignores_missing_block():
    cfg = InventoryConfig.from_campaign_config({"model_tiers": {}})
    assert cfg.enabled is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_config.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement config**

Create `backend/src/grimoire/inventory/config.py`:

```python
"""Per-campaign inventory configuration, read from the campaign config block."""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_FUNGIBLES = frozenset(
    {"gold", "silver", "coins", "arrows", "rations", "torches"}
)


@dataclass(frozen=True)
class InventoryConfig:
    enabled: bool = False
    flag_threshold: float = 0.6
    fungible_resources: frozenset[str] = field(default_factory=lambda: _DEFAULT_FUNGIBLES)

    @classmethod
    def from_campaign_config(cls, campaign_config: dict | None) -> InventoryConfig:
        block = (campaign_config or {}).get("inventory") or {}
        extra = {str(x).strip().lower() for x in block.get("fungible_resources", [])}
        return cls(
            enabled=bool(block.get("enabled", False)),
            flag_threshold=float(block.get("flag_threshold", 0.6)),
            fungible_resources=_DEFAULT_FUNGIBLES | extra,
        )
```

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/inventory/config.py backend/tests/inventory/test_config.py
git commit -m "feat(inventory): per-campaign config (#444)"
```

---

## Task 5: Item-identity resolver

Resolves a natural-language item string to `(item_ref, item_name, fungible)`: (1) fungible keyword → `resource:<slug>`; (2) existing emergent/library item match → its id; (3) otherwise auto-create an emergent item and return its new id. The resolver depends only on a small protocol so it's unit-testable with a fake.

**Files:**
- Create: `backend/src/grimoire/inventory/resolver.py`
- Test: `backend/tests/inventory/test_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_resolver.py`:

```python
import pytest

from grimoire.inventory.config import InventoryConfig
from grimoire.inventory.resolver import ItemResolver

pytestmark = pytest.mark.asyncio


class FakeStore:
    def __init__(self, existing=None):
        self.existing = existing or {}      # slug -> name
        self.created = []                   # (entity_id, frontmatter)

    async def find_item_by_name(self, campaign_id, name):
        slug = name.strip().lower().replace(" ", "-")
        if slug in self.existing:
            return {"item_ref": slug, "item_name": self.existing[slug]}
        return None

    async def create_emergent_item(self, campaign_id, name, *, source, turn_id=None):
        slug = name.strip().lower().replace(" ", "-")
        self.created.append((slug, name))
        return slug


def _cfg():
    return InventoryConfig(enabled=True)


async def test_fungible_keyword_resolves_to_resource():
    r = ItemResolver(FakeStore(), _cfg())
    ref, name, fungible = await r.resolve("c1", "120 gold", turn_id=None)
    assert ref == "resource:gold"
    assert fungible is True


async def test_existing_item_match():
    store = FakeStore(existing={"silver-ring": "Silver Ring"})
    r = ItemResolver(store, _cfg())
    ref, name, fungible = await r.resolve("c1", "silver ring", turn_id=None)
    assert ref == "silver-ring"
    assert fungible is False
    assert store.created == []


async def test_unknown_item_auto_creates_emergent():
    store = FakeStore()
    r = ItemResolver(store, _cfg())
    ref, name, fungible = await r.resolve("c1", "rusty key", turn_id="t1")
    assert ref == "rusty-key"
    assert store.created == [("rusty-key", "rusty key")]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_resolver.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the resolver**

Create `backend/src/grimoire/inventory/resolver.py`:

```python
"""Resolve natural-language item strings to stable item refs.

Order: fungible keyword -> existing item -> auto-created emergent item.
Depends on a narrow store protocol so it is unit-testable with a fake.
"""

from __future__ import annotations

import re
from typing import Protocol

from grimoire.util import slugify_id

from .config import InventoryConfig

# Leading quantity/article noise: "120 gold", "a rusty key", "the silver ring"
_QTY_PREFIX = re.compile(r"^\s*(\d+\s+|a\s+|an\s+|the\s+|some\s+)+", re.IGNORECASE)


class InventoryItemStore(Protocol):
    async def find_item_by_name(self, campaign_id: str, name: str) -> dict | None: ...
    async def create_emergent_item(
        self, campaign_id: str, name: str, *, source: str, turn_id: str | None = None
    ) -> str: ...


def _clean_name(raw: str) -> str:
    return _QTY_PREFIX.sub("", raw or "").strip()


class ItemResolver:
    def __init__(self, store: InventoryItemStore, config: InventoryConfig) -> None:
        self._store = store
        self._config = config

    async def resolve(
        self, campaign_id: str, raw_item: str, *, turn_id: str | None
    ) -> tuple[str, str, bool]:
        """Return (item_ref, item_name, fungible)."""
        name = _clean_name(raw_item)
        slug = slugify_id(name) if name else "unknown-item"

        # 1. Fungible keyword.
        if slug in self._config.fungible_resources:
            return f"resource:{slug}", name.title() or slug, True

        # 2. Existing item.
        match = await self._store.find_item_by_name(campaign_id, name)
        if match is not None:
            return match["item_ref"], match["item_name"], False

        # 3. Auto-create emergent item.
        new_ref = await self._store.create_emergent_item(
            campaign_id, name, source="inventory", turn_id=turn_id
        )
        return new_ref, name, False
```

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/inventory/resolver.py backend/tests/inventory/test_resolver.py
git commit -m "feat(inventory): item-identity resolver (#444)"
```

---

## Task 6: StateStore derived-table & item methods

Add the SQLite-side methods the inventory module needs (the storage layer owns SQLite). These cover the derived `inventory_holdings` table, the `inventory_flags` table, and the item lookup/auto-create used by the resolver.

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py`
- Test: `backend/tests/inventory/test_persistence.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/inventory/test_persistence.py`:

```python
async def test_upsert_and_list_holdings(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.upsert_inventory_holding(
        campaign_id="c1", holder_kind="character", holder_id="flo",
        item_ref="ring", item_name="Ring", quantity=2, fungible=False,
        equipped=False, provenance=None, notes=None,
    )
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="flo")
    assert len(rows) == 1
    assert rows[0]["quantity"] == 2

    await store.delete_inventory_holding("c1", "character", "flo", "ring")
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="flo")
    assert rows == []


async def test_record_and_list_flags(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.record_inventory_flag(
        campaign_id="c1", turn_id="t1", op_json='{"action":"drop"}',
        flag_reason="low_confidence", created_at="2026-05-28T00:00:00Z",
    )
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert len(flags) == 1
    fid = flags[0]["id"]
    await store.resolve_inventory_flag("c1", fid)
    assert await store.list_inventory_flags("c1", resolved=False) == []


async def test_find_and_create_emergent_item(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    assert await store.find_item_by_name("c1", "Rusty Key") is None
    ref = await store.create_emergent_item("c1", "Rusty Key", source="inventory")
    assert ref == "rusty-key"
    found = await store.find_item_by_name("c1", "rusty key")
    assert found is not None and found["item_ref"] == "rusty-key"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_persistence.py -v`
Expected: FAIL — methods not defined.

- [ ] **Step 3: Add the methods to StateStore**

In `backend/src/grimoire/state_store/store.py`, add these methods to the `StateStore` class (place them near the other campaign-content methods, e.g. after `get_emergent`). Use the existing `self.db.execute` / `self.db.fetchall` helpers — match the surrounding code's exact DB-helper names (verify against neighboring methods; the codebase uses `self.db.fetchone`/`self.db.fetchall` and `self.db.execute`).

```python
    # ── Inventory derived state (#444) ──────────────────────────────

    async def upsert_inventory_holding(
        self,
        *,
        campaign_id: str,
        holder_kind: str,
        holder_id: str,
        item_ref: str,
        item_name: str,
        quantity: int,
        fungible: bool,
        equipped: bool,
        provenance: str | None,
        notes: str | None,
    ) -> None:
        rid = f"{campaign_id}:{holder_kind}:{holder_id}:{item_ref}"
        await self.db.execute(
            """
            INSERT INTO inventory_holdings
              (id, campaign_id, holder_kind, holder_id, item_ref, item_name,
               quantity, fungible, equipped, provenance, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              item_name=excluded.item_name, quantity=excluded.quantity,
              fungible=excluded.fungible, equipped=excluded.equipped,
              provenance=excluded.provenance, notes=excluded.notes
            """,
            (rid, campaign_id, holder_kind, holder_id, item_ref, item_name,
             int(quantity), int(fungible), int(equipped), provenance, notes),
        )

    async def delete_inventory_holding(
        self, campaign_id: str, holder_kind: str, holder_id: str, item_ref: str
    ) -> None:
        rid = f"{campaign_id}:{holder_kind}:{holder_id}:{item_ref}"
        await self.db.execute("DELETE FROM inventory_holdings WHERE id = ?", (rid,))

    async def clear_holder_inventory(
        self, campaign_id: str, holder_kind: str, holder_id: str
    ) -> None:
        await self.db.execute(
            "DELETE FROM inventory_holdings WHERE campaign_id=? AND holder_kind=? AND holder_id=?",
            (campaign_id, holder_kind, holder_id),
        )

    async def list_inventory_holdings(
        self,
        campaign_id: str,
        *,
        holder_kind: str | None = None,
        holder_id: str | None = None,
        item_ref: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM inventory_holdings WHERE campaign_id = ?"
        params: list = [campaign_id]
        if holder_kind is not None:
            sql += " AND holder_kind = ?"
            params.append(holder_kind)
        if holder_id is not None:
            sql += " AND holder_id = ?"
            params.append(holder_id)
        if item_ref is not None:
            sql += " AND item_ref = ?"
            params.append(item_ref)
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    async def record_inventory_flag(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        op_json: str,
        flag_reason: str,
        created_at: str,
    ) -> str:
        from grimoire.util import new_id

        fid = new_id("invflag")
        await self.db.execute(
            """
            INSERT INTO inventory_flags
              (id, campaign_id, turn_id, op_json, flag_reason, resolved, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (fid, campaign_id, turn_id, op_json, flag_reason, created_at),
        )
        return fid

    async def list_inventory_flags(self, campaign_id: str, *, resolved: bool) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM inventory_flags WHERE campaign_id=? AND resolved=? "
            "ORDER BY created_at DESC",
            (campaign_id, int(resolved)),
        )
        return [dict(r) for r in rows]

    async def resolve_inventory_flag(self, campaign_id: str, flag_id: str) -> None:
        await self.db.execute(
            "UPDATE inventory_flags SET resolved=1 WHERE campaign_id=? AND id=?",
            (campaign_id, flag_id),
        )

    async def find_item_by_name(self, campaign_id: str, name: str) -> dict | None:
        """Resolve an item name to a campaign-visible item via the content index."""
        from grimoire.util import slugify_id

        slug = slugify_id(name)
        row = await self.db.fetchone(
            "SELECT asset_id, frontmatter FROM campaign_content_index "
            "WHERE campaign_id=? AND entity_subkind='item' AND asset_id=?",
            (campaign_id, slug),
        )
        if row is None:
            return None
        import json

        fm = json.loads(row["frontmatter"]) if row["frontmatter"] else {}
        return {"item_ref": row["asset_id"], "item_name": fm.get("name", name)}

    async def create_emergent_item(
        self, campaign_id: str, name: str, *, source: str, turn_id: str | None = None
    ) -> str:
        from grimoire.util import slugify_id

        slug = slugify_id(name)
        await self.write_emergent(
            campaign_id=campaign_id,
            kind="item",
            entity_id=slug,
            frontmatter={"id": slug, "name": name, "tags": ["emergent"]},
            body="",
            source=source,
            turn_id=turn_id,
        )
        return slug
```

> Implementation note: confirm `self.db.execute(sql, params)` exists and commits (autocommit) on this `Database` wrapper. If writes must run inside `_txn()`, wrap each `execute` accordingly: `async with self._txn() as conn: await conn.execute(sql, params)`. Check how `upsert_campaign` performs its writes and mirror it exactly.

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_persistence.py -v`
Expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/state_store/store.py backend/tests/inventory/test_persistence.py
git commit -m "feat(inventory): StateStore derived-table + item methods (#444)"
```

---

## Task 7: Overlay-section persistence

Read/write the `inventory:` section in a holder's existing overlay file, branching on holder origin. This is the file-SSOT writer; it also re-syncs the derived `inventory_holdings` rows for the holder.

**Files:**
- Create: `backend/src/grimoire/inventory/persistence.py`
- Test: `backend/tests/inventory/test_overlay_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_overlay_persistence.py`:

```python
import pytest

from grimoire.inventory.models import HolderKind, InventoryEntry
from grimoire.inventory.persistence import InventoryPersistence

pytestmark = pytest.mark.asyncio


async def test_emergent_holder_roundtrip(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    # Seed an emergent character holder.
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"}, body="barkeep", source="test",
    )
    p = InventoryPersistence(store)
    entries = [InventoryEntry(item_ref="ring", item_name="Ring", quantity=1)]
    await p.write_holder_inventory(
        campaign_id="c1", holder_kind=HolderKind.CHARACTER, holder_id="joe",
        entries=entries, source="inventory", turn_id="t1",
    )

    # File SSOT updated.
    doc = await store.get_emergent("c1", "character", "joe")
    assert doc["frontmatter"]["inventory"]["entries"][0]["item_ref"] == "ring"

    # Derived table synced.
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert len(rows) == 1 and rows[0]["item_ref"] == "ring"

    # Round-trip read.
    read = await p.read_holder_inventory("c1", HolderKind.CHARACTER, "joe")
    assert read[0].item_ref == "ring"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_overlay_persistence.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement persistence**

Create `backend/src/grimoire/inventory/persistence.py`:

```python
"""Read/write the `inventory:` section in a holder's overlay file (SSOT),
keeping the derived `inventory_holdings` rows in sync.

Holder origin determines the overlay file:
  - campaign-emergent  -> emergent frontmatter
  - campaign-override / library-*  -> override YAML patch
PC profiles resolve as characters via the same cascade; their inventory rides
in the resolved frontmatter, written through the override patch path.
"""

from __future__ import annotations

from typing import Any

from .models import HolderKind, InventoryEntry


class InventoryPersistence:
    def __init__(self, store: Any) -> None:
        self._store = store

    async def read_holder_inventory(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str
    ) -> list[InventoryEntry]:
        resolved = await self._store.resolve_entity(
            campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id,
            world_id=await self._world_id_for(campaign_id, holder_kind, holder_id),
        )
        fm = (resolved or {}).get("frontmatter", {}) or {}
        block = fm.get("inventory") or {}
        return [InventoryEntry.model_validate(e) for e in block.get("entries", [])]

    async def write_holder_inventory(
        self,
        *,
        campaign_id: str,
        holder_kind: HolderKind,
        holder_id: str,
        entries: list[InventoryEntry],
        source: str,
        turn_id: str | None,
    ) -> None:
        section = {"entries": [e.model_dump(exclude_none=True) for e in entries]}
        world_id = await self._world_id_for(campaign_id, holder_kind, holder_id)
        resolved = await self._store.resolve_entity(
            campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id,
            world_id=world_id,
        )
        origin = (resolved or {}).get("source", "")

        if origin == "campaign-emergent":
            doc = await self._store.get_emergent(campaign_id, holder_kind.value, holder_id)
            fm = dict((doc or {}).get("frontmatter", {}))
            body = (doc or {}).get("body", "")
            fm["inventory"] = section
            await self._store.write_emergent(
                campaign_id=campaign_id, kind=holder_kind.value, entity_id=holder_id,
                frontmatter=fm, body=body, source=source, turn_id=turn_id,
            )
        else:
            # Library-scoped (override / library-*): write an override patch.
            from grimoire.state_store.paths import make_library_id

            library_id = make_library_id(world_id, holder_kind.value, holder_id)
            await self._store.write_override(
                campaign_id=campaign_id, library_id=library_id,
                patch={"inventory": section}, source=source, turn_id=turn_id,
            )

        await self._sync_derived(campaign_id, holder_kind, holder_id, entries)

    async def _sync_derived(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str,
        entries: list[InventoryEntry],
    ) -> None:
        await self._store.clear_holder_inventory(campaign_id, holder_kind.value, holder_id)
        for e in entries:
            await self._store.upsert_inventory_holding(
                campaign_id=campaign_id, holder_kind=holder_kind.value, holder_id=holder_id,
                item_ref=e.item_ref, item_name=e.item_name, quantity=e.quantity,
                fungible=e.fungible, equipped=e.equipped, provenance=e.provenance,
                notes=e.notes,
            )

    async def _world_id_for(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str
    ) -> str | None:
        """Find which world a library-defined holder belongs to, or None if emergent."""
        # Emergent holders have no world_id. Probe emergent first.
        emergent = await self._store.get_emergent(campaign_id, holder_kind.value, holder_id)
        if emergent is not None:
            return None
        rows = await self._store.db.fetchall(
            "SELECT world_id FROM campaign_world_refs WHERE campaign_id=? ORDER BY priority",
            (campaign_id,),
        )
        for r in rows:
            wid = r["world_id"]
            resolved = await self._store.resolve_entity(
                campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id, world_id=wid,
            )
            if resolved is not None:
                return wid
        # Default to the first world ref so override path is well-formed.
        return rows[0]["world_id"] if rows else None
```

> Implementation note: verify `make_library_id` exists in `state_store/paths.py` with signature `(world_id, kind, asset_id)`. The Task-2 explorer confirmed `resolve_entity` uses `make_library_id(world_id, kind, asset_id)` internally — reuse the same helper.

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_overlay_persistence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/inventory/persistence.py backend/tests/inventory/test_overlay_persistence.py
git commit -m "feat(inventory): overlay-section persistence with derived sync (#444)"
```

---

## Task 8: Inventory events

**Files:**
- Create: `backend/src/grimoire/inventory/events.py`
- Modify: `backend/src/grimoire/events.py`, `backend/src/grimoire/api/stream.py`
- Test: `backend/tests/inventory/test_events.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_events.py`:

```python
from grimoire import events as global_events
from grimoire.inventory import events as inv_events


def test_event_constants_exist():
    assert inv_events.INVENTORY_CHANGED == "inventory_changed"
    assert inv_events.INVENTORY_FLAGGED == "inventory_flagged"


def test_events_registered_globally():
    assert global_events.INVENTORY_CHANGED == "inventory_changed"
    assert global_events.INVENTORY_FLAGGED == "inventory_flagged"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_events.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the constants**

Create `backend/src/grimoire/inventory/events.py`:

```python
"""Inventory event-type constants."""

INVENTORY_CHANGED = "inventory_changed"
INVENTORY_FLAGGED = "inventory_flagged"
```

In `backend/src/grimoire/events.py`, add near the other domain events (e.g. after the continuity block):

```python
# Inventory (#444)
INVENTORY_CHANGED = "inventory_changed"
INVENTORY_FLAGGED = "inventory_flagged"
```

In `backend/src/grimoire/api/stream.py`, add both names to the `_FORWARDED_EVENTS` tuple (around line 30) so they relay to the frontend WS:

```python
    events.INVENTORY_CHANGED,
    events.INVENTORY_FLAGGED,
```

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/inventory/events.py backend/src/grimoire/events.py backend/src/grimoire/api/stream.py backend/tests/inventory/test_events.py
git commit -m "feat(inventory): event constants + WS forwarding (#444)"
```

---

## Task 9: InventoryService.apply()

The orchestration layer: resolve config, map `INVENTORY_CHANGE` deltas → `InventoryOperation`s, resolve item identity + holders, run the state machine in order over the touched holders' holdings, persist changed holders, record flags (low-confidence OR reconciled), and emit events.

**Files:**
- Create: `backend/src/grimoire/inventory/service.py`
- Modify: `backend/src/grimoire/inventory/__init__.py` (exports)
- Test: `backend/tests/inventory/test_service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_service.py`:

```python
import pytest

from grimoire.event_bus import EventBus
from grimoire.inventory import events as inv_events
from grimoire.inventory.models import HolderKind, InventoryAction, InventoryOperation
from grimoire.inventory.service import InventoryService

pytestmark = pytest.mark.asyncio


async def _enable(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    # Enable inventory in the campaign config block.
    await store.set_campaign_config("c1", {"inventory": {"enabled": True, "flag_threshold": 0.6}})


async def test_disabled_campaign_is_noop(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(action=InventoryAction.ACQUIRE, item="ring", holder="joe", confidence=1.0)
    res = await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    assert res is None  # disabled -> no-op
    assert await store.list_inventory_holdings("c1") == []


async def test_acquire_persists_and_emits(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"}, body="", source="test",
    )
    bus = EventBus()
    seen = []
    bus.subscribe(inv_events.INVENTORY_CHANGED, lambda e: seen.append(e))
    svc = InventoryService(store=store, event_bus=bus)
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="silver ring", holder="joe",
        quantity=1, confidence=0.95,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert rows and rows[0]["item_ref"] == "silver-ring"
    assert len(seen) == 1


async def test_low_confidence_records_flag(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"}, body="", source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="rusty key", holder="joe", confidence=0.2,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert len(flags) == 1
    assert flags[0]["flag_reason"] == "low_confidence"
```

> This test references `store.set_campaign_config(campaign_id, dict)`. If no such setter exists, add a thin one beside `get_campaign_config` in `store.py` (UPDATE campaigns SET config=? WHERE id=?), or set the config column directly via `store.db.execute`. Verify and adjust in Step 3.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_service.py -v`
Expected: FAIL — service missing.

- [ ] **Step 3: Implement the service**

Create `backend/src/grimoire/inventory/service.py`:

```python
"""InventoryService: deterministic application of inventory operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from grimoire.event_bus import Event, EventBus
from grimoire.types.state import DeltaKind, StateDelta

from . import events as inv_events
from .config import InventoryConfig
from .models import (
    FlaggedOp,
    FlagReason,
    HolderKind,
    InventoryAction,
    InventoryEntry,
    InventoryOperation,
)
from .persistence import InventoryPersistence
from .resolver import ItemResolver
from .state_machine import Holdings, ResolvedOp, apply_op


def deltas_to_operations(deltas: list[StateDelta]) -> list[InventoryOperation]:
    """Map INVENTORY_CHANGE deltas to typed operations (extraction order)."""
    ops: list[InventoryOperation] = []
    for d in deltas:
        if d.kind is not DeltaKind.INVENTORY_CHANGE:
            continue
        a = d.after or {}
        try:
            ops.append(
                InventoryOperation(
                    action=InventoryAction(a.get("action", "acquire")),
                    item=str(a.get("item", "")),
                    holder=str(a.get("holder", "")),
                    to=a.get("to"),
                    quantity=a.get("quantity"),
                    equipped=a.get("equipped"),
                    provenance=a.get("provenance"),
                    confidence=float(d.confidence),
                    source=d.source or "extractor",
                    evidence=d.evidence or "",
                )
            )
        except (ValueError, TypeError):
            continue
    return ops


class InventoryService:
    def __init__(
        self,
        *,
        store: Any,
        event_bus: EventBus,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._bus = event_bus
        self._clock = clock
        self._persist = InventoryPersistence(store)

    async def _config(self, campaign_id: str) -> InventoryConfig:
        return InventoryConfig.from_campaign_config(
            await self._store.get_campaign_config(campaign_id)
        )

    async def apply(
        self, *, campaign_id: str, turn_id: str | None, operations: list[InventoryOperation]
    ) -> dict | None:
        config = await self._config(campaign_id)
        if not config.enabled or not operations:
            return None

        resolver = ItemResolver(self._store, config)
        holdings: Holdings = {}
        touched: set[tuple[HolderKind, str]] = set()
        flags: list[FlaggedOp] = []

        for op in operations:
            holder_kind, holder_id = self._holder(op.holder)
            if holder_id is None:
                flags.append(FlaggedOp(op=op, reason=FlagReason.UNRESOLVED_HOLDER))
                continue
            item_ref, item_name, fungible = await resolver.resolve(
                campaign_id, op.item, turn_id=turn_id
            )
            to_kind, to_id = self._holder(op.to) if op.to else (None, None)

            await self._ensure_loaded(campaign_id, holdings, touched, holder_kind, holder_id)
            if to_id is not None and to_kind is not None:
                await self._ensure_loaded(campaign_id, holdings, touched, to_kind, to_id)

            resolved = ResolvedOp(
                action=op.action, item_ref=item_ref, item_name=item_name, fungible=fungible,
                holder_kind=holder_kind, holder_id=holder_id,
                to_kind=to_kind, to_id=to_id, quantity=op.quantity,
                equipped=op.equipped, provenance=op.provenance, acquired_in_post=turn_id,
            )
            step = apply_op(holdings, resolved)
            touched.add((holder_kind, holder_id))
            if to_id is not None and to_kind is not None:
                touched.add((to_kind, to_id))

            if step.flag is not None:
                flags.append(FlaggedOp(op=op, reason=step.flag))
            elif op.confidence < config.flag_threshold:
                flags.append(FlaggedOp(op=op, reason=FlagReason.LOW_CONFIDENCE))

        # Persist every touched holder (file SSOT + derived rows).
        for (hk, hid) in touched:
            entries = list(holdings.get((hk, hid), {}).values())
            await self._persist.write_holder_inventory(
                campaign_id=campaign_id, holder_kind=hk, holder_id=hid,
                entries=entries, source="inventory", turn_id=turn_id,
            )

        await self._record_flags(campaign_id, turn_id, flags)

        await self._bus.emit(
            Event(type=inv_events.INVENTORY_CHANGED,
                  payload={"campaign_id": campaign_id, "turn_id": turn_id,
                           "holders": [{"kind": k.value, "id": i} for (k, i) in touched]})
        )
        if flags:
            await self._bus.emit(
                Event(type=inv_events.INVENTORY_FLAGGED,
                      payload={"campaign_id": campaign_id, "turn_id": turn_id,
                               "count": len(flags)})
            )
        return {"touched": len(touched), "flags": len(flags)}

    async def _ensure_loaded(
        self, campaign_id: str, holdings: Holdings, touched: set,
        kind: HolderKind, hid: str,
    ) -> None:
        if (kind, hid) in holdings:
            return
        entries = await self._persist.read_holder_inventory(campaign_id, kind, hid)
        holdings[(kind, hid)] = {e.item_ref: e for e in entries}

    async def _record_flags(
        self, campaign_id: str, turn_id: str | None, flags: list[FlaggedOp]
    ) -> None:
        now = self._clock().isoformat()
        for f in flags:
            await self._store.record_inventory_flag(
                campaign_id=campaign_id, turn_id=turn_id,
                op_json=f.op.model_dump_json(), flag_reason=f.reason.value, created_at=now,
            )

    @staticmethod
    def _holder(ref: str | None) -> tuple[HolderKind | None, str | None]:
        """Resolve a holder ref to (kind, id). Locations are detected by a
        'location:' prefix or fall back to character. Refs may be ids or
        'library:.../characters/<id>' composite refs — take the trailing id."""
        if not ref:
            return None, None
        raw = ref.strip()
        kind = HolderKind.CHARACTER
        if raw.startswith("location:") or "/locations/" in raw:
            kind = HolderKind.LOCATION
        hid = raw.split("/")[-1].split(":")[-1]
        return kind, (hid or None)
```

Update `backend/src/grimoire/inventory/__init__.py`:

```python
"""Deterministic inventory subsystem (#444)."""

from .config import InventoryConfig
from .service import InventoryService, deltas_to_operations

__all__ = ["InventoryConfig", "InventoryService", "deltas_to_operations"]
```

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_service.py -v`
Expected: PASS (all three tests). If `set_campaign_config` is missing, add it per the Step-1 note, then re-run.

- [ ] **Step 5: Run the whole inventory suite**

Run: `cd backend && uv run pytest tests/inventory/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/inventory/service.py backend/src/grimoire/inventory/__init__.py backend/tests/inventory/test_service.py backend/src/grimoire/state_store/store.py
git commit -m "feat(inventory): InventoryService deterministic apply pipeline (#444)"
```

---

## Task 10: Extractor — typed operations

Replace the freeform inventory delta with the typed-op shape in the JSON schema and both strategy builders, then update existing extractor tests.

**Files:**
- Modify: `backend/src/grimoire/extractor/schema.py:99-110`
- Modify: `backend/src/grimoire/extractor/llm_strategy.py:255-272`
- Modify: `backend/src/grimoire/extractor/rule_based.py:208-234`
- Modify tests: `backend/tests/extractor/test_rule_based.py`, `test_llm_strategy.py`, `test_service.py`, `backend/tests/integration/test_golden_extractor.py`

- [ ] **Step 1: Update the JSON schema**

In `backend/src/grimoire/extractor/schema.py`, replace the `inventory_change` object (lines 99-110) with:

```python
    inventory_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["acquire", "drop", "transfer", "consume",
                         "adjust", "equip", "unequip"],
            },
            "item": {"type": "string"},
            "holder": {"type": "string"},
            "to": {"type": ["string", "null"]},
            "quantity": {"type": ["integer", "null"]},
            "equipped": {"type": ["boolean", "null"]},
            "provenance": {"type": ["string", "null"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["action", "item", "holder", "confidence"],
    }
```

- [ ] **Step 2: Update the LLM strategy builder**

In `backend/src/grimoire/extractor/llm_strategy.py`, replace `_make_inventory_delta` (lines 255-272) with:

```python
def _make_inventory_delta(item: dict, *, campaign_id: CampaignId, source: str) -> StateDelta:
    holder = item.get("holder", "unknown")
    action = item.get("action", "acquire")
    return StateDelta(
        kind=DeltaKind.INVENTORY_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{holder}:{action}:{item.get('item', '')}",
        after={
            "action": action,
            "item": item.get("item"),
            "holder": holder,
            "to": item.get("to"),
            "quantity": item.get("quantity"),
            "equipped": item.get("equipped"),
            "provenance": item.get("provenance"),
            "campaign_id": campaign_id,
        },
        confidence=float(item.get("confidence", 0.0)),
        source=source,
        evidence=str(item.get("evidence", "")),
        extra={"strategy": "structured_llm", "category": "inventory_changes"},
    )
```

(Remove the now-unused `target_table="character_state"` — inventory deltas are routed to InventoryService, not a generic table.)

- [ ] **Step 3: Update the rule-based builder**

In `backend/src/grimoire/extractor/rule_based.py`, replace `_make_inventory_delta` (lines 208-234) with a typed-op shape. Map `direction` to an action (`gain` → `acquire`, `loss` → `drop`):

```python
def _make_inventory_delta(
    *,
    actor: str,
    item: str,
    direction: str,  # 'gain' | 'loss'
    evidence: str,
    confidence: float,
    campaign_id: CampaignId,
    source: str,
) -> StateDelta:
    action = "acquire" if direction == "gain" else "drop"
    return StateDelta(
        kind=DeltaKind.INVENTORY_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"{actor.lower()}:{action}:{item.strip().lower()}",
        after={
            "action": action,
            "item": item.strip(),
            "holder": actor,
            "quantity": 1,
            "campaign_id": campaign_id,
        },
        confidence=confidence,
        source=source,
        evidence=evidence,
        extra={"strategy": "rule_based", "direction": direction},
    )
```

- [ ] **Step 4: Update existing extractor tests to the new shape**

In `backend/tests/extractor/test_rule_based.py`, update the two inventory assertions:

```python
def test_inventory_pick_up_emits_inventory_change():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based("winifred picked up the silver ring.", campaign_id="c", config=config)
    )
    inv = [d for d in deltas if d.kind == DeltaKind.INVENTORY_CHANGE]
    assert len(inv) == 1
    assert inv[0].after["item"].strip() == "silver ring"
    assert inv[0].after["action"] == "acquire"
    assert inv[0].after["holder"] == "winifred"
    assert inv[0].confidence == 0.8


def test_handed_emits_loss_direction():
    config = ExtractorConfig()
    deltas = list(
        extract_rule_based("vivienne handed the silver ring to julian.", campaign_id="c", config=config)
    )
    inv = [d for d in deltas if d.kind == DeltaKind.INVENTORY_CHANGE]
    assert inv and inv[0].after["action"] == "drop"
```

In `backend/tests/extractor/test_llm_strategy.py`, update the golden payload (lines ~53-60) and any assertion to the typed shape:

```python
        "inventory_changes": [
            {"action": "acquire", "item": "silver ring", "holder": "julian", "quantity": 1, "confidence": 0.95}
        ],
```

Find the assertion that checks the resulting delta and update it to assert `after["action"] == "acquire"` and `after["holder"] == "julian"` instead of `after["delta"]`.

In `backend/tests/extractor/test_service.py`, the inventory assertion (line ~70) only checks `d.kind == DeltaKind.INVENTORY_CHANGE` — leave the kind check but, if it asserts on `after["delta"]`, change to `after["action"]`.

In `backend/tests/integration/test_golden_extractor.py` (line ~64), the fixture is recorded LLM text containing `"inventory_change"`. If the golden fixture's JSON uses the old `{"item","action"}` form it still contains `inventory_change`; the assertion `assert "inventory_change" in response.text` stays valid. If the test asserts a parsed delta field, update it to the new shape. Re-record the golden fixture only if the assertion parses fields (run with the fixture-record flag the repo uses — check `tests/integration/conftest.py`).

- [ ] **Step 5: Run extractor tests**

Run: `cd backend && uv run pytest tests/extractor/ tests/integration/test_golden_extractor.py -v`
Expected: PASS. Fix any remaining old-shape assertions surfaced by failures.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/extractor/ backend/tests/extractor/ backend/tests/integration/test_golden_extractor.py
git commit -m "feat(extractor): typed inventory operations (#444)"
```

---

## Task 11: Orchestrator + bootstrap wiring

Wire `InventoryService` into the DI container and have the orchestrator call it after `apply_routing`. Inventory deltas are routed to the service (not the generic table).

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py` (constructor + apply call)
- Modify: `backend/src/grimoire/orchestrator/delta_applier.py` (skip generic apply for INVENTORY_CHANGE)
- Modify: `backend/src/grimoire/bootstrap.py` (construct + pass to orchestrator)
- Test: `backend/tests/integration/test_inventory_pipeline.py`

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/integration/test_inventory_pipeline.py`:

```python
import pytest

from grimoire.event_bus import EventBus
from grimoire.inventory.models import InventoryAction, InventoryOperation
from grimoire.inventory.service import InventoryService, deltas_to_operations
from grimoire.types.state import DeltaKind, StateDelta
from grimoire.types.common import Scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_deltas_to_operations_filters_and_maps():
    deltas = [
        StateDelta(kind=DeltaKind.FACT_ADD, target_scope=Scope.CAMPAIGN_SQLITE, target_id="x"),
        StateDelta(
            kind=DeltaKind.INVENTORY_CHANGE, target_scope=Scope.CAMPAIGN_SQLITE, target_id="y",
            after={"action": "transfer", "item": "ring", "holder": "flo", "to": "julian", "quantity": 1},
            confidence=0.9,
        ),
    ]
    ops = deltas_to_operations(deltas)
    assert len(ops) == 1
    assert ops[0].action is InventoryAction.TRANSFER
    assert ops[0].to == "julian"


async def test_pipeline_applies_inventory(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.set_campaign_config("c1", {"inventory": {"enabled": True}})
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="flo",
        frontmatter={"id": "flo", "name": "Flo"}, body="", source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(action=InventoryAction.ACQUIRE, item="ring", holder="flo", confidence=1.0)
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    rows = await store.list_inventory_holdings("c1", item_ref="ring")
    assert len(rows) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_inventory_pipeline.py -v`
Expected: `test_deltas_to_operations_filters_and_maps` PASSES (pure), `test_pipeline_applies_inventory` may PASS already (service standalone). Both should pass — this test locks behavior before wiring. If both pass, proceed; the wiring steps below add the orchestrator path.

- [ ] **Step 3: Add the orchestrator constructor parameter**

In `backend/src/grimoire/orchestrator/service.py`, in `OrchestratorService.__init__` (around line 171-193), add a parameter and store it:

```python
        inventory: Any | None = None,
```

and in the body (near `self._transient_state = transient_state`):

```python
        self._inventory = inventory
```

- [ ] **Step 4: Call inventory after apply_routing**

In `backend/src/grimoire/orchestrator/service.py`, in `_continue_turn_after_pre_roll`, immediately after the `applied_ids, queued_ids = await self._delta.apply_routing(...)` call (around line 1049) and before the transient_state block (~1058), add:

```python
        if self._inventory is not None and extraction is not None:
            try:
                from grimoire.inventory.service import deltas_to_operations

                ops = deltas_to_operations(list(extraction.deltas))
                if ops:
                    await self._inventory.apply(
                        campaign_id=campaign_id, turn_id=turn_id, operations=ops
                    )
            except Exception:
                logger.exception("inventory apply failed; continuing turn")
```

- [ ] **Step 5: Skip generic apply for INVENTORY_CHANGE in delta_applier**

In `backend/src/grimoire/orchestrator/delta_applier.py`, in the `apply_routing` auto-deltas loop (after the continuity block, ~line 230, before the generic `apply_delta` at line 231), add:

```python
            if delta.kind == DeltaKind.INVENTORY_CHANGE:
                # Inventory deltas are applied by InventoryService after routing.
                # Persist the delta row for audit/undo but skip generic table apply.
                continue
```

> Verify `DeltaKind` is imported in `delta_applier.py`. The delta itself is still recorded by the orchestrator's audit/delta-log path; if `apply_routing` is the only place deltas get persisted, instead of `continue` call `await self._store.record_delta_only(delta, ...)` if such a method exists. Check whether skipping here drops the audit row; if so, keep routing it through `apply_delta` (it will write a harmless row to the deltas table) — the InventoryService still owns the real state. Decide based on whether a duplicate/no-op row is acceptable; default to `continue` (no generic apply) since the file-delta from InventoryService is the reversible record.

- [ ] **Step 6: Construct InventoryService in bootstrap**

In `backend/src/grimoire/bootstrap.py`, in Phase 1 (after continuity is constructed, ~line 246), add:

```python
    if container.inventory is None:
        from grimoire.inventory import InventoryService

        container.inventory = InventoryService(
            store=container.state_store,
            event_bus=container.event_bus,
        )
```

Add an `inventory` attribute to the DI container dataclass/object (find where `time_engine`, `continuity` etc. are declared as container fields and add `inventory: Any | None = None` alongside them).

In Phase 3, where `OrchestratorService(...)` is constructed (~line 507-521), add the argument:

```python
        inventory=container.inventory,
```

- [ ] **Step 7: Run the integration test + a smoke import**

Run: `cd backend && uv run pytest tests/integration/test_inventory_pipeline.py -v`
Expected: PASS.
Run: `cd backend && uv run python -c "import grimoire.bootstrap"`
Expected: no import error.

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/orchestrator/ backend/src/grimoire/bootstrap.py backend/tests/integration/test_inventory_pipeline.py
git commit -m "feat(inventory): wire InventoryService into orchestrator + bootstrap (#444)"
```

---

## Task 12: Watcher rebuild — repopulate derived table from files

When SQLite is rebuilt (or a campaign overlay file is edited externally), repopulate `inventory_holdings` from each overlay file's `inventory:` section.

**Files:**
- Modify: `backend/src/grimoire/watcher/watcher.py`
- Test: `backend/tests/inventory/test_rebuild.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_rebuild.py`:

```python
import pytest

from grimoire.inventory.models import HolderKind, InventoryEntry
from grimoire.inventory.persistence import InventoryPersistence

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_rebuild_repopulates_holdings_from_files(store, file_watcher):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"}, body="", source="test",
    )
    p = InventoryPersistence(store)
    await p.write_holder_inventory(
        campaign_id="c1", holder_kind=HolderKind.CHARACTER, holder_id="joe",
        entries=[InventoryEntry(item_ref="ring", item_name="Ring", quantity=3)],
        source="inventory", turn_id=None,
    )
    # Simulate DB loss of the derived rows.
    await store.db.execute("DELETE FROM inventory_holdings")
    assert await store.list_inventory_holdings("c1") == []

    # Rebuild from files.
    await file_watcher.scan_now(scope="campaigns")

    rows = await store.list_inventory_holdings("c1", item_ref="ring")
    assert len(rows) == 1 and rows[0]["quantity"] == 3
```

> This needs a `file_watcher` fixture wiring a `FileWatcher` to the same `store`+`data_root`. If one doesn't exist in `tests/integration/conftest.py` or `tests/state_store/conftest.py`, add it there: construct `FileWatcher` the way `bootstrap.py` does (lines ~571-580) pointing at `store` and `store.data_root`.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_rebuild.py -v`
Expected: FAIL — derived rows not rebuilt after delete.

- [ ] **Step 3: Add the rebuild hook**

In `backend/src/grimoire/watcher/watcher.py`, in `scan_now` after `_drop_orphan_content_rows(...)` and before emitting `LIBRARY_INDEXED` (around line 356-363), add a campaign-scoped inventory rebuild:

```python
        if do_campaigns:
            await self._rebuild_inventory_holdings(seen_content)
```

Add the method to the `FileWatcher` class:

```python
    async def _rebuild_inventory_holdings(self, seen_content: set[str]) -> None:
        """Repopulate inventory_holdings from `inventory:` sections in overlay/
        emergent files. Source of truth is the file; the table is derived."""
        import json

        # Find indexed campaign entities that carry an inventory section.
        rows = await self.store.db.fetchall(
            "SELECT campaign_id, entity_subkind, asset_id, frontmatter "
            "FROM campaign_content_index "
            "WHERE entity_subkind IN ('character', 'location') AND frontmatter LIKE '%\"inventory\"%'"
        )
        rebuilt_holders: set[tuple[str, str, str]] = set()
        for r in rows:
            fm = json.loads(r["frontmatter"]) if r["frontmatter"] else {}
            block = fm.get("inventory") or {}
            entries = block.get("entries") or []
            cid, kind, hid = r["campaign_id"], r["entity_subkind"], r["asset_id"]
            await self.store.clear_holder_inventory(cid, kind, hid)
            rebuilt_holders.add((cid, kind, hid))
            for e in entries:
                await self.store.upsert_inventory_holding(
                    campaign_id=cid, holder_kind=kind, holder_id=hid,
                    item_ref=e["item_ref"], item_name=e.get("item_name", e["item_ref"]),
                    quantity=int(e.get("quantity", 1)), fungible=bool(e.get("fungible", False)),
                    equipped=bool(e.get("equipped", False)),
                    provenance=e.get("provenance"), notes=e.get("notes"),
                )
```

> Note: `self.store` is the FileWatcher's StateStore reference — confirm the attribute name (the explorer showed the watcher calls `self.store.bulk_load_index_mtimes()`; reuse that exact attribute). For override files the override patch's frontmatter is indexed merged or raw — verify `campaign_content_index.frontmatter` for an override row contains the `inventory` key (it stores the override patch dict, which includes `inventory`). If override rows store only the patch (they do), the `LIKE '%"inventory"%'` filter still matches.

- [ ] **Step 4: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_rebuild.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/watcher/watcher.py backend/tests/inventory/test_rebuild.py
git commit -m "feat(inventory): rebuild derived holdings from files on scan (#444)"
```

---

## Task 13: REST API router

**Files:**
- Create: `backend/src/grimoire/api/campaigns/inventory.py`
- Modify: wherever campaign routers are registered (find the module that does `app.include_router` / collects campaign routers — search for an existing campaign router like `sheets.py` registration and mirror it)
- Test: `backend/tests/inventory/test_api.py`

- [ ] **Step 1: Write the failing API test**

Create `backend/tests/inventory/test_api.py`:

```python
import pytest

pytestmark = [pytest.mark.asyncio]


async def test_get_inventory_disabled_returns_409(client, seeded_campaign):
    resp = await client.get(f"/api/campaigns/{seeded_campaign}/inventory")
    assert resp.status_code == 409


async def test_get_inventory_enabled_lists_holdings(client, seeded_campaign, enable_inventory):
    await enable_inventory(seeded_campaign)
    # Seed a holding directly via state store fixture helper.
    resp = await client.get(f"/api/campaigns/{seeded_campaign}/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert "holders" in body
```

> Use the existing API test fixtures (`client`, plus a seeded campaign). Find them in `backend/tests/scenario/conftest.py` or `backend/tests/api/conftest.py`; mirror an existing campaign-scoped API test (e.g. for sheets or commitments) for the `client` and seeding fixtures, and add `enable_inventory` and `seeded_campaign` helpers there if absent.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_api.py -v`
Expected: FAIL — route missing (404).

- [ ] **Step 3: Implement the router**

Create `backend/src/grimoire/api/campaigns/inventory.py` (mirror the structure of a sibling router such as `sheets.py` — same `APIRouter` prefix style, dependency injection of the container/state store, and error conventions):

```python
"""Inventory REST API (#444). Mounted under /api/campaigns/{campaign_id}/inventory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from grimoire.inventory.config import InventoryConfig
from grimoire.inventory.models import InventoryOperation

router = APIRouter()


async def _require_enabled(store, campaign_id: str) -> InventoryConfig:
    cfg = InventoryConfig.from_campaign_config(await store.get_campaign_config(campaign_id))
    if not cfg.enabled:
        raise HTTPException(status_code=409, detail="feature_disabled")
    return cfg


@router.get("/campaigns/{campaign_id}/inventory")
async def get_inventory(campaign_id: str, store=Depends(...), item_ref: str | None = None):
    await _require_enabled(store, campaign_id)
    rows = await store.list_inventory_holdings(campaign_id, item_ref=item_ref)
    holders: dict[str, list] = {}
    for r in rows:
        holders.setdefault(f"{r['holder_kind']}:{r['holder_id']}", []).append(r)
    return {"holders": [{"holder": k, "entries": v} for k, v in holders.items()]}


@router.get("/campaigns/{campaign_id}/inventory/holders/{kind}/{holder_id}")
async def get_holder(campaign_id: str, kind: str, holder_id: str, store=Depends(...)):
    await _require_enabled(store, campaign_id)
    rows = await store.list_inventory_holdings(campaign_id, holder_kind=kind, holder_id=holder_id)
    return {"holder": f"{kind}:{holder_id}", "entries": rows}


@router.post("/campaigns/{campaign_id}/inventory/operations")
async def submit_operation(campaign_id: str, op: InventoryOperation, inventory=Depends(...)):
    op = op.model_copy(update={"source": "user", "confidence": 1.0})
    result = await inventory.apply(campaign_id=campaign_id, turn_id=None, operations=[op])
    if result is None:
        raise HTTPException(status_code=409, detail="feature_disabled")
    return result


@router.get("/campaigns/{campaign_id}/inventory/flags")
async def list_flags(campaign_id: str, resolved: bool = False, store=Depends(...)):
    await _require_enabled(store, campaign_id)
    return {"flags": await store.list_inventory_flags(campaign_id, resolved=resolved)}


@router.post("/campaigns/{campaign_id}/inventory/flags/{flag_id}/resolve")
async def resolve_flag(campaign_id: str, flag_id: str, store=Depends(...)):
    await _require_enabled(store, campaign_id)
    await store.resolve_inventory_flag(campaign_id, flag_id)
    return {"ok": True}
```

> Replace `Depends(...)` with the project's real dependency providers. Find how `sheets.py` injects the state store and services (e.g. a `get_state_store` / `get_container` dependency) and use the same providers. The `inventory` service must be reachable from a dependency — add a `get_inventory_service` provider mirroring the existing service providers, returning `container.inventory`.

- [ ] **Step 4: Register the router**

Mount `inventory.router` alongside the other campaign routers (find where `sheets.router` / `entities.router` are included and add `app.include_router(inventory.router, prefix="/api")` matching the existing prefix pattern).

- [ ] **Step 5: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/api/campaigns/inventory.py backend/tests/inventory/test_api.py
git commit -m "feat(inventory): REST API router (#444)"
```

---

## Task 14: HUD widget descriptor + read endpoint

**Files:**
- Modify: `backend/src/grimoire/hud/widgets.py`
- Modify: `backend/src/grimoire/api/campaigns/inventory.py` (add HUD-shaped read endpoint)
- Test: `backend/tests/inventory/test_hud_widget.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/inventory/test_hud_widget.py`:

```python
from grimoire.hud.widgets import core_widget_by_id, core_widget_ids


def test_inventory_widget_registered():
    assert "core.inventory" in core_widget_ids()
    w = core_widget_by_id("core.inventory")
    assert w is not None
    assert "inventory_changed" in w.refresh_on
    assert w.owner_module == "inventory"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/inventory/test_hud_widget.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the descriptor**

In `backend/src/grimoire/hud/widgets.py`, append to the `CORE_WIDGETS` list (before the closing `]`):

```python
    HudWidget(
        id="core.inventory",
        title="Inventory",
        scope=WidgetScope.SCENE,
        visible_when="true",
        read=WidgetRead(endpoint="/campaigns/{id}/inventory/hud?scene_id={sid}"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=["inventory_changed", "turn_complete", "scene_started"],
        owner_module="inventory",
    ),
```

> Verify the `WidgetRead.endpoint` templating tokens the HUD aggregator supports (`{id}`, `{sid}`). The explorer showed `/scenes/{sid}/...` and `/campaigns/{id}/...` forms — match exactly. If scene-scoped filtering isn't needed for v1, drop the `?scene_id={sid}` and show all holders.

- [ ] **Step 4: Add the HUD-shaped read endpoint**

In `backend/src/grimoire/api/campaigns/inventory.py`, add:

```python
@router.get("/campaigns/{campaign_id}/inventory/hud")
async def inventory_hud(campaign_id: str, scene_id: str | None = None, store=Depends(...)):
    cfg = InventoryConfig.from_campaign_config(await store.get_campaign_config(campaign_id))
    if not cfg.enabled:
        return {"status": "hidden", "data": []}
    rows = await store.list_inventory_holdings(campaign_id)
    grouped: dict[str, list[str]] = {}
    for r in rows:
        label = f"{r['holder_kind']}:{r['holder_id']}"
        qty = f" ×{r['quantity']}" if r["quantity"] != 1 else ""
        eq = " (equipped)" if r["equipped"] else ""
        grouped.setdefault(label, []).append(f"{r['item_name']}{qty}{eq}")
    data = [{"label": k, "items": v} for k, v in grouped.items()]
    return {"status": "ok", "data": data}
```

> Match the exact response shape the BLOCK renderer expects (`{status, data, ...}`). Check `hud/service.py` / the `WidgetSnapshot` contract and an existing BLOCK endpoint (e.g. `/scenes/{sid}/recent-facts`) and mirror its payload shape so `BlockWidget.tsx` renders without changes.

- [ ] **Step 5: Run to verify passing**

Run: `cd backend && uv run pytest tests/inventory/test_hud_widget.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/hud/widgets.py backend/src/grimoire/api/campaigns/inventory.py backend/tests/inventory/test_hud_widget.py
git commit -m "feat(inventory): HUD widget descriptor + read endpoint (#444)"
```

---

## Task 15: Frontend — API client + Zod types

**Files:**
- Create: `frontend/src/api/inventory.ts`
- Test: `frontend/src/api/__tests__/inventory.test.ts` (if api tests live elsewhere, mirror that location)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/__tests__/inventory.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { InventoryHoldingSchema } from "../inventory";

describe("InventoryHoldingSchema", () => {
  it("parses a holding row", () => {
    const row = {
      item_ref: "ring",
      item_name: "Ring",
      quantity: 2,
      fungible: false,
      equipped: false,
      holder_kind: "character",
      holder_id: "flo",
    };
    expect(() => InventoryHoldingSchema.parse(row)).not.toThrow();
  });

  it("rejects a row missing item_ref", () => {
    expect(() => InventoryHoldingSchema.parse({ item_name: "x", quantity: 1 })).toThrow();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test inventory`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the client**

Create `frontend/src/api/inventory.ts` (mirror `frontend/src/api/campaign/api.ts` conventions — `api.get/post`, Zod schemas):

```typescript
import { z } from "zod";
import { api } from "./client";

export const InventoryHoldingSchema = z.object({
  item_ref: z.string(),
  item_name: z.string(),
  quantity: z.number(),
  fungible: z.boolean().default(false),
  equipped: z.boolean().default(false),
  provenance: z.string().nullable().optional(),
  notes: z.string().nullable().optional(),
  holder_kind: z.string().optional(),
  holder_id: z.string().optional(),
});
export type InventoryHolding = z.infer<typeof InventoryHoldingSchema>;

export const InventoryFlagSchema = z.object({
  id: z.string(),
  turn_id: z.string().nullable(),
  op_json: z.string(),
  flag_reason: z.string(),
  resolved: z.number(),
  created_at: z.string(),
});
export type InventoryFlag = z.infer<typeof InventoryFlagSchema>;

const enc = encodeURIComponent;

export const inventoryApi = {
  list: (campaignId: string) =>
    api.get<{ holders: { holder: string; entries: InventoryHolding[] }[] }>(
      `/api/campaigns/${enc(campaignId)}/inventory`,
    ),
  flags: (campaignId: string, resolved = false) =>
    api.get<{ flags: InventoryFlag[] }>(
      `/api/campaigns/${enc(campaignId)}/inventory/flags?resolved=${resolved}`,
      { schema: z.object({ flags: z.array(InventoryFlagSchema) }) },
    ),
  resolveFlag: (campaignId: string, flagId: string) =>
    api.post<{ ok: boolean }>(
      `/api/campaigns/${enc(campaignId)}/inventory/flags/${enc(flagId)}/resolve`,
    ),
  submitOperation: (campaignId: string, op: unknown) =>
    api.post<{ touched: number; flags: number }>(
      `/api/campaigns/${enc(campaignId)}/inventory/operations`,
      op,
    ),
};
```

- [ ] **Step 4: Run to verify passing**

Run: `cd frontend && pnpm test inventory`
Expected: PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

```bash
git add frontend/src/api/inventory.ts frontend/src/api/__tests__/inventory.test.ts
git commit -m "feat(inventory): frontend API client + Zod schemas (#444)"
```

---

## Task 16: Frontend — flagged-ops review panel

The basic inventory list renders via the existing BLOCK widget (no component needed). The flagged-ops panel needs interactivity (Confirm/Undo), so it is a dedicated component, refreshed live via `useCampaignEvent("inventory_flagged", ...)`.

**Files:**
- Create: `frontend/src/routes/campaign/SideHud/widgets/InventoryFlagsPanel.tsx`
- Create: `frontend/src/routes/campaign/SideHud/__tests__/InventoryFlagsPanel.test.tsx`
- Modify: `frontend/src/routes/campaign/SideHud/SideHud.tsx` (add `core.inventory` to `CORE_WIDGET_IDS`; mount the panel)

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/routes/campaign/SideHud/__tests__/InventoryFlagsPanel.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { InventoryFlagsList } from "../widgets/InventoryFlagsPanel";

describe("InventoryFlagsList", () => {
  it("renders flag reasons and a resolve button", () => {
    const flags = [
      { id: "f1", turn_id: "t1", op_json: '{"action":"drop","item":"dagger"}',
        flag_reason: "reconciled_missing_item", resolved: 0, created_at: "2026-05-28T00:00:00Z" },
    ];
    render(<InventoryFlagsList flags={flags} onResolve={vi.fn()} />);
    expect(screen.getByText(/reconciled_missing_item/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resolve/i })).toBeInTheDocument();
  });

  it("calls onResolve with the flag id", async () => {
    const onResolve = vi.fn();
    const flags = [
      { id: "f1", turn_id: null, op_json: "{}", flag_reason: "low_confidence",
        resolved: 0, created_at: "2026-05-28T00:00:00Z" },
    ];
    render(<InventoryFlagsList flags={flags} onResolve={onResolve} />);
    screen.getByRole("button", { name: /resolve/i }).click();
    expect(onResolve).toHaveBeenCalledWith("f1");
  });

  it("renders nothing when there are no flags", () => {
    const { container } = render(<InventoryFlagsList flags={[]} onResolve={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test InventoryFlagsPanel`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the component**

Create `frontend/src/routes/campaign/SideHud/widgets/InventoryFlagsPanel.tsx`:

```typescript
import { useCallback, useEffect, useState } from "react";

import { inventoryApi, type InventoryFlag } from "../../../../api/inventory";
import { useCampaignEvent } from "../../../../state/useCampaignEvent";

export function InventoryFlagsList({
  flags,
  onResolve,
}: {
  flags: InventoryFlag[];
  onResolve: (id: string) => void;
}) {
  if (flags.length === 0) return null;
  return (
    <ul className="inventory-flags">
      {flags.map((f) => (
        <li key={f.id}>
          <span className="flag-reason">{f.flag_reason}</span>
          <code className="flag-op">{f.op_json}</code>
          <button type="button" onClick={() => onResolve(f.id)}>
            Resolve
          </button>
        </li>
      ))}
    </ul>
  );
}

export function InventoryFlagsPanel({ campaignId }: { campaignId: string }) {
  const [flags, setFlags] = useState<InventoryFlag[]>([]);

  const refresh = useCallback(() => {
    void inventoryApi
      .flags(campaignId, false)
      .then((r) => setFlags(r.flags))
      .catch(() => setFlags([]));
  }, [campaignId]);

  useEffect(refresh, [refresh]);
  useCampaignEvent("inventory_flagged", refresh);

  const onResolve = useCallback(
    (id: string) => {
      void inventoryApi.resolveFlag(campaignId, id).then(refresh);
    },
    [campaignId, refresh],
  );

  if (flags.length === 0) return null;
  return (
    <section aria-label="Inventory review" className="inventory-flags-panel">
      <h3>Inventory review ({flags.length})</h3>
      <InventoryFlagsList flags={flags} onResolve={onResolve} />
    </section>
  );
}
```

- [ ] **Step 4: Wire into SideHud**

In `frontend/src/routes/campaign/SideHud/SideHud.tsx`:
- Add `"core.inventory"` to the `CORE_WIDGET_IDS` set (line ~59-68).
- Import and mount `<InventoryFlagsPanel campaignId={campaignId} />` in the HUD body (near where other campaign-scoped panels render). Confirm `campaignId` is in scope there (the explorer showed `renderWidget(..., campaignId)` is threaded through, so it is).

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && pnpm test InventoryFlagsPanel`
Expected: PASS (3 tests).
Run: `cd frontend && pnpm typecheck && pnpm lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/campaign/SideHud/
git commit -m "feat(inventory): HUD flagged-ops review panel (#444)"
```

---

## Task 17: Scenario test — full HTTP path

**Files:**
- Test: `backend/tests/scenario/test_inventory_scenario.py`

- [ ] **Step 1: Write the scenario test**

Create `backend/tests/scenario/test_inventory_scenario.py` (mirror an existing scenario test's app/client setup):

```python
import pytest

pytestmark = [pytest.mark.scenario, pytest.mark.asyncio]


async def test_inventory_end_to_end(scenario_app):
    client = scenario_app.client
    campaign_id = await scenario_app.create_campaign(inventory={"enabled": True})

    # Submit a manual operation: a PC acquires gold.
    op = {"action": "acquire", "item": "120 gold", "holder": "pc-alice", "confidence": 1.0}
    r = await client.post(f"/api/campaigns/{campaign_id}/inventory/operations", json=op)
    assert r.status_code == 200

    # Read it back.
    r = await client.get(f"/api/campaigns/{campaign_id}/inventory")
    body = r.json()
    refs = [e["item_ref"] for h in body["holders"] for e in h["entries"]]
    assert "resource:gold" in refs

    # Transfer to another holder.
    op2 = {"action": "transfer", "item": "120 gold", "holder": "pc-alice",
           "to": "pc-bob", "quantity": 20, "confidence": 1.0}
    r = await client.post(f"/api/campaigns/{campaign_id}/inventory/operations", json=op2)
    assert r.status_code == 200

    r = await client.get(f"/api/campaigns/{campaign_id}/inventory/holders/character/pc-bob")
    assert any(e["item_ref"] == "resource:gold" for e in r.json()["entries"])
```

> Adapt `scenario_app` / `create_campaign` to the repo's actual scenario fixtures (find them in `backend/tests/scenario/conftest.py`). If creating a campaign with an inventory config isn't supported by the helper, create the campaign then `POST`/patch its config, or set it via the state store before the HTTP calls.

- [ ] **Step 2: Run the scenario test**

Run: `cd backend && uv run pytest tests/scenario/test_inventory_scenario.py -v -m scenario`
Expected: PASS. Fix fixture mismatches surfaced by failures.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/scenario/test_inventory_scenario.py
git commit -m "test(inventory): end-to-end scenario (#444)"
```

---

## Task 18: Documentation

**Files:**
- Modify: `CLAUDE.md`, `AGENTS.md`, `README.md`

- [ ] **Step 1: Update module tables**

In `CLAUDE.md`: add `inventory/` to the "one package per domain module" list under Repository Layout, and add a row to the Module Ownership table:

```
| Inventory | Holdings, operations | Deterministic item/resource tracking per holder |
```

Do the same in `AGENTS.md` (mirror the table there).

- [ ] **Step 2: Note the per-campaign toggle**

In `CLAUDE.md`, under the campaign config / Data Directory section, document the `inventory:` block in `campaign.yaml`:

```yaml
inventory:
  enabled: true            # default false (opt-in)
  flag_threshold: 0.6      # ops below this confidence are applied but flagged
  fungible_resources: [gold, silver, arrows, rations, torches]
```

- [ ] **Step 3: README feature list**

In `README.md`, add inventory to the feature list (one line describing toggleable deterministic inventory tracking).

- [ ] **Step 4: Run the full backend suite + lint/format**

Run: `cd backend && uv run pytest -q`
Expected: PASS (all, including new inventory tests).
Run: `cd backend && uv run ruff check && uv run ruff format --check`
Expected: clean. Run `uv run ruff format` if needed and re-stage.
Run: `cd frontend && pnpm test && pnpm typecheck && pnpm lint`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md AGENTS.md README.md
git commit -m "docs(inventory): module table, toggle, feature list (#444)"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Tasks map to spec sections — storage (T1, T6, T7, T12), models (T2), determinism/state machine (T3), resolver/emergent items (T5), config/toggle (T4), extractor typed ops (T10), pipeline/undo (T11), events/WS (T8), API (T13), HUD widget (T14), frontend client + panel (T15, T16), scenario (T17), docs (T18). Error handling (spec §"Error Handling") is realized by the flag reasons in T3/T9 and the `try/except` in T11 step 4 and T9.
- **Determinism + undo:** The state machine (T3) is pure. Reversibility comes from `write_override`/`write_emergent` recording file deltas through the existing delta-log (verify those methods record a reversible delta — the explorer confirmed `write_emergent` "records delta for reversibility"). If a write path does **not** record a reversible delta, add that before claiming undo works, and adjust the integration test in T11 to assert undo restores prior inventory.
- **Verification debts flagged inline** (resolve while implementing, do not skip): exact `Database` write helper + autocommit (T6), `make_library_id` signature (T7), `set_campaign_config` existence (T9), delta-applier audit-row behavior when skipping generic apply (T11 step 5), FileWatcher store attribute name (T12), HUD BLOCK payload shape + endpoint templating tokens (T14), and the real FastAPI dependency providers (T13).
