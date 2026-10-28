# Per-Package B+ Quality Gate

**Outcome:** Block merges only when calibrated evidence shows an applicable package or theory report falls below B+ (87/100).

## Scope
- Require individual package/report results, not a repository average.
- Distinguish missing credentials, incomplete analysis, N/A, candidates and genuine score failures.
- Secure judging against untrusted PRs; preserve ordinary tests, lint ratchets and reviews.

## Milestones
1. Report-only shadow checks with pinned tool and model identities.
2. Quantify rerun stability and false-positive appeals on unchanged SHAs.
3. Activate a reviewed CI check with explicit expiring exception policy.

## Proving scenario
A package at 86.9 blocks, an unavailable doctor fails as infrastructure rather than 0/100, and a fork PR cannot exfiltrate secrets.

## Independence and interfaces
Depends on calibrated judgments, not on completing every other engineering-health project; rollout is a separate explicit decision.

## Open questions
- Whole-repo or impacted-package grading?
- How should boundary scores be appealed?
- What is an acceptable secure judging runner?
