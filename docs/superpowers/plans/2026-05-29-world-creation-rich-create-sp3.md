# Rich Creation + Auto-ID (SP3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (subagents can't write in this env). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Name-first creation — id auto-derived from the name — with a rich world-create form (genre/description/tags/tone) and an entity-create form that reuses the kind's descriptor for its headline fields.

**Architecture:** A pure `slugify` + a shared `<IdField>` (Name input + sticky auto-derived id). `WorldsListView` gets a richer inline create form submitting the full `meta` (verified: `world.create_world` persists it in one call). `EntityListView` create uses `<EntityForm mode="create">`, which renders only fields flagged `createDefault` (flat, no Advanced/body); the caller owns the primary label field (name, or `title` for lore) + the `<IdField>`.

**Tech Stack:** React 18 + TypeScript, Vitest + @testing-library/react.

Spec: `docs/superpowers/specs/2026-05-28-world-creation-rich-create-design.md`

---

## Task 1: `slugify` helper

**Files:**
- Create: `frontend/src/routes/library/slugify.ts`
- Test: `frontend/src/routes/library/__tests__/slugify.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import { slugify } from "../slugify";

describe("slugify", () => {
  it("lowercases and hyphenates", () => {
    expect(slugify("Ravenmark")).toBe("ravenmark");
    expect(slugify("The Old Gods")).toBe("the-old-gods");
  });
  it("strips punctuation and collapses separators", () => {
    expect(slugify("Drizzt Do'Urden!!")).toBe("drizzt-dourden");
    expect(slugify("  a__b  c ")).toBe("a-b-c");
  });
  it("trims leading/trailing hyphens and handles empty", () => {
    expect(slugify("--Hi--")).toBe("hi");
    expect(slugify("")).toBe("");
    expect(slugify("***")).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/slugify.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `slugify.ts`**

```ts
/** Derive a library id ([a-z0-9-]) from a human name. */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/['']/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/slugify.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/slugify.ts frontend/src/routes/library/__tests__/slugify.test.ts
git commit -m "feat(frontend): slugify helper for auto-ids (#441)"
```

---

## Task 2: `<IdField>` component

A Name input plus a derived, editable id that stays synced to the name until the user edits the id (then it sticks).

**Files:**
- Create: `frontend/src/routes/library/IdField.tsx`
- Test: `frontend/src/routes/library/__tests__/IdField.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { IdField } from "../IdField";

describe("IdField", () => {
  it("derives id from name until id is edited", () => {
    const onName = vi.fn();
    const onId = vi.fn();
    render(<IdField nameLabel="Name" name="" id="" onNameChange={onName} onIdChange={onId} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
    expect(onName).toHaveBeenCalledWith("Ravenmark");
    expect(onId).toHaveBeenCalledWith("ravenmark");
  });

  it("stops auto-syncing once the id is manually edited", () => {
    const onId = vi.fn();
    function Harness() {
      const [name, setName] = (require("react") as typeof import("react")).useState("");
      const [id, setId] = (require("react") as typeof import("react")).useState("");
      return (
        <IdField
          nameLabel="Name"
          name={name}
          id={id}
          onNameChange={setName}
          onIdChange={(v) => {
            setId(v);
            onId(v);
          }}
        />
      );
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("ID"), { target: { value: "custom" } });
    onId.mockClear();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
    // id should NOT have been re-derived after a manual edit
    expect(onId).not.toHaveBeenCalledWith("ravenmark");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/IdField.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `IdField.tsx`**

```tsx
import { useState } from "react";

import { slugify } from "./slugify";

/**
 * Name input with a derived, editable id. The id auto-follows the name until
 * the user edits the id directly, after which it "sticks".
 */
export function IdField({
  nameLabel,
  name,
  id,
  onNameChange,
  onIdChange,
}: {
  nameLabel: string;
  name: string;
  id: string;
  onNameChange: (next: string) => void;
  onIdChange: (next: string) => void;
}) {
  const [touched, setTouched] = useState(false);
  return (
    <>
      <label>
        <span>{nameLabel}</span>
        <input
          required
          value={name}
          onChange={(e) => {
            onNameChange(e.target.value);
            if (!touched) onIdChange(slugify(e.target.value));
          }}
        />
      </label>
      <label>
        <span>ID</span>
        <input
          required
          value={id}
          pattern="[a-z0-9][a-z0-9-]*"
          title="lowercase letters, digits, and hyphens"
          onChange={(e) => {
            setTouched(true);
            onIdChange(e.target.value);
          }}
        />
      </label>
    </>
  );
}
```

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/IdField.test.tsx && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/IdField.tsx frontend/src/routes/library/__tests__/IdField.test.tsx
git commit -m "feat(frontend): IdField with sticky auto-id (#441)"
```

---

## Task 3: Rich world create form

**Files:**
- Modify: `frontend/src/routes/library/WorldsListView.tsx` (state `:29-33`, `submit` `:84-99`, create `<form>` `:123-148`)
- Test: `frontend/src/routes/library/__tests__/WorldsListView.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `WorldsListView.test.tsx` (reuse its existing mock of `libraryApi.createWorld`; add the mock fn if absent):

```tsx
it("creates a world with genre/description/tags via the rich form", async () => {
  vi.mocked(libraryModule.libraryApi.listWorlds).mockResolvedValue([]);
  vi.mocked(libraryModule.libraryApi.createWorld).mockResolvedValue({ id: "ravenmark" } as never);
  render(
    <MemoryRouter>
      <WorldsListView />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /New world/ }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
  fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Grimdark fantasy" } });
  fireEvent.click(screen.getByRole("button", { name: /^Create/ }));
  await waitFor(() =>
    expect(libraryModule.libraryApi.createWorld).toHaveBeenCalledWith(
      "ravenmark",
      expect.objectContaining({ name: "Ravenmark", genre: "Grimdark fantasy" }),
    ),
  );
});
```

> Ensure the mock object includes `createWorld: vi.fn()`. If the existing `vi.mock` block lists explicit fns, add `createWorld` (and keep `listWorlds`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/WorldsListView.test.tsx`
Expected: FAIL (no "Genre" field; id no longer typed first).

- [ ] **Step 3: Update state + submit in `WorldsListView.tsx`**

Replace the create-form state block (`:29-33` region — `newId`/`newName`) with:

```tsx
const [creating, setCreating] = useState(false);
const [newName, setNewName] = useState("");
const [newId, setNewId] = useState("");
const [newGenre, setNewGenre] = useState("");
const [newDescription, setNewDescription] = useState("");
const [newTagsText, setNewTagsText] = useState("");
const [submitErr, setSubmitErr] = useState<string | null>(null);
const [busy, setBusy] = useState(false);
```

Replace `submit` (`:84-99`) with:

```tsx
async function submit(e: React.FormEvent) {
  e.preventDefault();
  setSubmitErr(null);
  setBusy(true);
  try {
    const meta: Record<string, unknown> = { name: newName.trim() };
    if (newGenre.trim()) meta.genre = newGenre.trim();
    if (newDescription.trim()) meta.description = newDescription.trim();
    const tags = newTagsText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    if (tags.length) meta.tags = tags;
    const created = await libraryApi.createWorld(newId.trim(), meta);
    setCreating(false);
    setNewName("");
    setNewId("");
    setNewGenre("");
    setNewDescription("");
    setNewTagsText("");
    navigate(`/library/worlds/${encodeURIComponent(created.id)}`);
  } catch (err) {
    setSubmitErr(err instanceof ApiError ? err.message : String(err));
  } finally {
    setBusy(false);
  }
}
```

- [ ] **Step 4: Update the create `<form>` (`:123-148`)**

Add the import:

```tsx
import { IdField } from "./IdField";
```

Replace the form body with:

```tsx
{creating && (
  <form onSubmit={submit} className="library-form" aria-label="Create world">
    <IdField
      nameLabel="Name"
      name={newName}
      id={newId}
      onNameChange={setNewName}
      onIdChange={setNewId}
    />
    <label>
      <span>Genre</span>
      <input value={newGenre} onChange={(e) => setNewGenre(e.target.value)} />
    </label>
    <label>
      <span>Description</span>
      <textarea rows={3} value={newDescription} onChange={(e) => setNewDescription(e.target.value)} />
    </label>
    <label>
      <span>Tags (comma separated)</span>
      <input value={newTagsText} onChange={(e) => setNewTagsText(e.target.value)} />
    </label>
    <button type="submit" disabled={busy}>
      {busy ? "Creating…" : "Create"}
    </button>
    {submitErr && (
      <p className="library-error" role="alert">
        {submitErr}
      </p>
    )}
  </form>
)}
```

- [ ] **Step 5: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/WorldsListView.test.tsx && pnpm typecheck`
Expected: PASS (both the existing delete test and the new create test).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/WorldsListView.tsx frontend/src/routes/library/__tests__/WorldsListView.test.tsx
git commit -m "feat(frontend): rich world create form with auto-id (#441)"
```

---

## Task 4: `createDefault` flags + `primaryLabelKey`

**Files:**
- Modify: `frontend/src/routes/library/entitySchemas.ts`
- Test: `frontend/src/routes/library/__tests__/entitySchemas.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `entitySchemas.test.ts`:

```ts
import { createDefaultFields, primaryLabelKey } from "../entitySchemas";

describe("create-mode helpers", () => {
  it("marks headline create fields per kind", () => {
    expect(createDefaultFields(getDescriptor("character")!).map((f) => f.key)).toEqual(
      expect.arrayContaining(["role", "description"]),
    );
    expect(createDefaultFields(getDescriptor("location")!).map((f) => f.key)).toContain("kind");
  });
  it("uses title as the primary label for lore, name otherwise", () => {
    expect(primaryLabelKey(getDescriptor("lore")!)).toBe("title");
    expect(primaryLabelKey(getDescriptor("character")!)).toBe("name");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts`
Expected: FAIL (`createDefaultFields`/`primaryLabelKey` not exported).

- [ ] **Step 3: Add `createDefault` to the type, mark fields, add helpers**

In `entitySchemas.ts`, add `createDefault?: boolean;` to `FieldDescriptor`.

Mark these existing fields with `createDefault: true` (add the property to each field object):
- CHARACTER: `role`, `description`
- LOCATION: `kind`, `description`
- ITEM: `description`
- MONSTER: `category`, `description`
- FACTION: `description`
- LORE: `secrecy`

Append these helpers after `managedKeys`:

```ts
/** Flat list of fields shown in the compact "create" form for a kind. */
export function createDefaultFields(descriptor: EntityDescriptor): FieldDescriptor[] {
  return descriptor.sections.flatMap((s) => s.fields).filter((f) => f.createDefault);
}

/** The frontmatter key that holds the entity's human label (title for lore). */
export function primaryLabelKey(descriptor: EntityDescriptor): string {
  return descriptor.sections[0]?.fields[0]?.key ?? "name";
}
```

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/entitySchemas.test.ts && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/entitySchemas.ts frontend/src/routes/library/__tests__/entitySchemas.test.ts
git commit -m "feat(frontend): createDefault flags + primaryLabelKey (#441)"
```

---

## Task 5: `EntityForm` create mode

**Files:**
- Modify: `frontend/src/routes/library/EntityForm.tsx`
- Test: `frontend/src/routes/library/__tests__/EntityForm.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `EntityForm.test.tsx`:

```tsx
it("create mode renders only createDefault fields, no Advanced/body", () => {
  const onFm = vi.fn();
  render(
    <EntityForm
      descriptor={descriptor}
      worldId="w1"
      mode="create"
      frontmatter={{}}
      body=""
      onFrontmatterChange={onFm}
      onBodyChange={() => {}}
    />,
  );
  // Role (createDefault) is shown; Aliases (not createDefault) is not.
  expect(screen.getByText("Role")).toBeInTheDocument();
  expect(screen.queryByText("Aliases")).not.toBeInTheDocument();
  // No Advanced section, no markdown body editor in create mode.
  expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument();
  expect(screen.queryByText("Markdown body")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityForm.test.tsx`
Expected: FAIL (`mode` prop unknown / Advanced still shown).

- [ ] **Step 3: Add `mode` to `EntityForm`**

In `EntityForm.tsx`, add to `Props`:

```tsx
mode?: "edit" | "create";
```

Destructure `mode = "edit"`. Import the helper:

```tsx
import { type EntityDescriptor, type FieldDescriptor, createDefaultFields, managedKeys } from "./entitySchemas";
```

At the top of the returned JSX, branch for create mode before the edit-mode return:

```tsx
if (mode === "create") {
  return (
    <div className="entity-form entity-form-create">
      {createDefaultFields(descriptor).map((field) => (
        <label key={field.key} className="entity-form-field">
          <span>{field.label}</span>
          {renderField(field, frontmatter[field.key], (next) => setKey(field.key, next))}
        </label>
      ))}
    </div>
  );
}
```

(Leave the existing edit-mode JSX — sections + Advanced + body — unchanged below.)

- [ ] **Step 4: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityForm.test.tsx && pnpm typecheck`
Expected: PASS (both edit-mode and create-mode cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/EntityForm.tsx frontend/src/routes/library/__tests__/EntityForm.test.tsx
git commit -m "feat(frontend): EntityForm create mode (#441)"
```

---

## Task 6: Wire rich entity create into `EntityListView`

**Files:**
- Modify: `frontend/src/routes/library/EntityListView.tsx` (state `:43-48`, `submit` `:86-114`, create `<form>` `:125-158`)
- Test: `frontend/src/routes/library/__tests__/EntityListView.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `EntityListView.test.tsx`:

```tsx
it("creates a character with auto-id and role via the rich create form", async () => {
  vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([]);
  vi.mocked(libraryModule.libraryApi.dependents).mockResolvedValue([]);
  const createEntity = vi.fn().mockResolvedValue({ asset_id: "alistair" });
  // @ts-expect-error augment mocked api
  libraryModule.libraryApi.createEntity = createEntity;

  render(
    <MemoryRouter initialEntries={["/library/worlds/w1/characters"]}>
      <Routes>
        <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
      </Routes>
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /New character/ }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Alistair" } });
  fireEvent.click(screen.getByRole("button", { name: /^Create/ }));
  await waitFor(() =>
    expect(createEntity).toHaveBeenCalledWith(
      "w1",
      "characters",
      expect.objectContaining({
        id: "alistair",
        frontmatter: expect.objectContaining({ name: "Alistair" }),
      }),
    ),
  );
});
```

> If the existing `vi.mock` block lists explicit fns, add `createEntity: vi.fn()` there instead of the `@ts-expect-error` reassignment, and `vi.mocked(...).mockResolvedValue(...)` in the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityListView.test.tsx`
Expected: FAIL (no "Name" label / id still typed first).

- [ ] **Step 3: Update imports + state in `EntityListView.tsx`**

Add imports:

```tsx
import { IdField } from "./IdField";
import { EntityForm } from "./EntityForm";
import { getDescriptor, primaryLabelKey } from "./entitySchemas";
import { type Frontmatter } from "./frontmatter";
```

Replace the create state (`:43-48` region) with:

```tsx
const [creating, setCreating] = useState(false);
const [newName, setNewName] = useState("");
const [newId, setNewId] = useState("");
const [createDraft, setCreateDraft] = useState<Frontmatter>({});
const [greetingForm, setGreetingForm] = useState<GreetingFormValue>(emptyGreetingForm);
const [submitErr, setSubmitErr] = useState<string | null>(null);
const [busy, setBusy] = useState(false);
```

- [ ] **Step 4: Update `submit` (`:86-114`)**

```tsx
async function submit(e: React.FormEvent) {
  e.preventDefault();
  setSubmitErr(null);
  setBusy(true);
  try {
    const id = newId.trim();
    const descriptor = getDescriptor(singular);
    let frontmatter: Record<string, unknown>;
    let body = "";
    if (isGreetingKind) {
      ({ frontmatter, body } = greetingFormToPayload({ ...greetingForm, name: newName.trim() }, id));
    } else if (descriptor) {
      const labelKey = primaryLabelKey(descriptor);
      frontmatter = { ...createDraft, id, [labelKey]: newName.trim() };
    } else {
      frontmatter = { name: newName.trim(), id };
    }
    const created = await libraryApi.createEntity(worldId, kindPlural, { id, frontmatter, body });
    setCreating(false);
    setNewName("");
    setNewId("");
    setCreateDraft({});
    setGreetingForm(emptyGreetingForm());
    navigate(
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(created.asset_id)}`,
    );
  } catch (err) {
    setSubmitErr(err instanceof ApiError ? err.message : String(err));
  } finally {
    setBusy(false);
  }
}
```

- [ ] **Step 5: Update the create `<form>` (`:125-158`)**

Replace the ID + Name labels with `<IdField>` and add the compact `EntityForm` for non-greeting kinds with a descriptor:

```tsx
{creating && (
  <form onSubmit={submit} className="library-form" aria-label={`Create ${singular}`}>
    <IdField
      nameLabel={getDescriptor(singular) && primaryLabelKey(getDescriptor(singular)!) === "title" ? "Title" : "Name"}
      name={newName}
      id={newId}
      onNameChange={setNewName}
      onIdChange={setNewId}
    />
    {!isGreetingKind && getDescriptor(singular) && (
      <EntityForm
        descriptor={getDescriptor(singular)!}
        worldId={worldId}
        mode="create"
        frontmatter={createDraft}
        body=""
        onFrontmatterChange={setCreateDraft}
        onBodyChange={() => {}}
      />
    )}
    {isGreetingKind && (
      <GreetingFormFields worldId={worldId} value={greetingForm} onChange={setGreetingForm} hideName />
    )}
    <button type="submit" disabled={busy}>
      {busy ? "Creating…" : "Create"}
    </button>
    {submitErr && (
      <p className="library-error" role="alert">
        {submitErr}
      </p>
    )}
  </form>
)}
```

- [ ] **Step 6: Run test + typecheck to verify they pass**

Run: `cd frontend && pnpm vitest run src/routes/library/__tests__/EntityListView.test.tsx && pnpm typecheck`
Expected: PASS (delete test, token-badge test, and new create test).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/library/EntityListView.tsx frontend/src/routes/library/__tests__/EntityListView.test.tsx
git commit -m "feat(frontend): rich entity create with auto-id + compact form (#441)"
```

---

## Task 7: Full verification

- [ ] **Step 1: Front-end gate**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm vitest run`
Expected: all PASS.

- [ ] **Step 2: Prettier-tidy new/changed files**

Run: `cd frontend && pnpm exec prettier --write src/routes/library/slugify.ts src/routes/library/IdField.tsx src/routes/library/WorldsListView.tsx src/routes/library/EntityListView.tsx src/routes/library/EntityForm.tsx src/routes/library/entitySchemas.ts "src/routes/library/__tests__/slugify.test.ts" "src/routes/library/__tests__/IdField.test.tsx"`
Then re-run typecheck + touched tests.

- [ ] **Step 3: Manual smoke (optional)**

Create a new world via the rich form (id auto-derives); add a character (auto-id, role/description shown); add a lore entry (label says "Title").

- [ ] **Step 4: Commit any formatting**

```bash
git add -A && git commit -m "style(frontend): prettier tidy for SP3 files (#441)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** slugify (T1) + IdField sticky auto-id (T2); rich world create with meta (T3); createDefault + primaryLabelKey (T4); EntityForm create mode (T5); entity create wiring incl. lore→title and greeting passthrough (T6). No backend changes (create endpoints already accept rich payloads; `world.create_world` persists meta — verified).
- **Type consistency:** `IdField` props (`nameLabel`,`name`,`id`,`onNameChange`,`onIdChange`) match T2 and both call sites (T3, T6); `createDefaultFields`/`primaryLabelKey` defined T4, used T5/T6; `EntityForm` `mode` default `"edit"` keeps SP1/SP2 call sites unchanged.
- **Greetings:** retain their bespoke create path; `getDescriptor("greeting")` is undefined so the compact `EntityForm` block is skipped.
- **Sharp edge:** the IdField test's `require("react")` harness — if `require` is unavailable under ESM vitest, replace with a top `import { useState } from "react"` and a normal harness component.
