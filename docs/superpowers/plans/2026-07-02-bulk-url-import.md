# Bulk Add Characters From URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Characters grid's "Download from URL" button opens a textarea modal accepting one URL per line; each URL is imported, its images localized, its embedded lorebook imported, and a tagline popup queues per new character.

**Architecture:** Pure frontend orchestration in `CharacterEditor.tsx` over existing backend endpoints. The chub import endpoint already downloads avatar + gallery + related lorebooks server-side, so the per-URL pipeline is only: `importCharacterFromChub` → `localizeImages` → `importCharacterBook`. The single `taglinePrompt` state becomes a queue.

**Tech Stack:** React 18 + TypeScript, vitest + @testing-library/react (mocked `api` module). No backend changes.

**Spec:** `docs/superpowers/specs/2026-07-02-bulk-url-import-design.md`

## Global Constraints

- Run all frontend commands **from `frontend/`** (`npx vitest run`, `npx tsc -b`). Never `npx --prefix frontend vitest run` — it skips `frontend/vitest.config.ts` and breaks every mock-based test.
- The app is served from `frontend/dist` by the backend; the final task must run `npm run build` so the feature actually reaches the served bundle.
- Failures never abort the batch: record and continue (next step of the same character, then next URL).
- Modal copy: title "Download from URL", hint "One URL per line — chub.ai links or direct card URLs.", buttons "Add" / "Cancel", textarea aria-label "Card URLs".

---

### Task 1: `UrlImportPrompt` modal component

**Files:**
- Create: `frontend/src/components/UrlImportPrompt.tsx`
- Test: `frontend/src/components/UrlImportPrompt.test.tsx`

**Interfaces:**
- Consumes: nothing (leaf component; reuses the `.tagline-modal` CSS classes that already style `TaglinePrompt`).
- Produces: `UrlImportPrompt({ onSubmit, onClose }: { onSubmit: (urls: string[]) => void; onClose: () => void })` — a modal that parses the textarea into trimmed, non-empty lines, then calls `onClose()` followed by `onSubmit(urls)`. Task 3 renders it.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/UrlImportPrompt.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { UrlImportPrompt } from "./UrlImportPrompt";

test("Add submits trimmed non-empty lines and closes", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: " creator/one \n\n   \ncreator/two\n" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(onSubmit).toHaveBeenCalledWith(["creator/one", "creator/two"]);
  expect(onClose).toHaveBeenCalled();
});

test("Add with no URLs does nothing", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("Cancel closes without submitting", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
  expect(onClose).toHaveBeenCalled();
  expect(onSubmit).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/UrlImportPrompt.test.tsx`
Expected: FAIL — cannot resolve `./UrlImportPrompt`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/UrlImportPrompt.tsx`:

```tsx
import { useState } from "react";

export function UrlImportPrompt({ onSubmit, onClose }:
  { onSubmit: (urls: string[]) => void; onClose: () => void }) {
  const [text, setText] = useState("");

  function submit() {
    const urls = text.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!urls.length) return;
    onClose();
    onSubmit(urls);
  }

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Download from URL">
      <div className="tagline-modal">
        <h3>Download from URL</h3>
        <p className="field-hint">One URL per line — chub.ai links or direct card URLs.</p>
        <textarea aria-label="Card URLs" value={text} rows={6}
                  onChange={(e) => setText(e.target.value)} />
        <div className="form-actions">
          <button className="primary" type="button" onClick={submit}>Add</button>
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/UrlImportPrompt.test.tsx`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/UrlImportPrompt.tsx frontend/src/components/UrlImportPrompt.test.tsx
git commit -m "feat(characters): URL-list modal for download-from-URL"
```

---

### Task 2: Tagline prompt state becomes a queue

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx` (state declaration ~line 59; three `<TaglinePrompt …>` render sites in the grid/detail/edit returns; two `setTaglinePrompt(...)` call sites in `onImport` and `downloadFromChub`)
- Test: `frontend/src/components/CharacterEditor.test.tsx` (existing tests must keep passing; no new tests — queue advancement is covered in Task 3)

**Interfaces:**
- Consumes: `TaglinePrompt` from `./TaglinePrompt` (unchanged: `{ wid, cid, name, onClose, onSaved? }`).
- Produces: `taglineQueue: { cid: string; name: string }[]` state + `setTaglineQueue` — Task 3 fills this with one entry per imported character. Rendering shows `taglineQueue[0]`; closing shifts the queue.

Behavior is unchanged for existing flows (a single-element queue is today's single prompt), so this task is green when the existing suite passes.

- [ ] **Step 1: Replace the state declaration**

In `frontend/src/components/CharacterEditor.tsx`, replace:

```tsx
  const [taglinePrompt, setTaglinePrompt] = useState<{ cid: string; name: string } | null>(null);
```

with:

```tsx
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
```

- [ ] **Step 2: Replace all three render sites**

The grid, detail, and edit returns each contain this block (identical in all three):

```tsx
        {taglinePrompt && (
          <TaglinePrompt wid={wid} cid={taglinePrompt.cid} name={taglinePrompt.name}
                         onSaved={(t) => setTagline(t)}
                         onClose={() => setTaglinePrompt(null)} />
        )}
```

Replace each with (saving also `reload()`s so grid-card taglines refresh as the queue drains):

```tsx
        {taglineQueue.length > 0 && (
          <TaglinePrompt wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                         onSaved={(t) => { setTagline(t); reload(); }}
                         onClose={() => setTaglineQueue((q) => q.slice(1))} />
        )}
```

(Indentation differs slightly per site — match each site's existing indentation.)

- [ ] **Step 3: Update the two setter call sites**

In `onImport` (single-file import branch), replace:

```tsx
      setTaglinePrompt({ cid: imported[0].cid, name: d.meta.name });
```

with:

```tsx
      setTaglineQueue([{ cid: imported[0].cid, name: d.meta.name }]);
```

In `downloadFromChub`, replace:

```tsx
      setTaglinePrompt({ cid: result.character, name: d.meta.name });
```

with:

```tsx
      setTaglineQueue([{ cid: result.character, name: d.meta.name }]);
```

- [ ] **Step 4: Verify types and the full existing suite pass**

Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: clean build, all tests pass (existing tagline-popup tests exercise the single-element queue).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx
git commit -m "refactor(characters): tagline prompt state becomes a queue"
```

---

### Task 3: Bulk pipeline wired to the Download from URL button

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx` (import `UrlImportPrompt`; add `urlPromptOpen` + `bulkUrl` state; replace `downloadFromChub` with `runBulkUrlImport`; rewire the grid button; render the modal + progress hint in the grid toolbar)
- Test: `frontend/src/components/CharacterEditor.test.tsx` (rewrite the two `window.prompt`-based grid-button tests; add bulk tests)

**Interfaces:**
- Consumes: `UrlImportPrompt({ onSubmit, onClose })` from Task 1; `taglineQueue`/`setTaglineQueue` from Task 2; existing `api.importCharacterFromChub(wid, url)` → `ChubImportResult { character, version, updated, gallery: { attempted, stored }, lore: { lorebooks_found, created } }`, `api.localizeImages(wid, cid, vid, onEvent)`, `api.importCharacterBook(wid, cid, vid)` → `{ created: { kind, id }[] }`, `api.readCharacter`, `openDetail(cid)`, `reload()`.
- Produces: user-facing behavior only; nothing downstream.

Note: `downloadVersionFromChub`, `linkChub`, and `describeChubResult` are untouched — the detail-view URL flows keep their `window.prompt`.

- [ ] **Step 1: Rewrite the two existing grid-button tests and add the new bulk tests**

In `frontend/src/components/CharacterEditor.test.tsx`, replace the test `"downloading from a URL creates a character and shows the result"` with:

```tsx
test("downloading from a URL runs the full pipeline and shows the summary", async () => {
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default", updated: false,
    gallery: { attempted: 2, stored: 2 },
    lore: { lorebooks_found: 1, created: [{ kind: "lore", id: "x" }] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "https://chub.ai/characters/creator/imp" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "https://chub.ai/characters/creator/imp"));
  // pipeline: localize + embedded lorebook run against the new character
  await waitFor(() => expect(api.localizeImages).toHaveBeenCalledWith("w", "imp", "default", expect.any(Function)));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp", "default"));
  // summary: 1 related entry + 1 embedded entry = 2; localize mock reports 1 localized
  await screen.findByText(/added 1\/1 character · 2 gallery images · 1 image localized · 2 lore entries imported/i);
  // single URL: detail opens (readCharacter mock -> Seraphine) and its tagline prompt queues
  await screen.findByText("Tagline for Seraphine");
});
```

Replace the test `"an empty URL prompt makes no API call"` with:

```tsx
test("cancelling the URL modal makes no API call", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
  expect(api.importCharacterFromChub).not.toHaveBeenCalled();
});
```

Add these new tests after them:

```tsx
test("bulk URL import pipelines every URL and queues tagline prompts", async () => {
  (api.importCharacterFromChub as any)
    .mockResolvedValueOnce({ character: "imp1", version: "default", updated: false,
      gallery: { attempted: 1, stored: 1 }, lore: { lorebooks_found: 0, created: [] } })
    .mockResolvedValueOnce({ character: "imp2", version: "default", updated: false,
      gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  (api.readCharacter as any).mockImplementation((_w: string, cid: string) => Promise.resolve({
    meta: { id: cid, name: cid === "imp1" ? "Imp One" : "Imp Two", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  }));
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "creator/one\n\ncreator/two\n" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.importCharacterFromChub).toHaveBeenCalledTimes(2));
  expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/one");
  expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/two");
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp1", "default"));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp2", "default"));
  // tagline prompts drain one at a time; Skip advances to the next character
  await screen.findByText("Tagline for Imp One");
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText("Tagline for Imp Two");
});

test("a failing URL is reported in the summary and the rest still import", async () => {
  (api.importCharacterFromChub as any)
    .mockRejectedValueOnce({ detail: "could not fetch a character card from that URL" })
    .mockResolvedValueOnce({ character: "imp2", version: "default", updated: false,
      gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "bad/url\ncreator/two" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await screen.findByText(/added 1\/2 characters.*failed — bad\/url: could not fetch/i);
  // the good URL still went through the pipeline
  expect(api.localizeImages).toHaveBeenCalledWith("w", "imp2", "default", expect.any(Function));
});

test("a mid-pipeline failure still finishes the character's remaining steps", async () => {
  (api.localizeImages as any).mockRejectedValueOnce({ detail: "boom" });
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default", updated: false,
    gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"), { target: { value: "creator/imp" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp", "default"));
  await screen.findByText(/added 1\/1 character.*failed — .*localize failed/i);
});
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: the rewritten/new tests FAIL (no modal appears — the button still calls `window.prompt`); all other tests pass.

- [ ] **Step 3: Implement the pipeline**

In `frontend/src/components/CharacterEditor.tsx`:

3a. Add the import at the top, next to the `TaglinePrompt` import:

```tsx
import { UrlImportPrompt } from "./UrlImportPrompt";
```

3b. Add state next to `taglineQueue`:

```tsx
  const [urlPromptOpen, setUrlPromptOpen] = useState(false);
  const [bulkUrl, setBulkUrl] = useState<{ current: number; total: number; name: string; step: string } | null>(null);
```

3c. Replace the whole `downloadFromChub` function with:

```tsx
  // Bulk pipeline for "Download from URL". Per URL: import (the backend already
  // downloads the avatar, chub gallery, and related chub lorebooks inside this
  // one call), localize embedded images, then import the card's embedded
  // character_book to world lore. Failures record and continue — one bad URL
  // shouldn't sink the batch. Tagline prompts queue up after the whole run.
  async function runBulkUrlImport(urls: string[]) {
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const added: { cid: string; name: string }[] = [];
    let localized = 0, gallery = 0, lore = 0;
    for (let i = 0; i < urls.length; i++) {
      setBulkUrl({ current: i + 1, total: urls.length, name: urls[i], step: "importing" });
      let result: ChubImportResult;
      try {
        result = await api.importCharacterFromChub(wid, urls[i]);
      } catch (err: any) {
        failures.push(`${urls[i]}: ${err.detail ?? String(err)}`);
        continue;
      }
      gallery += result.gallery.stored;
      lore += result.lore.created.length;
      let name = result.character;
      try {
        name = (await api.readCharacter(wid, result.character)).meta.name;
      } catch { /* fall back to the id */ }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "localizing images" });
      try {
        await api.localizeImages(wid, result.character, result.version, (e) => {
          if (e.summary) localized += e.summary.localized;
        });
      } catch (err: any) {
        failures.push(`${name}: localize failed (${err.detail ?? String(err)})`);
      }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "importing lorebook" });
      try {
        const { created } = await api.importCharacterBook(wid, result.character, result.version);
        lore += created.length;
      } catch (err: any) {
        failures.push(`${name}: lorebook import failed (${err.detail ?? String(err)})`);
      }
      added.push({ cid: result.character, name });
      await reload();  // the new card appears in the grid as it lands
    }
    setBulkUrl(null);
    const parts = [`Added ${added.length}/${urls.length} character${urls.length === 1 ? "" : "s"}`];
    if (gallery) parts.push(`${gallery} gallery image${gallery === 1 ? "" : "s"}`);
    if (localized) parts.push(`${localized} image${localized === 1 ? "" : "s"} localized`);
    if (lore) parts.push(`${lore} lore entr${lore === 1 ? "y" : "ies"} imported`);
    setImportMsg(parts.join(" · ") + (failures.length ? ` · failed — ${failures.join("; ")}` : ""));
    if (urls.length === 1 && added.length === 1) await openDetail(added[0].cid);
    setTaglineQueue(added);
  }
```

3d. In the grid return's toolbar, rewire the button:

```tsx
          <button className="subtle" onClick={downloadFromChub}>Download from URL</button>
```

becomes:

```tsx
          <button className="subtle" onClick={() => setUrlPromptOpen(true)}>Download from URL</button>
```

3e. Render the modal in the grid return, directly under the `taglineQueue` block:

```tsx
        {urlPromptOpen && (
          <UrlImportPrompt onClose={() => setUrlPromptOpen(false)} onSubmit={runBulkUrlImport} />
        )}
```

3f. Add the progress hint in the grid toolbar, next to the existing `bulkLocalize` hint:

```tsx
          {bulkUrl && (
            <span className="field-hint">
              Adding {bulkUrl.current}/{bulkUrl.total} — {bulkUrl.name}: {bulkUrl.step}…
            </span>
          )}
```

- [ ] **Step 4: Run the component tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: all pass, including the four rewritten/new tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(characters): bulk add from URL list with full import pipeline"
```

---

### Task 4: Full verification and served-bundle build

**Files:**
- Modify: none (verification only; `frontend/dist/*` changes from the build)

**Interfaces:**
- Consumes: everything above.
- Produces: the feature in the served bundle.

- [ ] **Step 1: Type-check and run the full frontend suite**

Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: clean build, all tests pass.

- [ ] **Step 2: Run the backend suite (regression check — no backend changes expected)**

Run (from repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass.

- [ ] **Step 3: Rebuild the served bundle**

Run (from `frontend/`): `npm run build`
Expected: `✓ built` — the backend serves `frontend/dist`, so without this the feature never reaches the running app.

- [ ] **Step 4: Verify the feature is in the bundle**

Run (from `frontend/`, PowerShell): `Select-String -Path dist/assets/index-*.js -Pattern "One URL per line" -Quiet`
Expected: `True`.

- [ ] **Step 5: Commit (only if dist is tracked; otherwise nothing to commit)**

```bash
git status --short
```

If `frontend/dist` shows as untracked/ignored, done — no commit. Otherwise `git add frontend/dist && git commit -m "chore: rebuild frontend bundle"`.
