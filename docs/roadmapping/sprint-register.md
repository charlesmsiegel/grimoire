# Sprint register — exhaustive, traceable candidate inventory

**Snapshot 2026-10-26.** This is the durable **index of every named thematic sprint**, including shipped foundations, exploratory candidates and deliberate deferrals. It was reconciled against [sprints.md](sprints.md) and the current `main`; this file **does not approve implementation** or create a promise to ship every line.

The register complements rather than replaces the full requirements in [execution-roadmap.md](execution-roadmap.md), the theme-specific design documents, and [the complete original issue text](archived-issue-text.md). A named category without a specification is **not** a ready-to-code ticket. When work is approved, write a focused spec on a new branch based on `main`, then update the relevant register entry with the actual landing commit and any remaining scope. **Never merge `roadmapping`.**

## Legend and priority

- **Delivered baseline:** a significant shipped slice exists; other features in the same subject may still be outstanding.
- **Immediate / Continuing:** current tech-debt wave or next engineering improvements; no guarantee all items are in one PR.
- **Early proof / Near-term:** an architecture experiment or small RP feature that should test a real interface before broad generalization.
- **Main mechanics:** belongs with deferred Mechanics II / mechanics improvement, not the stale 01–12 checklist.
- **Later / Optional:** an explicitly deferred goal or future idea; **optional** may legitimately never be implemented.
- **Candidate:** retained as a potential feature, but unsized, unscheduled and without approved acceptance criteria.

**Do not equate closed GitHub issues with shipped features.** The original backlog consolidation retired 19 trackers into this roadmap; original goals and dispositions remain linked below.

## Complete thematic roster

### Rules & Adjudication

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `RULE-01` | Game Mechanics | Main mechanics sprint; Mechanics II intentionally deferred | [Mechanics II scope](execution-roadmap.md#3-mechanics-improvement--including-deferred-mechanics-ii) |
| `RULE-02` | Rulesets | Main mechanics sprint | [Rulesets and one-module contract](backend-flexibility.md) |
| `RULE-03` | Resolution | Main mechanics / obstacle integration | [Rules and consequences](architecture.md) |
| `RULE-04` | Combat | Main mechanics / challenges | [Mechanics and release acceptance](releases.md) |
| `RULE-05` | Homebrew Support | Mechanics expansion | [Ruleset and module boundaries](backend-flexibility.md) |
| `RULE-06` | System Integration | Mechanics expansion | [File-backed game contracts](backend-flexibility.md) |

### Characters & Resources

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `CHAR-01` | Character Progression | Future gameplay candidate | [Inventory](inventory.md), [Creature → Character](creature-to-character.md) |
| `CHAR-02` | Creature → Character | Near-term proof / open design | [Individualization and capture](creature-to-character.md) |
| `CHAR-03` | Inventory MVP (near-term) | Near-term independent slice | [Mechanics-free holdings](inventory.md) |
| `CHAR-04` | Equipment & Inventory Expansion | Later after MVP | [Equipment, provenance, reconciliation](inventory.md) |
| `CHAR-05` | Economy | Later candidate; not immediate | [Inventory](inventory.md), [Creature → Character](creature-to-character.md) |
| `CHAR-06` | Crafting | Later candidate; not immediate | [Inventory](inventory.md), [Creature → Character](creature-to-character.md) |
| `CHAR-07` | Party Management | Future gameplay candidate | [Inventory](inventory.md), [Creature → Character](creature-to-character.md) |

### Challenges & Play

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `PLAY-01` | Obstacles | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-02` | Encounter Design | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-03` | Quests | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-04` | Exploration | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-05` | Travel | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-06` | Information Discovery | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |
| `PLAY-07` | Player Agency | Future gameplay candidate | [Fiction-first architecture](architecture.md), [Releases](releases.md) |

### People & Societies

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `NPC-01` | Relationships | Future living-world candidate | [Simulation requirements](deferred-systems.md), [Architecture](architecture.md) |
| `NPC-02` | Social Simulation | Future living-world candidate | [Simulation requirements](deferred-systems.md), [Architecture](architecture.md) |
| `NPC-03` | Reputation | Future living-world candidate | [Simulation requirements](deferred-systems.md), [Architecture](architecture.md) |
| `NPC-04` | NPC Autonomy | Later candidate; not immediate | [Simulation requirements](deferred-systems.md), [Architecture](architecture.md) |
| `NPC-05` | Faction Dynamics | Later candidate; not immediate | [Simulation requirements](deferred-systems.md), [Architecture](architecture.md) |
| `NPC-06` | Time-Skip Simulation | Deferred dependent | [NPC/group ticks #102](deferred-systems.md) |

### Worlds & Time

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `WORLD-01` | Worldbuilding | Future world/time candidate | [Content architecture](content-architecture-study.md), [Deferred](deferred-systems.md) |
| `WORLD-02` | World Simulation | Later living-world outcomes | [Offscreen causality](deferred-systems.md) |
| `WORLD-03` | Time Management | Future world/time candidate | [Content architecture](content-architecture-study.md), [Deferred](deferred-systems.md) |
| `WORLD-04` | Long Activities | Deferred dependent | [Long-activity #108](deferred-systems.md) |
| `WORLD-05` | Location Management | Future world/time candidate | [Content architecture](content-architecture-study.md), [Deferred](deferred-systems.md) |
| `WORLD-06` | Spatial World / Maps | Deferred on source identity | [Location graph and map #42/#43](deferred-systems.md) |
| `WORLD-07` | Procedural Generation | Later candidate; not immediate | [Content architecture](content-architecture-study.md), [Deferred](deferred-systems.md) |
| `WORLD-08` | World Maintenance | Later read/write workflow | [Optional in-app editing](world-maintenance.md) |

### Stories & Campaigns

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `STORY-01` | Narrative Arcs | Future narrative/campaign candidate | [Campaigns and transferred goals](deferred-systems.md), [Releases](releases.md) |
| `STORY-02` | Consequences | Future narrative/campaign candidate | [Campaigns and transferred goals](deferred-systems.md), [Releases](releases.md) |
| `STORY-03` | Scenario Design | Future narrative/campaign candidate | [Campaigns and transferred goals](deferred-systems.md), [Releases](releases.md) |
| `STORY-04` | Campaign Management | Future narrative/campaign candidate | [Campaigns and transferred goals](deferred-systems.md), [Releases](releases.md) |
| `STORY-05` | Cross-Campaign Scene Import | Deferred pending campaign state model | [Scene copy #93](deferred-systems.md) |
| `STORY-06` | Long-Activity Resolution | Deferred dependent | [Long-activity #108](deferred-systems.md) |

### Context & Reliability

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `CTX-01` | Memory Consolidation | Continuous reliability / later expansion | [Agentic workers](agentic-workers.md), [Deferred features](deferred-systems.md) |
| `CTX-02` | Knowledge Management | Continuous reliability / later expansion | [Agentic workers](agentic-workers.md), [Deferred features](deferred-systems.md) |
| `CTX-03` | Agent Evaluation | Continuous reliability / later expansion | [Agentic workers](agentic-workers.md), [Deferred features](deferred-systems.md) |
| `CTX-04` | Historical Turn Replay (optional / reassess) | Optional; may not ship | [Replay #151](backlog-discretionary.md) |
| `CTX-05` | Quality Assurance | Continuous reliability / later expansion | [Agentic workers](agentic-workers.md), [Deferred features](deferred-systems.md) |
| `CTX-06` | Performance Optimization | Continuous reliability / later expansion | [Agentic workers](agentic-workers.md), [Deferred features](deferred-systems.md) |
| `CTX-07` | Library Activity Feed | Deferred after change provenance | [Library activity #55](deferred-systems.md) |

### Presentation & Participation

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `UX-01` | User Interface | Later presentation candidate | [VN and expressions](visual-novel.md), [Deferred UI](backlog-discretionary.md) |
| `UX-02` | UI/HUD Customization (later) | Deferred UI work | [HUD #196](backlog-discretionary.md) |
| `UX-03` | Live Prompt Preview (optional) | Optional UI; low priority | [Preview #132](backlog-discretionary.md) |
| `UX-04` | Visual Novel & Expressions (landed core) | Delivered; extension only after gap proof | [VN shipped PR #554](visual-novel.md) |
| `UX-05` | Visual Storytelling | Expanded presentation candidate, core VN delivered | [VN baseline and options](visual-novel.md) |
| `UX-06` | Image Generation | Later presentation candidate | [VN and expressions](visual-novel.md), [Deferred UI](backlog-discretionary.md) |
| `UX-07` | Voice Roleplay | Later candidate; not immediate | [VN and expressions](visual-novel.md), [Deferred UI](backlog-discretionary.md) |
| `UX-08` | Audio Ambience | Later candidate; not immediate | [VN and expressions](visual-novel.md), [Deferred UI](backlog-discretionary.md) |
| `UX-09` | Accessibility | Later presentation candidate | [VN and expressions](visual-novel.md), [Deferred UI](backlog-discretionary.md) |
| `UX-10` | Multi-PC/Multiplayer (later) | Deferred; multi-PC ≠ network multiplayer | [Multi-PC #81](backlog-discretionary.md) |

### Content Ecosystem

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `CONTENT-01` | Content Import | Later content ecosystem candidate | [Content composition](content-composition.md), [Architecture study](content-architecture-study.md) |
| `CONTENT-02` | Content Sharing | Later candidate; not immediate | [Content composition](content-composition.md), [Architecture study](content-architecture-study.md) |
| `CONTENT-03` | Export Refinement / Privacy | Deferred; optional slices | [Export #140](deferred-systems.md) |
| `CONTENT-04` | Community Marketplace | Later candidate; not immediate | [Content composition](content-composition.md), [Architecture study](content-architecture-study.md) |

### Campaign Tuning

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `TUNE-01` | Difficulty | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-02` | Drama–Simulation | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-03` | Pacing | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-04` | Tone | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-05` | Lethality | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-06` | Plot Pressure | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-07` | Scarcity | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-08` | Spotlight | Later policy/first consumer | [Campaign tuning](campaign-tuning.md) |
| `TUNE-09` | Adaptive Difficulty | Later candidate; not immediate | [Campaign tuning](campaign-tuning.md) |

### Decision & Generation Systems

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `DEC-01` | Decision Models | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |
| `DEC-02` | Agentic Workers | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |
| `DEC-03` | Auxiliary Generation Task Kinds (review residual) | Partial framework landed; audit remaining | [Auxiliary #188](backlog-discretionary.md) |
| `DEC-04` | State Management | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |
| `DEC-05` | Generation Orchestration | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |
| `DEC-06` | Model Routing | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |
| `DEC-07` | Inference Economics | Incremental on existing foundation | [Architecture](architecture.md), [Agentic workers](agentic-workers.md) |

### Backend & Extensibility

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `BACK-01` | World Resolution | Early architecture decision | [Effective World facade](content-architecture-study.md) |
| `BACK-02` | Content Identity | Early architecture decision | [Qualified identities, provenance](content-architecture-study.md) |
| `BACK-03` | Mechanics Extensibility | Early boundary proof | [Contrasting-system ruleset tests](backend-flexibility.md) |
| `BACK-04` | State Transactions | Mechanics II vertical first | [Reuse proposed ledger; no competing transaction system](backend-flexibility.md) |
| `BACK-05` | Content Composition | Early proof, architecture unselected | [Composition alternatives](content-architecture-study.md) |
| `BACK-06` | Change Sets | After qualified identity / provenance | [Reviewable file-scoped edits](world-maintenance.md) |
| `BACK-07` | Agentic Runtime | Incremental on landed loops | [Optional bounded jobs](agentic-workers.md) |
| `BACK-08` | Campaign Policies | Incremental with first consumer | [Typed campaign knobs](campaign-tuning.md) |
| `BACK-09` | Module Packaging | Later, after real content imports | [Versioning and distribution](backend-flexibility.md) |

### Engineering Health

| ID | Sprint / capability | Current posture | Primary source |
|---|---|---|---|
| `HEALTH-01` | Theory Building | Immediate engineering health | [Naur-based theory review](engineering-health.md) |
| `HEALTH-02` | Package Modularization (landed) | Delivered production Python pass | [37-package validation](../engineering/backend-package-validation.md) |
| `HEALTH-03` | Code Simplification | Immediate engineering health | [Behavior-preserving code debt](engineering-health.md) |
| `HEALTH-04` | Code Hygiene | Continuing | [Naming, unnecessary complexity, errors](engineering-health.md) |
| `HEALTH-05` | Backend Refactoring | Continuing; package split already shipped | [Code debt; not re-split by size](engineering-health.md) |
| `HEALTH-06` | Type Safety | Continuing lint/type cleanup | [Type and lint ratchets](engineering-health.md) |
| `HEALTH-07` | API Consistency | Continuing | [Store / route / client contract ownership](engineering-health.md) |
| `HEALTH-08` | Error Handling | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-09` | Test Hygiene | Continuing; some fixture consolidation landed | [Characterization/coverage/cost](engineering-health.md) |
| `HEALTH-10` | Dependency Hygiene | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-11` | Dead Code | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-12` | Documentation | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-13` | Migration Safety | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-14` | Concurrency Review | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-15` | Observability | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-16` | Security Review | Continuing engineering health | [Engineering health](engineering-health.md), [Quality gate](quality-pr-gate.md) |
| `HEALTH-17` | Skill Calibration | After cleanup; report-only first | [False-positive calibration](quality-pr-gate.md) |
| `HEALTH-18` | Quality Gate | Last stage; not currently enforced | [Every applicable grade >=B+ 87](quality-pr-gate.md) |

## Backlog source coverage — 19 retired issue trackers

| Historical source | Current roadmap home | Disposition |
|---|---|---|
| #167–171 (5 inventory issues) | [Inventory](inventory.md); `CHAR-03`, `CHAR-04` | Requirements active; simple counted holdings near-term; full equipment later |
| #42, #43 (spatial), #50 (variants), #55 (activity) | [Deferred systems](deferred-systems.md); `WORLD-07`, `BACK-01`, `BACK-02`, `CTX-07` | Deferred on source-qualified World View/provenance |
| #93, #102, #108, #140 | [Deferred systems](deferred-systems.md); `STORY-05`, `NPC-06`, `WORLD-04`, `CONTENT-03` | Deferred, not implemented |
| #151, #190 | [Discretionary backlog](backlog-discretionary.md); `CTX-04`, `DEC-03` | Explicitly possibly unnecessary; not promised |
| #81, #132, #188, #196 | [Discretionary backlog](backlog-discretionary.md); `UX-10`, `UX-03`, `DEC-03`, `UX-02` | Deferred / partial framework as described, not a required 1.0 checklist |

**Audit count:** 104 candidate/foundation labels across 13 thematic blocks. The original [sprints.md](sprints.md) is the canonical ordered thematic list. This register must remain **exhaustive** with that list; don't remove a row because a feature was implemented—change its status to delivered baseline and preserve genuinely remaining goals.

## Cross-cutting commitments that must be checked for every approved sprint

1. **Readable ownership:** Markdown/frontmatter and existing editable JSON remain authoritative; database projections/indices are rebuildable. Users may hand-edit files or use external agents.
2. **Independent rulesets:** a campaign selects one mechanics module or `none`; no required d20 checks for fiction-first play. Mechanics II belongs to a later dedicated mechanics improvement phase.
3. **Explicit effective world and provenance:** don't conflate source definitions, campaign instances, actor versions, art, imported overlays or similar names.
4. **Deterministic state authority:** model judgments can propose, decide or narrate, but validated store mutators own legal effects, recovery, idempotency, undo and scope.
5. **Decision-model economics:** inference should be bounded, metered and only used when it adds value. Agentic workers are optional and must not impose overhead on ordinary chat.
6. **Backward compatibility and platform safety:** preserve existing worlds/campaigns, voice and prompt behavior, Android/Pydantic compatibility, and safe migration/fork/export.
7. **Testing and maintainability:** file counts or B+ scores are not goals by themselves; characterize existing behavior, recover the domain theory, and keep the existing ratchets/CI gates.
8. **No speculative implementation:** entries marked candidate/optional need a motivating user outcome, consumer, acceptance scenario, clear dependencies and separate reviewed spec before code.

## Status-change process

When a sprint is selected: (a) reconcile the current `main` code and tests; (b) write a brief theory and scope statement; (c) select realistic representative scenarios and failure cases; (d) check earlier archived requirements/notes; (e) create a separate spec/implementation branch; (f) after merging, annotate the delivered baseline and any residual here and in the execution roadmap.

**Explicitly unselected decisions:** referenced content libraries vs more general DAG; virtual vs compiled read-only World facade; source-version/pin/override policy; exact typed policy clients; cross-kind creature/art/stat fallback implementation; transaction reuse beyond Mechanics II; which optional replay, HUD, auxiliary and marketplace features deserve eventual work. These are **questions to investigate**, not omissions.
