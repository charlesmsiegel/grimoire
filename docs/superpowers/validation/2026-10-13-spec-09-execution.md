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

Ruling: Resolve the proven Windows missing-ctypes gate blocker during final
acceptance by reusing existing unavailable-native recycling stubs, preserving
Windows identity/refusal and never adding permanent deletion -- the required
gate otherwise cannot pass -- cost if wrong is touching native-startup behavior
beyond historical retrieval. Normal ctypes-present behavior stays covered by
its owning tests. This narrow fix is assigned to the S6 implementer.

The user subsequently narrowed the remaining work to a basic implementation
of every surface and an immediate PR, explicitly instructing not to fix tests.
Further test/ratchet work, test repairs, Windows compatibility repair, task
review and full-gate work are deferred. The Windows repair had not begun; its
earlier assignment is withdrawn. Already committed work and verification
evidence remain intact. No deferred check is reported as passing.


## S6 basic implementation and PR handoff

S6 committed in `96f4e1a54`. Caller-directed non-turn tier-3 sources now carry
qualified identities, source-aware expansion/materialization and aggregate
coverage under shared semantic bounds. Ten synthetic recipe shapes, planted
gold graders, five offline signal arms, opt-in live reranking/downstream
generation, CLI dispatch and existing eval reporting are implemented.

Initial combined RED recorded 13 failures and one pass; additional corpus RED
recorded six failures. Source-scoping GREEN passed 10 tests before the latest
user steering. Final owned Python sources passed syntax-only compilation.
No final matrix, long report, live call or full-gate success is claimed.

The latest user instruction supersedes earlier requirements to repair the
Windows baseline or complete final verification/review before publication.
All six code boxes are marked implemented; S6 acceptance and review/final-gate
boxes remain open. This is a basic implementation PR with deferred validation.
The worktree and ignored task ledgers are retained; no merge is authorized.

Known S6 limits: recorded synthetic text rules rather than an exhaustive static
vector mapping; first-scene matrix coverage pending; phase timing is a coarse
semantic/cache-versus-remainder split; source-local lexical ranks need aggregate
calibration assessment; hard-rule/live contradiction graders are basic. The
final corpus/runner/CLI integration may fail and has not been rerun. The proven
clean-main Windows missing-ctypes test failure remains unchanged.

Theory: independent signals admit live historical evidence, deterministic
merge/expansion/packing produces a bounded frozen narrator prompt, and optional
semantic/rerank phases refine existing candidates within shared limits. The
implementation reuses SearchDocuments, overlay ancestry, vector cache/adapters,
relevance capture and eval reporting. Source qualification preserves campaign
provenance; generated isolated archives keep measurement independent of the
user's library. Default history recall remains off.

## Takeover: CI, acceptance and review (2026-10-13, PR #515)

The PR was taken over to finish implementation, testing and review. CI on the
opened head was red on ruff (10 pairs over baseline), pytest (10 failures on
py3.11/3.14) and the pydantic-1.10 job (the same failures).

**Fixes to make the gate pass.** The ten ruff pairs (two redefined loop
names in the source-qualification paths, the eval package's imports, a
`build` too complex to read, a blind catch now carrying its reason). The eval
generator called `scenes.set_branch_keys`, which lives on `scenes.write`, so
the offline suite inside `pytest backend` crashed before grading; with it
fixed, the semantic-only arm failed rule case 9 (below). The routing,
settings and operation-guard pins learned the `history_check` route and the
history runner's live generation. The rerank pair is now rendered by
`test_llm_fakes.py` and answered by a `campaign_flow` cassette entry, as
section 14 requires, with a test that its body parses to a grade. The eval
README's history commands give both platforms' interpreters. One test spied
on `compose_turn` without `history=`.

`test_serving_compression.py`'s two bundle tests fail on clean `main` too,
with the dependency set CI now installs: Starlette 1.8's `FileResponse` watches
for a disconnect while it streams, and the test's ASGI harness sent one
straight after the request. This branch fixed the harness too, but #514
landed the same repair on `main` first, so the rebase onto it keeps `main`'s
version and this branch no longer touches the file.

**Audit against the spec, and what changed.** A read-only spec-versus-code
audit found these, now fixed:

- A first scene embedded the query on every turn: `exhausted` now spends
  nothing (`semantic: skipped:exhausted`), tested.
- Ruling: a live set within `POOL_MAX` is tier 1's pool whole — the bound
  already pays for it, and a young campaign otherwise widened to a tier 2
  scoring the same documents, failing rule case 9 in the semantic-only arm.
  Spec 5.4 records the clarification. The cost if wrong is tier 1 scoring a
  few unadmitted documents in campaigns under 64 scenes.
- Case 9 now also plays a first scene (exhausted, tier 1, no request), and
  case 8 checks the excluded name is not a matched lexical term.
- A non-provider exception from the semantic stage (a cache write, a refused
  argument) discarded the structural and lexical evidence through `gather`'s
  catch; it now degrades to `failed:error` and keeps them (11.3).
- `gather_sync` re-raised a portal future cancelled at shutdown, which could
  strand the appended post (10.1, M7); it now degrades to `error`. A
  `skipped:locked` bridge said `exhausted`, which a planner would read as
  "nothing else exists"; it says `error`.
- With no Embedding role, an elapsed deadline reported `skipped:deadline`
  rather than `off`.
- `budget.fit` divided the ceiling by the setting's depth, not the
  selection's (`Query.max_scenes`).
- The inspector preview and the group round's outside-lock peek now fail
  soft.
- Settings copy says an unpriced endpoint makes Costs read incomplete (14);
  the template README lost a duplicated paragraph; `Decision.calls` no longer
  claims nothing in production reads it.
- New tests: a success clears an outage another caller noted (history and
  lore); no Embedding role is structural plus lexical at tier 1; a stalled
  endpoint through `gather` costs one phase and the next turn backs off; the
  off path reads no SearchDocument, resolves no space and builds no client;
  World info and history are the only sections with a `shed` hook.

Kept as they are, with reasons:

- The tier-2 lookahead loads (the S3 ruling above) stay: they count toward
  `WIDEN_LIMIT` and warm a cold campaign; tier 1 still scores at most
  `POOL_MAX`.
- `merge(..., rerank=)` stays a synchronous seam. 10 reranks its final merge
  once, and will adapt the async route callable when 10-S1 lands, rather
  than this slice making `merge` async ahead of its only caller.
- `shared_cast` counts present actors; a non-present speaker or addressee
  already admits through `actor`, which admits on its own.
- A phase-deadline timeout sets the shared memo, as section 10.2's
  acceptance asks ("the next turn hits the outage memo"). (Later reversed by
  the user's ruling below.)
- The Windows missing-`ctypes` failure (`test_compiled.py`, through
  `recycle_bin.py`) was on `main` and not this branch's; #514 fixed it on
  `main`, and this branch is rebased onto that.

**Adversarial review of the whole diff.** An independent review probed the
turn paths, locks, prompt leaks, freezing, budget, semantic publication and
tiers, and found them sound apart from these:

- Fixed: a `{{` in one recalled post closed by a `}}` in a later part is one
  macro to the block expander, which swallowed the span markers between and
  raised `KeyError` in compose — on legacy `post_chat`, after the player's
  post was appended. An item whose markers lost a partner is now dropped
  (its posts can no longer be named), and the section call in compose is
  guarded so recalled history can never fail a turn. Reproduced first.
- Fixed: with recall off, the round driver still peeked at the pending
  response and read the transcript before `gather` read the settings; it now
  asks `history_recall.enabled()` first.
- Not built: `gather_sync` blocks a threadpool worker on the portal while
  `gather` needs threadpool tokens itself, so about forty concurrent turns
  could starve the pool. A single-user app does not reach that; a bounded
  wait is the fix if it ever does.
- Ruled by the user: the phase deadline (4 s, which includes the file
  stages and a warm of up to 63 documents) cutting a slow but healthy
  endpoint used to set the shared outage memo, switching lore recall off for
  60 s. The memo stays, but only a failure the provider or transport
  reported trips it -- never the phase's own deadline (an `anyio`
  `TimeoutError` or the door's `embeddings.DEADLINE`). Spec 6.3, 10.2, 09-C2
  and section 15 say so. The cut still files an error row rather than
  `aborted`; `budgeted=True` would change that, and was not asked for.

**Acceptance runs.** The offline matrix (`--history`, inside `pytest
backend`) and the opt-in long matrix (`--history --long`, archives past the
64-document pool and the 1000-document bound) both pass section 12.5's
ordering on the recorded synthetic vectors: `hybrid` at or above every arm on
recall at 3 and strictly above `context-only` and `lexical`, every rule case
passing in every arm, and the packed section within its ceiling. These are
synthetic scoring fixtures, not a provider-quality claim. The live arm
(`--history --live`) needs a configured Embedding and Decision role and was
not run here.

An isolated end-to-end check (scratch store, mocked provider, cassette
replies) drove a narrator turn with recall on and off: on, the sent prompt
carried "Earlier in the campaign" with the earlier scene's header, summary
and excerpt lines and no signal or rank text, the prompt-log capture carried
the history row, and the live inspector sent no request; off, the prompt was
unchanged. It also caught a missing space in the Settings copy, fixed.

**Codex review of `6f94111`.** Eight P2 findings, each verified against the
code and fixed with a test that fails before the fix:

- Two legacy scenes can project to one SearchDocument key (it hashes the
  title, which defaults to the sid, and a null identity), and semantic ranks
  were keyed by that digest, so one scene took the other's rank and coverage
  never reached `exhausted`. Ranks and completeness are now per scene key; the
  vector stays shared by text.
- `merge` called a round's semantic incomplete unless it was off or preview;
  a round that reached `exhausted` with semantic `ok` evaluated everything and
  now counts as complete.
- A rerank that graded nothing (every answer declined) said `ok`; it says
  `skipped:unanswered`, and the inspector now shows the rerank status.
- Vectors saved before a cancellation stayed published (10.4). A stop now
  withdraws what the call saved, except vectors of a campaign whose rows may
  already have been recorded (a row naming a missing vector would cost 05 a
  re-embed).
- The shared-pool starvation the earlier review raised: the history phase's
  thread hops now run under their own per-loop limiter
  (`store/history/threads.py`), so a `def` route blocked in `gather_sync`
  holds a shared token the phase never needs. The embed-space guard accepts a
  `limiter=` on the worker that resolves the space. The optional rerank's
  capture still uses the shared pool, a residue only with rerank on.
- With recall on and nothing selected (empty, exhausted, an error), the
  inspector row and capture now carry the coverage as a text-less row, as a
  held-back-only World info row does; off is unchanged.
- A roll continuation in the same round is composed with that round's
  evidence (still never a retrieval), superseding the S3 ruling above.

**Codex review of `902ae20`.** Five P2 findings:

- Fixed: withdrawing a cancelled retrieval's vectors by text could delete
  another retrieval's concurrent save of the same text. Publication now saves
  each campaign's vectors only just before recording them, so a stop seen
  first writes nothing and nothing is ever withdrawn.
- Fixed: plain co-presence past `STRUCTURAL_PLAIN_CAP` ranks without
  admitting, but section 5.4 cuts the semantic pool from the structural list,
  so those scenes are now in the pool (after the admitted union) and keep
  their relations if semantic admits them.
- Fixed: the eval's `network_ms` timed all of `semantic.score` and its
  `file_ms` absorbed packing, rerank and live generation. Network is now the
  embed door and the rerank's decide calls, `retrieval_ms` is retrieval's own
  wall, and `file_ms` is their difference (12.3).
- Answered: the production rerank keeps the kit's Score form. Section 7.3
  makes Score the default and requires `pointwise` only of a Rank form, which
  stays implemented and tested for a later, measured switch.
- Answered: per-response regenerate replays its whole frozen snapshot (a
  legacy response with none is refused), history included, by the S2 ruling
  above; a fresh compose is the legacy regenerate path, which gathers.

**Codex review of `7e60100`.** Five P2 findings, all fixed with a test that
fails before the fix:

- Tier 3 ranked each named source alone, so every source had a rank 1 and a
  weak match first in one campaign tied a strong one first in another. Each
  signal's ranks are now renumbered over the aggregate by its own raw value
  (closing the "cross-source rank calibration" limit noted for S6; lexical
  scores still carry each source's own corpus statistics).
- The phase deadline is the turn's own budget, so both `history-recall`
  embed attempts pass `budgeted=True`: a request it cuts files `aborted`, not
  an error (the follow-up to the user's outage-memo ruling; CLAUDE.md's
  budgeted list names it).
- Compose re-fitted every item to `ceiling // setting depth` even when the
  query selected fewer (`max_scenes`). `Evidence.depth` now carries the
  selection's depth, and compose uses it (0, for hand-built evidence, keeps
  the setting's).
- The context-only eval arm credited gold, and passed the forbidden check,
  from the shared fact token alone. Each gold and forbidden scene now carries
  its own marker in its summary; context-only credits a gold scene only on its
  own marker, and every arm fails if a forbidden scene's marker is recalled --
  into the history section, or for context-only anywhere but the recap, which
  is recency by design (spec 1 and 16) and does carry a later absorbed scene.
- The rerank's network time is now only its `decide` await.

**Codex review of `ea1995d`.** Five P2 findings and none blocking. With the
user's ruling that the review loop ends once Codex finds nothing blocking,
these are the last round before merge. All are fixed with a test that fails
before the fix:

- Tier 3's renumbered lexical ranks still sorted source-local BM25 scores, so
  a term rare in one small source outranked a stronger match elsewhere.
  `lexical.score(others=)` now counts every named source's documents into the
  corpus statistics while each source scores only its own.
- `_prepare_sources` dropped each source's held-back structural matches. They
  are now qualified to their campaign at tier 3 and kept, across sources in
  structural order, for the semantic pool (5.4).
- The eval exempted the recap from the forbidden check. Every arm now grades
  its whole packed prompt. Case 7 plays later filler into the recap's window,
  because the recap is the campaign's newest one-liners whichever scene is
  played. That is main's behaviour, which 09 does not change, so the case
  measures the `before` rule rather than recency.
- The inspector row carried only the selected items. It now lists the
  strongest unselected candidates with their signals (`candidates`, capped at
  `HISTORY_UNSELECTED_SHOWN`) and counts them all (`unselected`), and the
  Context panel shows them.
- Lexical terms came from the whole scan window while the semantic text was
  clipped. Terms now come from the same `QUERY_BYTES` window, starting at its
  first whole word, which bounds a long post's term list and scoring pass.

**CI on `a26be68`.** `backend (py3.14)` failed one spec-03 test,
`test_compiled_purge.py::test_owned_schema_literals_are_erased_before_completion[fresh-metadata-current-enabled]`.
The test's last cache write was dropped. The cause was a compiled-cache session's
30 s idle timer, which outlives the state that scheduled it. Each cache test
installs a fresh `_State`, while sessions that other tests open (09's lexical
statistics among them) keep their timers. On firing, `_idle` took the lock of
whatever state was current. A batch's first fence read acquires that lock
without blocking, so a stray timer firing at the wrong instant refused the put.
This PR's tests open many more sessions, which raised the odds. `_idle` now
leaves a state alone when that state does not hold the timer's session.
Sessions are only ever built new, so the unlocked check turns away only what
the locked one would. `test_a_stray_idle_timer_never_takes_the_current_states_lock`
fails without the change.
