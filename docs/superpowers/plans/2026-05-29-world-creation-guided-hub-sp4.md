# Guided World Hub (SP4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (subagents can't write in this env). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the world landing page a hub that orients the user: setup progress, per-kind entity counts, and contextual "suggested next" actions.

**Architecture:** A thin backend `GET /library/worlds/{world_id}/summary` computes counts (via the existing `list_in_world` per kind) plus `has_description`/`has_genre` from the world meta. A new `<WorldHub>` renders that summary as the world index route (replacing the `Navigate to="characters"` redirect); `WorldDetailView` gains an "Overview" tab linking back to the hub.

**Tech Stack:** FastAPI + pytest (backend); React 18 + TypeScript, Vitest + @testing-library/react (frontend).

Spec: `docs/superpowers/specs/2026-05-28-world-creation-guided-hub-design.md`

**Descoped from this plan (logged, not silently dropped — see spec §4):**
- Tab regrouping into "Contents" vs "Settings" and the action-menu condensation — pure visual polish; deferred.
- The "suggested next" → create-intent deep-link that auto-opens a tab's create form — deferred; suggestions link to the relevant tab, where the user clicks "+ New …" (one extra click). A follow-up can add `?create=1`.

---

## Task 1: Backend `/summary` endpoint

**Files:**
- Modify: `backend/src/grimoire/api/library.py` (add route + a `WorldSummary` response model)
- Test: `backend/tests/api/test_library_routes.py`

- [ ] **Step 1: Write the failing test**

`FakeLibrary` already implements `get_world` and `list_in_world` (returns one character). Add:

```python
def test_world_summary_returns_counts_and_flags(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/worlds/wod-london/summary")
    assert response.status_code == 200
    body = response.json()
    # FakeLibrary.list_in_world returns one entity for every kind.
    assert body["counts"]["characters"] == 1
    assert set(body["counts"]) == {
        "characters", "locations", "items", "lore", "factions", "monsters", "greetings",
    }
    assert body["has_description"] is False  # FakeLibrary world has empty description
    assert body["has_genre"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py::test_world_summary_returns_counts_and_flags -v`
Expected: FAIL with 404 (route not found).

- [ ] **Step 3: Implement the route**

In `backend/src/grimoire/api/library.py`, add a response model near the other schema classes:

```python
class WorldSummaryResponse(BaseModel):
    counts: dict[str, int]
    has_description: bool
    has_genre: bool
```

Add the route (place it with the other `/library/worlds/{world_id}/...` GETs, BEFORE the catch-all `/library/worlds/{world_id}/{kind}` at line ~284 so it isn't shadowed):

```python
_SUMMARY_KINDS = {
    "characters": "character",
    "locations": "location",
    "items": "item",
    "lore": "lore",
    "factions": "faction",
    "monsters": "monster",
    "greetings": "greeting",
}


@router.get("/library/worlds/{world_id}/summary")
async def world_summary(world_id: str, library: LibraryDep) -> WorldSummaryResponse:
    try:
        world = await library.get_world(world_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    counts: dict[str, int] = {}
    for plural, singular in _SUMMARY_KINDS.items():
        entities = await library.list_in_world(world_id, singular)
        counts[plural] = len(entities)
    return WorldSummaryResponse(
        counts=counts,
        has_description=bool((world.description or "").strip()),
        has_genre=bool((world.genre or "").strip()),
    )
```

- [ ] **Step 4: Run the test (slow — ~3-6 min)**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py -k "world_summary or entity_schema" -v`
Expected: PASS (summary + the existing entity_schema cases).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run ruff format src/grimoire/api/library.py tests/api/test_library_routes.py && uv run ruff check src/grimoire/api/library.py
git add backend/src/grimoire/api/library.py backend/tests/api/test_library_routes.py
git commit -m "feat(api): world summary endpoint for the hub (#441)"
```

---

## Task 2: `worldSummary` API client

**Files:**
- Modify: `frontend/src/api/library/worlds.ts` (add type + client method)
- Test: covered via the WorldHub component test (Task 3) which mocks this method.

- [ ] **Step 1: Add the type and client method**

In `worlds.ts`, add the interface near `WorldMeta`:

```ts
export interface WorldSummary {
  counts: Record<string, number>;
  has_description: boolean;
  has_genre: boolean;
}
```

Add to the `libraryApi` object (near `getWorld`):

```ts
worldSummary: (worldId: string) =>
  request<WorldSummary>("GET", `/library/worlds/${encodeURIComponent(worldId)}/summary`),
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/library/worlds.ts
git commit -m "feat(frontend): worldSummary API client (#441)"
```

---

## Task 3: `<WorldHub>` component

**Files:**
- Create: `frontend/src/routes/library/WorldHub.tsx`
- Test: `frontend/src/routes/library/__tests__/WorldHub.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { WorldHub } from "../WorldHub";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return { ...actual, libraryApi: { ...actual.libraryApi, worldSummary: vi.fn() } };
});

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/library/worlds/w1"]}>
      <Routes>
        <Route path="/library/worlds/:worldId" element={<WorldHub />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorldHub", () => {
  it("shows per-kind counts and an add affordance for empty kinds", async () => {
    vi.mocked(libraryModule.libraryApi.worldSummary).mockResolvedValue({
      counts: { characters: 3, locations: 0, items: 1, lore: 2, factions: 0, monsters: 0, greetings: 1 },
      has_description: true,
      has_genre: true,
    });
    renderHub();
    await waitFor(() => expect(screen.getByText("Characters")).toBeInTheDocument());
    expect(screen.getByText("3")).toBeInTheDocument();
    // Empty kinds surface a "suggested next" add affordance.
    expect(screen.getByText(/Add a location/i)).toBeInTheDocument();
  });

  it("reflects setup progress from flags + counts", async () => {
    vi.mocked(libraryModule.libraryApi.worldSummary).mockResolvedValue({
      counts: { characters: 0, locations: 0, items: 0, lore: 0, factions: 0, monsters: 0, greetings: 0 },
      has_description: false,
      has_genre: false,
    });
    renderHub();
    await waitFor(() => expect(screen.getByText(/World setup/i)).toBeInTheDocument());
    expect(screen.getByText(/0%/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/WorldHub.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `WorldHub.tsx`**

```tsx
import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { libraryApi, type WorldSummary } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

const KIND_TABS: { plural: string; label: string; singular: string }[] = [
  { plural: "characters", label: "Characters", singular: "character" },
  { plural: "locations", label: "Locations", singular: "location" },
  { plural: "items", label: "Items", singular: "item" },
  { plural: "lore", label: "Lore", singular: "lore entry" },
  { plural: "factions", label: "Factions", singular: "faction" },
  { plural: "monsters", label: "Monsters", singular: "monster" },
  { plural: "greetings", label: "Greetings", singular: "greeting" },
];

/** Checklist that drives the setup-progress bar. Intentionally simple guidance. */
function checklist(summary: WorldSummary) {
  return [
    { ok: summary.has_description, label: "Add a description", to: "meta" },
    { ok: summary.has_genre, label: "Set a genre", to: "meta" },
    { ok: (summary.counts.characters ?? 0) > 0, label: "Add a character", to: "characters" },
    { ok: (summary.counts.locations ?? 0) > 0, label: "Add a location", to: "locations" },
    { ok: (summary.counts.greetings ?? 0) > 0, label: "Write an opening greeting", to: "greetings" },
  ];
}

export function WorldHub() {
  const { worldId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.worldSummary(worldId), [worldId]),
  );

  return (
    <section className="world-hub">
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && <WorldHubBody summary={data} worldId={worldId} />}
      </AsyncBoundary>
    </section>
  );
}

function WorldHubBody({ summary, worldId }: { summary: WorldSummary; worldId: string }) {
  const base = `/library/worlds/${encodeURIComponent(worldId)}`;
  const items = checklist(summary);
  const done = items.filter((i) => i.ok).length;
  const percent = Math.round((done / items.length) * 100);
  const unmet = items.filter((i) => !i.ok);

  return (
    <>
      <div className="world-hub-progress">
        <h4>World setup · {percent}%</h4>
        <div className="world-hub-progress-bar" aria-hidden>
          <span style={{ width: `${percent}%` }} />
        </div>
        <ul className="world-hub-checklist">
          {items.map((i) => (
            <li key={i.label} className={i.ok ? "done" : "todo"}>
              {i.ok ? "✓" : "▢"} <Link to={`${base}/${i.to}`}>{i.label}</Link>
            </li>
          ))}
        </ul>
      </div>

      <h4>Contents</h4>
      <ul className="world-hub-counts">
        {KIND_TABS.map((k) => {
          const n = summary.counts[k.plural] ?? 0;
          return (
            <li key={k.plural} className={n === 0 ? "empty" : ""}>
              <Link to={`${base}/${k.plural}`}>
                <span className="world-hub-count">{n}</span>
                <span className="world-hub-kind">{k.label}</span>
                {n === 0 && <span className="world-hub-add">Add a {k.singular}</span>}
              </Link>
            </li>
          );
        })}
      </ul>

      {unmet.length > 0 && (
        <>
          <h4>Suggested next</h4>
          <ul className="world-hub-suggestions">
            {unmet.map((i) => (
              <li key={i.label}>
                <Link to={`${base}/${i.to}`}>{i.label}</Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
```

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/WorldHub.test.tsx && pnpm typecheck`
Expected: PASS.

> Note: the first test asserts "Add a location" text, which appears BOTH in the empty-count affordance and the suggestions list. `getByText` throws on multiple matches — if that happens, switch the assertion to `screen.getAllByText(/Add a location/i)[0]` or scope with `within`. Adjust when you see the failure.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/WorldHub.tsx frontend/src/routes/library/__tests__/WorldHub.test.tsx
git commit -m "feat(frontend): WorldHub component (#441)"
```

---

## Task 4: Wire the hub as the world index + Overview tab + styles

**Files:**
- Modify: `frontend/src/routes/library/index.tsx` (`:25` index redirect → hub)
- Modify: `frontend/src/routes/library/WorldDetailView.tsx` (`ENTITY_TABS` `:10-20`, add "Overview")
- Modify: `frontend/src/index.css` (hub styles)

- [ ] **Step 1: Replace the index redirect with the hub**

In `index.tsx`, add the import:

```tsx
import { WorldHub } from "./WorldHub";
```

Change the world index route (`:25`) from:

```tsx
<Route index element={<Navigate to="characters" replace />} />
```

to:

```tsx
<Route index element={<WorldHub />} />
```

- [ ] **Step 2: Add an "Overview" tab in `WorldDetailView.tsx`**

Prepend an Overview entry that links to the world base (the index/hub). Since the tabs use relative `to`, an Overview tab needs `end` matching on the base path. Update `ENTITY_TABS` and the `NavLink` render to support an explicit `end` flag:

```tsx
const ENTITY_TABS = [
  { to: ".", label: "Overview", end: true },
  { to: "characters", label: "Characters" },
  { to: "monsters", label: "Monsters" },
  { to: "items", label: "Items" },
  { to: "locations", label: "Locations" },
  { to: "lore", label: "Lore" },
  { to: "factions", label: "Factions" },
  { to: "greetings", label: "Greetings" },
  { to: "meta", label: "Meta" },
  { to: "dependents", label: "Dependent campaigns" },
];
```

In the tab `NavLink` map, pass `end`:

```tsx
{ENTITY_TABS.map((tab) => (
  <NavLink
    key={tab.to}
    to={tab.to}
    end={(tab as { end?: boolean }).end}
    className={({ isActive }) => (isActive ? "world-tab active" : "world-tab")}
  >
    {tab.label}
  </NavLink>
))}
```

- [ ] **Step 3: Add hub styles to `index.css`**

After the structured-form block added in SP1, add:

```css
/* ----- World hub (issue #441) ----- */
.world-hub {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.world-hub-progress-bar {
  height: 8px;
  background: var(--bg-elev);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.world-hub-progress-bar > span {
  display: block;
  height: 100%;
  background: var(--accent, #3a7);
}
.world-hub-checklist,
.world-hub-suggestions {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: 0.85rem;
}
.world-hub-checklist li.done {
  color: var(--fg-muted);
}
.world-hub-counts {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: var(--space-2);
}
.world-hub-counts li {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-elev);
}
.world-hub-counts li.empty {
  border-style: dashed;
}
.world-hub-counts a {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3);
}
.world-hub-count {
  font-size: 1.4rem;
  font-weight: 600;
}
.world-hub-kind {
  color: var(--fg-muted);
  font-size: 0.85rem;
}
.world-hub-add {
  font-size: 0.75rem;
  color: var(--accent, #3a7);
}
```

- [ ] **Step 4: Update the WorldDetailView test if present**

Run `grep -rn "WorldDetailView" frontend/src/routes/library/__tests__ 2>/dev/null`. If a test asserts the index renders the characters list, update it for the Overview/hub. (If none, skip.)

- [ ] **Step 5: Run the full front-end gate**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm vitest run`
Expected: all PASS.

- [ ] **Step 6: Prettier-tidy + commit**

```bash
cd frontend && pnpm exec prettier --write src/routes/library/WorldHub.tsx src/routes/library/index.tsx src/routes/library/WorldDetailView.tsx src/api/library/worlds.ts
git add frontend/src/routes/library/index.tsx frontend/src/routes/library/WorldDetailView.tsx frontend/src/index.css frontend/src/routes/library/WorldHub.tsx frontend/src/api/library/worlds.ts
git commit -m "feat(frontend): wire WorldHub as world index + Overview tab (#441)"
```

---

## Task 5: Backend gate + final verification

- [ ] **Step 1: Backend gate (slow)**

Run: `cd backend && uv run ruff check && uv run ruff format --check && uv run pytest tests/api/test_library_routes.py -v`
Expected: all PASS.

- [ ] **Step 2: Manual smoke (optional)**

Open a world with no description → hub shows low progress + "Add a description"; empty kinds show "Add a …"; counts match; clicking a count/suggestion navigates to that tab; the Overview tab returns to the hub.

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A && git commit -m "style(frontend): hub polish (#441)" || echo "nothing to commit"
```

---

## Self-Review Notes (author)

- **Spec coverage:** summary endpoint with counts + has_description/has_genre (T1); client (T2); hub with progress checklist, counts grid, suggested-next (T3); wired as index + Overview tab + styles (T4). Tab-regrouping and create-intent deep-link explicitly deferred (logged above).
- **Route ordering:** `/summary` must be registered before the catch-all `/{kind}` route, else `summary` is parsed as a kind. Called out in T1 Step 3.
- **Type consistency:** `WorldSummary` shape identical in backend response model and frontend type; `worldSummary` client name used in T2/T3.
- **Test sharp edge:** "Add a location" appears in two places — T3 note explains the `getAllByText` fallback.
