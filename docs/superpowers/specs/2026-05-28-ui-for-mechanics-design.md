# UI for Creating Mechanics — Design (issue #443)

**Date:** 2026-05-28
**Issue:** #443 "UI for Mechanics"
**Status:** Design approved; ready for implementation planning.
**Module:** `backend/src/grimoire/mechanics/`, `frontend/src/routes/library/`

## Summary

Grimoire mechanics modules are Python packages dropped into `data/mechanics/<id>/`.
Each ships a `manifest.yaml`, a `mechanics.py` implementing the `MechanicsModule`
protocol (~14 methods including `resolve_roll`, `evaluate_pre_roll`,
`capabilities_of`), optional `sheets/<kind>.json` and `content/<kind>.json` JSON
Schemas, and an optional `theme.css`. Today the frontend exposes only a
**read-only** library view (`MechanicsView.tsx`) and all backend mechanics routes
are read-only — there is no path to author a module from the app.

This feature adds a UI that **scaffolds** a new module and **edits the
declarative parts** of modules (new or existing): manifest metadata, sheet
schemas, content schemas, and theme CSS. The behavioral Python logic remains
hand-edited on disk; the UI generates a `mechanics.py` stub once at scaffold
time and never re-touches it.

## Scope decisions

| Decision | Choice |
|----------|--------|
| Authoring level | **Scaffold + declarative editor.** UI authors the declarative files and generates a Python stub; behavioral logic stays hand-edited on disk. |
| Surfaces | **All four:** manifest metadata, sheet schema builder, content schema builder, theme CSS editor. |
| Create vs edit | **Create new AND edit existing** modules' declarative parts. |
| Schema builder fidelity | **Visual builder for all 14 widgets**, plus a raw-JSON escape hatch per-property and for the whole schema. Live `SheetRenderer` preview throughout. |

### Explicitly out of scope (v1)

- Editing `mechanics.py` (behavioral Python) through the UI. Generated once; then hand-edited on disk.
- Deleting modules from the UI.
- The deferred mechanics items (sandboxing, sheet migration, custom JS bundles, etc.) from `2026-05-18-mechanics-COMPLETED.md` §10–§17.
- Authoring `hud_widgets` config beyond what the raw-JSON manifest editor allows (no dedicated visual builder for HUD widget definitions in v1).

## Architecture

### 1. Backend — authoring write path

The Mechanics module owns all writes into `data/mechanics/`. Writes are funneled
through the Mechanics service so the module-ownership rule holds.

New file `backend/src/grimoire/mechanics/authoring.py` defines a
`MechanicsAuthor` that collaborates with `MechanicsService` (which exposes
`config.root` — the `data/mechanics/` directory — and `async rescan()`).

`MechanicsAuthor` methods (all async; all call `rescan()` after writing and
return the resulting `RescanReport` so callers can surface load errors):

- `scaffold(manifest_spec: dict) -> ScaffoldResult`
  1. Validate `manifest_spec` against `MECHANICS_MANIFEST_SCHEMA`
     (`validation/manifests.py`).
  2. Reject (caller maps to 409) if `<root>/<id>` already exists.
  3. Create `<root>/<id>/`, write `manifest.yaml` (YAML dump, stable key order).
  4. Generate `mechanics.py` (see *Generated stub* below).
  5. For each declared `sheet_kind`, write a placeholder
     `sheets/<kind>.json` (a minimal valid object schema:
     `{"type": "object", "title": "<Kind>", "properties": {}}`).
  6. For each declared `content_kind`, write a placeholder
     `content/<kind>.json` similarly.
  7. If `manifest_spec.ui.theme_css` names a file, create an empty file at that
     path.
  8. `rescan()`; return `ScaffoldResult(module_id, created_paths, report)`.

- `write_manifest(module_id, manifest_spec)` — validate against the manifest
  schema; require the module dir to exist (else 404); write `manifest.yaml`;
  leave every other file (including `mechanics.py`) untouched; `rescan()`.

- `write_sheet_schema(module_id, kind, schema)` — validate `schema` with
  `jsonschema`'s `check_schema`; write `sheets/<kind>.json`; `rescan()`.

- `write_content_schema(module_id, kind, schema)` — validate with `check_schema`;
  write `content/<kind>.json`; `rescan()`.

- `write_theme_css(module_id, css)` — write the theme CSS file (path resolved
  from `manifest.ui.theme_css`, defaulting to `theme.css`); `rescan()`.

**Guards (every method):**

- `module_id` and `kind` must match `^[a-z0-9][a-z0-9_-]*$` (ids) /
  `^[a-z0-9][a-z0-9_-]*$` (kinds); reject anything else.
- Resolve the target path and assert it stays inside `<root>/<id>/` — reject
  path traversal (`..`, absolute paths, symlink escape).
- Manifest `id` must equal the directory name / `module_id`.

**Errors** are raised as typed exceptions the router maps to HTTP:
`ModuleExistsError` → 409, `ModuleNotFoundError` → 404,
`ManifestValidationError` / `SchemaValidationError` (carry a list of messages) →
422.

#### Generated `mechanics.py` stub

The stub subclasses `DiskBackedMechanicsModule` (`mechanics/base.py`), which
already supplies `sheet_schema`, `list_content_kinds`, and `content_schema` by
reading the on-disk JSON files. The stub adds **safe-default** bodies for the
remaining protocol methods so a freshly scaffolded module **loads green**
immediately and never breaks `rescan()`:

```python
from grimoire.mechanics.base import DiskBackedMechanicsModule
from grimoire.types.common import ValidationResult


class Mechanics(DiskBackedMechanicsModule):
    id = "<id>"
    name = "<name>"
    version = "<version>"
    api_version = "<api_version>"

    # --- Behavioral logic: implement these on disk. ---

    def validate_sheet(self, entity_kind, sheet):
        return ValidationResult(valid=True)

    def initialize_sheet(self, entity_kind, entity_id):
        # TODO: return a starting sheet for this entity kind.
        return {}

    def capabilities_of(self, entity_ref, sheet):
        # TODO: derive capabilities from the sheet.
        return []

    def power_definitions(self):
        return []

    def power_definition(self, power_id):
        return None

    def evaluate_pre_roll(self, player_input, scene):
        # TODO: propose rolls based on player input.
        return []

    def resolve_roll(self, roll, rng_seed):
        # TODO: resolve the roll deterministically from rng_seed.
        return {"roll_id": roll.id, "outcome": "", "narration_hint": ""}

    def validate_narrated_event(self, event, scene):
        return ValidationResult(valid=True)

    def character_creation_steps(self):
        return []

    def time_tick(self, entity_ref, sheet, duration, context):
        return []

    def system_summary(self):
        return "<description or name>"
```

The exact method set is pinned to the `MechanicsModule` protocol at
implementation time (`types/protocols.py`); the loader validates the entry
satisfies the protocol, so the generator and the protocol must stay in lockstep.
A test asserts the generated stub loads without error.

### 2. Backend — REST routes

Added to `backend/src/grimoire/api/library.py` alongside the existing read-only
mechanics routes. GET routes for sheet schema, content schema, and theme CSS
already exist and are reused for "load current value into the editor".

| Method & path | Purpose | Errors |
|---------------|---------|--------|
| `POST /api/library/mechanics` | Scaffold a new module. Body: manifest spec (+ optional initial schemas). | 409 duplicate id, 422 invalid manifest |
| `PUT /api/library/mechanics/{id}/manifest` | Replace `manifest.yaml`. | 404 missing, 422 invalid |
| `PUT /api/library/mechanics/{id}/sheets/{kind}` | Write `sheets/<kind>.json`. | 404, 422 |
| `PUT /api/library/mechanics/{id}/content/{kind}` | Write `content/<kind>.json`. | 404, 422 |
| `PUT /api/library/mechanics/{id}/theme.css` | Write the theme CSS file. | 404 |

Every write response includes the post-write `RescanReport` (specifically this
module's load errors and warnings) so the UI reports load status inline.

Request/response bodies are Pydantic models. The manifest body mirrors
`MECHANICS_MANIFEST_SCHEMA`; schema bodies are arbitrary JSON objects validated
server-side with `check_schema`.

### 3. Frontend

API client additions in `frontend/src/api/library/mechanics.ts`:
`createModule`, `updateManifest`, `putSheetSchema`, `putContentSchema`,
`putThemeCss`. Zod schemas validate responses (including the rescan report).

UI changes in `frontend/src/routes/library/`:

- **`MechanicsList`** gains a **"New module"** button opening a short create
  form (id, name, version, api_version, initial sheet/content kinds). On submit
  it calls `createModule`, then navigates to the editor for the new id.

- **`ModuleDetailCard` becomes a tabbed editor** (`MechanicsEditor`):
  - **Manifest** — a form covering all manifest fields (id read-only after
    creation; name, version, author, homepage, description, sheet_kinds,
    content_kinds, capabilities, expression_vocabulary_extensions,
    `ui.theme_css`). Adding/removing a `sheet_kind`/`content_kind` is reflected
    in the Sheets/Content tabs (placeholder schema created on save).
  - **Sheets** — one `SchemaBuilder` per declared `sheet_kind`.
  - **Content** — one `SchemaBuilder` per declared `content_kind`.
  - **Theme** — a CSS text editor plus the existing scoped live preview
    (`scopeCss` under `.mechanics-<id>`).

- **`SchemaBuilder`** (new, shared by Sheets and Content):
  - An ordered list of fields. Each field has a key, a widget type (one of the
    14 `WidgetName`s), and `required` flag.
  - **Add field** → choose a widget → a per-widget config sub-form. All 14
    widgets get a config form (text/textarea/number/boolean/select/
    multi-select/dot-rating/dice-pool/health-track/power-list/grid-rating/
    slot-list/keyword-list/nested-section), matching the `SchemaProperty`
    fields in `frontend/src/sheets/types.ts`.
  - **Raw-JSON escape hatch**: a per-property toggle to edit that property's raw
    JSON, and a whole-schema toggle to edit the entire `SheetSchema` JSON.
    Switching between visual and raw keeps the underlying JSON as source of
    truth.
  - **Live preview**: a `SheetRenderer` pane renders the schema being built
    against sample/empty data, reusing the existing widget library.
  - On save, emits a `SheetSchema` (`{type, title, properties, required}`) to
    the corresponding PUT route.

- The editor displays the generated `mechanics.py` path with a note that
  behavioral logic is hand-edited on disk, and surfaces this module's rescan
  load errors / warnings returned by every write.

### 4. Module-boundary note

Writing into `data/mechanics/` is a deliberate dev-time **authoring** action,
distinct from the "mechanics modules are read-only at *runtime*" rule. All writes
go through the Mechanics service, preserving ownership. CLAUDE.md's "Don't write
directly to SQLite / Don't put mechanics logic in core" guidance is unaffected
(no game-system logic enters core; we only generate a stub and persist
author-provided declarative files). Add a one-line note to CLAUDE.md clarifying
that mechanics authoring is the sanctioned write path into `data/mechanics/`.

## Data flow

```
Author (browser)
  │  POST /api/library/mechanics            (create)
  │  PUT  .../{id}/manifest|sheets|content|theme.css   (edit)
  ▼
api/library.py route ──► MechanicsAuthor (mechanics/authoring.py)
                              │  validate (manifest schema / check_schema)
                              │  write file(s) under data/mechanics/<id>/
                              │  generate mechanics.py (scaffold only)
                              ▼
                         MechanicsService.rescan()
                              │
                              ▼
                         RescanReport ──► route response ──► editor shows load status
```

## Error handling

- **Validation failures** (manifest or JSON Schema) → 422 with a list of
  messages; the editor shows them inline against the offending field/section and
  does not navigate away.
- **Duplicate id on create** → 409; the create form shows "a module with this id
  already exists".
- **Missing module on edit** → 404.
- **Path traversal / bad id or kind** → 400.
- **Module written but fails to load** (e.g., author later edits a broken
  `mechanics.py`) → the write still succeeds (file is persisted); the rescan
  report's load errors are surfaced in the editor so the author can fix it.

## Testing

### Backend

- **Unit (`tests/mechanics/test_authoring.py`):**
  - `scaffold` creates the expected files; the generated `mechanics.py` loads
    green (module appears in `rescan().loaded`, not `failed`).
  - `scaffold` refuses an existing id (`ModuleExistsError`).
  - Placeholder sheet/content schemas are written per declared kind and are
    valid JSON Schemas.
  - `write_manifest` / `write_sheet_schema` / `write_content_schema` /
    `write_theme_css` validate input, persist exactly one file, and leave
    `mechanics.py` untouched.
  - Path-traversal and bad-id/kind inputs are rejected.
  - Invalid manifest / invalid JSON Schema raise the typed validation errors.
- **Scenario (`tests/` HTTP, `-m scenario`):** POST create → module appears in
  `GET /api/library/mechanics/installed`; PUT a sheet schema → matching GET
  returns it; PUT an invalid schema → 422.

### Frontend

- **Component (Vitest + RTL):**
  - `SchemaBuilder` emits the correct JSON for each widget's config form.
  - Per-property and whole-schema raw-JSON toggles round-trip without data loss.
  - Manifest form round-trips all fields.
  - The "New module" create flow calls `createModule` and routes to the editor.
- **Live preview** renders the built schema through `SheetRenderer` without
  throwing on partial/empty schemas.

## Documentation

- Update CLAUDE.md: note mechanics authoring as the sanctioned write path into
  `data/mechanics/`, and that `mechanics.py` is generated once then hand-edited.
- Update the README feature list if it enumerates mechanics capabilities.
- The in-app `MechanicsRequirements` help panel stays accurate (it already
  documents the directory layout and required fields).

## Open questions / follow-ups (not blocking v1)

- Whether to offer a visual builder for `hud_widgets` (currently raw-JSON via the
  manifest editor).
- Whether editing a module that is the *active* mechanics of a running campaign
  should warn before saving (live sheet schemas could change underfoot). v1
  surfaces the rescan result but does not block.
