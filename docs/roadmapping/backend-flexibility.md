# Backend flexibility and extensibility — proposed priority

**Status:** Proposed ordering and engineering research agenda, **not** approved specs or release commitments.

**Prerequisite sequencing preference:** land selected stable non-inventory/non-mechanics backlog PRs first, then reconcile their delivered goals and baseline the code for the cleanup wave. Avoid landing architecture-coupled backlog work that locks in the world-identity or mechanic design before the intended spikes. See [backlog-consolidation.md](backlog-consolidation.md).
**Date:** 2026-10-15.
**Branch rule:** `roadmapping` is never merged; implementation specs and PRs branch from current `main`.

## Thesis

Before building many independent gameplay systems, establish **just enough reusable backend contracts** that mechanics, world composition, obstacles, simulated world state and in-app maintenance can work through the same authoritative model. Front-load *expensive-to-reverse interfaces*, **not** a giant speculative platform rewrite.

**Parallel early sprint: Theory Building and Code Simplification.** There is substantial technical debt from AI/agent-authored code. Behavior-preserving refactors and code hygiene have independent value—even without unlocking two features. Before proliferating new abstractions, use [Engineering Health](engineering-health.md) to re-establish the actual theory of existing world state, mechanics and agent routing; review historical code-overview health/theory findings; and simplify validated high-churn hotspots. A clean theoretical boundary can be more important than building a new interface immediately.

Grimoire's file-based Markdown/frontmatter and existing editable JSON records remain authoritative; indexes, SQLite and compiled world projections may be disposable accelerators only. Mechanics-free play must remain first-class. Worlds recommend mechanics; a campaign selects one system or none.

## Existing foundations (do not reimplement)

Code / repository review on main:

- `store/routing.py`, `inference.py`, `tool_calls.py`: task-based inference, structured decisions, sampling, tool loops, budgets and cost attribution. The current `ROADMAP-CHECKLIST.md` marks 01/01a–01i/02 and 01g tool calling landed.
- `routes/investigation.py` and `store/investigation/`: early bounded multi-step investigators; 12 is **not** fully landed and read/write capabilities must remain carefully separated.
- `store/overlay.py`, `store/sync.py`: effective campaign-over-world copy-on-write, tombstones, detachments, promotion, and incoming conflict review. The code assumes *one physical world root* and `kind/id` identities within that view.
- `store/modules/pack.py`, `binding.py`, `sheets/`, `checks.py`, `dice.py`: declarative mechanic packs, optional content templates, world default and campaign override of one mechanics system (including `none`).
- The existing `docs/superpowers/specs/2026-10-09-roadmap-13-mechanics-ii-actions-effects-design.md` designs Action legality, an Effect DSL, deterministic sampled NPC action choices, and a recoverable **file-backed mechanics transaction ledger**. This is not yet landed. Do not design another competing transaction implementation.
- `store/compiled.py` and `docs/store-guarantees.md`: derived SQLite, not an authoritative database.

The above is a focused code review, not a full architecture audit.

## Enabling sprint candidates

| Priority | Sprint (1–2 words) | Capability | Status / recommended scope |
|---|---|---|---|
| 1 | **World Resolution** | A single effective-world read/query interface independent of whether data is local, imported, or inherited | High-priority narrow interface + compatibility proof; avoid rewriting all consumers at once |
| 2 | **Content Identity** | Source-qualified IDs, provenance, aliases, explicit relation/override targets, version/reference policy | Co-design with World Resolution; preserve existing IDs through compatibility mapping |
| 3 | **Mechanics Extensibility** | A ruleset-neutral contract for legality, triggering, resolution, effects and optional rule-specific capabilities | Thin contract tests against contrasting systems; build on current modules/Mechanics II, not an unbounded extension engine |
| 4 | **State Transactions** | Durable, replayable changes across mechanical and eventually world state | Reuse existing Mechanics II ledger plan and learn from one vertical slice; generalize only with a second concrete consumer |
| 5 | **Content Composition** | Shared bestiaries/equipment and optional additions under one world façade | Architecture spike: DAG vs simple libraries; virtual vs compiled effective view; make decision before pervasive reliance |
| 6 | **Change Sets** | Typed, scoped, validated, reviewable multi-record edit proposals with stale-write checks | Initially for World Maintenance and Content Import, after provenance/targeting; no speculative universal transaction manager |
| 7 | **Agentic Runtime** | Common job/tool orchestration, capabilities, budgets, traces, cancellation and resumability | Extend existing 01g + roadmap 12 pilots; reading can mature earlier; editing requires Change Sets |
| 8 | **Campaign Policies** | Typed campaign-local knobs governing challenge, simulation, autonomy and narration | Small policy/consumer interface; use existing context and routing, don't generalize far ahead of consumers |
| 9 | **Module Packaging** | Reusable registries, dependencies, updates, versions, independently owned repositories | Defer rich package manager/marketplace until cross-repo consumption works end to end |

Several names *refine existing thematic buckets*, not additions to nine totally new large workstreams. E.g. World Resolution/Content Identity/Content Composition support Worldbuilding/Content Import/State Management; Mechanics Extensibility supports Rulesets and System Integration; Agentic Runtime extends Agentic Workers and Generation Orchestration.

## Proposed dependency shape

```
                 +--> Mechanics Extensibility --> Mechanics II Action/Effect pilot
                 |                                    |
Existing 0.2.x --+--> World Resolution <--> Content Identity   |
 foundations     |           |                      |          |
                 |           +--> Content Composition spike    |
                 |                         |                    |
                 |                         +--> World Maintenance + Change Sets
                 |                                              ^
                 +--> Existing Tool Loops --> Agentic Runtime --+

Mechanics II transaction pilot --> reusable State Transactions contract
                                     (only after cross-feature evidence)

Typed Campaign Policies can progress in parallel once first consumers are chosen.
Module Packaging can follow when world/content/module reuse is proven.
```

This diagram is a planning dependency hypothesis, not a strict build graph. Identity and view contracts are co-designed. A world-composition **prototype** should happen early, but wholesale adoption waits for migration evidence.

## What should genuinely precede gameplay sprints?

**Before a large wave of mechanics, obstacles, autonomous worlds and world-maintenance features:**
- Establish **World Resolution + Content Identity** boundary, at least a stable source-aware read interface and a migration-compatible design. Prove a sample imported record cannot collide with or silently replace a local record.
- Establish the **minimum mechanics extension contract** by mapping the same fictional action into several different kinds of RPG resolution, *including fiction-triggered PbtA moves and seasonal Ars Magica mechanics*. Do not prematurely require arbitrary plugin code or a universal check schema.
- Validate a **recoverable state mutation path** through the existing Mechanics II Action/Effect slice. It already provides a concrete transaction design.
- Keep the existing **decision, model routing, and tool-call** foundation. Focus only on missing consumer-facing hooks.

**Can arrive incrementally once those contracts exist:**
- Full world import DAG and optional Chub-like card collection composition.
- Generalized reviewable Change Sets and write-capable World Maintenance agentic workers.
- Comprehensive campaign tuning, full plugin packaging/registry, marketplace, arbitrary world simulation.

**Parallelizable work:** A World Resolution/Identity architecture spike; real-rulebook contract comparison; read-only agentic investigation hardening; evaluation of regression/cost/continuity. Avoid blocking unrelated gameplay improvements on unfinished abstract layers.

## Thin vertical slices to prevent speculative architecture

### A. World contract spike
- Existing campaign accesses one logical World interface; reads local, inherited, or imported entries through the same `get`/`list`/`search` seam.
- Read-only pilot with one invented reusable bestiary and an invented setting World. Preserve existing `overlay` semantics, actors, images, sync and asset paths.
- Detect collisions with source-qualified identity, while legacy `kind/id` continues to work.
- Compare lazy virtual lookup vs compiled read-only effective world. Any compiled projection rebuilds entirely from authoritative Markdown/JSON.
- Test a changed imported file, a missing dependency, an override and a mechanics-free campaign.

### A2. Creature → Character boundary proof

Use [Creature → Character](creature-to-character.md) as a compact consumer test for World View and Mechanics Extensibility. A reusable campaign-visible creature record should be able to produce multiple distinct, campaign-owned NPCs. Starting descriptions/art and, when compatible, a stat baseline derive from the creature, but personal voice, history, current HP and progression belong to each actor. The current sheet writer rejects a creature-kind sheet type assigned to a character; devise explicit compatible mapping rather than bypassing validation. One-time art reuse is supported, while *live cross-kind fallback* remains an open design question. Keep a modest instantiation feature separate from a full inheritance framework.

### B. Mechanics contract spike
- Take **actual** sample resolution flows from contrasting rulesets: a D&D attack, WoD contested action, Ars Magica seasonal/magical action, PbtA move triggered by the fiction.
- Determine which can use the current declarative module API and proposed Mechanics II Action/Effect DSL as-is; mark any real unsupported capability.
- Add hooks only when the contrasting tests demonstrate the necessity. One campaign still binds one mechanics module or `none`.

### C. Mutation proof
- Implement/validate the smallest part of the planned Mechanics II transaction ledger that gives durable roll/effect results and recovery with Markdown/JSON authority.
- Later apply similar guard/changeset principles to world maintenance *if the duplication is real*, rather than forcing world edits into a mechanics-specific ledger prematurely.

## Go/no-go criteria for *new abstraction* work (not routine cleanup)

An enabling **new abstraction** earns early priority if it:
1. Removes a known concrete impediment for at least two subsequent gameplay/authoring features, **or** protects a difficult-to-migrate persistence/identity guarantee.
2. Can be tested through existing user workflows and small sample worlds.
3. Keeps existing 0.2.x mechanics-free play, provider routing, cost limits and content intact.
4. Does not require a canonical database or a one-shot migration of every world.
5. Makes the new *contract* stable while allowing backend implementation changes later.

Reject early framework work that exists only to future-proof hypothetical features. **Use vertical slices to discover the right interface.** This gate does *not* apply to cleanup: refactoring, type hygiene, API consistency and fixing genuine duplication are worthwhile for correctness, comprehension and iteration costs, as described in [engineering-health.md](engineering-health.md).

## Versioning suggestion (not an agreed release schedule)

Current 0.2.x remains **Playable Roleplay / Narrative Orchestration**. The next significant feature family could be titled **Extensible Game Foundations**, containing World Resolution/Identity and Mechanics contract work *plus a small actual gameplay slice*. Do not require all nine workstreams before advancing to playable mechanics. Subsequent minor releases can continue mechanics, obstacles, simulation and agentic maintenance in whichever dependency order proves practical.

## Things we have not decided

- Do World and Content Library share an underlying storage type, or only a resolved-read interface?
- Do content dependencies support arbitrary recursive imports or a constrained graph?
- When is local content an override vs a variant vs a distinct identity?
- Is the world view virtual, compiled, or both?
- How do updates affect a live campaign?
- What limited rule-specific executable hooks (if any) are necessary?
- Is a common change-set journal justified outside mechanics?

These questions belong in targeted spikes and later standalone specs, not assumed answers.


## Planned later merge gate

After the Engineering Health cleanup wave, improve and calibrate the owner's code-overview/theory-building skills and make B+ (>=87) the minimum for every applicable graded package/report on Grimoire PRs. Generic detector fixes can land upstream in coding-skills; Grimoire-specific skill variants are acceptable with explicit provenance and tests. Details: [quality-pr-gate.md](quality-pr-gate.md).
