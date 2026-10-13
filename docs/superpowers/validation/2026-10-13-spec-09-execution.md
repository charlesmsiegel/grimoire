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
and rank, exhausted coverage, skipped:preview status and 700-token ceiling are
visible, with no page errors. The synthetic older/current scenes each have
Mara and Seraphine, meeting the two-actor shared-cast rule. Turn checks and
helper shutdown remain pending. S3's narrator reply chip sent the recalled
section to the local cassette mock, including the synthetic agreement summary;
an NPC reply omitted it as required. No page errors occurred. All chat, model
catalog and key-check endpoints were redirected to the local mock before the
test-only Primary role was configured. The persisted narrator prompt row also
passed assertions for both structural and lexical reasons, semantic status
off, and 32 packed tokens within its 700-token ceiling. The helpers were
stopped after this checkpoint; final verification will restart current code.

## S2 task checkpoint

S2 implemented in `a80e6f77a`, with macro-empty evidence correction in
`2a624c406`. Independent review found a bare-header/false-evidence-id defect
when expanded summary text disappeared. Frozen summary spans now enforce
content admission; 41 focused tests pass. Scoped re-review approved with no
new blocking findings. Final UI 85/typecheck, lint/type/eslint ratchets and 533
template checks passed; broader backend runs are recorded in the task report.
Old lore/frozen campaign files remain unchanged. Empty-enabled preview
diagnostics and existing test warnings are deferred for whole-branch triage.

Ruling: Preserve per-response regenerate's frozen snapshot and reuse its
captured history; fresh legacy regenerate gathers outside locks before compose —
current snapshot authority governs outdated spec route references — the cost
if wrong is needing an explicit recompose operation to refresh history during
rerolls. The existing generation request still runs; its history phase is
skipped.

## S3 task checkpoint

S3 implemented in `e92780979`. Independent task review approved without
blocking findings. The async worker/portal seam, once-per-round narrator gate,
locked replay authority and frozen retry/regenerate passed 29 focused and 697
surrounding tests. Lint/type ratchets remain unchanged. Existing dependency
deprecations are deferred to the final review; the full gate is still pending.

Ruling: Keep appended roll continuation's existing recomposition behavior,
with no new historical retrieval — S3 explicitly excludes that caller and
does not specify carrying history across a new compose — the cost if wrong
is adding a frozen-history handoff so earlier evidence survives the resume.
The final whole-branch review must judge this user expectation independently.

Ruling: Prepare bounded tier-2 lookahead when pre-semantic tier-1 coverage is
empty/thin, warming pool documents first in the single query request — cold
semantic-only retrieval otherwise has no warming path — the cost if wrong is
bounded speculative vector reads wasted if semantic makes tier 1 sufficient.
Lookahead counts toward the 1000 unique-load cap; only actual widening applies
its signals, and tier 1 still evaluates at most 64 union-pool documents.

Ruling: Complete available semantic coverage requires valid computed cosines
for every eligible document, rather than attempted cache loads — a missing or
evicted vector has not evaluated that signal — the cost if wrong is repeated
bounded widening while an incomplete cache warms. Tier 1 counts only its
applied pool scores; speculative lookahead cannot suppress actual widening.
Valid below-threshold scores count as evaluated. Load attempts enforce caps.

Ruling: Add optional `keep` to `record_embedded`, disabling cancelled staged
batches before flush and checking before each new vector save — cancellation
during materialized-row staging otherwise publishes advisory rows — the cost
if wrong is needing a stronger fence around in-flight atomic cache/file commits.
Existing 08 callers retain default behavior. An atomic operation already in
progress cannot be interrupted; the final review must judge that boundary.

Ruling: Carry an optional publication predicate into compiled batch flush,
checking again after acquiring its writer lock and before starting SQL — review
proved that cancellation during lock wait slips past the earlier callback — the
cost if wrong is adapting this new optional low-level cache seam. Existing
callers retain default behavior. Cancellation is distinct from lifecycle
invalidation and must not trigger a global cache purge after commit.

S4 fix verification also exposed a Windows baseline failure when its optional
native-clock test globally hides `ctypes`: `recycle_bin.py:183` imports it
unguarded. The exact node reproduces on clean main `c647d06c4`, with a fresh
synthetic TEMP home before imports, baseline sources and bytecode/cache output
disabled. Main source stayed untouched. This is separate from the S4 fix and
remains a required full-gate resolution before completion.

## S4 task checkpoint

S4 implemented in b3b05c7b4, with writer-lock cancellation correction in
71b55d485. Independent review reproduced a pre-transaction publication gap;
the deterministic regression failed before the fix. Six amended checks pass
for lock wait, default/guarded success and unrelated-row preservation during
staging, precommit and postcommit. Scoped re-review approved with no new
breakage. Final lint/type ratchets remain 1136/161. Earlier amended runs passed
90, 77 and 43 tests; broader run failures and corrected harness behavior are
recorded accurately in the task report. Full make check remains pending.

## User workflow steering

The user explicitly waived final review passes and authorized opening a pull
request after all six slices are implemented. Final whole-branch/native code
and diff-versus-spec review passes are skipped by that instruction, rather
than claimed as passed. Existing narrow task reviews and required make check
remain in the workflow. The PR destination is the verified public repository
charlesmsiegel/grimoire; no existing PR for this branch was found.

## S5 task checkpoint

S5 implemented in c8517df54. Independent task review approved with no findings.
Required guard/equivalence/resolver selection passed 772 tests; history/play
196 and docs/rerank108 also passed. Ruff1136/mypy161 remain at baseline.
The existing history kit and replay case are activated with captured decisions,
graded-slot-only ordering and reused frozen expansions. Full gate is pending.
