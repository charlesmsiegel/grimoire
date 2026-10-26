# Roadmapping workspace

**DO NOT MERGE THIS BRANCH.** `roadmapping` is a permanent planning workspace, not an implementation branch. Ideas are promoted into **separate reviewed specs and implementation branches based on current `main`**. Never open a PR to merge `roadmapping`.

## Start here — October 26, 2026 reconciliation

- **[Execution roadmap](execution-roadmap.md)** — current status, enabling work, practical sequence, acceptance scenarios and definition of ready.
- **[Sprint register](sprint-register.md)** — **all 104 named workstreams/shipped baselines** from the thematic inventory, with source links and deliberate deferrals. Check this before starting a new sprint.
- **[Sprints](sprints.md)** — the thirteen thematic blocks, not a delivery checklist or fixed schedule.
- **[Releases](releases.md)** — provisional 0.2.x / 0.3.x / 1.0 player outcomes, not release promises.

The earlier GitHub issue backlog is cleared. Previously deferred features have **not** been implemented simply because the issues closed. The historic implementation roadmaps 01–12 are owner-confirmed landed; **Mechanics II is deliberately deferred to the main mechanics-improvement sprint**. Production Python package modularization and the VN/expressions core have since landed. The next engineering phase is **remaining technical-debt simplification, valid score baselines and skill/gate calibration**, not a rerun of those delivered projects.

## Product direction

Grimoire is a **general-purpose, local-first, fiction-first roleplaying engine**. Mechanics-free casual roleplay, including consensual adult ERP, remains first-class alongside rules-driven tabletop campaigns (D&D, World of Darkness, Ars Magica, PbtA).

**Compute what can be computed; use LLMs where they add value.** Authoritative facts, rules, identity, write scopes, legality and recoverable outcomes are explicit; models interpret ambiguity, make optional bounded decisions or proposals and narrate/voice established consequences.

The world's authored content is independent of its ruleset; campaigns choose **one mechanics module or `none`**. Preserve Markdown/frontmatter and the current human-editable JSON as authoritative files. Derived databases, caches and indexes must remain disposable unless a concrete superior roleplay experience justifies otherwise.

## Player-facing feature and long-term requirement documents

- [Inventory](inventory.md) — **near-term** mechanics-free quantities/holdings; later equipment, transfer, provenance, resolution and review. #167–171 retained.
- [Creature → Character](creature-to-character.md) — one reusable creature archetype produces unique persistent individuals; immediate creation on committed monster capture, defaults inherited until overridden.
- [Visual Novel & Expressions](visual-novel.md) — **shipped baseline** in PR #554 and October follow-ups; separate unresolved presentation candidates from already-delivered VN and outfit-expression libraries.
- [Campaign tuning](campaign-tuning.md) — difficulty, pacing, drama vs simulation, tone, lethality, pressure, spotlight and related controls.
- [Deferred systems](deferred-systems.md) — qualified character variants, spatial worlds, library activity, scene copying, time skips, long activities, optional replay, export refinements.
- [Discretionary backlog](backlog-discretionary.md) — multi-PC, optional live preview, remaining auxiliary generation, HUD customization; #151 and #190 may never need implementation.

## Architecture and engineering planning

- [Fiction-first architecture](architecture.md) — decisions, narration, outcomes and inference cost tiers.
- [Backend flexibility](backend-flexibility.md) — World Resolution, Content Identity, Mechanics Extensibility, State Transactions, Change Sets, agent runtime, and prioritized proof cases.
- [Content architecture study](content-architecture-study.md) — **open decision**: source-aware World view, library/DAG architecture, inheritance, provenance and migration constraints.
- [Content-composition discussion log](content-composition.md) — earlier debated alternatives; **do not** read early proposals as approved decisions.
- [File authority](file-authority.md) — editable file-backed source of truth and evaluation of read-only projections.
- [World maintenance](world-maintenance.md) — optional in-app world/cast/lore integrations through reviewable scoped writes, alongside external Codex/Claude agents.
- [Agentic workers](agentic-workers.md) — bounded tool-using jobs; optional alongside cheap decision models and single-shot narration.
- [Engineering health](engineering-health.md) — Naur Theory Building, intentional code simplification, types, tests, error handling, migration, concurrency and hygiene.
- [Quality PR gate](quality-pr-gate.md) — after cleanup and detector calibration, a **B+ (87/100) floor per applicable graded package/report**, including independently verified theory, not repo-average grade laundering.

## Historical backlog and provenance — retain, don't re-implement blindly

- [Backlog consolidation](backlog-consolidation.md) — historical two-wave issue-retirement plan, dispositions and why closures were not feature completion.
- [Archived issue text](archived-issue-text.md) — complete original text of **19 moved issues**, with dated alternatives preserved as history.
- [Discretionary backlog](backlog-discretionary.md) — uncommitted and optional goals to revisit only when a real player need arises.

**Every candidate is preserved in the [Sprint Register](sprint-register.md).** These documents are not implementation specs. When an actual sprint is selected, verify the then-current code, recover the subsystem theory, specify invariants/compatibility/acceptance and create an independently reviewed spec/branch. Update the register after each landing, distinguishing **shipped / partial / deferred / declined** work. `roadmapping` itself remains unmerged permanently.
