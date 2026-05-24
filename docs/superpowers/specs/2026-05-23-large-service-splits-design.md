# Large Service Splits (Characters, Library, Gateway, StateStore)

Date: 2026-05-23
Status: Approved
PR: 6 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 3 (Lifecycle extraction)

## Problem

Four additional services exceed 1,500 lines and mix multiple responsibilities:

| Service | Lines | Methods | Key Concern Mix |
|---------|-------|---------|-----------------|
| `CharactersService` | ~2,180 | 66 | CRUD, promotion, drift, views, caching, sheets |
| `LibraryService` | ~1,720 | 53 | CRUD, scanning, indexing, composition, validation |
| `LLMGatewayService` | ~1,625 | 50 | Routing, completion, streaming, embedding, caching, audit, health |
| `StateStore` | ~2,145 | 53+ | File writes, indexing, delta log, search, snapshots, pins |

## Solution

For each service, keep the public facade class and extract focused collaborator classes underneath. Same pattern as PR 4 (OrchestratorService split).

## Detailed Design

### CharactersService Split

**File:** `backend/src/grimoire/characters/service.py` → split into:

| Collaborator | Responsibility | Est. Lines |
|--------------|---------------|------------|
| `CharacterViewCache` | LRU caching for active PC refs and compressed views, invalidation hooks | ~200 |
| `CharacterSheetManager` | Sheet CRUD, bulk creation, template rendering | ~300 |
| `CharacterDriftChecker` | Drift sampling, cadence gating, LLM-based drift checks | ~200 |
| `CharacterPromoter` | Promotion from emergent → library, state migration | ~150 |

**Stays on CharactersService (~1,300 lines):** Core CRUD, `list_for_campaign`, `get_full_card`, `get_compressed_card`, `recommend_tiers`, `set_active_pc`. The facade delegates to collaborators for views, sheets, drift, and promotion.

### LibraryService Split

**File:** `backend/src/grimoire/library/service.py` → split into:

| Collaborator | Responsibility | Est. Lines |
|--------------|---------------|------------|
| `CompositionManager` | Campaign composition CRUD (add/remove world refs, upgrade, resolve) | ~250 |
| `LibraryScanner` | Filesystem scanning, index rebuild, change detection | ~200 |
| `LibraryValidator` | Schema validation, reclassification, preview | ~150 |

**Stays on LibraryService (~1,100 lines):** Entity CRUD (worlds, characters, locations, factions, etc.), `get_entity`, `list_by_kind`, `create_entity`, `update_entity`, `delete_entity`.

### LLMGatewayService Split

**File:** `backend/src/grimoire/llm_gateway/gateway.py` → split into:

| Collaborator | Responsibility | Est. Lines |
|--------------|---------------|------------|
| `RouteResolver` | Per-task routing, tier resolution, fallback chains | ~250 |
| `CompletionClient` | Non-streaming LLM completion, retry logic | ~200 |
| `StreamClient` | Streaming LLM completion, chunk assembly | ~250 |
| `EmbeddingClient` | Embedding requests, batching, caching | ~200 |
| `GatewayAuditLog` | Request/response logging to SQLite | ~150 |

**Stays on LLMGatewayService (~575 lines):** Provider registration, health monitoring, route management, public `complete()`/`stream()`/`embed()` methods that delegate to clients.

### StateStore Split

**File:** `backend/src/grimoire/state_store/store.py` → split into:

| Collaborator | Responsibility | Est. Lines |
|--------------|---------------|------------|
| `FileWriteCoordinator` | Atomic file + SQLite writes, restore-on-failure | ~200 |
| `LibraryIndexRepository` | Library index queries and updates | ~250 |
| `CampaignIndexRepository` | Campaign-scoped entity queries | ~200 |
| `SearchRepository` | Vector + keyword search, priority hints | ~200 |
| `ContextPinRepository` | Context pin CRUD | ~100 |

**Stays on StateStore (~1,000 lines):** Transaction management (`_txn`), delta log, snapshot operations. `StateStore` remains the authoritative coherence boundary -- collaborators receive the database connection from it.

### Shared Pattern

All splits follow the same pattern:
1. Collaborator receives its dependencies via constructor.
2. Facade constructs collaborators in `__init__`.
3. Facade's public methods delegate to the appropriate collaborator.
4. Collaborators are independently testable.
5. Existing public API signatures do not change.

### Request Objects for LLMGatewayService

Introduce request dataclasses to replace long parameter lists:

#### `GatewayCallContext`

Replaces scattered parameters through completion/streaming calls:

```python
@dataclass(frozen=True)
class GatewayCallContext:
    task: str
    campaign_id: str | None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop_sequences: list[str] | None = None
```

#### `LLMRequestLogEntry`

Replaces the 16-parameter `request_log.record()` call:

```python
@dataclass(frozen=True)
class LLMRequestLogEntry:
    task: str
    model: str
    campaign_id: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    status: str
    error: str | None = None
    provider_id: str | None = None
    route: str | None = None
    cached: bool = False
```

#### `PostIndexRecord`

Replaces the 12-parameter `scenes/indexer.py upsert_post_row()` call:

```python
@dataclass(frozen=True)
class PostIndexRecord:
    post_id: str
    scene_id: str
    campaign_id: str
    branch_id: str
    author: str
    body: str
    turn_id: str | None = None
    ordinal: int = 0
    is_player: bool = False
    word_count: int = 0
    character_ref: str | None = None
```

### Shared Plugin/Mechanics Dynamic Loader

Extract a shared `backend/src/grimoire/dynamic_loader.py` helper from the duplicate loader implementations in `mechanics/loader.py` and `plugins/loader.py`.

Both loaders perform the same steps:
1. Stable module naming from filesystem path
2. `importlib` spec creation and execution
3. Cleanup on failed import (remove from `sys.modules`)
4. Validation error formatting
5. Path safety checks

The shared helper handles steps 1-5. Each domain keeps its manifest interpretation (mechanics YAML schema vs plugin entry points) separate.

```python
def load_module_from_path(
    path: Path,
    *,
    module_prefix: str,
    validate: Callable[[ModuleType], list[str]] | None = None,
) -> ModuleType:
    """Load a Python module from a filesystem path with cleanup on failure."""
    ...
```

## Scope

### In scope
- Extract collaborator classes from 4 services
- Keep facade classes with same public API
- Introduce `GatewayCallContext`, `LLMRequestLogEntry`, and `PostIndexRecord` request objects
- Extract shared `dynamic_loader.py` from mechanics/plugins loaders
- Update tests to test collaborators directly where beneficial

### Not in scope
- Changing public method signatures
- Adding new functionality
- Changing the CharactersService caching strategy
- Introducing a repository abstraction layer (deferred to PR 8)

## Verification

1. `pytest` full suite passes.
2. Each facade file is under 1,300 lines.
3. Each collaborator file is under 300 lines.
4. All existing public methods still exist with same signatures.
5. No circular imports.
6. `mechanics/loader.py` and `plugins/loader.py` both import from `dynamic_loader.py`.
7. Request object dataclasses have tests verifying construction and field access.
