# Inference slice I — Retirement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 2.** This revision answers the round-1 adversarial plan review (`.superpowers/sdd/plan-reviews/slice-i-plan-review.md`, verdict "No"). Every finding is answered under "Rulings": the controller's twelve rulings first, then the review's other findings, then the plan's own rulings as amended. A second review follows.

**Goal:** Retire the legacy settings layout and the connection-dict lowering, with no change a user can see except those listed under "Needs user ratification before Task 2". The legacy GLM reasoning effort becomes derived presets. The legacy keys and connection fields are deleted from format-2 stores. The translation layer is deleted as a second runtime read path. Every generation goes through `inference.generate` and an adapter registry that takes typed targets instead of connection dicts, and `FALLBACK_KEY` and `STRUCTURED_KEY` are deleted with the lowering.

**Architecture:**

- **One reader of the legacy layout: a shared planner, used in memory by play and persisted by the migration.**
  - `store/inference/legacy_plan.py` is a new leaf module. It holds the legacy-to-new mapping (moved verbatim from `translate.global_view`/`campaign_view`/`embedding_view`), the model-facts overlay (moved from `migrate._stated`), the derived reasoning presets, and the notes for what cannot be carried over.
  - `migrate` and `retire` persist what it plans. `resolve` makes exactly one call into it, `legacy_plan.overlay(cfg, meta)`, and resolves the result as format 2, in memory. That call applies to a store below format 2, to an unmarked campaign at format 2, and to any scope not yet retired. Nothing is written on the play path, and play is never refused.
  - `translate.py` is deleted, together with every format-1 branch in `resolve`, `settings` and `in_use`, and the legacy GLM read in `llm_sampling`. `test_planned_equals_persisted` holds the in-memory answer equal to what the migration and retirement write. A guard holds the planner to being the only reader (rulings 1–2).
- **Retirement is the last stage of `migrate.ensure`.** It runs after the marker, in the same run, on every store at format 2. Its order is ruling 6:
  0. a `pre-retirement-` archive, unless this same run already took the `pre-inference-` one;
  1. one global write: the derived presets, each global slot repointed, the legacy config keys deleted, the retirement marker;
  2. each campaign: its marker checked, then the same in one write;
  3. once every campaign is readable, marked and retired: each connection's legacy fields recorded in the retirement record, then stripped, keeping every `rev`.

  Every writer fails closed on a file it cannot read. Retirement is idempotent, deterministic and resumable, and it never moves the migration's `state`.
- **The lowering is replaced by typed targets.** `grimoire/wire.py` is a new gateway leaf, standard library only. It holds `Target`, one attempt as an adapter sends it, and `Chain`, a primary target and its optional fallback. The resolver builds both, and `ResolvedInference.chain` replaces `.conn`.
  - `grimoire/adapters.py` is the per-kind registry, with `generate`, `models`, `check`, native `decide` (from slice H) and capability flags.
  - `LLMClient` dispatches through the registry and keeps its retries, fallback, idle bound, image lowering, prefill tails and usage stamping.
  - `inference.generate` is the only door to generation. `inference.decide` (H's `stages`/`run_stages`) and `inference.generate` hand the facade a `Chain`.
  - The fallback rides on `Chain.fallback`, not under `FALLBACK_KEY`. Structured mode is `Target.structured`, not `STRUCTURED_KEY`. The account block is `Target.account`, not `ACCOUNT_KEY`.
- **The suite moves to format 2 first.** Task 3 makes each test's store an upgraded default library at format 2, while the translation still exists to show that the conversion changed nothing. Only after that do Tasks 5–10 delete things.

**Tech Stack:** Python ≥3.11, FastAPI, httpx, pytest; the React/vitest frontend, for the retired-notes notice, the `max` choice (if ratified) and some type cleanup.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`. Slice I is §14 row I ("User-visible: No"). It draws on §11 (all of it), §5.4, §5.6, §7.1–§7.2, §8, §9.3, §10, §12, §13, §14.1, §15 "Migration" and §17. Task 2 folds this plan's rulings, as the user ratified them, into the spec.

**Base:** `claude/inference-slice-i` at `b2c85c6`. That is an F-era commit, not F's head: F's head is `e6c4d12`. D (`877e964`), E (`eb415d4`), F, G and H are separate branches off `main`, and none of them is an ancestor of `b2c85c6`. **Every A9 name lives only on those branches until they are integrated** (M1). Task 1 checks the assumptions only after rebasing onto the integration branch the controller names, never against the un-integrated base. Slice I lands last, after D, E, F, G and H.

---

## Needs user ratification before Task 2

Row I says "User-visible: No". Each item below is a user-visible change, or a departure from the spec's text, that this plan could not remove. **The controller asks the user about every item before Task 2 starts.** Task 2 folds each answer into the spec. An item the user declines takes the fallback named beside it.

1. **GLM `max` becomes a preset reasoning effort** (CR5; review Q3, I6 item 1).
   - What changes: the preset editor's Reasoning choice gains `max`. It is sent only to GLM models on `openai_compatible`; on every other adapter it is "unsupported" and nothing is sent. A GLM selection whose connection is at `max` is repointed to a derived "… · reasoning max" preset, so its wire does not change.
   - Side effect on other devices: a C–H build on the same synced library reads a `max` preset as invalid and sends no effort.
   - Fallback if declined: `max` is not added. A GLM connection at `max` stops sending it on every task once this build runs. The loss is reported in the durable notice (item 7), and `glm_max_under_route_preset` carries an exact named difference (Task 5).
2. **Derived presets start from the slot's own preset** (ruling 4; review M4). §11.2 step 4 says "its own sampler preset" (the connection's legacy `sampler_preset`). This plan derives from the preset the role, fallback or pin selects, which is the only reading that keeps the wire when a user repointed a role's preset after C.
   - Visible: new presets named "<preset> · reasoning <effort>" (or "Reasoning <effort>") appear in the Presets list, and each GLM selection shows its derived preset.
   - Visible: editing the base preset (for example "Warm") no longer reaches those selections, which now point at the frozen copy (review I6 item 4).
   - Visible: the Presets editor's "Preview on…" a GLM provider shows only what the preset itself sets; it no longer adds the provider's legacy effort, which no selection reads any more.
3. **A route-level preset that sets no reasoning effort, over a GLM connection with a legacy effort, stops sending that effort on that route** (review I6 item 2). No form keeps the wire whole. A route preset is shared by the route's primary and its fallback (§5.2), so a derived route preset would start sending reasoning to a fallback that never sent it, and leaving it alone drops the primary's effort. The plan leaves the route preset alone, which keeps every fallback exactly as it was. The loss is reported in the durable notice (item 7).
4. **A reroll's override preset that sets no reasoning effort stops inheriting the GLM connection's legacy effort** (review I6 item 3). An override is not stored, so no notice is written.
5. **Older builds on a synced library after retirement** (review I6 item 5; §11.3).
   - A pre-C build sees no model settings: its connections have no `model`, so it sends requests with an empty model.
   - A C–H build keeps playing. It loses only what item 1 names. It writes empty legacy fields back on each connection edit and `active_connection_id` on each start, which this build ignores and removes again on its next start.
6. **A second never-pruned archive, `pre-retirement-<stamp>.zip`** (CR4; review I5, Q2). It is listed in Backups beside `pre-inference-` and is never deleted by retention. It is a full-library archive, like `pre-inference-`. The review's smaller option (only `config.md`, each `campaign.md` and `llm_connections/*.md`) is the fallback if the user prefers it, for example for Android storage.
7. **A durable "not carried over" notice on the `/models` page** (CR5 fallback; review I6). It is stored in the library (`inference-retired.json`), so every device shows it until the user dismisses it. It lists what items 1 (if declined) and 3 lost, worded as permanent.
8. **§13's per-adapter `embed` is dropped** (ruling 10; review M6). Embedding stays D's caller-owned `EmbeddingsClient` door, because the store must not import the gateway (#239). The registry carries an `embeds` flag held equal to the store's rule by a test.
9. **§7.2's `generate` signature** (ruling 8): `generate(task, messages, *, client, resolved, usage=None, schema=None, stream=True)`. It takes the call site's resolution, as `decide` does, rather than `cid` and `override`.
10. **How §11.4's "delete the translation layer" is read** (CR1; review Q1). The second runtime read path is deleted: `translate.py`, every format-1 branch of the resolver and the legacy GLM read. The mapping itself lives on in one guarded planner, which the migration persists and play uses in memory. That is what keeps §11.2, §12, §5.6, §15 and §17.8 true.
11. **New storage the spec does not name** (rulings 15 and 16): the retirement marker `inference_retired: "1"` in `config.md` and each `campaign.md`, and the retirement record `<home>/inference-retired.json`. Neither is visible on its own. They are listed because they are spec additions.

The plan removed these user-visible changes from round 1 (CR1): 409 `not_migrated` on play, `ready: False` on a format-1 store, the "Play resumes once it has finished" banner sentence, the migration retry loop, retirement's effect on the migration status line, and the `skipped` channel for losses.

---

## Global Constraints

**The user's rules:**

- **Never run the full suite locally, and never `make check` or `make check-py`.**
  - Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates`.
  - A task that touches `frontend/` also runs `make check-eslint` and its named vitest files.
- **CI is the full run, at five checkpoints** (CR9). CI's workflow has `workflow_dispatch`, so the branch runs it without a PR.
  - After **Task 3** (3d), **Task 5**, **Task 6** (6b), **Task 9** (9d) and **Task 10**, the controller runs `git push origin claude/inference-slice-i`, then `gh workflow run ci.yml --ref claude/inference-slice-i`, then `gh run watch <id> --exit-status`.
  - **The next task does not start until that run is green.** A red run is fixed inside the task it belongs to, then pushed and run again.
  - Do not push again while a run is live: the workflow's concurrency group cancels the earlier run on the same ref.
  - **Merging waits for CI to be green.**
- **No live LLM calls.** Never run `evals/run.py --live` or `--record` without the user's explicit approval, relayed by the controller. Every fake goes through `backend/tests/llm_fakes.py`.
- **Review gates are Claude stand-ins.**
  - Until the PR, a Claude stand-in review serves as each Codex gate: the plan's adversarial review, each task's review, the final `/codex:review` and the final spec-conformance review.
  - Codex reviews the PR itself.
- **Invented names only.**
  - Use Seraphine, Mara, Winifred, Realm, Saltmarch, Rowan and Tobin, and ids like `glm`, `glm2`, `spare`, `nokey`. Preset names like "Warm" and "Cold".
  - Fake keys look like `sk-test-…`.
  - No figure measured from a real library goes anywhere.
- **Docs move with the code** (CR10). A task that changes a claim `CLAUDE.md`, `CONTRIBUTING.md` or `docs/store-guarantees.md` makes updates that document in the same commit. A task that adds a `*_guard.py` adds its `CONTRIBUTING.md` row. Either way, the task runs `tests/test_docs_guard.py`. Task 12 only sweeps what is left.
- **Ratified departures only.** Nothing under "Needs user ratification" is implemented in a form the user did not approve. Task 4 Step 5 is the one step that is conditional on an answer.

**Spec values that bind here (verbatim):**

- `inference_format` is `2` (`inference_keys.CURRENT_FORMAT`). The legacy config keys are `active_connection_id`, `fallback_connection_id`, `route_*`, `embeddings_connection_id` and `embeddings_model`. The legacy connection fields are `model`, `post_process`, `reasoning_effort`, `sampler_preset`, `vision` and `prefill` (`llm_connections.MODEL_FIELDS`).
- Derived presets are made for **`openai_compatible` GLM connections only, and only for values a preset can represent**. The params are the base preset's params plus that effort. The name is `"<preset name> · reasoning <effort>"`, or `"Reasoning <effort>"` when there was no preset. The id is deterministic (`slugify` of that name). Identical derivations collapse to one.
- GLM models are `llm_reasoning.GLM_MODELS` (`glm-5.3`, `glm-5.3-flash`). GLM efforts are `low`, `high` and `max`. Preset efforts are `llm_sampling.REASONING`: `off`, `low`, `medium` and `high`, plus `max` if item 1 is ratified. **Representable** is their intersection.
- "While the migration is `pending` or `failed`, play resolves through the translation" (§11.2). In this plan that means through the planner, in memory.
- "A store still at format 1 at that point is migrated first, backup included" (§11.4).
- `space_id` stays exactly `f"{provider_id}\0{rev}\0{model}"`. Every `rev` survives retirement (`keep_rev` writes).
- Keys never reach logs, captures, diagnostics or API responses (§9.4). `Target.api_key` is `repr=False`.

**Fixtures that are never regenerated:**

- `backend/tests/fixtures/inference_baseline.json` and `inference_baseline_c.json`.
- `backend/tests/fixtures/frozen_campaign/home/`.
- `test_lore_golden`'s golden.

Only `frozen_campaign/snapshot.json` may be regenerated, deliberately and with the reviewed diff (CLAUDE.md, "The frozen campaign"). Under ruling 1 it is not expected to move (Task 5 Step 4).

**CLAUDE.md rules this slice touches:**

- **Imports** are at module scope and acyclic (`test_import_guard.py`).
  - `legacy_plan.py` imports `inference_keys`, `routing`, `llm_reasoning`, `llm_sampling` (for `REASONING`), `paths`, `sampler_presets`, `llm_connections` and, from Task 6b, `retired`. It never imports `migrate`, `resolve`, `retire`, `facts` or `embed_space`, so `resolve → legacy_plan` adds no cycle. That is why the planner does not call `embed_space.resolve` (Task 4, `legacy_embeds`), and why the facts overlay is values: the writes stay in `migrate._facts`.
  - `wire.py` imports only the standard library.
  - `adapters.py` imports the provider clients, `wire`, `llm_sampling`, `llm_reasoning`, `llm_usage`, `llm_errors` and `decisions`, and never the store.
  - The store never imports `adapters` or `llm`.
  - Route modules bind the operation module as `from .. import inference as operations` (slice F's rule).
- **Routing guard:** a call site resolves with `require_inference(<literal>, cid, operation=…)`. Task 8 adds a rule: **a generation is `operations.generate(<literal>, …)`**. `client.stream`/`complete` appear only in `inference.py`. `client.single` appears only in `routes/config.py`'s model test, and `client.decide_native` only in `inference.py` and that model test (H's probe).
- **Lock domain:** `retire.py` is classified in `store/locks.py` beside `migrate`. Its campaign step takes `campaign_lock_nowait(cid)`. `legacy_plan.py` and `retired.py` mutate nothing campaign-scoped. Each is classified only if the guard asks, `OUTSIDE_DOMAIN`, with that reason.
- **Atomic writes:** every write goes through `store.atomic` (`test_atomic_guard.py`).
- **Lint gates are ratcheted:** when a count shrinks, run `make baseline PY=$PY` and commit the smaller file.
- **Linear history:** rebase, then `--ff-only`.

**Commands.** The worktree has no venv of its own, so pass the main checkout's:

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> … -q`
- Frontend: `cd frontend && npx vitest run <file> …`. Run vitest from `frontend/` (CLAUDE.md).
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`, `make check-eslint`.

**How the plan guarantees that retirement loses no user data and refuses no play:**

1. **Play is never refused, and plays identically** (ruling 1).
   - A format-1 store, an unmarked campaign and any unretired scope resolve in memory through the planner, and nothing is written on the play path.
   - `test_resolution_matches_the_baseline` keeps pinning how a format-1 store plays, against the frozen JSON (Task 5).
   - `test_planned_equals_persisted` holds the in-memory answer equal to what the migration and retirement write (Tasks 5 and 6).
2. **The mapping survives in one place.** Only the second runtime read path is deleted. The mapping lives in `legacy_plan.py`, and a guard fails any other reader (Task 5).
3. **Format 1 always migrates first, backup included.**
   - Retirement runs only inside `migrate.ensure`, and only on a `config.md` that is already current.
   - If the migration's backup fails, nothing is written and retirement never starts.
4. **No archive, no write.** Retirement takes a `pre-retirement-` archive before its first write, unless this same run already took `pre-inference-`. If the archive fails, retirement writes nothing: no preset, no repoint, no deletion (Task 6, `test_a_failed_retirement_archive_writes_nothing`). Play is unaffected, because the planner serves in memory.
5. **Derive before delete; migrate before retire.**
   - A scope's legacy keys go in the same write that repoints that scope.
   - A campaign is retired only after its marker is re-read inside the hold. An unmarked campaign is migrated in that same write first. A newer one is skipped (Task 6, C2).
   - Connection fields are stripped only once `config.md` and every campaign are readable, marked and retired, and no connection is unreadable (C3).
   - Each connection's fields are written to the retirement record before the strip, so a campaign that arrives unmarked later still resolves.
6. **Fail closed** (ruling 6; CR3). Every new writer reads strictly. A file that exists and cannot be read, or whose frontmatter does not parse, or that lacks its expected marker or `kind`, raises `frontmatter.UnreadableError`. That item is not retired and never rewritten. The status reader is fail-soft and never raises.
7. **Nothing is lost silently.** What cannot be carried over is a note in the retirement record, shown on `/models` until dismissed, on every device (ruling 5). It is never only in `skipped`.
8. **The legacy state stays restorable** from the `pre-retirement-` archive, taken on the day of retirement (item 4 above).
9. **Proven on the old trees.**
   - Both frozen baselines are resolved in memory and after migration and retirement, and must match the JSON except for exact named differences (Task 5).
   - A copy of the frozen campaign's `home/` plays a turn in memory, then migrated and retired (Tasks 5 and 6). `home/` itself is never touched.

---

## Assumptions to re-check in Task 1

G and H are planned in parallel with this plan. This section was rewritten against their revised plans (`claude/inference-slice-g` at `fe38689` and `claude/inference-slice-h` at `fcf0f8a`) (CR8). Each line is what this plan assumes they deliver, together with how Task 1 checks it. **If one fails, Task 1 stops and reports. It does not improvise.**

- **A1 (G).** `continuity` is `operation="decide", default_role="decision"` (G Task 5). Absorb's identity phase and the reconcile sweep resolve with `require_inference("continuity-identity"|"continuity-reconcile", cid, operation="decide")` inside the thunk `_soft_resolved` takes, then call `operations.decide("<task>", items, client=client, resolved=resolved, explain=…, campaign=…, around=…)`. The task literal is on the line after `operations.decide(`.
  - Check: `grep -n '"continuity"' backend/src/grimoire/store/routing.py`.
  - Check (M2): `grep -rn -A1 "operations.decide(" backend/src/grimoire | grep continuity` prints both tasks.
- **A2 (G).** G adds no `client.complete`/`client.stream` call site and no reader of a legacy key or connection field. It adds two `.conn` readers: `_noting(client, resolved.conn, holder)` in the identity and reconcile `around` hooks. Task 8 converts both to `resolved.chain.primary`, and Task 10's guard would catch either if missed.
  - Check: `grep -rn "_noting(client, resolved.conn" backend/src/grimoire`. Record the files.
- **A3 (H).** The native backend's shapes (H Tasks 2, 4):
  - `LLMClient.decide_native(item: decisions.Item, conn: dict, usage=None, *, retries=None) -> decisions.ItemResult`. One item and a `retries` keyword. It calls `_without_fallback(conn)` itself, refuses a kind with no native adapter and an item with a `native_gap` (`code="native_unrepresentable"`), sends no sampling and installs no `Estimate`.
  - `llm.native_body(item, conn) -> dict` (pure, module level) and `llm.NATIVE_REJECTED_STATUSES`.
  - `LLMClient.complete(..., retries=None)`, and `FakeLLM.complete` records `retries`.
  - `inference.Stage(mode, conn, retries)`, a pure `stages(resolved)` that copies a conn "without `FALLBACK_KEY`" and never pops, `inference.run_stages(task, items, chain, *, client, resolved, …)` (public, for `--live`), `_Call.conn`/`.retries`, `_native(items, call)`, and `NATIVE_CONCURRENCY`.
  - The structured stage carries `FALLBACK_KEY` only when the resolver attached a structured fallback (H ruling 6). H's coordination note (I11) hands I both of those dependencies on `FALLBACK_KEY` to re-express as stage data.
  - Check: `grep -n "def decide_native\|def native_body\|NATIVE_REJECTED_STATUSES\|retries: int | None" backend/src/grimoire/llm.py` and `grep -n "class Stage\|def stages\|def run_stages\|class _Call\|NATIVE_CONCURRENCY" backend/src/grimoire/inference.py`.
- **A4 (H).** The native stamp is `llm_usage.with_account(call.conn, decision_mode="native")` in `_native`. H's model-test probe in `routes/config.py` is `client.decide_native(probes.PROBE_ITEM, llm_usage.with_account(conn, operation="decide", decision_mode="native"), m.usage, retries=0)` under `store.usage.meter("model-test")`. `test_usage_guard._GENERATORS` holds `"decide_native"`.
  - Check: `grep -rn "decide_native(" backend/src/grimoire` and `grep -n "_GENERATORS" backend/tests/test_usage_guard.py`.
- **A5 (H).** `resolve.OPERATION_CAPABILITY["decide"] == ("decide_native", "generate")`, `resolve.generates(attempt)`, `resolve.native_only(caps)`, and `Attempt.decision_mode` that can be `"native"`. F's skip (`skipped`, `skip_text`) is deleted. A native attempt carries `controls=llm_sampling.not_applicable(conn, WHY_NATIVE)`, and `controls.preview(preset_id, conn, model, operation="")` answers `n/a` for a native-only decide model (H Task 9).
  - Check: `grep -n "OPERATION_CAPABILITY\|def generates\|def native_only\|not_applicable" backend/src/grimoire/store/inference/resolve.py backend/src/grimoire/llm_sampling.py backend/src/grimoire/store/inference/controls.py`.
- **A6 (H).** H bounds strict mode's string limits: `decisions.MAX_ENUM_STRING_CHARS`, `ENUM_STRING_CHARS_ABOVE`, `MAX_SCHEMA_STRING_CHARS` and `schema_chars` (H Task 1, ruling 14). **I adds none** (CR12).
  - Check: `grep -n "MAX_ENUM_STRING_CHARS\|MAX_SCHEMA_STRING_CHARS\|def schema_chars" backend/src/grimoire/decisions.py`. Absent: stop and report. Do not add them here.
- **A7 (H).** `inference.Capture = Callable[[messages, outcome, conn], Awaitable[None]]`, called once per backend call after it settles. For a native item the messages are `json.dumps(llm.native_body(item, conn))`, and the conn is the stage's account-stamped copy with `sampling` removed. The speaker's capture is `run_in_threadpool(_capture, cid, sid, "response-selector", msgs, conn, outcome)`, with `routes/character_turns._capture(cid, sid, task, messages, conn, outcome=None)`.
  - Check: `grep -n "^Capture\|Capture =" backend/src/grimoire/inference.py` and `grep -n "def _capture" backend/src/grimoire/routes/character_turns.py`.
- **A8 (H).** `evals/runner.resolve_connections` returns `dict[str, dict | ResolvedInference]`: the whole resolution for a decide case, and `.conn` for a generate case. `live(case, target, record=False, *, client=None, backend="chain")` runs a decide case through `inference.run_stages` with a chain built from `stages(resolved)`, or a forced one-stage `native`/`structured` chain built from `A0.conn` without `FALLBACK_KEY`. `--provider/--model` resolve through `override_inference`. A generate case still calls the client with `.conn`, and Task 8 converts it.
  - Check: `grep -n "def resolve_connections\|def live\|run_stages\|Stage(" evals/runner.py`.
- **A9 (D/E/F, landed on their branches).** These names exist after integration (M1):
  - D: `store/inference/embed.py` (`embed_sync`, `embed`, `_stamp`); `resolve.embedding`, `resolve.embed_attempt`, `resolve.embed_endpoint(conn, current)`; `translate.embedding_view`; `embed_space.endpoint_of` (reads `conn["api_key"]`); `embed_space.moved_by`; `facts.FactsUnreadableError`.
  - E: `llm_usage.ACCOUNT_KEY`, `ACCOUNT_FIELDS == ("operation", "role", "billing", "decision_mode")`, `with_account`, `account`; the holder field `requested_model`; `llm._estimate` with `COUNT_TIMEOUT_S`; `store/inference/in_use.py`.
  - F: `resolve.FALLBACK_KEY`/`STRUCTURED_KEY`/`ACCOUNT_KEY`, `llm.FALLBACK_KEY` (`"_fallback"`)/`STRUCTURED_KEY` (`"_structured"`), `llm.DEGRADE` (`"_degrade"`), `llm.ATTEMPTED`, `inference._with_mode`, `inference._BACKENDS`.
  - Check each with one `grep -n`. A rename is followed silently. A missing name stops the task.
- **A10 (H).** H adds no new private connection-dict key. `NONE_KEY` (`"<none>"`) is a distribution key in `decisions`, not a conn key. H's `stages` uses `_without_fallback`, which Task 10 deletes and its guard catches.
- **A11 (H ruling 21).** H leaves OpenRouter's `provider.require_parameters` to I. **I declines it.** It is a wire change, and row I is "User-visible: No". Task 1's report hands it back to the controller as an open item for a later slice.

---

## Review Focus

1. **A library that skipped C–H, opened for the first time by this build.** It is at format 1. It plays immediately, in memory, exactly as the frozen baselines recorded (with the named differences only), and nothing is written on the play path. The background migration and retirement then persist the same answer.
   - Task 5: `test_a_format_1_store_plays_in_memory_and_writes_nothing`, `test_resolution_matches_the_baseline` (kept), `test_planned_equals_persisted`.
   - Task 6: `test_planned_equals_persisted` again, now through retirement; `test_a_format_1_store_retires_under_its_pre_inference_archive`.
2. **A GLM connection whose legacy effort is `low` or `high` (and `max`, if item 1 is ratified)**, used as Primary with its own preset, as a role fallback with no preset, and as a campaign pin with a dangling preset id. In memory, and after retirement, the wire carries the same `reasoning_effort` on every one of them. A route-level preset over such a connection (and `max`, if item 1 is declined) is noted durably, never dropped silently.
   - Task 4: `test_derived_presets_keep_the_wire_in_memory`, `test_a_route_preset_over_a_glm_effort_is_noted`, and `test_max_is_derived` or `test_max_is_noted_not_derived`.
   - Task 6: `test_derived_presets_keep_the_wire`, `test_retired_notes_are_durable_and_dismissable`.
3. **An unmarked campaign at format 2**: skipped as busy, created by an older build, or imported from an old bundle after the strip. Its legacy overrides resolve in memory, including while another writer holds its lock, and nothing is written. Retirement migrates it first, in the same write, and the strip waits for it.
   - Task 5: `test_an_unmarked_campaign_resolves_its_overrides_in_memory`, `test_a_busy_unmarked_campaign_still_resolves_and_nothing_is_written`.
   - Task 6: `test_a_campaign_skipped_by_step_8_is_migrated_before_it_is_retired`, `test_a_newer_campaign_is_never_retired`, `test_an_unreadable_campaign_holds_the_strip`, `test_the_strip_records_the_fields_first_and_the_planner_reads_them`.
4. **A file a writer cannot read** (a zero-byte sync placeholder, a truncated download, a sync client holding it). It is never rewritten, never treated as done, and the status never raises.
   - Task 6: `test_retire_global_refuses_a_config_it_cannot_parse`, `test_retire_campaign_refuses_an_unparseable_campaign`, `test_strip_model_fields_refuses_an_unparseable_connection`, `test_status_never_raises_on_an_unreadable_connection`.
5. **The fallback, structured mode, the native chain, the degrade sibling and image-bearing messages through `Chain`.** The facade sends what it sent before:
   - a route preset follows onto the fallback;
   - a text-only fallback is dropped for image-bearing messages;
   - structured mode goes only on the flagged attempt;
   - H's stages keep their attach rule;
   - the post-image degrade sibling still fires;
   - E's estimate still runs where it ran.

   Tests: `test_a_chain_sends_what_the_lowered_dict_sent` (9a); Task 9's converted facade suites keep every assertion; `test_format2_play` passes in Tasks 8–10.
6. **A synced library where an older build writes legacy keys or fields after retirement.** The resolver never reads them (the retirement marker stops the in-memory derivation), the next `ensure` deletes them again, and nothing raises.
   - Task 6: `test_legacy_keys_written_after_retirement_are_ignored_then_removed`, `test_a_connection_edit_after_retirement_writes_no_legacy_field`.

---

## Rulings

### Controller rulings on the round-1 review

- **CR1 (C1, I3, Q1).** Play is never refused. A format-1 store, an unmarked campaign at format 2 and (so that the derived presets apply before retirement persists them) any unretired scope resolve in memory through one guarded planner, `legacy_plan.py`, which includes the facts overlay and the derived presets. The format-1 baseline comparison is kept. `test_planned_equals_persisted` is added. Ruling 3's write on the play path is gone. This honours §11.2, §12, §5.6, §15, §17.8 and row I.
- **CR2 (C2).** `retire_campaign` re-reads the campaign's marker inside its hold. An unmarked campaign is migrated in the same write before any key is deleted. A newer one is skipped.
- **CR3 (C3, I4).** Every new writer fails closed through `frontmatter.UnreadableError`, slice D's `FactsUnreadableError` pattern. An unreadable campaign, connection or `config.md` stops retirement for that item and is never treated as finished or overwritten. The strip runs only once every campaign is verified migrated and retired.
- **CR4 (I5, Q2).** A `pre-retirement-` archive is taken before retirement's first write. It is never pruned, taken once per root, reused on resume, and skipped only when this same run took `pre-inference-`. No archive, no write.
- **CR5 (Q3, I6).** `max` joins `REASONING` as a GLM-only value, unsupported elsewhere: **needs user approval** (ratification item 1; Task 4 Step 5). The fallback beside it accepts the loss and reports it in the durable, library-stored notice, never through `skipped`.
- **CR6 (Q4, M7).** The birth seam is public (`config.birth_fields()`), and the test birth comes from `GRIMOIRE_TEST_BIRTH`, set at conftest import beside `GRIMOIRE_INFERENCE_AUTOMIGRATE`.
- **CR7 (Q5).** Task 9 is split four ways: 9a the registry; 9b the facade behind a stdlib-only shim (`wire.from_lowered`); 9c H's native chain on targets; 9d the fakes and suites, with the shim deleted. Task 10's guard names the shim.
- **CR8 (I7, M2).** A1–A10 are rewritten against the current G and H plans (A3, A4, A7 and A8 corrected; A11 added).
- **CR9 (I8).** CI runs on the branch through `workflow_dispatch` after Tasks 3, 5, 6, 9 and 10, and the controller waits for green before the next task. The full suite is never run locally.
- **CR10 (I8).** Docs move with the code task that changes the claim. A task adding a guard adds its `CONTRIBUTING.md` row and runs `test_docs_guard.py`, so the tree is never red between Tasks 5 and 12.
- **CR11 (I6, M4, M5, M6).** User-visible changes are minimised, and every remaining one is under "Needs user ratification before Task 2". The controller asks before Task 2.
- **CR12 (M3).** Ruling 14 and Task 11 are dropped: H bounds the strict-mode limits.

### The review's other findings, as applied

- **I1.** A store a newer build switched plays best-effort as format 2, and its settings writes stay refused with 409 `newer_format`. `refuse_unmigrated` reads `keys.is_current`, not `translate` (Task 5).
- **I2.** `llm_connections._write_raw` never writes an empty `MODEL_FIELDS` value. `ensure_migrated` seeds no model field at format 2. `strip_model_fields` writes the raw frontmatter minus those keys. Only non-empty values count as left (Task 6b).
- **I8 (Task 3).** Task 3 is split by suite family (3a seam, 3b routes, 3c store, 3d inference and play, then the flip). Its greps are multi-line.
- **I9.** Named differences are exact. Each asserts the recorded value before changing it, touches one key, and applies to an asserted set of states. The sweep and the test prepare their frozen copy through one shared helper, and `home/` is asserted untouched (Task 5).
- **M1.** The base is described as it is (above).
- **M4.** Ruling 4 is a named amendment of §11.2 step 4 (ratification item 2).
- **M5.** Under CR1, §12's "translation serves" row, §5.6 and §10's banner sentence stay true. Task 2 only renames "translation" to the in-memory plan there.
- **M8.** There is no retry loop (CR1 removed its reason), so a data-dir change has nothing to rebind. `PUT /config/data-dir` reschedules `ensure` as today.
- **M9.** The two direct `inference.resolve("chat", cid)` reads in `routes/scenes.py` already carry `# routing-ok:` markers. Task 8 changes `.conn` to `.chain` on the same lines, and the marker cap does not move.

### The plan's rulings (Task 2 folds each into the spec, as ratified)

1. **Play is never refused, and it reads the legacy layout only through the planner, in memory** (CR1).
   - `resolve` makes one call, `legacy_plan.overlay(cfg, meta)`. It returns the format-2 view of the global and campaign settings, the virtual derived presets, the model-facts overlay and the notes. The resolver reads that view as format 2.
   - Below format 2, `overlay` plans the whole mapping. At format 2 it plans an unmarked campaign's mapping, and the derived-preset repoint of every scope without the retirement marker. A retired scope costs nothing, because the marker is already in the `cfg`/`meta` in hand.
   - The lookup is fail-soft, as today's translation is. The migration and retirement use the strict one.
   - Nothing is written on the play path. The banner, `GET /config`'s `ready` and §12's rows are unchanged. There is no retry loop: the next start retries, as §11.2 says.
2. **The mapping moves; the translation is deleted.** `translate.global_view`/`campaign_view`/`embedding_view` move verbatim into `legacy_plan.py`, the one legacy reader, shared by `migrate`, `retire` and `resolve`. `translate.py` and every format-1 branch of `resolve`/`settings`/`in_use` go. `routing.legacy_key`/`routing.CONFIG_KEYS` stay as spelling data that the planner, the write refusals and retirement read.
3. *(Dropped: CR1/I3. An unmarked campaign is read in memory and persisted only by the background migration and §11.1's write path.)*
4. **The derivation rule** (§11.2 step 4, made exact; amended per ratification item 2).
   - **Which slots.** Every selection slot (each generative role, its fallback, and each route pin) at global scope and in every campaign, where:
     - the provider's `kind` is `openai_compatible`;
     - its legacy `reasoning_effort` is `E` in `legacy_plan.REPRESENTABLE`;
     - the slot's model satisfies `llm_reasoning.is_glm`;
     - the slot's preset, read, sets no `reasoning_effort`.
   - **What it points at.** A derived preset whose params are the slot's preset's params plus `{"reasoning_effort": E}`. Its name is `"<preset name> · reasoning <E>"`, or `"Reasoning <E>"` when the slot names no readable preset.
   - **The id.** `slugify(name)`. If a preset with that id already holds different params or a different name, the id is `f"{slugify(name)}-{sha256(canonical params)[:8]}"`.
   - **Determinism.** Identical derivations collapse to one file, and two devices write the same bytes.
5. **What is not representable is noted durably, never only in `skipped`** (CR5; review I6).
   - A route-level preset (`preset_<route>`, global or campaign, `PRESET_CLEAR` included) that sets no reasoning effort, on a route whose selection at that scope is an `openai_compatible` GLM provider with a legacy effort, gets one note per (scope, route, provider). It is not derived, for ratification item 3's reason.
   - `E == "max"` gets one note per slot, **only if ratification item 1 is declined**.
   - Notes are computed by the planner, so `/models` shows them from this build's first start. Retirement persists them in the retirement record, and they stay until dismissed, worded "was not carried over". A per-call override is not stored, so it gets no note. §11.2 step 4 gains one line saying so.
6. **Retirement order and failure policy** (CR2–CR4). After the marker, in the same `ensure`:
   - **(0) Archive.** `backups.create_backup(prefix=RETIRE_PREFIX)`, unless this run took `pre-inference-`. It is reused on resume while the note names it and it exists. If it fails, nothing below runs.
   - **(a) Global.** One `config.md` write under `llm_connections.LOCK` then `config_lock`: derived presets first (`sampler_presets.put_derived`), then the repoint, the deletion of every legacy config key and global `route_*`, and `RETIRED_KEY`.
   - **(b) Campaigns.** Each under `campaign_lock_nowait(cid)`, with the marker re-read inside the hold. Unmarked: migrated in the same write first. Newer: skipped. Busy: left for the next run. One write carries the repoint, the deletion, the markers and `revision.bump(cid)`.
   - **(c) Strip.** Only when `config.md` and every campaign are readable, marked and retired, and every connection reads: each connection's non-empty `MODEL_FIELDS` go to the retirement record, then a `keep_rev` write strips them.
   - **Fail closed.** Every reader in (a)–(c) is strict. An `UnreadableError` stops that item, and only that item.
   - **The status.** Retirement never moves `migrate.status().state`. What is left is reported in `Status.retirement` (`left`, `failed`), read fail-soft, and is not rendered. So a fresh install reads `done`, and the Models page's quiet line is unchanged (CR11, I2, I4).
7. **Legacy keys and fields stay writable at format 1** (they are the planner's input) **and refused at format 2**, as today. `llm_connections.ensure_migrated` seeds them only below format 2. `_write_raw` never writes an empty `MODEL_FIELDS` value (I2).
8. **`inference.generate`** is `generate(task, messages, *, client, resolved, usage=None, schema=None, stream=True)` (ratification item 9).
   - It takes the call site's resolution, the way `decide` does (§7.4).
   - §7.2's `cid` and `override` are the inputs of that resolution: `require_inference` and `override_inference`.
   - With `stream=True` it returns an async iterator of text; with `stream=False`, an awaitable of the joined text.
9. **Typed targets replace the lowered dict.**
   - `Attempt.target: wire.Target`.
   - `ResolvedInference.chain: wire.Chain | None`.
   - The account block becomes `Target.account: wire.Account`, so `ACCOUNT_KEY` goes with `FALLBACK_KEY` and `STRUCTURED_KEY`.
   - `llm.ATTEMPTED` stays a holder key, and it holds the `Target` that answered.
   - H's `Stage` carries `chain: wire.Chain`. H's two `FALLBACK_KEY` dependencies become stage data: a stage's chain carries a fallback exactly when the old conn carried the key (A3).
10. **The registry.** `grimoire/adapters.py` has one class per `kind`, with `generate`, `models`, `check` and `decide` (native; it raises where unsupported), and the flags `carries_images`, `lists_models`, `embeds` and `decides_natively`.
    - **Embed** stays D's caller-owned `EmbeddingsClient` door (ratification item 8). The store must not import the gateway.
    - Two pairs are held equal by tests: the registry's `embeds` flag and `resolve.embed_endpoint`, and the store's adapter facts (`providers.py`) and the registry flags.
11. **E's cancel window is kept.** A cancel that lands during the local token count (at most `COUNT_TIMEOUT_S`) discards a reply that was already generated.
    - Closing the window needs a meter that files its row asynchronously, which is out of scope.
    - Task 9b keeps `_estimate` exactly where `_resilient` calls it. Task 9b documents the window in `docs/store-guarantees.md` under "What is not promised".
12. **`client.single` and `client.decide_native` stay facade methods.** `single` serves the model test's generate probe and `decide_native` H's native probe. The generate guard allows them only in `routes/config.py`, and `decide_native` also in `inference.py`.
13. **The suite is born as an upgraded default library** (CR6). `config.birth_fields()` adds Primary = `openrouter` at `config.DEFAULT_MODEL` when `GRIMOIRE_TEST_BIRTH=upgraded-default`. That is what migrating a fresh format-1 store yields.
    - Tests that build legacy state start from `legacy_store()`.
    - The frozen JSONs are never regenerated.
    - The pre-migration comparisons are **kept**. They and the migrated-and-retired comparisons share one exact named-difference transform (I9).
14. *(Dropped: CR12. H bounds the strict-mode limits.)*
15. **Retirement markers.** `inference_keys.RETIRED_KEY = "inference_retired"`, value `"1"`, written in the retirement write of `config.md` and of each `campaign.md`.
    - It gates the in-memory derivation (ruling 1), so a legacy field an older build writes after retirement changes nothing here (§11.3).
    - It is how retirement knows a scope is verified retired (ruling 6c).
    - It is in `config._CONFIG_KEYS` (default `""`). C–H writers keep unknown frontmatter keys, and Task 6 checks that.
16. **The retirement record** `<home>/inference-retired.json`, owned by the new `store/inference/retired.py`, written through `store.atomic` under its own lock, and resolved through `store.paths`.
    - `fields`: `{conn_id: {MODEL_FIELDS: value}}`, written before the strip. `MODEL_FIELDS` holds no key or URL, so the record never holds a credential (§9.4). Only the planner's lookup reads it, as the fallback for a connection with no legacy fields left, so a campaign that arrives unmarked after the strip still resolves (C3).
    - `notes`: the ruling-5 notes, `{id, scope, subject, provider_id, effort, kind, text, dismissed}`. `id` is a digest of the note's other fields. Merging never un-dismisses a note.
    - Writers read it strictly, and readers fail soft.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec | rulings, as ratified | 2 |
| `store/config.py` | `birth_fields()` (public), `TEST_BIRTH_ENV`; `RETIRED_KEY` default; legacy defaults out of `read_config`; `retire_write` (strict) | 3a, 4, 6a, 6b |
| `store/inference_keys.py` | `born_current()` always true; `RETIRED_KEY` | 3a, 4 |
| `tests/conftest.py`, `tests/inference_fixtures.py`, `tests/inference_baseline.py` | `GRIMOIRE_TEST_BIRTH` at import; `UPGRADED_DEFAULT`; `legacy_store()`; markers `upgraded_birth` (3a–3c only), `product_birth` | 3a, 3d |
| suites by family (inventory, Task 1) | format-2 state, or `legacy_store()` | 3b, 3c, 3d |
| `store/inference/legacy_plan.py` (new), `tests/test_inference_legacy_plan.py` (new) | the planner: mapping, facts overlay, derivation, notes, `overlay` | 4, 5 |
| `llm_sampling.py`, `llm_reasoning.py`, `frontend/src/api/types.ts` | `max` (if ratified); GLM effort from the preset only | 4, 5 |
| `store/inference/translate.py`, `tests/test_inference_translate.py` | **deleted** (cases move to the planner's tests) | 5 |
| `store/inference/resolve.py`, `resolved.py`, `settings.py`, `in_use.py`, `migrate.py`, `store/embed_space.py`, `routes/common.py`, `routes/config.py`, `store/llm_connections.py` (`get_active`) | one planner call; no legacy branch | 5 |
| `tests/test_legacy_reader_guard.py` (new) | only the planner reads the legacy layout | 5 |
| `tests/fixtures/frozen_campaign/sweep.py`, `tests/frozen_copy.py` (new helper) | the sweep and the tests prepare their copy identically | 5 |
| `store/inference/retire.py` (new), `store/frontmatter.py` (`UnreadableError`, `read_strict`), `store/backups.py` (`RETIRE_PREFIX`), `store/sampler_presets.py` (`put_derived`), `store/locks.py` | retirement writes, fail closed, archive | 6a |
| `store/inference/retired.py` (new), `store/llm_connections.py` (`strip_model_fields`, `_write_raw`, `ensure_migrated`, `_dangling`), `routes/inference.py` or the settings route module (dismiss) | record, strip, I2, notes | 6b |
| `frontend/src/components/inference/RetiredNotes.tsx` (+ test), `frontend/src/routes/ModelsView.tsx`, `frontend/src/api/types.ts`, `client.ts`, `routes/ConfigView.tsx` | the notice; legacy fields out of the types | 6b |
| `wire.py` (new), `tests/wire_kit.py` (new) | `Target`, `Account`, `Sampling`, `Chain`; test builders | 7 |
| `store/inference/capabilities.py` | `post_image_reach(kind, vision_fact, vision_cap)` | 7 |
| `inference.py` | `generate`; `note_outcome`; decide and stages on chains | 8, 9c, 9d |
| every generation call site: `routes/*.py`, `store/usage.py`, `llm_reasoning.py`, `backend/scripts/ingest_scene.py`, `evals/runner.py`, `evals/run.py` | through `operations.generate`; display reads off the target | 8 |
| `tests/test_routing_guard.py`, `test_usage_guard.py`, `test_operation_guard.py` | the generate rules | 8 |
| `adapters.py` (new), `tests/test_adapter_registry.py` (new) | the registry | 9a |
| `llm.py`, `llm_sampling.py`, `llm_usage.py`, `model_guidance.py`, `health.py`, `store/post_images.py`, `routes/common.py` (`build_llm`), `wire.py` (`from_lowered`, temporary) | on `Target`/`Chain` behind the shim | 9b |
| `inference.py` (`Stage`, `stages`, `run_stages`, `_Call`, `_native`, `Capture`), `routes/config.py` (probe), `routes/character_turns.py` (`_capture`), `evals/runner.py` | H's chain on targets | 9c |
| `tests/llm_fakes.py` and the facade suites | on chains; shim deleted | 9d |
| `store/inference/resolve.py`, `resolved.py`, `controls.py`, `store/embed_space.py`, `store/inference/embed.py`, `routes/config.py`, `routes/scenes.py`, equivalence helpers | the lowering deleted | 10 |
| `tests/test_lowering_retired_guard.py` (new) | the lowering stays gone | 10 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `docs/store-guarantees.md`, `AGENTS.md`, `evals/README.md` | each with the task that changes its claim; Task 12 sweeps | 3–10, 12 |

---

### Task 1: Rebase onto D–H and re-check every assumption

**Files:** none changed except fix-ups the rebase forces. Report: `.superpowers/sdd/2026-10-08-inference-slice-i-retirement/task-1-report.md`.

- [ ] **Step 1: Rebase.** `git -C <worktree> fetch origin && git rebase <integration branch>` once D–H have landed (on `main`, or on the integration branch the controller names). Resolve conflicts in this plan file only. Before the rebase, the only commits on this branch beyond `b2c85c6` are plan commits (M1).
- [ ] **Step 2: Check A1–A11.** Run each check under "Assumptions to re-check". Record each as PASS, RENAMED (with the new name) or FAIL.
- [ ] **Step 3: Inventory.** Record the file lists (the counts go only in the report) of these, because Tasks 3, 5, 8 and 10 size themselves from them:
  - `grep -rn "translate\." backend/src`
  - `grep -rn "\.conn\b" backend/src evals backend/scripts`
  - `grep -rn "FALLBACK_KEY\|STRUCTURED_KEY\|ACCOUNT_KEY\|with_account\|_without_fallback" backend/src backend/tests evals`
  - `grep -rnE "\.(stream|complete|single|decide_native)\(" backend/src evals backend/scripts`
  - `rg -lU "active_connection_id|fallback_connection_id|embeddings_connection_id|set_campaign_routing|active_connection\b" backend/tests`
- [ ] **Step 4: Start green.** Run:

  ```
  tests/test_inference_*.py tests/test_format2_play.py tests/test_llm.py
  tests/test_llm_structured.py tests/test_operation_guard.py
  tests/test_routing_guard.py tests/test_usage_guard.py tests/test_frozen_campaign.py
  ```

  Expected: all pass. A failure here belongs to a sibling slice: report it and stop.
- [ ] **Step 5:** If any assumption FAILs, stop. Report the failing line and the plan edit it needs, and wait for the controller. Otherwise report PASS, with A11 listed as an open item for the controller.

**Controller, between Task 1 and Task 2:** ask the user every item under "Needs user ratification before Task 2", and record the answers in the Task 1 report. Task 4 Step 5 and Task 2 read them.

### Task 2: Settle the spec

**Files:** Modify `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`: §5.4, §7.1, §7.2, §8 (if item 1 is ratified), §11.1, §11.2 ("When it runs" and step 4), §11.3, §11.4, §12 (one word), §13, §14 row I and §15 "Migration".

- [ ] **Step 1:** Fold the rulings, as ratified, one short paragraph or line each.
  - §11.4 gains the retirement order and failure policy (ruling 6), the archive, the markers and the record (rulings 15–16), and the reading of "delete the translation layer" (item 10).
  - §11.1 and §11.2 "When it runs": "through the translation" becomes "through the planner (`legacy_plan.py`), in memory". The rest of each sentence stays.
  - §12's "Migration backup failed" row: "translation serves" becomes "the in-memory plan serves". §5.6 and §10's banner sentence stay as they are (M5).
  - §11.2 step 4 gains the exact rule and its amendment (rulings 4–5; items 2–4).
  - §8 (if item 1 is ratified): `REASONING` gains `max`. The GLM row says GLM takes low, high or max from the preset, and every other row says `max` is unsupported (nothing sent).
  - §7.1 names `legacy_plan.py`, `retire.py` and `retired.py` in place of `translate.py`, and `wire.py`/`adapters.py`.
  - §7.2's signature becomes ruling 8's.
  - §5.4 and §13 name `wire.Target`/`Chain` and `adapters.py` (rulings 9–10). §13 states embed's caller-owned door (item 8).
  - §11.3 gains item 5's two sentences about older builds after retirement.
  - §14 row I's "User-visible" cell lists the ratified items.
  - §15 "Migration" gains "planned equals persisted, before and after; named differences exact".
- [ ] **Step 2:** Search the spec for every "until slice I" and "slice I" and make each one past tense, or point it at the section that now holds it.
  - Expected: `grep -n "until slice I" docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` prints nothing.
- [ ] **Step 3:** Commit: `docs(spec): inference slice I rulings -- retirement, generate, the registry`.

### Task 3: The suite plays at format 2

No product behaviour changes, except that `born_current()` is no longer tied to the background switch (product code never sets `AUTOMIGRATE` to `0`). The translation still exists while this task runs, so each converted suite proves that the conversion changed nothing. **One commit per sub-task, each green on its targeted list. CI checkpoint after 3d.**

#### Task 3a: The birth seam

**Files:**
- Modify: `store/config.py` (`_birth_marker` becomes the public `birth_fields()`; `TEST_BIRTH_ENV = "GRIMOIRE_TEST_BIRTH"`), `store/inference_keys.py` (`born_current`), `tests/conftest.py` (markers), `tests/inference_fixtures.py`, `tests/inference_baseline.py` (`client_at`).
- Test: `tests/test_inference_storage.py`.

**Interfaces:**
- `inference_keys.born_current() -> bool`: always `True`. `automigrate()` still reads `AUTOMIGRATE_ENV` and now gates only the background thread.
- `config.birth_fields() -> dict[str, str]`: `{FORMAT_KEY: CURRENT_FORMAT}`, plus `{role_primary_provider: "openrouter", role_primary_model: DEFAULT_MODEL}` when `os.environ.get(TEST_BIRTH_ENV) == "upgraded-default"`. Its docstring says it is a test seam, and that `test_a_product_store_is_born_with_the_marker_alone` pins the product birth.
- `tests.inference_fixtures.UPGRADED_DEFAULT: dict[str, str]`: those three keys, asserted equal to `birth_fields()` under the env var.
- `tests.inference_fixtures.legacy_store(home: Path | None = None) -> None`: writes `config.md` as `---\n---\n` under `home` (default `store.home()`) **before anything else reads the store**, so the store is format 1.
- pytest marker `upgraded_birth` (3a–3c only): sets `TEST_BIRTH_ENV` with `monkeypatch.setenv` for a suite that opted in with `pytestmark`.

- [ ] **Step 1: Write the failing tests** in `test_inference_storage.py`:

  ```python
  @pytest.mark.upgraded_birth
  def test_a_test_store_is_born_an_upgraded_default_library(home):
      cfg = store.read_config()
      assert keys.is_current(cfg)
      assert (cfg["role_primary_provider"], cfg["role_primary_model"]) == ("openrouter", config.DEFAULT_MODEL)

  def test_a_product_store_is_born_with_the_marker_alone(home, monkeypatch):
      monkeypatch.delenv(config.TEST_BIRTH_ENV, raising=False)
      cfg = store.read_config()
      assert keys.is_current(cfg) and not cfg.get("role_primary_provider")

  def test_legacy_store_is_format_1(home):
      inference_fixtures.legacy_store()
      assert not keys.is_current(store.read_config())
  ```

- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** Change `born_current()`, `birth_fields()` and their callers. Register `upgraded_birth` in `pytest_configure`. `client_at(home)` calls `legacy_store(home)` before `create_app()`. Rewrite conftest's `AUTOMIGRATE` comment: it now means "no background thread".
- [ ] **Step 4:** Run `tests/test_inference_storage.py tests/test_config_store.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_migrate.py`, then the gates.
- [ ] **Step 5:** Commit: `test(inference): a public birth seam, and legacy_store()`.

#### Tasks 3b, 3c, 3d: Convert by family

**Inventory** (Task 1's list, refined here; multi-line, I8):
- `rg -lU "active_connection_id|fallback_connection_id|embeddings_connection_id|embeddings_model|set_campaign_routing|route_(scene|absorb|dossier|continuity|summary|tracker|suggestions|voice|image|tagline|scenario|opener)\b" backend/tests`
- `rg -lU "llm-connections[\s\S]{0,400}?\"(model|prefill|vision|post_process|sampler_preset|reasoning_effort)\"\s*:" backend/tests`, which catches a body whose `"model":` sits on its own line.
- `rg -lU "(create|update)_connection\([\s\S]{0,400}?\b(model|prefill|vision|post_process|sampler_preset|reasoning_effort)=" backend/tests`
- `rg -ln "connections/[^\"]*\.md|write_connection_raw|_write_raw" backend/tests` (raw connection-file writes)
- `rg -ln "\"active_connection\"|active_connection_id\"\]" backend/tests` (tests that read `GET /config`)
- `rg -ln "key not set\"" backend/tests` (the format-1 wording)

Each hit is converted one of two ways:
- **(a)** It builds state the app would only write at format 1, and the test is not about the legacy layout. Rewrite it in format-2 terms:
  - roles and pins through `inference_fixtures.put_settings`;
  - model facts through `PUT /api/llm-connections/{id}/facts`;
  - presets on the role selection, not on the connection.

  The suite gains `pytestmark = pytest.mark.upgraded_birth`.
- **(b)** The test is about the legacy layout or the migration: `test_inference_migrate`, `test_inference_translate`, `test_inference_equivalence*`, `test_frozen_campaign`, the baselines, `test_llm_connections_store`'s seeding cases and `test_config_store`'s legacy-key cases. It calls `legacy_store()` first, or uses `client_at`.

- [ ] **3b (routes family):** `test_routes.py`, `test_routing_routes.py`, `test_character_turns.py`, `test_scene_break_routes.py`, `test_tracker_routes.py`, `test_greetings_routes.py`, `test_continuity_routes.py`, `test_draft_runs.py`, `test_model_test_call.py`, `test_provider_api.py`, `test_usage_routes.py`, `test_prompt_log_routes.py`, and every other `test_*_routes.py` the inventory finds. Run them and `tests/test_format2_play.py`, then the gates. Commit: `test(inference): route suites build format-2 state`.
- [ ] **3c (store family):** `test_*_store.py`, `test_context_semantic.py`, `test_semsearch_store.py`, `test_continuity_similarity.py`, `test_embed_space_role.py`, `test_post_images*.py`, `test_absorb_*.py`, and the rest of the store hits. Run them, then the gates. Commit: `test(inference): store suites build format-2 state`.
- [ ] **3d (inference and play family, then the flip):**
  - Convert `test_inference_*.py` (class (b) where it applies), `test_format2_play.py` (its docstring: every suite now plays at format 2), `test_llm*.py`, `test_reasoning_display.py`, `test_evals.py`, `test_ingest_scene.py` and `test_frozen_campaign.py`.
  - **Flip the default.** `conftest.py` sets `os.environ["GRIMOIRE_TEST_BIRTH"] = "upgraded-default"` at import, beside `AUTOMIGRATE`, so a spawned subprocess inherits it and a mid-test `monkeypatch.undo()` cannot reach it (M7).
  - Delete the `upgraded_birth` marker, every `pytestmark` that names it, and every `@pytest.mark.upgraded_birth` (3a's test included).
  - Add the `product_birth` marker, an autouse check that applies `monkeypatch.delenv(TEST_BIRTH_ENV)` to a marked test, and mark `test_a_product_store_is_born_with_the_marker_alone` with it.
  - **Docs with code:** in `CLAUDE.md` "Working notes", the suite is born an upgraded default library at format 2 (`GRIMOIRE_TEST_BIRTH`), and "So the older play suites run at format 1" is deleted. Run `tests/test_docs_guard.py`.
  - Run every converted file, plus `tests/test_inference_storage.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_routes.py tests/test_character_turns.py`, then the gates.
  - Expected: all pass. A converted suite that fails has found a real format-1/format-2 difference: report it, and do not special-case it.
  - Commit: `test(inference): the suite is born an upgraded default library at format 2`.
- [ ] **CI checkpoint (Task 3).** The controller pushes, runs CI on the branch and waits for green. A suite the greps missed shows up here and is converted in 3d.

### Task 4: The shared planner (no runtime change)

The mapping moves into `legacy_plan.py`, and the derivation and the notes are added there, pure. `translate` and `migrate` delegate to it, so the runtime is unchanged and every existing equivalence test still passes. Task 5 makes play use it.

**Files:**
- Create: `backend/src/grimoire/store/inference/legacy_plan.py`, `backend/tests/test_inference_legacy_plan.py`.
- Modify:
  - `store/inference/translate.py`: `global_view`/`campaign_view`/`embedding_view` call the planner (deleted in Task 5).
  - `store/inference/migrate.py`: `global_fields`, `campaign_fields`, `_enriched` and `_stated` call the planner.
  - `store/inference_keys.py`: `RETIRED_KEY = "inference_retired"` (ruling 15; nothing writes it until Task 6a).
  - `store/config.py`: `RETIRED_KEY` in `_CONFIG_KEYS`, default `""`, so `read_config` returns it.
  - `store/inference/__init__.py`.
  - Step 5 only: `llm_sampling.py`, `frontend/src/api/types.ts` (`ReasoningEffort`), `frontend/src/components/SamplerPresetEditor.test.tsx`.

**Interfaces** (in `legacy_plan.py`):

```python
Lookup = Callable[[str], dict | None]         # connection id -> raw connection, legacy fields included
PresetRead = Callable[[str], dict | None]     # preset id -> preset
REPRESENTABLE: tuple[str, ...]                # tuple(e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)

class Derived(NamedTuple): id: str; name: str; params: dict
class Note(NamedTuple):
    id: str; scope: str; subject: str; provider_id: str; effort: str
    kind: str          # "route_preset" | "unrepresentable"
    text: str
class Plan(NamedTuple):
    fields: dict[str, str]                    # the format-2 keys this scope would be written with (repointed preset keys included)
    facts: dict[str, dict[str, dict]]         # provider -> model -> facts overlay (global, format 1 only)
    presets: tuple[Derived, ...]
    notes: tuple[Note, ...]

def derived_name(base_name: str, effort: str) -> str
def derive(base: dict | None, effort: str, existing: PresetRead) -> Derived
def global_plan(cfg: Mapping[str, str], lookup: Lookup, presets: PresetRead) -> Plan
def campaign_plan(meta: Mapping[str, str], *, global_current: bool, lookup: Lookup, presets: PresetRead) -> Plan
def legacy_embeds(cfg: Mapping[str, str], lookup: Lookup) -> bool   # in place of embed_space.resolve (no cycle)
def lookup(*, strict: bool) -> Lookup     # moved from migrate._lookup (strict: ConnectionUnreadableError) and translate's fail-soft read
```

- `global_plan`, by state:
  - **Below format 2:** the mapping (moved verbatim from `translate.global_view`), `_enriched`, the Embedding role when `legacy_embeds`, the facts overlay as values (what `migrate._stated` reads off each connection; `migrate._facts` keeps the writes), then the derivation over the mapped selection slots.
  - **At format 2 and not retired:** the derivation only. `fields` holds only the changed preset keys.
  - **Retired (`RETIRED_KEY`):** an empty plan.
- `campaign_plan`, by state:
  - **`global_current` false:** the mapping, as `translate.campaign_view` gives it below format 2.
  - **Global current, campaign unmarked:** the mapping plus the derivation.
  - **Marked, not retired:** the derivation.
  - **Retired:** empty.
- Notes are ruling 5's, computed per scope.

- [ ] **Step 1: Write the failing tests** in `test_inference_legacy_plan.py`. They build legacy stores with `legacy_store()` plus raw connection writes, and read plans directly:

  ```python
  def test_derived_name_and_id():
      assert legacy_plan.derived_name("Warm", "high") == "Warm · reasoning high"
      assert legacy_plan.derived_name("", "low") == "Reasoning low"
      d = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: None)
      assert d == legacy_plan.Derived("warm-reasoning-high", "Warm · reasoning high",
                                      {"temperature": 0.9, "reasoning_effort": "high"})

  def test_a_taken_id_gets_a_deterministic_suffix():
      taken = {"name": "Warm · reasoning high", "params": {"temperature": 0.1}}
      d1 = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: taken)
      d2 = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high", lambda _: taken)
      assert d1 == d2 and d1.id.startswith("warm-reasoning-high-") and len(d1.id) == len("warm-reasoning-high-") + 8

  def test_the_global_mapping_is_translates(state):        # every baseline state, both baselines:
      # global_plan(cfg).fields == the pre-Task-4 translate.global_view output, persisted
      # (recorded at the start of this task into the test as a literal per state; never regenerated)
  def test_the_facts_overlay_is_step_3s(home):             # vision/prefill/post_process -> facts, as `_stated` wrote them
  def test_legacy_embeds_agrees_with_embed_space_resolve(state):   # every baseline state: legacy_embeds(cfg) == (embed_space.resolve(cfg) is not None)
  def test_derived_presets_keep_the_wire_in_memory(home):
      # `glm` openai_compatible, effort "high", model "glm-5.3", sampler_preset "warm" (temperature 0.9);
      # Primary on it; fallback on `glm2` (effort "low", no preset); Saltmarch pins `scene`
      # to glm at a dangling preset id -> repoint: role_primary_preset "warm-reasoning-high",
      # role_primary_fallback_preset "reasoning-low", Saltmarch use_scene_preset "reasoning-high"
  def test_identical_derivations_collapse(home):           # two slots, same base and effort -> one Derived
  def test_a_preset_that_already_sets_reasoning_is_left_alone(home):
  def test_openrouter_and_non_glm_slots_derive_nothing(home):   # the openrouter_glm_with_effort shape
  def test_a_route_preset_over_a_glm_effort_is_noted(home):     # preset_summary="cold" over glm -> one Note(kind="route_preset")
  def test_a_retired_scope_plans_nothing(home):                 # RETIRED_KEY set -> Plan((), …) empty
  def test_a_marked_unretired_scope_plans_the_derivation_only(home)
  def test_notes_have_stable_ids(home)                          # same store, two plans -> same Note.id
  ```

  `test_the_global_mapping_is_translates` pins the move. Its literals are recorded from `translate` **before** Step 3 changes it, and they are pasted into the report.
- [ ] **Step 2:** Run `tests/test_inference_legacy_plan.py`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `legacy_plan.py`. Then make `translate` and `migrate` delegate to it, with the mapping's body moved, not copied.
  - The derivation reads each provider's `kind` and legacy `reasoning_effort` through `lookup`.
  - `REPRESENTABLE` is computed, not written out, so Step 5's answer flows through it.
- [ ] **Step 4:** Run:

  ```
  tests/test_inference_legacy_plan.py tests/test_inference_translate.py tests/test_inference_migrate.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_import_guard.py
  tests/test_lock_domain_guard.py
  ```

  Then the gates. Expected: pass, with no existing assertion changed.
- [ ] **Step 5: `max` as a GLM-only preset effort — NEEDS USER APPROVAL (ratification item 1).** Run exactly one branch, by the answer recorded in Task 1's report.
  - **Approved:**
    - `llm_sampling.REASONING` becomes `("off", "low", "medium", "high", "max")`, so `REPRESENTABLE` becomes `("low", "high", "max")`.
    - `_openai_reasoning`'s GLM branch already sends `max` through `glm_effort`.
    - Every other adapter answers `max` as `UNSUPPORTED`, nothing sent, with `WHY_MAX = "max is a GLM level"` and source `adapter`. That covers OpenRouter (whether or not a catalog is cached), the OpenAI preset (all three families), `anthropic`, `claude`, and other `openai_compatible` (strict and extended). No provider documentation is consulted, so nothing else claims `max`.
    - `WHY_GLM` drops "set on the connection".
    - Frontend: `ReasoningEffort` adds `"max"`. The editor reads the choice list from the server. `SamplerPresetEditor.test.tsx` gains a case showing `max` when the server lists it.
    - Tests:
      - `test_max_is_a_glm_only_effort`: parametrized over every adapter row of §8; GLM sends `{"reasoning_effort": "max"}`, and every other row is unsupported and sends nothing.
      - `test_max_is_derived` in `test_inference_legacy_plan.py`: a GLM connection at `max` repoints to "Reasoning max".
    - Run those, `tests/test_llm_sampling.py tests/test_reasoning_display.py`, and `cd frontend && npx vitest run src/components/SamplerPresetEditor.test.tsx`, then the gates and `make check-eslint`.
  - **Declined (the fallback):** nothing in `llm_sampling` changes, and `REPRESENTABLE` stays `("low", "high")`.
    - A slot at `max` gets `Note(kind="unrepresentable")`, and nothing is derived for it.
    - Test: `test_max_is_noted_not_derived`. A GLM connection at `max` gives one note per slot naming the provider and the slot, and no `Derived` whose id holds `max`.
    - Task 6b persists the note, and `/models` shows it on every device until dismissed.
- [ ] **Step 6:** Commit: `feat(inference): one planner for the legacy layout -- mapping, facts, derived presets, notes`.

### Task 5: Play reads the legacy layout only through the planner, in memory

**Files:**
- Delete: `store/inference/translate.py`, `tests/test_inference_translate.py`. Each translate case becomes a planner case in `test_inference_legacy_plan.py`.
- Modify:
  - `store/inference/legacy_plan.py`: `Overlay`, `overlay`.
  - `store/inference/resolve.py`, `resolved.py`, `settings.py`, `in_use.py`, `migrate.py`, `store/embed_space.py`, `store/context/semantic.py` (docstring).
  - `llm_reasoning.py`, `llm_sampling.py`.
  - `routes/common.py`, `routes/config.py`, `store/llm_connections.py` (`get_active` deleted).
  - `tests/test_inference_equivalence.py`, `test_inference_equivalence_c.py`, `test_frozen_campaign.py`, `tests/fixtures/frozen_campaign/sweep.py`.
  - Docs: `CLAUDE.md`, `CONTRIBUTING.md`, `docs/store-guarantees.md`.
- Create: `tests/test_inference_format2.py`, `tests/test_legacy_reader_guard.py`, `tests/frozen_copy.py`.

**Interfaces:**
- Produces:

  ```python
  class Overlay(NamedTuple):
      cfg: dict[str, str]                 # the global settings as format 2 sees them
      meta: dict[str, str]                # the campaign's, likewise ({} with no campaign)
      presets: Mapping[str, dict]         # virtual derived presets, by id
      facts: Mapping[str, Mapping[str, dict]]
      notes: tuple[Note, ...]
  def overlay(cfg: Mapping[str, str], meta: Mapping[str, str]) -> Overlay
  ```

  - `overlay` is identity, with no reads, when `cfg` carries `RETIRED_KEY` and `meta` is empty or carries it too.
  - It builds its own fail-soft lookup (`lookup(strict=False)`: an unreadable connection reads as absent, as the translation read it) and reads presets through `sampler_presets.read_preset`, so `resolve` names one function of the planner. From Task 6b the lookup falls back to the retirement record's `fields`.
- `resolve.resolve` calls `legacy_plan.overlay(cfg, meta)` **once**, then resolves `overlay.cfg`/`overlay.meta` as format 2. A preset read checks `overlay.presets` first, and a facts read checks `overlay.facts` first.
- `llm_reasoning.glm_effort(model: str, value: str | None) -> str`: the preset's value only.
- `refuse_unmigrated` uses `keys.is_current` (I1).
- Deleted:
  - `translate.*`, `resolve.IMAGES_ON_OUTRANKS`, `resolve._bridged` and every format-1 branch of `resolve.unusable`/`incapable`;
  - `ResolvedInference.legacy_route`;
  - `_overridden`'s `current` parameter (the format-2 meaning stays);
  - the legacy branch of `llm_sampling._openai_reasoning` (`value is None` → `glm_effort(conn)`);
  - `llm_connections.get_active`.

- [ ] **Step 1: Write the failing tests.**

  ```python
  # test_inference_format2.py
  def test_a_format_1_store_plays_in_memory_and_writes_nothing(client, home):
      legacy_store(); _legacy_active(client)                      # openrouter keyed, active
      before = _settings_digest(home)    # config.md, llm_connections/, sampler_presets/, each campaign.md, facts
      r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hi"})
      assert r.status_code == 200
      assert _settings_digest(home) == before                     # no settings, connection, preset or facts file written
  def test_an_unmarked_campaign_resolves_its_overrides_in_memory(client):  # route_scene -> "spare", campaign unmarked, format 2
      assert routes.common.require_inference("chat", cid).conn["id"] == "spare"   # `.chain.primary.provider_id` from Task 10
      assert not keys.is_current(campaigns.read_campaign(cid)["meta"])            # still unmarked: nothing written
  def test_a_busy_unmarked_campaign_still_resolves_and_nothing_is_written(client):  # I3, inverted: hold campaign_lock in a thread
  def test_a_newer_store_still_plays_and_refuses_settings_writes(client):            # I1: inference_format "3"
  def test_a_format_1_glm_store_still_sends_its_effort(home):     # the derived preset, virtual; effective has reasoning_effort "high"
  def test_a_retired_scope_never_reads_the_legacy_effort(home):   # RETIRED_KEY set, connection still carries "high", no derived preset -> nothing sent
  def test_glm_with_no_preset_effort_reports_supported_and_sends_nothing():
      eff = llm_sampling.effective({..."model": "glm-5.3", "kind": "openai_compatible", "sampling": {"params": {}}})
      assert eff["controls"]["reasoning_effort"]["state"] == "supported" and "reasoning_effort" not in eff["effective"]
  def test_overlay_is_free_on_a_retired_store(monkeypatch):       # lookup and preset reads never called
  ```

  In `test_inference_equivalence.py` and `_c.py`:
  - **Keep `test_resolution_matches_the_baseline` for every state** (I9, CR1). It now resolves the legacy copy in memory, through the planner, and compares it to the frozen JSON through `retired_expectation`.
  - **Add `test_planned_equals_persisted`, for every state of both baselines.** Every task cell's observed resolution (provider, model, preset id and name, sampling, `effective`, fallback) on the legacy copy, in memory, equals the same observation on that copy after `migrate.ensure()`. In this task the persisted side is migrated and still carries the in-memory derivation. From Task 6 it is migrated and retired.
  - `retired_expectation(recorded: dict, state: str) -> dict`. The only named differences, each exact:
    1. **Derived slots.** In a cell whose slot the planner derived, `sampling.preset_id`/`preset_name` become the derived preset's. The `effective` wire must still be equal.
    2. **Route preset over a GLM effort.** Take a cell whose recorded `sampling.scope` is the value the baseline records for a route-level preset, whose `sampling.params` hold no `reasoning_effort`, and whose `effective` does hold one. Assert that `reasoning_effort` equals the connection's legacy effort recorded by the state's builder, then delete that one key from `effective`. Assert that the set of states touched is exactly `{"glm_max_under_route_preset"}`.
    3. **Item 1 declined only.** Take a cell whose recorded `effective.reasoning_effort == "max"` that rule 2 did not cover. Assert the value, delete that one key, and assert the set of states touched is exactly `{"glm_max_under_route_preset"}`.

    A cell that differs in anything else fails.
- [ ] **Step 2:** Run those tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `resolve` makes its single `overlay` call. Delete the format-1 branches and `translate.py`, and point every `translate` import at the planner or the resolver.
  - `settings.view` and `in_use` read through `resolve`, so they show a format-1 store as the format-2 layout it will be written as. There is no legacy wording. `settings.view` also returns `retirement_notes` (the planner's notes; Task 6b adds the record's), not rendered yet.
  - `embed_space`/`resolve.embedding` read `role_embedding_*` from `overlay.cfg` (D's note). `embed_endpoint(conn, current)` loses `current`.
  - `routes/config._public_config` is unchanged, so `ready` and the banner are unchanged.
  - `migrate` persists `global_plan`/`campaign_plan`. Its `_run` early return is unchanged in this task.
- [ ] **Step 4: The frozen campaign** (I9).
  - `tests/frozen_copy.py` holds `copy_home(dst) -> Path`, the one way to copy `home/`. `frozen_client`, `frozen_home` and `sweep._write_snapshot` all use it.
  - `test_frozen_campaign`'s existing turn and absorb tests run on the format-1 copy, in memory, unchanged.
  - Add `test_a_migrated_copy_plays_a_turn`: `migrate.ensure()` on a copy, `state == "done"`, then the same turn.
  - Every frozen test asserts that `home/`'s digest is unchanged at teardown (§11.5).
  - `snapshot.json` is not expected to move: the in-memory plan is the old translation, plus derived names only where a GLM slot exists. If it moves, regenerate it with the sweep command in CLAUDE.md and paste the diff in the report. The only acceptable change is a derived preset's id or name on a GLM slot. Any other change: stop and report.
- [ ] **Step 5: The legacy-reader guard** (`tests/test_legacy_reader_guard.py`).
  - `test_only_the_planner_reads_the_legacy_layout`. An AST walk over `backend/src/grimoire` flags:
    - a str constant equal to a `LEGACY_GLOBAL_KEYS` member or a legacy `route_<k>` spelling;
    - any reference to `routing.CONFIG_KEYS` or `routing.legacy_key`;
    - `X.get("reasoning_effort"|"sampler_preset")` or `X["reasoning_effort"|"sampler_preset"]` where the receiver's name is `conn`, `raw` or `connection`.

    These may appear only in `ALLOWED = {"store/inference/legacy_plan.py", "store/inference/retire.py", "store/inference/retired.py", "store/inference_keys.py", "store/config.py", "store/llm_connections.py", "store/routing.py", "store/campaigns/lifecycle.py"}`. (`retire.py` and `retired.py` arrive in Task 6; the guard allows them by path.)
  - `test_only_resolve_migrate_and_retire_import_the_planner`: `resolve.py` calls exactly one name of `legacy_plan` (`overlay`), exactly once.
  - `test_the_guard_flags_a_planted_reader`: a planted `cfg.get("active_connection_id")` in a temp module is reported.
  - The docstring states its reach. A preset's `params["reasoning_effort"]` is legitimate, and `migrate.py` is deliberately not in `ALLOWED`: anything it would read moves into the planner. CLAUDE.md: "The guards do not prove absence".
- [ ] **Step 6: Docs with code** (CR10).
  - `CONTRIBUTING.md`: a row for `test_legacy_reader_guard.py`.
  - `CLAUDE.md` "Adding an LLM call site?": "A store the migration has not reached is read through `translate`…" becomes the planner, in memory.
  - `CLAUDE.md` "Model settings moved…": "play carries on through the legacy translation" becomes "…through the planner, in memory".
  - `docs/store-guarantees.md` "Model settings migration": "Until that write lands the resolver reads the legacy settings through the translation" becomes the same, with `legacy_plan`.
- [ ] **Step 7:** Run:

  ```
  tests/test_inference_format2.py tests/test_inference_legacy_plan.py tests/test_inference_migrate.py
  tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_read_api.py
  tests/test_inference_override.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  tests/test_embed_space_role.py tests/test_context_semantic.py tests/test_llm_sampling.py
  tests/test_reasoning_display.py tests/test_frozen_campaign.py tests/test_format2_play.py
  tests/test_legacy_reader_guard.py tests/test_routing_guard.py tests/test_import_guard.py
  tests/test_lock_domain_guard.py tests/test_docs_guard.py tests/test_todo_route.py
  ```

  Then `cd frontend && npx vitest run src/components/inference`, then the gates. Expected: all pass.
- [ ] **Step 8:** Commit: `feat(inference): play reads the legacy layout through one planner, in memory; translate.py goes`.
- [ ] **CI checkpoint (Task 5).**

### Task 6: Retirement persists the plan

Two commits. **CI checkpoint after 6b.**

#### Task 6a: The archive, the global and campaign writes, fail closed

**Files:**
- Create: `store/inference/retire.py`, `tests/test_inference_retire.py`.
- Modify:
  - `store/frontmatter.py`: `UnreadableError`, `read_strict`.
  - `store/backups.py`: `RETIRE_PREFIX`; `_PREFIXES`; `sweep`'s exclusion; the listing.
  - `store/sampler_presets.py`: `put_derived`.
  - `store/config.py`: `retire_write`.
  - `store/inference/migrate.py`: the retirement stage; `Status.retirement`; the note's `retire_safety`/`retire_failed`; the `_run` early return.
  - `store/locks.py`.
  - Docs: `docs/store-guarantees.md`, `CLAUDE.md`, `tests/test_docs_guard.py`.

**Interfaces:**
- `frontmatter.UnreadableError(OSError)`: "a file that exists and could not be read or parsed; a writer never replaces it" (D's `FactsUnreadableError` docstring, generalised).
- `frontmatter.read_strict(path: Path, *, require: str | None) -> dict`. It raises `FileNotFoundError` for an absent file. It raises `UnreadableError` when:
  - the read fails (`OSError`, `UnicodeDecodeError`);
  - the file is empty or whitespace;
  - its frontmatter does not parse (no opening or closing fence);
  - `require` is given and is missing or empty in it.

  Otherwise it returns the meta.
- `backups.RETIRE_PREFIX = "pre-retirement-"`, kept by the same never-prune rule as `SAFETY_PREFIX` and listed by `GET /backups` beside it.
- `config.retire_write(set_keys: Mapping[str, str], drop: Iterable[str]) -> None`. Under `config_lock`, it calls `read_strict(config path, require=FORMAT_KEY)`, refuses a non-current marker, sets and drops, and writes atomically. It never adds a key outside `set_keys`.
- `sampler_presets.put_derived(pid: str, name: str, params: dict) -> None`. It writes when absent and does nothing when identical. It raises `ValueError` when the id holds something else, which `derive`'s digest suffix makes unreachable.
- In `retire.py`:
  - `retire_global(lookup: Lookup) -> tuple[Note, ...]`, under `llm_connections.LOCK` then `config_lock`. It reads `config.md` strictly, computes `global_plan`, writes each derived preset, then `config.retire_write(plan.fields | {RETIRED_KEY: "1"}, drop=LEGACY_GLOBAL_KEYS + global route keys)`.
  - `retire_campaign(cid: str, lookup: Lookup) -> tuple[Note, ...] | None`. The caller holds the campaign lock; like `migrate.campaign`, it raises `RuntimeError` when `locks.holds_campaign(cid)` is false. Inside the hold it calls `read_strict(campaign.md, require=None)` and re-reads the marker:
    - **newer:** returns `None`, nothing written;
    - **unmarked:** `campaign_plan(meta, global_current=True, …)` carries the migration's fields (step 8's work) and the derivation;
    - **marked:** the derivation only.

    It writes its plan's derived presets first (`put_derived`), then one atomic `campaign.md` write: the fields, `FORMAT_KEY`, `RETIRED_KEY`, the `route_*` keys deleted, `updated` left alone. Then `revision.bump(cid)`.
  - `left() -> tuple[str, ...]`, **fail-soft, never raises**, one human-readable item each:
    - `config.md` unretired or unreadable;
    - a campaign unretired, unmarked or unreadable;
    - a connection unreadable, or holding a non-empty `MODEL_FIELDS` value (6b).
- `migrate`:
  - `_retire(run)` runs after the marker, and on every `ensure` of a current store:
    1. the archive (ruling 6.0);
    2. `retire_global`;
    3. `retire_campaign` for each campaign under `campaign_lock_nowait`, where busy, unreadable or a strict lookup failure leaves that campaign;
    4. the strip (6b).

    An `UnreadableError` or `ConnectionUnreadableError` stops only its item.
  - `_run`'s early return `if current and not left` becomes `if current and not left and not retire.left()`.
  - `Status` gains `retirement: dict = {"left": [...], "failed": ""}`. `state` is computed exactly as before.

- [ ] **Step 1: Write the failing tests** (`test_inference_retire.py`), on legacy stores then `migrate.ensure()`:

  ```python
  def test_retirement_takes_a_pre_retirement_archive_first(home)       # format-2 store: one RETIRE_PREFIX archive, taken before any write
  def test_a_failed_retirement_archive_writes_nothing(home, monkeypatch):   # create_backup raises -> no preset file, no repoint,
                                                                              # no deletion, no RETIRED_KEY; a chat turn plays identically
  def test_the_archive_is_taken_once_per_root_and_reused_on_resume(home)    # a later step fails; the next run reuses it
  def test_a_format_1_store_retires_under_its_pre_inference_archive(home)   # one archive, pre-inference-, none pre-retirement-
  def test_sweep_never_prunes_a_pre_retirement_archive(home)
  def test_backups_lists_a_pre_retirement_archive(client)
  def test_derived_presets_keep_the_wire(home)                          # the Task 4 store: effective before == after; presets on disk
  def test_retirement_repoints_and_deletes_in_one_write(home)           # no LEGACY_GLOBAL_KEYS, no route_* in raw files; RETIRED_KEY; every rev unchanged
  def test_space_ids_survive_retirement(home)                           # embed_space.endpoint()["space"] before == after
  def test_a_campaign_skipped_by_step_8_is_migrated_before_it_is_retired(home)   # C2: busy at step 8, free at retirement:
                                                                                  # use_scene_* present, route_scene gone, one write
  def test_a_newer_campaign_is_never_retired(home)                      # C2
  def test_retire_global_refuses_a_config_it_cannot_parse(home)         # I4: zero bytes, a truncated fence, no marker -> UnreadableError; file bytes unchanged
  def test_retire_campaign_refuses_an_unparseable_campaign(home)        # I4: same three cases; campaign.md bytes unchanged; the others retire
  def test_an_unreadable_connection_holds_the_scopes_that_name_it(home) # CR3
  def test_a_busy_campaign_is_retired_on_the_next_run(home)
  def test_retirement_is_idempotent(home)                               # ensure() twice -> the second writes nothing (digest of home)
  def test_retirement_never_moves_the_migration_state(home)             # a product-birth store reads "done"; a store with left items reads "done" and names them in retirement.left
  def test_status_never_raises_on_an_unreadable_connection(home)        # I4: retirement.left names it
  def test_two_devices_write_the_same_bytes(tmp_path)                   # two copies retired separately -> identical files
  ```

  Plus `test_planned_equals_persisted` (Task 5), whose persisted side is now retired, and `test_a_migrated_and_retired_copy_plays_a_turn` in `test_frozen_campaign.py`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** in ruling 6's order. Classify `retire.py` in `store/locks.py` beside `migrate`, in the same list and for the same reason: its campaign step runs under `campaign_lock_nowait`, taken by the caller.
- [ ] **Step 4: Docs with code** (CR10).
  - `docs/store-guarantees.md` "Model settings migration" gains "### Retirement". It covers:
    - the order;
    - "no archive, no write", and the `pre-retirement-grimoire-` archive, never pruned;
    - derive before delete, and migrate before retire;
    - fail closed;
    - `rev` kept;
    - the markers;
    - that retirement never moves the status `state`.
  - `CLAUDE.md` "Model settings moved…" gains one sentence on retirement and names the `pre-retirement-grimoire-` archive.
  - `test_docs_guard.test_store_guarantees_names_the_inference_migration` also holds `backups.RETIRE_PREFIX` and `migrate`'s retirement entry point, in the section and in `CLAUDE.md`.
- [ ] **Step 5:** Run:

  ```
  tests/test_inference_retire.py tests/test_inference_migrate.py tests/test_inference_legacy_plan.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_frozen_campaign.py
  tests/test_sampler_presets_store.py tests/test_backups*.py tests/test_config_store.py
  tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_legacy_reader_guard.py
  tests/test_docs_guard.py
  ```

  Then the gates.
- [ ] **Step 6:** Commit: `feat(inference): retirement archives, derives, repoints and deletes the legacy keys, failing closed`.

#### Task 6b: The record, the strip, and the notice

**Files:**
- Create: `store/inference/retired.py`, `frontend/src/components/inference/RetiredNotes.tsx` (+ `.test.tsx`).
- Modify:
  - `store/inference/retire.py`: `strip`; `left` widened.
  - `store/inference/legacy_plan.py`: `lookup` falls back to `retired.read()["fields"]` for a connection whose file holds no legacy field.
  - `store/llm_connections.py`: `strip_model_fields`; `_write_raw`; `ensure_migrated`; `_dangling`.
  - `store/config.py`: the legacy defaults out of `read_config` (kept in `_CONFIG_KEYS`).
  - `store/inference/settings.py`: `retirement_notes`.
  - The settings route module: dismiss.
  - `frontend/src/routes/ModelsView.tsx`, `frontend/src/api/types.ts`, `client.ts`, `routes/ConfigView.tsx`.
  - Docs: `docs/store-guarantees.md`.

**Interfaces:**
- `retired.py` (ruling 16):
  - `record_fields(conn_id, values: Mapping[str, str]) -> None`;
  - `record_notes(notes: Iterable[Note]) -> None`, which merges by id and never un-dismisses;
  - `dismiss(note_id) -> bool`;
  - `read() -> dict`, fail-soft.

  Writers read the file strictly (`UnreadableError` on a non-empty file whose JSON does not parse) and write through `store.atomic` under `retired._lock`.
- `llm_connections.strip_model_fields(conn_id: str) -> bool`:
  1. `read_strict(path, require="kind")`;
  2. `retired.record_fields(conn_id, <its non-empty MODEL_FIELDS>)`;
  3. a raw frontmatter rewrite minus `MODEL_FIELDS`, with `keep_rev`.

  It returns whether it wrote.
- `retire.strip(lookup) -> list[str]`. It runs only when `config.md` carries `RETIRED_KEY`, every campaign reads, is marked and carries `RETIRED_KEY`, and every connection file reads. Otherwise it writes nothing, and the reason is in `left()`.
- `llm_connections._write_raw` omits every `MODEL_FIELDS` key whose value is empty. A read already defaults them to `""` (I2).
- `ensure_migrated` seeds model fields and `active_connection_id` only below format 2 (ruling 7, I2).
- Retirement persists each scope's notes with `retired.record_notes` in the same pass.
- `settings.view` returns `retirement_notes`: the record's undismissed notes, plus the planner's notes not yet recorded (ids collapse).
- The dismiss route: `POST /api/inference/retired-notes/{note_id}/dismiss` → 200, or 404 for an unknown id. It calls `refuse_newer()` only. It is a settings write that spends nothing and touches no campaign.
- `RetiredNotes`: on `/models`, under the page heading, a list of the notes' sentences, each with "Dismiss". It renders nothing when the list is empty.
  - The sentence ends "— this was not carried over." It never says "for later" (I6).
  - A note names its scope (global, or the campaign's name), its route or role, and its provider, for example: "On the Summary route, the preset “Cold” sets no reasoning effort, so the GLM provider “glm” no longer sends its reasoning effort (high) there — this was not carried over."
  - The final copy is the implementer's, reviewed in the task review.

- [ ] **Step 1: Write the failing tests:**

  ```python
  def test_retirement_deletes_legacy_fields(home)                      # no MODEL_FIELDS in any connection file; every rev unchanged; the record holds them
  def test_fields_are_stripped_only_after_every_campaign_is_retired(home)   # one busy campaign -> connection files keep reasoning_effort; next run strips
  def test_an_unreadable_campaign_holds_the_strip(home)                 # C3: a zero-byte unmarked campaign.md -> no strip; retirement.left names it;
                                                                        # once readable, the next run migrates, retires, strips
  def test_strip_model_fields_refuses_an_unparseable_connection(home)   # I4: bytes unchanged, nothing recorded
  def test_the_strip_records_the_fields_first_and_the_planner_reads_them(home)   # C3: after the strip, write an unmarked Saltmarch with
                                                                                # route_scene: spare -> resolves spare's model in memory; the next ensure persists it
  def test_legacy_keys_written_after_retirement_are_ignored_then_removed(home)   # raw-write active_connection_id and a connection's
                                                                                # reasoning_effort -> resolve unchanged (RETIRED_KEY); ensure() removes both
  def test_a_product_birth_store_reads_done(home)                       # I2 (product_birth marker)
  def test_a_connection_edit_after_retirement_writes_no_legacy_field(client)    # I2: rename and key change -> no MODEL_FIELDS key in the file
  def test_a_fresh_store_never_gets_active_connection_id(home)          # product_birth; list_connections(); raw config has none
  def test_retired_notes_are_durable_and_dismissable(client, home)      # a route-preset note survives a second run; dismissed stays dismissed after
                                                                        # another retirement pass; a second root reading the same files sees it
  def test_a_noted_loss_is_shown_before_retirement_writes_it(client)    # format-1 store: settings view carries the planner's note
  ```

  Frontend:
  - `RetiredNotes.test.tsx`: renders each note; Dismiss calls the API and removes the row; nothing renders with no notes.
  - `ModelsView.test.tsx`: the notice sits under the heading.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Frontend: delete `reasoning_effort?`/`sampler_preset?` from the connection types, and the legacy keys from the `Config` type and the `ConfigKey` union. Fix the type errors that follow; none should be in rendering code, since C removed the legacy UI.
  - Add `retirement_notes` to the settings view type, and `migration.retirement` as an optional, unrendered field.
- [ ] **Step 4: Docs with code.** `docs/store-guarantees.md` "### Retirement" gains:
  - the strip's precondition;
  - the record, and that only the planner reads its `fields`;
  - the notes;
  - under "What is not promised": older builds after retirement (item 5's two sentences).
- [ ] **Step 5:** Run:

  ```
  tests/test_inference_retire.py tests/test_inference_migrate.py tests/test_inference_legacy_plan.py
  tests/test_config_store.py tests/test_llm_connections_store.py tests/test_provider_api.py
  tests/test_inference_settings.py tests/test_inference_read_api.py tests/test_embed_space_role.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_legacy_reader_guard.py
  tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_docs_guard.py
  ```

  Then `cd frontend && npm run typecheck && npx vitest run src/components/inference src/routes/ModelsView.test.tsx src/routes/ConfigView.test.tsx src/api/client.test.ts src/routes/ProvidersView.test.tsx`, then the gates and `make check-eslint`.
- [ ] **Step 6:** Commit: `feat(inference): retirement strips the connection fields last, keeps a record, and says what it could not carry`.
- [ ] **CI checkpoint (Task 6).**

### Task 7: `wire.Target`, built beside the lowered dict

**Files:**
- Create: `backend/src/grimoire/wire.py`, `backend/tests/wire_kit.py`, `backend/tests/test_wire.py`, `backend/tests/test_inference_target.py`.
- Modify:
  - `store/inference/resolved.py`: `Attempt.target`; `ResolvedInference.chain`.
  - `store/inference/resolve.py`: `_target`; `_chain` sets `structured` and `account` on targets in the same place it sets them on dicts.
  - `store/inference/capabilities.py`: `post_image_reach`.
  - `store/post_images.py`: `capability` delegates to `post_image_reach`.

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
      def without_sampling(self) -> Target: ...         # H's native capture (A7)

  @dataclass(frozen=True)
  class Chain:
      primary: Target; fallback: Target | None = None
      @property
      def attempts(self) -> tuple[Target, ...]: ...
      def with_account(self, **fields: str) -> Chain: ...    # both targets
      def alone(self) -> Chain: ...                          # Chain(self.primary): "without FALLBACK_KEY"
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
  def test_alone_drops_only_the_fallback():
  def test_wire_imports_nothing_from_the_package(): # AST: only stdlib imports

  # test_inference_target.py -- for every baseline state, in memory and migrated-and-retired
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

The facade is unchanged in this task: `generate` still hands `client.stream` the dict, `resolved.conn`. What moves are the call sites. They stop calling the client directly, and they stop reading `.conn` for anything except handing it on.

**Files:**
- Modify:
  - `backend/src/grimoire/inference.py`: `generate`, `note_outcome`.
  - `llm_reasoning.py`: `stream` takes a source factory.
  - Every generation call site from Task 1's inventory: `routes/common.py` (`draft_completion`, `_bounded_call` users, `_record_prompt` callers), `routes/streaming.py`, `routes/character_turns.py`, `routes/tracker.py`, `routes/characters.py`, `routes/scenes.py`, `routes/greetings.py`, `routes/continuity.py`, `routes/mechanics.py`, `routes/worlds.py`, `routes/campaigns.py`, `routes/passage_characters.py`, `store/usage.py`, `backend/scripts/ingest_scene.py`, `evals/runner.py` (generate cases: the whole resolution, A8), `evals/run.py`.
  - G's two `_noting(client, resolved.conn, holder)` sites (A2): they read `resolved.chain.primary`'s display facts from this task.
  - Tests: `tests/test_routing_guard.py`, `tests/test_usage_guard.py`, `tests/test_operation_guard.py`.
  - Docs: `CONTRIBUTING.md` (the guard halves), `CLAUDE.md` ("Adding an LLM call site?": the generation line).

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

  - It raises `ValueError` when `resolved.task != task`, `resolved.operation != "generate"`, or nothing resolved. This is raised **before** any client call.
  - `schema=` is passed through. Ruling 8: §7.2 allows it on a generate call too.
  - `note_outcome(client: LLMClient, resolved: ResolvedInference, error: LLMError | None) -> None`.
- Produces: `llm_reasoning.stream(usage: dict, start: Callable[[], AsyncIterator[str]]) -> AsyncIterator[dict]`. It installs the display buffer, *then* calls `start()`.
- Call sites read display facts from `resolved.chain.primary` (`.kind`, `.model` and `.provider_id`) for `_record_prompt`, `made_by` and `turn.served`. They never read them from `.conn` again.
- `routes/scenes.py`'s two `# routing-ok:` reads change `.conn` to `.chain` on the same lines, and the marker cap does not move (M9).

- [ ] **Step 1: Write the failing guard tests:**

  ```python
  # test_routing_guard.py
  def test_every_generation_goes_through_inference_generate():
      # test_usage_guard._generation_calls over backend/src, evals, backend/scripts:
      # `stream`/`complete` allowed only in inference.py (decide's backend and generate);
      # `single` only in routes/config.py's model test; `decide_native` only in inference.py
      # and routes/config.py's model test (H's probe, A4).
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

  A call site that kept only `conn` now keeps `resolved`, the `UsableInference` from `require_inference`.
- [ ] **Step 4: Docs with code.**
  - `CONTRIBUTING.md`: the generate halves of the routing, operation and usage guard rows.
  - `CLAUDE.md` "Adding an LLM call site?": a generation is `operations.generate(<task>, messages, client=…, resolved=…, usage=m.usage)`.
- [ ] **Step 5:** Run the guard files plus:

  ```
  tests/test_routes.py tests/test_routing_routes.py tests/test_character_turns.py
  tests/test_scene_break_routes.py tests/test_tracker_routes.py tests/test_greetings_routes.py
  tests/test_continuity_routes.py tests/test_continuity_reconcile_routes.py tests/test_absorb_identity.py
  tests/test_reasoning_display.py tests/test_draft_runs.py tests/test_ingest_scene.py tests/test_evals.py
  tests/test_format2_play.py tests/test_usage_routes.py tests/test_model_test_call.py
  tests/test_prompt_log_routes.py tests/test_docs_guard.py
  ```

  Then the gates. Expected: pass, with no assertion changed. Fakes still see `request["conn"]`.
- [ ] **Step 6:** Commit: `refactor(inference): every generation goes through inference.generate`.

### Task 9: The adapter registry, and the facade on targets (four commits)

Split per CR7. Each sub-task runs `tests/test_format2_play.py` and both equivalence suites besides its own list. **CI checkpoint after 9d.**

#### Task 9a: The registry, beside the facade

**Files:** Create `backend/src/grimoire/adapters.py`, `backend/tests/test_adapter_registry.py`. Production still dispatches dicts.

**Interfaces:**

```python
class Adapter(Protocol):
    kind: str
    carries_images: bool; lists_models: bool; embeds: bool; decides_natively: bool
    def generate(self, messages: list[dict], target: Target, usage: dict | None,
                 *, schema: dict | None = None) -> AsyncIterator[str]: ...
    async def models(self, target: Target) -> list[dict]: ...     # LLMError("bad_response") where not lists_models
    async def check(self, target: Target) -> None: ...
    async def decide(self, item: decisions.Item, target: Target, usage: dict | None) -> decisions.ItemResult: ...
        # H's native, one item (A3); LLMError("bad_response") where not decides_natively
    def decision_body(self, item: decisions.Item, target: Target) -> dict: ...   # H's native_body, per kind

KINDS: tuple[str, ...] = ("openrouter", "openai_compatible", "anthropic", "claude")
def build(*, openrouter, openai_compatible, anthropic, claude) -> dict[str, Adapter]
TEXT_ONLY_KINDS: frozenset[str]   # derived: not carries_images
LISTABLE_KINDS: frozenset[str]    # derived: lists_models
```

The classes are `OpenRouterAdapter`, `OpenAICompatibleAdapter`, `AnthropicAdapter` and `ClaudeAgentAdapter`, each wrapping its client. In this sub-task their bodies are copies of today's per-kind code in `LLMClient._provider`, `list_models`, `check`, `decide_native` and `native_body`. 9b deletes the facade's copies.

- [ ] **Step 1: Write the failing tests:**

  ```python
  def test_every_kind_has_one_adapter():
      assert set(adapters.build(**_fakes())) == set(adapters.KINDS) == {p.kind for p in providers.PRESETS.values()}
  def test_adapter_facts_agree_with_the_registry():
      # for every providers.PRESETS entry p and its adapter a = registry[p.kind]:
      #   not a.embeds           => "embed" in p.never
      #   not a.decides_natively => "decide_native" in p.never
      #   not a.carries_images   => "vision" in p.never
      # and each `never` entry true of the whole kind is a False flag
  def test_the_registry_embed_flag_matches_the_store_endpoint_rule():
  def test_a_chain_sends_what_the_lowered_dict_sent(tmp_path):
      # for every migrated-and-retired baseline state and task: a recording client is driven
      # once through adapters[target.kind] with the attempt's Target, and once through
      # today's LLMClient._provider with attempt.conn; the wire calls (model, api_key,
      # base_url, sampling kwargs, reasoning kwargs, strict, schema) are equal.
  def test_a_native_body_is_the_same_from_a_target(tmp_path):   # adapters' decision_body == llm.native_body(item, conn)
  ```

- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement. **Step 4:** Run `tests/test_adapter_registry.py tests/test_import_guard.py tests/test_format2_play.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`, then the gates.
- [ ] **Step 5:** Commit: `feat(llm): the adapter registry, beside the facade`.

#### Task 9b: The facade on chains, behind a shim

**Files:** `llm.py`, `llm_sampling.py` (`effective(target)`, `split(target)`, `not_applicable(target, why)`), `llm_usage.py` (`account(usage, target)`), `model_guidance.py` (`for_target(target)`), `health.py` (`record(target, error)`), `store/post_images.py` (`images_for(target)`, `reach(target | None)`), `routes/common.py` (`build_llm`'s `images` callable), `wire.py` (`from_lowered`), `tests/llm_fakes.py` (dual recording), `docs/store-guarantees.md` (E's cancel window).

**Interfaces:**
- `LLMClient`:
  - `stream(messages, chain: Chain | dict, usage=None, *, schema=None, retries=None)`, and `complete(…)` with the same arguments;
  - `single(messages, target: Target | dict, usage=None)`;
  - `list_models(target)`, `check(target)`, `note_outcome(target, error)`.

  Each entry point passes its argument through **one** private `_as_chain`, which calls `wire.from_lowered` on a dict. That is the shim. The constructor keeps its client keywords (the test seams). Its `fallback=` keyword is deleted, because every call carries its chain.
- `wire.from_lowered(conn: dict) -> Chain`: **standard library only** (#239). It knows `"_fallback"`, `"_structured"`, `"_account"` and `"_degrade"` as literals. It is temporary: 9d deletes it, and Task 10's guard bans the name.
- `llm.fallback_sampling(primary: Target, fallback: Target) -> Target`; `llm._same_route(a, b)` by `provider_id`; `llm.prefill_capable(target) -> bool`. The degrade sibling is `dataclasses.replace(target, degrade=True)`. `llm.ATTEMPTED` holds the `Target`.
- `_routes(chain)` builds `[(primary, retries), (fallback_sampling(primary, fallback), 0)]`. `_usable_routes` drops a fallback whose kind is in `adapters.TEXT_ONLY_KINDS` for image-bearing messages. `_dispatch` calls `self._adapters[target.kind].generate(…, schema=schema if target.structured else None)`.
- **E's `_estimate` call stays exactly where `_resilient` makes it** (ruling 11), with a comment naming the window.
- `inference.generate`/`decide`/`note_outcome` **still hand dicts** in this sub-task, through the shim. `decide_native` still takes a dict, and `llm_usage.with_account` stays, both until 9c.
- `FakeLLM.stream/complete/single` accept a dict or a `Chain`/`Target`. They record `request["chain"]`/`request["target"]` always (converting a dict with `wire.from_lowered`), and `request["conn"]` only when handed a dict. So every existing assertion holds.

- [ ] **Step 1:** Write `test_the_shim_is_the_only_dict_door` (only `_as_chain` calls `from_lowered` in `llm.py`) and `test_from_lowered_round_trips_every_baseline_attempt` (`from_lowered(attempt.conn) == Chain(attempt.target, fallback target)` for every state). Run: FAIL.
- [ ] **Step 2:** Implement. Delete the facade's per-kind copies, so only the adapters hold them.
- [ ] **Step 3: Docs with code.** `docs/store-guarantees.md` "What is not promised" gains E's cancel window (ruling 11).
- [ ] **Step 4:** Run:

  ```
  tests/test_adapter_registry.py tests/test_llm.py tests/test_llm_structured.py tests/test_llm_lifecycle.py
  tests/test_llm_error_status.py tests/test_llm_image_adapters.py tests/test_llm_sampling.py
  tests/test_post_images_dispatch.py tests/test_post_images.py tests/test_model_guidance.py
  tests/test_model_guidance_dispatch.py tests/test_provider_health.py tests/test_reasoning_display.py
  tests/test_llm_fakes.py tests/test_format2_play.py tests/test_inference_equivalence.py
  tests/test_inference_equivalence_c.py tests/test_import_guard.py tests/test_docs_guard.py
  ```

  Then the gates. Expected: pass, with no assertion changed.
- [ ] **Step 5:** Commit: `refactor(llm): the facade dispatches typed targets through the registry (dict shim)`.

#### Task 9c: H's native chain on targets

**Files:** `inference.py` (`Stage`, `stages`, `run_stages`, `_Call`, `_structured`, `_native`, `_with_mode`, `Capture`), `llm.py` (`decide_native`, `native_body`), `llm_usage.py` (`with_account` deleted), `routes/config.py` (H's probe), `routes/character_turns.py` (`_capture`), `evals/runner.py` (`--decide-backend` chains), `tests/llm_fakes.py` (`decide_native`), and the decide suites.

**Interfaces** (ruling 9; A3, A4, A7, A8):
- `Stage(mode: str, chain: wire.Chain, retries: int | None)`.
  - `stages(resolved)` keeps H's rule exactly. "Without `FALLBACK_KEY`" becomes `Chain.alone()`. The structured stage carries `Chain(A0.target, A1.target)` exactly when the resolver attached a structured fallback.
  - `_Call.chain` replaces `_Call.conn`.
  - `_with_mode(chain, mode)` is `chain.with_account(decision_mode=mode)`.
- `LLMClient.decide_native(item, target: Target, usage=None, *, retries=None)`. It dispatches `self._adapters[target.kind].decide`, and keeps H's refusals, retries, observer and capture. `llm.native_body(item, target)`.
- `Capture = Callable[[list[dict], dict, Target], Awaitable[None]]`. A native stage passes `target.without_sampling()`. `_capture(cid, sid, task, messages, target, outcome=None)` reads `.kind`/`.model`/`.provider_id`.
- H's probe: `client.decide_native(probes.PROBE_ITEM, target.with_account(operation="decide", decision_mode="native"), m.usage, retries=0)`, where `target` is the model test's `Target`. Until Task 10 it comes from `wire.from_lowered(conn).primary`, and then from `resolve.target_for`.
- `evals/runner`: the forced one-stage chains use `Stage("native"|"structured", Chain(A0.target), None)`.
- `FakeLLM.decide_native(item, target, usage=None, *, retries=None)` records `(item, target, retries)` in `native_requests`.

- [ ] **Step 1:** Convert H's suites and F's decide suites to `request["chain"]`/`request["target"]` and `native_requests` targets: `test_inference_decide.py`, `test_inference_decide_native.py`, and the decide cases in `test_scene_break_routes.py`, `test_routes.py` (voice drift), `test_character_turns.py` (speaker), `test_absorb_identity.py`, `test_continuity_reconcile_routes.py`, `test_model_test_call.py` (probe) and `test_evals.py`. Keep every assertion about stages, retries, the fallback, the mode, the capture and the ledger.
- [ ] **Step 2:** Implement until they pass. `llm_usage.with_account` is deleted. Every stamp is `Target.with_account`/`Chain.with_account`.
- [ ] **Step 3:** Run those suites plus `tests/test_usage_guard.py tests/test_operation_guard.py tests/test_routing_guard.py tests/test_prompt_log*.py tests/test_format2_play.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`, then the gates.
- [ ] **Step 4:** Commit: `refactor(inference): decide's stages carry chains of targets`.

#### Task 9d: The generate path, the fakes and the suites on chains; the shim deleted

**Files:** `inference.py` (`generate`/`note_outcome` hand `resolved.chain`/`.chain.primary`), `tests/llm_fakes.py` (no `request["conn"]`), `wire.py` (`from_lowered` deleted), `llm.py` (`_as_chain` deleted), and the suites:
- `tests/test_llm.py`, `test_llm_structured.py`, `test_llm_lifecycle.py`, `test_llm_error_status.py`, `test_llm_image_adapters.py`, `test_llm_sampling.py`;
- `test_post_images_dispatch.py`, `test_model_guidance_dispatch.py`, `test_model_guidance.py`, `test_provider_health.py`, `test_reasoning_display.py`, `test_llm_fakes.py`;
- every suite reading `request["conn"]` (Task 1's inventory): `test_routing_routes.py`, `test_scene_break_routes.py`, `test_routes.py`, `test_character_turns.py`, `test_model_test_call.py`, `test_inference_override.py`, `test_format2_play.py`.

- [ ] **Step 1: Convert.**
  - Each dict literal like `{"kind": "openrouter", "model": "m", "api_key": "k", "sampling": {...}}` becomes `wire_kit.target(kind="openrouter", model="m", api_key="k", sampling=wire.Sampling(...))`.
  - Each `request["conn"]["model"]` becomes `request["target"].model`.
  - Each `conn[FALLBACK_KEY] = fb` becomes `wire_kit.chain(primary, fb)`.

  Do not change what any test asserts about the wire, the holder, the retry count or the fallback. Only the input spelling changes.
- [ ] **Step 2:** Delete the shim (`from_lowered`, `_as_chain`, the fakes' dict branch). `test_the_shim_is_the_only_dict_door` becomes `test_the_facade_takes_no_dict` (passing a dict raises `TypeError`).
- [ ] **Step 3:** Run every suite listed under **Files**, plus:

  ```
  tests/test_adapter_registry.py tests/test_inference_decide.py tests/test_inference_decide_native.py
  tests/test_inference_fallback.py tests/test_operation_guard.py tests/test_usage_guard.py
  tests/test_import_guard.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  ```

  Then the gates. Expected: pass.
- [ ] **Step 4:** Commit: `refactor(llm): the facade takes chains of typed targets; the dict shim goes`.
- [ ] **CI checkpoint (Task 9).**

### Task 10: Delete the connection-dict lowering

**Files:**
- Modify:
  - `store/inference/resolve.py`: delete `_lowered`, `lower`, `with_facts`, `own_sampling`, `FALLBACK_KEY`, `STRUCTURED_KEY`, `ACCOUNT_KEY`, and the dict writes in `_account`/`_flag_structured`; build `Target` directly in `_attempt`.
  - `store/inference/resolved.py`: delete `Attempt.conn` and `ResolvedInference.conn`/`.fallback`.
  - `routes/common.py`: `UsableInference.conn` becomes `.chain`, never None.
  - `store/inference/controls.py`: `preview(preset_id, target, operation="")` builds a `Target` (H's `operation`, A5).
  - `routes/config.py`: the model test's probes (H's included) and `_with_effective` for the provider editor build targets through `resolve.target_for`; `post_images.reach(chat.chain…)`.
  - `store/embed_space.py`: D's note. `endpoint_of` reads `attempt.target.api_key`, and its `"conn"` entry becomes `"target"`.
  - `store/inference/embed.py`: `_stamp` reads the target.
  - `llm.py`: `FALLBACK_KEY`, `STRUCTURED_KEY`, `_without_fallback`, and `DEGRADE` as a dict key.
  - `llm_usage.py`: `ACCOUNT_KEY`.
  - `store/inference/settings.py`.
  - `tests/inference_baseline.py` and `inference_baseline_c.py`: observe projects `Target` into the same JSON keys, and the JSON is untouched.
  - `tests/test_inference_target.py`: target-only.
  - Docs: `CLAUDE.md` ("Adding an LLM call site?"), `CONTRIBUTING.md`.
- Create: `backend/tests/test_lowering_retired_guard.py`.

**Interfaces:**
- Consumes: `wire.Target`/`Chain` (Task 7) and the registry (Task 9).
- Produces:
  - `resolve.target_for(raw: dict, model: str, sampling: dict, *, model_facts: dict) -> wire.Target`, for `controls.preview` and the model test, which build a target outside any route.
  - `embed_space.endpoint()` returns `{model, base_url, key, space, provider, provider_name, provider_kind, target}`.

- [ ] **Step 1: Write the failing guard:**

  ```python
  def test_the_lowering_stays_retired():
      # AST over backend/src/grimoire, evals, backend/scripts. Flags:
      #  - a name or attribute FALLBACK_KEY, STRUCTURED_KEY, ACCOUNT_KEY, with_facts, own_sampling,
      #    _lowered, _without_fallback, from_lowered (CR7: the shim), _as_chain;
      #  - `lower` imported from store.inference.resolve;
      #  - attribute `.conn` on a ResolvedInference/UsableInference/Attempt binding
      #    (`require_inference(...)`, `override_inference(...)[0]`, `inference.resolve(...)`,
      #    `.attempts[i]`), on a `Stage` (`stages(...)` items, `Stage(...)`) and on a `_Call`
      #    (`call`, `replace(call, ...)`) (I7);
      #  - a str constant "_fallback", "_structured", "_account" or "_degrade".
  def test_the_guard_flags_a_planted_lowering():   # one plant per rule, each reported
  ```

- [ ] **Step 2:** Run. Expected: FAIL, listing what remains.
- [ ] **Step 3:** Delete and convert until the guard passes.
  - `test_a_chain_sends_what_the_lowered_dict_sent` loses its `_provider`/`.conn` half. It keeps the chain half against literal expectations recorded in Task 9a's run and pasted into the test.
  - The equivalence helpers' `_resolved`/`_lowered` read `Target` fields: `provider_id` becomes `conn`, plus `model`, `asdict(sampling)`, `model_params`, `prefill`, `post_process`, `reads_images` and `llm_sampling.effective(target)["effective"]`.
  - The frozen JSON cell `vision` (a legacy field) is compared as `facts.of(...)`'s `vision`. That is what the lowering put there at format 2 (`with_facts`), so the observation is the same value.
- [ ] **Step 4: Docs with code.**
  - `CONTRIBUTING.md`: a row for `test_lowering_retired_guard.py`.
  - `CLAUDE.md` "Adding an LLM call site?": "what it returns carries the connection dict `LLMClient` takes as `.conn` (read that, until the facade stops needing a connection at all)" becomes `.chain`. "under `FALLBACK_KEY`" becomes `Chain.fallback`. The facade dispatches through `adapters.py`.
- [ ] **Step 5:** Run:

  ```
  tests/test_lowering_retired_guard.py tests/test_inference_*.py tests/test_embed_space_role.py
  tests/test_context_semantic.py tests/test_semsearch_store.py tests/test_continuity_similarity.py
  tests/test_model_test_call.py tests/test_provider_api.py tests/test_format2_play.py
  tests/test_adapter_registry.py tests/test_routing_guard.py tests/test_import_guard.py
  tests/test_frozen_campaign.py tests/test_evals.py tests/test_docs_guard.py
  ```

  Then the gates, with `make baseline PY=$PY` if counts shrank. Expected: pass.
- [ ] **Step 6:** Commit: `refactor(inference): delete the connection-dict lowering, FALLBACK_KEY and STRUCTURED_KEY`.
- [ ] **CI checkpoint (Task 10).**

### Task 11: *(Removed: CR12, review M3. H bounds strict mode's limits; A6 checks it.)*

### Task 12: The last docs sweep, and the stand-in gates

**Files:** `AGENTS.md` (only if it names a retired symbol), `backend/src/grimoire/store/inference/__init__.py`'s docstring, `evals/README.md` (if it names `.conn`), and anything the sweep finds.

- [ ] **Step 1: Sweep.** `grep -rn "translate\|FALLBACK_KEY\|STRUCTURED_KEY\|ACCOUNT_KEY\|\.conn\b\|from_lowered" CLAUDE.md CONTRIBUTING.md AGENTS.md docs/store-guarantees.md evals/README.md backend/src/grimoire/store/inference/__init__.py`. Each hit is either history that says so, or it is rewritten. The claims themselves already moved with Tasks 3d–10.
- [ ] **Step 2:** Run `tests/test_docs_guard.py tests/test_install_scripts.py` and `make check-templates PY=$PY`. Expected: pass.
- [ ] **Step 3:** Commit (if anything changed): `docs: inference slice I -- the last stale mentions`.
- [ ] **Step 4: Stand-in gates.** These are controller-run, not this task's code:
  - (a) A whole-branch review standing in for `/codex:review`.
  - (b) A final spec-conformance review against §14 row I, §11 (all of it), §7.2, §8 and §13, as ratified. It asks whether every bullet of row I is implemented: derived presets, the deleted keys and fields, the deleted translation layer (as item 10 reads it), the registry with `inference.generate`, and the deleted lowering with `FALLBACK_KEY` and `STRUCTURED_KEY`. It also asks whether every user-visible change in the diff is one the user ratified.

  Findings are fixed before the PR. Codex reviews the PR. Merging waits for CI green.

---

## Self-review notes (for the plan reviewer)

- **Coverage of §14 row I and §11.4:**
  - Derived presets: Task 4 plans them (in memory from Task 5); Task 6a persists them.
  - Legacy keys deleted: Task 6a. Legacy fields deleted: Task 6b, last, behind every precondition.
  - The translation layer deleted: Task 5. It is deleted as a second read path, with the mapping in one guarded planner (item 10).
  - Format 1 migrated first, backup included: unchanged migration steps, then Task 6's stage in the same run, under its `pre-inference-` archive.
  - The adapter registry with `inference.generate`: Tasks 8 and 9a–9d.
  - Lowering, `FALLBACK_KEY` and `STRUCTURED_KEY` deleted: Task 10.
- **Row I's "User-visible: No":** every user-visible change left is listed for ratification (items 1–7). Round 1's refusal, banner, `ready` and status-line changes are gone (CR1, ruling 6).
- **§11.2, §12, §5.6, §15 and §17.8** stay true. Play resolves through the plan whenever the migration is pending or failed, and plays identically by the kept baseline comparison and `test_planned_equals_persisted`.
- **Carried notes:**
  - D's (`resolve.embedding` on translate): Task 5.
  - D's (`embed_space` key from the lowered dict): Task 10.
  - D's `FactsUnreadableError` pattern: generalised as `frontmatter.UnreadableError` in Task 6a.
  - E's cancel window: ruling 11, Task 9b.
  - F's (`complete(schema=)` in place of `generate`): Tasks 8 and 9.
  - F's (delete `FALLBACK_KEY`/`STRUCTURED_KEY`): Task 10.
  - H's I11 (two `FALLBACK_KEY` dependencies as stage data): Task 9c, `Chain.alone()` and the attach rule.
  - H's strict-mode limits: H's (A6).
  - H's ruling 21 (`require_parameters`): declined, reported (A11).
- **The order keeps the tree green, and CI proves it at five points.**
  - The suite moves to format 2 per family, each family opting in until the flip (3a–3d, then CI).
  - The planner lands beside translate before play uses it (Task 4). Play switches to it with every baseline still compared (Task 5, then CI).
  - Retirement writes only what the planner already serves in memory, so `test_planned_equals_persisted` covers both sides (Task 6, then CI).
  - Targets exist before anything consumes them (Task 7).
  - Call sites move before the facade's types change (Task 8).
  - The facade moves behind a shim that the fakes share, then decide moves, then generate, then the shim goes (9a–9d, then CI).
  - The dict is deleted only when its guard finds nothing left (Task 10, then CI).
  - Every doc claim moves in the task that changes it, so `test_docs_guard` is green at every commit.
