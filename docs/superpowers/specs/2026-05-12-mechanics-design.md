# Mechanics — Design (Shipped)

> Captures the Mechanics module as actually built. The matching "remaining" spec at `2026-05-16-mechanics-remaining-design.md` covers everything from the original `specs/06-mechanics.md` that did **not** land in this work.

**Commit:** `491da88` — "Build Mechanics API surface and module loader (task 16)" (followed by `ff349e2` fixing the non-deterministic RNG fallback and `87e0643` renaming setting→world).
**Module:** `backend/src/grimoire/mechanics/`
**Tests:** `backend/tests/mechanics/{test_discovery,test_loader,test_rng,test_service}.py`
**Conformance suite:** `backend/src/grimoire/testing/conformance/mechanics.py`

## Purpose

Mechanics is the game-system layer. No mechanics module ships with Grimoire — this package is the API contract, the on-disk discovery and loader, and the per-campaign façade that the Orchestrator, Extractor, Time Engine, and Context Builder call into. Campaigns with `mechanics: null` (or an unloaded module reference) transparently fall through a `NullMechanicsModule` so callers never need to special-case the absence of mechanics.

## Module surface

The package re-exports a small surface (`mechanics/__init__.py`):

- **Service / façade:** `MechanicsService`, `ActiveModuleResolver`, `RescanReport`
- **Discovery:** `discover`, `DiscoveredModule`, `DiscoveryError`
- **Loader:** `load_module`, `LoadResult`, `satisfies_mechanics_protocol`, `DEFAULT_ENTRY_CANDIDATES`
- **Registry:** `MechanicsRegistry`, `RegisteredModule`
- **Null module:** `NullMechanicsModule`, `NULL_MECHANICS_ID = "null"`
- **RNG:** `derive_roll_seed`
- **Config:** `MechanicsConfig`, `ValidationConfig`, `RngConfig`, `DefaultsConfig`

The protocol the modules themselves implement (`MechanicsModule`) lives in `backend/src/grimoire/types/protocols.py:318`. The façade the app calls into (`Mechanics`) is at `protocols.py:364`. Typed payloads (`Capability`, `Roll`, `RollResult`, `ProposedRoll`, `NarratedEvent`, `CreationStep`, `TickContext`, `ModuleManifest`, `PowerDefinition`, `ResourceCost`, `RollModifier`, `MechanicsResult`, `ApiVersion`) live in `backend/src/grimoire/types/mechanics.py`.

## On-disk layout discovered

`MechanicsService.rescan()` walks `config.root` (default `<data_root>/mechanics/`) one level deep. A directory becomes a candidate when it contains `manifest.yaml`:

```
data/mechanics/<id>/
├── manifest.yaml      # required
├── mechanics.py       # required entry module
```

Subdirectories starting with `.` are skipped. Roots that don't exist are silently skipped. `sheets/`, `content/`, `theme.css`, and `ui/` directories are reserved by the spec but the loader does not look at them yet (see remaining doc §1, §2, §7).

### Manifest shape

`validation/manifests.py` validates the parsed YAML against `MECHANICS_MANIFEST_SCHEMA` (JSON Schema). Required fields: `id` (pattern `^[a-z0-9][a-z0-9_-]*$`), `name`, `version` (semver), `api_version` (currently only `"1"`). Optional: `author`, `homepage`, `description`, `sheet_kinds`, `content_kinds`, `capabilities`, `ui` (with `theme_css` and `custom_components` sub-fields). `additionalProperties: true` so modules can stash extras.

`ApiVersion.V1 = "1"` is the only accepted version. Bumping requires extending both `validation/manifests.py:MECHANICS_API_VERSIONS` and `types/mechanics.py:ApiVersion`.

## Public API

### `MechanicsService` (façade)

Constructed once at startup in `api/container.py` with the shared `MechanicsConfig` and `StateStore`. The service owns the in-memory `MechanicsRegistry` and tracks the most recent discovery/load failures so the Library UI can render them.

```python
class MechanicsService:
    # Discovery
    async def rescan() -> RescanReport
    def discovery_errors() -> list[DiscoveryError]
    def failed_modules() -> dict[str, list[str]]

    # Manual registration (tests / in-process modules)
    def register_module(manifest, instance) -> None
    def get_module(module_id) -> MechanicsModule | None

    # Per-campaign active module
    async def active_module(campaign_id) -> MechanicsModule | None

    # Sheets
    async def sheet_schema(campaign_id, entity_kind) -> JsonSchema | None
    async def get_sheet(campaign_id, entity_ref, entity_kind=None) -> dict | None
    async def update_sheet(campaign_id, entity_ref, patch, *,
                           entity_kind=None, source="user", turn_id=None) -> dict

    # Capabilities / rolls / validation
    async def capabilities_of(campaign_id, entity_ref, entity_kind=None) -> list[Capability]
    async def evaluate_pre_roll(campaign_id, player_input, scene) -> list[ProposedRoll]
    async def resolve_roll(campaign_id, roll, branch_id=None) -> RollResult
    async def validate_narrated_event(campaign_id, event, scene) -> ValidationResult
    async def time_tick(campaign_id, entity_ref, duration, context=None, *,
                        entity_kind=None) -> list[StateDelta]

    # Registry introspection
    async def list_installed_modules() -> list[ModuleManifest]
    async def module_info(module_id) -> ModuleManifest | None
    def installed() -> list[RegisteredModule]
```

### `MechanicsModule` (the protocol a module implements)

Identity attributes (`id`, `name`, `version`, `api_version`) plus the methods enumerated in `loader.py:REQUIRED_METHODS`:

```
sheet_schema, validate_sheet, initialize_sheet,
list_content_kinds, content_schema,
capabilities_of, power_definitions, power_definition,
evaluate_pre_roll, resolve_roll, validate_narrated_event,
character_creation_steps, time_tick, system_summary
```

`MechanicsModule` is a `typing.Protocol` with `...` bodies, so it isn't `runtime_checkable`. The loader spells out the membership check in `satisfies_mechanics_protocol` / `_missing_members`.

## Discovery and load flow (`MechanicsService.rescan`)

1. Snapshot previous ids (registered + previously failed) so removals can be detected.
2. Call `discover(roots=[config.root])`:
   - For each candidate dir, parse `manifest.yaml` via `files/yaml_io.load_yaml`.
   - Top-level must be a mapping; otherwise emit a `DiscoveryError`.
   - Track `seen_ids: dict[str, Path]` and reject duplicates (first occurrence wins).
   - Returns `(list[DiscoveredModule], list[DiscoveryError])`.
3. For each discovery error, push `(dir_name, message)` into the rescan's `failed` list.
4. For each discovered module, call `load_module(d)`:
   - JSON-Schema validate the manifest (`validate_mechanics_manifest`).
   - Cross-check `id == directory_name`.
   - Build `ModuleManifest` from raw dict.
   - Import `mechanics.py` under synthetic name `grimoire_mechanics._loaded.<id>` (hyphens → underscores) using `importlib.util.spec_from_file_location`. Failed imports are caught and the synthetic module is removed from `sys.modules`.
   - Resolve the entry: manifest's `entry_class` if declared, otherwise look for `MECHANICS` (pre-built instance), `Mechanics`, or `MechanicsModule` (classes get `()`-instantiated).
   - Verify protocol membership; verify `instance.id == manifest.id`.
   - Returns `LoadResult` with either a populated `(manifest, instance)` or an `errors` list.
5. On success, register; on failure, unregister any prior entry and record the reasons in `self._failed` so the UI can show them.
6. Compute `removed = previous_ids - seen` and unregister those.
7. Return `RescanReport(discovered, loaded, failed, removed)`.

`failed` mirrors the shape of the Plugin rescan report so the Library UI's existing component can render both.

## Active-module resolution

`active_module(campaign_id)` reads the `mechanics_module` column from the `campaigns` table:

- Missing campaign → `NotFoundError`.
- `None`, empty string, or `"null"` → returns `None` (treated as `mechanics: null`).
- Anything else → looks up the registry; if the module isn't loaded, logs a WARNING and returns `None`.

`_active_or_null` returns the registered instance or the singleton `NullMechanicsModule` so the convenience pass-throughs that need a concrete module (`update_sheet`, `capabilities_of`, `time_tick`) don't have to branch on `None`.

The `NullMechanicsModule` (`null.py`) implements the protocol with empty/no-op behavior: `sheet_schema → None`, `validate_sheet → valid=True`, `capabilities_of → []`, `evaluate_pre_roll → []`, `resolve_roll → RollResult(dice=[], successes=0, outcome="no mechanics")`, `time_tick → []`, etc.

## Sheets

Sheet storage is delegated to `StateStore` (`get_sheet` / `write_sheet` keyed by `(campaign_id, kind, entity_id, mechanics_id)`).

`update_sheet`:
1. Look up the campaign's `mechanics_module`. `None` raises `ValueError("...mechanics: null; sheets are not stored")`.
2. Parse `entity_ref` via `_parse_entity_ref`, which accepts `"<kind>:<id>"`, `"<kind>/<id>"`, `"library:..."`/`"campaign:..."` (delegates to `EntityRef.parse`), or a bare id paired with `entity_kind`. Falls back to `"character"` when nothing is supplied.
3. Read the current sheet; if absent, call `module.initialize_sheet(kind, entity_id)`.
4. `_deep_merge(current, patch)` — nested dicts recurse, lists and scalars in `patch` replace.
5. If `config.validation.strict_sheets` (default `True`): call `module.validate_sheet(kind, merged)`; raise `ValueError` on failure.
6. `state_store.write_sheet(...)`. `source="mechanics"` is rewritten to `"mechanics:<id>"`; other sources pass through.

The HTTP routes (`api/campaigns.py:733-779`) expose `GET /campaigns/{id}/sheets/{kind}/{entity_id}` and `PUT /campaigns/{id}/sheets/{kind}/{entity_id}`. The PUT route deliberately reads the campaign's `mechanics_module` itself to key the write — picking the "first installed module" caused silent read/write desyncs when more than one mechanics plugin was installed.

## Rolls and per-branch RNG

`resolve_roll(campaign_id, roll, branch_id=None)`:
1. If no active module, delegate to `NullMechanicsModule`.
2. Look up the branch seed (`_branch_seed`): read `branches.rng_seed` for `branch_id` (default `"<campaign_id>:main"`). If the branch row is missing, derive a deterministic fallback from `SHA-256(branch_id)[:8]` — `ff349e2` replaced the built-in `hash()` here because Python's `hash()` is per-process randomized and would defeat replay determinism.
3. `derive_roll_seed(branch_seed, roll.seed, roll.id)` mixes the three via SHA-256 and returns a non-negative 63-bit int. Same inputs → same output across processes.
4. Delegate to `module.resolve_roll(roll, derived_seed)`.

Determinism guarantee: replaying a branch with the same seed and the same roll id reproduces the dice exactly. Forking preserves the per-branch seed (the State Store's `fork_branch` is responsible for copying it).

## Pre-roll proposals

`evaluate_pre_roll(campaign_id, player_input, scene)` returns `[]` for null campaigns; otherwise delegates to the active module. The Orchestrator wraps the call in `_do_pre_roll` (`orchestrator/service.py:543-572`):

- `Exception` from `evaluate_pre_roll` or `resolve_roll` is logged at WARNING and produces an empty result list (mechanics is optional context for the LLM).
- If `OrchestratorConfig.pre_roll.confirm_before_executing == "always"`, the orchestrator returns the proposals without resolving them. Otherwise it resolves each via `mechanics.resolve_roll` and packages them as `MechanicsResult(roll, result)` for the Context Builder.

## Narrated-event validation

`validate_narrated_event(campaign_id, event, scene)`:
- Null campaign → `ValidationResult(valid=True)` (trivial accept).
- Otherwise delegate to the module.
- If `config.validation.strict_events` is **false** (the default) and the module returns `valid=False`, the failure is downgraded: errors move into `warnings`, `valid` flips to `True`, and `proposed_deltas` are preserved. The caller still sees the original error text in `warnings`. With `strict_events: true`, the failing `ValidationResult` is returned as-is so the Extractor can route the event to review.

The Extractor's `MechanicsValidator` protocol (`extractor/protocols.py:21`) is the narrow interface it depends on; `extractor/service.py:305` calls it per candidate event and treats any exception as a soft failure that gets flagged but doesn't abort extraction.

## Time-tick

`time_tick(campaign_id, entity_ref, duration, context=None, *, entity_kind=None)`:
1. Null campaign → `[]`.
2. Parse the entity ref; load the sheet via `StateStore.get_sheet` (passing `{}` if absent).
3. Build a default `TickContext(campaign_id, duration=duration)` if none supplied. (Within-campaign branching was removed in #494; `branch_id` is no longer a `TickContext` field.)
4. Delegate to `module.time_tick(entity_ref, sheet, duration, context)`.

The Time Engine (`time_engine/service.py:472, 785`) drives this per present character on each clock advance and feeds the returned deltas back to the State Store.

## Capabilities

`capabilities_of(campaign_id, entity_ref, entity_kind=None)`:
1. Null campaign → `[]`.
2. Load the sheet.
3. Delegate to `module.capabilities_of(entity_ref, sheet or {})`.

`power_definitions()` / `power_definition(id)` from the protocol are not currently surfaced through the façade (no caller wires them yet).

## HTTP surface

`api/library.py`:
- `GET /api/library/mechanics/installed` → `MechanicsService.installed()` (list of `RegisteredModule`).
- `POST /api/library/mechanics/rescan` → `MechanicsService.rescan()` (re-runs discovery + load).

`api/campaigns.py`:
- `GET /api/campaigns/{id}/sheets/{kind}/{entity_id}` → `MechanicsService.get_sheet(...)`.
- `PUT /api/campaigns/{id}/sheets/{kind}/{entity_id}` → `StateStore.write_sheet(...)` keyed by the campaign's current `mechanics_module`.
- `PATCH /api/campaigns/{id}` accepts a `mechanics` field, which writes the new value into `campaigns.mechanics_module`. Switching is permitted; the bulk-creation / inactive-sheet workflow described in spec 06 is not implemented.

The Frontend has a registered-modules browser at `frontend/src/routes/library/MechanicsView.tsx` (list + rescan + per-module detail page) and a generic widget-library renderer at `frontend/src/sheets/` (`SheetRenderer`, `renderField`, `scopeCss`, plus widgets `DotRating`, `GridRating`, `HealthTrack`, `PowerList`, `SlotList`, `DicePool`, `NestedSection`, etc.).

## Configuration

`MechanicsConfig` (frozen dataclass) mirrors spec 06 §Configuration:

```python
MechanicsConfig(
    root=Path("./data/mechanics"),
    reload_on_file_change=False,          # accepted but not enforced; rescan is manual
    validation=ValidationConfig(
        strict_sheets=True,               # enforced in update_sheet
        strict_events=False,              # enforced in validate_narrated_event
    ),
    rng=RngConfig(per_branch_seed=True),  # False → seed 0 everywhere
    defaults=DefaultsConfig(no_mechanics_warning=False),  # UI-only flag; not consumed yet
)
```

`MechanicsConfig.for_data_root(data_root)` is the canonical factory and is what `api/container.py` calls.

## Error handling (as implemented)

- **Discovery:** unparseable YAML or non-mapping top-level → `DiscoveryError` recorded; loader skipped for that dir.
- **Duplicate ids across roots:** first occurrence wins; subsequent ones become `DiscoveryError`.
- **Manifest schema failures:** formatted with their JSON pointer (`manifest invalid at <pointer>: <msg>`).
- **`id` ≠ directory name:** appended as a load error.
- **Import failures:** caught; the synthetic module is removed from `sys.modules` so a re-run can re-import cleanly.
- **Protocol mismatch:** load fails with `missing members: [...]`.
- **`instance.id` ≠ `manifest.id`:** load fails with a friendly mismatch message.
- **Per-call errors in pre-roll / resolve_roll / validate_narrated_event:** the Orchestrator and Extractor log at WARNING and continue (mechanics is treated as optional context).
- **Failed modules persist across rescans:** `MechanicsService.failed_modules()` returns the most recent error list per id so the UI can keep showing them after a failed reload.

## Test wiring

`backend/tests/mechanics/conftest.py` provides a `write_module(root, id, *, manifest=..., mechanics_py=...)` helper that synthesises minimal modules on a tmp path; the `service` fixture wires a `MechanicsService` around a real `StateStore`. The `MechanicsConformance` suite in `testing/conformance/mechanics.py` runs the same set of checks against any third-party module so authors can verify protocol fit without depending on Grimoire internals.
