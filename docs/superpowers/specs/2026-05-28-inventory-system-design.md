# Inventory Management System — Design

**Issue:** #444 — Deterministic Subsystems: Inventory
**Date:** 2026-05-28
**Status:** Approved design, pending implementation plan

## Summary

A toggleable, per-campaign inventory subsystem that tracks who holds what. The
only non-deterministic step is the extractor identifying inventory changes from
post prose; everything after extraction is a deterministic, ordered state
machine. Mutations are expressed as the same reversible file-write deltas the
system already handles, so undo, campaign forks, and replay work with no
inventory-specific reversal code.

The extractor already emits `INVENTORY_CHANGE` deltas, but today they dump a
freeform string (`"delta": "acquired"`) into the generic `character_state`
table — there is no subsystem that applies them, no quantities, no transfer
validation, and `Item.current_holder` is never updated during play. This design
replaces that with typed operations plus a deterministic application layer.

## Scope

**In scope.** Discrete items (the rooftop key, a silver locket) and physical
fungible resources (gold, ammo, rations) — anything one character could hand to
another. Each entry carries a quantity, an `equipped` flag, and provenance/notes.

**Out of scope.** Abstract character resources (HP, willpower, mana) belong to
the sibling issue #445 (Resource Management): they are not objects and cannot be
given away. Container nesting (a chest holding items held by a character) is
deferred. No game-system logic (encumbrance, equip bonuses, weight) lives in
core — mechanics modules read inventory and interpret it.

**Decision test for "is this inventory?":** *Could one character physically give
it to another?* Yes → inventory. No → #445 or mechanics.

## Decisions

| Topic | Decision |
|-------|----------|
| What it tracks | Discrete items + physical fungibles; transferable things only |
| Unknown items | Auto-create campaign-local **emergent** items; fungibles map to canonical `resource:<slug>` ids |
| Holders | Characters and locations (no containers in v1) |
| Conflicts | Prose is canon → **reconcile + flag** (auto-apply, record discrepancy) |
| Confidence | Auto-apply everything; flag low-confidence + reconciled ops in an audit log; **play never blocks**; user can inspect/undo |
| Entry fields | `item_ref`, `item_name`, `quantity`, `fungible`, `equipped`, `provenance`, `notes`, `acquired_in_post` |
| Source of truth | `inventory:` section embedded in each holder's **existing overlay** (override YAML / emergent frontmatter / PC profile); derived SQLite table for fast queries; fully rebuildable from files |
| Application | Approach 1 — dedicated module invoked synchronously in the orchestrator post-extraction pipeline; mutations emitted as reversible file deltas |
| Deliverable | Backend + REST/WS API + React HUD widget & flagged-ops review panel |

## Architecture & Module Layout

New domain module `backend/src/grimoire/inventory/`:

```
inventory/
  __init__.py
  service.py        # InventoryService — apply pipeline + I/O orchestration
  state_machine.py  # pure operation resolution (no I/O), exhaustively unit-tested
  resolver.py       # item-identity resolution: known / emergent / fungible-resource
  config.py         # InventoryConfig (per-campaign toggle + settings)
  models.py         # Pydantic: InventoryEntry, InventoryOperation, OperationResult, FlaggedOp
  events.py         # inventory event-type constants
```

**Dependency direction (one-way).** `inventory` depends on `state_store` (write
overlay deltas + derived table), `world` (resolve / auto-create item entities),
and `event_bus` (emit). The **orchestrator** depends on `inventory` and calls it
in the turn pipeline. The **extractor** only changes its *schema* (it emits typed
ops) and does **not** depend on the inventory module. The **context builder**
reads inventory via the resolved entity (the `inventory:` block rides along in
merged frontmatter) — no new coupling.

**Determinism boundary.** `state_machine.py` is a pure function —
`(current_holdings, operation) → (new_holdings, result/flag)` — with no I/O,
clock, or LLM access. `service.py` orchestrates the I/O around it: resolve
items, load current state, emit file deltas, update SQLite, record flags, emit
events.

## Data Model

### `InventoryEntry`

```python
class InventoryEntry(BaseModel):
    item_ref: str             # stable id: library item id, emergent item id, or "resource:<slug>"
    item_name: str            # display-name snapshot
    quantity: int = 1         # always >= 1 while held; entry removed when it hits 0
    fungible: bool = False    # true for stackable resources (gold/ammo/rations)
    equipped: bool = False    # narrative flag; mechanics modules interpret it
    provenance: str | None = None  # "looted from the bandit captain"
    notes: str | None = None
    acquired_in_post: str | None = None
```

### Overlay file (source of truth)

An `inventory:` block embedded in each holder's **existing** overlay file. The
write target depends on holder origin:

- **Library-defined** character/location → override YAML
  (`campaigns/<id>/overrides/worlds/<world>/<kind>/<id>.yaml`)
- **Emergent** character/location → emergent file frontmatter
  (`campaigns/<id>/emergent/<kind>/<id>.md`)
- **PC** → PC profile frontmatter (`campaigns/<id>/characters/<id>/profile.md`)

Example (library NPC override):

```yaml
inventory:
  entries:
    - item_ref: the-rooftop-key
      item_name: "The Rooftop Key"
      quantity: 1
      provenance: "found in the old clocktower"
    - item_ref: "resource:gold"
      item_name: "Gold"
      quantity: 120
      fungible: true
```

Because overrides merge into the resolved entity, inventory rides along for free
when the context builder resolves a character or location.

### Derived SQLite table

Fast cross-holder queries (e.g. "who holds the rooftop key?"). Rebuilt from
overlay files by the watcher's `scan_now`.

```sql
CREATE TABLE inventory_holdings (
  id           TEXT PRIMARY KEY,    -- campaign_id:holder_kind:holder_id:item_ref
  campaign_id  TEXT NOT NULL,
  holder_kind  TEXT NOT NULL,       -- 'character' | 'location'
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
```

> Note: branches were removed from the schema in migration 036; all
> campaign-scoped tables key on `campaign_id` only. The inventory tables
> follow that convention (no `branch_id`).

### Flagged-op audit table

Low-confidence and reconciled ops the user can review and undo:

```sql
CREATE TABLE inventory_flags (
  id           TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL,
  turn_id      TEXT,
  op_json      TEXT NOT NULL,       -- the originating InventoryOperation
  flag_reason  TEXT NOT NULL,       -- see reasons below
  resolved     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);
```

`flag_reason` ∈ `{ low_confidence, reconciled_missing_item, reconciled_quantity,
reconciled_holder, unresolved_item, unresolved_holder }`.

### Item-identity resolution (`resolver.py`)

Given an extracted item string + holder, resolve to an `item_ref` via the read
cascade, in order:

1. **Existing item** — a library or emergent item matches → use its id.
2. **Fungible keyword** — matches the configured resource vocabulary (gold,
   silver, arrows, rations, torches, …) → canonical `resource:<slug>`,
   `fungible: true`.
3. **Auto-create emergent item** — otherwise create a campaign-local emergent
   item via the `world` module (existing `emergent/` pattern) and use its new
   id. The creation is itself a logged delta.

The fungible resource vocabulary is a small configurable list in
`InventoryConfig` (extends a built-in default set).

## Extractor: typed operations

Replace the freeform `delta` string with a typed operation so application is
deterministic. New `inventory_change` schema in `extractor/schema.py`:

```python
inventory_change = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "action":     {"type": "string",
                   "enum": ["acquire", "drop", "transfer", "consume",
                            "adjust", "equip", "unequip"]},
    "item":       {"type": "string"},                # natural language; resolved later
    "holder":     {"type": "string"},                # acting holder ref
    "to":         {"type": ["string", "null"]},      # destination holder for transfer
    "quantity":   {"type": ["integer", "null"]},     # default 1; signed delta for `adjust`
    "equipped":   {"type": ["boolean", "null"]},
    "provenance": {"type": ["string", "null"]},
    "evidence":   {"type": "string"},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0}
  },
  "required": ["action", "item", "holder", "confidence"]
}
```

`llm_strategy.py` and `rule_based.py` are updated to emit this shape.
`_make_inventory_delta` builds a `StateDelta(kind=INVENTORY_CHANGE)` carrying the
structured op in `after` (no longer targeting `character_state`); its
`target_scope` becomes a marker the orchestrator routes to `InventoryService`.
The extractor prompt gains a short gloss of each action verb so the model
chooses correctly.

## Deterministic State Machine

`state_machine.py` exposes a pure `apply(holdings, op) -> StateMachineResult`.
`holdings` is the in-memory state for the holders an op touches (the service
loads them before calling). Semantics:

| action | rule | conflict → reconcile + flag |
|--------|------|------------------------------|
| `acquire` | add / stack `quantity` on holder | none (always valid) |
| `drop` | remove `quantity` from holder; drop to holder's current location if known | holder lacks item/quantity → grant then drop; flag `reconciled_missing_item` |
| `transfer` | decrement source (`holder`), increment destination (`to`) | `holder` lacks item → grant to `holder` first; flag `reconciled_missing_item` |
| `consume` | decrement `quantity` (default 1); remove entry at 0 | over-consume → clamp to 0; flag `reconciled_quantity` |
| `adjust` | apply explicit delta (used for fungibles, e.g. "spends 30 gold" → −30) | result < 0 → clamp to 0; flag `reconciled_quantity` |
| `equip` / `unequip` | toggle the `equipped` flag | item not held → grant then equip; flag `reconciled_missing_item` |

**Determinism guarantees.** Within a turn, ops apply in extraction order;
resolution is a pure function of `(current holdings, op)`; no randomness, clock,
or LLM. Fungible entries stack by `item_ref`. Discrete items track a `quantity`
but distinct emergent ids never auto-merge. Every reconciliation, and every op
with `confidence < config.flag_threshold`, produces a `FlaggedOp`.

## Turn Pipeline Integration, Toggle & Undo

### Toggle

Lives in the campaign composition / `campaign.yaml`:

```yaml
inventory:
  enabled: true            # default false — opt-in, "campaigns that care"
  flag_threshold: 0.6      # ops below this confidence are applied but flagged for review
  fungible_resources: [gold, silver, arrows, rations, torches]   # extends built-in defaults
```

Surfaced as `InventoryConfig`, loaded in `bootstrap.py` (phase 1, content
services). `InventoryService` is constructed unconditionally but **only invoked
when `enabled`**.

### Pipeline

After the extractor produces deltas, when inventory is enabled the orchestrator
calls `await inventory.apply(campaign_id, turn_id, ops)`. The service:

1. Resolves each op's item + holders (`resolver.py`, auto-creating emergent
   items as needed — those creations are themselves logged deltas).
2. Loads current holdings for touched holders (SQLite fast path).
3. Runs the pure state machine over the ops in extraction order.
4. Emits the net change **as file-write `StateDelta`s** that rewrite each touched
   holder's `inventory:` overlay section. These flow through the existing
   delta-log, so **undo/replay reverse inventory automatically** — the
   `before`/`after` file bytes are captured exactly as for any override write.
5. Upserts the derived `inventory_holdings` rows, writes any `FlaggedOp`s, and
   emits `INVENTORY_CHANGED` / `INVENTORY_FLAGGED` events for the HUD.

Because mutations are expressed as the same reversible file deltas the system
already handles, turn undo, campaign forks, and replay all work with no
inventory-specific reversal code.

### Multi-call turn dedup (#622)

In `per_character_multi_call` mode every speaker round runs its own extraction
and calls `apply()` again under the **same `turn_id`**. Two NPC responses that
restate one event ("Alice takes the ring" / "…watches Alice pocket the ring")
would otherwise apply the additive op twice — doubling a fungible acquire or
re-running a transfer (reconciling the now-missing source and minting a second
copy). The service keeps a per-turn set of applied op signatures and **skips an
op whose signature already applied in an earlier round of the same turn**. The
snapshot is taken before each round, so the policy is *within-round repeats are
real, cross-round repeats are restatements*: two genuine 10-gold payments
narrated in one response (one `apply()` call) both apply, while the same payment
restated by the next speaker is dropped. `turn_id=None` (manual API ops) opts
out entirely.

The signature is `(action, resolved item_ref, case-folded holder, to,
normalised quantity)`. Holder ids are case-folded and reduced to their resolved
trailing segment so `winifred` / `.../characters/winifred` collapse. Quantity
follows `apply_op`'s reading — an unspecified count is 1 for every action
*except* `CONSUME` (where it means "the whole stack"), so a `consume 1` followed
by a `consume the rest` are distinct, not a restatement.

A turn's signatures are dropped the moment the turn ends (`turn_complete` /
`turn_cancelled`), so the map's live size tracks only concurrently-active
multi-call turns and an active turn is never evicted; a generous LRU cap is a
leak safety-net for a turn whose end event never arrives. **Pre-roll resume**
(#584) retries the same `turn_id` after unwinding the committed batch, so
`restore_holders` clears the unwound turn's signatures — otherwise the retry
would skip the restored op as a cross-round duplicate and complete the turn
without its inventory change.

## REST / WebSocket API

New router `backend/src/grimoire/api/campaigns/inventory.py`, mounted only when
the feature is enabled (otherwise `409 feature_disabled`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/campaigns/{id}/inventory` | All holdings grouped by holder; filters `?holder_kind=&holder_id=` and `?item_ref=` (powers "who holds X?") |
| `GET`  | `/campaigns/{id}/inventory/holders/{kind}/{holder_id}` | One holder's entries |
| `POST` | `/campaigns/{id}/inventory/operations` | Submit a **manual user operation** (same typed-op shape, `source=user`, confidence 1.0) — routed through the identical state machine for the same determinism + undo + audit trail |
| `GET`  | `/campaigns/{id}/inventory/flags?resolved=false` | List flagged ops |
| `POST` | `/campaigns/{id}/inventory/flags/{flag_id}/resolve` | Mark a flag reviewed, or `undo` it (reuses turn-undo of the originating delta) |

**WebSocket.** The existing campaign WS channel relays `INVENTORY_CHANGED` and
`INVENTORY_FLAGGED` so the HUD updates live without polling. Pydantic response
models in `models.py`; Zod mirrors on the frontend.

## Frontend HUD

Two pieces in the existing HUD widget system (`frontend/src/` + backend `hud/`):

- **Inventory widget** — registered like the cast / scene-info widgets. Lists
  holdings for the current scene's cast and the scene location, grouped by
  holder, with quantity, an equipped marker, and provenance on hover.
  Subscribes to `INVENTORY_CHANGED` for live updates. Hidden when the feature is
  off.
- **Flagged-ops review panel** — a small surface (badge count on the widget
  header) listing flagged ops with their reason (`low_confidence` /
  `reconciled_*` / `unresolved_*`), the evidence snippet, and **Confirm** /
  **Undo** actions calling the flags API. This is the "play never blocks, but
  discrepancies are surfaced" review surface.

API client functions in `frontend/src/api/`, Zod schemas in
`frontend/src/types/`, a `useInventory` hook subscribing to the WS channel,
component tests with React Testing Library.

## Error Handling

- **Item resolution failure** (can't match or create) → op skipped, recorded as
  a flag with reason `unresolved_item`; never throws into the turn loop.
- **Holder doesn't exist** (bad ref from the LLM) → op flagged
  `unresolved_holder`, skipped.
- **Feature disabled mid-campaign** → existing overlay `inventory:` sections and
  SQLite rows are left intact (data preserved); the orchestrator stops invoking
  the service and the API/HUD report disabled. Re-enabling resumes from
  preserved state.
- **File / SQLite divergence** → files are SSOT; the `scan_now` rebuild
  repopulates `inventory_holdings` from overlay sections, so a deleted DB
  self-heals.
- **State-machine invariants** (quantity never negative; entries with quantity 0
  removed) are enforced in the pure layer and asserted in tests.

## Testing Strategy

- **Unit — pure state machine:** every action × valid/conflict path; stacking,
  clamping, reconciliation-flag emission, equip toggles. No I/O — exhaustive and
  fast.
- **Unit — resolver:** known item / fungible keyword / emergent auto-create
  branches.
- **Integration (`integration`):** extractor → orchestrator → inventory → overlay
  file written + SQLite row + events emitted; toggle on/off; undo restores prior
  inventory; DB-delete + `scan_now` rebuild round-trips from files.
- **Scenario (`scenario`):** full HTTP path — submit posts that pick up / drop /
  transfer / spend, assert inventory endpoints, submit a manual op, resolve a
  flag.
- **Frontend:** widget renders holdings, live-updates on a WS event; flag panel
  Confirm/Undo calls the API.
- **Regression:** update the existing `test_rule_based` / `test_llm_strategy` /
  `test_service` inventory tests for the typed-op shape (they currently assert
  the old freeform delta).

## Documentation Updates (in the implementation PR)

- Add the `inventory` module to the module-ownership tables in `CLAUDE.md` and
  `AGENTS.md`.
- Note the per-campaign `inventory:` toggle in campaign configuration docs.
- Update the README feature list if inventory is user-facing enough to warrant
  it.

## Out of Scope / Future Work

- Container nesting (chests, backpacks) as holders.
- Mechanics-module integration for encumbrance / equip bonuses (reads this
  subsystem; no core changes needed).
- Fungible resource management beyond physical items (#445).
