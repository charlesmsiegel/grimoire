# Continuity Capstone — Slice G: Observability, Performance, Docs and Acceptance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the continuity capstone. Hold its model calls and log rows to §29, audit the cost rules of §25 and fix what the audit finds, make every review surface speak §30's language, bring the onboarding documents in line with what the capstone added, prove each of §32's acceptance criteria and §33's stopping rule by a named test on the integrated tree, and run the two final review gates over the whole capstone.

**Architecture:** G adds no feature (§33). Most of it is tests that pin what Slices A–F already ship. The rest is the fixes those pins and the hand-offs force:

- `similarity` normalizes each record's identity text once per sweep, not once per pair (§25.2);
- actor names are read from a record's meta, never from its card versions and image directories (the existing `characters.name_and_versions`, and F's `pcs.name_of`, behind `relationships.actor_name` and the saved-idea read);
- a delete whose continuity cascade fails after the delete landed answers a structured `partial_delete` 500 that says the delete landed, and keeps moving the write token (`routes/ledger.forget_or_partial`);
- the review surfaces speak in words: relation words in journal labels and refusals, reason sentences and missing-record names in Reviewed links / merges, and one wording for each link relation across the Ledger, the Story Graph and the journal;
- four small hand-off fixes: an unmerge refreshes the rail, `birthdays.crossed` skips an unreadable birthdate, a failed reconcile run's error has a TS type, and the scene list's "Try again" reads the list again.

There are three new test modules: `test_continuity_read_cost.py` (constant reads, and no model or embedding call on any read path), `test_continuity_wording.py` (§30) and `test_capstone_acceptance.py` (the spec's acceptance appendix cites tests that exist, and §28.10's cases each have a counterexample). Existing suites and `test_docs_guard.py` gain tests. The evals gain one counterexample recording. The docs work touches CONTRIBUTING's guard table, store-guarantees' derived-sidecar bullet, one README bullet, `evals/README.md`'s variant list, `PHASE-STATUS.md`, and the spec (amendments, a new Appendix B, and the Status line).

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest + `TestClient`; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice G:

- §29, all of it, pinned;
- §25.1–§25.4, audited and pinned, including F's §19.7 hand-offs (F plan Decisions 4 and 5), with a ruling on the shell's pre-capstone transcript read (Decision 17);
- §30, on every surface the capstone added: the avoid-list guard reads every source file, and each preferred phrase is pinned to the surface that shows it;
- §28.10, each of its ten cases held to a grader check that a counterexample recording trips;
- the docs list in §27 and §31 G: CLAUDE.md's detached-run inventory, CONTRIBUTING's guard table, templates/README.md, docs/store-guarantees.md, and §30's optional README mention;
- §0 steps 6 and 7 (the two final gates), over the whole capstone, in CLAUDE.md's order;
- §32 AC1–AC19 and §33, walked on the integrated tree;
- every hand-off A–F addressed to G or left deferred, each closed by a task here or recorded, with its reason, in the spec.

Out of scope, by §33 or by ruling (each is recorded in the spec at Task 7): groups and facts as graph nodes; encoding record ids in the Ledger's mutator paths; the Hebrew month-only birthday residual; `clock.read`'s tolerance of a hand-edited clock.json.

**Branching:** Work on `claude/continuity-capstone-slice-g`. It was cut from the Slice E tip to hold this plan, and it is rebased onto the **Slice F tip** before Task 1 (stacked slices, rebased onto `main` at landing, the owner's direction). If A–F gain fixes after that, rebase onto the new F tip.

**Before Task 1:**

1. Run `git status`. If the tree holds edits that are not this slice's, stop and ask the owner; never sweep them into a Slice G commit. Confirm the base: `git merge-base --is-ancestor claude/continuity-capstone-slice-f HEAD` succeeds, and `git log --oneline claude/continuity-capstone-slice-f..HEAD` lists only this plan's commits. Confirm that the F slice ledger records F's gate as complete. If it does not, stop: G audits the integrated capstone, and an F still in flight would move under it.
2. Create the ledger, `.superpowers/sdd/2026-10-06-continuity-capstone-g-polish/progress.md` (gitignored). Record the base gate in it. From the repo root, run `make check PY=$PWD/backend/.venv/bin/python` (every target) and `backend/.venv/bin/python evals/run.py`. Write down each pre-existing failure: the test id and a one-line cause. Task 8 may excuse only those. `backend/tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and it is environmental.
3. Confirm the boundary commits of the capstone's ranges on `main`. They are fixed history (Global Constraints, "The capstone diff"). `git log --oneline -1 <sha>` must print the subjects listed there. If one does not, stop and ask: the final review would be scoped wrong.
4. Confirm that every name this plan consumes exists on the F tip with the signature given below. If a name moved, adapt the affected Interfaces block **before** that task starts, and record the move in the ledger.
   - **C:** `routes/scenes._identity_outcome`, which logs `"continuity identity check"`. `identity.Examination.counts()` has the keys `proposed`, `examined`, `candidates`, `deterministic`, `semantic`, `embedded`, `existing`, `new`, `uncertain`, `unchecked`, `downgraded` and `hint_only`. `identity.CHECK_DECISIONS`. `similarity.Subject`, `lexical`, `tokens`, `trigrams`, `body`, `_TEXT`, `pool`, `semantic`, `embed_missing`. `llm_errors.KINDS`. Also `test_absorb_identity.py`'s fixtures and helpers: `scene`, `_seed_ledger`, `_llm`, `_absorb`, `_decisions`, `_configure_embeddings` and `identity_requests`.
   - **D:** `routes/continuity._log_pass` and `_WORDS`; `_sweep_pass`, `_passes`, `start_reconcile`, `schedule_reconcile` and `get_continuity`. `reconcile.discover(cid, *, stamp, full, touched, embed)`, `persist_found`, `select`, `build_payload`, `persist_proposals`, `RECONCILE_MAX_PAIRS`, `RECONCILE_WARM_LIMIT`, `DECISIONS`, `SWEEPS`. `pending.findings`, `pending.Current.load`, `pending.fingerprint`. `candidates.write`. `review.describe`, `create_link`, `_link_label`, `remove_link`, `forget_ref`. `todo._Ctx`, `_chore_continuity_overlaps`, `_chore_continuity_closures`, `_items_continuity_overlaps`, `_items_continuity_closures`. In `test_continuity_reconcile_routes.py`: `_campaign`, `_threads`, `_key`, `_install`, `_entry`, `_reply`, `_duplicate`, `_held`, `_refresh`, `_settled`, `_reconcile_requests`, `_reconcile_row`, `_plot_edit`, `_applied`, `TOUCHED`, `REASON`. In `test_continuity_reconcile.py`: `_configure`, `_chores`, `_sweep`, `_embedded`, `_write_basis`. On the frontend: `components/continuity/labels.ts`, `ReviewedGroup.tsx`, `useContinuityReview.failedNote`, `api.removeAlias`, `api.removeLink`, and `onShellChanged` in `appEvents.ts`.
   - **E:** `drivers.snapshot(cid, offscreen=False, *, pressure_result=None)`. `suggest._actor_name` and `_IdeaContext`. Three tests in `tests/test_suggestion_controls_route.py`: `test_opening_paths_make_no_model_call`, `test_saved_card_round_trip_goes_stale_after_resolution` and `test_cassette_reply_with_drivers_parses` (E Task 12).
   - **F:** `pcs.name_of(root, pid)` and `overlay.pc_roster` (F Task 1). `graph.build(cid)`, `graph.NODE_KINDS` and `graph.EDGE_KINDS` (F Task 2). `GET /campaigns/{cid}/continuity/graph` (F Task 7). `RELATION_PHRASE: Record<LinkRelation, { out: string; in: string }>` in `frontend/src/components/storyGraph/model.ts`, and the `LinkRelation` union in `api/types.ts` (F Tasks 8 and 9). Several tests in `tests/test_continuity_graph.py`: `test_graph_tuples_are_pinned`, `test_scenes_follow_play_order_not_recency_or_date`, `test_focusable_is_exactly_the_chooser_driver_set`, `test_anchorable_is_exactly_the_chooser_anchor_set`, `test_reads_do_not_grow_with_the_node_count`, `test_graph_reads_no_card_or_image` and `test_graph_makes_no_model_call`. `tests/test_continuity_routes.py::test_graph_route_makes_no_model_call`. In `StoryGraphView.test.tsx`: "renders the drawing from one graph read" and "only one graph read across mount, lenses, toggles, arcs and nodes". `REQUIRED_PRESENT` in `test_continuity_writer_guard.py` includes `graph`.
   - **Pre-capstone:** `characters.name_and_versions(root, cid) -> tuple[str, list[str]]` (reads no card, raises `CharacterNotFound` where `read_character` would, stat-memoized), `overlay.char_root` and `overlay.pc_root`, `store.scenes.mark_absorbed(cid, sid, one_line, summary)`, `POST /campaigns/{cid}/advance/preview` (`@computes_only`, body `AdvanceTime`), the `_CampaignActivityStamp` middleware in `main.py` (it stamps a mutating response below 300, and bumps the revision on an unhandled raise), and `evals.cases.Recording(variant, expect_fail, ext)`.
5. Collect the hand-offs. Read the A–F slice ledgers (`.superpowers/sdd/2026-10-05-continuity-capstone-{a,b,c,d,e,f}-*/progress.md`, in whichever checkout or worktree ran that slice; record a ledger that cannot be found as absent). Read them only after step 1 has confirmed F's gate complete, so the F ledger holds F's Task 14 Step 9 hand-offs and the E ledger its final lines. Copy every item that names Slice G, polish, docs, observability, performance or "deferred" into this ledger under `## Inherited hand-offs`. Use one line per item, with its source (file and line) and either an owning task or a recorded reason for deferring it. Seed the section with the items below; their owners are this plan's decisions. An item the ledgers hold that is not seeded here, and whose fix would change code outside Decisions 6 and 9–12, is a scope question: record it and ask the owner before Task 1, rather than widen Scope silently.
   - **(a1)** A, final review h: an I/O failure mid-cascade answers 500 without a revision bump. The premise is false: the activity middleware already bumps the revision on an unhandled raise (`main.py`, pre-capstone), so the token moves today. What is missing is a body that says the delete landed. **Task 4** (Decision 10) structures the 500 and keeps the token moving.
   - **(a2)** A, F8 (cycle detection uses the raw alias graph), F11 (`create_alias`'s `source` is unvalidated), and review d and f (`affected` under-reports over hand-edited cycles; the private `_text` helpers do not strip). **Deferred.** Each is reachable only by a hand edit or an internal parameter, and `effective.live_canon` already stops at the bad hop.
   - **(a3)** A, F6 (the Ledger force-delete path) and F14 (GET /continuity re-reads per label). **Closed** by D Task 15 ("Delete anyway"), A's `test_get_continuity_reads_each_ledger_once`, and D's `test_get_continuity_with_suppressions_reads_each_ledger_a_bounded_number_of_times`.
   - **(b1)** B, Task 1 note: an OverflowError from a huge-year birthdate escapes `birthdays.crossed`, and so an advance. **Task 4** (Decision 11).
   - **(b2)** B, Task 4 note: `clock.read` raises on a hand-edited clock.json holding a very long integer or deep nesting. **Deferred.** It is a pre-capstone reader. The capstone reads the clock only through soft wrappers (`pressure._soft`, `_IdeaContext.load`), so no capstone surface fails on it.
   - **(b3)** B, final minors. The parseable-due-with-no-clock behaviour is **closed**: spec §13.5 states it. For "two out-of-plan store changes recorded only in the ledger", Task 7 records the two tolerance widenings (`events._UNREADABLE`, `continuity.doc._UNREADABLE`) under §31 Slice B, unless the spec already names them.
   - **(b4)** B Open Question, and E's h2: Hebrew month-only birthdays (`--Adar`) disappear in leap years. **Deferred.** The fix is in the month-key match, which the intent prompt's Birthdays line renders, and §15 holds that prompt byte-identical. Task 7 records it as a residual in §13.6.
   - **(c1)** C, final minors: the `before !== ""` "Switched" label (a race), the candidate titles folded by `_text` (display-only), a moved candidate's raw `latest_beat` (display-only), `identityProposal`'s bare id for a record that was not offered (the model's own spelling of an id it named), a spent budget skipping semantic matching with no `fallback` (a race), `distinguished_from` carrying an id `_offerable` excluded (hand-edited ids only), and fallback survival pinned for two of five paths. **Deferred**, each display-only, race-only or hand-edit-only.
   - **(c2)** C Task 14 Step 7, and B Task 11 Steps 7–8: the final adversarial review against the spec may not have run for C and B. **Task 8**: the whole-capstone gates cover them.
   - **(d1)** D Task 19: `removeAlias` and `removeLink` do not call `notifyShell`. **Task 4** (Decision 11).
   - **(d2)** D Task 19: the Ledger's delete and mutator paths keep record ids unencoded. **Deferred.** This is pre-capstone Ledger behaviour. New ids are slugs (`materializer` allocates `slugify(title)`, and an explicit new id must already be one). Fixing a `/` in a legacy id needs a path converter on every sibling ledger route. Recorded in the spec as a residual (Task 7).
   - **(d3)** D Task 19: GET /continuity's raw links carry no titles. **Task 5** (Decision 12).
   - **(d4)** D Task 19: `perform()` has no moved-reader guard. **Closed** by D's final fix. Cite "a reviewed action refused after a campaign switch says nothing on the new campaign" and its two siblings in `LedgerContinuity.test.tsx`.
   - **(d5)** D Task 19, performance items: a zero-vector text is re-sent on every incremental sweep, and refs pended after a run's last take wait for the next fresh run. **Task 2** (Decisions 4 and 5).
   - **(d6)** D Task 19: /codex reviews for D. **Task 8.**
   - **(d7)** D, final minors. The failed-run error type goes to **Task 4**. The follow-on persist-1 malformed branch is **closed**: confirm `test_a_follow_on_pass_refused_at_its_persist_1_keeps_the_first_pass` and the "a follow-on pass that failed" case in `LedgerContinuity.test.tsx` exist. Two more are **deferred** as copy nuance in rare sequences, where the next Refresh corrects the note: a second Refresh pass overwriting the first pass's note, and FOLLOW_ON_NOTE replacing NO_MODEL_NOTE.
   - **(e1)** E Tasks 3, 5 and 9: an undated campaign's near and move controls are no-ops rather than refusals. **Closed** by E's final fix `737c236` ("fix(chooser): near and move are unavailable when the campaign has no date"): the chooser disables both with a visible hint and never sends either without a date, and spec §16.2 says so. E's ledger withdrew its own hand-off, because its premise (that the drivers read's `now` is clock-only) was false. The route still reads an undated near or move as a skipped check (§26, E Decision 12). It is not a deferral, so it is not listed in the Slice G rulings (Task 7).
   - **(e2)** E, Task 13 hand-offs: `/codex:review` and `/codex:adversarial-review` for Slice E. Codex was unavailable, and E's Task 13 implementer stood in. **Task 8**: the whole-capstone gates cover E, as for (c2) and (d6).
   - **(e3)** E, Task 13 hand-offs: tune `DRIVER_PROMPT_CAP` against real prompts (the index and the rendered timeline share it). **Deferred.** Tuning needs real prompts, and G measures only synthetic stores (Global Constraints, Privacy). The constant's comment already says to tune it against real prompts later. Task 7 records it under §31 Slice G rulings, with no figure.
   - **(e4)** E, Task 11 ruling and Task 13 hand-offs: ScenesView's error banner "Try again" clears the banner without reading the scene list again. It predates the capstone, but E's seeded chooser had to route around it. **Task 4** (Decision 11).
   - **(f1)** F Decision 4: literal "once per request". **Task 3** (Decision 7).
   - **(f2)** F Decision 5: narrowing the location-name read. **Task 3** (Decision 8).
   - **(f3)** F: groups and facts as optional graph nodes. **Deferred.** §19.2 makes them optional, and §33 parks "more graph node families".
   - **(f4)** F: the inventory lines that might name the graph. **Task 6** updates CONTRIBUTING's guard row; nothing else needs the graph named (Decision 13).
   - **(f5)** F Open Questions. A scene stamped in a secondary calendar's notation reads as Undated: **ruled honest** by §3.8 (primary provider only). A very large campaign draws one wide canvas: **deferred**, because §19.7 forbids a cap and column virtualization is the stated fix if one is ever needed.
   - **(f6)** F, setup note and h8: the `by` link relation reads "Due by" in the graph's `RELATION_PHRASE` and "By" in D's `RELATION_PHRASES`, and "G's wording pass picks one". **Task 5** (Decision 12).

---

## Global Constraints

- **Privacy.** Fixtures, scratch scripts, docstrings, comments and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch. Phrases built from them are fine ("Mara's errand 3", "Winifred's debt", "Saltmarch market"), as are ids already used as placeholders in the tree (`mara-s-map`, `mara-s-oath`, `the-coronation`, `find-the-ledger`, `the-midnight-deadline`, `winifred-s-chart`). Never read `~/.grimoire` or any real store. No committed text describes store contents, counts or distributions. Performance is measured only on synthetic stores and pools built in tests or scratch scripts. Figures are recorded in the ledger, never in committed text. Committed docstrings argue structurally and say "to be tuned against real prompts later".
- **No model names or identifiers** in any committed text (code, comments, fixtures, docs, commit messages).
- **Scope (§33).** No new route, store file, candidate kind, node or edge kind, driver kind, link relation, prompt template or LLM call. Code changes are confined to the fixes named in Decisions 6, 9, 10, 11 and 12, and to any violation a pin in Tasks 1–3 finds. Decision 11 includes the scene list's retry, and Decision 12 includes F's `storyGraph/model.ts`. Each violation is fixed in the module that owns it, with a RED test first. A pin that fails for a test-setup reason, such as a missing marker or fixture or a fake that cannot be built, is fixed in the test and recorded in the ledger, never in the producer. Task 7's counterexample recording is test data: it adds no eval case and changes no prompt.
- **Review gates.** Run `/codex:review` and `/codex:adversarial-review` if `codex` is on `PATH` (`which codex`). Otherwise each gate is stood in for by independent Claude reviewers: fresh subagents that wrote none of the capstone's code, which is the substitute the owner chose for every gate in this capstone. The ledger records which reviewers stood in, their briefs, and every finding with its resolution.
- **Spec values (verbatim):**
  - the route (§29): `Route("continuity", "Continuity checks", "The duplicate check beside absorb and the reconciliation sweep after End Scene or a refresh.", ("continuity-identity", "continuity-reconcile"), True)`;
  - log rows (§29): counts only, carrying the campaign id, the candidate count, deterministic vs semantic candidate counts, the embedding mode (`off` | `configured` | `failure`), resolver decision counts, `pairs_capped` and `superseded`; no title, beat, identity text, reason or prompt;
  - §30 prefers "Possible overlap", "May be finished", "Needs resolution review", "Basic matching active", "Semantic matching not configured", "Merged into", "Continuation of" and "Claims to address". It avoids "AI detected duplicate", "Continuity score", "Broken campaign" and "Embedding required";
  - §25.1: at most one identity-resolver call per absorb; at most one reconciliation call per run; never one call per pair.
- **Copy (verbatim, pinned by tests):**
  - `review.RELATION_WORDS`: `continues` → "continues", `subthread_of` → "is a subthread of", `pays_off` → "pays off", `before` → "is due before", `on` → "is due on", `after` → "is due after", `by` → "is due by", `related_to` → "is related to". A relation the table does not hold reads "is linked to". These are the sentence forms of §5.3's Meaning column ("a is due before / on / after / by event b").
  - `RELATION_PHRASES` (`labels.ts`): `by` → "Due by"; every other entry is unchanged. F's `RELATION_PHRASE[r].out` equals `RELATION_PHRASES[r]` for every `LinkRelation`, so the Ledger and the Story Graph lead a link with one phrase.
  - Link refusals: "That kind of link cannot join these two records." (`create_link`) and "That kind of link cannot join these records in that direction." (`_plan_link`).
  - `BROKEN_REASONS` (`labels.ts`):
    - `missing_source` → "The merged record no longer exists.";
    - `missing_target` → "The record it was merged into no longer exists.";
    - `wrong_type` → "It joins two different kinds of record.";
    - `cycle` → "It is part of a loop of merges.";
    - `malformed_record` → "The stored entry could not be read.";
    - `missing_endpoint` → "One of its records no longer exists.";
    - `self_collapsing` → "Both ends are now the same record.";
    - `invalid_relation` → "This kind of link cannot join these records.";
    - any other code → "This entry can no longer be followed."
  - `missingName(ref)`: "a missing thread", "a missing commitment" or "a missing event", by prefix; "a missing record" otherwise.
  - Partial delete: HTTP 500 whose body is `{"kind": "partial_delete", "landed": [<noun>], "detail": <sentence>}`. The keys sit at the top level, because the app's `HTTPException` handler (`main.py`) hoists a dict `detail`. `<noun>` is "thread", "commitment" or "event", and the sentence depends on it:
    - thread or commitment (the delete is journalled, so Undo restores it): "the <noun> was deleted, but its merges and links could not all be removed (<ExceptionName>); the delete can be undone";
    - event (`store.events.delete` writes no journal row, and an event is never merged): "the event was deleted, but its links could not all be removed (<ExceptionName>); remove the rest from Reviewed links / merges".
- **Write token.** A write answered non-2xx after part of it landed must leave the token moved. The activity middleware (`main.py`, `_CampaignActivityStamp`) stamps a mutating response only below 300, and bumps the revision when a route raises an unhandled exception. An `HTTPException` reaches neither, because Starlette turns it into a response inside the middleware. So a route that answers a partial write with an `HTTPException` calls `store.revision.bump(cid)` itself (CLAUDE.md, "A campaign carries a write token").
- **Imports and guards.** Imports stay at module scope, the graph stays acyclic, and modules inside `store/` bind submodules (`test_import_guard`). No new mutator is added, so `locks.DOMAIN_MODULES` is unchanged and `UNREVIEWED` does not grow. Any call handing a root to `characters.*` or `pcs.*` passes an overlay-resolved root (`test_overlay_guard`).
- **Lint ratchet.** `lint-baselines/*.json` never grows. Every new function stays at ruff complexity ≤ 10. Every new `except Exception` carries `# noqa: BLE001 -- <reason>`. Exception classes end in `Error`. Every backend task ends with `make check-lint check-mypy`, and every frontend task with `make check-eslint`, because `check-web` runs no eslint. Each of those lint steps expects PASS at baseline, or an improvement handled in the same task: `scripts/ratchet.py` exits 1 on an improvement, so when it reports one, run `make baseline PY=$PWD/backend/.venv/bin/python` and stage the changed `lint-baselines/<tool>.json` in that task's commit.
- **Docs rules.** No two of README, CONTRIBUTING, AGENTS, store-guarantees, the screenshots README and CLAUDE.md may share a run of more than 16 words (`test_no_document_restates_another`). A venv command added to `CLAUDE.md`, `README.md`, `evals/README.md` or the frozen-campaign README gives both `.venv/bin/python` and `.venv/Scripts/python.exe` within four lines (`test_install_scripts`). CONTRIBUTING is not scanned, but follows the same practice.
- **Bite mutations.** A step that mutates source to show a pin bites first copies the file to the scratchpad. After restoring the file, it runs `cmp <copy> <file>`. Expected: no output. Where the task has not yet changed that file, `git diff --exit-code -- <file>` does the same.
- **Commits.** A Commit step stages exactly its task's Files, plus any `lint-baselines/*.json` its lint step re-baselined, by path; never `git add -A` or `git add .`. Afterwards, `git status --short` lists none of the task's files.
- **The capstone diff.** `BASE` is `646e2c8` ("docs: resolve slice A plan review findings"), the `main` commit just before Slice A's first implementation commit. The capstone's commit ranges, looped one at a time:

  | Range | First commit | Last commit |
  |---|---|---|
  | A: `646e2c8..f52c7f4` | `a534d56` "feat(continuity): add continuity.json store with tolerant reader and strict writer" | `f52c7f4` "fix(continuity): undo restores semantically malformed records and refuses blind link checks" |
  | B: `0fa88a7..877468f` | `6c642d8` "docs: draft continuity capstone slice B and C plans" | `877468f` "docs: fold the Codex review of the slice C plan into the plan and spec" |
  | C: `6e150db..c84108c` | `567ad40` "feat(absorb): ask for and carry why_new / distinguished_from on new-record rows" | `c84108c` "fix(continuity): a candidate's status rides the identity prompt budget" |
  | D: `81bbb82..368e6ec` | `91e3b88` "feat(continuity): candidate cache with tolerant reader and locked writer" | `368e6ec` "fix(ledger): a refreshed finding opens its action form fresh" |
  | E–G: `368e6ec..HEAD` | | |

  Unrelated features landed between those ranges and touch some of the same files: model routing presets, profiles, create-world, post images, group play, lore activation, the regex pipeline and the image store. They are out of scope for every review here. `git log A..B C..D` is **not** the union of two ranges (git subtracts both left sides from both right sides), so the ranges are always looped.
- **Commands.** One command per purpose, and a `-k` filter is never combined with a file list it would deselect.
  - Backend, from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <files> -q -p no:cacheprovider`.
  - Frontend, from `frontend/`: `npx vitest run <files>` and `npx tsc --noEmit -p .`.
  - Gates, from the repo root: `make <target> PY=$PWD/backend/.venv/bin/python`.

## Review Focus

Five failure modes a person using the capstone is most likely to hit, which no earlier slice's tests reach. The most likely comes first, and each is pinned by a named test in the task that owns it.

1. **Internal tokens on review surfaces.** Linking two records from the review writes the journal label "Mara's map pays_off Mara's oath", which the History rail shows. A broken link or a dangling merge in Reviewed links / merges shows `missing_endpoint` and `thread:mara-s-map`. And one `by` link reads "By" in Reviewed links / merges but "Due by" in the Story Graph.
   - Expected: "Mara's map pays off Mara's oath"; the reason as a sentence; a missing record as "a missing thread"; an unknown reason code as the generic sentence, never the code; a `by` link reads "Due by" in the Ledger and the graph, and "is due by" in its journal label.
   - Pinned in Task 5: `test_link_journal_labels_use_relation_words`, `test_link_refusals_carry_no_store_token`, LedgerContinuity's "a broken entry says why in words and names a missing record by its kind", and `storyGraph/model.test.ts`'s "the Ledger and the graph lead a link with one phrase".
2. **A long campaign's read paths get slower with the ledger.** Today the Ledger's continuity read reads a full card per named actor, and a PC's card read lists that PC's image directories per version. The saved-idea read does the same for a PC's birthday. A per-record re-read of a whole file would also grow with the ledger.
   - Expected: each whole file is read a constant number of times, independent of record, scene and finding counts. No scene file is read more often as the campaign grows. No transcript, card version or image directory is read.
   - Pinned in Task 3: `test_read_paths_read_each_file_a_constant_number_of_times`, `test_the_continuity_chores_read_a_constant_number_of_files`, and `test_actor_name_reads_meta_only`.
3. **A burst of End Scenes during a live sweep.** Three saves land while the first pass waits on the model.
   - Expected: one follow-on pass carrying every adopter's refs, so two model calls in all. Refs that arrive after the last take wait for the next fresh run and are never lost.
   - Pinned in Task 2: `test_a_burst_of_adopters_coalesces_into_one_follow_on`.
4. **A delete whose continuity cascade fails after the record is gone.** Today it answers a bare 500, which reads as a failed delete although the record is gone. The token does move, because the activity middleware bumps it on an unhandled raise. A fix that answered through an `HTTPException` would lose that bump, so a held `POST /advance` expectation would then still pass.
   - Expected: a 500 `partial_delete` that names what landed and how to finish (Undo for a thread or commitment; Reviewed links / merges for an event), and a token that still moves.
   - Pinned in Task 4: `test_a_delete_whose_cascade_fails_moves_the_token` (thread and commitment) and `test_an_event_delete_whose_cascade_fails_moves_the_token`, with Step 5's bite (drop the explicit bump; the token assertion fails).
5. **A log row a user shares in a bug report carries private text** through a field nobody meant to hold it: a new mode field, or an exception message passed as `embedding_error`.
   - Expected: every string field in both info rows comes from a closed vocabulary, and no title, beat, identity text, prompt line or reason appears anywhere in the row.
   - Pinned in Task 1: `test_identity_log_row_is_counts_and_closed_modes_only` and `test_reconcile_log_row_is_counts_and_closed_modes_only`.

---

## Decisions (and why)

1. **§29 is pinned, not rebuilt.** Both §29 rows already exist: C's `"continuity identity check"` row (`routes/scenes._identity_outcome`) and D's `"continuity reconcile"` row (`routes/continuity._log_pass`).
   - **What they carry.** Every field §29 lists: the campaign, `candidates`, `deterministic` and `semantic`, `embedding` (`off` | `configured` | `failure`), and the decision counts. For identity those are the four `CHECK_DECISIONS` plus `downgraded` and `hint_only`. For the sweep there is one count per word in `reconcile.DECISIONS`. The sweep row also carries `pairs_capped` and `superseded`.
   - **What no test holds is the closed shape.** A later edit that adds a free-text field, or passes an exception's message as `embedding_error`, passes both existing tests: they check for named titles, not for every field.
   - **So G pins four things:** each row's exact key set; every string field to a closed vocabulary; every count to a non-bool `int`; and the absence of every title, beat, prompt line and model reason.
   - **The extra fields stay.** The fields beyond §29's list (`status`, `matching`, `sweep`, `llm`, `continuity`, `adjudicated`, `proposed`, `examined`, `embedded`, `embedding_error`) are counts or modes. §29 is amended to name them.
   - **`pairs_capped` and `superseded` are on the sweep row only**, because only the sweep caps pairs or races a newer run.
2. **The route is pinned verbatim, and is the only home of `continuity-*` tasks.** Without that pin, a third continuity call could be filed under another route and never appear on the Continuity checks picker.
3. **The capture note is held to the route.** A docs-guard test requires `docs/incoming-llm-capture.md` to name, in backticks, every task the `continuity` route claims. C and D wrote that sentence for their tasks, and §29 requires it.
4. **A run's model calls are bounded by passes, not by triggers** (§25.1; D Decision 6).
   - **The rule.** A run makes at most one call per pass. It has at most two passes: its own, and one follow-on that carries every ref adopters pended before the run took the set.
   - **So** End Scene and Refresh each cost at most one call, a run at most two, and a burst of adopters still exactly one more.
   - **The trade-off.** Refs pended after the last take wait for the next fresh run, which takes them first (D's `test_a_full_refresh_keeps_refs_left_pending_for_its_follow_on`). That is §11.1's own rule: "a skipped automatic run is therefore caught by the next one".
   - **Rejected:** a follow-on per adopter, or looping until the set is empty. Either makes the calls in one run grow with the number of saves, which is the N+1 §25.1 forbids. Recorded in §11.1 and §25.1 at Task 7.
5. **A zero-vector text is re-sent once per sweep, and that is accepted** (D hand-off d5).
   - **Why it happens.** `vectors.save` never caches a vector with no direction. So a ref whose text the provider answers with zeros stays vectorless and so stays "required".
   - **What it costs.** One text per incremental sweep, inside `RECONCILE_WARM_LIMIT`. It holds back no other text (D Task 4's split and retry), and the ref's prior pairs are kept under the vectorless rule.
   - **Rejected:** remembering "unembeddable" texts in the cache basis. That adds a key for one provider quirk, and a provider that later answers properly would never be asked again. Recorded in §9.4 at Task 7.
6. **Lexical features are computed once per record per sweep** (§25.2).
   - **The problem.** `similarity.lexical(a, b)` normalizes both identity texts and builds both token and trigram sets **for every pair**. That is O(n²·L) string work where O(n·L) suffices. A synthetic pool measured during planning showed that normalization, not the set comparison, dominates per-pair cost. It is a real share of a sweep at `RECONCILE_MAX_PAIRS`, which the constant's docstring promises keeps the storage-move refusal window short.
   - **The fix.** `Subject.features()` computes `(tokens(body(text)), trigrams(body(text)))` once per instance and caches it in a new slot; `lexical` reads it. Its results are unchanged. Absorb's identity check benefits too. The two title comparisons stay per pair: a title is a few words.
   - **Memory** is bounded by the pool size times the identity-text clip (`CONTINUITY_IDENTITY_BYTES`), for one sweep.
   - **Rejected:** an `lru_cache` keyed on text. It is unbounded across sweeps unless sized, and a size would be one more constant to tune.
7. **"Once per request" means a constant number of whole-file reads** (F hand-off f1; F Decision 4). §19.7 and §25.3 say the graph "may walk scenes, chronicle and the ledgers once per request".
   - **The reading G pins on every read path:**
     - each whole file is read a constant number of times, independent of record, scene and finding counts;
     - no single scene file is read more often as the campaign grows;
     - no transcript, card version or image directory is read.
   - **The read paths:** the Ledger, GET /continuity, the candidates read, the drivers read, the graph, the saved-idea read and the two Todo continuity chores.
   - **Rejected: literal once.** It would thread one `Ledgers` through `pressure.build`, `drivers.snapshot`, `involvement.of`, `effective.records` and `pending.Current.load`. That is five signatures across four slices, for no N+1 and no growth.
   - The same reading amends §18.2's "three small JSON reads" (D already notes events.json). Recorded in §18.2, §19.7 and §25.3 at Task 7.
8. **Location names keep the whole-kind overlay read** (F hand-off f2; F Decision 5).
   - **Why.** It is one call per graph read, constant per request, and the same sweep the turn loop pays.
   - **Rejected:** narrowing it to the locations a history names. That is a second entity reader plus one read per named location, which is cheaper only for a world with many unvisited locations.
   - **Pinned:** `overlay.list_entities(cid, "locations")` is called exactly once per `graph.build`, at both sizes.
9. **Actor names are read from meta, never from card versions or images** (§25.3's rule, applied to the capstone's other read paths).
   - **The problem.** `relationships.actor_name` calls `characters.read_character`, which reads every version's card and lists its images. Its PC branch calls `pcs.read_pc`, which also scans image directories per version (F Decision 5). The candidates read names every actor its findings mention through it. The saved-idea read names a PC's birthday through `pcs.read_pc`.
   - **The fix.** The character branch reads `characters.name_and_versions(overlay.char_root(cid, aid), aid)[0]`. That function already exists (the describe queue's): it reads `character.md`'s name and lists version ids, reads no card or image, raises `CharacterNotFound` exactly where `read_character` would, and is stat-memoized. The PC branch reads F's `pcs.name_of(overlay.pc_root(cid, aid), aid)`, which has the same contract. `suggest._actor_name`'s PC branch uses `pcs.name_of` too.
   - **The names are byte-identical**, so prompts and the frozen snapshot do not move. Every other caller (context, briefing, search, absorb labels, the Ledger) gets cheaper.
   - **Rejected:** a new `characters.name_of`, which would duplicate `name_and_versions` without its memo. Also rejected: rosters (F's choice for the graph). They read every actor's meta to name the few a finding mentions.
10. **A delete whose cascade fails after the delete landed answers a structured 500 `partial_delete`, and the token still moves** (A hand-off a1; §5.7).
    - **Today.** `delete_thread`, `delete_commitment` and `delete_campaign_event` call `continuity_review.forget_ref` after the record is gone. An `OSError`, or a `ContinuityError` from a file garbled under the hold, escapes the route unhandled. The reader gets a bare "Internal Server Error", which reads as a failed delete although the record is gone. That is the report §5.7 refuses to give ("a cascade that fails after the delete has landed would report a completed write as an error").
    - **The token already moves.** `_CampaignActivityStamp` (`main.py`, pre-capstone) bumps the revision whenever a campaign route raises unhandled. A's hand-off a1 said the token stayed put; it was wrong (checked against a throwaway store during planning).
    - **The fix.** `routes/ledger.forget_or_partial(cid, ref, *, name, noun)` wraps the call. On either error it calls `store.revision.bump(cid)` and raises `HTTPException(500)` with the Global Constraints body. The explicit bump is required: an `HTTPException` becomes a response through the middleware's `_send`, which stamps only below 300, and never reaches the bump-on-raise. Without it, the structured 500 would leave the token where it was, a regression on today. D's dismiss route already answers a partial write this way (`partial_dismiss`, D Decisions 16 and 17). The event route imports the helper, as `routes/continuity.py` already imports `routes/ledger.py`.
    - **The sentence differs by noun.** Thread and commitment deletes are journalled (`store.undo.journalled`), so their sentence says the delete can be undone. An event delete is not (`store.events.delete` writes no journal row), so its sentence points at Reviewed links / merges, which lists each link left behind as broken, with Remove link.
11. **Four small hand-off fixes.** Each is a one-line class of change with its own test.
    - `api.removeAlias` and `api.removeLink` gain `.then(notifyShell)`, as apply, dismiss and restore have (d1): an unmerge changes the rail's Ledger count.
    - `birthdays.crossed` skips a birthdate that raises any of `birthdays._UNREADABLE`, not only `CalendarError` (b1). That tuple exists for exactly this; its comment says a provider's arithmetic raises `OverflowError` on a year past its range.
    - `api/types.ts` gains `ReconcileRunError = { kind: string; detail: string; status: number; sweep: "full" | "incremental"; saved: boolean; follow_on: boolean }`, and `failedNote` reads `ApiError.body` as `Partial<ReconcileRunError>` (d7).
    - ScenesView's error banner "Try again" reads the scene list again (e4). The list's load effect gains a `reload` counter in its dependencies, and "Try again" increments it; the effect already clears `failed` when it runs. Today the button only clears the banner, which leaves the search tools over an empty list, with no banner and no read under way. The defect predates the capstone, but E changed this page, and E's seeded chooser had to route around it (E Task 11 ruling). §33 keeps small usability fixes open.
12. **§30: no store token reaches a reader.**
    - **Journal labels.** `review.create_link`, `remove_link` (through `_link_label`) and `forget_ref` label a link "<a> <RELATION_WORDS[relation]> <b>", with " — removed" and " — removed with deleted record" suffixes as today. `RELATION_WORDS` lives in `review`, the only module that writes these labels.
    - **Refusals.** The two link refusals use the constant copy (Global Constraints).
    - **Raw links.** GET /continuity's `raw_links` gain `a_title` and `b_title` through `review.describe`, as effective links already do (d3).
    - **Reviewed links / merges** renders every reason through `brokenReason(code)`. A title that is empty or equals its ref is shown as `missingName(ref)`, because `describe` answers a missing record with its ref.
    - **One wording per relation (F's h8).** `RELATION_PHRASES.by` becomes "Due by", the phrase F's graph already uses and the form §5.3's Meaning column gives ("a is due … by event b"). It is the only entry where the two tables differ. F's `RELATION_PHRASE[r].out` is taken from `RELATION_PHRASES[r]` rather than redeclared, and a vitest pins the equality for every `LinkRelation`. F's `in` phrases ("Deadline for", "Continued by" and the rest) have no Ledger counterpart and stay F's.
    - **The journal's verb form differs on purpose.** `RELATION_WORDS` uses the same words in a sentence form: a journal label is read as one sentence in the History rail ("Mara's oath is due by Saltmarch market"), while both UI tables lead a line under a record's title ("Due by Saltmarch market"). `test_relation_words_cover_every_relation` holds each temporal word to "is due <relation>".
    - **An avoid-list guard** reads every non-test source file under `backend/src/grimoire/` and `frontend/src/`, and `README.md`, for §30's avoided phrases. A list of capstone files would miss the next surface. A test cannot derive that list from the capstone's commit ranges either, because CI checks out one commit deep (`actions/checkout`'s default). No avoided phrase occurs anywhere in that source today, so the wider scan costs nothing.
    - **Preferred phrases are pinned** to the surface that shows each one, so rewording a capstone surface away from §30 fails by name. "Merged into" also appears in pre-capstone files, so "somewhere in the source" would never fail.
    - **Unchanged by decision:** D's choice that an unknown relation in `RELATION_PHRASES` reads as itself (a stored word a reader chose). It is reachable only by a hand edit. The hedged proposal labels also stay as they are ("Suggested: before", "… on", "… after", "… by", in `DECISION_LABELS` on both sides). They name the decision word a model proposed, both copies are pinned against each other (D Decision 25), and changing one word would move two pinned tables. Nor does the chooser's time-anchor select change (E's `StoryPressure`). Its options are the plain words a scene's time relation takes, and that relation is not a link.
13. **Docs change only what is false, and new claims are held to the code.**
    - **CLAUDE.md.** Each inventory the capstone could have moved is re-derived. The "Twenty-five handlers" count needs no starter added since D. The `background` class must name `put_chronicle` and `post_reconcile`. The write-token paragraph must name both sweep persists. "Hand edits to the ledger go through `routes/ledger.py`" stays true, because apply uses `ledger.move_thread`/`move_commitment`. "The ledger's seven sections" means `LedgerView`'s seven record tables; Continuity review is a column section with no records of its own. Edit only what turns out false. A new docs-guard test pins the reconcile starters, the way the tracker test pins its starters.
    - **CONTRIBUTING.** The writer-guard row names the Story Graph, which the guard now requires. The `test_docs_guard.py` row names the CLAUDE.md and templates/README claims it now checks.
    - **store-guarantees.** One sentence at the end of "The derived sidecars do not expire" sets the candidate cache against those three: it does notice change. The sentence makes no claim to be the only one. The vector cache is keyed by a text hash, and saved scene ideas turn visibly stale on read.
    - **templates/README.md** is checked. A docs-guard test holds its `` `store/<path>.py:<fn>` `` builder citations to real functions, and requires a `### `continuity_*/`` heading for each continuity family.
    - **README** gains §30's optional bullet.
    - **Not done:** a CLAUDE.md paragraph on the stopping rule. The owner limited CLAUDE.md to what its own claims require (Open questions). The parked status goes in `PHASE-STATUS.md` and the spec.
14. **The acceptance evidence is a spec appendix held to the tests.** Appendix B lists, per AC1–AC19 and §33, the tests and eval cases that prove it on the integrated tree. `backend/tests/test_capstone_acceptance.py` fails when a cited backend test, frontend test title or eval case does not exist, or when a criterion has no row. Without that test, a rename would leave an acceptance criterion silently proven by nothing.
    - **§28.10 gets its own table.** Each of the ten cases maps to its eval case, its grader check, and the counterexample recording that trips that check. `test_evals.py::test_recording_scores_as_declared` already proves each recording trips exactly its declared checks, so the acceptance test only has to hold the mapping: a row whose check no counterexample of its case declares fails.
    - **Cases 4, 5 and 7 have no tripping counterexample today.** D bundled cases 2–8 into one eval case, `continuity-reconcile`. Its four counterexamples trip `distinct`, `continuation`, `keep_open`, `unproven` and `evidence`, but never `cross_type`, `close` or `fulfilled`. So a grader that stopped scoring those three would stay green. Task 7 adds one counterexample, `continuity-reconcile.timid`, that trips exactly those three.
15. **The whole-capstone review is scoped by ranges and regions, never by the bare `BASE..HEAD` diff.**
    - **The path set** is the union of files the capstone ranges touched (looped). Reviewers read `git diff BASE..HEAD -- <path set>` with the per-range logs, and are told that unrelated features touched some of the same files.
    - **Every path has a reviewer.** The code-review stand-in is four reviewers, one per code area. Task 8 assigns every path in the set to an area, or to the documents every reviewer receives, and checks that nothing is left over before any reviewer is dispatched. Two route modules, `routes/continuity.py` and `routes/scenes.py`, are split across areas by function, on purpose; each brief names its part.
    - **Every section has a reviewer.** The adversarial stand-in is five reviewers, one per spec region; each asks whether the capstone implements that region end to end. The regions are checked against the spec's heading list, §0 to §33, before dispatch, so §28's test lists and the store side of §12 are read too.
    - **The gates run in CLAUDE.md's order.** The code-review findings are resolved, and `make check` and the evals replay pass, before the adversarial review starts. That review reads a diff rebuilt at the new tip, so the final spec-conformance check sees the code as it will land, fixes included.
16. **Deferred hand-offs are recorded in the spec, not only in the ledger.** The ledger is gitignored, and B's final review flagged deviations "recorded only in the gitignored ledger". Every item Before Task 1 step 5 defers is listed, with its reason, in a "Slice G rulings" bullet list under §31 Slice G.
17. **§25.4's "no full transcript reads" binds what the capstone adds to Todo and the shell.**
    - **The shell reads transcripts, and did before the capstone.** `GET /shell` reads each open scene's transcript to count its model replies (`routes/shell._scene_turns`). The rail draws that count, and its own docstring argues the cost is bounded by the open scenes. Read literally, §25.4 ("Todo and shell badge reads must not perform … full transcript reads") would forbid it.
    - **The ruling.** §25.4 is about the candidate cache: the capstone may add no embedding or transcript work to Todo or the shell. Removing a pre-capstone rail count is not this slice's to do (§33). Task 7 amends §25.4 to say so.
    - **The pin.** The shell's transcript reads stay exactly the pre-capstone ones: the open scenes', whatever the continuity records and findings number. Task 3 holds the open-scene count at one while the ledger grows twelvefold. It expects `read_scene` only for that scene, as often at both sizes, and no continuity review work at all.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/src/grimoire/store/continuity/similarity.py` | `Subject.features()`; `lexical` reads it |
| `backend/src/grimoire/store/continuity/reconcile.py` | `RECONCILE_MAX_PAIRS` docstring states the per-pair cost structurally |
| `backend/src/grimoire/store/relationships.py` | `actor_name` through `characters.name_and_versions` / `pcs.name_of` |
| `backend/src/grimoire/store/suggest.py` | `_actor_name`'s PC branch through `pcs.name_of` |
| `backend/src/grimoire/store/birthdays.py` | `crossed` skips an unreadable birthdate |
| `backend/src/grimoire/store/continuity/review.py` | `RELATION_WORDS`; link labels and refusals in words |
| `backend/src/grimoire/routes/ledger.py` | `forget_or_partial`; both deletes use it |
| `backend/src/grimoire/routes/campaigns.py` | `delete_campaign_event` uses it |
| `backend/src/grimoire/routes/continuity.py` | raw links gain `a_title` / `b_title` |
| `frontend/src/api/client.ts`, `frontend/src/api/types.ts` | unmerge and remove-link notify the shell; `ReconcileRunError`; raw-link titles |
| `frontend/src/components/continuity/labels.ts`, `ReviewedGroup.tsx`, `useContinuityReview.ts` | `BROKEN_REASONS`, `brokenReason`, `missingName`; `RELATION_PHRASES.by` reads "Due by"; the group renders through them; the typed failed-run body |
| `frontend/src/components/storyGraph/model.ts` (F's) | `RELATION_PHRASE[r].out` taken from `RELATION_PHRASES` |
| `frontend/src/routes/ScenesView.tsx` | "Try again" reads the scene list again |
| `evals/cases.py`, `evals/recordings/continuity-reconcile.timid.json` (new), `evals/README.md` | the `timid` counterexample for §28.10 cases 4, 5 and 7 |
| `backend/tests/test_continuity_read_cost.py` (new) | constant reads; no model or embedding call on any read path; actor names without cards; the shell's transcript reads stay the pre-capstone ones |
| `backend/tests/test_continuity_wording.py` (new) | §30 avoid-list guard over every source file; preferred phrases pinned to their surfaces; relation words cover every relation |
| `backend/tests/test_capstone_acceptance.py` (new) | Appendix B cites tests and cases that exist; each §28.10 case has a counterexample that trips its check |
| `backend/tests/test_routing.py`, `test_absorb_identity.py`, `test_continuity_reconcile_routes.py`, `test_continuity_reconcile.py`, `test_continuity_similarity.py`, `test_relationships_store.py`, `test_ledger_routes.py`, `test_events_routes.py`, `test_birthday_occurrences.py`, `test_continuity_review.py`, `test_continuity_routes.py`, `test_docs_guard.py` | appended pins (one label assertion in `test_continuity_review.py` is updated, Task 5) |
| `frontend/src/api/client.test.ts`, `frontend/src/routes/LedgerContinuity.test.tsx`, `frontend/src/routes/ScenesView.test.tsx`, `frontend/src/components/storyGraph/model.test.ts` | the shell notice; broken entries in words; the retry re-reads; one phrase per relation |
| `CONTRIBUTING.md`, `docs/store-guarantees.md`, `README.md`, `docs/superpowers/PHASE-STATUS.md`, `CLAUDE.md` (only if a claim is false) | Tasks 6 and 8 |
| `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md` | amendments, Appendix B and Status (Tasks 7 and 8) |

Backend source paths below are relative to `backend/src/grimoire/` and tests to `backend/tests/`, unless they start with `frontend/` or name a repo-root document.

---

### Task 1: Observability — the §29 route, both log rows and the capture note, pinned

**Files:**
- Test: `tests/test_routing.py`, `tests/test_absorb_identity.py`, `tests/test_continuity_reconcile_routes.py`, `tests/test_docs_guard.py` (each appended)
- Modify: only if a pin fails on arrival, then the producer (`routes/scenes._identity_outcome` or `routes/continuity._log_pass`), never the test

**Interfaces:**
- Consumes: `routing.ROUTES`, `routing.TASK_ROUTE`, `store.logs.scan(level="info", campaign=cid)`, `identity.CHECK_DECISIONS`, `identity.examine` (patched in one case), `reconcile.SWEEPS`, `continuity_routes._WORDS`, `llm_errors.KINDS`.
- Produces: test-module constants `IDENTITY_MODES`, `IDENTITY_COUNTS`, `IDENTITY_ROW_KEYS`, `RECONCILE_ROW_KEYS` and `MODES` (a field → allowed-values map), and two helpers. `_row_texts(fake) -> list[str]` returns every stripped line of 12 or more characters from every message the fake received. `_dumped(row) -> str` is `json.dumps(row, ensure_ascii=False)`: the default `json.dumps` writes an em dash as the escape `\u2014`, and both continuity user prompts put em dashes in their lines, so a leaked prompt line would never match the default dump. They are used only in these two test files.

- [ ] **Step 1: Write the tests.**
  - `test_routing.py::test_the_continuity_route_is_spelled_as_section_29_spells_it`:
    - the `continuity` route equals the Global Constraints `Route(...)`, field by field;
    - `TASK_ROUTE["continuity-identity"] == TASK_ROUTE["continuity-reconcile"] == "continuity"`;
    - no other key of `TASK_ROUTE` starts with `"continuity"`;
    - `TASK_ROUTE["suggestions"] == "suggestions"`: §29 says the scene-suggestion task keeps its name.
  - `test_absorb_identity.py::test_identity_log_row_is_counts_and_closed_modes_only`, parametrized over four cases:
    - `ok`: a `new` decision with the reason "a second search";
    - `no connection`: the `continuity` route points at a keyless connection (`POST /api/llm-connections` with `{"kind": "openrouter", "name": "Keyless"}`, then `PUT /api/routing`), as `test_misrouted_identity_reports_itself_and_leaves_absorb_standing` does;
    - `embedding failure`: `_configure_embeddings` with `FakeEmbeddings(error=EmbeddingsError("network", "connection refused"))`;
    - `examination raised`: `continuity_identity.examine` patched to raise `RuntimeError("Mara's oath")`. No examination exists, so `_identity_outcome` logs with no counts, and the phase's `reason` holds the exception text.

    For each case, take the one `"continuity identity check"` row:
    - `set(row) - {"ts", "level", "module", "message"} == IDENTITY_ROW_KEYS`, where `IDENTITY_ROW_KEYS` is `IDENTITY_MODES | IDENTITY_COUNTS`. `IDENTITY_MODES` is `{"kind", "campaign", "scene", "status", "matching", "embedding", "embedding_error"}`, and `IDENTITY_COUNTS` is `{"proposed", "examined", "candidates", "deterministic", "semantic", "embedded", "downgraded", "hint_only"} | set(identity.CHECK_DECISIONS)` (today `existing`, `new`, `uncertain` and `unchecked`, so twelve counts in all). In the `examination raised` case the key set is `IDENTITY_MODES` alone;
    - `row["kind"] == "continuity-identity"`, `row["campaign"] == cid` and `row["scene"] == sid`;
    - every count is a non-bool `int`, and `deterministic + semantic == candidates`;
    - `status` is in `{"ok", "degraded", "failed", "skipped"}`, `matching` in `{"basic", "semantic"}`, `embedding` in `{"off", "configured", "failure"}` (`""` in the `examination raised` case only), and `embedding_error` in `{""} | llm_errors.KINDS | {"unexpected"}`;
    - in the `embedding failure` case `embedding_error == "network"`, and "connection refused" is not in `_dumped(row)`; in the `examination raised` case `status == "failed"`, and "Mara's oath" is not in `_dumped(row)`;
    - no seeded or proposed title, beat or reason, and no line from `_row_texts(fake)`, is a substring of `_dumped(row)`.
  - `test_continuity_reconcile_routes.py::test_reconcile_log_row_is_counts_and_closed_modes_only`, parametrized over three cases:
    - `ok`: `_key` plus `_reply(_duplicate())`, whose reason is `REASON`;
    - `off`: no key;
    - `failed`: an undecodable reply such as `"no json here"`.

    For each case, take the newest `_reconcile_row(cid)`:
    - `set(row) - {"ts", "level", "module", "message"} == RECONCILE_ROW_KEYS`, where `RECONCILE_ROW_KEYS` is `{"kind", "campaign", "sweep", "matching", "embedding", "embedding_error", "llm", "continuity", "candidates", "deterministic", "semantic", "adjudicated", "pairs_capped", "superseded"} | set(continuity_routes._WORDS)`;
    - every count is a non-bool `int`, and `pairs_capped` and `superseded` are `bool`;
    - `sweep` is in `reconcile.SWEEPS`, `llm` in `{"off", "skipped", "ok", "failed"}`, `continuity` in `{"ok", "malformed"}`, and the three shared modes are as above;
    - no title or beat of `LEDGER_THREAD` or `RECOVER_THE_LEDGER`, not `REASON`, and no line from `_row_texts(fake)` appears in `_dumped(row)`.
  - `test_docs_guard.py::test_llm_capture_doc_names_every_continuity_task`: for each task of the `continuity` route, `` f"`{task}`" `` occurs in `docs/incoming-llm-capture.md`.
- [ ] **Step 2: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routing.py tests/test_absorb_identity.py tests/test_continuity_reconcile_routes.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. These pin what C and D shipped. A failure is a §29 defect: fix the producer, re-run, and record it in the ledger as a finding. A failure for a test-setup reason is fixed in the test instead (Global Constraints, Scope).
- [ ] **Step 3: Show that the pins bite.** Make four mutations, one at a time, run the two row tests after each, and then restore:
  - add `title="x"` to `_log_pass`'s `store.logs.record(...)`;
  - pass `embedding_error=str(exc)` in place of the kind;
  - drop `"hint_only"` from `counts()`;
  - add `note=<line>` to `_identity_outcome`'s `store.logs.record(...)`, where `<line>` is an em-dash line of the identity user prompt copied from `_row_texts(fake)` in the `ok` case (a `Row … — proposed …` line), and add `"note"` to `IDENTITY_ROW_KEYS` for this run only. The key-set assertion would catch a new key first, so this simulates a later edit that updates both; the leak check is what must catch it, and it can only through `_dumped`.

  Expected: each mutation fails the test for its row, and the fourth passes if `_dumped` is swapped for the default `json.dumps`. Record the four in the ledger. `git diff --exit-code` passes afterwards (Global Constraints, Bite mutations).
- [ ] **Step 4: Run the guards that see these modules.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routing_guard.py tests/test_usage_guard.py tests/test_routing_routes.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 6: Commit:** `test(continuity): pin the §29 route, both log rows and the capture note`

---

### Task 2: Sweep cost — one normalization per record, and the per-run call bound (§25.1, §25.2)

**Files:**
- Modify: `store/continuity/similarity.py`; `store/continuity/reconcile.py` (the `RECONCILE_MAX_PAIRS` docstring only)
- Test: `tests/test_continuity_similarity.py`, `tests/test_continuity_reconcile.py`, `tests/test_continuity_reconcile_routes.py`, `tests/test_absorb_identity.py` (each appended)
- Scratch, not committed: a timing script under the scratchpad

**Interfaces:**
- Produces: `similarity.Subject.features() -> tuple[frozenset[str], frozenset[str]]`, returning `(tokens(body(self.text)), trigrams(body(self.text)))`. It is computed on first call and cached in a new `__slots__` entry `_features`, initialized `None`. `tokens`, `trigrams` and `body` are called through the module globals, so a test can count them. `lexical(a, b)` uses `a.features()` and `b.features()`, and returns a dict equal to today's for every pair. The constructor's signature is unchanged.
- Consumes: D's `reconcile.RECONCILE_MAX_PAIRS`, `continuity_routes.schedule_reconcile`, `_held`, `_plot_edit`, `_applied`, `_reconcile_requests` and `_reconcile_row`.

- [ ] **Step 1: Measure before (synthetic only).** In a scratchpad script, build pools of 50, 100 and 200 `similarity.Subject`s with placeholder text: "Mara's errand N" titles, and beats drawn from Realm and Saltmarch words. Time all-pairs `lexical`, then time a pure-Python `vectors.dot` at a common embedding width. Record the per-pair figures in the ledger only. Expected: a figure per size, and no store read.
- [ ] **Step 2: Write the tests.**
  - `test_continuity_similarity.py::test_lexical_normalizes_each_text_once`. Build six subjects, and count calls to `similarity.tokens` and `similarity.trigrams` with `monkeypatch` wrappers.
    - Scoring all 15 pairs gives exactly 6 calls to each.
    - Scoring them all again adds none.
  - `test_continuity_reconcile.py::test_a_sweep_builds_each_identity_text_once`. Seed four threads (`_ledger`, `_recover`, `_tithe` and `_map`) and the commitment "Mara's oath". `monkeypatch.setitem` counting wrappers over `similarity._TEXT["thread"]` and `["commitment"]`. One full `_sweep(cid)` makes 4 thread-text calls and 1 commitment-text call.
  - `test_continuity_reconcile.py::test_a_zero_vector_text_costs_one_resend_per_sweep` (Decision 5).
    - Setup: `_configure()` and `_chores(cid, s0)`. A `FakeEmbeddings` answers `[0.0, 0.0]` for one changed ref's text and a unit vector for every other text. Change that ref's beat and one other's, then run two incremental `_embedded` sweeps with different stamps.
    - Assertions: each sweep sends the zero-vector text exactly once; the other changed ref is in `sweep.rescored` after the first; and no sweep sends more than `reconcile.RECONCILE_WARM_LIMIT` texts.
  - `test_continuity_reconcile_routes.py::test_a_burst_of_adopters_coalesces_into_one_follow_on` (Review Focus 3, Decision 4), marked `@pytest.mark.reconcile`. Without the marker, conftest's `_auto_reconcile_off_unless_asked` sets `AUTO_RECONCILE` to `False`, and `schedule_reconcile` returns before pending anything. The sibling `test_schedule_reconcile_hands_its_refs_to_an_adopted_run` carries the marker for this reason.
    - Setup: `_campaign`, `_threads`, and a third thread "Mara's map" (`mara-s-map`), plus `_key`. Hold the first pass: `held = _install(client, _held())`, then `first = _refresh(client, cid)` and `held.await_held()`. While it is held, call `continuity_routes.schedule_reconcile(client.app, cid, held, edits=[e], progress=_applied(e))` three times, one `_plot_edit` per thread. Then release.
    - Assertions: the run lands with `result["follow_on"] is True`; `len(_reconcile_requests(held)) == 2`; the second request's user prompt contains `TOUCHED` and all three thread titles; and `take_touched` afterwards is empty.
  - `test_continuity_reconcile_routes.py::test_a_capped_sweep_says_so_in_its_run_and_log_row`. Set `monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 1)`, seed three threads, and Refresh with no key. Both `run["result"]["pairs_capped"]` and `_reconcile_row(cid)["pairs_capped"]` are `True`.
  - `test_continuity_reconcile_routes.py::test_sweep_work_runs_off_the_event_loop` (§25.2).
    - Wrap `reconcile.discover`, `persist_found`, `select`, `build_payload` and `persist_proposals` in recorders. Each appends whether `asyncio.get_running_loop()` raises `RuntimeError` in its thread, then calls through.
    - Refresh with a key and a duplicate reply. Every recorder fired at least once, and every value recorded is `True` (no loop in that thread).
  - `test_absorb_identity.py::test_the_identity_examination_runs_off_the_event_loop`: the same recorder over `continuity_identity.examine` during an absorb that examines a row.
- [ ] **Step 3: Run them and confirm the RED.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_routes.py tests/test_absorb_identity.py -q -p no:cacheprovider`. Expected:
  - `test_lexical_normalizes_each_text_once` FAILS (30 calls, not 6);
  - the rest PASS, because they pin D's design. A FAIL among them is a §25 violation: fix it in the owning module (for the text count, pool each kind once in `discover`) and record it. A FAIL for a test-setup reason, such as a missing `reconcile` marker, is fixed in the test and recorded, never in the producer (Global Constraints, Scope);
  - for the burst test, show the bite by mutating the follow-on into one pass per pended ref (4 calls), then restore. Expected: `git diff --exit-code -- backend/src/grimoire/routes/continuity.py` passes (Global Constraints, Bite mutations).
- [ ] **Step 4: Implement** `Subject.features()` and switch `lexical` to it. Then reword `RECONCILE_MAX_PAIRS`'s docstring: a pair costs two comparisons of sets built once per record, plus at most one dot product, and normalization is per record. Keep "to be tuned against real prompts later" and add no figure.
- [ ] **Step 5: Run them, and every suite over similarity.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_continuity_identity.py tests/test_absorb_identity.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_prompt.py tests/test_continuity_reconcile_routes.py tests/test_evals.py tests/test_eval_graders.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS, with no existing assertion edited.
- [ ] **Step 6: Measure after.** Run Step 1's script again and record the figures in the ledger. Expected: the per-pair lexical cost drops to the set comparisons.
- [ ] **Step 7: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 8: Commit:** `perf(continuity): normalize each identity text once per sweep; pin the per-run call bound`

---

### Task 3: Read cost — constant reads, actor names from meta, and no model call on any read path (§25.3, §25.4, §3.10)

**Files:**
- Create: `tests/test_continuity_read_cost.py`
- Modify: `store/relationships.py`, `store/suggest.py`
- Test: `tests/test_relationships_store.py` (appended)

**Interfaces:**
- Consumes: F's `pcs.name_of(root, pid)` and `graph.build`; D's `candidates.write` and `pending.fingerprint`; the existing `characters.name_and_versions(root, cid)` and `store.scenes.mark_absorbed(cid, sid, one_line, summary)`.
- Produces:
  - `relationships.actor_name(cid, token)`. Same signature and same names, read through `pcs.name_of(overlay.pc_root(cid, aid), aid)` and `characters.name_and_versions(overlay.char_root(cid, aid), aid)[0]`. The `except` clause is unchanged.
  - `suggest._actor_name`'s `pcs` branch becomes `pcs.name_of(overlay.pc_root(ctx.cid, aid), aid)`, with the same `except` tuple.
  - In `test_continuity_read_cost.py`:
    - `_seed(client, n) -> tuple[str, list[str]]` builds the campaign, under the `client` fixture's store. The world "Realm" holds the character Mara (birthdate `--05-12`) and the PC Seraphine (birthdate `--05-14`). The campaign "Saltmarch" has its clock at 2026-05-10, set the way `tests.test_continuity_pressure._campaign` sets it. It then gets `n` scenes "Saltmarch scene i", each seating Seraphine and Mara; `n` threads "Mara's errand i" and `n` commitments "Winifred's debt i", each moved in its own scene; `n` events "Saltmarch market i" in the next 30 days; `n` saved ideas, each serving one thread, plus one anchored to Seraphine's birthday occurrence; one `pays_off` link; and `n` live `possible_duplicate` findings over consecutive threads, written with `candidates.write` and fingerprinted with `pending.fingerprint`. It returns the campaign id and the scene ids in play order.
    - `_reads(monkeypatch, cid, sid) -> dict[str, Counter]` counts calls to three groups of readers:
      - whole-file readers: `store.plot.read`, `store.commitments.read`, `store.events.read`, `store.continuity.doc.read`, `store.continuity.candidates.read`, `store.chronicle.read_chronicle`, `store.relationships.read`, `store.scene_ideas.read` and `store.appearances.cast.roster`;
      - per-scene readers, counted by sid: `get_time_history`, `get_location_history` and `read_scene_meta`;
      - must-be-zero readers: `read_scene`, `read_scene_window`, `characters.read_card`, `characters.read_character`, `pcs.read_pc` and `assets.list_images`.
    - **Both scene bindings.** `store/scenes/__init__.py` re-exports the scene readers by value, so a call through `store.scenes.read_scene` never reaches a patch on `store.scenes.read.read_scene`. The Ledger route and `routes/continuity.py` call `store.scenes.list_scenes` that way. So `_reads` patches each scene reader (the five above, and `list_scenes`) on both `grimoire.store.scenes` and `grimoire.store.scenes.read`, with one counting wrapper around the original. The originals are captured once, at import, so calling `_reads` once per size does not stack a wrapper on a wrapper. Modules inside `store/` reach the readers through `scenes_read` aliases of the submodule, which the second patch covers.
    - **Calibration.** `_reads` then calls `store.scenes.read_scene(cid, sid)` and `store.scenes.read.read_scene(cid, sid)` once each, asserts that the `read_scene` counter reads 2, and zeroes every counter. A spy that misses a binding fails here, rather than passing every must-be-zero check.

- [ ] **Step 1: Write the tests.**
  - `test_relationships_store.py::test_actor_name_reads_meta_only`:
    - names are unchanged for a world character, a campaign-copied character, a world PC, and a missing id (which answers the id);
    - a character whose every version file was removed answers its id, as today: `name_and_versions` raises `CharacterNotFound` exactly where `read_character` does;
    - `read_card`, `read_character`, `pcs.read_pc` and `assets.list_images` count 0.
  - `test_continuity_read_cost.py::test_read_paths_read_each_file_a_constant_number_of_times` (Review Focus 2, Decision 7). It is parametrized over six routes: `GET /api/campaigns/{cid}/ledger`, `.../continuity`, `.../continuity/candidates`, `.../continuity/drivers`, `.../continuity/graph` and `.../scene-ideas?greetings=false`. For each route, seed at `n=1` and at `n=12` and read once under `_reads`. Then:
    - each whole-file count at 12 equals its count at 1;
    - the largest per-scene count at 12 equals the largest at 1;
    - the must-be-zero readers count 0;
    - every reader the route is known to use counts at least 1 at `n=1`. This positive control keeps a mis-wired spy from passing; for example, the graph reads `plot.read`.
  - `test_continuity_read_cost.py::test_the_continuity_chores_read_a_constant_number_of_files` (§18.2, §25.4). Call `todo._chore_continuity_overlaps(todo._Ctx(cid))`, `_chore_continuity_closures`, and both `_items_continuity_*` directly, at both sizes. The whole-file counts are equal across sizes. `read_scene`, `read_scene_window` and `list_scenes` count 0, through either binding.
  - `test_continuity_read_cost.py::test_continuity_review_reads_name_actors_without_cards`: the candidates read for findings whose records involve Seraphine and Mara has both names in `names`, and `pcs.read_pc`, `read_character` and `assets.list_images` count 0.
  - `test_continuity_read_cost.py::test_the_saved_idea_read_names_a_pc_birthday_without_images`: the idea anchored to Seraphine's birthday reads with her name in its anchor label, and `assets.list_images` counts 0.
  - `test_continuity_read_cost.py::test_the_graph_reads_location_names_once` (Decision 8): `overlay.list_entities` is called with `(cid, "locations")` exactly once per `graph.build`, at both sizes.
  - `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding` (§3.10, AC13, AC16).
    - Parametrized over the six routes above plus seven more. Two are the read paths Slice B made canonical: `GET /api/campaigns/{cid}/scenes/{sid}/briefing`, with `sid` the first seeded scene, and `POST /api/campaigns/{cid}/advance/preview` with `{"days": 7}`, which builds the digest and is `@computes_only`. The other five are `GET /api/todo?campaign={cid}`, `GET /api/todo`, `GET /api/todo/continuity-overlaps/items?campaign={cid}`, `GET /api/todo/continuity-closures/items?campaign={cid}` and `GET /api/shell?campaign={cid}`. `GET .../clock` is left out: it reads `clock.state`, which the capstone did not change.
    - Make embeddings configured: `store.embed_space.resolve` returns a space dict. Replace `similarity._CLIENT` with a recording `FakeEmbeddings`. Patch `grimoire.embeddings.EmbeddingsClient.embed` at class level to a recorder.
    - The LLM fake is built once with one entry that never matches, `fake = llm_fakes.from_entries([{"when": {"system_contains": "\x00no request matches"}, "reply": ""}])`, and installed as `routes.get_llm` → `lambda: fake`, as E's `test_opening_paths_make_no_model_call` does. `from_entries([])` cannot be built: `Cassette.__init__` raises on an empty list. A request that reached this fake would also raise `CassetteMiss`.
    - Assertions: 2xx; `fake.calls == 0`; and the `FakeEmbeddings`' calls and the class recorder are both empty.
    - **Recorders, not raisers.** A raiser inside `_soft` or `_attempt` is swallowed and passes (the trap F's plan gate found).
  - `test_continuity_read_cost.py::test_the_shell_read_does_no_continuity_review_work` (§25.4, Decision 17).
    - Seed at `n=1` and at `n=12`. Then mark every seeded scene but the last absorbed (`store.scenes.mark_absorbed`), so one scene is open at both sizes. Set recorders on `continuity.pending.findings`, `continuity.candidates.read`, `continuity.reconcile.discover`, `continuity.pressure.build` and `continuity.drivers.snapshot`, and read under `_reads`.
    - Assertions: `GET /api/shell?campaign={cid}` records 0 calls to the five recorders; `read_scene` is called only for the open scene, and as often at 12 as at 1 (the pre-capstone turn count, so not zero); `read_scene_window` counts 0; and no key of the `campaign` block contains "continuity" or "candidate".
- [ ] **Step 2: Run them and confirm the RED.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_relationships_store.py tests/test_continuity_read_cost.py -q -p no:cacheprovider`. Expected:
  - `test_actor_name_reads_meta_only`, the two naming tests, and the zero-reader asserts of the candidates and scene-ideas routes FAIL (card and image reads);
  - the rest PASS.
  - A whole-file count that grows with `n` is a violation. If the read is a capstone module's, fix it there by hoisting the read (record it). If it is pre-capstone code on a path the capstone does not own, record it in the ledger as pre-existing and narrow that reader's assertion for that route only, with a comment saying why.
- [ ] **Step 3: Implement.** Switch `relationships.actor_name` to `pcs.name_of` and `characters.name_and_versions`, then `suggest._actor_name`'s PC branch to `pcs.name_of`.
- [ ] **Step 4: Run them, and every suite that renders an actor name.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_characters_store.py tests/test_relationships_store.py tests/test_continuity_read_cost.py tests/test_shell_route.py tests/test_frozen_campaign.py tests/test_actor_context.py tests/test_context.py tests/test_briefing_route.py tests/test_ledger_route.py tests/test_absorb_store.py tests/test_continuity_review_routes.py tests/test_scene_ideas_provenance.py tests/test_overlay_guard.py tests/test_import_guard.py tests/test_paths_guard.py -q -p no:cacheprovider`. Expected: PASS, with no existing test edited and `snapshot.json` untouched, because the names are byte-identical.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 6: Commit:** `perf(continuity): read paths name actors from meta and read each file a constant number of times`

---

### Task 4: Hand-off fixes — partial deletes, the rail after an unmerge, `crossed`, the failed-run type, the scene list's retry

**Files:**
- Modify: `routes/ledger.py`, `routes/campaigns.py`, `store/birthdays.py`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/components/continuity/useContinuityReview.ts`, `frontend/src/routes/ScenesView.tsx`, `frontend/src/routes/LedgerView.tsx`, `frontend/src/components/EventsPanel.tsx`
- Test: `tests/test_ledger_routes.py`, `tests/test_events_routes.py`, `tests/test_birthday_occurrences.py`, `frontend/src/api/client.test.ts`, `frontend/src/routes/ScenesView.test.tsx`, `frontend/src/routes/LedgerView.test.tsx`, `frontend/src/components/EventsPanel.test.tsx` (each appended; one existing ScenesView test edited, Step 1)

**Interfaces:**
- Produces:
  - `routes/ledger.forget_or_partial(cid: str, ref: str, *, name: str, noun: str) -> None`. It calls `continuity_review.forget_ref(cid, ref, name=name)`. On `OSError` or `continuity_doc.ContinuityError` it calls `store.revision.bump(cid)` and raises `HTTPException(500, detail={...})`, chained from the error, with the Global Constraints body and the sentence for `noun`. `delete_thread` (`noun="thread"`), `delete_commitment` (`"commitment"`) and `delete_campaign_event` (`"event"`, via `from . import ledger as ledger_routes`) call it in place of `forget_ref`, inside the same hold. Its docstring says why the bump is explicit (Decision 10).
  - `birthdays.crossed`'s per-row guard becomes `except _UNREADABLE:`. Its comment now says that a provider's arithmetic raises `OverflowError` on a year past its range (the reason `_UNREADABLE` gives), while a broken `describe` still fails as before.
  - The TS `ReconcileRunError` (Decision 11). `failedNote` reads `err.body as Partial<ReconcileRunError> | undefined`, with unchanged behaviour.
  - `api.removeAlias` and `api.removeLink` end in `.then(notifyShell)`, each with a one-line comment: an unmerge changes the rail's Ledger count.
  - The two delete surfaces re-read on a `partial_delete` refusal (resolution log #30). In `LedgerView.tsx`'s `run()` catch and `EventsPanel.tsx`'s `run()` catch, when `err instanceof ApiError && err.kind === "partial_delete"`, the surface also does what its success path does to re-read — LedgerView closes the row (`setOpenRow(null)`) and bumps the epoch; EventsPanel calls `reload()` — and still shows the refusal's sentence (`errorText(err)`). Any other refusal behaves as today (no re-read). The delete landed, so the row must not stay drawn from the pre-delete read: pressing Delete again would answer 404.
  - `ScenesView`: `const [reload, setReload] = useState(0)`; the list's load effect depends on `[cid, reload]`; the list banner's "Try again" calls `setReload((n) => n + 1)`. The costs effect and the shell banner's own "Try again" (`retryShell`) are unchanged.

- [ ] **Step 1: Write the tests.**
  - `test_ledger_routes.py::test_a_delete_whose_cascade_fails_moves_the_token`, parametrized over thread and commitment.
    - Setup: seed the record and a link naming it; take `before = store.revision.current(cid)`; `monkeypatch.setattr` `forget_ref` on the `grimoire.store.continuity.review` module to raise `OSError("disk full")`. Both route modules reach it through that module object, so one patch serves RED and GREEN.
    - Assertions: DELETE answers 500; the body's `kind == "partial_delete"`, `landed == [<noun>]`, and `detail` equals the Global Constraints sentence for the noun, with `OSError`; the record is gone; `store.revision.current(cid) != before`.
  - `test_events_routes.py::test_an_event_delete_whose_cascade_fails_moves_the_token`: the same, for `DELETE /campaigns/{cid}/events/{eid}` with a `by` link from a commitment to the event. Its `detail` is the event sentence: it names Reviewed links / merges and does not say the delete can be undone.
  - `test_ledger_routes.py::test_a_delete_whose_cascade_lands_answers_as_before`: the regression guard. DELETE still answers `{"ok": True}`, and the link is gone.
  - `test_birthday_occurrences.py::test_crossed_skips_an_overflowing_birthdate`. The rows are one actor with `"99999999999999999999-05-09"` and Mara with `"--05-11"`, over a span containing 05-11. `crossed` returns Mara's row and does not raise.
  - `client.test.ts`:
    - "removeAlias and removeLink tell the shell": `onShellChanged(spy)`; both calls on a 200 call the spy once each, and a rejected call does not call it;
    - a type-level line that uses its value, because `tsconfig.json` sets `noUnusedLocals`: `const e: Partial<ReconcileRunError> = { saved: true, follow_on: false }; expect(e.follow_on).toBe(false);`. Vitest erases types, so only `tsc` can fail this line.
  - `LedgerView.test.tsx`: "a delete that landed but could not clear its links re-reads the ledger". The thread delete rejects with `new ApiError(500, <thread sentence>, "partial_delete", {kind: "partial_delete", landed: ["thread"], detail: <thread sentence>})`, and the next `campaignLedger` read no longer lists the thread. After the click, the row is gone, the editor is closed, and the sentence shows. A second case: a 409 refusal of another kind leaves the row and does not re-read (the read mock is called once).
  - `EventsPanel.test.tsx`: "an event delete that landed but could not clear its links re-reads the events": the same shape for `deleteCampaignEvent` with `landed: ["event"]` and the event sentence. The event row is gone after the re-read, and the sentence shows.
  - `ScenesView.test.tsx`:
    - "the list's Try again reads the list again": `listScenes` rejects, and the banner "The scenes could not be read." shows. The mock then resolves with "The third", and clicking the banner's "Try again" calls `listScenes` a second time. "The third" renders, and the banner is gone. This follows "a failed shell read costs the counts, not the list", which pins the shell banner's retry the same way.
    - E's "a seeded open after a failed list survives Try again, and + New scene still opens" changes `mockRejectedValue` to `mockRejectedValueOnce`, so the suite's default list answers the re-read. Its assertions that the banner is gone after Try again held only because the button read nothing. This is the one edit to an existing frontend test, made because the retry is the behaviour being changed. It passes before and after the fix.
- [ ] **Step 2: Run them and confirm the RED.** From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_routes.py tests/test_events_routes.py tests/test_birthday_occurrences.py -q -p no:cacheprovider`. From `frontend/`: `npx vitest run src/api/client.test.ts src/routes/ScenesView.test.tsx src/routes/LedgerView.test.tsx src/components/EventsPanel.test.tsx`, then `npx tsc --noEmit -p .`. Expected:
  - the two partial-delete tests FAIL with the patched `OSError` raised out of `client.delete`: the route does not handle it, and the test clients re-raise server exceptions (`raise_server_exceptions` defaults to true). The token assertion is a pin, not a RED. Today the middleware's bump-on-raise already moves the token; Step 5 shows what it guards;
  - `crossed` FAILS with `OverflowError`;
  - the shell-notice test FAILS (the spy is not called);
  - the retry test FAILS (`listScenes` is called once);
  - the two partial-delete surface tests FAIL: the row is still listed, because neither `run()` re-reads on a refusal (their other-refusal cases PASS — they pin today's behaviour);
  - `tsc` reports TS2305 for `ReconcileRunError` in `client.test.ts`, and nothing else.
- [ ] **Step 3: Implement** the helper and its three call sites, the `crossed` guard, the client lines, the type, and the retry.
- [ ] **Step 4: Run them, the unchanged suites and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_routes.py tests/test_events_routes.py tests/test_birthday_occurrences.py tests/test_clock_store.py tests/test_continuity_routes.py tests/test_continuity_review.py tests/test_revision.py tests/test_import_guard.py tests/test_route_order.py -q -p no:cacheprovider`, then `npx vitest run src/api/client.test.ts src/routes/LedgerContinuity.test.tsx src/routes/ScenesView.test.tsx src/routes/LedgerView.test.tsx src/components/EventsPanel.test.tsx` and `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5: Show that the token pin bites.** Copy `routes/ledger.py` to the scratchpad and delete the `store.revision.bump(cid)` line from `forget_or_partial`. Run `test_a_delete_whose_cascade_fails_moves_the_token` and `test_an_event_delete_whose_cascade_fails_moves_the_token`. Expected: each FAILS on the token assertion only, because an `HTTPException` reaches neither the middleware's stamp nor its bump-on-raise. Restore the file. Expected: `cmp` against the copy prints nothing, and both tests PASS again. Record the bite in the ledger.
- [ ] **Step 6: Lint, types and eslint.** `make check-lint check-mypy check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 7: Commit:** `fix(continuity): a partial delete says it landed; unmerge refreshes the rail; crossed skips an unreadable birthdate; the scene list's retry reads again`

---

### Task 5: §30 — no store token reaches a reader, and one wording per relation

**Files:**
- Modify: `store/continuity/review.py`, `routes/continuity.py`, `frontend/src/api/types.ts`, `frontend/src/components/continuity/labels.ts`, `frontend/src/components/continuity/ReviewedGroup.tsx`, `frontend/src/components/storyGraph/model.ts` (F's)
- Create: `tests/test_continuity_wording.py`
- Test: `tests/test_continuity_review.py`, `tests/test_continuity_routes.py`, `frontend/src/routes/LedgerContinuity.test.tsx`, `frontend/src/components/storyGraph/model.test.ts`

**Interfaces:**
- Produces:
  - `review.RELATION_WORDS: dict[str, str]` (Global Constraints) and `review._relation_words(relation) -> str`, which answers with the table entry or "is linked to". The three link labels use it, and the two refusals use the constant copy.
  - `get_continuity`'s `raw_links` rows gain `"a_title": review.describe(cid, a, ledgers)` and `"b_title"`, where `a` and `b` are the row's `_text` values (an empty one titles as `""`). In TS, `ContinuityRawLink` gains `a_title: string; b_title: string`.
  - In `labels.ts`: `BROKEN_REASONS: Record<string, string>`; `brokenReason(code: string): string`, an own-property lookup that falls back to the generic sentence; `missingName(ref: string): string`; and `recordName(title: string, ref: string): string`, which returns `title` unless it is empty or equals `ref`, in which case it returns `missingName(ref)`. `ReviewedGroup` names every alias side and link end through `recordName`, and every reason through `brokenReason`. `RELATION_PHRASES.by` becomes "Due by" (Decision 12).
  - In F's `storyGraph/model.ts`: each `RELATION_PHRASE[r].out` is `RELATION_PHRASES[r]`, imported from `labels.ts` (which `model.ts` already imports for `KIND_PHRASES`). Its `in` phrases are unchanged.
  - In `test_continuity_wording.py`: `SOURCES`, the files the avoid-list guard reads, built by walking `backend/src/grimoire/` for `*.py` and `frontend/src/` for `*.ts` and `*.tsx`, leaving out `*.test.ts(x)` and `frontend/src/testkit/`, plus `README.md`. Also `PREFERRED: dict[str, tuple[str, ...]]`, each repo-relative path mapped to the §30 phrases it must show:
    - `frontend/src/components/continuity/labels.ts`: "Possible overlap", "May be finished", "Needs resolution review", "Basic matching active", "Semantic matching not configured", "Continuation of";
    - `frontend/src/components/continuity/ReviewedGroup.tsx`: "Merged into";
    - `frontend/src/components/review/ReviewPanel.tsx`: "Basic matching active", "Semantic matching not configured";
    - `frontend/src/components/SceneIdeaPicker.tsx`: "Claims to address";
    - `backend/src/grimoire/routes/todo.py`: "Possible overlap", "May be finished", "Needs resolution review".

- [ ] **Step 1: Write the tests.**
  - `test_continuity_review.py::test_link_journal_labels_use_relation_words`, parametrized over `pays_off`, `subthread_of` and `related_to`. Create a link, remove it, then create one and delete its record through the DELETE route. The three journal labels read "<a> <words> <b>", "<a> <words> <b> — removed" and "<a> <words> <b> — removed with deleted record", and none contains `_`. The existing `test_create_link_journals_and_round_trips` assertion becomes `"Mara's map pays off Mara's oath"`. That is the one edit to an existing backend test, and it is made because the label is the behaviour being changed.
  - `test_continuity_routes.py::test_link_refusals_carry_no_store_token`. Here `detail` is `resp.json()["detail"]`, the copy string: the app's `HTTPException` handler hoists the refusal's dict, so `kind` sits beside it at the top level, as the existing `r.json()["kind"]` assertions read it.
    - `POST /continuity/links` with `pays_off` from a thread to an event answers 400, with `detail` equal to the constant copy;
    - an apply whose `relation` is in the wrong direction answers 400 with its constant copy;
    - neither `detail` contains `_` or `'`.
  - `test_continuity_routes.py::test_raw_links_carry_their_ends_titles`. A self-collapsing raw link (Winifred's chart merged into Mara's map, then a link between them written by hand) has `a_title` and `b_title` equal to the two titles. A hand-written link to `event:gone` has that end's title equal to its ref (`describe`'s answer for a missing record).
  - `tests/test_continuity_wording.py`:
    - `test_relation_words_cover_every_relation`: `set(review.RELATION_WORDS) == set(effective.RELATIONS)`; no value contains `_`; and each of `before`, `on`, `after` and `by` reads `"is due " + relation` (§5.3's Meaning column, Decision 12).
    - `test_no_continuity_surface_uses_a_phrase_section_30_avoids` (Decision 12). Lower-cased, no file in `SOURCES` contains "ai detected", "detected duplicate", "duplicate detected", "continuity score", "health score", "broken campaign", "embedding required", "embeddings required" or "continuity disabled". Positive controls: `SOURCES` holds at least one file under each of `frontend/src/components/continuity/`, `frontend/src/components/storyGraph/`, `frontend/src/components/review/` and `backend/src/grimoire/store/continuity/`. It also holds `frontend/src/routes/embeddingsOn.ts` (the §9.5 disclosure), `frontend/src/components/StoryPressure.tsx`, `backend/src/grimoire/store/suggest.py` and `backend/src/grimoire/routes/scenes.py`, so a walk rooted in the wrong place fails rather than scanning nothing.
    - `test_section_30_preferred_phrases_are_used`: every phrase `PREFERRED` maps to a file occurs in that file, case-insensitively. The union of `PREFERRED`'s phrases is exactly §30's eight, so no phrase can drop out of the map.
  - `LedgerContinuity.test.tsx`:
    - "a broken entry says why in words and names a missing record by its kind". The fixture's invented reason `"endpoint missing"` becomes the server's real `"missing_endpoint"`, and gains `a_title` (a real title) and `b_title` (equal to its ref). The row shows "One of its records no longer exists.", the real title, and "a missing commitment". It shows neither `missing_endpoint` nor the ref. The existing "reviewed links offer Unmerge…" test finds the broken row by the new sentence.
    - "a dangling merge says why in words": an alias with `reason: "missing_target"` shows "The record it was merged into no longer exists.".
    - "an unknown reason code is never shown": `reason: "frobbed"` shows "This entry can no longer be followed." and not "frobbed".
  - `storyGraph/model.test.ts`, "the Ledger and the graph lead a link with one phrase": the keys of `RELATION_PHRASE` and of `RELATION_PHRASES` are the same set; for every key `r`, `RELATION_PHRASE[r].out === RELATION_PHRASES[r]`; and `RELATION_PHRASES.by === "Due by"`.
- [ ] **Step 2: Run them and confirm the RED.** From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py tests/test_continuity_routes.py tests/test_continuity_wording.py -q -p no:cacheprovider`. From `frontend/`: `npx vitest run src/routes/LedgerContinuity.test.tsx src/components/storyGraph/model.test.ts`. Expected:
  - the label, refusal, raw-title and relation-words tests FAIL;
  - the avoid-list guard and the preferred-phrase test PASS (regression pins). Show that each bites: add "Continuity score" to a `labels.ts` comment, then reword `KIND_PHRASES.possible_duplicate` in `labels.ts`, running both tests after each and restoring in between. Expected: each fails, and `git diff --exit-code -- frontend/src/components/continuity/labels.ts` passes afterwards;
  - the three LedgerContinuity cases FAIL;
  - the one-phrase test FAILS on `by` ("By" against "Due by").
- [ ] **Step 3: Implement** the backend words, refusals and raw-link titles, then the TS type, `labels.ts`, `ReviewedGroup` and `model.ts`.
- [ ] **Step 4: Run them, and the review suites.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py tests/test_continuity_routes.py tests/test_continuity_wording.py tests/test_continuity_review_routes.py tests/test_continuity_undo.py tests/test_ledger_routes.py -q -p no:cacheprovider`, then `npx vitest run src/routes/LedgerContinuity.test.tsx src/components/ChangesPanel.test.tsx src/components/storyGraph/model.test.ts src/routes/StoryGraphView.test.tsx` and `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5: Lint, types and eslint.** `make check-lint check-mypy check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 6: Commit:** `fix(continuity): review surfaces speak in words — relation labels, reasons, missing records and one phrase per relation`

---

### Task 6: Docs — CLAUDE.md, CONTRIBUTING, templates/README, store-guarantees and README, with new claims held to the code

**Files:**
- Modify: `CONTRIBUTING.md`, `docs/store-guarantees.md`, `README.md`, and `CLAUDE.md` only where Step 2 finds a claim false
- Test: `tests/test_docs_guard.py` (appended; the tracker test's decorator predicate is lifted to a module-level `_is_route(fn)` both tests share)

**Interfaces:**
- Produces: `test_docs_guard._is_route(fn: ast.FunctionDef) -> bool`. It is the tracker test's existing predicate, unchanged.

- [ ] **Step 1: Write the tests.**
  - `test_claude_md_names_every_handler_that_starts_a_reconcile_sweep`. Walk `SRC / "routes" / "*.py"`. Keep each top-level `def` that `_is_route` accepts and that contains an `ast.Call` whose callee is a `Name` or `Attribute` spelled `start_reconcile` or `schedule_reconcile`. Then:
    - the found set is non-empty, so a stale walk fails loudly;
    - each name appears in backticks in CLAUDE.md's "Detached runs" section (today: `put_chronicle` and `post_reconcile`).
  - `test_templates_readme_names_real_builders`. Every `` `store/<path>.py:<fn>` `` in `templates/README.md` resolves: `importlib.import_module("grimoire.store." + path.replace("/", "."))` has the attribute `fn`. The citation list is non-empty.
  - `test_templates_readme_documents_every_continuity_family`. For every directory under `templates/` whose name starts with `continuity_`, the README holds a heading starting `` ### `<dir>/` ``, and at least one such directory exists.
- [ ] **Step 2: Audit the documents.** Record each line below in the ledger as "true" or "fixed".
  - CLAUDE.md, "Detached runs": `git diff 368e6ec..HEAD -- backend/src/grimoire/routes | grep -nE 'run_draft|reserve_|start_computing|start_reconcile|schedule_reconcile|schedule\('` finds no new run starter, so "Twenty-five handlers" stands. The `background` bullet names both reconcile starters.
  - CLAUDE.md, write-token paragraph: it names `reconcile.persist_found` and `persist_proposals`.
  - CLAUDE.md, "Hand edits to the ledger": apply reaches the ledger only through `routes/ledger.move_thread`/`move_commitment` (`grep -n "ledger_routes\." backend/src/grimoire/routes/continuity.py`). "Seven sections" counts `LedgerView`'s `SECTIONS` (seven); Continuity review is a column section.
  - CLAUDE.md, "Adding a module that mutates campaign-scoped state": `continuity.doc` and `continuity.candidates` are in `DOMAIN_MODULES`, and `UNREVIEWED` did not grow since `BASE` (`git diff 646e2c8..HEAD -- backend/src/grimoire/store/locks.py`).
  - templates/README.md: the `continuity_identity/`, `continuity_reconcile/` and `scene_suggestions/` sections name their builder, variables, reply shape, the undecodable-reply rule and the Debug-capture sentence.
  - docs/store-guarantees.md lists no campaign files (`grep -n '\.json' docs/store-guarantees.md`), so no file list is added.

  Expected: every line true except the Step 3 edits; a false one is fixed in the same commit.
- [ ] **Step 3: Edit.**
  - CONTRIBUTING's `test_continuity_writer_guard.py` row reads: "continuity's discovery and projection modules, the Story Graph's included, never write plot or commitment records, events, scene ideas, reviewed aliases and links, or suppressions — only `continuity.review` and the routes apply a reviewed decision".
  - CONTRIBUTING's `test_docs_guard.py` row reads: "this page, `AGENTS.md`, `docs/store-guarantees.md`, and the claims of `CLAUDE.md` and `templates/README.md` it can check, still match the code".
  - store-guarantees' "The derived sidecars do not expire" bullet gains, at its end: "Unlike these three, the continuity candidate cache does notice: each finding keeps the fingerprint of the records it was found against, and one whose records have since changed is shown as stale until the next sweep (`store/continuity/pending.py`)." It claims no uniqueness. The vector cache is keyed by a text hash, and saved scene ideas turn stale on read, so "the one derived file that does notice" would be false (Decision 13).
  - README's "Other highlights" gains, after **Model routing**: "- **Continuity review and the Story Graph** — after each wrap-up Grimoire looks over the campaign's plot threads and commitments for ones that may be the same business, belong together, or be finished, and lists what it finds in the Ledger to merge, link, close or set aside; nothing changes until you choose. It works with no embeddings connection and catches more with one. The Story Graph draws the scenes played so far beside what is still owed and what is coming up."
- [ ] **Step 4: Run the docs and install-script guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_guard.py tests/test_install_scripts.py -q -p no:cacheprovider`. Expected: PASS, including `test_no_document_restates_another` for every pair and the three new tests.
- [ ] **Step 5: Lint.** `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 6: Commit:** `docs: the capstone's guard row, derived cache, README mention; hold CLAUDE.md and templates/README claims to the code`

---

### Task 7: Spec amendments, deferred hand-offs, and the acceptance walk (§32, §33)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`, `evals/cases.py`, `evals/README.md`
- Create: `tests/test_capstone_acceptance.py`, `evals/recordings/continuity-reconcile.timid.json`
- Test: whatever gap Step 3 finds, in the suite that owns it

**Interfaces:**
- Produces:
  - the spec's `# Appendix B. Acceptance evidence`. It is one Markdown table, `| Criterion | Claim | Evidence |`, with a row each for `AC1`…`AC19` and `§33`. Evidence items are written as follows:
    - a backend test is `` `test_<file>.py::test_<name>` ``;
    - a frontend test is `` `frontend/src/<path>.test.ts(x)` "<exact title>" ``;
    - an eval case is `` eval `<case>` ``;
    - AC19's evidence names `make check`, `evals/run.py` and §31 Slice G's gate bullet.
  - Appendix B's second table, `| §28.10 case | Eval case | Check | Tripped by |`, one row per case 1–10 (Decision 14). A row names its eval case, its grader check (`identity.*`, `reconcile.*` or `suggest.*`) and the counterexample variant that trips that check:
    - case 1: `continuity-identity`, `identity.same_obligation`, `unknown-id`;
    - case 2: `continuity-reconcile`, `reconcile.distinct`, `merged` (and `continuity-identity`'s `identity.distinct`, `merged`);
    - case 3: `continuity-reconcile`, `reconcile.continuation`, `merged` (and `continuity-identity`'s `identity.continuation`, `merged`);
    - case 4: `continuity-reconcile`, `reconcile.cross_type`, `timid`;
    - case 5: `continuity-reconcile`, `reconcile.close`, `timid`;
    - case 6: `continuity-reconcile`, `reconcile.keep_open`, `eager`;
    - case 7: `continuity-reconcile`, `reconcile.fulfilled`, `timid`;
    - case 8: `continuity-reconcile`, `reconcile.unproven`, `eager`;
    - case 9: `scene-suggestions`, `suggest.focus_coverage` and `suggest.distinct`, `cloned`;
    - case 10: `scene-suggestions`, `suggest.date_consistent`, `bad-date`. `scene-suggestions-anchor-on` covers the derived `on` date, which no reply can get wrong, so its compliant recording is the evidence and the row says so.
  - `Recording("timid", ("reconcile.cross_type", "reconcile.close", "reconcile.fulfilled"), "json")`, appended to `continuity-reconcile`'s recordings in `evals/cases.py`.
  - `test_capstone_acceptance.py`. Its helper `_appendix() -> str` returns the text from the Appendix B heading to the next `# Appendix` heading or the end. Five tests:
    - `test_every_criterion_has_a_row`: `AC1`–`AC19` and `§33` each head a row;
    - `test_cited_backend_tests_exist`: each file exists under `backend/tests/` and defines the function (`def <name>(` at any indent);
    - `test_cited_frontend_tests_exist`: each file exists and contains `"<title>"` verbatim;
    - `test_cited_eval_cases_exist`: each case name is a key of `evals.cases.BY_ID`;
    - `test_section_28_10_cases_each_have_a_tripping_counterexample`: the §28.10 table has rows for cases 1–10. Each row's eval case is in `BY_ID`, and each check it names is in the `expect_fail` of the named variant among `BY_ID[case].recordings`, except case 10's anchor-on clause, which names a compliant recording. `test_evals.py::test_recording_scores_as_declared` already proves that a recording trips exactly its declared checks.

- [ ] **Step 1: Write `test_capstone_acceptance.py` and run it.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py -q -p no:cacheprovider`. Expected: FAIL, because there is no Appendix B yet.
- [ ] **Step 2: Add the §28.10 counterexample (Decision 14).** Write `evals/recordings/continuity-reconcile.timid.json`: the compliant recording with three rows changed. `c3` (case 4) becomes `distinct`, with `from` and `to` empty. `c4` (case 5) and `c6` (case 7) become `keep_open`, each with a reason and `evidence_scenes: []`, as the compliant `keep_open` row has. Every other row stays as it is. Register the `Recording` above, and add `timid` to `evals/README.md`'s list of counterexample variants. Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py tests/test_eval_graders.py -q -p no:cacheprovider`, then, from the repo root, `backend/.venv/bin/python evals/run.py --case continuity-reconcile` (on Windows: `backend\.venv\Scripts\python.exe evals\run.py --case continuity-reconcile`). Expected: every recording scores as declared, `timid` included. `distinct` is in the `cross` vocabulary and `keep_open` in both lifecycle vocabularies, so `reconcile.enum` passes, and `keep_open` is no status word, so `reconcile.evidence` passes. Only the three verdict checks trip. Commit: `test(evals): a counterexample for section 28.10 cases 4, 5 and 7`.
- [ ] **Step 3: Walk the criteria.** For each row below, confirm on the integrated tree that every named test exists and passes, and that it proves the claim rather than something near it. Where a row has no test that proves its claim, write one in the owning suite (RED, then GREEN, or a characterization with its bite shown) or record a ruling. Then write Appendix B from the confirmed rows.
  - AC1: `test_absorb_identity.py::test_close_candidate_mapped_to_existing_rewrites_the_row`, `::test_a_new_record_with_no_close_neighbour_makes_no_identity_call`; `test_continuity_identity.py::test_reworded_duplicate_is_examined_with_the_record_as_candidate`, `::test_cross_type_never_a_candidate`; eval `continuity-identity`.
  - AC2: `test_continuity_identity.py::test_wordless_paraphrase_is_not_a_candidate_without_embeddings`, `::test_semantic_matching_finds_a_wordless_paraphrase`; `test_continuity_reconcile.py::test_unconfigured_makes_no_embedding_call`; `test_continuity_reconcile_routes.py::test_refresh_works_with_no_connection`.
  - AC3: `test_todo_route.py::test_missing_embeddings_shows_one_library_chore_on_the_global_page`, `::test_embeddings_with_recall_depth_zero_show_no_chore`, `::test_no_embeddings_still_shows_continuity_chores`.
  - AC4: `test_continuity_reconcile_routes.py::test_a_duplicate_proposal_mutates_nothing_until_apply`; `test_continuity_reconcile.py::test_a_stale_thread_is_nominated_for_closure_not_closed`, `::test_thread_and_commitment_overlap_is_a_relation_never_a_duplicate`; `test_continuity_writer_guard.py::test_the_scanned_modules_exist`; eval `continuity-reconcile`.
  - AC5: `test_continuity_effective.py::test_removing_alias_restores_two_records`; `test_continuity_review.py::test_create_alias_journals_and_undo_removes`; `test_continuity_undo.py::test_undo_alias_create_and_redo`; `test_continuity_review_routes.py::test_applying_a_duplicate_writes_an_alias_only`.
  - AC6: `test_continuity_effective.py::test_identity_law_threads`, `::test_identity_law_commitments`, `::test_render_helpers_obey_identity_law`, `::test_render_helpers_show_canonical_ids_only`; `test_frozen_campaign.py::test_the_frozen_campaign_still_reads_the_way_it_was_recorded`.
  - AC7: `test_suggest_store.py::test_snapshot_has_ids_commitments_timeline_and_index`, `::test_prompt_renders_refs_commitments_timeline_and_index`.
  - AC8: `test_continuity_pressure.py::test_several_holidays_are_kept_and_today_included`, `::test_events_listed_regardless_of_horizon_and_ordered_on_the_fixed_axis`; `test_suggest_controls.py::test_timeline_is_capped_but_keeps_sooner_and_anchors`; `test_suggest_store.py::test_timeline_contains_what_sooner_picks`.
  - AC9: `test_continuity_pressure.py::test_hebrew_birthday_and_event_ordering`, `::test_fake_provider_axis`; `test_suggest_controls.py::test_on_derives_the_date`; `test_suggest_store.py::test_plugin_calendar_dates_derive_in_native_notation`; eval `scene-suggestions-anchor-on`.
  - AC10: `test_suggestion_controls_route.py::test_must_and_avoid_overlap_is_400`, `::test_must_cap_and_kind_are_400`, `::test_stale_refs_are_409`; `test_suggest_controls.py::test_focus_avoid_must_report_misses`, `::test_batch_anchor_forces_every_suggestion`; `frontend/src/components/NewSceneChooser.test.tsx` "driver controls alter the request body" and "anchor mode sends the anchor and relation".
  - AC11: `frontend/src/components/SceneIdeaPicker.test.tsx` "a generated card shows its validated reasons", "constraint misses show warning chips" and "a rejected date says so"; `test_suggest_store.py::test_parse_output_resolves_labels_and_dates`.
  - AC12: `test_scene_ideas_provenance.py::test_post_scene_idea_round_trips_provenance`, `::test_stale_reason_after_resolution`, `::test_reads_never_write`; `test_suggestion_controls_route.py::test_saved_card_round_trip_goes_stale_after_resolution`; `frontend/src/components/SceneIdeaPicker.test.tsx` "stale ideas sit under a collapsed Stale group outside the budget".
  - AC13: `test_todo_route.py::test_todo_makes_no_embedding_calendar_or_client_call`, `::test_continuity_counts_come_from_the_cache_after_the_live_filter`; `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding`.
  - AC14: `test_continuity_graph.py::test_focusable_is_exactly_the_chooser_driver_set`, `::test_anchorable_is_exactly_the_chooser_anchor_set`, `::test_scenes_follow_play_order_not_recency_or_date`; `frontend/src/routes/StoryGraphView.test.tsx` "renders the drawing from one graph read".
  - AC15: `test_continuity_undo.py::test_undo_alias_create_and_redo`, `::test_undo_link_create_and_redo`; `test_continuity_review.py::test_create_link_journals_and_round_trips`; `frontend/src/routes/LedgerContinuity.test.tsx` "apply requires an explicit action".
  - AC16: `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding`; `test_continuity_routes.py::test_graph_route_makes_no_model_call`; `test_suggestion_controls_route.py::test_opening_paths_make_no_model_call`; `frontend/src/routes/StoryGraphView.test.tsx` "only one graph read across mount, lenses, toggles, arcs and nodes"; `frontend/src/components/NewSceneChooser.test.tsx` "changing a control starts no generation"; `frontend/src/routes/LedgerContinuity.test.tsx` "a stale 409 re-renders current records and does not start a run".
  - AC17: `test_frozen_campaign.py::test_the_frozen_campaign_still_reads_the_way_it_was_recorded`, `::test_the_read_only_sweep_writes_nothing`; `test_continuity_doc.py::test_absent_file_reads_empty`; `test_continuity_candidates.py::test_absent_cache_reads_empty`; `test_scene_ideas_provenance.py::test_ideas_without_provenance_read_nothing_extra`.
  - AC18: `frontend/src/routes/ConfigView.test.tsx` "the embeddings chip reports embedding separately from recall depth" and "the disclosure names every embedded payload".
  - AC19: `make check`, `evals/run.py`, and §31 Slice G's gate bullet (Task 8).
  - §33: `test_continuity_candidates.py::test_kinds_are_the_four_in_spec_order`; `test_continuity_graph.py::test_graph_tuples_are_pinned`; `test_suggest_controls.py::test_controls_tuples_are_pinned`; and the Task 8 stopping-rule review.

  Two checks apply to every row:
  - A cited test whose only guard is a raiser inside a soft wrapper does not prove "no call". Pair it with this plan's recorder test, as AC13 and AC16 do.
  - Expected: every row has at least one passing test that proves its claim. Each gap closed is recorded in the ledger.
- [ ] **Step 4: Amend the spec.** Each edit cites this plan's decision number.
  - §29 names both rows' exact keys and closed vocabularies, and says where the capture note lives: `templates/README.md` and `docs/incoming-llm-capture.md` (Decisions 1–3).
  - §9.4: a zero-vector text is never cached, and is re-sent once per sweep within `RECONCILE_WARM_LIMIT`; accepted (Decision 5).
  - §11.1 and §25.1: a run is its own pass plus at most one follow-on pass for adopted triggers, with at most one model call per pass. Refs pended after the last take wait for the next fresh run (Decision 4).
  - §25.2: lexical features are computed once per record per sweep (Decision 6).
  - §18.2, §19.7 and §25.3 take Decision 7's reading of "once" and name the pinning tests; §19.7 also records the kept location read (Decision 8); §25.3 applies the no-card, no-image rule to the candidates and saved-idea reads (Decision 9).
  - §5.7: the partial delete (Decision 10). It is worded on what was true before G: the middleware's bump-on-raise already moved the token, and the reader got a bare 500. The change gives that 500 a body saying what landed, by noun, and keeps the token moving through an explicit bump.
  - §12.6 and §12.8: reason sentences, missing-record names and relation words (Decision 12).
  - §5.3 and §30: one wording per link relation. The Ledger and the Story Graph lead a link with `RELATION_PHRASES` ("Due by" for `by`), and the journal uses the sentence forms of §5.3's Meaning column. This closes F's h8 (Decision 12).
  - §25.4: the rule binds what the capstone adds to Todo and the shell, and the shell's open-scene turn count predates it (Decision 17).
  - §28.10: the case-to-check table lives in Appendix B, and the `timid` counterexample covers cases 4, 5 and 7 (Decision 14).
  - §13.6: the Hebrew month-only residual (b4).
  - §31 Slice B gains b3's two tolerance widenings, unless the spec already names them.
  - §31 Slice G gains a deviations list (Decisions 4–12 and 17, as for D) and a "Slice G rulings" list. The rulings list holds every deferred hand-off from Before Task 1 step 5, with its reason (Decision 16). It holds no closed item: (e1) is closed by `737c236`, so it is not listed.
  - Appendix B (Step 3).
- [ ] **Step 5: Close the hand-offs.** Every `## Inherited hand-offs` line in the ledger names its closing task and commit, or its deferral and the spec section that now records it. Expected: no line has neither.
- [ ] **Step 6: Run the acceptance, docs and import guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py tests/test_docs_guard.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 7: Lint.** `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline, or an improvement re-baselined in this task's commit (Global Constraints, Lint ratchet).
- [ ] **Step 8: Commit:** `docs(spec): continuity capstone acceptance evidence and slice G's recorded deviations`, after Step 2's commit and any gap-closing test, each in its own `test(...)` commit.

---

### Task 8: The slice gate — make check, evals, and the two final reviews over the whole capstone, in CLAUDE.md's order

- [ ] **Step 1: The full gate.** From the repo root: `make check PY=$PWD/backend/.venv/bin/python`. This covers check-lint, check-mypy, check-templates, check-eslint, check-web, check-py (under its coverage floor) and check-pydantic1. Expected: PASS, except for failures the ledger listed in Before Task 1 step 2. No ratchet reports an improvement, because each task re-baselined its own (Global Constraints, Lint ratchet). G changes no template, so `check-templates` passes unchanged.
- [ ] **Step 2: Evals replay.** `backend/.venv/bin/python evals/run.py` (on Windows: `backend\.venv\Scripts\python.exe evals\run.py`). Expected: every recording scores as declared, Task 7's `timid` included. G changes no prompt, so a failure here is a regression to investigate, not a re-record.
- [ ] **Step 3: Build the review inputs, and give every path a reviewer.** In the scratchpad:
  - `paths.txt`: `for r in 646e2c8..f52c7f4 0fa88a7..877468f 6e150db..c84108c 81bbb82..368e6ec 368e6ec..HEAD; do git log --format= --name-only "$r"; done | sort -u`, kept to paths that exist at `HEAD` or at `646e2c8`;
  - `capstone.diff`: `git diff 646e2c8..HEAD -- $(cat paths.txt)`;
  - `ranges.txt`: `git log --oneline <range>` for each range, looped;
  - `area1.txt` to `area4.txt` and `docs.txt`: a scratch script assigns every line of `paths.txt` by the rules below (Decision 15), and prints any path no rule matches. Backend paths are relative to `backend/src/grimoire/`.
    - **(1) Substrate, canonical reads, Ledger and review:** `store/continuity/{__init__,doc,canon,effective,involvement,review,candidates,pending}.py`; `store/{undo,scene_refs,plot,commitments,events,locks,revision,briefing,clock,timeline}.py`, `store/__init__.py` and `store/context/` (AC6's canonical prompt, briefing and digest path); `routes/{ledger,campaigns,models,__init__}.py`. Also its part of two shared modules: in `routes/continuity.py`, the read, alias and link, candidates, apply, dismiss and restore routes; in `routes/scenes.py`, the briefing route.
    - **(2) Similarity, identity and reconcile:** `store/continuity/{similarity,identity,reconcile}.py`; `store/absorb/`; `store/{pending_reviews,routing}.py`; `main.py`; `routes/{common,config,runs}.py`; `templates/continuity_*/` and `templates/absorb/`; `scripts/verify_templates.py`; and `evals/`, except `evals/README.md`. Its part of the shared modules: in `routes/scenes.py`, the identity phase, `_identity_outcome` and `put_chronicle`'s trigger; in `routes/continuity.py`, `_sweep_pass`, `_passes`, `_log_pass`, `start_reconcile`, `schedule_reconcile` and `post_reconcile`.
    - **(3) Time, suggestions, ideas, Todo, shell and graph:** `store/continuity/{pressure,drivers,graph}.py`; `store/{birthdays,suggest,scene_ideas,chores,pcs,overlay}.py`; `routes/{todo,shell}.py`; `templates/scene_suggestions/`. Its part of the shared modules: in `routes/scenes.py`, the suggestion, intent and scene-idea routes; in `routes/continuity.py`, the drivers and graph routes.
    - **(4) The frontend:** every path under `frontend/src/`. Among them are `api/client.ts`, `api/types.ts`, `App.tsx`, `index.css`, `ledgerPaths.ts` and `shell/rail.ts`; `components/{continuity,review,ledger,storyGraph}/`; `NewSceneChooser`, `StoryPressure`, `pressureControls`, `useSceneSuggestions`, `sceneDraft`, `SceneIdeaPicker`, `PageShell` and `ShellPayloadContext`; the routes `LedgerView`, `ConfigView`, `embeddingsOn`, `ScenesView`, `CampaignHub` and `StoryGraphView`; and `testkit/`.
    - **Tests** go with what they exercise. A backend test named for a module goes to that module's area. `conftest.py`, `llm_fakes.py`, `review_runs.py`, `fixtures/llm/` and the eval tests go to (2). `suggest_golden.py` and `fixtures/suggest_golden.json` go to (3). Every other backend test, the store guards and `fixtures/frozen_campaign/` among them, goes to (1). A frontend test goes to (4).
    - **`docs.txt`:** `docs/`, every `*.md` and `lint-baselines/`. No code reviewer is assigned them. Every reviewer receives the spec and the plans whole, and adversarial reviewer (5) reads the rest against §27 and §31 G.

  Expected: `capstone.diff` is non-empty, and `ranges.txt` shows the boundary subjects from Global Constraints. The script prints no unmatched path; a path it does print is given a rule, and its brief amended, before any reviewer is dispatched. `sort -u area1.txt area2.txt area3.txt area4.txt docs.txt` equals `paths.txt`, and the only paths in more than one file are `routes/continuity.py` and `routes/scenes.py`. Any commit in `368e6ec..HEAD` that is not E, F or G is marked out of scope in the brief.
- [ ] **Step 4: Implementation → done, `/codex:review` over the whole capstone.** If `codex` is on `PATH`, run it over `capstone.diff`. Otherwise dispatch four independent Claude reviewers (fresh subagents that wrote none of the capstone), one per area of Step 3. Each gets the spec, the seven plans, `ranges.txt`, its area file and `git diff 646e2c8..HEAD -- $(cat areaN.txt)`. For a shared route module it is told which part is its own. It is told that unrelated features touched some of the same files and are out of scope, and is asked for correctness defects, each with a failing input. Record the stand-in, the briefs and the raw findings in the ledger. Expected: a findings list per reviewer, possibly empty.
- [ ] **Step 5: Resolve the code-review findings, before the next gate.** Verify each finding against the code before acting (superpowers:receiving-code-review). Then either:
  - fix it in the owning module, RED then GREEN, one `fix(continuity): …` commit per finding or per tight group; or
  - rule on it with a reason. An accepted deviation is amended into the spec under §31 Slice G.

  Then run `make check PY=$PWD/backend/.venv/bin/python` and the evals replay. Expected: PASS, and every code-review finding reads fixed (with its test and commit) or ruled (with its reason) in the ledger, under this gate's own heading. The adversarial review does not start until this step is done (CLAUDE.md: a gate's findings are resolved before the next stage).
- [ ] **Step 6: Done → actually done, `/codex:adversarial-review` against the whole capstone and the spec.** First rebuild `capstone.diff` and `ranges.txt` at the new tip, since Step 5's fixes are part of what this gate reviews. If `codex` is on `PATH`, run it over `capstone.diff` plus the spec. Otherwise dispatch five independent Claude reviewers, one per spec region:
  - **(1)** §3–§8; the store and route side of §12.1–§12.9 (what apply writes for each action, and refresh and stale handling); §21–§22; §26; §27; and §28.1;
  - **(2)** §9–§11, §23–§24, §25.1–§25.2, §29, and §28.2–§28.4;
  - **(3)** §13–§18, §25.4, and §28.5–§28.7;
  - **(4)** the UI of §12, §16 and §17, plus §19, §20, §25.3, and §28.8–§28.9;
  - **(5)** §0, §1–§2, §28.10, §30, §31 (its deviation lists and the Slice G rulings), §32 (through Appendix B) and §33.

  Before dispatch, check the regions against the spec's numbered headings, `grep -nE '^#{1,2} [0-9]+(\.[0-9]+)?\.? ' docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. Expected: every heading from §0 to §33 falls in at least one region. A parent heading counts as covered when each of its subsections is. §12, §16 and §17 fall in two regions on purpose, once for the store and once for the UI.

  Ask each reviewer, specifically: does the capstone implement this region end to end? Report gaps, drift and quietly dropped requirements, each against a section and with the code that shows it. Reviewer (5) also checks the stopping rule. The diff must add none of §33's parked items, and G must add no feature (Global Constraints, Scope). Record the stand-in, the briefs and the raw findings in the ledger. Expected: a findings list per reviewer.
- [ ] **Step 7: Resolve the adversarial findings.** The same two ways as Step 5. Then run `make check PY=$PWD/backend/.venv/bin/python` and the evals replay again. Expected: PASS, and every adversarial finding reads fixed or ruled in the ledger, under this gate's own heading.
- [ ] **Step 8: Record completion.**
  - §31 Slice G gains a gate bullet (AC19). It says whether `codex` was on `PATH`. If it was not, the bullet names how many Claude reviewers stood in for each gate, and over which areas and regions. It records that the code-review findings were resolved before the adversarial review ran, and that both sets are resolved.
  - The spec's **Status** line keeps its account of the spec-gate review and Appendix A. Its last sentence, "Ready for implementation planning.", becomes "Implemented in Slices A–G (§31); acceptance evidence in Appendix B; continuity is parked (§33)."
  - `docs/superpowers/PHASE-STATUS.md` gains a closing section, `## Continuity capstone (2026-10)`. It names the spec and the seven plans, says in one paragraph what the capstone added (reviewed merges and links, the reconciliation sweep reviewed in the Ledger, temporal pressure and scene drivers, driver-aware suggestions and saved ideas, the Story Graph), points to Appendix B, and states: "Continuity is parked (spec §33): further work here is correctness defects and small usability fixes; the next major investment is mechanics."

  Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. Commit: `docs: the continuity capstone is complete and parked`.
- [ ] **Step 9: Close the ledger.** Every inherited hand-off is closed or deferred-and-recorded, and every review finding is resolved. The ledger's last line names the G tip and states "Slice G done". Expected: `git status` is clean.

---

## Self-review notes

**Spec coverage (Slice G):**

| Spec section | Task |
|---|---|
| §29 task labels, the route verbatim, literal call sites in `routes/`; `suggestions` keeps its name | 1 (route pin), with the existing `test_routing_guard` and `test_usage_guard` |
| §29 log rows: counts only, the listed fields, no narrative text | 1 (key sets, closed vocabularies, leak check over `_dumped`) |
| §29 docs note that Debug capture still records replies | 1 (docs-guard pin) |
| §25.1 one identity call per absorb; one reconcile call per run; never per pair | 2 (burst bound, marked `reconcile`), with the existing `test_one_batched_call_for_several_ambiguous_rows` and `test_refresh_with_a_connection_adjudicates_once` |
| §25.2 worker thread; changed×all and all×all under the cap; `pairs_capped`; texts and vectors once; batches; in-memory dot products; top-k; unordered dedupe | 2 (off-loop, text once, normalization once, capped reported), with D's `test_incremental_pairs_only_changed_records_against_all`, `test_a_full_sweep_scores_every_unordered_pair_once`, `test_sweep_embeds_at_most_the_warm_limit` and the top-k tests |
| §25.3 graph read cost; no per-node card reads; no image scans | 3 (constant reads through both scene bindings, location read once, actor names from meta), with F's Task 7 tests |
| §25.4 Todo and shell add no embedding or transcript work; no new shell badge | 3 (the recorder test; the shell's transcript reads held to the one open scene at both sizes), 7 (Decision 17's ruling) |
| §3.10 and AC16, every read path, the briefing, the digest preview and both Todo item routes included | 3 (recorder test), 7 |
| §30 prefer/avoid on every source file; no internal token on a review surface; one wording per link relation | 5 |
| §28.10 ten cases, each with a check a counterexample trips | 7 (`timid`; Appendix B's §28.10 table; `test_capstone_acceptance.py`) |
| §31 G docs: CLAUDE.md inventory, CONTRIBUTING guard table, templates/README, store-guarantees; §30 README mention; §27's docs bullets | 6 |
| §0 steps 6–7; AC19 | 8 (every path assigned an area; the code review resolved before the adversarial review; regions checked against every heading) |
| §32 AC1–AC19 and §33, walked and held to the tests | 7 (Appendix B, `test_capstone_acceptance.py`), 8 (stopping-rule review) |
| hand-offs A–F naming G, or deferred | Before Task 1 step 5; 2, 3, 4, 5, 6, 7, 8 |

**Deliberate deviations (amended into the spec at Task 7):**

- the two log rows carry fields beyond §29's list, all counts or closed modes (Decision 1);
- a run may make two model calls, its own pass's and one follow-on's (Decision 4);
- a zero-vector text is re-sent once per sweep (Decision 5);
- "once per request" is a constant number of whole-file reads, never literal once (Decision 7), and the location names stay a whole-kind read (Decision 8);
- a partial delete answers a structured 500 `partial_delete`, worded by noun, and bumps the token itself (Decision 10);
- journal labels and refusals no longer carry the stored relation token, and `by` reads "Due by" in the Ledger (Decision 12);
- §25.4 binds what the capstone adds; the shell's pre-capstone transcript read stays (Decision 17).

**Open questions (none block execution):**

- Whether CLAUDE.md should carry a one-line pointer to §33's stopping rule. The owner limited CLAUDE.md to what its own claims require, so this plan records the parked status in `PHASE-STATUS.md` and the spec only. The owner may add the line.
- The reconcile prompt still names actors through `relationships.actor_name`, so it gets Decision 9's cheaper read for free. Moving it to rosters was not considered necessary: the sweep is a background run bounded by `RECONCILE_MAX_CANDIDATES` and `RECONCILE_ACTORS`, and it memoizes per actor.
- The residuals recorded in the spec (d2, b4, b2) remain correctness items for after the capstone, under §33's "correctness defects" channel. `DRIVER_PROMPT_CAP` (e3) waits for real prompts to tune against.

**Type consistency:**

- `Subject.features()` (Task 2) is the only new similarity name. `lexical`'s return shape is unchanged.
- `relationships.actor_name` (Task 3) reads the pre-capstone `characters.name_and_versions(root, cid)[0]` and F's `pcs.name_of(root, pid)`; `suggest._actor_name` reads `pcs.name_of`. G adds no store reader.
- `_seed(client, n) -> tuple[str, list[str]]` and `_reads(monkeypatch, cid, sid)` (Task 3) are used by every test in `test_continuity_read_cost.py`, the shell pin included.
- `routes/ledger.forget_or_partial(cid, ref, *, name, noun)` (Task 4) is used by `delete_thread`, `delete_commitment` and `delete_campaign_event`. Its sentence is chosen by `noun`, matching the Global Constraints copy.
- `review.RELATION_WORDS` and `_relation_words` (Task 5) are used by the three link labels. `test_relation_words_cover_every_relation` holds them to `effective.RELATIONS` and to §5.3's "is due <relation>".
- `RELATION_PHRASES.by` is "Due by", and F's `RELATION_PHRASE[r].out` is `RELATION_PHRASES[r]` for every `LinkRelation` (Task 5); `model.test.ts` holds the key sets equal.
- `ContinuityRawLink.a_title` and `b_title` (Task 5, TS) match `get_continuity`'s new keys. `recordName`, `brokenReason` and `missingName` are used only by `ReviewedGroup`.
- `ReconcileRunError` (Task 4) types the body `failedNote` reads. Its keys are D's `error` keys (`kind`, `detail`, `status`, `sweep`, `saved`, `follow_on`).
- `IDENTITY_COUNTS` and `RECONCILE_ROW_KEYS` (Task 1) are built from `identity.CHECK_DECISIONS` and `continuity_routes._WORDS`, never retyped, so a new decision word changes the expected set and the producer together.
- `Recording("timid", …)` (Task 7) is the variant Appendix B's §28.10 rows 4, 5 and 7 name, and `test_section_28_10_cases_each_have_a_tripping_counterexample` reads it from `BY_ID`.

---

## Adversarial review resolution log

The plan → implementation gate ran on 2026-10-06. Codex is not installed, so independent Claude reviewers stood in for `/codex:adversarial-review` (Global Constraints, Review gates). They read the plan under three lenses: spec coverage, grounding, and process and privacy. They reported 29 findings. Each was checked against this plan, the spec and the code before it was resolved: the E tip in this worktree, and F's ledger and code in F's worktree. Four claims were checked by running code against a throwaway store in the scratchpad. A failed cascade already moves the token. The default `TestClient` re-raises. An `HTTPException(500)` raised at the cascade answers with `kind`, `landed` and `detail` at the top level, but leaves the token unmoved. And the planned `timid` body, scored by `evals.runner.score`, trips exactly the three checks it is meant to. Twenty-eight findings hold and one is rejected. Four of the twenty-eight repeat another finding in full and share its resolution (14, 22, 23 and 24).

| # | Finding (lens, severity) | Applied / Rejected | What changed, or why not |
|---|---|---|---|
| 1 | E's and F's hand-offs to G are not seeded, so none has an owner (spec-coverage, important) | Applied | Verified against the E ledger's Task 13 hand-offs and F's h8. New seeds: (e2) E's gate reviews → Task 8; (e3) `DRIVER_PROMPT_CAP` tuning → deferred to the Slice G rulings, with no figure; (e4) the ScenesView retry → Task 4, with Decision 11 and Scope widened; (f6) the `by` wording → Task 5. Step 5 reads the ledgers only after F's gate is confirmed. An unseeded item that would change code goes to the owner. |
| 2 | Decision 12 adds a third wording for each link relation instead of settling h8 (spec-coverage, important) | Applied | `by` is the only entry where D's `RELATION_PHRASES` ("By") and F's `RELATION_PHRASE.out` ("Due by") differ. `RELATION_PHRASES.by` becomes "Due by", and F's `out` phrases are taken from it. A `model.test.ts` case pins the key sets and every `out`. `RELATION_WORDS` keeps a sentence form on purpose, because it is §5.3's Meaning column ("is due by"), and a test holds each temporal word to it. Decision 12 says why. It also names the hedged `DECISION_LABELS` and the chooser's anchor select as unchanged, with reasons. |
| 3 | The §30 avoid-list guard skips surfaces the capstone added (spec-coverage, important) | Applied | Every named file is in the capstone path set. The guard now reads every non-test source file under `backend/src/grimoire/` and `frontend/src/`, plus `README.md`, rather than a set derived from the commit ranges. CI checks out one commit deep, so a test cannot run `git log` over the ranges, and a checked-in list goes stale. No avoided phrase occurs anywhere in that source today. Positive controls name `embeddingsOn.ts`, `StoryPressure.tsx`, `suggest.py`, `routes/scenes.py` and four directories. |
| 4 | Task 8's four code-review areas leave capstone files unassigned (spec-coverage, important) | Applied | Confirmed with Step 3's own range loop. `main.py`, `routes/{common,config,models}.py`, `store/{briefing,chores,clock,commitments,events,locks,pending_reviews,plot,revision,routing,timeline}.py`, `store/context/`, `templates/absorb/` and most of the frontend had no area. Step 3 now assigns every path by rule, tests and fixtures included, to an area or to `docs.txt`. It prints any unmatched path, and expects the union to equal `paths.txt`. Two route modules are split by function on purpose, so the overlap check allows exactly those two, not "exactly one area". |
| 5 | The five adversarial regions miss §28.1–§28.7 and the store side of §12.1–§12.3 and §12.9 (spec-coverage, important) | Applied | Region (1) gains the store and route side of §12.1–§12.9, and §28.1. Region (2) gains §28.2–§28.4, (3) gains §28.5–§28.7, and (5) takes all of §1–§2. Before dispatch, Task 8 Step 6 checks the regions against the spec's numbered headings: a grep listing all 122, §0 to §33. |
| 6 | The final adversarial review runs before the code-review findings are resolved (spec-coverage, important) | Applied | Task 8 is reordered: code review (Step 4); resolve, then `make check` and the replay (Step 5); rebuild the diff and run the adversarial review (Step 6); resolve again (Step 7). Each gate's resolution is recorded under its own heading. Decision 15 states the order. |
| 7 | AC16's recorder test skips the briefing, the digest and the closures items route (spec-coverage, minor) | Applied in part | Added `GET …/scenes/{sid}/briefing`, `GET /api/todo/continuity-closures/items` and `POST …/advance/preview`. B's canonical digest (`clock.digest`) is reached through the preview. `GET /clock` returns `clock.state`, which the capstone did not change, so it is not added, and Task 3 says why. `_seed` now returns the scene ids. |
| 8 | The evals work does not map §28.10's ten cases to graded checks (spec-coverage, minor) | Applied | Verified in `evals/cases.py`: no counterexample of `continuity-reconcile` trips `reconcile.cross_type`, `close` or `fulfilled` (cases 4, 5 and 7). Task 7 Step 2 adds `continuity-reconcile.timid` (c3 `distinct`; c4 and c6 `keep_open`), and lists it in `evals/README.md`. Scored offline in the scratchpad, that body trips exactly those three checks. Appendix B gains a §28.10 table. `test_capstone_acceptance.py` gains a fifth test, holding each row's check to a declared counterexample; `test_evals` proves the trip. Decision 14 records it. |
| 9 | §30's preferred phrases and §29's `suggestions` name have no pin (spec-coverage, minor) | Applied | The route test asserts `TASK_ROUTE["suggestions"] == "suggestions"`. `test_section_30_preferred_phrases_are_used` maps each phrase to the surface that shows it today: `labels.ts`, `ReviewedGroup.tsx`, `ReviewPanel.tsx`, `SceneIdeaPicker.tsx` and `routes/todo.py`. "Somewhere in the source" was rejected: "Merged into" also appears in pre-capstone files, so that check could never fail. The map's phrases must be exactly §30's eight. |
| 10 | The burst test omits `@pytest.mark.reconcile`, so `schedule_reconcile` is a no-op (grounding, important) | Applied | Verified: conftest's `_auto_reconcile_off_unless_asked` turns `AUTO_RECONCILE` off, and `schedule_reconcile` then returns at once. The sibling test carries the marker. The burst test is now marked. The Scope constraint and Task 2 Step 3 say a test-setup failure is fixed in the test, never in the producer. |
| 11 | The no-model-call test builds its fake with `from_entries([])`, which raises (grounding, important) | Applied | Verified: `Cassette.__init__` raises on an empty list, a trap E's ledger already records. The fake is built once with one entry that never matches, as E's `test_opening_paths_make_no_model_call` does. The test asserts `fake.calls == 0`. |
| 12 | Scene-reader spies patch only `scenes.read`, missing the package re-exports (grounding, important) | Applied | Verified: `store/scenes/__init__.py` binds the readers by value, and the Ledger route and `routes/continuity.py` call `store.scenes.list_scenes`. The cited `campaigns.py:1858` is the pins route, off these paths, but the mechanism holds. `_reads` patches both bindings with one wrapper. It then calibrates on a seeded scene: one call through each binding must count 2 before the counters are zeroed. |
| 13 | The partial-delete copy says "the delete can be undone", false for events (grounding, important) | Applied | Verified: thread and commitment deletes run under `store.undo.journalled`, while `store.events.delete` and the event route write no journal row. The sentence now depends on the noun. The event sentence points at Reviewed links / merges, where a link left behind is listed as broken. Decision 10, Review Focus 4 and the Global Constraints copy match, and the event test pins its sentence. |
| 14 | Hand-offs missing from the seeded list; Task 5 worsens the `by` wording (grounding, important) | Applied (as 1 and 2) | The same defect as 1 and 2, with the same resolution. |
| 15 | §25.4's "no full transcript reads" is claimed for the shell, which reads transcripts (grounding, important) | Applied | Verified: `routes/shell._scene_turns` reads each open scene's transcript, in code that predates `BASE`. New Decision 17 rules that §25.4 binds what the capstone adds, and Task 7 amends §25.4. The shell pin moves to `test_continuity_read_cost.py`. With one open scene at both sizes, `read_scene` is called only for that scene, as often at 12 as at 1, with no continuity review work. The Self-review row now says this. |
| 16 | `characters.name_of` duplicates `characters.name_and_versions` (grounding, minor) | Applied | Verified: `name_and_versions` reads no card, raises where `read_character` would, and is stat-memoized. `name_of` is dropped from Decision 9, Task 3, the File structure and Type consistency. `relationships.actor_name` reads `name_and_versions(...)[0]`. Its test folds into `test_actor_name_reads_meta_only`, with a removed-versions case. |
| 17 | Task 4's RED is a raised `OSError`, not a 500 (grounding, minor) | Applied | Reproduced on a throwaway store: with the default `TestClient`, the `OSError` reaches the test. Task 4 Step 2 now expects the raise. Resolved together with 21. |
| 18 | The refusal test reads the wrong `detail` (grounding, minor) | Rejected | The app's `HTTPException` handler (`main.py`) hoists a dict `detail` to the top level. So `resp.json()["detail"]` is the copy string, with `kind` beside it, as the existing tests' `r.json()["kind"]` reads show. A throwaway-store run of a dict-detail 500 confirmed the top-level keys. The proposed `resp.json()["detail"]["detail"]` would fail on a string. Task 5 gains a parenthetical, so the same misreading is not made during implementation. |
| 19 | The "no connection" case misdescribes the test it copies (grounding, minor) | Applied | Verified: that test routes `continuity` to a keyless `openrouter` connection and deletes nothing. The case now says so, with the two calls. |
| 20 | The store-guarantees sentence calls the cache "the one" derived file that notices (grounding, minor) | Applied | The vector cache is keyed by a text hash, and saved ideas turn stale on read. The sentence now reads "Unlike these three, the continuity candidate cache does notice…" (Task 6, Decision 13). |
| 21 | Decision 10's premise is false: the token already moves when the cascade fails (process-and-privacy, important) | Applied | Verified in `main.py`: `_CampaignActivityStamp` bumps on an unhandled raise, in code that predates `BASE`. Reproduced on a throwaway store: 500, token moved, record gone. Decision 10, Review Focus 4, the Write-token constraint, hand-off a1 and the §5.7 amendment now say the change structures the 500. The explicit bump stays: an `HTTPException` reaches neither the stamp nor the bump-on-raise (checked: a 500 raised that way left the token unmoved), and D's `partial_dismiss` already handles it the same way. Task 4 Step 2 calls the token assertion a pin, and new Step 5 shows its bite by deleting the bump. |
| 22 | The burst test cannot pass without the marker (process-and-privacy, important) | Applied (as 10) | The same defect as 10. |
| 23 | h8 is not seeded, and Task 5 adds a third `by` wording (process-and-privacy, important) | Applied (as 1 and 2) | The same defect as 1 and 2. |
| 24 | E's G-addressed hand-offs are not seeded (process-and-privacy, minor) | Applied (as 1) | The same defect as 1. The `DRIVER_PROMPT_CAP` item is deferred with no figure, because tuning against real prompts would mean measuring a live store. |
| 25 | The ratchet two-step is deferred to Task 8 (process-and-privacy, minor) | Applied | Verified: `scripts/ratchet.py` exits 1 on an improvement. The Lint-ratchet constraint and every per-task lint step now say to re-baseline in that task's commit. Task 8 Step 1 expects no improvement left over. |
| 26 | The venv-spelling rule names the wrong documents (process-and-privacy, minor) | Applied | Verified against `test_install_scripts.py`'s `DOCS`: `CLAUDE.md`, `README.md`, `evals/README.md` and the frozen-campaign README, but not CONTRIBUTING. The Docs-rules constraint is corrected. |
| 27 | The log-row leak checks are blind to non-ASCII prompt text (process-and-privacy, minor) | Applied | Verified: both continuity user prompts carry em dashes, which default `json.dumps` escapes. Task 1 adds `_dumped(row)` (`ensure_ascii=False`) for every leak check. A fourth bite mutation leaks an em-dash prompt line, and adds its key to the expected set for that run, because the key-set assertion would otherwise catch it first. |
| 28 | Bite steps lack a restore check, and commit steps name no paths (process-and-privacy, minor) | Applied | Two new Global Constraints bullets. Bite mutations: copy the file, then `cmp` after restoring, or `git diff --exit-code` on a file the task has not touched. Commits: stage the task's Files by path, never `-A`. The bite steps in Tasks 1, 2, 4 and 5 name their check. |
| 29 | Task 4's type-level test is not RED under vitest (process-and-privacy, minor) | Applied | Verified: vitest erases types, and `tsconfig.json` includes the tests and sets `noUnusedLocals`. Step 2 runs `npx tsc --noEmit -p .` and expects TS2305 for `ReconcileRunError`. The line uses its value, so it is not a TS6133 error once GREEN. |
| 30 | Decision 10 changes only the backend body: both frontend delete callers skip their re-read on any rejection, so a delete that landed leaves its row drawn beside a sentence saying it was deleted, and a second Delete answers 404 (confirmation pass, minor; re-graded by effect — a reader sees a deleted record still listed) | Applied | Verified: `LedgerView.tsx` `run()` (~:451) bumps the epoch only after `await write()` resolves, and `EventsPanel.tsx` `run()` (~:42) calls `reload()` only on success. Task 4 now names both files: on a `partial_delete` refusal each re-reads as its success path does and keeps the sentence; any other refusal is unchanged. One vitest per surface, plus an other-refusal pin in LedgerView. |

**Self-review after the amendments** (the writing-plans checklist):

- **Spec coverage.** Re-walked against §25, §28.10, §29, §30, §31 G and §32–§33. The table gains rows for §28.10 and the §25.4 ruling, and the §0 row now names the gate order.
- **Step scan.** Every new step names its command and its Expected line. Task 4 gains a bite step and Task 7 a counterexample step. A step that edits an existing test says which test, and why.
- **Type consistency.** One fix beyond the findings: Task 1 spelled the identity counts out, although the Type consistency note promised a key set built from `identity.CHECK_DECISIONS`. It is now built that way (`IDENTITY_COUNTS`). `_seed`'s and `_reads`' new signatures, `RELATION_PHRASES.by` and `Recording("timid", …)` are consistent across tasks.
- **Review Focus.** Items 1 and 4 are restated to match the verified behaviour, and each still names its pinning test.
- **Proportion.** The plan stays a plan: no function body was added, only names, signatures, assertions and commands.
