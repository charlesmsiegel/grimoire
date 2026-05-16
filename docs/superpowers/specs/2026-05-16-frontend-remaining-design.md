# Frontend — Remaining Work

> Everything from the original `specs/14-frontend.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-frontend-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-frontend-design.md`
**Module:** `frontend/src/`

## 1. Wire the sheet widget library into Cast & Mechanics views

Spec 14 §Cast view requires the resolved character detail to render its "Mechanical sheet (rendered via widget library)". Spec 14 §Mechanics view requires "Sheets for all entities in the campaign that have sheets" rendered via the widget library.

Today:
- `frontend/src/sheets/` ships `SheetRenderer`, all 14 widgets, the generic fallback, and `scopeCss` — but the only importer is `sheets/index.ts`.
- `CastView`'s detail panel (`routes/campaign/CastView.tsx:202-213`) shows capabilities as a `<code>JSON.stringify(cap)</code>` list and has no sheet section.
- `MechanicsView` (`routes/campaign/MechanicsView.tsx:218-222`) renders the fetched sheet as `<pre>{JSON.stringify(sheet, null, 2)}</pre>`.

Wire `SheetRenderer` into both views:
- Need the schema for the active mechanics + sheet kind. Backend already exposes mechanics manifests with `sheet_kinds`; add (or surface) `GET /api/mechanics/{moduleId}/sheets/{kind}` returning the JSON Schema.
- Need the active module's `theme.css` text so `SheetRenderer` can scope it. Mechanics manifests already declare `ui.theme_css`; add an endpoint that serves the raw CSS body.
- `MechanicsView`'s `CharacterSheet` should call `SheetRenderer` with `(moduleId, schema, value=sheet, onChange=updateSheet, themeCss)` and PUT changes via the existing `viewsApi.setSheet` (or add one).
- `CastView`'s detail panel adds a "Mechanical sheet" section that does the same in read-only mode (`readOnly={true}`).

## 2. Activate the per-PC capabilities side panel

`SidePanel` (`routes/campaign/SidePanel.tsx:71-77`) renders a "Capabilities" stub with copy "Per-PC capabilities surface here once a mechanics module is active. (Task 34 wires the full sheet view.)" — but task 34 shipped without it.

Spec 14 §Campaign Play view side panel: "Capabilities (active PCs)" and "Mechanics (rolls, slots)". With §1 (sheet rendering) in place, pull each active PC's resolved capabilities from `viewsApi.listCharacters(campaignId)` (the `capabilities` field already comes back populated) and render per-capability chips or a compact list, plus a rolls/slots summary derived from the resolved sheet.

## 3. Edit override / Edit library / Promote to library from Cast view

`CastView`'s action row (`routes/campaign/CastView.tsx:216-230`) ships three `disabled` placeholder buttons titled "Wired in a follow-up task." Spec 14 §Cast view requires:

- **Edit override** — opens an editor for the campaign-local override file (REST endpoint exists per spec 14 §Backend contract under `PATCH /campaigns/{id}/characters/{id}` or similar — confirm shape during planning).
- **Edit library** — opens the library editor at `EntityEditorView` with the dependents warning (already implemented).
- **Promote to library** — `POST /campaigns/{id}/{kind}/{entity-id}/promote-to-library`, available only for emergent characters; surface a target-world picker.

Add the editor sheet/dialog, wire the API calls, and clear the `disabled` titles.

## 4. Bulk-create-missing-sheets

`MechanicsView`'s sheet panel (`routes/campaign/MechanicsView.tsx:153-157`) shows a `disabled` "Bulk-create N missing sheets" button. Spec 14 §Mechanics view: "Missing-sheets panel (entities that should have sheets under the active mechanics but don't — offer bulk create)."

Needs a backend endpoint to initialize sheets for all entities missing one under the active module (mechanics has `initialize_sheet`), and the button to call it and refresh.

## 5. Save-prompt-template-to-card

`ImagesView`'s `PromptTemplate` (`routes/campaign/ImagesView.tsx:231-238`) ships a `disabled` "Save to card" button. Spec 14 §Images view: "per-character prompt templates" — implies persistence.

Wire `PATCH /api/library/worlds/{world_id}/characters/{character_id}` (or campaign-override path for resolved overrides) with `image.{base_prompt, negative_prompt, canonical_seed}` and clear the `dirty` flag on success.

## 6. Image queue live panel

`ImagesView`'s `Queue` (`routes/campaign/ImagesView.tsx:119-133`) is a static placeholder. Spec 14 §Images view: "queue".

Subscribe to `image_queued` / `image_ready` / `imagegen_job_failed` (spec 14 §WebSocket events) and render active + queued jobs. The `state/campaignStream.tsx` `routeToStore` does not handle image events today — add either a per-campaign image-queue reducer or extend the global store, then drive the panel off it.

## 7. Composition view: diff preview, drag-and-drop reorder, mechanics/style/preset pickers

`CompositionView` (`routes/campaign/CompositionView.tsx`) ships the editor but with three gaps:

- The upgrade banner's "Preview diff" button is `disabled` (line 141). Needs a diff endpoint (or compose one client-side from `GET /library/worlds/{id}?version={n}` for both versions) and a modal renderer.
- Reorder is via ▲/▼ buttons; spec 14 §Composition view calls for "Drag-and-drop to reorder priority".
- The mechanics / style guide / image preset rows are free-text `<input>`s (lines 217-238); spec 14 mock-up uses dropdowns ("change ▼"). Reuse the catalog fetches already present elsewhere.

## 8. Add multi-PC PC switcher rich metadata + last-played

`PCSwitcher` (`routes/campaign/PCSwitcher.tsx:18-33`) is a bare `<select>`. Spec 14 §PC switcher renders a palette with "Aleksandr (vampire) — scene 47, Camden club, last played 12m ago" rows including scene / location / last-played per PC, and remembers each PC's last position.

Needs:
- Backend PCs payload to include per-PC `current_scene_id`, `current_location_ref`, `last_played_at` (or compose on the Frontend by joining `listPCs` with `listScenes`).
- A popover/listbox component (consider the v1 Radix pull-in below) replacing the `<select>`.
- Restore "remember each PC's last position": when switching, the `usePlayState` should refetch that PC's `set-current-scene` and re-orient. Today the active scene is derived from "first open scene" regardless of PC.

## 9. Scene jump within a campaign

Spec 14 §Performance budgets: "Scene jump: < 500ms to render". The TimelineView lets the user select a scene (`TimelineView.tsx:75-79`) which only opens a detail panel inline; there is no way to jump the play view to that scene. Add either a "Jump to scene" affordance on `SceneDetail` that updates the active scene id (and survives reload), or a route param like `/campaigns/:id/play?scene=...`.

## 10. Top-bar PC switcher placement + campaign name in PlayView

Spec 14 §Campaign Play view top bar shows `campaign name | active PC switcher` side-by-side. Today `PlayView` (`routes/campaign/PlayView.tsx:91-93`) shows only `<h2>Campaign · {campaignId}</h2>` — no PC switcher in the top bar, no real campaign name (just the id), and the switcher lives inside `InputArea` instead. Move/duplicate the switcher into the top bar and pull the real campaign name from the store.

## 11. Cmd-K-style world / campaign quick switcher (rejected)

Spec 14 §Open questions: "Multi-campaign quick switcher. Cmd-K palette for jumping. Nice-to-have."

The original spec marks this as a nice-to-have and the YAGNI bar isn't met yet (recent campaigns are one click in `NavSidebar`). **Treat as rejected**; revisit only if real users complain.

## 12. Cross-world variant diff preview

`VariantsPanel` (`routes/library/VariantsPanel.tsx`) lists variants with a short snippet. Spec 14 §Cross-world variant view: "lists them with **diff preview**." Add a diff renderer (against either the body text or the frontmatter shape — pick one during brainstorm) per variant pair.

## 13. Image preset live sample preview

`ImagePresetsView` (`routes/library/ImagePresetsView.tsx:114-122`) renders a static placeholder where spec 14 §Style guides, image presets says "Image preset editor previews against a sample." Once an ImageGen backend is wired (it ships per the imagegen plugin), call `POST /api/library/image-presets/{id}/preview` (or compose via `POST /api/campaigns/{id}/images/generate` with the preset id) and render the returned image.

## 14. Style guide & image preset create/edit parity

Style guides have a full create + edit flow (`routes/library/StyleGuidesView.tsx`). Image presets do not — `ImagePresetsView` is read-only. Spec 14 §Style guides, image presets: "Simple text/config editors." Add list `+ New`, detail edit, and delete.

## 15. Persist per-campaign settings tabs

`CampaignSettings` (`routes/CampaignSettings.tsx`) tabs **Routing**, **ImageGen**, **Storage**, **Advanced** surface form structure but explicitly do not persist (line 4-11 module docstring). Spec 14 §Per-campaign settings expects all to persist. For each tab:

- **Routing**: `PUT /api/campaigns/{id}/routing` with `{llm: {task: plugin_id}, embedding: {task: plugin_id}}`
- **ImageGen**: `PUT /api/campaigns/{id}/imagegen` with `{backend, preset, sampler_defaults}`
- **Storage**: `PUT /api/campaigns/{id}/storage` with `{schedule, retention_days}`
- **Advanced**: `PUT /api/campaigns/{id}/advanced` with `{debug_log: bool, per_task_prompts: {task: text}}`

Confirm shapes with the backend during planning.

## 16. App settings: library-path editing + backup persistence

`AppSettings`' Library tab (`routes/AppSettings.tsx:115-130`) is read-only with "in-app editing ships with the configuration editor". Backup tab is local-state-only. Wire `PATCH /api/config/app` (or whatever the backend exposes) for both.

## 17. App settings: Appearance font + density wiring

`AppearanceTab` (`routes/AppSettings.tsx:948-984`) keeps `fontFamily` and `density` as local state — neither is applied to layout. Plumb them through the theme/store so CSS variables update on `<html data-font="..." data-density="...">` and ship the matching stylesheet.

## 18. Per-mechanics PostCSS build-time scoping (v2; deferred)

Spec 14 §Tech stack lists "PostCSS plugin for per-mechanics CSS scoping." Today only the runtime scoper (`sheets/scopeCss.ts`) exists, which is fine for theme.css dropped in at runtime. A build-time variant would be needed only when mechanics modules also ship bundled JS/CSS via Vite — see §19. **Defer to v2.**

## 19. Custom JS bundles for mechanics sheets (v2; deferred)

Spec 14 §v2 escape hatch: "The mechanics manifest can declare custom UI bundles for specific sheet kinds. The Frontend dynamically imports them ... Out of v1 scope. The widget library is sufficient to ship." Confirmed by §1 above — keep the JSON-schema path as the only v1 surface. **v2.**

## 20. Custom widget types from mechanics (v2; deferred)

Spec 14 §Open questions: "Custom widget types from mechanics. A mechanics module declares its own widget — registered with the Frontend at load time. v2." Out of scope.

## 21. Per-campaign Frontend extensions (v2; deferred)

Spec 14 §Open questions: "Per-campaign Frontend extensions. Mechanics-specific panels (a Vampire chronicle wants a Prince-tracker; an Ars Magica saga wants a Tribunal panel). v2." Out of scope.

## 22. Library sharing UI helpers (v2; deferred)

Spec 14 §Open questions: "Library sharing. Export/import of world bundles between users (zip a directory). File-based; UI helpers in v2." Out of scope.

## 23. Library activity feed (v2; deferred)

Spec 14 §Open questions: "Library activity feed. Recent edits across library, affected campaigns. Useful for active multi-campaign users." Listed as deferred — leave alone unless real demand surfaces.

## 24. Plugin UI extensions (v2; deferred)

Spec 14 §Open questions: "Plugin UI extensions. Mechanics-provided React components for sheets — v2; the architecture supports it." Overlaps with §19; defer.

## 25. Offline support (v2; deferred)

Spec 14 §Open questions: "Offline support. Desktop app should work offline (local models + library files). Cloud-model dependencies surface offline mode." Out of v1 — the SPA today assumes the backend is reachable.

## 26. Tauri desktop packaging (later)

Spec 14 §Tech stack: "Tauri for desktop packaging (later)." The Vite SPA ships dev-server-only today. Pick this up when the rest of the v1 surface stabilizes; not blocking.

## 27. Headless accessibility primitives migration

Spec 14 §Tech stack calls out "Headless accessibility primitives (Radix / Headless UI)" and `components/a11y.tsx:3-4` admits "Heavier components (popovers, dialogs) will move to Radix when those views are implemented; this file keeps the shell self-contained." Current dialogs are bare divs with `role="dialog" aria-modal="true"` (e.g. `EntityEditorView`'s `ConfirmEditDialog`); current PC switcher is a `<select>`. When §8 (rich PC switcher) lands or any popover/combobox is needed, pull in Radix Primitives or Headless UI rather than hand-rolling a third one.

## 28. Performance budgets verification

Spec 14 §Performance budgets sets explicit numbers (initial load < 2s, library 100 assets < 500ms, campaign switch < 300ms with library cached, etc.). Today there is no instrumentation. Add Lighthouse runs to CI (or at minimum a manual perf-budget checklist in the verification skill) before claiming the budgets are met. Library caching across campaign switches relies on `useResource`'s default `[]` dep list — actual cache behavior should be measured rather than assumed.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 (sheet rendering in Cast + Mechanics) — unlocks §2, §3, §4 by giving them a live sheet to act on
2. §2 (capabilities side panel) + §4 (bulk-create-missing-sheets) — natural follow-ups using §1's wiring
3. §3 (Cast actions: edit override / edit library / promote) — touches editor flows in a focused way
4. §6 (image queue) + §5 (save prompt to card) + §13 (preset sample preview) + §14 (preset create/edit) — Images & Library tail
5. §7 (composition diff + DnD + dropdowns) + §8 (rich PC switcher) + §10 (top-bar layout) + §9 (scene jump) — UX polish on the play loop
6. §15 + §16 + §17 — Settings persistence sweep (Routing, ImageGen, Storage, Advanced, Library path, Backup, Appearance)
7. §12 (variant diff preview)
8. §27 (Radix migration) — only when §8 lands and a real popover is needed
9. §28 (performance instrumentation) — final pre-release pass

Items §11 (rejected), §18-§26 (v2 / deferred) stay out of the queue unless their gating conditions change.
