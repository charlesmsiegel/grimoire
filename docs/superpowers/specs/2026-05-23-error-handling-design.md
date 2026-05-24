# Error Handling Refactor

Date: 2026-05-23
Status: Approved
PR: 2 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing)

## Problem

HTTP status codes are derived from exception class name substrings in `map_lookup_errors()` (`backend/src/grimoire/api/util.py:42-90`). Renaming a class silently changes its HTTP status. Three separate per-router wrapper functions exist (`_map_retcon_error`, `_map_alternate_error`) that duplicate the logic because the generic mapper can't distinguish domain-specific cases.

67 exception classes exist across 16 files. None carry an `http_status` attribute. All rely on string matching for HTTP translation.

## Solution

Add an `http_status` class attribute to domain exception base classes. Replace substring matching in `map_lookup_errors()` with `getattr(exc, 'http_status', 500)`. Remove per-router wrapper functions.

## Detailed Design

### Step 1: Add `http_status` to Exception Hierarchies

Each domain error module already has a base class. Add `http_status: int = 500` as a class attribute on the base, then override on subclasses where the status differs.

**Mapping by module (derived from current `map_lookup_errors` behavior):**

| Module | Base Class | File |
|--------|-----------|------|
| `characters/errors.py` | `CharactersError` | 4 exceptions |
| `library/errors.py` | `LibraryError` | 4 exceptions |
| `orchestrator/errors.py` | `OrchestratorError` | 16 exceptions |
| `llm_gateway/errors.py` | `GatewayError` | 8 exceptions |
| `state_store/errors.py` | `StateStoreError` | 3 exceptions |
| `export/errors.py` | `ExportError` | 3 exceptions |
| `extras/errors.py` | `ExtrasError` | 3 exceptions |
| `world/errors.py` | `WorldError` | 2 exceptions |
| `time_engine/errors.py` | `TimeEngineError` | 3 exceptions |
| `context/errors.py` | `ContextBuilderError` | 1 exception |
| `continuity/service.py` | (inline classes) | 4 exceptions |
| `imagegen/service.py` | `NoBackendAvailableError` | 1 exception |

**Status code assignments:**

```
# 404 Not Found
CharacterNotFoundError, LibraryNotFoundError, WorldNotFoundError,
StateStoreNotFoundError, ExtrasNotFoundError, FactNotFoundError,
CommitmentNotFoundError, ContradictionReportNotFoundError,
UnknownCampaignError, UnknownPCError, AlternateNotFoundError,
RetconBatchNotFoundError, AuxiliaryNotFoundError,
UnknownAdapterError (export)

# 400 Bad Request
InvalidRefError, PromotionError (both modules), ReclassificationError,
ValidationFailed, VocabularyError, ExtrasKeyError, ExtrasCapError,
InvalidSkipError, CheckpointTokenError, LockInOverflowError,
InvalidRequestError (gateway), ContentFilterError (gateway),
ConfidenceFloorError

# 409 Conflict
OrchestratorError (base), TurnAlreadyInProgressError,
NoTurnsToUndoError, TurnCancelledError, TurnTimeoutError,
LatestPostOnlyError, CannotDeletePrimaryError,
RetconInFlightError, RetconBatchClosedError,
CampaignIdExists, AuxiliaryAlreadyCommittedError,
LibraryConflictError, StateStoreConflictError,
ExtrasHardCapError, CompositionError

# 503 Service Unavailable
RouteNotFoundError, ProviderNotFoundError,
NoBackendAvailableError

# 403 Forbidden
AuthenticationError (gateway)

# 429 Too Many Requests (new — currently falls through to 500)
RateLimitError (gateway)

# 500 Internal Server Error (default)
GatewayError (base), TransientError, PermanentError,
TimeEngineError (base), ContextBuilderError (base)
```

### Step 2: Simplify `map_lookup_errors()`

Replace the current 50-line substring matcher with:

```python
def map_lookup_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    status_code = getattr(exc, 'http_status', None)
    if status_code is None:
        # Walk cause chain for wrapped exceptions
        cause = exc.__cause__ or exc.__context__
        seen: set[int] = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            status_code = getattr(cause, 'http_status', None)
            if status_code is not None:
                break
            cause = cause.__cause__ or cause.__context__
    if status_code is None:
        status_code = 500
    detail = str(exc) or type(exc).__name__
    return HTTPException(status_code=status_code, detail=detail)
```

### Step 3: Remove Per-Router Wrappers

Delete these functions and replace their call sites with direct `map_lookup_errors()`:

- `_map_retcon_error()` in `api/campaigns.py:1312-1326`
- `_map_alternate_error()` in `api/alternates.py:66-74`
- `_map_errors()` in `api/extras.py:53-66`

### Step 4: Move Inline Exception Classes

Four exception classes in `continuity/service.py` (`FactNotFoundError`, `CommitmentNotFoundError`, `ContradictionReportNotFoundError`, `ConfidenceFloorError`) should move to a `continuity/errors.py` file for consistency with other modules.

Similarly, `NoBackendAvailableError` in `imagegen/service.py:137` should move to `imagegen/errors.py`.

## Scope

### In scope
- Add `http_status` class attribute to all 67 domain exception classes
- Simplify `map_lookup_errors()` to use `getattr`
- Remove 3 per-router wrapper functions
- Move inline exception definitions to `errors.py` modules
- Add `RateLimitError` → 429 mapping (currently falls through to 500)

### Not in scope
- Changing exception hierarchies or class names
- Adding new exception types
- Changing how routers catch exceptions
- Adding global exception handlers (FastAPI middleware)

## Verification

1. `ruff check` and `ruff format --check` pass.
2. `pytest` full suite passes.
3. Grep for `map_retcon_error`, `map_alternate_error`, `_map_errors` returns zero hits.
4. Grep for `"notfound" in name` in `util.py` returns zero hits.
5. Every exception class in `backend/src/grimoire/` (excluding `testing/`) has an `http_status` attribute.
