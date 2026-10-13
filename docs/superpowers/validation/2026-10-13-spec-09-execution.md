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
S1 core is implemented in `dbbcada84`, with review corrections in `0d22d1fc7`.
The independent task review reproduced protected constituent-term leakage and
loss of previously expanded evidence across query rounds. Three regressions
failed before fixes; 41 focused tests pass after them. Scoped re-review
approved both fixes with no new blocking findings. The original S1 run passed
363 focused tests, the lint/type ratchets
and all 527 template checks. Frozen campaign files remain unchanged.

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

Plan gate approved: Codex thread 01a134d3-3cc9-7082-84f9-d64733ec7f58. All six plans pass, with the revised S1 renderer, full-item expansion callback and out-of-lock omission handoff as prerequisites. Code/review/final/landed boxes remain unticked until their corresponding evidence exists.

Ruling: Pass the live context route's existing resolution through an optional
`resolved=None` breakdown parameter — 01i is landed, so preview must include
the known prompt-window cap without resolving twice — the cost if wrong is
adapting this optional display API; None retains absolute/context shares.

Ruling: Require explicit `source_campaigns` for non-turn tier 3 and qualify
`SceneRef` keys using an optional source campaign — the original contract had
no source-selection input and could collapse legacy sids or copied fork
identities — the cost if wrong is adapting Spec 12's future wrapper to this
keyword/key spelling. Query and ordinary turn refs remain compatible. The
amended spec/S6 plan gate passed before this seam is implemented.

The amendment review `01a13507-8e6f-7673-a9c0-2fd3a04601fd` found that
anchor-only exhaustion could suppress requested sources. Eligibility now
aggregates the explicit source scope before coverage, with unknown sources
preventing exhaustion. S6 must test first-scene and fully admitted anchors,
aggregate counts and unreadable-source non-exhaustion. Scoped re-review
`01a1350f-5c6b-7dc3-ac00-b4b69a46c0d7` approved with no material findings,
using the supplied public amendment packet because shell setup failed.

Ruling: Preserve the existing archive producer and add a separate private
`Prepared.archive_scan_text` carrying its exact un-clipped activation window —
the query scan intentionally filters director notes/images and can use a
different last-post window — the cost if wrong is adapting this private
preparation handoff. Query/Evidence schemas remain compatible; the archive
scan is used only for omission, never embedding. Ordinary already-appended
posts need no extra seed; director notes use compose's existing `wi_seed`.

## Isolated browser verification

The in-app Browser runtime could not start because its Windows setup helper
was missing. The fallback uses cached Playwright 1.61.1 and installed Edge,
with a fresh headless profile. The backend runs on 8199 with `GRIMOIRE_HOME`
set before imports to ignored worktree scratch; the frontend runs on 5199.
The first worlds response was asserted empty before API-seeding only Realm,
Saltmarch and Mara. Model replies are routed to the shared cassette mock.

S2 settings check: defaults 0/1200/0.35/off, three numeric fields saved and
retained after reload, rerank saved on and retained after reload then restored
off, no page errors. Screenshot was visually inspected in ignored scratch.
Two test-locator mistakes were corrected using observed UI semantics: the
accessible button name is Save despite uppercase CSS, and the Context pane
can be reopened through `?section=context`. No product change was needed.
Live inspector check also passed: real shared-cast evidence, structural reason
and rank, exhausted coverage, skipped:preview status and700-token ceiling are
visible, with no page errors. The synthetic older/current scenes each have
Mara and Seraphine, meeting the two-actor shared-cast rule. Turn checks and
helper shutdown remain pending.

## S2 task checkpoint

S2 implemented in `a80e6f77a`, with macro-empty evidence correction in
`2a624c406`. Independent review found a bare-header/false-evidence-id defect
when expanded summary text disappeared. Frozen summary spans now enforce
content admission;41 focused tests pass. Scoped re-review approved with no
new blocking findings. Final UI85/typecheck, lint/type/eslint ratchets and533
template checks passed; broader backend runs are recorded in the task report.
Old lore/frozen campaign files remain unchanged. Empty-enabled preview
diagnostics and existing test warnings are deferred for whole-branch triage.
