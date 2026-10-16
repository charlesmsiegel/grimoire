# Roadmapping workspace

**DO NOT MERGE THIS BRANCH.** The roadmapping branch is a persistent planning workspace, not an implementation branch. Promoted work becomes separate specifications and then separate implementation branches based on main.

## Product direction
Grimoire is a general-purpose fiction-first roleplaying engine. Mechanics-free casual roleplay, including consensual adult ERP, remains a first-class mode alongside future rules-driven tabletop campaigns (D&D, World of Darkness, Ars Magica, PbtA).

Design maxim: **compute what can be computed; use LLMs where they add value**. Use explicit state and decision systems for authoritative facts, policy, rules and outcomes; LLMs interpret ambiguous player actions, propose open-ended possibilities, and narrate or voice results.

## Planning documents
- [Sprints](sprints.md): candidate workstreams grouped by theme
- [Inventory](inventory.md): near-term mechanics-free counted holdings MVP, then item identity, operations and richer UI — **not yet implemented**
- [Deferred systems](deferred-systems.md): long-term world/spatial/simulation/export goals plus deferred #93 (scene import) and optional #151 (replay)
- [Archived issue text](archived-issue-text.md): full original bodies of 19 reviewed issue trackers transferred to planning, including dated suggestions
- [Deferred backlog review](backlog-discretionary.md): seven optional/long-term items removed from active queue; four independent issues kept for implementation
- [Architecture](architecture.md): decisions, resolution, narration and inference economics
- [Campaign tuning](campaign-tuning.md): dials and optional behavior
- [Releases](releases.md): 0.2.x retrospective and provisional 1.0 boundary
- [Content architecture study](content-architecture-study.md): **open decision**, code-grounded alternatives and migration risks
- [Backend flexibility](backend-flexibility.md): prioritized enabling sprints, what's already implemented, and proposed dependency order
- [Backlog consolidation](backlog-consolidation.md): land selected non-inventory/non-mechanics backlog, close verified issues, reconcile product goals before cleanup
- [Engineering health](engineering-health.md): AI-authored debt, Theory Building, code hygiene, measured cleanup sprints
- [Quality PR gate](quality-pr-gate.md): post-cleanup B+ minimum, skill accuracy, reproducible Naur judging, possible Grimoire-specific copies

Ideas here are not approved specs. Break them down into individually reviewable requirements, acceptance tests, and engineering specifications on other branches. Keep this planning branch unmerged permanently.

- [File authority](file-authority.md): Markdown/JSON user-editable source of truth, derived databases only, reasons to reconsider
- [World maintenance](world-maintenance.md): optionally bring external card/lore/group integration workflows into Grimoire through guarded, reviewable changes
- [Agentic workers](agentic-workers.md): shared multi-step worker infrastructure alongside decision and narrative models
