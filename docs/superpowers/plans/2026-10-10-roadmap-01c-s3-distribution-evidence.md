# 01c-S3: The recorded evidence and the distribution grader

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01c-decision-distributions-sampling-design.md`
(the 01c-S3 slice entry, §3, §4.3, §10, §12 Q1 and Q4).
**Builds on:** 01a-S3/S4 (live cost and latency, run files, `--compare`),
01c-S1 (`draws.py`), 01c-S2 (`TaskPolicy.native_first`, the native-first stage).
**Review gates:** not run (speed mode); the Codex gates are owed.

## Goal

Finish 01c-C1. The recorded policy gets the evals check that its §4.3 bar
needs, `decide.distribution`, and the place the evidence lives: the
"Decision distributions" section in `evals/README.md`. No task is switched
on.

## Design

- **`graders.grade_distribution(item, question, native)`** returns one
  `decide.distribution` check.
  - `native` is the item's `ItemResult` when a native endpoint answered it
    (`ctx["native_results"]`, which the live runner and the native
    recordings already fill), else None.
  - None gives `n/a: answered structured` (`STRUCTURED_DISTRIBUTION`). This
    mirrors `NATIVE_RATIONALE`.
  - Otherwise the result goes through `draws.draw` with a fixed seed and
    nothing narrowed. The grader does not keep a second copy of the
    sampler's §5.4 rules, so "usable" means `basis == "sampled"`.
  - The `answer` rows fail, and the detail names the row: `no_report`,
    `partial` or `inconsistent`. The reported mass goes beside it.
  - A non-answer (`basis == "none"`: refused, abstained) is
    `n/a: no answer to draw from`. `decide.answer` already grades it, and
    there is nothing to draw from.
  - The mass is printed on a pass too. That is the per-kind figure that
    §12 Q1 says `MASS_SLACK` is tuned against. It reaches the run file
    through the check's detail.
- **Wired into `decide-speaker` only.** Today that is the one case the §4.3
  procedure names. It gets four new hand-authored native recordings:
  - `native` (OpenAI) and `native-openrouter`: compliant, and both pass.
  - `native-partial` (OpenAI, mass 0.5) and `native-inconsistent`
    (OpenRouter: an explicit pick with no weight). Each trips
    `decide.distribution` alone.
- **The README section "Decision distributions"** contains:
  - the policy, in three lines;
  - the check;
  - the §4.3 procedure (the command, in both venv forms), the bar, the cost
    note and the record;
  - a record table with one row per kind, both marked **owed**.

## Open questions, decided

- **Q4 (where the evidence lives):** adopted. It goes in `evals/README.md`,
  "Decision distributions". `routing.TASK_POLICY`'s comment already names
  that section (S2).
- **Q1 (`MASS_SLACK`):** unchanged. The grader shows each answer's
  reported mass so a live run can tune it later.
- **Evidence:** the user's live run cannot be produced offline. It costs
  money and needs the user's key. This slice builds the mechanism and
  records in the README that the evidence is owed for both kinds. Live
  figures would come from the synthetic eval corpus only, never from a
  library.

## Files

- `evals/graders.py`: `STRUCTURED_DISTRIBUTION` and `grade_distribution`.
- `evals/cases.py`: the speaker grader calls it, plus four recordings.
- `evals/recordings/decide-speaker.native*.json`: four bodies.
- `evals/README.md`: the case row, the n/a note, the new section and the
  recordings list.

## Tests

- `backend/tests/test_eval_graders.py`:
  - n/a on structured;
  - a pass on a usable choice and on a predicate;
  - a fail on partial, on inconsistent and on a missing or invalid report;
  - n/a on a refusal and on an abstention.
- `backend/tests/test_evals.py`:
  - a live native pick through the runner, parametrised over usable,
    partial and inconsistent;
  - a live structured pick, which reads n/a in the report;
  - the existing replay, orphan and recording tests, which cover the four
    new recordings.

## Left open

- The live evidence run for each kind, which the user owes. 02-C2a's flip
  of `response-selector` is gated on it.
- `decide.distribution` is not wired into `decide-joint`. That is the NPC
  action shape 13-C3 will sample, so 13's evidence change wires it.
- The comparison table gets no mass column. The mass is in each check's
  detail in the run file.
