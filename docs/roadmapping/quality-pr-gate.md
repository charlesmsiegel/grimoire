# Quality PR gate — B+ floor after remediation

**Status:** agreed direction / proposed implementation policy; **NOT CURRENTLY ENFORCED**.
**Date:** 2026-10-15.
**Applies to:** Grimoire pull requests, after code remediation and skill calibration.
**Branch:** this remains on the permanently unmerged `roadmapping` planning branch. Implementation requires a separately reviewed spec/PR from `main`.

## Intended end state

The owner intends to improve the skills in [charlesmsiegel/coding-skills](https://github.com/charlesmsiegel/coding-skills), especially `code-overview`, `code-doctor`, the Python/TypeScript specialists and Naur-oriented `theory-building`, then install them as a **hard pull-request gate**.

**Minimum grade: B+ or better = >= 87/100** under the current code-overview rubric (`skills/code-overview/scripts/rubric.py`). Block merge when **any applicable graded package/report-level score** is below B+. Do not let an overall repo average hide a failing package, or let a strong Health score hide a weak Theory score.

Distinct score types need distinct reporting:
- **Code Health** — graded on verified mechanically detectable defects, not unverified candidates.
- **Code Theory** — Naur-style judgment about coherent domain modeling, extension/absorption, justifications, abstractions and honest limits; three independent judges in the current implementation.
- **Measurement Coverage** — only where the code-overview workflow actually defines a graded, applicable result. A genuine "not applicable" must be explicit; missing or crashed analysis must not masquerade as passing/null.
- **Code Map** — contextual architecture artifact, not itself a letter grade.

The individual **rubric categories/dimensions** are diagnostic; the B+ minimum is on each independently **graded package/report**. A separate rule could later elevate individual critical findings or specific categories to blockers. Do not silently conflate a dimension with a package score, particularly for the coarse Naur-theory four-rung dimension ladder.

**No grace period after enforcement begins:** a PR affecting a gated package with a below-threshold score fails. The broad cleanup and skill fixes happen *before* switching on the required status check. Avoid making the existing large low-grade backlog the daily blocker while it is being paid down.

## Required preparation: skill accuracy is part of the project

The code-health reports checked into Grimoire were **generated 2026-08-18**, not a verdict on the current source tree. The historical Store/API/LLM Health grades were F, while the Theory grades were C/C-/C. These establish a cleanup baseline, not the future standard.

**False-positive calibration is mandatory:** sample and reproduce findings, label proven defects versus heuristic candidates, and implement detector regression fixtures so a corrected false positive stays corrected. Prior reports appear to flag examples such as `from __future__ import annotations` and deliberate package-level public re-exports as unused imports; verify and correct detector logic rather than deleting valid design or broadly suppressing findings. Include FastAPI route registration/dynamic dispatch and intentional portability patterns among adversarial fixtures.

Don't `# noqa`, exclude packages, inflate line counts, artificially partition packages or modify weights merely to manufacture B+. If a Grimoire convention truly requires an exception, it must be specific, justified, covered by tests, and visible in the report. Replacing a noisy finding with an **unverified candidate** is legitimate only when the evidence cannot prove a defect and the detector documents that ambiguity.

## Skill packaging: general upstream vs Grimoire-specific variants

**Both are acceptable**, because the owner controls both repositories. Prefer this order of maintenance:

1. Fix broadly valid detector errors and scoring deficiencies **upstream in `coding-skills`**.
2. Add a versioned **Grimoire profile** for intentional project-specific invariants: Markdown/JSON SSOT, world/campaign COW, `store.atomic`, lock order, Android/Pydantic support, runtime provenance, re-export patterns, route generation, AST guards. This profile is configuration/evidence, not automatic exemption.
3. Where project-specific logic truly cannot be cleanly expressed as configuration, maintain a **Grimoire-local fork/wrapper or vendored copy** of the applicable skill(s). Pin its upstream commit and maintain explicit local diffs, upgrade tests and a backport/rebase path. Avoid silent behavior drift and maintaining two unrelated scoring rubrics.

Grimoire may adopt the upstream skill release via pinned commit, then layer project-specific policy while keeping a comparable core report. Forking is an option, not a prerequisite.

## Gate design

### Stage 1 — remediation (not blocking on grade)

- Refresh the code-overview artifact set at a pinned source revision and skill revision; reuse the repository's existing confirmed package map, checking for drift.
- Fix prioritized high-risk defects and replace false-positive heuristics with sounder detectors.
- Capture Naur theory notes and rehearse real upcoming requirements: content DAG, source-aware identity, another mechanics system, write-capable world-maintenance worker, additional record types.
- Preserve characterization tests and existing gates; document why an intentional duplicate or boundary is correct.

### Stage 2 — calibration (report-only)

- Add a CI report job using **exactly pinned versions** of general skills, any Grimoire overlays, and model/rubric/prompt versions.
- Run repeatedly on **unchanged commits** and evaluate output stability, especially the three-judge Theory panel. If borderline package grades bounce across 87 with no code changes, the gate is not ready.
- Compare results on synthetic known-defect/known-clean fixtures. Verify false-positive and false-negative behavior.
- Output per-package results, exact score, evidence paths, candidates, confidence/disagreement and grader completeness, plus side-by-side changes from the merge base.
- Keep non-applicability distinct from detector failure, tool crash, skipped code, or absent external credentials.
- Measure actual PR runtime and model expense; run expensive judgment only when relevant package surfaces change, with a full periodic all-package evaluation for drift.

### Stage 3 — blocking

- Integrate through a reproducible `make check-* ` target and a GitHub required status check, consistent with Grimoire's current `.github/workflows/ci.yml` / `CONTRIBUTING.md` contract.
- Enforce the >=87 threshold **per graded package and independent graded report**, including root-level reports where evaluated, not just a weighted repository summary.
- Deterministic, reliably proven defects can block directly. For Code Theory, demand a stable, version-pinned, evidence-cited judging protocol: no arbitrary score on a single stochastic run should be able to fail a PR without reproducible review. The underlying instruction is still B+ minimum; variability must be fixed before enabling the hard gate rather than silently weakening the rule.
- Fail **closed** when a required detector/judge is missing, incomplete, or failed. Explicit genuine N/A is allowed only when the package/metric policy says so. A failed model request is an infrastructure failure with clear rerun instructions, not a grade.
- Keep existing Ruff/mypy/ESLint ratchets, tests, architecture guards and Codex review gates; a B+ doesn't replace any of them.
- Provide actionable GitHub annotations/artifacts: which package and score failed, specific evidence, exact tool/prompt revisions, false-positive challenge process.
- A disputed score must have a documented review path that fixes the tool or the underlying issue; any exceptional policy bypass must be explicit and auditable, never an invisible pass.

## Security and reproducibility concerns

Grimoire's repository is public. PR code is untrusted input, including source comments and test files. A model-based Theory judge must treat repository content as **data**, not instructions. Do not execute arbitrary PR code as part of grading. Never expose model/API credentials to workflows executing untrusted forked PR code; use a genuinely isolated, least-privilege review service/job or otherwise solve the credential boundary first. GitHub's workflow topology and trust controls require an explicit threat-model review.

Persist the evaluated source SHA, `coding-skills` commit (and any local variant), model identity, rubric version, prompt and judge protocol. A change to grader version should rebaseline/requalify; a silent grader upgrade invalidates score comparisons. Store no real user-world/campaign data in public PR artifacts or prompts.

## Questions to settle at spec time

- Should only modified packages be regraded at PR time or all packages on every PR, with a separately scheduled full run?
- What source-change dependency graph widens an affected package's evaluation scope?
- How can the Naur three-judge panel be stable enough at threshold 87 to block reliably, especially after a provider/model update?
- Which metric families are truly applicable for each package (e.g. Android or metric-free utilities), and when is null legitimate?
- How does a targeted false-positive appeal become a regression test for the detector?
- Is Grimoire policy configuration sufficient, or is a maintained local skill fork necessary?
- How are source changes evaluated securely without letting an untrusted PR access judge credentials?

**Do not turn on this hard PR gate until these questions have concrete, tested answers.** The user's destination is a real hard B+ quality standard, not a permanent report-only recommendation.
