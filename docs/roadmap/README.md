# Grimoire V2 — ordered sprint roadmap

**Target branch:** `v2`. This is an implementation-priority plan for the new *headless* V2 core, not the older V1 `roadmapping` notebook. Every implementation PR branches from `v2`, targets **base `v2`** and follows [AGENTS.md](../../AGENTS.md). The V2 branch is not merged into V1 `main`.

**There are 106 active proposals: 90 retained from the earlier catalog and 16 newly defined V2 architectural/play/migration/sync proposals.** This table schedules only each proposal's *first useful slice*. The copied older proposals remain useful as ambition catalogs, but any claim that a V1 runtime/editor API is already available must be re-specified for V2 before implementation. See [Thematic catalog](CATALOG.md) and [V2 architecture](../ARCHITECTURE.md).

## Primary objective: migrate and play soon

1. **Keep data separate:** V2's desktop default library is `~/.grimoire-v2/` (environment: `GRIMOIRE_V2_HOME`). V1's `~/.grimoire/` is never an implicit V2 library or a writable migration source. Android defaults to its own private V2 data directory, and must support explicit local storage configuration.
2. **Build the ontology and storage first:** source-qualified definitions, composable entity facets, campaign instances, editable canonical records, typed relation graph, events and image object/placement semantics. The graph's search index is derived; canonical data remains human-editable Markdown/JSON.
3. **Migration is an early feature, not a polishing task:** [V1 migration skill](sprints/v1-to-v2-migration-skill.md) must include an **executable, read-only V0.2.1 inventory scanner with a deterministic loss/identity/art report**, not simply a written skill. [Import/apply](sprints/v1-to-v2-import-apply.md) translates actual worlds, actor versions, PCs, scenes, images, crop, galleries and expression/outfit lineage into an isolated V2 store with a reviewable mapping. Read [the migration skill](../../.agents/skills/migrate-v1-to-v2/SKILL.md).
4. **Preserve separately generated NPC voices:** default V2 calls an RP model **once for each speaking NPC contribution**, just as V0.2.1 does. Graph traversal and known/perceived/related evidence selection are deterministic and do not require an extra LLM call. Further planning, perception inference, character theory-of-mind, critique, image generation and autonomy jobs are **feature-flagged, metered, and optional**.
5. **Make play possible before large moonshots:** the [first text runner](sprints/minimal-text-scene-runner.md) must open an imported V1 scene, give an NPC a separate knowledge-scoped reply, save and resume. The [mobile client](sprints/v2-mobile-client.md) must support phone play and **actual desktop/phone sync**, not a placeholder concept.
6. **Synchronization is a product capability, not a vendor dependency:** [device revisions/conflicts](sprints/sync-revisions-and-conflict-core.md) are designed alongside native storage. [App-managed transfer](sprints/app-managed-device-sync.md) provides authenticated reachable-peer transport early. A local app **can** sync directly when peers can reach each other; when devices are not connected, off-LAN sync requires a user-provided reachable endpoint, relay or optional service. It does **not** require Syncthing. Conflicting offline writes remain preserved and reviewable.

## How order and parallelism work

**Ordered numbering is the recommended pickup sequence, not a serial dependency between rows in one wave.** Every sprint in a wave has **no hard prerequisite in its own wave**; each owns a separate primary package/API surface. Implementers may develop same-wave work concurrently against earlier frozen interfaces, but if shared mutations or API changes emerge, land the common contract first or move a sprint. The graph and relation schemas are central shared contracts, so migration, model runtime and sync should consume rather than redefine them. An earlier wave's unrelated unfinished sprint never blocks a later sprint whose *listed hard prerequisites* are already merged and verified.

Priorities: **P0** essential for migration/play/safety or known RP quality; **P1** major user value; **P2** optional growth; **P3** frontier research. The order does not constrain engineering ambition. **Cost is different:** expensive model calls during play are opt-in and bounded. Each first slice needs a proving scenario, an owner and tested failure behavior before integration.

## Fastest usable path

| Gate | Required result | Milestone |
|---|---|---|
| Foundation + inventory | V2 file root, IDs, generic revision-safe files, typed definitions/instances, and a **runnable read-only V0.2.1 inventory report** | End of Wave 2 |
| Data | Graph, accepted events, image objects and sync revision safety | End of Wave 3 |
| Migration and model | Reviewed V0.2.1 import with artwork; separate per-NPC generation; transport | End of Wave 4 |
| Desktop play | A genuine migrated text scene can be saved/reloaded | End of Wave 5 |
| Phone play | A phone and desktop exchange one accepted turn **and pictures** with explicit sync status | End of Wave 6 |

These are **functional gates**, not dates. Import and phone play cannot be described as supported merely because the corresponding docs exist.

## Waves at a glance

| Wave | Parallel sprints | Main outcome |
|---|---:|---|
| [1](#wave-1) | 8 | Foundational contracts, isolation and cost baselines |
| [2](#wave-2) | 8 | Native entities/files, provider/voice contracts and a runnable V1 inventory |
| [3](#wave-3) | 9 | Graph, artwork, events and device revision safety |
| [4](#wave-4) | 9 | V1 import, per-NPC generation, and peer transfer |
| [5](#wave-5) | 9 | First playable imported text campaign |
| [6](#wave-6) | 5 | Phone play, synchronization and early UX |
| [7](#wave-7) | 24 | Reliable long-term use and independent enhancements |
| [8](#wave-8) | 15 | Advanced mechanics, authoring and audio foundations |
| [9](#wave-9) | 12 | Richer interaction, simulation and presentation |
| [10](#wave-10) | 5 | Complex content, governance and publishing |
| [11](#wave-11) | 2 | Voice-native and long-horizon research |

## Full ordered backlog

The **number** is the recommended order for selecting the next PR. A same-wave group is deliberately parallel, not numbered because one waits for another. **Prerequisites** name required first-slice contracts in *previous* waves.

### Wave 1 — Foundational contracts, isolation and cost baselines

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 001 | [V2 Library Paths And Isolation](sprints/v2-library-paths-and-isolation.md) | **P0** | V2 baseline only | bootstrap / storage roots | Set ~/.grimoire-v2/ default and explicitly reject V1 write targets |
| 002 | [Source Qualified Identity](sprints/source-qualified-identity.md) | **P0** | V2 baseline only | source / qualified IDs | Pin cross-world/actor IDs and prevent same-name collisions |
| 003 | [Player Sovereignty](sprints/player-sovereignty.md) | **P0** | V2 baseline only | authority / PC authoring | Keep PC intention, voluntary dialogue and interiority under human control |
| 004 | [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | **P0** | V2 baseline only | inference / costs and flags | Require per-NPC-generation baseline and opt-in paid analysis stages |
| 005 | [Story Quality Benchmarks](sprints/story-quality-benchmarks.md) | **P0** | V2 baseline only | evals / RP story quality | Baseline distinct NPC voices, agency, perspective and total cost |
| 006 | [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | **P0** | V2 baseline only | testing / adversarial failures | Set up repeatable faults for native record and sync writes (first slice: harness + synthetic record and replica writers; native and sync writer scenarios move to 010/019) |
| 007 | [Safe Tool Execution](sprints/safe-tool-execution.md) | **P0** | V2 baseline only | security / authorization | Constrain imported content and external tools to inspect/propose scopes |
| 008 | [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | **P1** | V2 baseline only | voice / typed intent hypotheses | Prove UI command versus PC speech distinction on typed utterances |

#### Wave 1 status

All eight first slices are merged as **headless, pure contracts with executable tests**; none is an app-integrated feature. CI covers Ubuntu (3.11–3.13), macOS and Windows. Each sprint's status line says what its first proof demonstrates, and which parts are synthetic or policy-level only. The invariants and the tests proving them are in the [testing catalog](../TESTING.md#v2-domain-invariants).

Every requirement Wave 1 deferred is listed in its owning sprint's spec under **Inherited from Wave 1**, as an *obligation* or a *first-proof addition*. First-proof additions are reflected in the owning rows below, and the Wave 1 sprints that a later sprint inherits from are among that row's prerequisites.

Cross-sprint seams are pinned by `backend/tests/test_wave1_integration.py`. A merge gate on `v2`, required checks with up-to-date branches or a merge queue, is described in [the CI gate](../TESTING.md#ci-gate).

## Wave 2 entry gate, parallel interfaces and exit proof

The **Wave 1 repair** (PR #565) delivered the eight headless first-slice contracts and restored green CI on supported OSes. Its handoffs are incorporated in the sprint specs below. A later status update must not imply V2 already has a runtime, persistent graph or playable scene.

[**Wave 2 interface and acceptance contract**](WAVE2_HANDOFFS.md) is the required shared handoff for independent PRs 009–016. It allocates distinct owners, provides a common **facet-neutral record envelope** based on Wave 1 identity (009 and 010 remain parallel), defines 012's epistemic/evidence vocabulary for Wave 3, and separates 013's provider adapter from 014's voice prompts and 015's V0.2.1 measurements. 016 owns *typed preference and skip intent*, while 035 will execute it in text play.

**Before starting another parallel merge:** the latest `v2` head must have completed green domain + compatibility CI. The repository owner still needs to require these checks and up-to-date branch verification or a merge queue in GitHub settings; docs cannot configure branch protection. Until enabled, each PR must be tested against the **current integration head** before merging, not merely the head from when its feature branch was created. Never merge atop red `v2`.

**Wave 2 is not complete** until both (1) a generic, fault-tested, revision-safe V2 record store interoperates with typed entities/instances **without coupling their two owning packages**, and (2) the **real, runnable V0.2.1 read-only scanner** produces an idempotent synthetic-library inventory including image object/blob **versus placements**, scene/source identity mapping, warnings, and proof that it modified neither source nor destination. Also verify the 012 evidence filter, 013 planned-call provider/fake, 014 independent voices, 015 truthful measured-baseline provenance, and 016 typed boundary intents. See [Wave 2 exit checklist](WAVE2_HANDOFFS.md#wave-2-exit-gate-two-independent-proofs).

We still target the first **playable migrated desktop text campaign in Wave 5** and phone+desktop synchronization in Wave 6; these are capability gates, not calendar dates.

### Wave 2 — Native contracts, provider economics and executable V1 inventory

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 009 | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | **P0** | [Source Qualified Identity](sprints/source-qualified-identity.md), [Player Sovereignty](sprints/player-sovereignty.md), [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | core / entity facets | Prove composable typed entities, source/instance identity and control roles without depending on 010's file writer |
| 010 | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md) | **P0** | [V2 Library Paths And Isolation](sprints/v2-library-paths-and-isolation.md), [Source Qualified Identity](sprints/source-qualified-identity.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | storage / canonical records | Prove a facet-agnostic revisioned Markdown/JSON envelope, source heads/links, unknown-field fidelity, and crash-safe V1-isolated writes |
| 011 | [V1 To V2 Migration Skill](sprints/v1-to-v2-migration-skill.md) | **P0** | [V2 Library Paths And Isolation](sprints/v2-library-paths-and-isolation.md), [Source Qualified Identity](sprints/source-qualified-identity.md) | migration / source inventory | Run an executable read-only 0.2.1 inventory scanner producing a deterministic identity, scene, picture/placement and loss manifest |
| 012 | [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | **P0** | [Source Qualified Identity](sprints/source-qualified-identity.md) | knowledge / perspective policy | Define typed witness/channel/time/evidence/claim contract; demonstrate actor-visible false reports without promoting them to truth |
| 013 | [Model Routing And Budgeting](sprints/model-routing-and-budgeting.md) | **P0** | [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | inference / provider adapter | Port a working planned-call provider adapter with real usage booking, execute authorization and bounded fault-injecting fake |
| 014 | [Characterization And Dialogue](sprints/characterization-and-dialogue.md) | **P0** | [Story Quality Benchmarks](sprints/story-quality-benchmarks.md), [Player Sovereignty](sprints/player-sovereignty.md) | generation / voice anchors | Test portrayal-scoped voice anchors and distinctive separately addressed NPC prompt inputs without a provider dependency |
| 015 | [V1 Cost And Voice Regression](sprints/v1-cost-and-voice-regression.md) | **P0** | [Story Quality Benchmarks](sprints/story-quality-benchmarks.md), [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | evals / cost parity | Capture provider-reported 0.2.1 per-NPC voice/cost baseline (opt-in) and reconcile story-quality/2 with inference ledger |
| 016 | [Session Zero Boundaries](sprints/session-zero-boundaries.md) | **P0** | [Player Sovereignty](sprints/player-sovereignty.md) | campaign / creative boundaries | Define typed in-memory session-zero preferences and pause/skip/redirect intents; actual play execution belongs to 035 |

### Wave 3 — Graph, artwork, events and device revision safety

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 017 | [Typed Relationship Graph](sprints/typed-relationship-graph.md) | **P0** | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md), [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | graph / relations and traversal | Traverse typed relations while respecting actor evidence access, time and scope |
| 018 | [Image Identity And Placements](sprints/image-identity-and-placements.md) | **P0** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | media / image identities | Preserve shared image blobs, per-record placements and version crop |
| 019 | [Sync Revisions And Conflict Core](sprints/sync-revisions-and-conflict-core.md) | **P0** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | sync / revision protocol | Preserve concurrent edits and detect conflict rather than overwrite, proven under the 006 fault harness |
| 020 | [Durable Event Evidence](sprints/durable-event-evidence.md) | **P0** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | events / evidence records | Persist private events, confirmed perceptions and contested reports using 012's evidence contract |
| 021 | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md) | **P0** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Player Sovereignty](sprints/player-sovereignty.md) | scene / accepted effects | Define accepted fictional events and one-time state publication |
| 022 | [Effective World Snapshots](sprints/effective-world-snapshots.md) | **P0** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Source Qualified Identity](sprints/source-qualified-identity.md), [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | world / effective reads | Read revision-consistent source and campaign views over the generic envelope and typed instances |
| 023 | [Counted Inventory](sprints/counted-inventory.md) | **P1** | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md), [Native File Store And Revisions](sprints/native-file-store-and-revisions.md) | inventory / holdings | Persist and update a few counted objects with stable instance IDs |
| 024 | [Creature Individualization](sprints/creature-individualization.md) | **P1** | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md), [Source Qualified Identity](sprints/source-qualified-identity.md) | actors / template instances | Instantiate one individually named creature from a species |
| 025 | [Fictional Abilities](sprints/fictional-abilities.md) | **P1** | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | world / power constraints | Represent what a powers-capable entity may and may not do |

### Wave 4 — V1 import, per-NPC generation, and peer transfer

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 026 | [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md) | **P0** | [V1 To V2 Migration Skill](sprints/v1-to-v2-migration-skill.md), [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md), [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Image Identity And Placements](sprints/image-identity-and-placements.md), [Safe Tool Execution](sprints/safe-tool-execution.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Durable Event Evidence](sprints/durable-event-evidence.md), [Effective World Snapshots](sprints/effective-world-snapshots.md), [Session Zero Boundaries](sprints/session-zero-boundaries.md) | migration / guarded apply | Apply a reviewed 011 inventory: import actor instances, a scene, actual image placements and evidence into isolated V2 |
| 027 | [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md) | **P0** | [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Model Routing And Budgeting](sprints/model-routing-and-budgeting.md), [Characterization And Dialogue](sprints/characterization-and-dialogue.md), [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md), [V1 Cost And Voice Regression](sprints/v1-cost-and-voice-regression.md) | runtime / actor generation | Generate independent, authorized, knowledge-scoped NPC responses against the measured V1 voice/cost baseline |
| 028 | [App Managed Device Sync](sprints/app-managed-device-sync.md) | **P0** | [Sync Revisions And Conflict Core](sprints/sync-revisions-and-conflict-core.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | sync / transport and pairing | Transfer an authenticated scene and image between two local peers |
| 029 | [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | **P0** | [Fictional Abilities](sprints/fictional-abilities.md), [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Player Sovereignty](sprints/player-sovereignty.md) | adjudication / freeform outcome | Resolve a risky mechanics-free action without narrator fiat |
| 030 | [Durable Action Recovery](sprints/durable-action-recovery.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Durable Event Evidence](sprints/durable-event-evidence.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | storage / atomic action recovery | Replay or abort a partial accepted event exactly once |
| 031 | [Context Retrieval](sprints/context-retrieval.md) | **P0** | [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md), [Effective World Snapshots](sprints/effective-world-snapshots.md) | inference / actor context packing | Pack deterministic, evidence-safe graph-selected actor context without triggering extra model calls |
| 032 | [Diegetic Communication](sprints/diegetic-communication.md) | **P1** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Durable Event Evidence](sprints/durable-event-evidence.md) | fiction / private communication | Deliver a private message only to its valid recipients |
| 033 | [Party And Presence](sprints/party-and-presence.md) | **P1** | [Creature Individualization](sprints/creature-individualization.md) | actors / party state | Separate present cast, controlled actors and reserves |
| 034 | [Unique Equipment](sprints/unique-equipment.md) | **P1** | [Counted Inventory](sprints/counted-inventory.md) | inventory / unique possessions | Keep two similar artifacts distinct by owner and revision |

### Wave 5 — First playable imported text campaign

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 035 | [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md) | **P0** | [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md), [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md), [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Fiction First Adjudication](sprints/fiction-first-adjudication.md), [Durable Event Evidence](sprints/durable-event-evidence.md), [Context Retrieval](sprints/context-retrieval.md), [Safe Tool Execution](sprints/safe-tool-execution.md), [Spoken Command And Intent](sprints/spoken-command-and-intent.md), [Session Zero Boundaries](sprints/session-zero-boundaries.md) | runtime / text scene loop | Play, skip/redirect, save and resume three turns with an imported 0.2.1 NPC while preserving accepted outcomes |
| 036 | [Campaign Archaeology](sprints/campaign-archaeology.md) | **P1** | [V1 To V2 Migration Skill](sprints/v1-to-v2-migration-skill.md), [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md) | migration / scene archaeology | Preview exact source scene segmentation and uncertain identities |
| 037 | [Character Card Ingestion](sprints/character-card-ingestion.md) | **P1** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | interop / card formats | Port V3/V2/CHARX cards as reviewed interchange, not the store |
| 038 | [Expression And Art Lineage](sprints/expression-and-art-lineage.md) | **P1** | [Image Identity And Placements](sprints/image-identity-and-placements.md), [Effective World Snapshots](sprints/effective-world-snapshots.md), [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md) | art / outfits and inheritance | Preserve imported outfit/expression provenance and per-instance image overrides |
| 039 | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md) | **P1** | [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | npc / goals and initiative | Allow one NPC to pursue a credible goal with no extra default generation |
| 040 | [Obstacles And Agency](sprints/obstacles-and-agency.md) | **P1** | [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | narrative / open obstacles | Support an unconventional solution without denying player choice |
| 041 | [Scene Physical Continuity](sprints/scene-physical-continuity.md) | **P1** | [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Durable Event Evidence](sprints/durable-event-evidence.md) | scene / local positions and sight | Track who left the room and what they still can reach |
| 042 | [Source Rebase And Semantic Merge](sprints/source-rebase-and-semantic-merge.md) | **P1** | [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Effective World Snapshots](sprints/effective-world-snapshots.md) | sources / reviewed rebase | Update one root fact while two campaigns retain independent memories |
| 043 | [World Change Sets](sprints/world-change-sets.md) | **P1** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md), [Typed Relationship Graph](sprints/typed-relationship-graph.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | world / reviewed batch changes | Preview an atomic two-record world change |

### Wave 6 — Phone play, synchronization and early UX

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 044 | [V2 Mobile Client](sprints/v2-mobile-client.md) | **P0** | [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md), [App Managed Device Sync](sprints/app-managed-device-sync.md) | mobile / first playable client | Play on phone, sync an accepted turn plus picture, resume on desktop |
| 045 | [Decision Policies](sprints/decision-policies.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | inference / optional decisions | Record a bounded NPC decision only if the mode permits the extra call |
| 046 | [Live Inheritance And Overrides](sprints/live-inheritance-and-overrides.md) | **P1** | [Source Rebase And Semantic Merge](sprints/source-rebase-and-semantic-merge.md) | world / field inheritance | Preserve a local portrait override across an accepted root update |
| 047 | [Narration Continuity Referee](sprints/narration-continuity-referee.md) | **P1** | [Scene Physical Continuity](sprints/scene-physical-continuity.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | generation / contradiction review | Check five continuity errors plus a separately labelled PC-authorship claim before a narration can become canonical |
| 048 | [Performance And Tracing](sprints/performance-and-tracing.md) | **P1** | [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md), [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | runtime / tracing | Measure cold and warm end-to-end cost and latency |

### Wave 7 — Reliable long-term use and independent enhancements

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 049 | [Offline Multi Device Sync](sprints/offline-multi-device-sync.md) | **P0** | [V2 Mobile Client](sprints/v2-mobile-client.md), [App Managed Device Sync](sprints/app-managed-device-sync.md), [Sync Revisions And Conflict Core](sprints/sync-revisions-and-conflict-core.md) | sync / offline branch reconciliation | Preserve two conflicting offline edits without silent loss |
| 050 | [Capture And Trainer Links](sprints/capture-and-trainer-links.md) | **P0** | [Counted Inventory](sprints/counted-inventory.md), [Creature Individualization](sprints/creature-individualization.md), [Party And Presence](sprints/party-and-presence.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / capture | Catch a named monster with one ball and no duplicate actor |
| 051 | [Ensemble Conversation](sprints/ensemble-conversation.md) | **P0** | [Diegetic Communication](sprints/diegetic-communication.md), [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Decision Policies](sprints/decision-policies.md) | generation / conversation floor | Handle three voices, interruption and meaningful silence |
| 052 | [Instant Campaign Creation](sprints/instant-campaign-creation.md) | **P0** | [Session Zero Boundaries](sprints/session-zero-boundaries.md) | frontend / campaign onboarding | Create a playable scene from one sentence |
| 053 | [Memory Consolidation](sprints/memory-consolidation.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | memory / long-horizon ledger | Maintain sourced facts across corrected scenes |
| 054 | [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | scene / fork and revision | Preview a fork without duplicating effects |
| 055 | [Calendar And Clocks](sprints/calendar-and-clocks.md) | **P1** | [Native File Store And Revisions](sprints/native-file-store-and-revisions.md) | campaign / clocks | Advance an independent campaign-local clock |
| 056 | [Catalog Composition](sprints/catalog-composition.md) | **P1** | [Effective World Snapshots](sprints/effective-world-snapshots.md) | world / catalog inclusion | Use a shared bestiary without copying its full contents |
| 057 | [Faction Decision Making](sprints/faction-decision-making.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | society / institutions | Choose an institution's action despite internal dissent |
| 058 | [Local Multi PC](sprints/local-multi-pc.md) | **P1** | [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md), [Player Sovereignty](sprints/player-sovereignty.md), [Safe Tool Execution](sprints/safe-tool-execution.md), [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | gameplay / local controllers | Allow two PC contributions under explicit independent control |
| 059 | [Location Topology](sprints/location-topology.md) | **P1** | [Typed Relationship Graph](sprints/typed-relationship-graph.md) | world / spatial topology | Prove location containment and traversable adjacency |
| 060 | [NPC Theory Of Mind](sprints/npc-theory-of-mind.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | NPC / belief reasoning | Reason about another NPC's mistaken belief |
| 061 | [Private And Local Voice](sprints/private-and-local-voice.md) | **P1** | [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | voice / local private providers | Support explicit private voice provider selection without model leakage |
| 062 | [Ruleset Action Contracts](sprints/ruleset-action-contracts.md) | **P1** | [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | mechanics / family contracts | Define separate D20, WoD, PbtA and Ars interfaces |
| 063 | [Rumors And Information Flow](sprints/rumors-and-information-flow.md) | **P1** | [Diegetic Communication](sprints/diegetic-communication.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | society / rumors | Spread one delayed and distorted report |
| 064 | [Scenario Preparation](sprints/scenario-preparation.md) | **P1** | [Obstacles And Agency](sprints/obstacles-and-agency.md) | narrative / scene prep | Draft a grounded situation without canonicalizing it |
| 065 | [Scene Workspace](sprints/scene-workspace.md) | **P1** | [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md), [V2 Mobile Client](sprints/v2-mobile-client.md) | frontend / scene reader | Improve typed/mobile scene navigation without dropping drafts |
| 066 | [Type And API Boundaries](sprints/type-and-api-boundaries.md) | **P1** | [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | API / isolated error contract | Improve one frozen API boundary outside other wave owners |
| 067 | [Voice Accessibility And Testing](sprints/voice-accessibility-and-testing.md) | **P1** | [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | evals / speech safety | Run false-command/noise tests without assuming always-on microphone |
| 068 | [Actor Specific Inference](sprints/actor-specific-inference.md) | **P2** | [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md), [V1 Cost And Voice Regression](sprints/v1-cost-and-voice-regression.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | evals / optional planning calls | Compare baseline per-NPC generation to optional extra planning/critic calls |
| 069 | [Coding Skills Calibration](sprints/coding-skills-calibration.md) | **P2** | V2 baseline only | engineering / external grader | Calibrate an independent defect and theory judge |
| 070 | [Multimodal Player Input](sprints/multimodal-player-input.md) | **P2** | [Scene Physical Continuity](sprints/scene-physical-continuity.md) | frontend / image reference input | Annotate a sketch as uncommitted player intent |
| 071 | [Theory Driven Simplification](sprints/theory-driven-simplification.md) | **P2** | V2 baseline only | engineering / isolated modules | Simplify one bounded V2 module without altering shared contracts |
| 072 | [VN Staging And Cues](sprints/vn-staging-and-cues.md) | **P2** | [Expression And Art Lineage](sprints/expression-and-art-lineage.md) | frontend / VN stage | Render authored stage cues without changing transcript |

### Wave 8 — Advanced mechanics, authoring and audio foundations

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 073 | [Auxiliary Writing Tools](sprints/auxiliary-writing-tools.md) | **P1** | [Player Sovereignty](sprints/player-sovereignty.md), [Scene Workspace](sprints/scene-workspace.md) | frontend / draft helpers | Generate a hypothetical non-canonical reply |
| 074 | [Downtime And Long Activities](sprints/downtime-and-long-activities.md) | **P1** | [Calendar And Clocks](sprints/calendar-and-clocks.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / downtime | Complete a 20-day study and advance time once |
| 075 | [Mechanical Resolution](sprints/mechanical-resolution.md) | **P1** | [Ruleset Action Contracts](sprints/ruleset-action-contracts.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | mechanics / resolution | Commit a recorded roll without narration re-rolling it |
| 076 | [Offscreen World Events](sprints/offscreen-world-events.md) | **P1** | [Calendar And Clocks](sprints/calendar-and-clocks.md), [Faction Decision Making](sprints/faction-decision-making.md), [Decision Policies](sprints/decision-policies.md) | simulation / offscreen scheduler | Advance one faction's plan during player absence |
| 077 | [Quests And Commitments](sprints/quests-and-commitments.md) | **P1** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Calendar And Clocks](sprints/calendar-and-clocks.md) | campaign / commitments | Track an accepted promise and deadline |
| 078 | [Relationships And Reputation](sprints/relationships-and-reputation.md) | **P1** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md) | social / relationship records | Update trust without giving absent NPCs global knowledge |
| 079 | [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md) | **P1** | [Source Rebase And Semantic Merge](sprints/source-rebase-and-semantic-merge.md), [Catalog Composition](sprints/catalog-composition.md) | world / source upgrades | Preview removing or updating a source without mistaken ID matching |
| 080 | [Speech To Draft](sprints/speech-to-draft.md) | **P1** | [Spoken Command And Intent](sprints/spoken-command-and-intent.md), [Private And Local Voice](sprints/private-and-local-voice.md), [V2 Mobile Client](sprints/v2-mobile-client.md) | voice / speech input | Dictate a draft without submitting a fictional action |
| 081 | [Story Graph And Navigation](sprints/story-graph-and-navigation.md) | **P1** | [Memory Consolidation](sprints/memory-consolidation.md), [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md) | frontend / story explorer | Find an old promise and its eventual outcome |
| 082 | [Agentic Investigations](sprints/agentic-investigations.md) | **P2** | [World Change Sets](sprints/world-change-sets.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | agents / evidence jobs | Review a bounded read-only world audit |
| 083 | [Character Voice Performance](sprints/character-voice-performance.md) | **P2** | [Private And Local Voice](sprints/private-and-local-voice.md), [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md) | voice / playback | Render separate voice performance from accepted actor text |
| 084 | [Cross Campaign Scene Transfer](sprints/cross-campaign-scene-transfer.md) | **P2** | [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md), [Source Qualified Identity](sprints/source-qualified-identity.md) | campaign / transfer | Copy a transcript with reviewed identity mapping |
| 085 | [Live Context Preview](sprints/live-context-preview.md) | **P2** | [Context Retrieval](sprints/context-retrieval.md) | frontend / prompt preview | Preview likely retrieval without paying for a turn |
| 086 | [Mechanics Module Authoring](sprints/mechanics-module-authoring.md) | **P2** | [Ruleset Action Contracts](sprints/ruleset-action-contracts.md) | mechanics / module editor | Author an isolated validated mechanics sheet |
| 087 | [World Authoring Assistant](sprints/world-authoring-assistant.md) | **P2** | [World Change Sets](sprints/world-change-sets.md), [Catalog Composition](sprints/catalog-composition.md) | authoring / world proposals | Review three linked towns and accept two |

### Wave 9 — Richer interaction, simulation and presentation

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 088 | [Campaign Tuning](sprints/campaign-tuning.md) | **P1** | [Decision Policies](sprints/decision-policies.md), [Obstacles And Agency](sprints/obstacles-and-agency.md) | campaign / pacing policies | Demonstrate a player-visible pacing control and its effect |
| 089 | [Character Growth](sprints/character-growth.md) | **P1** | [Creature Individualization](sprints/creature-individualization.md), [Party And Presence](sprints/party-and-presence.md) | actors / advancement | Advance individual abilities without changing species template |
| 090 | [Combat Encounter Orchestration](sprints/combat-encounter-orchestration.md) | **P1** | [Mechanical Resolution](sprints/mechanical-resolution.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Scene Physical Continuity](sprints/scene-physical-continuity.md) | gameplay / combat session | Resolve two multi-actor rounds under one ruleset |
| 091 | [Conversation Turn Taking](sprints/conversation-turn-taking.md) | **P1** | [Speech To Draft](sprints/speech-to-draft.md), [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | voice / interruption | Recognize interruption and draft acceptance safely |
| 092 | [Fair Mysteries And Deduction](sprints/fair-mysteries-and-deduction.md) | **P1** | [Obstacles And Agency](sprints/obstacles-and-agency.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Narration Continuity Referee](sprints/narration-continuity-referee.md) | stories / mysteries | Prove one fixed-answer mystery supports novel investigation |
| 093 | [Prompt Inspection And Evals](sprints/prompt-inspection-and-evals.md) | **P1** | [Context Retrieval](sprints/context-retrieval.md), [Story Quality Benchmarks](sprints/story-quality-benchmarks.md), [Memory Consolidation](sprints/memory-consolidation.md) | evaluation / prompt provenance | Explain selected and omitted historical evidence |
| 094 | [Provenance And Impact](sprints/provenance-and-impact.md) | **P1** | [World Change Sets](sprints/world-change-sets.md), [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md) | frontend / source impact | Show which campaigns inherit a changed record |
| 095 | [Economy And Crafting](sprints/economy-and-crafting.md) | **P2** | [Unique Equipment](sprints/unique-equipment.md), [Counted Inventory](sprints/counted-inventory.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / trade and craft | Trade/craft an item without duplicate effects |
| 096 | [Image Generation](sprints/image-generation.md) | **P2** | [Image Identity And Placements](sprints/image-identity-and-placements.md) | media / image creation | Generate an approved portrait without rewriting authored subject IDs |
| 097 | [Maps And Exploration](sprints/maps-and-exploration.md) | **P2** | [Location Topology](sprints/location-topology.md) | frontend / map views | Show and edit a four-node map without required coordinates |
| 098 | [Publishing And Anonymization](sprints/publishing-and-anonymization.md) | **P2** | [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md), [Memory Consolidation](sprints/memory-consolidation.md) | export / story publication | Produce a non-mutating anonymized EPUB |
| 099 | [Soundscapes And Audio Cues](sprints/soundscapes-and-audio-cues.md) | **P2** | [V2 Mobile Client](sprints/v2-mobile-client.md) | audio / ambience | Optional non-authoritative ambient audio in phone client |

### Wave 10 — Complex content, governance and publishing

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 100 | [Narrative Arcs](sprints/narrative-arcs.md) | **P1** | [Quests And Commitments](sprints/quests-and-commitments.md), [Scenario Preparation](sprints/scenario-preparation.md), [Story Graph And Navigation](sprints/story-graph-and-navigation.md) | stories / long arcs | Replan an arc after the player rejects a key event |
| 101 | [Code Quality Gate](sprints/code-quality-gate.md) | **P2** | [Coding Skills Calibration](sprints/coding-skills-calibration.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md), [Theory Driven Simplification](sprints/theory-driven-simplification.md) | CI / calibration gate | Run report-only calibrated quality checks before blocking |
| 102 | [Collaborative Multiplayer](sprints/collaborative-multiplayer.md) | **P2** | [Local Multi PC](sprints/local-multi-pc.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | network / co-play | Coordinate two controllers without unauthorized state changes |
| 103 | [Content Publication](sprints/content-publication.md) | **P2** | [Catalog Composition](sprints/catalog-composition.md), [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md), [Character Card Ingestion](sprints/character-card-ingestion.md) | sharing / content manifests | Round-trip one versioned world/catalog package |
| 104 | [Multi Speaker Table Audio](sprints/multi-speaker-table-audio.md) | **P2** | [Local Multi PC](sprints/local-multi-pc.md), [Conversation Turn Taking](sprints/conversation-turn-taking.md) | voice / multiuser capture | Attribute two known audio channels to separate PCs |

### Wave 11 — Voice-native and long-horizon research

| # | Sprint | Priority | Required earlier sprints | Primary owner | First useful proof |
|---:|---|---|---|---|---|
| 105 | [Live Voice Roleplay](sprints/live-voice-roleplay.md) | **P1** | [Conversation Turn Taking](sprints/conversation-turn-taking.md), [Character Voice Performance](sprints/character-voice-performance.md), [Diegetic Communication](sprints/diegetic-communication.md), [V2 Mobile Client](sprints/v2-mobile-client.md) | voice / duplex RP | Run a voice session with committed text aligned to a spoken NPC |
| 106 | [Immersive World Interface](sprints/immersive-world-interface.md) | **P3** | [Maps And Exploration](sprints/maps-and-exploration.md), [VN Staging And Cues](sprints/vn-staging-and-cues.md) | frontend / spatial view | Explore one room visually with scene truth unchanged |

## Cost and voice acceptance gates

- **Separate responses are intentional:** a turn containing two speaking NPCs must produce two distinct actor-scoped RP generations, even if four NPCs are present; no automatic shared-narrator compression.
- **Zero extra inference by default:** graph traversals, routine observation propagation, image placement resolution and ordinary actor-context assembly must use files/indexes/rules, not per-entity background LLM calls.
- **Known charged work is visible:** count actual prompt/completion/cached tokens, embedding and decision calls, retries/fallbacks, provider cost, per-turn/session totals and optional maintenance jobs. A disabled feature must issue no hidden model calls.
- **Baseline against V0.2.1:** compare same model and synthetic multi-NPC scenario at similar target output lengths for billed cost, voice fidelity, correct perspective and latency. The target for normal play is cost parity or better, with improvements documented rather than assumed.
- **User controls:** named feature flags and per-turn/session spend ceilings, with visible fallback and no fabricated state commitments when a budget is exhausted.

## Sync and migration acceptance gates

- **Independent homes:** V1's configured home and V2's home are distinct; migration reads only a frozen snapshot and never auto-points V2 at V1 data. Preserve a complete original source and unknown fields for audits.
- **Identity and art fidelity:** import maps source identities, campaign instances, original V3/PC version metadata, group links, image-object IDs, blob hashes, avatar/gallery/focus, expression outfits, hidden/inherited overrides and scene/greeting art with evidence and explicit warnings.
- **Idempotent and resumable:** second import does not duplicate actors, events or image objects; crash recovery and re-runs do not overwrite local V2 edits. No silent unverified LLM reconstruction.
- **Two-device test:** create one scene on desktop, sync to phone, play while offline, reconnect/sync and resume on desktop; all images, actor knowledge and accepted events remain consistent. Conflicts remain available rather than last-writer wins.
- **Transport is replaceable:** LAN direct connectivity can work without a service; inaccessible peers need an optional relay, user-chosen remote store or other reachable endpoint. Encryption and pairing keys are never shipped in synced campaign content.

## Scope and review

All changes are proposals on an isolated feature branch and must pass the V2 independent review gates before merging. V2 remains headless until the corresponding implementation work lands. The 90 inherited ideas are retained to avoid lost moonshots, but their V1-specific implementation references are design history rather than ready-to-run V2 instructions.
