# Mechanics — Remaining Work (COMPLETED 2026-05-18)

> Everything from the original `specs/06-mechanics.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-mechanics-design.md`).

**Status:** §1–§7 and §9 implemented on `claude/implement-mechanics-design-Hpv4g` (PR #374). §8 (API-version negotiation) and §10–§17 (sandbox, sheet versioning, localisation, network policy, cross-module communication, server-side multiplayer rolls, hot-swap, custom JS bundles) remain deferred or rejected per the spec.

**Companion (already shipped):** `2026-05-12-mechanics-design.md`
**Module:** `backend/src/grimoire/mechanics/`

## 1. Sheet schemas loaded from disk

Spec 06 §The mechanics module API: each module ships `sheets/<entity_kind>.json` per declared kind. Today the loader reads `manifest.yaml` and imports `mechanics.py` but never opens the `sheets/` directory. `MechanicsModule.sheet_schema(entity_kind)` is delegated straight to the module instance, leaving every author to wire their own file loading.

Likely shape: a default base class (or a loader convenience) that, given the discovered module's directory, loads `sheets/<kind>.json` lazily and caches it. Validate each schema with `check_schema` at load time so a broken sheet schema becomes a load error, not a runtime surprise during sheet rendering. Modules can still override `sheet_schema` to compute schemas dynamically.

Decide where the canonical reference to `module_dir` lives — `RegisteredModule` currently holds only the manifest and the instance; one option is to extend it with the source path and pass that into the entry's constructor when the loader instantiates it.

## 2. Content schemas + content browsers

Spec 06 §The mechanics module API and §Responsibilities ("Provide content browsers (spells, items, vis sources, etc.) per system"): modules declare `content_kinds: [...]` in their manifest and ship `content/<kind>.json` JSON Schemas. The protocol exposes `list_content_kinds()` and `content_schema(kind)`.

Status:
- Manifest validation accepts `content_kinds` (`validation/manifests.py:95`).
- The protocol and `NullMechanicsModule` define `list_content_kinds` / `content_schema`.
- The façade (`MechanicsService`) does not expose either method, the loader does not read the `content/` directory, no HTTP route surfaces them, and the Frontend has no content-browser view.

Needed:
- Façade pass-throughs (`async def list_content_kinds(campaign_id)` and `async def content_schema(campaign_id, kind)`) plus matching `Mechanics` protocol entries in `types/protocols.py`.
- A storage answer: where do content instances (a specific spell, a specific vis source) live? Likely under `data/campaigns/<id>/content/<kind>/<id>.<mechanics-id>.yaml` mirroring the sheet layout, but confirm with the World/State Store work before committing.
- REST routes under `/api/campaigns/{id}/content/{kind}` for list/get/put.
- A Frontend content-browser view per `content_kinds`.

## 3. Theme CSS loaded and scoped

Spec 06 §Sheet UI rendering > Theme CSS and §The mechanics module API: a module can ship `theme.css` and declare it in `manifest.ui.theme_css`. The Frontend's `SheetRenderer` already supports a `themeCss` prop and `scopeCss` to wrap rules under `.mechanics-<moduleId>` (`frontend/src/sheets/SheetRenderer.tsx`, `scopeCss.ts`). What's missing is the backend side: the loader never reads `theme.css` and no API route serves it.

Needed:
- On load, if `manifest.ui.theme_css` is set, read the file relative to `module_dir` and expose it via the registered module (likely on `RegisteredModule.theme_css: str | None`).
- A REST route (`GET /api/library/mechanics/{id}/theme.css` or include the CSS inline in the registered-module payload) so the Frontend can pass it to `SheetRenderer`.
- Surface a load warning if the manifest declares `theme_css` but the file is missing.

## 4. Character creation flow

Spec 06 §Character creation: modules expose `character_creation_steps() -> list[CreationStep]`, each step is a JSON Schema form. The Frontend walks the steps; the final result is a complete sheet written under `data/campaigns/<id>/sheets/characters/<character-id>.<mechanics-id>.yaml`. Library-baseline sheets created before any campaign exists also flow through this.

Status: the protocol method exists and `CreationStep` is defined in `types/mechanics.py:110`, but nothing else is wired — no façade method, no orchestrator/world hook, no REST route, no Frontend wizard.

Needed:
- `MechanicsService.character_creation_steps(campaign_id)` (or per-module-id for library-baseline use) returning `list[CreationStep]`.
- A `MechanicsService.finalize_character_creation(campaign_id, character_ref, step_outputs)` that composes the step outputs into a starting sheet, validates it, and writes it through `StateStore.write_sheet` with `source="mechanics:<id>"`.
- REST routes under `/api/campaigns/{id}/characters/{character_id}/creation` (GET steps, POST submit).
- Frontend wizard built on `SheetRenderer`/`renderField`.

## 5. Pre-roll confirmation round-trip

Spec 06 §Pre-roll evaluation: "The Frontend can show proposed rolls before submission and let the player accept/modify/decline. Confirmed rolls are resolved; results are included in the prompt; the LLM narrates around them."

The Orchestrator already short-circuits when `OrchestratorConfig.pre_roll.confirm_before_executing == "always"` and returns proposals without resolving them (`orchestrator/service.py:560`). What's missing is the rest of the loop: today the proposals are silently dropped from the turn (the function returns `[]`), the turn proceeds without rolls, and there is no UI for review or a path back into the turn after confirmation.

Needed:
- Emit a `pre_roll_pending` event with the list of `ProposedRoll`s and either pause the turn or terminate it pending a follow-up `submit_post(..., resolved_rolls=[...])`.
- A REST + WebSocket protocol entry for "resolve these proposals" (accept / modify / decline per proposal).
- Frontend UI to render proposals before the LLM call.
- Decide whether `confirm_before_executing` is per-call (e.g., `"high_stakes"` mode that only confirms `ProposedRoll.high_stakes=True`); the config enum is currently free-form.

Note: pairs naturally with the Orchestrator's `cancel_turn` / interactive scene-break work (see Orchestrator remaining §2, §5).

## 6. Mid-campaign mechanics switching

Spec 06 §Switching modules mid-campaign: switching is supported but flagged — old sheets stay on disk but become inactive, the Frontend prompts for bulk creation of new ones, capabilities/content/rolls follow the new module. Switching back is a no-op for the preserved old sheets.

Status: `PATCH /api/campaigns/{id}` already accepts a new `mechanics` value and writes it to `campaigns.mechanics_module` (`api/campaigns.py:228`). Nothing else is implemented — no event, no inactive-sheet flag, no Frontend bulk-creation prompt, no warning surface.

Needed:
- A dedicated `POST /api/campaigns/{id}/mechanics/switch` route (or extend PATCH) that:
  - Records the previous `mechanics_module` so it can be detected as "inactive but preserved".
  - Emits a `mechanics_switched` event.
  - Returns a list of entities that have an old-module sheet and lack a new-module one so the UI can drive a bulk-creation wizard.
- A Frontend prompt that walks the new module's character-creation flow (§4) for each missing sheet.
- Treat `mechanics: null` as a valid target ("drop mechanics and finish in narrative mode").

## 7. v1 file-watcher reload (`reload_on_file_change`)

`MechanicsConfig.reload_on_file_change: bool = False` is defined (`mechanics/config.py:33`) but unenforced. Spec 06 §Discovery and loading: "Reloading: a file watcher on `data/mechanics/` can trigger module reload during development. Production: restart to pick up changes."

Needed: when the config flag is on, register a watcher on `config.root` (likely via the existing `watcher/` infrastructure) that calls `MechanicsService.rescan()` on relevant file events. Debounce so an editor save doesn't trigger five reloads.

## 8. Per-module API-version negotiation surface

Spec 06 §Responsibilities lists "Validate that a mechanics module's manifest matches its declared API version". Today the manifest schema only accepts `api_version: "1"` and the loader rejects anything else. There is no compatibility matrix, no deprecation warning path, and `ApiVersion` only has `V1`.

Needed when a v2 API ships: extend `validation/manifests.py:MECHANICS_API_VERSIONS` and `types/mechanics.py:ApiVersion`; define which v1 methods remain callable on v2 modules; surface a "module targets api_version=1; some features (e.g., custom JS UI) unavailable" warning in `RescanReport`. Out of scope until v2 actually exists.

## 9. `power_definitions` exposure

Spec 06 §Powers and capabilities: "`power_definitions()` returns the *vocabulary* — every power the system defines, at every rating, with description, cost, and effect. The Context Builder can reference these for archive-tier inclusion..." The protocol method exists (`types/protocols.py:338`) and `NullMechanicsModule` returns `[]`, but `MechanicsService` doesn't expose `power_definitions` / `power_definition`, no REST route serves them, and the Context Builder doesn't consult them.

Needed:
- `MechanicsService.power_definitions(campaign_id)` and `power_definition(campaign_id, power_id)` pass-throughs.
- A Context Builder integration that pulls the definitions for any power named in a character's capabilities and includes them at the archive tier.
- Optional `GET /api/library/mechanics/{id}/powers` for a Frontend reference browser.

## 10. Custom JS bundle escape hatch (v2; deferred)

Spec 06 §v2 escape hatch: custom JS bundles: a module can ship pre-built bundles in `ui/` for sheets the widget library can't express. Manifest `ui.custom_components` is already accepted by the schema (`validation/manifests.py:109`). The Frontend doesn't dynamic-import anything yet. Explicitly v2 per the spec; record so it isn't re-litigated.

## 11. Sandbox / safety for module code (v2; deferred)

Spec 06 §Open questions: "Mechanics modules run unrestricted Python. v2 might sandbox via subprocess or WASM if untrusted modules become a concern." Out of scope for v1.

## 12. Sheet versioning / migration across module updates (v2; deferred)

Spec 06 §Open questions: if a module bumps a minor version and adds a field, do existing sheets auto-migrate? The spec defers to the module's responsibility. No code; record so it doesn't become a surprise.

## 13. Localisation of schema labels / capability descriptions (v2; deferred)

Spec 06 §Open questions. Out of scope for v1.

## 14. Network requests from modules (informational; not a code item)

Spec 06 §Open questions: "Allowed but discouraged; mechanics should generally be self-contained." Nothing to build; just don't add a policy enforcing the prohibition unless evidence accumulates.

## 15. Cross-module communication (rejected for v1)

Spec 06 §What the API does not (yet) allow: "Cross-module communication (one mechanics referencing another)". Treat as **rejected** unless a concrete use case appears.

## 16. Server-side roll requests over the network (rejected; v2-multiplayer)

Spec 06 §What the API does not (yet) allow: "Server-side roll requests over network (single-user local; v2 multiplayer would add this)". Out of scope until multiplayer is on the table.

## 17. Plugin-style hot-swap during a session (rejected)

Spec 06 §What the API does not (yet) allow. Treat as **rejected**; the rescan + restart story is the supported answer.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. **§1 (disk-loaded sheet schemas) + §3 (theme CSS)** — both are "loader reads the rest of the module directory and exposes it". Cheapest win; unblocks every system that uses the shipped widget library without a custom `mechanics.py` factory.
2. **§9 (power_definitions exposure) + §2 (content schemas)** — purely additive façade methods + a REST surface. §9 first because Context Builder integration is small and immediately useful; §2 is larger because it implies a content storage layout.
3. **§4 (character creation) + §5 (pre-roll confirmation)** — both require a multi-step Frontend round-trip. Share a planning pass to settle on the protocol shape (turn pause vs. separate submit path) before either is coded.
4. **§6 (mid-campaign switch flow)** — depends on §4 to drive bulk creation.
5. **§7 (file-watcher reload)** — small dev-ergonomics item; do last unless a contributor is actively iterating on a third-party module.
6. **§8 (API-version negotiation)** — defer until v2 is concrete.
