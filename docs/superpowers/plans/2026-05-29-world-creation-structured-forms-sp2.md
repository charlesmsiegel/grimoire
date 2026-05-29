# Structured Entity Forms — All Kinds (SP2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the SP1 structured-form system to location, item, monster, faction, and lore, and add a token-estimate badge to every entity list card.

**Architecture:** Reuse SP1's `<EntityForm>`, widgets, and drift-guard machinery unchanged. Add one `EntityDescriptor` per kind to `entitySchemas.ts` and register it; the editor already routes any registered descriptor. Add `estimateEntityTokens` badges to `EntityListView` cards. Generalize the drift guard to every registered kind via per-kind property fixtures.

**Tech Stack:** React 18 + TypeScript, Vitest + @testing-library/react; FastAPI + pytest.

Spec: `docs/superpowers/specs/2026-05-28-world-creation-structured-forms-all-kinds-design.md`

> SP1 already added `collapsed?: boolean` to `EntitySectionDescriptor` and `<EntityForm>` renders collapsed sections as `<details>`. SP2 only *uses* that; no renderer change needed.

---

## Task 1: Location, Item, Monster descriptors

**Files:**
- Modify: `frontend/src/routes/library/entitySchemas.ts`
- Test: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/library/__tests__/entitySchemas.test.ts`:

```ts
describe("location/item/monster descriptors", () => {
  it("registers location with kind + connections", () => {
    const keys = managedKeys(getDescriptor("location")!);
    expect(keys).toEqual(expect.arrayContaining(["kind", "parent_id", "connections", "coordinates"]));
  });
  it("registers item with provenance + current_holder", () => {
    const keys = managedKeys(getDescriptor("item")!);
    expect(keys).toEqual(expect.arrayContaining(["provenance", "current_holder"]));
  });
  it("registers monster with category + abilities", () => {
    const keys = managedKeys(getDescriptor("monster")!);
    expect(keys).toEqual(expect.arrayContaining(["category", "abilities", "weaknesses"]));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: FAIL (`getDescriptor("location")` is undefined → `!` deref → throws).

- [ ] **Step 3: Add the three descriptors and register them**

In `frontend/src/routes/library/entitySchemas.ts`, add before `const REGISTRY`:

```ts
const LOCATION: EntityDescriptor = {
  kind: "location",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "kind",
          label: "Kind",
          widget: "enum",
          options: [
            { value: "city", label: "City" },
            { value: "building", label: "Building" },
            { value: "room", label: "Room" },
            { value: "region", label: "Region" },
            { value: "outdoor", label: "Outdoor" },
            { value: "other", label: "Other" },
          ],
        },
        { key: "parent_id", label: "Parent location", widget: "ref", refKinds: ["location"] },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Geography",
      fields: [
        { key: "climate_zone", label: "Climate zone", widget: "text" },
        { key: "indoor", label: "Indoor", widget: "bool" },
        {
          key: "coordinates",
          label: "Coordinates",
          widget: "object",
          fields: [
            { key: "x", label: "X", widget: "number" },
            { key: "y", label: "Y", widget: "number" },
          ],
        },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "permanent_features", label: "Permanent features", widget: "stringList" },
        { key: "typical_occupants", label: "Typical occupants", widget: "stringList" },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
    {
      title: "Connections",
      collapsed: true,
      fields: [
        {
          key: "connections",
          label: "Connections",
          widget: "objectList",
          fields: [
            { key: "to", label: "To", widget: "ref", refKinds: ["location"] },
            { key: "via", label: "Via", widget: "text" },
            { key: "duration_min", label: "Duration (min)", widget: "number" },
            { key: "notes", label: "Notes", widget: "text" },
          ],
        },
      ],
    },
  ],
};

const ITEM: EntityDescriptor = {
  kind: "item",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "provenance", label: "Provenance", widget: "text" },
        { key: "current_holder", label: "Current holder", widget: "ref", refKinds: ["character"] },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
  ],
};

const MONSTER: EntityDescriptor = {
  kind: "monster",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "category",
          label: "Category",
          widget: "enum",
          options: [
            { value: "beast", label: "Beast" },
            { value: "undead", label: "Undead" },
            { value: "dragon", label: "Dragon" },
            { value: "fey", label: "Fey" },
            { value: "demon", label: "Demon" },
            { value: "aberration", label: "Aberration" },
            { value: "humanoid", label: "Humanoid" },
            { value: "construct", label: "Construct" },
            { value: "elemental", label: "Elemental" },
            { value: "other", label: "Other" },
          ],
        },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "threat_level", label: "Threat level", widget: "text" },
        { key: "habitat", label: "Habitat", widget: "stringList" },
        { key: "abilities", label: "Abilities", widget: "stringList" },
        { key: "weaknesses", label: "Weaknesses", widget: "stringList" },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
  ],
};
```

Update the registry:

```ts
const REGISTRY: Partial<Record<EntityKind, EntityDescriptor>> = {
  character: CHARACTER,
  location: LOCATION,
  item: ITEM,
  monster: MONSTER,
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/entitySchemas.ts frontend/src/routes/library/__tests__/entitySchemas.test.ts
git commit -m "feat(frontend): location/item/monster descriptors (#441)"
```

---

## Task 2: Faction and Lore descriptors

**Files:**
- Modify: `frontend/src/routes/library/entitySchemas.ts`
- Test: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `entitySchemas.test.ts`:

```ts
describe("faction/lore descriptors", () => {
  it("registers faction with membership refLists", () => {
    const keys = managedKeys(getDescriptor("faction")!);
    expect(keys).toEqual(expect.arrayContaining(["leaders", "members", "allies", "rivals", "base_location"]));
  });
  it("registers lore using title as the label key (not name)", () => {
    const keys = managedKeys(getDescriptor("lore")!);
    expect(keys).toContain("title");
    expect(keys).not.toContain("name");
    expect(keys).toEqual(expect.arrayContaining(["secrecy", "keywords", "position", "selective_logic"]));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: FAIL (`getDescriptor("faction")` undefined).

- [ ] **Step 3: Add the two descriptors and register them**

Add before `const REGISTRY`:

```ts
const FACTION: EntityDescriptor = {
  kind: "faction",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "kind", label: "Kind", widget: "text" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "base_location", label: "Base location", widget: "ref", refKinds: ["location"] },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
    {
      title: "Membership",
      fields: [
        { key: "leaders", label: "Leaders", widget: "refList", refKinds: ["character"] },
        { key: "members", label: "Members", widget: "refList", refKinds: ["character"] },
        { key: "allies", label: "Allies", widget: "refList", refKinds: ["faction"] },
        { key: "rivals", label: "Rivals", widget: "refList", refKinds: ["faction"] },
      ],
    },
  ],
};

const LORE: EntityDescriptor = {
  kind: "lore",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "title", label: "Title", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "tags", label: "Tags", widget: "tags" },
        { key: "keywords", label: "Keywords", widget: "tags" },
        {
          key: "secrecy",
          label: "Secrecy",
          widget: "enum",
          options: [
            { value: "public", label: "Public" },
            { value: "common-knowledge", label: "Common knowledge" },
            { value: "common-knowledge-among-kindred", label: "Common knowledge (among kindred)" },
            { value: "restricted", label: "Restricted" },
            { value: "secret", label: "Secret" },
          ],
        },
      ],
    },
    {
      title: "Relations",
      fields: [
        { key: "related_locations", label: "Related locations", widget: "refList", refKinds: ["location"] },
        { key: "related_factions", label: "Related factions", widget: "refList", refKinds: ["faction"] },
        { key: "related_characters", label: "Related characters", widget: "refList", refKinds: ["character"] },
      ],
    },
    {
      title: "Activation (lorebook)",
      collapsed: true,
      fields: [
        { key: "secondary_keys", label: "Secondary keys", widget: "tags" },
        {
          key: "selective_logic",
          label: "Selective logic",
          widget: "enum",
          options: [
            { value: "and_any", label: "AND any" },
            { value: "and_all", label: "AND all" },
            { value: "not_any", label: "NOT any" },
            { value: "not_all", label: "NOT all" },
          ],
        },
        { key: "constant", label: "Constant", widget: "bool" },
        { key: "enabled", label: "Enabled", widget: "bool" },
        { key: "priority", label: "Priority", widget: "number" },
        { key: "probability", label: "Probability", widget: "number" },
        {
          key: "position",
          label: "Position",
          widget: "enum",
          options: [
            { value: "before_cast", label: "Before cast" },
            { value: "after_cast", label: "After cast" },
            { value: "at_depth", label: "At depth" },
            { value: "archive", label: "Archive" },
          ],
        },
        { key: "at_depth", label: "At depth", widget: "number" },
        { key: "scan_depth", label: "Scan depth", widget: "number" },
        { key: "case_sensitive", label: "Case sensitive", widget: "bool" },
        { key: "match_whole_words", label: "Match whole words", widget: "bool" },
        { key: "comment", label: "Comment", widget: "textarea", rows: 2 },
      ],
    },
  ],
};
```

Update the registry to include `faction: FACTION, lore: LORE`.

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/entitySchemas.ts frontend/src/routes/library/__tests__/entitySchemas.test.ts
git commit -m "feat(frontend): faction + lore descriptors (#441)"
```

---

## Task 3: Token badges on entity list cards

**Files:**
- Modify: `frontend/src/routes/library/EntityListView.tsx` (`EntityListBody`, card render `:262-298`)
- Test: `frontend/src/routes/library/__tests__/EntityListView.test.tsx` (existing — add a case)

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/routes/library/__tests__/EntityListView.test.tsx` (follow the file's existing mock setup; if it mocks `libraryApi.listEntities`, reuse it). New case:

```tsx
it("shows a token badge on each entity card", async () => {
  vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
    {
      asset_id: "alistair",
      world_id: "w1",
      kind: "character",
      name: "Alistair",
      frontmatter: { name: "Alistair" },
      body: "a".repeat(40),
      tags: [],
    } as never,
  ]);
  render(
    <MemoryRouter initialEntries={["/library/worlds/w1/characters"]}>
      <Routes>
        <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
      </Routes>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("Alistair")).toBeInTheDocument());
  expect(screen.getByText(/tokens/)).toBeInTheDocument();
});
```

> If `EntityListView.test.tsx` does not exist yet, create it with the same `vi.mock("../../../api/library", …)` pattern used in `EntityEditorView.test.tsx`, mocking `listEntities` and `dependents`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityListView.test.tsx`
Expected: FAIL (no token text on cards).

- [ ] **Step 3: Add the badge to the card**

In `EntityListView.tsx`, add imports:

```tsx
import { TokenBadge } from "../../components/TokenBadge";
```

In `EntityListBody`, within the `<li className="library-card">` for each entity (after the `<Link>` block, near `library-card-meta`), compute the text and render the badge. The list mixes `LibraryEntity` and `Greeting`; greetings have no `frontmatter`:

```tsx
const tokenText =
  "frontmatter" in e ? `${JSON.stringify(e.frontmatter)}\n${e.body ?? ""}` : (e.body ?? "");
```

and render inside the card (e.g. just below the `<Link>`):

```tsx
<p className="library-card-meta">
  <TokenBadge text={tokenText} />
</p>
```

(Place it so it doesn't break the existing `<Link>`; the badge sits in the card footer, not inside the anchor.)

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityListView.test.tsx && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/EntityListView.tsx frontend/src/routes/library/__tests__/EntityListView.test.tsx
git commit -m "feat(frontend): token badge on entity list cards (#441)"
```

---

## Task 4: Extend the drift guard to all kinds

Generalize SP1's character-only guard so every registered descriptor is checked against its model's schema property names.

**Files:**
- Create: `frontend/src/routes/library/__tests__/fixtures/location-schema-properties.json`
- Create: `frontend/src/routes/library/__tests__/fixtures/item-schema-properties.json`
- Create: `frontend/src/routes/library/__tests__/fixtures/monster-schema-properties.json`
- Create: `frontend/src/routes/library/__tests__/fixtures/faction-schema-properties.json`
- Create: `frontend/src/routes/library/__tests__/fixtures/lore-schema-properties.json`
- Modify: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`
- Modify: `backend/tests/api/test_library_routes.py`

- [ ] **Step 1: Create the fixtures (exact model field names)**

`location-schema-properties.json`:

```json
["world_id","id","name","parent_id","kind","aliases","tags","climate_zone","indoor","coordinates","permanent_features","connections","typical_occupants","description","body","extras"]
```

`item-schema-properties.json`:

```json
["world_id","id","name","aliases","tags","provenance","current_holder","description","body","extras"]
```

`monster-schema-properties.json`:

```json
["world_id","id","name","category","aliases","tags","threat_level","habitat","abilities","weaknesses","description","body","extras"]
```

`faction-schema-properties.json`:

```json
["world_id","id","name","kind","base_location","leaders","members","allies","rivals","tags","description","body","extras"]
```

`lore-schema-properties.json`:

```json
["world_id","id","title","body","tags","keywords","related_locations","related_factions","related_characters","secrecy","secondary_keys","selective_logic","constant","enabled","case_sensitive","match_whole_words","priority","probability","position","at_depth","scan_depth","comment","import_source"]
```

- [ ] **Step 2: Generalize the front-end drift test**

Replace the single `character descriptor drift` block in `entitySchemas.test.ts` with a per-kind loop:

```ts
import characterProps from "./fixtures/character-schema-properties.json";
import locationProps from "./fixtures/location-schema-properties.json";
import itemProps from "./fixtures/item-schema-properties.json";
import monsterProps from "./fixtures/monster-schema-properties.json";
import factionProps from "./fixtures/faction-schema-properties.json";
import loreProps from "./fixtures/lore-schema-properties.json";
import type { EntityKind } from "../../../api/library";

const FIXTURES: Record<string, string[]> = {
  character: characterProps,
  location: locationProps,
  item: itemProps,
  monster: monsterProps,
  faction: factionProps,
  lore: loreProps,
};

describe("descriptor drift", () => {
  for (const [kind, props] of Object.entries(FIXTURES)) {
    it(`${kind} descriptor only manages keys in its schema`, () => {
      const allowed = new Set(props);
      for (const key of managedKeys(getDescriptor(kind as EntityKind)!)) {
        expect(allowed.has(key), `${kind} key '${key}' missing from schema`).toBe(true);
      }
    });
  }
});
```

(Remove the now-superseded single-character drift block and its lone `import properties …` line.)

- [ ] **Step 3: Run the front-end drift test**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: PASS for all six kinds.

- [ ] **Step 4: Generalize the backend fixture-match test**

Replace `test_entity_schema_character_matches_frontend_fixture` in `backend/tests/api/test_library_routes.py` with a parametrized test:

```python
import pytest


@pytest.mark.parametrize(
    "kind",
    ["character", "location", "item", "monster", "faction", "lore"],
)
def test_entity_schema_matches_frontend_fixture(client, kind: str) -> None:
    """Each committed front-end fixture must equal the model's schema property
    names, so a backend field rename forces a fixture update (which re-checks
    the descriptor in entitySchemas.test.ts)."""
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/routes/library/__tests__/fixtures"
        / f"{kind}-schema-properties.json"
    )
    expected = set(json.loads(fixture.read_text()))
    props = set(client.get(f"/api/library/entity-schemas/{kind}").json()["properties"].keys())
    assert props == expected
```

- [ ] **Step 5: Run the backend test (slow — ~3-6 min)**

Run: `cd backend && uv run pytest tests/api/test_library_routes.py -k entity_schema -v`
Expected: PASS (8 cases: route, 404, 6 fixture-match params). If a fixture-match fails, the model changed — update that kind's fixture, then re-run the front-end drift test.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/__tests__/fixtures/ frontend/src/routes/library/__tests__/entitySchemas.test.ts backend/tests/api/test_library_routes.py
git commit -m "test: extend schema-drift guard to all entity kinds (#441)"
```

---

## Task 5: Full verification

- [ ] **Step 1: Front-end gate**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm vitest run`
Expected: all PASS.

- [ ] **Step 2: Tidy new files with prettier (repo isn't globally prettier-clean)**

Run: `cd frontend && pnpm exec prettier --write src/routes/library/entitySchemas.ts src/routes/library/EntityListView.tsx "src/routes/library/__tests__/EntityListView.test.tsx" "src/routes/library/__tests__/entitySchemas.test.ts"`
Then re-run typecheck + the touched tests to confirm formatting didn't break anything.

- [ ] **Step 3: Backend gate for touched files**

Run: `cd backend && uv run ruff check tests/api/test_library_routes.py && uv run ruff format --check tests/api/test_library_routes.py`
Expected: PASS. (`pytest` already run in Task 4.)

- [ ] **Step 4: Manual smoke (optional)**

Open a world → a location, an item, a faction, a lore entry → confirm structured forms, the collapsed Activation/Connections sections, the Advanced fallback, and token badges on the list cards.

- [ ] **Step 5: Commit any formatting changes**

```bash
git add -A
git commit -m "style(frontend): prettier tidy for SP2 files (#441)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** descriptors for location/item/monster/faction/lore (T1,T2); collapsed sections reused from SP1 (Connections, Activation); list-card token badges incl. greeting body-only path (T3); drift guard for every kind via per-kind fixtures + parametrized backend test (T4). Greetings keep their bespoke form (no descriptor) — unchanged, matches spec non-goal.
- **Type consistency:** all descriptors use the `Widget` union and `refKinds: EntityKind[]` from SP1; `managedKeys`/`getDescriptor` unchanged; fixtures list exact `types/world.py` field names (lore uses `title`, not `name`).
- **Lore title/name:** descriptor manages `title`; `name` deliberately absent (asserted in T2 test). Backend keeps `name`↔`title` in sync (`library/service.py`), so editing `title` still lists correctly.
