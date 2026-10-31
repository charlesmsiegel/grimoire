# V2 thematic sprint catalog (107 proposals)

The [ordered roadmap](README.md) is authoritative for **priorities, dependencies, parallel waves and the early migration/play/sync path**. This thematic catalog preserves the 90 earlier ideas and highlights 17 V2-specific foundations. V1 assumptions in carried proposal bodies are not implemented V2 features.

## V2-specific foundations and playable milestones (17)

| Proposal | First proof | Scheduled wave |
|---|---|---:|
| [V2 Library Paths And Isolation](sprints/v2-library-paths-and-isolation.md) | Set ~/.grimoire-v2/ default and explicitly reject V1 write targets | 1 |
| [Turn Costs And Feature Flags](sprints/turn-costs-and-feature-flags.md) | Require per-NPC-generation baseline and opt-in paid analysis stages | 1 |
| [Entity Facets And Instance Model](sprints/entity-facets-and-instance-model.md) | Make typed entities/instances/PC control roles composable | 2 |
| [Native File Store And Revisions](sprints/native-file-store-and-revisions.md) | Persist native definitions/instances with revision preconditions | 2 |
| [V1 To V2 Migration Skill](sprints/v1-to-v2-migration-skill.md) | Preview V0.2.1 IDs, scene files, image references and losses without writes | 2 |
| [V1 Cost And Voice Regression](sprints/v1-cost-and-voice-regression.md) | Capture actual 0.2.1 billed tokens and per-NPC voice baselines | 2 |
| [World-optional Chat Scopes](sprints/world-optional-chat-scopes.md) | Start a chat without a world, attach characters/scenarios/lorebooks and keep one history across navigation views | 3 |
| [Typed Relationship Graph](sprints/typed-relationship-graph.md) | Traverse typed relations with scope, time and epistemic bounds | 3 |
| [Image Identity And Placements](sprints/image-identity-and-placements.md) | Preserve shared image blobs, per-record placements and version crop | 3 |
| [Sync Revisions And Conflict Core](sprints/sync-revisions-and-conflict-core.md) | Preserve concurrent edits and detect conflict rather than overwrite | 3 |
| [Durable Event Evidence](sprints/durable-event-evidence.md) | Persist an accepted private event and evidence without access leakage | 3 |
| [V1 To V2 Import Apply](sprints/v1-to-v2-import-apply.md) | Import two actors, one scene and their actual pictures into isolated V2 | 4 |
| [Per Npc Generation Runtime](sprints/per-npc-generation-runtime.md) | Generate each speaking NPC in a separate knowledge-scoped model call | 4 |
| [App Managed Device Sync](sprints/app-managed-device-sync.md) | Transfer an authenticated scene and image between two local peers | 4 |
| [Minimal Text Scene Runner](sprints/minimal-text-scene-runner.md) | Play/resume both a world-based campaign and world-less card/scenario chat | 5 |
| [Source Rebase And Semantic Merge](sprints/source-rebase-and-semantic-merge.md) | Update one root fact while two campaigns retain independent memories | 5 |
| [V2 Mobile Client](sprints/v2-mobile-client.md) | Play on phone, sync an accepted turn plus picture, resume on desktop | 6 |

## Earlier 90 proposals by theme

## Voice, speech and immersive audio (9)

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

## Playable actions, actors and rules (20)

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
| [Player sovereignty](sprints/player-sovereignty.md) | Players own PC intent, actions and interior life |
| [Fiction-first adjudication](sprints/fiction-first-adjudication.md) | Fair outcomes without mandatory mechanics |
| [Scene physical continuity](sprints/scene-physical-continuity.md) | Local staging, objects and visibility |
| [Fictional abilities](sprints/fictional-abilities.md) | Coherent powers without requiring dice |
| [Combat encounters](sprints/combat-encounter-orchestration.md) | Whole multi-round tactical encounters |
| [Fair mysteries](sprints/fair-mysteries-and-deduction.md) | Stable answers and evidence for actual deduction |

## World identity, reusable content and authoring (11)

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
| [Instant campaign creation](sprints/instant-campaign-creation.md) | One premise to playable RP and organic world growth |

## Living worlds, narrative memory and spatial play (16)

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
| [Diegetic communication](sprints/diegetic-communication.md) | Private channels, receivers and in-fiction speech |
| [NPC theory of mind](sprints/npc-theory-of-mind.md) | Beliefs about others' imperfect beliefs |

## Decisions, narrative quality and campaign life (15)

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
| [Narration continuity referee](sprints/narration-continuity-referee.md) | Evidence-led contradiction detection and repair |
| [Campaign archaeology](sprints/campaign-archaeology.md) | Dry-run import and reconstruction of RP histories |
| [Ensemble conversation](sprints/ensemble-conversation.md) | Floor, interrupts and subtext for many NPCs |
| [Actor-specific inference](sprints/actor-specific-inference.md) | Independent character-reasoning experiments |

## Presentation, participation and moonshot interfaces (12)

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
| [Multimodal player input](sprints/multimodal-player-input.md) | Images, sketches and gestures as reviewed intent |
| [Offline multi-device sync](sprints/offline-multi-device-sync.md) | Conflict-preserving cross-device library edits |
| [Session-zero boundaries](sprints/session-zero-boundaries.md) | Explicit creative limits and skip/redirect controls |

## Engineering trust and foundations (7)

| Proposal | Outcome |
|---|---|
| [Theory-driven simplification](sprints/theory-driven-simplification.md) | Remove conceptual debt, not merely large files |
| [Typed boundaries](sprints/type-and-api-boundaries.md) | Precise APIs, errors and contracts |
| [Failure injection](sprints/testing-and-failure-injection.md) | Crash, race and compatibility testing |
| [Performance and tracing](sprints/performance-and-tracing.md) | Measured end-to-end latency and cost |
| [Coding-skills calibration](sprints/coding-skills-calibration.md) | Reliable detector and Naur-theory judgments |
| [B+ quality gate](sprints/code-quality-gate.md) | Only calibrated >=87/100 package/report checks may block |
| [Safe tool execution](sprints/safe-tool-execution.md) | Least-privilege permissions and hostile-content defenses |

