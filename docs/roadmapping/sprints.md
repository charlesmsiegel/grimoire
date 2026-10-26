# Sprint inventory

These names are high-level candidates or shipped foundations, **not** an implementation order or individual version commitments. For the current execution sequence and independently traceable work list, see [Execution Roadmap](execution-roadmap.md) and [Sprint Register](sprint-register.md). The register must retain *every* entry in this table; feature names alone are not accepted implementation specs.

| Thematic block | Candidate sprints |
| --- | --- |
| Rules & Adjudication | Game Mechanics, Rulesets, Resolution, Combat, Homebrew Support, System Integration |
| Characters & Resources | Character Progression, [Creature → Character](creature-to-character.md), [Inventory MVP (near-term)](inventory.md), Equipment & Inventory Expansion, Economy, Crafting, Party Management |
| Challenges & Play | Obstacles, Encounter Design, Quests, Exploration, Travel, Information Discovery, Player Agency |
| People & Societies | Relationships, Social Simulation, Reputation, NPC Autonomy, Faction Dynamics, Time-Skip Simulation |
| Worlds & Time | Worldbuilding, World Simulation, Time Management, Long Activities, Location Management, Spatial World / Maps, Procedural Generation, World Maintenance |
| Stories & Campaigns | Narrative Arcs, Consequences, Scenario Design, Campaign Management, Cross-Campaign Scene Import, Long-Activity Resolution |
| Context & Reliability | Memory Consolidation, Knowledge Management, Agent Evaluation, Historical Turn Replay (optional / reassess), Quality Assurance, Performance Optimization, Library Activity Feed |
| Presentation & Participation | User Interface, UI/HUD Customization (later), Live Prompt Preview (optional), [Visual Novel & Expressions (landed core)](visual-novel.md), Visual Storytelling, Image Generation, Voice Roleplay, Audio Ambience, Accessibility, Multi-PC/Multiplayer (later) |
| Content Ecosystem | Content Import, Content Sharing, Export Refinement / Privacy, Community Marketplace |
| Campaign Tuning | Difficulty, Drama–Simulation, Pacing, Tone, Lethality, Plot Pressure, Scarcity, Spotlight, Adaptive Difficulty |
| Decision & Generation Systems | Decision Models, Agentic Workers, Auxiliary Generation Task Kinds (review residual), State Management, Generation Orchestration, Model Routing, Inference Economics |
| Backend & Extensibility | World Resolution, Content Identity, Mechanics Extensibility, State Transactions, Content Composition, Change Sets, Agentic Runtime, Campaign Policies, Module Packaging |
| Engineering Health | Theory Building, [Package Modularization (landed)](engineering-health.md), Code Simplification, Code Hygiene, Backend Refactoring, Type Safety, API Consistency, Error Handling, Test Hygiene, Dependency Hygiene, Dead Code, Documentation, Migration Safety, Concurrency Review, Observability, Security Review, Skill Calibration, Quality Gate |

The last four blocks contain cross-cutting architectures, policy and engineering-health concerns, not mutually exclusive features or new obligatory release-sized work. **Engineering Health** is worthwhile independently of immediate feature unlocks, and the first priority is understanding and simplifying accumulated AI-authored-code debt through [Theory Building](engineering-health.md). For proposed extensibility interfaces and dependencies, see [backend-flexibility.md](backend-flexibility.md). The intended post-cleanup PR policy is **B+ (87/100) or higher on every applicable graded package/report**, after reducing false positives and stabilizing the judge. See [quality-pr-gate.md](quality-pr-gate.md).

## Distinctions
- Obstacles create interesting problems. Resolution determines success or failure. Consequences persist outcomes. Encounter Design arranges coherent situations.
- NPC Autonomy determines motivation and behavior; narration expresses it.
- World Simulation generates causal offscreen developments; Narrative Arcs shapes dramatic emphasis.
- Rulesets are pluggable, not D&D checks by default. Mechanics-free play remains supported.
- Thematic blocks should not become a mandatory all-features-before-1.0 checklist.
- **Inventory #167–171**, nine architecture-dependent goals (including scene copy #93 and possibly-unneeded replay #151), and the other optional UI/auxiliary/multi-PC goals now live in [Inventory](inventory.md), [Deferred Systems](deferred-systems.md), and [Deferred Backlog Review](backlog-discretionary.md). **Their standalone issue trackers are retired; they are not implemented.** Inventory's basic quantities/holdings are a **near-term** candidate even without rules. [Full issue text](archived-issue-text.md) is retained for traceability.
- Cross-world character variants, spatial location graphs/maps, content provenance, scene transfers, historical replay, offscreen ticks, activity resolution, and export filtering should be specified against the eventual World View, Identity, State Transactions and mechanics contracts, not retrofitted twice.
- Character and item fiction must remain usable in **mechanics-free** campaigns. Inventory isn't synonymous with the mechanics engine; a near-term goal is to track capture-device/potion counts without a mechanics module.
- **Creature → Character:** one generic species/creature may yield many distinct individual actors. **Successful capture must immediately create (or reuse) the actor**, even for mechanics-free play; conversational significance can instead prompt individualization. Art/description/baseline traits inherit until independently overridden, while actual individual resources, memories and ownership are personal. Capture-device quantities stay in Inventory; captured monsters themselves are **characters, not inventory stacks**. This is a potential focused sprint and an Identity/World View/Mechanics Extensibility test, **not** a new GitHub backlog issue.
- **Backlog status corrected 2026-10-26:** the four formerly remaining implementation issues #62, #128, #208 and #437 were completed on October 16. The repo currently has **zero open issues and zero open PRs**; none of the deferred goals is thereby implemented. #151 and #190 remain discretionary and may not warrant feature work.
- **Engineering status:** all previously planned non-Mechanics-II roadmaps 01–12 are confirmed landed by the owner; Mechanics II is deferred to the primary mechanics-improvement sprint. Production Python modularization (37 packages) and the initial frontend/test-hygiene splits also landed. Further simplification and quality work remain valuable; do not run the same structural split again by habit. See [Execution Roadmap](execution-roadmap.md).
- **Visual Novel and Expressions:** the historical VN reader, actor expression libraries, outfits and overrides landed in PR #554 and later improvements. Treat that as a **delivered baseline**, with any *new* Visual Storytelling work justified separately. See [visual-novel.md](visual-novel.md).
