# PR 6: Large Service Splits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split CharactersService, LibraryService, LLMGatewayService, and StateStore into facade + collaborator classes. Extract shared dynamic loader. Consolidate date/JSON helpers. Introduce `GatewayCallContext`, `LLMRequestLogEntry`, and `PostIndexRecord` request objects.

**Architecture:** Same pattern as PR 4-5: each service keeps its public API as a facade that delegates to focused collaborators. Collaborators receive dependencies via constructor. The shared `dynamic_loader.py` replaces duplicate importlib logic in mechanics and plugins loaders.

**Tech Stack:** Python 3.12+

---

### Task 1: Extract shared dynamic_loader.py

**Files:**
- Create: `backend/src/grimoire/dynamic_loader.py`
- Modify: `backend/src/grimoire/mechanics/loader.py`
- Modify: `backend/src/grimoire/plugins/loader.py`

- [ ] **Step 1: Read both loaders and identify common logic**

Read `mechanics/loader.py` and `plugins/loader.py`. Identify the shared steps:
1. Stable module naming from filesystem path
2. `importlib` spec creation and execution
3. Cleanup on failed import (`sys.modules` removal)
4. Validation error formatting
5. Path safety checks

- [ ] **Step 2: Create dynamic_loader.py with the shared function**

```python
"""Shared dynamic module loading for mechanics and plugins."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)


def load_module_from_path(
    path: Path,
    *,
    module_prefix: str,
    validate: Callable[[ModuleType], list[str]] | None = None,
) -> ModuleType:
    """Load a Python module from a filesystem path with cleanup on failure."""
    name = f"{module_prefix}.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    if validate is not None:
        errors = validate(module)
        if errors:
            sys.modules.pop(name, None)
            raise ImportError(
                f"validation failed for {path}: {'; '.join(errors)}"
            )
    return module
```

Adapt the exact implementation based on what both loaders actually do — the above is the skeleton.

- [ ] **Step 3: Update both loaders to use the shared function**

Replace the duplicated importlib logic in each loader with calls to `load_module_from_path()`. Keep domain-specific validation (YAML schema for mechanics, entry points for plugins) in each loader.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/mechanics/ tests/plugins/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/dynamic_loader.py backend/src/grimoire/mechanics/loader.py backend/src/grimoire/plugins/loader.py
git commit -m "refactor: extract shared dynamic_loader from mechanics/plugins loaders"
```

---

### Task 2: Consolidate date/JSON parsing helpers

**Files:**
- Modify: `backend/src/grimoire/util.py`
- Modify: `backend/src/grimoire/state_store/delta_log.py` (import from util instead of local)

- [ ] **Step 1: Add shared helpers to util.py**

Read `delta_log.py` to find `_json_loads` and `_json_dumps`. Add consolidated versions to `util.py`:

```python
def safe_json_loads(value: str | dict | list | None) -> Any:
    """Parse JSON string, or return already-parsed dicts/lists unchanged."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def safe_json_dumps(value: Any) -> str:
    """Serialize to JSON with deterministic key ordering."""
    return json.dumps(value, sort_keys=True, default=str)
```

- [ ] **Step 2: Update delta_log.py to import from util**

Replace local `_json_loads`/`_json_dumps` with imports from `grimoire.util`.

- [ ] **Step 3: Run tests, commit**

```
git add backend/src/grimoire/util.py backend/src/grimoire/state_store/delta_log.py
git commit -m "refactor: consolidate JSON parsing helpers into util.py"
```

---

### Task 3: Create request object dataclasses

**Files:**
- Create: `backend/src/grimoire/llm_gateway/types.py`
- Create: `backend/src/grimoire/scenes/types.py`

- [ ] **Step 1: Define GatewayCallContext and LLMRequestLogEntry**

In `llm_gateway/types.py`:

```python
"""LLM gateway request objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayCallContext:
    task: str
    campaign_id: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


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

- [ ] **Step 2: Define PostIndexRecord**

In `scenes/types.py`:

```python
"""Scene indexer request objects."""

from __future__ import annotations

from dataclasses import dataclass


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

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/llm_gateway/types.py backend/src/grimoire/scenes/types.py
git commit -m "feat: add GatewayCallContext, LLMRequestLogEntry, PostIndexRecord"
```

---

### Task 4: Split CharactersService

**Files:**
- Create: `backend/src/grimoire/characters/view_cache.py`
- Create: `backend/src/grimoire/characters/sheet_manager.py`
- Create: `backend/src/grimoire/characters/drift_checker.py`
- Create: `backend/src/grimoire/characters/promoter.py`
- Modify: `backend/src/grimoire/characters/service.py`

- [ ] **Step 1: Extract CharacterViewCache (~200 lines)**

Move LRU caching logic (`_active_pc`, `_view_cache`, invalidation hooks) into `view_cache.py`.

- [ ] **Step 2: Extract CharacterSheetManager (~300 lines)**

Move sheet CRUD, bulk creation, template rendering into `sheet_manager.py`.

- [ ] **Step 3: Extract CharacterDriftChecker (~200 lines)**

Move drift sampling, cadence gating into `drift_checker.py`.

- [ ] **Step 4: Extract CharacterPromoter (~150 lines)**

Move promotion logic into `promoter.py`.

- [ ] **Step 5: Wire collaborators into CharactersService, run tests**

Run: `cd backend && uv run pytest tests/characters/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/characters/
git commit -m "refactor(characters): extract view_cache, sheet_manager, drift_checker, promoter"
```

---

### Task 5: Split LibraryService

**Files:**
- Create: `backend/src/grimoire/library/composition.py`
- Create: `backend/src/grimoire/library/scanner.py`
- Create: `backend/src/grimoire/library/validator.py`
- Modify: `backend/src/grimoire/library/service.py`

- [ ] **Step 1: Extract CompositionManager, LibraryScanner, LibraryValidator**

Follow the spec tables for which methods go where. Wire into `LibraryService.__init__`.

- [ ] **Step 2: Run tests, commit**

```
git add backend/src/grimoire/library/
git commit -m "refactor(library): extract composition, scanner, validator collaborators"
```

---

### Task 6: Split LLMGatewayService

**Files:**
- Create: `backend/src/grimoire/llm_gateway/route_resolver.py`
- Create: `backend/src/grimoire/llm_gateway/completion_client.py`
- Create: `backend/src/grimoire/llm_gateway/stream_client.py`
- Create: `backend/src/grimoire/llm_gateway/embedding_client.py`
- Create: `backend/src/grimoire/llm_gateway/audit_log.py`
- Modify: `backend/src/grimoire/llm_gateway/gateway.py`

- [ ] **Step 1: Extract 5 collaborators following spec tables**

- [ ] **Step 2: Wire into LLMGatewayService, run tests, commit**

```
git add backend/src/grimoire/llm_gateway/
git commit -m "refactor(gateway): extract route_resolver, completion/stream/embedding clients, audit_log"
```

---

### Task 7: Split StateStore

**Files:**
- Create: `backend/src/grimoire/state_store/file_coordinator.py`
- Create: `backend/src/grimoire/state_store/library_index_repo.py`
- Create: `backend/src/grimoire/state_store/campaign_index_repo.py`
- Create: `backend/src/grimoire/state_store/delta_repo.py`
- Create: `backend/src/grimoire/state_store/snapshot_repo.py`
- Create: `backend/src/grimoire/state_store/search_repo.py`
- Create: `backend/src/grimoire/state_store/pin_repo.py`
- Modify: `backend/src/grimoire/state_store/store.py`

- [ ] **Step 1: Extract 7 collaborators following spec tables**

- [ ] **Step 2: Wire into StateStore, run tests**

Run: `cd backend && uv run pytest tests/state_store/ -x -q`
Expected: All pass

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/state_store/
git commit -m "refactor(state_store): extract file_coordinator, repositories, search, pins"
```

---

### Task 8: Final verification

- [ ] **Step 1: Verify all facade sizes**

Run: `cd backend && wc -l src/grimoire/characters/service.py src/grimoire/library/service.py src/grimoire/llm_gateway/gateway.py src/grimoire/state_store/store.py`
Expected: Each under 1,300 lines

- [ ] **Step 2: Run full suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass
