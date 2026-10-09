# Inference slice I — Retirement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 3.** Revision 2 answered the round-1 adversarial plan review (`.superpowers/sdd/plan-reviews/slice-i-plan-review.md`, verdict "No"). This revision answers its re-review (`slice-i-plan-rereview.md`, verdict "NEEDS FIXES": N1–N20). Every finding is answered under "Rulings", in this order: the controller's rulings on each round, then the review's other findings, then the plan's own rulings as amended.

**Rebase onto C–H (2026-10-09).** Task 1 rebuilt this branch on `main` at `16fb57d`, where C–H are squash-landed, and re-checked A1–A13 there (no FAIL; `task-1-report.md`). This pass makes the plan match the code as landed. No ruling's substance changed, and none became impossible as written, so no "**Controller to rule:**" note was needed. The larger in-place changes are marked "2026-10-09" or "on `main`". What changed:
- **Base** (header, A9, Task 1 Step 1): `main` at `16fb57d`, with C `1177012`, D `e4566c1`, E `4712a27`, F `75478e0`, G `5af18f9` and H `16fb57d`. A12 and A13 now name `1177012`, and A12's grep reads `llm_connections._read`.
- **A12's consequence.** C's migration lookup already *raises* `ConnectionUnreadableError` for a zero-byte, unfenced or `kind`-less file. So Task 4's `test_the_migrate_lookup_is_cs` expects that error, not `None`.
- **A10 is RENAMED.** H's stages strip the fallback with the public `inference.without_fallback`, which `evals/runner.chain` also calls. Task 10's guard bans it by that name, and Task 1 Step 3's grep finds it.
- **Task 6a builds on C's strict record read.** It moves `migrate.RecordUnreadableError` and `migrate._record` into `store/frontmatter.py`, rather than adding a parallel `UnreadableError`/`read_strict` (guarantee 6, CR3, ruling 6, ruling 16, Tasks 6a and 6b).
- **One lock.** `llm_connections.LOCK` *is* `locks.config_lock()`. It is one reentrant, cross-process lock, and it raises `StoreBusy` (409) after `LOCK_TIMEOUT` (30 s). `config.retire_write` holds `config.format_hold()` (ruling 6(a), N4, N19, Tasks 6a and 6b).
- **Births go through C's `lifecycle.publish_birth`** (R2-1, N3, ruling 15, Task 6a). A fork goes through it too, so a fork of an unmarked source is translated and marked, not copied (ruling 15).
- **Task 5** names a home for `translate.Lookup`, `translate.is_current` and `translate.embedding_role`. **Task 6a** rewrites `migrate.py`'s stale "slice I, ruling 3" docstring line.
- **A2 and Task 8** name F's voice-drift `_noting` as a third `.conn` reader.
- **Task 9a** absorbs H's `llm.NATIVE_DECISION_KINDS` table. **Task 9c** re-expresses four more H dependencies on `FALLBACK_KEY` and the conn id. **Tasks 9b/9c** pin the native ledger row (`decision_mode="native"`, `usage_rollup.VERSION` 6).
- **Task 10's guard** bans `without_fallback` and `NATIVE_DECISION_KINDS`, and follows tuple-unpacked `override_inference` bindings.
- **Ratification items 5, 6 and 7** have their factual references corrected. Their substance is unchanged.
- **The record's module (2026-10-09, Task 6b re-review N-2).** The retirement record lives at the store level, `store/inference_retired.py` (beside `inference_keys.py`, for the same import-graph reason), not `store/inference/retired.py`: `llm_connections` imports it at module scope for the strip and a delete, and `store/inference/` cannot be imported from there without a cycle. Path references below are updated; a bare `retired.py` means that module.

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

**Base:** `main` at `16fb57d` (Task 1, 2026-10-09). C–H are squash-landed there: C `1177012` (#485), D `e4566c1` (#486), E `4712a27` (#487), F `75478e0` (#488), G `5af18f9` (#489) and H `16fb57d` (#490). This branch is `main` plus this plan's own commits, and every A9 name is on `main` (M1). *(Revision 4 planned against `b2c85c6`, an F-era commit that had none of the D–H branch heads as an ancestor. Task 1 left that base behind; `backup/slice-i-pre-main` keeps it.)* Slice I lands last, after C–H.

---

## Needs user ratification before Task 2

Row I says "User-visible: No". Each item below is a user-visible change, or a departure from the spec's text, that this plan could not remove. **The controller asks the user about every item before Task 2 starts.** Task 2 folds each answer into the spec. Beside each item is what a decline means (N7). Each is one of three things:
- **Fallback:** a named alternative, with the step that implements it.
- **No alternative:** the item is a consequence of something else, which is named.
- **Re-plan:** declining it means the controller stops and the plan is revised.

1. **GLM `max` becomes a preset reasoning effort** (CR5; review Q3, I6 item 1).
   - What changes: the preset editor's Reasoning choice gains `max`. It is sent only to GLM models on `openai_compatible`; on every other adapter it is "unsupported" and nothing is sent. A GLM selection whose connection is at `max` is repointed to a derived "… · reasoning max" preset, so its wire does not change.
   - Side effect on other devices: a C–H build on the same synced library reads a `max` preset as invalid and sends no effort.
   - **Fallback (Task 4 Step 5, "Declined" branch):** `max` is not added. A GLM connection at `max` stops sending it on every task once this build runs. The loss is reported in the durable notice (item 7), and `glm_max_under_route_preset` carries an exact named difference (Task 5).
2. **Derived presets start from the slot's own preset** (ruling 4; review M4). §11.2 step 4 says "its own sampler preset" (the connection's legacy `sampler_preset`). This plan derives from the preset the role, fallback or pin selects. That is the only reading that keeps the wire when a user repointed a role's preset after C.
   - Visible: new presets named "<preset> · reasoning <effort>" (or "Reasoning <effort>") appear in the Presets list, and each GLM selection shows its derived preset.
   - Visible: editing the base preset (for example "Warm") no longer reaches those selections, which now point at the frozen copy (review I6 item 4).
   - **Re-plan if declined.** The literal text changes the wire for every role whose preset was repointed after C, which row I forbids.
3. **A route-level preset that sets no reasoning effort, over a GLM connection with a legacy effort, stops sending that effort on that route** (review I6 item 2). A route preset is shared by the route's primary and its fallback (§5.2). A derived route preset would therefore start sending reasoning to a fallback that never sent it, and leaving it alone drops the primary's effort. The plan leaves the route preset alone, which keeps every fallback exactly as it was. The loss is reported in the durable notice (item 7).
   - **No alternative:** no form keeps the wire whole. Declining means a re-plan.
4. **Calls outside a stored selection stop adding a GLM connection's legacy effort** (review I6 item 3; re-review N15). This covers:
   - a reroll's override preset that sets no reasoning effort;
   - the model test call on such a connection;
   - the provider editor's controls readout;
   - the Presets editor's "Preview on…" a GLM provider.

   None of these is stored, so no notice is written.
   - **No alternative:** this is a consequence of §11.4's "the legacy GLM `reasoning_effort` stops being read".
5. **Older builds on a synced library after retirement** (review I6 item 5; §11.3).
   - A pre-C build sees no model settings: its connections have no `model`, so it sends requests with an empty model.
   - A C–H build keeps playing, and loses only what item 1 names. It can write `active_connection_id` back: its `llm_connections.ensure_migrated` seeds it wherever `llm_connections/.migrated` is absent, so at most once per library. This build ignores it and removes it again on its next start (re-review N5).
   - *Corrected 2026-10-09:* this item used to say "on each start". On `main`, as at the old base, the seed is gated by `.migrated`. What a user sees is unchanged.
   - *Corrected 2026-10-09 after the whole-branch code review (🟡1), awaiting the user's ratification:* a C–H build loses more than `max`. It knows no retirement record, so a campaign that arrives unmarked after the strip (a restore, an old folder copied in), or a fork of one, is played and migrated by it from the stripped connection file with an empty pinned model and no preset, for good. Nothing on `main` (4e822aa) stops it: its `migrate.campaign` skips only a campaign marked current or newer, its strict connection read refuses only a file it cannot read or that names no `kind`, and the only store-wide stop it honours is a newer `inference_format`, which would also refuse every model-settings write on that build.
   - **No alternative:** this is a consequence of §11.4's deletion itself.
6. **A second never-pruned archive, `pre-retirement-grimoire-<stamp>.zip`** (CR4; review I5, Q2; re-review N12). It is listed in Backups beside C's `pre-inference-grimoire-<stamp>.zip` (`backups.SAFETY_PREFIX`, `_SAFETY_NAME_RE`), and retention never deletes it: `backups.sweep` counts and prunes only the ordinary `grimoire-` series. It is a full-library archive, like `pre-inference-`. Like that archive, it is in `list_backups()`, so `backups.due()` counts it as a restore point. A fresh install never takes one, because it is born retired (re-review N3).
   - **Fallback (Task 6a Step 4, "Declined" branch):** a settings-only archive, `pre-retirement-settings-grimoire-<stamp>.zip`, under the same never-prune rule. It is not a whole-store restore point and does not count toward the backup schedule (R2-7). It holds `config.md`, each `campaign.md`, `llm_connections/*.md`, `sampler_presets/` and `inference-retired.json`. That may suit Android storage.
7. **A durable "not carried over" notice on the `/models` page** (CR5 fallback; review I6). It is stored in the library, in `<home>/inference-retired.json` at the store root, so every device shows it until the user dismisses it. That file is not under `<home>/.cache/`, where C keeps its per-root migration note (`inference-migration.json`) and which backups leave out as derived data. So the notice is in every backup and every synced copy. It lists what items 1 (if declined) and 3 lost, and any model fact the C migration did not carry (re-review N9), worded as permanent.
   - **Re-plan if declined.** Without it, those losses would be silent, which guarantee 7 forbids.
8. **§13's per-adapter `embed` is dropped** (ruling 10; review M6). Embedding stays D's caller-owned `EmbeddingsClient` door, because the store must not import the gateway (#239). The registry carries an `embeds` flag that a test holds equal to the store's rule.
   - **Re-plan if declined.** A per-adapter embed that the store calls breaks #239.
9. **§7.2's `generate` signature** (ruling 8): `generate(task, messages, *, client, resolved, usage=None, schema=None, stream=True)`. It takes the call site's resolution, as `decide` does, rather than `cid` and `override`.
   - **Re-plan if declined.** Task 8's call-site shape and its guards are built on it.
10. **How §11.4's "delete the translation layer" is read** (CR1; review Q1). The second runtime read path is deleted: `translate.py`, every format-1 branch of the resolver, and the legacy GLM read. The mapping itself lives on in one guarded planner, which the migration persists and play uses in memory. That is what keeps §11.2, §12, §5.6, §15 and §17.8 true.
    - **Re-plan if declined.** The literal reading brings back round 1's C1: play refused at format 1.
    - *Amended 2026-10-09 (user ruling on the spec review's F1):* two format-1 answers stay in `resolve`, because a format-1 store always gave them — a provider-only reroll runs that provider's own model and preset (and a provider and a model take its own preset), and the `missing_key` sentence keeps its format-1 wording. Both read the planner's overlay (`Overlay.legacy`, `Overlay.selection`), never a legacy field, and `test_resolution_matches_the_baseline` holds a format-1 store to the frozen JSON exactly again.
11. **New storage the spec does not name** (rulings 15 and 16): the retirement marker `inference_retired: "1"` in `config.md` and each `campaign.md`, and the retirement record `<home>/inference-retired.json`. Neither is visible on its own. They are listed because they are spec additions.
    - **Re-plan if declined.** The marker is what keeps a retired scope free of the legacy read. The record is what keeps a campaign that arrives after the strip resolvable.

The plan removed these user-visible changes from round 1 (CR1):
- 409 `not_migrated` on play;
- `ready: False` on a format-1 store;
- the "Play resumes once it has finished" banner sentence;
- the migration retry loop;
- retirement's effect on the migration status line;
- the `skipped` channel for losses.

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
- **Ratified departures only.** Nothing under "Needs user ratification" is implemented in a form the user did not approve. Two steps are conditional on an answer: Task 4 Step 5 (item 1) and Task 6a Step 4 (item 6). Declining any item marked "Re-plan" stops the controller before Task 2 (N7).

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
4. **No archive, no write.** Retirement decides the archive once per pass, over every remaining unit of work (R2-3). A pass that deletes or replaces any stored value takes, or reuses, a `pre-retirement-` archive before its first write of any kind. It skips the archive in two cases: when this same run *created* a `pre-inference-` archive, or when the whole pass only adds markers. If the archive fails, retirement writes nothing: no preset, no repoint, no deletion (Task 6a, `test_a_failed_retirement_archive_writes_nothing`). Play is unaffected, because the planner serves in memory. A fresh install and a new campaign are born retired, so neither ever takes an archive (N3).
5. **Derive before delete; migrate before retire.**
   - A scope's legacy keys go in the same write that repoints that scope.
   - A campaign is retired only after its marker is re-read inside the hold. An unmarked campaign is migrated in that same write first. A newer one is skipped (Task 6, C2).
   - Connection fields are stripped only once `config.md` and every campaign are readable, marked and retired, and no connection is unreadable (C3).
   - Each connection's fields are written to the retirement record before the strip, so a campaign that arrives unmarked later still resolves. A model fact the C migration did not carry gets a note first (N9).
   - The strip holds `llm_connections.LOCK` (which is `config_lock`) across its read, record and write, so a connection edit is never overwritten (N4).
   - The migration persists only the old mapping, never a repoint, so no persisted preset key names a file that does not exist (N2).
6. **Fail closed** (ruling 6; CR3; N1, N6). Every new writer reads strictly, and so does every lookup that feeds a write. The failure is one of three:
   - `frontmatter.RecordUnreadableError`, which is C's `migrate.RecordUnreadableError` moved to `store/frontmatter.py` (Task 6a);
   - the read's own `OSError`/`UnicodeDecodeError`, which pass through as they do in C's `_record`;
   - `ConnectionUnreadableError`, from C's `llm_connections.read_connection_strict`, under retirement's lookup.

   One of them is raised when a file exists and:
   - cannot be read;
   - is empty;
   - has frontmatter that does not parse;
   - lacks its expected marker or `kind`.

   This covers a zero-byte or unfenced connection file, and an unparseable retirement record. That item is not retired and never rewritten, and nothing that names it is retired. The status reader is fail-soft and never raises.
7. **Nothing is lost silently.** What cannot be carried over is a note in the retirement record, shown on `/models` until dismissed, on every device (ruling 5). It is never only in `skipped`.
8. **The legacy state stays restorable** from the `pre-retirement-` archive, taken on the day of retirement (item 4 above).
9. **Proven on the old trees.**
   - Both frozen baselines are resolved in memory and after migration and retirement, and must match the JSON except for exact named differences (Task 5).
   - A copy of the frozen campaign's `home/` plays a turn in memory, then migrated and retired (Tasks 5 and 6). `home/` itself is never touched.

---

## Assumptions to re-check in Task 1

G and H are planned in parallel with this plan. This section was rewritten against their revised plans, re-read for revision 3 at the heads of `claude/inference-slice-g` and `claude/inference-slice-h` (CR8). Task 1 checks against whatever those branch heads hold when they are integrated, not against a pinned sha (re-review, neighbour pins). Each line is what this plan assumes they deliver, together with how Task 1 checks it. **If one fails, Task 1 stops and reports. It does not improvise.** *(2026-10-09: G and H landed on `main` as `5af18f9` and `16fb57d`. Task 1 checked every line there: A1–A9 PASS, A10, A12 and A13 RENAMED, A11 open, no FAIL. Each RENAMED line below now carries the landed name.)*

- **A1 (G).** `continuity` is `operation="decide", default_role="decision"` (G Task 5). Absorb's identity phase and the reconcile sweep resolve with `require_inference("continuity-identity"|"continuity-reconcile", cid, operation="decide")` inside the thunk `_soft_resolved` takes, then call `operations.decide("<task>", items, client=client, resolved=resolved, explain=…, campaign=…, around=…)`. The task literal is on the line after `operations.decide(`.
  - Check: `grep -n '"continuity"' backend/src/grimoire/store/routing.py`.
  - Check (M2): `grep -rn -A1 "operations.decide(" backend/src/grimoire | grep continuity` prints both tasks.
- **A2 (G).** G adds no `client.complete`/`client.stream` call site and no reader of a legacy key or connection field. It adds two `.conn` readers: `_noting(client, resolved.conn, holder)` in the identity and reconcile `around` hooks. Task 8 converts both to `resolved.chain.primary`, and Task 10's guard would catch either if missed.
  - Check: `grep -rn "_noting(client, resolved.conn" backend/src/grimoire`. Record the files.
  - *On `main` (Task 1):* the grep prints G's two, `routes/scenes.py:2121` (identity) and `routes/continuity.py:689` (reconcile). It also prints a third that A2 did not name: F's voice-drift `around` hook, at `routes/scenes.py:2653`. Task 8 converts all three.
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
- **A9 (D/E/F, landed on `main`).** These names are on `main` at `16fb57d` (M1; PASS in Task 1):
  - D: `store/inference/embed.py` (`embed_sync`, `embed`, `_stamp`); `resolve.embedding`, `resolve.embed_attempt`, `resolve.embed_endpoint(conn, current)`; `translate.embedding_view`; `embed_space.endpoint_of` (reads `conn["api_key"]`); `embed_space.moved_by`; `facts.FactsUnreadableError`.
  - E: `llm_usage.ACCOUNT_KEY`, `ACCOUNT_FIELDS == ("operation", "role", "billing", "decision_mode")`, `with_account`, `account`; the holder field `requested_model`; `llm._estimate` with `COUNT_TIMEOUT_S`; `store/inference/in_use.py`.
  - F: `resolve.FALLBACK_KEY`/`STRUCTURED_KEY`/`ACCOUNT_KEY`, `llm.FALLBACK_KEY` (`"_fallback"`)/`STRUCTURED_KEY` (`"_structured"`), `llm.DEGRADE` (`"_degrade"`), `llm.ATTEMPTED`, `inference._with_mode`, `inference._BACKENDS`.
  - Check each with one `grep -n`. A rename is followed silently. A missing name stops the task.
- **A10 (H; RENAMED in Task 1).** H adds no new private connection-dict key. `NONE_KEY` (`"<none>"`) is a distribution key in `decisions`, not a conn key. H's `stages` does not use `llm._without_fallback`. It uses a public copy, **`inference.without_fallback(conn)`**, and `evals/runner.chain` calls it too. Task 9c replaces it with `Chain.alone()`, and Task 10's guard bans it by its public name, beside `_without_fallback`, which `LLMClient.decide_native` and `_routes` still use.
- **A12 (C; re-review N1, R2-5; RENAMED in Task 1).** C's strict connection read is on the base. That is C's fix, planned as commit 68fd9a6 and squash-landed in **`1177012`** (PR #485): `llm_connections.read_connection_strict` raises `ConnectionUnreadableError` for a file with no `kind`, which covers a zero-byte or unfenced file. Before it, a zero-byte connection read as "no such connection", and step 8 or the switch could persist a pin with an empty model on top of it. Retirement deletes the `route_*` and legacy global keys that would repair such a pin, so I depends on the fix and must not land without it. I's own writes use `mode="retire"` either way.
  - Check: `git merge-base --is-ancestor 1177012 HEAD` (found with `git log --oneline -S "ConnectionUnreadableError" -- backend/src/grimoire/store/llm_connections.py`), and `grep -n 'if strict and "kind" not in meta' backend/src/grimoire/store/llm_connections.py`. The check lives in `llm_connections._read(id, *, strict=False)`, which `read_connection_strict` calls. A grep of `read_connection_strict` itself hits only its docstring. Then one targeted run of C's test for a `kind`-less connection file (`test_inference_migrate.py::test_a_connection_file_with_no_record_in_it_fails_the_run`). **Absent: Task 1 stops.**
  - *Consequence, on `main`:* C's migration lookup (`migrate._lookup`, `read_connection_strict`) already **raises** `ConnectionUnreadableError` for a zero-byte, whitespace, unfenced or `kind`-less file. So `lookup(mode="migrate")` and `lookup(mode="retire")` read a connection file the same way, and differ only in their record fallback (R3-2, N6).
- **A13 (C; re-review N9, R2-6; RENAMED in Task 1).** C re-copies each connection's `vision`, `prefill` and `post_process` into its model's facts under `llm_connections.LOCK` before the format switch. That was planned as commit 4e68846 and squash-landed in **`1177012`**: `migrate._switch` runs `_facts(run)` inside `with llm_connections.LOCK:` before the marker write. I's `fact_not_carried` note covers whatever still differs, and does not replace that copy.
  - Check: `git merge-base --is-ancestor 1177012 HEAD` (found with `git log --oneline -S "_facts" -- backend/src/grimoire/store/inference/migrate.py`), and a read of `migrate._switch` showing `_facts(run)` under `llm_connections.LOCK`. **Absent: Task 1 stops.**
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
4. **A file a writer cannot read** (a zero-byte sync placeholder, a truncated download, a sync client holding it): `config.md`, a `campaign.md`, a connection, or the retirement record. It is never rewritten, never treated as done, nothing that names it is retired, and the status never raises.
   - Task 6a: `test_retire_global_refuses_a_config_it_cannot_parse`, `test_retire_campaign_refuses_an_unparseable_campaign`, `test_an_unreadable_connection_holds_the_scopes_that_name_it` (zero-byte, unfenced, no `kind`), `test_status_never_raises_on_an_unreadable_connection`.
   - Task 6b: `test_strip_model_fields_refuses_an_unparseable_connection`, `test_an_unreadable_record_holds_a_late_unmarked_campaign`, `test_a_connection_edit_during_the_strip_survives`.
5. **The fallback, structured mode, the native chain, the degrade sibling and image-bearing messages through `Chain`.** The facade sends what it sent before:
   - a route preset follows onto the fallback;
   - a text-only fallback is dropped for image-bearing messages;
   - structured mode goes only on the flagged attempt;
   - H's stages keep their attach rule;
   - the post-image degrade sibling still fires;
   - E's estimate still runs where it ran.

   Tests: `test_a_chain_sends_what_the_lowered_dict_sent` (9a); Task 9's converted facade suites keep every assertion; `test_format2_play` passes in Tasks 8–10.
6. **A synced library where an older build writes legacy keys or fields after retirement.** The resolver never reads them, because the retirement marker stops the in-memory derivation. `retire.left()` sees them, so the next `ensure` deletes them again. Nothing raises.
   - Task 6b: one test per scope, each writing only that scope's legacy key: `test_a_legacy_config_key_written_after_retirement_is_ignored_then_removed`, `test_a_legacy_campaign_key_written_after_retirement_is_ignored_then_removed` and `test_a_legacy_connection_field_written_after_retirement_is_ignored_then_removed`. Also `test_a_connection_edit_after_retirement_writes_no_legacy_field`.

---

## Rulings

### Controller rulings on the round-1 review

- **CR1 (C1, I3, Q1).** Play is never refused. A format-1 store, an unmarked campaign at format 2 and (so that the derived presets apply before retirement persists them) any unretired scope resolve in memory through one guarded planner, `legacy_plan.py`, which includes the facts overlay and the derived presets. The format-1 baseline comparison is kept. `test_planned_equals_persisted` is added. Ruling 3's write on the play path is gone. This honours §11.2, §12, §5.6, §15, §17.8 and row I.
- **CR2 (C2).** `retire_campaign` re-reads the campaign's marker inside its hold. An unmarked campaign is migrated in the same write before any key is deleted. A newer one is skipped.
- **CR3 (C3, I4).** Every new writer fails closed through `frontmatter.RecordUnreadableError`, slice D's `FactsUnreadableError` pattern. It is C's `migrate.RecordUnreadableError`, moved to `store/frontmatter.py` (2026-10-09: C landed it, so Task 6a builds on it rather than adding a second one). An unreadable campaign, connection or `config.md` stops retirement for that item and is never treated as finished or overwritten. The strip runs only once every campaign is verified migrated and retired.
- **CR4 (I5, Q2).** A `pre-retirement-` archive is taken before retirement's first write. It is never pruned, taken once per root, reused on resume, and skipped only when this same run took `pre-inference-`. No archive, no write.
- **CR5 (Q3, I6).** `max` joins `REASONING` as a GLM-only value, unsupported elsewhere: **needs user approval** (ratification item 1; Task 4 Step 5). The fallback beside it accepts the loss and reports it in the durable, library-stored notice, never through `skipped`.
- **CR6 (Q4, M7).** The birth seam is public (`config.birth_fields()`), and the test birth comes from `GRIMOIRE_TEST_BIRTH`, set at conftest import beside `GRIMOIRE_INFERENCE_AUTOMIGRATE`.
- **CR7 (Q5).** Task 9 is split four ways: 9a the registry; 9b the facade behind a stdlib-only shim (`wire.from_lowered`); 9c H's native chain on targets; 9d the fakes and suites, with the facade's dict door deleted. `wire.from_lowered` itself goes in Task 10, with the probes that still use it (re-review N8). Task 10's guard names the shim.
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

### Controller rulings on the re-review (revision 3)

- **N1 (Critical).** Retirement's strict lookup separates "absent" from "unreadable".
  - A zero-byte, whitespace, unfenced or `kind`-less connection file raises `ConnectionUnreadableError`. That is `legacy_plan.lookup(mode="retire")`, built on C's `llm_connections.read_connection_strict`, which already raises for each of those (A12). That scope stops: nothing is rewritten and nothing is marked retired.
  - The test covers a zero-byte file, an unfenced one and a `kind`-less one.
  - C's migration keeps its own lookup (`mode="migrate"`). C closed the same hole on its own PR (`1177012`), and Task 1 checked that the fix is present (A12, R2-5).
- **N2.** The migration's mapping and retirement's repoints are two products: `Plan.mapped` and `Plan.repoint`.
  - The migration persists `mapped` only, so it never persists a derived preset id without its preset file, and a base preset's params are never lost.
  - `test_the_global_mapping_is_translates` reads `mapped`.
- **N3 / I2.** `RETIRED_KEY` is stamped at birth, so a fresh install takes no `pre-retirement-` archive and a new campaign triggers no pass. Both are tested.
  - `config.md` gets it through `birth_fields()`, the existing `born_current` seam.
  - Each new `campaign.md` gets it at creation, when `config.md` is itself retired, which is always so on a fresh install. It comes through C's birth seam, `lifecycle.publish_birth`, through an option only `create_campaign` passes (revision 4, R2-1, as amended 2026-10-09). In an unretired store a new campaign joins the next pass with the rest, so its GLM pins still get their derived presets.
- **N4.** `strip_model_fields` holds `llm_connections.LOCK` across the read, the record and the write. `retire.strip` re-checks its precondition per connection inside that hold. The interleaving is tested.
  - *On `main`:* `llm_connections.LOCK` **is** `locks.config_lock()`. It is one reentrant, cross-process lock, and a holder waits up to `LOCK_TIMEOUT` (30 s) before `StoreBusy`, which answers 409. So a `PUT /llm-connections/{id}` that meets the strip waits, or after 30 s answers 409. It is never overwritten.
  - The holds stay per connection. One hold across the whole strip would put every model-settings write behind it.
- **N5.** `retire.left()` sees a legacy key or field an older build wrote back into a retired scope, so the next start removes it with a deletion-only write. The test is split per scope, so each case fails for its own reason first.
- **N6.** Every path that persists from the retirement record reads it strictly, and an unparseable record stops that item (fail closed). The fallback applies only when the connection file exists and holds no **non-empty** `MODEL_FIELDS` value.
- **N7.** Every ratification item names a fallback with its implementing step, "no alternative", or "re-plan". Item 6's settings-only archive has a conditional step (Task 6a Step 4). The claim that only one step is conditional is corrected.
- **N8.** The target builders exist before 9b needs them: `resolve.target_for` moves into Task 9a.
  - `llm_sampling.effective`/`split`/`not_applicable` accept the dict callers until those callers move in Task 10.
  - `wire.from_lowered` stays until the model-test probes and those callers stop needing it, and is deleted with them in Task 10. Task 10's guard keeps banning it.
- **N9.** Before a connection is stripped, a `vision`, `prefill` or `post_process` value that its model's facts do not state gets a `Note(kind="fact_not_carried")`. C's PR re-copies those facts before its switch (A13, checked in Task 1), and this note covers whatever still differs.
- **N10–N20 (Minor).** Applied as proposed. Each change and where it lands:
  - **N10:** `Note` lives in the leaf `retired.py`, created in Task 4.
  - **N11:** notes reach `settings.view` through `resolve.retirement_notes`, and `resolve` calls `legacy_plan.overlay` from one private wrapper.
  - **N12:** the archive is named `pre-retirement-grimoire-`, and `RETIRE_PREFIX` joins `_sweep_abandoned_temps`.
  - **N13:** only a `pre-inference-` archive *created* this run skips retirement's. A reused archive's age is acceptable, for the reason stated in ruling 6.
  - **N14:** dismissing records a planner-only note first. A note's id ignores its text.
  - **N15:** ratification item 4 now covers the model test and the readouts.
  - **N16:** the birth-stamp sentence in CLAUDE.md moves with Task 3a.
  - **N17:** `test_a_c_era_writer_keeps_the_retired_marker`.
  - **N18:** a newer campaign holds the strip, and that is stated.
  - **N19:** `config_lock` is taken around `put_derived`, and each retirement item checks `run.halted()`. On `main`, every sampler-preset write already runs inside `config.format_hold()`, which is `config_lock`, so `put_derived` gets that hold by joining it.
  - **N20:** `record_fields` keeps the first values, and the fallback never answers for an absent file.

### Controller rulings on the second re-review (revision 4)

- **R2-1.** A new campaign's retired stamp is written only at creation, never by `_inference_marker()` or by any settings write.
  - *Amended 2026-10-09:* C landed `lifecycle.publish_birth(cid, meta, body="", translate=None)`. Its docstring calls it "the birth-stamp seam, and the one place a new marker is added", and both `create_campaign` and `fork._copy` call it.
  - So the stamp is a create-only keyword on `publish_birth`, which only `create_campaign` passes, instead of a separate `lifecycle._birth_marker()`. The rule is the same: only creation stamps. Tested: on a retired `config.md`, a settings write to a campaign whose retirement is pending leaves it unretired with its pin and effort intact, and the next pass retires it with its repoint.
- **R2-2.** Once `config.md` carries `RETIRED_KEY`, the migration's lookup (step 8 and the §11.1 write path) answers a connection with no non-empty `MODEL_FIELDS` from the retirement record. It reads the record strictly, so an unparseable record leaves the campaign unwritten. A late unmarked campaign is therefore never persisted with an empty pin. The plan's two tests for this case run through `ensure()` with step 8 active and through the §11.1 write path.
- **R2-3.** The archive is decided once per pass, over every remaining unit of work: global, each readable campaign, stray keys and strip candidates. A pass with any write left takes (or reuses) the `pre-retirement-` archive before its first write of any kind, marker-only writes included.
- **R2-4.** `post_images.reach` and `health.record` join the 9b dict door, with their three dict callers listed until Task 10.
- **R2-5.** A12 is a Task 1 check, not an open item. It verifies that C's strict read is on the base: commit 68fd9a6 (PR #485), squash-landed as `1177012`, where `read_connection_strict` raises `ConnectionUnreadableError` for a file with no `kind`. Task 1 stops if it is absent.
- **R2-6.** New assumption A13: I depends on C re-copying the model facts under the connection lock before its switch (4e68846, squash-landed as `1177012`). Task 1 checks it the same way and stops if it is absent.
- **R2-7.** Under ratification item 6's fallback, the settings-only archive gets its own prefix, `pre-retirement-settings-`. It is never in `list_backups()`, the whole-store restore points, and so never counted by `backups.due()`. `GET /backups` lists it apart, as `partial: true`, as `create_image_backup`'s archives are. Tested.
- **R2-8.** `test_a_failed_retirement_archive_writes_nothing` states that the archive precedes every write of the pass, marker-only writes included, which is why no `RETIRED_KEY` is written.

### Round 3 minors

- **R3-1.** Each retirement unit re-checks the archive decision inside its own hold. A unit whose in-hold plan now needs an archive the pass did not take is skipped and left for the next run. A `ConnectionUnreadableError` raised while building `pass_plan` drops only that unit.
- **R3-2.** The migrate-mode record fallback is keyed on the record holding an entry for that connection, not on `config.md`'s marker. An entry already proves a strip happened.
- **R3-3.** A §11.1 write refused because the record cannot be read answers a stated 409 `retirement_unreadable`, never a 500. This follows D's `facts_unreadable` precedent, and `test_the_write_path_reads_the_record_on_a_retired_store` asserts the status and the body.
- **R3-4.** Task 6a adds `RETIRED_KEY` to `UPGRADED_DEFAULT`, and adds `tests/inference_fixtures.py` and `test_a_product_store_is_born_with_the_marker_alone` (now asserting both markers) to its Files and steps. From 6a on, the suite is born retired, so a test that needs an unretired format-2 store removes the marker explicitly.

### The plan's rulings (Task 2 folds each into the spec, as ratified)

1. **Play is never refused, and it reads the legacy layout only through the planner, in memory** (CR1).
   - `resolve` makes one call, `legacy_plan.overlay(cfg, meta)`. It returns the format-2 view of the global and campaign settings, the virtual derived presets, the model-facts overlay and the notes. The resolver reads that view as format 2.
   - Below format 2, `overlay` plans the whole mapping. At format 2 it plans an unmarked campaign's mapping, and the derived-preset repoint of every scope without the retirement marker. A retired scope costs nothing, because the marker is already in the `cfg`/`meta` in hand.
   - Play's lookup is fail-soft, as today's translation is (`mode="soft"`). The migration keeps C's lookup (`mode="migrate"`). It changes in one way only: a stripped connection, one that has an entry in the retirement record, answers from that record, read strictly (R2-2, R3-2). Retirement and every write it makes use `mode="retire"`, which raises on an unreadable, empty, unfenced or `kind`-less file and on an unparseable retirement record (N1, N6).
   - The migration persists `Plan.mapped`, the old translation verbatim, and never a repoint. Retirement persists `mapped | repoint` after writing the derived presets. Play applies both in memory (N2).
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
   - **(0) Archive.** `backups.create_backup(prefix=RETIRE_PREFIX)` (or the settings-only form, if item 6 is declined). It is decided once per pass, over `pass_plan`: the global scope, every readable campaign, stray keys and the strip (R2-3). When any unit deletes or replaces a stored value, it is taken before the pass's first write of any kind.
     - It is reused on resume while the note names it and the file exists.
     - It is skipped when this run *created* a `pre-inference-` archive. A `pre-inference-` archive reused from an earlier run does not count: it predates every edit since (N13). It is also skipped when the whole pass only adds markers.
     - If it fails, nothing below runs.
     - A reused `pre-retirement-` archive can predate items a later run retires. That is acceptable because every retirement deletion keeps its value in a new key or in the record. The one exception is a legacy key an older build wrote *after* retirement (N5), whose value this build never read.
   - **(a) Global.** One `config.md` write, in one hold of `config.format_hold()`: derived presets first (`sampler_presets.put_derived`), then the repoint, the deletion of every legacy config key and global `route_*`, and `RETIRED_KEY`. *(2026-10-09: this read "under `llm_connections.LOCK` then `config_lock`". On `main` those are one lock, `locks.config_lock()`: reentrant and cross-process, 409 `StoreBusy` after 30 s. `format_hold` is that lock with the format read inside it, and it raises `NewerFormatError` on a newer store.)*
   - **(b) Campaigns.** Each under `campaign_lock_nowait(cid)`, with the marker re-read inside the hold.
     - Unmarked: migrated in the same write first.
     - Newer: skipped. It holds the strip for as long as it stays newer, which is fail-safe and stated in the docs and in `left()` (N18).
     - Busy: left for the next run.
     - Its derived presets are written under `config_lock` (N19), taken inside the campaign hold. That is the only order `main` allows: `config_lock` is innermost, never held around a campaign lock (`locks.config_lock`'s docstring). Then one write carries the repoint, the deletion, the markers and `revision.bump(cid)`.
   - **(a′/b′) Stray keys.** A retired scope that holds a legacy key again, written by an older build, gets a deletion-only write: nothing derived, nothing read from the key (N5).
   - **(c) Strip.** Only when `config.md` and every campaign are readable, marked and retired, and every connection reads. Per connection, under `llm_connections.LOCK` across the precondition re-check, the read, the record and the write (N4):
     - each `vision`/`prefill`/`post_process` its model's facts do not state becomes a `fact_not_carried` note (N9);
     - its non-empty `MODEL_FIELDS` go to the retirement record;
     - a `keep_rev` write strips them.
   - **Fail closed.** Every reader in (a)–(c) is strict, including the lookup and the record. A `RecordUnreadableError` (or the read's own `OSError`/`UnicodeDecodeError`) or a `ConnectionUnreadableError` stops that item, and every scope that names it. Each item checks `run.halted()` first, as the migration's steps do (N19).
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
15. **Retirement markers.** `inference_keys.RETIRED_KEY = "inference_retired"`, value `"1"`.
    - It is written in the retirement write of `config.md` and of each `campaign.md`.
    - **It is stamped at birth** (N3): by `config.birth_fields()` on a new `config.md`, and on a new campaign when `config.md` is retired (R2-1), through C's `lifecycle.publish_birth`.
      - `publish_birth` gains a create-only keyword (for example `retired: bool = False`), and only `create_campaign` passes it. When it is set, `RETIRED_KEY` is added beside `_inference_marker()`'s fields if `config.md` carries it. That read happens in the same `config_lock` hold `publish_birth` already takes for the format. `_inference_marker()`, which `set_campaign_inference` calls on every campaign settings write, stamps `FORMAT_KEY` only and never `RETIRED_KEY`, because a settings write is not a retirement.
      - **A fork** also goes through `publish_birth` (`fork._copy`), without that keyword.
        - A source that is marked current or newer is copied as it stands. A retired source's `RETIRED_KEY` is part of that copy.
        - An unmarked source is not copied as it stands. *(Corrected 2026-10-09: this read "a fork … copies what its source says".)* `publish_birth` translates it through `fork._translated`, which is `migrate.campaign_fields(meta, migrate.connection_reader())`, then stamps `FORMAT_KEY` only, so the fork joins the next retirement pass.
        - If the translation raises `OSError`, the fork is born unmarked.
        - That path reaches the planner only through `migrate.campaign_fields`, which Task 4 makes delegate. So Task 5's legacy-reader guard needs no entry for `fork.py`.
        - `migrate.connection_reader()` must become `legacy_plan.lookup(mode="migrate")` in Task 4, so a fork made after the strip gets the record fallback (R3-2). An unreadable record raises `RecordUnreadableError`, an `OSError`, so the fork is born unmarked rather than with an empty pin.
      - The app has no campaign import route. A campaign that arrives by sync or a restore keeps what its file says.
    - It gates the in-memory derivation (ruling 1), so a legacy field an older build writes after retirement changes nothing here (§11.3).
    - It is how retirement knows a scope is verified retired (ruling 6c).
    - It is in `config._CONFIG_KEYS` (default `""`). C–H writers keep unknown frontmatter keys, which `test_a_c_era_writer_keeps_the_retired_marker` checks (N17).
16. **The retirement record** `<home>/inference-retired.json`, owned by the new `store/inference_retired.py`, written through `store.atomic` under its own lock, and resolved through `store.paths`.
    - `fields`: `{conn_id: {MODEL_FIELDS: value}}`, written before the strip. `MODEL_FIELDS` holds no key or URL, so the record never holds a credential (§9.4).
      - Only the planner's lookup reads it. It is the fallback for a connection whose file exists and holds no **non-empty** `MODEL_FIELDS` value, so a campaign that arrives unmarked after the strip still resolves (C3, N6).
      - It never answers for a connection whose file is absent, so a deleted provider does not come back as a selection (N20).
      - A later strip keeps the first recorded values, which are the ones C translated (N20).
    - `notes`: the ruling-5 notes plus `fact_not_carried` (N9), each `{id, scope, subject, provider_id, effort, kind, text, dismissed}`. `id` is a digest of (`scope`, `subject`, `provider_id`, `effort`, `kind`) only, so a wording change in a later build does not bring back a dismissed note (N14). Merging never un-dismisses a note.
    - `retired.Note` is defined here, in a leaf module the planner imports, so the two never import each other (N10).
    - Every writer, and every lookup that feeds a write (`mode="retire"`), reads it strictly: a non-empty file that does not parse raises `frontmatter.RecordUnreadableError` (N6). Display readers fail soft.

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
| `store/inference/resolve.py`, `resolved.py`, `settings.py`, `in_use.py`, `migrate.py`, `store/embed_space.py`, `routes/common.py`, `routes/config.py`, `store/llm_connections.py` (`get_active` deleted; `Lookup`) | one planner call; no legacy branch | 5 |
| `tests/test_legacy_reader_guard.py` (new) | only the planner reads the legacy layout | 5 |
| `tests/fixtures/frozen_campaign/sweep.py`, `tests/frozen_copy.py` (new helper) | the sweep and the tests prepare their copy identically | 5 |
| `store/inference/retire.py` (new), `store/frontmatter.py` (`RecordUnreadableError`, `read_record`: C's, moved from `migrate`), `store/inference/migrate.py` (imports them), `store/backups.py` (`RETIRE_PREFIX`), `store/sampler_presets.py` (`put_derived`), `store/locks.py` | retirement writes, fail closed, archive | 6a |
| `store/inference_retired.py` (new in Task 4 with `Note`), `store/llm_connections.py` (`strip_model_fields`, `_write_raw`, `ensure_migrated`, `_dangling`), `routes/inference.py` or the settings route module (dismiss) | record, strip, I2, notes, facts check | 4, 6b |
| `store/campaigns/lifecycle.py` (`publish_birth`'s create-only keyword, passed by `create_campaign` alone), `store/config.py` (`birth_fields`) | born retired (N3, R2-1) | 6a |
| `frontend/src/components/inference/RetiredNotes.tsx` (+ test), `frontend/src/routes/ModelsView.tsx`, `frontend/src/api/types.ts`, `client.ts`, `routes/ConfigView.tsx` | the notice; legacy fields out of the types | 6b |
| `wire.py` (new), `tests/wire_kit.py` (new) | `Target`, `Account`, `Sampling`, `Chain`; test builders | 7 |
| `store/inference/capabilities.py` | `post_image_reach(kind, vision_fact, vision_cap)` | 7 |
| `inference.py` | `generate`; `note_outcome`; decide and stages on chains | 8, 9c, 9d |
| every generation call site: `routes/*.py`, `store/usage.py`, `llm_reasoning.py`, `backend/scripts/ingest_scene.py`, `evals/runner.py`, `evals/run.py` | through `operations.generate`; display reads off the target | 8 |
| `tests/test_routing_guard.py`, `test_usage_guard.py`, `test_operation_guard.py` | the generate rules | 8 |
| `adapters.py` (new), `tests/test_adapter_registry.py` (new) | the registry | 9a |
| `llm.py`, `llm_sampling.py`, `llm_usage.py`, `model_guidance.py`, `health.py`, `store/post_images.py`, `routes/common.py` (`build_llm`), `wire.py` (`from_lowered`, temporary, deleted in Task 10) | on `Target`/`Chain` behind the shim | 9b |
| `store/inference/resolve.py` (`target_for`) | every target builder before 9b | 9a |
| `inference.py` (`Stage`, `stages`, `run_stages`, `_Call`, `_native`, `Capture`), `routes/config.py` (probe), `routes/character_turns.py` (`_capture`), `evals/runner.py` | H's chain on targets | 9c |
| `tests/llm_fakes.py` and the facade suites | on chains; the facade's dict door deleted | 9d |
| `store/inference/resolve.py`, `resolved.py`, `controls.py`, `store/embed_space.py`, `store/inference/embed.py`, `routes/config.py`, `routes/scenes.py`, equivalence helpers | the lowering deleted | 10 |
| `tests/test_lowering_retired_guard.py` (new) | the lowering stays gone | 10 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `docs/store-guarantees.md`, `AGENTS.md`, `evals/README.md` | each with the task that changes its claim; Task 12 sweeps | 3–10, 12 |

---

### Task 1: Rebase onto C–H and re-check every assumption

**Files:** none changed except fix-ups the rebase forces. Report: `.superpowers/sdd/2026-10-08-inference-slice-i-retirement/task-1-report.md`.

- [ ] **Step 1: Rebase.** *(Done 2026-10-09.)* C–H landed on `main`, so the base is `main` at `16fb57d`. The branch was rebuilt with `git checkout -B claude/inference-slice-i origin/main`. This plan's five commits were cherry-picked onto it, in order, with no conflicts. The old head, `332e113`, is kept as `backup/slice-i-pre-main`. Beyond `b2c85c6`, that head held only the five plan commits (M1). Below `b2c85c6` it held old C–F commits, which `main` carries as squashes, and those were dropped.
- [ ] **Step 2: Check A1–A11.** Run each check under "Assumptions to re-check". Record each as PASS, RENAMED (with the new name) or FAIL.
- [ ] **Step 3: Inventory.** Record the file lists (the counts go only in the report) of these, because Tasks 3, 5, 8 and 10 size themselves from them:
  - `grep -rn "translate\." backend/src`
  - `grep -rn "\.conn\b" backend/src evals backend/scripts`
  - `grep -rn "FALLBACK_KEY\|STRUCTURED_KEY\|ACCOUNT_KEY\|with_account\|_without_fallback\|\bwithout_fallback\|\bstages(\|run_stages\|Stage(" backend/src backend/tests evals`. The last four alternatives were added 2026-10-09. H's public `inference.without_fallback` and its stage helpers carry the fallback too, and the original pattern missed them and their test users.
  - `grep -rnE "\.(stream|complete|single|decide_native)\(" backend/src evals backend/scripts`
  - `rg -lU "active_connection_id|fallback_connection_id|embeddings_connection_id|set_campaign_routing|active_connection\b" backend/tests`
- [ ] **Step 4: Start green.** Run:

  ```
  tests/test_inference_*.py tests/test_format2_play.py tests/test_llm.py
  tests/test_llm_structured.py tests/test_operation_guard.py
  tests/test_routing_guard.py tests/test_usage_guard.py tests/test_frozen_campaign.py
  ```

  Expected: all pass. A failure here belongs to a sibling slice: report it and stop.
- [ ] **Step 5:** If any assumption FAILs, stop. Report the failing line and the plan edit it needs, and wait for the controller. A12 and A13 are stop-on-absence checks (R2-5, R2-6). Otherwise report PASS, with A11 listed as an open item for the controller.

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
- [ ] **Step 4: Docs with code** (N16). Edit `CLAUDE.md` "Working notes". Before: "`GRIMOIRE_INFERENCE_AUTOMIGRATE=0` … turns off that birth stamp and the background migration together". After: it turns off the background migration only, and the birth stamp is always written. Run `tests/test_docs_guard.py`.
- [ ] **Step 5:** Run `tests/test_inference_storage.py tests/test_config_store.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_migrate.py tests/test_docs_guard.py`, then the gates.
- [ ] **Step 6:** Commit: `test(inference): a public birth seam, and legacy_store()`.

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
  - **Docs with code:** in `CLAUDE.md` "Working notes", the suite is born an upgraded default library at format 2 (`GRIMOIRE_TEST_BIRTH`), and "So the older play suites run at format 1" is deleted. (The birth-stamp sentence already moved in 3a.) Run `tests/test_docs_guard.py`.
  - Run every converted file, plus `tests/test_inference_storage.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_routes.py tests/test_character_turns.py`, then the gates.
  - Expected: all pass. A converted suite that fails has found a real format-1/format-2 difference: report it, and do not special-case it.
  - Commit: `test(inference): the suite is born an upgraded default library at format 2`.
- [ ] **CI checkpoint (Task 3).** The controller pushes, runs CI on the branch and waits for green. A suite the greps missed shows up here and is converted in 3d.

### Task 4: The shared planner (no runtime change)

The mapping moves into `legacy_plan.py`, and the derivation and the notes are added there, pure. `translate` and `migrate` delegate to it, so the runtime is unchanged and every existing equivalence test still passes. Task 5 makes play use it.

**Files:**
- Create: `backend/src/grimoire/store/inference/legacy_plan.py`, `backend/src/grimoire/store/inference_retired.py` (only `Note` and `note_id` in this task; N10), `backend/tests/test_inference_legacy_plan.py`.
- Modify:
  - `store/inference/translate.py`: `global_view`/`campaign_view`/`embedding_view` call the planner (deleted in Task 5).
  - `store/inference/migrate.py`: `global_fields`, `campaign_fields`, `_enriched` and `_stated` call the planner, and persist `Plan.mapped` only (N2). `_lookup` and `connection_reader()` (the lookup `fork._translated` hands `campaign_fields`; ruling 15) return `legacy_plan.lookup(mode="migrate")`.
  - `store/inference_keys.py`: `RETIRED_KEY = "inference_retired"` (ruling 15; nothing writes it until Task 6a).
  - `store/config.py`: `RETIRED_KEY` in `_CONFIG_KEYS`, default `""`, so `read_config` returns it.
  - `store/inference/__init__.py`.
  - Step 5 only: `llm_sampling.py`, `frontend/src/api/types.ts` (`ReasoningEffort`), `frontend/src/components/SamplerPresetEditor.test.tsx`.

**Interfaces** (in `legacy_plan.py`):

```python
Lookup = Callable[[str], dict | None]         # connection id -> raw connection, legacy fields included
                                              # (Task 5 moves the alias to llm_connections.Lookup; this name re-binds it)
PresetRead = Callable[[str], dict | None]     # preset id -> preset
REPRESENTABLE: tuple[str, ...]                # tuple(e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)

class Derived(NamedTuple): id: str; name: str; params: dict
# retired.Note (leaf module, N10):
#   Note(id, scope, subject, provider_id, effort, kind, text)
#   kind: "route_preset" | "unrepresentable" | "fact_not_carried"
#   id = retired.note_id(scope, subject, provider_id, effort, kind)   (never the text, N14)
class Plan(NamedTuple):
    mapped: dict[str, str]                    # the migration's fields: today's translation, byte for byte (N2)
    repoint: dict[str, str]                   # retirement's derived preset keys, applied over `mapped`
    facts: dict[str, dict[str, dict]]         # provider -> model -> facts overlay (global, format 1 only)
    presets: tuple[Derived, ...]              # the presets `repoint` names
    notes: tuple[retired.Note, ...]

def derived_name(base_name: str, effort: str) -> str
def derive(base: dict | None, effort: str, existing: PresetRead) -> Derived
def global_plan(cfg: Mapping[str, str], lookup: Lookup, presets: PresetRead) -> Plan
def campaign_plan(meta: Mapping[str, str], *, global_current: bool, lookup: Lookup, presets: PresetRead) -> Plan
def legacy_embeds(cfg: Mapping[str, str], lookup: Lookup) -> bool   # in place of embed_space.resolve (no cycle)
def lookup(*, mode: Literal["soft", "migrate", "retire"]) -> Lookup
    # "soft": translate's fail-soft read (play).
    # "migrate": migrate._lookup moved verbatim -- C's behaviour (A12): read_connection_strict,
    #   memoised, so a zero-byte, unfenced or `kind`-less file RAISES ConnectionUnreadableError
    #   and an absent one answers None. From Task 6b, a connection
    #   whose file exists and holds no non-empty MODEL_FIELDS value answers from the retirement
    #   record whenever the record holds an entry for that connection (R3-2: the entry proves a
    #   strip; config.md's marker is not consulted). The record is read strictly: an unparseable
    #   record raises RecordUnreadableError, so step 8 leaves the campaign unwritten and the §11.1 write
    #   path answers 409 retirement_unreadable (R2-2, R3-3).
    # "retire": raises ConnectionUnreadableError when the file exists and is unreadable, empty,
    #   unfenced or has no `kind` (C's read_connection_strict, as "migrate" reads it) -- N1. From Task 6b it falls back to
    #   the record, read strictly (N6).
```

- `global_plan`, by state:
  - **Below format 2:** `mapped` is the mapping (moved verbatim from `translate.global_view`), `_enriched`, and the Embedding role when `legacy_embeds`. `facts` is the facts overlay as values (what `migrate._stated` reads off each connection; `migrate._facts` keeps the writes). `repoint` is the derivation over the mapped selection slots.
  - **At format 2 and not retired:** `mapped` is empty, and `repoint` holds the derivation.
  - **Retired (`RETIRED_KEY`):** an empty plan.
- `campaign_plan`, by state:
  - **`global_current` false:** the mapping, as `translate.campaign_view` gives it below format 2.
  - **Global current, campaign unmarked:** `mapped` is the mapping, and `repoint` the derivation.
  - **Marked, not retired:** `repoint` only.
  - **Retired:** empty.
- Notes are ruling 5's, computed per scope.
- **Who persists what (N2).** `migrate` persists `mapped` and never `repoint`, so a persisted preset key always names a preset file that exists, and the slot keeps its base preset (for example "warm") for the derivation to read. Only `retire` persists `repoint`, after `put_derived`. `overlay` (Task 5) applies `mapped | repoint` in memory.

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
      # global_plan(cfg).mapped == the pre-Task-4 translate.global_view output, persisted (N2)
      # (recorded at the start of this task into the test as a literal per state; never regenerated)
  def test_the_facts_overlay_is_step_3s(home):             # vision/prefill/post_process -> facts, as `_stated` wrote them
  def test_legacy_embeds_agrees_with_embed_space_resolve(state):   # every baseline state: legacy_embeds(cfg) == (embed_space.resolve(cfg) is not None)
  def test_derived_presets_keep_the_wire_in_memory(home):
      # `glm` openai_compatible, effort "high", model "glm-5.3", sampler_preset "warm" (temperature 0.9);
      # Primary on it; fallback on `glm2` (effort "low", no preset); Saltmarch pins `scene`
      # to glm at a dangling preset id -> plan.repoint: role_primary_preset "warm-reasoning-high",
      # role_primary_fallback_preset "reasoning-low", Saltmarch use_scene_preset "reasoning-high";
      # plan.mapped keeps role_primary_preset "warm"
  def test_identical_derivations_collapse(home):           # two slots, same base and effort -> one Derived
  def test_a_preset_that_already_sets_reasoning_is_left_alone(home):
  def test_openrouter_and_non_glm_slots_derive_nothing(home):   # the openrouter_glm_with_effort shape
  def test_a_route_preset_over_a_glm_effort_is_noted(home):     # preset_summary="cold" over glm -> one Note(kind="route_preset")
  def test_a_retired_scope_plans_nothing(home):                 # RETIRED_KEY set -> Plan((), …) empty
  def test_a_marked_unretired_scope_plans_the_derivation_only(home)   # mapped == {}, repoint non-empty
  def test_the_migration_never_persists_a_repoint(home)         # N2: legacy GLM store, ensure() without retirement ->
                                                                # role_primary_preset == "warm", and every persisted preset key names an existing file
  def test_the_migrate_lookup_is_cs(home)                       # A12: mode="migrate" on a zero-byte file raises ConnectionUnreadableError,
                                                                # as C's landed _lookup does; absent -> None (2026-10-09: read "answers None")
  def test_the_retire_lookup_refuses_what_it_cannot_read(home)  # N1: zero bytes, unfenced, no `kind` -> ConnectionUnreadableError; absent -> None
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
  - `routes/common.py`, `routes/config.py`, `store/llm_connections.py` (`get_active` deleted; `Lookup` added, below).
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
  - It applies `mapped | repoint` for each scope (N2).
  - It builds its own fail-soft lookup (`lookup(mode="soft")`: an unreadable connection reads as absent, as the translation read it) and reads presets through `sampler_presets.read_preset`, so `resolve` names one function of the planner. From Task 6b the lookup falls back to the retirement record's `fields`.
- `resolve` holds the single call site: a private `_overlay(cfg, meta)` that calls `legacy_plan.overlay`. `resolve.resolve` resolves `_overlay(...).cfg`/`.meta` as format 2. A preset read checks `overlay.presets` first, and a facts read checks `overlay.facts` first.
- `resolve.retirement_notes(cid: str = "") -> tuple[retired.Note, ...]` reads `_overlay(...).notes`. `settings.view` gets its notes there and never imports the planner (N11).
- `llm_reasoning.glm_effort(model: str, value: str | None) -> str`: the preset's value only.
- `refuse_unmigrated` uses `keys.is_current` (I1).
- **Homes for the rest of `translate`'s API** (2026-10-09). `main` uses three more `translate` names beside the three views:
  - **`translate.is_current`.** It is a pass-through to `inference_keys.is_current`, so every caller calls that directly:
    - `resolve._current_layout` and `resolve.resolve`/`embedding`;
    - `embed_space` (four uses);
    - `routes/common.py`, where it is spelled `inference_translate.is_current`, at the `refuse_unmigrated` and rates checks.
  - **`translate.embedding_role`** (`settings._embedding_card`, `in_use._selections`, `embed_space` ×3) becomes `resolve.embedding_role(cfg) -> tuple[str, str]`.
    - It reads `role_embedding_provider`/`role_embedding_model` off `_overlay(cfg, {}).cfg`, stripped (the `embeddings_*` trim rule `embedding_view` kept).
    - It calls `_overlay`, so `resolve` still names `legacy_plan.overlay` exactly once.
    - `translate.embedding_view`'s format-2 branch becomes private in `resolve`, for `resolve.embedding`. Its legacy branch is the planner's mapping.
    - `settings` and `in_use` already import `resolve`, and `embed_space` imports it as `inference_resolve`.
  - **`translate.Lookup`** (annotations in `resolve`, `settings`, `in_use` and `migrate`) becomes `llm_connections.Lookup`.
    - That is the type of a raw-connection reader, beside `read_connection_raw`/`read_connection_strict`, in a module every one of them can import without a cycle.
    - `legacy_plan.Lookup` re-binds it, so `settings` and `in_use` annotate without naming the planner (N11).
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
  - `settings.view` and `in_use` read through `resolve`, so they show a format-1 store as the format-2 layout it will be written as. There is no legacy wording. `settings.view` also returns `retirement_notes`, from `resolve.retirement_notes` (Task 6b adds the record's). They are not rendered yet.
  - `embed_space`/`resolve.embedding` read `role_embedding_*` from `overlay.cfg` (D's note). `embed_endpoint(conn, current)` loses `current`.
  - `routes/config._public_config` is unchanged, so `ready` and the banner are unchanged.
  - `migrate` persists `global_plan(...).mapped`/`campaign_plan(...).mapped`, and never `repoint` (N2). Its `_run` early return is unchanged in this task.
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

    These may appear only in `ALLOWED = {"store/inference/legacy_plan.py", "store/inference/retire.py", "store/inference_retired.py", "store/inference_keys.py", "store/config.py", "store/llm_connections.py", "store/routing.py", "store/campaigns/lifecycle.py"}`. (`retire.py` and `retired.py` arrive in Task 6; the guard allows them by path.)
  - `test_only_resolve_migrate_and_retire_import_the_planner`. Three rules:
    - only `resolve.py`, `migrate.py` and `retire.py` import it (`settings.py` does not, N11);
    - `resolve.py` names exactly one function of `legacy_plan`, `overlay`;
    - it calls it exactly once, inside `_overlay`.
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
  - `store/frontmatter.py`: `RecordUnreadableError` and `read_record`, which are C's `migrate.RecordUnreadableError` and `migrate._record`, moved here. `retire` cannot import `migrate`, because `migrate.ensure` calls retirement.
  - `store/backups.py`: `RETIRE_PREFIX` and a `_RETIRE_NAME_RE` beside `_SAFETY_NAME_RE`, in every place that regex is read (N12):
    - `_PREFIXES`;
    - `_is_backup_artifact`, for the archive and its `.pre-retirement-…` temp;
    - `_is_abandoned_temp`, which `_sweep_abandoned_temps` uses;
    - `list_backups()`;
    - `_taken_at`, which `due()` reaches through `list_backups()` and which asserts that a name parses under one of the series it knows.

    `sweep` lists `_NAME_RE` alone, so it needs no change. A test holds that it never prunes the new prefix. With item 6 declined only (Step 4), the file also gains `create_backup(..., only=None)`, `RETIRE_SETTINGS_PREFIX` and `list_partial_backups()` (R2-7).
  - `store/sampler_presets.py`: `put_derived`.
  - `store/config.py`: `retire_write`; `birth_fields()` stamps `RETIRED_KEY` (N3).
  - `tests/inference_fixtures.py`: `UPGRADED_DEFAULT` gains `RETIRED_KEY: "1"`, so it stays equal to `birth_fields()` (R3-4).
  - `tests/test_inference_storage.py`: `test_a_product_store_is_born_with_the_marker_alone` asserts both markers, `FORMAT_KEY` and `RETIRED_KEY`, and still no Primary (R3-4).
  - `store/campaigns/lifecycle.py`: C's `publish_birth` gains a create-only keyword (for example `retired: bool = False`), which only `create_campaign` passes. With it set, the birth write adds `RETIRED_KEY` beside `_inference_marker()`'s fields when `config.md` carries it, read in the `config_lock` hold `publish_birth` already takes (N3, R2-1). `fork._copy` does not pass it. `_inference_marker()` and `set_campaign_inference` are unchanged. *(2026-10-09: this was a separate `lifecycle._birth_marker()`. C's docstring makes `publish_birth` "the one place a new marker is added".)*
  - `store/inference/legacy_plan.py`: `lookup(mode="retire")` (N1).
  - `store/inference/migrate.py`:
    - the retirement stage; `Status.retirement`; the note's `retire_safety`/`retire_failed`; the `_run` early return;
    - `RecordUnreadableError` and `read_record`, imported from `frontmatter`. `migrate.RecordUnreadableError` stays bound, so `test_inference_migrate.py`'s `pytest.raises(migrate.RecordUnreadableError)` holds, and `_record`'s callers move to `read_record` with no change in behaviour;
    - **the module docstring's step 4** (2026-10-09). It still reads "*(derived reasoning presets: slice I, ruling 3)*", but ruling 3 is dropped. Step 4 becomes a pointer: derived reasoning presets are retirement's, persisted after the marker by `retire` (rulings 4 and 6), and the migration persists `Plan.mapped` only (N2).
  - `store/locks.py`.
  - Docs: `docs/store-guarantees.md`, `CLAUDE.md`, `tests/test_docs_guard.py`.

**Interfaces:**
- `frontmatter.RecordUnreadableError(OSError)`: C's class, moved unchanged with its docstring. It stands for a record that is there but holds nothing: zero bytes, or a frontmatter block that is unfenced or never closed. A writer never replaces it.
- `frontmatter.read_record(path: Path, what: str, *, require: str | None = None) -> tuple[dict[str, str], str]`. It is C's `_record`, moved, plus `require`.
  - An absent file raises `FileNotFoundError`.
  - The read's own `OSError`/`UnicodeDecodeError` pass through, as in C, and every caller catches them beside `RecordUnreadableError`, as `migrate`'s callers do.
  - It raises `RecordUnreadableError` when the file is empty or whitespace, when its frontmatter does not parse (no opening or closing fence), and when `require` is given and is missing or empty in it.

  Otherwise it returns the meta and the body. With `require=None` it behaves exactly as C's `_record`, so the migration's behaviour does not change.
- `backups.RETIRE_PREFIX = "pre-retirement-"`, so archives are named `pre-retirement-grimoire-<stamp>.zip`.
  - It is kept by the same never-prune rule as C's `SAFETY_PREFIX` (`"pre-inference-"`).
  - It is listed by `GET /backups` beside that archive, in the `backups` list `list_backups()` returns.
  - Its `.pre-retirement-…` temp is recognised by `_is_backup_artifact` and cleaned by `_sweep_abandoned_temps` (N12).
- `config.retire_write(set_keys: Mapping[str, str], drop: Iterable[str]) -> None`. In one `config.format_hold()`, it calls `read_record(config path, "config.md", require=FORMAT_KEY)`, refuses a non-current marker, sets and drops, and writes atomically. That hold is `config_lock` with the format read inside it, and it raises `NewerFormatError` on a newer store. `retire_write` never adds a key outside `set_keys`.
- `sampler_presets.put_derived(pid: str, name: str, params: dict) -> None`. It writes when absent and does nothing when identical. It raises `ValueError` when the id holds something else, which `derive`'s digest suffix makes unreachable.
- In `retire.py`:
  - `retire_global(lookup: Lookup) -> tuple[Note, ...]`, in one hold of `llm_connections.LOCK`. On `main` that lock *is* `config_lock`, so `retire_write`'s `format_hold` re-enters it. It reads `config.md` strictly and computes `global_plan` with `lookup(mode="retire")`. It writes each derived preset, then calls `config.retire_write(plan.mapped | plan.repoint | {RETIRED_KEY: "1"}, drop=LEGACY_GLOBAL_KEYS + global route keys)`.
    - On an already-retired `config.md` that holds a legacy key again, it writes the deletion only (N5).
    - A `ConnectionUnreadableError` from the lookup stops it before any write (N1).
  - `retire_campaign(cid: str, lookup: Lookup) -> tuple[Note, ...] | None`. The caller holds the campaign lock; like `migrate.campaign`, it raises `RuntimeError` when `locks.holds_campaign(cid)` is false. Inside the hold, and as `migrate.campaign` does inside one `config.format_hold()`, it calls `read_record(campaign.md, …)` and re-reads the marker:
    - **newer:** returns `None`, nothing written;
    - **unmarked:** `campaign_plan(meta, global_current=True, …)` carries the migration's fields (step 8's work) and the derivation;
    - **marked:** the derivation only.

    - **retired, holding a `route_*` key again:** the deletion only (N5).

    It writes its plan's derived presets first, with `put_derived` under `config_lock` (N19). Then comes one atomic `campaign.md` write, holding `mapped | repoint`, `FORMAT_KEY` and `RETIRED_KEY`, with the `route_*` keys deleted and `updated` left alone. Then `revision.bump(cid)`. A `ConnectionUnreadableError` from `lookup(mode="retire")` stops it before any write (N1).
  - **Only a non-empty legacy value is work** (N3, I2; 2026-10-09). On `main` a `config.md` is born holding every `LEGACY_GLOBAL_KEYS` member as `""`.
    - `config.read_config` materialises its defaults on the first read, and those defaults include `active_connection_id`, `fallback_connection_id`, `embeddings_connection_id`, `embeddings_model` and every `route_*` (`routing.CONFIG_KEYS`), all `""`.
    - `born_current()` (`automigrate()`, true in a product build) puts `inference_format: "2"` beside them.
    - Every store a C–H build created, and every store this build creates until 6b takes the legacy defaults out of `read_config`, therefore holds those 16 empty keys.
    - So a key whose value is empty or whitespace is the same as an absent key, everywhere retirement counts work:
      - `left()` never names it;
      - `pass_plan` plans no deletion for it;
      - `needs_archive` never counts it;
      - no write is made for it alone.
    - A write the pass makes anyway, the marker for example, drops it with the rest of the scope's legacy keys. The same holds for a `route_*` key in a `campaign.md`, and for `MODEL_FIELDS` (I2, 6b).
  - `left() -> tuple[str, ...]`, **fail-soft, never raises**, one human-readable item each:
    - `config.md` unretired or unreadable, or retired but holding a **non-empty** `LEGACY_GLOBAL_KEYS` member or a non-empty global `route_*` key (N5);
    - a campaign unretired, unmarked or unreadable, or retired but holding a **non-empty** `route_*` key (N5). A newer campaign reads "written by a newer build; the strip waits for it" (N18);
    - a connection unreadable, or holding a non-empty `MODEL_FIELDS` value (6b).
  - `pass_plan(lookup) -> PassPlan`: the whole pass, built before any write. It covers the global plan, each readable campaign's plan, the stray legacy keys in retired scopes, and the strip candidates (R2-3).
  - `needs_archive(pass_plan: PassPlan) -> bool`: true when any unit of the pass deletes or replaces a stored **non-empty** value (a legacy key, a field, a repointed preset key). It is false only when the whole pass adds nothing but markers, plus the deletion of keys whose values are empty (N3, R2-3).
- `migrate`:
  - `_retire(run)` runs after the marker, and on every `ensure` of a current store:
    0. `pass_plan(...)`, computed once;
    1. the archive (ruling 6.0), when `needs_archive(pass_plan)`, before the pass's first write of **any** kind, marker-only writes included. It is reused when the note names one that exists (R2-3);
       - each unit (global, a campaign, a stray-key deletion, a connection's strip) re-plans inside its own hold. If that in-hold plan deletes or replaces a value and the pass took no archive, the unit is skipped and left for the next run (R3-1);
       - a `ConnectionUnreadableError` raised while building `pass_plan` drops only the units that name that connection, not the whole plan (R3-1);
    2. `retire_global`;
    3. `retire_campaign` for each campaign under `campaign_lock_nowait`, where busy, unreadable or a strict lookup failure leaves that campaign;
    4. the strip (6b).

    A `RecordUnreadableError` (or the read's own `OSError`/`UnicodeDecodeError`) or a `ConnectionUnreadableError` stops only its item, and every scope that names it. Each item checks `run.halted()` first (N19).
  - `_run`'s early return `if current and not left` becomes `if current and not left and not retire.left()`.
  - `Status` gains `retirement: dict = {"left": [...], "failed": ""}`. `state` is computed exactly as before.

- [ ] **Step 1: Write the failing tests** (`test_inference_retire.py`), on legacy stores then `migrate.ensure()`:

  ```python
  def test_retirement_takes_a_pre_retirement_archive_first(home)       # format-2 store: one RETIRE_PREFIX archive, taken before any write
  def test_a_failed_retirement_archive_writes_nothing(home, monkeypatch):   # create_backup raises -> no preset file, no repoint,
                                                                              # no deletion, and no RETIRED_KEY: the archive precedes every write of the
                                                                              # pass, marker-only ones included (R2-8); a chat turn plays identically
  def test_a_resumed_pass_with_only_campaign_work_left_takes_an_archive(home)   # R2-3: config.md retired, one campaign needing a repoint,
                                                                              # no .cache note -> one RETIRE_PREFIX archive, taken before that campaign's write
  def test_a_resumed_pass_with_only_the_strip_left_takes_an_archive(home)    # R2-3: every scope retired, fields still on connections
  def test_a_marker_only_pass_takes_no_archive(home)                    # R2-3: a C-era campaign with no legacy key -> RETIRED_KEY only, no archive
  def test_a_fresh_install_with_empty_legacy_keys_takes_no_archive(home)   # N3: config.md written as a C-H build births it on main
                                                                        # (inference_format "2", no RETIRED_KEY, all 16 LEGACY_GLOBAL_KEYS
                                                                        # present as "") and one campaign born the same way -> retire.left()
                                                                        # names only the missing markers, needs_archive(pass_plan) is False,
                                                                        # ensure() takes no RETIRE_PREFIX archive and writes RETIRED_KEY
                                                                        # only; a second ensure() writes nothing; with the marker already
                                                                        # present and the empty keys still there, left() is empty and
                                                                        # ensure() writes nothing at all
  def test_a_unit_that_grew_work_after_planning_is_left_for_the_next_run(home, monkeypatch)   # R3-1: the pass plans marker-only; a patched hook
                                                                              # writes a route_scene into the campaign before its unit -> that unit writes
                                                                              # nothing; the next ensure() takes the archive and retires it
  def test_an_unreadable_connection_drops_only_its_units_from_the_pass(home)   # R3-1
  def test_the_archive_is_taken_once_per_root_and_reused_on_resume(home)    # a later step fails; the next run reuses it
  def test_a_format_1_store_retires_under_its_pre_inference_archive(home)   # one archive, pre-inference-, none pre-retirement-
  def test_a_reused_pre_inference_archive_does_not_stand_in(home)       # N13: a pre-inference- archive kept from an earlier run -> a pre-retirement- one is taken
  def test_a_product_birth_store_takes_no_archive_and_writes_nothing(home)   # N3: product_birth; ensure() twice -> no RETIRE_PREFIX file in
                                                                              # backup_dir(), home digest unchanged, config.md born with RETIRED_KEY
  def test_a_new_campaign_on_a_retired_store_triggers_no_pass(home)     # N3: born with RETIRED_KEY; retire.left() empty; ensure() writes nothing
  def test_a_new_campaign_on_an_unretired_store_joins_the_next_pass(home)   # N3: no RETIRED_KEY at birth; the next ensure() retires it
  def test_a_settings_write_never_marks_a_campaign_retired(client, home)    # R2-1: config.md retired; Saltmarch marked, unretired, its scene pin on
                                                                            # glm (effort "high", preset "warm"); set_campaign_inference (a role edit) ->
                                                                            # no RETIRED_KEY, use_scene_* and the in-memory effort intact; the next
                                                                            # ensure() retires it with use_scene_preset "warm-reasoning-high"
  def test_an_unmarked_campaign_written_on_a_retired_store_is_not_marked_retired(client, home)   # R2-1: the §11.1 path migrates it
                                                                            # (FORMAT_KEY) but never stamps RETIRED_KEY; the next pass retires it
  def test_a_c_era_writer_keeps_the_retired_marker(home)                # N17: config.write_config, set_campaign_inference and a connection
                                                                        # edit each leave RETIRED_KEY where it was
  def test_sweep_never_prunes_a_pre_retirement_archive(home)
  def test_backups_lists_a_pre_retirement_archive(client)
  def test_derived_presets_keep_the_wire(home)                          # the Task 4 store: effective before == after; presets on disk
  def test_retirement_repoints_and_deletes_in_one_write(home)           # no LEGACY_GLOBAL_KEYS, no route_* in raw files; RETIRED_KEY; every rev unchanged
  def test_space_ids_survive_retirement(home)                           # embed_space.endpoint()["space"] before == after
  def test_a_campaign_skipped_by_step_8_is_migrated_before_it_is_retired(home)   # C2: busy at step 8, free at retirement:
                                                                                  # use_scene_* present, route_scene gone, one write
  def test_a_newer_campaign_is_never_retired(home)                      # C2
  def test_retire_global_refuses_a_config_it_cannot_parse(home)         # I4: zero bytes, a truncated fence, no marker -> RecordUnreadableError; file bytes unchanged
  def test_retire_campaign_refuses_an_unparseable_campaign(home)        # I4: same three cases; campaign.md bytes unchanged; the others retire
  @pytest.mark.parametrize("damage", ["zero_bytes", "unfenced", "no_kind"])
  def test_an_unreadable_connection_holds_the_scopes_that_name_it(home, damage)   # N1: the GLM connection a global slot and an
                                                                        # unmarked Saltmarch pin name is damaged -> config.md and campaign.md bytes unchanged,
                                                                        # no RETIRED_KEY on either, no preset written; once repaired, the next run retires both
  def test_a_busy_campaign_is_retired_on_the_next_run(home)
  def test_retirement_is_idempotent(home)                               # ensure() twice -> the second writes nothing (digest of home)
  def test_retirement_never_moves_the_migration_state(home)             # a product-birth store reads "done"; a store with left items reads "done" and names them in retirement.left
  def test_status_never_raises_on_an_unreadable_connection(home)        # I4: retirement.left names it
  def test_two_devices_write_the_same_bytes(tmp_path)                   # two copies retired separately -> identical files
  ```

  Plus `test_planned_equals_persisted` (Task 5), whose persisted side is now retired, and `test_a_migrated_and_retired_copy_plays_a_turn` in `test_frozen_campaign.py`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** in ruling 6's order. Classify `retire.py` in `store/locks.py` beside `migrate`, in the same list and for the same reason: its campaign step runs under `campaign_lock_nowait`, taken by the caller.
  - **Birth fixtures (R3-4).** Update `UPGRADED_DEFAULT` and `test_a_product_store_is_born_with_the_marker_alone` in the same commit. From here on the suite is born retired, so a test that needs an unretired format-2 store (Tasks 4–6's derivation cases, for example) removes `RETIRED_KEY` from `config.md` explicitly, through a `inference_fixtures.unretired()` helper.
- [ ] **Step 4: The archive's form, by ratification item 6** (N7). Run exactly one branch, by the answer recorded in Task 1's report.
  - **Approved:** the full archive, `backups.create_backup(prefix=RETIRE_PREFIX)`.
  - **Declined (the fallback):** `backups.create_backup` gains `only: Iterable[str] | None = None`, a set of root-relative globs. Retirement passes `retire.TOUCHED = ("config.md", "campaigns/*/campaign.md", "llm_connections/*.md", "sampler_presets/**", "inference-retired.json")`.
    - The archive uses its own prefix, `backups.RETIRE_SETTINGS_PREFIX = "pre-retirement-settings-"`, and is never pruned (R2-7).
    - It is **not a restore point.** `list_backups()` (which promises "each is a whole store to restore from") never includes it, so `backups.due()` never counts it, and a partial archive never postpones the next scheduled full backup.
    - `GET /backups` lists it apart through a new `list_partial_backups()`, each row marked `partial: true`, the way `list_image_backups()` keeps `create_image_backup`'s archives apart.
    - `_sweep_abandoned_temps` cleans its temp too.
    - The run's note records it under the same `retire_safety` key, so it is reused on resume.
    - Tests:
      - `test_a_settings_only_archive_holds_exactly_the_touched_files`: its zip's name list equals the glob expansion, and no scene file is in it.
      - `test_a_settings_only_archive_is_not_a_restore_point` (R2-7): with only that archive in `backup_dir()`, `list_backups()` is empty and `due()` is True; `GET /backups` shows it with `partial: true`; and a later full backup is not delayed by it.
    - Docs: the Retirement section says which files the archive holds.
- [ ] **Step 5: Docs with code** (CR10).
  - `docs/store-guarantees.md` "Model settings migration" gains "### Retirement". It covers:
    - the order;
    - "no archive, no write", and the `pre-retirement-grimoire-` archive, never pruned;
    - derive before delete, and migrate before retire;
    - fail closed;
    - `rev` kept;
    - the markers;
    - that retirement never moves the status `state`;
    - that a fresh install and a new campaign are born retired and take no archive (N3);
    - that a campaign a newer build marked holds the strip until this build can read it as retired (N18).
  - `CLAUDE.md` "Model settings moved…" gains one sentence on retirement and names the `pre-retirement-grimoire-` archive.
  - `test_docs_guard.test_store_guarantees_names_the_inference_migration` also holds `backups.RETIRE_PREFIX` and `migrate`'s retirement entry point, in the section and in `CLAUDE.md`.
- [ ] **Step 6:** Run:

  ```
  tests/test_inference_retire.py tests/test_inference_migrate.py tests/test_inference_legacy_plan.py
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_frozen_campaign.py
  tests/test_sampler_presets_store.py tests/test_backups*.py tests/test_config_store.py
  tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_legacy_reader_guard.py
  tests/test_campaign_lifecycle*.py tests/test_inference_storage.py tests/test_inference_legacy_plan.py
  tests/test_inference_format2.py tests/test_docs_guard.py
  ```

  Then the gates.
- [ ] **Step 7:** Commit: `feat(inference): retirement archives, derives, repoints and deletes the legacy keys, failing closed`.

#### Task 6b: The record, the strip, and the notice

**Files:**
- Create: `frontend/src/components/inference/RetiredNotes.tsx` (+ `.test.tsx`).
- Modify:
  - `store/inference_retired.py` (created in Task 4 with `Note`): the record.
  - `store/inference/retire.py`: `strip`; `left` widened; the facts check (N9).
  - `store/inference/legacy_plan.py`: `lookup` falls back to the record's `fields` for a connection whose file exists and holds no non-empty `MODEL_FIELDS` value. The `retire` mode, and the `migrate` mode whenever the record holds an entry for that connection, read the record strictly (N6, N20, R2-2). Per R3-2, the entry proves a strip happened; `config.md`'s retirement marker is not consulted. *(2026-10-09: this read "the `migrate` mode once `config.md` is retired", which R3-2 replaced.)*
  - `store/inference/migrate.py`: step 8 (`_campaign_step` → `migrate.campaign`) and the §11.1 write path build their lookup with `mode="migrate"`, which now reaches the record for any connection the record holds (R3-2).
    - Step 8 treats `RecordUnreadableError` as "leave this campaign". It already does: C's step 8 catches `OSError`, and the error is one.
    - The §11.1 write path's route answers **409** with detail `{"kind": "retirement_unreadable", "detail": "The record of retired model settings could not be read; try again once it has synced."}`, following D's `facts_unreadable` precedent. It is never a 500 (R3-3).
  - `store/llm_connections.py`: `strip_model_fields`; `_write_raw`; `ensure_migrated`; `_dangling`.
  - `store/config.py`: the legacy defaults out of `read_config` (kept in `_CONFIG_KEYS`).
  - `store/inference/settings.py`: `retirement_notes`.
  - The settings route module: dismiss.
  - `frontend/src/routes/ModelsView.tsx`, `frontend/src/api/types.ts`, `client.ts`, `routes/ConfigView.tsx`.
  - Docs: `docs/store-guarantees.md`.

**Interfaces:**
- `retired.py` (ruling 16):
  - `record_fields(conn_id, values: Mapping[str, str]) -> None`. It keeps any values already recorded for `conn_id` and adds only keys not yet there, so the first values (the ones C translated) win (N20).
  - `record_notes(notes: Iterable[Note]) -> None`, which merges by id and never un-dismisses;
  - `dismiss(note: Note | str) -> bool`;
  - `read(*, strict: bool = False) -> dict`. Fail-soft for display. `strict=True` is for every writer and for `lookup(mode="retire")`: it raises `frontmatter.RecordUnreadableError` on a non-empty file whose JSON does not parse (N6).

  Writes go through `store.atomic` under `retired._lock`. **Lock order:** `llm_connections.LOCK` → `retired._lock`, never the reverse. Nothing that holds `retired._lock` takes another lock (N4).
  - *On `main`:* `llm_connections.LOCK` is `config_lock`. That lock's docstring (`locks.config_lock`), and the comment on `llm_connections.LOCK`, both say the only lock taken under it is `inference.facts`' process-local `_lock`. 6b adds `retired._lock` to both, under the same rule: it is process-local and innermost.
- `llm_connections.strip_model_fields(conn_id: str) -> bool`. It holds `llm_connections.LOCK` across all three steps, so a `PUT /llm-connections/{id}` waits rather than being overwritten (N4).
  - That lock is `config_lock`: a `_ProcessScopedLock`, an `RLock` plus an OS file lock. A waiter gives up with `StoreBusy` (409) after 30 s, and is never overwritten.

  The three steps:
  1. `frontmatter.read_record(path, f"connection {conn_id}", require="kind")`, for the raw meta that step 3 rewrites. `read_connection_strict` returns only the known fields, so it cannot be used here;
  2. `retired.record_fields(conn_id, <its non-empty MODEL_FIELDS>)`;
  3. a raw frontmatter rewrite minus `MODEL_FIELDS`, with `keep_rev`.

  It returns whether it wrote.
- `retire.strip(lookup) -> list[Note]`.
  - **Precondition.** `config.md` carries `RETIRED_KEY`; every campaign reads, is marked and carries `RETIRED_KEY`; every connection file reads. Otherwise it writes nothing, and the reason is in `left()`.
  - It takes `llm_connections.LOCK` per connection and **re-checks the precondition inside that hold**, then runs that connection's facts check and `strip_model_fields` (N4). The re-check reads each `campaign.md` without taking its campaign lock: `config_lock` is never held around a campaign lock (`locks.config_lock`'s docstring).
  - **The facts check (N9).** For each of the connection's non-default `vision`, `prefill` and `post_process`, it compares against its model's facts. The read is strict, and a `FactsUnreadableError` stops that connection's strip. Where the facts state no value for the field, a `Note(kind="fact_not_carried", subject=<field>, provider_id=<id>)` is recorded **before** the strip. The facts are never written here: the user's word is never overwritten, and C re-copies them before its switch (A13). This note covers whatever still differs.
- `llm_connections._write_raw` omits every `MODEL_FIELDS` key whose value is empty. A read already defaults them to `""` (I2).
- `ensure_migrated` seeds model fields and `active_connection_id` only below format 2 (ruling 7, I2).
- Retirement persists each scope's notes with `retired.record_notes` in the same pass.
- `settings.view` returns `retirement_notes`: the record's undismissed notes, plus `resolve.retirement_notes()`' planner notes not yet recorded (ids collapse; N11).
- The dismiss route: `POST /api/inference/retired-notes/{note_id}/dismiss` → 200. It calls `refuse_newer()` only. It is a settings write that spends nothing and touches no campaign.
  - A note the planner computed but the record does not hold yet is recorded first, already dismissed (N14).
  - It answers 404 only for an id that neither the record nor the planner knows.
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
  def test_a_connection_edit_during_the_strip_survives(client, home)    # N4: a patched retired.record_fields starts a PUT /llm-connections/glm
                                                                        # (new key) in a thread and asserts it is still blocked; after the strip, join:
                                                                        # the new key and its new rev are in the file, and no MODEL_FIELDS key is
  def test_an_unreadable_record_holds_a_late_unmarked_campaign(home)    # N6, R2-2: after the strip, corrupt inference-retired.json, add an unmarked
                                                                        # Saltmarch with route_scene: spare; run ensure() with step 8 active (not
                                                                        # retire_campaign alone) -> step 8 raises RecordUnreadableError for it and leaves
                                                                        # campaign.md bytes unchanged, retirement.left names it; once the record is
                                                                        # repaired, step 8 migrates it with spare's recorded model and retirement retires it
  def test_the_write_path_reads_the_record_on_a_retired_store(client, home)   # R2-2: same late Saltmarch, reached first by a campaign
                                                                        # settings write (§11.1) -> use_scene_model is spare's recorded model, never "";
                                                                        # with the record corrupted: status 409, detail kind "retirement_unreadable",
                                                                        # and nothing is written (R3-3)
  def test_the_record_fallback_needs_no_config_marker(home)             # R3-2: strip, then drop RETIRED_KEY from config.md by hand -> step 8 still
                                                                        # persists spare's recorded model for a late unmarked campaign
  def test_an_empty_legacy_field_still_falls_back_to_the_record(home)   # N6: a C-H edit wrote model: "" after the strip -> the fallback answers
  def test_the_record_never_answers_for_a_deleted_connection(home)      # N20
  def test_the_first_recorded_fields_win(home)                          # N20: a pre-C build re-adds model: "other"; the next strip keeps the first value
  def test_a_fact_the_migration_skipped_is_noted_before_the_strip(home) # N9: C's facts write for glm raised FactsUnreadableError, so glm
                                                                        # carries prefill: true with no stated fact -> one fact_not_carried note, recorded
                                                                        # before the field goes; a stated fact gives no note
  def test_the_strip_records_the_fields_first_and_the_planner_reads_them(home)   # C3, R2-2: after the strip, write an unmarked Saltmarch with
                                                                                # route_scene: spare -> resolves spare's model in memory; the next ensure(),
                                                                                # with step 8 active, persists use_scene_model == spare's recorded model and
                                                                                # its preset (never ""), and retirement then retires it
  # N5: one scope per test, each writing ONLY that scope's legacy key, so each fails for its own reason first
  def test_a_legacy_config_key_written_after_retirement_is_ignored_then_removed(home)     # active_connection_id -> resolve unchanged;
                                                                                          # retire.left() names it; ensure() deletes it alone
  def test_a_legacy_campaign_key_written_after_retirement_is_ignored_then_removed(home)   # route_scene in a retired campaign.md
  def test_a_legacy_connection_field_written_after_retirement_is_ignored_then_removed(home)   # reasoning_effort on a stripped connection
  def test_a_product_birth_store_reads_done(home)                       # I2 (product_birth marker)
  def test_a_connection_edit_after_retirement_writes_no_legacy_field(client)    # I2: rename and key change -> no MODEL_FIELDS key in the file
  def test_a_fresh_store_never_gets_active_connection_id(home)          # product_birth; list_connections(); raw config has none
  def test_retired_notes_are_durable_and_dismissable(client, home)      # a route-preset note survives a second run; dismissed stays dismissed after
                                                                        # another retirement pass; a second root reading the same files sees it
  def test_a_planner_note_can_be_dismissed_before_retirement(client)    # N14: format-1 store; dismiss -> recorded dismissed; never shown again
  def test_a_note_id_ignores_its_wording(home)                          # N14: same fields, different text -> same id
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
  - The three `_noting(client, resolved.conn, holder)` sites in `decide` `around` hooks: G's two (A2), absorb's identity check at `routes/scenes.py:2121` and the reconcile sweep at `routes/continuity.py:689`, and F's voice-drift hook at `routes/scenes.py:2653`, which the grep in A2 also prints. They read `resolved.chain.primary`'s display facts from this task. *(2026-10-09: this named G's two only.)*
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

**Files:** Create `backend/src/grimoire/adapters.py`, `backend/tests/test_adapter_registry.py`. Modify `store/inference/resolve.py` (`target_for`, moved here from Task 10 so that every target builder exists before 9b; N8). Production still dispatches dicts.

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

- `resolve.target_for(raw: dict, model: str, sampling: dict, *, model_facts: dict) -> wire.Target`. It is the same builder `_attempt`'s `_target` (Task 7) uses, for a target outside any route: `controls.preview`, `routes/config._with_effective` and the model test's two probes. Task 10 points those callers at it.

The classes are `OpenRouterAdapter`, `OpenAICompatibleAdapter`, `AnthropicAdapter` and `ClaudeAgentAdapter`, each wrapping its client. In this sub-task their bodies are copies of today's per-kind code in `LLMClient._provider`, `list_models`, `check`, `decide_native` and `native_body`. 9b deletes the facade's copies.

**H's native table is absorbed here** (2026-10-09). H landed `llm.NATIVE_DECISION_KINDS: dict[str, NativeAdapter]`, which is already a per-kind dispatch table.
- Each `NativeAdapter(body, client, conn_fields)` is a pure body builder `(item, model) -> dict`, the name of the `LLMClient` attribute that sends it, and the connection fields that adapter's `decide` takes by name. Today the table holds `openrouter` (no extra fields) and `openai_compatible` (`conn_fields=("base_url",)`).
- So:
  - `decides_natively` is True for exactly the table's kinds. Its docstring says that it answers for the *kind*, unlike `resolve.decides_natively(attempt)`, H's function of the same name, which says one *model* is not known unable. `test_adapter_facts_agree_with_the_registry` asserts that, against `providers.PRESETS[*].never`: `anthropic` and `claude` list `decide_native`, the `openai` preset does not, and every other `openai_compatible` preset does.
  - Each adapter's `decision_body` is its table entry's `body`, given `target.model`.
  - The `openai_compatible` adapter's `decide` reads `target.base_url` where the table names `base_url`.
- The table stays beside the registry in this sub-task. 9b deletes the facade's per-kind copies but keeps the table for `decide_native`, which still takes a dict until 9c. 9c deletes `NATIVE_DECISION_KINDS` and `NativeAdapter`, and moves `evals/runner.chain`'s kind check (`kind not in llm.NATIVE_DECISION_KINDS`) to the registry flag.

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
  def test_decides_natively_is_the_native_table(): # {k for k, a in registry.items() if a.decides_natively} == set(llm.NATIVE_DECISION_KINDS)
  def test_target_for_matches_the_attempt_target(state):         # every baseline state: target_for(raw, model, sampling, facts) == attempt.target
  ```

- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement. **Step 4:** Run `tests/test_adapter_registry.py tests/test_inference_target.py tests/test_import_guard.py tests/test_format2_play.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`, then the gates.
- [ ] **Step 5:** Commit: `feat(llm): the adapter registry, beside the facade`.

#### Task 9b: The facade on chains, behind a shim

**Files:** `llm.py`, `llm_sampling.py` (`effective`, `split` and `not_applicable` take `Target | dict`; N8), `llm_usage.py` (`account(usage, target)`), `model_guidance.py` (`for_target(target)`), `health.py` (`record(target, error)`), `store/post_images.py` (`images_for(target)`, `reach(target | None)`), `routes/common.py` (`build_llm`'s `images` callable), `wire.py` (`from_lowered`), `tests/llm_fakes.py` (dual recording), `docs/store-guarantees.md` (E's cancel window).

**Interfaces:**
- `LLMClient`:
  - `stream(messages, chain: Chain | dict, usage=None, *, schema=None, retries=None)`, and `complete(…)` with the same arguments;
  - `single(messages, target: Target | dict, usage=None)`;
  - `list_models(target)`, `check(target)`, `note_outcome(target, error)`.

  Each entry point passes its argument through **one** private `_as_chain`, which calls `wire.from_lowered` on a dict. That is the shim. The constructor keeps its client keywords (the test seams). Its `fallback=` keyword is deleted, because every call carries its chain.
- `wire.from_lowered(conn: dict) -> Chain`: **standard library only** (#239). It knows `"_fallback"`, `"_structured"`, `"_account"` and `"_degrade"` as literals.
  - It is temporary. It stays until the model-test probes and the dict callers below stop needing it, and **Task 10 deletes it with them** (N8). Task 10's guard bans the name.
- `llm_sampling.effective(x: Target | dict)`, `split(x)` and `not_applicable(x, why)` take a `Target`. A dict goes through `from_lowered(x).primary` (N8). These are the dict callers that keep that door open until Task 10:
  - `store/inference/controls.preview` (`controls.py`'s `llm_sampling.effective(lowered)`);
  - `store/inference/resolve._attempt` (the attempt's `controls`) and H's `_chain` (`not_applicable(conn, WHY_NATIVE)`);
  - `routes/config._with_effective`;
  - the model test's probes (Task 9c).
- `post_images.reach(x: Target | dict | None)` and `health.record(x: Target | dict, error)` take the same door (R2-4), for three dict callers that move in Task 10:
  - `routes/config.py`'s `store.post_images.reach(chat.conn)`;
  - `routes/config.py`'s two `registry.record(conn, …)` calls (the model test's outcome).
- `llm.fallback_sampling(primary: Target, fallback: Target) -> Target`; `llm._same_route(a, b)` by `provider_id`; `llm.prefill_capable(target) -> bool`. The degrade sibling is `dataclasses.replace(target, degrade=True)`. `llm.ATTEMPTED` holds the `Target`.
- `_routes(chain)` builds `[(primary, retries), (fallback_sampling(primary, fallback), 0)]`. `_usable_routes` drops a fallback whose kind is in `adapters.TEXT_ONLY_KINDS` for image-bearing messages. `_dispatch` calls `self._adapters[target.kind].generate(…, schema=schema if target.structured else None)`.
- **E's `_estimate` call stays exactly where `_resilient` makes it** (ruling 11), with a comment naming the window.
- `inference.generate`/`decide`/`note_outcome` **still hand dicts** in this sub-task, through the shim. `decide_native` still takes a dict, and `llm_usage.with_account` stays, both until 9c. So does `llm.NATIVE_DECISION_KINDS`, which 9c deletes (9a).
- **The ledger row keeps its shape** (2026-10-09). E and H file `operation`, `role`, `billing` and `decision_mode` from the account block (`usage.Meter.SERVED`). Moving that block from `ACCOUNT_KEY` to `Target.account` must file the same fields with the same values.
  - The values matter. `usage._modellable` refuses to model a row whose `decision_mode` is `decisions.NATIVE_BACKEND` (`"native"`). A native row that lost the field would start being priced at chat rates.
  - `usage_rollup.VERSION` is `6` and stays `6` while the row shape is unchanged. A change to any row field bumps it to `7` in the same commit, so an older rollup file is never read with the new rows folded in.
  - A GLM row's `preset` names the derived preset after retirement. That is a value, not a field, so the version does not move.
  - Test: `test_the_ledger_row_is_unchanged_through_the_shim`. For a generate call and a structured decide call, each driven once through a dict and once through its `Chain`, the filed rows are equal field for field.
- `FakeLLM.stream/complete/single` accept a dict or a `Chain`/`Target`. They record `request["chain"]`/`request["target"]` always (converting a dict with `wire.from_lowered`), and `request["conn"]` only when handed a dict. So every existing assertion holds.

- [ ] **Step 1:** Write these tests, then run them. Expected: FAIL.
  - `test_the_shim_is_the_only_dict_door`: in `llm.py`, only `_as_chain` calls `from_lowered`.
  - `test_from_lowered_round_trips_every_baseline_attempt`: `from_lowered(attempt.conn) == Chain(attempt.target, fallback target)` for every state.
  - `test_effective_answers_the_same_for_a_dict_and_its_target`: for every baseline attempt, `effective(attempt.conn) == effective(attempt.target)`.
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

**Files:** `inference.py` (`Stage`, `stages`, `run_stages`, `_Call`, `_structured`, `_native`, `_with_mode`, `_without_mode`, `_stops_chunks`, `_same_connection`, `without_fallback` deleted, `Capture`), `llm.py` (`decide_native`, `native_body`; `NATIVE_DECISION_KINDS` and `NativeAdapter` deleted, 9a), `llm_usage.py` (`with_account` deleted), `routes/config.py` (H's probe), `routes/character_turns.py` (`_capture`), `evals/runner.py` (`--decide-backend` chains), `tests/llm_fakes.py` (`decide_native`), and the decide suites.

**Interfaces** (ruling 9; A3, A4, A7, A8):
- `Stage(mode: str, chain: wire.Chain, retries: int | None)`.
  - `stages(resolved)` keeps H's rule exactly. "Without `FALLBACK_KEY`" becomes `Chain.alone()`. The structured stage carries `Chain(A0.target, A1.target)` exactly when the resolver attached a structured fallback.
  - `_Call.chain` replaces `_Call.conn`.
  - `_with_mode(chain, mode)` is `chain.with_account(decision_mode=mode)`.
- `LLMClient.decide_native(item, target: Target, usage=None, *, retries=None)`. It dispatches `self._adapters[target.kind].decide`, and keeps H's refusals, retries, observer and capture. `llm.native_body(item, target)`.
- `Capture = Callable[[list[dict], dict, Target], Awaitable[None]]`. A native stage passes `target.without_sampling()`. `_capture(cid, sid, task, messages, target, outcome=None)` reads `.kind`/`.model`/`.provider_id`.
- H's probe: `client.decide_native(probes.PROBE_ITEM, target.with_account(operation="decide", decision_mode="native"), m.usage, retries=0)`, where `target` is the model test's `Target`. Until Task 10 it is `wire.from_lowered(conn).primary`, and Task 10 moves both probes to `resolve.target_for` and deletes `from_lowered` with them (N8).
- `evals/runner`: the forced one-stage chains use `Stage("native"|"structured", Chain(A0.target), None)`, in place of `inference.without_fallback(primary.conn)`. The native refusal's kind check reads the registry's `decides_natively` flag in place of `kind not in llm.NATIVE_DECISION_KINDS` (9a). The preset `never` check and `resolve.decides_natively(primary)` stay. That function shares a name with the registry flag but answers a different question: the flag says the *kind* can decide natively, and the function says this *model* is not known unable. The 9a docstring says so.
- `FakeLLM.decide_native(item, target, usage=None, *, retries=None)` records `(item, target, retries)` in `native_requests`.
- **H's other dependencies on `FALLBACK_KEY` and the conn id** (2026-10-09). H's coordination note names two, which are covered above: `stages`' strip, now `Chain.alone()`, and ruling 6's attach rule. Task 1 found four more in the code H landed. Each becomes stage data:
  - **`inference._stops_chunks(error, conn)`** reads `llm.FALLBACK_KEY in conn` to decide whether a bare chunk error stops a structured stage. It becomes `_stops_chunks(error, chain)`, with `chain.fallback is not None`.
  - **`inference._without_mode(conn)`** strips `STRUCTURED_KEY` and `FALLBACK_KEY` for the prompt-only re-send. It becomes `Chain(dataclasses.replace(chain.primary, structured=False))`.
  - **`run_stages`' connection-wide stop**: `dead: list[dict]` and `_same_connection(conn, other)`, which compares `conn["id"]`. These become a list of the stopped stages' `chain.primary.provider_id`, compared the same way: an empty id never matches.
  - **`LLMClient.decide_native`'s own strip.** It drops the fallback and also `sampling` (`{k: v for k, v in _without_fallback(conn).items() if k != "sampling"}`), so the ledger row files no `preset`. `_native` makes the same strip for `named`. Both become `target.without_sampling()` inside the facade. A test asserts that a native row still carries no `preset`.
  - `inference.without_fallback(conn)`, H's public copy that `stages` and `evals/runner.chain` call (A10), is deleted here. Every caller holds a `Chain` and calls `.alone()`.
  - `_native`'s `NATIVE_TIMEOUT_STOP` (a stage starts nothing more once that many items in a row have timed out) is not a key dependency, but it lives in the code this sub-task rewrites, and it must survive unchanged.
- **The native ledger row** (2026-10-09). `_native`'s stamp moves from `llm_usage.with_account(call.conn, decision_mode=NATIVE)` to `call.chain.with_account(decision_mode=NATIVE)`. The row it files must still carry `decision_mode="native"`, so `usage._modellable` keeps excluding it and `usage_rollup.VERSION` stays `6` (9b). Test: `test_a_native_row_still_files_decision_mode_native`, in `test_inference_decide_native.py`. A native call's ledger row has `decision_mode == "native"`, no `preset`, and no `modelled_usd` when a rate exists for its model.

- [ ] **Step 1:** Convert H's suites and F's decide suites to `request["chain"]`/`request["target"]` and `native_requests` targets: `test_inference_decide.py`, `test_inference_decide_native.py`, and the decide cases in `test_scene_break_routes.py`, `test_routes.py` (voice drift), `test_character_turns.py` (speaker), `test_absorb_identity.py`, `test_continuity_reconcile_routes.py`, `test_model_test_call.py` (probe) and `test_evals.py`. Keep every assertion about stages, retries, the fallback, the mode, the capture and the ledger. The assertions on `NATIVE_TIMEOUT_STOP` and on the connection-wide stop (in `test_inference_decide_native.py` and `test_inference_decide.py`) are kept, and so are the bare-chunk-error cases that reach `_stops_chunks`.
- [ ] **Step 2:** Implement until they pass. `llm_usage.with_account` is deleted. Every stamp is `Target.with_account`/`Chain.with_account`.
- [ ] **Step 3:** Run those suites plus `tests/test_usage_guard.py tests/test_operation_guard.py tests/test_routing_guard.py tests/test_prompt_log*.py tests/test_format2_play.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`, then the gates.
- [ ] **Step 4:** Commit: `refactor(inference): decide's stages carry chains of targets`.

#### Task 9d: The generate path, the fakes and the suites on chains; the facade's dict door deleted

**Files:** `inference.py` (`generate`/`note_outcome` hand `resolved.chain`/`.chain.primary`), `tests/llm_fakes.py` (no `request["conn"]`), `llm.py` (`_as_chain` deleted), `routes/config.py` (the generate probe hands `single` `wire.from_lowered(conn).primary`, as H's probe already does), and the suites. `wire.from_lowered` stays: the probes and the `llm_sampling` dict callers still use it until Task 10 (N8). The suites:
- `tests/test_llm.py`, `test_llm_structured.py`, `test_llm_lifecycle.py`, `test_llm_error_status.py`, `test_llm_image_adapters.py`, `test_llm_sampling.py`;
- `test_post_images_dispatch.py`, `test_model_guidance_dispatch.py`, `test_model_guidance.py`, `test_provider_health.py`, `test_reasoning_display.py`, `test_llm_fakes.py`;
- every suite reading `request["conn"]` (Task 1's inventory): `test_routing_routes.py`, `test_scene_break_routes.py`, `test_routes.py`, `test_character_turns.py`, `test_model_test_call.py`, `test_inference_override.py`, `test_format2_play.py`.

- [ ] **Step 1: Convert.**
  - Each dict literal like `{"kind": "openrouter", "model": "m", "api_key": "k", "sampling": {...}}` becomes `wire_kit.target(kind="openrouter", model="m", api_key="k", sampling=wire.Sampling(...))`.
  - Each `request["conn"]["model"]` becomes `request["target"].model`.
  - Each `conn[FALLBACK_KEY] = fb` becomes `wire_kit.chain(primary, fb)`.

  Do not change what any test asserts about the wire, the holder, the retry count or the fallback. Only the input spelling changes.
- [ ] **Step 2:** Delete the facade's dict door (`_as_chain`) and the fakes' dict branch. `test_the_shim_is_the_only_dict_door` becomes `test_the_facade_takes_no_dict`: passing a dict raises `TypeError`. `from_lowered` and its round-trip test stay until Task 10.
- [ ] **Step 3:** Run every suite listed under **Files**, plus:

  ```
  tests/test_adapter_registry.py tests/test_inference_decide.py tests/test_inference_decide_native.py
  tests/test_inference_fallback.py tests/test_operation_guard.py tests/test_usage_guard.py
  tests/test_import_guard.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py
  ```

  Then the gates. Expected: pass.
- [ ] **Step 4:** Commit: `refactor(llm): the facade takes chains of typed targets; its dict door goes`.
- [ ] **CI checkpoint (Task 9).**

### Task 10: Delete the connection-dict lowering

**Files:**
- Modify:
  - `store/inference/resolve.py`: delete `_lowered`, `lower`, `with_facts`, `own_sampling`, `FALLBACK_KEY`, `STRUCTURED_KEY`, `ACCOUNT_KEY`, and the dict writes in `_account`/`_flag_structured`; build `Target` directly in `_attempt`.
  - `store/inference/resolved.py`: delete `Attempt.conn` and `ResolvedInference.conn`/`.fallback`.
  - `routes/common.py`: `UsableInference.conn` becomes `.chain`, never None.
  - `store/inference/controls.py`: `preview(preset_id, target, operation="")` builds a `Target` with `resolve.target_for` (H's `operation`, A5).
  - `routes/config.py`: the model test's probes (H's included) and `_with_effective` for the provider editor build targets through `resolve.target_for` (Task 9a); `post_images.reach(chat.chain…)`.
  - `llm_sampling.py`: `effective`/`split`/`not_applicable` take a `Target` only (the dict door goes; N8).
  - `store/post_images.py` (`reach`) and `health.py` (`record`): `Target` only; their three `routes/config.py` callers pass targets (R2-4).
  - `wire.py`: `from_lowered` deleted, together with its last callers above (N8), and its round-trip test.
  - `store/embed_space.py`: D's note. `endpoint_of` reads `attempt.target.api_key`, and its `"conn"` entry becomes `"target"`.
  - `store/inference/embed.py`: `_stamp` reads the target.
  - `llm.py`: `FALLBACK_KEY`, `STRUCTURED_KEY`, `_without_fallback`, and `DEGRADE` as a dict key. (`inference.without_fallback` and `NATIVE_DECISION_KINDS` already went in 9c.)
  - `llm_usage.py`: `ACCOUNT_KEY`.
  - `store/inference/settings.py`.
  - `tests/inference_baseline.py` and `inference_baseline_c.py`: observe projects `Target` into the same JSON keys, and the JSON is untouched.
  - `tests/test_inference_target.py`: target-only.
  - Docs: `CLAUDE.md` ("Adding an LLM call site?"), `CONTRIBUTING.md`.
- Create: `backend/tests/test_lowering_retired_guard.py`.

**Interfaces:**
- Consumes: `wire.Target`/`Chain` (Task 7) and the registry (Task 9).
- Consumes: `resolve.target_for` (Task 9a).
- Produces:
  - `embed_space.endpoint()` returns `{model, base_url, key, space, provider, provider_name, provider_kind, target}`.

- [ ] **Step 1: Write the failing guard:**

  ```python
  def test_the_lowering_stays_retired():
      # AST over backend/src/grimoire, evals, backend/scripts. Flags:
      #  - a name or attribute FALLBACK_KEY, STRUCTURED_KEY, ACCOUNT_KEY, with_facts, own_sampling,
      #    _lowered, _without_fallback, without_fallback (H's public copy, A10),
      #    NATIVE_DECISION_KINDS, NativeAdapter (H's table, absorbed by the registry, 9a),
      #    from_lowered (CR7: the shim), _as_chain;
      #  - `lower` imported from store.inference.resolve;
      #  - attribute `.conn` on a ResolvedInference/UsableInference/Attempt binding
      #    (`require_inference(...)`, `override_inference(...)[0]`, the FIRST name of a
      #    tuple unpack of `override_inference(...)` -- `resolved, routed = ...` and
      #    `resolved, _ = ...`, the shape every production caller uses --
      #    `inference.resolve(...)`, `.attempts[i]`), on a `Stage` (`stages(...)` items,
      #    `Stage(...)`) and on a `_Call` (`call`, `replace(call, ...)`) (I7);
      #  - a str constant "_fallback", "_structured", "_account" or "_degrade".
  def test_the_guard_flags_a_planted_lowering():   # one plant per rule, each reported, including
                                                   # `resolved, _ = override_inference(...)` then `resolved.conn`
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
  - D's `FactsUnreadableError` pattern: C's `RecordUnreadableError` already follows it. Task 6a moves it, with `_record`, to `frontmatter` (`read_record`) and adds `require`.
  - E's cancel window: ruling 11, Task 9b.
  - F's (`complete(schema=)` in place of `generate`): Tasks 8 and 9.
  - F's (delete `FALLBACK_KEY`/`STRUCTURED_KEY`): Task 10.
  - H's I11 (two `FALLBACK_KEY` dependencies as stage data): Task 9c, `Chain.alone()` and the attach rule. Four more found on `main` (`_stops_chunks`, `_without_mode`, `run_stages`' `dead`/`_same_connection`, `decide_native`'s sampling strip): Task 9c as well.
  - H's `NATIVE_DECISION_KINDS` table: absorbed by the registry (9a), deleted in 9c. H's native ledger row (`decision_mode="native"`, `usage_rollup.VERSION` 6): pinned in 9b and 9c.
  - H's strict-mode limits: H's (A6).
  - H's ruling 21 (`require_parameters`): declined, reported (A11).
- **Revision 3 (the re-review).** N1–N9 are answered in the task text, not only in the rulings:
  - N1: `lookup(mode="retire")` and its parametrised test (6a); A12 checks that C's own fix is present (Task 1).
  - N2: `Plan.mapped`/`repoint` (Task 4), with the migration persisting `mapped` only (Tasks 4 and 5).
  - N3: born retired (6a), with archive-free birth tests.
  - N4: the strip's lock and its interleaving test (6b).
  - N5: `left()` widened (6a), with three per-scope tests (6b).
  - N6: the record read strictly by writers and the retire lookup (6b).
  - N7: a decline for every ratification item, and Task 6a Step 4 for item 6.
  - N8: `target_for` in 9a, the `llm_sampling` dict door until Task 10, and `from_lowered` deleted in Task 10.
  - N9: the `fact_not_carried` note (6b).
  - N10–N20 are listed in the rulings with where each landed.
- **Revision 4 (the second re-review).**
  - R2-1: a create-only keyword on C's `publish_birth`, passed only by `create_campaign` (6a), with two tests. (It was a separate `_birth_marker()` until 2026-10-09.)
  - R2-2: the migrate lookup reads the record on a retired store (6b), and both late-campaign tests run through step 8.
  - R2-3: `pass_plan`/`needs_archive(pass_plan)` (6a), with three tests.
  - R2-4: the 9b dict door, widened.
  - R2-5 and R2-6: A12 and A13, as Task 1 checks.
  - R2-7: the settings-only archive is kept off the restore-point list (6a Step 4).
  - R2-8: the failed-archive test's comment.
- **The order keeps the tree green, and CI proves it at five points.**
  - The suite moves to format 2 per family, each family opting in until the flip (3a–3d, then CI).
  - The planner lands beside translate before play uses it (Task 4). Play switches to it with every baseline still compared (Task 5, then CI).
  - Retirement writes only what the planner already serves in memory, so `test_planned_equals_persisted` covers both sides (Task 6, then CI).
  - Targets exist before anything consumes them (Task 7).
  - Call sites move before the facade's types change (Task 8).
  - Every target builder exists first (9a). The facade moves behind a shim that the fakes share (9b), then decide moves (9c), then generate, and the facade's dict door goes (9d, then CI). `from_lowered` goes last, with the probes and readouts that used it (Task 10).
  - The dict is deleted only when its guard finds nothing left (Task 10, then CI).
  - Every doc claim moves in the task that changes it, so `test_docs_guard` is green at every commit.
