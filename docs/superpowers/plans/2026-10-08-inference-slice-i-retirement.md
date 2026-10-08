# Inference slice I — Retirement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the legacy settings layout and the connection-dict lowering. The legacy GLM reasoning effort becomes derived presets, the legacy keys and connection fields are deleted from format-2 stores, and the translation layer is deleted. Every generation goes through `inference.generate` and an adapter registry that takes typed targets instead of connection dicts, and `FALLBACK_KEY` and `STRUCTURED_KEY` are deleted with the lowering.

**Architecture:**

- **One reader of the legacy layout, and it is the migration.** `store/inference/translate.py` is deleted. Its legacy-to-new mapping moves into `store/inference/migrate.py` as the migration's private planner. That mapping is how a format-1 store still migrates, so it is not deleted. Nothing on the runtime path reads a legacy key or a legacy connection field. A store below format 2 is refused at the seam with 409 `not_migrated` until the background migration lands, and the migration retries in the background (ruling 1).
- **Retirement is the last stage of `migrate.ensure`.** It runs after the marker, in the same run, on every store at format 2. It writes in this order:
  1. The derived reasoning presets, and each selection repointed to its derived preset (new module `store/inference/retire.py`).
  2. Each scope's legacy keys deleted in the same write that repoints that scope.
  3. Only once every campaign is done: the legacy fields stripped from every connection, each `rev` kept.

  Retirement is idempotent and deterministic, and it is resumable the way the migration is.
- **The lowering is replaced by typed targets.** `grimoire/wire.py` is a new gateway leaf. It holds `Target`, one attempt as an adapter sends it, and `Chain`, a primary target and its optional fallback. The resolver builds both. `ResolvedInference.chain` replaces `.conn`.
  - `grimoire/adapters.py` is the per-kind registry, with `generate`, `models`, `check`, native `decide` (from slice H) and capability flags.
  - `LLMClient` dispatches through the registry and keeps its retries, fallback, idle bound, image lowering, prefill tails and usage stamping.
  - `inference.generate` is the only door to generation. `inference.decide` and `inference.generate` hand the facade a `Chain`.
  - The fallback rides on `Chain.fallback`, not under `FALLBACK_KEY`. Structured mode is `Target.structured`, not `STRUCTURED_KEY`. The account block is `Target.account`, not `ACCOUNT_KEY`.
- **The suite moves to format 2 first.** Today the suite plays on format-1 stores through the translation (`GRIMOIRE_INFERENCE_AUTOMIGRATE=0` also turns off the birth stamp). Task 3 makes each test's store an upgraded default library at format 2, while the translation still exists to show that conversion changed nothing. Only after that do Tasks 5–10 delete things.

**Tech Stack:** Python ≥3.11, FastAPI, httpx, pytest; the React/vitest frontend, for one banner sentence and some type cleanup.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`. Slice I is §14 row I. It draws on §11.4, §11.2 step 4, §11.1, §11.3, §5.4, §7.1, §7.2, §7.4 (strict-mode limits), §9.3, §13, §14.1 and §15. Task 2 folds this plan's rulings into the spec.

**Base:** `claude/inference-slice-i` at `b2c85c6` (slice F's head). Slice I lands last, after D, E, F, G and H. Task 1 rebases onto them and re-checks every assumption below.

## Global Constraints

**The user's rules:**

- **Never run the full suite, and never `make check` or `make check-py`.**
  - Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates`.
  - A task that touches `frontend/` also runs `make check-eslint` and its named vitest files.
  - CI runs everything. **Merging waits for CI to be green.**
- **No live LLM calls.** Never run `evals/run.py --live` or `--record`. Every fake goes through `backend/tests/llm_fakes.py`.
- **Review gates.**
  - Until the PR, a Claude stand-in review serves as each Codex gate: the plan's adversarial review, each task's review, the final `/codex:review` and the final spec-conformance review.
  - Codex reviews the PR itself.
- **Invented names only.**
  - Use Seraphine, Mara, Winifred, Realm, Saltmarch, Rowan and Tobin, and ids like `glm`, `spare`, `nokey`.
  - Fake keys look like `sk-test-…`.
  - No figure measured from a real library goes anywhere.

**Spec values that bind here (verbatim):**

- `inference_format` is `2` (`inference_keys.CURRENT_FORMAT`). The legacy config keys are `active_connection_id`, `fallback_connection_id`, `route_*`, `embeddings_connection_id` and `embeddings_model`. The legacy connection fields are `model`, `post_process`, `reasoning_effort`, `sampler_preset`, `vision` and `prefill` (`llm_connections.MODEL_FIELDS`).
- Derived presets are made for **`openai_compatible` GLM connections only, and only for values a preset can represent**. The base is the selection's own sampler preset's params plus that effort, named `"<preset name> · reasoning <effort>"`, or `"Reasoning <effort>"` when it had no preset, with a deterministic id (`slugify` of that name). Identical derivations collapse to one.
- GLM models are `llm_reasoning.GLM_MODELS` (`glm-5.3`, `glm-5.3-flash`). GLM efforts are `low`, `high` and `max`. Preset efforts are `off`, `low`, `medium` and `high` (`llm_sampling.REASONING`). **Representable: `low`, `high`.**
- "A store still at format 1 at that point is migrated first, backup included" (§11.4).
- `space_id` stays exactly `f"{provider_id}\0{rev}\0{model}"`. Every `rev` survives retirement (`keep_rev` writes).
- Keys never reach logs, captures, diagnostics or API responses (§9.4). `Target.api_key` is `repr=False`.

**Fixtures that are never regenerated:**

- `backend/tests/fixtures/inference_baseline.json` and `inference_baseline_c.json`.
- `backend/tests/fixtures/frozen_campaign/home/`.
- `test_lore_golden`'s golden.

Only `frozen_campaign/snapshot.json` may be regenerated, deliberately and with the reviewed diff, and only if Task 5 moves it (CLAUDE.md, "The frozen campaign").

**CLAUDE.md rules this slice touches:**

- **Imports** are at module scope and acyclic (`test_import_guard.py`).
  - `wire.py` imports only the standard library.
  - `adapters.py` imports the provider clients, `wire`, `llm_sampling`, `llm_reasoning`, `llm_usage`, `llm_errors` and `decisions`, and never the store.
  - The store never imports `adapters` or `llm`.
  - Route modules bind the operation module as `from .. import inference as operations` (slice F's rule).
- **Routing guard:** a call site resolves with `require_inference(<literal>, cid, operation=…)`. Task 8 adds a rule: **a generation is `operations.generate(<literal>, …)`**, and `client.stream`/`complete` appear only in `inference.py`, plus `client.single` only in the model test.
- **Lock domain:** `retire.py` is classified in `store/locks.py`. Its campaign step takes `campaign_lock_nowait(cid)`, like the migration's.
- **Atomic writes:** every write goes through `store.atomic` (`test_atomic_guard.py`).
- **Lint gates are ratcheted:** when a count shrinks, run `make baseline PY=$PY` and commit the smaller file.
- **Linear history:** rebase, then `--ff-only`.

**Commands.** The worktree has no venv of its own, so pass the main checkout's:

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> … -q`
- Frontend: `cd frontend && npx vitest run <file> …`. Run vitest from `frontend/` (CLAUDE.md).
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`, `make check-eslint`.

**How the plan guarantees that deleting legacy reads loses no user data:**

1. **The mapping survives where it is needed.** Only the runtime reader of the legacy layout is deleted. The legacy-to-new mapping moves into `migrate.py` (Task 5) and stays the migration's input. A guard (Task 6) fails any other reader.
2. **Format 1 always migrates first, backup included.**
   - Retirement runs only inside `migrate.ensure`, and only on a `config.md` that is already current.
   - A format-1 store therefore passes through the existing steps first: the `pre-inference-` safety backup, providers, facts, campaigns and the marker. Only then does retirement run, in the same run.
   - If the backup fails, nothing is written and retirement never starts (Task 4, `test_a_failed_backup_retires_nothing`).
3. **Derive before delete.**
   - A connection's `reasoning_effort` is stripped only after every scope that could select it has had its presets derived and repointed.
   - Connection fields are stripped only once no campaign is left unmarked or un-retired (Task 6, `test_fields_are_stripped_only_after_every_campaign_is_retired`).
4. **Nothing is lost silently.** A value no preset can represent (`max`, or an effort under a route-level preset) is reported in the migration status's `skipped`, which the Models page's quiet line shows. It is never dropped without a word (Task 4).
5. **The legacy state stays restorable.**
   - A format-2 store took its never-pruned `pre-inference-` archive when C–H migrated it, and that archive holds exactly the keys and fields retirement deletes.
   - A format-1 store takes the archive in this build's step 1.
6. **Play is refused, never guessed.** Between I's first start and the migration landing, a format-1 store is refused with 409 `not_migrated`. Nothing is resolved from a partial or empty reading and then written down.
7. **Proven on the old trees.**
   - Both frozen baselines are migrated, then retired, then resolved, and must match the JSON except for named differences (Tasks 4 and 5).
   - A copy of the frozen campaign's `home/` is migrated and then plays a turn (Task 5).

## Assumptions to re-check before Task 1

G and H are being planned in parallel with this plan. Each line below is what this plan assumes they deliver, from the spec, together with how Task 1 checks it. **If one fails, Task 1 stops and reports. It does not improvise.**

- **A1 (G).** Continuity-identity and continuity-reconcile call `inference.decide(task, items, client=…, resolved=…)` after `require_inference(task, cid, operation="decide")`. `routing` gives `continuity` `operation="decide"` and `default_role="decision"`.
  - Check: `grep -n "continuity" backend/src/grimoire/store/routing.py`.
  - Check: `grep -rn "operations.decide(\"continuity" backend/src`.
- **A2 (G).** G adds no new `client.complete`/`client.stream` call site, no new `.conn` reader, and no new reader of a legacy key or connection field.
  - Check: the Task 8 inventory grep (below) finds no G file beyond the decide calls.
- **A3 (H).** H's native backend is gateway code that normalises into `decisions.py`. It is reached from `inference._BACKENDS["native"]` through a facade or adapter method that takes the **lowered connection dict**, for example `LLMClient.decide_native(items, conn, usage)` or methods on `openrouter.py` and `openai_compatible.py`.
  - Check: `grep -rn "native" backend/src/grimoire/inference.py backend/src/grimoire/llm.py backend/src/grimoire/openrouter.py backend/src/grimoire/openai_compatible.py`.
  - If H's shape differs, the "H's native backend" bullet of Task 9, Step 3 adapts to it. If H added a gateway module, list it in `adapters.py`'s imports.
- **A4 (H).** H stamps `decision_mode="native"` with `llm_usage.with_account(conn, …)` on a dict, the same way F stamps `"structured"`. Task 9 replaces both with `Target.with_account(…)`.
- **A5 (H).**
  - `resolve.OPERATION_CAPABILITY["decide"]` accepts `decide_native` or `generate`.
  - `Attempt.decision_mode` can be `"native"`, and `resolve.decision_mode` answers it.
  - The F-era decide skip is replaced.
  - Check: `grep -n "OPERATION_CAPABILITY\|def decision_mode\|native" backend/src/grimoire/store/inference/resolve.py`.
- **A6 (H).** Either H bounded strict mode's documented schema limits beyond the 1,000-value enum budget, or it left them to I (§7.4: "bounding them is slice H's, or I's with the adapter registry"). The limits are 15,000 characters of enum strings across a schema with more than 250 enum values, and 120,000 characters of property names, definition names and enum values in total.
  - Check: `grep -n "15_000\|120_000\|MAX_ENUM" backend/src/grimoire/decisions.py`.
  - Present: **skip Task 11**. Absent: Task 11 runs.
- **A7 (H).** H captures the mode, normalised answers and distributions (§9.4) through `routes.common._record_prompt` or a sibling. Its reads of `conn["kind"]`/`effective_model(conn)` are among the call-site reads Task 8 converts.
- **A8 (H).** `evals/runner.py`'s `--live` path still resolves with `require_inference(...).conn` and calls `client.complete(..., schema=)`. Task 8 converts it. The test is offline only and never runs `--live`.
- **A9 (D/E/F, landed).** The names this plan uses exist with these meanings:
  - D: `store/inference/embed.py` (`embed_sync`, `embed`, `_stamp`); `resolve.embedding`, `resolve.embed_attempt`, `resolve.embed_endpoint(conn, current)`; `translate.embedding_view`; `embed_space.endpoint_of` (reads `conn["api_key"]`); `embed_space.moved_by`.
  - E: `llm_usage.ACCOUNT_KEY`, `ACCOUNT_FIELDS == ("operation", "role", "billing", "decision_mode")`, `with_account`, `account`; the holder field `requested_model`; `llm._estimate` with `COUNT_TIMEOUT_S`; `store/inference/in_use.py`.
  - F: `resolve.FALLBACK_KEY`/`STRUCTURED_KEY`/`ACCOUNT_KEY`, `llm.FALLBACK_KEY`/`STRUCTURED_KEY`, `inference._with_mode`, `inference._BACKENDS`.
  - Check each with one `grep -n`. A rename is followed silently. A missing name stops the task.
- **A10 (H).** H introduces no new private connection-dict key. If it did (a `NATIVE_KEY`, say), Task 10 deletes it with the others and Task 10's guard lists it.

## Review Focus

1. **A library that skipped C–H, opened for the first time by this build.** It is at format 1, so play answers 409 `not_migrated` with the status until the background migration lands. The migration retries if the first pass is deferred or fails. After it lands, every task plays exactly as the frozen baselines recorded, with retirement already applied.
   - Task 5: `test_a_format_1_store_refuses_play_until_migrated_then_plays`, `test_the_migration_retries_until_it_lands`.
   - Task 5: both equivalence suites (migrated and retired).
   - Task 5: `test_frozen_campaign`'s turn, played on a migrated copy.
2. **A GLM connection whose legacy effort is `low` or `high`**, used as Primary with its own preset, as a role fallback with no preset, and as a campaign pin with a dangling preset id. After retirement, the wire carries the same `reasoning_effort` on every one of them, through derived presets. A `max` effort, and a route-level preset over such a connection, are reported in `skipped` and never dropped silently.
   - Task 4: `test_derived_presets_keep_the_wire`, `test_max_is_reported_not_derived`, `test_a_route_preset_over_a_glm_effort_is_reported`.
3. **A campaign an older build created after the switch.** It is unmarked and holds legacy `route_*` overrides. On its first turn under I, its overrides still apply: it is migrated on that read. A busy lock answers 409 `not_migrated` and never "no override".
   - Task 5: `test_an_unmarked_campaign_is_migrated_on_its_first_resolve`, `test_a_busy_unmarked_campaign_is_refused_not_misread`.
4. **The fallback, structured mode, the degrade sibling and image-bearing messages through `Chain`.** The facade sends what it sent before:
   - a route preset follows onto the fallback;
   - a text-only fallback is dropped for image-bearing messages;
   - structured mode goes only on the flagged attempt;
   - the post-image degrade sibling still fires;
   - E's estimate still runs where it ran.

   Tests: Task 9's converted facade suites keep every assertion. Task 9 adds `test_a_chain_sends_what_the_lowered_dict_sent`. `test_format2_play` passes in Tasks 8–10.
5. **A synced library where a pre-C build writes legacy keys or fields after retirement.** The resolver never reads them, the next `ensure` deletes them again, and nothing raises.
   - Task 6: `test_legacy_keys_written_after_retirement_are_ignored_then_removed`.

## Rulings on the spec's ambiguities (Task 2 folds each into the spec)

1. **Play at format 1 is refused with 409 `not_migrated`** at the seam. This was §11.2's "play resolves through the translation" until slice I.
   - The background migration retries with backoff while the store is `pending` or `failed`: `MIGRATION_RETRY_SECONDS = 60.0`, doubling, capped at `MIGRATION_RETRY_CAP_SECONDS = 3600.0`.
   - The banner says that play resumes once the upgrade lands. This is user-visible only on a store that never reached format 2.
2. **The mapping moves; the translation is deleted.** `translate.global_view`/`campaign_view` become `migrate._legacy_global`/`_legacy_campaign`, the migration's private planner and the only legacy reader.
   - `translate.py` and its read-time role go.
   - `routing.legacy_key`/`routing.CONFIG_KEYS` stay as spelling data that only the migration, the write refusals and the retirement read.
3. **An unmarked campaign at format 2 is migrated on its first resolve.** `resolve` itself stays read-only and answers `unmigrated="campaign"`. The seam (`routes.common`) and the settings view call `migrate.campaign_on_read(cid)`, which takes `campaign_lock_nowait` and migrates, then resolve again. If it is busy, the answer is 409 `not_migrated`. This extends §11.1's "a write to an unmarked campaign migrates it first" to reads, because no other reader of its keys remains.
4. **The derivation rule (§11.2 step 4, made exact).**
   - **Which slots.** Every selection slot (each generative role, its fallback, and each route pin) at global scope and in every campaign, where:
     - the provider's `kind` is `openai_compatible`;
     - its legacy `reasoning_effort` is `E` in `("low", "high")`;
     - the slot's model satisfies `llm_reasoning.is_glm`;
     - the slot's preset, read, sets no `reasoning_effort`.
   - **What it points at.** The slot is repointed to a derived preset. Its params are the base preset's params plus `{"reasoning_effort": E}`. Its name is `"<base name> · reasoning <E>"`, or `"Reasoning <E>"` when the slot names no readable preset.
   - **The id.** `slugify(name)`. If a preset with that id already holds different params or a different name, the id is `f"{slugify(name)}-{sha256(canonical params)[:8]}"`.
   - **Determinism.** Identical derivations collapse to one file, and two devices write the same bytes.
5. **What is not representable is reported, not derived.** Each case adds one `skipped` note per provider:
   - `E == "max"`: no preset value says it.
   - A route-level preset (`preset_<route>`, global or campaign, `PRESET_CLEAR` included) that sets no reasoning effort, where a GLM-effort provider is in the store: a route preset is shared by every attempt on that route, and deriving it would send reasoning to a fallback that never sent it.

   A per-call override preset is not stored, so it gets no note. §11.2 step 4 gains one line saying so.
6. **Retirement order.** After the marker, in the same `ensure`:
   - (a) One global write, under `llm_connections.LOCK` then `config_lock`: repoint the slots and delete the legacy config keys.
   - (b) Each campaign: one write under `campaign_lock_nowait(cid)` that repoints its slots and deletes its legacy keys. A busy campaign is skipped and retried.
   - (c) Only when no campaign is left: strip `MODEL_FIELDS` from each connection with a `keep_rev` write.

   A store already at format 2 takes **no new archive**: the C-era `pre-inference-` archive is never pruned and holds the legacy state.
7. **Legacy keys and fields stay writable at format 1** (they are the migration's input) **and refused at format 2**, as today. After retirement, a pre-C build sees no model settings, which §11.4 accepts. `llm_connections.ensure_migrated` writes `active_connection_id` only below format 2.
8. **`inference.generate`** is `generate(task, messages, *, client, resolved, usage=None, schema=None, stream=True)`.
   - It takes the call site's resolution, the way `decide` does (§7.4).
   - §7.2's `cid` and `override` are the inputs of that resolution: `require_inference` and `override_inference`.
   - With `stream=True` it returns an async iterator of text; with `stream=False`, an awaitable of the joined text.
9. **Typed targets replace the lowered dict.**
   - `Attempt.target: wire.Target`.
   - `ResolvedInference.chain: wire.Chain | None`.
   - The account block becomes `Target.account: wire.Account`, so `ACCOUNT_KEY` goes with `FALLBACK_KEY` and `STRUCTURED_KEY`.
   - `llm.ATTEMPTED` stays a holder key, and it holds the `Target` that answered.
10. **The registry.** `grimoire/adapters.py` has one class per `kind`, with `generate`, `models`, `check` and `decide` (native; it raises where unsupported) and flags `carries_images`, `lists_models`, `embeds` and `decides_natively`.
    - **Embed** stays D's caller-owned `EmbeddingsClient` door. The store must not import the gateway's SDK chain.
    - The registry's `embeds` flag and `resolve.embed_endpoint` are held equal by a test, and so are the store's adapter facts (`providers.py`) and the registry flags.
11. **E's cancel window is kept.** A cancel that lands during the local token count (at most `COUNT_TIMEOUT_S`) discards a reply that was already generated.
    - Closing the window needs a meter that files its row asynchronously, which is out of scope.
    - Task 9 keeps `_estimate` exactly where `_resilient` calls it. Task 12 documents the window in `docs/store-guarantees.md` under "What is not promised".
12. **`client.single` stays a facade method** for the model test's one probe. The routing guard allows it only in `routes/config.py`.
13. **The suite is born as an upgraded default library.** Every test store is born at format 2 with Primary = `openrouter` at `config.DEFAULT_MODEL`, which is what migrating a fresh format-1 store yields. Tests that build legacy state start from `legacy_store()`.
    - The frozen JSONs are never regenerated.
    - Their pre-migration comparisons are retired, in favour of migrated-and-retired comparisons with named differences.
14. **Strict-mode limits** (only if A6 finds them absent) are enforced in `decisions.validate` as `MAX_ENUM_CHARS = 15_000` (applied when a chunk's schema has more than `ENUM_CHARS_ABOVE = 250` enum values) and `MAX_SCHEMA_CHARS = 120_000`. `decisions.chunks` splits before either is crossed.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec | rulings 1–14 folded in | 2 |
| `store/inference_keys.py` | `born_current()` no longer gated by `AUTOMIGRATE_ENV` | 3 |
| `tests/conftest.py`, `tests/inference_fixtures.py` | an upgraded default library at birth; `legacy_store()`; marker `product_birth` | 3 |
| ~50 suites (inventory by grep, Task 3) | build format-2 state, or start from `legacy_store()` | 3 |
| `store/inference/retire.py` (new) | derivation planner, global and campaign retirement writes, field strip, `legacy_left()` | 4, 6 |
| `store/sampler_presets.py` | `put_derived(pid, name, params)` | 4 |
| `store/inference/migrate.py` | retirement stage; `_legacy_global`/`_legacy_campaign` (moved from translate); `campaign_on_read`; status counts retirement | 4, 5, 6 |
| `store/inference/translate.py`, `tests/test_inference_translate.py` | **deleted** (cases move to migrate's planner tests) | 5 |
| `store/inference/resolve.py`, `resolved.py`, `settings.py`, `in_use.py`, `store/embed_space.py`, `routes/common.py`, `routes/config.py` | read the current layout only; `unmigrated`; no legacy wording, bridge or GLM fallback | 5 |
| `llm_reasoning.py`, `llm_sampling.py` | GLM effort from the preset only | 5 |
| `main.py` | migration retry loop | 5 |
| `frontend/src/components/inference/InferenceBanner.tsx` (+ test) | "play resumes once it has finished" | 5 |
| `store/config.py`, `store/llm_connections.py`, `store/campaigns/lifecycle.py` | `drop_keys`, `strip_model_fields`; `ensure_migrated` below format 2 only; no legacy defaults | 6 |
| `tests/test_legacy_reader_guard.py` (new) | only the migration reads the legacy layout | 6 |
| `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/routes/ConfigView.tsx` | legacy fields out of the types | 6 |
| `wire.py` (new), `tests/wire_kit.py` (new) | `Target`, `Account`, `Sampling`, `Chain`; test builders | 7 |
| `store/inference/capabilities.py` | `post_image_reach(kind, vision_fact, vision_cap)` | 7 |
| `inference.py` | `generate`; `note_outcome`; decide on chains | 8, 9 |
| every generation call site: `routes/*.py`, `store/usage.py`, `llm_reasoning.py`, `backend/scripts/ingest_scene.py`, `evals/runner.py`, `evals/run.py` | through `operations.generate`; Target reads | 8 |
| `tests/test_routing_guard.py`, `test_usage_guard.py`, `test_operation_guard.py` | the generate rules | 8 |
| `adapters.py` (new) | the registry | 9 |
| `llm.py`, `llm_sampling.py`, `llm_usage.py`, `model_guidance.py`, `health.py`, `store/post_images.py`, `routes/common.py` (`build_llm`) | on `Target`/`Chain` | 9 |
| `tests/llm_fakes.py` and the facade suites | on chains | 9 |
| `store/inference/resolve.py`, `resolved.py`, `controls.py`, `store/embed_space.py`, `routes/config.py`, `routes/scenes.py`, equivalence helpers | the lowering deleted | 10 |
| `tests/test_lowering_retired_guard.py` (new) | the lowering stays gone | 10 |
| `decisions.py` | strict-mode limits (if A6) | 11 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `docs/store-guarantees.md`, `AGENTS.md` | docs | 12 |

---

### Task 1: Rebase onto D–H and re-check every assumption

**Files:** none changed except fix-ups the rebase forces. Report: `.superpowers/sdd/2026-10-08-inference-slice-i-retirement/task-1-report.md`.

- [ ] **Step 1: Rebase.** `git -C <worktree> fetch origin && git rebase origin/main`, once D–H have landed on `main`, or onto the integration branch the controller names. Resolve conflicts in this plan file only. Nothing else in the branch differs from F.
- [ ] **Step 2: Check A1–A10.** Run each check command under "Assumptions to re-check". Record each result as PASS, RENAMED (with the new name) or FAIL.
- [ ] **Step 3: Inventory.** Record the counts and file lists of these patterns, because Tasks 3, 5, 8 and 10 size themselves from them:
  - `grep -rn "translate\." backend/src`
  - `grep -rn "\.conn\b" backend/src evals backend/scripts`
  - `grep -rn "FALLBACK_KEY\|STRUCTURED_KEY\|ACCOUNT_KEY\|with_account" backend/src backend/tests`
  - `grep -rnE "\.(stream|complete|single)\(" backend/src evals backend/scripts`
  - `grep -rln "active_connection_id\|fallback_connection_id\|embeddings_connection_id\|set_campaign_routing" backend/tests`
- [ ] **Step 4: Start green.** Run:

  ```
  tests/test_inference_*.py tests/test_format2_play.py tests/test_llm.py
  tests/test_llm_structured.py tests/test_operation_guard.py
  tests/test_routing_guard.py tests/test_usage_guard.py tests/test_frozen_campaign.py
  ```

  Expected: all pass. A failure here belongs to a sibling slice. Report it and stop.
- [ ] **Step 5:** If any assumption FAILs, stop. Report the failing line and the plan edit it needs, and wait for the controller. Otherwise commit the report-free rebase (nothing to commit if it was clean) and report PASS.

### Task 2: Settle the spec

**Files:** Modify `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`: §5.4, §7.1, §7.2, §7.4 (strict-mode line), §10 (banner sentence), §11.1, §11.2 (step 4 and "When it runs"), §11.3, §11.4, §13, §14 row I and §15.

- [ ] **Step 1:** Fold rulings 1–14 into those sections, one short paragraph or line each.
  - §11.4 gains the retirement order (ruling 6), the format-1 refusal (ruling 1), the moved mapping (ruling 2) and migrate-on-read (ruling 3).
  - §11.2 step 4 gains the exact rule (rulings 4 and 5).
  - §7.2's signature becomes ruling 8's.
  - §5.4 and §13 name `wire.Target`/`Chain` and `adapters.py` (rulings 9 and 10).
  - §15 "Migration" gains "both baselines migrated and retired, with named differences" (ruling 13).
- [ ] **Step 2:** Search the spec for every "until slice I" and "slice I" and make each one past tense or point it at the section that now holds it.
  - Expected: `grep -n "until slice I" docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` prints nothing.
- [ ] **Step 3:** Commit: `docs(spec): inference slice I rulings -- retirement, generate, the registry`.

### Task 3: The suite plays at format 2

This task makes no product behaviour change, except that `born_current()` is no longer tied to the background switch. Every test store becomes an upgraded default library. The translation still exists while this task runs, so each converted suite proves the conversion changed nothing.

**Files:**
- Modify: `backend/src/grimoire/store/inference_keys.py` (`born_current`), `backend/tests/conftest.py`, `backend/tests/inference_fixtures.py`, `backend/tests/inference_baseline.py` (`client_at`), `backend/tests/test_format2_play.py` (docstring).
- Modify: each suite the inventory finds (Step 4).

**Interfaces:**
- Produces:
  - `inference_keys.born_current() -> bool`: always `True`. `automigrate()` still reads `AUTOMIGRATE_ENV` and now gates only the background thread.
  - `tests.inference_fixtures.UPGRADED_DEFAULT: dict[str, str]`: `{FORMAT_KEY: "2", role_primary_provider: "openrouter", role_primary_model: config.DEFAULT_MODEL}`.
  - `tests.inference_fixtures.legacy_store(home: Path | None = None) -> None`: writes `config.md` as `---\n---\n` under `home` (default `store.home()`) **before anything else reads the store**, so the store is format 1.
  - pytest marker `product_birth`: a test so marked is born the way a real install is (marker only, no Primary).

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_inference_storage.py`:

  ```python
  def test_a_test_store_is_born_an_upgraded_default_library(home):
      cfg = store.read_config()
      assert keys.is_current(cfg)
      assert (cfg["role_primary_provider"], cfg["role_primary_model"]) == ("openrouter", config.DEFAULT_MODEL)

  @pytest.mark.product_birth
  def test_a_product_store_is_born_with_the_marker_alone(home):
      cfg = store.read_config()
      assert keys.is_current(cfg) and not cfg.get("role_primary_provider")

  def test_legacy_store_is_format_1(home):
      inference_fixtures.legacy_store()
      assert not keys.is_current(store.read_config())
  ```

- [ ] **Step 2:** Run them. Expected: FAIL, because a fresh store is not current.
- [ ] **Step 3:** Change `born_current()` to `return True`, and update its docstring and `AUTOMIGRATE_ENV`'s comment. In `conftest.py`:
  - Keep `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`, which now means "no background thread". Rewrite its comment to say so.
  - Add an autouse fixture `_born_an_upgraded_default_library(monkeypatch, request)`. Unless the test carries `product_birth`, it monkeypatches `store.config._birth_marker` to return `dict(inference_fixtures.UPGRADED_DEFAULT)`.
  - Register the marker in `pytest_configure`.

  `inference_baseline.client_at(home)` calls `legacy_store(home)` before `create_app()`. Its builders build legacy state, and that remains true.
- [ ] **Step 4: Inventory and convert.** Run the four greps below. Each hit is converted one of two ways:
  - **(a)** It builds state the app would only write at format 1, and the test is not about the legacy layout. Rewrite it in format-2 terms:
    - roles and pins through `inference_fixtures.put_settings`;
    - model facts through `PUT /api/llm-connections/{id}/facts`;
    - presets on the role selection, not on the connection.
  - **(b)** The test is about the legacy layout or the migration: `test_inference_migrate`, `test_inference_translate`, `test_inference_equivalence*`, `test_frozen_campaign`, the baselines, `test_llm_connections_store`'s seeding cases and `test_config_store`'s legacy-key cases. Call `legacy_store()` first, or use `client_at`.

  The greps:
  - `grep -rlE "active_connection_id|fallback_connection_id|embeddings_connection_id|embeddings_model|set_campaign_routing|route_(scene|absorb|dossier|continuity|summary|tracker|suggestions|voice|image|tagline|scenario|opener)\b" backend/tests`
  - `grep -rlE "llm-connections[^\"]*\"[^)]*\"(model|prefill|vision|post_process|sampler_preset|reasoning_effort)\"" backend/tests`
  - `grep -rlE "(create|update)_connection\([^)]*(model|prefill|vision|post_process|sampler_preset|reasoning_effort)=" backend/tests`
  - `grep -rln "key not set\"" backend/tests` (the format-1 wording: "OpenRouter key not set" without the role suffix)

  Task 1's inventory lists the files. Record the final list in the task report.
- [ ] **Step 5:** Run every converted file, plus:

  ```
  tests/test_inference_storage.py tests/test_format2_play.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  tests/test_frozen_campaign.py tests/test_routes.py tests/test_routing_routes.py
  tests/test_character_turns.py tests/test_scene_break_routes.py
  ```

  Expected: all pass. A converted suite that fails has found a real format-1/format-2 difference. Report it; do not special-case it.
- [ ] **Step 6:** Commit: `test(inference): the suite is born an upgraded default library at format 2`.

### Task 4: Derived reasoning presets (retirement, step a/b)

**Files:**
- Create: `backend/src/grimoire/store/inference/retire.py`, `backend/tests/test_inference_retire.py`.
- Modify: `store/inference/migrate.py` (call retirement after the marker; docstring step 4), `store/sampler_presets.py` (`put_derived`), `store/locks.py` (classify `retire` beside `migrate`, in the same list and for the same reason: its campaign step runs under `campaign_lock_nowait` taken by the caller), `store/inference/__init__.py`, `tests/test_inference_equivalence_c.py` (`retired_expectation`), `tests/test_inference_migrate.py`.

**Interfaces:**
- Produces, in `retire.py`:
  - `REPRESENTABLE: tuple[str, ...] = ("low", "high")`, asserted equal to `tuple(e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)`.
  - `class Derived(NamedTuple): id: str; name: str; params: dict`.
  - `derived_name(base_name: str, effort: str) -> str`.
  - `derive(base: dict | None, effort: str, existing: Callable[[str], dict | None]) -> Derived`. `base` is a read preset or None; `existing` reads a preset by id.
  - `plan(fields: Mapping[str, str], lookup: Lookup, presets: Callable[[str], dict | None]) -> Plan`.
  - `class Plan(NamedTuple): repoint: dict[str, str]; derived: tuple[Derived, ...]; notes: tuple[str, ...]`.
    - `repoint` maps preset key to new preset id, over `migrate._selection_keys()`.
    - `notes` holds the ruling-5 notices.
  - `retire_global(lookup: Lookup) -> list[str]`: one `config.md` write under `llm_connections.LOCK` and `config_lock`. Returns notes.
  - `retire_campaign(cid: str, lookup: Lookup) -> list[str]`: the caller holds the campaign lock, and like `migrate.campaign` it raises `RuntimeError` when `locks.holds_campaign(cid)` is false. One atomic `campaign.md` write plus `revision.bump(cid)`, leaving `updated` alone. Returns notes.
  - `legacy_left() -> tuple[str, ...]`: in this task, the slots `plan` would still repoint, at global scope and in each readable campaign. Task 6 widens it to keys and fields.
- Produces: `sampler_presets.put_derived(pid: str, name: str, params: dict) -> None`. Writes when absent. Does nothing when identical. Raises `ValueError` when the id holds something else; `derive`'s digest suffix makes that unreachable.
- Consumes: `migrate._selection_keys()`, `migrate._lookup()`, `inference_keys.role_key/fallback_key/pin_key/preset_key`, `llm_reasoning.is_glm`, `llm_reasoning.GLM_EFFORTS`, `sampler_presets.read_preset`, `paths.slugify`.

- [ ] **Step 1: Write the failing tests** in `test_inference_retire.py`, on legacy stores built with `legacy_store()` plus raw connection writes, then `migrate.ensure()`:

  ```python
  def test_derived_name_and_id():
      assert retire.derived_name("Warm", "high") == "Warm · reasoning high"
      assert retire.derived_name("", "low") == "Reasoning low"
      d = retire.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: None)
      assert d == retire.Derived("warm-reasoning-high", "Warm · reasoning high",
                                 {"temperature": 0.9, "reasoning_effort": "high"})

  def test_a_taken_id_gets_a_deterministic_suffix():
      taken = {"name": "Warm · reasoning high", "params": {"temperature": 0.1}}
      d1 = retire.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: taken)
      d2 = retire.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: taken)
      assert d1 == d2 and d1.id.startswith("warm-reasoning-high-") and len(d1.id) == len("warm-reasoning-high-") + 8

  def test_derived_presets_keep_the_wire(home):
      # A `glm` openai_compatible connection, effort "high", model "glm-5.3",
      # sampler_preset "warm" (temperature 0.9); Primary on it; fallback on a
      # second GLM connection "glm2" (effort "low", no preset); a campaign
      # pinning `scene` to glm at a dangling preset id.
      ...  # build, then:
      wire_before = {t: _effective(t, cid) for t in TASKS}   # llm_sampling.effective(...)["effective"]
      assert migrate.ensure().state == "done"
      assert {t: _effective(t, cid) for t in TASKS} == wire_before
      cfg = store.read_config()
      assert cfg["role_primary_preset"] == "warm-reasoning-high"
      assert cfg["role_primary_fallback_preset"] == "reasoning-low"
      assert campaign_meta(cid)["use_scene_preset"] == "reasoning-high"
      assert sampler_presets.read_preset("warm-reasoning-high")["params"] == {"temperature": 0.9, "reasoning_effort": "high"}

  def test_identical_derivations_collapse(home):  # two slots, same base and effort -> one file
  def test_a_preset_that_already_sets_reasoning_is_left_alone(home):
  def test_openrouter_and_non_glm_slots_derive_nothing(home):  # the openrouter_glm_with_effort shape
  def test_max_is_reported_not_derived(home):
      ...; got = migrate.ensure()
      assert any("reasoning max" in s for s in got.skipped)
      assert not [p for p in sampler_presets.list_presets() if "max" in p["id"]]
  def test_a_route_preset_over_a_glm_effort_is_reported(home):  # preset_summary="cold" -> a note naming the provider
  def test_retirement_is_idempotent(home):  # ensure() twice -> second run writes nothing (digest of home unchanged)
  def test_a_busy_campaign_is_retired_on_the_next_run(home):  # hold campaign_lock in a thread; skipped; then done
  def test_a_failed_backup_retires_nothing(home, monkeypatch):  # backups.create_backup raises -> no preset file, no repoint
  ```

  `_effective(task, cid)` is `llm_sampling.effective(inference.resolve(task, cid).conn)["effective"]`. Task 10 changes it to read the target.
- [ ] **Step 2:** Run `tests/test_inference_retire.py`. Expected: FAIL (no module `retire`).
- [ ] **Step 3: Implement `retire.py`.**
  - `plan` walks each slot's provider, model and preset in `fields`.
  - The provider's `kind` and legacy `reasoning_effort` are read **through `lookup`**, which is the migration's strict lookup. A file that exists but cannot be read raises and fails the run, as the migration does today.
  - A slot whose preset reads as a preset that sets `reasoning_effort` is skipped.
  - Each route preset key (`inference_keys.preset_key(route.key)`) whose value is non-empty and, read, sets no `reasoning_effort` adds the ruling-5 note once per GLM-effort provider in `lookup`'s store.

  Then, in `migrate.py`:
  - After the marker write (`_switch`), and on every `ensure` of a current store, call the retirement stage, `_retire(run, left)`.
  - It runs `retire_global`, then `retire_campaign` for each campaign under `campaign_lock_nowait`, with busy campaigns appended to `run.skipped`.
  - It extends `run.skipped` with the notes.
  - `_run`'s early return `if current and not left` becomes `if current and not left and not retire.legacy_left()`. Add `legacy_left()` here as "any slot `plan` would repoint". Task 6 widens it.
- [ ] **Step 4:** Add `retired_expectation(recorded: dict, state: str) -> dict` to `test_inference_equivalence_c.py`. For `glm_reasoning` and `glm_max_under_route_preset`, it rewrites only each task cell's `sampling.preset_id`/`preset_name` to the derived preset's, where the slot was derived. Nothing else changes; the `effective` wire must be equal. Its after-migration test applies it.
  - `glm_max_under_route_preset` derives nothing (`max`). Its effective cells must still match **in this task**, because the legacy read is still live. Task 5 names the difference.
- [ ] **Step 5:** Run:

  ```
  tests/test_inference_retire.py tests/test_inference_migrate.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  tests/test_sampler_presets_store.py tests/test_lock_domain_guard.py
  ```

  Then `make check-lint check-mypy PY=$PY`. Expected: all pass.
- [ ] **Step 6:** Commit: `feat(inference): retirement derives reasoning presets from the legacy GLM effort`.

### Task 5: The runtime reads format 2 only

**Files:**
- Delete: `store/inference/translate.py`, `tests/test_inference_translate.py`. Each of its cases becomes a planner case in `test_inference_migrate.py` against `migrate.global_fields`/`campaign_fields`.
- Modify:
  - `store/inference/migrate.py`: `Lookup`; `_legacy_global`/`_legacy_campaign` moved verbatim (minus `current`); `_legacy_embeds(cfg, lookup) -> bool` in place of `embed_space.resolve(cfg)`; `campaign_on_read`.
  - `store/inference/resolve.py`, `resolved.py`, `settings.py`, `in_use.py`.
  - `store/embed_space.py` (D's note: `resolve.embedding` reads `role_embedding_*` straight from `cfg`).
  - `store/context/semantic.py` (docstring).
  - `llm_reasoning.py`, `llm_sampling.py`.
  - `routes/common.py`, `routes/config.py`, `store/llm_connections.py` (`get_active` deleted), `main.py`.
  - `frontend/src/components/inference/InferenceBanner.tsx` and its test.
  - `tests/test_inference_equivalence.py`, `test_inference_equivalence_c.py`, `test_frozen_campaign.py`, and the `frozen_client`/`frozen_home` fixtures.

**Interfaces:**
- Produces:
  - `ResolvedInference.unmigrated: str = ""`: `"store"` when `config.md` is below format 2 (then `attempts == ()`), `"campaign"` when the campaign is unmarked at format 2, else `""`. The `current` field is kept and set to `not unmigrated`.
  - `migrate.campaign_on_read(cid: str) -> bool`: takes `campaign_lock_nowait(cid)` and returns whether the campaign is marked afterwards. False when busy or unreadable.
  - `routes.common._usable` order: `unmigrated == "campaign"` leads to `campaign_on_read` and one re-resolve. Any remaining `unmigrated` raises `_not_migrated()`. Then `inference.refusal`.
  - `llm_reasoning.glm_effort(model: str, value: str | None) -> str`: the preset's value only.
  - `main.MIGRATION_RETRY_SECONDS = 60.0` and `MIGRATION_RETRY_CAP_SECONDS = 3600.0`.
- Deleted:
  - `translate.*`, `resolve.IMAGES_ON_OUTRANKS`, `resolve._bridged` and every format-1 branch of `resolve.unusable`/`incapable`;
  - `ResolvedInference.legacy_route`;
  - `_overridden`'s `current` parameter (the format-2 meaning stays);
  - `llm_connections.get_active`.

- [ ] **Step 1: Write the failing tests:**

  ```python
  # test_inference_format2.py
  def test_a_format_1_store_refuses_play_until_migrated_then_plays(client):
      legacy_store(); _legacy_active(client)          # openrouter keyed, active
      r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hi"})
      assert r.status_code == 409 and r.json()["detail"]["kind"] == "not_migrated"
      assert migrate.ensure().state == "done"
      assert client.post(...chat...).status_code == 200

  def test_an_unmarked_campaign_is_migrated_on_its_first_resolve(client): # route_scene -> "spare" in a campaign without the marker
      assert routes.common.require_inference("chat", cid).conn["id"] == "spare"   # `.chain.primary.provider_id` from Task 10
      assert keys.is_current(campaigns.read_campaign(cid)["meta"])

  def test_a_busy_unmarked_campaign_is_refused_not_misread(client):  # hold the campaign lock in a thread -> 409 not_migrated

  def test_the_legacy_glm_effort_is_no_longer_read(home):  # a format-2 provider file still carrying reasoning_effort: "high", no derived preset -> effective has no reasoning_effort
  def test_glm_with_no_preset_effort_reports_supported_and_sends_nothing():
      eff = llm_sampling.effective({..."model": "glm-5.3", "kind": "openai_compatible", "sampling": {"params": {}}})
      assert eff["controls"]["reasoning_effort"]["state"] == "supported" and "reasoning_effort" not in eff["effective"]

  # test_main_migration.py (or the existing main/migration suite)
  def test_the_migration_retries_until_it_lands(monkeypatch):  # first ensure -> failed, second -> done; stop.wait called with 60.0 then the loop ends
  ```

  In `test_inference_equivalence.py` and `_c.py`:
  - Replace `test_resolution_matches_the_baseline` with `test_a_legacy_state_is_refused_until_migrated`. For every state, every task cell answers 409 `not_migrated`.
  - The after-migration tests stay, with one named difference added to `retired_expectation`: in `glm_max_under_route_preset`, every cell's `effective` loses `reasoning_effort: "max"` (ruling 5).
- [ ] **Step 2:** Run those tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Move the mapping into `migrate`.
  - `resolve.resolve` reads `cfg` and `meta` as they stand:
    - not `keys.is_current(cfg)` gives `ResolvedInference(..., attempts=(), current=False, unmigrated="store")`;
    - an unmarked campaign gives `unmigrated="campaign"` with the global answer computed and **no** campaign keys read.
  - `settings.view` calls `campaign_on_read` for an unmarked campaign before reading it. At format 1 its rows carry the not-migrated sentence as `problem`.
  - `in_use` at format 1 reports nothing.
  - `embed_space`/`resolve.embedding` read `role_embedding_*` with the strip rule. `embed_endpoint(conn, current)` keeps `current` for `_legacy_embeds` only.
  - `routes/config._public_config` at format 1 gives `active_connection: None, ready: False`.
  - `main._migrate_inference` loops: while the state is `pending` or `failed` and `not stop.is_set()`, call `stop.wait(delay)`, then `delay = min(delay * 2, CAP)`. The run-exclusion deferral counts as `pending`. Each attempt takes `exclude_maintenance` afresh, so nothing is held while the loop waits and an image-store run can start between tries.
  - `InferenceBanner`'s pending/failed sentence ends: "Play resumes once it has finished." The newer-format sentence is unchanged.
- [ ] **Step 4: The frozen campaign.** `frozen_client` and the turn and absorb tests run `migrate.ensure()` on the copy first, and assert `state == "done"`.
  - If `snapshot.json` moves, regenerate it with the sweep command in CLAUDE.md. Paste the diff in the report. The only acceptable change is inference keys that the migration wrote into the copied config. Any other change: stop and report.
- [ ] **Step 5:** Run:

  ```
  tests/test_inference_format2.py tests/test_inference_migrate.py tests/test_inference_resolve.py
  tests/test_inference_settings.py tests/test_inference_read_api.py tests/test_inference_override.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  tests/test_embed_space_role.py tests/test_context_semantic.py tests/test_llm_sampling.py
  tests/test_reasoning_display.py tests/test_frozen_campaign.py tests/test_format2_play.py
  tests/test_routing_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py
  tests/test_todo_route.py
  ```

  Then `cd frontend && npx vitest run src/components/inference`, then the gates. Expected: all pass.
- [ ] **Step 6:** Commit: `feat(inference): the runtime reads format 2 only; the translation is the migration's`.

### Task 6: Retirement deletes the legacy keys and fields

**Files:**
- Modify:
  - `store/inference/retire.py`: delete keys inside `retire_global`/`retire_campaign`; `strip_fields`; full `legacy_left`.
  - `store/inference/migrate.py`: status; ordering (c).
  - `store/config.py`: `drop_keys`; legacy defaults out of `read_config`.
  - `store/llm_connections.py`: `strip_model_fields`; `ensure_migrated` writes `active_connection_id` only below format 2; `_dangling` no longer sweeps legacy keys.
  - `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/routes/ConfigView.tsx`: legacy fields and keys out of the types and comments.
- Create: `backend/tests/test_legacy_reader_guard.py`.

**Interfaces:**
- Produces:
  - `config.drop_keys(names: Iterable[str]) -> None`: a raw frontmatter rewrite under `config_lock`, atomic, that never adds a key.
  - `llm_connections.strip_model_fields(conn_id: str) -> bool`: removes `MODEL_FIELDS` with a `keep_rev` write. Returns whether it wrote.
  - `retire.legacy_left() -> tuple[str, ...]`: human-readable items: a legacy config key, a campaign's legacy key, a connection's legacy field, or a slot still to derive.
  - Migration `status()`: `pending` while `legacy_left()` is non-empty on a current store.
- Consumes: `inference_keys.LEGACY_GLOBAL_KEYS`, `routing.CONFIG_KEYS`, `llm_connections.MODEL_FIELDS`.

- [ ] **Step 1: Write the failing tests:**

  ```python
  def test_retirement_deletes_legacy_keys_and_fields(home):  # after ensure(): no LEGACY_GLOBAL_KEYS in raw config.md, no route_* in raw campaign.md, no MODEL_FIELDS in any connection file; every rev unchanged
  def test_fields_are_stripped_only_after_every_campaign_is_retired(home):  # one busy campaign -> connection files keep reasoning_effort; next run strips
  def test_space_ids_survive_retirement(home):  # embed_space.endpoint()["space"] before == after
  def test_legacy_keys_written_after_retirement_are_ignored_then_removed(home):  # raw-write active_connection_id + a connection's model field; resolve unchanged; status pending; ensure() -> removed
  def test_a_fresh_store_never_gets_active_connection_id(home):  # product_birth; list_connections(); raw config has no active_connection_id

  # test_legacy_reader_guard.py
  def test_only_the_migration_reads_the_legacy_layout():
      # AST over backend/src/grimoire: a str constant equal to a LEGACY_GLOBAL_KEYS
      # member or to "reasoning_effort"/"sampler_preset" used as a subscript or
      # .get() key on something other than a preset/params dict, and any
      # reference to routing.CONFIG_KEYS / routing.legacy_key, appears only in
      # ALLOWED = {"store/inference/migrate.py", "store/inference/retire.py",
      #            "store/inference_keys.py", "store/config.py",
      #            "store/llm_connections.py", "store/routing.py",
      #            "store/campaigns/lifecycle.py"}
  def test_the_guard_flags_a_planted_reader():  # a planted `cfg.get("active_connection_id")` in a temp module is reported
  ```

  The guard's `reasoning_effort` rule matches only `conn.get("reasoning_effort")` / `conn["reasoning_effort"]`, where the receiver's name is `conn`, `raw` or `connection`. A preset's `params["reasoning_effort"]` is legitimate. The docstring states this reach (CLAUDE.md: "The guards do not prove absence").
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** in the order of ruling 6.
  - Drop the legacy defaults `active_connection_id`, `fallback_connection_id` and `embeddings_*` from `read_config`'s defaults. Keep them in `_CONFIG_KEYS` so a format-1 store can still be written as the migration's input (ruling 7).
  - Frontend: delete `reasoning_effort?`/`sampler_preset?` from the connection types, and the legacy keys from the `Config` type and the `ConfigKey` union. Fix the type errors that follow; none should be in rendering code, since C removed the legacy UI.
- [ ] **Step 4:** Run:

  ```
  tests/test_inference_retire.py tests/test_inference_migrate.py tests/test_legacy_reader_guard.py
  tests/test_config_store.py tests/test_llm_connections_store.py tests/test_provider_api.py
  tests/test_embed_space_role.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  tests/test_atomic_guard.py
  ```

  Then `cd frontend && npm run typecheck && npx vitest run src/routes/ConfigView.test.tsx src/api/client.test.ts src/routes/ProvidersView.test.tsx`, then the gates and `make check-eslint`.
- [ ] **Step 5:** Commit: `feat(inference): retirement deletes the legacy keys and connection fields`.

### Task 7: `wire.Target`, built beside the lowered dict

**Files:**
- Create: `backend/src/grimoire/wire.py`, `backend/tests/wire_kit.py`, `backend/tests/test_wire.py`, `backend/tests/test_inference_target.py`.
- Modify: `store/inference/resolved.py` (`Attempt.target`; `ResolvedInference.chain`), `store/inference/resolve.py` (`_target`; `_chain` sets `structured` and `account` on targets in the same place it sets them on dicts), `store/inference/capabilities.py` (`post_image_reach`), `store/post_images.py` (`capability` delegates to `post_image_reach`).

**Interfaces:**
- Produces, in `wire.py` (standard library only):

  ```python
  @dataclass(frozen=True)
  class Sampling: preset_id: str = ""; preset_name: str = ""; scope: str = "none"; params: dict = field(default_factory=dict)

  @dataclass(frozen=True)
  class Account:   # the ledger's view of an attempt (spec 9.3); fields = llm_usage.ACCOUNT_FIELDS
      operation: str = ""; role: str = ""; billing: str = ""; decision_mode: str = ""

  @dataclass(frozen=True)
  class Target:
      provider_id: str; kind: str; model: str          # model: the one SENT (claude unset -> "opus")
      provider_name: str = ""; base_url: str = ""
      api_key: str = field(default="", repr=False)
      rev: str = ""; requested_model: str = ""          # requested_model: E's holder field (A9)
      sampling: Sampling = Sampling(); sampler_support: str = ""
      model_params: tuple[str, ...] | None = None; model_features: dict | None = None
      prefill: bool = False; post_process: str = "none"; reads_images: str = "unknown"
      structured: bool = False; degrade: bool = False; account: Account = Account()
      @property
      def label(self) -> str: ...                       # provider_name or provider_id
      def with_account(self, **fields: str) -> Target: ...   # dataclasses.replace, a NEW Account

  @dataclass(frozen=True)
  class Chain:
      primary: Target; fallback: Target | None = None
      @property
      def attempts(self) -> tuple[Target, ...]: ...
      def with_account(self, **fields: str) -> Chain: ...    # both targets
  ```

- Produces:
  - `Attempt.target: wire.Target`, built from the same lowered values as `Attempt.conn`.
  - `ResolvedInference.chain -> wire.Chain | None`: the sent attempt's target, with the fallback's target exactly when today's dict carries `FALLBACK_KEY`.
  - `capabilities.post_image_reach(kind: str, vision_fact: str, vision_cap: Cap) -> str`: `"yes"`, `"no"` or `"unknown"`, by `post_images.capability`'s rule.
- Produces, in `tests/wire_kit.py`: `target(**fields) -> wire.Target` and `chain(primary, fallback=None) -> wire.Chain`. Their defaults are a keyed OpenRouter target at `vendor/active`, so a test spells only what it means.

- [ ] **Step 1: Write the failing tests:**

  ```python
  # test_wire.py
  def test_a_target_never_prints_its_key():
      t = wire_kit.target(api_key="sk-test-secret")
      assert "sk-test-secret" not in repr(t) and "sk-test-secret" not in repr(wire.Chain(t))
  def test_with_account_never_shares_a_block():
      t = wire_kit.target(); u = t.with_account(decision_mode="structured")
      assert t.account.decision_mode == "" and u.account.decision_mode == "structured"
  def test_wire_imports_nothing_from_the_package(): # AST: only stdlib imports

  # test_inference_target.py -- for every baseline state, migrated (tests.inference_baseline STATES)
  @pytest.mark.parametrize("state", sorted(baseline.STATES))
  def test_every_target_mirrors_its_lowered_dict(state, tmp_path):
      for task, cid in cells:
          r = inference.resolve(task, cid)
          for a in r.attempts:
              c, t = a.conn, a.target
              assert (t.provider_id, t.kind, t.model) == (c["id"], c["kind"], llm.effective_model(c))
              assert asdict(t.sampling) == c["sampling"]
              assert t.structured == (c.get(resolve.STRUCTURED_KEY) is True)
              assert asdict(t.account) == {**asdict(wire.Account()), **c.get(resolve.ACCOUNT_KEY, {})}
              assert t.reads_images == post_images.capability(c)
              assert (t.prefill, t.post_process) == (c["prefill"], c["post_process"])
          assert (r.chain.fallback is not None) == (resolve.FALLBACK_KEY in (r.conn or {}))
  ```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `resolve._target(conn: dict, attempt_caps: dict, model_facts: dict) -> wire.Target` is called in `_attempt`.
  - `_chain` replaces each attempt with `dataclasses.replace(a, target=a.target.with_account(operation=…)…)` wherever it writes the dict's account or structured flag. One place writes both until Task 10.
- [ ] **Step 4:** Run `tests/test_wire.py tests/test_inference_target.py tests/test_inference_resolve.py tests/test_post_images.py tests/test_import_guard.py` and the gates. Expected: pass.
- [ ] **Step 5:** Commit: `feat(inference): typed targets beside the lowered connection dict`.

### Task 8: `inference.generate`, and every generation through it

This task leaves the facade unchanged: `generate` still hands `client.stream` the dict, `resolved.conn`. What moves are the call sites. They stop calling the client directly and stop reading `.conn` for anything except handing it on.

**Files:**
- Modify:
  - `backend/src/grimoire/inference.py`: `generate`, `note_outcome`.
  - `llm_reasoning.py`: `stream` takes a source factory.
  - Every generation call site from Task 1's inventory: `routes/common.py` (`draft_completion`, `_bounded_call` users, `_record_prompt` callers), `routes/streaming.py`, `routes/character_turns.py`, `routes/tracker.py`, `routes/characters.py`, `routes/scenes.py`, `routes/greetings.py`, `routes/continuity.py`, `routes/mechanics.py`, `routes/worlds.py`, `routes/campaigns.py`, `routes/passage_characters.py`, `store/usage.py`, `backend/scripts/ingest_scene.py`, `evals/runner.py`, `evals/run.py`.
- Modify tests: `tests/test_routing_guard.py`, `tests/test_usage_guard.py`, `tests/test_operation_guard.py`.

**Interfaces:**
- Produces, in `inference.py`:

  ```python
  @overload
  def generate(task: str, messages: list[dict], *, client: LLMClient, resolved: ResolvedInference,
               usage: dict | None = None, schema: dict | None = None,
               stream: Literal[True] = True) -> AsyncIterator[str]: ...
  @overload
  def generate(..., stream: Literal[False]) -> Awaitable[str]: ...
  ```

  - `ValueError` when `resolved.task != task`, `resolved.operation != "generate"`, or nothing resolved. This is raised **before** any client call.
  - `schema=` is passed through. Ruling 8: §7.2 allows it on a generate call too.

  `note_outcome(client: LLMClient, resolved: ResolvedInference, error: LLMError | None) -> None`.
- Produces: `llm_reasoning.stream(usage: dict, start: Callable[[], AsyncIterator[str]]) -> AsyncIterator[dict]`. It installs the display buffer, *then* calls `start()`.
- Call sites read display facts from `resolved.chain.primary`: `.kind`, `.model` and `.provider_id`, for `_record_prompt`, `made_by` and `turn.served`. They never read them from `.conn` again.

- [ ] **Step 1: Write the failing guard tests:**

  ```python
  # test_routing_guard.py
  def test_every_generation_goes_through_inference_generate():
      # test_usage_guard._generation_calls over backend/src, evals, backend/scripts:
      # allowed only in inference.py (decide's backend and generate) and, for
      # `single`, routes/config.py's model test.
  def test_the_generate_check_flags_a_planted_call():  # a planted `client.complete(m, conn)` in a route module is reported

  # test_operation_guard.py (the generate half, beside decide's)
  def test_every_generate_names_a_generate_task_and_passes_resolved():
      # bindings(tree, modname, "grimoire.inference") -> each `generate` call has a
      # first positional str literal whose route operation is "generate" (or a
      # registered non-route task) and a `resolved=` keyword.
  def test_generate_is_never_handed_around_as_a_value():

  # test_usage_guard.py
  def test_every_generate_call_passes_a_meters_holder():  # `usage=<meter>.usage` or `usage=holder` from a `with ...meter(...)`
  ```

- [ ] **Step 2:** Run the three guard files. Expected: the new tests FAIL, listing every call site.
- [ ] **Step 3: Implement `generate`, `note_outcome` and `llm_reasoning.stream`.** Convert each call site to the shape below. Keep the task literal, the meter and `_bounded_call` exactly where they were.

  ```python
  async for delta in operations.generate("chat", messages, client=client, resolved=resolved, usage=meter.usage):
  text = await _bounded_call(operations.generate("tagline", messages, client=client, resolved=resolved,
                                                 usage=m.usage, stream=False))
  ```

  A call site that kept only `conn` now keeps `resolved`, the `UsableInference` from `require_inference`. `routes/scenes.py:5210/5323` read token-count kinds from `inference.resolve("chat", cid).chain`.
- [ ] **Step 4:** Run the guard files plus:

  ```
  tests/test_routes.py tests/test_routing_routes.py tests/test_character_turns.py
  tests/test_scene_break_routes.py tests/test_tracker_routes.py tests/test_greetings_routes.py
  tests/test_continuity_routes.py tests/test_reasoning_display.py tests/test_draft_runs.py
  tests/test_ingest_scene.py tests/test_evals.py tests/test_format2_play.py tests/test_usage_routes.py
  tests/test_model_test_call.py tests/test_prompt_log_routes.py
  ```

  Then the gates. Expected: pass, with no assertion changed. Fakes still see `request["conn"]`.
- [ ] **Step 5:** Commit: `refactor(inference): every generation goes through inference.generate`.

### Task 9: The adapter registry, and the facade on targets

**Files:**
- Create: `backend/src/grimoire/adapters.py`, `backend/tests/test_adapter_registry.py`.
- Modify:
  - `llm.py`: every method takes `Chain`/`Target`.
  - `llm_sampling.py`: `effective(target)`, `split(target)`.
  - `llm_usage.py`: `account(usage, target)`; `with_account` removed.
  - `model_guidance.py`: `for_target(target)` replaces `for_connection(conn, model)`.
  - `health.py`: `record(target, error)`.
  - `store/post_images.py`: `images_for(target)`, `reach(target | None)`.
  - `routes/common.py` (`build_llm`'s `images` callable).
  - `inference.py`: generate and decide pass `resolved.chain`; `_with_mode` becomes `Chain.with_account`; H's native backend uses `adapters` (A3).
  - `tests/llm_fakes.py`.
- Convert, replacing dict literals with `wire_kit.target(...)` and keeping every assertion:
  - `tests/test_llm.py`, `test_llm_structured.py`, `test_llm_lifecycle.py`, `test_llm_error_status.py`, `test_llm_image_adapters.py`, `test_llm_sampling.py`;
  - `test_post_images_dispatch.py`, `test_model_guidance_dispatch.py`, `test_model_guidance.py`, `test_provider_health.py`, `test_reasoning_display.py`, `test_llm_fakes.py`;
  - and each suite reading `request["conn"]` (Task 1 inventory): `test_routing_routes.py`, `test_scene_break_routes.py`, `test_routes.py`, `test_character_turns.py`, `test_model_test_call.py`, `test_inference_override.py`, `test_format2_play.py`.

**Interfaces:**
- Produces, in `adapters.py`:

  ```python
  class Adapter(Protocol):
      kind: str
      carries_images: bool; lists_models: bool; embeds: bool; decides_natively: bool
      def generate(self, messages: list[dict], target: Target, usage: dict | None,
                   *, schema: dict | None = None) -> AsyncIterator[str]: ...
      async def models(self, target: Target) -> list[dict]: ...     # LLMError("bad_response") where not lists_models
      async def check(self, target: Target) -> None: ...
      async def decide(self, items, target: Target, usage: dict | None): ...  # H's native; LLMError("bad_response") where not decides_natively

  KINDS: tuple[str, ...] = ("openrouter", "openai_compatible", "anthropic", "claude")
  def build(*, openrouter, openai_compatible, anthropic, claude) -> dict[str, Adapter]
  TEXT_ONLY_KINDS: frozenset[str]   # derived: not carries_images
  LISTABLE_KINDS: frozenset[str]    # derived: lists_models
  ```

  Classes: `OpenRouterAdapter`, `OpenAICompatibleAdapter`, `AnthropicAdapter`, `ClaudeAgentAdapter`, each wrapping its client. The per-kind bodies of today's `LLMClient._provider`, `list_models` and `check` move into them unchanged in what they send.
- Produces, on `LLMClient`:
  - `stream(messages, chain: Chain, usage=None, *, schema=None)`;
  - `complete(…)` with the same arguments;
  - `single(messages, target: Target, usage=None)`;
  - `list_models(target)`, `check(target)`, `note_outcome(target, error)`.

  The constructor keeps its client keywords (the test seams). Its `fallback=` keyword is deleted: every call carries its chain.
- Produces:
  - `llm.fallback_sampling(primary: Target, fallback: Target) -> Target`;
  - `llm._same_route(a: Target, b: Target) -> bool`, by `provider_id`;
  - `llm.prefill_capable(target) -> bool`.

  The degrade sibling is `dataclasses.replace(target, degrade=True)`. `llm.ATTEMPTED` holds the `Target`.
- Fakes:
  - `FakeLLM.stream/complete/single(messages, chain_or_target, usage=None, *, schema=None)` record `{"messages", "chain", "target"}`.
  - `request["target"]` is the primary `Target`, and `FakeLLM.conn` becomes `FakeLLM.target`.
  - `llm_usage.account(usage, target)` stamps the holder as today.

- [ ] **Step 1: Write the failing tests:**

  ```python
  # test_adapter_registry.py
  def test_every_kind_has_one_adapter():
      assert set(adapters.build(**_fakes())) == set(adapters.KINDS) == {p.kind for p in providers.PRESETS.values()}
  def test_adapter_facts_agree_with_the_registry():
      # for every providers.PRESETS entry p and its adapter a = registry[p.kind]:
      #   not a.embeds           => "embed" in p.never
      #   not a.decides_natively => "decide_native" in p.never
      #   not a.carries_images   => "vision" in p.never
      # and each `never` entry that is true of the whole kind (the same in every preset of
      # that kind) is a False flag -- the store's adapter facts and the registry say one thing
  def test_the_registry_embed_flag_matches_the_store_endpoint_rule():
      # resolve.embed_endpoint({...kind...}, True) != "" possible <=> adapters ... embeds
  def test_a_chain_sends_what_the_lowered_dict_sent(tmp_path):
      # for every migrated baseline state and task: a recording client is driven once with
      # resolved.chain; the wire calls (model, api_key, base_url, sampling kwargs, reasoning
      # kwargs, strict, schema) equal a recording of today's dispatch over resolved.conn,
      # captured in this test from a frozen copy of the pre-Task-9 `_provider` mapping.
  ```

  The last test is the behaviour pin for the rewrite. It keeps a private copy of today's dict-to-kwargs mapping (`_legacy_kwargs(conn)`), which Task 10 deletes together with `.conn`. Task 10 then keeps the test's chain half against recorded expectations from this run, written inline as literals.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement the registry and convert the facade.**
  - `_routes(chain)` builds `[(primary, retries), (fallback_sampling(primary, fallback), 0)]`.
  - `_usable_routes` drops a fallback whose kind is in `adapters.TEXT_ONLY_KINDS` for image-bearing messages.
  - `_dispatch` uses `messages.for_target(target)` and `self._adapters[target.kind].generate(…, schema=schema if target.structured else None)`.
  - **E's `_estimate` call stays exactly where `_resilient` makes it** (ruling 11), with a comment naming the window.
  - `inference.generate`, `decide` and `note_outcome` pass `resolved.chain`, or `.chain.primary` for `note_outcome`.
  - `decide`'s mode stamp is `chain.with_account(decision_mode=mode)`.
  - **H's native backend (A3/A4):** move H's native call into `Adapter.decide` for `openrouter` and `openai_compatible` (the OpenAI preset). `inference._BACKENDS["native"]` calls `client.decide_native(items, target, usage)`, which dispatches through the registry. H's tests are converted the way the facade suites are.
- [ ] **Step 4:** Convert the test suites listed under **Files**.
  - Each dict literal like `{"kind": "openrouter", "model": "m", "api_key": "k", "sampling": {...}}` becomes `wire_kit.target(kind="openrouter", model="m", api_key="k", sampling=wire.Sampling(...))`.
  - Each `request["conn"]["model"]` becomes `request["target"].model`.
  - Each `conn[FALLBACK_KEY] = fb` becomes `wire_kit.chain(primary, fb)`.

  Do not change what any test asserts about the wire, the holder, the retry count or the fallback. Only the input spelling changes.
- [ ] **Step 5:** Run:

  ```
  tests/test_adapter_registry.py tests/test_llm.py tests/test_llm_structured.py
  tests/test_llm_lifecycle.py tests/test_llm_error_status.py tests/test_llm_image_adapters.py
  tests/test_llm_sampling.py tests/test_post_images_dispatch.py tests/test_post_images.py
  tests/test_model_guidance.py tests/test_model_guidance_dispatch.py tests/test_provider_health.py
  tests/test_reasoning_display.py tests/test_llm_fakes.py tests/test_routing_routes.py
  tests/test_scene_break_routes.py tests/test_routes.py tests/test_character_turns.py
  tests/test_model_test_call.py tests/test_inference_override.py tests/test_format2_play.py
  tests/test_inference_decide.py tests/test_inference_fallback.py tests/test_operation_guard.py
  tests/test_usage_guard.py tests/test_import_guard.py
  ```

  Add H's native suites (A3). Then the gates. Expected: pass.
- [ ] **Step 6:** Commit: `refactor(llm): the adapter registry; the facade takes chains of typed targets`.

### Task 10: Delete the connection-dict lowering

**Files:**
- Modify:
  - `store/inference/resolve.py`: delete `_lowered`, `lower`, `with_facts`, `own_sampling`, `FALLBACK_KEY`, `STRUCTURED_KEY`, `ACCOUNT_KEY`, `_account`/`_flag_structured`'s dict writes; build `Target` directly in `_attempt`.
  - `store/inference/resolved.py`: `Attempt.conn` and `ResolvedInference.conn`/`.fallback` deleted.
  - `routes/common.py`: `UsableInference.conn` becomes `.chain`, never None.
  - `store/inference/controls.py`: `preview` builds a `Target`.
  - `routes/config.py`: model test probe; `_with_effective` for the provider editor; `post_images.reach(chat.chain…)`.
  - `store/embed_space.py`: D's note. `endpoint_of` reads `attempt.target.api_key`, and its `"conn"` entry becomes `"target"`.
  - `store/inference/embed.py`: `_stamp` reads the target.
  - `llm.py`: `FALLBACK_KEY`, `STRUCTURED_KEY`, `_without_fallback`, `DEGRADE` as a dict key.
  - `llm_usage.py`: `ACCOUNT_KEY`.
  - `store/inference/settings.py`; `tests/inference_baseline.py` and `inference_baseline_c.py` (observe projects `Target` into the same JSON keys; the JSON is untouched); `tests/test_inference_target.py` (becomes target-only).
- Create: `backend/tests/test_lowering_retired_guard.py`.

**Interfaces:**
- Consumes: `wire.Target`/`Chain` (Task 7), the registry (Task 9).
- Produces:
  - `resolve.target_for(raw: dict, model: str, sampling: dict, *, model_facts: dict) -> wire.Target`, for `controls.preview` and the model test, which build a target outside any route.
  - `embed_space.endpoint()` returns `{model, base_url, key, space, provider, provider_name, provider_kind, target}`.

- [ ] **Step 1: Write the failing guard:**

  ```python
  def test_the_lowering_stays_retired():
      # AST over backend/src/grimoire, evals, backend/scripts: no name or attribute
      # FALLBACK_KEY, STRUCTURED_KEY, ACCOUNT_KEY, with_facts, own_sampling, _lowered,
      # `lower` imported from store.inference.resolve, or attribute `.conn` on a
      # ResolvedInference/UsableInference/Attempt binding (`require_inference(...)`,
      # `override_inference(...)[0]`, `inference.resolve(...)`, `.attempts[i]`), and
      # no str constant "_fallback", "_structured" or "_account".
  def test_the_guard_flags_a_planted_lowering():
  ```

- [ ] **Step 2:** Run. Expected: FAIL, listing what remains.
- [ ] **Step 3:** Delete and convert until the guard passes.
  - `test_a_chain_sends_what_the_lowered_dict_sent` loses its `_legacy_kwargs` half and keeps literal expectations (Task 9, Step 1).
  - The equivalence helpers' `_resolved`/`_lowered` read `Target` fields: `provider_id` becomes `conn`, plus `model`, `asdict(sampling)`, `model_params`, `prefill`, `post_process`, `reads_images` and `llm_sampling.effective(target)["effective"]`.
  - The frozen JSON cell `vision` (a legacy field) is compared as `facts.of(...)`'s `vision`. That is what the lowering put there at format 2 (`with_facts`), so the observation is the same value.
- [ ] **Step 4:** Run:

  ```
  tests/test_lowering_retired_guard.py tests/test_inference_*.py tests/test_embed_space_role.py
  tests/test_context_semantic.py tests/test_semsearch_store.py tests/test_continuity_similarity.py
  tests/test_model_test_call.py tests/test_provider_api.py tests/test_format2_play.py
  tests/test_adapter_registry.py tests/test_routing_guard.py tests/test_import_guard.py
  tests/test_frozen_campaign.py
  ```

  Then the gates, with `make baseline PY=$PY` if counts shrank. Expected: pass.
- [ ] **Step 5:** Commit: `refactor(inference): delete the connection-dict lowering, FALLBACK_KEY and STRUCTURED_KEY`.

### Task 11: Strict-mode schema limits (only if A6 found none)

**Files:** Modify `backend/src/grimoire/decisions.py`; Test `backend/tests/test_decisions.py`.

**Interfaces:**
- Produces:
  - `decisions.MAX_ENUM_CHARS = 15_000` and `decisions.ENUM_CHARS_ABOVE = 250`: the enum-string budget applies when a chunk's schema has more than 250 enum values.
  - `decisions.MAX_SCHEMA_CHARS = 120_000`: property names, definition names and enum values in total.
  - `validate` refuses one item that alone crosses either budget (`DecideRequestError`). `chunks` splits a batch before a chunk crosses either.

- [ ] **Step 1: Write the failing tests:**

  ```python
  def test_an_item_past_the_enum_string_budget_is_refused():   # 251 options of 60-char ids -> DecideRequestError naming MAX_ENUM_CHARS
  def test_the_enum_string_budget_needs_more_than_250_values(): # 250 options of 70-char ids -> valid
  def test_an_item_past_the_schema_budget_is_refused():
  def test_chunks_split_before_a_budget_is_crossed():           # 8 items each just under half -> chunks of 1
  ```

- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement by counting the same JSON Schema `decisions.schema` emits. **Step 4:** Run `tests/test_decisions.py tests/test_inference_decide.py` and the gates. Expected: pass.
- [ ] **Step 5:** Commit: `feat(decisions): bound strict mode's enum-string and schema-size limits`.

### Task 12: Docs, and the stand-in gates

**Files:** Modify `CLAUDE.md`, `CONTRIBUTING.md`, `AGENTS.md` (only if it names a retired symbol), `docs/store-guarantees.md`, `backend/src/grimoire/store/inference/__init__.py`'s docstring, `evals/README.md` (if it names `.conn`).

- [ ] **Step 1: CLAUDE.md.**
  - Rewrite "Adding an LLM call site?": `require_inference(<task>, cid, operation=…)`, then `operations.generate(<task>, messages, client=…, resolved=…, usage=m.usage)`. The facade takes `resolved.chain`. Format 2 only, with the migration as the one legacy reader. Delete every mention of `translate`, `FALLBACK_KEY` and the "until the facade stops needing a connection" sentence.
  - In "Working notes", the suite is born an upgraded default library at format 2. Delete "So the older play suites run at format 1".
  - "Model settings moved…": play at format 1 answers 409 `not_migrated` until the migration lands, and the migration retries in the background. Retirement derives presets and deletes legacy keys.
- [ ] **Step 2: CONTRIBUTING.md.** Add rows for the new guards and markers: `test_legacy_reader_guard.py`, `test_lowering_retired_guard.py`, the generate halves of the routing, operation and usage guards, and `test_adapter_registry.py`.
- [ ] **Step 3: docs/store-guarantees.md**, "Model settings migration":
  - Replace "Until that write lands the resolver reads the legacy settings through the translation" with the format-1 refusal and the retry.
  - Add "### Retirement". It covers ruling 6's order; that nothing is deleted before it is derived; that `rev` is kept; and that no new archive is taken, so the C-era archive is the way back.
  - Rewrite "Older builds keep the old settings, frozen" as "…until retirement deletes them".
  - In "What is not promised", add E's cancel window (ruling 11) and "a pre-C build on a retired library sees no model settings".
- [ ] **Step 4:** Run `tests/test_docs_guard.py tests/test_install_scripts.py` and `make check-templates PY=$PY`. Expected: pass.
- [ ] **Step 5:** Commit: `docs: inference slice I -- retirement, generate, the registry`.
- [ ] **Step 6: Stand-in gates.** These are controller-run, not this task's code:
  - (a) A whole-branch review standing in for `/codex:review`.
  - (b) A final spec-conformance review against §14 row I, §11.4, §11.2 step 4, §7.2 and §13, asking whether every bullet of row I is implemented: derived presets, the deleted keys and fields, the deleted translation layer, the registry with `inference.generate`, and the deleted lowering with `FALLBACK_KEY` and `STRUCTURED_KEY`.

  Findings are fixed before the PR. Codex reviews the PR. Merging waits for CI green.

---

## Self-review notes (for the plan reviewer)

- **Coverage of §14 row I and §11.4:**
  - Derived presets: Task 4.
  - Legacy keys and fields deleted: Task 6.
  - The translation layer deleted: Task 5 deletes the runtime reader. The mapping is kept as the migration's input, and a guard keeps it there (Task 6).
  - Format-1 migrated first, backup included: Tasks 4 and 5, and the guarantees section.
  - The adapter registry with `inference.generate`: Tasks 8 and 9.
  - Lowering, `FALLBACK_KEY` and `STRUCTURED_KEY` deleted: Task 10.
- **Carried notes:**
  - D's (`resolve.embedding` on translate): Task 5.
  - D's (`embed_space` key from the lowered dict): Task 10.
  - E's cancel window: ruling 11, Tasks 9 and 12.
  - F's (`complete(schema=)` in place of `generate`): Tasks 8 and 9.
  - F's (delete `FALLBACK_KEY`/`STRUCTURED_KEY`): Task 10.
  - H's strict-mode limits: Task 11, conditional.
- **The order keeps the tree green.**
  - The suite moves to format 2 while the translation still proves the move changed nothing (Task 3).
  - Derivation lands before the legacy effort stops being read (Tasks 4 and 5).
  - Nothing reads the legacy layout before it is deleted (Tasks 5 and 6).
  - Targets exist before anything consumes them (Task 7).
  - Call sites move before the facade's types change (Tasks 8 and 9).
  - The dict is deleted only when its guard finds nothing left (Task 10).
