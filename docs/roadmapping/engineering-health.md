# Engineering Health — code hygiene and backend cleanup

**Status:** candidate cross-cutting sprint family and priority guidance, not approved implementation specs.

**Current status — 2026-10-26:** Backlog consolidation and the initial cleanup wave **have happened**. Production Python's 37 oversized packages were split and validated on `main` (see [validation](../engineering/backend-package-validation.md)); subsequent frontend API/type/component and shared test-fixture simplifications also landed. Do **not** rerun these as pending work. The immediate value now is remaining cognitive complexity, type/lint debt, semantic correctness, test fidelity and Naur's theory; then detector calibration and eventually a hard PR gate. [Execution Roadmap](execution-roadmap.md) is the living order. Historic pre-cleanup instructions below are retained as context, not a current blocking phase.

**Objective baseline:** the committed 2026-10-26 `main` ratchets record **590 Ruff, 132 mypy, 280 ESLint** existing findings. The generated 2026-10-21 code-overview health scores improved dramatically over the August report, but are **not** a current post-VN or complete Theory judgment: some packages remain below B+ and Scripts/Android are ungraded. Retake a pinned full assessment before using scores as a merge gate.

**Sequencing update (2026-10-15):** First land as many independent existing non-inventory, non-mechanics backlog issues as practical and close them with evidence; reconcile shipped capability and residual goals into the thematic roadmap; **then** capture the post-backlog code-quality baseline and begin concentrated cleanup. Major architectural-coupled backlog issues may need to wait. See [backlog-consolidation.md](backlog-consolidation.md).
**Date:** 2026-10-15.
**Working branch:** `roadmapping` stays unmerged. Individual changes go through normal specs, branches, tests and reviews based on `main`.

## Why this is first-class roadmap work

Refactoring, cleanup and maintenance **are valuable in their own right**. A sprint does **not** have to unlock two features to earn priority. Reliable, comprehensible backend code, fewer bugs, shorter feedback loops, less reviewer effort and easier changes have direct value even if no visible gameplay feature is added.

Grimoire is a fast-growing project with important player data. Technical debt that makes small edits dangerous or slow should be paid down deliberately, not hidden under unrelated features or indefinitely deferred to "later polish".

Keep an **Engineering Health** thematic track alongside **Backend & Extensibility** and gameplay. Prefer standalone behavior-preserving cleanup PRs to opportunistic refactors hidden inside feature PRs.

**Especially for AI/agent-authored code**, a green test suite and an apparent implementation can coexist with duplicated domain logic, locally convenient assumptions, and a fragmented conceptual model. Cleaning that up must restore the *theory of the system*, not just the syntax. This is different from **Agentic Workers** (a future runtime capability): the concern here is technical debt left by coding agents.

## Theory Building — a first-class early sprint

Use the owner's [coding-skills](https://github.com/charlesmsiegel/coding-skills) **code-overview** and **theory-building** skills to investigate both measurable code-health defects and Peter Naur's *Programming as Theory Building*: what is the domain model, why is it structured that way, and will plausible next requirements be natural extensions rather than patches?

The code-overview skill produces distinct **Code Map**, **Health**, **Measurement**, and **Theory** artifacts. The theory panel uses independent judges to assess absorption of new requirements, world-mapping, abstraction, justification and honest limits. Its grade is a *judgment with cited evidence*, not a deterministic correctness metric; never optimize the letter grade in isolation.

Existing Grimoire reports, **generated 2026-08-18 at a63cb05f5**, are already checked into the repository. Their historical results include:
- Store backend: **Health F, 42.4/100; Theory C, 75.0/100.**
- API backend: **Health F, 33.4/100; Theory C−, 70.0/100.**
- LLM backend: **Health F, 51.1/100; Theory C, 75.0/100.**

They are NOT a fresh October 15 verdict: the code and the author's skills have both evolved. Some static reports may misclassify intentional module re-exports or standard patterns. Triage findings with code evidence, not an automated rewrite. The repository already has a confirmed package map at `docs/code-overview.json`; reuse it when appropriate and inspect drift.

For a new assessment, rerun the current **code-overview** against the current code with today's tool version and record the exact analyzed commit and tool revision, per-package grading completeness, findings-versus-candidates split, and *the actual theory judgments*. Compare like with like; the same grade from different judges/versions need not be comparable.

**Rehearse real next requirements**, especially: a second inherited content source beneath one World; a new RPG mechanics system that differs sharply from D&D; a new stateful world-maintenance write operation; an added entity kind; a new tool-using worker; a safe override of an imported record. For each, identify whether the present theory yields an extension, demands a patch, or requires changing the core model. Repeated patch outcomes are stronger architectural evidence than a raw complexity count.

Historical theory panels already cite real friction, including special-case handling of `plotmap` inheritance, repeated store sync branches, manually mirrored API vocabulary, and weak generalization of world/campaign editing routes. Re-verify each against current main before creating a work item.

**Deliverables:** an evidence-based conceptual model of Grimoire's key subsystems, a list of mismatches between that model and implementation, validated hot spots ranked by churn/risk, and separately executable small cleanup specs. For each meaningful refactor, include a short **theory note**: what problem the subsystem models, alternatives rejected, honest limits, and how representative next changes would extend it.


## Candidates (1–2-word sprints)

| Candidate sprint | Main target | Readiness / stance |
| --- | --- | --- |
| **Theory Building** | Recover and test the domain model behind agent-authored code; rehearse plausible new requirements | Early cross-cutting priority, alongside architecture studies |
| **Code Simplification** | Remove unnecessary layers, repetition-with-variants, false abstractions and accidental complexity | Prioritize validated high-churn hotspots |
| **Code Hygiene** | Naming, duplication, dead branches, complexity hotspots, cohesion | Ongoing; justify changes by identified maintainability problems |
| **Backend Refactoring** | Narrow package boundaries and responsibilities in large store and route modules | Prioritize high-change, high-risk surfaces before their next significant feature |
| **Type Safety** | Reduce mypy ratchet debt; typed internal state, APIs, and model boundaries | Baselines exist; reduce them progressively without broad `Any` or ignores |
| **API Consistency** | Consistent service contracts, models, read/write behavior, errors, validation | Especially valuable for store vs route vs agent-client parity |
| **Error Handling** | Normalize failure codes, avoid unintended fail-open/silent success and accidental exception masking | Respect deliberate fail-soft *read* policies and strict *write* policies |
| **Test Hygiene** | Fixture quality, regression coverage, isolation, test clarity, useful fast developer loops | Existing major acceleration landed; don't repeat it or delete distinct behavior tests |
| **Dependency Hygiene** | Imports, package cycles, optional dependencies, version compatibility and boundaries | Preserve existing import guard; remove only real unnecessary coupling |
| **Dead Code** | Unused paths, expired compatibility shims, obsolete providers and duplicated implementations | Prove callers absent; remove with guards/tests and migration review |
| **Documentation** | README, architecture diagrams, store invariants, API contracts, developer onboarding | Keep synchronized with code and tests; reduce duplicated normative guidance |
| **Migration Safety** | Compatibility fixtures, fixture/backup restore, old-campaign upgrades and downgrade boundaries | High priority for any world/composition/identity change |
| **Concurrency Review** | Lock ordering, external edits, interruption windows, idempotency and recovery | High priority around new transaction/agent write paths |
| **Observability** | Structured failures, traces, cost attribution, event/action debugging | Existing model metering is strong; focus on gaps actually found |
| **Security Review** | Imported untrusted cards, plugins, asset paths, scope permissions and authoring-agent tools | Target identified trust boundaries; avoid vague audits without testable scope |

This list is *deliberately broad*. Not every row needs an entire minor release, and not every row is currently a large backlog. Scope from real findings. Names stay short; actual future specs require evidence and tests.

## What main already has (avoid duplicated projects)

Reviewed against current main:

- `lint-baselines/ruff.json` and `lint-baselines/mypy.json`, with `scripts/ratchet.py`, already prevent new findings and support gradual baseline reduction. `mypy.ini` documents a nonzero existing debt. Don't "fix" metrics by disabling rules or increasing baselines.
- `CONTRIBUTING.md`, `CLAUDE.md`, `AGENTS.md` and AST guards already enforce module imports, storage atomicity, lock domains, copy-on-write and other invariants. Improvements should strengthen, not bypass, them.
- The October 8 test-suite acceleration produced a *measured* major gain. `docs/superpowers/validation/2026-10-08-test-performance-report.md` reports CI critical path reduced from 58.1 to 11.5 minutes median on its measured baseline, while maintaining ~93.7% combined coverage; `2026-10-08-test-consolidation-ledger.md` records why further consolidation was not justified on that snapshot. Reprofile today's tree before proposing more.
- The former `store/overlay.py` and `routes/campaigns.py` monoliths were subsequently **converted to packages** with explicit compatibility and review evidence. This historical hotspot prompted useful work; don't repeat a successful physical split purely for smaller-file metrics. Evaluate the *current* owner modules and remaining cognitive coupling without weakening invariants.
- `docs/store-guarantees.md` states known transaction and concurrency limitations. These are excellent inputs to targeted correctness/hardening sprints.

Repository-provided code-health HTML reports, where present, are **triage signals**, not audited truth or replacements for profiling, call-graph analysis and code review. Avoid chasing a synthetic score.

## Proposed cadence

**Early cleanup wave, in parallel with the World Resolution and Mechanics Extensibility architectural spikes:**

1. **Theory Audit:** rerun the current code-overview skill, articulate each major subsystem's theory, and rehearse prospective requirements; record score limitations and evidence.
2. **Backend Audit:** inventory maintainability/complexity hotspots, call paths, duplicate mechanisms, error-handling inconsistencies and risky dependencies using current main.
3. **Boundary Cleanup:** small, behavior-preserving refactors around chosen high-risk APIs; tighten types and contract tests, stabilize known seams.
4. **Test Hygiene:** improve targeted test structure/diagnostics where evidence shows a real benefit; maintain full-suite coverage and the already-optimized fast loop.
5. **Documentation:** synchronize developer guidance and contracts so agents and human maintainers don't follow outdated behavior.

**Continuous throughout development:** Type Safety, Code Hygiene, Dead Code, Error Handling, Migration Safety, and Concurrency Review. Schedule concentrated sprints when their backlog becomes meaningful rather than waiting for end-of-roadmap cleanup.

A healthy release can contain both a gameplay feature and a deliberate engineering-quality milestone. A fully standalone cleanup release is acceptable if the codebase needs it; the user does not require every sprint to add an RP feature.

## End-state: hard quality gate on every PR

Once the existing code is cleaned up and the owner's `coding-skills` detectors and Naur theory panel have been improved to reduce false positives, require **B+ or higher (at least 87/100) for every applicable graded package/report** before a PR can merge. A repository average must never conceal one failing package. The gate is **not active yet**.

The work has two distinct follow-on sprints:
- **Skill Calibration:** correct false positives, improve evidence and Naur judge reproducibility, add regression fixtures, and optionally create Grimoire-specific skill profiles or forks.
- **Quality Gate:** shadow CI and validate stability before turning the B+ floor into a required merge check. Keep existing ratchets, tests and architecture/review guards.

Grimoire-specific skills are a reasonable option, but avoid divergent silent scoring: fix generic detector bugs upstream, layer local policies where possible, and pin/trace any local fork. Code Theory is partly a model judgment, so hard gating requires reproducible evidence and a challenge process; a stochastic grader crossing 87 on an unchanged SHA is not an acceptable developer workflow.

See [quality-pr-gate.md](quality-pr-gate.md) for the proposed full policy and implementation hazards (including model credentials for untrusted GitHub PRs).

## Acceptance criteria: hygiene work must still demonstrate value

The primary target for a **Theory Building** sprint is better conceptual integrity, not a grade threshold. A useful before/after comparison answers whether at least a few previously awkward realistic changes can now be expressed cleanly, and whether an unfamiliar maintainer or agent can explain the module boundaries and invariants without reconstructing them from ad-hoc code paths.

Good criteria include:
- A targeted behavior-preserving refactor has green **existing** tests plus new characterization/contract tests where gaps were exposed.
- A maintainability hotspot has fewer responsibilities or clearer module dependencies, without increasing API surface or moving complexity elsewhere.
- Specific known type/lint findings actually disappear and the ratchet baseline is reduced **by the amount fixed**, with no blanket suppressions.
- A repeated change operation becomes simpler and takes fewer edits or fewer risky cross-package changes (demonstrate with representative modifications).
- A test cleanup improves iteration time or diagnostic quality **without losing unique state transitions, regression coverage or fault detection**.
- Old campaign data, world records and mechanics-free play remain compatible; a migration test demonstrates it.
- Documentation matches the landed contract; guards distinguish tolerated hand-edited input from strict writes.

Sometimes the payoff is correctness or easier reasoning rather than a numerical metric. Evidence can be an explicit before/after dependency map, a removed stale code path, or a demonstrably reduced number of independent invariants.

## Revised prioritization rule

An engineering sprint is worth doing early when it achieves **any one** of:
1. Fixes or prevents meaningful correctness, security, data-integrity, or migration risks.
2. Simplifies a high-change/high-complexity backend surface before the next large feature.
3. Reduces repeated developer/agent effort and accelerates trustworthy iteration.
4. Eliminates demonstrable duplication or inconsistent representations.
5. Establishes a hard-to-change interface required for upcoming functionality.

Don't require two downstream features as proof of value. Also reject refactors performed **only** to increase abstraction, rearrange files cosmetically, or improve an unvalidated quality score.

## Scope caution

The prior **Backend & Extensibility** list describes *new contracts* (World Resolution, Content Identity, Mechanics Extensibility, etc.). **Engineering Health** describes *maintaining and improving the code we already have*. They reinforce each other but should not be collapsed: otherwise cleanup will never receive priority when feature delivery is tempting.

Implementation of Hygiene work should be small, reviewable, and independently revertible whenever possible. Respect the established Markdown/JSON file authority, Android compatibility, existing COW behavior, runtime privacy boundaries and the project's CI/test gates.
