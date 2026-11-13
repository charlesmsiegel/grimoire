# Roadmap implementation audit

**Snapshot:** [`92144e17ccea819e5e760bccd77286d019b3fc9e`](https://github.com/charlesmsiegel/grimoire/commit/92144e17ccea819e5e760bccd77286d019b3fc9e), branch `v2`, audited 2026-11-13.

This is a code-and-test audit of the 119 scheduled **first-useful slices**, including inherited first-proof additions. Requirements were not narrowed to match the code. A check does not close later milestones, certify a live model, or prove physical-phone/browser accessibility. Unchecked entries distinguish missing implementation from a proof not yet established. The [ordered roadmap](README.md#implementation-checkmarks) carries the same checkmarks.

## Wave results

| Wave | Checked slices | Wave complete | Exit / remaining limit |
|---|---:|:---:|---|
| [1](#wave-1) | 8/8 | ☑ | All eight scoped foundation contracts and their integration proof are present; this is not playable-product certification. |
| [2](#wave-2) | 7/8 | ☐ | 015 has no live provider-reported V0.2.1 baseline capture. |
| [3](#wave-3) | 10/10 | ☑ | All ten scheduled headless first slices are present. Later subject-validation and other milestones remain open. |
| [4](#wave-4) | 9/10 | ☐ | 033 still rejects non-character actors in chat rosters, despite supporting creature scene casts. |
| [5](#wave-5) | 8/8 | ☑ | All eight scheduled first slices are present, including both persistent text-play paths. Later milestones are not implied. |
| [6](#wave-6) | 5/6 | ☐ | 047 lacks the independent prose-claim extraction required by its inherited first-proof addition. Physical-phone validation also remains unverified. |
| [7](#wave-7) | 6/7 | ☐ | 112 retains production UI integration gaps. 113 has a real CLI/Chromium follow-up proof. The full keyboard/screen-reader/touch acceptance gate is also unverified; HTTP/API proofs alone do not close it. |
| [8](#wave-8) | 3/24 | ☐ | 049, 054 and 066 have executable headless first-useful proofs at the boundaries below. Other domain slices and UI exit 115 remain incomplete or unverified. |
| [9](#wave-9) | 0/16 | ☐ | Scheduled domain slices and UI exit 116 are not complete. |
| [10](#wave-10) | 1/13 | ☐ | 095 meets the bounded headless trade/craft proof through counted holdings; the other domain slices and UI exit 117 remain incomplete. |
| [11](#wave-11) | 0/6 | ☐ | Scheduled domain slices and UI exit 118 are not complete. |
| [12](#wave-12) | 0/3 | ☐ | Voice/immersion implementations and UI exit 119 are absent. |

## Verification

Source and test bodies were inspected at the pinned commit. Targeted runs and the broader audit run are recorded below; no paid model calls, personal libraries, platform configuration changes or production-code edits were made.

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
- Scope / gap: No live provider-reported V0.2.1 measurement exists; baseline capture pending, so actual measured baseline/voice regression proof is missing.

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
- Scope / gap: Non-character reuse is deferred to 033.

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

033 still rejects non-character actors in chat rosters, despite supporting creature scene casts.

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

- [ ] Scheduled first-useful slice ([requirements](sprints/party-and-presence.md)).
- Evidence: [`backend/src/grimoire/partystore/library.py`](../../backend/src/grimoire/partystore/library.py); party; [`chatstore/library.py`](../../backend/src/grimoire/chatstore/library.py):785; [`backend/tests/test_partystore_scenarios.py`](../../backend/tests/test_partystore_scenarios.py); Party/reserve moves and scene casts preserve distinct creature references; boxed creatures cannot speak, release preserves memory.
- Scope / gap: Missing explicitly scheduled non-character chat cast support: backend/src/grimoire/chatstore/library.py:785–786 rejects actor.kind != character. Party/reserve and creature scene casts are implemented, but do not fully satisfy scheduled row.

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

047 lacks the independent prose-claim extraction required by its inherited first-proof addition. Physical-phone validation also remains unverified.

<a id="slice-044"></a>
### 044 — V2 Mobile Client

- [x] Scheduled first-useful slice ([requirements](sprints/v2-mobile-client.md)).
- Evidence: [`backend/src/grimoire/app`](../../backend/src/grimoire/app); [`backend/tests/test_textapi_proof.py`](../../backend/tests/test_textapi_proof.py); [`docs/MOBILE.md`](../../docs/MOBILE.md); [`backend/tests/test_app_release_proof.py`](../../backend/tests/test_app_release_proof.py).
- Scope / gap: Runnable responsive web/Termux client and paired loopback sync; physical-phone acceptance not verified, distinct from code completeness.

<a id="slice-052"></a>
### 052 — Instant Campaign Creation

- [x] Scheduled first-useful slice ([requirements](sprints/instant-campaign-creation.md)).
- Evidence: [`backend/src/grimoire/quickstart`](../../backend/src/grimoire/quickstart); [`backend/tests/test_quickstart.py`](../../backend/tests/test_quickstart.py).
- Scope / gap: Character/scenario/bundle/premise quick start and later card add, real start page wiring.

<a id="slice-045"></a>
### 045 — Decision Policies

- [x] Scheduled first-useful slice ([requirements](sprints/decision-policies.md)).
- Evidence: [`backend/src/grimoire/decisionstore`](../../backend/src/grimoire/decisionstore); [`backend/tests/test_decision_store.py`](../../backend/tests/test_decision_store.py).
- Scope / gap: Bounded optional capped decision with evidence and retry replay; default app integration is separately incomplete in 112.

<a id="slice-046"></a>
### 046 — Live Inheritance And Overrides

- [x] Scheduled first-useful slice ([requirements](sprints/live-inheritance-and-overrides.md)).
- Evidence: [`backend/src/grimoire/imagestore/artpin.py`](../../backend/src/grimoire/imagestore/artpin.py); [`backend/tests/test_art_pin.py`](../../backend/tests/test_art_pin.py); [`backend/src/grimoire/core/inheritance.py`](../../backend/src/grimoire/core/inheritance.py); [`backend/src/grimoire/effective/inspect.py`](../../backend/src/grimoire/effective/inspect.py); [`backend/tests/test_inheritance.py`](../../backend/tests/test_inheritance.py); [`backend/tests/test_mediaapi_lineage.py`](../../backend/tests/test_mediaapi_lineage.py).
- Scope / gap: Inheritance inspector/clear/reset/override plus derived and pinned art. Later per-instance field overrides and old actor snapshot migration excluded.

<a id="slice-047"></a>
### 047 — Narration Continuity Referee

- [ ] Scheduled first-useful slice ([requirements](sprints/narration-continuity-referee.md)).
- Evidence: [`backend/src/grimoire/referee/check.py`](../../backend/src/grimoire/referee/check.py); [`backend/tests/test_referee_scenarios.py`](../../backend/tests/test_referee_scenarios.py); [`docs/roadmap/sprints/narration-continuity-referee.md`](../../docs/roadmap/sprints/narration-continuity-referee.md); [`backend/src/grimoire/referee/__init__.py`](../../backend/src/grimoire/referee/__init__.py).
- Scope / gap: inherited first-proof addition requires independent non-narrator atomic-claim extraction from prose. Pure referee consumes supplied labelled claims only; no production prose-to-review integration.

<a id="slice-048"></a>
### 048 — Performance And Tracing

- [x] Scheduled first-useful slice ([requirements](sprints/performance-and-tracing.md)).
- Evidence: [`backend/src/grimoire/tracing`](../../backend/src/grimoire/tracing); [`backend/tests/test_scene_bench.py`](../../backend/tests/test_scene_bench.py).
- Scope / gap: Cold/warm end-to-end benchmark spans, cost/usage; synthetic model benchmark is not a live-provider latency claim.

<a id="wave-7"></a>
## Wave 7

112 retains production UI integration gaps. 113 now has a real CLI/Chromium follow-up proof. The full keyboard/screen-reader/touch acceptance gate is also unverified; HTTP/API proofs alone do not close it.

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

- [ ] Scheduled first-useful slice ([requirements](sprints/ui-inference-prompts-costs.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/providers/page.js`](../../backend/src/grimoire/app/static/pages/providers/page.js); [`backend/tests/test_app_inference_page.py`](../../backend/tests/test_app_inference_page.py); [`backend/src/grimoire/app/inference.py`](../../backend/src/grimoire/app/inference.py); [`backend/src/grimoire/generation/brief.py`](../../backend/src/grimoire/generation/brief.py); [`docs/roadmap/UI_COVERAGE.md`](../../docs/roadmap/UI_COVERAGE.md).
- Scope / gap: real endpoint/model/fallback/test/cost/latency UI, but no current authorized-context inspector despite 031 context pack and generation brief code; 045 optional decisions delivered but not app-wired. Historical prompt snapshots absent is legitimate later/conditional limitation; browser smoke separately unverified.

<a id="slice-113"></a>
### 113 — UI Campaign State and Canon Review

- [x] Scheduled first-useful slice ([requirements](sprints/ui-campaign-state-canon.md)); follow-up to the audit snapshot above.
- Evidence: [`app/campaign_config.py`](../../backend/src/grimoire/app/campaign_config.py) and [`app/campaign_services.py`](../../backend/src/grimoire/app/campaign_services.py) wire explicit startup stage/agenda and optional labeling configuration into CLI/chat/hub; [`test_app_campaign_config.py`](../../backend/tests/test_app_campaign_config.py) covers policy, authority, retries and usage; [`test_app_campaign_review.py`](../../backend/tests/test_app_campaign_review.py) covers stale turn and frame refusal; [`test_app_campaign_browser.py`](../../backend/tests/test_app_campaign_browser.py) runs actual CLI startup/restart and Chromium interactions over synthetic data.
- Scope / limits: browser proof includes keyboard cancellation, failed-label retry, stale second-tab review, separate player/director effect keys, actor-private evidence, absent-NPC exclusion, configured NPC choice, narrow viewport and exactly-once consequence after restart. 039 agendas and 041 stages remain authored startup configuration, not later persisted simulation/editors. Labeling has an explicit independent paid route; no effect follows automatically from narration. Full Wave 7 screen-reader/physical-phone acceptance remains unverified; this closes 113's scheduled first-useful slice only.

<a id="slice-114"></a>
### 114 — UI Media, Sync and Resilience

- [x] Scheduled first-useful slice ([requirements](sprints/ui-media-sync-resilience.md)).
- Evidence: [`backend/src/grimoire/app/static/pages/sync`](../../backend/src/grimoire/app/static/pages/sync); [`backend/tests/test_mediaapi.py`](../../backend/tests/test_mediaapi.py); [`backend/src/grimoire/app/static/pages/art`](../../backend/src/grimoire/app/static/pages/art); [`backend/src/grimoire/app/adapters/media.py`](../../backend/src/grimoire/app/adapters/media.py); [`backend/tests/test_app_release_proof.py`](../../backend/tests/test_app_release_proof.py).
- Scope / gap: Actual media placements/crop/reuse/hide plus pairing/sync/conflict/fork controls. Physical-phone keyboard/touch/screenreader acceptance unverified; no identified code gap in first slice.

<a id="wave-8"></a>
## Wave 8

049, 054 and 066 have executable headless first-useful proofs at the boundaries below. Other domain slices and UI exit 115 remain incomplete or unverified.

<a id="slice-049"></a>
### 049 — Offline Multi Device Sync

- [x] Scheduled first-useful slice ([requirements](sprints/offline-multi-device-sync.md)).
- Evidence: [`sync/semantic.py`](../../backend/src/grimoire/sync/semantic.py); [`test_sync_semantic.py`](../../backend/tests/test_sync_semantic.py); [`test_peersync_semantic.py`](../../backend/tests/test_peersync_semantic.py); [`test_continuity_forks.py`](../../backend/tests/test_continuity_forks.py). Actual compatible synthetic libraries detect relationship/event changes after a scene versus its offline edit, retain variants, block generic-resolution bypasses and stale/deletion races, and explicitly archive/restore compatible fiction before ordinary exactly-once ledger reconciliation. Interrupted resolution reopens and resumes under pinned premises.
- Scope / gap: Headless semantic reconciliation beyond earlier turn forks, documented in [CONTINUITY.md](../CONTINUITY.md). Uncertain ancestry and incompatible continuations refuse; remote fork controls and broad UI are 115. Long-period disconnected media/tombstone/retcon convergence remains later work.

<a id="slice-050"></a>
### 050 — Capture And Trainer Links

- [ ] Scheduled first-useful slice ([requirements](sprints/capture-and-trainer-links.md)).
- Evidence: [`test_effect_completion.py`](../../backend/tests/test_effect_completion.py) / [`test_effect_completion_recovery.py`](../../backend/tests/test_effect_completion_recovery.py) with capture_fixtures.py prove generic accepted-effect recovery, one ball spend and allied_with(existing TAM,WISP).
- Scope / gap: No production capture/trainer association workflow or rejection/full capture identity proof. grimoire.capture is external model usage capture, not creature capture.

<a id="slice-051"></a>
### 051 — Ensemble Conversation

- [ ] Scheduled first-useful slice ([requirements](sprints/ensemble-conversation.md)).
- Evidence: grimoire.runtime and grimoire.comms provide per-NPC generation and addressed channels.
- Scope / gap: No ensemble floor/interruption/silence scheduler for three voices.

<a id="slice-053"></a>
### 053 — Memory Consolidation

- [ ] Scheduled first-useful slice ([requirements](sprints/memory-consolidation.md)).
- Evidence: [`app/adapters/play_story.py`](../../backend/src/grimoire/app/adapters/play_story.py) derives last-time recap from the last four kept player-visible lines; [`campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py) explicitly marks long-term ledger unavailable.
- Scope / gap: No sourced consolidation across scenes and corrections.

<a id="slice-054"></a>
### 054 — Scenes Forks And Retcons

- [x] Scheduled first-useful slice ([requirements](sprints/scenes-forks-and-retcons.md)).
- Evidence: [`continuitystore`](../../backend/src/grimoire/continuitystore); [`test_continuitystore.py`](../../backend/tests/test_continuitystore.py); [`test_effect_continuity.py`](../../backend/tests/test_effect_continuity.py); [`test_continuity_forks.py`](../../backend/tests/test_continuity_forks.py). The transferred-key scenario previews downstream possession, actions, declared dialogue, summary and evidence impacts without changing originals. Native source pins and potential scene/event ordering distinguish recorded causality from uncertainty. Bounded authored rehearsals are speculative and model-free; retained forks preserve exact scene envelopes without replaying accepted effects.
- Scope / gap: Director-only headless previews, explicit compatible-source restoration and pinned correction exports for 053; no automatic downstream repair, prose inference, generated continuations, alternative promotion or 115 UI. Missing/stale/legacy inputs remain incomplete or refused rather than inferred. See [CONTINUITY.md](../CONTINUITY.md).

<a id="slice-055"></a>
### 055 — Calendar And Clocks

- [ ] Scheduled first-useful slice ([requirements](sprints/calendar-and-clocks.md)).
- Evidence: [`campaignapi/api.py`](../../backend/src/grimoire/campaignapi/api.py) marks clocks unavailable; [`migrate_v1/_layout.py`](../../backend/src/grimoire/migrate_v1/_layout.py) recognizes but does not import calendar data.
- Scope / gap: No campaign-local clock advancement or deadlines.

<a id="slice-056"></a>
### 056 — Catalog Composition

- [ ] Scheduled first-useful slice ([requirements](sprints/catalog-composition.md)).
- Evidence: chats and cardstore support direct qualified reusable character/lorebook bindings (107/037); rebase supports instance field updates.
- Scope / gap: No 056 optional catalog inclusion/exclusion, dependency composition or three-world shared-catalog proof. Direct bindings alone are earlier-slice functionality.

<a id="slice-057"></a>
### 057 — Faction Decision Making

- [ ] Scheduled first-useful slice ([requirements](sprints/faction-decision-making.md)).
- Evidence: [`goals/__init__.py`](../../backend/src/grimoire/goals/__init__.py) and [`goals/policy.py`](../../backend/src/grimoire/goals/policy.py) implement authored single-NPC agendas and deterministic choices; [`test_goals_policy.py`](../../backend/tests/test_goals_policy.py) exercises these.
- Scope / gap: No institution decision model resolving internal dissent.

<a id="slice-058"></a>
### 058 — Local Multi PC

- [ ] Scheduled first-useful slice ([requirements](sprints/local-multi-pc.md)).
- Evidence: [`textapi/api.py`](../../backend/src/grimoire/textapi/api.py) enforces a trusted library-owner player; [`test_textapi.py`](../../backend/tests/test_textapi.py)::test_only_the_owner_plays_in_this_slice verifies rejection of others.
- Scope / gap: No independent local PC controllers/drafts and combined accepted contribution.

<a id="slice-059"></a>
### 059 — Location Topology

- [ ] Scheduled first-useful slice ([requirements](sprints/location-topology.md)).
- Evidence: [`graph/model.py`](../../backend/src/grimoire/graph/model.py) provides located_in; [`staging/model.py`](../../backend/src/grimoire/staging/model.py) and [`staging/senses.py`](../../backend/src/grimoire/staging/senses.py) provide scene-local areas/ways/reach, tested by test_staging_*.py.
- Scope / gap: Scene staging explicitly is not stored world canon. No persistent location hierarchy/adjacency/travel topology over four stable world locations.

<a id="slice-060"></a>
### 060 — NPC Theory Of Mind

- [ ] Scheduled first-useful slice ([requirements](sprints/npc-theory-of-mind.md)).
- Evidence: goals policy consumes ActorKnowledge for the actor itself.
- Scope / gap: No hypotheses about another actor’s beliefs or mistaken-belief/second-order reasoning proof.

<a id="slice-061"></a>
### 061 — Private And Local Voice

- [ ] Scheduled first-useful slice ([requirements](sprints/private-and-local-voice.md)).
- Evidence: [`voice/__init__.py`](../../backend/src/grimoire/voice/__init__.py) and [`voice/profile.py`](../../backend/src/grimoire/voice/profile.py) are prose characterization, explicitly not TTS; intent provides speech-origin guards.
- Scope / gap: No selectable local/private STT/TTS providers, audio privacy controls or leakage proof.

<a id="slice-062"></a>
### 062 — Ruleset Action Contracts

- [ ] Scheduled first-useful slice ([requirements](sprints/ruleset-action-contracts.md)).
- Evidence: adjudication and obstacles implement fiction-first outcomes; existing entity facets do not implement rules-family adapters.
- Scope / gap: No separate D20/WoD/PbtA/Ars action contract implementations and examples.

<a id="slice-063"></a>
### 063 — Rumors And Information Flow

- [ ] Scheduled first-useful slice ([requirements](sprints/rumors-and-information-flow.md)).
- Evidence: comms and evidence support addressed communication and bounded actor knowledge.
- Scope / gap: No delayed/distorted rumor propagation records or scheduler.

<a id="slice-064"></a>
### 064 — Scenario Preparation

- [ ] Scheduled first-useful slice ([requirements](sprints/scenario-preparation.md)).
- Evidence: obstacles provide player-agency contracts and authored scene obstacles.
- Scope / gap: No grounded noncanonical situation-preparation generator producing reviewed stakes/directions.

<a id="slice-065"></a>
### 065 — Scene Workspace

- [ ] Scheduled first-useful slice ([requirements](sprints/scene-workspace.md)).
- Evidence: [`app/static/shell/drafts.js`](../../backend/src/grimoire/app/static/shell/drafts.js) persists drafts; [`chats/page.js`](../../backend/src/grimoire/app/static/pages/chats/page.js) and [`chats/model.js`](../../backend/src/grimoire/app/static/pages/chats/model.js) supply campaign/source/pin filters; [`app/adapters/shelf.py`](../../backend/src/grimoire/app/adapters/shelf.py) stores personal filter preferences.
- Scope / gap: Earlier 108/107 navigation exists, but 065 derived-portrayal navigation and mature efficient large-library workspace are absent; chats list is capped and rebuilt, docs/CHATS.md reserves equivalence/derived filtering for 065.

<a id="slice-066"></a>
### 066 — Type And API Boundaries

- [x] Scheduled first-useful slice ([requirements](sprints/type-and-api-boundaries.md)).
- Evidence: [`textapi/errors.py`](../../backend/src/grimoire/textapi/errors.py) has one typed ApiRefused carrying machine-readable reason and HTTP 400/404/409; [`docs/TEXT_API.md`](../../docs/TEXT_API.md) documents the frozen op/error contract. [`app/static/shell/api.js`](../../backend/src/grimoire/app/static/shell/api.js) defines ApiError preserving reason/status and distinguishing network/bad_response; [`shell/ui.js`](../../backend/src/grimoire/app/static/shell/ui.js) displays the distinct reason without losing drafts. [`test_textapi.py`](../../backend/tests/test_textapi.py) checks invalid payload 400, missing chat 404, stale edits 409 and write-free rejection. Targeted text API and paired offline-turn tests: 18 passed in 33.27s.
- Scope / gap: First useful isolated-boundary proof is satisfied by existing 044/108 implementations regardless sprint attribution; not a claim of repository-wide type migration or generated schemas.

<a id="slice-067"></a>
### 067 — Voice Accessibility And Testing

- [ ] Scheduled first-useful slice ([requirements](sprints/voice-accessibility-and-testing.md)).
- Evidence: [`test_intent_voice.py`](../../backend/tests/test_intent_voice.py) covers low ASR confidence/alternatives, ambiguous destructive commands and typed reentry.
- Scope / gap: No voice accessibility corpus, synthetic acoustic noise/simultaneous speech evaluation or false-command metrics. These are earlier 008 intent safety tests.

<a id="slice-068"></a>
### 068 — Actor Specific Inference

- [ ] Scheduled first-useful slice ([requirements](sprints/actor-specific-inference.md)).
- Evidence: [`evals/gate2.py`](../../backend/src/grimoire/evals/gate2.py) compares recorded optional-call captures; [`app/inference.py`](../../backend/src/grimoire/app/inference.py) explicitly marks planning/critique stages unimplemented; [`test_app_inference.py`](../../backend/tests/test_app_inference.py) asserts unavailability.
- Scope / gap: No baseline-versus-extra-planner/critic implemented experiment and actual quality/cost comparison.

<a id="slice-069"></a>
### 069 — Coding Skills Calibration

- [ ] Scheduled first-useful slice ([requirements](sprints/coding-skills-calibration.md)).
- Evidence: Engineering guidance and ordinary test suites exist.
- Scope / gap: No pinned independent defect/theory grader calibration corpus, repeatability results or false-positive analysis found.

<a id="slice-070"></a>
### 070 — Multimodal Player Input

- [ ] Scheduled first-useful slice ([requirements](sprints/multimodal-player-input.md)).
- Evidence: images/imagestore/mediaapi and UI media upload exist for stored media.
- Scope / gap: No sketch-region annotations carried as uncommitted player intent.

<a id="slice-071"></a>
### 071 — Theory Driven Simplification

- [ ] Scheduled first-useful slice ([requirements](sprints/theory-driven-simplification.md)).
- Evidence: Existing modules have contracts and tests.
- Scope / gap: No identified bounded simplification with theory comparison and measured change-rehearsal proof found; general modularity is not this deliverable.

<a id="slice-072"></a>
### 072 — VN Staging And Cues

- [ ] Scheduled first-useful slice ([requirements](sprints/vn-staging-and-cues.md)).
- Evidence: Existing UI scene reader and image slots render transcript/media.
- Scope / gap: No authored VN stage cue timeline, multi-character blocking/focus or cue transitions separated from transcript.

<a id="slice-115"></a>
### 115 — Wave 8 UI Exit: Reliable Campaign Play

- [ ] Scheduled first-useful slice ([requirements](sprints/wave-8-ui-exit.md)).
- Evidence: Existing UI offers owner branch choices and reports unavailable campaign features.
- Scope / gap: Required retcon, clocks, advanced party/story and other Wave 8 domain controls are not implemented; Wave 8 not complete.

<a id="wave-9"></a>
## Wave 9

Scheduled domain slices and UI exit 116 are not complete.

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
- Evidence: [`app/inference.py`](../../backend/src/grimoire/app/inference.py) explicitly returns prompt_preview unavailable; [`test_app_inference.py`](../../backend/tests/test_app_inference.py) asserts this.
- Scope / gap: No on-demand draft retrieval/context preview without generation/writes.

<a id="slice-086"></a>
### 086 — Mechanics Module Authoring

- [ ] Scheduled first-useful slice ([requirements](sprints/mechanics-module-authoring.md)).
- Evidence: Existing entity editor validates ordinary facets.
- Scope / gap: No mechanics-module sheet editor, family-specific schema validation or isolated recorded check authoring.

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
- Scope / gap: ContextPack is explicitly disposable; InferenceApi marks prompt_preview unavailable because no service keeps prompts. Thus historical selected/omitted evidence cannot be inspected after world changes.

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
