# Frontend Consistency Audit

**Scope:** `frontend/src/` (~220 TS/TSX files, one 7,839-line `index.css`), plus the
backend view endpoints the campaign UI renders from.
**Method:** full read of the library-world and campaign-world view stacks, plus
systematic sweeps of data-fetching idioms, dialog/confirm flows, tab systems, card
classes, CSS duplication, and utility duplication. Every claim below carries a
`file:line` reference; all were verified against the working tree.

---

## Executive summary

| # | Finding | Severity |
|---|---------|----------|
| 1 | The campaign **World view does not apply the campaign overlay**: list endpoints walk library refs only — no emergent entities, no overrides, fabricated source chains. The UI renders `ChainBadge` as if it could show override/emergent state, but for items/locations/lore/factions it never can. | High |
| 2 | **Monsters are invisible in campaigns**: composable in `CompositionView`, listed in the library, served by `GET /api/campaigns/{id}/monsters` — but the frontend never calls it and there is no campaign tab. | High |
| 3 | Campaign **Greetings tab bypasses the read cascade entirely** — it fetches raw *library* greetings per world and silently swallows per-world failures. | High |
| 4 | **Two parallel async stacks** (`useApi`+`Loading` vs `useResource`+`AsyncBoundary`) with divergent loading/error/empty rendering, plus ~8 components that use raw `useEffect` instead of either. | High |
| 5 | **Six hand-rolled modal implementations**, none using Radix Dialog (contradicting the stated convention), none with Escape handling or focus trapping. | High |
| 6 | **Destructive-action confirmation is split**: rich `ConfirmDestructiveDialog` (dependents + typed confirmation) for library worlds/entities; bare `window.confirm` for campaigns, scenes, PCs, style guides, calendars, presets, holiday sets. | Medium |
| 7 | `errorMessage(err)` is hand-rolled in **9 files**; `slugify` exists **twice with different behavior**; two near-identical SaveIndicators. | Medium |
| 8 | **Five tab/nav systems** with three state models (URL routes, `useState`, hand-rolled `tab-row`); campaign World tabs are not URL-addressable. `role="tab"` is used without arrow-key support or `aria-controls` anywhere. | Medium |
| 9 | **Seven card class families** with copy-pasted CSS (25+ structurally identical blocks), 13 distinct `*-empty` classes, duplicate `.button-link` definition, hardcoded colors bypassing theme tokens. | Medium |
| 10 | Zod validation covers ~3 endpoints despite "Zod for runtime validation of API responses" being the stated convention; two raw `fetch()` escapes bypass both client layers. | Medium |

---

## Part I — The headline divergence: worlds in the Library vs. "World" in a campaign

The same conceptual objects — a world's characters, monsters, items, locations, lore,
factions, greetings — are presented through **three unrelated UI models** depending on
where you stand:

| Dimension | Library world (`routes/library/`) | Campaign → World (`routes/campaign/WorldView.tsx`) | Campaign → Cast (`routes/campaign/CastView.tsx`) |
|---|---|---|---|
| Kinds shown | characters, monsters, items, locations, lore, factions, greetings (`WorldDetailView.tsx:10-21`) | items, locations, lore, factions, greetings (`WorldView.tsx:24-30`) | characters only |
| Navigation | URL routes per kind (`library/index.tsx:25-31`), deep-linkable | local `useState` tabs (`WorldView.tsx:34`) — refresh/deep-link always lands on "Items" | single route, `?character=` param honored (`CastView.tsx:67`) |
| Layout | card grid → dedicated editor page | static cards with `<details>` preview | master/detail split pane |
| Card class | `library-card` (`EntityListView.tsx:293`) | `entity-card-static` (`WorldView.tsx:311`) | `entity-card` *as a button* (`CastView.tsx:111-115`) |
| Filters | `CardFilters`/`useCardFilters` | `CardFilters`/`useCardFilters` | bespoke `Filters` + hand-rolled `applyFilters` (`CastView.tsx:176-225`, `623-657`) |
| Actions | create, edit, delete (with dependents), convert, token badge | **none** — `CardIconBar actions={[]}` (`WorldView.tsx:300,324`) | remove-PC via `window.confirm`, override via raw-JSON dialog, promote |
| Editing fidelity | structured `EntityForm` driven by kind descriptors | n/a (read-only) | override = free-text JSON textarea (`CastView.tsx:497-503`) |
| Source attribution | n/a (library is the source) | `ChainBadge` rendered, but see §3 — always "library" | `ChainBadge` with real emergent/override states |

The Cast/World *split itself* is intentional (code comments cite "spec 14 §Cast view" /
"§World view"), and a play-centric cast pane is defensible UX. What is **not**
spec-driven is that the two halves disagree on data semantics, interaction model, card
anatomy, filtering, and editability — detailed below.

### 1. Kind coverage gaps

**Monsters.** Every layer except the campaign UI knows about monsters:

- Library tab: `WorldDetailView.tsx:13` (`monsters`), list/edit fully supported.
- Composition include-filter: `CompositionView.tsx:31-39` lets users include/exclude
  `monsters` per world ref.
- Backend: `GET /api/campaigns/{id}/monsters` exists
  (`backend/src/grimoire/api/campaigns/entities.py:63-68`).
- Frontend: `viewsApi` (`api/views.ts:26-115`) has **no `listMonsters`**, and
  `WorldView.tsx:24-30` has no Monsters tab.

Net effect: a user can compose monsters into a campaign and then has no way to see what
resolved. This is a pure frontend gap — the endpoint is ready.

**Characters.** Excluded from the campaign World view because Cast owns them. Fine per
spec — but Cast renders a totally different presentation (master/detail, bespoke
filters) for entities that are *peers* of items/locations in the library. A user moving
from the library's character grid to the campaign sees no continuity.

### 2. The Greetings tab bypasses the read cascade

The other four World tabs request campaign-scoped resolved lists. Greetings does not —
there is no campaign greetings endpoint, so the frontend fans out to **library**
endpoints directly:

- `WorldView.tsx:235-247`: fetch composition, then
  `Promise.all(worldIds.map((id) => viewsApi.listGreetingsForWorld(id).catch(() => [])))`.

Consequences:

1. Per-world failures are swallowed (`.catch(() => [])`) — a half-broken library shows
   a silently shorter list, no error state.
2. No emergent greetings, no overrides, no `ChainBadge` — greeting cards
   (`WorldView.tsx:286-302`) are the only entity cards in the campaign without source
   attribution.
3. The composition's per-ref `include` filter is ignored — a world ref that excludes
   `greetings` still gets its greetings listed here.

### 3. The campaign World view doesn't actually apply the campaign overlay

This is the deepest version of "displays in significantly different ways that aren't
accounted for by the overlay" — the list views *don't run the overlay at all*:

- `backend/src/grimoire/api/campaigns/helpers.py:286-318`
  (`_resolved_from_library_entity`): list endpoints wrap raw `LibraryEntity` rows in a
  fabricated single-element `source_chain` of `LIBRARY_LIVE`. The docstring is explicit:
  *"World-view listings walk the composition's world refs, which are all
  library-sourced … Per-entity campaign overrides are applied on the detail resolve
  path, not on these list endpoints."*
- `backend/src/grimoire/world/service.py:437-445` (`list_for_campaign`) →
  `library.list_for_composition` — library refs only.

So for items/locations/lore/factions:

- **Emergent entities never appear.** An item spawned during play exists in
  `campaigns/<id>/emergent/` but the World→Items tab won't list it. Emergent
  *characters* do appear in Cast (`characters.list_for_campaign` runs the real
  cascade), so the two campaign panes follow different resolution rules.
- **Overrides are invisible.** A campaign override on a location changes nothing in the
  list; `ChainBadge` (`routes/campaign/common.tsx:43-60`) has rendering branches for
  override/emergent that are dead code on these tabs.
- The empty-state copy — "No items **resolved for this campaign** yet"
  (`WorldView.tsx:73`) — promises cascade semantics the data path doesn't deliver.

Fixing this properly is a backend+frontend change (make the list endpoints resolve
through the cascade, or add a query flag), but the frontend should stop implying
semantics it doesn't have in the meantime.

### 4. Interaction-model divergence

- **Read-only dead end.** Campaign World cards render an empty `CardIconBar`
  (`WorldView.tsx:300,324`). Meanwhile the backend already exposes
  `POST /{campaign_id}/{kind}/{entity_id}/promote-to-library` for non-character kinds
  (`entities.py:131-152`) — unreachable from any UI. Characters got promote + override
  affordances in Cast; everything else got nothing.
- **Override editing fidelity.** Library editing is a structured, descriptor-driven
  `EntityForm` with widgets, ID slugification, and validation. The campaign-side
  equivalent (`EditOverrideDialog`, `CastView.tsx:451-520`) is a raw JSON textarea that
  overwrites the whole override and exists only for characters (backend constraint:
  `PATCH …/characters/{id}/override` is the only override route, `entities.py:92`).
  The "campaign overlay" concept — central to the architecture — has the weakest
  editing UI in the app.
- **Navigation asymmetry.** Library kind tabs are URL routes; campaign World tabs are
  component state. You can link a collaborator to
  `/library/worlds/ravenloft/locations` but not to a campaign's Locations tab.
- **Per-kind affordance asymmetries.** The campaign view *adds* niceties the library
  lacks (location hierarchy tree `WorldView.tsx:140-166`, lore grouped by keyword
  `WorldView.tsx:183-216`), while the library *adds* capabilities the campaign lacks
  (creation, conversion, token badges, extras editing). Neither is a superset; users
  must learn both.

### 5. Target model (recommendation)

Make "a world's contents" one component family, parameterized by scope:

1. **One `EntityBrowser`** (grid + filters + kind tabs + optional hierarchy/grouping
   decorators) consuming a common `ResolvedEntity`-shaped row, used by both
   `routes/library/EntityListView` and `routes/campaign/WorldView`.
2. **Scope-driven affordances**, not separate components: library scope → edit/delete/
   convert; campaign scope → view source chain, edit override, promote-to-library.
3. **Same kind coverage everywhere**: characters and monsters become tabs in the
   campaign World view too (Cast can remain as the play-centric *detail* surface;
   the World→Characters tab can deep-link into it).
4. **Campaign lists run the real cascade** (backend change), so the badge layer is
   truthful and emergent content is visible outside Cast.
5. **URL-addressable tabs** in both scopes.

> **Decision (2026-06-09):** Cast stays a separate view but becomes the true
> dramatis personae — PCs and emergent characters always, plus library
> characters once they appear in at least one scene's declared or present cast
> (derived from scene sidecars; no new bookkeeping). The campaign World view
> gains Characters and Monsters tabs showing the full resolved composition.
> First slice implemented on this branch: `GET /api/campaigns/{id}/cast`
> (`api/campaigns/entities.py`), Cast view reads it, and the campaign World
> view gained Characters (default) and Monsters tabs with an "In cast"
> cross-link into the Cast detail. Still open from this section:
> cascade-truthful list endpoints (§3), the shared `EntityBrowser`, a campaign
> greetings endpoint (§2), and override-editing parity (§4).

---

## Part II — Cross-cutting UI inconsistencies

### 6. Two-and-a-half async stacks

| Stack | Hook | Boundary | Loading | Error | Empty | Retry | Stale-data behavior |
|---|---|---|---|---|---|---|---|
| Campaign | `useApi` (`api/useApi.ts`) — `Loadable<T>` union | `Loading` (`routes/campaign/common.tsx:21-36`) | `<p class="muted">` | `<p class="error" role="alert">` | `<p class="muted">` | none | flashes to loading on reload |
| Library | `useResource` (`api/useResource.ts`) — `{data,error,loading}` | `AsyncBoundary` (`routes/library/AsyncBoundary.tsx`) | `<p class="library-status" role="status">` | `<div class="library-status library-error">` + **Retry button** | `<p class="library-status">` | yes | keeps previous data visible |
| Ad hoc | raw `useEffect`+`useState` | none | varies | varies (`error`, `wizard-error`, `library-error`) | varies | no | n/a |

Usage split (verified): `useApi` ×24 across 8 campaign files; `useResource` ×22 across
14 library files (plus `routes/campaign/MechanicsView.tsx:37`, which uses **both hooks
in one component**). Ad hoc `useEffect` fetching lives in `ContentBrowser.tsx:100-124`,
`CampaignView.tsx:46-61`, `CampaignsView.tsx`, `routes/library/WorldDependentsView.tsx:41-76`,
`CastView.tsx:540-556`, `CompositionView.tsx:429-447`, and others.

The user-visible result: library pages offer retry and don't flash on reload; campaign
pages flash and offer no retry; error text styling differs by section. One hook + one
boundary should win (the `useResource` semantics — retry + stale-while-revalidate — are
the better baseline; `useApi`'s discriminated union is the nicer type).

A small dead-code smell while there: `GreetingsTab` renders
`<Loading state={composition}>{() => <p>Loading composition…</p>}</Loading>` inside a
branch where `status !== "ok"` (`WorldView.tsx:226-230`) — the children can never
render; the fallback copy is unreachable.

### 7. Five tab/nav systems, three state models

| Implementation | Where | State | Classes |
|---|---|---|---|
| `NavLink` subnav | `CampaignView.tsx:86-99` | URL | `campaign-subnav-link` |
| `NavLink` tabs | `LibraryLayout.tsx:28-38` | URL | `library-tab` |
| `NavLink` tabs | `WorldDetailView.tsx:162-173` | URL | `world-tab` |
| `NavLink` tabs | `ObservabilityLayout.tsx`, `EntityEditorView` subtabs | URL | `observability-tab`, `entity-subtab` |
| Shared `Tabs` component | `routes/campaign/common.tsx:69-85` (used by `WorldView`) | `useState` | `tab` |
| Hand-rolled copy of `Tabs` | `ContentBrowser.tsx:60-72` — same markup, same classes, not the shared component | `useState` | `tab` |
| `useState` + `tab-bar` | `appsettings/AppSettings.tsx:31-50`, `campaign/settings/CampaignSettings.tsx:42-96` | `useState` | `tab-bar` |

Two concrete problems beyond aesthetics:

- **Addressability** is accidental: settings tabs and campaign World tabs aren't
  linkable or restorable; everything `NavLink`-based is.
- **A11y**: every `useState` variant emits `role="tablist"`/`role="tab"` with no
  arrow-key navigation, no `aria-controls`/`id` pairing with panels, and no roving
  tabindex (`common.tsx:69-85`, `ContentBrowser.tsx:60-72`). Per WAI-ARIA APG, that is
  worse than plain buttons. Either implement the tab keyboard pattern once in the
  shared `Tabs` or drop the ARIA roles.

### 8. Card system fragmentation

Seven block-card families render the "thing in a grid" concept: `campaign-card`,
`library-card`, `entity-card` (a `<button>`), `entity-card-static`, `timeline-card`,
`provider-card`, `why-character-card` — plus `suggestion-card` and `PostItem`. CSS-side,
the structural block (`background: var(--bg-elev); border: 1px solid
var(--border-subtle); border-radius: var(--radius-md); …`) is copy-pasted across 25+
rule blocks (`index.css:919-938, 2722-2743, 3996-4021, 4105-4111, 4220-4241,
6105-6140, 6354-6371`, …).

Behavioral drift between the two main entity cards:

- `library-card`: name + id + tags + `TokenBadge` + actionable icon bar, whole card is
  a `Link`.
- `entity-card-static` (campaign): name + `ChainBadge` + "world:" line + `<details>`
  preview + **empty** icon bar; not a link anywhere.
- `entity-card` (campaign Cast/ContentBrowser): the card *is* a button, with the icon
  bar outside it in the `<li>`.

A single `EntityCard` component with slots (badge, meta, preview, actions) plus a
`.card` base class would collapse most of this. (`CardIconBar` already proved this
pattern works for actions.)

### 9. Dialogs: six hand-rolled modals, zero Radix

CLAUDE.md states "Radix UI primitives for accessible components (Dialog, DropdownMenu,
Popover)" — but no dialog in the app uses Radix:

| Dialog | Pattern | Escape | Backdrop click | Focus trap |
|---|---|---|---|---|
| `ConfirmDestructiveDialog` (`routes/library/ConfirmDestructiveDialog.tsx`) | `modal-backdrop`/`modal` divs | no | no | no |
| `ForkDialog` (`routes/campaign/ForkDialog.tsx`) | same | no | no | no |
| `ConvertModal` (`routes/library/ConvertModal.tsx`) | `library-convert-modal` | no | no | no |
| `ImportDialog` (`routes/library/ImportDialog.tsx`) | `import-dialog` | no | no | no |
| `ImportSceneDialog` (`routes/campaign/ImportSceneDialog.tsx`) | `import-overlay` | no | no | no |
| `SceneLedgerDialog` (`routes/campaign/SceneLedgerDialog.tsx`) | `ledger-dialog-backdrop` | no | **yes** | no |
| `DiffPreviewModal` (`CompositionView.tsx:449-477`) | `modal-backdrop`/`modal-panel` | no | **yes** | no |
| `EditOverrideDialog`, `PromoteToLibraryDialog` (`CastView.tsx:490, 576`) | `modal-backdrop`/`modal` | no | no | no |

Note the class drift within one pattern: `modal` vs `modal-panel` vs three bespoke
overlay classes. One `<Dialog>` wrapper (Radix, or a single in-house base with
Escape + focus management) removes the whole category.

### 10. Destructive actions: two confirmation grammars (plus `window.prompt`)

- **Rich path** — `ConfirmDestructiveDialog` with dependents lookup and optional typed
  confirmation: library worlds and entities only (`WorldDetailView.tsx:184-203`,
  `WorldsListView`, `EntityListView.tsx:217-233`, `EntityEditorView`).
- **`window.confirm` path** (verified, 8 sites): deleting *campaigns*
  (`CampaignsView.tsx:156`), scenes (`TimelineView.tsx:58`), removing PCs
  (`CastView.tsx:50`), style guides (`StyleGuidesView.tsx:50`), calendars
  (`CalendarsView.tsx:94`), image presets (`ImagePresetsView.tsx:32`), holiday sets
  (`HolidaySetsView.tsx:85`), extras keys (`ExtrasTable.tsx:147`), template variants
  (`appsettings/TemplatesTab.tsx:116`).
- **`window.prompt` path**: forking a world (`WorldDetailView.tsx:82`) — while forking
  a *campaign* gets a real `ForkDialog`; time-skip minutes and free-text fact recording
  (`PlayView.tsx:111,122`).

Deleting a campaign (the highest-stakes artifact in `~/.grimoire/`) gets the weakest
confirmation in the app, and `ConfirmDestructiveDialog`'s dependents/typed-confirmation
machinery is exactly what it should be using. Suggested rule: *any* delete of a
`~/.grimoire/` artifact goes through `ConfirmDestructiveDialog` (promote it from
`routes/library/` to `components/`); `window.confirm`/`window.prompt` are banned (an
ESLint `no-restricted-properties` rule makes this stick, mirroring the existing
`no-bespoke-delete` rule).

### 11. Filtering

`CardFilters` + `useCardFilters` is the canonical toolbar (library worlds list, library
entity lists, campaign World tabs) — but `CastView` ships its own `Filters` component
and a hand-rolled `applyFilters` (`CastView.tsx:176-225, 623-657`) duplicating search +
tag logic with a different UI (selects + free-text "Tag contains") in a different CSS
family (`cast-filters` vs `card-filters`). TimelineView and ImagesView have large lists
with no search at all. Extending `useCardFilters` with a `facets` option (source/role
selects) would let Cast adopt it without losing functionality.

### 12. Empty states

13 distinct `*-empty` CSS classes (`scene-empty`, `side-empty`, `wizard-empty`,
`ledger-empty`, `hud-widget-empty`, `inspector-empty`, …) plus `muted` and
`library-status` paragraphs, with wording drift ("No X yet." / "No X resolved for this
campaign yet." / "Nothing to show." / "Nothing yet."). One `<EmptyState>` component
(message + optional action) and one class would do.

### 13. CSS & theming

- Single 7,839-line `index.css` with 1,119 class definitions. Well-sectioned, but the
  size invites the duplication documented above (cards ×25, chips/badges ×15, grids ×6,
  form-field conventions ×4: `.field`, `.entity-form-field`, `.sheet-field`,
  `.library-form label`).
- `.button-link` is defined **twice** with near-identical rules
  (`index.css:2697`, `index.css:4675`).
- Hardcoded colors bypass theme tokens: `.source-badge.llm/.user`
  (`index.css:6317-6330`: `#e3e8f0`, `#4a5568`, `#e8f5e9`, `#2e7d32`) — these won't
  adapt to the dark theme — and ~18 `var(--token, #literal)` fallbacks whose literals
  match neither theme (`index.css:1532-1535, 3204, 4349-4350`, …).
- Inline styles are not a problem (8 total, mostly legitimate dynamic widths).

Recommendation: introduce base primitives (`.card`, `.chip`, `.grid-cards`, `.empty`,
one form-row class), make section-specific classes modifiers, and consider splitting
`index.css` by section (Vite handles multiple imports fine) so ownership is reviewable.

---

## Part III — Code-level improvements

### 14. API layer

- **Two HTTP clients**: `api/client.ts` (ApiError normalization, optional Zod
  `schema` opt) and `api/library/request.ts` (30s TTL GET cache, no Zod). The mechanics
  endpoints are reachable through **both** (`viewsApi.installedMechanics`/`getSheetSchema`
  in `api/views.ts:105-109` vs `mechanicsApi` in `api/library/`), so one half of the
  app can see 30s-stale data the other half just changed. Fold the cache into the one
  client (per-namespace opt-in) and delete the second layer.
- **Zod coverage ≈ 3 endpoints** (`api/campaign/api.ts:42,123`, `api/inventory.ts:38`)
  against a stated convention of validating API responses. Either wire `schema:` into
  the high-traffic list endpoints or drop the convention from CLAUDE.md; the half-state
  is the worst option. ✔ *Resolved (#599): list/grid-feeding endpoints pass a
  `checkSchema` (dev-only `safeParse` + once-per-endpoint `console.warn`, raw payload
  returned either way), with the payload types `z.infer`'d from `api/schemas/`; strict
  `schema` parsing is reserved for transforming boundary parsers like sheet schemas.*
- **Raw `fetch()` escapes**: `WorldDependentsView.tsx` (bespoke `fetchJson`) and
  `ImagesView.tsx` (DELETE image job with no error handling) bypass ApiError handling
  entirely.

### 15. Utility duplication (the frontend lacks the backend's "shared helpers first" rule)

- `errorMessage(err: unknown): string` exists in **9 files**:
  `appsettings/shared.ts:10`, `campaign/settings/shared.ts:16`,
  `CampaignCreate/CampaignCreate.tsx:48`, `observability/HealthPanel.tsx:26`,
  `StartupWizard/StartupWizard.tsx:96`, `campaign/ContentBrowser.tsx:28`,
  `campaign/SceneBreakPrompt.tsx:23`, `campaign/PreRollConfirmation.tsx:38`,
  `campaign/CharacterCreation.tsx:48` (plus a variant `errorMessages` in
  `library/mechanics/MechanicsEditor.tsx:37`). One export next to `ApiError` in
  `api/client.ts` ends this.
- `slugify` ×2 with **divergent behavior**: `routes/library/slugify.ts` (strips
  apostrophes, no length cap, tested) vs `routes/CampaignCreate/types.ts:91-98`
  (64-char cap, no apostrophe handling, untested). Same user concept ("type a name, get
  an id"), different ids.
- `SaveIndicator` (`campaign/settings/SaveIndicator.tsx`) vs `ConfigSaveIndicator`
  (`appsettings/ConfigSaveIndicator.tsx`): same classes, same idea, one extra state —
  and behind them, `useAutoSavedResource` vs `useAppConfig` duplicate debounced-save
  logic in two `shared.ts` files.
- CLAUDE.md's backend section codifies "reach for shared helpers first; a private
  reimplementation is a review smell." The frontend needs the same paragraph and a
  `src/lib/` (or expanded `components/`) home: `errorMessage`, `slugify`, `<Dialog>`,
  `<EmptyState>`, `<Tabs>`, `<SaveIndicator>`, `<AsyncSection>`.

### 16. Accessibility

- Tabs: ARIA roles without the keyboard contract (§7).
- Dialogs: `aria-modal` without focus trapping or Escape (§9); focus is not returned to
  the trigger on close.
- Inconsistent live regions: errors are `role="alert"` in most places but plain `<p>`s
  in others (e.g. `wizard-error` sites); loading is `role="status"` in the library
  boundary only.
- `window.confirm`-based deletes are keyboard-accessible but unstyled, untranslated,
  and inconsistent with the themed app (§10).

### 17. Smaller smells

- `CastView` builds character refs with inline template strings in two places
  (`CastView.tsx:43-47, 83-87`) — `canonicalizeCharacterRef` exists but the ref
  *construction* should also be one helper (`api/campaign/characterRef.ts` is the home).
- `ContentBrowser` hand-rolls loading/error state and the tab row when `useApi` and
  `Tabs` exist in sibling files (`ContentBrowser.tsx:60-72, 94-124`); it also reuses
  wizard classes (`wizard-error`, `wizard-meta`) and `modal-actions` outside any wizard
  or modal (`ContentBrowser.tsx:246-255`).
- `WorldView.fetcherFor` carries a `lore` branch its callers' type excludes
  (`WorldView.tsx:51-62`) — harmless, but a sign of the per-tab copy-paste.
- Several "best-effort" fetches swallow *all* errors, not just the documented 404
  (`CampaignView.tsx:57`, `PreservedSheetsBanner` catch at `CampaignView.tsx:138-142`,
  greetings fan-out `WorldView.tsx:240`). Silent-failure policy should be: log to
  console in dev, never `catch(() => {})` bare.

---

## Part IV — Phased remediation plan

Ordered so user-visible consistency lands early and each phase is independently
shippable.

> **Status (2026-06-09, this branch):** Phases 0–2 and 4 are implemented, plus
> the Cast/World restructure's first slice (§5 decision note). Concretely:
> Monsters tab + URL-synced world tabs + greetings fan-out fixes; shared
> `errorMessage`/`slugify`; `components/Dialog` (Radix) with every modal
> ported; `ConfirmDestructiveDialog`/`PromptDialog` replacing all native
> popups (now lint-banned); keyboard-accessible `components/Tabs` adopted by
> the world view, content browser, and both settings screens; merged
> `SaveIndicator`; one async stack (`useResource` +
> `AsyncSection`/`AsyncBoundary`, `useApi` deleted); one HTTP layer (library
> cache wraps `api/client`, raw-fetch escapes gone); Cast filters on
> `CardFilters`; duplicate `.button-link` and hardcoded badge colors fixed;
> the Zod stance decided and wired (item 11, #599 — observational
> `checkSchema` on list endpoints, types inferred from `api/schemas/`).
> Phase 4 (#602): `index.css` split into per-section `src/styles/*.css`
> imports; `.card`/`.chip`/`.grid-cards`/`.form-field` primitives with section
> classes as modifiers; bespoke `*-empty` classes folded into `.empty-state`;
> `var(--x, literal)` fallbacks swept and never-defined token names re-pointed
> at real theme tokens. Phase 3 has since landed too: the backend half
> (#600) made campaign kind-list endpoints run the read cascade (emergent +
> overrides + truthful chains via `WorldService.list_resolved_for_campaign`),
> added the campaign greetings endpoint, and generalized override PATCH
> beyond characters (merge semantics); the frontend half (#601) renders both
> scopes' world contents through the shared `components/EntityBrowser`
> (`card entity-browser-card`, replacing `entity-card-static` and the
> `EntityListView` `library-card` usage) with scope-driven affordances —
> campaign cards expose edit-override (structured `EntityForm` patch mode;
> the raw-JSON dialog is gone) and promote-to-library. **Still open:**
> nothing from this audit; every phase is implemented.

**Phase 0 — Quick wins (no design decisions, hours each)**
1. Add `viewsApi.listMonsters` + Monsters tab in campaign `WorldView` (backend is
   ready). *(frontend only)* ✔ *Done — landed with the Cast decision (§5 note).*
2. Sync campaign World tab selection to the URL (`?tab=` or subroutes, matching
   library's URL-per-kind).
3. Export one `errorMessage` from `api/client.ts`; delete 9 copies. Unify `slugify`
   (library semantics + optional cap); delete the `CampaignCreate` copy.
4. Replace `window.confirm`/`window.prompt` destructive flows with
   `ConfirmDestructiveDialog` (move it to `components/`); add an ESLint
   `no-restricted-properties` rule for `window.confirm`/`window.prompt`.
5. Delete the duplicate `.button-link` block; replace `.source-badge` hardcoded colors
   with tokens.
6. Fix the Greetings tab error swallowing (surface per-world failures) and honor the
   composition `include` filter when fanning out.

**Phase 1 — Shared primitives (`components/`)**
7. `<Dialog>` (Radix `@radix-ui/react-dialog`, already the stated convention): port the
   6 hand-rolled modals; Escape/focus/backdrop become uniform.
8. `<Tabs>`: one component with `mode="route" | "state"`, full APG keyboard support;
   port `ContentBrowser`, settings `tab-bar`s, and the NavLink tab families onto it.
9. `<EmptyState>`, `<SaveIndicator>` (superset of both existing ones), and an
   `<AsyncSection>` built on a single merged hook (`useResource` semantics — retry +
   stale-while-revalidate — with `useApi`'s discriminated-union types). Migrate
   mechanically; delete `Loading` and `AsyncBoundary`.

**Phase 2 — One data layer**
10. Merge `api/library/request.ts` into `api/client.ts` (TTL cache as an option);
    re-point `mechanicsApi`/`libraryApi`; remove the two raw `fetch` escapes.
11. Decide the Zod stance: add schemas for the entity/campaign list endpoints (the ones
    that feed grids) or amend the convention. Prefer `safeParse` + console.warn in dev
    so drift is visible without crashing play. ✔ *Done — #599.*

**Phase 3 — Unify the world-contents experience (the §1-§5 fix)**
12. Backend: make campaign kind-list endpoints resolve through the cascade (emergent +
    overrides + real source chains), add a campaign greetings endpoint, and (if product
    agrees) generalize the override PATCH beyond characters. ✔ *Done (#600).*
13. Frontend: extract `EntityBrowser` from `EntityListView` + `WorldView` with
    scope-driven affordances (edit/delete/convert in library; chain/override/promote in
    campaign); wire the existing generic promote endpoint; replace the raw-JSON
    override dialog with the descriptor-driven `EntityForm` in patch mode.
    *(Characters + Monsters tabs and the appeared-only Cast endpoint already
    landed — §5 decision note.)* ✔ *Done (#601).*

**Phase 4 — CSS consolidation**
14. Introduce `.card` / `.chip` / `.grid-cards` / `.empty` / one form-row primitive;
    convert section classes to modifiers; split `index.css` by section; sweep the
    `var(--x, #literal)` fallbacks. ✔ *Done (#602) — primitives live in
    `src/styles/primitives.css` (`.form-field` is the form-row class and
    `.empty-state` the empty primitive); the `EntityBrowser` extraction (#601)
    then unified world-contents cards on `card entity-browser-card`
    (`library-card` remains only on the style-guide/preset/plugin/holiday
    list views).*

---

## Appendix — Verified inventories

**`window.confirm` / `window.prompt` sites:** `appsettings/TemplatesTab.tsx:116`,
`CampaignsView.tsx:156`, `library/ExtrasTable.tsx:147`, `library/StyleGuidesView.tsx:50`,
`library/WorldDetailView.tsx:82` (prompt), `library/CalendarsView.tsx:94`,
`library/ImagePresetsView.tsx:32`, `library/HolidaySetsView.tsx:85`,
`campaign/PlayView.tsx:111,122` (prompt), `campaign/TimelineView.tsx:58`,
`campaign/CastView.tsx:50`.

**`errorMessage` definitions:** `appsettings/shared.ts:10`,
`CampaignCreate/CampaignCreate.tsx:48`, `observability/HealthPanel.tsx:26`,
`StartupWizard/StartupWizard.tsx:96`, `campaign/settings/shared.ts:16`,
`campaign/ContentBrowser.tsx:28`, `campaign/SceneBreakPrompt.tsx:23`,
`campaign/PreRollConfirmation.tsx:38`, `campaign/CharacterCreation.tsx:48`; variant:
`library/mechanics/MechanicsEditor.tsx:37`; unrelated same-name:
`campaign/SideHud/widgets/widget-common.ts:79`.

**`ConfirmDestructiveDialog` consumers (all library):** `WorldDetailView`,
`WorldsListView`, `EntityListView`, `EntityEditorView`.

**Hook adoption:** `useApi` ×24 (8 campaign files); `useResource` ×22 (14 library files
+ `campaign/MechanicsView.tsx`, which uses both).

**Campaign entity endpoints (backend):** characters, items, locations, lore, factions,
monsters (`api/campaigns/entities.py:23-68`); generic promote
(`entities.py:131`); character-only override PATCH (`entities.py:92`). Frontend wraps
all but monsters and the generic promote (`api/views.ts`).
