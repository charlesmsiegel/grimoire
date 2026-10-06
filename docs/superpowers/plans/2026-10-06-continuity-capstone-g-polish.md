# Continuity Capstone — Slice G: Observability, Performance, Docs and Acceptance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the continuity capstone. Hold its model calls and log rows to §29, audit the cost rules of §25 and fix what the audit finds, make every review surface speak §30's language, bring the onboarding documents in line with what the capstone added, prove each of §32's acceptance criteria and §33's stopping rule by a named test on the integrated tree, and run the two final review gates over the whole capstone.

**Architecture:** G adds no feature (§33). Most of it is tests that pin what Slices A–F already ship. The rest is the fixes those pins and the hand-offs force:

- `similarity` normalizes each record's identity text once per sweep, not once per pair (§25.2);
- actor names are read from a record's meta, never from its card versions and image directories (`characters.name_of`, and F's `pcs.name_of`, behind `relationships.actor_name` and the saved-idea read);
- a delete whose continuity cascade fails after the delete landed moves the write token (`routes/ledger.forget_or_partial`);
- the review surfaces speak in words: relation words in journal labels and refusals, and reason sentences and missing-record names in Reviewed links / merges;
- three small hand-off fixes: an unmerge refreshes the rail, `birthdays.crossed` skips an unreadable birthdate, and a failed reconcile run's error has a TS type.

There are three new test modules: `test_continuity_read_cost.py` (constant reads, and no model or embedding call on any read path), `test_continuity_wording.py` (§30) and `test_capstone_acceptance.py` (the spec's acceptance appendix cites tests that exist). Existing suites and `test_docs_guard.py` gain tests. The docs work touches CONTRIBUTING's guard table, store-guarantees' derived-sidecar bullet, one README bullet, `PHASE-STATUS.md`, and the spec (amendments, a new Appendix B, and the Status line).

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest + `TestClient`; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice G:

- §29, all of it, pinned;
- §25.1–§25.4, audited and pinned, including F's §19.7 hand-offs (F plan Decisions 4 and 5);
- §30, on every surface the capstone added;
- the docs list in §27 and §31 G: CLAUDE.md's detached-run inventory, CONTRIBUTING's guard table, templates/README.md, docs/store-guarantees.md, and §30's optional README mention;
- §0 steps 6 and 7 (the two final gates), over the whole capstone;
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
   - **F:** `pcs.name_of(root, pid)` and `overlay.pc_roster` (F Task 1). `graph.build(cid)`, `graph.NODE_KINDS` and `graph.EDGE_KINDS` (F Task 2). `GET /campaigns/{cid}/continuity/graph` (F Task 7). Several tests in `tests/test_continuity_graph.py`: `test_graph_tuples_are_pinned`, `test_scenes_follow_play_order_not_recency_or_date`, `test_focusable_is_exactly_the_chooser_driver_set`, `test_anchorable_is_exactly_the_chooser_anchor_set`, `test_reads_do_not_grow_with_the_node_count`, `test_graph_reads_no_card_or_image` and `test_graph_makes_no_model_call`. `tests/test_continuity_routes.py::test_graph_route_makes_no_model_call`. In `StoryGraphView.test.tsx`: "renders the drawing from one graph read" and "only one graph read across mount, lenses, toggles, arcs and nodes". `REQUIRED_PRESENT` in `test_continuity_writer_guard.py` includes `graph`.
5. Collect the hand-offs. Read the A–F slice ledgers (`.superpowers/sdd/2026-10-05-continuity-capstone-{a,b,c,d,e,f}-*/progress.md`, in whichever checkout or worktree ran that slice; record a ledger that cannot be found as absent). Copy every item that names Slice G, polish, docs, observability, performance or "deferred" into this ledger under `## Inherited hand-offs`. Use one line per item, with its source (file and line) and either an owning task or a recorded reason for deferring it. Seed the section with the items below; their owners are this plan's decisions.
   - **(a1)** A, final review h: an I/O failure mid-cascade answers 500 without a revision bump. **Task 4** (Decision 10).
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
   - **(e1)** E Tasks 3, 5 and 9: an undated campaign's near and move controls are no-ops rather than refusals. **Deferred**, per E's rulings: spec §26 and E Decision 12 read them as a skipped check.
   - **(f1)** F Decision 4: literal "once per request". **Task 3** (Decision 7).
   - **(f2)** F Decision 5: narrowing the location-name read. **Task 3** (Decision 8).
   - **(f3)** F: groups and facts as optional graph nodes. **Deferred.** §19.2 makes them optional, and §33 parks "more graph node families".
   - **(f4)** F: the inventory lines that might name the graph. **Task 6** updates CONTRIBUTING's guard row; nothing else needs the graph named (Decision 13).
   - **(f5)** F Open Questions. A scene stamped in a secondary calendar's notation reads as Undated: **ruled honest** by §3.8 (primary provider only). A very large campaign draws one wide canvas: **deferred**, because §19.7 forbids a cap and column virtualization is the stated fix if one is ever needed.

---

## Global Constraints

- **Privacy.** Fixtures, scratch scripts, docstrings, comments and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch. Phrases built from them are fine ("Mara's errand 3", "Winifred's debt", "Saltmarch market"), as are ids already used as placeholders in the tree (`mara-s-map`, `mara-s-oath`, `the-coronation`, `find-the-ledger`, `the-midnight-deadline`, `winifred-s-chart`). Never read `~/.grimoire` or any real store. No committed text describes store contents, counts or distributions. Performance is measured only on synthetic stores and pools built in tests or scratch scripts. Figures are recorded in the ledger, never in committed text. Committed docstrings argue structurally and say "to be tuned against real prompts later".
- **No model names or identifiers** in any committed text (code, comments, fixtures, docs, commit messages).
- **Scope (§33).** No new route, store file, candidate kind, node or edge kind, driver kind, link relation, prompt template or LLM call. Code changes are confined to the fixes named in Decisions 6, 9, 10, 11 and 12, and to any violation a pin in Tasks 1–3 finds. Each such violation is fixed in the module that owns it, with a RED test first.
- **Review gates.** Run `/codex:review` and `/codex:adversarial-review` if `codex` is on `PATH` (`which codex`). Otherwise each gate is stood in for by independent Claude reviewers: fresh subagents that wrote none of the capstone's code, which is the substitute the owner chose for every gate in this capstone. The ledger records which reviewers stood in, their briefs, and every finding with its resolution.
- **Spec values (verbatim):**
  - the route (§29): `Route("continuity", "Continuity checks", "The duplicate check beside absorb and the reconciliation sweep after End Scene or a refresh.", ("continuity-identity", "continuity-reconcile"), True)`;
  - log rows (§29): counts only, carrying the campaign id, the candidate count, deterministic vs semantic candidate counts, the embedding mode (`off` | `configured` | `failure`), resolver decision counts, `pairs_capped` and `superseded`; no title, beat, identity text, reason or prompt;
  - §30 prefers "Possible overlap", "May be finished", "Needs resolution review", "Basic matching active", "Semantic matching not configured", "Merged into", "Continuation of" and "Claims to address". It avoids "AI detected duplicate", "Continuity score", "Broken campaign" and "Embedding required";
  - §25.1: at most one identity-resolver call per absorb; at most one reconciliation call per run; never one call per pair.
- **Copy (verbatim, pinned by tests):**
  - `review.RELATION_WORDS`: `continues` → "continues", `subthread_of` → "is a subthread of", `pays_off` → "pays off", `before` → "is due before", `on` → "is due on", `after` → "is due after", `by` → "is due by", `related_to` → "is related to". A relation the table does not hold reads "is linked to".
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
  - Partial delete: HTTP 500 with `{"kind": "partial_delete", "landed": ["thread" | "commitment" | "event"], "detail": "the <noun> was deleted, but its merges and links could not all be removed (<ExceptionName>); the delete can be undone"}`.
- **Write token.** A write answered non-2xx after part of it landed calls `store.revision.bump(cid)` itself, because the activity middleware sees only success (CLAUDE.md, "A campaign carries a write token").
- **Imports and guards.** Imports stay at module scope, the graph stays acyclic, and modules inside `store/` bind submodules (`test_import_guard`). No new mutator is added, so `locks.DOMAIN_MODULES` is unchanged and `UNREVIEWED` does not grow. Any call handing a root to `characters.*` or `pcs.*` passes an overlay-resolved root (`test_overlay_guard`).
- **Lint ratchet.** `lint-baselines/*.json` never grows; an improvement is re-baselined (`make baseline`) in the same commit. Every new function stays at ruff complexity ≤ 10. Every new `except Exception` carries `# noqa: BLE001 -- <reason>`. Exception classes end in `Error`. Every backend task ends with `make check-lint check-mypy`, and every frontend task with `make check-eslint`, because `check-web` runs no eslint.
- **Docs rules.** No two of README, CONTRIBUTING, AGENTS, store-guarantees, the screenshots README and CLAUDE.md may share a run of more than 16 words (`test_no_document_restates_another`). A command added to README, CONTRIBUTING or an evals/frozen-campaign README spells both the `bin/` and the `Scripts/` interpreter forms (`test_install_scripts`).
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

1. **Internal tokens on review surfaces.** Linking two records from the review writes the journal label "Mara's map pays_off Mara's oath", which the History rail shows. A broken link or a dangling merge in Reviewed links / merges shows `missing_endpoint` and `thread:mara-s-map`.
   - Expected: "Mara's map pays off Mara's oath"; the reason as a sentence; a missing record as "a missing thread"; an unknown reason code as the generic sentence, never the code.
   - Pinned in Task 5: `test_link_journal_labels_use_relation_words`, `test_link_refusals_carry_no_store_token`, and LedgerContinuity's "a broken entry says why in words and names a missing record by its kind".
2. **A long campaign's read paths get slower with the ledger.** Today the Ledger's continuity read reads a full card per named actor, and a PC's card read lists that PC's image directories per version. The saved-idea read does the same for a PC's birthday. A per-record re-read of a whole file would also grow with the ledger.
   - Expected: each whole file is read a constant number of times, independent of record, scene and finding counts. No scene file is read more often as the campaign grows. No transcript, card version or image directory is read.
   - Pinned in Task 3: `test_read_paths_read_each_file_a_constant_number_of_times`, `test_the_continuity_chores_read_a_constant_number_of_files`, and `test_actor_name_reads_meta_only`.
3. **A burst of End Scenes during a live sweep.** Three saves land while the first pass waits on the model.
   - Expected: one follow-on pass carrying every adopter's refs, so two model calls in all. Refs that arrive after the last take wait for the next fresh run and are never lost.
   - Pinned in Task 2: `test_a_burst_of_adopters_coalesces_into_one_follow_on`.
4. **A delete whose continuity cascade fails after the record is gone.** Today it answers 500 with the write token unmoved, so a held `POST /advance` expectation still passes.
   - Expected: a 500 `partial_delete` naming what landed, and a moved token.
   - Pinned in Task 4: `test_a_delete_whose_cascade_fails_moves_the_token` (thread and commitment) and `test_an_event_delete_whose_cascade_fails_moves_the_token`.
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
   - **The fix.** A new `characters.name_of(root, cid)` mirrors F's `pcs.name_of`. It raises `CharacterNotFound` exactly where `read_character` does (no character, or no addressable version), and otherwise returns `meta.get("name", cid)` from one frontmatter read. `relationships.actor_name` uses both, and so does `suggest._actor_name`'s PC branch.
   - **The names are byte-identical**, so prompts and the frozen snapshot do not move. Every other caller (context, briefing, search, absorb labels, the Ledger) gets cheaper.
   - **Rejected:** rosters (F's choice for the graph). They read every actor's meta to name the few a finding mentions.
10. **A delete whose cascade fails after the delete landed answers 500 `partial_delete` and moves the token** (A hand-off a1; §5.7).
    - **Today.** `delete_thread`, `delete_commitment` and `delete_campaign_event` call `continuity_review.forget_ref` after the record is gone. An `OSError`, or a `ContinuityError` from a file garbled under the hold, then answers 500 with nothing having moved the token, although the delete landed and is journalled.
    - **The fix.** `routes/ledger.forget_or_partial(cid, ref, *, name, noun)` wraps the call. On either error it calls `store.revision.bump(cid)` and raises the partial-delete 500. The event route imports it, as `routes/continuity.py` already imports `routes/ledger.py`.
11. **Three small hand-off fixes.** Each is a one-line class of change with its own test.
    - `api.removeAlias` and `api.removeLink` gain `.then(notifyShell)`, as apply, dismiss and restore have (d1): an unmerge changes the rail's Ledger count.
    - `birthdays.crossed` skips a birthdate that raises any of `birthdays._UNREADABLE`, not only `CalendarError` (b1). That tuple exists for exactly this; its comment says a provider's arithmetic raises `OverflowError` on a year past its range.
    - `api/types.ts` gains `ReconcileRunError = { kind: string; detail: string; status: number; sweep: "full" | "incremental"; saved: boolean; follow_on: boolean }`, and `failedNote` reads `ApiError.body` as `Partial<ReconcileRunError>` (d7).
12. **§30: no store token reaches a reader.**
    - **Journal labels.** `review.create_link`, `remove_link` (through `_link_label`) and `forget_ref` label a link "<a> <RELATION_WORDS[relation]> <b>", with " — removed" and " — removed with deleted record" suffixes as today. `RELATION_WORDS` lives in `review`, the only module that writes these labels.
    - **Refusals.** The two link refusals use the constant copy (Global Constraints).
    - **Raw links.** GET /continuity's `raw_links` gain `a_title` and `b_title` through `review.describe`, as effective links already do (d3).
    - **Reviewed links / merges** renders every reason through `brokenReason(code)`. A title that is empty or equals its ref is shown as `missingName(ref)`, because `describe` answers a missing record with its ref.
    - **An avoid-list guard** scans the continuity surfaces' source for §30's avoided phrases.
    - **Unchanged by decision:** D's choice that an unknown relation in `RELATION_PHRASES` reads as itself (a stored word a reader chose). It is reachable only by a hand edit.
13. **Docs change only what is false, and new claims are held to the code.**
    - **CLAUDE.md.** Each inventory the capstone could have moved is re-derived. The "Twenty-five handlers" count needs no starter added since D. The `background` class must name `put_chronicle` and `post_reconcile`. The write-token paragraph must name both sweep persists. "Hand edits to the ledger go through `routes/ledger.py`" stays true, because apply uses `ledger.move_thread`/`move_commitment`. "The ledger's seven sections" means `LedgerView`'s seven record tables; Continuity review is a column section with no records of its own. Edit only what turns out false. A new docs-guard test pins the reconcile starters, the way the tracker test pins its starters.
    - **CONTRIBUTING.** The writer-guard row names the Story Graph, which the guard now requires. The `test_docs_guard.py` row names the CLAUDE.md and templates/README claims it now checks.
    - **store-guarantees.** One sentence in "The derived sidecars do not expire" names the candidate cache as the one derived file that does notice change.
    - **templates/README.md** is checked. A docs-guard test holds its `` `store/<path>.py:<fn>` `` builder citations to real functions, and requires a `### `continuity_*/`` heading for each continuity family.
    - **README** gains §30's optional bullet.
    - **Not done:** a CLAUDE.md paragraph on the stopping rule. The owner limited CLAUDE.md to what its own claims require (Open questions). The parked status goes in `PHASE-STATUS.md` and the spec.
14. **The acceptance evidence is a spec appendix held to the tests.** Appendix B lists, per AC1–AC19 and §33, the tests and eval cases that prove it on the integrated tree. `backend/tests/test_capstone_acceptance.py` fails when a cited backend test, frontend test title or eval case does not exist, or when a criterion has no row. Without that test, a rename would leave an acceptance criterion silently proven by nothing.
15. **The whole-capstone review is scoped by ranges and regions, never by the bare `BASE..HEAD` diff.**
    - **The path set** is the union of files the capstone ranges touched (looped). Reviewers read `git diff BASE..HEAD -- <path set>` with the per-range logs, and are told that unrelated features touched some of the same files.
    - **The code-review stand-in** is four reviewers, one per code area. **The adversarial stand-in** is five, one per spec region; each asks whether the capstone implements that region end to end.
16. **Deferred hand-offs are recorded in the spec, not only in the ledger.** The ledger is gitignored, and B's final review flagged deviations "recorded only in the gitignored ledger". Every item Before Task 1 step 5 defers is listed, with its reason, in a "Slice G rulings" bullet list under §31 Slice G.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/src/grimoire/store/continuity/similarity.py` | `Subject.features()`; `lexical` reads it |
| `backend/src/grimoire/store/continuity/reconcile.py` | `RECONCILE_MAX_PAIRS` docstring states the per-pair cost structurally |
| `backend/src/grimoire/store/characters.py` | `name_of` |
| `backend/src/grimoire/store/relationships.py` | `actor_name` through `characters.name_of` / `pcs.name_of` |
| `backend/src/grimoire/store/suggest.py` | `_actor_name`'s PC branch through `pcs.name_of` |
| `backend/src/grimoire/store/birthdays.py` | `crossed` skips an unreadable birthdate |
| `backend/src/grimoire/store/continuity/review.py` | `RELATION_WORDS`; link labels and refusals in words |
| `backend/src/grimoire/routes/ledger.py` | `forget_or_partial`; both deletes use it |
| `backend/src/grimoire/routes/campaigns.py` | `delete_campaign_event` uses it |
| `backend/src/grimoire/routes/continuity.py` | raw links gain `a_title` / `b_title` |
| `frontend/src/api/client.ts`, `frontend/src/api/types.ts` | unmerge and remove-link notify the shell; `ReconcileRunError`; raw-link titles |
| `frontend/src/components/continuity/labels.ts`, `ReviewedGroup.tsx`, `useContinuityReview.ts` | `BROKEN_REASONS`, `brokenReason`, `missingName`; the group renders through them; the typed failed-run body |
| `backend/tests/test_continuity_read_cost.py` (new) | constant reads; no model or embedding call on any read path; actor names without cards |
| `backend/tests/test_continuity_wording.py` (new) | §30 avoid-list guard; relation words cover every relation |
| `backend/tests/test_capstone_acceptance.py` (new) | Appendix B cites tests and cases that exist |
| `backend/tests/test_routing.py`, `test_absorb_identity.py`, `test_continuity_reconcile_routes.py`, `test_continuity_reconcile.py`, `test_continuity_similarity.py`, `test_characters_store.py`, `test_relationships_store.py`, `test_shell_route.py`, `test_ledger_routes.py`, `test_events_routes.py`, `test_birthday_occurrences.py`, `test_continuity_review.py`, `test_continuity_routes.py`, `test_docs_guard.py` | appended pins (one label assertion in `test_continuity_review.py` is updated, Task 5) |
| `frontend/src/api/client.test.ts`, `frontend/src/routes/LedgerContinuity.test.tsx` | the shell notice; broken entries in words |
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
- Produces: test-module constants `IDENTITY_ROW_KEYS`, `RECONCILE_ROW_KEYS` and `MODES` (a field → allowed-values map), and a helper `_row_texts(fake) -> list[str]`. The helper returns every stripped line of 12 or more characters from every message the fake received. They are used only in these two test files.

- [ ] **Step 1: Write the tests.**
  - `test_routing.py::test_the_continuity_route_is_spelled_as_section_29_spells_it`:
    - the `continuity` route equals the Global Constraints `Route(...)`, field by field;
    - `TASK_ROUTE["continuity-identity"] == TASK_ROUTE["continuity-reconcile"] == "continuity"`;
    - no other key of `TASK_ROUTE` starts with `"continuity"`.
  - `test_absorb_identity.py::test_identity_log_row_is_counts_and_closed_modes_only`, parametrized over four cases:
    - `ok`: a `new` decision with the reason "a second search";
    - `no connection`: the `continuity` route points at a deleted connection id, as `test_misrouted_identity_reports_itself_and_leaves_absorb_standing` does;
    - `embedding failure`: `_configure_embeddings` with `FakeEmbeddings(error=EmbeddingsError("network", "connection refused"))`;
    - `examination raised`: `continuity_identity.examine` patched to raise `RuntimeError("Mara's oath")`. No examination exists, so `_identity_outcome` logs with no counts, and the phase's `reason` holds the exception text.

    For each case, take the one `"continuity identity check"` row:
    - `set(row) - {"ts", "level", "module", "message"} == IDENTITY_ROW_KEYS`, where `IDENTITY_ROW_KEYS` is `{"kind", "campaign", "scene", "status", "matching", "embedding", "embedding_error"} | {"proposed", "examined", "candidates", "deterministic", "semantic", "embedded", "existing", "new", "uncertain", "unchecked", "downgraded", "hint_only"}`. In the `examination raised` case it is `IDENTITY_ROW_KEYS` minus those twelve counts;
    - `row["kind"] == "continuity-identity"`, `row["campaign"] == cid` and `row["scene"] == sid`;
    - every count is a non-bool `int`, and `deterministic + semantic == candidates`;
    - `status` is in `{"ok", "degraded", "failed", "skipped"}`, `matching` in `{"basic", "semantic"}`, `embedding` in `{"off", "configured", "failure"}` (`""` in the `examination raised` case only), and `embedding_error` in `{""} | llm_errors.KINDS | {"unexpected"}`;
    - in the `embedding failure` case `embedding_error == "network"`, and "connection refused" is not in `json.dumps(row)`; in the `examination raised` case `status == "failed"`, and "Mara's oath" is not in `json.dumps(row)`;
    - no seeded or proposed title, beat or reason, and no line from `_row_texts(fake)`, is a substring of `json.dumps(row)`.
  - `test_continuity_reconcile_routes.py::test_reconcile_log_row_is_counts_and_closed_modes_only`, parametrized over three cases:
    - `ok`: `_key` plus `_reply(_duplicate())`, whose reason is `REASON`;
    - `off`: no key;
    - `failed`: an undecodable reply such as `"no json here"`.

    For each case, take the newest `_reconcile_row(cid)`:
    - `set(row) - {"ts", "level", "module", "message"} == RECONCILE_ROW_KEYS`, where `RECONCILE_ROW_KEYS` is `{"kind", "campaign", "sweep", "matching", "embedding", "embedding_error", "llm", "continuity", "candidates", "deterministic", "semantic", "adjudicated", "pairs_capped", "superseded"} | set(continuity_routes._WORDS)`;
    - every count is a non-bool `int`, and `pairs_capped` and `superseded` are `bool`;
    - `sweep` is in `reconcile.SWEEPS`, `llm` in `{"off", "skipped", "ok", "failed"}`, `continuity` in `{"ok", "malformed"}`, and the three shared modes are as above;
    - no title or beat of `LEDGER_THREAD` or `RECOVER_THE_LEDGER`, not `REASON`, and no line from `_row_texts(fake)` appears in `json.dumps(row)`.
  - `test_docs_guard.py::test_llm_capture_doc_names_every_continuity_task`: for each task of the `continuity` route, `` f"`{task}`" `` occurs in `docs/incoming-llm-capture.md`.
- [ ] **Step 2: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routing.py tests/test_absorb_identity.py tests/test_continuity_reconcile_routes.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. These pin what C and D shipped. A failure is a §29 defect: fix the producer, re-run, and record it in the ledger as a finding.
- [ ] **Step 3: Show that the pins bite.** Make three mutations to the source, one at a time, run the two row tests after each, and then restore the source:
  - add `title="x"` to `_log_pass`'s `store.logs.record(...)`;
  - pass `embedding_error=str(exc)` in place of the kind;
  - drop `"hint_only"` from `counts()`.

  Expected: each mutation fails the test for its row. Record the three in the ledger. `git diff` is empty afterwards.
- [ ] **Step 4: Run the guards that see these modules.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routing_guard.py tests/test_usage_guard.py tests/test_routing_routes.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
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
  - `test_continuity_reconcile_routes.py::test_a_burst_of_adopters_coalesces_into_one_follow_on` (Review Focus 3, Decision 4).
    - Setup: `_campaign`, `_threads`, and a third thread "Mara's map" (`mara-s-map`), plus `_key`. Hold the first pass: `held = _install(client, _held())`, then `first = _refresh(client, cid)` and `held.await_held()`. While it is held, call `continuity_routes.schedule_reconcile(client.app, cid, held, edits=[e], progress=_applied(e))` three times, one `_plot_edit` per thread. Then release.
    - Assertions: the run lands with `result["follow_on"] is True`; `len(_reconcile_requests(held)) == 2`; the second request's user prompt contains `TOUCHED` and all three thread titles; and `take_touched` afterwards is empty.
  - `test_continuity_reconcile_routes.py::test_a_capped_sweep_says_so_in_its_run_and_log_row`. Set `monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 1)`, seed three threads, and Refresh with no key. Both `run["result"]["pairs_capped"]` and `_reconcile_row(cid)["pairs_capped"]` are `True`.
  - `test_continuity_reconcile_routes.py::test_sweep_work_runs_off_the_event_loop` (§25.2).
    - Wrap `reconcile.discover`, `persist_found`, `select`, `build_payload` and `persist_proposals` in recorders. Each appends whether `asyncio.get_running_loop()` raises `RuntimeError` in its thread, then calls through.
    - Refresh with a key and a duplicate reply. Every recorder fired at least once, and every value recorded is `True` (no loop in that thread).
  - `test_absorb_identity.py::test_the_identity_examination_runs_off_the_event_loop`: the same recorder over `continuity_identity.examine` during an absorb that examines a row.
- [ ] **Step 3: Run them and confirm the RED.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_routes.py tests/test_absorb_identity.py -q -p no:cacheprovider`. Expected:
  - `test_lexical_normalizes_each_text_once` FAILS (30 calls, not 6);
  - the rest PASS, because they pin D's design. A FAIL among them is a §25 violation: fix it in the owning module (for the text count, pool each kind once in `discover`) and record it;
  - for the burst test, show the bite by mutating the follow-on into one pass per pended ref (4 calls), then restore.
- [ ] **Step 4: Implement** `Subject.features()` and switch `lexical` to it. Then reword `RECONCILE_MAX_PAIRS`'s docstring: a pair costs two comparisons of sets built once per record, plus at most one dot product, and normalization is per record. Keep "to be tuned against real prompts later" and add no figure.
- [ ] **Step 5: Run them, and every suite over similarity.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_continuity_identity.py tests/test_absorb_identity.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_prompt.py tests/test_continuity_reconcile_routes.py tests/test_evals.py tests/test_eval_graders.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS, with no existing assertion edited.
- [ ] **Step 6: Measure after.** Run Step 1's script again and record the figures in the ledger. Expected: the per-pair lexical cost drops to the set comparisons.
- [ ] **Step 7: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
- [ ] **Step 8: Commit:** `perf(continuity): normalize each identity text once per sweep; pin the per-run call bound`

---

### Task 3: Read cost — constant reads, actor names from meta, and no model call on any read path (§25.3, §25.4, §3.10)

**Files:**
- Create: `tests/test_continuity_read_cost.py`
- Modify: `store/characters.py`, `store/relationships.py`, `store/suggest.py`
- Test: `tests/test_characters_store.py`, `tests/test_relationships_store.py`, `tests/test_shell_route.py` (each appended)

**Interfaces:**
- Consumes: F's `pcs.name_of(root, pid)` and `graph.build`; D's `candidates.write` and `pending.fingerprint`.
- Produces:
  - `characters.name_of(root: Path, cid: str) -> str`. It calls `_require_char`, raises `CharacterNotFound` when `_version_ids(root, cid)` is empty, and returns `parse_frontmatter(_meta_path(root, cid).read_text(...))[0].get("name", cid)`. It reads no card, image or sidecar.
  - `relationships.actor_name(cid, token)`. Same signature and same names, read through `pcs.name_of(overlay.pc_root(cid, aid), aid)` and `characters.name_of(overlay.char_root(cid, aid), aid)`. The `except` clause is unchanged.
  - `suggest._actor_name`'s `pcs` branch becomes `pcs.name_of(overlay.pc_root(ctx.cid, aid), aid)`, with the same `except` tuple.
  - In `test_continuity_read_cost.py`:
    - `_seed(client, n) -> str` builds the campaign, under the `client` fixture's store. The world "Realm" holds the character Mara (birthdate `--05-12`) and the PC Seraphine (birthdate `--05-14`). The campaign "Saltmarch" has its clock at 2026-05-10, set the way `tests.test_continuity_pressure._campaign` sets it. It then gets `n` scenes "Saltmarch scene i", each seating Seraphine and Mara; `n` threads "Mara's errand i" and `n` commitments "Winifred's debt i", each moved in its own scene; `n` events "Saltmarch market i" in the next 30 days; `n` saved ideas, each serving one thread, plus one anchored to Seraphine's birthday occurrence; one `pays_off` link; and `n` live `possible_duplicate` findings over consecutive threads, written with `candidates.write` and fingerprinted with `pending.fingerprint`. It returns the campaign id.
    - `_reads(monkeypatch) -> dict[str, Counter]` counts calls to three groups of readers:
      - whole-file readers: `store.plot.read`, `store.commitments.read`, `store.events.read`, `store.continuity.doc.read`, `store.continuity.candidates.read`, `store.chronicle.read_chronicle`, `store.relationships.read`, `store.scene_ideas.read` and `store.appearances.cast.roster`;
      - per-scene readers, counted by sid: `scenes.read.get_time_history`, `get_location_history` and `read_scene_meta`;
      - must-be-zero readers: `scenes.read.read_scene`, `read_scene_window`, `characters.read_card`, `characters.read_character`, `pcs.read_pc` and `assets.list_images`.

- [ ] **Step 1: Write the tests.**
  - `test_characters_store.py::test_name_of_matches_read_character_without_cards_or_images`:
    - `name_of` equals `read_character(...)["meta"]["name"]` for a world character and a campaign copy;
    - a missing id raises `CharacterNotFound`, and so does a character whose every version file was removed;
    - recorders on `characters.read_card` and `assets.list_images` count 0.
  - `test_relationships_store.py::test_actor_name_reads_meta_only`:
    - names are unchanged for a world character, a campaign-copied character, a world PC, and a missing id (which answers the id);
    - `read_card`, `read_character`, `pcs.read_pc` and `assets.list_images` count 0.
  - `test_continuity_read_cost.py::test_read_paths_read_each_file_a_constant_number_of_times` (Review Focus 2, Decision 7). It is parametrized over six routes: `GET /api/campaigns/{cid}/ledger`, `.../continuity`, `.../continuity/candidates`, `.../continuity/drivers`, `.../continuity/graph` and `.../scene-ideas?greetings=false`. For each route, seed at `n=1` and at `n=12` and read once under `_reads`. Then:
    - each whole-file count at 12 equals its count at 1;
    - the largest per-scene count at 12 equals the largest at 1;
    - the must-be-zero readers count 0;
    - every reader the route is known to use counts at least 1 at `n=1`. This positive control keeps a mis-wired spy from passing; for example, the graph reads `plot.read`.
  - `test_continuity_read_cost.py::test_the_continuity_chores_read_a_constant_number_of_files` (§18.2, §25.4). Call `todo._chore_continuity_overlaps(todo._Ctx(cid))`, `_chore_continuity_closures`, and both `_items_continuity_*` directly, at both sizes. The whole-file counts are equal across sizes. `read_scene`, `read_scene_window` and `scenes.read.list_scenes` count 0.
  - `test_continuity_read_cost.py::test_continuity_review_reads_name_actors_without_cards`: the candidates read for findings whose records involve Seraphine and Mara has both names in `names`, and `pcs.read_pc`, `read_character` and `assets.list_images` count 0.
  - `test_continuity_read_cost.py::test_the_saved_idea_read_names_a_pc_birthday_without_images`: the idea anchored to Seraphine's birthday reads with her name in its anchor label, and `assets.list_images` counts 0.
  - `test_continuity_read_cost.py::test_the_graph_reads_location_names_once` (Decision 8): `overlay.list_entities` is called with `(cid, "locations")` exactly once per `graph.build`, at both sizes.
  - `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding` (§3.10, AC13, AC16).
    - Parametrized over the six routes above plus four more: `GET /api/todo?campaign={cid}`, `GET /api/todo`, `GET /api/todo/continuity-overlaps/items?campaign={cid}` and `GET /api/shell?campaign={cid}`.
    - Make embeddings configured: `store.embed_space.resolve` returns a space dict. Replace `similarity._CLIENT` with a recording `FakeEmbeddings`. Patch `grimoire.embeddings.EmbeddingsClient.embed` at class level to a recorder. Override `routes.get_llm` with `llm_fakes.from_entries([])`.
    - Assertions: 200; and the fake's `calls`, the class recorder and the LLM fake's `requests` are all empty.
    - **Recorders, not raisers.** A raiser inside `_soft` or `_attempt` is swallowed and passes (the trap F's plan gate found).
  - `test_shell_route.py::test_the_shell_read_does_no_continuity_review_work` (§25.4).
    - Seed a cache, and set recorders on `continuity.pending.findings`, `continuity.candidates.read`, `continuity.reconcile.discover`, `continuity.pressure.build` and `continuity.drivers.snapshot`.
    - Assertions: `GET /api/shell?campaign={cid}` records 0 calls, and no key of the `campaign` block contains "continuity" or "candidate".
- [ ] **Step 2: Run them and confirm the RED.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_characters_store.py tests/test_relationships_store.py tests/test_continuity_read_cost.py tests/test_shell_route.py -q -p no:cacheprovider`. Expected:
  - `name_of` FAILS (`AttributeError`);
  - `test_actor_name_reads_meta_only`, the two naming tests, and the zero-reader asserts of the candidates and scene-ideas routes FAIL (card and image reads);
  - the rest PASS.
  - A whole-file count that grows with `n` is a violation. If the read is a capstone module's, fix it there by hoisting the read (record it). If it is pre-capstone code on a path the capstone does not own, record it in the ledger as pre-existing and narrow that reader's assertion for that route only, with a comment saying why.
- [ ] **Step 3: Implement** `characters.name_of`, then switch `relationships.actor_name` and `suggest._actor_name`'s PC branch.
- [ ] **Step 4: Run them, and every suite that renders an actor name.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_characters_store.py tests/test_relationships_store.py tests/test_continuity_read_cost.py tests/test_shell_route.py tests/test_frozen_campaign.py tests/test_actor_context.py tests/test_context.py tests/test_briefing_route.py tests/test_ledger_route.py tests/test_absorb_store.py tests/test_continuity_review_routes.py tests/test_scene_ideas_provenance.py tests/test_overlay_guard.py tests/test_import_guard.py tests/test_paths_guard.py -q -p no:cacheprovider`. Expected: PASS, with no existing test edited and `snapshot.json` untouched, because the names are byte-identical.
- [ ] **Step 5: Lint and types.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
- [ ] **Step 6: Commit:** `perf(continuity): read paths name actors from meta and read each file a constant number of times`

---

### Task 4: Hand-off fixes — partial deletes, the rail after an unmerge, `crossed`, the failed-run type

**Files:**
- Modify: `routes/ledger.py`, `routes/campaigns.py`, `store/birthdays.py`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/components/continuity/useContinuityReview.ts`
- Test: `tests/test_ledger_routes.py`, `tests/test_events_routes.py`, `tests/test_birthday_occurrences.py`, `frontend/src/api/client.test.ts` (each appended)

**Interfaces:**
- Produces:
  - `routes/ledger.forget_or_partial(cid: str, ref: str, *, name: str, noun: str) -> None`. It calls `continuity_review.forget_ref(cid, ref, name=name)`. On `OSError` or `continuity_doc.ContinuityError` it calls `store.revision.bump(cid)` and raises `HTTPException(500, detail={...})` with the Global Constraints copy, chained from the error. `delete_thread` (`noun="thread"`), `delete_commitment` (`"commitment"`) and `delete_campaign_event` (`"event"`, via `from . import ledger as ledger_routes`) call it in place of `forget_ref`, inside the same hold.
  - `birthdays.crossed`'s per-row guard becomes `except _UNREADABLE:`. Its comment now says that a provider's arithmetic raises `OverflowError` on a year past its range (the reason `_UNREADABLE` gives), while a broken `describe` still fails as before.
  - The TS `ReconcileRunError` (Decision 11). `failedNote` reads `err.body as Partial<ReconcileRunError> | undefined`, with unchanged behaviour.
  - `api.removeAlias` and `api.removeLink` end in `.then(notifyShell)`, each with a one-line comment: an unmerge changes the rail's Ledger count.

- [ ] **Step 1: Write the tests.**
  - `test_ledger_routes.py::test_a_delete_whose_cascade_fails_moves_the_token`, parametrized over thread and commitment.
    - Setup: seed the record and a link naming it; take `before = store.revision.current(cid)`; patch `continuity_review.forget_ref`, as the route module sees it, to raise `OSError("disk full")`.
    - Assertions: DELETE answers 500 with `kind == "partial_delete"`, `landed == [<noun>]`, and "deleted" in `detail`; the record is gone; `store.revision.current(cid) != before`.
  - `test_events_routes.py::test_an_event_delete_whose_cascade_fails_moves_the_token`: the same, for `DELETE /campaigns/{cid}/events/{eid}`.
  - `test_ledger_routes.py::test_a_delete_whose_cascade_lands_answers_as_before`: the regression guard. DELETE still answers `{"ok": True}`, and the link is gone.
  - `test_birthday_occurrences.py::test_crossed_skips_an_overflowing_birthdate`. The rows are one actor with `"99999999999999999999-05-09"` and Mara with `"--05-11"`, over a span containing 05-11. `crossed` returns Mara's row and does not raise.
  - `client.test.ts`:
    - "removeAlias and removeLink tell the shell": `onShellChanged(spy)`; both calls on a 200 call the spy once each, and a rejected call does not call it;
    - a type-level line: `const e: Partial<ReconcileRunError> = { saved: true, follow_on: false }` typechecks.
- [ ] **Step 2: Run them and confirm the RED.** From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_routes.py tests/test_events_routes.py tests/test_birthday_occurrences.py -q -p no:cacheprovider`. From `frontend/`: `npx vitest run src/api/client.test.ts`. Expected:
  - the two partial-delete tests FAIL (500 without `partial_delete`, and the token unchanged);
  - `crossed` FAILS with `OverflowError`;
  - the shell test FAILS (the spy is not called).
- [ ] **Step 3: Implement** the helper and its three call sites, the `crossed` guard, the client lines, and the type.
- [ ] **Step 4: Run them, the unchanged suites and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_routes.py tests/test_events_routes.py tests/test_birthday_occurrences.py tests/test_clock_store.py tests/test_continuity_routes.py tests/test_continuity_review.py tests/test_revision.py tests/test_import_guard.py tests/test_route_order.py -q -p no:cacheprovider`, then `npx vitest run src/api/client.test.ts src/routes/LedgerContinuity.test.tsx` and `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5: Lint, types and eslint.** `make check-lint check-mypy check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
- [ ] **Step 6: Commit:** `fix(continuity): a partial delete moves the token; unmerge refreshes the rail; crossed skips an unreadable birthdate`

---

### Task 5: §30 — no store token reaches a reader

**Files:**
- Modify: `store/continuity/review.py`, `routes/continuity.py`, `frontend/src/api/types.ts`, `frontend/src/components/continuity/labels.ts`, `frontend/src/components/continuity/ReviewedGroup.tsx`
- Create: `tests/test_continuity_wording.py`
- Test: `tests/test_continuity_review.py`, `tests/test_continuity_routes.py`, `frontend/src/routes/LedgerContinuity.test.tsx`

**Interfaces:**
- Produces:
  - `review.RELATION_WORDS: dict[str, str]` (Global Constraints) and `review._relation_words(relation) -> str`, which answers with the table entry or "is linked to". The three link labels use it, and the two refusals use the constant copy.
  - `get_continuity`'s `raw_links` rows gain `"a_title": review.describe(cid, a, ledgers)` and `"b_title"`, where `a` and `b` are the row's `_text` values (an empty one titles as `""`). In TS, `ContinuityRawLink` gains `a_title: string; b_title: string`.
  - In `labels.ts`: `BROKEN_REASONS: Record<string, string>`; `brokenReason(code: string): string`, an own-property lookup that falls back to the generic sentence; `missingName(ref: string): string`; and `recordName(title: string, ref: string): string`, which returns `title` unless it is empty or equals `ref`, in which case it returns `missingName(ref)`. `ReviewedGroup` names every alias side and link end through `recordName`, and every reason through `brokenReason`.

- [ ] **Step 1: Write the tests.**
  - `test_continuity_review.py::test_link_journal_labels_use_relation_words`, parametrized over `pays_off`, `subthread_of` and `related_to`. Create a link, remove it, then create one and delete its record through the DELETE route. The three journal labels read "<a> <words> <b>", "<a> <words> <b> — removed" and "<a> <words> <b> — removed with deleted record", and none contains `_`. The existing `test_create_link_journals_and_round_trips` assertion becomes `"Mara's map pays off Mara's oath"`. That is the one edit to an existing test, and it is made because the label is the behaviour being changed.
  - `test_continuity_routes.py::test_link_refusals_carry_no_store_token`:
    - `POST /continuity/links` with `pays_off` from a thread to an event answers 400, with `detail` equal to the constant copy;
    - an apply whose `relation` is in the wrong direction answers 400 with its constant copy;
    - neither detail contains `_` or `'`.
  - `test_continuity_routes.py::test_raw_links_carry_their_ends_titles`. A self-collapsing raw link (Winifred's chart merged into Mara's map, then a link between them written by hand) has `a_title` and `b_title` equal to the two titles. A hand-written link to `event:gone` has that end's title equal to its ref (`describe`'s answer for a missing record).
  - `tests/test_continuity_wording.py`:
    - `test_relation_words_cover_every_relation`: `set(review.RELATION_WORDS) == set(effective.RELATIONS)`, and no value contains `_`;
    - `test_no_continuity_surface_uses_a_phrase_section_30_avoids`. Read every non-test source file under `frontend/src/components/continuity/`, `frontend/src/components/storyGraph/` and `frontend/src/components/review/`, plus `pressureControls.ts`, `NewSceneChooser.tsx`, `SceneIdeaPicker.tsx`, `routes/LedgerView.tsx`, `routes/StoryGraphView.tsx`, `routes/ConfigView.tsx`, `backend/src/grimoire/routes/todo.py`, `routes/continuity.py` and `store/continuity/*.py`. Lower-cased, none contains "ai detected", "detected duplicate", "duplicate detected", "continuity score", "health score", "broken campaign", "embedding required", "embeddings required" or "continuity disabled". At least one file is read for each listed directory: a renamed directory would otherwise scan nothing.
  - `LedgerContinuity.test.tsx`:
    - "a broken entry says why in words and names a missing record by its kind". The fixture's invented reason `"endpoint missing"` becomes the server's real `"missing_endpoint"`, and gains `a_title` (a real title) and `b_title` (equal to its ref). The row shows "One of its records no longer exists.", the real title, and "a missing commitment". It shows neither `missing_endpoint` nor the ref. The existing "reviewed links offer Unmerge…" test finds the broken row by the new sentence.
    - "a dangling merge says why in words": an alias with `reason: "missing_target"` shows "The record it was merged into no longer exists.".
    - "an unknown reason code is never shown": `reason: "frobbed"` shows "This entry can no longer be followed." and not "frobbed".
- [ ] **Step 2: Run them and confirm the RED.** From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py tests/test_continuity_routes.py tests/test_continuity_wording.py -q -p no:cacheprovider`. From `frontend/`: `npx vitest run src/routes/LedgerContinuity.test.tsx`. Expected:
  - the label, refusal, raw-title and relation-words tests FAIL;
  - the avoid-list guard PASSES (a regression pin). Show that it bites by adding "Continuity score" to a `labels.ts` comment, then restore it;
  - the three LedgerContinuity cases FAIL.
- [ ] **Step 3: Implement** the backend words, refusals and raw-link titles, then the TS type, `labels.ts` and `ReviewedGroup`.
- [ ] **Step 4: Run them, and the review suites.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py tests/test_continuity_routes.py tests/test_continuity_wording.py tests/test_continuity_review_routes.py tests/test_continuity_undo.py tests/test_ledger_routes.py -q -p no:cacheprovider`, then `npx vitest run src/routes/LedgerContinuity.test.tsx src/components/ChangesPanel.test.tsx` and `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5: Lint, types and eslint.** `make check-lint check-mypy check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
- [ ] **Step 6: Commit:** `fix(continuity): review surfaces speak in words — relation labels, reasons and missing records`

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
  - store-guarantees' "The derived sidecars do not expire" bullet gains, at its end: "The continuity candidate cache is the one derived file that does notice: each finding keeps the fingerprint of the records it was found against, and one whose records have since changed is shown as stale until the next sweep (`store/continuity/pending.py`)."
  - README's "Other highlights" gains, after **Model routing**: "- **Continuity review and the Story Graph** — after each wrap-up Grimoire looks over the campaign's plot threads and commitments for ones that may be the same business, belong together, or be finished, and lists what it finds in the Ledger to merge, link, close or set aside; nothing changes until you choose. It works with no embeddings connection and catches more with one. The Story Graph draws the scenes played so far beside what is still owed and what is coming up."
- [ ] **Step 4: Run the docs and install-script guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_guard.py tests/test_install_scripts.py -q -p no:cacheprovider`. Expected: PASS, including `test_no_document_restates_another` for every pair and the three new tests.
- [ ] **Step 5: Lint.** `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS at baseline.
- [ ] **Step 6: Commit:** `docs: the capstone's guard row, derived cache, README mention; hold CLAUDE.md and templates/README claims to the code`

---

### Task 7: Spec amendments, deferred hand-offs, and the acceptance walk (§32, §33)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`
- Create: `tests/test_capstone_acceptance.py`
- Test: whatever gap Step 2 finds, in the suite that owns it

**Interfaces:**
- Produces:
  - the spec's `# Appendix B. Acceptance evidence`. It is one Markdown table, `| Criterion | Claim | Evidence |`, with a row each for `AC1`…`AC19` and `§33`. Evidence items are written as follows:
    - a backend test is `` `test_<file>.py::test_<name>` ``;
    - a frontend test is `` `frontend/src/<path>.test.ts(x)` "<exact title>" ``;
    - an eval case is `` eval `<case>` ``;
    - AC19's evidence names `make check`, `evals/run.py` and §31 Slice G's gate bullet.
  - `test_capstone_acceptance.py`. Its helper `_appendix() -> str` returns the text from the Appendix B heading to the next `# Appendix` heading or the end. Four tests:
    - `test_every_criterion_has_a_row`: `AC1`–`AC19` and `§33` each head a row;
    - `test_cited_backend_tests_exist`: each file exists under `backend/tests/` and defines the function (`def <name>(` at any indent);
    - `test_cited_frontend_tests_exist`: each file exists and contains `"<title>"` verbatim;
    - `test_cited_eval_cases_exist`: each case name is a key of `evals.cases.BY_ID`.

- [ ] **Step 1: Write `test_capstone_acceptance.py` and run it.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py -q -p no:cacheprovider`. Expected: FAIL, because there is no Appendix B yet.
- [ ] **Step 2: Walk the criteria.** For each row below, confirm on the integrated tree that every named test exists and passes, and that it proves the claim rather than something near it. Where a row has no test that proves its claim, write one in the owning suite (RED, then GREEN, or a characterization with its bite shown) or record a ruling. Then write Appendix B from the confirmed rows.
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
- [ ] **Step 3: Amend the spec.** Each edit cites this plan's decision number.
  - §29 names both rows' exact keys and closed vocabularies, and says where the capture note lives: `templates/README.md` and `docs/incoming-llm-capture.md` (Decisions 1–3).
  - §9.4: a zero-vector text is never cached, and is re-sent once per sweep within `RECONCILE_WARM_LIMIT`; accepted (Decision 5).
  - §11.1 and §25.1: a run is its own pass plus at most one follow-on pass for adopted triggers, with at most one model call per pass. Refs pended after the last take wait for the next fresh run (Decision 4).
  - §25.2: lexical features are computed once per record per sweep (Decision 6).
  - §18.2, §19.7 and §25.3 take Decision 7's reading of "once" and name the pinning tests; §19.7 also records the kept location read (Decision 8); §25.3 applies the no-card, no-image rule to the candidates and saved-idea reads (Decision 9).
  - §5.7: the partial delete (Decision 10).
  - §12.6 and §12.8: reason sentences, missing-record names and relation words (Decision 12).
  - §13.6: the Hebrew month-only residual (b4).
  - §31 Slice B gains b3's two tolerance widenings, unless the spec already names them.
  - §31 Slice G gains a deviations list (Decisions 4–12, as for D) and a "Slice G rulings" list. The rulings list holds every deferred hand-off from Before Task 1 step 5, with its reason (Decision 16).
  - Appendix B (Step 2).
- [ ] **Step 4: Close the hand-offs.** Every `## Inherited hand-offs` line in the ledger names its closing task and commit, or its deferral and the spec section that now records it. Expected: no line has neither.
- [ ] **Step 5: Run the acceptance, docs and import guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py tests/test_docs_guard.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 6: Lint.** `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 7: Commit:** `docs(spec): continuity capstone acceptance evidence and slice G's recorded deviations` (plus any gap-closing test, in its own `test(...)` commit before this one)

---

### Task 8: The slice gate — make check, evals, and the two final reviews over the whole capstone

- [ ] **Step 1: The full gate.** From the repo root: `make check PY=$PWD/backend/.venv/bin/python`. This covers check-lint, check-mypy, check-templates, check-eslint, check-web, check-py (under its coverage floor) and check-pydantic1. Expected: PASS, except for failures the ledger listed in Before Task 1 step 2. G changes no template, so `check-templates` passes unchanged. If a change resolved a recorded lint finding, run `make baseline` and commit the smaller file.
- [ ] **Step 2: Evals replay.** `backend/.venv/bin/python evals/run.py` (on Windows: `backend\.venv\Scripts\python.exe evals\run.py`). Expected: every recording scores as declared. G changes no prompt, so a failure here is a regression to investigate, not a re-record.
- [ ] **Step 3: Build the review inputs.** In the scratchpad:
  - `paths.txt`: `for r in 646e2c8..f52c7f4 0fa88a7..877468f 6e150db..c84108c 81bbb82..368e6ec 368e6ec..HEAD; do git log --format= --name-only "$r"; done | sort -u`, kept to paths that exist at `HEAD` or at `646e2c8`;
  - `capstone.diff`: `git diff 646e2c8..HEAD -- $(cat paths.txt)`;
  - `ranges.txt`: `git log --oneline <range>` for each range, looped.

  Expected: `capstone.diff` is non-empty, and `ranges.txt` shows the boundary subjects from Global Constraints. Any commit in `368e6ec..HEAD` that is not E, F or G is marked out of scope in the brief.
- [ ] **Step 4: Implementation → done, `/codex:review` over the whole capstone.** If `codex` is on `PATH`, run it over `capstone.diff`. Otherwise dispatch four independent Claude reviewers (fresh subagents that wrote none of the capstone), one per code area:
  - **(1) store substrate and review:** `store/continuity/{doc,canon,effective,involvement,review,candidates,pending}.py`, `store/undo.py`, `store/scene_refs.py`, `routes/ledger.py`, the event and Ledger routes in `routes/campaigns.py`, and the CRUD, apply and dismiss routes in `routes/continuity.py`;
  - **(2) similarity, identity and reconcile:** `store/continuity/{similarity,identity,reconcile}.py`, the `store/absorb/` changes, `routes/scenes.py` (the identity phase and `put_chronicle`'s trigger), the run work in `routes/continuity.py`, `routes/runs.py`, `templates/continuity_*`, and `evals/`;
  - **(3) time, suggestions, ideas, Todo and graph:** `store/continuity/{pressure,drivers,graph}.py`, `store/birthdays.py`, `store/suggest.py`, `store/scene_ideas.py`, `routes/todo.py`, `routes/shell.py`, and `templates/scene_suggestions/`;
  - **(4) the frontend:** `ledgerPaths.ts`, `LedgerView`, `components/continuity/`, `components/review/`, `ConfigView`, the chooser, `pressureControls`, `SceneIdeaPicker`, `ScenesView`, `storyGraph/`, `StoryGraphView`, and the rail.

  Each reviewer gets the spec, the seven plans, `ranges.txt` and its slice of `capstone.diff`. It is told that unrelated features touched some of the same files and are out of scope, and is asked for correctness defects, each with a failing input. Record the stand-in, the briefs and the raw findings in the ledger. Expected: a findings list per reviewer, possibly empty.
- [ ] **Step 5: Done → actually done, `/codex:adversarial-review` against the whole capstone and the spec.** If `codex` is on `PATH`, run it over `capstone.diff` plus the spec. Otherwise dispatch five independent Claude reviewers, one per spec region:
  - **(1)** §3–§8, the store side of §12.4–§12.8, §21–§22, §26 and §27;
  - **(2)** §9–§11, §23–§24, §25.1–§25.2 and §29;
  - **(3)** §13–§18 and §25.4;
  - **(4)** the UI of §12, §16 and §17, plus §19, §20, §25.3 and §28.8–§28.9;
  - **(5)** §0, the non-goals of §1–§2, §28.10, §30, §31's deviation lists, §32 (through Appendix B) and §33.

  Ask each, specifically: does the capstone implement this region end to end? Report gaps, drift and quietly dropped requirements, each against a section and with the code that shows it. Reviewer (5) also checks the stopping rule. The diff must add none of §33's parked items, and G must add no feature (Global Constraints, Scope). Expected: a findings list per reviewer.
- [ ] **Step 6: Resolve.** Verify each finding against the code before acting (superpowers:receiving-code-review). Then either:
  - fix it in the owning module, RED then GREEN, one `fix(continuity): …` commit per finding or per tight group; or
  - rule on it with a reason. An accepted deviation is amended into the spec under §31 Slice G.

  Then run `make check PY=$PWD/backend/.venv/bin/python` and the evals replay again. Expected: PASS. Every finding in the ledger reads fixed (with its test and commit) or ruled (with its reason).
- [ ] **Step 7: Record completion.**
  - §31 Slice G gains a gate bullet. It says that no Codex CLI was available, names how many Claude reviewers stood in for each gate and over which areas and regions, and says that their findings are resolved (AC19).
  - The spec's **Status** line keeps its account of the spec-gate review and Appendix A. Its last sentence, "Ready for implementation planning.", becomes "Implemented in Slices A–G (§31); acceptance evidence in Appendix B; continuity is parked (§33)."
  - `docs/superpowers/PHASE-STATUS.md` gains a closing section, `## Continuity capstone (2026-10)`. It names the spec and the seven plans, says in one paragraph what the capstone added (reviewed merges and links, the reconciliation sweep reviewed in the Ledger, temporal pressure and scene drivers, driver-aware suggestions and saved ideas, the Story Graph), points to Appendix B, and states: "Continuity is parked (spec §33): further work here is correctness defects and small usability fixes; the next major investment is mechanics."

  Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capstone_acceptance.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. Commit: `docs: the continuity capstone is complete and parked`.
- [ ] **Step 8: Close the ledger.** Every inherited hand-off is closed or deferred-and-recorded, and every review finding is resolved. The ledger's last line names the G tip and states "Slice G done". Expected: `git status` is clean.

---

## Self-review notes

**Spec coverage (Slice G):**

| Spec section | Task |
|---|---|
| §29 task labels, the route verbatim, literal call sites in `routes/` | 1 (route pin), with the existing `test_routing_guard` and `test_usage_guard` |
| §29 log rows: counts only, the listed fields, no narrative text | 1 |
| §29 docs note that Debug capture still records replies | 1 (docs-guard pin) |
| §25.1 one identity call per absorb; one reconcile call per run; never per pair | 2 (burst bound), with the existing `test_one_batched_call_for_several_ambiguous_rows` and `test_refresh_with_a_connection_adjudicates_once` |
| §25.2 worker thread; changed×all and all×all under the cap; `pairs_capped`; texts and vectors once; batches; in-memory dot products; top-k; unordered dedupe | 2 (off-loop, text once, normalization once, capped reported), with D's `test_incremental_pairs_only_changed_records_against_all`, `test_a_full_sweep_scores_every_unordered_pair_once`, `test_sweep_embeds_at_most_the_warm_limit` and the top-k tests |
| §25.3 graph read cost; no per-node card reads; no image scans | 3 (constant reads, location read once, actor names from meta), with F's Task 7 tests |
| §25.4 Todo and shell do no embedding or transcript work; no new shell badge | 3 |
| §3.10 and AC16, every read path | 3 (recorder test), 7 |
| §30 prefer/avoid; no internal token on a review surface | 5 |
| §31 G docs: CLAUDE.md inventory, CONTRIBUTING guard table, templates/README, store-guarantees; §30 README mention; §27's docs bullets | 6 |
| §0 steps 6–7; AC19 | 8 |
| §32 AC1–AC19 and §33, walked and held to the tests | 7 (Appendix B, `test_capstone_acceptance.py`), 8 (stopping-rule review) |
| hand-offs A–F naming G, or deferred | Before Task 1 step 5; 2, 3, 4, 5, 6, 7 |

**Deliberate deviations (amended into the spec at Task 7):**

- the two log rows carry fields beyond §29's list, all counts or closed modes (Decision 1);
- a run may make two model calls, its own pass's and one follow-on's (Decision 4);
- a zero-vector text is re-sent once per sweep (Decision 5);
- "once per request" is a constant number of whole-file reads, never literal once (Decision 7), and the location names stay a whole-kind read (Decision 8);
- a partial delete answers 500 `partial_delete` (Decision 10);
- journal labels and refusals no longer carry the stored relation token (Decision 12).

**Open questions (none block execution):**

- Whether CLAUDE.md should carry a one-line pointer to §33's stopping rule. The owner limited CLAUDE.md to what its own claims require, so this plan records the parked status in `PHASE-STATUS.md` and the spec only. The owner may add the line.
- The reconcile prompt still names actors through `relationships.actor_name`, so it gets Decision 9's cheaper read for free. Moving it to rosters was not considered necessary: the sweep is a background run bounded by `RECONCILE_MAX_CANDIDATES` and `RECONCILE_ACTORS`, and it memoizes per actor.
- The residuals recorded in the spec (d2, b4, b2) remain correctness items for after the capstone, under §33's "correctness defects" channel.

**Type consistency:**

- `Subject.features()` (Task 2) is the only new similarity name. `lexical`'s return shape is unchanged.
- `characters.name_of(root, cid)` (Task 3) mirrors F's `pcs.name_of(root, pid)`. Both are used by `relationships.actor_name`, and `pcs.name_of` by `suggest._actor_name`.
- `routes/ledger.forget_or_partial(cid, ref, *, name, noun)` (Task 4) is used by `delete_thread`, `delete_commitment` and `delete_campaign_event`.
- `review.RELATION_WORDS` and `_relation_words` (Task 5) are used by the three link labels. `test_relation_words_cover_every_relation` holds them to `effective.RELATIONS`.
- `ContinuityRawLink.a_title` and `b_title` (Task 5, TS) match `get_continuity`'s new keys. `recordName`, `brokenReason` and `missingName` are used only by `ReviewedGroup`.
- `ReconcileRunError` (Task 4) types the body `failedNote` reads. Its keys are D's `error` keys (`kind`, `detail`, `status`, `sweep`, `saved`, `follow_on`).
- `IDENTITY_ROW_KEYS` and `RECONCILE_ROW_KEYS` (Task 1) are built from `identity.CHECK_DECISIONS` and `continuity_routes._WORDS`, never retyped, so a new decision word changes the expected set and the producer together.

---

## Plan-gate review resolution

Pending. The plan → implementation gate (`/codex:adversarial-review`, or its recorded Claude stand-in, since Codex is not installed) has not yet run on this plan. Its findings are recorded here, one row each with what each was checked against and its resolution, before Task 1 starts.
