# Grimoire — independent future sprint proposals

This is a **forward-looking idea catalog**, not a sprint history, approved issue queue, release schedule or checklist. The 74 new proposals are intentionally **thematically narrow**: each names a player, author or developer outcome, an early proving slice, optional milestones, bounded interfaces and open design questions. A proposal may contain a research spike and a long-term moonshot; neither is an automatic prerequisite for the first usable improvement.

**No human engineering-time or staffing cap limits what belongs here.** Ambitious proposals are welcome when they promise better roleplay or a substantially better interface. They still need clear authority, compatibility, privacy and honest evaluation.

**Start with [Voice: the central design question](VOICE.md).** Voice controls are not synonymous with speech-to-text or reading dialogue aloud. The central unresolved issue is how to distinguish **app commands, character dialogue, narrated PC actions and director instructions** without unintended submissions or destructive changes. [Spoken Command and Intent Routing](sprints/spoken-command-and-intent.md) owns this exploration; other voice sprints are independently useful.

## How to read or select a proposal

- Browse by desired outcome below or use [Capability lookup](capabilities.md) to recover a known thematic label.
- Read [Principles](principles.md): fiction-first, human-editable file authority, one rules module or none, safe deterministic effects, stable source identity, no secret canonical caches and proportional model effort.
- The **first milestone** should ideally yield a useful vertical slice or falsifiable architectural experiment. Later milestones and moonshots extend that one theme rather than becoming a second platform.
- **Independence and interfaces** is more important than a strict dependency graph. Integrate via narrow existing or proposed contracts; use adapters/fixtures where possible rather than blocking an entire sprint on an unrelated mega-project.
- Resolve open questions when creating an implementation spec. Work from current `main`, characterize what already exists, cover failures/migrations and review independently.
- **This roadmapping branch remains planning-only and is never merged or PR'd into main.** Each implementation uses a new branch from current main.

## Proposals by capability

No numbered ordering below denotes delivery priority; groups are for browsing. One early slice may proceed while architectural research in another area is ongoing.

### Voice, speech and immersive audio (9)

| Proposal | Outcome |
|---|---|
| [Hands-free command and intent routing](sprints/spoken-command-and-intent.md) | Separate PC speech, actions, director directions and app commands; top design decision |
| [Speech-to-draft](sprints/speech-to-draft.md) | Dictate/edit posts without advancing automatically |
| [Spoken turn-taking](sprints/conversation-turn-taking.md) | Pause, interruption, barge-in and safe acceptance boundaries |
| [Character voice performance](sprints/character-voice-performance.md) | Distinct licensed voices and text-aligned delivery |
| [Live spoken RP](sprints/live-voice-roleplay.md) | Low-latency conversations grounded in committed narrative state |
| [Multi-speaker table audio](sprints/multi-speaker-table-audio.md) | Assign multiple human voices to the right controllers |
| [Soundscapes](sprints/soundscapes-and-audio-cues.md) | Optional diegetic and ambient audio |
| [Private/offline voice](sprints/private-and-local-voice.md) | Local speech backends, consent and retention |
| [Voice robustness and accessibility](sprints/voice-accessibility-and-testing.md) | Noisy-room, atypical-speech and false-command tests |

### Playable actions, actors and rules (14)

| Proposal | Outcome |
|---|---|
| [Turn effects and wrap-up](sprints/turn-effects-and-wrap-up.md) | When an event becomes canonical during play versus later review |
| [Action recovery](sprints/durable-action-recovery.md) | Exactly-once multi-file effects with crash recovery |
| [Counted inventory](sprints/counted-inventory.md) | Fiction-first holder quantities |
| [Unique items and equipment](sprints/unique-equipment.md) | Individual instances, ownership and slots |
| [Economies and crafting](sprints/economy-and-crafting.md) | Trade, resource costs and multi-step production |
| [Creature individualization](sprints/creature-individualization.md) | One generic creature becomes one unique actor |
| [Capture and trainer links](sprints/capture-and-trainer-links.md) | Immediately capture/reuse an actor without double spending |
| [Party and presence](sprints/party-and-presence.md) | Control, companions, reserve and scene cast are distinct |
| [Character growth](sprints/character-growth.md) | Individual advancement and long-term change |
| [Ruleset contracts](sprints/ruleset-action-contracts.md) | Truthful D&D, WoD, Ars Magica and PbtA interfaces |
| [Mechanical resolution](sprints/mechanical-resolution.md) | Legal actions, rolls and deterministic effects |
| [Mechanics module authoring](sprints/mechanics-module-authoring.md) | Validate and edit rules content safely |
| [Obstacles and agency](sprints/obstacles-and-agency.md) | Open-ended challenges with fair consequences |
| [Quests and commitments](sprints/quests-and-commitments.md) | Promises, deadlines and non-linear goals |

### World identity, reusable content and authoring (10)

| Proposal | Outcome |
|---|---|
| [Source-qualified identity](sprints/source-qualified-identity.md) | No accidental equivalence by display name |
| [Effective world snapshots](sprints/effective-world-snapshots.md) | One consistent view during a turn/job |
| [Inheritance and overrides](sprints/live-inheritance-and-overrides.md) | Templates, personal customizations and source changes |
| [Shared catalogs](sprints/catalog-composition.md) | Optional add-ons and bounded discovery |
| [Source upgrades](sprints/source-upgrades-and-dependencies.md) | Versions, removal, missing dependencies and conflict handling |
| [Character-card ingestion](sprints/character-card-ingestion.md) | Import lore and portrayals without canonicalizing prompts |
| [World Change Sets](sprints/world-change-sets.md) | Reviewable multi-record edits |
| [Provenance explorer](sprints/provenance-and-impact.md) | Trace sources, overrides and downstream impact |
| [Content packages](sprints/content-publication.md) | Portable sources, attribution, licensing and sharing |
| [Worldbuilding assistant](sprints/world-authoring-assistant.md) | Constraint-aware reviewed generation of reusable records |

### Living worlds, narrative memory and spatial play (14)

| Proposal | Outcome |
|---|---|
| [Location topology](sprints/location-topology.md) | Source-aware reachability and paths |
| [Interactive maps](sprints/maps-and-exploration.md) | Explore and author spatial representations |
| [Calendars and clocks](sprints/calendar-and-clocks.md) | Chronology, deadlines and event schedules |
| [Long activities](sprints/downtime-and-long-activities.md) | Travel, study, recovery and seasons |
| [Offscreen world events](sprints/offscreen-world-events.md) | Event-driven changes without infinite NPC ticks |
| [Relationships and reputation](sprints/relationships-and-reputation.md) | Personal bonds and locally held attitudes |
| [NPC autonomy](sprints/npc-goals-and-autonomy.md) | Individual motives, knowledge and independent action |
| [Faction decisions](sprints/faction-decision-making.md) | Groups, institutions and internal disagreements |
| [Knowledge and perspective](sprints/knowledge-and-perspective.md) | Truth, witnesses, beliefs, uncertainty and secrets |
| [Rumors and communication](sprints/rumors-and-information-flow.md) | Credible, delayed and distorted information spread |
| [Narrative arcs](sprints/narrative-arcs.md) | Drama and long-term payoffs without railroading |
| [Memory consolidation](sprints/memory-consolidation.md) | Coherent, sourced long-term story memory |
| [Context retrieval](sprints/context-retrieval.md) | Relevant, audience-safe and bounded prompt material |
| [Prompt evidence and evaluation](sprints/prompt-inspection-and-evals.md) | Inspect why facts were included or missed |

### Decisions, narrative quality and campaign life (11)

| Proposal | Outcome |
|---|---|
| [Decision policies](sprints/decision-policies.md) | Constrained and recordable choices before narration |
| [Characterization](sprints/characterization-and-dialogue.md) | Distinct written character voices and behavior |
| [Model routing](sprints/model-routing-and-budgeting.md) | Cost, provider choice, latency and resilience |
| [Roleplay benchmarks](sprints/story-quality-benchmarks.md) | Evaluate agency, coherence, characterization and enjoyment |
| [Campaign tuning](sprints/campaign-tuning.md) | Meaningful dials for pacing, challenge, spotlight and drama |
| [Forks and retcons](sprints/scenes-forks-and-retcons.md) | Safely change past scenes and dependent continuity |
| [Scene transfer](sprints/cross-campaign-scene-transfer.md) | Move a scene across campaigns without identity collisions |
| [Campaign publishing](sprints/publishing-and-anonymization.md) | Books, POV and privacy-safe exports |
| [Agentic investigations](sprints/agentic-investigations.md) | Bounded evidence-led optional worker jobs |
| [Scenario preparation](sprints/scenario-preparation.md) | Grounded, non-binding scene premises |
| [Auxiliary writing](sprints/auxiliary-writing-tools.md) | Polish, translate, brainstorm and hypothetical non-canonical variants |

### Presentation, participation and moonshot interfaces (9)

| Proposal | Outcome |
|---|---|
| [Scene workspace](sprints/scene-workspace.md) | Accessible, low-friction reading, writing and review |
| [VN direction](sprints/vn-staging-and-cues.md) | Extend the existing VN reader with cinematic staging |
| [Art and expressions](sprints/expression-and-art-lineage.md) | Existing outfit/emotion assets, inheritance and editing |
| [Image generation](sprints/image-generation.md) | Coherent and reviewable portraits/backgrounds |
| [Story exploration](sprints/story-graph-and-navigation.md) | Navigate narrative history, causal threads and evidence |
| [Local multi-PC](sprints/local-multi-pc.md) | Compose several PCs then advance once |
| [Multiplayer collaboration](sprints/collaborative-multiplayer.md) | Multiuser control, permissions and concurrency |
| [Live context preview](sprints/live-context-preview.md) | Inspect likely prompt effects without committing a turn |
| [Immersive interface](sprints/immersive-world-interface.md) | Optional spatial rooms, avatars and VR/AR research |

### Engineering trust and foundations (7)

| Proposal | Outcome |
|---|---|
| [Theory-driven simplification](sprints/theory-driven-simplification.md) | Remove conceptual debt, not merely large files |
| [Typed boundaries](sprints/type-and-api-boundaries.md) | Precise APIs, errors and contracts |
| [Failure injection](sprints/testing-and-failure-injection.md) | Crash, race and compatibility testing |
| [Performance and tracing](sprints/performance-and-tracing.md) | Measured end-to-end latency and cost |
| [Coding-skills calibration](sprints/coding-skills-calibration.md) | Reliable detector and Naur-theory judgments |
| [B+ quality gate](sprints/code-quality-gate.md) | Only calibrated >=87/100 package/report checks may block |
| [Safe tool execution](sprints/safe-tool-execution.md) | Least-privilege permissions and hostile-content defenses |

## A few deliberately parallel starting slices

The roadmap does not make inventory wait for a complete mechanics or content manager; a counted-holdings MVP can use the current campaign store. Creature individualization can be demonstrated in mechanics-free scenes. World source identity and effective-view experiments can proceed separately, and an initial voice-command intent router can be tested with **typed transcripts before a speech backend exists**. Each proves its own claim before building abstractions for hypothetical consumers.

The current **VN reader and expression/outfit libraries already exist**: their proposals are enhancements, not foundational rebuilds. The old bulk Python package modularization is likewise completed; new code-health work addresses residual debt.

## Archive and promotion

The previous **26 broad proposals** have been preserved under [sprints/old/](sprints/old/README.md) for traceability. They are not concurrent active sprint definitions; follow links here instead. Historical decisions and implemented foundations remain available in Git history.

When promoting a proposal: pin source state, answer its blocking open questions or define a spike, write a reviewed spec and acceptance/failure tests, plan migration and rollback, and work on a fresh branch from main. A moonshot does not need a delivery estimate to remain a legitimate candidate.
