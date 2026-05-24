# PR 2: Error Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `http_status` class attribute to all domain exception classes, simplify `map_lookup_errors()` to use `getattr`, remove per-router error wrapper functions, move inline exception definitions to `errors.py` modules, and fix 7 backend Ruff failures.

**Architecture:** Each domain error module already has a base exception class. We add `http_status: int = 500` as a class attribute on each base, then override on subclasses where the HTTP status differs. The central `map_lookup_errors()` function becomes a simple `getattr(exc, 'http_status', 500)` lookup with cause-chain walking. Per-router wrapper functions are deleted.

**Tech Stack:** Python 3.12+, FastAPI

---

### Task 1: Fix 7 backend Ruff failures

**Files:**
- Modify: `backend/tests/llm_gateway/test_event_emissions.py`
- Modify: `backend/tests/orchestrator/test_integrated_deltas.py`
- Modify: `backend/tests/scenes/test_summary_skipped.py`

- [ ] **Step 1: Fix all 7 Ruff issues**

1. `test_event_emissions.py:157` — break the long line
2. `test_integrated_deltas.py:7` — remove unused `pytest` import
3. `test_integrated_deltas.py:77-78` — move imports to top of file
4. `test_summary_skipped.py:60` — break the long line
5. `test_summary_skipped.py:87-88` — collapse nested `if` statements into a single `if ... and ...:`

- [ ] **Step 2: Verify**

Run: `cd backend && uv run ruff check tests/`
Expected: Pass

- [ ] **Step 3: Commit**

```
git add backend/tests/
git commit -m "fix(tests): resolve 7 Ruff lint failures"
```

---

### Task 2: Move inline exception classes to errors.py modules

**Files:**
- Create: `backend/src/grimoire/continuity/errors.py`
- Create: `backend/src/grimoire/imagegen/errors.py`
- Modify: `backend/src/grimoire/continuity/service.py`
- Modify: `backend/src/grimoire/imagegen/service.py`

- [ ] **Step 1: Create continuity/errors.py**

Move `FactNotFoundError`, `CommitmentNotFoundError`, `ContradictionReportNotFoundError`, and `ConfidenceFloorError` from `continuity/service.py` (lines ~57-69) to a new `continuity/errors.py`:

```python
"""Continuity domain exceptions."""


class ContinuityError(Exception):
    """Base for continuity exceptions."""
    http_status = 500


class FactNotFoundError(ContinuityError, KeyError):
    http_status = 404


class CommitmentNotFoundError(ContinuityError, KeyError):
    http_status = 404


class ContradictionReportNotFoundError(ContinuityError, KeyError):
    http_status = 404


class ConfidenceFloorError(ContinuityError, ValueError):
    http_status = 400
```

- [ ] **Step 2: Update continuity/service.py imports**

Replace the inline class definitions with:
```python
from grimoire.continuity.errors import (
    CommitmentNotFoundError,
    ConfidenceFloorError,
    ContradictionReportNotFoundError,
    FactNotFoundError,
)
```

- [ ] **Step 3: Create imagegen/errors.py**

Move `NoBackendAvailableError` from `imagegen/service.py` (~line 137):

```python
"""ImageGen domain exceptions."""


class ImageGenError(Exception):
    """Base for imagegen exceptions."""
    http_status = 500


class NoBackendAvailableError(ImageGenError, RuntimeError):
    http_status = 503
```

- [ ] **Step 4: Update imagegen/service.py import**

Replace the inline class definition with:
```python
from grimoire.imagegen.errors import NoBackendAvailableError
```

- [ ] **Step 5: Verify imports resolve**

Run: `cd backend && uv run python -c "from grimoire.continuity.errors import FactNotFoundError; from grimoire.imagegen.errors import NoBackendAvailableError; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/continuity/errors.py backend/src/grimoire/imagegen/errors.py backend/src/grimoire/continuity/service.py backend/src/grimoire/imagegen/service.py
git commit -m "refactor: move inline exception classes to errors.py modules"
```

---

### Task 3: Add http_status to all existing exception base classes

**Files:**
- Modify: `backend/src/grimoire/characters/errors.py`
- Modify: `backend/src/grimoire/library/errors.py`
- Modify: `backend/src/grimoire/orchestrator/errors.py`
- Modify: `backend/src/grimoire/llm_gateway/errors.py`
- Modify: `backend/src/grimoire/state_store/errors.py`
- Modify: `backend/src/grimoire/export/errors.py`
- Modify: `backend/src/grimoire/extras/errors.py`
- Modify: `backend/src/grimoire/world/errors.py`
- Modify: `backend/src/grimoire/time_engine/errors.py`
- Modify: `backend/src/grimoire/context/errors.py`

- [ ] **Step 1: Add http_status to each error module**

For each file, add `http_status` class attributes following the mapping from the spec. The pattern for each module is: base class gets `http_status = 500` (default), subclasses override to the correct status code.

**characters/errors.py:**
```python
class CharactersError(Exception):
    http_status = 500

class CharacterNotFoundError(CharactersError):
    http_status = 404

class ImportError_(CharactersError):
    http_status = 400

class PromotionError(CharactersError):
    http_status = 400
```

**library/errors.py:**
```python
class LibraryError(Exception):
    http_status = 500

class LibraryNotFoundError(LibraryError):
    http_status = 404

class LibraryConflictError(LibraryError):
    http_status = 409

class PromotionError(LibraryError):
    http_status = 400

class ReclassificationError(LibraryError):
    http_status = 400
```

**orchestrator/errors.py** — all default to 409 (conflict/precondition) except:
```python
class OrchestratorError(Exception):
    http_status = 409

class UnknownCampaignError(OrchestratorError):
    http_status = 404

class UnknownPCError(OrchestratorError):
    http_status = 404

class AlternateNotFoundError(OrchestratorError):
    http_status = 404

class RetconBatchNotFoundError(OrchestratorError):
    http_status = 404

class AuxiliaryNotFoundError(OrchestratorError):
    http_status = 404

# All other OrchestratorError subclasses inherit 409
```

**llm_gateway/errors.py:**
```python
class GatewayError(Exception):
    http_status = 500

class RouteNotFoundError(GatewayError):
    http_status = 503

class ProviderNotFoundError(GatewayError):
    http_status = 503

class TransientError(GatewayError):
    http_status = 500

class RateLimitError(TransientError):
    http_status = 429

class PermanentError(GatewayError):
    http_status = 500

class AuthenticationError(PermanentError):
    http_status = 403

class InvalidRequestError(PermanentError):
    http_status = 400

class ContentFilterError(PermanentError):
    http_status = 400
```

**state_store/errors.py:**
```python
class StateStoreError(Exception):
    http_status = 500

class NotFoundError(StateStoreError):
    http_status = 404

class ConflictError(StateStoreError):
    http_status = 409

class InvalidRefError(StateStoreError):
    http_status = 400
```

**export/errors.py:**
```python
class ExportError(Exception):
    http_status = 500

class UnknownAdapterError(ExportError, KeyError):
    http_status = 404

class EmptyExportError(ExportError):
    http_status = 400

class ValidationFailed(ExportError):
    http_status = 400
```

**extras/errors.py:**
```python
class ExtrasError(Exception):
    http_status = 500

class ExtrasNotFoundError(ExtrasError):
    http_status = 404

class ExtrasHardCapError(ExtrasError):
    http_status = 409

class ExtrasPromotionError(ExtrasError):
    http_status = 400
```

**world/errors.py:**
```python
class WorldError(Exception):
    http_status = 500

class WorldNotFoundError(WorldError):
    http_status = 404

class CompositionError(WorldError):
    http_status = 409
```

**time_engine/errors.py:**
```python
class TimeEngineError(Exception):
    http_status = 500

class TimeNotSetError(TimeEngineError):
    http_status = 409

class InvalidSkipError(TimeEngineError):
    http_status = 400

class CheckpointTokenError(TimeEngineError):
    http_status = 400
```

**context/errors.py:**
```python
class ContextBuilderError(Exception):
    http_status = 500

class LockInOverflowError(ContextBuilderError):
    http_status = 400
```

Add `http_status` as a single-line class attribute on each class. Do NOT change existing `__init__` signatures, docstrings, or custom attributes — only add the `http_status` line.

- [ ] **Step 2: Verify all modules import cleanly**

Run: `cd backend && uv run python -c "from grimoire.characters.errors import *; from grimoire.library.errors import *; from grimoire.orchestrator.errors import *; from grimoire.llm_gateway.errors import *; from grimoire.state_store.errors import *; from grimoire.export.errors import *; from grimoire.extras.errors import *; from grimoire.world.errors import *; from grimoire.time_engine.errors import *; from grimoire.context.errors import *; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/*/errors.py backend/src/grimoire/context/errors.py
git commit -m "feat(errors): add http_status class attribute to all domain exceptions"
```

---

### Task 4: Simplify map_lookup_errors()

**Files:**
- Modify: `backend/src/grimoire/api/util.py`

- [ ] **Step 1: Replace map_lookup_errors with getattr-based implementation**

Replace lines 42-90 in `backend/src/grimoire/api/util.py`:

```python
def map_lookup_errors(exc: Exception) -> HTTPException:
    """Translate well-known service-layer exceptions to HTTP errors.

    Uses the ``http_status`` class attribute on domain exceptions. Walks
    the cause chain so wrapped exceptions (e.g. OrchestratorError wrapping
    a RouteNotFoundError) resolve to the inner exception's status.
    """
    if isinstance(exc, HTTPException):
        return exc

    detail = str(exc) or type(exc).__name__

    # Walk the cause chain: the orchestrator wraps gateway errors in
    # OrchestratorError "from exc.cause"; the inner exception's status
    # is more specific than the wrapper's.
    cause: BaseException | None = exc.__cause__ or exc.__context__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        status_code = getattr(cause, "http_status", None)
        if status_code is not None and status_code != 500:
            return HTTPException(status_code=status_code, detail=detail)
        cause = cause.__cause__ or cause.__context__

    status_code = getattr(exc, "http_status", 500)
    return HTTPException(status_code=status_code, detail=detail)
```

- [ ] **Step 2: Verify**

Run: `cd backend && uv run ruff check src/grimoire/api/util.py`
Expected: Pass

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/util.py
git commit -m "refactor(api): simplify map_lookup_errors to use http_status attribute"
```

---

### Task 5: Remove per-router error wrapper functions

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py`
- Modify: `backend/src/grimoire/api/alternates.py`
- Modify: `backend/src/grimoire/api/extras.py`

- [ ] **Step 1: Remove _map_retcon_error from campaigns.py**

Delete the `_map_retcon_error()` function definition. Replace all call sites with `map_lookup_errors(exc)`.

- [ ] **Step 2: Remove _map_alternate_error from alternates.py**

Delete the `_map_alternate_error()` function. Replace call sites with `map_lookup_errors(exc)`.

- [ ] **Step 3: Remove _map_errors from extras.py**

Delete the `_map_errors()` function/decorator. Replace call sites with `map_lookup_errors(exc)`.

- [ ] **Step 4: Verify**

Run: `cd backend && uv run ruff check src/grimoire/api/campaigns.py src/grimoire/api/alternates.py src/grimoire/api/extras.py`
Expected: Pass

Run: `cd backend && grep -rn "_map_retcon_error\|_map_alternate_error\|_map_errors" src/grimoire/`
Expected: Zero hits

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/api/campaigns.py backend/src/grimoire/api/alternates.py backend/src/grimoire/api/extras.py
git commit -m "refactor(api): remove per-router error wrapper functions"
```

---

### Task 6: Verify full suite

- [ ] **Step 1: Run ruff**

Run: `cd backend && uv run ruff check src/grimoire/ tests/`
Expected: Pass

- [ ] **Step 2: Run pytest**

Run: `cd backend && uv run pytest -x -q`
Expected: All tests pass. Error mapping behavior should be identical — same exceptions map to same HTTP status codes.

- [ ] **Step 3: Verify http_status coverage**

Run: `cd backend && uv run python -c "
import importlib, pkgutil, inspect
import grimoire
errors = []
for importer, modname, ispkg in pkgutil.walk_packages(grimoire.__path__, grimoire.__name__ + '.'):
    if 'test' in modname: continue
    try: mod = importlib.import_module(modname)
    except: continue
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, Exception) and obj is not Exception and hasattr(obj, 'http_status'):
            errors.append(f'{obj.__module__}.{name}: {obj.http_status}')
for e in sorted(set(errors)): print(e)
"`
Expected: Every domain exception class prints with its http_status value.
