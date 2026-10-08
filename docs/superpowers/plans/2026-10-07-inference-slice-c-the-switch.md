# Inference slice C — the switch: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the new inference settings real.

- Stores migrate once to format 2: backup first, marker last.
- The backend writes providers, model facts, roles and routes, and at format 2 it reads model behaviour from model facts.
- The facade takes each call's fallback from the resolver.
- The legacy cascade is retired.
- The user configures everything on `/providers`, `/models`, the Presets editor, the Settings summary, the Inspector, the reroll picker and the wizard.

**Architecture:**

- **Backend first (Tasks 1–8).** Storage keys, the migration, format-2 lowering, the settings and provider write APIs, the facade fallback, the override, and retiring the legacy surfaces. All of it sits behind the resolver slice A built.
- **Frontend second (Tasks 9–16).** Shared pickers come first, then each surface.
- **One PR.** The migration and the new UI ship together. After migration the backend ignores legacy keys, so the old UI must not outlive it (spec §14: "main never ships a UI that writes settings the backend ignores").

**Tech Stack:** FastAPI + file-backed store (Python 3.11+, pydantic v1/v2-agnostic), React + TypeScript + Vite, vitest + RTL, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`.

- Slice C is §14 row C. It draws on §4, §5, §6.3–6.5, §10, §11, §12, §14.1, §15 and §17.
- Task 0 amends the spec for the rulings at the end of this plan.
- Survey notes: `.superpowers/sdd/slice-c-notes/`.

## Global Constraints

**Rule 1: nothing spends without asking.** Every paid call started from a settings surface states what it sends and what it may cost, then waits for confirmation. The server enforces this; the UI does not. In this slice the rule covers three calls:

- the model test call;
- the Claude-subscription health check (it generates);
- **an Embedding role change** (re-embedding the library), per ruling 6.

**Rule 2: one resolver.** Every screen that says what will serve a task reads that answer from the backend. That includes:

- the status bar and the global header model;
- `ready` and `health`;
- the `/models` "resolves to"/"inherits" labels;
- capability warnings;
- the Embeddings chip.

No frontend table restates capabilities, routes or the embedding rule. `embeddingsOn.ts` is deleted.

**Rule 3: behaviour-neutral migration.**

- After migration, every task resolves to the same provider, model, sampling and fallback as before, and sends the same wire.
- `backend/tests/fixtures/inference_baseline.json` is **never regenerated**.
- New states go in a new fixture, `inference_baseline_c.json`, recorded from the code at this slice's base (the slice B head) before any Task 1 change. Record it in Task 0.
- The differences allowed after migration are listed in Task 2. There are no others.

**Rule 4:** embeddings never fall back. **Rule 5:** a price nobody reported is never rendered as zero.

**Migration.** The migration is global, once, idempotent and resumable. It runs in the background and is serialised by a migration lock.

- Order:
  1. `llm_connections.ensure_migrated()` (today's format-1 seeding; every `resolve()` already runs it, so it is not a migration write)
  2. backup
  3. providers
  4. facts
  5. roles
  6. routes
  7. split routes
  8. campaigns
  9. marker, last
- Derived reasoning presets (§11.2 step 4) move to slice I together with the legacy GLM field (ruling 3).
- A **fresh store** (no `config.md`) is never migrated: this build creates it already at format 2 (ruling 13).
- If the backup fails, nothing is written.
- Each campaign is written under its own `campaign_lock_nowait(cid)`, one at a time. `revision.bump(cid)` happens in the same hold.
- The migration persists `translate.global_view`/`campaign_view` output **verbatim** (padded ids, dangling references and `PRESET_CLEAR` stay as they are), then applies §11.2's enrichments.
- Every derived value is deterministic.

**Frozen data.**

- The frozen campaign `home/` is never migrated in place; tests use a copy.
- The `inference_baseline.json` and `test_lore_golden` goldens are never regenerated.

**Newer format.** When a store's `inference_format` is above `"2"`, every model-settings write returns 409 `newer_format`. Play continues.

**Unmigrated store.** While the migration is `pending` or `failed`, every new-layout settings write returns 409 `not_migrated` with the status. A write into a format-1 store would be ignored.

**Keys.** Keys are spelled only through `store/inference_keys.py` (Task 1 moves `keys.py` there).

- Legacy keys and fields are left in place, frozen. Nothing dual-writes them (§16).
- At format 2, legacy inference keys and legacy per-connection model fields are refused on write with 400.

**CLAUDE.md conventions.**

- Imports at module scope and acyclic. Store cross-package imports bind submodules.
- pydantic v1/v2-agnostic.
- `store.atomic` for every write. Path resolvers for every path.
- Classify new `cid`-taking mutators in `store/locks.py`.
- Hold `maintenance_excluded` around tree operations.
- The list/detail pattern for record pages. Pages that own the screen use `ColumnSection`.
- Keys only through `useHotkeys`, with `label` and `group`.
- In frontend tests, `await` means the page has settled.
- Shared scaffolding goes in `frontend/src/testkit/`.
- The `# routing-ok:` marker cap.
- Lint ratchets: `make baseline` when a count shrinks.

**Privacy:** invented names (Mara, Saltmarch, Seraphine, Winifred, Realm) and fake keys.

**Tests.**

- Backend: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/<file> -q`
- Frontend: `cd frontend && npx vitest run <file>`
- Gate: `make check`.
- Known root-only failure: `tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
- Tests run with the startup migration off: `conftest` sets `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`. A migration test calls `migrate.ensure()` itself.

**Parallel worktrees.**

- Reset to the slice branch first; the harness bases worktrees on `main`.
- Report in the final message; agents cannot write the shared checkout.
- Run `npm ci` inside the worktree. Never symlink `node_modules`.

## Review Focus

1. **A legacy store opens `/models` and sees exactly what plays.** Covers the active connection, a routed `summary`, a dangling `route_voice`, and a GLM connection with `reasoning_effort: max` under a route preset. Rows, cards, the status bar and every turn agree. → Task 4 `test_settings_view_matches_every_resolution`; Task 2 C-fixture sweep.
2. **A busy campaign during migration.** It stays legacy and plays through the translation. A write to it migrates it first. The next `ensure()` finishes it. → Task 2 `test_a_busy_campaign_is_skipped_and_finished_next_time`; Task 4 `test_a_campaign_write_migrates_that_campaign_first`.
3. **Deleting a provider or a preset that a role, fallback or pin names.** Global references are cleared; campaign references dangle. → Task 5 `test_deleting_a_provider_clears_global_roles_and_pins`, `test_deleting_a_preset_clears_global_role_and_pin_presets`.
4. **Two tabs.** Each writes one role. A named selection is replaced whole, and its siblings are untouched. → Task 4 `test_a_role_write_replaces_only_the_named_role`.
5. **A Claude-subscription provider in the wizard, and an Embedding change.** Neither spends without a confirmation the server checks. → Task 5 `test_a_generating_health_check_needs_confirm`; Task 4 `test_an_embedding_change_needs_confirm`; Task 16 `the wizard does not probe a Claude provider unasked`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec | rulings below folded in | 0 |
| `backend/tests/inference_baseline_c.py`, `fixtures/inference_baseline_c.json` (new) | states the old fixture lacks, recorded at the slice base | 0 |
| `store/inference_keys.py` (moved from `store/inference/keys.py`) | the one key speller; `GLOBAL_KEYS`, `CAMPAIGN_KEYS`, `is_newer` | 1 |
| `store/config.py`, `store/llm_connections.py`, `store/inference/facts.py`, `store/campaigns/lifecycle.py` | hold the new keys/fields; `set_stated`; `set_campaign_inference` | 1 |
| `store/inference/migrate.py` (new), `store/backups.py`, `main.py`, `routes/config.py` | the migration, its lock and status, the safety backup's retention, the triggers | 2 |
| `store/inference/resolve.py`, `capabilities.py`, `routes/common.py` | format-2 lowering from facts; facts `vision: off` no longer a capability `no` | 3 |
| `store/inference/settings.py`, `routes/inference.py` (new) | read view and validated writes for roles and routes | 4 |
| `routes/config.py`, `routes/models.py`, `store/llm_connections.py`, `store/sampler_presets.py` | providers with preset+billing, facts endpoints, health confirm, presets list, used-by, delete sweeps, format-2 refusals | 5 |
| `store/inference/resolve.py`, `llm.py`, `routes/common.py`, `routes/character_turns.py` | the fallback rides the conn; an incapable fallback is dropped; `Attempt.retries` | 6 |
| `routes/common.py`, `routes/models.py`, `store/inference/resolve.py` | the override takes provider/model/preset | 7 |
| `store/routing.py`, `store/sampler_presets.py`, `routes/*`, `store/scenes/lifecycle.py`, `llm_sampling.py`, `routes/todo.py` | retire `/routing` and the legacy cascade; header/ready/health and new-scene model from the resolver; `table()` gains reasoning | 8 |
| `frontend/src/api/*`, `frontend/src/components/inference/*` (new) | client + shared pickers, readouts, banners | 9 |
| `frontend/src/routes/ProvidersView.tsx` (new) | `/providers` | 10 |
| `frontend/src/routes/ModelsView.tsx` (new) | `/models` | 11 |
| `frontend/src/components/SamplerPresetEditor.tsx` | reasoning + Preview on… | 12 |
| `frontend/src/routes/ConfigView.tsx` | summary card; upgrade + newer banners | 13 |
| `frontend/src/components/SceneInspector.tsx`, `routes/CampaignView.tsx` | Inspector Models; header | 14 |
| `frontend/src/components/RerollRoute.tsx` | provider ▸ model ▸ preset | 15 |
| `frontend/src/routes/SetupWizard.tsx` | Provider and Models steps | 16 |
| `CLAUDE.md`, `docs/store-guarantees.md`, `backend/tests/test_docs_guard.py` | docs and guard | 17 |

---

### Task 0: Settle the spec, and record what the old fixture never saw

**Files:** the spec; `backend/tests/inference_baseline_c.py`, `backend/tests/fixtures/inference_baseline_c.json`, `backend/tests/test_inference_equivalence_c.py` (all new)

- **Spec.** Amend it for rulings 1–14 below, each in the section it touches: §4.2/§6.2, §10, §11.2, §11.3, §12 and §14 row C/D.
- **New fixture.** Build `inference_baseline_c.py` in the style of `inference_baseline.py`. It **wraps** `observe()` by import and adds its own keys; `inference_baseline.py` is not edited. Its states:
  - `glm_reasoning`: an openai_compatible GLM connection with `reasoning_effort: low` and a sampler preset.
  - `glm_max_under_route_preset`: GLM with `reasoning_effort: max`, plus a global `preset_summary`.
  - `openrouter_glm_with_effort`: an OpenRouter GLM model with `reasoning_effort: high` that was never sent.
  - `vision_off`: `vision: off` with a catalog row that says vision True.
  - `vision_on_catalog_no`: `vision: on` with a catalog row that says vision False.
  - `prefill_strict`: `prefill: true`, `post_process: strict`.
  - `embed_openrouter_legacy`: `embeddings_connection_id` names an OpenRouter connection.
- **What `observe` adds for these states:**
  - each task's lowered `prefill`, `post_process` and `vision`;
  - `llm_sampling.effective(conn)["effective"]`;
  - the image-description seam's answer, as **pass/refuse only** (its `missing` source legitimately moves from catalog to user);
  - `post_images.capability`.
- **Record it once at the slice base.** Run the recorder on this commit, before Task 1, and commit the JSON. From then on it is frozen like the first fixture.

- [ ] **Step 1:** Amend the spec. Commit `docs(spec): settle slice C's migration, lowering and confirmations`.
- [ ] **Step 2:** Write the recorder and the test (`test_inference_equivalence_c.py`: every C-state's `observe` equals the JSON). Record. Run it: PASS. Commit `test(inference): pin what slice A's fixture never exercised`.

---

### Task 1: Storage can hold the new settings

**Files:** `store/inference_keys.py` (moved; update `translate.py`, `cascade.py`, `resolve.py` and every importer), `store/config.py`, `store/llm_connections.py`, `store/inference/facts.py`, `store/campaigns/lifecycle.py`; Test: `tests/test_inference_storage.py` (new). Update `tests/test_llm_connections_store.py::test_rev_changes_on_create_and_update`, because `name` is now rev-neutral. That is the one intended change; name it in the report.

**Interfaces:**

*Why `keys.py` moves.* `store/inference/__init__.py` imports `resolve`, which imports `config`. If `config` imported the package, initialisation would go circular. The move makes the key speller a store-level leaf that `config` can import. Verify with `python -c "import grimoire.store.config"` and `test_import_guard`.

*`inference_keys`:*
- `GLOBAL_KEYS` contains, for all 15 `routing.ROUTES`:
  - `role_<r>_<part>` and `role_<r>_fallback_<part>` for each generative role;
  - `role_embedding_{provider,model}`;
  - `use_<route>` and `use_<route>_<part>`;
  - `preset_<route>`;
  - `FORMAT_KEY`.
- `CAMPAIGN_KEYS` is `GLOBAL_KEYS` minus the embedding role and minus the route keys of non-`campaign_scoped` routes. It **excludes** `FORMAT_KEY`: callers never write the marker as a field. `set_campaign_inference`, campaign creation and the migration stamp it themselves.
- `LEGACY_GLOBAL_KEYS`: `active_connection_id`, `fallback_connection_id`, `embeddings_connection_id`, `embeddings_model`, `route_*`.
- `is_newer(meta) -> bool`: true when the value parses as an int greater than 2. A malformed value is not newer.

*`config.py`:* `read_config`/`write_config` carry every `GLOBAL_KEYS` key, defaulting to `""`.

*`llm_connections.py`:*
- `_FIELDS` gains `preset` and `billing`.
- `REV_NEUTRAL_FIELDS` gains `name`, `preset` and `billing` (§4.1).
- `MODEL_FIELDS = ("model","vision","prefill","post_process","reasoning_effort","sampler_preset")`: the legacy fields that are refused at format 2 (Task 5).

*`facts.py`:*
- `set_stated(provider_id, model, *, vision=None, prefill=None, post_process=None) -> None`.
- `None` leaves a field unchanged.
- Valid values: `vision` ∈ `"", "on", "off"`; `prefill` is a bool; `post_process` ∈ `"none", "strict"`. Anything else raises `ValueError`.
- Reads, merges and writes under the module lock.

*`lifecycle.py`:*
- `set_campaign_inference(cid, fields) -> bool` (changed?).
- Only `CAMPAIGN_KEYS`; any other key raises `ValueError`. A blank value removes the key.
- Holds `campaign_lock(cid)`. Writes nothing when nothing changed.
- When the store's global layout is current, it also writes the campaign's `inference_format: "2"` in the same hold. Otherwise new keys would be ignored (§11.1).
- Bumps `revision.bump(cid)` when it wrote anything.
- Create, import and new-campaign flows stamp the marker when the global layout is current. Fork copies the source as it is, and `ensure()` migrates an unmarked one.

*Shared refusal helpers (Tasks 4 and 5 use them):*
- `routes/common.refuse_newer()` → 409 `newer_format` when `inference_keys.is_newer(read_config())`.
- `routes/common.refuse_unmigrated()` → 409 `not_migrated` with `migrate.status()` when the global layout is not current. Until Task 2 lands it checks only `translate.is_current`.

*Capabilities (ruling 2; it lives here so Task 2's sweep is green):* facts `vision: off` asserts nothing; `vision: on` is a user `yes`; `overrides.vision = no` is the user's `no`.

*Fresh store (ruling 13):* when `config.md` does not exist, the first materialisation of defaults by this build writes `inference_format: "2"`.

- [ ] **Step 1: Failing tests.**
  - `test_vision_off_is_not_a_capability_no`
  - `test_a_fresh_store_is_born_at_format_2`
  - `test_read_config_round_trips_every_inference_key`
  - `test_is_current_sees_the_marker_through_read_config`
  - `test_is_newer`
  - `test_campaign_keys_exclude_the_embedding_role_global_routes_and_the_marker`
  - `test_connection_preset_and_billing_round_trip_and_keep_rev`
  - `test_renaming_a_connection_keeps_its_rev_and_catalog`
  - `test_set_stated_merges_and_validates`
  - `test_set_campaign_inference_is_locked_bumps_revision_and_refuses_other_keys`
  - `test_set_campaign_inference_marks_the_campaign_on_a_current_store`
  - `test_set_campaign_inference_that_changes_nothing_writes_nothing`
  - `test_a_new_campaign_is_marked_on_a_current_store`
  - `test_store_config_imports_cleanly`
  - Update `tests/store_api_baseline.json` for the new `inference_keys` binding (deliberate; name it in the report).
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_config_store.py tests/test_llm_connections_store.py tests/test_inference_*.py tests/test_lock_domain_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py tests/test_routes.py`.
- [ ] **Step 5: Commit** `feat(inference): the store can hold roles, routes and the format marker`

---

### Task 2: The migration

**Files:**
- Create `store/inference/migrate.py`.
- Modify:
  - `store/backups.py` (`create_backup(prefix="")`; retention never prunes a `pre-inference-` archive);
  - `main.py` (start the background migration after startup; `GRIMOIRE_INFERENCE_AUTOMIGRATE=0` skips it);
  - `routes/config.py` (`put_data_dir` kicks it for the new root);
  - `store/locks.py` (a migration lock: process lock + proclock; classify `migrate`);
  - `backend/tests/conftest.py` (set the env var).
- Tests: `tests/test_inference_migrate.py` (new), and a post-migration sweep added to `tests/test_inference_equivalence.py` and `test_inference_equivalence_c.py`. The fixtures themselves are untouched.

**Interfaces:**
- `status() -> Status(state: "done"|"pending"|"running"|"failed"|"newer", reason, skipped: tuple[str, ...])`.
  - `done`, `newer` and `pending` are **derived from the store on every call**: the global marker, and any unmarked campaigns.
  - Only `running` and a failure reason are remembered, keyed by root, in `<home>/.cache/inference-migration.json`. Nothing leaks between test apps or roots.
- `ensure() -> Status` runs the steps synchronously under the migration lock (process lock + proclock).
  - If the lock is held, it returns the current status without waiting.
  - It pins `home()` at start and aborts before writing the marker if the root has changed.
  - As a store module, it takes **no** run exclusion itself.
- `campaign(cid) -> bool` migrates one unmarked campaign (steps 6–7 plus its marker) and returns whether it wrote.
  - The **caller holds** `campaign_lock(cid)`; this function never takes a lock.
  - It bumps the revision but does not stamp `updated`, which would reorder the library.
- `start(app)` lives in `main.py`, not the store.
  - When `GRIMOIRE_INFERENCE_AUTOMIGRATE` is not `0`, it holds `runs.maintenance_excluded(app)` and runs `ensure()` on a worker thread, without blocking.
  - The lifespan and `put_data_dir` call it.
  - `put_data_dir` answers 409 `busy` while the migration lock is held.
- **Failure policy.** An item that cannot be migrated (an unreadable `campaign.md`, a value its writer refuses) is skipped and its reason recorded in `skipped`. Only a failed backup fails the whole migration.

**Steps** (spec §11.2 with Task 0's amendments):
1. **Backup.** `create_backup(prefix="pre-inference-")`. On `OSError` or `StoreBusy`, record `failed` with the reason, write nothing, and retry on the next `start`. A store already at format 2 with only campaigns left takes no new backup.
2. **Providers.**
   - Write `preset=providers.infer(conn).id` and `billing=providers.billing(conn)` through `update_connection`. Both are rev-neutral.
   - A connection that already has `preset` is skipped. The skip is load-bearing, because a no-op update restamps the rev.
3. **Facts.** For each connection, call `set_stated(id, model, vision, prefill, post_process)` from its legacy fields, keyed by its model.
   - An empty-model `claude` connection is keyed `opus`.
   - Any other empty model is keyed `""`. The format-2 overlay reads that key for an empty selection model.
   - Defaults are not written.
4. *(Derived reasoning presets are deferred to slice I, ruling 3.)*
5. **Roles.** Persist `translate.global_view`'s role keys verbatim. The enrichment: an empty-model `claude` becomes `opus` wherever it is the selection's model.
   - **Embedding** is written only when `embed_space.resolve(<legacy cfg>)` is non-None (ruling 5).
6. **Routes.** Persist `global_view`'s `use_*`/`use_*_*` keys verbatim. Enrichments as in step 5. `preset_*` is untouched.
7. **Split routes.** `speaker` ← `scene`, `scene_break` ← `summary`, `voice_drift` ← `voice`. Copy `use_*`, `use_*_*` and `preset_*`.
8. **Campaigns.** For each unmarked campaign, run `with campaign_lock_nowait(cid): campaign(cid)`, one campaign at a time.
   - A busy campaign is listed in `skipped`.
   - Later `ensure()` calls finish unmarked campaigns even when the global marker is already set.
9. **Marker.** `inference_format: "2"`.

**Allowed differences after migration.** These are the only ones the post-migration sweeps may show; each test lists them by name.
1. The `routing` observation. It is a retired surface (Task 8); the sweep drops that key.
2. Provider-only `OVERRIDE_BODIES` cells. Task 2's sweep leaves them out by name. **Task 7** adds their assertion: each resolves to the standing model, checked as the full cell. This lets the two tasks run in either order.

Nothing else may differ, including the fallback cells, which `observe` keeps reading through `llm._routes(conn)`.

- [ ] **Step 1: Failing tests.**
  - `test_each_baseline_state_resolves_identically_after_migration`, over both fixtures.
  - `test_migration_is_idempotent`: no byte changes on a second run.
  - `test_a_failed_backup_writes_nothing_and_says_why`.
  - `test_the_safety_backup_survives_retention`.
  - `test_rev_is_preserved`.
  - `test_ollama_and_zai_urls_get_their_presets`.
  - `test_dangling_and_padded_references_stay_as_they_were`.
  - `test_a_legacy_openrouter_embedding_stays_off`.
  - `test_a_busy_campaign_is_skipped_and_finished_next_time`.
  - `test_each_migrated_campaign_bumps_its_revision`.
  - `test_the_frozen_campaign_is_migrated_only_as_a_copy`.
  - `test_a_newer_format_store_is_not_migrated`.
  - `test_two_concurrent_ensures_migrate_once`.
  - `test_startup_does_not_block_on_the_migration`.
  - `test_automigrate_off_in_tests` (the lifespan **and** `put_data_dir`).
  - `test_a_root_switch_mid_run_writes_no_marker`.
  - `test_put_data_dir_is_busy_while_migrating`.
  - `test_an_unmigratable_item_is_skipped_not_fatal`.
  - `test_campaign_migration_does_not_reorder_the_library`.
  - `test_campaign_migrates_under_the_callers_lock`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_inference_*.py tests/test_frozen_campaign.py tests/test_backups*.py tests/test_lock_*_guard.py tests/test_import_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_maintenance*.py`.
- [ ] **Step 5: Commit** `feat(inference): migrate a store to roles and routes, backup first, marker last`

---

### Task 3: At format 2, model behaviour comes from model facts

**Files:** `store/inference/resolve.py` (lowering), `store/inference/capabilities.py`, `routes/common.py` (the "Images: on" bridge); Test: `tests/test_inference_format2.py` (new)

**Interfaces:**
- `resolve.lower` at format 2 overlays `facts.of(provider, model, rev)` onto the lowered dict, setting `vision`, `prefill` and `post_process`. The legacy connection's values are replaced, so every existing consumer reads the per-model value unchanged: `llm.py` prefill and post_process, `post_images.capability`, and `llm_sampling`'s GLM check.
- **Untouched by the overlay:**
  - The legacy `sampler_preset`. The selection's preset already comes from the cascade.
  - The legacy GLM `reasoning_effort`. It keeps applying when the effective preset sets none, until slice I (ruling 3).
- **Unstated facts map explicitly:** `vision` `""`, `prefill` `None` → `False`, `post_process` `""` → `"none"`.
- **One refusal decision.** Move the seam's refusal logic (`_refuse_unusable`, `_refuse_incapable`, the bridge) into a pure store function, `inference.refusal(resolved) -> tuple[int, dict] | None`, in `store/inference/resolve.py` or a new `refusal.py`. `routes/common` raises from it, and Task 4's `settings.view` reports it as `problem`. No copy.
- `_IMAGES_ON_OUTRANKS` (the bridge) applies only at format 1. At format 2, facts `vision: on` is already a user `yes`.
- **Reroll onto another model.** At format 2 that model's facts apply, not the connection's. Per-model facts are the design (§4.2). This is not part of the baseline override cells (they observe neither prefill nor vision); say so in a docstring.

- [ ] **Step 1: Failing tests.**
  - `test_format_2_prefill_and_post_process_come_from_facts`
  - `test_format_2_post_images_read_the_model_facts`
  - `test_vision_off_stops_post_images_but_not_image_descriptions`
  - `test_vision_on_is_a_user_yes`
  - `test_the_images_on_bridge_is_format_1_only`
  - `test_glm_legacy_effort_still_applies_under_a_route_preset`
  - The C-fixture sweep (Task 2) passes for `vision_off`, `vision_on_catalog_no`, `prefill_strict`, `glm_*` and `openrouter_glm_with_effort`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_inference_*.py tests/test_post_images*.py tests/test_image_description_*.py tests/test_llm*.py tests/test_response_extend.py tests/test_reasoning_display.py`.
- [ ] **Step 5: Commit** `feat(inference): at format 2 a model's own facts drive the wire`

---

### Task 4: Read and write roles and routes

**Files:** Create `store/inference/settings.py` and `routes/inference.py` (register it); modify `routes/models.py` and `store/locks.py` (classify `settings`); Test: `tests/test_inference_settings.py` (new)

**Interfaces:**

`settings.view(scope, cid="") -> dict`:
```
{format, newer, migration: {state, reason, skipped},
 roles: {primary|fast|decision: {stored: {provider, model, preset}, fallback: {...},
                                 resolves: Sel|null, inherits: Sel|null, problem: str|null},
         embedding: {stored: {provider, model}, resolves: Sel|null, on: bool}},       # global scope only
 routes: [{key, label, hint, tasks, operation, default_role, requires, campaign_scoped,
           use, pin: {provider, model, preset}, preset,
           resolves: Sel|null, inherits: Sel|null, problem: str|null,
           role: "primary"|"fast"|"decision"|null}],
 providers: [{id, name, kind, preset, usable}], presets: [{id, name}], preset_clear}
# Sel = {provider, provider_name, model, preset, preset_name, via, scope}
```

- **`resolves`** is what `require_inference(<route's first task>, cid)` serves. It is computed with one `inference.resolve` per route, and `problem` is `inference.refusal(resolved)` (Task 3), the seam's own decision.
- **`inherits`** is the same answer with this scope's own choice for that row silenced. It is what "inherit (resolves to …)" shows, so a row naming X is never offered "inherit (X)". This is the role of today's `_silenced`.

`settings.write(scope, cid, body, *, confirm_embedding=False) -> None`:
- `body = {roles?: {role: {selection?: {provider, model, preset}, fallback?: {...}}}, routes?: {route: {use?, pin?: {...}, preset?}}, presets?: {route: id}}`.
- **A named selection is replaced whole**, so blank parts clear. Selections and fields the body does not name are untouched.

Refusals (400 unless noted):
- unknown provider;
- unknown preset (not `PRESET_CLEAR`);
- `use=embedding`;
- a preset on the Embedding role;
- the Embedding role, or a non-campaign route, written at campaign scope;
- an unknown role or route;
- legacy keys;
- 409 `newer_format`;
- 409 `not_migrated` when the global store is not at format 2;
- **an Embedding role change without `confirm_embedding: true`**. Copy: "Changing the embedding model re-embeds your library through <provider>, which may cost money — confirm to change it." (ruling 6).

**Campaign writes:**
- A campaign write takes `campaign_lock(cid)` once.
- Inside that hold, it runs `migrate.campaign(cid)` first when the campaign is **unmarked**: skipped, forked from a skipped campaign, or created by an older build on a synced device. Then it writes. A marker therefore never lands over legacy overrides that were never translated.

**Embedding confirmation:** `confirm_embedding` is needed only when the Embedding provider or model **changes to a non-empty value**. Clearing it, or rewriting the same value, needs no confirmation.

Routes:
- `GET`/`PUT /api/inference/settings`.
- `GET`/`PUT /api/campaigns/{cid}/inference` (404 when the campaign is missing).
- `PUT` takes `{..., confirm_embedding?: bool}` and returns the fresh view.

- [ ] **Step 1: Failing tests.**
  - `test_settings_view_matches_every_resolution`: Review Focus 1, run over both fixtures after migration.
  - `test_inherits_silences_this_scopes_own_choice`
  - `test_unset_fast_and_decision_inherit`
  - `test_a_role_write_replaces_only_the_named_role`
  - `test_use_embedding_is_refused`
  - `test_campaign_cannot_set_embedding_or_global_routes`
  - `test_an_embedding_preset_is_refused`
  - `test_an_embedding_change_needs_confirm`: Review Focus 5.
  - `test_a_write_on_a_newer_store_is_409`
  - `test_a_write_before_migration_is_409_not_migrated`
  - `test_a_campaign_write_migrates_that_campaign_first` (both a skipped campaign **and** a fork of one)
  - `test_clearing_embedding_needs_no_confirm`
  - `test_campaign_write_bumps_the_revision`
  - `test_the_view_never_carries_a_key`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_routing_guard.py tests/test_pydantic_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_routes.py`.
- [ ] **Step 5: Commit** `feat(inference): read and write roles and routes`

---

### Task 5: Providers, model facts, health and presets on the API

**Files:** `routes/config.py`, `routes/models.py`, `store/llm_connections.py` (delete sweep), `store/sampler_presets.py` (delete sweep); Test: `tests/test_provider_api.py` (new)

**Interfaces:**

*Provider create and update:*
- `ConnectionCreate`/`ConnectionUpdate` gain `preset` and `billing`.
- On create with a `url_locked` preset, `base_url` is the preset's. A different value is 400: "this provider's address is fixed".
- `kind` must equal the preset's kind (400).
- `billing` defaults to the preset's.
- The `anthropic` kind can be created.
- At format 2, `MODEL_FIELDS` on create or update are 400: "set this on the model, not the provider". At format 1 they stay writable.

*Presets:*
- `GET /api/providers/presets` returns each `capabilities.preset_body(p)` plus `generating_check: bool` (true for kinds whose check generates: `claude`).

*Facts:*
- `GET /api/llm-connections/{conn_id}/facts?model=` returns `facts.of` plus `capabilities: {name: cap_body}`.
- `PUT /api/llm-connections/{conn_id}/facts` with `{model, vision?, prefill?, post_process?, overrides?: {cap: ""|"yes"|"no"}}`. A `""` override removes that override.
- 400 on an invalid value, 404 on an unknown provider. Rates are slice E's.

*Health:*
- `POST /api/llm-connections/{conn_id}/health {confirm?: bool}`.
- For a generating kind (`claude`), `confirm is True` is required; otherwise 400 with "This check sends one short message and may use your subscription — confirm to run it.", and nothing is sent. Free kinds are unchanged.

*Delete sweeps:*
- `delete_connection` also clears the global `role_*_provider`, `role_*_fallback_provider` and `use_*_provider` keys equal to the id, together with their sibling model and preset parts.
- `delete_preset` clears the global role, fallback and pin `*_preset` parts equal to the id.
- Campaign references dangle.

*`PUT /config` at format 2:* refuses `LEGACY_GLOBAL_KEYS` with 400 "this setting moved to Models".

*Newer format:* every model-settings write here calls `common.refuse_newer()` (Task 1). That covers provider, facts, presets, the test run, and `PUT /config`'s inference keys.

*Used by:*
- `GET /api/llm-connections/{conn_id}` gains `used_by: [{kind: "role"|"route"|"fallback", key, scope, cid?}]`.
- It is computed from the stored global settings and each campaign's stored keys.
- It is read on the detail only, never on the list.

- [ ] **Step 1: Failing tests.**
  - `test_a_locked_preset_fixes_the_address`
  - `test_kind_must_match_the_preset`
  - `test_an_anthropic_provider_can_be_created`
  - `test_presets_list_names_the_generating_check`
  - `test_facts_round_trip_and_validate`
  - `test_an_empty_override_clears_it`
  - `test_a_generating_health_check_needs_confirm`
  - `test_a_free_health_check_needs_no_confirm`
  - `test_deleting_a_provider_clears_global_roles_and_pins`
  - `test_deleting_a_preset_clears_global_role_and_pin_presets`
  - `test_used_by_names_roles_routes_fallbacks_and_campaigns`
  - `test_model_fields_on_a_provider_are_refused_on_format_2_only`
  - `test_put_config_refuses_legacy_inference_keys_on_format_2`
  - `test_every_settings_write_refuses_a_newer_store` (parametrized)
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_provider_health.py tests/test_llm_connections_store.py tests/test_sampler_presets*.py tests/test_model_test_call.py tests/test_routes.py`.
- [ ] **Step 5: Commit** `feat(config): providers carry their preset and billing; model facts and confirmed checks`

---

### Task 6: The facade sends the resolver's fallback

**Files:**
- Modify: `store/inference/resolve.py`, `store/inference/resolved.py`, `llm.py`, `routes/common.py`, `routes/character_turns.py`.
- Tests: `tests/test_inference_fallback.py` (new).
- Tests that change, with the reason for each:
  - `tests/test_response_extend.py`, `test_sampler_presets_routes.py`, `test_regex_provenance.py`, `test_inference_resolve.py` (around `_facade_fallback` and `_fallback_connection`), and `test_routes.py` (around lines 389–426, `_real_facade` ~3794, and ~3894). Each one builds or asserts the global `_fallback_connection`, which this task deletes. Each switches to the per-call fallback.
  - Slice B's `test_an_incapable_fallback_is_reported_and_the_facade_still_sends_it`. Its pin is now inverted, so replace it.

**Interfaces:**
- The attempt builder attaches the fallback attempt's lowered conn to the primary's conn under `llm.FALLBACK_KEY = "_fallback"`. It is absent when there is no fallback, or when `fallback_missing` is non-empty. That is §5.3's drop; the fallback is still reported.
- `LLMClient._routes(conn)` uses `conn[FALLBACK_KEY]` when present, otherwise the constructor's `fallback` (tests build clients that way). `_same_route`, `fallback_sampling` and text-only filtering apply unchanged.
- `_dispatch` strips `FALLBACK_KEY` before any adapter, capture, health record, `ATTEMPTED`/`note_outcome`, `sent_names` or `_preset_refusal` sees the dict. One strip, at the boundary, tested at each consumer.
- `single` never reads it.
- `build_llm` passes no `fallback`. `_fallback_connection` is deleted. The two prompt-capture readers use `conn.get(llm.FALLBACK_KEY)`.
- `Attempt.retries: int`: the primary gets `config.llm_retries()` and the fallback gets 0. This is §5.4's slice-C field. It is informational; the facade keeps its own budget.
- **Baseline observer.** Unchanged: it keeps reading `llm._routes(conn)`, which now reads `_fallback`. That end-to-end proof is stronger than reading the resolver.

- [ ] **Step 1: Failing tests.**
  - `test_the_facade_sends_the_resolved_fallback`
  - `test_a_campaign_role_fallback_is_used_in_that_campaign`
  - `test_an_incapable_fallback_is_not_sent_and_is_reported`
  - `test_the_fallback_key_reaches_no_adapter_capture_or_health_record`
  - `test_single_never_uses_a_fallback`
  - `test_a_client_built_with_a_fallback_still_uses_it`
  - Both equivalence suites are green.
  - `tests/test_llm.py` and `tests/test_post_images_dispatch.py` are unmodified and green.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_routes.py tests/test_inference_*.py tests/test_llm*.py tests/test_response_*.py tests/test_regex_provenance.py tests/test_sampler_presets_routes.py tests/test_post_images*.py tests/test_incoming_capture.py tests/test_provider_health.py`.
- [ ] **Step 5: Commit** `feat(llm): each call carries its own fallback, and an incapable one is dropped`

---

### Task 7: The reroll override names a provider, a model and a preset

**Files:** `routes/models.py` (`RegenerateBody`), `routes/common.py` (`override_inference`), `store/inference/resolve.py` (`_overridden`); Test: `tests/test_inference_override.py` (new)

**Interfaces:**
- `RegenerateBody` gains `provider` and `preset`. `connection_id` is read as `provider` when `provider` is absent.
- **Format 2:**
  - A provider-only override keeps the standing model on that provider.
  - If there is no standing selection, it is 400: "name a model for this provider".
  - A `preset` replaces the route-level preset for the primary only. The fallback keeps its own, per §5.2.
  - `PRESET_CLEAR` means "no preset".
  - An unknown preset is 400.
- **Format 1:** today's meanings stay exactly as they are.
- `routed` compares effective provider, model and preset.

- [ ] **Step 1: Failing tests.**
  - `test_provider_only_keeps_the_standing_model_on_format_2`, asserting the full cell: sampling, `model_params`, `routed`.
  - `test_provider_only_without_a_standing_selection_is_400`
  - `test_provider_only_keeps_the_legacy_meaning_on_format_1`
  - `test_an_override_preset_outranks_the_route_preset_on_the_primary_only`
  - `test_preset_clear_in_an_override`
  - `test_an_unknown_override_preset_is_400`
  - `test_connection_id_is_read_as_provider`
  - `test_routed_compares_the_preset_too`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_routes.py -k regenerate tests/test_response_*.py tests/test_inference_*.py`.
- [ ] **Step 5: Commit** `feat(inference): a reroll can name a provider, a model and a preset`

---

### Task 8: Retire the legacy cascade; the header asks the resolver

**Files:**
- Modify: `store/routing.py`, `store/sampler_presets.py`, `routes/common.py`, `routes/config.py`, `routes/campaigns.py`, `routes/scenes.py` (scene create), `store/scenes/lifecycle.py`, `llm_sampling.py`, `routes/models.py`, `routes/todo.py`, `backend/tests/inference_baseline.py`.
- Tests:
  - Update `tests/test_routing.py`, `test_routing_routes.py`, `test_sampler_presets_store.py`, `test_store_api_baseline.py` (+ json), and `test_inference_resolve.py` (drop its comparison with `routing.resolve`).
  - Both equivalence tests drop the `routing` observation.
  - Every deleted test is listed in the commit body.

**Interfaces:**
- **Delete:**
  - `routing.resolve` and `routing.bundle`
  - `sampler_presets.resolve`, `inherited` and `scope_values`
  - `common._routing_body`, `_routing_fields`, `_preset_fields` and `_route_sampling`
  - `/routing` and `/campaigns/{cid}/routing`
  - `RoutingUpdate`
- **`inference_baseline.observe`:** stops calling `/api/routing`. The `routing` key is removed from the comparison in both equivalence tests, with a docstring line naming this task. The JSON is untouched; the comparison ignores that key.
- **`GET /config` at format 2:**
  - `active_connection`, `ready` and `health` describe what **chat** would run on. They come from the one resolve `_public_config` already makes (the existing marked resolve near `config.py:68`), so there is no new resolver call site and no new marker.
  - It gains `inference: {roles: {r: {provider_name, model, preset_name}}, embedding_on}`, computed with `cascade.role_selection` over one `translate.global_view`. That is pure and makes no `resolve` calls. The frontend keeps the same fields, so `App.tsx` and `api/models.ts` follow it unchanged.
  - The `ConfigUpdate`/`_public_config` pairing test still holds.
- **New scenes:** a new scene stamps `model` from "what chat would run on". That comes from the existing marked helper in `routes/scenes.py` (~5131) and is passed into `store/scenes/lifecycle`. No new `# routing-ok:` marker, because `RESOLVER_CALL_CAP` is full.
- **`llm_sampling.table()`** gains `{name: "reasoning_effort", label, kind: "choice", choices: [...REASONING]}`.
- **Todo:** the two embeddings links (`routes/todo.py` ~484, ~959) point to `/models/role/embedding`.
- **Report:** list every remaining reader of `LEGACY_GLOBAL_KEYS` outside `store/inference*`, `migrate` and `ensure_migrated`, each with its justification.

- [ ] **Step 1: Failing tests.**
  - `test_the_routing_endpoints_are_gone`
  - `test_config_reports_the_resolved_primary_on_format_2`
  - `test_config_inference_summary_makes_no_resolve_calls`; the `test_routing_guard` marker count is unchanged.
  - `test_a_new_scene_stamps_the_resolved_model`
  - `test_table_lists_reasoning_effort`
  - `test_the_todo_embeddings_links_go_to_models`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run every `tests/test_routing*.py tests/test_sampler_presets*.py tests/test_inference_*.py tests/test_scenes*.py tests/test_todo*.py tests/test_routes.py tests/test_import_guard.py tests/test_routing_guard.py`.
- [ ] **Step 5: Commit** `refactor(inference): retire the legacy routing cascade; the header asks the resolver`

**Frontend interim:** after this task, the old ConfigView, ConnectionEditor and ModelRoutingPicker call deleted endpoints. Their tests mock `api`, so `make check-web` stays green. The app itself is incomplete until Tasks 9–16, which is acceptable inside one PR. Do not open the PR before Task 17.

---

### Task 9: Frontend foundations

**Files:**
- Modify: `frontend/src/api/{types,client}.ts`, `frontend/src/testkit/campaignMocks.tsx` (add the new API functions).
- Create in `frontend/src/components/inference/`, each with a test: `ProviderModelPicker.tsx`, `PresetSelect.tsx`, `ControlsReadout.tsx`, `CapabilityBadges.tsx`, `TestCallDialog.tsx`, `InferenceBanner.tsx`.

**Interfaces:**
- **Client:**
  - `getInferenceSettings`, `putInferenceSettings(body, {confirmEmbedding?})`
  - `getCampaignInference`, `putCampaignInference`
  - `listProviderPresets`
  - `readModelFacts`, `putModelFacts`
  - `previewModelTest`, `runModelTest` (a draft run, polled through `draftRun`)
  - `checkConnection(id, {confirm?})`
  - Types match Tasks 4–5 exactly.
- **`ProviderModelPicker`:**
  - Props: `{needs: CapabilityNeed[], value: {provider, model}, onChange, providers}`.
  - Calls `readConnectionCapabilities` once per need and combines the answers:
    - a model is **Fits** when every need fits;
    - **hidden** when any need is hidden;
    - otherwise **Unverified**, with a "Test…" button that opens `TestCallDialog`.
  - A provider-wide reason replaces the list.
  - A typed id is accepted (§6.3).
- **`PresetSelect`:** `{value, onChange, presets, allowClear?}`.
- **`ControlsReadout`:** `{presetId, provider, model}`. Renders the server's state, wire and why per non-`supported` control. It computes nothing itself.
- **`CapabilityBadges`:** shows yes, no or unverified, with the test error in the title.
- **`TestCallDialog`:**
  1. On open, it previews: provider, model, what is sent, and the estimate. A null estimate reads "cost unknown — one tiny request"; never `$0.00`.
  2. **Run test** runs it with `confirm: true` and is disabled while a run is in flight, so a double click cannot pay twice.
  3. Modal Escape via `useHotkeys([{keys: "escape", run, whileTyping: true, label: "Close", group: "Dialog"}], {modal: true})`.
- **`InferenceBanner`:** `{status}`. Shows the newer-format banner ("This library was upgraded by a newer Grimoire") or "Upgrade pending: the safety backup failed (reason)". Used by every model-settings surface (§10).

- [ ] **Step 1: Failing tests.**
  - `ProviderModelPicker groups fits and unverified across several needs`
  - `shows the provider-wide reason`
  - `accepts a typed model id`
  - `TestCallDialog sends nothing until Run test`
  - `Run test cannot be pressed twice`
  - `never shows a zero cost for an unknown price`
  - `ControlsReadout renders the server's states`
  - `Escape closes the test dialog`
  - `InferenceBanner shows each state`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run eslint (`make check-eslint`, with `npm ci` in the worktree) and `npx tsc --noEmit -p .`.
- [ ] **Step 5: Commit** `feat(web): pickers that ask the server what a model can do`

---

### Task 10: `/providers`

**Files:**
- Create: `frontend/src/routes/ProvidersView.tsx` (+ test).
- Modify:
  - `App.tsx`: routes `/providers`, `/providers/:id`, `/providers/:id/models/*` (a splat, because model ids contain `/`), and `/connections` → `<Navigate to="/providers">`.
  - `shell/rail.ts`: titles.
  - `src/librarySections.ts`, `AppPaletteSource.tsx`, `components/ErrorNote.tsx`: Connections → Providers.
- Delete: `ConnectionsView.tsx`, `ConnectionEditor.tsx`, `ConnectionForm.tsx` and their tests. Move `CLAUDE_MODEL_OPTIONS` to `components/inference/claudeModels.ts` until Task 15.

**Behaviour** (§10; the page owns the screen):

*Column.* `ColumnSection` "Providers" with `+ New provider` and one row per provider. Selection is URL-driven. A selected provider also lists its models in a second `ColumnSection`.

*New provider.*
- Choose a preset first (`listProviderPresets`).
- The URL field is prefilled, and locked when `url_locked` is set.
- Key field.
- After save, free checks run automatically. A generating check does not.

*Provider detail.* Read-only by default, with an **Edit** button in the sidebar.
- **Sidebar:**
  - billing and extended samplers;
  - health: free checks run on open; a `generating_check` provider shows "Check (sends one short message)", which opens a confirm step and then calls `checkConnection(id, {confirm: true})`;
  - catalog age + Refresh;
  - **Used by** chips → `/models/role/<r>` or `/models/route/<k>`.
- **Main:** the catalog, with `CapabilityBadges` for generate, vision, embed and decide. Then output processing (`RegexRulesEditor`, as before).

*Model detail.*
- Facts: vision, prefill, post-processing and capability overrides. Read-only until **Edit**, which saves through `putModelFacts`.
- **Test…** opens `TestCallDialog`.

*Banner.* `InferenceBanner` is shown, and writes are disabled while the store is newer or not yet migrated.

- [ ] **Step 1: Failing tests.**
  - `clicking a provider shows the read-only view with its sidebar`
  - `Edit reveals the form`
  - `+ New provider asks for a preset first`
  - `a locked preset's address cannot be edited`
  - `a Claude provider's check asks before sending`
  - `Used by chips link to the models page`
  - `a model's facts are read-only until Edit`
  - `a model id with a slash opens its facts`
  - `/connections redirects to /providers`
  - `a newer store disables editing`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run eslint and tsc.
- [ ] **Step 5: Commit** `feat(web): providers, their models and what each can do`

---

### Task 11: `/models`

**Files:** Create `frontend/src/routes/ModelsView.tsx` (+ test). Modify `App.tsx` (routes `/models`, `/models/role/:role`, `/models/route/:key`), `shell/rail.ts`, and the Settings links.

**Behaviour** (§10, adapted to CLAUDE.md's page-owns-the-screen rule):

*Column.* Records sit in two `ColumnSection`s:
- **Roles** (4 rows).
- **Routing** (15 rows, under an "Advanced" toggle).

There is no `+ New`, because the set is fixed.

*Main.*
- With nothing selected, an overview of four read-only role cards: provider ▸ model ▸ preset, plus `ControlsReadout`.
- A selected role or route opens read-only. **Edit** opens the form, Save returns to the read-only view, and Cancel returns to it without saving.

*Role form.*
- Fields: `ProviderModelPicker` (needs `["generate"]` for Primary/Fast, `["decide"]` for Decision, `["embed"]` for Embedding) ▸ `PresetSelect`.
- Fallback: a disclosure with the same fields. Not on Embedding.
- Embedding: no preset. Saving an Embedding change shows the re-embedding confirmation and then sends `confirmEmbedding: true`.
- Unset Fast and Decision read "Same as Primary" / "Same as Fast" from `inherits`.
- The Decision card lists the routes whose `role` is `decision`, from the view, or says none do.

*Route form.* "Role ▾ (default: <default_role>)" or "Specific model…", plus a preset override.
- A pin uses `ProviderModelPicker` with needs `["generate", ...requires]`, so the image route asks for generate and vision.
- Labels read "inherit (resolves to …)" from `inherits`.

*Saving.* Each Save sends only the selection or row being edited (Review Focus 4).

*Warnings.* All four §10 warnings, verbatim, inline on the card or row they concern. They are computed from the capabilities API for the selected model.

*Banner.* `InferenceBanner`.

- [ ] **Step 1: Failing tests.**
  - `clicking a role shows the read-only view`
  - `Edit reveals the role form`
  - `Save returns to view; Cancel discards`
  - `a role form picks provider, then a fitting model, then a preset`
  - `unset Fast reads Same as Primary`
  - `the embedding role has no preset or fallback`
  - `an embedding change asks before saving`
  - `saving sends only that role`
  - `a route can pin a specific model that fits its requires`
  - Each of the four warnings appears in its case.
  - `the Decision card lists the routes using it`
  - `a newer store disables editing`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run eslint and tsc.
- [ ] **Step 5: Commit** `feat(web): pick a model for each role, and re-point a route`

---

### Task 12: Presets editor — reasoning and Preview on…

**Files:** `frontend/src/components/SamplerPresetEditor.tsx` (+ test)

**Behaviour:**
- The form renders `table()`'s `reasoning_effort` row as a choice.
- The view shows it.
- The UI calls these **Presets** (§4.3).
- **Preview on…** is `ProviderModelPicker` (needs `["generate"]`) plus `ControlsReadout` for the viewed preset.
- `InferenceBanner`, with writes disabled on a newer store.

- [ ] **Step 1: Failing tests.**
  - `the form offers reasoning effort`
  - `Preview on… shows the server's controls for that model`
  - `a newer store disables editing`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(web): presets carry reasoning effort and preview on a model`

---

### Task 13: Settings becomes a summary

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx` (+ test).
- Delete:
  - `frontend/src/routes/embeddingsOn.ts` and its test. Move `EMBEDDINGS_COPY` verbatim to `components/inference/copy.ts` and update `backend/tests/test_continuity_wording.py`'s scan list.
  - `components/ModelRoutingPicker.tsx` and its test, after Task 14.

**Behaviour** (§10):
- The **Connection**, **Model routing** and **Embeddings** sections are replaced by one **Models** section. It contains:
  - a summary card of the four roles, from `GET /config`'s `inference` block;
  - links to `/models` and `/providers`;
  - the recall depth and threshold;
  - the Embeddings chip;
  - the **embedding privacy disclosure**: `EMBEDDINGS_COPY` and the "what gets sent" paragraph, verbatim.
- `?section=semantic` still opens Models, for old links.
- `test_capstone_acceptance.py` requires `ConfigView.test.tsx` to keep two tests with these exact titles. Rewrite both against the new section and keep the titles:
  - "the embeddings chip reports embedding separately from recall depth"
  - "the disclosure names every embedded payload"
- Retries, timeouts and image sending stay.
- The Embeddings chip reads `embedding_on`.
- `InferenceBanner` shows here, including "Upgrade pending" (§11.2 step 1).

- [ ] **Step 1: Failing tests.**
  - `the models summary shows each role's resolved model`
  - `the embeddings state is the server's`
  - `?section=semantic opens the models section`
  - `no legacy connection or routing controls remain`
  - `a failed upgrade shows its reason`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(web): settings summarise models instead of editing connections`

---

### Task 14: Campaign Inspector "Models" and the play header

**Files:** `frontend/src/components/SceneInspector.tsx` (+ test), `frontend/src/routes/CampaignView.tsx` (+ test, via `testkit/campaignHarness.tsx`)

**Behaviour:**
- The Inspector's "Model routing" section becomes **Models**. It holds the campaign's overrides of Primary, Fast and Decision (Embedding is excluded) and its campaign-scoped routes.
  - Data comes from `getCampaignInference` and saves through `putCampaignInference`.
  - Labels read "inherit (resolves to …)" from `inherits`.
  - Rows are read-only until **Edit**.
  - `InferenceBanner` is shown.
- The play header's model and readiness come from `getCampaignInference(cid)`: the `scene` route's `resolves` and `problem`.
- `SceneInspector`'s `getModels` reads the resolved Primary's catalog. `api/models.ts` follows `GET /config`'s resolved `active_connection` (Task 8).

- [ ] **Step 1: Failing tests.**
  - `the inspector's Models section overrides a role for this campaign`
  - `it never offers the embedding role`
  - `rows are read-only until Edit`
  - `the header shows the resolved scene model`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(web): campaign model overrides in the inspector`

---

### Task 15: Reroll override

**Files:** `frontend/src/components/RerollRoute.tsx` (+ test), `RegenerateOverrides` and its callers

**Behaviour:**
- provider ▸ model ▸ preset. The model comes from `ProviderModelPicker` (needs `["generate"]`); the preset from `PresetSelect`.
- It sends `{provider, model, preset}`.
- "Default" sends no override.
- Claude models come through the same picker. If a `claude` provider lists no catalog, the user types an id. Delete `claudeModels.ts` when nothing uses it.

- [ ] **Step 1: Failing tests.**
  - `the reroll picker sends provider, model and preset`
  - `Default sends no override`
  - `an anthropic provider offers its models`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(web): reroll on any provider, model and preset`

---

### Task 16: Setup wizard

**Files:** `frontend/src/routes/SetupWizard.tsx` (+ test)

**Behaviour** (§10):

The steps are Storage → **Provider** → **Models** → Look → World.

- **Provider:** choose a preset, enter the key, then save. Free checks run automatically. A generating check runs only behind a confirm button.
- **Models:**
  - **Primary** is required.
  - **Fast** and **Decision** default to "Same as …".
  - **Embedding** is optional, with one line saying what it enables. Saving it needs the embedding confirmation.
  - Saving goes through `putInferenceSettings`.

**Fresh stores.** A fresh store is born at format 2 (ruling 13), so the wizard writes roles straight away. For a store that exists but is still migrating, the wizard reads `getInferenceSettings().migration` first. It shows "Finishing the upgrade…" until the state is `done`, and only then writes.

- [ ] **Step 1: Failing tests.**
  - `the wizard creates a provider from a preset`
  - `the wizard does not probe a Claude provider unasked`
  - `Primary is required to continue`
  - `Fast and Decision default to same as`
  - `embedding is optional and asks before saving`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(web): the wizard sets up a provider and the roles`

---

### Task 17: Docs, guards and the gate

**Files:** `CLAUDE.md`, `docs/store-guarantees.md`, `backend/tests/test_docs_guard.py`, and `CONTRIBUTING.md` (only if a guard or marker family changed)

- **CLAUDE.md:**
  - "Adding an LLM call site?": say what a format-2 store reads (roles, routes, model facts), that the fallback rides on the resolved conn, and that the facade drops an incapable one.
  - Add §14.1's spending rule: settings surfaces confirm, and the server enforces it.
  - Remove "the model test has no client caller until slice C".
  - Rewrite "today's settings are read through a legacy translation, so the surfaces that already exist still show their twelve routes".
  - Update "Nothing creates the store until the first API call" for the format-2 birth.
  - Recount the detached-runs inventory if a handler changed.
  - Name the `pre-inference-` safety archive. It falls outside `GET /backups`' listing pattern, so the upgrade banner names it too.
- **`docs/store-guarantees.md`:** a "Model settings migration" section covering:
  - the safety backup first, and that it is never pruned;
  - the marker last;
  - idempotent and resumable;
  - campaigns under their own lock, with busy ones retried;
  - background running, and the 409 while pending;
  - older builds frozen;
  - the newer-format refusal.
- **`test_docs_guard.py`:**
  - The section must name `migrate.ensure`, `inference_keys.FORMAT_KEY` and `inference_keys.CURRENT_FORMAT`, and each name must exist in code.
  - Keep the docs under the `MAX_SHARED_RUN` limit.

- [ ] **Step 1:** Write the docs and run `tests/test_docs_guard.py`.
- [ ] **Step 2:** Run `make check`, and `make check-pydantic1` separately if `make` stops early. Run `make baseline` if a count shrank.
- [ ] **Step 3: Commit** `docs: model settings migration, and the call-site rule at format 2`
- [ ] **Step 4: Gates.** Run a final whole-branch review and an adversarial review against the spec (Claude stand-ins), then one fix wave. Then open the PR.

---

## Parallelism

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | The fixture must be recorded before any code changes |
| 1 | 1 | Every backend task uses its keys and writers |
| 2 | 2, 7 | 2 is storage + migration; 7 is the override and owns the provider-only cells |
| 3 | 3, then 6 | Both change `resolve.py` (the lowering, then the fallback attach), so they run in sequence |
| 3' | 4, 5 (after 3) | 4 is `settings.py`/`routes/inference.py` and uses Task 3's `refusal`; 5 is `routes/config.py`. They share only `routes/models.py` (separate classes) |
| 4 | 8 | Retires what 4 replaces |
| 5 | 9 | The frontend foundation |
| 6 | 10, 11, 12, 15, 16 | Each uses Task 9's components. 10 and 11 both touch `App.tsx`/`rail.ts`, so merge them by hand. 15 deletes `claudeModels.ts` only after 10 lands |
| 7 | 14, then 13 | 13 deletes `ModelRoutingPicker` after 14 removes its last importer |
| 8 | 17 | Last |

## Rulings (Task 0 folds each into the spec)

1. **The format-2 lowering overlays model facts** (`vision`, `prefill`, `post_process`).
   - Otherwise the facts panel would write settings nothing sends, and a provider created at format 2 could never prefill.
   - Legacy `sampler_preset` is ignored at format 2; the cascade supplies the preset. (Plan review C2.)
2. **Facts `vision: off` only stops post images; it is not a capability `no`.**
   - It is today's post-image preference, now per model (§4.2).
   - Treating it as `no` would refuse image descriptions that serve today, which breaks rule 3.
   - `overrides.vision = no` is the user's `no`. (I2.)
3. **Reasoning effort.**
   - The legacy GLM `reasoning_effort` keeps applying when the effective preset sets none, at both formats, until slice I. That is slice B's existing rule.
   - **Derived reasoning presets (§11.2 step 4) move to slice I** with it.
     - In C they would change every observed preset (id, name, params) and buy no change on the wire.
     - `glm_effort` matches by model name regardless of kind, so an OpenRouter GLM connection would start sending reasoning it never sent.
   - Slice I derives presets when it stops reading the legacy field: openai_compatible GLM only, representable values only. (C3; re-review 1–2.)
4. **The migration runs in the background, never on the play path or inside a request.**
   - Startup schedules it, and `put_data_dir` reschedules it.
   - While it is pending or failed, new-layout writes answer 409 `not_migrated`.
   - It holds a migration lock; `main.start()` holds `maintenance_excluded` (never the store module); it skips busy campaigns with the no-wait lock; a data-dir switch mid-run writes no marker.
   - Tests turn the automatic start off with `GRIMOIRE_INFERENCE_AUTOMIGRATE=0` and call `ensure()` themselves. (C1, I7.)
5. **The Embedding role is migrated only when the legacy configuration actually embeds** (`embed_space.resolve` is non-None). A legacy OpenRouter embedding choice stays off, as §6.1 promises. (I1.)
6. **An Embedding role change requires `confirm_embedding: true`, server-enforced, from slice C.**
   - C newly makes paid OpenRouter embeddings selectable, and rule 1 cannot wait for D.
   - D keeps the metering and the embed operation. (I9.)
7. **Campaign keys need the campaign marker.**
   - `set_campaign_inference` stamps it when the global layout is current, and new campaigns are stamped at creation.
   - A write to **any unmarked** campaign migrates that campaign first, in the same lock hold. (I4; re-review 4.)
8. **`GET /config`'s `active_connection`, `ready` and `health` describe what chat would run on at format 2**, from the resolve `_public_config` already makes. The status bar and the model cache follow the resolver without frontend changes; the roles summary there is pure (no resolve calls). (I3, M4; re-review 7, 26.)
9. **"Inherit (resolves to …)" comes from `inherits`**, which is resolved with the scope's own choice silenced. (I8.)
10. **At format 2, the legacy keys and legacy model fields are refused on write.** At format 1 they stay writable. `delete_preset` sweeps role, fallback and pin presets as `delete_connection` sweeps providers. (I11.)
11. **The Claude health check needs `confirm`, enforced server-side.**
12. **The safety backup is named `pre-inference-…` and retention never prunes it.** Rule 3's "the user can go back" depends on it existing. (I7.)
13. **A fresh store is born at format 2.** A store with no `config.md` is never migrated — no backup of an empty install, nothing before the wizard's Storage step; this build's first materialisation of defaults writes the marker. (Re-review 10.)
14. **One refusal decision.** The seam and `settings.view` share `inference.refusal`, so `problem` is the seam's answer, not a copy; and the run exclusion is held in `main.py`, never in a store module (CLAUDE.md). (Re-review 6, 13.)
