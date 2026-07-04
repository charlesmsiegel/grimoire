# Campaign World Editing — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The campaign's "World Copy" page (`/campaigns/:cid/world`) edits the campaign's own copy for every tab — characters, PCs, greetings (locations/lore already do) — with campaign-only actions: version pick/import and greeting marks.

**Architecture:** Follow `EntityEditor`'s existing pattern: editors take a `scope: EntityScope` (`{kind: "world" | "campaign", id}`); `api/client.ts` core CRUD functions switch their first parameter from `wid` to `scope`. World-only features (chub, imports, image uploads, taglines, tagging queue) are gated behind `scope.kind === "world"`. Campaign-only sidebar sections (Version pick/import, greeting marks) render only in campaign scope.

**Tech Stack:** React 18, TypeScript, vitest (run **from `frontend/`**: `npx vitest run`; typecheck: `npx tsc -b`).

**Spec:** `docs/superpowers/specs/2026-07-04-campaign-world-editing-design.md`
**Depends on:** the backend plan (`2026-07-04-campaign-world-editing-backend.md`) being merged — every new endpoint used here exists after it.

## Global Constraints

- Run vitest **from `frontend/`** — `npx --prefix frontend vitest run` from the repo root silently skips `vitest.config.ts` and breaks all mock-based tests (see CLAUDE.md).
- Keep the list/detail page pattern from CLAUDE.md intact: view mode read-only, Edit button in the sidebar `.form-actions`, metadata in `.side-section` blocks.
- Marks vocabulary in UI copy: **Mark complete** / **Won't do** / **Clear** — badges say `done` (completed), `skip` (skipped), `played`.
- The untagged-image tagging queue stays world-only. The Tags tab stays world-only.
- Commit after every task.

---

### Task 1: scope-parameterize `api/client.ts` + new endpoints and types

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces (produced — every later task consumes these):**
- These functions change their first parameter from `wid: string` to `scope: EntityScope` and build URLs via the existing `entityBase(scope)`:
  `listCharacters`, `readCharacter`, `setDefaultVersion`, `createVersion`, `updateVersion`, `deleteVersion`,
  `listGreetings`, `createGreeting`, `readGreeting`, `updateGreeting`, `deleteGreeting`, `setEdges`,
  `listPCs`, `readPC`, `updatePC`, `createPCVersion`, `updatePCVersion`, `deletePCVersion`.
  (Example: `listCharacters: (scope: EntityScope) => request<CharacterSummary[]>("GET", `${entityBase(scope)}/characters`)`.)
- These stay world-only with `wid` signatures (no campaign routes exist): `createCharacter`, `deleteCharacter`, `createPC`, `deletePC`, `importGreetings`, `importCharacter`, all chub/tagline/localize/image-upload/lorebook/tags functions, `setCharacterBirthdate`, subjects functions, `listUntaggedImages`.
- New functions:

```ts
markGreeting: (cid: string, gid: string, status: "completed" | "skipped" | "none") =>
  request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/greetings/${gid}/mark`, { status }),
pickVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
  request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/pick-version`, { version }),
importVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
  request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/import-version`, { version }),
actorImageUrl: (scope: EntityScope, cid: string, vid: string, name: string) =>
  `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`,
```

- Type changes:

```ts
export type GreetingMark = "played" | "completed" | "skipped" | null;
// Greeting gains:            mark?: GreetingMark;   (campaign list carries it)
// Availability gains:        mark?: GreetingMark;
```

- [ ] **Step 1: Write the failing tests** (append to `client.test.ts`; it mocks `globalThis.fetch` directly with a local `jsonOk` helper — same style below. Also fix line 184's `api.setEdges("w", ...)` to `api.setEdges({ kind: "world", id: "w" }, ...)`)

```ts
test("scope-parameterized calls route to worlds or campaigns", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.listCharacters({ kind: "campaign", id: "run" });
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters",
    expect.objectContaining({ method: "GET" }));
  await api.readGreeting({ kind: "world", id: "w" }, "g1");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/worlds/w/greetings/g1",
    expect.objectContaining({ method: "GET" }));
});

test("greeting marks and version picks POST to their campaign routes", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.markGreeting("run", "g1", "skipped");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/greetings/g1/mark",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ status: "skipped" }) }));
  await api.pickVersion("run", "characters", "mara", "veteran");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters/mara/pick-version",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ version: "veteran" }) }));
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/api/client.test.ts` (from `frontend/`). Expected: type/URL failures.

- [ ] **Step 3: Implement** the signature changes, new functions, and types listed above. Every changed function is a one-line URL swap: `/api/worlds/${wid}` → `${entityBase(scope)}`.

- [ ] **Step 4: Fix the compile fallout now, mechanically** — callers updated to pass `{ kind: "world", id: wid }` for now (later tasks make them scope-aware for real): `loreOwners.ts`, `CharacterEditor.tsx`, `PCEditor.tsx`, `GreetingEditor.tsx`, `CastPanel.tsx` (lines 60-61, 157), `SceneInspector.tsx` (line 34), `CampaignWizard.tsx` if it calls any changed function. Run `npx tsc -b` until clean.

- [ ] **Step 5: Run tests + typecheck** — `npx vitest run` and `npx tsc -b`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): scope-parameterized client API + mark/pick/import endpoints"
```

---

### Task 2: cast pickers and lore helpers read the campaign copy

**Files:**
- Modify: `frontend/src/api/loreOwners.ts`, `frontend/src/components/OwnedLorePanel.tsx`, `frontend/src/components/CastPanel.tsx` (lines 60-61, 157), `frontend/src/components/SceneInspector.tsx` (line 34)
- Test: existing `CastPanel.test.tsx` / `SceneInspector.test.tsx` updates

**Interfaces:**
- Produces: `loreOwnerOptions(scope: EntityScope)` (avatar URLs via `api.actorImageUrl(scope, ...)`); `OwnedLorePanel` takes `scope: EntityScope` instead of `wid` (its `listEntities` call already accepts scope). Callers inside a campaign pass campaign scope.

- [ ] **Step 1: Update tests** — in `CastPanel.test.tsx` / `SceneInspector.test.tsx`, the mocked `api.listCharacters` / `api.listPCs` now expect campaign scope; drop mocks for the removed world-PC merge.

- [ ] **Step 2: Implement**
  - `loreOwners.ts`: `loreOwnerOptions(scope: EntityScope)` — the three list calls take `scope`; avatar uses `api.actorImageUrl(scope, c.id, c.default_version, "avatar")`. Update its callers (grep `loreOwnerOptions(`) to pass their scope.
  - `OwnedLorePanel`: prop `wid: string` → `scope: EntityScope`; `api.listEntities(scope, "lore")`. Update callers (`PCEditor`, `CharacterEditor`, `EntityEditor` — grep `OwnedLorePanel`).
  - `CastPanel.tsx` line 60-61: the campaign copy now holds every actor —

```ts
      api.listCharacters({ kind: "campaign", id: cid }).then(setChars);
      api.listCampaignPCs(cid).then(setPCs);
```

  (delete the world/local PC merge). Line 157: `api.createGreeting({ kind: "campaign", id: cid }, ...)` — an opener saved as a greeting belongs to the campaign, not the world baseline.
  - `SceneInspector.tsx` line 34: same swap to campaign scope.

- [ ] **Step 3: Run** `npx vitest run` + `npx tsc -b`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "refactor(frontend): cast pickers and lore helpers read the campaign copy"
```

---

### Task 3: `GreetingEditor` — campaign scope + mark control + badges

**Files:**
- Modify: `frontend/src/components/GreetingEditor.tsx`, `frontend/src/index.css` (badge style)
- Test: `frontend/src/components/GreetingEditor.test.tsx`

**Interfaces:**
- Consumes: `api.listGreetings(scope)` (campaign items carry `mark`), `api.markGreeting`, scope-aware CRUD from Task 1.
- Produces: props become `{ scope, wid, onOpenCharacter, focus }` where `scope: EntityScope` drives all greeting/character CRUD and `wid: string` stays for the world-only tag vocabulary (`api.listTags(wid)`). World-gated: tagging queue + untagged fetch, image subjects (fetch, chips, popover), "Import greetings from this character/version". Campaign-only: a **Status** side-section in view mode and mark badges in the rail.

- [ ] **Step 1: Write the failing tests** (append to `GreetingEditor.test.tsx`; follow its existing mock pattern — it mocks `../api/client`)

```tsx
it("campaign scope: marks a greeting as won't-do from the sidebar", async () => {
  api.listGreetings = vi.fn().mockResolvedValue([
    { id: "g1", name: "Gala", character: "c", version: "v", present: [],
      requires_tags: [], predecessor_join: "all", mark: null },
  ]);
  api.readGreeting = vi.fn().mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "c", version: "v", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  api.markGreeting = vi.fn().mockResolvedValue({ ok: true });
  render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await userEvent.click(await screen.findByRole("button", { name: "Gala" }));
  await userEvent.click(await screen.findByRole("button", { name: "Won't do" }));
  expect(api.markGreeting).toHaveBeenCalledWith("run", "g1", "skipped");
});

it("campaign scope: played greetings show a disabled status control", async () => {
  api.listGreetings = vi.fn().mockResolvedValue([
    { id: "g1", name: "Gala", character: "c", version: "v", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]);
  api.readGreeting = vi.fn().mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "c", version: "v", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await userEvent.click(await screen.findByRole("button", { name: /Gala/ }));
  expect(await screen.findByRole("button", { name: "Mark complete" })).toBeDisabled();
  expect(screen.getByText(/started this greeting in a scene/i)).toBeInTheDocument();
});

it("campaign scope: hides the tagging queue and never fetches untagged images", async () => {
  api.listGreetings = vi.fn().mockResolvedValue([]);
  api.listUntaggedImages = vi.fn();
  render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByRole("button", { name: "+ New greeting" });
  expect(api.listUntaggedImages).not.toHaveBeenCalled();
});
```

(Also update the file's existing world-mode renders to `scope={{ kind: "world", id: "w" }} wid="w"`.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/components/GreetingEditor.test.tsx`

- [ ] **Step 3: Implement**
  - Props: `{ scope, wid, onOpenCharacter, focus }: { scope: EntityScope; wid: string; ... }`. Add `const worldScope = scope.kind === "world";` and a `mark` lookup state.
  - All greeting/character CRUD calls (`listGreetings`, `readGreeting`, `createGreeting`, `updateGreeting`, `deleteGreeting`, `setEdges`, `listCharacters`) pass `scope`. `listTags(wid)` unchanged.
  - Gate with `worldScope`: the `listUntaggedImages` fetch + "Tag images" button + `TaggingQueue` branch; the `getGreetingSubjects` fetch, `imageExtras` subject chips/popover, `saveSubjects`; the "Import greetings from this character/version" button.
  - Rail badges — in the row button:

```tsx
{g.name}
{scope.kind === "campaign" && g.mark && (
  <span className={`mark-badge ${g.mark}`}>
    {g.mark === "completed" ? "done" : g.mark === "skipped" ? "skip" : "played"}
  </span>
)}
```

  - **Status** side-section in the view sidebar (after the Edit `.form-actions`, campaign scope only). `mark` comes from the loaded list (`greetings.find((g) => g.id === gid)?.mark`):

```tsx
{scope.kind === "campaign" && (
  <div className="side-section">
    <h4>Status</h4>
    {mark === "played" ? (
      <div className="field-hint">Started this greeting in a scene — the mark is fixed.</div>
    ) : (
      <div className="field-hint">
        {mark === "completed" ? "Marked complete: successors are unlocked."
          : mark === "skipped" ? "Won't do: hidden from new scenes; the plot routes around it."
          : "Unmarked."}
      </div>
    )}
    <div className="chips">
      <button className={"chip" + (mark === "completed" ? " on" : "")} disabled={mark === "played"}
              onClick={() => setMark("completed")}>Mark complete</button>
      <button className={"chip" + (mark === "skipped" ? " on" : "")} disabled={mark === "played"}
              onClick={() => setMark("skipped")}>Won't do</button>
      <button className="chip" disabled={mark === "played" || !mark}
              onClick={() => setMark("none")}>Clear</button>
    </div>
  </div>
)}
```

    with

```tsx
async function setMark(status: "completed" | "skipped" | "none") {
  try {
    await api.markGreeting(scope.id, gid!, status);
    await reload();
  } catch (err: any) {
    setError(err.detail ?? String(err));
  }
}
```

  - `index.css`: a small `.mark-badge` (margin-left, reduced opacity, `.skipped` uses line-through on the row name is optional — keep it a badge).

- [ ] **Step 4: Run tests** — full `npx vitest run` (existing GreetingEditor tests must stay green) + `npx tsc -b`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): GreetingEditor campaign scope with mark controls and badges"
```

---

### Task 4: `PCEditor` — campaign scope + Version pick/import side-section

**Files:**
- Modify: `frontend/src/components/PCEditor.tsx`
- Test: `frontend/src/components/PCEditor.test.tsx`

**Interfaces:**
- Consumes: scope-aware `listPCs`/`readPC`/`updatePC`/PC-version CRUD, `api.listAppearances(cid)` (lock state), `api.pickVersion`/`importVersion`, world reads via `{kind: "world", id: wid}` for the import list.
- Produces: props `{ scope, wid, onOpenLore }`. Campaign view sidebar gains a **Version** side-section: locked → locked chip + "Import from world…" (world version select + confirm); unlocked with >1 version → "Pick this version" (confirm, warns it purges the rest). `+ Version` is hidden for locked actors. World-scope rendering is unchanged. New PCs in campaign scope use `api.createCampaignPC` (route exists); tag chips in campaign scope edit free-form via `updatePC` without the world vocabulary (show current tags as removable chips + a text input to add one, since `listTags` is world-only — keep the world-scope chip picker as-is).

- [ ] **Step 1: Write the failing tests**

```tsx
const PC_DETAIL = {
  meta: { id: "elara", name: "Elara", tags: [], default_version: "young" },
  versions: [
    { id: "young", name: "Young", persona: { name: "Elara", pronouns: "", summary: "", description: "d" } },
    { id: "older", name: "Older", persona: { name: "Elara", pronouns: "", summary: "", description: "d2" } },
  ],
};

it("campaign scope: picking a version confirms and calls pickVersion", async () => {
  api.listPCs = vi.fn().mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  api.readPC = vi.fn().mockResolvedValue(PC_DETAIL);
  api.listAppearances = vi.fn().mockResolvedValue([]);          // unlocked
  api.pickVersion = vi.fn().mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await userEvent.click(await screen.findByRole("button", { name: "Elara" }));
  await userEvent.click(await screen.findByRole("button", { name: "Pick this version" }));
  expect(api.pickVersion).toHaveBeenCalledWith("run", "pcs", "elara", "young");
});

it("campaign scope: a locked PC offers import from world", async () => {
  api.listPCs = vi.fn().mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  api.readPC = vi.fn()
    .mockResolvedValueOnce({ ...PC_DETAIL, versions: [PC_DETAIL.versions[0]] }) // campaign copy
    .mockResolvedValue(PC_DETAIL);                                              // world list
  api.listAppearances = vi.fn().mockResolvedValue([
    { kind: "pcs", id: "elara", version: "young", role: "player", scenes: [] },
  ]);
  api.importVersion = vi.fn().mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await userEvent.click(await screen.findByRole("button", { name: "Elara" }));
  expect(await screen.findByText(/locked/i)).toBeInTheDocument();
  await userEvent.selectOptions(await screen.findByLabelText("Import version"), "older");
  await userEvent.click(screen.getByRole("button", { name: "Import from world" }));
  expect(api.importVersion).toHaveBeenCalledWith("run", "pcs", "elara", "older");
});
```

(Existing tests: update render call to `scope={{ kind: "world", id: "w" }} wid="w"`.)

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**
  - Props + scope plumbing on all CRUD calls; `api.listTags(wid)` only fetched when `scope.kind === "world"`.
  - Lock state: when campaign scope, on `select()` also `api.listAppearances(scope.id)` → `locked = roster.find(r => r.kind === "pcs" && r.id === pid)?.version ?? null`.
  - Version side-section replaces the current bare version `<select>` in **view** mode when campaign scope:

```tsx
{scope.kind === "campaign" && (
  <div className="side-section">
    <h4>Version</h4>
    {locked ? (
      <>
        <div className="field-hint">Locked to <b>{versionName(locked)}</b> for this campaign.</div>
        <select aria-label="Import version" value={importVid} onChange={(e) => setImportVid(e.target.value)}>
          <option value="">— world version —</option>
          {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        <button className="subtle" disabled={!importVid} onClick={runImport}>Import from world</button>
      </>
    ) : detail.versions.length > 1 ? (
      <>
        <div className="field-hint">Viewing {versionName(vid)}. Picking locks it and removes the others from this campaign.</div>
        <button className="subtle" onClick={runPick}>Pick this version</button>
      </>
    ) : (
      <div className="field-hint">Single version; it locks when first used in a scene.</div>
    )}
  </div>
)}
```

    with `worldVersions` fetched lazily (`api.readPC({ kind: "world", id: wid }, pid)` — tolerate failure with `[]`, e.g. a world-deleted PC), and

```tsx
async function runPick() {
  if (!detail || !window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
  await api.pickVersion(scope.id, "pcs", detail.meta.id, vid);
  await select(detail.meta.id, vid);
}

async function runImport() {
  if (!detail || !importVid) return;
  if (!window.confirm("Replace the locked version with the world's copy?")) return;
  await api.importVersion(scope.id, "pcs", detail.meta.id, importVid);
  await select(detail.meta.id, importVid);
}
```

  - Edit-mode `picker` row: hide `+ Version` when `locked`; `newPC` uses `api.createCampaignPC(scope.id, { name })` in campaign scope; `Delete PC` hidden in campaign scope (no route).
  - Campaign tag editing: replace the world chip picker with current-tag chips (click to remove) plus an `<input>` + Add button feeding `updatePC(scope, pid, { tags })`.

- [ ] **Step 4: Run tests + typecheck.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): PCEditor campaign scope with version pick/import"
```

---

### Task 5: `CharacterEditor` — campaign scope, world-only gating, version pick/import

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: scope-aware character CRUD, `api.actorImageUrl`, `api.listAppearances`, `api.pickVersion`/`importVersion`.
- Produces: props `{ scope, wid, resetSignal, focus, onOpenLore, onOpenGreeting }`. One flag drives gating: `const worldScope = scope.kind === "world";`

**World-only blocks (render/execute only when `worldScope`):**
- Grid toolbar (line ~700): "Import card", "Download from URL", "Check chub.ai links" buttons (+ their file inputs/prompts/handlers), the unlinked-versions list, `TaglinePrompt` queue. **"+ New character" and card "Delete" are also world-only** (no campaign routes).
- Detail view: the whole `chub-source-block`; image-shelf mutations (avatar upload/delete/crop, gallery upload/promote/delete, copy-from-greeting, localize buttons/progress) — images render read-only in campaign scope via `api.actorImageUrl(scope, ...)`; the tagline editor and birthdate field; the "Appears in" image-appearances section (`listImageAppearances` is world-only — skip the fetch too).
- Keep in both scopes: the grid of cards, the detail view (name/tags/fields/greetings text), the version segmented control, Edit mode for card text fields (`updateVersion`), `+ Version` (campaign: only while unlocked), `OwnedLorePanel` (Task 2 made it scope-aware), the world-greetings links (`listGreetings(scope)` — campaign greetings work through `onOpenGreeting` within the same page).

**Campaign-only:** a version **pick/import** block in `detail-actions`, mirroring Task 4 exactly (`kind: "characters"`, world versions from `api.readCharacter({ kind: "world", id: wid }, cid)`, lock state from `listAppearances`; when locked, the segmented version control collapses to the single locked version naturally since the campaign copy only has one).

- [ ] **Step 1: Write the failing tests**

```tsx
it("campaign scope: hides world-only tooling and uses campaign image URLs", async () => {
  api.listCharacters = vi.fn().mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", has_avatar: true, versions: [] },
  ]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByRole("button", { name: "Import card" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Download from URL" })).toBeNull();
  expect(screen.queryByRole("button", { name: "+ New character" })).toBeNull();
  const img = document.querySelector("img.char-card-avatar")!;
  expect(img.getAttribute("src")).toContain("/api/campaigns/run/characters/mara/");
});

it("campaign scope: picking a version calls pickVersion", async () => {
  api.listCharacters = vi.fn().mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  api.readCharacter = vi.fn().mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [
      { id: "young", name: "Young", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
      { id: "veteran", name: "Veteran", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
    ],
  });
  api.listAppearances = vi.fn().mockResolvedValue([]);
  api.pickVersion = vi.fn().mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await userEvent.click(await screen.findByText("Mara"));
  await userEvent.click(await screen.findByRole("button", { name: "Pick this version" }));
  expect(api.pickVersion).toHaveBeenCalledWith("run", "characters", "mara", "young");
});
```

(Existing tests: update render props to world scope.)

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** per the gating list above. Mechanics:
  - `avatarSrc` (line ~687) becomes `api.actorImageUrl(scope, cid, version, "avatar") + (bust ? `?v=${avatarBust}` : "")`.
  - Skip world-only effects in campaign scope: the `listImageAppearances`/tagline fetches guard on `worldScope`.
  - Add the pick/import block after the segmented version control (line ~827) with the same `runPick`/`runImport` shape as Task 4 (kind `"characters"`).
  - `+ Version` (edit mode) additionally requires `!locked` in campaign scope.

- [ ] **Step 4: Run tests + typecheck.** Expected: PASS (all existing CharacterEditor tests too).

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): CharacterEditor campaign scope with pick/import and world-only gating"
```

---

### Task 6: `WorldView` wiring — scope to every tab, Tags tab world-only

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx`
- Test: `frontend/src/routes/WorldView.test.tsx` (create if absent, following the other route tests' mock style)

**Interfaces:**
- Consumes: everything above.
- Produces: in campaign mode every editor gets `scope={{ kind: "campaign", id: cid }}` plus `wid` for world-vocabulary reads; the **Tags tab is hidden** in campaign mode (tags are world vocabulary; campaign PCs edit free-form tags).

- [ ] **Step 1: Write the failing test**

```tsx
it("campaign mode passes campaign scope and hides the Tags tab", async () => {
  api.getCampaign = vi.fn().mockResolvedValue({ meta: { id: "run", name: "Run", world: "w" }, body: "" });
  api.getWorld = vi.fn().mockResolvedValue({ meta: { id: "w", name: "W" }, body: "", counts: {} });
  api.listCharacters = vi.fn().mockResolvedValue([]);
  render(
    <MemoryRouter initialEntries={["/campaigns/run/world"]}>
      <Routes><Route path="/campaigns/:cid/world" element={<WorldView campaign />} /></Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/World Copy/);
  expect(screen.queryByRole("button", { name: "Tags" })).toBeNull();
  expect(api.listCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "run" });
});
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**
  - The `TABS` array is filtered in campaign mode: `const tabs = campaign ? TABS.filter((t) => t.key !== "tags") : TABS;` (if the current tab is `tags` when campaign, fall back to `characters`).
  - Pass `scope` (already computed at line 50) + `wid` to `CharacterEditor`, `PCEditor`, `GreetingEditor` (replacing the bare `wid` prop): e.g. `<GreetingEditor scope={scope} wid={wid} onOpenCharacter={openCharacter} focus={focusGreeting} />`.
  - `EntityEditor`/`LorebookImport` usage unchanged (already scope-aware / world-gated: keep `LorebookImport` world-only — wrap the import `<details>` in `{!campaign && ...}`).

- [ ] **Step 4: Run tests + typecheck.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): campaign world tabs edit the campaign copy; Tags stays world-only"
```

---

### Task 7: chooser regression + full verification

**Files:**
- Modify: `frontend/src/components/NewSceneChooser.test.tsx`
- Test: full suite

- [ ] **Step 1: Add the regression test** — marked greetings never reach the chooser (the server filters skipped; the chooser already filters `available === false`, which covers completed/played never being offered as *unavailable* items). `NewSceneChooser.test.tsx` has a `renderChooser` helper and a module-level `GREETINGS` array in its `beforeEach` — this test overrides the mock locally:

```tsx
test("renders exactly the server-filtered greeting list (skipped absent, marks tolerated)", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", available: true, reasons: [], unlocked: false, mark: "completed" },
  ]);
  renderChooser();
  await screen.findByText("Gala");                 // a marked-complete greeting still renders
  expect(screen.queryByText("Reckoning")).toBeNull();  // nothing beyond the server's list
});
```

- [ ] **Step 2: Full verification**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`.
Run (from repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q` (nothing here should touch it, but confirm).
Expected: everything green.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "test(frontend): chooser renders exactly the server-filtered greeting list"
```

---

## Deviations from the spec (intentional)

- Creating/deleting **characters** and deleting PCs inside a campaign is deferred (no backend routes in v1); campaign-local **PCs** can be created (`createCampaignPC` exists). Campaign greetings have full CRUD.
- The Tags tab is hidden in campaign mode rather than mirrored — tag vocabulary is a world concern; campaign PC tags are free strings (matches the backend).
- Campaign character images are read-only (served from the copied assets); uploads/localize/chub stay world-side.
