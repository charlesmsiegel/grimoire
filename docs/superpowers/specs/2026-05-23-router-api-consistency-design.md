# Router/API Consistency

Date: 2026-05-23
Status: Approved
PR: 7 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 2 (Error handling)

## Problem

`backend/src/grimoire/api/campaigns.py` is 2,785 lines with 83 route handlers across 18 domains. It contains helper functions, payload classes, and domain logic mixed with HTTP handling. Additionally, the API layer has inconsistent response models, no standard pagination, and mixed URL naming conventions.

## Solution

Split `campaigns.py` into focused sub-router modules. Add response models, standardize pagination, and normalize URL naming across all routers.

## Detailed Design

### Step 1: Split campaigns.py

Create `backend/src/grimoire/api/campaigns/` package with sub-modules:

| Module | Routes | Lines (est.) | Domains |
|--------|--------|-------------|---------|
| `core.py` | 6 | ~200 | Campaign CRUD, rescan |
| `settings.py` | 18 | ~500 | All settings tabs (routing, tiers, imagegen, summaries, integrated-deltas, storage, advanced, generation, narrator) |
| `composition.py` | 5 | ~100 | Composition CRUD, world refs |
| `pcs.py` | 5 | ~100 | PC management, set-active, set-current-scene |
| `turns.py` | 7 | ~200 | Submit, advance, regenerate, undo, resolve-proposals, resolve-scene-break |
| `retcon.py` | 5 | ~150 | Retcon post, replay state, accept/try-again/cancel |
| `fork.py` | 5 | ~150 | Fork, branches, lineage, pending forks |
| `scenes.py` | 5 | ~200 | Scene CRUD, end, seed |
| `entities.py` | 9 | ~200 | Resolved entity views (characters, items, locations, lore, factions, monsters), promotion, override |
| `sheets.py` | 3 | ~150 | Sheet CRUD, bulk-create-missing |
| `continuity.py` | 5 | ~150 | Facts, commitments, ledger, contradictions |
| `images.py` | 17 | ~400 | Image generation, management, jobs, settings |
| `export.py` | 4 | ~100 | Export, adapters, preview, history |
| `reviews.py` | 3 | ~100 | Review queue approve/reject/update |
| `__init__.py` | 0 | ~30 | Re-export combined router |
| `helpers.py` | 0 | ~200 | Shared helpers (_require_campaign_row, _load_campaign_config, etc.) |

**Router composition in `__init__.py`:**

```python
from fastapi import APIRouter
from .core import router as core_router
from .settings import router as settings_router
# ... etc

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
router.include_router(core_router)
router.include_router(settings_router)
# ... etc
```

**`main.py` change:** Replace single `campaigns_router` import with the combined router from the package.

### Step 2: Shared Helpers

Move to `campaigns/helpers.py`:

| Helper | Current Line | Used By |
|--------|-------------|---------|
| `_require_campaign_row()` | 575 | Settings, composition, scenes, entities |
| `_load_campaign_config()` | 582 | Settings GET routes, create |
| `_write_campaign_config()` | 596 | Settings PUT routes, create |
| `_read_routing_blocks()` | 711 | Routing settings |
| `_require_scene_owned()` | 1470 | Scenes |
| `_list_kind()` | 1715 | Entity views |
| `_continuity_for()` | 2157 | Continuity routes |
| `_require_review_owned()` | 2717 | Reviews |
| `_seed_greeting_first_post()` | 122 | Create campaign, seed scene |
| `_img_to_markdown()` | 46 | Greeting helper |
| `_substitute_placeholders()` | 72 | Greeting helper |
| `_resolve_pc_display_name()` | 86 | Greeting helper |
| `_resolve_character_display_name()` | 108 | Greeting helper |

### Step 3: Standard Pagination

Create a reusable pagination dependency:

```python
# backend/src/grimoire/api/pagination.py

@dataclass
class PaginationParams:
    limit: int = Query(default=50, ge=1, le=200)
    offset: int = Query(default=0, ge=0)

PaginationDep = Annotated[PaginationParams, Depends()]
```

Apply to all list endpoints that currently lack pagination:
- `list_campaigns()`, `list_scenes()`, `list_pcs()`, `list_characters()`, `list_items()`, `list_locations()`, `list_lore()`, `list_factions()`, `list_monsters()`, `list_images()`, `list_image_jobs()`

Endpoints that already have `limit` parameters should adopt `PaginationDep` for consistency.

### Step 4: Response Models

Add Pydantic response models for endpoints that currently return `-> Any`. Priority endpoints (most used by frontend):

- `list_campaigns` → `list[CampaignSummary]`
- `get_campaign` → `CampaignDetail`
- `list_scenes` → `list[SceneSummary]`
- `get_scene` → `SceneDetail`
- `list_pcs` → `list[PCEntry]`

Define response models in `backend/src/grimoire/api/campaigns/schemas.py`. These are API-facing models, distinct from domain types.

### Step 5: URL Naming Normalization

Fix snake_case URLs to kebab-case:

| Current | Fixed |
|---------|-------|
| `/library/worlds/{id}/world_diff` | `/library/worlds/{id}/world-diff` |

Verify all endpoints use kebab-case for multi-word path segments. Single-word segments and path parameters are unaffected.

## Scope

### In scope
- Split `campaigns.py` into ~14 sub-modules
- Create shared pagination dependency
- Add response models to high-priority list/get endpoints
- Normalize URL naming to kebab-case
- Move helpers to shared module

### Not in scope
- Adding authentication
- Changing business logic in route handlers
- Moving domain logic out of routes (deferred to service split PRs)
- Full OpenAPI schema coverage for every endpoint

## Verification

1. `pytest` full suite passes (especially `tests/api/`).
2. `campaigns.py` file no longer exists (replaced by `campaigns/` package).
3. Each sub-module is under 500 lines.
4. `ruff check` passes.
5. Frontend can still call all endpoints (no URL changes except `world_diff` → `world-diff`).
6. OpenAPI schema at `/docs` shows response models for priority endpoints.
