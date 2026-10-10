# 01b. Decision capture: slice plans S1–S3

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01b-decision-capture-design.md`
**Checklist:** `ROADMAP-CHECKLIST.md`, 01b-S1, 01b-S2, 01b-S3.
**Date:** 2026-10-10

One plan per slice, in landing order. Each slice is one commit on the same
branch, so a reviewer can read them apart. The gates are listed under
[Review record](#review-record).

The code was re-read before planning, as the spec asks. Nothing has landed on
this subsystem since the spec's baseline that moves a cited line's meaning.
01a-S1 has not landed, so S1 adds `_Call.stage` and `_Call.positions` itself
(spec §8 question 5).

## 01b-S1: Stage and batch positions on the decide outcome

**Goal.** Each call's capture outcome says which stage sent it (`stage`) and
which batch items it carried (`at`). The decide-chain golden moves by those
two keys and nothing else.

1. `inference._Call` gains `stage: int = 0` and `positions: tuple[int, ...] = ()`.
   `run_stages` sets both per stage: the index into the chain it was handed,
   and `tuple(pending)`.
2. `_at(call, unit)` maps a stage-local run of positions to batch positions.
   A backend called without positions (a test that calls one directly) reads
   its own positions as the batch's.
3. `_outcome(..., stage=, at=)` stamps both keys after `mode`/`provider`/`model`,
   ahead of `items` or `error`. `_structured` passes the chunk's unit and
   `_native` passes the item's index.
4. Re-record `tests/fixtures/decide_chain_golden.json`. Prove the diff by
   stripping `stage` and `at` from every capture and comparing the result
   with the old file: all 108 runs must match. The golden's docstring records
   the re-recording and cites spec §3.2.
5. Update the tests that pin an outcome dict exactly. They are
   `test_inference_decide.py` (three tests), `test_inference_decide_native.py`
   (two) and `test_character_turns.py` (the selector capture). Pin the
   positions of a fallback stage that was handed one item of three.

**Acceptance.** The golden check passes. The stripped comparison shows no
other change. The two continuity sites still capture nothing.

## 01b-S2: The capture helper, the decision pool and the four scene-level sites

**Goal.** Every scene-level decide site files one prompt-log entry per
decision, in a retention pool of its own. The UI lists those entries apart
from turns.

1. **`store/prompt_log.py`.**
   - `record(..., operation="")` writes `operation` into the row and the
     payload.
   - Eviction counts within the new entry's pool (`_pool`).
   - `_well_formed_row` and `_well_formed` accept an optional string
     `operation`.
   - `DECIDE = "decide"`.
2. **`inference.Recorder`.** A `Capture` subclass whose `settled(messages,
   outcome, target, error)` receives the call's `LLMError`. `_captured` calls
   `settled` for a `Recorder`, and the plain three-argument form for any
   other capture. The golden's capture and the test captures are untouched.
   - Why: the outcome holds the failure only as `"kind: detail"` text. The
     status and the code are not in it, and the spec says not to move
     `decisions.outcome` or the golden to carry them.
3. **`routes/common._record_prompt`** gains `operation=` and `fence=`. The
   fence is asked inside the existing non-blocking hold, after the scene
   check and before the write. A False answer writes nothing and stamps
   nothing.
4. **`routes/decision_capture.py`.**
   - `capturing(cid, sid, task, *, fence, abandoned)` is an async context
     manager.
   - `Scope.hook(part)` returns a `_Hook` (a `Recorder`) that only appends.
   - `Scope.note(part, key, value)` records what the site decided after the
     call.
   - `_record_of` replaces the failure text with `error_kind`, `error_status`
     and `code`.
   - `_breakdown` builds the sections: `message_{i}` for a scope of one call,
     `c{k}_message_{i}` and `call {k+1} · {role}` otherwise, then the
     envelope last.
   - `MAX_CALLS_IN_FULL = 4`: messages are dropped at append time.
     `MAX_OUTCOME_CHARS = 64_000`: whole records are dropped from the end, then
     the notes.
   - The index row's model is the first call that answered, else a stage-0
     call. The sampler report is the first call that went out, and none when
     it was native.
   - On entry, read `capturing()` and the strict identity, off the loop.
   - File in a worker thread on a normal exit, an `LLMError` or a
     `DecideRequestError`. Never file on any other exception, or when
     `abandoned()` answers True.
   - Re-raise unchanged. Every failure writes one warning naming the task and
     the exception type.
   - A scope that made no call and raised nothing files nothing.
   - `is_decision(entry)` is the server's form of the frontend's
     `isDecision`.
   - `OUTCOME_SECTION_ID` moves here. `character_turns` keeps a re-export, and
     its `_capture` (the generation path, and what the golden calls) builds
     its sections through `message_row` and `outcome_row`.
5. **Sites.**
   - The speaker pick (`character_turns._select`): the scope sits inside the
     existing `DecideRequestError` handler, so a refused request is filed and
     still becomes the invalid handoff.
   - Scene-break (`scenes._break_ask`).
   - The duplicate check (`scenes._resolve_identity`): one scope around the
     chunked call.
   - Voice drift (`scenes._stage_voice_drift`): the per-NPC loop goes inside
     one scope with `part = aid`. A new `abandoned=` parameter is passed
     `_review_abandoned(run)` from `_absorb_work`.
6. **The diff route.** `against=live` on a decision entry answers 409
   `not_comparable`.
7. **Frontend.**
   - `PromptEntry.operation` and the widened `task` union.
   - The five labels and `isDecision` in `turnLabels.ts`.
   - `SceneInspector`:
     - Turn history lists `turnRows`.
     - A Decisions `SideSection`, collapsed by default.
     - The compare picker offers no live side for a decision, and lists only
       same-task decisions (a turn lists only turns).
     - Clicking a decision row clears a live comparison.
   - `ConfigView.lastPrompt` reads the newest non-decision entry.
   - The Kept turn prompts caption and copy.
8. **Tests.**
   - `test_decision_capture.py` (new) drives most rules through the
     scene-break route, the one site a single call reaches.
   - The rules: the entry shape, a native failure with the fallback that
     answered, capture off, a raising filer, privacy, the four fence cases,
     the scope's exception rules, notes and parts, both size caps, both pools,
     the malformed `operation`, and the diff refusal for a new and a legacy
     entry.
   - Site tests sit beside their fixtures:
     - voice drift in `test_routes.py`;
     - the two-chunk duplicate check, and a failed check's kind-only record,
       in `test_absorb_identity.py`;
     - the speaker's rewritten envelope test in `test_character_turns.py`.
   - Frontend tests are in `SceneInspector.test.tsx` and
     `ConfigView.test.tsx`.

**Acceptance.** Spec §6 backend tests 1–5 for the four sites, test 6 without
the reconcile clause, test 7 for the scene pools, tests 8 and 10, the diff
clause of test 9, and tests 11 and 12. Frontend tests 1–3.

## 01b-S3: Campaign-level capture for the reconcile sweep, and the guard

**Goal.** The reconcile sweep captures each pass at campaign level, fenced on
its own run. A guard holds every decide call to the helper.

1. **`store/prompt_log.py`.** `NO_SCENE = ""`, and `_pool` gets a third pool
   for campaign decisions.
2. **`common._record_campaign_prompt(cid, task, breakdown, ...)`.** Inside the
   non-blocking hold it proves the campaign exists (`read_campaign`), asks
   the fence, writes and stamps the revision. It swallows
   `CampaignNotFound`, `StoreBusy` and `OSError`.
3. **`decision_capture`.** `NO_SCENE` re-exported. A `NO_SCENE` scope reads no
   identity and files through `_record_campaign_prompt`. The residual window
   before `forget_subject` is stated in the module docstring.
4. **`continuity._adjudicate`.** One scope per pass, with
   `fence=lambda: not stillborn()`.
5. **Routes.** `GET /campaigns/{cid}/prompts` and `GET
   /campaigns/{cid}/prompts/{eid}`, scoped to `NO_SCENE`. Both answer 404 for
   an unknown campaign.
6. **Frontend.**
   - `api.listCampaignPrompts` and `api.getCampaignPrompt`.
   - `continuity/SweepCaptures.tsx`: a collapsed "What the sweep asked"
     `<details>` under the pane's Refresh. It is read on open and re-read when
     a refresh settles. A row opens the capture read-only through
     `ContextBreakdown`, with a back button.
   - Held state is keyed by campaign.
7. **The guard: `tests/test_capture_guard.py`.**
   - It reuses `test_operation_guard`'s `decide_calls` and `_walk`.
   - In `routes/`, a call needs `capture=<scope>.hook(...)` with `<scope>`
     bound by an enclosing `async with ...capturing(...) as <scope>`.
   - Elsewhere, a call needs `capture=<parameter of the enclosing function>`.
   - Anything else needs `# capture-ok: <reason>`. An empty reason fails, and
     the markers are capped at 2.
   - Planted cases cover both directions, and a vacuity check requires at
     least five sites.
   - The guard is shown to flag the reconcile site as it stood before this
     slice.
8. **Docs.**
   - CLAUDE.md's "capture nothing" sentence is rewritten to name the helper,
     the pools, the campaign-level entries and the guard.
   - CONTRIBUTING's guard table gains `test_capture_guard.py` and
     `# capture-ok:`.
9. **Tests.**
   - A sweep files one campaign-level entry: no scene lists it, and no scene
     route reads it.
   - The campaign routes list only `NO_SCENE` entries.
   - A forgotten sweep files nothing. Removing the fence shows that this test
     can fail.
   - The campaign pool.
   - The frontend list opens an entry read-only (`LedgerContinuity.test.tsx`).

**Acceptance.** The reconcile clause of §6 test 6, the campaign-pool clauses
of test 7, the two campaign-route clauses of test 9, and frontend test 4. The
guard fails on a planted uncaptured `decide` and passes on the tree.

## Deliberate deviations from the spec

- **The guard's location.** The capture guard is `test_capture_guard.py`, not
  part of `test_decision_capture.py`. `test_docs_guard` holds every
  `test_*guard*.py` to CONTRIBUTING's guard table, and the marker family to
  the same page, so a guard file by that name is where a contributor looks.
- **Where the site tests live.** The voice-drift and duplicate-check site
  tests live beside the fixtures that build those scenes (`test_routes.py`,
  `test_absorb_identity.py`). Copying those fixtures into the new file would
  have created a second copy to keep in step.
- **A scope that decided nothing files nothing.** For example, a voice-drift
  phase whose clock refused every NPC. The spec files "one entry per decision
  it makes", and such a scope made none. A refused request
  (`DecideRequestError`) still files its `calls: []` entry.

## Review record

Codex is not available in the environment this was built in. Each gate below
was a substitute review, recorded here, and **the Codex gates remain owed**
for all three slices. This follows the convention the Status table uses for
spec gates.

- **Plan gate:** substitute. This plan was written as built, and checked
  against the spec section by section by the final-gate review below.
- **Review** (in place of `/codex:review`): an independent adversarial review
  of `git diff bd24132..HEAD`. Findings and their resolution are below.
- **Final gate** (the diff against the spec): the same review's conformance
  table, clause by clause.

Findings: no blocking finding, two should-fix and five minor. Each was
checked against the code.

- **Fixed (should-fix).** The review's `abandoned` check ran outside the hold
  that covers the write, and the duplicate check had none. A Discard landing
  in between still filed a capture for a dismissed review.
  - Both absorb sites now also pass a synchronous fence over the review's
    flags (`_review_stopped`, `_still_wanted`), asked inside the hold.
- **Fixed (should-fix).** A campaign-level scope could be opened without a
  fence, leaving only the campaign-exists check, which is the residual the
  spec accepts for the sweep alone.
  - Such a scope now captures nothing and logs a counts-only warning.
- **Fixed.**
  - Calls refused unsent no longer take any of the four in-full places.
  - Once the places are full a native body is no longer built
    (`Recorder.wants_messages`).
  - `_captured`'s warning names the exception type, not its text.
    `test_a_raising_capture_leaves_the_decision_and_the_ok_row` pinned the
    text, and is updated to C2's rule.
  - "What the sweep asked" clears a stale read error.
  - The two cosmetic nits.
- **Left as is.**
  - The sweep list re-reads when a Refresh or a followed sweep settles, but
    not for a sweep the hook never followed. Reopening the list re-reads it.
  - Test 3 is covered through the scene-break site alone. Every site gets
    its hook from the same `Scope.hook`.
- **Conformance.** Every requirement of spec §3, §4, §6 and the slices' scope
  and acceptance was found implemented. The three deviations above were
  judged acceptable.
