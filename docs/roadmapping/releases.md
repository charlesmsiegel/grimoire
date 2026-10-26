# Versions, milestones and 1.0

## Real 0.2.x context
The changes from 0.1.0 to 0.2.0 were deliberately broad rather than thematically tidy. The motivating user outcome was **being able to play casual/light mechanics-free roleplay**, including ERP, not delivering one subsystem.

Major 0.2.0 achievements reported by the project owner:
- Consistent, individual NPC voices and more deliberate speaker control.
- World and campaign context present in every prompt, enabling later tuning.
- Greater control over generation conditions and distinct models for distinct tasks.

Useful shorthand: **Playable Roleplay / Narrative Orchestration**. This is not an exhaustive Git-derived changelog. Current 0.2.0 is followed by iterative fixes/tweaks, perhaps through about 0.2.20 before the next feature family.

## October 26, 2026 — execution status, not a new version commitment

The backlog phase has finished (**zero open issues/PRs** on the checked repository), and substantial backend/front-end modularization, code hygiene and test-fixture deduplication have landed. The October 23 [VN/expressions](visual-novel.md) core is also shipped. The following pre-cleanup description is now **historical context**. See [Execution Roadmap](execution-roadmap.md) for what's still actually pending.

The owner confirmed that all earlier implementation roadmaps **01–12 landed**. **Mechanics II (13) is deferred** to the principal mechanics-improvement sprint; do not interpret the old roadmap checklist's schedule as current. A modest, independent **Inventory** MVP and the creature-to-character capture workflow remain candidates, not shipped features.

## Pre-cleanup backlog phase (2026-10-15 plan)

**Historical plan, completed:** Before the concentrated Engineering Health cleanup and later B+ PR-gate work, finish the small active backlog and the ten then-in-review PRs; move discretionary work into the roadmap rather than invent busywork. Inventory now has a **near-term mechanics-independent counts/holdings MVP** because it already improves fictional continuity in monster-catching campaigns; it need not wait for a D&D-style mechanics release. Close with verification and map delivered **capabilities** and residual **goals** to the roadmap; do not reschedule completed issues as new work. Defer features whose assumptions depend on the proposed content-identity/world resolution work. See [backlog-consolidation.md](backlog-consolidation.md). This is sequencing, **not** a new version-number commitment.

## Provisional player-outcome release themes
| Family | Goal |
| --- | --- |
| 0.2.x | Sustainable mechanics-free roleplay and orchestration |
| 0.3.x | Play with game rules and character mechanics |
| 0.4.x | Meet obstacles and face meaningful consequences |
| 0.5.x | Experience living, evolving NPCs and worlds |
| 0.6.x+ | Broader progression, campaigns, tuning and integration |
| 1.0 | Reliable, sustained, general-purpose roleplaying |

Do not treat version numbers as percentages or these themes as delivery promises. Some capabilities must develop in parallel; Context & Reliability and Generation Orchestration are continuous concerns.

**Updated planning direction (2026-10-15):** dedicated **Theory Building, Code Simplification and Engineering Health** work is valuable before or in parallel with the next backend extensibility and gameplay feature phase. A standalone cleanup release is acceptable; there is no requirement to invent new features to justify refactoring. Use [engineering-health.md](engineering-health.md) to prioritize validated code debt and avoid chasing a raw automated score.

**2026-10-15 revised possibility:** Before broad new gameplay features, prioritize a narrow **Backend Flexibility / Extensible Game Foundations** milestone: a source-aware World View and identity boundary, a thin mechanics extension contract, and a minimal Mechanics II Action/Effect vertical slice. This is an enabling phase *within* the next feature release, not a reason to build nine abstract platform frameworks before shipping mechanics. See [backend-flexibility.md](backend-flexibility.md). Release numbering and scope remain provisional.

## Transferred backlog features (not shipped)

The planned [Inventory](inventory.md) feature family, [architecture-dependent systems](deferred-systems.md), and [discretionary backlog](backlog-discretionary.md) have been transferred out of the immediate GitHub issue queue; none is presumed implemented. Basic counted inventory is now a near-term candidate. **#93 scene copy is deferred** pending campaign data storage choices; **#151 replay and #190 auxiliary accept routing may be unnecessary** and are not promised future features. Relevant goals include shared-item-aware inventory, world-spatial graphs, cross-world character variants, offscreen simulation, long activities, scene import, historical replay and richer export. Individual vertical slices may be viable before all of these dependencies land, but the old issue bodies are not accepted implementation contracts.

## Near-term mechanics-free inventory (planning update)

A small, opt-in holdings system is potentially a high-value minor release even without mechanics: track how many capture devices, potions and similar supplies a PC or party owns, and decrement/increment with validated operations. The first slice needs neither equipment-stat interpretation nor shared bestiary dependency resolution, and can fit before or after the engineering cleanup as sequencing dictates. Full inventory/gear mechanics, automatic reconciliation, and richer UI belong to later slices. See [inventory.md](inventory.md).

## Suggested 1.0 boundaries
**Needed in credible core form:** rules and adjudication for selected systems; character state/progression; Obstacles and Consequences; some NPC autonomy and world/time simulation; long-term continuity and campaign management; basic campaign tuning; import and usable UI as needed; reliability and measurable inference cost.

**Can plausibly follow after 1.0:** advanced crafting/economy across every system, procedural generation at scale, giant quest frameworks, multiplayer, voice/audio, rich image workflows, community marketplace and sharing. A specialized feature essential for a *supported* game (e.g. Ars Magica laboratory seasons) may nevertheless be needed before 1.0 even if universal Crafting waits.

Representative acceptance campaigns:
1. D&D: exploration, combat, resources, advancement.
2. World of Darkness: politics, supernatural powers, agendas, long consequences.
3. Ars Magica: seasonal covenant/laboratory play, troupe, magic and multi-year sagas.
4. PbtA: fiction-triggered moves, partial success, GM moves, playbooks.

Support concrete editions, not every published rules supplement. Mechanics-free roleplay remains a complete, intentional mode. Aim for multi-session games with no regular human GM intervention needed to repair continuity, resolve rules, construct challenges or maintain state. Benchmark cost per turn, latency, and violations of authoritative outcomes.
