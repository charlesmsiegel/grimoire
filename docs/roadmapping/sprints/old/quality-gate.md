# Quality Gate

**Priority:** after Engineering Health and Skill Calibration. **Not active yet.**

## Goal

Make high-quality, comprehensible code the default for every new Grimoire PR by enforcing **B+ or better (at least 87/100)** on **each applicable graded package and report**, including code health and Naur's theory. A repository average cannot conceal a failing package; strong health cannot conceal weak theory.

## Work to plan

- Add a deterministic local check target and CI reporting using pinned coding-skills, rubric, prompts, model identities, package scope and source commit.
- Run a **report-only shadow gate** first. Compare against confirmed-good and planted-defect code; rerun unchanged SHAs to understand judge variance and false positives.
- Represent **not applicable**, **incomplete/failed analysis**, **unavailable model credentials**, **candidates**, and **actual failing scores** distinctly. Missing required evaluation fails closed, not as 100 or 0.
- When the gate is reliable, require the status check for merge. Report evidence and actual per-package numeric scores; keep raw artifacts, reviewer-disagreement summaries and clear reproduction/appeal instructions.
- Keep Ruff, mypy, ESLint ratchets, architecture guards, test coverage and independent Codex spec/plan/review checks. They enforce different obligations.
- Secure model judging against untrusted pull-request text and executable code; fork PRs must not gain secrets or arbitrary high-privilege tool access. No private-world or campaign content in logs.
- Requalify the gate on detector, model, rubric or prompt upgrades. Make exceptional overrides explicit, recorded and reviewable rather than silent exemptions.

## Proving scenarios

An unchanged commit gets a stable result; one package at 86.9 blocks despite a high repo average; an incomplete doctor is an infrastructure failure; a false-positive challenge becomes a test; a malicious PR cannot read judging credentials; a verified B+ score does not let unrelated CI regressions through.

## Dependencies and boundaries

[Engineering Health](engineering-health.md) pays down old debt. [Skill Calibration](skill-calibration.md) demonstrates accuracy and judge repeatability. Blocking activation is a separate release decision: this file does not authorize enabling the gate today.

## Open questions

- Do PRs score every package or only impacted packages plus a dependency-expanded scope, with periodic full runs?
- How is dependency impact computed when the shared schema, store or inference runtime changes?
- What is the exact anti-flakiness protocol for a Theory result on the 87-point boundary?
- What are valid N/A cases for Android, scripts and measurement-less packages?
- How are appeals and temporary explicit exceptions approved and expired without weakening the standard?
- What secure runner/job layout and model credential isolation work for untrusted fork PRs?
