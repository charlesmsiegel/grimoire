# Frontend Restructuring

Date: 2026-05-23
Status: Approved
PR: 10 of ~10 in the Grimoire code quality refactor series
Depends on: PR 7 (Router/API consistency)

## Problem

Several frontend files exceed 600 lines with mixed concerns:

| File | Lines | Concern Mix |
|------|-------|-------------|
| `CampaignSettings.tsx` | 1,134 | 9 settings tabs in one component |
| `AppSettings.tsx` | 1,130 | Multiple settings panels in one component |
| `library.ts` (API) | 788 | All library CRUD in one module |
| `CalendarsView.tsx` | 661 | Calendar CRUD + display + editing |
| `PostItem.tsx` | 607 | Post rendering + actions + alternates + images |
| `campaign.ts` (API) | 604 | All campaign API calls in one module |

`usePlayState` mixes REST loading, WebSocket event reduction, performance spans, expression side effects, and command methods.

The API client has no runtime response validation -- backend field renames cause confusing downstream type errors instead of clear validation failures.

Test mock classes (`FakeOrchestrator`, `FakeContinuity`, etc.) are defined inline in multiple test files.

## Solution

Split large components into focused panels. Extract `usePlayState` into sub-hooks. Add runtime response validation at the API boundary. Consolidate test mocks.

## Detailed Design

### Step 1: Split Settings Components

**`CampaignSettings.tsx` → `routes/campaign/settings/` package:**

| Module | Responsibility | Est. Lines |
|--------|---------------|------------|
| `CampaignSettings.tsx` | Tab container, tab navigation | ~100 |
| `GeneralTab.tsx` | Name, description, mechanics, style guide | ~150 |
| `RoutingTab.tsx` | LLM routing configuration | ~150 |
| `TiersTab.tsx` | Model tier assignments | ~100 |
| `ImageGenTab.tsx` | Image generation settings | ~100 |
| `SummariesTab.tsx` | Summary configuration | ~100 |
| `StorageTab.tsx` | Storage/backup settings | ~100 |
| `AdvancedTab.tsx` | Advanced options | ~100 |
| `NarratorTab.tsx` | Narrator voice settings | ~100 |

Each tab is a focused component with its own local state hooks. The container component handles tab switching and renders the active tab.

**`AppSettings.tsx` → `routes/settings/` package:**

Same pattern -- split into feature panels based on the settings sections currently rendered inline.

### Step 2: Split PostItem

**`PostItem.tsx` → `routes/campaign/post/` package:**

| Module | Responsibility |
|--------|---------------|
| `PostItem.tsx` | Container, layout, post header | 
| `PostBody.tsx` | Markdown rendering, image inlining |
| `PostActions.tsx` | Action buttons (regenerate, retcon, delete) |
| `AlternatesBar.tsx` | Alternate swipe UI |
| `PostImages.tsx` | Image gallery for post |

### Step 3: Extract usePlayState Sub-Hooks

**`usePlayState.tsx` → split into:**

| Module | Responsibility |
|--------|---------------|
| `playReducer.ts` | Pure reducer function, action types, state shape |
| `usePlayDataLoader.ts` | Initial REST fetch of campaign, scenes, posts |
| `usePlayStreamEvents.ts` | WebSocket event subscription, event → action mapping |
| `usePlayCommands.ts` | User action dispatch (submit, advance, regenerate, undo) |
| `usePlayState.ts` | Composes the above, exports the unified hook |

`playReducer.ts` is a pure function with no side effects -- easy to unit test. `usePlayStreamEvents.ts` subscribes to WebSocket events and dispatches actions to the reducer. `usePlayCommands.ts` provides async methods that call the API and dispatch result actions.

### Step 4: Split API Modules

**`api/library.ts` (788 lines) → split by resource:**

| Module | Responsibility |
|--------|---------------|
| `api/library/index.ts` | Re-exports, shared cache |
| `api/library/worlds.ts` | World CRUD |
| `api/library/characters.ts` | Character library CRUD |
| `api/library/entities.ts` | Generic entity operations |
| `api/library/compositions.ts` | Composition management |
| `api/library/mechanics.ts` | Mechanics/plugin listing |

**`api/campaign.ts` (604 lines) → split by concern:**

| Module | Responsibility |
|--------|---------------|
| `api/campaign/index.ts` | Re-exports |
| `api/campaign/core.ts` | Campaign CRUD |
| `api/campaign/turns.ts` | Turn submission, advance, undo |
| `api/campaign/scenes.ts` | Scene management |
| `api/campaign/settings.ts` | Settings read/write |

### Step 5: Runtime Response Validation

Add `zod` as a dev/runtime dependency. Define schemas for high-traffic API responses:

```typescript
// api/schemas/campaign.ts
import { z } from "zod";

export const CampaignSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  mechanics_module: z.string().nullable(),
  created_at: z.string(),
  last_played_at: z.string().nullable(),
});

export type CampaignSummary = z.infer<typeof CampaignSummarySchema>;
```

Validation runs in the API client's `request<T>()` function when a schema is provided:

```typescript
async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions & { schema?: z.ZodType<T> } = {},
): Promise<T> {
  // ... existing fetch logic ...
  const data = await parseBody(res);
  if (opts.schema) {
    return opts.schema.parse(data);  // Throws ZodError on mismatch
  }
  return data as T;
}
```

Validation is opt-in per call site. Start with the 5 most-used endpoints; expand coverage over time.

### Step 6: Fix Existing Lint Warnings

- `frontend/src/api/client.ts:59,92` — Remove unused eslint-disable comments
- `frontend/src/routes/CampaignCreate/StepStartingScene.tsx:156` — Memoize `selected` object
- `frontend/src/routes/campaign/InputArea.tsx:126` — Remove unused eslint-disable comment

### Step 7: Consolidate Test Mocks (Backend)

Create `backend/tests/mocks.py` with shared fake classes:

```python
class FakeOrchestrator:
    """Minimal orchestrator stub for API route tests."""
    ...

class FakeContinuity:
    """Minimal continuity stub for API route tests."""
    ...

class FakeCharacters:
    """Minimal characters stub for API route tests."""
    ...
```

Remove inline definitions from `test_campaigns_routes.py` and other test files. Import from the shared module.

## Scope

### In scope
- Split `CampaignSettings.tsx` and `AppSettings.tsx` into tab/panel components
- Split `PostItem.tsx` into sub-components
- Extract `usePlayState` into 4 sub-hooks
- Split `library.ts` and `campaign.ts` API modules
- Add `zod` for opt-in runtime response validation
- Fix 4 frontend lint warnings
- Consolidate backend test mocks

### Not in scope
- Changing UI behavior or appearance
- Adding new frontend features
- Full zod coverage for all endpoints
- Migrating to a different state management library
- Adding frontend tests for split components (follow-up)

## Verification

1. `pnpm lint` passes with zero warnings.
2. `pnpm typecheck` passes.
3. `pnpm test` passes (vitest).
4. Backend `pytest` passes (for mock consolidation).
5. No file in `frontend/src/` exceeds 500 lines (except generated/vendor files).
6. Manual smoke test: campaign settings, play view, post interactions, library management all work.
7. `usePlayState` sub-hooks each have unit tests for their isolated responsibility.
