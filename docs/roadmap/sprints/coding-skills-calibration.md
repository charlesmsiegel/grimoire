# Coding-Skills Detector and Theory Calibration

**Outcome:** Get accurate, reproducible judgments from the independent coding-skills project before using grades to block Grimoire changes.

## Scope
- Pin tool/model revisions; verify specialists really parsed each graded package.
- Create labeled fixtures for false positives, real defects, unsupported analyses and intentionally odd Grimoire patterns.
- Calibrate independent Naur theory judges and separate unknown, N/A, candidate and confirmed finding.

## Milestones
1. Reproduce inaccurate verdicts on a pinned source snapshot.
2. Fix upstream rules or project profile, then run repeatability trials.
3. Publish a validated rubric, evidence corpus and accuracy report.

## Moonshot
Use change-based theory rehearsals, not just static smells, to predict whether a codebase can gracefully absorb a new requirement.

## Proving scenario
A correct compatibility facade is not penalized; a planted defect is found; missing analysis cannot receive a perfect score.

Live run: [judge panel live run](../../evals/JUDGE_PANEL_LIVE_RUN.md) gives the approved panel, commands and the rule for checking the slice.

Live run (2026-11-20): one 108-call capture did not establish the proof; see [the results](../../CALIBRATION.md#live-capture-2026-11-20).

Live run (2026-11-22, parser 6): the sixth capture established the proof, after four more that did not; see [the results](../../CALIBRATION.md#live-capture-2026-11-22-parser-6) and their limits.

## Independence and interfaces
Standalone tool-quality sprint; the blocking gate is a separate consumer and must not activate prematurely.

## Open questions
- Which rules belong upstream versus a Grimoire profile?
- How much judge variance is tolerable near 87/100?
- What is the minimum independent labeled corpus?
