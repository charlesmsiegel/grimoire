# Backend package modularization plan

**Spec:** `docs/superpowers/specs/2026-10-20-backend-package-modularization.md`
**Reference:** `a7ebe3961`; 35 production Python files exceed 1,000 lines.
**Execution:** independent package tasks with exclusive file ownership, followed
by coordinating-agent guard/baseline integration and independent review.

## Global constraints

- Behavior, authoritative files, privacy, comments and issue context are preserved.
- Preserve all former exposed bindings with explicit facade re-exports.
- Preserve route order, signatures, dependencies, markers and complete OpenAPI.
- The independently reviewed amendment permits only the two source-pointer
  literals listed in the spec's OpenAPI descriptions. Keep the original hashes;
  assert corrected literals and reverse only those spellings on a copy before
  comparing the entire schema, with structural and same-description controls.
- One owner per mutable/rebound state; readers access that module directly.
- Preserve logger names and persisted logical module labels after relocation.
- Imports stay module-scoped, acyclic, and module-bound across store packages.
- Internal fault-injection tests may patch the exact owner, preserving assertions;
  real runtime extension points and shared mutable identity must still work.
- No module proxy, hidden dependency registry, relaxed guard/lint rule, coverage
  reduction, new runtime dependency, live library or frozen-home regeneration.
- Aim for every production file at most 1,000 physical lines, using cohesive
  responsibilities. Record concrete reasons for any unavoidable exception.
- No push, PR, merge, Mechanics II or future quality-gate activation.

## 1. Capture the starting contracts

- [x] Save the exact 35 original sources and pinned-tool diagnostics under ignored
  `build/`, before any extraction. Keep temporary generators out of commits.
- [x] Capture former bindings and ordered route metadata. Capture complete
  application OpenAPI separately under the configured Pydantic-2 and isolated
  Pydantic-1 environments, with an isolated store/home. Add a characterization
  test/fixture for the original interfaces before changing production source.
- [x] Read existing complete coverage artifacts as a map; run deterministic
  code-doctor analysis and triage actual state/dependency risks per subsystem.
- [x] Characterize an emitted log row's original logical module label. Retain
  existing cancellation, fork, recovery, overlay, frozen-store and prompt tests.

## 2. Recover and extract the seven primary subsystems

Before editing each, write its ownership map, dependency direction and existing
patch seams to a task report; have the independent reviewer check the map when
it requires a new ownership decision. The concrete names below describe domain
boundaries; merge or subdivide only when actual dependencies justify it.

### Gateway operations: `inference.py`

- [x] Separate generation/structured resends, decision stage execution, decision
  escalation, pricing/budget projections, tool-loop runtime and loop transport.
  Keep `_Loop` intact (789 lines) if its focused imports fit within the target.
- [x] Give backend dispatch and the loop weak-set one owner. Shared resend and
  attempt-accounting helpers must sit below both generation and tools. Public
  operations remain on the compatibility facade.
- [x] Inventory and preserve actual distribution/cap/clock patches; update internal
  injection targets only where their implementation owner moves. Preserve all
  client-call metering, stage failure/answer distinction and cancellation cleanup.
- [x] Run inference generation/decide/escalation/tool-loop suites and relevant
  routing/usage/operation/tool/capture/import guards on both Pydantic versions.

### Scene routes and character turns

- [x] Extract scene CRUD/suggestions, turn setup and rerolls, review phases,
  review lifecycle/save, rolling summaries, scene-break detection, follow-ups,
  cast/settings, inspection, and transcript edits/replay by responsibility.
- [x] Put the router in one leaf. All endpoint leaves use that same instance;
  finalize its list in place against the explicit original route-key sequence,
  preserving APIRoute identity and rejecting missing/extra/duplicate keys before
  the facade is exposed. Keep rolling-inflight set/lock and review
  persist/backoff/clock controls with their actual lifecycle owner. Shared
  identity and mutation fences are dependencies, never duplicated rules.
- [x] Split character-turn state/selection, stored plans, generation, persistence
  and response reroll/continuation along the existing bounded-round model.
  Preserve one actor contribution per step and roll-resume snapshots.
- [x] Capture all route order/overlaps before edits. Exercise scene freeze,
  detached review/cancel, reroll/alternate/replay, rolling/break races and group
  round behavior. All existing failure injection must still intercept calls.

### Overlay

- [x] Separate low-level roots/ref vocabulary, ledger reads/writes, immutable
  read views, native source identity, copy/base reservation, flat records,
  whole actors/rosters, per-file image/sidecar overlay and world lifecycle.
- [x] Place read-only primitives below mutation orchestration. Avoid a ledger
  writer importing a higher-level facade that imports the same ledger. Exact
  markers and selected source namespaces remain distinct from slug similarity.
- [x] Preserve strict preflight-before-effects, base/copy crash ordering and
  campaign-lock scope. Track each moved writer's original lock classification;
  no writer becomes reviewed merely because it moved.
- [x] Run overlay/view/tombstone, group identity/membership, sync, movement,
  revision/journal and path/atomic/overlay/lock/import guards.

### Compiled cache

- [x] Extract derivation contracts/key validation, platform/file-clock support,
  batch lookup/staging, database ownership/recovery, deletion intents/purge,
  maintenance and lifecycle. Keep `Batch` intact if its coherent module fits.
- [x] One explicit module owns `_state`, inherited owners, reset/fork hooks and
  session lifetime. Every consumer reads its current owner; importing `_state`
  by value across leaves is forbidden. Preserve live facade reads of rebound
  exported state without an assignment-forwarding module proxy.
- [x] One exact leaf imports SQLite; SQL consumers reference that owner. Retain
  optional-SQLite/ctypes fallbacks and every bounded-query check after relocation.
- [x] Run compiled ownership/foreign-file/corruption/fork/delete/lock/budget,
  persisted overview/history/searchdocs and cache-sync tests plus guards.

### Context assembly

- [x] Separate input gathering and reader projection, cast/setting/tracker
  materialization, the section catalog/pinning, section rendering/source detail,
  packing/composition and inspector breakdown. Rendering and packing consume
  the same frozen snapshot; model variants never gather or expand macros again.
- [x] Give `SECTIONS` and section metadata one owner. Preserve withheld history,
  catalog replacement, tracker-read injection and macro/token memo semantics.
- [x] Run context/openers/director/model-guidance/pins/epistemic/tracker/history,
  prompt byte-equality and frozen-campaign checks, then relevant AST guards.

### Detached runs

- [x] Separate subject/exclusion vocabulary and Run data, per-app RunRegistry,
  HTTP/SSE transport, reservation/execution adapters and maintenance exclusion.
- [x] The registry remains per-app; handshake creation still crosses the portal,
  and a cancelled maintenance run keeps exclusion until its thread returns.
- [x] Characterize reserve-before-start cancellation, producer unwind, replay
  cursors, identity fencing, root changes and cross-process maintenance locks.
  Preserve timeout/reservation patches at their real owner.

## 3. Continue the other oversized production modules

For each group repeat contract capture, responsibility/owner recovery, scoped
review, extraction, focused tests and implementation review. Reuse the same
explicit facade/module dependency conventions; do not reuse a mechanical
line-count partitioner as architecture.

- [x] Other gateway contracts: `decisions.py`, `llm.py`, `tool_calls.py`.
- [x] Other routes: `config.py`, `common.py`, `streaming.py`, `todo.py`,
  `characters.py`, `tracker.py`, `ledger.py`, `worlds.py`.
- [x] Other store services: `cache_sync.py`, `usage.py`, `characters.py`,
  `suggest.py`, `assets.py`, `image_migration.py`, `image_gc.py`, `sync.py`,
  `locks.py`, `logs.py`, `world_bundle.py`.
- [x] Nested store subsystems: `continuity/reconcile.py`, `inference/resolve.py`,
  `inference/migrate.py`, `inference/legacy_plan.py`, `context/epistemic.py`,
  `absorb/materializer.py`.

## 4. Integrate, validate and commit

- [x] Update every affected architecture guard to inspect new owners/leaves,
  preserving negative controls and exact permissions. Fix stale static source
  readers and production prose references; never regenerate frozen data/API
  baselines just to conceal a missing interface.
- [x] Match before/after diagnostics by rule/message/original definition and
  reconcile only relocation keys or earned reductions through the ratchet.
- [x] Run all recipes: lint, mypy, templates, ESLint, frontend typecheck/coverage,
  complete backend coverage and fresh isolated Pydantic-1 suite. Always put this
  worktree's `backend/src` first. Preserve subprocess filesystem access needed
  by the existing Windows harness; distinguish unavailable Make/3.11/APK checks.
- [x] Run independent implementation and final spec-versus-diff reviews. Verify
  all production file sizes and record any remaining >1,000-line exception with
  its actual reason. Update handoff/theory/evidence and commit reviewable units.

## Review checkpoints

Spec review approved after adding stable observable logging labels and making
every `make check` constituent unconditional. Plan review is required before
production or characterization-test edits. Subsequent ownership-map amendments
are reviewed before their extraction; routine source moves then follow the map.

Plan review approved for characterization and extraction through reviewed maps.
The route-map review approved one shared router with eager construction and
explicit final in-place ordering, rather than separate routers per leaf; this
retains observable dispatch and original route objects for interleaved domains.

## Execution evidence

The original contract fixture was captured at `a7ebe3961`, before production
extraction: 35 module interfaces, 4,487 exposed bindings, 265 ordered routes,
complete application OpenAPI under each Pydantic major, and emitted logical log
identity. Its 46 characterization tests passed against both original runtimes.

All 35 originals have now been extracted through reviewed ownership maps and
independent implementation reviews. The first nine had 5,920 matching global
binding observations; the next eight route packages had 2,939; the peer review
of locks/logs/bundles/assets/compiled had 1,092; root's independent store review
had 5,398. Whole definitions, comments, annotations, tuple bindings, overloads
and cleanup/locking spans were checked, rather than trusting test totals alone.

Every original route table and application OpenAPI field still match the
pre-extraction fixture on both Pydantic versions, apart from the two explicitly
reviewed source pointers in published descriptions. The original hashes remain
unchanged; bounded comparison and negative controls enforce that exception.
All 732 production Python
files parse with Python 3.11 grammar and none exceeds 1,000 lines; the maximum
is 957. Fresh diagnostic audits match all 764 Ruff and 141 mypy identities, with
no additions or removals. Their baselines rekey relocated findings only; ESLint
remains at 307. Frontend typecheck/coverage and all 553 template checks pass.

Final cross-author guard review found and verified repairs for facade-binding
shadowing, a missed streaming sibling and moved todo method owners. The final
affected guard run passes 274 cases under each Pydantic major, with another 82
overview/docs/proposal cases passing after the last source-only guard cleanup.
The initial complete runs then exposed twelve integration failures on each
runtime: missed internal patch targets, a scanner's top-level relative import
context, benchmark registry/clock rebinding and stale source prose. Those
repairs retain the original assertions and now pass affected selections on both
majors. The initial runs passed 23,107 / 23,101 cases, with 46 / 52 skips; their
twelve failures remain failed evidence, despite 94.19% measured coverage.
Later attempts exposed exactly two published source pointers; the reviewed
documentation-only amendment above preserves the original schema hashes with
an exact-literal comparison and four negative controls. All 50 contract cases
pass on each Pydantic major. Complete gate runs of that repaired snapshot
finished successfully: 23,141 passed / 46 skipped on the primary interpreter,
with 94.19% coverage against the unchanged 93% floor; 23,135 passed / 52 skipped
on Pydantic 1. Both exited 0. All package contracts and documentation guards
also passed within both complete collections. The final independent
specification checkpoint approved the frozen implementation and delivery
documents after parsing both actual JUnit reports and the coverage XML:
23,187 matching test identities, no failures or errors, and 94.192016% combined
coverage. Its record is `build/final-spec-review.md`.

Current results and platform limits are recorded in
`docs/engineering/backend-package-validation.md`; durable responsibility notes
are in `docs/engineering/backend-package-theory.md`. Prior-phase full-suite
results do not validate this phase.
