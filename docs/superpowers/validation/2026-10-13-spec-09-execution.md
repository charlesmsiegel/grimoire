# Spec 09 execution record

Worktree: `spec-09-hybrid-history`, based on `main` at `c647d06c4`.
Spec: `docs/superpowers/specs/2026-10-09-roadmap-09-hybrid-historical-retrieval-design.md`.

The originating request authorizes implementation of the six slices in this
isolated worktree. Each slice has its own plan; no slice is marked landed
until it is actually on main.

## Spec gate

Codex adversarial review on 2026-10-13 found semantic pool overrun, premature
exhaustion and a missing macro-expanded section ceiling. These were corrected
in the spec with required counterexample tests. A scoped re-review accepted
exhaustion and the macro fit and found a lexical-candidate retention conflict;
the candidate bound now follows the live set separately from semantic caps.
The final scoped Codex re-review approved with no material findings.

Review threads: `01a13448-4d28-7003-81a2-22f096a29398`,
`01a13453-a60e-79d1-80fc-76b9ec6572ce`,
`01a134b3-b063-7ee2-892a-6e6614bb625d`.
Sandbox shell startup failed for the reviewer, so the first review used public
GitHub `main` at the exact starting commit, and re-reviews used supplied diffs.
No private store/configuration content was sent.

## Baseline

Focused worktree tests: 26 lore-golden/rerank-kit tests and 169 SearchDocument
scene/live/expansion/embedding tests passed. The existing FastAPI/Starlette
deprecation warning was present. Frontend dependencies installed from lockfile.

## Implementation stages

All six slice plans written. The first plan-gate attempt could not read the
local sandbox. The second reviewed supplied plans/current spec patch and passed
S3–S6, conditional on corrected prerequisites. It found S1 renderer staging
and S2 frozen-evidence refill gaps, now corrected; scoped re-review approved with no material findings.
No product implementation yet. S1's implementer has performed read-only API
preparation only and is waiting for plan approval.

Ruling: Move the authoritative templates/shared renderer into S1 — its budget
tests must count real template output — the cost if wrong is earlier template
coupling, rather than differing budget and compose renderers.

Ruling: Compute summary omission and refill before compose, outside locks —
frozen Evidence has no expansion seam and group compose holds a lock — the
cost if wrong is refilling fewer scenes when source/layout changes mid-phase;
composition still cannot perform another retrieval.

Ruling: Merge's bound expansion callback returns a complete EvidenceItem —
unselected Candidate records do not contain header/summary — the cost if wrong
is adapting the callback when spec 10 integrates, with no change to the
low-level excerpt contract or public candidate schema.

Plan gate approved: Codex thread 01a134d3-3cc9-7082-84f9-d64733ec7f58. All six plans pass, with the revised S1 renderer, full-item expansion callback and out-of-lock omission handoff as prerequisites. S1 implementation starts next; code/review/final/landed boxes remain unticked.
