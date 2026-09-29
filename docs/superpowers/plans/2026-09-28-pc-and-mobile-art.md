# PC Detail and Mobile Art Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PCs a dedicated character-style detail page and make PC and character art directly visible and reachable on phones.

**Architecture:** Keep the existing PC URLs, route record URLs to a new `PCPage`, and leave the recordless WorldView section as a PC list and creator. Move the current PC record logic from `PCEditor` into the page without duplicating writes. Use a small shared current-version art viewer and phone art entry on PC and character pages; retain both pages' existing art shelves and editing APIs.

**Tech Stack:** React, React Router, TypeScript, Vitest, existing FastAPI image endpoints.

**Spec:** `docs/superpowers/specs/2026-09-28-pc-and-mobile-art-design.md`

## Global Constraints

- Preserve `/worlds/:wid/pcs/:pid` and `/campaigns/:cid/world/pcs/:pid` as canonical PC record URLs; `sectionHref` builds them.
- Keep PC image, persona, version, sheet, campaign sync, and avatar crop writes on the existing APIs. No backend or store format changes.
- Use only repository placeholder names in tests and docs; never inspect the real data store.
- A read failure is not an empty record or empty art shelf. Old record/version replies cannot replace current state.
- Defer adversarial review to the PR, as the user previously directed; run the ordinary checks available in this Windows environment.

## Review Focus

- A direct link to a missing PC shows an error rather than an empty/new PC form (Task 1 test).
- Creating a PC through either creator opens its own URL in edit mode, while a later visit starts read only (Task 1 tests).
- A slow image list for version A cannot replace version B's art (Task 2 test).
- An image write in campaign scope stays in campaign scope, and a failed write leaves the selected record/version in place (Task 2 tests).
- A gallery-only version on a phone exposes the gallery image and direct Art action; a version with no art still exposes the add path (Task 4 tests).

---

### Task 1: Dedicated PC route and record page

**Files:**
- Create: `frontend/src/routes/PCPage.tsx`, `frontend/src/routes/PCPage.test.tsx`
- Modify: `frontend/src/components/PCEditor.tsx`, `frontend/src/components/PCEditor.test.tsx`, `frontend/src/routes/WorldView.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`

**Interfaces:**
- `PCPage({ campaign = false }: { campaign?: boolean })` derives the scope and PC id from the route, keys the record body by scope/id, and renders one `PageShell`.
- The recordless `PCEditor` lists and creates PCs. `navigate(recordHref(pid), { state: { newPC: true } })` opens a just-created PC in edit mode; direct links do not.
- The page owns `detail`, selected `vid`, `persona`, read/edit mode, version and campaign-lock operations, tags, `LibraryPanel`, `OwnedLorePanel`, and optional `SheetPanel`. Task 2 adds its art panel.

- [ ] **Step 1: Write failing route and page tests.** Assert both PC URLs mount one `PageShell`, a PC list row navigates to the dedicated page, a direct link reads the persona without a textarea, Edit/Save/Cancel keep current semantics, and a missing PC reports an error. Assert new-PC and sheet-wizard creation navigate with `newPC` state and open the new page in edit mode.
- [ ] **Step 2: Run those tests from `frontend/`; confirm the route/page assertions fail for the absent `PCPage`.**
- [ ] **Step 3: Move PC record state and writes from `PCEditor` to `PCPage`; reduce `PCEditor` to its list and creation controls.** Register exact record routes before the WorldView splats in `App.tsx`; use `sectionHref` for links. Resolve the optional module in the page as `CharacterPage` does. Keep one owner for each write rather than leaving the old editor's record logic alongside the new page.
- [ ] **Step 4: Run `PCPage`, `PCEditor`, `WorldView`, App route, and world-path tests until green.** Update old editor tests to assert the new route contract rather than testing a record view that no longer exists.
- [ ] **Step 5: Commit the route and persona move.**

### Task 2: PC Art tab and current-version image behavior

**Files:**
- Create: `frontend/src/components/pc/PCArtTab.tsx`, `frontend/src/components/pc/PCArtTab.test.tsx`
- Modify: `frontend/src/routes/PCPage.tsx`, `frontend/src/routes/PCPage.test.tsx`, `frontend/src/components/PCEditor.test.tsx`

**Interfaces:**
- `PCArtTab` receives `scope`, `wid`, `pid`, `vid`, image listing, image descriptions, and callbacks for refreshed detail/listing and reported errors. The page owns selected version and stale-read cancellation.
- Image URLs use `api.actorImageUrl(scope, "pcs", pid, vid, name, { w, v })`; the listing's `v` token is used after upload, promote, remove, description save, or crop change.

- [ ] **Step 1: Write failing tests.** Assert Art tab count and version-specific avatar/gallery, numeric `gallery_N` order, original-image links, add/promote/remove, description Save/Draft only in world scope, crop, campaign-scope writes, a failed image listing distinct from an empty listing, and stale version-A listing rejection after switching to B.
- [ ] **Step 2: Run focused tests; confirm missing Art-tab and failure-state assertions fail.**
- [ ] **Step 3: Extract the existing PC image actions from the old editor into `PCArtTab` and connect them to `PCPage`.** Keep the page's selected id and version through write errors. Refresh both detail and the listing after a successful write; cancel or ignore stale reads on scope, PC, and version changes.
- [ ] **Step 4: Run focused PC tests and typecheck until green.**
- [ ] **Step 5: Commit PC Art parity.**

### Task 3: Large current-version art viewer

**Files:**
- Create: `frontend/src/components/CurrentArtViewer.tsx`, `frontend/src/components/CurrentArtViewer.test.tsx`
- Modify: `frontend/src/components/pc/PCArtTab.tsx`, `frontend/src/components/character/ArtTab.tsx`, `frontend/src/routes/CharacterPage.test.tsx`, `frontend/src/index.css`

**Interfaces:**
- `CurrentArtViewer({ images, label }: { images: { name: string; url: (w?: number) => string }[]; label: string })` shows the selected current-version image large, lets a tile select another image, and links that image to its original URL. Empty input renders a plain empty state.
- Character ArtTab passes only the selected version's avatar and gallery to this viewer; inherited current-version images count, while shadowed world copies and other versions remain in their existing shelves.

- [ ] **Step 1: Write failing viewer and CharacterPage tests.** Assert avatar-first viewing, gallery-first fallback, tile selection, selected-version refresh, original link, image-free state, and preserved character world/campaign shelves and controls.
- [ ] **Step 2: Run focused tests; confirm the large viewer assertions fail.**
- [ ] **Step 3: Implement `CurrentArtViewer` and mount it above both Art shelves.** Draw a downscale sized to the main area, link to the original, and let image changes reset selection to a valid image.
- [ ] **Step 4: Run viewer, PC Art, and CharacterPage tests and typecheck until green.**
- [ ] **Step 5: Commit the shared viewer.**

### Task 4: Visible phone art entry on PC and character pages

**Files:**
- Create: `frontend/src/components/MobileArtEntry.tsx`, `frontend/src/components/MobileArtEntry.test.tsx`
- Modify: `frontend/src/routes/PCPage.tsx`, `frontend/src/routes/PCPage.test.tsx`, `frontend/src/routes/CharacterPage.tsx`, `frontend/src/routes/CharacterPage.test.tsx`, `frontend/src/index.css`

**Interfaces:**
- `MobileArtEntry` receives the current version's avatar or first gallery image URL, name, and `onOpenArt` callback. It renders a main-area preview and a visible **View art** action even when no image exists.
- PC and character tab state both open Art from this action; phone tabs expose Art without horizontal scrolling. Desktop identity portraits and crop controls remain in the context column.

- [ ] **Step 1: Write failing 375px viewport tests for both pages.** Assert a main-area preview and View art action with avatar, gallery-only, and image-free versions; clicking it opens the Art pane. Assert no duplicate desktop preview, and that the Art tab is visible in the phone tab row without horizontal scrolling.
- [ ] **Step 2: Run focused tests; confirm phone visibility assertions fail.**
- [ ] **Step 3: Implement the shared entry and responsive tab layout.** Reuse the selected version's image tokens and existing thumbnail builder; make the portrait and Art action touch targets. Keep desktop `identity-art` and crop behavior.
- [ ] **Step 4: Run both page suites, typecheck, and production build until green.**
- [ ] **Step 5: Commit mobile art access.**

### Task 5: Integration and verification

**Files:**
- Modify only tests or documentation needed to align the integrated behavior.

**Interfaces:** Every PC URL, version, and image control names the same record after route navigation, and both mobile pages expose the same Art destination.

- [ ] **Step 1: Check each spec requirement against the final diff and repair gaps.** In particular trace the two PC scopes, new-PC edit state, version-specific image tokens, and mobile gallery-only fallback.
- [ ] **Step 2: Run frontend coverage, typecheck, build, ESLint baseline, affected backend guards, and `git diff --check`.** Record any pre-existing or environment-only full-gate failures separately from this change.
- [ ] **Step 3: Exercise both PC routes and the character route at phone width against the isolated `verify` store if the browser harness is available.** Use only placeholder data and no real library screenshots.
- [ ] **Step 4: Inspect the staged diff for private names, commit any integration fixes, and prepare the branch for PR review.** Honor the user's instruction to leave adversarial review for the PR.
