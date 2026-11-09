# Grimoire Roadmap — Dependency-Safe Parallel Waves

**Planning-only branch.** This roadmap schedules the **first independently mergeable milestone** of each of the **90 current sprint proposals**, not every later milestone or moonshot in those files. All original 26 broad proposals remain archived under [`sprints/old/`](sprints/old/README.md). The [Thematic catalog](CATALOG.md) and [Capability lookup](capabilities.md) provide alternate browsing; [VOICE.md](VOICE.md) describes the voice-first design questions.

**Goal:** Each wave improves the playable product, roleplay quality, user experience, content creation or reliability without hiding a prerequisite **inside the same wave**. Every row has its own first delivered proof, a bounded primary ownership area and an explicit set of hard prerequisites in **earlier** waves. The table is a dependency plan, not an estimate of human effort or a promise to build every moonshot.

## Priority and execution rules

- **P0 — Trust and core RP transformation:** player agency, fair action consequences, continuity, voice control, knowledge, durable identity and actual play. Prioritize these when prerequisites are satisfied.
- **P1 — High-value depth and reliability:** compelling characters, game systems, creator tools, campaign management and testability.
- **P2 — Expansion:** substantial optional systems, advanced production modes and ambitious content workflows.
- **P3 — Exploratory moonshot:** transformative but unproven interface/platform directions, kept on the roadmap without speculative prerequisites.

**Priority and wave number are different:** a P0 voice experience may land in Wave 8 because it needs independent Wave 1–6 audio/conversation contracts. Wave numbers are *topological readiness layers*, not calendar quarters. Work from **current `main`** on separate implementation branches; this planning branch is never merged. A later wave's item may start as soon as **its listed hard dependencies** have been integrated and validated; unrelated unfinished items in an earlier wave do not block it.

**What “parallel” means in these tables:** within each wave no sprint requires an output of another sprint in that wave. Each claims a distinct **primary code/contract ownership area** shown below; implementers must freeze the interfaces coming from earlier waves, use adapters and contract fixtures, and avoid competing writes to shared files. If two implementations discover a shared unowned API or migration that requires concurrent modification, assign its contract owner, split it into a separately integrated prerequisite, or move one sprint to a later wave. **Do not quietly merge overlapping branches and call it parallelism.** A read-only experiment should stay read-only until its dependent writable contract is available.

**Completion of a prerequisite** means the required first slice, stable contract, migration and failure tests are on `main`—not that its distant moonshot is finished. The rest of a proposal's milestones can be promoted as further slices with newly checked prerequisites. All ordinary code/spec review, reproducible tests, privacy safeguards and rollback still apply. Where a design experiment rejects its hypothesis, revise downstream links rather than implementing a fictional dependency.

## Wave overview

| Wave | Parallel proposals | Improvement delivered |
|---|---:|---|
| [1: Trust boundaries and independent baselines](#wave-1) | 14 | Establish agency, voice-intent hypotheses, identity/rules contracts, safety, evaluation and approachable UI without rebuilding the game engine. |
| [2: Playable fidelity, memory boundaries and input](#wave-2) | 11 | Deliver useful freeform capabilities over current files while establishing scene-effect timing, actor knowledge and editable speech. |
| [3: Freeform outcomes, scene grounding and state safety](#wave-3) | 10 | Produce stable outcomes, recoverable state and believable physical/fictional communication, with usable typed and spoken output. |
| [4: Trustworthy narration, first experiences and core orchestration](#wave-4) | 12 | Improve moment-to-moment play: legal actions, grounded narration, responsive NPCs and lightweight onboarding. |
| [5: Longer-lived worlds, decisions and branching](#wave-5) | 8 | Integrate known actors, state, source-aware catalogs and editable history through stable narrow interfaces. |
| [6: Complex conversation, combat and coordinated content](#wave-6) | 11 | Give the narrator controlled ensembles and fights while extending authoring and history without creating a monolith. |
| [7: Living consequences and creator/voice tools](#wave-7) | 11 | Make time, institutions and relationships matter, with reviewable worldbuilding and strong campaign navigation. |
| [8: Deep play, media and advanced evaluations](#wave-8) | 8 | Extend existing foundations toward rich investigations, live voice, maps and character-scale consequences. |
| [9: Long-form products, publishable content and collaboration](#wave-9) | 3 | Consolidate durable story arcs, source packaging and real multiplayer over existing committed interfaces. |
| [10: Exploratory long-range interfaces](#wave-10) | 2 | Pursue independent infrastructure and presentation moonshots after their necessary contracts exist, not by requiring one enormous platform. |

## Parallel wave details

A row's **Prerequisites** are the only *hard roadmap dependencies for its listed first slice*. Future feature integrations and richer milestones may have additional optional consumers or prerequisites listed in the sprint itself. **Primary owner** names the protected implementation surface for the wave; it is not a new canonical data layer.

### Wave 1

**Trust boundaries and independent baselines.** Establish agency, voice-intent hypotheses, identity/rules contracts, safety, evaluation and approachable UI without rebuilding the game engine.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Player Sovereignty](sprints/player-sovereignty.md) | **P0** | Existing Grimoire only | prompt / PC authorship | Prove PC actions and inner life are not narrated without permission |
| [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | **P0** | Existing Grimoire only | voice / typed-intent lab | Test commands versus dialogue on typed utterances with no live writes |
| [Private And Local Voice](sprints/private-and-local-voice.md) | **P1** | Existing Grimoire only | voice / provider privacy | Disclose and control one speech provider's data flow |
| [Voice Accessibility And Testing](sprints/voice-accessibility-and-testing.md) | **P0** | Existing Grimoire only | evaluation / voice fixtures | Build false-command, noise and recovery fixtures |
| [Story Quality Benchmarks](sprints/story-quality-benchmarks.md) | **P0** | Existing Grimoire only | evaluation / RP quality | Baseline agency, continuity and characterization |
| [Source Qualified Identity](sprints/source-qualified-identity.md) | **P0** | Existing Grimoire only | storage / source IDs | Prove distinct equal-named source records |
| [Ruleset Action Contracts](sprints/ruleset-action-contracts.md) | **P0** | Existing Grimoire only | mechanics / contracts | Spike D&D, WoD, Ars Magica and PbtA differences |
| [Calendar And Clocks](sprints/calendar-and-clocks.md) | **P1** | Existing Grimoire only | time / calendar store | Advance a deadline reproducibly |
| [Scene Workspace](sprints/scene-workspace.md) | **P0** | Existing Grimoire only | frontend / scene workspace | Improve one measured typing/navigation pain point |
| [Coding Skills Calibration](sprints/coding-skills-calibration.md) | **P1** | Existing Grimoire only | external / coding skills | Create pinned true-defect and false-positive cases |
| [Safe Tool Execution](sprints/safe-tool-execution.md) | **P0** | Existing Grimoire only | security / capability policy | Prove no untrusted source can gain write authority |
| [Campaign Archaeology](sprints/campaign-archaeology.md) | **P1** | Existing Grimoire only | CLI / transcript preview | Preview segmentation and source evidence without writes |
| [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | **P1** | Existing Grimoire only | testing / fault harness | Inject a controlled file-write failure |
| [Theory Driven Simplification](sprints/theory-driven-simplification.md) | **P1** | Existing Grimoire only | engineering / reserved isolated package | Simplify one agreed package outside other Wave 1 owners |

### Wave 2

**Playable fidelity, memory boundaries and input.** Deliver useful freeform capabilities over current files while establishing scene-effect timing, actor knowledge and editable speech.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md) | **P0** | [Player Sovereignty](sprints/player-sovereignty.md) | scene / effect acceptance | Specify and prove when a mid-scene event becomes truth |
| [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | **P0** | [Source Qualified Identity](sprints/source-qualified-identity.md) | knowledge / evidence store | Distinguish witnesses from director-only facts |
| [Counted Inventory](sprints/counted-inventory.md) | **P0** | Existing Grimoire only | inventory / counted holdings | Persist eight balls, spend three, acquire four and reload nine |
| [Creature Individualization](sprints/creature-individualization.md) | **P0** | [Source Qualified Identity](sprints/source-qualified-identity.md) | actors / individualization | Promote one goblin without duplicating its species |
| [Speech To Draft](sprints/speech-to-draft.md) | **P0** | [Spoken Command And Intent](sprints/spoken-command-and-intent.md), [Private And Local Voice](sprints/private-and-local-voice.md) | voice / STT composer | Produce an editable dictated post with no implicit advance |
| [Session Zero Boundaries](sprints/session-zero-boundaries.md) | **P0** | [Player Sovereignty](sprints/player-sovereignty.md) | campaign / content boundaries | Set and exercise a skip or fade-to-black preference |
| [Fictional Abilities](sprints/fictional-abilities.md) | **P0** | [Player Sovereignty](sprints/player-sovereignty.md) | world / ability descriptions | Track a freeform ability's limits without fake dice |
| [Location Topology](sprints/location-topology.md) | **P1** | [Source Qualified Identity](sprints/source-qualified-identity.md) | world / location graph | Resolve connected places despite colliding names |
| [Characterization And Dialogue](sprints/characterization-and-dialogue.md) | **P1** | [Story Quality Benchmarks](sprints/story-quality-benchmarks.md), [Player Sovereignty](sprints/player-sovereignty.md) | generation / voice style | Differentiate three characters without knowledge leakage |
| [Performance And Tracing](sprints/performance-and-tracing.md) | **P1** | [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | runtime / tracing | Explain time and cost of one complete turn |
| [Type And API Boundaries](sprints/type-and-api-boundaries.md) | **P1** | [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | API / isolated error contract | Improve one frozen API boundary outside other wave owners |

### Wave 3

**Freeform outcomes, scene grounding and state safety.** Produce stable outcomes, recoverable state and believable physical/fictional communication, with usable typed and spoken output.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | **P0** | [Player Sovereignty](sprints/player-sovereignty.md), [Fictional Abilities](sprints/fictional-abilities.md), [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md) | generation / freeform resolution | Adjudicate one risky mechanics-free action fairly |
| [Scene Physical Continuity](sprints/scene-physical-continuity.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md) | scene / local staging | Track a room, closed door, moving actors and held item |
| [Effective World Snapshots](sprints/effective-world-snapshots.md) | **P0** | [Source Qualified Identity](sprints/source-qualified-identity.md) | world / effective read view | Read a consistent inherited world for one turn |
| [Durable Action Recovery](sprints/durable-action-recovery.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | storage / action journal | Recover an interrupted two-record action exactly once |
| [Diegetic Communication](sprints/diegetic-communication.md) | **P0** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | fiction / communication events | Private whisper/telepathy reaches only intended witnesses |
| [Conversation Turn Taking](sprints/conversation-turn-taking.md) | **P0** | [Speech To Draft](sprints/speech-to-draft.md), [Spoken Command And Intent](sprints/spoken-command-and-intent.md) | voice / turn state machine | Distinguish pause, end, cancellation and interruption |
| [Character Voice Performance](sprints/character-voice-performance.md) | **P1** | [Private And Local Voice](sprints/private-and-local-voice.md) | voice / TTS playback | Read two characters with distinct version-linked voices |
| [Unique Equipment](sprints/unique-equipment.md) | **P1** | [Counted Inventory](sprints/counted-inventory.md) | items / unique instances | Keep two same-named artifacts independently owned |
| [Party And Presence](sprints/party-and-presence.md) | **P1** | [Creature Individualization](sprints/creature-individualization.md) | actors / party graph | Separate present companion from reserve and scene cast |
| [Mechanics Module Authoring](sprints/mechanics-module-authoring.md) | **P1** | [Ruleset Action Contracts](sprints/ruleset-action-contracts.md) | editor / rules modules | Validate one authored sheet and check |

### Wave 4

**Trustworthy narration, first experiences and core orchestration.** Improve moment-to-moment play: legal actions, grounded narration, responsive NPCs and lightweight onboarding.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Mechanical Resolution](sprints/mechanical-resolution.md) | **P0** | [Ruleset Action Contracts](sprints/ruleset-action-contracts.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | mechanics / action execution | Resolve a legal roll and commit its result once |
| [Live Inheritance And Overrides](sprints/live-inheritance-and-overrides.md) | **P1** | [Source Qualified Identity](sprints/source-qualified-identity.md), [Effective World Snapshots](sprints/effective-world-snapshots.md) | world / inherited fields | Prove individual portrait override survives source change |
| [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md) | **P0** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | NPC / goal actions | Choose one goal-driven NPC action independently of plot |
| [Multimodal Player Input](sprints/multimodal-player-input.md) | **P2** | [Scene Physical Continuity](sprints/scene-physical-continuity.md) | frontend / image reference input | Annotate a sketch as uncommitted player intent |
| [Narration Continuity Referee](sprints/narration-continuity-referee.md) | **P0** | [Scene Physical Continuity](sprints/scene-physical-continuity.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | generation / continuity checking | Catch contradictions without silently changing scene state |
| [Instant Campaign Creation](sprints/instant-campaign-creation.md) | **P0** | [Session Zero Boundaries](sprints/session-zero-boundaries.md) | frontend / campaign onboarding | Create a playable scene from one sentence |
| [Soundscapes And Audio Cues](sprints/soundscapes-and-audio-cues.md) | **P2** | [Private And Local Voice](sprints/private-and-local-voice.md) | audio / ambience | Play optional location ambience with a mute fallback |
| [Auxiliary Writing Tools](sprints/auxiliary-writing-tools.md) | **P1** | [Player Sovereignty](sprints/player-sovereignty.md), [Scene Workspace](sprints/scene-workspace.md) | frontend / draft helpers | Generate a hypothetical non-canonical reply |
| [Obstacles And Agency](sprints/obstacles-and-agency.md) | **P0** | [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | stories / obstacle planning | Run one obstacle allowing unconventional solutions |
| [Character Card Ingestion](sprints/character-card-ingestion.md) | **P1** | [Source Qualified Identity](sprints/source-qualified-identity.md), [Effective World Snapshots](sprints/effective-world-snapshots.md) | import / character cards | Review a card without canonizing its scenario instructions |
| [Model Routing And Budgeting](sprints/model-routing-and-budgeting.md) | **P1** | [Performance And Tracing](sprints/performance-and-tracing.md) | inference / provider router | Test reliable, measured per-task fallback |
| [Code Quality Gate](sprints/code-quality-gate.md) | **P1** | [Coding Skills Calibration](sprints/coding-skills-calibration.md), [Theory Driven Simplification](sprints/theory-driven-simplification.md), [Testing And Failure Injection](sprints/testing-and-failure-injection.md) | CI / shadow grade checks | Publish report-only per-package B+ evidence; do not activate an unqualified blocker |

### Wave 5

**Longer-lived worlds, decisions and branching.** Integrate known actors, state, source-aware catalogs and editable history through stable narrow interfaces.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Capture And Trainer Links](sprints/capture-and-trainer-links.md) | **P0** | [Counted Inventory](sprints/counted-inventory.md), [Creature Individualization](sprints/creature-individualization.md), [Party And Presence](sprints/party-and-presence.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / capture | Catch a named monster with one ball and no duplicate actor |
| [Catalog Composition](sprints/catalog-composition.md) | **P1** | [Effective World Snapshots](sprints/effective-world-snapshots.md) | world / catalog inclusion | Use a shared bestiary without copying its full contents |
| [NPC Theory Of Mind](sprints/npc-theory-of-mind.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | NPC / belief reasoning | Reason about another NPC's mistaken belief |
| [Context Retrieval](sprints/context-retrieval.md) | **P0** | [Effective World Snapshots](sprints/effective-world-snapshots.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | inference / context selection | Build an audience-correct bounded prompt |
| [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | scene / fork and revision | Preview a fork without duplicating effects |
| [World Change Sets](sprints/world-change-sets.md) | **P1** | [Effective World Snapshots](sprints/effective-world-snapshots.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | authoring / change sets | Apply one reviewed two-record edit with stale-base checking |
| [Expression And Art Lineage](sprints/expression-and-art-lineage.md) | **P1** | [Live Inheritance And Overrides](sprints/live-inheritance-and-overrides.md) | art / asset manager | Preserve expression overrides and art provenance |
| [Decision Policies](sprints/decision-policies.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Fiction First Adjudication](sprints/fiction-first-adjudication.md) | inference / decision selector | Record a constrained, explainable NPC reaction |

### Wave 6

**Complex conversation, combat and coordinated content.** Give the narrator controlled ensembles and fights while extending authoring and history without creating a monolith.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Ensemble Conversation](sprints/ensemble-conversation.md) | **P0** | [Diegetic Communication](sprints/diegetic-communication.md), [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Decision Policies](sprints/decision-policies.md) | generation / conversation floor | Handle three voices, interruption and meaningful silence |
| [Combat Encounter Orchestration](sprints/combat-encounter-orchestration.md) | **P1** | [Mechanical Resolution](sprints/mechanical-resolution.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Scene Physical Continuity](sprints/scene-physical-continuity.md) | gameplay / combat session | Resolve two multi-actor rounds under one ruleset |
| [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md) | **P1** | [Catalog Composition](sprints/catalog-composition.md), [Live Inheritance And Overrides](sprints/live-inheritance-and-overrides.md) | world / source upgrades | Preview a missing source and safe catalog update |
| [Faction Decision Making](sprints/faction-decision-making.md) | **P1** | [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | society / institutions | Choose an institution's action despite internal dissent |
| [Rumors And Information Flow](sprints/rumors-and-information-flow.md) | **P1** | [Diegetic Communication](sprints/diegetic-communication.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | society / rumors | Spread one delayed and distorted report |
| [Memory Consolidation](sprints/memory-consolidation.md) | **P0** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md) | memory / long-horizon ledger | Maintain sourced facts across corrected scenes |
| [Cross Campaign Scene Transfer](sprints/cross-campaign-scene-transfer.md) | **P2** | [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md), [Source Qualified Identity](sprints/source-qualified-identity.md) | campaign / transfer | Copy a transcript with reviewed identity mapping |
| [Local Multi PC](sprints/local-multi-pc.md) | **P1** | [Player Sovereignty](sprints/player-sovereignty.md), [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md) | frontend / multi-PC draft | Submit two PC posts before one GM advance |
| [Scenario Preparation](sprints/scenario-preparation.md) | **P1** | [Obstacles And Agency](sprints/obstacles-and-agency.md) | stories / scene proposals | Prepare grounded optional tensions for a scene |
| [VN Staging And Cues](sprints/vn-staging-and-cues.md) | **P2** | [Expression And Art Lineage](sprints/expression-and-art-lineage.md) | frontend / VN stage | Render authored stage cues without changing transcript |
| [Downtime And Long Activities](sprints/downtime-and-long-activities.md) | **P1** | [Calendar And Clocks](sprints/calendar-and-clocks.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / downtime | Complete a 20-day study and advance time once |

### Wave 7

**Living consequences and creator/voice tools.** Make time, institutions and relationships matter, with reviewable worldbuilding and strong campaign navigation.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Offscreen World Events](sprints/offscreen-world-events.md) | **P1** | [Calendar And Clocks](sprints/calendar-and-clocks.md), [Faction Decision Making](sprints/faction-decision-making.md), [Decision Policies](sprints/decision-policies.md) | simulation / offscreen scheduler | Advance one faction's plan during player absence |
| [Relationships And Reputation](sprints/relationships-and-reputation.md) | **P1** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [NPC Goals And Autonomy](sprints/npc-goals-and-autonomy.md) | social / relationship records | Update trust without giving absent NPCs global knowledge |
| [Quests And Commitments](sprints/quests-and-commitments.md) | **P1** | [Turn Effects And Wrap Up](sprints/turn-effects-and-wrap-up.md), [Calendar And Clocks](sprints/calendar-and-clocks.md) | campaign / commitments | Track an accepted promise and deadline |
| [World Authoring Assistant](sprints/world-authoring-assistant.md) | **P2** | [World Change Sets](sprints/world-change-sets.md), [Catalog Composition](sprints/catalog-composition.md) | authoring / world proposals | Review three linked towns and accept two |
| [Provenance And Impact](sprints/provenance-and-impact.md) | **P1** | [World Change Sets](sprints/world-change-sets.md), [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md) | frontend / source impact | Show which campaigns inherit a changed record |
| [Story Graph And Navigation](sprints/story-graph-and-navigation.md) | **P1** | [Memory Consolidation](sprints/memory-consolidation.md), [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md) | frontend / story explorer | Find an old promise and its eventual outcome |
| [Live Context Preview](sprints/live-context-preview.md) | **P2** | [Context Retrieval](sprints/context-retrieval.md) | frontend / prompt preview | Preview likely retrieval without paying for a turn |
| [Agentic Investigations](sprints/agentic-investigations.md) | **P2** | [World Change Sets](sprints/world-change-sets.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | agents / evidence jobs | Review a bounded read-only world audit |
| [Multi Speaker Table Audio](sprints/multi-speaker-table-audio.md) | **P2** | [Local Multi PC](sprints/local-multi-pc.md), [Conversation Turn Taking](sprints/conversation-turn-taking.md) | voice / multiuser capture | Attribute two known audio channels to separate PCs |
| [Actor Specific Inference](sprints/actor-specific-inference.md) | **P2** | [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Decision Policies](sprints/decision-policies.md), [Story Quality Benchmarks](sprints/story-quality-benchmarks.md) | evaluation / independent NPC calls | Compare independent actor decisions with a single narrator |
| [Image Generation](sprints/image-generation.md) | **P2** | [Expression And Art Lineage](sprints/expression-and-art-lineage.md) | media / image tasks | Generate and review one character image without altering source |

### Wave 8

**Deep play, media and advanced evaluations.** Extend existing foundations toward rich investigations, live voice, maps and character-scale consequences.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Fair Mysteries And Deduction](sprints/fair-mysteries-and-deduction.md) | **P1** | [Obstacles And Agency](sprints/obstacles-and-agency.md), [Knowledge And Perspective](sprints/knowledge-and-perspective.md), [Narration Continuity Referee](sprints/narration-continuity-referee.md) | stories / mysteries | Prove one fixed-answer mystery supports novel investigation |
| [Maps And Exploration](sprints/maps-and-exploration.md) | **P2** | [Location Topology](sprints/location-topology.md) | frontend / map views | Show and edit a four-node map without required coordinates |
| [Economy And Crafting](sprints/economy-and-crafting.md) | **P2** | [Unique Equipment](sprints/unique-equipment.md), [Counted Inventory](sprints/counted-inventory.md), [Durable Action Recovery](sprints/durable-action-recovery.md) | gameplay / trade and craft | Trade/craft an item without duplicate effects |
| [Character Growth](sprints/character-growth.md) | **P1** | [Creature Individualization](sprints/creature-individualization.md), [Party And Presence](sprints/party-and-presence.md) | actors / advancement | Advance individual abilities without changing species template |
| [Publishing And Anonymization](sprints/publishing-and-anonymization.md) | **P2** | [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md), [Memory Consolidation](sprints/memory-consolidation.md) | export / story publication | Produce a non-mutating anonymized EPUB |
| [Prompt Inspection And Evals](sprints/prompt-inspection-and-evals.md) | **P1** | [Context Retrieval](sprints/context-retrieval.md), [Story Quality Benchmarks](sprints/story-quality-benchmarks.md), [Memory Consolidation](sprints/memory-consolidation.md) | evaluation / prompt provenance | Explain selected and omitted historical evidence |
| [Live Voice Roleplay](sprints/live-voice-roleplay.md) | **P0** | [Ensemble Conversation](sprints/ensemble-conversation.md), [Conversation Turn Taking](sprints/conversation-turn-taking.md), [Character Voice Performance](sprints/character-voice-performance.md), [Diegetic Communication](sprints/diegetic-communication.md) | voice / live session | Run an interruptible, text-synced PC/NPC speech session |
| [Campaign Tuning](sprints/campaign-tuning.md) | **P1** | [Decision Policies](sprints/decision-policies.md), [Obstacles And Agency](sprints/obstacles-and-agency.md) | campaign / pacing policies | Demonstrate a player-visible pacing control and its effect |

### Wave 9

**Long-form products, publishable content and collaboration.** Consolidate durable story arcs, source packaging and real multiplayer over existing committed interfaces.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Narrative Arcs](sprints/narrative-arcs.md) | **P1** | [Quests And Commitments](sprints/quests-and-commitments.md), [Scenario Preparation](sprints/scenario-preparation.md), [Story Graph And Navigation](sprints/story-graph-and-navigation.md) | stories / long arcs | Replan an arc after the player rejects a key event |
| [Content Publication](sprints/content-publication.md) | **P2** | [Catalog Composition](sprints/catalog-composition.md), [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md), [Character Card Ingestion](sprints/character-card-ingestion.md) | sharing / content manifests | Round-trip one versioned world/catalog package |
| [Collaborative Multiplayer](sprints/collaborative-multiplayer.md) | **P2** | [Local Multi PC](sprints/local-multi-pc.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Safe Tool Execution](sprints/safe-tool-execution.md) | network / co-play | Coordinate two controllers without unauthorized state changes |

### Wave 10

**Exploratory long-range interfaces.** Pursue independent infrastructure and presentation moonshots after their necessary contracts exist, not by requiring one enormous platform.

| Sprint | Priority | Hard prerequisites (previous waves only) | Primary owner | Independently useful first slice |
|---|---|---|---|---|
| [Offline Multi Device Sync](sprints/offline-multi-device-sync.md) | **P2** | [Source Upgrades And Dependencies](sprints/source-upgrades-and-dependencies.md), [Durable Action Recovery](sprints/durable-action-recovery.md), [Scenes Forks And Retcons](sprints/scenes-forks-and-retcons.md) | sync / offline revisions | Reconcile two offline device copies without silent loss |
| [Immersive World Interface](sprints/immersive-world-interface.md) | **P3** | [Maps And Exploration](sprints/maps-and-exploration.md), [VN Staging And Cues](sprints/vn-staging-and-cues.md) | frontend / spatial view | Explore one room visually with scene truth unchanged |

## Critical cross-wave dependency paths

These are **examples**, not mandates that every other proposal use these paths:

- **Voice-native play:** [Intent routing](sprints/spoken-command-and-intent.md) → [Speech drafts](sprints/speech-to-draft.md) + [Turn-taking](sprints/conversation-turn-taking.md) and independent [Character TTS](sprints/character-voice-performance.md) + [Diegetic channels](sprints/diegetic-communication.md) → [Ensemble conversation](sprints/ensemble-conversation.md) → [Live voice RP](sprints/live-voice-roleplay.md). The starting voice-intent prototype runs on typed utterances, so no speech provider is a hidden Wave 1 prerequisite.
- **Coherent fiction:** [Player sovereignty](sprints/player-sovereignty.md) → [Turn-effect authority](sprints/turn-effects-and-wrap-up.md) + [Fictional abilities](sprints/fictional-abilities.md) → [Fiction-first adjudication](sprints/fiction-first-adjudication.md) and independent [Physical scene continuity](sprints/scene-physical-continuity.md) + [Knowledge](sprints/knowledge-and-perspective.md) → [Narration referee](sprints/narration-continuity-referee.md).
- **Captured companions:** [Counted inventory](sprints/counted-inventory.md) + [Creature individualization](sprints/creature-individualization.md) → [Party/Presence](sprints/party-and-presence.md) + [Durable effects](sprints/durable-action-recovery.md) → [Capture](sprints/capture-and-trainer-links.md). This does not require the entire mechanics engine.
- **Reusable worlds:** [Qualified identity](sprints/source-qualified-identity.md) → [Effective-world reads](sprints/effective-world-snapshots.md) → [Catalog composition](sprints/catalog-composition.md) and [Inheritance](sprints/live-inheritance-and-overrides.md) → [Source upgrades](sprints/source-upgrades-and-dependencies.md), while [World Change Sets](sprints/world-change-sets.md) supply a separate reviewed-authoring path.
- **Living narratives:** [Knowledge](sprints/knowledge-and-perspective.md) → [NPC goals](sprints/npc-goals-and-autonomy.md) → [Faction actions](sprints/faction-decision-making.md), with independent [Calendars](sprints/calendar-and-clocks.md) → [Offscreen events](sprints/offscreen-world-events.md), and [Memory](sprints/memory-consolidation.md) feeding long-term story navigation and arcs.
- **Graded code quality:** [Coding-skills calibration](sprints/coding-skills-calibration.md) + a completed [Theory simplification slice](sprints/theory-driven-simplification.md) + [Fault tests](sprints/testing-and-failure-injection.md) → **report-only shadow gate** in [Quality Gate](sprints/code-quality-gate.md). Blocking activation remains a separate decision after proven accuracy and sufficient cleanup.

## Coordination / release safeguards

1. **Reserve file ownership when starting each wave.** The primary owners in each table cannot both edit the same route, canonical schema, store writer or package facade without first landing a dedicated interface change. Feature-specific adapters and fixtures are preferable to parallel edits of a shared center.
2. **Resolve difficult choices as spikes.** If a first milestone is architectural research, its deliverable is a documented, tested decision or rejected hypothesis—not a silently assumed foundation.
3. **Guard the story authority boundary.** Canonical records remain human-editable Markdown/JSON. Narration, media, audio and autonomous decisions cannot silently mutate committed actions. Director control cannot impersonate the player's PC.
4. **Test meaningful player outcomes.** P0 work must improve agency, knowledge fidelity, continuity or low-friction voice/scene play; a new abstract service alone is not proof.
5. **No false completeness.** Already shipped VN/expressions, model routes, dice and ingest foundations are reused. Moonshots are intentionally unbounded in ambition, but each listed first slice must function without future infrastructure.

The grouped [catalog](CATALOG.md) and complete [capability lookup](capabilities.md) remain the navigation aids; the **wave table above owns sequencing and hard dependency claims**. For archival reasons, the 26 replaced broad proposals live under [sprints/old/](sprints/old/README.md).
