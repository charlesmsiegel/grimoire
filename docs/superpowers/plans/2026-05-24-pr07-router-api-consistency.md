# PR 7: Router/API Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `campaigns.py` (2,785 lines, 83 routes) into ~14 focused sub-router modules. Add standard pagination, response models for priority endpoints, normalize URLs to kebab-case, and add WebSocket idle timeout.

**Architecture:** `api/campaigns/` becomes a package. Each sub-module defines its own `router = APIRouter()`. The package `__init__.py` composes them into a single router that `main.py` imports. Shared helpers move to `campaigns/helpers.py`. A reusable `PaginationParams` dependency is created in `api/pagination.py`.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic

---

### Task 1: Create pagination dependency

**Files:**
- Create: `backend/src/grimoire/api/pagination.py`

- [ ] **Step 1: Define PaginationParams**

```python
"""Standard pagination dependency for list endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query


@dataclass
class PaginationParams:
    limit: int = Query(default=50, ge=1, le=200)
    offset: int = Query(default=0, ge=0)


PaginationDep = Annotated[PaginationParams, Depends()]
```

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/api/pagination.py
git commit -m "feat(api): add standard PaginationParams dependency"
```

---

### Task 2: Create campaigns/ package with helpers

**Files:**
- Create: `backend/src/grimoire/api/campaigns/__init__.py`
- Create: `backend/src/grimoire/api/campaigns/helpers.py`

- [ ] **Step 1: Move shared helpers from campaigns.py to helpers.py**

Move these functions to `campaigns/helpers.py`:
- `_require_campaign_row()` (line 575)
- `_load_campaign_config()` (line 582)
- `_write_campaign_config()` (line 596)
- `_read_routing_blocks()` (line 711)
- `_require_scene_owned()` (line 1470)
- `_list_kind()` (line 1715)
- `_continuity_for()` (line 2157)
- `_require_review_owned()` (line 2717)
- `_seed_greeting_first_post()` (line 122) and its sub-helpers (`_img_to_markdown`, `_substitute_placeholders`, `_resolve_pc_display_name`, `_resolve_character_display_name`)

- [ ] **Step 2: Create __init__.py that will compose sub-routers**

Start with a placeholder:

```python
"""Campaign API routes — composed from sub-modules."""

from fastapi import APIRouter

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Sub-routers will be included here as they're extracted
```

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/campaigns/
git commit -m "refactor(api): create campaigns package with shared helpers"
```

---

### Task 3: Extract sub-routers one at a time

For each sub-router, the process is:
1. Create the sub-module file
2. Move route handlers from `campaigns.py` to the new file
3. Import and include the sub-router in `__init__.py`
4. Run tests: `cd backend && uv run pytest tests/api/ -x -q`
5. Commit

**Files to create (one task per file):**

- [ ] **Step 1: Extract `campaigns/core.py`** (6 routes: list, rescan, create, get, update, delete)
- [ ] **Step 2: Extract `campaigns/settings.py`** (18 routes: all settings GET/PUT pairs)
- [ ] **Step 3: Extract `campaigns/composition.py`** (5 routes)
- [ ] **Step 4: Extract `campaigns/pcs.py`** (5 routes)
- [ ] **Step 5: Extract `campaigns/turns.py`** (7 routes: submit, advance, regenerate, undo, resolve-proposals, resolve-scene-break)
- [ ] **Step 6: Extract `campaigns/retcon.py`** (5 routes)
- [ ] **Step 7: Extract `campaigns/fork.py`** (5 routes: fork, branches, lineage, pending)
- [ ] **Step 8: Extract `campaigns/scenes.py`** (5 routes)
- [ ] **Step 9: Extract `campaigns/entities.py`** (9 routes: resolved views, promotion, override)
- [ ] **Step 10: Extract `campaigns/sheets.py`** (3 routes)
- [ ] **Step 11: Extract `campaigns/continuity.py`** (5 routes)
- [ ] **Step 12: Extract `campaigns/images.py`** (17 routes)
- [ ] **Step 13: Extract `campaigns/export.py`** (4 routes)
- [ ] **Step 14: Extract `campaigns/reviews.py`** (3 routes)

After each extraction, update `__init__.py`:

```python
from .core import router as core_router
from .settings import router as settings_router
# ... etc

router.include_router(core_router)
router.include_router(settings_router)
```

- [ ] **Step 15: Delete the old campaigns.py**

- [ ] **Step 16: Update main.py import**

Change:
```python
from grimoire.api.campaigns import router as campaigns_router
```
This should work without changes because the package `__init__.py` exports `router`.

- [ ] **Step 17: Run full API test suite**

Run: `cd backend && uv run pytest tests/api/ -x -q`
Expected: All pass

- [ ] **Step 18: Commit**

```
git add backend/src/grimoire/api/campaigns/ backend/src/grimoire/main.py
git rm backend/src/grimoire/api/campaigns.py
git commit -m "refactor(api): split campaigns.py into 14 focused sub-routers"
```

---

### Task 4: Add response models to priority endpoints

**Files:**
- Create: `backend/src/grimoire/api/campaigns/schemas.py`
- Modify: `backend/src/grimoire/api/campaigns/core.py`
- Modify: `backend/src/grimoire/api/campaigns/scenes.py`
- Modify: `backend/src/grimoire/api/campaigns/pcs.py`

- [ ] **Step 1: Define response models**

```python
"""API response models for campaign endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class CampaignSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    mechanics_module: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    created_at: str
    last_played_at: str | None = None


class SceneSummary(BaseModel):
    id: str
    title: str | None = None
    status: str | None = None
    created_at: str | None = None
```

Read the actual query results in `list_campaigns()` and `list_scenes()` to ensure the fields match.

- [ ] **Step 2: Apply response_model to list/get endpoints**

```python
@router.get("", response_model=list[CampaignSummary])
async def list_campaigns(...):
```

- [ ] **Step 3: Run tests, commit**

```
git add backend/src/grimoire/api/campaigns/schemas.py backend/src/grimoire/api/campaigns/
git commit -m "feat(api): add response models to priority campaign endpoints"
```

---

### Task 5: Normalize URLs and add pagination

- [ ] **Step 1: Fix snake_case URLs**

Search for `world_diff` in library routes and rename to `world-diff`. Update any frontend references.

- [ ] **Step 2: Apply PaginationDep to list endpoints**

Add `pagination: PaginationDep` to `list_campaigns()`, `list_scenes()`, and other list endpoints that currently lack pagination. Apply `LIMIT ? OFFSET ?` to their SQL queries.

- [ ] **Step 3: Run tests, commit**

```
git commit -m "refactor(api): normalize URLs to kebab-case and add standard pagination"
```

---

### Task 6: Add WebSocket idle timeout

**Files:**
- Modify: `backend/src/grimoire/api/ws.py`

- [ ] **Step 1: Add asyncio.wait_for timeout to receive loop**

```python
import asyncio

IDLE_TIMEOUT_SECONDS = 300  # 5 minutes

@router.websocket("/campaigns/{campaign_id}/stream")
async def campaign_stream(websocket: WebSocket, campaign_id: str) -> None:
    # ... existing setup ...
    await stream.connect(campaign_id, websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000)
                break
            except WebSocketDisconnect:
                break
    finally:
        await stream.disconnect(campaign_id, websocket)
```

- [ ] **Step 2: Run tests, commit**

```
git add backend/src/grimoire/api/ws.py
git commit -m "feat(api): add WebSocket idle timeout"
```

---

### Task 7: Final verification

- [ ] **Step 1: Verify campaigns.py is gone**

Run: `ls backend/src/grimoire/api/campaigns.py`
Expected: File not found (replaced by `campaigns/` package)

- [ ] **Step 2: Verify sub-module sizes**

Run: `cd backend && wc -l src/grimoire/api/campaigns/*.py`
Expected: Each under 500 lines

- [ ] **Step 3: Run full suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass
