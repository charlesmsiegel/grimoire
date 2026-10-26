# Execution roadmap — decision and handoff map

**Snapshot:** 2026-10-26, reviewed against Grimoire `main` at `f290a7a9a6ea9b08b86f083ade7e5848b05add04`.  
**Status:** planning and reconciliation, **not an approved sprint-by-sprint delivery schedule**.
**Authority:** thematic inventory [sprints.md](sprints.md); exhaustive [sprint register](sprint-register.md); designs and archived issue text linked below.  
**Branch rule:** `roadmapping` is a permanent, **never-merge** planning workspace. Actual specifications, implementations and PRs start separately from current `main`.

## Current handoff status — don't repeat completed work

- The two-wave October backlog consolidation succeeded. GitHub currently returns **zero open issues and zero open PRs**; previously deferred goals still exist in the roadmap, and closure as `not_planned` is **not** delivery.
- Per the project owner, **roadmap specs 01–12 and related earlier non-mechanics work landed**. The old `ROADMAP-CHECKLIST.md` was stale and is no longer present on `main`. **Mechanics II (spec 13) is intentionally deferred to the principal mechanics-improvement sprint**, not an active pre-cleanup blocker.
- Production backend package modularization is **landed**: see `docs/engineering/backend-package-theory.md` and `backend-package-validation.md` on `main`. That validation recorded **37 converted packages and no production Python file above 1,000 lines** at the time of its audit. This is a completed foundation, **not** a request to re-split all 37 subsystems.
- Subsequent frontend/client/type modularization and shared test fixture cleanup also landed; re-evaluate residual debt on today's source, not on the oversized October 19 file list.
- **Visual Novel & Expressions landed** in [PR #554](https://github.com/charlesmsiegel/grimoire/pull/554) and later October commits; preserve further opportunities separately in [visual-novel.md](visual-novel.md). Do not plan a second implementation of a feature already present.
- The repo's checked-in `code-overview` package map has expanded to **12 evaluated units** (generated October 21, 2026). The generated scores are **a dated baseline**, not current grades after October 24–26 VN changes.

## Engineering Health — immediate continuation lane

The existing cleanup and ratchet effort has paid down substantial AI-authored technical debt. Continuing it is valuable on its own, not only because it unlocks features.

| Work slice | What remains to establish | Evidence / completion gate | Related plan |
|---|---|---|---|
| **Theory Audit** | Reconstruct the actual domain theory of high-churn/intricate subsystems, and rehearse plausible next requirements instead of rewarding cosmetic line splits | Reviewed source evidence, conceptual boundaries, reason for the current model, at least three change-rehearsal outcomes | [Engineering Health](engineering-health.md) |
| **Code Simplification** | Reduce remaining accidental complexity, duplicate variants, unnecessary layers and ambiguous ownership | Representative changes become clearer/smaller; original semantics, route order and store invariants remain pinned | [Engineering Health](engineering-health.md) |
| **API Consistency** | Identify route/store/client contracts with duplicated or mismatched validation, errors and state transitions | A testable single owner; compatible endpoints, error taxonomy and source-of-truth semantics | [Engineering Health](engineering-health.md) |
| **Type & Lint Debt** | Pay down verified Ruff, mypy and ESLint findings, especially type/error/async issues requiring thought | Rules stay enabled; each decrease regenerates the ratchet baseline; no inflated baseline counts | [Engineering Health](engineering-health.md) |
| **Test Hygiene** | Improve diagnostics, fixtures, test architecture, regression/compatibility coverage and fast developer loops | Do not delete distinct failure cases to improve file size or speed; preserve Android/Pydantic 1 and full-suite behavior | [Engineering Health](engineering-health.md) |
| **Correctness Hardening** | Tighten recovery, lock order, migration/undo, derived cache lifecycle, prompt privacy, import data and agent-write trust boundaries | Characterization and fault-injection coverage; authoritative files never silently lost or overwritten | [Engineering Health](engineering-health.md) |
| **Documentation & Dead Code** | Retire outdated source-path claims, unused shims and duplicated guidance after behavior is characterized | Documentation matches actual API/ownership; deleted code proven unreachable, not merely unreferenced by static tools | [Engineering Health](engineering-health.md) |

**Measured status, not promises:** on current `main`, the committed lint baselines total **590 Ruff / 132 mypy / 280 ESLint findings** (2026-10-26). These are existing ratcheted findings, not new CI regressions. A significant fraction may need real refactors rather than mechanically safe autofix.

The **October 21** code-overview Health report scores were: Store 87.2 B+, API 89.2 B+, Providers/Prompts 87.1 B+, backend tests 92.1 A−, Decisions 95.9 A, Tool Calls 93.2 A; below proposed B+ threshold: Frontend 82.2 B−, Evals 84.0 B, Inference 85.0 B, LLM 84.8 B. Scripts and Android remained ungraded. **Do not** infer a pass/fail today from those pre-VN snapshots or substitute Health scores for Naur Theory scores. Some static candidates are false positives.

### Quality-gate follow-on (NOT active now)

1. **Skill Calibration:** correct genuine generic `coding-skills` detector mistakes upstream; create explicit Grimoire profiles or pinned forks only for intentional project-specific invariants, with false-positive regression fixtures. Improve measurement labeling of Grimoire's synthetic and generated evals, and mapping of packages with multiple file roots.
2. **Shadow Gate:** report-only CI of the actual `code-overview` health and independent, cited Naur Theory panels; pin scoring/prompt/model revisions; repeat unchanged SHA evaluations to demonstrate stability; mark failed/missing detectors **unknown**, not passing.
3. **Quality Gate:** enforce **B+ (>=87) on each applicable graded package/report**, not just the repository average, **once** baseline and judging reliability are sufficient. Keep existing lint ratchets, CI tests, Codex review and strict untrusted-PR credential isolation.

No arbitrary model judgment should randomly block an unchanged commit. See [quality-pr-gate.md](quality-pr-gate.md).

## Architecture enabling lane — early design proofs, not a platform rewrite

These decisions are expensive to reverse and can be investigated **in parallel** with local cleanup without requiring a wholesale migration.

| Sprint / study | Concrete proving case | Important constraint |
|---|---|---|
| **World Resolution + Content Identity** | Same named Goblin from two sources remains distinct; one effective World read interface resolves local, imported and inherited content, including images and actor versions | Preserve today's world-over-campaign COW, existing IDs and user files; no silent name-based merge |
| **Content Composition** | One setting imports a reusable bestiary without eagerly copying every record or inserting it all into prompts; handle override, upgrade, missing source | DAG vs shallow library references and virtual vs compiled read-only World view are **open**, not selected |
| **Mechanics Extensibility** | D&D attack, WoD contested action, Ars Magica seasonal work and PbtA fiction-triggered move through minimal contrasting contracts | One mechanics module (or `none`) per campaign; no assumed universal d20 check |
| **State Transactions** | A legal action and irreversible effect are recorded once; retry/recovery/undo never doubles a resource spend | Build upon the existing proposed Mechanics II Action/Effect ledger; do **not** invent competing engines |
| **Change Sets** | Import/update a card, review edits to character, groups and lore, detect stale base and commit only approved scoped file changes | External editors and Grimoire's optional agent workers share file authority; derived DB is not SSOT |
| **Agentic Runtime** | Optional bounded multi-step card integration or exceptional investigation with trace, explicit authority and cost budget | Extend already-landed provider-neutral tool loops and bounded investigation rather than putting agent loops in routine turns |

See [backend-flexibility.md](backend-flexibility.md), [content-architecture-study.md](content-architecture-study.md), [content-composition.md](content-composition.md), [agentic-workers.md](agentic-workers.md), [world-maintenance.md](world-maintenance.md) and [file-authority.md](file-authority.md).

## Player-facing vertical slices — candidates after / alongside enabling work

### 1. Inventory MVP — intentionally mechanics-free

**Player outcome:** a PC has eight ordinary capture balls and two special ones, spends three ordinary balls, later receives four, and now has **nine ordinary and two special**. Read/edit through a lightweight UI, persist authoritative campaign files, and include only relevant holdings in narrator context. Failed overspends are flagged; retries don't double-consume. **Do not** block this on full mechanics, craft/economy, global bestiary identity, or a customizable HUD.

**Follow-ons:** discrete equipment instances, custody and scene provenance, equip/unequip, extraction and conflict review, currency/consumption semantics, system-specific encumbrance/gear and richer inventory UI. All five formerly open inventory issues #167–171 are preserved in [inventory.md](inventory.md), with full original bodies [archived](archived-issue-text.md).

### 2. Creature → Character — one archetype, many individuals

**Player outcome:** a nameless goblin becomes a persistent NPC when individualized; a captured monster becomes its own character **immediately on committed successful capture**. Several individuals share a source creature without sharing identity, memories or mutable stats. Inherited default portrait/traits apply **until overridden**; failed capture doesn't mint an actor; repeated application doesn't create duplicates. The captured companion is **not a stack in Inventory**. Existing actor identity survives renaming, eventual species evolution and transfer.

**Staging:** first show a small deterministic store/service slice against present campaign entities; then prove live cross-kind art/descriptive fallbacks and explicit ruleset-dependent sheet conversion. Do not bypass creature-vs-character sheet validation. See [creature-to-character.md](creature-to-character.md) and [inventory.md](inventory.md).

### 3. Mechanics improvement — including deferred Mechanics II

**Mechanics II belongs here**, rather than at the end of the already-landed 01–12 implementation checklist. The complete mechanic action/effect/resolution/conditions/contests/clock/undo work must be reconciled against current modules, action contracts and file transaction guarantees before writing a new spec. Game-specific effects must not force dice/check semantics onto mechanics-free play. Only actual documented support, not every supplement/edition, is a 1.0 requirement.

### 4. Obstacles, Consequences and Living Worlds

Develop explicit **Obstacles / Encounter Design** to create coherent playable problems; **Resolution** to establish attempts; **Consequences** to persist outcomes; **NPC Autonomy / Faction Dynamics** to choose believable actions; **World Simulation / Time Management** to progress offscreen actors and clocks; and **Narrative Arcs** to shape presentation without overriding the causal simulation. Reuse the same world state, decisions and effect/transaction authority.

Transferred long-term requirements include location graphs/maps (#42/#43), library activity/impact (#55), cross-world character variants (#50), cross-campaign scene import (#93), time-skip NPC/group progression (#102), long-activity resolution (#108), and export filtering (#140). See [deferred-systems.md](deferred-systems.md). **#151 historical replay is optional and may be unnecessary.**

## Noncore and deliberately postponed candidates

**Do not elevate into the immediate backlog without a real use case:** multi-PC turn loop (#81) vs full asynchronous multiplayer; customizable HUD (#196); live prompt preview while typing (#132); still-missing auxiliary-generation task variants (#188); auxiliary accept/discard dispatcher (#190, possibly unnecessary); full historical turn replay (#151, possibly unnecessary); marketplace, deep module packaging and large-scale procedural generation. Preserve the goals in [backlog-discretionary.md](backlog-discretionary.md) and [deferred-systems.md](deferred-systems.md).

**VN/expressions:** core is shipped; richer Visual Storytelling, UI/accessibility, voice, audio, image workflows and potential presentation-cue decisions remain candidates but must not be treated as a request to reimplement the existing reader. See [visual-novel.md](visual-novel.md).

## Decision and release discipline

- **Thematic names aren't specs.** A proposed sprint earns an implementation spec with concrete player/author outcomes, explicit boundaries, acceptance/failure tests, dependencies, version compatibility and migration/rollback strategy.
- **Preserve agreed architecture:** Markdown/frontmatter and current editable JSON are SSOT; caches are disposable. The world's fiction is mechanics-independent. Campaign chooses one module or `none`. Inference handles judgment/narration; deterministic code enforces known outcomes and legal mutations.
- **No forced deadline or dependency megagraph.** Small independent gameplay features (especially counted Inventory) can precede broader resolution work. Exploratory WorldView and cross-ruleset spikes may occur while quality cleanup continues. A new abstraction must be justified by real consumer evidence, but cleanup has intrinsic value.
- **Release family labels remain provisional:** the 0.2.x playable casual RP foundation is shipped; the 0.3.x notion of extensible rules/mechanics remains a candidate framing; later obstacle, simulation, progression and distribution milestones are player-outcome themes, not calendar promises.
- **Protect the permanent planning branch:** never merge `roadmapping`, never open a PR for it. Promote an approved slice through a **separate spec and implementation branch based on current `main`**. When one lands, update the register to credit the shipped capability, and explicitly retain any unmet goal.

## Lost-work prevention checklist

Before beginning or closing any sprint, check:

- [ ] The goal exists in [sprint-register.md](sprint-register.md) and has a clear home/source document, **or** a new register entry is added.
- [ ] Existing implementation, CI, tests and earlier PRs have been checked: no duplicate shipped work.
- [ ] The sprint's boundary and invariants are stated; mechanics-free and editable-file modes are considered.
- [ ] Dependent unresolved decisions are named rather than silently selected.
- [ ] Specific acceptance and recovery/error cases are captured in the spec.
- [ ] Deferred issue goals are not lost when their tracker was closed `not_planned`.
- [ ] The resulting PR/commit is cited in the register; partially satisfied goals retain a named successor.

**The append-only original issue bodies in [archived-issue-text.md](archived-issue-text.md) and the design discussion in [content-composition.md](content-composition.md) should remain available even when the living status map changes.**
