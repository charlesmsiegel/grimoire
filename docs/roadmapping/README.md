# Grimoire — future sprint proposals

This directory contains **what we might build or improve next**, not a changelog, retired issue queue or implementation checklist. Each linked file is an independently understandable **pre-spec sprint proposal**: intended outcome, work, a proving scenario, dependencies and **Open questions at the bottom**.

**Start from the player's or developer's outcome, not from a version number.** Select a proposal, decide its open questions, then create a reviewed spec and implementation branch from current main. The planning-only roadmapping branch is **never merged**.

## 1. Code quality and foundations

| Sprint proposal | What success means |
|---|---|
| [Engineering Health](sprints/engineering-health.md) | Simpler, safer code, fewer verified defects, stronger tests/types and clearer boundaries |
| [Skill Calibration](sprints/skill-calibration.md) | Reliable coding-skills detectors and Naur theory judgments without avoidable false positives |
| [Quality Gate](sprints/quality-gate.md) | After calibration, **B+ (87/100) per applicable graded package/report** becomes a dependable merge requirement |
| [World Resolution & Identity](sprints/world-resolution.md) | One source-aware effective-world interface, stable identity and migration-safe provenance |
| [Content Composition & Worldbuilding](sprints/content-composition.md) | Worlds reuse genuine shared catalogs and optional add-ons without duplicating or flooding prompts |
| [Mechanics & Rulesets](sprints/mechanics.md) | Real D&D, WoD, Ars Magica and PbtA semantics, including deferred **Mechanics II** actions/effects |
| [State Transactions](sprints/state-transactions.md) | Validated, recoverable, exactly-once committed game effects and file-backed state |

These are **not one strict dependency chain**: the World View and ruleset-contract experiments can run beside engineering cleanup. Do not create an abstract platform before testing it with player-facing vertical slices.

## 2. Small playable improvements

| Sprint proposal | What success means |
|---|---|
| [Inventory](sprints/inventory.md) | In freeform RP the PC has the correct number of capture balls, potions and other resources, with manual edits and no double spending |
| [Creature → Character](sprints/creature-to-character.md) | A goblin can become a named NPC; a captured monster immediately becomes a distinct actor inheriting species defaults |
| [Character Development & Party](sprints/character-development.md) | Individuals grow and can join/leave parties or reserves without losing identity or distinct state |
| [Challenges & Exploration](sprints/challenges.md) | Meaningful obstacles, multiple solutions, quests, travel and player agency with persistent outcomes |

Inventory's simple counted-holdings slice need **not** wait for full mechanics or a universal content-package manager.

## 3. Longer-running story and world systems

| Sprint proposal | What success means |
|---|---|
| [NPC Autonomy & Societies](sprints/societies.md) | Relationships, reputations, faction goals and believable NPC behavior |
| [World Simulation & Time](sprints/world-simulation.md) | Offscreen developments, calendars, time skips and long activities have coherent consequences |
| [Locations & Maps](sprints/spatial-world.md) | Source-aware spatial graph, neighborhood/path queries and optional authorable maps |
| [Narrative Design](sprints/storytelling.md) | Arcs, scenarios and consequences follow player choices without railroading |
| [Campaign Management](sprints/campaign-management.md) | Safe scene transfer/fork, portable history, refined anonymized/POV exports |
| [Context & Evaluation](sprints/context-evaluation.md) | Reliable memory, audience-aware retrieval, inspectable prompts and measured play quality |
| [Generation Systems](sprints/generation.md) | Bounded decisions, consistent narration, model routing, cost control and useful auxiliary tasks |
| [Agentic Workers](sprints/agentic-workers.md) | Optional safe multi-step investigation/authoring without making every turn an agent |
| [World Maintenance](sprints/world-maintenance.md) | Reviewable, source-scoped multi-file edits with history, conflicts and external-editor support |

## 4. Optional and later expansion

| Sprint proposal | What success means |
|---|---|
| [Presentation & Accessibility](sprints/presentation.md) | Better scene UI, visual storytelling/VN extensions, optional HUD and usable desktop/mobile controls |
| [Media — Image, Voice & Audio](sprints/media.md) | Optional generated art, character voices and sound without changing story authority |
| [Campaign Tuning](sprints/campaign-tuning.md) | Real, independent difficulty, pacing, drama, tone, lethality and adaptive-play controls |
| [Content Ecosystem](sprints/content-ecosystem.md) | Portable imports, sharing, package versioning, optional marketplace |
| [Multi-PC & Multiplayer](sprints/multi-pc.md) | Several properly attributed PC posts per turn; networked collaboration only when justified |
| [Procedural Generation](sprints/procedural-generation.md) | Reviewed, reusable worlds/places/encounters that respect established content |

These topics are retained **as candidates**, not 1.0 promises. Features such as a full marketplace, networked multiplayer, historical replay and universal auxiliary-result routing may never be worth building.

## Before promoting a sprint to a specification

- Read [Principles](principles.md) for immutable constraints: fiction-first, one rules module or none, Markdown/JSON authority, qualified identities, safe writes, modest inference cost and normal external editing.
- Use [Capability lookup](capabilities.md) to find a workstream by its older thematic name; every future-oriented label has a sprint home.
- Review the **Open questions at the bottom** of its sprint file. Answer significant choices or make an explicit testable research spike; don't silently settle them in code.
- Verify current main before proposing work. A delivered foundation is not an absent feature.
- Write concrete acceptance scenarios, compatibility/failure tests, interfaces, migration/rollback and an independent code-spec review.

The directory deliberately contains no issue histories, old PR lists or stale status snapshots. Those remain available through normal Git history without cluttering pre-spec browsing.
