# PR 10: Frontend Restructuring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split large route components and API modules, extract `usePlayState` into sub-hooks, add `zod` runtime response validation, fix 4 lint warnings, and consolidate backend test mocks.

**Architecture:** Settings components split into tab panels. API modules split by resource. `usePlayState` decomposes into pure reducer + data loader + stream events + commands. `zod` validates responses opt-in at the API boundary. Backend test mocks move to a shared module.

**Tech Stack:** TypeScript, React, Vite, zod, Vitest, Python (backend test mocks)

---

### Task 1: Fix frontend lint warnings

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/CampaignCreate/StepStartingScene.tsx`
- Modify: `frontend/src/routes/campaign/InputArea.tsx`

- [ ] **Step 1: Remove unused eslint-disable comments in client.ts (lines 59, 92)**

The `// eslint-disable-next-line no-console` comments are no longer needed — remove them if the console.warn calls below them are acceptable, or adjust accordingly.

- [ ] **Step 2: Memoize `selected` object in StepStartingScene.tsx (line 156)**

Wrap the `selected` object in `useMemo` to stabilize the reference.

- [ ] **Step 3: Remove unused eslint-disable in InputArea.tsx (line 126)**

- [ ] **Step 4: Verify**

Run: `cd frontend && pnpm lint`
Expected: Zero warnings

- [ ] **Step 5: Commit**

```
git add frontend/src/
git commit -m "fix(frontend): resolve 4 eslint warnings"
```

---

### Task 2: Split CampaignSettings.tsx into tab panels

**Files:**
- Create: `frontend/src/routes/campaign/settings/CampaignSettings.tsx` (tab container, ~100 lines)
- Create: `frontend/src/routes/campaign/settings/GeneralTab.tsx`
- Create: `frontend/src/routes/campaign/settings/RoutingTab.tsx`
- Create: `frontend/src/routes/campaign/settings/TiersTab.tsx`
- Create: `frontend/src/routes/campaign/settings/ImageGenTab.tsx`
- Create: `frontend/src/routes/campaign/settings/SummariesTab.tsx`
- Create: `frontend/src/routes/campaign/settings/StorageTab.tsx`
- Create: `frontend/src/routes/campaign/settings/AdvancedTab.tsx`
- Create: `frontend/src/routes/campaign/settings/NarratorTab.tsx`
- Create: `frontend/src/routes/campaign/settings/index.ts`

- [ ] **Step 1: Read the existing CampaignSettings.tsx to identify tab boundaries**

Read `frontend/src/routes/CampaignSettings.tsx`. Identify where each tab's JSX starts and ends. Each tab typically has its own local state (useState hooks for that tab's data) and a save handler.

- [ ] **Step 2: Create settings/ directory and extract tabs one at a time**

For each tab:
1. Create `{TabName}Tab.tsx`
2. Move the tab's JSX, hooks, and handlers into it
3. Export as a named component
4. Import in the parent `CampaignSettings.tsx`

- [ ] **Step 3: Update imports in router config**

Update whatever route definition points to `CampaignSettings` to import from the new location.

- [ ] **Step 4: Verify in browser**

Run dev server: `cd frontend && pnpm dev`
Navigate to campaign settings. Verify all tabs render and save correctly.

- [ ] **Step 5: Commit**

```
git add frontend/src/routes/campaign/settings/
git commit -m "refactor(frontend): split CampaignSettings into tab panel components"
```

---

### Task 3: Split AppSettings.tsx

Same pattern as Task 2, splitting into feature panels. Read the file to identify panel boundaries.

- [ ] **Step 1: Extract panels, update imports, verify in browser, commit**

```
git commit -m "refactor(frontend): split AppSettings into panel components"
```

---

### Task 4: Extract usePlayState sub-hooks

**Files:**
- Create: `frontend/src/routes/campaign/playReducer.ts`
- Create: `frontend/src/routes/campaign/usePlayDataLoader.ts`
- Create: `frontend/src/routes/campaign/usePlayStreamEvents.ts`
- Create: `frontend/src/routes/campaign/usePlayCommands.ts`
- Modify: `frontend/src/routes/campaign/usePlayState.tsx`

- [ ] **Step 1: Extract playReducer.ts**

Move the reducer function and action types out of `usePlayState.tsx` into a pure module:

```typescript
// playReducer.ts
export type PlayState = { /* ... */ };
export type PlayAction = { /* ... */ };
export function playReducer(state: PlayState, action: PlayAction): PlayState { /* ... */ }
```

This is a pure function with no side effects — easy to unit test.

- [ ] **Step 2: Extract usePlayDataLoader.ts**

Move the initial REST fetch logic (campaign, scenes, posts loading) into its own hook:

```typescript
export function usePlayDataLoader(campaignId: string) {
  // useState for loaded data, useEffect for initial fetch
  // Returns: { campaign, scenes, posts, loading, error }
}
```

- [ ] **Step 3: Extract usePlayStreamEvents.ts**

Move WebSocket event subscription and event → action mapping:

```typescript
export function usePlayStreamEvents(
  campaignId: string,
  dispatch: React.Dispatch<PlayAction>,
) {
  // Subscribe to WebSocket events, dispatch actions
}
```

- [ ] **Step 4: Extract usePlayCommands.ts**

Move user action dispatch methods (submit, advance, regenerate, undo):

```typescript
export function usePlayCommands(campaignId: string, dispatch: React.Dispatch<PlayAction>) {
  return {
    submit: async (text: string) => { /* ... */ },
    advance: async () => { /* ... */ },
    regenerate: async () => { /* ... */ },
    undo: async () => { /* ... */ },
  };
}
```

- [ ] **Step 5: Compose in usePlayState.tsx**

The main hook becomes a thin composition layer:

```typescript
export function usePlayState(campaignId: string) {
  const [state, dispatch] = useReducer(playReducer, initialState);
  const data = usePlayDataLoader(campaignId);
  usePlayStreamEvents(campaignId, dispatch);
  const commands = usePlayCommands(campaignId, dispatch);
  return { ...state, ...data, ...commands };
}
```

- [ ] **Step 6: Verify in browser**

Run dev server. Navigate to the play view. Submit a turn, advance, regenerate. Verify WebSocket events update the UI.

- [ ] **Step 7: Commit**

```
git add frontend/src/routes/campaign/playReducer.ts frontend/src/routes/campaign/usePlay*.ts*
git commit -m "refactor(frontend): extract usePlayState into focused sub-hooks"
```

---

### Task 5: Split API modules

**Files:**
- Create: `frontend/src/api/library/` (index.ts, worlds.ts, characters.ts, entities.ts, compositions.ts, mechanics.ts)
- Create: `frontend/src/api/campaign/` (index.ts, core.ts, turns.ts, scenes.ts, settings.ts)

- [ ] **Step 1: Split library.ts by resource**

Read `frontend/src/api/library.ts`. Group functions by resource (worlds, characters, entities, compositions, mechanics). Create sub-modules and re-export from `index.ts`.

- [ ] **Step 2: Split campaign.ts by concern**

Same pattern for `frontend/src/api/campaign.ts`.

- [ ] **Step 3: Update all import sites**

Grep for `from "../api/library"` and `from "../api/campaign"` and update to the new paths, or ensure `index.ts` re-exports everything for backward compatibility.

- [ ] **Step 4: Run typecheck and tests**

Run: `cd frontend && pnpm typecheck && pnpm test`
Expected: Pass

- [ ] **Step 5: Commit**

```
git commit -m "refactor(frontend): split library.ts and campaign.ts API modules by resource"
```

---

### Task 6: Add zod runtime response validation

**Files:**
- Modify: `frontend/package.json` (add zod dependency)
- Create: `frontend/src/api/schemas/campaign.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Install zod**

Run: `cd frontend && pnpm add zod`

- [ ] **Step 2: Define schemas for priority endpoints**

```typescript
// frontend/src/api/schemas/campaign.ts
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

- [ ] **Step 3: Add optional schema parameter to request()**

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
    return opts.schema.parse(data) as T;
  }
  return data as T;
}
```

- [ ] **Step 4: Apply to listCampaigns as the first opt-in call**

```typescript
export function listCampaigns() {
  return api.get("/api/campaigns", { schema: z.array(CampaignSummarySchema) });
}
```

- [ ] **Step 5: Commit**

```
git commit -m "feat(frontend): add zod runtime response validation (opt-in)"
```

---

### Task 7: Consolidate backend test mocks

**Files:**
- Create: `backend/tests/mocks.py`
- Modify: `backend/tests/api/test_campaigns_routes.py`

- [ ] **Step 1: Extract shared fake classes**

Read `test_campaigns_routes.py` (lines 8-128) for `FakeOrchestrator`, `FakeContinuity`, `FakeCharacters`, etc. Move them to `tests/mocks.py`.

- [ ] **Step 2: Update test files to import from mocks.py**

Replace inline class definitions with `from tests.mocks import FakeOrchestrator, ...`

- [ ] **Step 3: Run tests, commit**

```
git add backend/tests/mocks.py backend/tests/api/
git commit -m "refactor(tests): consolidate shared mock classes into tests/mocks.py"
```

---

### Task 8: Final verification

- [ ] **Step 1: Frontend lint + typecheck**

Run: `cd frontend && pnpm lint && pnpm typecheck`
Expected: Zero warnings, zero errors

- [ ] **Step 2: Frontend tests**

Run: `cd frontend && pnpm test`
Expected: All pass

- [ ] **Step 3: Backend tests**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 4: Verify no file exceeds 500 lines**

Run: `cd frontend && find src -name "*.tsx" -o -name "*.ts" | xargs wc -l | sort -rn | head -20`
Expected: No file over 500 lines (except generated/vendor)
