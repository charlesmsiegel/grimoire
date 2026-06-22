# Frontend Deferred-UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Steps use `- [ ]`.

**Goal:** Build the WorldView authoring hub (characters, PCs, tags, locations/lore+keys, greetings, lorebook import) and extend the play space with a cast panel, start-from-greeting, and generate-opener — extending the existing "occult grimoire" identity.

**Architecture:** Self-contained editor components under `components/`, hosted by a thin `WorldView` tab route; all data flows through the typed `api` client. Tests mock `../api/client` (the established pattern).

**Tech Stack:** React 18, react-router 6, Vite, Vitest + Testing Library. Tokens-only CSS in `index.css`.

## Global Constraints

- Run from `frontend/`: `npm test` (vitest). Suite green at **49**; keep green each task.
- **Theme tokens only** — no hardcoded colors/fonts; reuse `--bg/--surface/--fg/--muted/--accent/--radius` and existing classes.
- Mirror existing patterns: `request`/`streamPost` in `client.ts`; `EditableRow`; `window.confirm` for deletes; reload list after mutations.
- Each new component gets a `.test.tsx` asserting wiring (calls + paths), not styling.
- Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Do not push/PR/touch main.

---

### Task 1: API client + types

**Files:** Modify `frontend/src/api/client.ts`; Test `frontend/src/api/client.test.ts`.

Add a `requestForm` helper (multipart) beside `request`:

```ts
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: form });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}
```

Add types (see spec "API client additions") and methods. Scope helper for entities:
`entityBase(scope) => scope.kind==='world' ? '/api/worlds/'+scope.id : '/api/campaigns/'+scope.id`
(use `{kind:'world'|'campaign', id}`). Method signatures exactly as in the spec; note
`createGreeting(wid, draft)` takes the full `{name,character,version,body?,requires_tags?,predecessor_join?}`.
`opener(cid,sid,prompt,onEvent)` reuses `streamPost(`…/opener`,{prompt},onEvent)`.

- [ ] **Test:** extend `client.test.ts` — stub `fetch`, assert e.g. `addTag('w','S')` POSTs `/api/worlds/w/tags` `{name:'S'}`; `addToCast('c','s',{kind:'pcs',id:'e'})` POSTs `/api/campaigns/c/scenes/s/cast`; `setEdges('w','g',{leads_to:['x']})` PUTs `/api/worlds/w/greetings/g/edges`; `lorebookImport('w',entries)` POSTs `/api/worlds/w/lorebook/import` `{entries}`. (~5 assertions.)
- [ ] Run `npm test src/api/client.test.ts` → fail → implement → pass.
- [ ] Commit: `feat(fe): API client methods for entities, characters, pcs, tags, greetings, cast, lorebook`.

---

### Task 2: WorldView shell + routing + CSS + Field helper

**Files:** Create `routes/WorldView.tsx`, `components/Field.tsx`; Modify `App.tsx`, `routes/WorldsView.tsx`, `index.css`; Test `routes/WorldView.test.tsx`.

- `App.tsx`: add `<Route path="/worlds/:wid" element={<WorldView/>} />`.
- `WorldsView.tsx`: make the row label navigate — pass `onSelect={() => navigate('/worlds/'+w.id)}` (use `useNavigate`); keep rename/delete.
- `Field.tsx`: `({label, children, hint?}) => <div className="field"><label>{label}</label>{children}{hint && <div className="field-hint">{hint}</div>}</div>`.
- `WorldView.tsx`: load `api.getWorld(wid)` for the title; render a `.tabs` strip; `useState` active tab (default `characters`); render the active editor (`<CharacterEditor wid/>` etc.); a "‹ Worlds" back-link.
- `index.css`: add `.tabs`, `.tab`, `.tab.active`, `.field`, `.field label`, `.field-hint`, `.form`, `.form-actions`, `.chip`, `.chip.on`, `.editor`, `.editor-list`, `.editor-body`, `.table`, `.table th/td` — tokens only (see spec "Visual direction").

- [ ] **Test:** `WorldView.test.tsx` — mock client (getWorld + each editor's list call returns []); assert tabs render; clicking "PCs" shows the PC editor's empty state / "New" control; back-link present.
- [ ] Run → fail → implement → pass.
- [ ] Commit: `feat(fe): WorldView hub route + section tabs + shared form CSS`.

> Tasks 3–9 each: write component + its `.test.tsx`, run that test file (fail→implement→pass), then `npm test` (full), then commit. Components are self-contained: they take `wid` (or `cid/sid`) and call `api` directly.

---

### Task 3: EntityEditor (locations/lore + keys)

**Files:** Create `components/EntityEditor.tsx`, `components/EntityEditor.test.tsx`.

`<EntityEditor wid kind/>` (kind: 'locations'|'lore'). Left: `EditableRow` list (+ a "New {kind}" picker). Selecting a row loads it into a form: `Field` name (text), body (textarea), keys (text, hint "comma-separated activation triggers; blank = always-on"). Save → `updateEntity` (or `createEntity` for new) then reload. Delete via row (`confirm`).
- [ ] **Test:** lists entities; create posts `{name,body,keys}`; editing keys + Save calls `updateEntity` with the keys; delete confirms. Commit `feat(fe): locations/lore entity editor with keys`.

---

### Task 4: TagEditor

**Files:** Create `components/TagEditor.tsx`, `.test.tsx`.

`<TagEditor wid/>`. `api.listTags` returns `{tid: display}`. Picker to add (`addTag`); `EditableRow` per tag (label=display, subtitle=id) with rename (`renameTag`) / delete (`deleteTag`, confirm). Reload after each.
- [ ] **Test:** renders a tag; add posts name; rename; delete. Commit `feat(fe): world tag vocabulary editor`.

---

### Task 5: PCEditor

**Files:** Create `components/PCEditor.tsx`, `.test.tsx`.

`<PCEditor wid/>` two-pane (`.editor`). Left rail: PC list + "New PC". Right: persona form (name, pronouns, summary, description textarea) bound to the selected **version**; a version selector (switch / + version / delete / set-default via `updatePC{default_version}`); **tag chips** from `listTags` toggling membership → `updatePC({tags})`. Save persona → `updatePCVersion` (or `createPC` for a brand-new PC). Load `readPC` on select.
- [ ] **Test:** create PC posts name; edit description + Save → `updatePCVersion`/`createPC`; toggling a tag chip → `updatePC` with the new tags; add a version → `createPCVersion`. Commit `feat(fe): PC editor (persona, versions, tag assignment)`.

---

### Task 6: CharacterEditor (+ import)

**Files:** Create `components/CharacterEditor.tsx`, `.test.tsx`.

`<CharacterEditor wid/>` two-pane. Left rail: character list + "New character" + "Import JSON" (file input → `importCharacter(wid,file,'json')`). Right: version selector (switch / + version / set-default / delete) + V3 card-field form over the selected version's `card.data`: name, description, personality, scenario, first_mes, mes_example, system_prompt, post_history_instructions, alternate_greetings (textarea, one per line ↔ array). Save → `updateVersion(wid,cid,vid, card)` (rebuild card preserving `spec`/`spec_version`/unknown `data` keys via spread). New character → `createCharacter`.
- [ ] **Test:** create posts name; editing description + Save → `updateVersion` with `card.data.description`; alternate_greetings textarea splits to array; set-default → `setDefaultVersion`; import file → `importCharacter`. Commit `feat(fe): character V3 card editor + versions + JSON import`.

---

### Task 7: GreetingEditor (+ import-from-character + edges)

**Files:** Create `components/GreetingEditor.tsx`, `.test.tsx`.

`<GreetingEditor wid/>` two-pane. Left rail: greeting list + "New greeting" + "Import from character" (pick character+version → `importGreetings`). Right: form — name, character `<select>` (from `listCharacters`), version `<select>` (from the chosen character's versions), body textarea, requires_tags chips (from `listTags`), predecessor_join `<select>` all|any. Below, an **edges** block: `leads_to` and `excludes` as chip multiselects over the *other* greetings (toggle on/off). Save → `createGreeting`/`updateGreeting` then `setEdges`. Load `readGreeting` + `listGreetings` (for edge targets) + plotmap edges via `readGreeting` is body-only, so fetch edges through a dedicated `getEdges`? Backend exposes edges only via PUT; **read edges from `availableGreetings`? no** — add nothing: edges are write-only on the greeting; for display, GreetingEditor keeps edges in local state seeded empty and the user re-asserts them on save. (Acceptable v1; note in code.) 

> Correction to avoid a read gap: expose edges by having `readGreeting` return them. Add to the **backend** `read_greeting`? Out of scope. Instead: the world `plotmap.json` is not surfaced. v1 GreetingEditor edits edges **blind** (sets, does not pre-fill). Document this limitation in the component and the spec's "deferred" list.

- [ ] **Test:** create greeting posts the draft; selecting character populates version options; toggling a requires_tag chip persists on Save; predecessor_join select; setting a leads_to chip + Save → `setEdges`; import-from-character → `importGreetings`. Commit `feat(fe): greeting editor (form, import-from-character, edges)`.

---

### Task 8: LorebookImport

**Files:** Create `components/LorebookImport.tsx`, `.test.tsx`.

`<LorebookImport wid/>`. File input + format `<select>` (lorebook|json|png|charx) → "Parse" → `lorebookParse` returns entries into local state. Render a `.table`: per row editable name (text), keys (text comma), category `<select>` (lore|locations), body preview (truncated). "Import all" → `lorebookImport(wid, entries)` → success line "Imported N entries". Parse writes nothing.
- [ ] **Test:** parse populates rows; changing a row's category updates state; Import posts the edited entries array; bad parse shows the error banner. Commit `feat(fe): lorebook import (parse, review/route, commit)`.

---

### Task 9: CastPanel + start-from-greeting + opener (CampaignView)

**Files:** Create `components/CastPanel.tsx`, `.test.tsx`; Modify `routes/CampaignView.tsx` (+ small CSS).

`<CastPanel cid sid sceneEmpty keySet onSeeded/>`:
- Cast list from `getCast(cid,sid)` (kind · name · role). "Add to scene": pick world character or PC (`listCharacters`/`listPCs`), role select (player/npc; PCs forced player), version select if multi → `addToCast` → reload cast.
- **Start from greeting**: `availableGreetings(cid)`; render each (disabled + `title=reasons.join('; ')` when `!available`); pick → `startFromGreeting(cid,sid,id)` → `onSeeded()` (parent reloads scene). Whole control disabled when `!sceneEmpty`.
- **Generate opener**: prompt textarea + "Generate" → `opener(cid,sid,prompt,onEvent)` streaming into a preview `<div>`; when done, "Save as greeting" (needs a character+version pick → `createGreeting` with the text + reasonable name) and "Copy". Disabled when `!keySet`.

`CampaignView`: render `<CastPanel cid sid={activeId} sceneEmpty={messages.length===0} keySet onSeeded={()=>selectScene(activeId)}/>` in the main column (e.g. a collapsible region above the transcript or a right rail). Pass `keySet` (already a prop).

- [ ] **Test:** `CastPanel.test.tsx` — renders cast; add-actor → `addToCast`; available greeting click → `startFromGreeting` + `onSeeded`; an unavailable greeting is disabled; opener streams (mock `opener` to invoke `onEvent({delta})` then resolve) into the preview; "Save as greeting" → `createGreeting`.
- [ ] Run full `npm test`. Commit `feat(fe): campaign cast panel + start-from-greeting + opener`.

---

## Final steps

- [ ] `npm test` green (≈49 + new). `npm run build` (tsc typecheck) passes.
- [ ] Whole-branch read-only review over `git diff lorebook-import...HEAD`; fix Critical/Important.
- [ ] Update `.superpowers/sdd/progress.md`. Squash branch to one commit (`git reset --soft lorebook-import` then one commit), keep branch.

## Self-Review

**Spec coverage:** client (T1) ✓; WorldView+tabs+links+CSS (T2) ✓; entities+keys (T3) ✓; tags (T4) ✓; PCs+versions+tags (T5) ✓; characters+card form+import (T6) ✓; greetings+import+edges (T7, with the edges-blind limitation documented) ✓; lorebook parse/route/commit (T8) ✓; cast panel + start-from-greeting + opener (T9) ✓. Deferred items (sync UI, suggested-cast, push panel, graph editor, export) explicitly out.

**Placeholder scan:** none — each task names files, behaviors, and concrete test assertions.

**Type consistency:** `api` method names here match the spec's "API client additions" block; components consume only those. `createGreeting(wid, draft)` clarified. The edges read-gap is resolved explicitly (blind-set v1, documented), not left ambiguous.
