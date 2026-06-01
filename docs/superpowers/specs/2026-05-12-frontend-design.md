# Frontend — Design (Shipped)

> Captures the Frontend design as actually built. The matching "remaining" spec at `2026-05-16-frontend-remaining-design.md` covers everything from the original `specs/14-frontend.md` that did **not** land in this work.

**First substantive commit:** `ac0c977` — "Scaffold frontend shell, routing, WebSocket client (task 29)" (followed by `e24b9f7` sheet widgets, `46db65a` library views, `252c8bf` play view, `9426305` cast/world/timeline/mechanics/composition/images, `1a9198f` campaign creation + settings, `08ed662` design pass, `d74db4a` startup wizard, and a long tail of fixes)
**Module:** `frontend/src/`
**Stack:** TypeScript + React 18 + Vite 5; React Router v7; `react-markdown` + `remark-gfm` for prose. No Tailwind, no Radix, no UI framework — hand-rolled CSS modules and primitives.

## Purpose

The Frontend is a desktop-first SPA that talks to the FastAPI backend over `/api/*` REST and one `/ws/campaigns/{id}/stream` WebSocket per mounted campaign. It surfaces the library (worlds, style guides, image presets, installed mechanics, installed plugins), the per-campaign play view, and the supporting per-campaign views (Cast, World, Timeline, Mechanics, Composition, Images). It also hosts the sheet widget library and the app/per-campaign settings.

Backend modules own state of truth (spec 14 §State management); the Frontend is a thin reader/dispatcher and never reasons about scenes, drift, or rolls itself.

## Top-level shell

`frontend/src/main.tsx` mounts `<App>` into `#root` inside `<StrictMode>`. `App.tsx:21-50` wires:

- `<ThemeProvider>` (`state/theme.tsx`) — `light` / `dark` / `system`, persisted in `localStorage["grimoire.theme"]`, applied via `document.documentElement.dataset.theme` and `colorScheme`. `cycle()` rotates light → dark → system.
- `<StoreProvider>` (`state/store.tsx`) — `useReducer` over `state/storeContext.ts` (campaigns, active campaign id, review queue, status info) plus two mutation helpers: `optimisticMutate(action, commit)` and `pessimisticMutate(commit, onSuccess)`.
- `<BrowserRouter>` with a top-level `<AppShell>` route hosting nested routes for `/`, `/library/*`, `/campaigns`, `/campaigns/new`, `/campaigns/:id` (with child routes for play / cast / world / timeline / mechanics / composition / images), `/campaigns/:id/settings`, `/settings`, and `*` → `NotFound`.

`AppShell` (`shell/AppShell.tsx`) hosts the persistent `<NavSidebar>`, the routed main pane, and a `<StatusBar>` bridge. It owns the global keyboard shortcuts (`Ctrl/Cmd+L` Library, `Ctrl/Cmd+K` Campaigns, `Ctrl/Cmd+B` collapse sidebar, `T` cycle theme), the auto-opening first-run startup wizard (driven by `useSetupStatus()`), and a `grimoire:open-startup-wizard` window event that lets Settings re-launch the wizard manually.

`NavSidebar` (`shell/NavSidebar.tsx`) shows Library / Campaigns / Settings plus a "Recent campaigns" list pulled from the store. `StatusBar` (`shell/StatusBar.tsx`) shows WS connection status, active campaign, model label, token budget, queue depth, drift count, and the theme cycle button.

## API client

`api/client.ts` is a minimal typed `fetch` wrapper. `request<T>` raises `ApiError(status, detail)` for non-2xx, rejects non-JSON success responses (the SPA fallback returns `index.html` with 200 when a missing `/api` prefix slips through), and exposes `api.get / post / put / patch / delete`.

Hooks `useApi` (`api/useApi.ts`) and `useResource` (`api/useResource.ts`) handle loading/error/data with a per-effect cancel flag so a stale fetch never overwrites a fresh one.

Per-domain wrappers under `api/`:
- `library.ts` — worlds, entities (characters / items / locations / lore / factions / greetings), style guides, image presets, mechanics, plugins, templates
- `campaign.ts` — PCs, scenes, posts, turns (submit / advance / regenerate / undo / end), commitments, facts, time-advance, image generate
- `views.ts` — resolved cast / items / locations / lore / factions / greetings; composition read/write; upgrade-ref; sheets; installed-mechanics
- `wizard.ts`, `setup.ts` — campaign creation + first-run setup
- `types.ts` — shared payload types

## WebSocket layer

`ws/client.ts:26-167` — `CampaignSocket` opens one connection per mounted campaign with exponential-backoff reconnect + jitter (default `500ms` initial, `30s` cap), suppresses reconnect after `close()`, and parses incoming `{ type, ... }` JSON messages. Statuses: `idle | connecting | open | closed | reconnecting`. `campaignStreamUrl(id)` builds the `ws(s)://host/ws/campaigns/{id}/stream` URL.

`state/campaignStream.tsx` provides one `<CampaignStreamProvider>` per active campaign id; on mount it builds a `CampaignSocket`, subscribes for status + messages, routes a subset of events into the global store via `routeToStore` (drift alerts, review-queue adds, status-bar fields), and exposes `useCampaignEvent(types[], handler)` for view-local subscriptions. Views supply their own handlers for `token`, `turn_complete`, `scene_started`, `scene_ended`, `pc_post_appended`, `image_ready`, `drift_detected`, etc.

## Library views (`routes/library/`)

`LibraryRoutes` (`routes/library/index.tsx`) nests under `<LibraryLayout>` (`LibraryLayout.tsx`) which surfaces five top-level tabs:

1. **Worlds** — `WorldsListView` lists worlds with `+ New world` inline form; `WorldDetailView` shows world header + breadcrumb + tabs (`characters` / `items` / `locations` / `lore` / `factions` / `greetings` / `meta` / `dependents`). Each entity kind tab is `EntityListView` (grid of cards + `+ New <kind>` inline form). `WorldMetaView` edits world metadata including raw JSON for calendar / atmosphere / defaults. `WorldDependentsView` lists campaigns that reference this world.
2. **Style guides** — `StyleGuidesView` with list, create, edit, detail sub-routes. Sections `pacing`, `voice`, `themes`, `avoid` are bullet-list editors; tags are comma-separated; body renders via `Markdown`.
3. **Image presets** — `ImagePresetsView` lists presets and renders preset detail with style preamble, negative prompt, default-params table, tags. Sample preview is a static placeholder ("unavailable until an ImageGen backend is configured").
4. **Installed mechanics** — `MechanicsView` (the library one) lists installed modules, "Rescan" button, expandable "What a mechanics module requires" requirements doc, per-module detail with manifest summary (sheet kinds, content kinds, capabilities, UI manifest, theme.css explanation).
5. **Installed plugins** — `PluginsView` lists installed plugins with kind filter (`llm_provider` / `embedding_provider` / `imagegen_backend` / `export_adapter` / all), rescan, per-plugin detail with manifest, config form rendered from `config_schema` via `SchemaField`, and a "Check now" health probe.

### Entity editor

`EntityEditorView` (`routes/library/EntityEditorView.tsx`) is the per-asset editor. Sub-tabs: `Editor` (frontmatter form + markdown body textarea), `Capabilities` (characters only — explains that mechanical sheets live per-campaign), `Variants` (cross-world same-asset-id list), `Preview` (rendered markdown). For characters, `CharacterExtras` adds dedicated voice / image-prompt fields. Greetings get a bespoke `GreetingFormFields` editor instead of the generic frontmatter/body split.

When the entity has dependent campaigns, save raises a confirmation dialog explaining "edits will be visible to campaigns when they upgrade their ref; pinned campaigns continue seeing the previous version" before persisting.

### Cross-world variants

`VariantsPanel` (`routes/library/VariantsPanel.tsx`) lists entities across worlds sharing the same `asset_id` with a short snippet — informational only, no diff or merge.

## Campaign play view

`CampaignView` (`routes/CampaignView.tsx`) renders the persistent campaign nav + an `<Outlet />`. The default child route (`CampaignPlayRoute`) mounts `<PlayView campaignId={...}>` (`routes/campaign/PlayView.tsx`), composed of:

- `SceneHeader` — location, in-game time, present cast
- `DriftBanner` — surfaces unsuppressed `drift_detected` events with per-character suppress button
- `ScenePane` — posts list (via `PostItem`), inline streaming buffer, generated images keyed by `post_id`, auto-scroll to bottom on new content
- `InputArea` — `<PCSwitcher>` + textarea (Ctrl/Cmd+Enter submit) + Submit button + Advance button (multi-PC scenes only)
- `SidePanel` — present cast, active threads, open commitments (top 5), capabilities placeholder, quick actions (Regenerate / Undo / End scene / Skip time / Manual fact)

`usePlayState(campaignId)` (`routes/campaign/usePlayState.tsx`) owns the per-PC + per-scene state with a `useReducer`. It loads PCs, picks the active one (the server-marked active or first), fetches the open scene, and subscribes to a fixed list of stream event types (`token`, `turn_complete`, `post_appended`, `pc_post_appended`, `scene_started`, `scene_ended`, `advance_disabled`, `advance_enabled`, `advance_requested`, `image_ready`, `drift_detected`, `scene_file_changed`). The reducer applies `token` deltas to an in-flight `streaming` buffer keyed by `turn_id`, appends posts on `pc_post_appended` / `post_appended`, refreshes on scene/scene-file events, and preserves a sticky `advance_disabled` reason across refreshes so the button's tooltip survives a reload.

Multi-PC flow: `InputArea` shows the Advance button when `scene.present_pc_refs.length >= 2`; submitting only appends a post and the backend chooses not to auto-respond. The Advance button calls `POST /api/campaigns/{id}/turns/advance` and the narrator runs against all queued PC posts.

Submission flow:
- `submit(text)` → `campaignApi.submitTurn(campaignId, activePcRef, text)`
- `advance()` → `campaignApi.advance(campaignId, scene.id)`
- `undo()` → `campaignApi.undo(campaignId, 1)` then refresh
- `endScene()` → `campaignApi.endScene(...)` then refresh

Rerolling a response is no longer a turn-level command: the reroll consolidation (#512) removed the global `regenerate()` action in favour of the per-post swipe/alternate (`campaignApi.regeneratePost`), triggered from each post.
- `setActivePC(ref)` — optimistic local update, swallows server failure (the next submit still records the chosen ref server-side)

### Source badges

`SourceBadge.tsx` renders `📚 library` / `🌿 emergent` / `✏️ override` chips with tooltips. The full clickable source-chain UI lives in `CastView` (see below); the side panel uses static badges for the present-cast list.

## Per-campaign views

- `CastView` (`routes/campaign/CastView.tsx`) — character grid with filters (source: all/library/emergent/override; role; tag substring) and a detail pane showing resolved card, `ChainBadge`-rendered source chain, voice anchor with sample dialogue carousel, and capabilities. "Edit override / Edit library / Promote to library" buttons are placeholders (`disabled` with explanatory tooltip).
- `WorldView` (`routes/campaign/WorldView.tsx`) — tabbed Items / Locations / Lore / Factions / Greetings. Locations get a parent-id hierarchy view; items surface `holder`/`current_holder` from extras; lore is grouped by `keywords` / `tags`; greetings span all composed worlds.
- `TimelineView` (`routes/campaign/TimelineView.tsx`) — scenes as ordinal-sorted cards with search + mood filter + status filter (open/closed). Thread summary section pairs `threads_introduced` to `threads_paid_off`. Selecting a scene reveals key beats, present cast, and the two thread lists. Color coding is class-based (`mood-${slug}`) for CSS to style.
- `MechanicsView` (`routes/campaign/MechanicsView.tsx`) — surfaces active module info, lists characters with sheet-present / -missing badges (status discovered by fetching each sheet and treating 404 as missing), shows the selected character's sheet as raw JSON. Roll log / combat tracker / content browser are explicit placeholders. Bulk-create-missing-sheets button is `disabled` (placeholder). `mechanics: null` campaigns get a "no mechanics selected" message.
- `CompositionView` (`routes/campaign/CompositionView.tsx`) — editor for world refs (priority via ▲/▼ buttons; per-ref include kinds with "include all" shortcut; `track_latest` toggle; remove), upgrade-available banner (shown when bound `version < catalog.version` and `track_latest=false`) with per-ref one-click upgrade. Mechanics / style guide / image preset are free-text inputs. Save PUTs the whole composition; Discard reverts to initial.
- `ImagesView` (`routes/campaign/ImagesView.tsx`) — tabs: Gallery (starred filter + "Generate from current scene" button), Queue (placeholder; events not yet routed), Templates (per-character base/negative prompt + canonical seed; "Test prompt" calls `generateImage`, "Save to card" is disabled).

## Campaign creation wizard

`CampaignCreate` (`routes/CampaignCreate/CampaignCreate.tsx`) is a 6-step wizard:

1. **Identity** — id (auto-derived from name unless edited), name, description, tags
2. **Composition** — world refs with priority + include + `track_latest` per ref
3. **Mechanics** — pick one installed module or "No mechanics (narrative only)"
4. **PCs** — choose characters from the composed cast (or define one), each gets `owner: local`
5. **Style & content** — library style guide picker (default mode) or inline style-guide text; image preset picker; free-text content boundaries
6. **Starting scene** — pick a greeting (or skip); the greeting drives the first scene seed

Submit creates the campaign, adds each PC sequentially (failures logged, don't unwind), seeds the first scene from the greeting when picked, appends the new campaign to the in-memory store, and navigates to the play view. PC and seed failures are non-fatal (the user can fix in the per-campaign view).

Each lazy fetch (style guides, image presets, cast, greetings) is gated by step and uses a `ref` flag or a cancel flag to avoid refetch loops on empty/error.

## Per-campaign settings

`CampaignSettings` (`routes/CampaignSettings.tsx`) — six tabs: General, Model routing, ImageGen, Mechanics, Storage, Advanced. Only **General** (name/description via `PATCH /campaigns/{id}`) and **Mechanics** (active module via `PATCH /campaigns/{id}`) persist today. Routing/ImageGen/Storage/Advanced surface the form structure with explanatory "not yet persisted" notes so the surface area is visible without losing data silently.

## App-level settings

`AppSettings` (`routes/AppSettings.tsx`) — seven tabs: Library, Providers, Prompts, Mechanics, Plugins, Backup, Appearance.

- **Library** — path display (read-only; comes from `app.yaml`)
- **Providers** — featured "Language model" card with selector + status badge + saved-config summary + inline model picker for plugins whose schema has an `x-source: "models"` field; secondary cards for embeddings, image generation, export adapters. A "Run setup wizard" button dispatches the `grimoire:open-startup-wizard` event.
- **Prompts** — full prompt-template editor (read bundled, save creates user variant, set active, delete user variant, create new variant from current body)
- **Mechanics / Plugins** — inventory + rescan
- **Backup** — UI surface (not persisted)
- **Appearance** — theme radio (light/dark/system) + font family + density (latter two not yet wired)

## Sheet widget library

`frontend/src/sheets/` ships the rendering layer described in spec 14 §Sheet widget library:

- `SheetRenderer` (`sheets/SheetRenderer.tsx`) takes `moduleId`, `schema`, `value`, `onChange`, optional `themeCss`, and `readOnly`; wraps children in `<div class="sheet mechanics-{moduleId}">`; injects scoped theme CSS via a `<style data-mechanics-scope="...">` tag with `useEffect` cleanup.
- `renderField` (`sheets/renderField.tsx`) dispatches each property to the right widget.
- All 14 widgets from spec 14 ship in `sheets/widgets/`: `Text`, `Textarea`, `NumberInput`, `Select`, `MultiSelect`, `BooleanInput`, `DotRating`, `DicePool`, `HealthTrack`, `PowerList`, `GridRating`, `SlotList`, `KeywordList`, `NestedSection`.
- `GenericFallbackWidget` renders as a JSON textarea **and** surfaces a `role="alert"` warning when the schema names an unknown widget — the forward-compat fallback from spec 14.
- `scopeCss` (`sheets/scopeCss.ts`) is a hand-rolled selector-prefixer for runtime-loaded theme CSS: it strips comments, walks rule blocks (recursing into `@media` / `@supports` / `@container` / `@layer`), prefixes every comma-separated selector with `.mechanics-{moduleId}`, rewrites `:root` / `html` / `body` to target the scope, and leaves selectors already starting with the scope class alone. `@keyframes` / `@font-face` / `@import` are left untouched.

## State management

`StoreProvider` (`state/store.tsx`) exposes:

- `optimisticMutate(action, commit)` — dispatch immediately, snapshot state, roll back to snapshot if `commit` rejects
- `pessimisticMutate(commit, onSuccess)` — await server response then dispatch

The reducer (`state/storeContext.ts`) covers campaigns list, active campaign id, review queue, drift alerts, token-budget / model-label / queue-depth status, and a `replace` action for rollback. Per-play state is held in the `PlayView`'s `usePlayState` reducer; per-route data uses `useResource` / `useApi`.

Reconnect rehydrates by refreshing the active scene + posts on `scene_started` / `scene_ended` / `scene_file_changed` events.

## Accessibility

- `SkipLink` to `#main-content` injected by `AppShell`
- `aria-label`, `aria-live`, `aria-expanded`, `role="alert"` used throughout
- Focus management: `InputArea` re-focuses the textarea when `busy` clears
- Keyboard shortcuts: see `hooks/useKeyboardShortcuts.ts` for the binding parser
- Dark / light / system themes with `prefers-color-scheme` listener and explicit toggle
- `VisuallyHidden`, `LiveRegion` primitives in `components/a11y.tsx`

## Error handling

- `api/client.ts` raises `ApiError(status, detail)` and rejects non-JSON 2xx
- `useResource` / `useApi` surface `error`, `loading`, `reload()` to the view layer
- `AsyncBoundary` (`routes/library/AsyncBoundary.tsx`) renders loading / error / empty / retry for library lists
- The play loop's `runAction` wrapper catches and pins an action error banner with dismiss
- WS reconnect is automatic with backoff + jitter; status reflects in the StatusBar
