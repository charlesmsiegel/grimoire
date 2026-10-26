# Skill Calibration

**Priority:** after significant cleanup, before a blocking quality gate.

## Goal

Improve the owner's **coding-skills** project (especially code-overview, language-specific code doctors, code visualization, science investigation and Naur's theory-building) until its results are correct, explainable and repeatable enough to judge Grimoire PRs.

## Work to plan

- Run the full skill pipeline on pinned source and skill revisions; preserve the package map and record drift. Confirm a doctor actually parsed each graded language/package.
- Reproduce and fix **false positives** with detector regression fixtures. Distinguish proven findings from uncertain candidates; candidates do not decrease health scores.
- Include deliberately tricky Grimoire conventions: Pydantic 1 field defaults; imported/re-exported compatibility facades; FastAPI route registration, intentional cycles and monkeypatch seams; source-qualified store paths; atomic file writes; Android optional dependencies; generated/static assets.
- Improve code-map support for packages with mixed **file and directory roots**, and label impossible or incomplete analyses rather than manufacturing a grade.
- Make measurement discovery understand Grimoire's synthetic/generated evaluation datasets and quality gates. A conventionally unlabelled JSON fixture is not evidence of no evaluation.
- Test measurement expressions that explicitly distinguish **unknown from zero**, including guarded partial sums and malformed fixture files intended to test decoders.
- Audit Naur judge prompts and evidence standards: world mapping, abstraction, justification, honest limits and ability to absorb future requirements. Three judges must be independent; preserve disagreements and citations.
- Fix general-purpose defects upstream; express local invariants in versioned Grimoire profiles where practical; fork/vend only genuinely irreducible project-specific logic. Pin commits and local deltas.

## Proving scenarios

A deliberately correct wrapper or Pydantic model remains unpenalized; a planted real defect triggers a concrete finding; a missing specialist/detector yields **unknown**, not A+; a complex feature's repeated special-case patching is exposed with source evidence. Repeated unchanged evaluations are sufficiently stable to support the desired later threshold.

## Dependencies and boundaries

Consumes actual findings from [Engineering Health](engineering-health.md). The [Quality Gate](quality-gate.md) cannot safely activate before calibration, including secure judge execution. No score threshold should be lowered just because the tool is noisy.

## Open questions

- Which conventions should be upstream detector rules, a Grimoire profile, or a maintained local fork?
- What labeled regression corpus of true defects, false positives and deliberately ambiguous leads is enough to calibrate each detector?
- Which package roots genuinely lack a language specialist, and what counts as an explicit N/A versus incomplete grade?
- What statistical stability standard must the three-judge Theory panel meet near a B+ boundary, including model updates?
- Which evaluations are worth the compute cost on every PR versus periodic whole-repo recalibration?
