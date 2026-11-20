# Roadmap implementation audit

**Historical snapshot:** [`b54b2bd898752684a2ad00b9fd7f1f02ff544326`](https://github.com/charlesmsiegel/grimoire/commit/b54b2bd898752684a2ad00b9fd7f1f02ff544326), branch `v2`, refreshed 2026-11-20. **62/119 scheduled slices; 4/12 scoped waves** (1, 3, 4, 5).

**Current reconciliation:** landed base [`0f7adcfb6d3e04401284f682773f07a6f5467bc5`](https://github.com/charlesmsiegel/grimoire/commit/0f7adcfb6d3e04401284f682773f07a6f5467bc5) supports **65/119 scheduled slices** after recognizing 047, 050 and 112. The additional 049/054 proofs in [PR #696](https://github.com/charlesmsiegel/grimoire/pull/696) bring this candidate’s rows to **67/119**; 056's shared-bestiary proof and 064's scenario preparation bring them to **69/119**; 053's consolidation and 063's delayed-rumor proofs bring them to **71/119**, and the 070, 067, 072, 061 and 058 proofs to **76/119**, with **4/12 scoped waves** unchanged. This is a bounded follow-up audit of the entries below, not a new exhaustive audit or a claim that #696 was already merged.

This refresh inspected every merged change since the original [92144e17 audit](https://github.com/charlesmsiegel/grimoire/pull/678). It rechecks affected slice criteria and wave gates; unchanged entries retain that original source/test audit, not a claim of a new exhaustive audit. The follow-up refresh includes #691, #681 and #693 through the pinned snapshot.

| Merged change | Audit decision |
|---|---|
| [#679](https://github.com/charlesmsiegel/grimoire/pull/679) | Retain 113 checked: configured production services and CLI/Chromium consequence proof. Wave 7 remains open. |
| [#680](https://github.com/charlesmsiegel/grimoire/pull/680) | Retain 033 checked and reconcile Wave 4 to 10/10: native creature chat binding, reserve/control/memory proof. |
| [#683](https://github.com/charlesmsiegel/grimoire/pull/683) | Check 062: four incompatible family proposals, validation, no-mechanics path and documented hook boundaries. |
| [#686](https://github.com/charlesmsiegel/grimoire/pull/686) | Keep 015 and Wave 2 unchecked: prerequisite documentation supplies no measured provider baseline. |
| [#688](https://github.com/charlesmsiegel/grimoire/pull/688) | Check 059: persistent qualified topology, campaign road block, same-name/missing-place refusals. |
| [#689](https://github.com/charlesmsiegel/grimoire/pull/689) | Retain 055 checked: explicit 20-day advancement, deadline and retry proof. |
| [#691](https://github.com/charlesmsiegel/grimoire/pull/691) | Check 051: three-voice coordination, interruption, silence and privacy. Keep 068 open: synthetic comparison is not billed live V0.2.1 evidence. |
| [#681](https://github.com/charlesmsiegel/grimoire/pull/681) | Check 057/060: institutional dissent and mistaken second-order hypotheses with tactic revision. Persistence and UI remain deferred. |
| [#693](https://github.com/charlesmsiegel/grimoire/pull/693) | Keep 053/063 open: durable correction and delayed rumor foundations exist, but complete proving scenarios remain unestablished. |
| [#684](https://github.com/charlesmsiegel/grimoire/pull/684), [`ff3d0875`](https://github.com/charlesmsiegel/grimoire/commit/ff3d0875f5f24dee1778b195a1afc30456c73c16) | Keep 056 partial: pinned shared character/lore/gear across three worlds, exclusions, local creature, quick chats and 2,000 unselected records are proven; shared pinned bestiary creature across two settings is missing. |
| [#685](https://github.com/charlesmsiegel/grimoire/pull/685), [`b252c671`](https://github.com/charlesmsiegel/grimoire/commit/b252c671ee497ce787c7657fef6de855119d5e04) | Check 047: independent prose extraction and exact-text continuity/PC-authorship review with authority-preserving keep gate. Wave 6 has 6/6 rows; physical-phone exit remains open. |
| [#692](https://github.com/charlesmsiegel/grimoire/pull/692), [`e8e7188d`](https://github.com/charlesmsiegel/grimoire/commit/e8e7188d55cadadf32401043c316b27ec3315c0b) | Check 050: native named capture, one-ball retry/reopen, distinct same-species actor, reserve capacity, failed capture and crash recovery. 033 roster/UI and 115 remain separate. |
| [#695](https://github.com/charlesmsiegel/grimoire/pull/695), [`1a742e4b`](https://github.com/charlesmsiegel/grimoire/commit/1a742e4b67038d00140d6eab116055b005aa614c) | Retain open Wave 6/7 exits: [offline acceptance](../ACCEPTANCE.md) proves synthetic native voice, keyboard/focus and emulated-phone Chromium journeys, not physical devices, touch or real screen readers. |
| [#682](https://github.com/charlesmsiegel/grimoire/pull/682), [`0f7adcfb`](https://github.com/charlesmsiegel/grimoire/commit/0f7adcfb6d3e04401284f682773f07a6f5467bc5) | Check 112: actor-authorized current context and optional metered/replayed decisions in production CLI/Chromium. Wave 7 has 7/7 rows; global accessibility/device gate remains open. |
| [#704](https://github.com/charlesmsiegel/grimoire/pull/704) | Check 053: attributable dossiers over several scenes; a corrected identity survives a stale and an older-basis recap end to end through the inspector, a generated brief and NPC context packs. UI, write path and sync remain with 115. |

Historical refresh relative to #678: **+8 slices** (033, 051, 055, 057, 059, 060, 062, 113), **+1 wave** (4). At the refresh base, implementation PRs had already checked 033/055/113; this documentation diff adds 051/057/059/060/062 and reconciles stale summaries. Original scheduled scope and inherited first-proof obligations are unchanged.

This is a code-and-test audit of the 119 scheduled **first-useful slices**, including inherited first-proof additions. Requirements were not narrowed to match the code. A check does not close later milestones, certify a live model, or prove physical-phone/browser accessibility. Unchecked entries distinguish missing implementation from a proof not yet established. The [ordered roadmap](README.md#implementation-checkmarks) carries the same checkmarks.

The current reconciliation preserves the original proving obligations. Browser evidence is synthetic Chromium/loopback with keyboard, focus and emulated narrow viewport; it does not certify physical-phone touch, real screen-reader use or the global Wave 6/7 acceptance gates. 015 remains unchecked without the live provider-reported baseline.

## Wave results

| Wave | Checked slices | Wave complete | Exit / remaining limit |
|---|---:|:---:|---|
| [1](#wave-1) | 8/8 | ☑ | All eight scoped foundation contracts and their integration proof are present; this is not playable-product certification. |
| [2](#wave-2) | 7/8 | ☐ | 015 has no live provider-reported V0.2.1 baseline capture. |
| [3](#wave-3) | 10/10 | ☑ | All ten scheduled headless first slices are present. Later subject-validation and other milestones remain open. |
| [4](#wave-4) | 10/10 | ☑ | 033 follow-up adds public non-character chat rosters and scene-runner reserve proof; see its dated evidence below. |
| [5](#wave-5) | 8/8 | ☑ | All eight scheduled first slices are present, including both persistent text-play paths. Later milestones are not implied. |
| [6](#wave-6) | 6/6 | ☐ | Scheduled first slices are implemented; physical-phone validation remains unverified. |
| [7](#wave-7) | 7/7 | ☐ | 112 and 113 have real CLI/Chromium follow-up proofs. The full keyboard/screen-reader/touch acceptance gate remains unverified; scoped proofs do not close the global exit gate. |
| [8](#wave-8) | 19/24 | ☐ | 049, 050, 051, 053, 054, 055, 056, 057, 058, 059, 060, 061, 062, 063, 064, 066, 067, 070 and 072 are complete at their bounded first-proof boundaries. Other domain slices and UI exit 115 remain incomplete or unverified. |
| [9](#wave-9) | 1/16 | ☐ | 086 meets its first slice; the other domain slices and UI exit 116 are not complete. |
| [10](#wave-10) | 1/13 | ☐ | 095 meets the bounded headless trade/craft proof through counted holdings; the other domain slices and UI exit 117 remain incomplete. |
| [11](#wave-11) | 0/6 | ☐ | Scheduled domain slices and UI exit 118 are not complete. |
| [12](#wave-12) | 0/3 | ☐ | Voice/immersion implementations and UI exit 119 are absent. |

## Verification

### Follow-up refresh verification (b54b2bd8, 2026-11-20)

- New-claim selection: **206 passed** in 24.81 seconds on Linux/Python 3.12. It covers ensemble, actor inference and reporting, institutions, social hypotheses, memory core/store/recovery, rumors/store/recovery and continuity actor-ID reservation. All providers are synthetic.
- The 051/057/060 checkmarks reflect complete bounded headless proofs. 068 retains the missing full-scenario evidence above; neither a partial API nor a passing unit selection closes those slices.
- The initial 160-test focused selection (159 plus the separately rerun Chromium test below) exercises the previously changed claims and real application composition. Together with source/requirements review, count/link reconciliation and `git diff --check`, this is a targeted documentation-only local gate, not a substitute for required full cross-platform CI. The follow-up PR records fresh combined results for its exact head and the independently reviewed diff.

### Initial refresh verification (1b4f247a, 2026-11-20)

Affected source and test bodies were inspected at the initial refresh snapshot `1b4f247a`. Local refresh results are recorded separately from historical runs below. No paid provider or private library was used. Exact documentation-head CI and independent review results belong on the refresh PR; this document does not certify pending checks.

- Focused Linux/Python 3.12 refresh run: **159 passed, 1 skipped** in 56.61 seconds. The selection covered rulesets, topology/index/store/chat/recovery, calendars/store/recovery/sync, native chat actors, party scenarios, campaign configuration/review/browser/page/inbox and V0.2.1 offline replay. These use synthetic inputs, never a paid provider.
- The sole skip was the optional browser dependency. After installing Playwright, `PYTHONPATH=backend/src python -m pytest backend/tests/test_app_campaign_browser.py -q -rs` with system Chromium: **1 passed** in 10.63 seconds. This is an actual CLI/Chromium proof with a synthetic local provider, not a physical-phone or screen-reader acceptance claim.
- One-off editorial verification found all 119 slice statuses identical between the audit and roadmap, all 12 per-wave totals consistent, and all local Markdown link targets present; `git diff --check` passed. No prose-parsing repository test or production change was added.
- These are targeted refresh results, not a new full-suite or cross-platform green claim. Exact final documentation-head reruns and CI witnesses are reported on the refresh PR.

### Historical verification (original 92144e17 audit)

The following are historical results, not current-head green evidence:

- Waves 1–4 focused/integration tests: **1,274 passed, 4 skipped** across 40 files.
- Counted-holdings, inference and source-API tests: **105 passed**; an additional synthetic real-store smoke confirmed two-resource barter, atomic craft, replay idempotence and insufficient-resource refusal.
- Paired offline-turn and TextApi tests: **18 passed**.
- Expanded sync/peer/ledger proof set: **734 passed, 6 skipped** in 360.73 seconds. These targeted counts overlap parts of other runs and should not be added as unique coverage.
- Linux/Python 3.12 `rest+app` shards (`PYTHONPATH=backend/src GRIMOIRE_TEST_SHARD=rest+app python -m pytest backend/tests -q`): **6,378 passed, 10 skipped, 1 failed** in 584.87 seconds. The failure is `test_migrate_v1_failures.py::test_a_socket_is_refused`: this environment denies creation of the test's `AF_UNIX` socket with `PermissionError` before the scanner runs. That negative case is unverified here; the run is **not green**. Other shards and supported operating systems were not covered by this broad run.
- Not claimed: full cross-platform CI, live V0.2.1 measurements, a paid-provider comparison, physical-phone execution, or manual keyboard/screen-reader/touch acceptance.

## Slice evidence

<a id="wave-1"></a>
## Wave 1

All eight scoped foundation contracts and their integration proof are present; this is not playable-product certification.

<a id="slice-001"></a>
### 001 — V2 Library Paths And Isolation

- [x] Scheduled first-useful slice ([requirements](sprints/v2-library-paths-and-isolation.md)).
- Evidence: [`backend/src/grimoire/library/paths.py`](../../backend/src/grimoire/library/paths.py); [`backend/tests/test_library_paths.py`](../../backend/tests/test_library_paths.py); V2 default, V1 isolation, write and migration-report destinations are checked.

<a id="slice-002"></a>
### 002 — Source Qualified Identity

- [x] Scheduled first-useful slice ([requirements](sprints/source-qualified-identity.md)).
- Evidence: [`backend/src/grimoire/core/identity.py`](../../backend/src/grimoire/core/identity.py); [`core/index.py`](../../backend/src/grimoire/core/index.py); [`backend/tests/test_identity.py`](../../backend/tests/test_identity.py); Qualified definitions/instances and immutable index preserve identity across same-name records.

<a id="slice-003"></a>
### 003 — Player Sovereignty

- [x] Scheduled first-useful slice ([requirements](sprints/player-sovereignty.md)).
- Evidence: [`backend/src/grimoire/core/authority.py`](../../backend/src/grimoire/core/authority.py); [`backend/tests/test_sovereignty_scenarios.py`](../../backend/tests/test_sovereignty_scenarios.py); Controller authority, explicit delegation and narration review/correction protect labelled PC contributions.
- Scope / gap: Does not promise perfect free-text authorship detection.

<a id="slice-004"></a>
### 004 — Turn Costs And Feature Flags

- [x] Scheduled first-useful slice ([requirements](sprints/turn-costs-and-feature-flags.md)).
- Evidence: [`backend/src/grimoire/core/inference/plan.py`](../../backend/src/grimoire/core/inference/plan.py); [`core/inference/usage.py`](../../backend/src/grimoire/core/inference/usage.py); [`backend/tests/test_inference_plan.py`](../../backend/tests/test_inference_plan.py); Planner emits one baseline generation per speaking NPC, with optional paid stages explicit and metered.

<a id="slice-005"></a>
### 005 — Story Quality Benchmarks

- [x] Scheduled first-useful slice ([requirements](sprints/story-quality-benchmarks.md)).
- Evidence: [`backend/src/grimoire/evals/checks.py`](../../backend/src/grimoire/evals/checks.py); [`backend/tests/test_evals_gate.py`](../../backend/tests/test_evals_gate.py); Offline story-quality gate checks agency, leakage, continuity, distinct voices, copied text and total cost.
- Scope / gap: Synthetic/offline first slice; no claim of live V1 parity or human quality validation.

<a id="slice-006"></a>
### 006 — Testing And Failure Injection

- [x] Scheduled first-useful slice ([requirements](sprints/testing-and-failure-injection.md)).
- Evidence: [`backend/src/grimoire/testing/faults.py`](../../backend/src/grimoire/testing/faults.py); [`backend/tests/test_fault_harness.py`](../../backend/tests/test_fault_harness.py); Confined deterministic fail/crash/interleave harness exercises synthetic writers and replicas.
- Scope / gap: Native writer and sync integration owned by 010/019.

<a id="slice-007"></a>
### 007 — Safe Tool Execution

- [x] Scheduled first-useful slice ([requirements](sprints/safe-tool-execution.md)).
- Evidence: [`backend/src/grimoire/security/policy.py`](../../backend/src/grimoire/security/policy.py); [`backend/tests/test_security_card_import.py`](../../backend/tests/test_security_card_import.py); Permissions/grants/approval enforce inspect/propose import and untrusted tool scopes.

<a id="slice-008"></a>
### 008 — Spoken Command And Intent

- [x] Scheduled first-useful slice ([requirements](sprints/spoken-command-and-intent.md)).
- Evidence: [`backend/src/grimoire/intent/router.py`](../../backend/src/grimoire/intent/router.py); [`backend/tests/test_intent_text.py`](../../backend/tests/test_intent_text.py); Typed utterances distinguish UI command and PC speech with ambiguity handling.
- Scope / gap: No microphone/audio integration in this slice.

<a id="wave-2"></a>
## Wave 2

015 has no live provider-reported V0.2.1 baseline capture.

<a id="slice-009"></a>
### 009 — Entity Facets And Instance Model

- [x] Scheduled first-useful slice ([requirements](sprints/entity-facets-and-instance-model.md)).
- Evidence: [`backend/src/grimoire/core/entity.py`](../../backend/src/grimoire/core/entity.py); [`backend/tests/test_entity.py`](../../backend/tests/test_entity.py); [`test_wave2_four_kinds.py`](../../backend/tests/test_wave2_four_kinds.py); Composable definitions, instances, facets and control roles preserve qualified identity independently of store.

<a id="slice-010"></a>
### 010 — Native File Store And Revisions

- [x] Scheduled first-useful slice ([requirements](sprints/native-file-store-and-revisions.md)).
- Evidence: [`backend/src/grimoire/store/library.py`](../../backend/src/grimoire/store/library.py); [`store/engine.py`](../../backend/src/grimoire/store/engine.py); [`store/journal.py`](../../backend/src/grimoire/store/journal.py); [`store/envelope.py`](../../backend/src/grimoire/store/envelope.py); [`backend/tests/test_store_recovery.py`](../../backend/tests/test_store_recovery.py); [`test_wave2_four_kinds.py`](../../backend/tests/test_wave2_four_kinds.py); Facet-agnostic revisions/envelopes, unknown fields, links and crash recovery interoperate with typed entities.

<a id="slice-011"></a>
### 011 — V1 To V2 Migration Skill

- [x] Scheduled first-useful slice ([requirements](sprints/v1-to-v2-migration-skill.md)).
- Evidence: [`backend/src/grimoire/migrate_v1/__init__.py`](../../backend/src/grimoire/migrate_v1/__init__.py); [`backend/tests/test_migrate_v1_scan.py`](../../backend/tests/test_migrate_v1_scan.py); Snapshot scanner inventories V0.2.1 identities/scenes/art and verifies unchanged sources without destination writes.
- Scope / gap: Apply is separate 026.

<a id="slice-012"></a>
### 012 — Knowledge And Perspective

- [x] Scheduled first-useful slice ([requirements](sprints/knowledge-and-perspective.md)).
- Evidence: [`backend/src/grimoire/core/knowledge.py`](../../backend/src/grimoire/core/knowledge.py); [`core/perspective.py`](../../backend/src/grimoire/core/perspective.py); [`backend/tests/test_knowledge_scenarios.py`](../../backend/tests/test_knowledge_scenarios.py); Events, propositions, reports and beliefs separate truth from actor evidence and perspective.

<a id="slice-013"></a>
### 013 — Model Routing And Budgeting

- [x] Scheduled first-useful slice ([requirements](sprints/model-routing-and-budgeting.md)).
- Evidence: [`backend/src/grimoire/providers/adapter.py`](../../backend/src/grimoire/providers/adapter.py); [`backend/tests/test_providers_adapter.py`](../../backend/tests/test_providers_adapter.py); Issued planned calls are metered; real HTTP and scripted adapters handle timeout, fallback and no replay of completed calls.

<a id="slice-014"></a>
### 014 — Characterization And Dialogue

- [x] Scheduled first-useful slice ([requirements](sprints/characterization-and-dialogue.md)).
- Evidence: [`backend/src/grimoire/voice/render.py`](../../backend/src/grimoire/voice/render.py); [`backend/tests/test_voice_scenarios.py`](../../backend/tests/test_voice_scenarios.py); Speaking actors receive separate voice bindings/fragments and generation calls.

<a id="slice-015"></a>
### 015 — V1 Cost And Voice Regression

- [ ] Scheduled first-useful slice ([requirements](sprints/v1-cost-and-voice-regression.md)).
- Evidence: [`backend/src/grimoire/evals/v021.py`](../../backend/src/grimoire/evals/v021.py); [`docs/evals/V021_BASELINE.md`](../../docs/evals/V021_BASELINE.md); [`backend/tests/test_v021_replay.py`](../../backend/tests/test_v021_replay.py); Capture conversion and receipt verification plus deterministic replay exist.
- Scope / gap: [#686](https://github.com/charlesmsiegel/grimoire/pull/686) documents capture prerequisites only. No live provider-reported V0.2.1 measurement exists: the one live attempt (2026-11-20, `anthropic`, `claude-sonnet-5-5`) failed on the second speaker's call, because 0.2.1 ends that request with an assistant turn and the model rejects it ([details](../evals/V021_BASELINE.md#live-attempt-2026-11-20)). Baseline capture is still pending, so actual measured baseline/voice regression proof is missing.

<a id="slice-016"></a>
### 016 — Session Zero Boundaries

- [x] Scheduled first-useful slice ([requirements](sprints/session-zero-boundaries.md)).
- Evidence: [`backend/src/grimoire/core/boundaries.py`](../../backend/src/grimoire/core/boundaries.py); [`backend/tests/test_session_zero_scenarios.py`](../../backend/tests/test_session_zero_scenarios.py); Typed session-zero policy and presentation/request decisions preserve canon and explicit intent.

<a id="wave-3"></a>
## Wave 3

All ten scheduled headless first slices are present. Later subject-validation and other milestones remain open.

<a id="slice-107"></a>
### 107 — World Optional Chat Scopes

- [x] Scheduled first-useful slice ([requirements](sprints/world-optional-chat-scopes.md)).
- Evidence: [`backend/src/grimoire/chatstore/library.py`](../../backend/src/grimoire/chatstore/library.py); [`backend/tests/test_chatstore_scenarios.py`](../../backend/tests/test_chatstore_scenarios.py); World-optional chats, source bindings, campaign-wide unique actor IDs and rebuilt navigation survive reopen.
- Scope / gap: Non-character reuse is owned by 033 and now implemented there; this earlier slice does not claim it independently.

<a id="slice-017"></a>
### 017 — Typed Relationship Graph

- [x] Scheduled first-useful slice ([requirements](sprints/typed-relationship-graph.md)).
- Evidence: [`backend/src/grimoire/graph/index.py`](../../backend/src/grimoire/graph/index.py); [`graphstore/library.py`](../../backend/src/grimoire/graphstore/library.py); [`backend/tests/test_graphstore_scenarios.py`](../../backend/tests/test_graphstore_scenarios.py); Typed relations preserve endpoints/time and director traversal; graph connectivity does not itself grant actor knowledge.
- Scope / gap: Actor views use evidence claims rather than unrestricted graph traversal.

<a id="slice-018"></a>
### 018 — Image Identity And Placements

- [x] Scheduled first-useful slice ([requirements](sprints/image-identity-and-placements.md)).
- Evidence: [`backend/src/grimoire/imagestore/library.py`](../../backend/src/grimoire/imagestore/library.py); [`backend/tests/test_imagestore_scenarios.py`](../../backend/tests/test_imagestore_scenarios.py); Shared image bytes and independent scoped placements/roles/crops survive reopen and failures.

<a id="slice-019"></a>
### 019 — Sync Revisions And Conflict Core

- [x] Scheduled first-useful slice ([requirements](sprints/sync-revisions-and-conflict-core.md)).
- Evidence: [`backend/src/grimoire/sync/session.py`](../../backend/src/grimoire/sync/session.py); [`backend/tests/test_sync_scenarios.py`](../../backend/tests/test_sync_scenarios.py); [`test_wave3_integration.py`](../../backend/tests/test_wave3_integration.py); Revision transfer and concurrent conflict preservation avoid wall-clock winner selection.
- Scope / gap: Transport separately 028; not every later record family is synchronized.

<a id="slice-020"></a>
### 020 — Durable Event Evidence

- [x] Scheduled first-useful slice ([requirements](sprints/durable-event-evidence.md)).
- Evidence: [`backend/src/grimoire/evidencestore/library.py`](../../backend/src/grimoire/evidencestore/library.py); [`backend/tests/test_evidencestore_scenarios.py`](../../backend/tests/test_evidencestore_scenarios.py); Private witness, false report and excluded bystander persist and rebuild correct actor perspectives.
- Scope / gap: Later 017 subject-validation obligation remains: append does not call check_subjects for definition/future-instance subjects.

<a id="slice-021"></a>
### 021 — Turn Effects And Wrap Up

- [x] Scheduled first-useful slice ([requirements](sprints/turn-effects-and-wrap-up.md)).
- Evidence: [`backend/src/grimoire/effectstore/library.py`](../../backend/src/grimoire/effectstore/library.py); [`backend/tests/test_effectstore_scenarios.py`](../../backend/tests/test_effectstore_scenarios.py); Proposed/accepted effects require authority endorsements and persist idempotent state changes.

<a id="slice-022"></a>
### 022 — Effective World Snapshots

- [x] Scheduled first-useful slice ([requirements](sprints/effective-world-snapshots.md)).
- Evidence: [`backend/src/grimoire/effective/library.py`](../../backend/src/grimoire/effective/library.py); [`core/portrayal.py`](../../backend/src/grimoire/core/portrayal.py); [`backend/tests/test_effective_store.py`](../../backend/tests/test_effective_store.py); Pinned/inherited effective source and portrayal snapshots include explicit clears and rebuildable derived state.

<a id="slice-023"></a>
### 023 — Counted Inventory

- [x] Scheduled first-useful slice ([requirements](sprints/counted-inventory.md)).
- Evidence: [`backend/src/grimoire/holdingstore/library.py`](../../backend/src/grimoire/holdingstore/library.py); [`backend/tests/test_holdingstore_scenarios.py`](../../backend/tests/test_holdingstore_scenarios.py); Exact holding changes preserve separate counted identities and durable idempotent updates.

<a id="slice-024"></a>
### 024 — Creature Individualization

- [x] Scheduled first-useful slice ([requirements](sprints/creature-individualization.md)).
- Evidence: [`backend/src/grimoire/core/individual.py`](../../backend/src/grimoire/core/individual.py); [`backend/tests/test_individual.py`](../../backend/tests/test_individual.py); Controller-authorized individualization preserves reference while assigning local identity and provenance.

<a id="slice-025"></a>
### 025 — Fictional Abilities

- [x] Scheduled first-useful slice ([requirements](sprints/fictional-abilities.md)).
- Evidence: [`backend/src/grimoire/core/abilities.py`](../../backend/src/grimoire/core/abilities.py); [`core/ability_use.py`](../../backend/src/grimoire/core/ability_use.py); [`backend/tests/test_ability_scenarios.py`](../../backend/tests/test_ability_scenarios.py); Fictional abilities express costs, limits and preconditions with explicit use rulings.

<a id="wave-4"></a>
## Wave 4

033 follow-up closes the non-character chat roster gap; the original snapshot lacked it.

<a id="slice-026"></a>
### 026 — V1 To V2 Import Apply

- [x] Scheduled first-useful slice ([requirements](sprints/v1-to-v2-import-apply.md)).
- Evidence: [`backend/src/grimoire/import_v1/_plan.py`](../../backend/src/grimoire/import_v1/_plan.py); [`backend/tests/test_import_v1.py`](../../backend/tests/test_import_v1.py); Verified unchanged inventory applies worlds, actors, scenes and shared art transactionally with idempotent receipts, preserving V2 edits.
- Scope / gap: Historical transcript import is later 036.

<a id="slice-027"></a>
### 027 — Per Npc Generation Runtime

- [x] Scheduled first-useful slice ([requirements](sprints/per-npc-generation-runtime.md)).
- Evidence: [`backend/src/grimoire/runtime/runtime.py`](../../backend/src/grimoire/runtime/runtime.py); generation; [`backend/tests/test_generation_runtime.py`](../../backend/tests/test_generation_runtime.py); [`test_wave4_integration.py`](../../backend/tests/test_wave4_integration.py); One metered call per speaking NPC uses sealed private, pinned/inherited actor briefs; scenario director generation and cancellation are supported.
- Scope / gap: Headless scripted proving cases; returns proposals rather than committing them.

<a id="slice-028"></a>
### 028 — App Managed Device Sync

- [x] Scheduled first-useful slice ([requirements](sprints/app-managed-device-sync.md)).
- Evidence: [`backend/src/grimoire/peersync/session.py`](../../backend/src/grimoire/peersync/session.py); [`peersync/server.py`](../../backend/src/grimoire/peersync/server.py); [`backend/tests/test_peersync.py`](../../backend/tests/test_peersync.py); Actual separate subprocesses pair/authenticate and transfer records and image bytes over loopback, reporting conflicts/incomplete states.; [`backend/src/grimoire/sync`](../../backend/src/grimoire/sync)/{units,ledger_resolve,deletion,dismiss,bookkeeping}.py; [`backend/src/grimoire/peersync/ledger.py`](../../backend/src/grimoire/peersync/ledger.py); backend/tests/test_{sync_knowledge,sync_ledger,sync_deletion,sync_dismiss,sync_shards,peersync_knowledge,peersync_ledger,peersync_resolve}.py; Inherited first-proof obligations also have implementation and tests: ledger/knowledge merges, dismissal, deletion propagation, sharding, remote resolution.
- Scope / gap: Loopback authenticated first transport; encrypted LAN/off-LAN, discovery, relays and background sync are later milestones.

<a id="slice-029"></a>
### 029 — Fiction First Adjudication

- [x] Scheduled first-useful slice ([requirements](sprints/fiction-first-adjudication.md)).
- Evidence: [`backend/src/grimoire/adjudicationstore/library.py`](../../backend/src/grimoire/adjudicationstore/library.py); adjudication; [`backend/tests/test_adjudication_store.py`](../../backend/tests/test_adjudication_store.py); Recorded frames/stakes, deterministic classification and cited factors produce authority-ledger decisions and effect handoffs.
- Scope / gap: Later graded contests/consequences and stronger source verification remain.

<a id="slice-030"></a>
### 030 — Durable Action Recovery

- [x] Scheduled first-useful slice ([requirements](sprints/durable-action-recovery.md)).
- Evidence: [`backend/src/grimoire/effectstore/recovery.py`](../../backend/src/grimoire/effectstore/recovery.py); [`backend/tests/test_effect_completion_recovery.py`](../../backend/tests/test_effect_completion_recovery.py); After real staged-write crash, complete/adopt/partial revoke validate receipts and finish or abort exactly once.
- Scope / gap: Startup partial detection, persisted proposal recovery and multi-transaction actions are later slices.

<a id="slice-031"></a>
### 031 — Context Retrieval

- [x] Scheduled first-useful slice ([requirements](sprints/context-retrieval.md)).
- Evidence: [`backend/src/grimoire/context/pack.py`](../../backend/src/grimoire/context/pack.py); contextstore; [`backend/tests/test_context_scenarios.py`](../../backend/tests/test_context_scenarios.py); Budgeted actor packs select evidence-safe bound source/common lore and reject stale packs in world/worldless chats.

<a id="slice-037"></a>
### 037 — Character Card Ingestion

- [x] Scheduled first-useful slice ([requirements](sprints/character-card-ingestion.md)).
- Evidence: [`backend/src/grimoire/cardstore/library.py`](../../backend/src/grimoire/cardstore/library.py); [`backend/tests/test_cardstore_scenarios.py`](../../backend/tests/test_cardstore_scenarios.py); Approved worldless character V3/lorebook/scenario ingestion retains authored roles, greeting/art provenance, idempotence and revisioned updates.
- Scope / gap: Not a claim that every container/card version in the eventual roadmap is done.

<a id="slice-032"></a>
### 032 — Diegetic Communication

- [x] Scheduled first-useful slice ([requirements](sprints/diegetic-communication.md)).
- Evidence: [`backend/src/grimoire/comms/transmit.py`](../../backend/src/grimoire/comms/transmit.py); [`backend/tests/test_comms_scenarios.py`](../../backend/tests/test_comms_scenarios.py); Whisper/telepathic/spoken receipts persist only for valid recipients, distinct from sent events.
- Scope / gap: Languages, setting-specific channels and expanded range/barrier handling are later slices.

<a id="slice-033"></a>
### 033 — Party And Presence

- [x] Scheduled first-useful slice ([requirements](sprints/party-and-presence.md)).
- Follow-up evidence ([#680](https://github.com/charlesmsiegel/grimoire/pull/680), rechecked 2026-11-20): [`chatstore/library.py`](../../backend/src/grimoire/chatstore/library.py), [`chats/model.py`](../../backend/src/grimoire/chats/model.py) and [`core/snapshot.py`](../../backend/src/grimoire/core/snapshot.py) support explicit native actors without changing their kind, including two same-species individuals in one chat. [`test_chatstore_actors.py`](../../backend/tests/test_chatstore_actors.py) proves public create/update/reopen, instance-targeted detach, qualified provenance, stale/unauthorized/paired-ID refusals and the real scene runner's boxed-speaker refusal. [`test_partystore_scenarios.py`](../../backend/tests/test_partystore_scenarios.py) retains the membership/release proof.
- Scope / gap: Headless scheduled slice complete. No new UI adapter, species voice-generation contract or controller-knowledge projection; later milestones are not certified by this check.

<a id="slice-034"></a>
### 034 — Unique Equipment

- [x] Scheduled first-useful slice ([requirements](sprints/unique-equipment.md)).
- Evidence: [`backend/src/grimoire/possessionstore/library.py`](../../backend/src/grimoire/possessionstore/library.py); [`possessions/plan.py`](../../backend/src/grimoire/possessions/plan.py); [`backend/tests/test_possessionstore_scenarios.py`](../../backend/tests/test_possessionstore_scenarios.py); Two same-name unique items keep distinct provenance/owner/holder/revisions and idempotent transfer/equipment state.
- Scope / gap: Ruleset effects, item abilities and authored artifacts are later slices.

<a id="wave-5"></a>
## Wave 5

All eight scheduled first slices are present, including both persistent text-play paths. Later milestones are not implied.

<a id="slice-035"></a>
### 035 — Minimal Text Scene Runner

- [x] Scheduled first-useful slice ([requirements](sprints/minimal-text-scene-runner.md)).
- Evidence: [`backend/src/grimoire/play/runner.py`](../../backend/src/grimoire/play/runner.py); [`backend/src/grimoire/scenestore`](../../backend/src/grimoire/scenestore); [`backend/tests/test_scene_proof.py`](../../backend/tests/test_scene_proof.py).
- Scope / gap: Play/save/reopen V1, card/lore and scenario with separate NPC voices; later canon/event milestones excluded.

<a id="slice-036"></a>
### 036 — Campaign Archaeology

- [x] Scheduled first-useful slice ([requirements](sprints/campaign-archaeology.md)).
- Evidence: [`backend/src/grimoire/archaeologystore/library.py`](../../backend/src/grimoire/archaeologystore/library.py); [`backend/tests/test_archaeologystore_scenarios.py`](../../backend/tests/test_archaeologystore_scenarios.py); [`backend/src/grimoire/archaeology/sillytavern.py`](../../backend/src/grimoire/archaeology/sillytavern.py); [`backend/tests/test_archaeology_st.py`](../../backend/tests/test_archaeology_st.py).
- Scope / gap: ST message/swipe import with explicit speakers and unknown audiences; V1 transcript provenance preserved.

<a id="slice-038"></a>
### 038 — Expression And Art Lineage

- [x] Scheduled first-useful slice ([requirements](sprints/expression-and-art-lineage.md)).
- Evidence: [`backend/src/grimoire/imagestore`](../../backend/src/grimoire/imagestore); [`backend/tests/test_imagestore_scenarios.py`](../../backend/tests/test_imagestore_scenarios.py).
- Scope / gap: Role/slot-qualified card placements with explicit unresolved/hide semantics.

<a id="slice-039"></a>
### 039 — NPC Goals And Autonomy

- [x] Scheduled first-useful slice ([requirements](sprints/npc-goals-and-autonomy.md)).
- Evidence: [`backend/src/grimoire/goals/policy.py`](../../backend/src/grimoire/goals/policy.py); [`backend/tests/test_goals_scenarios.py`](../../backend/tests/test_goals_scenarios.py).
- Scope / gap: Deterministic credible agenda/limit policy in ordinary NPC call; no extra default inference.

<a id="slice-040"></a>
### 040 — Obstacles And Agency

- [x] Scheduled first-useful slice ([requirements](sprints/obstacles-and-agency.md)).
- Evidence: [`backend/src/grimoire/obstacles`](../../backend/src/grimoire/obstacles); [`backend/tests/test_scene_obstacles.py`](../../backend/tests/test_scene_obstacles.py).
- Scope / gap: Novel approach allowed, adjudicated risk/cost/stakes supported; group/procedural later milestones excluded.

<a id="slice-041"></a>
### 041 — Scene Physical Continuity

- [x] Scheduled first-useful slice ([requirements](sprints/scene-physical-continuity.md)).
- Evidence: [`backend/src/grimoire/staging`](../../backend/src/grimoire/staging); [`backend/tests/test_staging_scenarios.py`](../../backend/tests/test_staging_scenarios.py).
- Scope / gap: Scene-local reach/see/hear, locations and custody transitions; persistent authored stage later.

<a id="slice-042"></a>
### 042 — Source Rebase And Semantic Merge

- [x] Scheduled first-useful slice ([requirements](sprints/source-rebase-and-semantic-merge.md)).
- Evidence: [`backend/src/grimoire/rebasestore/rebase.py`](../../backend/src/grimoire/rebasestore/rebase.py); [`backend/tests/test_rebasestore_scenarios.py`](../../backend/tests/test_rebasestore_scenarios.py); [`backend/src/grimoire/rebasestore/canon.py`](../../backend/src/grimoire/rebasestore/canon.py); [`backend/tests/test_rebasestore_history.py`](../../backend/tests/test_rebasestore_history.py).
- Scope / gap: Reviewed pinned-actor rebase and selected chat actors proposed as reusable world; history preserved.

<a id="slice-043"></a>
### 043 — World Change Sets

- [x] Scheduled first-useful slice ([requirements](sprints/world-change-sets.md)).
- Evidence: [`backend/src/grimoire/changesetstore/library.py`](../../backend/src/grimoire/changesetstore/library.py); [`backend/tests/test_changesetstore.py`](../../backend/tests/test_changesetstore.py).
- Scope / gap: Atomic two-record review/apply, exact-byte approval and stale abort.

<a id="wave-6"></a>
## Wave 6

Scheduled first slices are implemented; physical-phone validation remains unverified.

<a id="slice-044"></a>
### 044 — V2 Mobile Client

- [x] Scheduled first-useful slice ([requirements](sprints/v2-mobile-client.md)).
- Evidence: [`backend/src/grimoire/app`](../../backend/src/grimoire/app); [`backend/tests/test_textapi_proof.py`](../../backend/tests/test_textapi_proof.py); [`docs/MOBILE.md`](../../docs/MOBILE.md); [`backend/tests/test_app_release_proof.py`](../../backend/tests/test_app_release_proof.py).
- Scope / gap: Runnable responsive web/Termux client and paired loopback sync; physical-phone acceptance not verified, distinct from code completeness.
- Emulated-phone follow-up: a touch (Pixel 7 profile) Chromium journey taps through setup, import, play, review, a cast-panel picture, touch scrolling, an emulated on-screen keyboard, back navigation and a 24px target sweep; the paired sync and offline-fork journey runs its second client as the same touch phone ([acceptance](../ACCEPTANCE.md)). It found and fixed 13–23px touch targets. The numbered physical-phone script in [ACCEPTANCE](../ACCEPTANCE.md#manual-release-evidence-still-required) and the no-Termux USB path in [MOBILE](../MOBILE.md) are ready; the Wave 6 exit stays open until a physical-phone run is recorded.

<a id="slice-052"></a>
### 052 — Instant Campaign Creation

- [x] Scheduled first-useful slice ([requirements](sprints/instant-campaign-creation.md)).
- Evidence: [`backend/src/grimoire/quickstart`](../../backend/src/grimoire/quickstart); [`backend/tests/test_quickstart.py`](../../backend/tests/test_quickstart.py).
- Scope / gap: Character/scenario/bundle/premise quick start and later card add, real start page wiring.

<a id="slice-045"></a>
### 045 — Decision Policies

- [x] Scheduled first-useful slice ([requirements](sprints/decision-policies.md)).
- Evidence: [`backend/src/grimoire/decisionstore`](../../backend/src/grimoire/decisionstore); [`backend/tests/test_decision_store.py`](../../backend/tests/test_decision_store.py).
- Scope / gap: Bounded optional capped decision with evidence and retry replay; 112 now wires the service into the real app, with opt-in per-turn caps and reroll replay.

<a id="slice-046"></a>
### 046 — Live Inheritance And Overrides

- [x] Scheduled first-useful slice ([requirements](sprints/live-inheritance-and-overrides.md)).
- Evidence: [`backend/src/grimoire/imagestore/artpin.py`](../../backend/src/grimoire/imagestore/artpin.py); [`backend/tests/test_art_pin.py`](../../backend/tests/test_art_pin.py); [`backend/src/grimoire/core/inheritance.py`](../../backend/src/grimoire/core/inheritance.py); [`backend/src/grimoire/effective/inspect.py`](../../backend/src/grimoire/effective/inspect.py); [`backend/tests/test_inheritance.py`](../../backend/tests/test_inheritance.py); [`backend/tests/test_mediaapi_lineage.py`](../../backend/tests/test_mediaapi_lineage.py).
- Scope / gap: Inheritance inspector/clear/reset/override plus derived and pinned art. Later per-instance field overrides and old actor snapshot migration excluded.

<a id="slice-047"></a>
### 047 — Narration Continuity Referee

- [x] Scheduled first-useful slice ([requirements](sprints/narration-continuity-referee.md)).
- Evidence: [`backend/src/grimoire/narration_review`](../../backend/src/grimoire/narration_review); [`backend/src/grimoire/play/continuity.py`](../../backend/src/grimoire/play/continuity.py); [`backend/tests/test_narration_extraction.py`](../../backend/tests/test_narration_extraction.py); [`backend/tests/test_scene_continuity.py`](../../backend/tests/test_scene_continuity.py); [`backend/tests/test_app_continuity.py`](../../backend/tests/test_app_continuity.py); [current contract](../REFEREE.md).
- Scope / limit: separate non-narrator atomic extraction feeds the pure referee in production generation. Synthetic HTTP/provider proofs cover source anchors, durable owner-only evidence, optional-call accounting, cancellation/retry, freshness and exact acceptance tokens. Whole-take spends accumulate; mixed reviewed takes require an explicit owner exception because the aggregate is unchecked. Model completeness is an assertion, not semantic certification. Live-model quality and 112/113 graphical acceptance remain separate gates.

<a id="slice-048"></a>
### 048 — Performance And Tracing

- [x] Scheduled first-useful slice ([requirements](sprints/performance-and-tracing.md)).
- Evidence: [`backend/src/grimoire/tracing`](../../backend/src/grimoire/tracing); [`backend/tests/test_scene_bench.py`](../../backend/tests/test_scene_bench.py).
- Scope / gap: Cold/warm end-to-end benchmark spans, cost/usage; synthetic model benchmark is not a live-provider latency claim.

<a id="wave-7"></a>
## Wave 7

All seven scheduled first-useful slices are checked, including real CLI/Chromium follow-up proofs for 112 and 113. The full keyboard/screen-reader/touch acceptance gate remains unverified; scoped proofs do not close the global exit gate.

<a id="slice-108"></a>
### 108 — UI Shell, Onboarding and Navigation

- [x] Scheduled first-useful slice ([requirements](sprints/ui-shell-onboarding-navigation.md)).
- Evidence: [`backend/src/grimoire/app/static/shell`](../../backend/src/grimoire/app/static/shell); [`backend/tests/test_app_shell.py`](../../backend/tests/test_app_shell.py); [`backend/tests/test_app_shell_js.py`](../../backend/tests/test_app_shell_js.py); [`backend/tests/test_app_setup.py`](../../backend/tests/test_app_setup.py).
- Scope / gap: Real setup/routing/navigation/palette/drafts/theme shell. Actual keyboard/mobile browser acceptance not verified.

<a id="slice-109"></a>
### 109 — UI Library, Facets and Portrayals

- [x] Scheduled first-useful slice ([requirements](sprints/ui-library-facets-portrayals.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/source`](../../backend/src/grimoire/app/static/pages/source); [`backend/tests/test_sourceapi.py`](../../backend/tests/test_sourceapi.py); [`backend/src/grimoire/app/adapters/sources.py`](../../backend/src/grimoire/app/adapters/sources.py).
- Scope / gap: Actual source authoring, provenance, revisions and field/art inheritance; native authored voice later/unavailable. Browser smoke unverified.

<a id="slice-110"></a>
### 110 — UI Import, Migration and Change Review

- [x] Scheduled first-useful slice ([requirements](sprints/ui-import-migration-review.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/import/page.js`](../../backend/src/grimoire/app/static/pages/import/page.js); [`backend/tests/test_reviewapi.py`](../../backend/tests/test_reviewapi.py); [`backend/src/grimoire/app/adapters/imports.py`](../../backend/src/grimoire/app/adapters/imports.py).
- Scope / gap: Real V1/card/history/change-set/rebase exact preview/approval with stale protections; browser smoke unverified.

<a id="slice-111"></a>
### 111 — UI Chat Launch and Scene Play

- [x] Scheduled first-useful slice ([requirements](sprints/ui-chat-launch-play.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/chat/page.js`](../../backend/src/grimoire/app/static/pages/chat/page.js); [`backend/tests/test_app_play.py`](../../backend/tests/test_app_play.py); [`backend/src/grimoire/app/static/pages/start/page.js`](../../backend/src/grimoire/app/static/pages/start/page.js); [`backend/src/grimoire/app/adapters/play.py`](../../backend/src/grimoire/app/adapters/play.py); [`backend/tests/test_app_play_js.py`](../../backend/tests/test_app_play_js.py).
- Scope / gap: Actual launches, typed intentions, separate speaker calls, takes/keep/skip/pause/resume and recovery; browser smoke unverified.

<a id="slice-112"></a>
### 112 — UI Inference, Prompts and Costs

- [x] Scheduled first-useful slice ([requirements](sprints/ui-inference-prompts-costs.md)); follow-up implementation and synthetic browser evidence below supersede the original snapshot's two integration gaps.
- Evidence: [`app/inference.py`](../../backend/src/grimoire/app/inference.py), [`play/runner.py`](../../backend/src/grimoire/play/runner.py), [`providers/context.js`](../../backend/src/grimoire/app/static/pages/providers/context.js); [`test_app_inference_context.py`](../../backend/tests/test_app_inference_context.py), [`test_app_inference_decisions.py`](../../backend/tests/test_app_inference_decisions.py), [`test_play_decisions.py`](../../backend/tests/test_play_decisions.py), and [`test_app_inference_browser.py`](../../backend/tests/test_app_inference_browser.py).
- Outcome: current actor-authorized brief, provenance and budget omissions through shared context assembly; optional bounded 045 choices in the production app with metering, durable replay and default-off settings. Actual CLI/Chromium proof configures a local model and tactics, runs separate NPC calls, disables optional calls, rerolls, recovers a failed inspection request and reopens after restart.
- Limits: device-local authored tactics; advanced conditional policies are preserved but not edited by the simple form. Historical snapshots and unsaved-draft retrieval remain later milestones. Chromium keyboard/narrow-viewport proof does not certify physical-phone or screen-reader acceptance for the full wave.

<a id="slice-113"></a>
### 113 — UI Campaign State and Canon Review

- [x] Scheduled first-useful slice ([requirements](sprints/ui-campaign-state-canon.md)); rechecked at the refresh snapshot.
- Evidence: [`app/campaign_config.py`](../../backend/src/grimoire/app/campaign_config.py) and [`app/campaign_services.py`](../../backend/src/grimoire/app/campaign_services.py) wire explicit startup stage/agenda and optional labeling configuration into CLI/chat/hub; [`test_app_campaign_config.py`](../../backend/tests/test_app_campaign_config.py) covers policy, authority, retries and usage; [`test_app_campaign_review.py`](../../backend/tests/test_app_campaign_review.py) covers stale turn and frame refusal; [`test_app_campaign_browser.py`](../../backend/tests/test_app_campaign_browser.py) runs actual CLI startup/restart and Chromium interactions over synthetic data.
- Scope / limits: browser proof includes keyboard cancellation, failed-label retry, stale second-tab review, separate player/director effect keys, actor-private evidence, absent-NPC exclusion, configured NPC choice, narrow viewport and exactly-once consequence after restart. 039 agendas and 041 stages remain authored startup configuration, not later persisted simulation/editors. Labeling has an explicit independent paid route; no effect follows automatically from narration. Full Wave 7 screen-reader/physical-phone acceptance remains unverified; this closes 113's scheduled first-useful slice only.

<a id="slice-114"></a>
### 114 — UI Media, Sync and Resilience

- [x] Scheduled first-useful slice ([requirements](sprints/ui-media-sync-resilience.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/sync`](../../backend/src/grimoire/app/static/pages/sync); [`backend/tests/test_mediaapi.py`](../../backend/tests/test_mediaapi.py); [`backend/src/grimoire/app/static/pages/art`](../../backend/src/grimoire/app/static/pages/art); [`backend/src/grimoire/app/adapters/media.py`](../../backend/src/grimoire/app/adapters/media.py); [`backend/tests/test_app_release_proof.py`](../../backend/tests/test_app_release_proof.py).
- Scope / gap: Actual media placements/crop/reuse/hide plus pairing/sync/conflict/fork controls. Physical-phone keyboard/touch/screenreader acceptance unverified; no identified code gap in first slice.

<a id="wave-8"></a>
## Wave 8

049, 050, 051, 053, 054, 055, 056, 057, 058, 059, 060, 061, 062, 063, 064, 066, 067, 070 and 072 are complete at their bounded first-proof boundaries. Other domain slices and UI exit 115 remain incomplete or unverified.

<a id="slice-049"></a>
### 049 — Offline Multi Device Sync

- [x] Scheduled first-useful slice ([requirements](sprints/offline-multi-device-sync.md)).
- Evidence: [`sync/semantic.py`](../../backend/src/grimoire/sync/semantic.py); [`test_sync_semantic.py`](../../backend/tests/test_sync_semantic.py); [`test_peersync_semantic.py`](../../backend/tests/test_peersync_semantic.py); [`test_continuity_forks.py`](../../backend/tests/test_continuity_forks.py). Actual compatible synthetic libraries detect relationship/event changes after a scene versus its offline edit, retain variants, block generic-resolution bypasses and stale/deletion races, and explicitly archive/restore compatible fiction before ordinary exactly-once ledger reconciliation. Interrupted resolution reopens and resumes under pinned premises.
- Scope / gap: Headless semantic reconciliation beyond earlier turn forks, documented in [CONTINUITY.md](../CONTINUITY.md). Uncertain ancestry and incompatible continuations refuse; remote fork controls and broad UI are 115. The scheduled conflicting-history proof is complete; arbitrary world-override/private-evidence semantic merging and long-period disconnected media/tombstone/retcon convergence are not claimed.

<a id="slice-050"></a>
### 050 — Capture And Trainer Links

- [x] Scheduled first-useful slice ([requirements](sprints/capture-and-trainer-links.md)).
- Evidence: [`companions`](../../backend/src/grimoire/companions), [`test_companions.py`](../../backend/tests/test_companions.py), [`test_companions_recovery.py`](../../backend/tests/test_companions_recovery.py) and [contract](../COMPANIONS.md). Native acceptance preserves the named actor and one-ball spend through retry/reopen, distinguishes a same-species individual, respects active/reserve capacity, creates no companion on failed capture and recovers native interrupted writes.
- Limits: accepted capture/trainer links are headless; 033 roster exposure and 115 broad UI are separate. No automatic NPC speech, control transfer or actor knowledge follows from the link. `grimoire.capture` remains external model usage capture.

<a id="slice-051"></a>
### 051 — Ensemble Conversation

- [x] Scheduled first-useful slice ([requirements](sprints/ensemble-conversation.md)).
- Evidence: [`ensemble/plan.py`](../../backend/src/grimoire/ensemble/plan.py) and [`ensemble/run.py`](../../backend/src/grimoire/ensemble/run.py); [`test_ensemble.py`](../../backend/tests/test_ensemble.py) exercises a direct question, grounded interjection, meaningful silence, three separately voiced contributions, audience-safe private remarks, player floor protection, stale cast/control, cancellation and retry accounting.
- Scope / gap: Headless scheduled exchange only: authored bids and private plan traces produce unaccepted proposals, not canonical dialogue. No app/UI (115), TTS or real-time overlap; marker screening is not semantic secrecy certification. See [contract](../ENSEMBLE.md).

<a id="slice-053"></a>
### 053 — Memory Consolidation

- [x] Scheduled first-useful slice ([requirements](sprints/memory-consolidation.md)).
- Evidence: [`memory/view.py`](../../backend/src/grimoire/memory/view.py), [`memorystore/library.py`](../../backend/src/grimoire/memorystore/library.py), [`test_memory.py`](../../backend/tests/test_memory.py) and [`test_memory_store.py`](../../backend/tests/test_memory_store.py) provide durable actor-scoped selections, corrections, stale-selection suppression, evidence provenance and bounded historical views; [`test_memory_recovery.py`](../../backend/tests/test_memory_recovery.py) covers local recovery. [`memory/dossier.py`](../../backend/src/grimoire/memory/dossier.py) consolidates authored, evidence-citing accounts into per-topic dossiers ([`test_memory_dossier.py`](../../backend/tests/test_memory_dossier.py)); [`test_memory_consolidation.py`](../../backend/tests/test_memory_consolidation.py) proves the scenario end to end: an NPC's perceptions across three scenes, a recap of the first two, the unmasking's correction and a backlog recap filed afterwards, through the durable ledger, `TextApi`'s runner wiring, the context inspector and a scripted generated brief. The corrected identity is the only account packed; the obsolete percept and both recaps never reach the pack or prompt; each remembered detail resolves to its exact evidence, scene and editor. [`test_context_memory.py`](../../backend/tests/test_context_memory.py) covers contested accounts, controller and moment mismatches.
- Scope / gap: Bounded first proof. Accounts are authored by the controller (no model or automatic summarization, no TextApi or UI write path; 115 owns the UI); identity is a topic, not a structured field; no sync adapter; thousands-of-scenes scale (milestone 3) is unmeasured. See [contract](../MEMORY.md).

<a id="slice-054"></a>
### 054 — Scenes Forks And Retcons

- [x] Scheduled first-useful slice ([requirements](sprints/scenes-forks-and-retcons.md)).
- Evidence: [`continuitystore`](../../backend/src/grimoire/continuitystore); [`test_continuitystore.py`](../../backend/tests/test_continuitystore.py); [`test_effect_continuity.py`](../../backend/tests/test_effect_continuity.py); [`test_continuity_forks.py`](../../backend/tests/test_continuity_forks.py). The transferred-key scenario previews downstream possession, actions, declared dialogue, summary and evidence impacts without changing originals. Native source pins and potential scene/event ordering distinguish recorded causality from uncertainty. Bounded authored rehearsals are speculative and model-free; retained forks preserve exact scene envelopes without replaying accepted effects.
- Scope / gap: Director-only headless previews, explicit compatible-source restoration and pinned correction exports for 053; no automatic downstream repair, prose inference, generated continuations, alternative promotion or 115 UI. The scheduled fork/impact proof and original transferred-key proving scenario are met; authored rehearsal strings reuse the impact graph and do not simulate alternative causal futures or close the later promotion milestone. Missing/stale/legacy inputs remain incomplete or refused rather than inferred. See [CONTINUITY.md](../CONTINUITY.md).

<a id="slice-055"></a>
### 055 — Calendar And Clocks

- [x] Scheduled first-useful slice ([requirements](sprints/calendar-and-clocks.md)).
- Evidence: [`calendarstore/library.py`](../../backend/src/grimoire/calendarstore/library.py) and [`CALENDARS.md`](../CALENDARS.md); [`test_calendarstore.py`](../../backend/tests/test_calendarstore.py)::test_twenty_day_proof_preserves_old_scene_and_retries_after_reopen proves explicit 20-day advancement, one deadline flag and unchanged old transcript dates; arithmetic, offline conflict and crash/I/O retry proofs are in `test_calendars.py`, `test_calendarstore_sync.py` and `test_calendarstore_recovery.py`.
- Scope / gap: Headless Gregorian/Hebrew/fixed-year custom chronology with explicit uncertainty resolution and retained-lineage operation receipts. No calendar UI (115), V1 plugin/data import, complex fictional leap rules, parallel schedule engine, automatic trigger effects, downtime or offscreen simulation. Existing campaign UI still reports clocks unavailable.

<a id="slice-056"></a>
### 056 — Catalog Composition

- [x] Scheduled first-useful slice ([requirements](sprints/catalog-composition.md)).
- Evidence: [`catalogs`](../../backend/src/grimoire/catalogs), [`test_catalog_scenarios.py`](../../backend/tests/test_catalog_scenarios.py) and [contract](../CATALOGS.md) prove shared pinned character/lore/gear in three worlds, gear exclusion, a setting-local creature, independent quick chats and stable context with 2,000 unselected records. `test_one_shared_bestiary_supports_two_settings_without_importing_a_card_set` proves the inherited bestiary scenario: one pinned shared creature, read from its single canonical file, is bound as a campaign creature actor in two world-attached settings, one through a setting-specific specialization that inherits the rest of the shared card; one setting excludes another shared creature from discovery, only the other campaign opts into a 2,002-record creator card set and binds one character and one lorebook selected from it; that lorebook's lore reaches its actual context pack, while none of the 2,000 unselected lorebooks, which the same scene text would trigger if bound, reaches either campaign's pack.
- Scope / gap: Headless only. No catalog UI (115), package distribution (103), follow-head source upgrades (079) or automatic world catalog attachment; a campaign's catalog opt-in is expressed only as its explicit chat bindings, and exclusions are availability, not access control; context costs are the existing deterministic estimates.

<a id="slice-057"></a>
### 057 — Faction Decision Making

- [x] Scheduled first-useful slice ([requirements](sprints/faction-decision-making.md)).
- Evidence: [`institutions/policy.py`](../../backend/src/grimoire/institutions/policy.py) and [`test_institutions.py`](../../backend/tests/test_institutions.py) prove conflicting groups, explicit canonical membership, leader rejection with retained member dissent, separate accepted assistance and local evidence without teaching a distant chapter. Retry, current membership and NPC-control checks fail closed.
- Scope / gap: Pure headless first slice: explicit leader choice, not autonomous political simulation. No persistent institution adapter, UI, resource/territory simulation, rumor delivery or strategic clock advancement. Acceptance and communication remain separate contracts; see [limits](../INSTITUTIONS.md).

<a id="slice-058"></a>
### 058 — Local Multi PC

- [x] Scheduled first-useful slice ([requirements](sprints/local-multi-pc.md)).
- Evidence: [`play/together.py`](../../backend/src/grimoire/play/together.py), [`play/drafts.py`](../../backend/src/grimoire/play/drafts.py), [`textapi/table.py`](../../backend/src/grimoire/textapi/table.py) and the combined turn in [`scene/model.py`](../../backend/src/grimoire/scene/model.py) (`grimoire.scene-turn/2`); [`test_local_multi_pc.py`](../../backend/tests/test_local_multi_pc.py). Two PCs under separate human controllers post one turn, the cast (or the narrator) answers once in one generation, each post keeps its submitter after reopening, and the NPC stays the director's. An NPC, an unseated or another player's character, a duplicate or empty contribution and an unauthenticated handle are refused with nothing written; a retry returns the same turn; per-post whisper audiences, the owner-only handover with control history, the no-revived-delegation rule, one player holding two PCs, editable/reorderable drafts and the text API's local table are covered.
- Scope / gap: Drafts are held in memory (persistence and interrupted-submission recovery are milestone 2); local players share the owner's screen; a combined turn takes no label, framing or effect; troupe play is milestone 3; the drafting UI is 115. See [contract](../LOCAL_MULTI_PC.md).

<a id="slice-059"></a>
### 059 — Location Topology

- [x] Scheduled first-useful slice ([requirements](sprints/location-topology.md)).
- Evidence: [`topology/index.py`](../../backend/src/grimoire/topology/index.py), [`topologystore/library.py`](../../backend/src/grimoire/topologystore/library.py); [`test_topology.py`](../../backend/tests/test_topology.py), [`test_topologystore.py`](../../backend/tests/test_topologystore.py), [`test_topology_chat.py`](../../backend/tests/test_topology_chat.py) and [`test_topologystore_recovery.py`](../../backend/tests/test_topologystore_recovery.py). Four-place save/reopen, containment, directed/conditional routes, isolated campaign block and same-named imported-town refusal are executable; stale dependencies, authorization, crash and retry are covered.
- Scope / gap: Headless director-only topology, not actor knowledge or scene staging. Weighted routes/hazards, movement/travel consumers, maps (097), semantic merge and UI exit 115 remain open; see [contract](../TOPOLOGY.md).

<a id="slice-060"></a>
### 060 — NPC Theory Of Mind

- [x] Scheduled first-useful slice ([requirements](sprints/npc-theory-of-mind.md)).
- Evidence: [`social_hypotheses/policy.py`](../../backend/src/grimoire/social_hypotheses/policy.py) and [`test_social_hypotheses.py`](../../backend/tests/test_social_hypotheses.py) prove an evidence-attributed mistaken second-order conjecture, bluff selection and confidence/tactic revision while director truth and the mayor’s actual belief remain unchanged. Exact-owner recall, depth/input bounds, evidence and authority refusals are covered.
- Scope / gap: Authored, in-memory hypotheses and proposed tactics only; no semantic inference oracle, persistent ledger adapter, runtime/UI wiring or live social-quality measurement. No provider calls; deeper intrigue is later work. See [contract](../SOCIAL_HYPOTHESES.md).

<a id="slice-061"></a>
### 061 — Private And Local Voice

- [x] Scheduled first-useful slice ([requirements](sprints/private-and-local-voice.md)).
- Evidence: [`speech`](../../backend/src/grimoire/speech), [`test_speech_privacy.py`](../../backend/tests/test_speech_privacy.py) and the [speech contract](../SPEECH.md) give separate STT/TTS selection, explicit unsupported fallback, upload/spending refusal, bounded ephemeral retention/export/deletion and duplicate suppression. [`speech/sphinx.py`](../../backend/src/grimoire/speech/sphinx.py) adds an installed local acoustic recognizer (optional PocketSphinx), validated by [`test_speech_local.py`](../../backend/tests/test_speech_local.py) on synthetic Flite speech under a Python socket guard (no connection, no retention, engine log silenced) and, in CI, in a process with no network at all and measured by [`speech/measure.py`](../../backend/src/grimoire/speech/measure.py) (load time, memory, latency, real-time factor, word error rate; a recorded container run is in the contract). The [`voice` adapter](../../backend/src/grimoire/app/adapters/voice.py) and the Voice settings page ([`pages/voice`](../../backend/src/grimoire/app/static/pages/voice)) offer off or this-device-only input, a separate microphone switch, launch-only transcript retention with delete (recordings are never kept), a recording limit and memory release; [`test_app_voice.py`](../../backend/tests/test_app_voice.py) and [`voice.e2e.mjs`](../../backend/tests/js/voice.e2e.mjs) prove the proving scenario: local-only recognition opens no connection and writes nothing, the microphone off recognizes nothing, and typed play is unaffected (voice ops run outside the library's writer lock, so a long recognition never stalls a core op).
- Scope / gap: First useful slice only. No real microphone, real speakers or phone were tested (the page's capture ran against a stand-in microphone; the engine against computer speech), so accuracy for people and a phone's budget are unmeasured; open dictation of synthetic speech is poor (42% word errors) while a command vocabulary is reliable. No speech output or streaming, and this backend is not yet installed into 067's chat voice strip or run through its `evaluate_audio` gate (the strip still starts unavailable).

<a id="slice-062"></a>
### 062 — Ruleset Action Contracts

- [x] Scheduled first-useful slice ([requirements](sprints/ruleset-action-contracts.md)).
- Evidence: [`rulesets/model.py`](../../backend/src/grimoire/rulesets/model.py), [`rulesets/families.py`](../../backend/src/grimoire/rulesets/families.py) and [`test_rulesets.py`](../../backend/tests/test_rulesets.py). Four concrete D20/WoD/PbtA/Ars action/result shapes validate through one pinned module envelope. Tests preserve the PbtA unresolved partial choice and Ars season/XP cap, reject mismatched families/editions, and prove no-mechanics emits no fake roll. [Research and deferred hooks](../RULESETS.md) distinguish opposed actions, clocks, seasonal conflicts and executable capabilities from universal fields.
- Scope / gap: Complete research/prototype first slice, with narrow reference profiles only. Results remain noncanonical proposals: no dice execution, durable resolution (075), executable hooks, combat (090) or UI (115). Pure repeatability is not an exactly-once stored receipt.

<a id="slice-063"></a>
### 063 — Rumors And Information Flow

- [x] Scheduled first-useful slice ([requirements](sprints/rumors-and-information-flow.md)).
- Evidence: [`test_rumor_scenarios.py`](../../backend/tests/test_rumor_scenarios.py) runs the proving scenario through the scene runner: a witness's faithful retelling, then a distorted letter to a distant director NPC timed 21 days on the campaign's 055 calendar and carried only by a courier route; once it arrives and her belief is admitted, her recorded intention changes from trading to the challenge her agenda gates on that proposition, her brief carries the letter's account and never the true insult, and the insult event, its witness's perception, rulings and the proposition's reported standing are unchanged. [`evidencestore/library.py`](../../backend/src/grimoire/evidencestore/library.py) and [`core/knowledge.py`](../../backend/src/grimoire/core/knowledge.py) give every library reader (scene runner, context packs, campaign API, memory) the queued sends as `Knowledge.dispatches`, so a sender's view never changes on delivery; [`test_rumor_store.py`](../../backend/tests/test_rumor_store.py), [`test_rumors.py`](../../backend/tests/test_rumors.py) and [`test_rumor_recovery.py`](../../backend/tests/test_rumor_recovery.py) prove calendar timing, the overlay's fail-closed cases (damaged or missing rumor ledger) and crash reconciliation.
- Scope / gap: Complete bounded first-useful slice. Belief admission on receipt is the director's explicit act, and delivery conditions are supplied explicitly rather than inferred. The rumor ledger is not synced (a peer without it shows no sends), faction/public-media spread at scale (milestone 3) and UI (115) remain pending. See [contract](../RUMORS.md).

<a id="slice-064"></a>
### 064 — Scenario Preparation

- [x] Scheduled first-useful slice ([requirements](sprints/scenario-preparation.md)).
- Evidence: [`preparation/generate.py`](../../backend/src/grimoire/preparation/generate.py) and [`test_preparation_generation.py`](../../backend/tests/test_preparation_generation.py) generate one scene through the real provider adapter (scripted transport): a metered, approved `planning` call grounded in secret, goal, calendar and choice evidence returns premise, cast, location, questions, clues, tensions, stakes and two directions. The reviewed meeting goes stale when the player decides to leave town; a new request after that choice prepares a different, roadside draft from the updated evidence, and the unplayed meeting never enters evidence (no writer exists). [`preparation/session.py`](../../backend/src/grimoire/preparation/session.py) and [`test_preparation.py`](../../backend/tests/test_preparation.py) own review, replay, rejection and staleness. Invented evidence, uncast or foreign instances, display-name matching, one direction, extra canon fields, truncated, refused, unbudgeted and unparseable replies fail closed.
- Scope / gap: Headless first slice. Callers assemble the evidence snapshot; no store reader, app service or UI (115) wires it. In-memory session only; no linked scenario sets, multi-session kits or durable notes. Credible stakes, open agency and respect for the latest choice are director review, not automatic checks (grounding is checked by evidence ID); one call per explicit request with no aggregate budget cap; no live-model quality or cost was measured. See [contract](../PREPARATION.md).

<a id="slice-065"></a>
### 065 — Scene Workspace

- [x] Scheduled first-useful slice ([requirements](sprints/scene-workspace.md)).
- Evidence: [`textapi/workspace.py`](../../backend/src/grimoire/textapi/workspace.py) (#694) pages the whole library's chats, sources and exact/validated-descendant portrayal comparisons with a continuation fingerprint, and lists campaign and world choosers; [`textapi/navcache.py`](../../backend/src/grimoire/textapi/navcache.py) reuses one validated navigation basis while a metadata stamp of the store is unchanged, shared with the legacy `chats` listing and the uncapped campaign list/hub. Source pages, the campaign hub and world entries link into Chats ([`source/page.js`](../../backend/src/grimoire/app/static/pages/source/page.js), [`campaign/hub.js`](../../backend/src/grimoire/app/static/pages/campaign/hub.js)); [`shell/drafts.js`](../../backend/src/grimoire/app/static/shell/drafts.js) keeps composer drafts. [`test_scene_workspace_js.py`](../../backend/tests/test_scene_workspace_js.py) runs [`workspace.e2e.mjs`](../../backend/tests/js/workspace.e2e.mjs) against a real app server: one campaign chat reached from Chats, its character's exact and derived-portrayal lists, its scenario, its campaign hub, its world (hub link, World chooser, world entry page), with the composer draft intact each time and the chat record unwritten; then a second draft in a chat of a portrayal derived from a catalog character, absent from that character's exact list, present under Include derived portrayals (a same-named unrelated character never is), and reached with its draft. Disabling descendant matching fails it. [`test_workspace.py`](../../backend/tests/test_workspace.py) checks that unchanged pages do not reload the store, that the reused basis equals a rebuild, that an in-process commit or an outside same-size, same-mtime rewrite rebuilds it, and that a load degraded by a one-off read failure is reloaded after its short retry window; [`library.e2e.mjs`](../../backend/tests/js/library.e2e.mjs) checks that a pinned revision's page browses by its pin.
- Scope / gap: First useful proof only, documented in [CHATS](../CHATS.md#scene-workspace-browsing-065) and [SCENE_WORKSPACE](../SCENE_WORKSPACE.md). The stamp walk stays linear in file count and the basis is per process (no cache persisted across restarts); a rewrite that keeps size, both timestamps and inode is not detected, and on Windows (ctime is creation time) nor is an in-place same-size rewrite that restores mtime. A library with a lasting load problem reloads after each two-second retry window. The campaign list reply now grows with the library. Saved filters are device-local and unsynced. Milestones 2–3 (compact status panels, configurable layouts/workspaces) and the sprint's keyboard-only missing-item correction scenario remain later work; no real screen reader or physical device was used.

<a id="slice-066"></a>
### 066 — Type And API Boundaries

- [x] Scheduled first-useful slice ([requirements](sprints/type-and-api-boundaries.md)).
- Evidence: [`textapi/errors.py`](../../backend/src/grimoire/textapi/errors.py) has one typed ApiRefused carrying machine-readable reason and HTTP 400/404/409; [`docs/TEXT_API.md`](../../docs/TEXT_API.md) documents the frozen op/error contract. [`app/static/shell/api.js`](../../backend/src/grimoire/app/static/shell/api.js) defines ApiError preserving reason/status and distinguishing network/bad_response; [`shell/ui.js`](../../backend/src/grimoire/app/static/shell/ui.js) displays the distinct reason without losing drafts. [`test_textapi.py`](../../backend/tests/test_textapi.py) checks invalid payload 400, missing chat 404, stale edits 409 and write-free rejection. Targeted text API and paired offline-turn tests: 18 passed in 33.27s.
- Scope / gap: First useful isolated-boundary proof is satisfied by existing 044/108 implementations regardless sprint attribution; not a claim of repository-wide type migration or generated schemas.

<a id="slice-067"></a>
### 067 — Voice Accessibility And Testing

- [x] Scheduled first-useful slice ([requirements](sprints/voice-accessibility-and-testing.md)).
- Evidence: false-command and noise tests now run on synthetic **audio** through a real offline recognizer, with no always-on microphone. [`speech/capture.py`](../../backend/src/grimoire/speech/capture.py) takes one gesture-opened window (keyboard, pointer or switch) and blocks speech already under way, late, cut-off, multi-segment or empty windows before recognition; [`speech/acoustic.py`](../../backend/src/grimoire/speech/acoustic.py) supplies deterministic quiet, noise, hum, far-talker and mixing degradations; [`evaluation.evaluate_audio`](../../backend/src/grimoire/speech/evaluation.py) measures unintended, wrong and bystander actions, completion and latency and refuses remote or paid backends. 37 synthetic espeak-ng/MBROLA clips ([`fixtures/speech_audio`](../../backend/tests/fixtures/speech_audio)) and 75 cases ([`speech_audio_corpus.py`](../../backend/tests/speech_audio_corpus.py)) run through PocketSphinx 5.0.4 ([`sphinx_recognizer.py`](../../backend/tests/sphinx_recognizer.py)): 0 unintended actions on 28 negatives, 0 wrong actions, 0 of 13 bystander utterances recognized, 72/75 tasks completed; background "delete scene" never acts; quiet and atypical (formant) speakers confirm a safe action by voice or Yes and return to typing ([`test_speech_acoustic.py`](../../backend/tests/test_speech_acoustic.py); unsafe mutants fail). The app's `speech.*` ops ([`adapters/speech.py`](../../backend/src/grimoire/app/adapters/speech.py), [`test_app_speech.py`](../../backend/tests/test_app_speech.py)) and the chat page's voice strip ([`voice.js`](../../backend/src/grimoire/app/static/pages/chat/voice.js), [`voice.test.mjs`](../../backend/tests/js/voice.test.mjs)) add captions, Yes/No review, Back to typing (Esc), Alt+V listen-once and read-aloud with pause and replay. Earlier transcript/event cases remain ([`test_speech_evaluation.py`](../../backend/tests/test_speech_evaluation.py)). See [speech contract](../SPEECH.md).
- Scope / gap: the first-useful proof is synthetic audio and one test recognizer, not real rooms, microphones, accents or disabilities. A bystander who starts promptly inside the person's window is attributed to the person (measured as known limits: an instant command can act, never delete, undo, reroll or post). Still unverified, needing people and devices: a real microphone/speaker run, hands-free campaign play with switch or keyboard-only input, accessibility-user testing (sprint milestone 3), and 061's product backend through `evaluate_audio`.

<a id="slice-068"></a>
### 068 — Actor Specific Inference

- [ ] Scheduled first-useful slice ([requirements](sprints/actor-specific-inference.md)).
- Evidence: [`actor_inference`](../../backend/src/grimoire/actor_inference), [`test_actor_inference.py`](../../backend/tests/test_actor_inference.py) and [`test_actor_inference_report.py`](../../backend/tests/test_actor_inference_report.py) provide opt-in actor-private planner/critic calls, baseline generation, conservative budget/cancellation, usage/latency reports and an injected synthetic comparison.
- Runtime/UI adoption: the app's `planning` setting (0–16, off by default) runs one capped, untrusted presentation suggestion before each speaking NPC's reply through `GenerationRuntime`; with it off a turn is still one call per speaking NPC ([`test_presentation_planning.py`](../../backend/tests/test_presentation_planning.py)). `python -m grimoire.capture.v2` makes the billed V2-vs-V0.2.1 comparison one step on 015's capture ([`test_capture_v2.py`](../../backend/tests/test_capture_v2.py), dry run and loopback only).
- Scope / gap: No billed comparison has been run: it waits on 015's live capture, and then needs its own spending approval. Actual billing remains unknown and matched measured V0.2.1 cost/voice evidence is absent. Against an `anthropic` capture, V2 reaches Anthropic through its OpenAI-compatible endpoint (no prompt caching, no cached-token counts), so cost will be reported `usage_incomplete`. Synthetic lexical quality and estimated/priced token usage cannot close the proving scenario; see [measurement limits](../ACTOR_INFERENCE.md).

<a id="slice-069"></a>
### 069 — Coding Skills Calibration

- [ ] Scheduled first-useful slice ([requirements](sprints/coding-skills-calibration.md)).
- Evidence: [`grimoire.calibration`](../../backend/src/grimoire/calibration.py) runs a pinned external deterministic-specialist corpus with three repeat trials, unknown-analysis handling and structural confusion counts. [`grimoire.judgepanel`](../../backend/src/grimoire/judgepanel.py) runs three pinned judges × three trials over a behavior-confirmed labelled corpus, with per-call coverage canaries, served-model pin checks, defect false-positive/negative rates, repeatability and pinned-rubric theory scoring; it reproduces the pinned rubric awarding 100 to unread verdicts and refuses to score them ([calibration report and limits](../CALIBRATION.md)).
- Scope / gap: One live, provider-reported capture was run on 2026-11-20 (108 calls; Opus 5.5, Sonnet 5.5 and Haiku 5.5 judges) and `proof.established` is false: `defect_panel_complete` and `theory_panel_scored_and_discriminates` failed ([results](../CALIBRATION.md#live-capture-2026-11-20)). The panel had no false positive or false negative on the 9 defect cases it settled, but one case is unknown, the Sonnet judge gave malformed answers in 13 of 30 defect trials, and the cohesive theory unit was scored in one of three trials. A complete panel and theory discrimination are not established. Structural wrapper detection is not a defect verdict; no blocking grader is activated.

<a id="slice-070"></a>
### 070 — Multimodal Player Input

- [x] Scheduled first-useful slice ([requirements](sprints/multimodal-player-input.md)).
- Evidence: the chat page's [reference panel](../../backend/src/grimoire/app/static/pages/chat/reference-intent.js) lets a player import a sketch, circle a region with a pointer drag (or type it), name the spot and take the offered draft action ("I hide here (Reference: doorway)") into the composer; nothing is posted until they post. With the post, [attach.js](../../backend/src/grimoire/app/static/pages/chat/attach.js) carries the sketch with the outline drawn on it as the post's picture, with provenance origin `player reference intent` and a note saying it is not a world fact. [reference.e2e.mjs](../../backend/tests/js/reference.e2e.mjs) runs the proving scenario on the real page against a real app server, and [test_reference_intent_page.py](../../backend/tests/test_reference_intent_page.py) checks the library on disk gains only the turn and the post's picture, with no map, actor, scene or campaign record written; [reference_carry.test.mjs](../../backend/tests/js/reference_carry.test.mjs) covers circling, refusals, races and retries. Docs: [SCENE_WORKSPACE](../SCENE_WORKSPACE.md#local-reference-intent-070).
- Scope / gap: No image interpretation: the spot is the one the player circled and named, and no model reads the sketch. Inferred positions/objects needing confirmation (milestone 2), gesture and live multimodal participation (milestone 3), carrying private provenance/rights/corrections and sync of unposted annotations remain. Verified in fakedom only, not a real browser or device.

<a id="slice-071"></a>
### 071 — Theory Driven Simplification

- [ ] Scheduled first-useful slice ([requirements](sprints/theory-driven-simplification.md)).
- Evidence: [Opaque envelope schema policy](../STORAGE.md#opaque-record-schema-policy-071-bounded-slice) removes one repeated validation decision in a verified high-churn module; 51 behavior cases passed before/after, and an executed hypothetical NUL-policy rehearsal required three edits before versus one after with 45 passing cases per variant.
- Evidence (actor change): [Chat role rules](../CHATS.md#role-rules-one-owner-071) make `grimoire.chats.ROLES` the single owner of each role's definition kind and actor mode, removing six scattered decision sites. An executed rehearsal adding a hypothetical creature `companion` role took six edits in two modules before (the two obvious table edits silently wrote a character instance for a creature) and one table entry after; `test_chats_new_role.py` proves the new role's creature actors are created, reused and refused by the one chat writer in one commit, and that a build without the role refuses such chats. `test_chats_compat_071.py` reads, composes and edits a chat library written by the pre-071 code, passing before and after; a 1,152-outcome differential dump matched except doubly invalid hand-edited roster entries, which now report the role first, and unhashable roles, which now refuse instead of raising `TypeError`. V2 imports no Pydantic; the suites pass on CPython 3.11 with only `pyluach`.
- Scope / gap: Not yet run on an Android device or emulator (Termux), so the Android runtime part of the proving scenario remains unverified. Chat roles only: app adapters, quickstart, text API filters and 020's character-only projection keep their own role lists, and world lookup is unchanged.

<a id="slice-072"></a>
### 072 — VN Staging And Cues

- [x] Scheduled first-useful slice ([requirements](sprints/vn-staging-and-cues.md)).
- Evidence: [`app/static/pages/chat/cues.js`](../../backend/src/grimoire/app/static/pages/chat/cues.js); [`chat/staging.js`](../../backend/src/grimoire/app/static/pages/chat/staging.js); [`textapi/views.py`](../../backend/src/grimoire/textapi/views.py) `table_present`; [`backend/tests/js/staging_timeline.test.mjs`](../../backend/tests/js/staging_timeline.test.mjs); [`backend/tests/js/staging.test.mjs`](../../backend/tests/js/staging.test.mjs); [`backend/tests/test_textapi.py`](../../backend/tests/test_textapi.py). An authored cue timeline over kept turns carries blocking (place, outfit art, off stage) and background forward, with per-turn focus, none/fade/move transitions on arrival, reduced motion and plain-text stage directions. Staging is limited to each kept take's historical `present` (on a 041 stage only player characters and heard speakers). The proving scenario stages one character in two outfits and a portrait-only NPC with authored and automatic focus and historical arrival/departure, and switching to the transcript leaves its text and the scene payload unchanged with no library call.
- Scope / gap: Cues are device-local and unsynced; art is the current decorative art for the authored slot, not a historical snapshot. Rule-based or inferred cue suggestions, line-level cues and adaptive cinematic composition (milestones 2–3) are not implemented; real-browser motion and screen-reader output are not certified.

<a id="slice-115"></a>
### 115 — Wave 8 UI Exit: Reliable Campaign Play

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-8-ui-exit.md)).
- Evidence: Existing UI offers owner branch choices and reports unavailable campaign features.
- Scope / gap: Required retcon, clocks, advanced party/story and other Wave 8 domain controls are not implemented; Wave 8 not complete.

<a id="wave-9"></a>
## Wave 9

086 meets its headless first slice (validated module authoring with pinned revisions); the other domain slices and UI exit 116 are not complete.

<a id="slice-073"></a>
### 073 — Auxiliary Writing Tools

- [ ] Scheduled first-useful slice ([requirements](sprints/auxiliary-writing-tools.md)).
- Evidence: Scene generation produces fictional turns; draft persistence stores user text.
- Scope / gap: No explicitly hypothetical noncanonical auxiliary reply-generation workflow.

<a id="slice-074"></a>
### 074 — Downtime And Long Activities

- [ ] Scheduled first-useful slice ([requirements](sprints/downtime-and-long-activities.md)).
- Evidence: effectstore supports durable exactly-once accepted effects.
- Scope / gap: No campaign clock or long-activity scheduling/completion; cannot prove 20-day study advances time once.

<a id="slice-075"></a>
### 075 — Mechanical Resolution

- [ ] Scheduled first-useful slice ([requirements](sprints/mechanical-resolution.md)).
- Evidence: adjudication/effects provide fiction-first reviewed outcomes and durable application.
- Scope / gap: No rules-family mechanical roll record that narration consumes without rerolling.

<a id="slice-076"></a>
### 076 — Offscreen World Events

- [ ] Scheduled first-useful slice ([requirements](sprints/offscreen-world-events.md)).
- Evidence: goals policy selects an actor’s current action.
- Scope / gap: No offscreen clock-based faction plan scheduler or bounded absence advancement.

<a id="slice-077"></a>
### 077 — Quests And Commitments

- [ ] Scheduled first-useful slice ([requirements](sprints/quests-and-commitments.md)).
- Evidence: Existing scene/turn records and graph relations record accepted fiction.
- Scope / gap: No explicit promise/commitment deadline and fulfillment-provenance ledger; clocks unavailable.

<a id="slice-078"></a>
### 078 — Relationships And Reputation

- [ ] Scheduled first-useful slice ([requirements](sprints/relationships-and-reputation.md)).
- Evidence: graph relations and actor-specific evidence implement relationships and knowledge scoping foundations.
- Scope / gap: No asymmetric trust/reputation updates with episode provenance and absent-NPC social knowledge proof.

<a id="slice-079"></a>
### 079 — Source Upgrades And Dependencies

- [ ] Scheduled first-useful slice ([requirements](sprints/source-upgrades-and-dependencies.md)).
- Evidence: [`rebase/review.py`](../../backend/src/grimoire/rebase/review.py) and rebasestore implement reviewed same-entity ancestor/new-source/override field merges; [`test_rebase_review.py`](../../backend/tests/test_rebase_review.py) and [`test_rebasestore_scenarios.py`](../../backend/tests/test_rebasestore_scenarios.py) cover them.
- Scope / gap: 042 is not 079 package upgrade/dependency handling: no source disable/removal impact preview and optional versioned dependency manifests.

<a id="slice-080"></a>
### 080 — Speech To Draft

- [ ] Scheduled first-useful slice ([requirements](sprints/speech-to-draft.md)).
- Evidence: intent can classify supplied speech-origin transcripts; drafts persist editable text.
- Scope / gap: No STT provider/microphone transcription-to-editable-draft workflow; synthetic intent tests are insufficient.

<a id="slice-081"></a>
### 081 — Story Graph And Navigation

- [ ] Scheduled first-useful slice ([requirements](sprints/story-graph-and-navigation.md)).
- Evidence: [`app/adapters/play_story.py`](../../backend/src/grimoire/app/adapters/play_story.py) returns scenes=[] with bookmarks and last-time recap; [`campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py) marks story_graph unavailable.
- Scope / gap: No promise-to-eventual-outcome scene graph, branch history or contradiction links.

<a id="slice-082"></a>
### 082 — Agentic Investigations

- [ ] Scheduled first-useful slice ([requirements](sprints/agentic-investigations.md)).
- Evidence: changesets and runtime tool permissions support reviewed writes and bounded basic calls.
- Scope / gap: No multi-step read-only world-audit job with attributed evidence, budget and cancellation.

<a id="slice-083"></a>
### 083 — Character Voice Performance

- [ ] Scheduled first-useful slice ([requirements](sprints/character-voice-performance.md)).
- Evidence: [`voice/profile.py`](../../backend/src/grimoire/voice/profile.py) controls prose portrayal only; generation keeps actor text separate.
- Scope / gap: No audio/TTS renderer for accepted actor/narrator text or per-character voice playback.

<a id="slice-084"></a>
### 084 — Cross Campaign Scene Transfer

- [ ] Scheduled first-useful slice ([requirements](sprints/cross-campaign-scene-transfer.md)).
- Evidence: Qualified source refs and import/history support exist.
- Scope / gap: No reviewed cross-campaign transcript copy with explicit actor identity mapping, untouched origin and noncopied effects.

<a id="slice-085"></a>
### 085 — Live Context Preview

- [ ] Scheduled first-useful slice ([requirements](sprints/live-context-preview.md)).
- Evidence: [`app/inference.py`](../../backend/src/grimoire/app/inference.py) offers current saved actor context after the 112 follow-up; unsaved-draft retrieval is not offered. [`test_app_inference_context.py`](../../backend/tests/test_app_inference_context.py) proves the saved-state inspection boundary.
- Scope / gap: No on-demand unsaved-draft retrieval/context preview. The 112 saved-state inspector does not implement that draft input.

<a id="slice-086"></a>
### 086 — Mechanics Module Authoring

- [x] Scheduled first-useful slice ([requirements](sprints/mechanics-module-authoring.md)).
- Evidence: [`mechanics/module.py`](../../backend/src/grimoire/mechanics/module.py), [`mechanics/sheet.py`](../../backend/src/grimoire/mechanics/sheet.py), [`mechanics/expr.py`](../../backend/src/grimoire/mechanics/expr.py) and [`mechanicsstore/store.py`](../../backend/src/grimoire/mechanicsstore/store.py), with [`test_mechanics_authoring.py`](../../backend/tests/test_mechanics_authoring.py), [`test_mechanicsstore.py`](../../backend/tests/test_mechanicsstore.py) and [`test_mechanicsstore_recovery.py`](../../backend/tests/test_mechanicsstore_recovery.py). An author drafts a module (sheet types, derived values, named checks) as an owned JSON file kept exactly as saved; validation lists the problems it finds by path with did-you-mean hints, checks every formula name and type statically, applies d20/pool family shapes and refuses executable hooks (007). Only a module with no problem publishes, as an immutable content-named revision; a malformed draft cannot publish, load or bind. Sheets and recorded-dice checks pin the revision: the proving scenario edits a module, a fresh check changes and the earlier record still confirms under its own revision and is never re-graded. V0.2.1's d20-basic/pool-basic ship as templates. [Contract](../MECHANICS_AUTHORING.md).
- Scope / gap: Headless first slice. No editor page (116), campaign selection of a published revision, compatibility/migration preview (milestone 2), interactive rule tests (3), rules compilation (4) or durable roll journal (075); `ref` fields, creation pools and advancement are not yet expressible.

<a id="slice-087"></a>
### 087 — World Authoring Assistant

- [ ] Scheduled first-useful slice ([requirements](sprints/world-authoring-assistant.md)).
- Evidence: changesets provides reviewed proposals and selective application infrastructure.
- Scope / gap: No authoring assistant generates three linked towns/guild and selectively accepts two without duplicates; catalog composition also missing.

<a id="slice-116"></a>
### 116 — Wave 9 UI Exit: Mechanics, Authoring and Audio

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-9-ui-exit.md)).
- Evidence: UI explicitly exposes only existing features and unavailable advanced capabilities.
- Scope / gap: Mechanical rolls, validated rules sheets, world suggestions, story/history and audio foundations required by Wave 9 exit remain absent.

<a id="wave-10"></a>
## Wave 10

095 meets the bounded headless trade/craft proof through counted holdings; the other domain slices and UI exit 117 remain incomplete.

<a id="slice-088"></a>
### 088 — Campaign Tuning

- [ ] Scheduled first-useful slice ([requirements](sprints/campaign-tuning.md)).
- Evidence: [`backend/src/grimoire/decisions/model.py`](../../backend/src/grimoire/decisions/model.py); [`backend/src/grimoire/goals`](../../backend/src/grimoire/goals); [`backend/src/grimoire/app/static/pages/campaign/page.js`](../../backend/src/grimoire/app/static/pages/campaign/page.js).
- Scope / gap: No player-visible pacing setting with a real decision/obstacle consumer or side-by-side pacing proof; decision policy and agenda primitives are not campaign tuning.

<a id="slice-089"></a>
### 089 — Character Growth

- [ ] Scheduled first-useful slice ([requirements](sprints/character-growth.md)).
- Evidence: [`backend/src/grimoire/core/abilities.py`](../../backend/src/grimoire/core/abilities.py); [`backend/src/grimoire/core/individual.py`](../../backend/src/grimoire/core/individual.py); [`backend/src/grimoire/campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py).
- Scope / gap: Ability definitions and per-instance identity exist, but no learned-skill/personal-history advancement operation or proof of two species instances advancing independently was found.

<a id="slice-090"></a>
### 090 — Combat Encounter Orchestration

- [ ] Scheduled first-useful slice ([requirements](sprints/combat-encounter-orchestration.md)).
- Evidence: [`backend/src/grimoire/play/runner.py`](../../backend/src/grimoire/play/runner.py); [`backend/src/grimoire/adjudication`](../../backend/src/grimoire/adjudication); [`backend/src/grimoire/staging`](../../backend/src/grimoire/staging).
- Scope / gap: Text turns, freeform adjudication and staging exist; no ruleset combat round/initiative/action-economy orchestrator or two-round three-actor proof.

<a id="slice-091"></a>
### 091 — Conversation Turn Taking

- [ ] Scheduled first-useful slice ([requirements](sprints/conversation-turn-taking.md)).
- Evidence: [`backend/src/grimoire/intent/commands.py`](../../backend/src/grimoire/intent/commands.py); [`backend/src/grimoire/play/runner.py`](../../backend/src/grimoire/play/runner.py); [`backend/src/grimoire/voice/profile.py`](../../backend/src/grimoire/voice/profile.py).
- Scope / gap: Text stop/cancel intent exists, but no audio utterance/end-of-turn/barge-in state machine, staged audio capture or interruption proof.

<a id="slice-092"></a>
### 092 — Fair Mysteries And Deduction

- [ ] Scheduled first-useful slice ([requirements](sprints/fair-mysteries-and-deduction.md)).
- Evidence: [`backend/src/grimoire/obstacles`](../../backend/src/grimoire/obstacles); [`backend/src/grimoire/referee`](../../backend/src/grimoire/referee); [`backend/src/grimoire/core/perspective.py`](../../backend/src/grimoire/core/perspective.py).
- Scope / gap: General obstacle and evidence primitives exist, but no fixed-answer mystery, independent clue routes or novel-investigation proof was found.

<a id="slice-093"></a>
### 093 — Prompt Inspection And Evals

- [ ] Scheduled first-useful slice ([requirements](sprints/prompt-inspection-and-evals.md)).
- Evidence: [`backend/src/grimoire/context/model.py`](../../backend/src/grimoire/context/model.py); [`backend/src/grimoire/app/inference.py`](../../backend/src/grimoire/app/inference.py); [`backend/tests/test_app_inference.py`](../../backend/tests/test_app_inference.py)::test_stages_say_which_optional_calls_the_app_can_play_and_why_the_rest_are_unavailable.
- Scope / gap: ContextPack is explicitly disposable; InferenceApi offers current context but marks prompt_history unavailable because no service keeps prompts. Thus historical selected/omitted evidence cannot be inspected after world changes.

<a id="slice-094"></a>
### 094 — Provenance And Impact

- [ ] Scheduled first-useful slice ([requirements](sprints/provenance-and-impact.md)).
- Evidence: [`backend/src/grimoire/sourceapi/api.py`](../../backend/src/grimoire/sourceapi/api.py)::_read; [`backend/src/grimoire/sourceapi/api.py`](../../backend/src/grimoire/sourceapi/api.py)::_linked_chats; [`backend/tests/test_sourceapi.py`](../../backend/tests/test_sourceapi.py).
- Scope / gap: Inherited-field provenance and directly linked chats exist. Missing roadmap first-proof impact analysis showing downstream campaigns that inherit a changed record versus overrides; direct by_source lookup is not transitive source-change impact.

<a id="slice-095"></a>
### 095 — Economy And Crafting

- [x] Scheduled first-useful slice ([requirements](sprints/economy-and-crafting.md)).
- Evidence: [`backend/src/grimoire/holdingstore/library.py`](../../backend/src/grimoire/holdingstore/library.py)::HoldingsLibrary; [`backend/src/grimoire/holdingstore/library.py`](../../backend/src/grimoire/holdingstore/library.py)::HoldingsChange; [`backend/src/grimoire/holdings/plan.py`](../../backend/src/grimoire/holdings/plan.py); [`backend/tests/test_holdingstore_scenarios.py`](../../backend/tests/test_holdingstore_scenarios.py)::test_craft_and_trade_are_atomic; [`backend/tests/test_holdingstore_scenarios.py`](../../backend/tests/test_holdingstore_scenarios.py)::test_proving_scenario_survives_reopen.
- Scope / gap: Bounded headless first proof is implemented through 023 counted holdings: previewable atomic two-resource barter and consume/add craft with idempotent operation IDs and insufficient-resource refusal. No economy-specific recipe/pricing/market model, unique-item manufacture or trade UI; later milestones and Wave 10 UI exit remain open.

<a id="slice-096"></a>
### 096 — Image Generation

- [ ] Scheduled first-useful slice ([requirements](sprints/image-generation.md)).
- Evidence: [`backend/src/grimoire/mediaapi/api.py`](../../backend/src/grimoire/mediaapi/api.py); [`backend/src/grimoire/app/adapters/media.py`](../../backend/src/grimoire/app/adapters/media.py); [`backend/tests/test_mediaapi.py`](../../backend/tests/test_mediaapi.py).
- Scope / gap: Media API uploads/places existing image bytes; no selected image-provider job, cost/cancel flow or generated-portrait approval proof.

<a id="slice-097"></a>
### 097 — Maps And Exploration

- [ ] Scheduled first-useful slice ([requirements](sprints/maps-and-exploration.md)).
- Evidence: [`backend/src/grimoire/staging`](../../backend/src/grimoire/staging); [`backend/src/grimoire/app/static/pages/campaign`](../../backend/src/grimoire/app/static/pages/campaign); [`backend/src/grimoire/app/pages.py`](../../backend/src/grimoire/app/pages.py).
- Scope / gap: Local stage/relations and campaign state display exist; no four-node graph map renderer, manual coordinate/link editing or map-navigation proof.

<a id="slice-098"></a>
### 098 — Publishing And Anonymization

- [ ] Scheduled first-useful slice ([requirements](sprints/publishing-and-anonymization.md)).
- Evidence: [`backend/src/grimoire/transcripts`](../../backend/src/grimoire/transcripts); [`backend/src/grimoire/archaeologystore`](../../backend/src/grimoire/archaeologystore); [`backend/src/grimoire/app/adapters/play_story.py`](../../backend/src/grimoire/app/adapters/play_story.py).
- Scope / gap: Stored/imported transcripts and story timeline exist; no non-mutating EPUB writer or stable anonymized publication export.

<a id="slice-099"></a>
### 099 — Soundscapes And Audio Cues

- [ ] Scheduled first-useful slice ([requirements](sprints/soundscapes-and-audio-cues.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/chat/page.js`](../../backend/src/grimoire/app/static/pages/chat/page.js); [`backend/src/grimoire/app/adapters/media.py`](../../backend/src/grimoire/app/adapters/media.py); [`backend/src/grimoire/voice/profile.py`](../../backend/src/grimoire/voice/profile.py).
- Scope / gap: No audio playback/ambience service or optional scene atmosphere/mute/cancellation proof. Existing voice package concerns written character voice, not sound.

<a id="slice-117"></a>
### 117 — Wave 10 UI Exit: Rich Interaction and Presentation

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-10-ui-exit.md)).
- Evidence: [`backend/src/grimoire/app/pages.py`](../../backend/src/grimoire/app/pages.py); [`backend/src/grimoire/app/static/pages/campaign/page.json`](../../backend/src/grimoire/app/static/pages/campaign/page.json); [`backend/src/grimoire/app/static/pages/art/page.json`](../../backend/src/grimoire/app/static/pages/art/page.json).
- Scope / gap: No Wave 10 encounter/map/pacing/growth/image-generation/publication UI first proof; current registered pages implement earlier UI slices. 095 primitives are headless, not this UI exit.

<a id="wave-11"></a>
## Wave 11

Scheduled domain slices and UI exit 118 are not complete.

<a id="slice-100"></a>
### 100 — Narrative Arcs

- [ ] Scheduled first-useful slice ([requirements](sprints/narrative-arcs.md)).
- Evidence: [`backend/src/grimoire/goals`](../../backend/src/grimoire/goals); [`backend/src/grimoire/obstacles`](../../backend/src/grimoire/obstacles); [`backend/src/grimoire/app/adapters/play_story.py`](../../backend/src/grimoire/app/adapters/play_story.py).
- Scope / gap: NPC goals, obstacles and a retrospective story timeline are not narrative-arc plans; no replan-after-rejected-event implementation/proof.

<a id="slice-101"></a>
### 101 — Code Quality Gate

- [ ] Scheduled first-useful slice ([requirements](sprints/code-quality-gate.md)).
- Evidence: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml); [`backend/src/grimoire/evals`](../../backend/src/grimoire/evals).
- Scope / gap: CI runs domain/compat tests; no report-only calibrated per-package B+ grader with pinned judge/tool identities, missing-judge handling and score evidence.

<a id="slice-102"></a>
### 102 — Collaborative Multiplayer

- [ ] Scheduled first-useful slice ([requirements](sprints/collaborative-multiplayer.md)).
- Evidence: [`backend/src/grimoire/campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py); [`backend/src/grimoire/textapi/api.py`](../../backend/src/grimoire/textapi/api.py); [`backend/src/grimoire/peersync`](../../backend/src/grimoire/peersync).
- Scope / gap: Sync preserves copies across devices, but app APIs explicitly act for one library owner who is both player and director. No authenticated two-controller co-play authority/turn-exchange proof.

<a id="slice-103"></a>
### 103 — Content Publication

- [ ] Scheduled first-useful slice ([requirements](sprints/content-publication.md)).
- Evidence: [`backend/src/grimoire/cards`](../../backend/src/grimoire/cards); [`backend/src/grimoire/import_v1`](../../backend/src/grimoire/import_v1); [`backend/src/grimoire/reviewapi/api.py`](../../backend/src/grimoire/reviewapi/api.py).
- Scope / gap: Card and V1 import exist; no portable world/catalog package exporter plus validated round-trip of package versions/dependencies/provenance.

<a id="slice-104"></a>
### 104 — Multi Speaker Table Audio

- [ ] Scheduled first-useful slice ([requirements](sprints/multi-speaker-table-audio.md)).
- Evidence: [`backend/src/grimoire/intent`](../../backend/src/grimoire/intent); [`backend/src/grimoire/comms/reader.py`](../../backend/src/grimoire/comms/reader.py); [`backend/src/grimoire/app/static/pages/chat/page.js`](../../backend/src/grimoire/app/static/pages/chat/page.js).
- Scope / gap: Text intent/channel routing is not microphone capture. No two named audio channels producing independently attributed PC drafts or speaker-bound confirmation.

<a id="slice-118"></a>
### 118 — Wave 11 UI Exit: Collaboration and Publication

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-11-ui-exit.md)).
- Evidence: [`backend/src/grimoire/app/pages.py`](../../backend/src/grimoire/app/pages.py); [`backend/src/grimoire/campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py).
- Scope / gap: No arc replanning, content-package export or two-controller collaboration UI proof.

<a id="wave-12"></a>
## Wave 12

Voice/immersion implementations and UI exit 119 are absent.

<a id="slice-105"></a>
### 105 — Live Voice Roleplay

- [ ] Scheduled first-useful slice ([requirements](sprints/live-voice-roleplay.md)).
- Evidence: [`backend/src/grimoire/voice/profile.py`](../../backend/src/grimoire/voice/profile.py); [`backend/src/grimoire/play/runner.py`](../../backend/src/grimoire/play/runner.py); [`backend/src/grimoire/app/static/pages/chat/page.js`](../../backend/src/grimoire/app/static/pages/chat/page.js).
- Scope / gap: No STT/TTS or duplex audio session joining spoken NPC output to committed text. Existing voice is text characterization.

<a id="slice-106"></a>
### 106 — Immersive World Interface

- [ ] Scheduled first-useful slice ([requirements](sprints/immersive-world-interface.md)).
- Evidence: [`backend/src/grimoire/staging`](../../backend/src/grimoire/staging); [`backend/src/grimoire/app/pages.py`](../../backend/src/grimoire/app/pages.py).
- Scope / gap: No explorable single-room spatial renderer/avatar interface; stage semantics alone do not provide spatial presentation.

<a id="slice-119"></a>
### 119 — Wave 12 UI Exit: Voice and Immersion

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-12-ui-exit.md)).
- Evidence: [`backend/src/grimoire/app/pages.py`](../../backend/src/grimoire/app/pages.py); [`backend/src/grimoire/app/static/pages/chat/page.js`](../../backend/src/grimoire/app/static/pages/chat/page.js).
- Scope / gap: No spoken exchange, mic consent/review, speech interruption recovery or spatial/text fallback proof.
