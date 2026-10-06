# Continuity Capstone — Slice D: Reconciliation Candidates and Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After End Scene, and on an explicit Refresh, a bounded background run finds possible duplicates, relations, thread closures and commitment resolutions. It caches them in `continuity_candidates.json` and asks one model call to propose a decision for the uncached ones. The reader then reviews each finding inside the Ledger and applies, dismisses or restores it, with every semantic write journalled. Todo counts what is pending without running anything.

**Architecture:** The backend adds three store modules under `backend/src/grimoire/store/continuity/`:

- `candidates` is the cache's IO. It is a leaf, like `doc`.
- `pending` is the one definition of a cached finding's *current* fingerprint and verdict. The GET, Todo, apply and both persists all use it, so none of them can disagree.
- `reconcile` holds discovery (reusing Slice C's `similarity` and Slice B's `pressure`), the two persists, and the prompt build and parse.

`review` gains apply, dismiss and restore validation. The routed LLM call, its meter, the run work, and both triggers live in `routes/continuity.py`:

- `PUT /chronicle` starts an incremental sweep after its lock block;
- `POST /continuity/reconcile` starts a full sweep.

Both run as one `background` run on `("campaign", cid)`, at most one live per campaign. Closure writes go through two helpers factored out of `routes/ledger.py`.

The frontend gains:

- `ledgerPaths.ts`, so the Ledger is addressable by path;
- the Continuity review section inside `LedgerView`, a column section plus a list/detail main pane;
- the Ledger force-delete path;
- `?section=` on Settings.

Todo gains the `embeddings` library chore and two campaign continuity chores.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), Jinja2 (StrictUndefined), anyio, pytest + `TestClient`; React + TypeScript + react-router, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice D:

- §5.5's identifiers, consumed;
- §6, §6.1, §6.2;
- §7.0's `candidates`, `reconcile` and `review` rows;
- §9.4's sweep half;
- §11 (all of it);
- §12 (all of it), with §5.7's force path deferred from Slice A (F6);
- §18.1, §18.2 and the §18 preamble;
- §21's candidates, reconcile, apply, dismiss and suppression routes;
- §22, §23 and §24 for `continuity_reconcile`;
- §25.1 and §25.2 for the sweep, §25.4;
- §26's reconcile, cache, concurrent-run and stale-apply rows;
- §28.4, §28.7 (the continuity and embeddings rows), §28.9 (Continuity review);
- §28.10 cases 2–8 (the reconcile half);
- §29's `continuity-reconcile`;
- AC3, AC4, AC13, AC15 and AC16 for the Ledger and Todo.

It also closes Slice C's "Slice D backlog" (C plan, pre-Task-1 step 3):

- (a) a pending review whose plot or commitment targets became alias sources;
- (b) the `continuity-reconcile` route task and §29's hint;
- (c) `RECONCILE_WARM_LIMIT` and `warm_window`;
- (d) AC3 and §18.1.

Out of scope: Slice E (suggestions) and Slice F (graph). The writer guard lists `graph`, which does not exist yet.

**Branching:** each slice is a branch stacked on the previous slice's tip, and the whole stack is rebased onto `main` at landing (the owner's direction). Slices do not gate on each other's reviews. Work on `claude/continuity-capstone-slice-d`, created from the **Slice C tip** (`claude/continuity-capstone-slice-c`). If A, B or C gain fixes after the branch is cut, rebase onto the new C tip.

**Before Task 1:**

1. Run `git status`. If the tree holds edits that are not this slice's, stop and ask the owner. Never sweep them into a Slice D commit.
2. Confirm that what this plan consumes exists on the base, with the signatures in Task Interfaces blocks:
   - from B: `store/continuity/pressure.py` `build`; `effective.render_threads`; `drivers.matching`;
   - from C: `store/continuity/similarity.py` `pool`, `lexical`, `with_cosine`, `plausible`, `rank_key`, `semantic`, `available`, `matching`; `routing` Route `"continuity"`; `scenes._soft_connection`.
   If a name moved, the Interfaces blocks here are what to adapt, and the move is recorded in the slice ledger.
3. Record the base gate. From the repo root, run `make check-py PY=$PWD/backend/.venv/bin/python` and `make check-web`. Write each pre-existing failure (test id and a one-line cause) into `.superpowers/sdd/2026-10-05-continuity-capstone-d-reconcile-review/progress.md`. Task 19 may excuse only failures listed there; `test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails as root and is environmental.
4. Copy Slice C's ledger `## Slice D backlog` section into this ledger. Mark each item with the task that closes it: (a) → Task 10, (b) → Task 8, (c) → Task 4, (d) → Task 14. Copy any item C's reviews added, and assign it a task or record why it is deferred.

## Global Constraints

- **Privacy:** fixtures, eval recordings, docstrings, comments and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch, phrases built from them ("Mara's map", "Winifred's chart", "Mara's oath", "The coronation", "Saltmarch Eve"), or ids already placeholders in the tree (`find-the-ledger`, `the-midnight-deadline`). Never read `~/.grimoire`. No committed text describes store contents, counts or distributions. Every constant below is justified structurally and marked "to be tuned against real prompts later".
- **Candidate kinds (spec §6.1):** `possible_duplicate`, `possible_relation`, `possible_thread_closure`, `possible_commitment_resolution`. Do not add other kinds.
- **Kind → group → chore (spec §6.2):**

  | Kind | Group | Chore |
  |---|---|---|
  | `possible_duplicate` | `overlaps` | `continuity-overlaps` |
  | `possible_relation` | `overlaps` | `continuity-overlaps` |
  | `possible_thread_closure` | `closures` | `continuity-closures` |
  | `possible_commitment_resolution` | `resolutions` | `continuity-closures` |

- **Identifiers (spec §5.5):**
  - `candidate_id = canon.candidate_id(kind, canonical refs)`;
  - pair fingerprints come from `canon.pair_fingerprint`, and lifecycle fingerprints from `canon.lifecycle_fingerprint`;
  - the *current* fingerprint of a stored finding is computed only by `pending.fingerprint` (Task 2).
- **Cache shape (spec §6), verbatim keys:** `{version: 1, generated, generation, basis: {embedding_space, embedding_model, identity_hashes, scored}, records: {<candidate-id>: {kind, refs, fingerprint, signals, proposal, created}}}`.
  - `proposal` is `{decision, from, to, relation, status, reason, evidence_scenes}` or `null`.
  - `basis.scored` (`{ref: generation}`) is additive beyond §6's keys (Decision 8, deviation 20).
  - A malformed cache reads as empty, and the next persist overwrites it.
- **Decision vocabulary (spec §11.3), verbatim:**
  - thread/thread pairs (`same_thread`): `duplicate`, `continuation`, `subthread`, `related`, `distinct`, `uncertain`;
  - commitment/commitment pairs (`same_commitment`): `duplicate`, `related`, `distinct`, `uncertain`. §5.3 and `effective.RELATIONS` allow `continues` and `subthread_of` between threads only, so §11.3's same-type list is read per type, and §12.3's "A continues B" is offered for thread pairs only (deviation 18);
  - cross-type pairs: `pays_off`, `related`, `distinct`, `uncertain`. Never `duplicate` across types;
  - thread lifecycle: `close`, `keep_open`, `uncertain`;
  - commitment lifecycle: `fulfilled`, `broken`, `expired`, `keep_open`, `uncertain`;
  - temporal pairs (Decision 9): `before`, `on`, `after`, `by`, `unrelated`, `uncertain`.
- **Direction (spec §11.3):** `duplicate`: `from` is aliased and `to` is canonical. `continuation`: `from` continues `to` (threads only). `subthread`: `from` is a subthread of `to` (threads only). `pays_off`: `from` is the thread and `to` the commitment. Any other combination, including `continuation` or `subthread` on a commitment pair, parses to `uncertain`.
- **Positive evidence (spec §11.4):** `close`, `fulfilled`, `broken` and `expired` need a non-empty `reason` **and** at least one `evidence_scenes` id from the run's known scene set; otherwise the decision is `uncertain`. Unknown scene ids are dropped.
- **Task name:** `continuity-reconcile`. In `routes/continuity.py` it is a literal in `_require_connection("continuity-reconcile", cid)` and in `store.usage.meter("continuity-reconcile", campaign=cid)`, around `client.complete(..., m.usage)`. The route in `store/routing.py` becomes, verbatim:

  ```
  Route("continuity", "Continuity checks",
        "The duplicate check beside absorb and the reconciliation sweep after End Scene or a refresh.",
        ("continuity-identity", "continuity-reconcile"), True)
  ```

- **Run model (spec §11.1):** class `background`, subject `runs.campaign_subject(cid)`, kind `continuity-reconcile`. It has no exclusion key, holds no scene and sends no notification. At most one is live per campaign, and a start while one is live returns the live run.
- **UI copy, verbatim:**
  - group labels: "Possible overlaps", "Possible closures", "Possible commitment resolutions", "Reviewed links / merges", "Dismissed findings";
  - the column section label: "Continuity review";
  - matching lines: "Basic matching active — semantic matching not configured" and "Semantic matching active";
  - "‹ All findings";
  - "This finding is no longer pending.";
  - "Records have changed since this was found.";
  - "Merging will hide an open thread behind a closed one" (Decision 21 gives the three sibling forms);
  - "This record has merged records: unmerge them first, or delete anyway", with the button "Delete anyway";
  - overlap actions per §12.3 and closure actions per §12.4 (Task 17 lists the exact button names);
  - `embeddings` chore `what`: "Semantic matching is not configured".
- **§30 wording:** prefer "Possible overlap", "May be finished", "Needs resolution review", "Basic matching active", "Merged into", "Continuation of". Never "AI detected duplicate", "Continuity score", "Broken campaign" or "Embedding required".
- **Imports:** at module scope, acyclic, binding submodules inside `store/` (`test_import_guard`).
  - `candidates` imports only `from .. import atomic, locks` and `from ..campaigns import paths as campaigns_paths`. It is a true leaf, as §7.0 requires: it validates ref prefixes with a private `_split(ref)` (`ref.partition(":")`, the same rule as `canon.split_ref`), not by importing `canon`. Task 1 pins this with `test_candidates_is_a_leaf`.
  - `pending` imports `from .. import fieldtext` and `from . import candidates, canon, doc, effective`.
  - `reconcile` imports `from ... import embeddings, prompts`, `from .. import aging, calendars, chronicle, clock, embed_space, errors, fieldtext, locks, paths, relationships, revision, scene_ids, vectors`, `from ..absorb import parse as absorb_parse`, `from ..campaigns import paths as campaigns_paths`, `from ..scenes import read as scenes_read`, and `from . import candidates, canon, doc, effective, involvement, pending, pressure, similarity`. (`errors` is needed for Decision 12's `errors.record`; `involvement` and `relationships` for Decision 13's actors. Task 3 Step 4's `test_import_guard` run is what proves these edges add no cycle.)
  - `review` adds `from . import candidates, pending` and `from ..scenes import read as scenes_read`.
  - These rows go beyond §7.0's "May import" column for `pending` (new), `reconcile` and `review`. Deviation 22 records the actual rows, and Task 19 Step 6 amends §7.0's table to match what `test_import_guard` enforces.
  - `routes/continuity.py` adds `from fastapi import Depends, Header, Request`, `from starlette.concurrency import run_in_threadpool`, `from ..llm import LLMClient`, `from ..llm_errors import LLMError`, `from . import ledger as ledger_routes, runs`, `from .common import _bounded_call, _llm_http_error, _noting, _require_connection, _soft_connection, computes_only, get_llm, run_error`, and `from ..store.continuity import candidates, pending, reconcile`.
  - `routes/scenes.py` adds `from . import continuity as continuity_routes`. **`routes/continuity.py` never imports `routes.scenes`.**
- **Lock domain:** `store.continuity.candidates` joins `locks.DOMAIN_MODULES`, and its public cid-taking mutators (`write`, `drop`) take `locks.campaign_lock(cid)`. `pending` and `reconcile` make no direct filesystem write, so they are not classified: `test_lock_domain_guard` treats a `DOMAIN_MODULES` entry with no direct write as a phantom, so §7.0's "each module that writes … continuity_candidates.json goes into `DOMAIN_MODULES`" is met by `candidates` alone (deviation 17). `reconcile.persist_*` still wraps its read-check-write in `campaign_lock` (reentrant), pinned by `test_persists_hold_the_campaign_lock` (Task 6). `UNREVIEWED` does not grow.
- **Revision:** each reconcile persist that writes calls `revision.bump(cid)` inside its own hold, after the write. A persist whose records and basis equal what is stored (ignoring `generated` and `generation`) writes nothing and bumps nothing (Decision 7). Apply, dismiss and restore are 2xx writes the middleware stamps. Apply's partial-write 500 bumps for itself (Task 12), and so does a dismiss whose suppression landed before its cache drop failed (Tasks 12 and 13).
- **Pydantic:** plain `BaseModel` only. The apply body is a `dict` validated in `review.plan_apply`, because §21's flat body has a key named `from` (Decision 15).
- **Logging:** log rows carry counts and modes only. No titles, beats, identity texts, reasons or prompts (spec §29).
- **Broad catches:** every new `except Exception` carries `# noqa: BLE001 -- <reason>`, or is narrowed. Every new function stays at ruff complexity ≤ 10. Every backend task ends with `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`, and every frontend task with `make check-eslint PY=$PWD/backend/.venv/bin/python`.
- **Lint ratchet:** `lint-baselines/*.json` never grows. An improvement is re-baselined (`make baseline`) in the same commit. Exception classes end in `Error` (ruff N818).
- **Commands:**
  - backend, from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <files> -q -p no:cacheprovider`;
  - frontend, from `frontend/`: `npx vitest run <files>`;
  - ratchets, from the repo root: `make check-lint|check-mypy|check-eslint|check-templates PY=$PWD/backend/.venv/bin/python`.

  One command per purpose. A `-k` filter never deselects a file listed with it.

## Review Focus

1. **A model-written plot id containing `/` or `:`** (`mara/map`, `act:2`) reaches a candidate's refs, a Todo item's `fix`, a Ledger row address and the highlight.
   - Expected: every frontend address is built by `ledgerHref` with `encodeSegment`, and parsed from the raw `location.pathname`. Deep links open the right row and finding. A candidate address never carries a ref (`canon.candidate_id` is `kind-<hash>`), so Todo's continuity items need only the right group segment; Todo's `owed` items carry a row id, built by `todo._ledger_href` with the same grammar.
   - Pinned in Task 15 (`parseLedgerTail` round-trips an id with "/" and ":"; `a row address with a slash in its id highlights that row`) and Task 14 (`test_continuity_items_link_to_their_group_and_id`, `test_owed_items_link_to_their_commitment_row`).
2. **A cache written by another device whose clock runs ahead** (the store sits in a synced folder, which CLAUDE.md invites).
   - Expected: a stored `generation` later than this machine's clock fences nothing. Without that, every local sweep would report `superseded` until the wall clock caught up, and findings would freeze. A stored generation from the past that is newer than this run's start still wins.
   - Pinned in Task 6 (`test_a_future_dated_generation_does_not_fence_forever` and `test_a_newer_generation_supersedes_an_older_run`).
3. **A large campaign whose sweep hits `RECONCILE_MAX_PAIRS`.**
   - Expected: the run reports `pairs_capped: true`. A ref the sweep did not finish keeps its old basis entry (absent when new), and the basis records when each finished ref was last scored (`basis.scored`). Scoring order is missing, then changed, then **least recently scored**, then ref (Decision 10), so repeated refreshes rotate through the whole ledger instead of rescoring the same alphabetical prefix forever, even once every ref has an entry. The identity hash is salted with the embedding space, so after a space change every ref not yet rescored under the new space stays "changed" for incremental sweeps too, and the new space reaches the tail.
   - Pinned in Task 3 (`test_a_capped_sweep_reaches_the_rest_next_time`, successive sweeps; `test_a_capped_sweep_after_a_space_change_reaches_the_tail`) and Task 6 (`test_basis_keeps_old_hashes_for_unscored_refs`).
4. **Refresh pressed while the automatic incremental sweep after End Scene is still live.**
   - Expected: the POST adopts the live run (one live per campaign). The new attempt id is recorded on the run (`run.adopted_attempts`), so `GET /campaigns/{cid}/runs?attempt=` finds it and a lost 202 can be re-found; the reaper removes the alias with the run. When the adopted run ends with `sweep: "incremental"`, landed or failed (a failed run carries `error.sweep`), the client starts **one** more refresh, which is then a full sweep. The reader never gets an incremental result for a button that promises a full one.
   - Pinned in Task 7 (`test_a_refresh_during_a_live_sweep_adopts_it_under_its_own_attempt`, `test_reap_drops_adopted_attempt_aliases`) and Task 16 (`a refresh that adopted an incremental sweep runs once more`, including the failed case).
5. **A finding applied in another tab, or opened from a Todo item after the next refresh removed it.**
   - Expected: apply answers 404 `not_found`. The view re-reads candidates without starting a run, and the address shows the group with "This finding is no longer pending." It never shows a stuck spinner or a raw error.
   - Pinned in Task 12 (`test_apply_on_a_finding_no_longer_cached_is_not_found`) and Task 17 (`an apply answered not_found shows the finding is no longer pending`).

---

## Decisions (and why)

1. **One module for "what a cached finding means now": `pending`.** §5.5 wants each identifier defined in one module. §18.2 wants Todo's live filter to be the same function as the GET's. The two persists (§11.1) and apply (§22) recompute the same fingerprint. Neither host spec §7.0 offers can hold it:
   - `candidates` and `canon` are leaves that cannot import `effective`;
   - `reconcile` drags `pressure` and the embeddings client into Todo's import graph;
   - `review` drags `undo`.

   So `pending` is a seventh read-only module, recorded as a deviation. It imports `candidates`, `canon`, `doc` and `effective` only.
2. **Verdicts.** `pending.verdict(current, record)` is the first match of:
   1. `unknown`: a ref's ledger is unreadable, or continuity.json's `file`, `aliases`, `links` or `suppressions` is malformed (`Current.continuity_malformed`), or the current fingerprint cannot be computed (`pending.fingerprint` answered `None` for refs that do exist, e.g. a lone surrogate in a title);
   2. `gone`: a ref is not a current canonical record, because it was deleted, merged away or never existed. A pair now aliased together is `gone` (one side is no longer canonical), which is what Task 6's merge test expects;
   3. `satisfied`: a pair with any effective link between its refs in either direction, or a lifecycle target that is no longer live;
   4. `suppressed`: the current fingerprint is a key in continuity.json's suppressions;
   5. `stale`: the stored fingerprint differs from the current one;
   6. `settled`: a model-only nomination the model declined (Decision 9);
   7. `live`.

   A malformed continuity.json reads, through `doc.read`, as no aliases, links or suppressions. Treating that as truth would resurrect every dismissed finding and rediscover every merged pair, so the whole cache is `unknown` while it lasts: hidden, kept, and never acted on (`diagnostics.malformed` already says why).

   Consumers:
   - the GET shows `live` and `stale`;
   - Todo counts `live` only;
   - apply and dismiss accept `live`, and `stale` when the request's `expect_fingerprint` equals the **current** fingerprint (§22 step 5, §12.9: the reader looked at the 409's current records and resubmitted). `gone`, `satisfied`, `suppressed` and `settled` answer 404 `not_found` ("no longer pending", deviation 19). `unknown` answers 409 (`malformed` or `unreadable`, Decision 16);
   - a persist keeps `live`, `settled` and `unknown`, and drops the rest. A ledger it cannot read is never a reason to throw findings away.
3. **Fingerprint sides.** A pair side is `{ref, title, status, kind, due}` from the effective record, where threads give `kind=""` and `due=""`. An **event** side (temporal pairs) is `{ref, title: name, status: "", kind: "", due: date}`, so a rescheduled event invalidates a dismissal of its temporal pairing. The lifecycle input is the effective `status`, `len(beats)` of the merged beats, `latest_beat`, `due` (commitments only, else `""`), and the ids of effective links with `a == ref` and a relation in `before|on|after|by`.
4. **Generation, and what "newer" means.**
   - `reconcile.generation(run_id) -> str` is `f"{time.time_ns():020d}-{run_id}"`, so it sorts as a string and ties break on the run id.
   - A persist is superseded iff `this_stamp < stored <= f"{time.time_ns():020d}~"`. A stored generation from this machine's future fences nothing (Review Focus 2).
   - A missing or unparseable stored generation is older than everything.
   - In process, one-live-per-campaign makes a newer concurrent run impossible except after `forget_subject` (campaign delete). The persist checks that case itself (Decision 7). The fence is therefore mostly cross-process, as §26 "newest generation wins" describes.
5. **One live reconcile per campaign needs a registry primitive.** `start_or_existing` adopts only by attempt id or exclusion key, and `background` has neither. A check made outside the registry lock lets End Scene and Refresh both create a run. So `start_or_existing` gains a keyword, `single_live: bool = False`.
   - The scan is a private `RunRegistry._live_single(subject, cls, kind) -> Run | None`, called under `self._lock`: a `running` run on the subject with the same `cls` and `kind` that is neither `forgotten` nor `cancel_requested` (a Stop already pressed must not be adopted by the next Refresh). `start_or_existing` is at ruff complexity 10 today with `max-complexity = 10` and no `runs.py` entry in `lint-baselines/ruff.json`, so it gains exactly one branch, after attempt adoption: `if single_live and (live := self._live_single(subject, cls, kind)) is not None: return self._adopt(live, subject, attempt_id), False`. If that still exceeds 10, the existing attempt-adoption block moves into its own helper too.
   - `_adopt` appends `attempt_id` (when given) to the new `run.adopted_attempts: list[str]` and indexes `_by_attempt[(subject, attempt_id)] = live.id`.
   - `_subject_runs` matches `r.attempt_id == attempt or attempt in r.adopted_attempts`, so `GET .../runs?attempt=` (the client's `findDraftRun`, used for lost-202 recovery and the poll's refind) finds an adopted run. `reap` deletes `(run.subject, a)` for every `a` in `run.adopted_attempts` when it still maps to `run.id`, beside the run's own attempt key. `reserve_campaign_background` returns `tuple[Run, bool] | None` rather than §11.1's `Run | None`, because the route needs `fresh` to skip `reservation` for an adopted run. This is a deviation.
6. **Triggers.**
   - **Automatic:** `put_chronicle` fires after its `with` block, on any commit that **completed in this request**. That covers the fresh path **and** the journalled resume path, because a resume finishes a commit. The idempotent replay returns inside the lock and never reaches the hook. It hands `body.edits` and the commit's `progress` journal (in scope inside the `with` block; `put_chronicle` keeps a reference) to `schedule_reconcile`, which computes `touched` **inside its never-raise `try`**: `_touched_refs(edits, progress)` walks `progress["edits"]` by position (`str(i)` slots with `state == "applied"`, as `apply_edits` writes them, not the applied-id list, since ids need not be unique), keeps entries whose edit is a dict with `kind` in `plot|commitment`, a dict `target` and a str `id`, and yields `thread:<id>` / `commitment:<id>`. A client-supplied edit of the wrong shape is skipped, never a 500 after the commit landed (§11.1 "the save response never depends on it").
   - **A trigger that adopts a live sweep** does not drop its `touched`. The run registry on `app.state.runs` keeps the pending refs, keyed by subject (`("campaign", cid)`), under the registry's own `_lock`: `RunRegistry.pend_touched(subject, refs)` and `take_touched(subject) -> set[str]` (Task 7). Nothing is kept at module scope. A `TestClient` builds an app per test and the suite reuses cids such as `run`, so module state would hand one test's leftover refs to the next test's first run (CLAUDE.md: "module state would leak runs between them"). An adopting `schedule_reconcile` adds its refs there. The live run takes that set after its own pass and, when non-empty, runs one more incremental pass with those refs inside the same run. Every fresh run also starts by taking whatever the set holds (Task 8, step 9). So two End Scenes back to back both get their touched records adjudicated, and a burst coalesces into one follow-up pass.
   - **The pending refs die with what they name.** `RunRegistry.forget_subject` also drops the subject's pending refs. A slug is reusable, so without this a recreated campaign of the same name would inherit the dead one's refs: the hazard `forget_subject` exists to close. A successful `PUT /config/data-dir` calls `runs.drop_pending_touched(app)`, which clears every entry. The refs name records in the old tree, and the move is refused only while a run is live, while refs can wait with no run. A run that is `cancel_requested` **or** `forgotten` never takes the set for its follow-on pass, so a deleted campaign's still-executing sweep cannot drain a recreated one's refs.
   - **Explicit:** `POST /continuity/reconcile` is `def`, has `status_code=202`, and is `@computes_only`.
     - It answers `{run}`.
     - A reservation refused by a storage move answers 409 `{kind: "busy"}`, as `reserve_draft` does.
     - It works with no connection, and the result says `llm: "off"`.
7. **The persist hold.** Each persist runs `with locks.campaign_lock(cid):` through `run_in_threadpool`, and the route helper retries `StoreBusy` (`_PERSIST_ATTEMPTS = 3`, `_PERSIST_BACKOFF = 0.5`, the review persist's numbers). Inside the hold, in order:
   1. `stillborn()` (the run is `cancel_requested` or `forgotten`) means no write, returning `cancelled`;
   2. `campaigns_paths.campaign_exists(cid)` false means no write, returning `gone`;
   3. the generation fence;
   4. reload `candidates.read`, then `pending.Current.load`. When `Current.continuity_malformed`, no write, returning `{"written": False, "continuity": "malformed"}`: the existing cache stays untouched rather than being rebuilt against a view with no suppressions, aliases or links;
   5. merge (Decision 8) and filter by verdict;
   6. compare the new `records` and `basis` with the stored ones; when equal, no write and no bump, returning `written: False, superseded: False`. An End Scene that found nothing new must not move the write token, which `ClockPanel` reads as `campaign_moved` and re-prices a time-skip preview over;
   7. `candidates.write`;
   8. `revision.bump`.
8. **What a persist keeps** (§11.1 steps 2 and 4).
   - **Persist 1** writes `discovered ∪ retained`.
     - **Retained:** every existing record **except**
       - a non-temporal pair kind that the sweep rescored (any of its refs is in `sweep.rescored`) and did not rediscover: a rescored pair that dropped out of the top-k is no longer a finding;
       - a lifecycle record whose `signals.reason` is `stale` or `overdue` when that source was checked this sweep (`sweep.lifecycle_checked[reason]`, True when its `_soft` read did not fall back) and did not re-nominate it. A clock moved backwards, a linked event rescheduled later or a raised `stale_after` clears the condition without moving the lifecycle fingerprint, and a Refresh must be able to retract the finding. `touched` records (model-only) and records whose source fell back are kept;
       - a temporal pair (`pending.is_temporal`, cached live or `settled`) when the temporal source was read this sweep (`sweep.lifecycle_checked["temporal"]`) and its id is not in `sweep.temporal_ids`. §11.3 nominates a temporal pair only while the due is unparseable, the commitment has no before/on/after/by link, and the event is not passed and is inside the suggestion horizon. An event that passes, or a temporal link to **another** event, ends that condition. Neither moves the pair fingerprint (a pair side carries no links, and an event side's date does not change when time passes), so without this rule a cached live temporal finding would outlive its nomination.

       Pairs whose refs were not rescored are retained.
     - **Carry-forward:** a discovered record whose id exists in the cache with a proposal and the same stored fingerprint keeps that `proposal` and its original `created`, unless the proposal is **void**: `pending.evidence_ok(proposal, scene_ids)` is false because an `evidence_scenes` id is not in `scenes_read.list_scenes` (a rename, the first datetime stamp, or a width-crossing `repad`). A void proposal becomes `null` on every retained or carried record, so `select` re-asks it (§5.6 "the next reconcile rebuilds it"). Fingerprints exclude scene ids by design (§5.5), so without this a renamed evidence scene would leave the finding unappliable for ever.
     - Every record then passes the verdict filter (Decision 2).
   - **Persist 2** sets `proposal` only on records **currently in the cache** whose verdict is still `live`. It also adds the model-only nominations (Decision 9) that came back with a proposal and whose recomputed fingerprint still matches. So a candidate applied, dismissed or merged between the two persists is never resurrected (§22).
   - **Basis:** `identity_hashes` gets the new hash for every ref in `sweep.rescored`, and `scored` (an additive basis key, `{ref: generation}`, deviation 20) gets `sweep.stamp` for the same refs. Every other ref that still exists keeps both old entries (absent when new), including a ref cut off by `RECONCILE_MAX_PAIRS` and a ref left unscored for want of a vector (Decision 10): its stale or absent hash already makes it changed next time. `embedding_space` and `embedding_model` come from the sweep's resolved space, or `""`; the touched rule (Decision 11) compares them, and the space is also inside each hash.
9. **Model-only nominations** are not persisted without a proposal.
   - **Temporal pairs** (§11.3) carry no deterministic signal beyond a date window.
   - **"Touched by this scene" lifecycle re-checks** (§11.1) are records the scene just moved; one cannot be stale.

   Persisting either without a proposal would flood "Possible overlaps" and "Possible closures" after every scene, and entirely so with no connection. So both go to the model and land only in persist 2, carrying the model's proposal. A declined one (temporal `unrelated`/`uncertain`; touched `keep_open`/`uncertain`) is cached as `settled`: hidden from the GET and Todo, and not re-asked while its fingerprint holds. Stale threads and overdue commitments are persisted deterministically, with or without a model. Eval case 5 (an answered thread is closed) is reachable only through the touched re-check, which is why it exists.
10. **Discovery** (`reconcile.discover`) runs in one worker thread.
    - **Pools:** `similarity.pool(cid, "thread")` and `pool(cid, "commitment")`, which include closed and resolved records (§11.2).
    - **Identity hash:** `sha256((space + "\0" + subject.text).encode("utf-8", "surrogatepass"))`, where `space` is the resolved space's `space` string or `""`. Salting with the space means a space change makes every ref changed, and a ref the change's first sweep did not reach stays changed for later incremental sweeps too. `surrogatepass` means a lone surrogate in a model-written title cannot raise. A record whose `canon` fingerprint still raises `UnicodeEncodeError` is skipped by discovery (no candidate), never a failed run.
    - **`changed`:** refs whose hash differs from `basis.identity_hashes` or have no entry. A full sweep scores every ref.
    - **Scoring order** (Review Focus 3): refs missing from the basis first, then changed refs, then by `basis.scored` ascending (least recently scored; absent first), then by ref. Without the third key, a capped full sweep over a fully populated basis would rescore the same alphabetical prefix every time.
    - **Pairs:** each ref to score is paired once against every other subject of either type; changed×changed pairs are deduped by unordered pair. Each pair costs one `similarity.lexical` (plus `with_cosine` when vectors exist), and the run stops at `RECONCILE_MAX_PAIRS`. A ref whose pairs were all scored joins `rescored`.
    - **Vectorless refs:** while a space is configured (mode `configured` or `failure`), a ref with no vector this run (an endpoint failure, or past `RECONCILE_WARM_LIMIT`) is scored lexically but does **not** join `rescored`. Its basis entry stays as it was and its existing pair records are retained (they are not "rescored and not rediscovered"), so an outage keeps prior semantic pairs (§26) and the ref is scored semantically once its vector exists. Mode `off` keeps the plain rule.
    - **Keeping pairs:** `similarity.plausible`, ranked by `similarity.rank_key`. A pair is kept when it is in the top `RECONCILE_TOP_K` of either endpoint, per type.
    - **Kinds:** same-type pairs become `possible_duplicate`, and thread×commitment pairs become `possible_relation`.
    - **Signals** (§6): `{title_exact, slug_equal, lexical: max(tokens, chars), cosine, shared_actors, shared_scenes, shared_anchors, via}`. `via` is `admitted_by`, used for the deterministic and semantic log counts.
11. **Lifecycle nominations** (§11.1 step 1, §13.5).
    - **Stale threads:** run `aging.prepare(cid, clock.now(cid))`, soft, over `effective.threads(cid)`. State `stale` nominates `possible_thread_closure` with signals `{reason: "stale", days_since}`.
    - **Overdue commitments:** run `pressure.build(cid, sources={"deadline", "linked_deadline", "event"})`, soft. Any item with `subject == ref` and `state == "overdue"` nominates `possible_commitment_resolution` with `{reason: "overdue", in_days, via: item kind, event: item ref when linked}`. That covers both a passed due and a reached before/by/on link. Nothing resolves.
    - **Touched records** (incremental only): the explicit `touched` refs **plus**, when `basis.embedding_space` equals the current space, every ref whose basis entry exists and differs from its current hash (a record moved since the last sweep; the identity text carries the latest beat, §9.1). After a space change every hash differs, so that second set is skipped for the one sweep. Live and not already nominated, they become model-only `{reason: "touched"}`. The second set is what lets a skipped automatic run (a refused reservation, a storage move) be caught by the next one, as §11.1 says. New refs (no entry) are not included, so the first sweep of an old campaign does not re-check its whole ledger.
    - **`lifecycle_checked`:** `{"stale": bool, "overdue": bool, "temporal": bool}`, True when that source's `_soft` read did not fall back (Decision 8's retraction rule). `temporal` is True only when the calendar was readable and both `aging.prepare` and the `event` pressure read succeeded. With no readable calendar no temporal pair is nominated, so a broken calendar must not retract cached ones either.
    - **`temporal_ids`:** the ids of every temporal pair nominated this sweep, taken **before** the verdict filter below. Decision 8 uses them to retract temporal findings whose condition has ended.
    - **Model-only nominations are verdict-filtered** at the end of `discover`. Discovery builds one `pending.Current`, and a model-only nomination (temporal or touched) stays in `sweep.model_only` only when `pending.verdict(current, nomination) == "live"`. Without this, a temporal pair the reader dismissed (`suppressed`) or answered with "Related" (`satisfied`), or a touched re-check whose lifecycle fingerprint has not moved under a Keep open suppression, would never stay cached, since persist 2 drops it. It would then be re-sent to the model on every sweep, including every End Scene, spending the `RECONCILE_MAX_CANDIDATES` budget on a decision the reader already made. That breaks §11.1 step 3 ("no cached proposal and no suppression"). A fresh nomination has no proposal and its fingerprint was computed against the same `Current`, so the filter can only answer `live`, `suppressed`, `satisfied`, `gone` or `unknown`.
    - **Temporal pairs** (model-only): a live commitment with a non-empty `due` that the calendar cannot parse, meaning `aging.prepare` gave a `now_fixed` while `aging.age` gives `due_in` and `days_over` both `None`, and with no effective before/on/after/by link. It pairs with the nearest `RECONCILE_TEMPORAL_EVENTS` pressure items of kind `event` whose state is not `passed` and `0 <= in_days <= calendars.UPCOMING_WINDOW_DAYS`. With no readable calendar there are no temporal pairs: a broken calendar must not make every due look unparseable.
12. **Embeddings in a sweep** (§9.4).
    - Load the space with `similarity.available()`; `None` means mode `off`. It is the `embed_space.resolve()` dict (`{model, base_url, key, space}`), so `Sweep.space = space["space"]` and `Sweep.model = space["model"]`.
    - **The probe:** one `loaded = vectors.load(space["space"], texts)` finds the uncached texts, sorted by ref. It is the sweep's only read of the cache (§25.2 "loads cached vectors once").
    - **`required`:** the uncached texts of `changed` refs, at most `RECONCILE_WARM_LIMIT` (a full sweep requires none).
    - **`warm`:** `embed_space.warm_window(other uncached, f"{cid}\0{stamp}", RECONCILE_WARM_LIMIT - len(required))`.
    - **The call:** `similarity.semantic(required, warm, deadline=time.monotonic() + embeddings.TIMEOUT, space=space, cached=texts, warm_limit=RECONCILE_WARM_LIMIT, loaded=loaded)`. With `loaded` given, `semantic` skips its own step-2 `vectors.load` and uses it.
    - The seed is the campaign id **plus** the run's stamp. A seed of `cid` alone gives the same offset to two runs over the same uncached list, so a stuck text at that offset would be retried forever (spec "seeded by `cid`" kept, rotation made real; recorded).
    - `semantic` gains the keywords `warm_limit: int = IDENTITY_WARM_LIMIT`, which also bounds the width-rule re-embed, and `loaded: dict | None = None`. C planned `semantic` as reused unchanged; these two additive keywords are the only change, recorded.
    - `EmbeddingsError`/`OSError` set mode `failure` and keep lexical candidates. One counts-only `store.errors.record("continuity-reconcile", <kind>, "semantic matching unavailable — basic matching used", campaign=cid, task="continuity-reconcile")`.
13. **Adjudication input** (§11.2), bounded by `RECONCILE_MAX_CANDIDATES`.
    - **Priority** (§11.1 step 3), over persisted records with `proposal is null` and verdict `live`, plus model-only nominations not already cached with a proposal. `select` re-checks each model-only nomination's verdict against the cache as persist 1 left it, and sends only `live` ones, on top of `discover`'s filter (Decision 11). So nothing suppressed, satisfied or gone is sent:
      1. resolutions with `reason == "overdue"`;
      2. `possible_duplicate` by `(-title_exact, -slug_equal, -(len(shared_actors)+len(shared_scenes)+len(shared_anchors)), -lexical, -(cosine or 0), id)`;
      3. the rest, by `candidates.KINDS` order, then id.
    - **Per record:** the snippet line (`snippets/plot_thread_line/absorb.j2` / `commitment_line/absorb.j2`; events as `event: <name> (<date>)`), the last `RECONCILE_BEATS` merged beats with their scene ids, pressure `{state, in_days}`, effective links naming it, and its involvement actors (§11.2): `involvement.of(cid, refs)`'s `actors`, named through `relationships.actor_name(cid, actor_ref)`, at most `RECONCILE_ACTORS` per record. Events carry none. Lifecycle candidates thereby get actors too, not only pairs through `shared_actors`.
    - **Shared context:** the campaign date (`pressure.build(...)["friendly"]`), and chronicle one-lines for every beat scene shown plus the last `RECONCILE_RECENT_SCENES` scenes.
    - **Known scene set:** the ids of beats shown ∪ chronicle lines shown.
    - Spec §11.2's "canonical active threads / unresolved commitments" are sent as the records the capped candidates name, not the whole ledger. That keeps the one prompt bounded by the cap (recorded).
14. **Constants** (`reconcile.py`). All are candidate-generation parameters, justified structurally and to be tuned against real prompts later.
    - `RECONCILE_WARM_LIMIT = embeddings.BATCH * 4`: four batch round trips under one shared `embeddings.TIMEOUT` deadline. Nobody waits on a sweep, but a live one blocks a storage move (§11.1), so it is bounded like semsearch's reader-facing window. Rotation covers the rest over later runs.
    - `RECONCILE_MAX_PAIRS = 20_000`: a pair costs one token/trigram Jaccard plus, with vectors, one pure-Python dot product of O(d) (no numpy on Android, §25.2). At embedding widths in the low thousands that is a few seconds of one worker thread, which keeps the storage-move refusal window short. It covers all pairs of roughly two hundred records of one type in a single sweep.
    - `RECONCILE_TOP_K = similarity.IDENTITY_TOP_K` (3): the same neighbour bound §9.3 sets for identity, so a record yields at most a handful of findings.
    - `RECONCILE_MAX_CANDIDATES = 24`: each candidate renders two short records, three beats and a signal line, so 24 keeps the one call's prompt in the same few-thousand-token range as absorb's.
    - `RECONCILE_BEATS = pending.ROW_BEATS` (3): matching the identity text's latest plus two previous beats (§9.1), and the same beats the review detail shows.
    - `RECONCILE_RECENT_SCENES = 6`.
    - `RECONCILE_TEMPORAL_EVENTS = 3`.
    - `RECONCILE_ACTORS = 4`: a name list per record, bounded so a long-running thread with a large cast cannot dominate the one prompt.
    - `RECONCILE_REASON_CHARS = 280`, matching `identity.REASON_CHARS`.
15. **The apply body is a `dict`.** §21 fixes a flat body with a key `from`. A plain `BaseModel` cannot declare a field named `from` without `Field(alias=...)`, which `test_pydantic_guard` forbids, and the guard allows no exemption marker. `routes/todo.py`'s `put_todo_ignored(..., body: dict)` is the precedent. The wire shape is exactly §21's; `review.plan_apply` validates it (deviation).
16. **Apply semantics** (§12.3, §12.4, §22). One `campaign_lock` hold in the route covers everything.
    - **Check:** `review.check_candidate`, in order:
      1. **strict canonicalization** (§22 step 1, §5.2): `review._require_wellformed(cid, {"aliases", "links", "suppressions"})` gives 409 `malformed` before anything else. The lenient view would read a malformed aliases section as empty and let `close` write a hidden alias source, the hazard `routes/ledger.py` already refuses for PUT and DELETE;
      2. 404 `not_found` when the id is not cached, or its verdict is `gone`, `satisfied`, `suppressed` or `settled` (hidden findings; "no longer pending", deviation 19). `unknown` from an unreadable ledger gives 409 through `review._require_readable`;
      3. 409 `stale_candidate` with `current: {fingerprint, records}` when the current fingerprint differs from `expect_fingerprint` (which defaults to the cached fingerprint, §21), or when the proposal is void (`pending.evidence_ok` false). A `stale` verdict with `expect_fingerprint == current` passes: §12.9's resubmission after looking. The suppression and settle then use the current fingerprint.
    - **Plan:** `review.plan_apply`, which validates every part before the first write.
      - `alias`: same-type pairs only. `canonical` must be one of the two refs, and the other is the source. Liveness comes from `review.validate_alias`, giving 409 `liveness_mismatch` unless `accept_status_change`. `copy_due` is valid only when the source due is non-empty and the canonical's is empty, else 400 `due_not_copyable`.
      - `link`: `relation` must fit `effective.RELATIONS` for `(from, to)`, both refs of the candidate, else 400 `invalid_relation`. For temporal pairs `before|on|after|by` with `from` = the commitment, plus `related_to`. For cross-type pairs `pays_off` (thread → commitment) or `related_to`. For same-type, `continues`, `subthread_of` (threads only) or `related_to`.
      - `close`: thread closures only, giving status `closed`.
      - `resolve`: commitment resolutions only, `status ∈ {fulfilled, broken, expired}`.
      - `keep_open`: lifecycle kinds only.
      - `beat` and `scene` come as a pair. The scene must exist, else 400 `bad_scene`.
      - Anything else is 400 `bad_op`.
    - **Write order:** status (via `ledger_routes.move_thread`/`move_commitment`, journalled), then the due copy (`move_commitment(..., due=<source due>)`, journalled), then alias or link (`review.create_alias(..., source="review")` / `review.create_link(..., scene=scene or "")`, journalled), then cache cleanup (`review.settle`: for `keep_open`, first a suppression with `decision: "keep_open"`, **then** `candidates.drop`). The suppression goes first because a finding that is suppressed but still cached is hidden by its `suppressed` verdict, so a drop that then fails costs nothing. Dropping first and then failing to suppress would remove the finding unsuppressed, and the next refresh would bring it back.
    - **Partial write:** an `OSError` part-way bumps the revision and answers 500 `{kind: "partial_apply", detail, landed: [parts]}`.
    - The due copy rides inside apply rather than as a second client PUT (§5.1). It still goes through the `routes/ledger.py` helper and is its own journalled row, so §5.1's "explicit, separate journalled write … in `routes/ledger.py`" holds and §21's `copy_due` has a meaning (recorded).
17. **Dismiss refuses a stale finding** with the same 409 `stale_candidate`, unless the body's optional `expect_fingerprint` equals the current fingerprint (the reader looked at the 409's current records). The UI disables a stale finding's actions (§12.2), and suppressing a meaning nobody looked at would hide its replacement. Dismiss runs the same malformed and hidden-verdict checks as apply, but **not** the evidence check: dismissing does not depend on the model's citation. `decision: "keep_open"` is valid for lifecycle kinds only (400 `bad_decision`). Dismiss writes in `settle`'s order (suppression, then drop). A `ContinuityError` from `put_suppression` therefore lands nothing and answers 409 `malformed` with no bump. An `OSError` from the drop after the suppression landed bumps the revision and answers 500 `{kind: "partial_dismiss", detail, landed: ["suppression"]}`, because the middleware stamps only a 2xx and the suppression write did land (CLAUDE.md: "a write answered non-2xx stamps for itself too"). Restore is one atomic write (`doc.drop_suppression`), so a failure lands nothing and needs no bump.
18. **The closure helpers** live in `routes/ledger.py` and are public, so CLAUDE.md's "hand edits to the ledger go through `routes/ledger.py`" stays true. `move_thread` and `move_commitment` write the **physical** id they are given, do no redirect, and expect the caller to hold `campaign_lock(cid)`. `scene=None` keeps the stored `last_scene`. `keep_later_scene=True` with a beat restores `last_scene` to `effective.later_scene(stored, scene)` inside the same journalled block, so the journal still shows one row. `put_thread`/`put_commitment` call them with their existing redirect, labels and 404s unchanged.
19. **A review staged before a merge** (Slice C deviation 12) is refused at save, never re-staged.
    - **Where:** in `put_chronicle`'s fresh branch, after `check_conflicts` and before the first write, `continuity_review.merged_edit_targets(cid, body.edits)` lists every `plot`/`commitment` edit whose physical target is now a live alias source.
    - **Answer:** 409 `{kind: "edits_target_merged", detail, edits: [{index, id, label, source, canonical, canonical_title}]}`. `detail` names them and says to reject those rows (or re-absorb) and save again.
    - **Why it is enough:** the panel's generic save-error path shows `detail`, and a rejected row is not in the batch, so the rest saves. Redirecting at save would write a beat staged against the source's `before` token onto the canonical, which `check_conflicts` cannot vouch for.
    - The resume path is not checked; its edits were judged when it first ran.
20. **The suite's switch.** `routes.continuity.AUTO_RECONCILE = True` is read by `schedule_reconcile`. `conftest.py` turns it off unless a test carries `@pytest.mark.reconcile`, the `_tracker_off_unless_asked` precedent. Without it, every existing `PUT /chronicle` test (most of `test_routes.py`, `test_review_detach.py` and others) would start a lifespan-hosted run that races fakes scripted by call order, writes a cache after the response, bumps tokens late, and can refuse a later `PUT /config/data-dir`.
21. **Liveness confirmation copy:** "Merging will hide an open thread behind a closed one" (spec), plus "Merging will hide a closed thread behind an open one", "Merging will hide an unresolved commitment behind a resolved one" and "Merging will hide a resolved commitment behind an unresolved one".
22. **Ledger addresses** (§12.1), built and parsed only by `frontend/src/ledgerPaths.ts`.
    - **Sections:** `facts | threads | commitments | relationships | standings | changes | timeline | continuity`.
    - **Groups:** `overlaps | closures | resolutions | reviewed | dismissed`. `/ledger/continuity` alone opens `overlaps`.
    - **Facts:** the canonical facts address is the bare `/ledger`. `/ledger/facts` is accepted.
    - **Unknown paths:** an unknown section or group replaces to `/ledger`.
    - **Parsing:** row ids are `encodeSegment`ed, and the tail is parsed from the raw `location.pathname` (the WorldView precedent; `useParams()["*"]` decodes).
    - **Highlight:** a thread or commitment row address naming an alias source highlights the effective row whose `aliases[].ref` names it.
23. **What the candidates read carries beyond §12.2.** Additive fields only:
    - each record also carries `beats` (the last `RECONCILE_BEATS`, `{scene, text}`) and `due`;
    - each candidate carries `stale_reason: "records" | "evidence" | null`. `stale` is true for a `stale` verdict **or** a void proposal (`pending.evidence_ok` against the one `list_scenes` map the read already takes), so the GET and apply agree;
    - `names: {<scene id | actor ref | event ref>: display}` for every id the response mentions (beats, `last_scene`, evidence scenes, `shared_actors`, `shared_anchors`), from that same scene map, `relationships.actor_name` and `review.describe`, so the detail never shows a scene filename or `characters:mara`;
    - `scenes: [{id, title}]`, newest first, for the closure form's evidence-scene select;
    - `run: RunHandle | null`, the live reconcile on the subject, so the section can follow a sweep already running on load.
24. **The Refresh flow.**
    - `api.reconcileContinuity` is `draftRun` on the campaign scope. `useContinuityReview` holds one `AbortController` per `cid`, passes its signal to every run call, aborts it in the effect cleanup, and skips every follow-up and `setState` once aborted.
    - On landed or failed alike, the view re-reads candidates, because a failed LLM call still landed persist 1 (§26).
    - A Refresh whose run ends with `sweep: "incremental"` triggers exactly one more Refresh (Review Focus 4): `result.sweep` when landed, `error.sweep` when failed (`_reconcile_work` copies `sweep` into a failed run's `error`, because `awaitRunAt` throws away `result` for a non-landed run).
    - A failed run's note is chosen by `useContinuityReview.failedNote` from the run's `error.saved` / `error.follow_on` (deviation 27), followed by the error detail: "The model check did not finish — basic findings are listed." only when persist 1 saved; "The refresh did not finish — nothing it found was saved." for the POST's own refusal or a failure before persist 1; "The follow-on pass did not finish — the first pass's findings are listed." when the follow-on pass failed after a first pass that saved; and "The refresh's outcome could not be read back." when the failure says neither (a reaped run, a restart, a poll that gave up). A landed run with `llm === "off"` shows "No model connection — findings are listed without a suggested decision."
    - **A sweep already running when the section loads** (`candidates.run` non-null, typically the automatic one after End Scene) is followed: `api.awaitCampaignRun(cid, run, signal)` (Task 15), single-flight with the Refresh latch, and candidates are re-read when it ends, whatever its state (§12.9 "re-reads candidates when it lands").
    - A `409 stale_candidate` never starts a run: it re-reads candidates only, and the detail re-renders from `error.body.current` at once, with its actions bound to `current.fingerprint` so the reader may resubmit (§12.9). A void-evidence 409 (same fingerprint) instead offers Refresh.
    - **One epoch.** The continuity reads and the ledger's own reads share `LedgerView`'s epoch: every continuity mutator (apply, dismiss, restore, removeAlias, removeLink) bumps it, and every ledger hand edit already does, so a closure applied updates the Threads count, a thread closed in the table drops its closure finding, and a merge hides the source row.
    - **After a 2xx apply or dismiss**, the view navigates (replace) to the finding's group address and shows a one-line confirmation (e.g. "Merged Mara's map into Winifred's chart."). "This finding is no longer pending." is shown only once a candidates read has settled.
25. **Proposal labels are hedged, and one table holds them.** A raw decision word never reaches the reader. `pending.DECISION_LABELS` (server, for Todo items) and `components/continuity/labels.ts` (client, for the detail) carry the same strings, each pinned by a test on its side: `duplicate` → "Suggested: same business (merge)", `continuation` → "Suggested: continuation of", `subthread` → "Suggested: subthread of", `related` → "Suggested: related", `pays_off` → "Suggested: pays off", `distinct` → "Suggested: different business", `close` → "Suggested: may be finished", `fulfilled`/`broken`/`expired` → "Suggested: fulfilled" / "Suggested: broken" / "Suggested: expired", `keep_open` → "Suggested: keep open", the temporal words → "Suggested: before" / "on" / "after" / "by", and `unrelated`/`uncertain` → "No clear suggestion". A stale finding's list marker is visible text, "Records changed" (or "Evidence moved" for `stale_reason == "evidence"`), never a glyph with a tooltip.

---

## File structure

Backend paths are relative to `backend/src/grimoire/`, tests to `backend/tests/`, and `templates/`, `evals/`, `scripts/` and `frontend/` to the repo root.

| File | Responsibility |
|---|---|
| `store/continuity/candidates.py` (new) | The cache's tolerant read, locked write and drop; `KINDS`, `PAIR_KINDS`, `LIFECYCLE_KINDS` |
| `store/continuity/pending.py` (new) | `Current`; the one current-fingerprint function; verdicts; group, chore and visibility tables; joined rows; `findings` |
| `store/continuity/reconcile.py` (new) | Constants; `generation`; `Sweep`; `discover`; `pressure_by_ref`; `persist_found`, `persist_proposals`; `select`; `build_payload`, `template_vars`, `build_prompt`, `parse_output`; `counts` |
| `store/continuity/similarity.py` | `semantic(..., warm_limit=IDENTITY_WARM_LIMIT)` |
| `store/continuity/effective.py` | `later_scene` |
| `store/continuity/review.py` | `validate_alias` (factored out of `create_alias`); `check_candidate`, `plan_apply`, `settle`, `dismiss`, `restore_suppression`, `merged_edit_targets` |
| `store/continuity/__init__.py` | Docstring module list gains `candidates`, `pending`, `reconcile` |
| `store/locks.py` | `store.continuity.candidates` in `DOMAIN_MODULES` |
| `store/routing.py` | Continuity route gains `continuity-reconcile` and §29's hint |
| `store/revision.py`, `store/chores.py` | Docstrings |
| `templates/continuity_reconcile/{system,user}.j2` (new) | The reconcile prompt |
| `routes/runs.py` | `start_or_existing(single_live=)`, `reserve_campaign_background`, the registry's pending-touched map (`pend_touched`, `take_touched`, `drop_pending_touched`) |
| `routes/config.py` | `put_data_dir` drops pending touched refs after a move |
| `tests/fixtures/frozen_campaign/sweep.py`, `snapshot.json` | `continuity.candidates.records` and `continuity.pending.findings` keys |
| `routes/common.py`, `routes/scenes.py` | `_soft_connection` moves to `common`; `put_chronicle` trigger and merged-target refusal |
| `routes/continuity.py` | Run work, triggers, `AUTO_RECONCILE`; reconcile, candidates, apply, dismiss and suppression routes; `GET /continuity` suppressions gain `live`/`titles` |
| `routes/ledger.py` | `move_thread`, `move_commitment` |
| `routes/models.py` | `ContinuityDismiss` |
| `routes/todo.py` | `embeddings`, `continuity-overlaps`, `continuity-closures`; `unpriced` fix adopts `?section=pricing`; docstring |
| `routes/__init__.py` | Docstring row for the continuity router |
| `scripts/verify_templates.py`, `templates/README.md`, `docs/incoming-llm-capture.md` | Template honesty and docs |
| `tests/llm_fakes.py` (unchanged), `tests/fixtures/llm/campaign_flow.json`, `tests/test_llm_fakes.py` | Cassette entry and rendered prompt |
| `evals/cases.py`, `evals/graders.py`, `evals/recordings/continuity-reconcile.*.json`, `evals/README.md` | The `continuity-reconcile` case |
| `tests/test_continuity_writer_guard.py` (new), `CONTRIBUTING.md` | §11.6 guard and its table row |
| `CLAUDE.md` | Detached-run inventory |
| `frontend/src/api/types.ts`, `frontend/src/api/client.ts` | Continuity types and calls; delete `force` |
| `frontend/src/ledgerPaths.ts` (new) | Ledger addresses |
| `frontend/src/App.tsx` | `/campaigns/:cid/ledger/*` |
| `frontend/src/routes/LedgerView.tsx`, `frontend/src/components/ledger/LedgerRowEditor.tsx` | Path-driven sections, highlight, force delete, Merged link, the Continuity column section |
| `frontend/src/components/continuity/{useContinuityReview.ts,ContinuityReview.tsx,CandidateDetail.tsx,ReviewedGroup.tsx,DismissedGroup.tsx}` (new) | Reads, refresh, lists, detail, actions |
| `frontend/src/routes/ConfigView.tsx` | `?section=` |
| `frontend/src/index.css` | Highlight and continuity rules |

---

### Task 1: `continuity.candidates` — the cache file

**Files:**
- Create: `store/continuity/candidates.py`
- Modify: `store/locks.py` (`DOMAIN_MODULES`, with a comment in the `continuity.doc` style), `store/continuity/__init__.py` (docstring bullet)
- Test: `tests/test_continuity_candidates.py` (new; `client` and `cid` fixtures copied from `tests/test_continuity_doc.py`)

**Interfaces:**
- Produces:
  - `VERSION = 1`; `KINDS` (the four, in §6.1 order); `PAIR_KINDS = KINDS[:2]`; `LIFECYCLE_KINDS = KINDS[2:]`.
  - `empty() -> dict`: `{"version": 1, "generated": "", "generation": "", "basis": {"embedding_space": "", "embedding_model": "", "identity_hashes": {}, "scored": {}}, "records": {}}`.
  - `read(cid) -> dict`. Tolerant, with `empty()`'s keys.
    - Absent, unparseable, `OSError`, `UnicodeDecodeError` or a non-dict top level give `empty()`.
    - A record is kept only when it is a dict with `kind ∈ KINDS`, a `refs` list of str of length 2 (pair kinds) or 1 (lifecycle kinds) where each passes `_split` with prefixes that fit the kind (`possible_thread_closure` `[thread]`; `possible_commitment_resolution` `[commitment]`; `possible_duplicate` two of one type in thread/commitment; `possible_relation` thread+commitment or commitment+event), and a str `fingerprint`.
    - `signals` that is absent or not a dict reads `{}`; `created` that is not a str reads `""`.
    - `proposal` is normalized: a non-dict reads `None`; otherwise `decision`, `from`, `to`, `relation`, `status` and `reason` must be str (absent reads `""`) and `evidence_scenes` a list of str (absent reads `[]`), or the whole proposal reads `None`. Every consumer downstream (`pending.settled`, `is_temporal`, `rows`, the apply evidence loop, the Todo builders) can then index without guarding.
    - Non-str `generation`/`generated` read `""`, and a non-dict `identity_hashes` or `scored` reads `{}` (non-str values inside either are dropped).
  - `malformed(cid) -> bool`: the file exists and is unparseable or not a dict.
  - `records(cid) -> list[dict]`: `[{"id": k, **v}]` sorted by id (spec §7.0's projection).
  - `write(cid, data: dict) -> None`: under `locks.campaign_lock(cid)`, `atomic.write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")`. It overwrites a malformed file, because the cache is rebuildable (§6).
  - `drop(cid, candidate_id) -> dict | None`: under the lock. It returns the popped record, and makes no write when the id is absent or the file is malformed.
  - A private `_split(ref) -> tuple[str, str] | None`: `ref.partition(":")`, `None` unless it is a str with a non-empty prefix and id. This is the same rule as `canon.split_ref`, kept local so the module stays a leaf (Global Constraints, Imports).

- [ ] **Step 1: Write the failing tests:**
  - `test_absent_cache_reads_empty`: `read(cid) == empty()`, `records(cid) == []`, `malformed(cid) is False`.
  - `test_unparseable_cache_reads_empty_and_is_overwritable`: `"{ no"` gives empty and `malformed is True`; `write(cid, {...})` succeeds and reads back.
  - `test_malformed_records_are_skipped_others_kept`: records `{"a": [], "b": {"kind": "bogus", ...}, "c": {"kind": "possible_duplicate", "refs": ["thread:mara-s-map"], ...}, "e": <a pair with refs ["x", "y"]>, "f": <a closure whose ref is "commitment:mara-s-oath">, "d": <valid pair>}` read as only `d`.
  - `test_bad_nested_fields_are_normalized`: `signals: []` reads `{}`; `proposal: {"decision": 3, "evidence_scenes": "012"}` reads `None`; a proposal with only `decision` reads with `""`/`[]` defaults.
  - `test_written_json_is_sorted_and_newline_terminated`.
  - `test_drop_returns_the_record_and_absent_writes_nothing`: the file's mtime is unchanged after dropping a missing id.
  - `test_records_projection_carries_ids_sorted`.
  - `test_candidates_is_a_leaf`: parse `candidates.py`'s AST. Its only `grimoire` imports are `atomic`, `locks` and `campaigns.paths` (§7.0 "A leaf … as `doc`").
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_candidates.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement `candidates.py`.** The path is `campaigns_paths.campaign_root(cid) / "continuity_candidates.json"`. The reader is modelled on `doc.read`. The private `_write` does not lock (doc.py's reason: a lock-taking helper reads to the lock-domain guard as an atomic unit). Add `"store.continuity.candidates"` to `DOMAIN_MODULES`, and the docstring bullet: "``candidates`` -- the derived candidate cache (`continuity_candidates.json`); rebuildable, never journalled."
- [ ] **Step 4: Run the tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_candidates.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py -q -p no:cacheprovider`. Expected: PASS (`store.continuity` is already in the facade; nothing new is exported). Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): candidate cache with tolerant reader and locked writer`

---

### Task 2: `continuity.pending` — current fingerprints and verdicts, defined once

**Files:**
- Create: `store/continuity/pending.py`
- Modify: `store/continuity/effective.py` (`later_scene`), `store/continuity/__init__.py` (docstring), `tests/fixtures/frozen_campaign/sweep.py` (two keys), `tests/fixtures/frozen_campaign/snapshot.json` (regenerated deliberately)
- Test: `tests/test_continuity_pending.py` (new), `tests/test_frozen_campaign.py`

**Interfaces:**
- Consumes: `candidates.read`, `canon.pair_fingerprint` / `lifecycle_fingerprint` / `split_ref`, `doc.read`, `effective.Ledgers`, `effective.records`, `effective.live_canon`, `effective.links`, `effective.is_live`.
- Produces:
  - `effective.later_scene(stored: str, other: str) -> str`. When both parse (`scene_ids.parse_sid`), it returns the higher `number`. When only one parses, that one. When neither, `stored` if non-empty, else `other`.
  - `ROW_BEATS = 3`, `GROUP_OF`, `CHORE_OF` (the Global Constraints table), `VISIBLE = frozenset({"live", "stale"})`, `VERDICTS = ("unknown", "gone", "satisfied", "suppressed", "stale", "settled", "live")`, `TEMPORAL_RELATIONS = ("before", "on", "after", "by")`.
  - `class Current` (plain). Attributes:
    - `records: dict[str, dict]` (every effective thread and commitment record by canonical ref, closed and resolved included);
    - `events: dict[str, dict]` (`"event:<id>" → {"title", "date"}`);
    - `live: dict[str, str]`;
    - `links: list[dict]`;
    - `suppressions: dict`;
    - `unreadable: frozenset[str]` (prefixes `thread`/`commitment`/`event`);
    - `continuity_malformed: bool`: `set(doc.malformed(cid)) & {"file", "aliases", "links", "suppressions"}` is non-empty.

    `Current.load(cid, ledgers: effective.Ledgers | None = None) -> Current` loads `effective.Ledgers` once (or takes the caller's) and reuses it for `live_canon` and `links`. `unreadable` maps `Ledgers.unreadable` (`plot` → `thread`, `commitments` → `commitment`, `events` → `event`; `events.read` answers a corrupt file with `{}`, which `Ledgers` already reports), plus the prefix of any `effective.records` call that raises.
  - `is_temporal(record) -> bool`: kind `possible_relation` with an `event:` ref.
  - `side(current, ref) -> dict | None`: Decision 3's side, or `None` when the ref is not a current canonical record.
  - `fingerprint(current, kind, refs) -> str | None`: Decision 3. `None` when a side is missing, and also when `canon` raises `UnicodeEncodeError` (a lone surrogate in a title), caught per record so one bad title costs one finding (`unknown`), never the GET. Slice A's `canon._digest` is not changed.
  - `evidence_ok(proposal: dict | None, scene_ids: set[str]) -> bool`: true when there is no proposal or every `evidence_scenes` id is in `scene_ids`. Used by persist 1 (void proposals), the GET (`stale_reason "evidence"`) and apply. Todo does not call it, since it takes no scene list.
  - `DECISION_LABELS` (Decision 25).
  - `satisfied(current, kind, refs) -> bool` and `settled(record) -> bool` (Decision 9: a `proposal` exists and `signals.get("reason") == "touched"` with decision `keep_open|uncertain`, or `is_temporal` with decision `unrelated|uncertain`).
  - `verdict(current, record) -> str`: Decision 2's first match.
  - `rows(current, refs, *, scene_title=lambda sid: "", pressure: dict | None = None) -> list[dict]`: one row per ref, `{ref, kind, title, status, latest_beat, last_scene: {id, title}, pressure, aliases, due, beats}`, where `beats` is the last `ROW_BEATS = 3` merged beats as `{scene, text}`. An event row has `status ""`, `due` = the date, and `beats []`. A ref that is not a current canonical record gives `{ref, kind, title: the physical record's stored title from the `Ledgers` that `Current` holds (the ref when there is none), status: "", gone: True}` with empty fields, never an exception.
  - `findings(cid, current: Current | None = None) -> list[tuple[str, dict, str]]`: `(candidate id, cached record, verdict)` for every cached record, sorted by id. It returns `[]` **without loading** `Current` when the cache has no records. Each record's verdict is computed under a narrow per-record guard (`AttributeError`, `TypeError`, `ValueError`, `UnicodeEncodeError`), which yields `gone`, so one record no normalization foresaw cannot 500 the GET or the global To do page (§26 "no campaign failure").
  - `label(current, refs) -> str`: the titles joined with `" / "`, a ref standing in for a missing title.

- [ ] **Step 1: Write the failing tests.** Fixture: Realm/Run with threads "Mara's map" and "Winifred's chart" and the commitment "Mara's oath", set through `plot.set_movement` / `commitments.set_movement`.
  - `test_later_scene_by_play_order` (parametrized): `("003--a", "005--b") → "005--b"`, `("005--b", "003--a") → "005--b"`, `("", "003--a") → "003--a"`, `("x", "y") → "x"`.
  - `test_pair_fingerprint_ignores_a_new_beat` (§28.1): a beat added to one side leaves `fingerprint` unchanged.
  - `test_lifecycle_fingerprint_changes_with_the_latest_beat` (§28.1).
  - `test_a_scene_rename_does_not_move_a_lifecycle_fingerprint` (§28.1): `scene_refs.repoint` the beat's scene; equal.
  - `test_canonicalized_aliases_give_a_stable_pair_fingerprint` (§28.1): after aliasing a third thread into one side, the pair's fingerprint is unchanged.
  - `test_temporal_side_changes_when_the_event_moves`: rescheduling the event changes a temporal pair's fingerprint.
  - `test_verdicts` (parametrized over constructed records): `gone` (a deleted ref; an alias source; a pair now aliased together), `satisfied` (a pair with a `related_to` link either way; a closed lifecycle target), `suppressed`, `stale`, `settled`, `live`, `unknown` (plot.json `"{ no"`; events.json `"{ no"` for a temporal pair; continuity.json `"{ no"` for every kind; a thread titled with a lone surrogate, `json.loads('"Mara\\ud83d"')`).
  - `test_findings_skips_the_ledgers_when_the_cache_is_empty`: monkeypatch `effective.Ledgers.load` to raise; `findings(cid) == []`.
  - `test_findings_survives_a_record_nothing_normalized`: monkeypatch `verdict` to raise `TypeError` for one id; that id reads `gone`, the others keep their verdicts.
  - `test_evidence_ok`: no proposal, all-known and one-unknown evidence ids.
  - `test_rows_for_a_gone_ref_do_not_raise`.
  - `test_rows_join_current_records`: titles, statuses, `aliases` and `beats` come from the effective record. `last_scene.title` comes from `scene_title`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_pending.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `pending.py` and `effective.later_scene`. The module docstring states Decision 1, and that Todo calls `findings` (no involvement, chronicle, calendar or embedding work).
- [ ] **Step 4: Run the tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_pending.py tests/test_continuity_effective.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Add the cache projections to the frozen sweep** (§7.0's required `candidates.records`, §27 "the frozen sweep gains the continuity projections"). In `tests/fixtures/frozen_campaign/sweep.py`, after Slice B's `continuity.*` keys, add `continuity.candidates.records[{cid}]` (`candidates.records(cid)`) and `continuity.pending.findings[{cid}]` (`[list(t) for t in pending.findings(cid)]`), importing `candidates` and `pending` beside B's continuity imports. The fixture's `home/` has no `continuity_candidates.json`, so both read `[]`. That is the proof that an old store with no cache reads as empty.
  - Regenerate deliberately, from `backend/`: `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`.
  - Prove the regeneration only added keys, with B Task 10 Step 4's script narrowed to this slice's prefixes: `assert added and all(k.startswith(("continuity.candidates.", "continuity.pending.")) for k in added), added`, plus its unchanged-values check for every other key.
  - Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q -p no:cacheprovider`. Expected: PASS, including `test_the_read_only_sweep_writes_nothing` (neither reader may create the cache file).
- [ ] **Step 6: Commit:** `feat(continuity): one definition of what a cached finding means now`. The commit body notes that the snapshot was regenerated deliberately and only gained keys.

---

### Task 3: `continuity.reconcile` — deterministic discovery

**Files:**
- Create: `store/continuity/reconcile.py` (discovery half)
- Test: `tests/test_continuity_reconcile.py` (new)

**Interfaces:**
- Consumes:
  - `similarity.pool`, `lexical`, `with_cosine`, `plausible`, `admitted_by`, `rank_key`, `IDENTITY_TOP_K` (Slice C);
  - `pressure.build` (Slice B);
  - `aging.prepare`, `aging.age`, `clock.now`;
  - `effective.threads`, `effective.commitments`, `effective.links`, `effective.live_canon`;
  - `pending.Current`, `pending.fingerprint`;
  - `canon.candidate_id`;
  - `candidates.read`.
- Produces:
  - The constants of Decision 14, plus `SWEEPS = ("full", "incremental")`.
  - `generation(run_id: str) -> str` (Decision 4).
  - `class Sweep` (plain). Attributes:
    - `stamp`, `full: bool`;
    - `discovered: dict[str, dict]` (id → cache record, `proposal None`);
    - `model_only: dict[str, dict]` (Decision 9);
    - `rescored: frozenset[str]`, `hashes: dict[str, str]`;
    - `lifecycle_checked: dict[str, bool]` (`stale`, `overdue`, `temporal`; Decision 11);
    - `temporal_ids: frozenset[str]` (every temporal pair nominated, before the verdict filter; Decision 11);
    - `continuity: Literal["ok", "malformed"]`: with `"malformed"` (continuity.json's file, aliases, links or suppressions malformed), `discover` returns no new pair or lifecycle record and `select` sends nothing (Decision 2);
    - `space: str`, `model: str`;
    - `embedding: Literal["off", "configured", "failure"]`, `embedding_error: str`;
    - `pairs_scored: int`, `pairs_capped: bool`;
    - `matching: str`.
  - `discover(cid, *, stamp: str, full: bool, touched: Iterable[str] = (), embed: bool = True) -> Sweep`. It never raises for an existing campaign: each of pools, aging and pressure is read through one `_soft(fn, fallback, *args)` helper (B's pattern, the module's only `noqa: BLE001`). `embed=False` skips Task 4's embedding step (mode `off`).
  - `pressure_by_ref(cid) -> dict[str, dict]`: `{ref: {state, in_days, friendly}}`. For each live canonical commitment it is the most urgent deadline item (by `pressure.PRESSURE_STATES` index). For each live thread it is `{"state": "stale"|"ok", "in_days": None, "friendly": ""}` from aging. It is soft, and `{}` on failure. Discovery and the candidates GET both use it.
- [ ] **Step 1: Write the failing tests.** Seed records with `plot.set_movement` / `commitments.set_movement` in a scene `s0 = scenes.create_scene(cid, "Saltmarch docks")`. Reuse C's `tests/review_runs.LEDGER_THREAD` and `RECOVER_THE_LEDGER` texts for the lexically close pair, and `SALTMARCH_TITHE` for the far one. Every candidate test first asserts the deciding signal, e.g. `assert similarity.lexical(a, b)["tokens"] >= similarity.TOKEN_FLOOR`.
  - `test_close_same_type_records_become_a_possible_duplicate`: the id is `canon.candidate_id("possible_duplicate", [...])`, the signals have Decision 10's keys, and `proposal is None`.
  - `test_thread_and_commitment_overlap_is_a_relation_never_a_duplicate` (§28.4): an identical title across types gives exactly one `possible_relation` and no `possible_duplicate`.
  - `test_far_records_are_not_paired`.
  - `test_incremental_pairs_only_changed_records_against_all`: write a cache whose basis hashes match every record but one. Monkeypatch `similarity.lexical` with a counter; every scored pair includes the changed ref, and `sweep.rescored == {changed}`.
  - `test_a_full_sweep_scores_every_unordered_pair_once`: n records give exactly n(n−1)/2 `lexical` calls.
  - `test_a_capped_sweep_reaches_the_rest_next_time` (Review Focus 3): five threads `r1..r5` (refs in that order), `reconcile.RECONCILE_MAX_PAIRS = 5`, and a basis in which **every** ref already has a current hash and the same old `scored` stamp. Full sweep 1 gives `pairs_capped is True` and `sweep.rescored == {r1}` (its four pairs fit; the fifth pair does not finish `r2`). Build the basis as persist 1 would (`hashes` and `scored` for `rescored` only) and run full sweep 2: its first scored ref is `r2`. Sweep 3 starts at `r3`, and the union of `rescored` over five successive sweeps is all five refs. An implementation that orders by ref once every entry exists fails at sweep 2.
  - `test_a_capped_sweep_after_a_space_change_reaches_the_tail`: a basis holding every ref's hash under space `"old"`, then an **incremental** sweep under space `"new"` capped at 5 gives `rescored == {r1}`. The next incremental sweep (r1's new hash recorded) still treats `r2..r5` as changed and scores `r2` first.
  - `test_touched_includes_refs_moved_since_the_last_sweep`: a basis entry for "Mara's map" that differs from its current hash puts it in `model_only` with `reason "touched"` on an incremental sweep with `touched=()`; a ref with **no** entry does not.
  - `test_a_lone_surrogate_title_is_skipped_not_fatal`: a thread titled `json.loads('"Mara\\ud83d"')` beside two close threads; the sweep completes and finds the close pair.
  - `test_a_changed_embedding_space_rescores_everything`.
  - `test_a_stale_thread_is_nominated_for_closure_not_closed` (§28.4): clock and scene dates past `stale_after` give `possible_thread_closure` with `signals["reason"] == "stale"`, and `plot.get(...)["status"]` is unchanged.
  - `test_a_passed_deadline_nominates_but_does_not_resolve` (§28.4): a due before `clock.now` gives a `possible_commitment_resolution` with `reason "overdue"`, and the status stays `open`.
  - `test_a_passed_linked_commitment_is_nominated`: a `before` link to a fired event (B's reached rule) nominates it with `via == "linked_deadline"`.
  - `test_touched_records_are_model_only`: `touched=["thread:mara-s-map"]` puts it in `model_only` and not in `discovered`.
  - `test_temporal_pairs_need_an_unparseable_due_and_a_readable_calendar`:
    - "before the bells stop" plus an event 3 days out gives a model-only temporal pair;
    - a parseable due gives none;
    - an existing `before` link gives none;
    - a raising calendar plugin (B's broken-plugin fixture) gives none;
    - more than `RECONCILE_TEMPORAL_EVENTS` events keep the nearest.
  - `test_discovery_survives_a_garbled_ledger_and_a_raising_plugin`: plot.json `"{ no"` plus B's raising plugin gives a `Sweep` with no thread candidates, `lifecycle_checked == {"stale": False, "overdue": False, "temporal": False}`, and no exception.
  - `test_model_only_nominations_are_verdict_filtered` (Decision 11): a temporal pair whose fingerprint is suppressed, a temporal pair joined by a `related_to` link, and a touched closure re-check under a `keep_open` suppression whose lifecycle fingerprint has not moved are all absent from `sweep.model_only`. The two temporal ids are still in `sweep.temporal_ids`.
  - `test_a_malformed_continuity_file_discovers_nothing`: continuity.json `"{ no"` with two close threads gives `continuity == "malformed"` and no discovered records.
  - `test_discovery_makes_no_store_write`: snapshot the campaign directory's file mtimes before and after; they are equal. `discover` is read-only; the persists write.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** the discovery half per Decisions 10 and 11. Pairs are scored by private `_score_pairs(to_score, subjects, vecs, budget) -> tuple[dict, frozenset, int, bool]`, and lifecycle and temporal nominations by `_lifecycle(cid, current, touched)` and `_temporal(cid, current)`, each ≤ complexity 10. The module docstring states §3.2 (scores rank, never write), Decision 9, and the constants' justifications.
- [ ] **Step 4: Run the tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): deterministic reconciliation discovery`

---

### Task 4: Embeddings in the sweep (§9.4 sweep half; C backlog (c))

**Files:**
- Modify: `store/continuity/reconcile.py`, `store/continuity/similarity.py` (`semantic` gains `warm_limit`)
- Test: `tests/test_continuity_reconcile.py`, `tests/test_continuity_similarity.py`

**Interfaces:**
- Consumes: `similarity.available`, `similarity.semantic`, `vectors.load`, `embed_space.warm_window`, `embeddings.BATCH`/`TIMEOUT`, and `llm_fakes.FakeEmbeddings` (installed as `monkeypatch.setattr(similarity, "_CLIENT", fake)`; configured as in `test_context_semantic.configure`).
- Produces:
  - `similarity.semantic(required, warm, *, deadline, space=None, cached=(), warm_limit: int = IDENTITY_WARM_LIMIT, loaded: dict | None = None) -> Semantic`. `warm_limit` replaces `IDENTITY_WARM_LIMIT` in its step 3 and step 7. A given `loaded` replaces its step-2 `vectors.load` (the caller already read those texts). Nothing else changes.
  - `discover` fills `embedding`, `embedding_error`, `space` and `model` per Decision 12, and applies `with_cosine` in pair scoring.
- [ ] **Step 1: Write the failing tests:**
  - `test_semantic_warm_limit_defaults_to_identity` (similarity): 40 uncached warm texts and no `required`. With no `warm_limit`, exactly `IDENTITY_WARM_LIMIT` are embedded (C's behaviour, unchanged). With `warm_limit=40`, `len(fake.calls[0]) == 40`.
  - `test_sweep_embeds_at_most_the_warm_limit` (§28.2): monkeypatch `RECONCILE_WARM_LIMIT = 4` over ten uncached records; the total texts sent are ≤ 4, and a spy on `vectors.load` records exactly **one** call per sweep (§25.2 "loads cached vectors once").
  - `test_semantic_uses_a_given_loaded_map` (similarity): with `loaded={}` passed, `vectors.load` is not called.
  - `test_incremental_changed_texts_are_required`: the changed record's text is in the first embed call even when the window would omit it.
  - `test_warm_window_rotates_between_runs`: two sweeps with **literal** stamps passed to `discover(..., stamp=...)` over the same ten uncached texts. The test first asserts that `zlib.crc32(f"{cid}\0{stamp}".encode()) % 10` differs for its two stamps (choosing them so), then that the two windows differ. A spy asserts the seed passed to `warm_window` starts with the cid. Stamps from `reconcile.generation` would collide one run in ten.
  - `test_per_chunk_saves_survive_a_later_failure` (§28.2): `monkeypatch.setattr(embeddings, "BATCH", 2)` with `FakeEmbeddings(error=EmbeddingsError("network", "x"), fail_after=1)` gives mode `failure`, and the first chunk's vectors in `vectors.load`.
  - `test_embedding_failure_keeps_lexical_candidates` (§26): lexical candidates are present, `embedding == "failure"`, and one error row has `task == "continuity-reconcile"` and no title in any field.
  - `test_off_width_vectors_are_forgotten` (§28.2, §26 width): a 3-wide cached vector among 2-wide fresh ones is forgotten.
  - `test_cosine_admits_a_pair_with_no_shared_words`: `vector_for` maps both texts to one vector, giving a candidate with `signals["via"] == "semantic"` and a float `cosine`.
  - `test_unconfigured_makes_no_embedding_call`: `fake.calls == []` and `matching == "basic"`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile.py tests/test_continuity_similarity.py -q -p no:cacheprovider`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Same command. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): bounded, rotating embeddings for the reconciliation sweep`

---

### Task 5: Prompt family, parser and adjudication selection

**Files:**
- Create: `templates/continuity_reconcile/system.j2`, `templates/continuity_reconcile/user.j2`
- Modify: `store/continuity/reconcile.py`, `scripts/verify_templates.py`, `templates/README.md`, `docs/incoming-llm-capture.md`, `tests/fixtures/llm/campaign_flow.json`, `tests/test_llm_fakes.py`
- Test: `tests/test_continuity_reconcile_prompt.py` (new), `tests/test_llm_fakes.py`

**Interfaces:**
- Produces:
  - `DECISIONS = {"same_thread": (...), "same_commitment": (...), "cross": (...), "thread": (...), "commitment": (...), "temporal": (...)}`, the Global Constraints vocabulary.
  - `vocabulary(record) -> str`: the key into `DECISIONS` (a `possible_duplicate` gives `same_thread` or `same_commitment` by its refs' type).
  - `select(cid, sweep) -> list[dict]`: Decision 13's priority and cap over the cache **as persist 1 left it**, plus the `sweep.model_only` entries that are not cached with a proposal and whose `pending.verdict` against one `pending.Current` loaded for the selection is `live`. Each item is `{id, kind, refs, signals, fingerprint}`.
  - `build_payload(cid, selected) -> dict`: `{"now": str, "chronicle": [{id, one_line}], "candidates": [{key: "c1"…, id, vocabulary, records: [{letter: "A"|"B", ref, line, beats: [{scene, text}], pressure, links: [str], actors: [str]}], signal_text}], "known_scenes": sorted list}`. Candidate keys follow selection order. The snippet lines are rendered by Python.
  - `template_vars(payload) -> dict` and `build_prompt(payload) -> list[dict]`: `[system, user]` via `prompts.render("continuity_reconcile/system.j2")` and `prompts.render("continuity_reconcile/user.j2", **template_vars(payload))`.
  - `parse_output(text, payload) -> dict[str, dict] | None`:
    - `None` iff `absorb_parse.extract_object(text) is None` (§24);
    - otherwise `{candidate id: proposal}` for each well-formed decision;
    - `candidate` is normalized like identity's `row` (strip, casefold, a leading `candidate` word removed), and the first decision per key wins;
    - an unknown key is dropped; an unknown decision for its vocabulary is `uncertain`;
    - `from`/`to` letters map to refs, and a missing or disallowed direction is `uncertain` (Global Constraints). A word outside its candidate's vocabulary (`continuation` or `subthread` on a `same_commitment` pair, `duplicate` across types) is `uncertain`, and so is any `relation` that `effective.RELATIONS` refuses for `(from, to)`, so no cached proposal names a link `create_link` would reject;
    - evidence scenes outside `known_scenes` are dropped, and the evidence floor applies (§11.4);
    - the reason is clipped to `RECONCILE_REASON_CHARS`.

    Proposal fields (§6):
    - `duplicate`: `relation ""`;
    - `continuation`: `"continues"`; `subthread`: `"subthread_of"`; `related`: `"related_to"`; `pays_off`: `"pays_off"`;
    - temporal words: `relation` = the word, `from` = the commitment, `to` = the event;
    - `close`: `status "closed"`; resolutions: `status` = the word.
    - Nothing raises on bad JSON.
- System prompt (static). It opens with the unique phrase **`You are reviewing a campaign's story ledger for records that may overlap or be finished`**, and contains none of the absorb, audit or identity openers. Each decision instruction carries one unique phrase, which the eval needles pin:
  - `"duplicate" only when both records are the same question or obligation`;
  - `a narrower or later question is "continuation" or "subthread", not "duplicate"`;
  - `A thread and a commitment are never "duplicate"`;
  - `Age alone is never evidence that a thread is finished`;
  - `A passed deadline alone is never evidence that a promise was kept or broken`;
  - `name at least one evidence scene id from the lines shown`;
  - `Do not invent a date`.

  It states the reply shape: ONLY `{"decisions": [{"candidate": "<key>", "decision": "<word>", "from": "A"|"B", "to": "A"|"B", "reason": "<one short sentence>", "evidence_scenes": ["<scene id>"]}]}`, and lists every vocabulary with each word in double quotes.
- `user.j2`: a `{#- … -#}` variable header; `Campaign date: …`; `Recent scenes:` (`<id> — <one_line>`); then per candidate `Candidate <key> — <kind label> (answer with: <words>)`, each record as `<letter>: <line>` with its beats (`[<scene>] <text>`), pressure, links and `people: <names>`, and `signals: <signal_text>`. Every optional piece is guarded on keys always passed.
- `campaign_flow.json` entry: `{"when": {"system_contains": "You are reviewing a campaign's story ledger for records that may overlap or be finished"}, "reply": "{\"decisions\": []}"}`.
- [ ] **Step 1: Write the failing tests** (`tests/test_continuity_reconcile_prompt.py`, building payloads through `select` / `build_payload` over a seeded campaign):
  - `test_parse_undecodable_is_none`: `parse_output("I think so.", payload) is None`.
  - `test_parse_decodable_empty_is_empty`: `"{}"` and `'{"decisions": 4}'` give `{}`.
  - `test_cross_type_duplicate_is_uncertain` (§11.3).
  - `test_disallowed_direction_is_uncertain`: `pays_off` with `from` = the commitment; `subthread` on commitments; `continuation` on commitments; `duplicate` with `from == to`.
  - `test_actors_are_sent_per_record`: a closure candidate's record carries its involvement actors' names (Mara), rendered in `user.j2`; an event record carries none.
  - `test_closure_without_known_evidence_is_uncertain` (§28.4 "closure requires a known evidence scene"): an unknown scene id is dropped, which gives `uncertain`; a known id plus a reason gives `close`.
  - `test_unknown_candidate_keys_and_enums_are_dropped_or_uncertain`.
  - `test_select_prioritizes_overdue_then_duplicates_and_caps` (§28.4 "capped and prioritized"): with `RECONCILE_MAX_CANDIDATES = 2`, one overdue resolution, two duplicates and one stale closure, selection is the overdue one, then the stronger duplicate.
  - `test_select_skips_cached_proposals` (§28.4 "only for candidates without a cached proposal").
  - `test_select_skips_suppressed_and_satisfied_model_only_nominations` (§11.1 step 3 "no cached proposal and no suppression"): a commitment with due "before the bells stop", plus two events in the horizon. Dismiss one temporal pair (`doc.put_suppression` with its current fingerprint) and join the other with a `related_to` link (`doc.put_link`). Then run two successive sweeps through `discover` → `persist_found` → `select` → `build_payload` → `build_prompt`. Neither pair is ever in the selection, and no rendered reconcile prompt names either event. A third, untouched temporal pair is selected, which proves the test can see one.
  - `test_known_scenes_are_the_shown_beats_and_chronicle_lines` (§11.2, §11.4).
  - `test_no_transcript_text_is_sent` (§11.2): a scene's post text appears nowhere in the rendered prompt.
  - `test_build_prompt_renders_with_no_optional_fields`.
  - In `tests/test_llm_fakes.py`: add `prompts.render("continuity_reconcile/system.j2")` to `_rendered_prompts()`, and `test_the_reconcile_body_is_the_shape_the_parser_expects` (the cassette reply parses to `{}`).
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile_prompt.py tests/test_llm_fakes.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** the templates, the functions and the cassette entry.
  - `scripts/verify_templates.py`: a pure block before `# --- store fixture`, importing `from grimoire.store.continuity import reconcile  # noqa: E402` on its own line. For three representative payloads (`pairs+lifecycle` with every optional field, `bare`, `temporal`) it checks the system and user renders against `build_prompt`, and each record `line` against the snippet render.
  - `templates/README.md`: a `### continuity_reconcile/ — the reconciliation sweep after End Scene or a refresh` section after `continuity_identity/`. It mirrors `store/continuity/reconcile.py:build_prompt` and documents the variables, the reply shape, parsing through `absorb.extract_object`, that undecodable means a failed run with deterministic findings kept, and the §29 sentence: replies, including each `reason`, are captured at Debug level under the existing Settings disclosure, while the info-level log row carries counts and modes only.
  - `docs/incoming-llm-capture.md`: the same §29 sentence, naming `continuity-reconcile`.
- [ ] **Step 4: Run the tests.** Same command. Expected: PASS.
- [ ] **Step 5: Run the template, docs and lint checks.** `make check-templates check-lint check-mypy PY=$PWD/backend/.venv/bin/python`, and `PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): continuity_reconcile prompt family and tolerant parser`

---

### Task 6: The two persists

**Files:**
- Modify: `store/continuity/reconcile.py`, `store/revision.py` (docstring list), `CLAUDE.md` (the write-token paragraph)
- Test: `tests/test_continuity_reconcile.py`, `tests/test_revision.py`

**Interfaces:**
- Consumes: `candidates.read`/`write`, `pending.Current`/`verdict`/`fingerprint`, `revision.bump`, `campaigns_paths.campaign_exists`, `locks.campaign_lock`.
- Produces:
  - `persist_found(cid, sweep, *, stillborn: Callable[[], bool] = lambda: False) -> dict`.
  - `persist_proposals(cid, sweep, proposals: dict[str, dict], *, stillborn=...) -> dict`.
  - Both return `{"written": bool, "superseded": bool, "gone": bool, "cancelled": bool, "continuity": "ok"|"malformed", "candidates": int}`. `candidates` counts records written with verdict `live`. Both follow Decisions 4, 7 and 8, and the cache's `generated` is `paths.now_iso()`.
  - `revision.py`'s "Everything a DETACHED run writes bumps where it writes" list names `store.continuity.reconcile.persist_found` and `persist_proposals`: the reconcile's two persists, whose route answered 202 or was a save.
  - `CLAUDE.md`'s write-token paragraph ("A campaign carries a write token…"), whose sentence on what a *detached* run writes names `_under_review_lock`, `_rolling_commit`, `_break_commit` and `_turn_settled`, gains the reconcile sweep's two persists, in its own words (not `revision.py`'s, `test_no_document_restates_another`): they land after a 202 or after a save already answered, and one that changes nothing stamps nothing.
- [ ] **Step 1: Write the failing tests:**
  - `test_persist_writes_discovered_and_bumps_the_token`: `store.revision.current(cid)` moves, and the file holds the records with `generation == sweep.stamp`.
  - `test_a_persist_after_a_dismiss_does_not_resurrect` (§28.4): discover; `doc.put_suppression` with the candidate's fingerprint; persist; the candidate is absent.
  - `test_a_persist_after_a_merge_drops_the_pair` (`gone`) and `test_a_persist_after_a_link_drops_the_pair` (`satisfied`).
  - `test_a_record_changed_since_discovery_is_dropped`: a title changes between discover and persist.
  - `test_cached_proposals_carry_forward_when_the_fingerprint_holds`, and `…_are_dropped_when_it_moved`.
  - `test_a_newer_generation_supersedes_an_older_run` (§28.4 "a superseded run does not overwrite a newer cache"): stored stamp `B > A`, with `B <= now`. Persisting A gives `superseded True` and the file is unchanged.
  - `test_a_future_dated_generation_does_not_fence_forever` (Review Focus 2): a stored `generation` of `f"{time.time_ns() + 3_600 * 10**9:020d}-x"` lets the persist write.
  - `test_persist_two_never_resurrects_an_applied_candidate`: persist 1; `candidates.drop`; `persist_proposals` with a proposal for it leaves it absent.
  - `test_persist_two_adds_model_only_with_a_proposal_and_marks_declined_settled`.
  - `test_stillborn_and_deleted_campaign_write_nothing`: `stillborn=lambda: True` gives `cancelled`. A deleted campaign directory gives `gone`, and no directory is recreated.
  - `test_basis_keeps_old_hashes_for_unscored_refs`: a capped sweep records `identity_hashes` and `scored` for `rescored` only, and every other existing ref keeps both old entries.
  - `test_embedding_failure_keeps_prior_semantic_pairs_and_rescores_next_time` (§26, Decision 10): a cached `via == "semantic"` pair for "Mara's map"; the map changes; the embedding call fails. The pair is retained, the map's basis entry is unchanged, and the next sweep with a working fake scores it.
  - `test_warm_overflow_refs_are_not_marked_scored`: `RECONCILE_WARM_LIMIT = 2` over five changed uncached refs; the three without a vector keep their old (absent) entries and are scored on the next sweep.
  - `test_a_lifecycle_finding_is_retracted_when_its_condition_clears`: a `stale` closure finding; rewind the clock so the thread is no longer stale; the next sweep's persist 1 removes it. Variant: the same with a raising calendar plugin keeps the finding (`lifecycle_checked["stale"]` False). A `touched` finding is kept.
  - `test_a_temporal_finding_is_retracted_when_its_condition_clears` (§11.3, Decision 8): a cached live temporal pair (written through `persist_proposals` with a `before` proposal). Variant (a): advance the clock past the event's date. Variant (b): `doc.put_link` a `before` link from the commitment to a **different** event. In each, the next sweep's persist 1 removes the finding. Variant (c): change nothing, but install B's raising calendar plugin before the next sweep (`lifecycle_checked["temporal"]` False). The finding is kept.
  - `test_a_renamed_evidence_scene_is_readjudicated_not_stuck` (§5.6): a cached closure proposal citing scene `s1`; `scene_refs.repoint` / rename `s1`; persist 1 sets the proposal to `null`, and `select` then picks the record.
  - `test_a_malformed_continuity_file_leaves_the_cache_untouched`: continuity.json `"{ no"`; both persists return `written False`, and the cache file is byte-identical.
  - `test_a_persist_that_changes_nothing_moves_no_token`: two identical sweeps; after the second, `revision.current(cid)` is unchanged and the file's mtime too.
  - `test_persists_hold_the_campaign_lock`: with the lock held by another thread, `persist_found` blocks (then raises `StoreBusy` under the test's short timeout) rather than writing.
  - `test_a_malformed_cache_is_overwritten`.
  - `test_revision.py`: `test_a_reconcile_persist_that_lands_moves_the_token` and `test_a_superseded_reconcile_persist_moves_nothing`, beside `test_a_follow_up_that_lands_moves_the_token`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile.py tests/test_revision.py -q -p no:cacheprovider`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, with a private `_commit(cid, sweep, build, stillborn)` holding the shared hold sequence (Decision 7).
- [ ] **Step 4: Run the tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile.py tests/test_revision.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): fenced reconcile persists that never resurrect a decision`

---

### Task 7: One live reconcile per campaign

**Files:**
- Modify: `routes/runs.py` (`Run.adopted_attempts`, `start_or_existing`, new `_live_single` and `_adopt`, `reap`, `_subject_runs`, new `reserve_campaign_background`; the pending-touched map: `RunRegistry.pend_touched`, `take_touched`, `forget_subject` clearing it, and module-level `drop_pending_touched(app)`), `routes/config.py` (`put_data_dir` calls `runs.drop_pending_touched(request.app)` after a successful move)
- Test: `tests/test_runs_registry.py`, `tests/test_campaign_background.py` (new, with the conftest `client`)

**Interfaces:**
- Produces:
  - `RunRegistry.start_or_existing(..., single_live: bool = False)`. This is Decision 5, inside `self._lock`, after attempt adoption and before the exclusion key, as one added branch calling `_live_single` and `_adopt`.
  - `Run.adopted_attempts: list[str]` (empty by default; appended only under the registry lock); `_subject_runs` and `reap` per Decision 5.
  - `reserve_campaign_background(app, cid: str, kind: str, attempt_id: str | None = None) -> tuple[Run, bool] | None`. It takes no campaign lock, uses `labels=_subject_labels(campaign_subject(cid))`, and passes `single_live=True` and `scene_identity=None`. `StoreMovingError` gives `None`. Its docstring cites `reserve_background`'s no-lock reason and spec §11.1.
  - The pending-touched map (Decision 6) is `RunRegistry._pending_touched: dict[Subject, set[str]]`, which lives on the per-app registry and is never module state:
    - `pend_touched(subject, refs: Iterable[str]) -> None` unions the refs in under `self._lock`;
    - `take_touched(subject) -> set[str]` pops the subject's set under `self._lock` (empty when there is none);
    - `forget_subject` also pops `_pending_touched[subject]`;
    - module-level `drop_pending_touched(app) -> None` clears the whole map under the lock. `put_data_dir` calls it after `store.set_data_dir` succeeds, beside `logs.forget_file_sizes()`.
- [ ] **Step 1: Write the failing tests:**
  - `test_single_live_returns_the_running_run` (registry): two `start_or_existing(("campaign", "c"), "background", "continuity-reconcile", None, None, labels, single_live=True)` calls return the same run, with `fresh` `True` then `False`.
  - `test_single_live_is_atomic_across_threads`: 20 threads start at once and get exactly one distinct run.
  - `test_single_live_ignores_other_kinds_terminal_forgotten_and_cancelling_runs`: a run with `cancel_requested = True` is not adopted; a fresh one is created.
  - `test_a_refresh_during_a_live_sweep_adopts_it_under_its_own_attempt` (Review Focus 4): the live run started with `attempt_id=None`; a second call with `"a1"` returns it, `run.adopted_attempts == ["a1"]`, and both `for_attempt` and the HTTP `GET /campaigns/{cid}/runs?attempt=a1` find it.
  - `test_two_draft_runs_are_each_found_only_by_their_own_attempt` (regression for the `_subject_runs` change).
  - `test_reap_drops_adopted_attempt_aliases`: reap the adopted run past `REAP_SECONDS`; `_by_attempt` holds neither its own key nor `(subject, "a1")`.
  - `test_reserve_campaign_background_maps_a_store_move_to_none`: inside `hold_still()` it returns `None`.
  - `test_pending_touched_is_taken_once_and_forgotten_with_the_subject` (registry): `pend_touched` twice unions the refs; `take_touched` returns them, and a second `take_touched` returns `set()`; after `pend_touched` then `forget_subject`, `take_touched` returns `set()`; `drop_pending_touched` empties every subject.
  - `test_a_storage_move_drops_pending_touched` (`test_campaign_background.py`): pend refs on the app's registry, `PUT /config/data-dir` to a new tmp root (200), and `take_touched` returns `set()`.
  - `test_background_runs_still_declare_no_key`: the existing `test_background_and_draft_declare_no_key` is unchanged.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_registry.py tests/test_campaign_background.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_registry.py tests/test_campaign_background.py tests/test_draft_runs.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`, and confirm `check-lint` reports no `C901` for `backend/src/grimoire/routes/runs.py` (`start_or_existing` was at 10 before this task).
- [ ] **Step 5: Commit:** `feat(runs): one live background run per campaign subject and kind`

---

### Task 8: The run, the routed call, and `POST /continuity/reconcile`

**Files:**
- Modify:
  - `routes/common.py`: `_soft_connection` moves here, verbatim with its docstring;
  - `routes/scenes.py`: imports it from `.common`;
  - `routes/continuity.py`;
  - `store/routing.py`;
  - `routes/__init__.py` (docstring row);
  - `tests/conftest.py`: `reconcile` marker and `_auto_reconcile_off_unless_asked`.
- Test: `tests/test_continuity_reconcile_routes.py` (new; conftest `client`, lifespan entered), `tests/test_revision.py`, `tests/test_routing_routes.py`, `tests/test_route_order.py`, `tests/test_path_guard_store.py`, `tests/test_continuity_routes.py`

**Interfaces:**
- Consumes: Task 3–7 producers; `runs.reservation`, `runs.start_computing`, `runs.release_before_start`, `runs.run_payload`; `_bounded_call`, `_noting`, `_llm_http_error`, `run_error`.
- Produces in `routes/continuity.py`:
  - `AUTO_RECONCILE = True` (Decision 20).
  - `_PERSIST_ATTEMPTS = 3`, `_PERSIST_BACKOFF = 0.5`.
  - `async def _persist(run, call: Callable[[], dict]) -> dict`: `run_in_threadpool` with `StoreBusy` retries; a final `StoreBusy` or `OSError` gives `{"written": False, "error": {kind: "busy"|"io", detail, status: 409|500}}`.
  - `async def _reconcile_work(run, cid, client, *, full: bool, touched: tuple[str, ...]) -> dict`:
    1. `stamp = reconcile.generation(run.id)`;
    2. `sweep = await run_in_threadpool(reconcile.discover, cid, stamp=stamp, full=full, touched=touched)`;
    3. persist 1 with `stillborn = lambda: run.cancel_requested or run.forgotten`. On `superseded`/`gone`/`cancelled` it returns landed (or `cancelled`) with the result.
    4. `conn, why = _soft_connection(lambda: _require_connection("continuity-reconcile", cid))`;
    5. `selected = await run_in_threadpool(reconcile.select, cid, sweep)`. No conn gives `llm "off"` with `reason why`; nothing selected gives `llm "skipped"`.
    6. Otherwise:

       ```
       payload = await run_in_threadpool(reconcile.build_payload, cid, selected)
       with store.usage.meter("continuity-reconcile", campaign=cid) as m:
           text = await _bounded_call(client.complete(reconcile.build_prompt(payload), conn, m.usage),
                                      on_timeout=_noting(client, conn, m.usage))
       ```

       `LLMError` gives `{"state": "failed", "error": {**run_error(_llm_http_error(exc)), "sweep": result["sweep"]}, "result": result}`. `parse_output` returning `None` gives `failed` with `{kind: "undecodable", detail: "the reconciliation check returned no readable answer", status: 502, sweep: …}`. Every failed outcome's `error` carries `sweep`, because the client's `awaitRunAt` keeps only `error` for a run that did not land (Decision 24). Otherwise persist 2.
    7. `store.logs.record("info", __name__, "continuity reconcile", kind="continuity-reconcile", campaign=cid, sweep=…, matching=…, embedding=…, embedding_error=…, llm=…, candidates=…, deterministic=…, semantic=…, adjudicated=…, pairs_capped=…, superseded=…, **decision counts)`. Scalars only.
    8. Return `{"state": "landed", "result": result}`.
    9. **Adopters' touched refs** (Decision 6). At the start of the work, `_take_touched(app, cid)` (`app.state.runs.take_touched(runs.campaign_subject(cid))`) is unioned into `touched`, so refs a previous run left behind are never lost. After step 7, unless the run is `cancel_requested` or `forgotten`, it takes the set again and, when non-empty, runs **one** more incremental pass (steps 1–7 with a new stamp and those refs) inside the same run. The run's `result` keeps the first pass's `sweep` (so a Refresh that absorbed an End Scene still reads `full`) with `follow_on: true` and the last pass's counts. The runner has no completion callback and a run cannot adopt or start a successor while it is itself `running`, so the live run absorbs what its adopters brought; anything arriving during that extra pass waits in the set for the next fresh run. Pinned by `test_two_end_scenes_back_to_back_both_get_their_touched_refs` (Task 9).

    The result is `{"sweep": "full"|"incremental", "matching", "embedding", "llm": "off"|"skipped"|"ok"|"failed", "reason", "candidates", "adjudicated", "pairs_capped", "superseded"}`.
  - `start_reconcile(app, cid, client, *, full: bool, attempt_id: str | None = None, touched: tuple[str, ...] = ()) -> tuple[Run, bool] | None`: `runs.reserve_campaign_background(app, cid, "continuity-reconcile", attempt_id)`. It does not start an adopted run.
  - `schedule_reconcile(app, cid, client, *, edits: list | None = None, progress: dict | None = None, touched: tuple[str, ...] = ()) -> None`: returns when `not AUTO_RECONCILE`. It never raises, and is spelled out rather than copied from `scenes._start_background` (whose reservation sits outside its `try`):

    ```
    run, fresh, started = None, False, False
    try:
        refs = set(touched) | _touched_refs(edits or [], progress or {})
        reserved = start_reconcile(app, cid, client, full=False, touched=tuple(sorted(refs)))
        if reserved is None:
            return
        run, fresh = reserved
        if not fresh:
            app.state.runs.pend_touched(runs.campaign_subject(cid), refs)  # Decision 6: the adopted run takes it
            return
        runs.start_computing(app, run, ...)
        started = True
    except Exception as exc:  # noqa: BLE001 -- the save already landed; a sweep is never its failure
        log.warning("could not start the continuity sweep for %s -- %s", cid, type(exc).__name__)
        if run is not None and fresh and not started:
            runs.release_before_start(app, run, "failed", {"kind": "run_failed", "detail": str(exc)})
    ```

    `release_before_start` runs only for a run reserved fresh and not yet started: a reservation that raised left nothing to release, and an adopted run is someone else's.
  - `@router.post("/campaigns/{cid}/continuity/reconcile", status_code=202)` + `@computes_only`: `def post_reconcile(cid: str, request: Request, client: LLMClient = Depends(get_llm), x_grimoire_attempt: str | None = Header(default=None))`. Decision 6; it mints an attempt (`uuid.uuid4().hex`) when the header is absent; a `fresh` run goes through `with runs.reservation(app, run): runs.start_computing(...)`.
- `store/routing.py`: the Global Constraints Route, verbatim.
- [ ] **Step 1: Write the failing tests** (`tests/test_continuity_reconcile_routes.py`, using `tests/draft_runs.post`/`settle`):
  - `test_refresh_works_with_no_connection` (§28.4): no key on the active connection. Two close threads give 202, then landed with `llm == "off"`, `matching == "basic"`, `sweep == "full"`, and the candidates file holding the duplicate.
  - `test_refresh_with_a_connection_adjudicates_once`: `llm_fakes.from_entries([{"when": {"system_contains": "You are reviewing a campaign's story ledger"}, "reply": <duplicate decision JSON>}])`. Exactly one reconcile request is made; the cached proposal is `duplicate` with refs; the usage ledger has one `continuity-reconcile` row.
  - `test_an_llm_failure_keeps_deterministic_candidates` (§26): an `error` cassette entry gives run `failed` with `error.kind` from the provider, and the candidates are present with `proposal None`.
  - `test_an_undecodable_reply_fails_the_run_and_keeps_candidates` (§24).
  - `test_a_duplicate_proposal_mutates_nothing_until_apply` (§28.4, §11.5): `plot.read`, `commitments.read` and continuity.json's aliases and links are byte-identical before and after the run. The test also seeds a temporal pair: an event "The coronation" three days out, and the commitment "Mara's oath" with the unparseable due "before the bells stop". The cassette reply proposes `duplicate` for the thread pair **and** `before` for the temporal pair. The bytes of `events.json` and of the scene-ideas file (`scene_ideas.read(cid)` before and after, plus the raw file bytes when it exists) are identical before and after the run. §11.5 names both files, and temporal adjudication works directly with events. The test asserts that the cached temporal proposal is `before`, so it cannot pass vacuously.
  - `test_the_post_is_preview_only` (`test_revision.py`): add `continuity_routes.post_reconcile.grimoire_computes_only` to `test_every_preview_only_campaign_route_is_marked_as_one`.
  - `test_a_deleted_campaign_run_writes_nothing`: start a run whose LLM call is held, delete the campaign, recreate the same slug, release; the new campaign has no `continuity_candidates.json`.
  - `test_a_live_sweep_refuses_a_storage_move`: while held, `PUT /config/data-dir` gives 409 `runs_in_flight` (accepted, §11.1).
  - `test_a_failed_run_carries_its_sweep_in_the_error`: the `error` cassette case's run `error["sweep"] == "full"`.

  Every blocking test in Tasks 8 and 9 uses `llm_fakes.HeldCassette([<reconcile entry>], hold={"system_contains": "You are reviewing a campaign's story ledger"})` installed at `app.dependency_overrides[routes.get_llm]`, with `await_held()` and `release()` (it holds `complete` too, which consumes `stream`). No inline fake.
  - `test_reconcile_log_row_carries_counts_only`: no field value contains a seeded title or beat.
  - `test_routing_routes.py::test_the_reconcile_sweep_runs_on_the_continuity_route`: the continuity route is pointed at `for-continuity`; two close threads; the POST settles; every reconcile request's `conn["id"] == routed`.
  - `test_continuity_routes.py`: `test_unknown_campaign_404_on_every_route` gains `("post", "/continuity/reconcile", None)`.
  - `test_path_guard_store.py`: `_FILL` gains `"candidate_id": "possible_duplicate-0123456789abcdef"` and `"fingerprint": "fp1_" + "0" * 64` (used by Tasks 12–13's routes, added here so the guard never sees an unfilled name).
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile_routes.py tests/test_revision.py tests/test_routing_routes.py tests/test_continuity_routes.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.** In `conftest.py`, register the marker `reconcile: keep the automatic continuity sweep after End Scene on for this test`, and add the autouse fixture that `monkeypatch.setattr(continuity_routes, "AUTO_RECONCILE", False)` unless the marker is present, with a docstring giving Decision 20's reason.
- [ ] **Step 4: Run the tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile_routes.py tests/test_revision.py tests/test_routing_routes.py tests/test_continuity_routes.py tests/test_routing_guard.py tests/test_routing.py tests/test_usage_guard.py tests/test_import_guard.py tests/test_route_order.py tests/test_path_guard_store.py -q -p no:cacheprovider`. Expected: PASS. `test_route_order` passes unchanged (no new crossing; record that in the commit body). Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): the reconciliation run and its explicit refresh`

---

### Task 9: The End Scene trigger, and the detached-run inventory

**Files:**
- Modify: `routes/scenes.py` (`put_chronicle`), `CLAUDE.md` (Detached runs)
- Test: `tests/test_continuity_reconcile_routes.py`

**Interfaces:**
- Consumes: `continuity_routes.schedule_reconcile` (Task 8).
- Produces:
  - `put_chronicle(cid, sid, body, request, client: LLMClient = Depends(get_llm))`. The `with` block assigns `result` and keeps a reference to `progress`. After the block, `continuity_routes.schedule_reconcile(request.app, cid, client, edits=body.edits, progress=progress)`, then `return result` (Decision 6). Nothing between the commit and the return can raise: `_touched_refs` (a private helper in `routes/continuity.py`) runs inside `schedule_reconcile`'s `try`.
  - `CLAUDE.md`:
    - "**Twenty-one handlers** start detached runs, in three classes" becomes "**Twenty-three handlers** start detached runs, in four classes";
    - a new `background` bullet after `draft`: `put_chronicle` (an incremental `continuity-reconcile` after a commit completes, never on the idempotent replay) and `post_reconcile` (the full sweep, `@computes_only`, 202), both on `("campaign", cid)` with at most one live per campaign. The bullet also names the turn's follow-ups (rolling summary, scene-break) and `tracker-update` as the class's other members, which no handler starts;
    - the "A landed turn schedules its own follow-ups" bullet keeps its text.

    The prose must not copy CONTRIBUTING's or store-guarantees' wording (`test_no_document_restates_another`).
- [ ] **Step 1: Write the failing tests** (marked `@pytest.mark.reconcile`):
  - `test_a_fresh_commit_starts_an_incremental_sweep` (§28.4): absorb with `llm_fakes.from_cassette("campaign_flow")` (its extraction, identity and, after Task 5, reconcile entries), then PUT /chronicle. A `background` run of kind `continuity-reconcile` exists on `("campaign", cid)`, its `terminal.wait(timeout=10)` succeeds, and `result["sweep"] == "incremental"`.
  - `test_a_replayed_save_starts_no_run` (§28.4): PUT the same body and token twice. After the first run settles, the second PUT leaves the run count at one.
  - `test_a_resumed_commit_starts_a_sweep` (Decision 6): construct a journalled, not-done `commits` entry (the `test_commits_store` resume pattern).
  - `test_a_reservation_failure_does_not_affect_the_save` (§28.4, §26): monkeypatch `runs.reserve_campaign_background` to raise `RuntimeError`. The response is 200, its `applied` and `failures` equal the expected edit ids, its key set equals an unpatched save's (whole-body equality cannot hold: `absorbed` is a timestamp), no run is left `running`, and a warning is logged.
  - `test_a_malformed_edit_still_saves`: an applied edit list containing `{"kind": "plot", "target": "x", "id": 3}` beside a good one gives 200, and `touched` holds only the good ref.
  - `test_touched_refs_come_from_applied_plot_and_commitment_edits`: a unit test of `_touched_refs` over a `progress` journal, by position, with a duplicated id whose second edit failed.
  - `test_a_refresh_during_the_automatic_sweep_returns_it`: the automatic run is held (`HeldCassette`, Task 8); POST /reconcile returns the same run id.
  - `test_two_end_scenes_back_to_back_both_get_their_touched_refs`: hold the first scene's sweep, save a second scene that moves "Winifred's chart", release; the run's reconcile requests (the held one and the follow-on pass) between them name both scenes' touched records.
  - `test_pending_touched_is_per_app_and_forgotten_with_the_campaign` (Decision 6):
    - (a) In one app, hold an automatic sweep and save a second scene that moves "Winifred's chart", so the adopting trigger pends `thread:winifred-s-chart`. Cancel the held run so it never takes the set, and close that `TestClient`. A new `TestClient` (a new app) on the same store and cid then starts a full refresh. Its reconcile request does not carry a touched re-check for "Winifred's chart", and its registry's `take_touched` is empty.
    - (b) Within one app, pend the same ref the same way and cancel the held run. `DELETE` the campaign, recreate the same slug with "Winifred's chart" seeded, and start a sweep. No request names a touched re-check for it, and `take_touched` on the subject is empty after the DELETE.
  - An unmarked smoke test, `test_the_suite_switch_keeps_saves_quiet`: no reconcile run after PUT /chronicle.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_reconcile_routes.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement**, and edit `CLAUDE.md`.
- [ ] **Step 4: Run the tests.** Same command, then the save suites: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py tests/test_review_detach.py tests/test_scene_freeze.py tests/test_retcon_routes.py tests/test_ledger_routes.py tests/test_commits_store.py tests/test_absorb_store.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS, with no existing test edited.
- [ ] **Step 5: Audit.** From `backend/`, run `grep -ln '/chronicle' tests/*.py`. For every file, confirm that no test opts in to `reconcile` except this slice's own. Record the file list in the commit body.
- [ ] **Step 6: Lint.** `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 7: Commit:** `feat(continuity): End Scene starts an incremental reconciliation sweep`

---

### Task 10: A review staged before a merge is refused at save (C backlog (a))

**Files:**
- Modify: `store/continuity/review.py` (`merged_edit_targets`), `routes/scenes.py` (`put_chronicle` fresh branch)
- Test: `tests/test_continuity_review.py`, `frontend/src/components/review/SceneReview.test.tsx` (one case, through `testkit/campaignHarness.tsx`)

**Interfaces:**
- Produces: `review.merged_edit_targets(cid, edits: list[dict]) -> list[dict]`.
  - It considers edits with `kind == "plot"` (target `{"kind": "plot", "id"}`) and `kind == "commitment"` (target `{"kind": "commitments", "id"}`).
  - An edit is listed when `effective.live_canon(cid)` maps its physical ref to another ref. Each entry is `{index, id, label, source, canonical, canonical_title}`.
  - A malformed continuity.json gives `[]` (no alias is effective then).
  - `put_chronicle` raises 409 `{kind: "edits_target_merged", detail, edits}` (Decision 19).
- [ ] **Step 1: Write the failing tests:**
  - `test_a_review_staged_before_a_merge_is_refused_naming_the_merged_rows`: seed "Mara's map" and "Winifred's chart" with `plot.set_movement`; absorb a scene that moves "Mara's map" (the `from_cassette("campaign_flow")` extraction body, or C's `review_runs` extraction body plus an identity entry, via `app.dependency_overrides[routes.get_llm]`); then merge it into "Winifred's chart" via `POST /continuity/aliases`, then PUT /chronicle. The answer is 409 `edits_target_merged`; `detail` names "Mara's map" and "Winifred's chart"; `plot.get(cid, "mara-s-map")` is unchanged and the chronicle has no entry for the scene.
  - `test_saving_without_the_merged_rows_succeeds`: the same body minus that edit gives 200.
  - `test_a_review_staged_after_the_merge_saves` (C's redirect; regression).
  - `test_a_replay_of_a_completed_save_is_not_refused`: the replay returns before the check.
  - Frontend, `SceneReview.test.tsx`: `a save refused for merged records explains it and saves without the rejected row`. This pins Decision 19's claim that the panel's generic save-error path is enough. A later change to the 409 branch, which already special-cases `edit_conflicts`, could otherwise swallow the detail or read the error as a conflict list, and leave the reader unable to save with no explanation.
    - Stage a review with two edit rows, one labelled "Mara's map".
    - Make `saveChronicle` reject once with an `ApiError` (409, kind `edits_target_merged`, `detail` "Mara's map was merged into Winifred's chart after this review was staged. Reject that row (or re-absorb) and save again.", `body.edits: [{index: 0, id: "mara-s-map", label: "Mara's map", source: "thread:mara-s-map", canonical: "thread:winifred-s-chart", canonical_title: "Winifred's chart"}]`).
    - Assert that "Could not save this review:" renders with that detail, and that no conflict rows render.
    - Click `Reject Mara's map`, then `Try saving again`, with `saveChronicle` now resolving.
    - The second call's `edits` omits the rejected row and keeps the other. The commit token is unchanged, and the panel closes as after any save.

    The case reaches its mocks through the existing `testkit/campaignHarness.tsx` and adds nothing to `src/routes/`. It is a **regression pin** against today's `useSceneReview.saveAbsorb`, so it passes at Step 2. The optional row marking from `edits[].index` is not adopted. That index counts the non-rejected batch, and the `detail` already names the rows. A second index mapping beside `edit_conflicts`' would be one more positional translation to keep right, for no gain the sentence does not give.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py -q -p no:cacheprovider`. Expected: FAIL. From `frontend/`: `npx vitest run src/components/review/SceneReview.test.tsx`. Expected: PASS (regression pin).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py tests/test_routes.py tests/test_review_detach.py tests/test_absorb_identity.py -q -p no:cacheprovider`. Expected: PASS. From `frontend/`, `npx vitest run src/components/review/SceneReview.test.tsx`. Expected: PASS. Then `make check-lint check-mypy check-eslint PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `fix(absorb): refuse a review staged against a record merged since`

---

### Task 11: The writer guard (§11.6)

**Files:**
- Create: `tests/test_continuity_writer_guard.py`
- Modify: `CONTRIBUTING.md` (the guard table row)

**Interfaces:**
- `SCANNED = ("similarity", "identity", "reconcile", "pending", "pressure", "drivers", "graph")`.
- `REQUIRED_PRESENT = {"similarity", "identity", "reconcile", "pending", "pressure", "drivers"}`. `graph` is Slice F's and may be absent; nothing else may be. The guard's docstring says `pending` is read-only by design (Decision 1): it is what Todo and both persists read through, so a write routed through it would bypass §11.5.
- `FORBIDDEN`, as `{absolute module: names}`:
  - `"grimoire.store.plot": {"set_movement", "restore"}`;
  - `"grimoire.store.commitments": {"set_movement", "restore"}`;
  - `"grimoire.store.continuity.doc": {"put_alias", "drop_alias", "put_link", "drop_link", "restore_alias", "restore_link", "restore_alias_snapshot", "restore_link_snapshot", "repoint_scenes", "put_suppression", "drop_suppression"}`;
  - `"grimoire.store.continuity.review": {"create_alias", "remove_alias", "create_link", "remove_link", "forget_ref"}`, the review mutators that exist today (review.py defines nothing else that writes). Task 12 adds `settle`, `dismiss` and `restore_suppression` in the commit that creates them, so `test_the_guard_names_functions_that_exist` is never red;
  - `"grimoire.store.events": {"create", "update", "delete", "fire", "unfire"}`, the public mutators of events.json;
  - `"grimoire.store.scene_ideas": {"add", "set_status", "mark_used", "repoint_scenes"}`, the public mutators of the scene-ideas store.

  `repoint_scenes` rewrites `links[*].scene`. Suppressions, the review mutators, events and scene ideas are listed so that §11.5's "changes nothing but the cache" cannot be routed around. §11.5 names events.json and scene ideas explicitly, and they go beyond §11.6's minimum list. `test_the_guard_names_functions_that_exist` pins every one of these names, so a rename in `events` or `scene_ideas` reds the guard instead of silently unguarding it. Slice E's `scene_ideas.add` caller is `routes/scenes.py`, which is not scanned.
- Helpers:
  - `_absolute(node: ast.ImportFrom, package: str) -> str`: resolves `level` against the scanned file's package (`grimoire.store.continuity`). Matching is exact, never by tail: a tail match on `doc` is too loose.
  - `_bindings(tree, package) -> (names: dict[local, (module, fn)], modules: dict[local, module])`: covers `from .. import plot`, `from .. import commitments as commitments_store`, `from . import doc`, `from .doc import put_alias as pa`, `from ..continuity import doc`, `import grimoire.store.plot`.
  - `_calls(tree, package)`: yields `(lineno, module, fn, rendered)` for `alias.fn(...)` with `ast.unparse(func.value)` in the module bindings, and for bare bound names.
- [ ] **Step 1: Write the tests:**
  - `test_discovery_modules_never_write_reviewed_state`: scan every present `SCANNED` module and assert no forbidden call, with the offenders listed.
  - `test_the_scanned_modules_exist`: `REQUIRED_PRESENT` ⊆ present.
  - `test_the_guard_names_functions_that_exist`: each forbidden name is callable on its module.
  - `test_the_detector_catches_every_spelling_of_the_call`: source snippets parsed with `package="grimoire.store.continuity"`, one per binding form above, plus `from .. import events` then `events.fire(...)` and `from ..scene_ideas import add` then `add(...)`.
  - `test_the_detector_does_not_flag_a_module_that_merely_looks_like_this_one`: a local `doc` dict with a `put_alias` key, and `candidates.write`.
  - `test_review_still_uses_the_vocabulary_it_is_supposed_to` (non-vacuity): `review.py` (not scanned) calls `doc.put_alias` and `doc.put_link`.
- [ ] **Step 2: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_writer_guard.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: `test_contributing_names_every_guard_test` FAILS until the table row exists; every other test PASSES (the modules already comply).
- [ ] **Step 3: Add the CONTRIBUTING row:** ``| `test_continuity_writer_guard.py` | continuity discovery modules never write plot or commitment records, events, scene ideas, reviewed aliases and links, or suppressions — only `continuity.review` and the routes apply a reviewed decision | — |``.
- [ ] **Step 4: Run them.** Same command. Expected: PASS. Then `make check-lint PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `test(continuity): writer guard keeps discovery read-only`

---

### Task 12: Apply, dismiss and restore — the store half and the ledger helpers

**Files:**
- Modify: `routes/ledger.py` (`move_thread`, `move_commitment`; `put_thread`/`put_commitment` call them), `store/continuity/review.py`, `routes/models.py` (`ContinuityDismiss`), `tests/test_continuity_writer_guard.py` (`FORBIDDEN["grimoire.store.continuity.review"]` gains `settle`, `dismiss`, `restore_suppression`)
- Test: `tests/test_ledger_routes.py` (existing tests unchanged), `tests/test_continuity_apply.py` (new), `tests/test_continuity_writer_guard.py`

**Interfaces:**
- Produces:
  - `ledger.move_thread(cid, pid, *, title: str = "", status: str = "", beat: str = "", scene: str | None = None, keep_later_scene: bool = False, label: str) -> None`.
  - `ledger.move_commitment(cid, mid, *, title: str = "", kind: str = "", status: str = "", due: str | None = None, beat: str = "", scene: str | None = None, keep_later_scene: bool = False, label: str) -> None`.
  - Both follow Decision 18. Each is one `store.undo.journalled(cid, {"w": "plot"|"commitment", "id": …}, kind=…, ref=…, field="thread"|"commitment", label=label)` block.
  - `review.validate_alias(cid, ref, to, *, replace=False, accept_status_change=False) -> dict`: every refusal `create_alias` makes today, returning `{"source": standing, "canonical": {ref, …standing}}`. `create_alias` calls it first, and its behaviour and refusals are unchanged.
  - `review.check_candidate(cid, candidate_id, expect: str | None, *, evidence: bool = True) -> dict`: `{"record", "current", "fingerprint"}` (the **current** fingerprint), or `RefusedError` per Decision 16: 409 `malformed` (strict canonicalization), 404 `not_found` (uncached or a hidden verdict), 409 from `_require_readable` (`unknown`), or 409 `stale_candidate` with `extra={"current": {"fingerprint": fp_or_empty, "records": pending.rows(...)}, "reason": "records"|"evidence"}`. `expect=None` means the cached fingerprint. `evidence=False` (dismiss) skips the void-proposal check.
  - `review.plan_apply(cid, checked: dict, body: dict) -> dict`: Decision 16's validation. Every key of `body` is optional and type-checked (str or bool as §21 implies), and a wrong type is 400 `bad_body`. The result is `{"op", "status"?, "beat", "scene", "alias"?: {ref, to, accept}, "link"?: {a, b, relation}, "copy_due"?: str, "target"?: physical id, "decision"?: "keep_open"}`.
  - `review.settle(cid, candidate_id, record: dict, fingerprint: str, decision: str | None) -> list[str]`: when `decision` is set, **first** `doc.put_suppression(cid, fingerprint, {kind, refs, decision, created})`, where `fingerprint` is `check_candidate`'s current one, and **then** `candidates.drop` (Decision 16's order). It returns the parts that landed (`"suppression"`, `"cache"`). An `OSError` after a landed part is re-raised as `review.PartialSettleError(OSError)` carrying `landed`. A suppressed finding that is still cached is hidden by its verdict, so a failed drop is harmless.
  - `review.dismiss(cid, candidate_id, decision: str, expect: str | None = None) -> dict`: under the lock, `check_candidate(expect=expect, evidence=False)`, then validate the decision (Decision 17), then `settle`. `doc.put_suppression`'s `ContinuityError` (a section that went malformed under the hold) is mapped to 409 `malformed`, never a 500; with suppression first, nothing has landed by then. Returns `{"ok": True, "fingerprint"}`.
  - `review.restore_suppression(cid, fp) -> dict`: under the lock, `doc.drop_suppression`; `None` gives 404 `not_found`. Returns `{"ok": True}`.
  - `class ContinuityDismiss(BaseModel): decision: str | None = None; expect_fingerprint: str | None = None` (additive beyond §21's `{decision}`).
- [ ] **Step 1: Write the failing tests:**
  - `test_ledger_routes.py` stays green unedited; this is the regression for `put_thread`/`put_commitment`.
  - `test_move_thread_with_a_beat_never_moves_last_scene_backwards` (§12.4): stored `last_scene "005--x"`, beat at `"003--y"`. The beat is appended with scene `003--y` and `last_scene` stays `005--x`. One journal row is written, and undo restores the record exactly.
  - `test_move_thread_without_a_beat_keeps_last_scene_and_title` (§12.4): `title ""` and `scene None`.
  - `test_move_commitment_keeps_kind_and_due` (`kind ""`, `due None`).
  - `test_create_alias_refusals_unchanged`: run the existing `test_continuity_review.py`.
  - `test_check_candidate_stale_carries_current_records` (§22.5).
  - `test_resubmitting_against_the_current_fingerprint_passes` (§12.9, §22 step 5): change a title; `check_candidate(expect=<cached>)` gives 409 `stale_candidate`; `check_candidate(expect=<current>)` passes and returns the current fingerprint.
  - `test_a_gone_evidence_scene_is_stale` (§5.6): a proposal names a deleted scene; 409 with `reason "evidence"`. Dismiss of the same finding succeeds.
  - `test_apply_on_a_finding_no_longer_cached_is_not_found` (Review Focus 5).
  - `test_hidden_verdicts_are_not_found`: suppressed, satisfied, gone and settled findings give 404 `not_found` from both apply and dismiss.
  - `test_apply_refuses_while_continuity_json_is_malformed` (§22 step 1): continuity.json's aliases section set to `[]`; a closure apply and a dismiss give 409 `malformed`, and no file changes.
  - `test_plan_apply_validates_everything_before_any_write` (parametrized):
    - `alias` on a cross-type pair → 400 `bad_op`;
    - `canonical` not among the refs → 400;
    - `link pays_off` reversed → 400 `invalid_relation`;
    - `subthread_of` on commitments → 400;
    - `continues` on commitments → 400 `invalid_relation`;
    - `resolve` with status `closed` → 400 `bad_status`;
    - `beat` without `scene` → 400;
    - `copy_due` with a non-empty canonical due → 400 `due_not_copyable`;
    - an open-into-closed merge without `accept_status_change` → 409 `liveness_mismatch` carrying `source`/`canonical`.
    - Each asserts every file's bytes are unchanged.
  - `test_dismiss_writes_a_suppression_and_removes_the_finding`; `test_keep_open_is_lifecycle_only`; `test_dismissing_a_stale_finding_is_refused`.
  - `test_restore_suppression_round_trip_and_404`.
  - `test_suppression_writes_are_not_journalled` (§12.8): the journal row count is unchanged.
  - `test_dismiss_with_a_failing_cache_drop_still_suppresses` (§22): monkeypatch `candidates.drop` to raise `OSError`. `review.dismiss` raises `PartialSettleError` with `landed == ["suppression"]`. continuity.json holds the suppression, and `pending.findings` gives the still-cached record the verdict `suppressed`, so the GET and Todo hide it and the next persist drops it.
  - `test_a_suppression_failure_lands_nothing`: monkeypatch `doc.put_suppression` to raise `ContinuityError`. The answer is 409 `malformed`, and the cache file is byte-identical (the drop never ran).
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_apply.py tests/test_ledger_routes.py tests/test_continuity_review.py -q -p no:cacheprovider`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, and add the three new review names to the writer guard's `FORBIDDEN`.
- [ ] **Step 4: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_apply.py tests/test_ledger_routes.py tests/test_continuity_review.py tests/test_continuity_writer_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): validated apply, dismiss and restore; ledger closure helpers`

---

### Task 13: Review routes (§21) and the joined candidates read (§12.2)

**Files:**
- Modify: `routes/continuity.py`, `routes/__init__.py` (docstring row), `CLAUDE.md` (the write-token paragraph's non-2xx sentence, which names `proposals.project`, gains apply's and dismiss's partial-write 500s, in its own words)
- Test: `tests/test_continuity_review_routes.py` (new; conftest `client`)

**Interfaces:**
- Produces:
  - `GET /campaigns/{cid}/continuity/candidates` (`def get_candidates(cid: str, request: Request)`, which needs `request.app.state.runs`). It answers `{"generated", "matching", "diagnostics": {**effective.diagnostics(...), "malformed": doc.malformed(cid), "cache_malformed": candidates.malformed(cid)}, "run": run_payload | None, "names", "scenes", "candidates": [{id, kind, group, refs, fingerprint, stale, stale_reason, signals, proposal, created, records}]}` (Decision 23).
    - **Outside the hold**, first: the one `list_scenes` map and `reconcile.pressure_by_ref(cid)`. The latter runs `aging.prepare` and `pressure.build`, which can run user calendar-plugin code, and §11.1 keeps that out of lock holds; a slow plugin must not block saves and applies behind a read.
    - **Inside `best_effort_campaign_lock`**: only `pending.Current.load`, `findings` and `rows`.
    - Candidates are the `pending.findings` entries with verdict in `VISIBLE`. `stale` is a `stale` verdict or a void proposal (`pending.evidence_ok`), and `stale_reason` says which.
    - `records` comes from `pending.rows(current, refs, scene_title=<the scene map>, pressure=<the pressure map>)`.
    - `run` is the newest running `continuity-reconcile` run on the subject, or `None`.
    - `matching` is the same function `GET /continuity` calls (after B and C rebased, one definition).
  - `POST /campaigns/{cid}/continuity/candidates/{candidate_id}/apply`, with `body: dict`. In one `campaign_lock` hold it runs Decision 16 and answers `{"ok": True, "applied": [parts], "alias"?, "link"?, "dues"?}`. On `OSError` it calls `store.revision.bump(cid)`, then raises 500 `{kind: "partial_apply", detail, landed}`.
  - `POST /campaigns/{cid}/continuity/candidates/{candidate_id}/dismiss` with `ContinuityDismiss`, giving `review.dismiss(cid, candidate_id, body.decision or "dismiss", body.expect_fingerprint)`. On `review.PartialSettleError` it calls `store.revision.bump(cid)` before raising 500 `{kind: "partial_dismiss", detail, landed}`, mirroring apply's partial path, because a landed write answered non-2xx is invisible to the middleware. A plain `OSError` (nothing landed) is a 500 with no bump. Apply's `OSError` handler also merges a `PartialSettleError`'s `landed` into its own list (a `keep_open` apply settles last).
  - `DELETE /campaigns/{cid}/continuity/suppressions/{fingerprint}`, giving `review.restore_suppression`.
  - `GET /continuity`: each suppression gains `live: bool` (`pending.fingerprint(current, kind, refs) == fingerprint`) and `titles: [str]` (§12.7). `pending.Current` is built **only when** suppressions is non-empty, and from the `Ledgers` the route already loaded (`Current.load(cid, ledgers=...)`), so `test_get_continuity_reads_each_ledger_once` stays green unedited.
  - Every `RefusedError` goes through `_refusal`.
- [ ] **Step 1: Write the failing tests.** Seed through `reconcile.discover` and `persist_found` directly.
  - `test_candidates_read_is_pure` (§3.10, AC16): monkeypatch `similarity._CLIENT` and the LLM fake to raise. The GET is 200, no run is started, and the token is unchanged.
  - `test_candidates_join_current_records_and_flag_stale` (§12.2): change a title gives `stale True`, with the records showing the new title.
  - `test_hidden_verdicts_are_not_listed`: suppressed, satisfied, gone and settled records are absent.
  - `test_candidates_read_reports_a_live_run`.
  - `test_a_malformed_cache_reads_empty` (§26): 200, `candidates == []`, `cache_malformed True`.
  - `test_a_hand_edited_cache_with_bad_nested_fields_reads_200`: `signals: "x"` and `proposal: {"decision": 3}` on otherwise valid records.
  - `test_a_malformed_continuity_file_hides_findings`: continuity.json `"{ no"`; 200, `candidates == []`, `diagnostics.malformed == ["file"]`.
  - `test_a_renamed_evidence_scene_is_flagged`: the GET gives `stale True, stale_reason "evidence"` for a proposal citing a renamed scene, matching apply's 409.
  - `test_candidates_read_names_scenes_and_actors`: `names` maps every beat scene and `shared_actors` ref to a display name, and `scenes` is newest first.
  - `test_pressure_is_computed_outside_the_hold`: monkeypatch `reconcile.pressure_by_ref` to assert `locks.campaign_lock` is not held by the calling thread.
  - `test_applying_a_duplicate_writes_an_alias_only` (§28.4): Keep A gives an alias `B → A` with `source == "review"`; plot.json is byte-identical; the finding is gone from the cache; one journal row.
  - `test_apply_liveness_mismatch_then_accept` (§12.3).
  - `test_apply_with_copy_due_writes_two_journalled_rows` (§5.1): the due copy row, then the alias row.
  - `test_applying_a_closure_closes_and_keeps_last_scene` (§12.4): with and without a beat; the evidence scene is earlier than `last_scene`.
  - `test_applying_a_resolution_sets_the_status`.
  - `test_temporal_accept_writes_the_link` (`before` from the commitment to the event). Then (§28.4 "a reviewed temporal link contributes to pressure") `pressure.build(cid, sources={"linked_deadline"})` yields an item whose `subject` is the commitment ref and whose `relation == "before"`, and the next sweep no longer nominates the temporal pair (`satisfied`).
  - `test_stale_apply_returns_409_with_current_records` (§28.4): the body is `{kind: "stale_candidate", detail, current: {fingerprint, records}, reason: "records"}` at top level, and nothing is written.
  - `test_resubmitting_against_the_current_fingerprint_applies` (§12.9): the same apply with `expect_fingerprint = current.fingerprint` from the 409 succeeds.
  - `test_keep_open_suppresses_with_its_decision` (§5.5).
  - `test_restore_brings_the_finding_back_at_the_next_refresh` (§12.7).
  - `test_get_continuity_marks_suppressions_live`: a suppression for since-changed records gives `live False`.
  - `test_get_continuity_with_suppressions_reads_each_ledger_a_bounded_number_of_times`: one suppression and two suppressions read plot.json the same number of times.
  - `test_apply_partial_write_names_what_landed`: monkeypatch `review.create_alias` to raise `OSError` after a due copy; 500 with `landed == ["due"]` and the token moved.
  - `test_a_failed_dismiss_after_a_landed_write_moves_the_token`: monkeypatch `candidates.drop` to raise `OSError`. The dismiss answers 500 with `kind == "partial_dismiss"` and `landed == ["suppression"]`. `store.revision.current(cid)` moved, and the following `GET /continuity/candidates` no longer lists the finding. Variant: `doc.put_suppression` raising `ContinuityError` gives 409 `malformed` and leaves the token unchanged.
  - `test_unknown_campaign_404_on_every_route` gains the four new routes.
  - `test_continuity_routes_not_captured_by_entities` gains `/continuity/candidates`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review_routes.py tests/test_continuity_routes.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and the route guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review_routes.py tests/test_continuity_routes.py tests/test_route_order.py tests/test_path_guard_store.py tests/test_pydantic_guard.py tests/test_revision.py tests/test_docs_guard.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(continuity): review routes over the candidate cache`

---

### Task 14: Todo — the embeddings chore and the continuity chores (§18; C backlog (d))

**Files:**
- Modify: `routes/todo.py` (`_chore_embeddings`, `_chore_continuity_overlaps`, `_chore_continuity_closures`, their `ITEMS`, the registrations, a private `_ledger_href`, the `owed` chore's and items' fixes, `_chore_unpriced`'s and `_items_unpriced`'s fixes, the module docstring), `store/chores.py` (docstring)
- Test: `tests/test_todo_route.py`

**Interfaces:**
- Consumes: `pending.findings`, `pending.CHORE_OF`, `pending.GROUP_OF`, `pending.label`, `embed_space.resolve`, `store.campaigns.read.list_campaigns`.
- Produces:
  - `_chore_embeddings(ctx)` in `LIBRARY_BUILDERS`:

    ```
    {"id": "embeddings", "scope": "library", "group": "Housekeeping", "severity": "note", "n": 1,
     "what": "Semantic matching is not configured",
     "why": "Grimoire still uses basic matching to find possible overlaps in your ledgers. "
            "Semantic matching improves detection when the same story business is phrased differently.",
     "fix": "/config?section=semantic", "fix_label": "Embeddings"}
    ```

    It is emitted iff `store.embed_space.resolve() is None` and `list_campaigns()` is non-empty. A raising `resolve` emits nothing: an outage is not "not configured".
  - `_chore_continuity_overlaps(ctx)` and `_chore_continuity_closures(ctx)` in `CAMPAIGN_BUILDERS`, after `owed`. Each is `{"id": "continuity-overlaps"|"continuity-closures", "scope": "campaign", "group": "Continuity", "severity": "note", "n", …}`:
    - `n` counts `live` findings whose `CHORE_OF[kind]` matches. Both share one `ctx._once("continuity", lambda: pending.findings(cid))`, and a raised `OSError`/`ValueError` emits nothing;
    - overlaps `what`: `f"{n} possible overlap{'s' if n != 1 else ''} to review"`;
    - closures `what`: `f"{n} record{'s' if n != 1 else ''} that may be finished"`;
    - `why`: one sentence each, saying the findings come from the last sweep and nothing is applied without review;
    - `fix`: `/campaigns/{cid}/ledger/continuity/overlaps`; for closures, `/campaigns/{cid}/ledger/continuity/closures` when at least one live `possible_thread_closure` is counted, else `/campaigns/{cid}/ledger/continuity/resolutions` (the chore counts both kinds, and a resolutions-only campaign would otherwise land on an empty group). All built by `_ledger_href`;
    - `fix_label`: "Continuity review".
  - `ITEMS["embeddings"]` gives one `{"id": "embeddings", "label": "Semantic matching", "detail": "No embeddings connection and model are set", "fix": "/config?section=semantic"}`. `ITEMS["continuity-overlaps"]` / `["continuity-closures"]` give `{id: candidate id, label: pending.label(...), detail: <kind phrase>[ · pending.DECISION_LABELS[decision]], fix: _ledger_href(cid, "continuity", GROUP_OF[kind], candidate id)}`. A raw decision word never reaches the page (Decision 25).
  - `_ledger_href(cid, section, *segments) -> str`: `/campaigns/<q(cid)>/ledger[/<section>][/<q(segment)>…]` with `q = urllib.parse.quote(s, safe="")`, the grammar of `ledgerHref` (§12.1 "Todo chores and items use these addresses").
  - The `owed` chore's fix becomes `_ledger_href(cid, "commitments")`, and each `owed` item's `_ledger_href(cid, "commitments", c["id"])`. Slice B pins the items' `id`, `label` and `detail`, not `fix`.
  - `_chore_unpriced`'s and `_items_unpriced`'s fixes become `"/config?section=pricing"`.
  - The docstrings (§18 preamble), narrowly. `todo.py`'s "nothing is cached" gains: "One exception, read-only: the continuity chores read the reconciliation cache (`continuity_candidates.json`) through `continuity.pending`'s live filter in the same request — suppressed, stale, merged-away and already-closed findings never count." `chores.py`'s "Nothing here is stored, cached, or written down" gets the same one-sentence exception.
- [ ] **Step 1: Write the failing tests:**
  - `test_missing_embeddings_shows_one_library_chore_on_the_global_page` (§28.7): one `embeddings` chore with the exact dict; it is absent from `GET /api/todo?campaign=<cid>`.
  - `test_no_campaign_no_embeddings_chore`.
  - `test_embeddings_with_recall_depth_zero_show_no_chore` (§28.7).
  - `test_a_raising_resolve_is_not_not_configured`.
  - `test_no_embeddings_still_shows_continuity_chores` (§28.7).
  - `test_continuity_counts_come_from_the_cache_after_the_live_filter` (§28.7): live, suppressed, stale, merged-away and already-closed findings give `n == 1`.
  - `test_absent_cache_shows_no_continuity_chore` (§28.7).
  - `test_todo_makes_no_embedding_calendar_or_client_call` (§28.7, §25.4): monkeypatch `similarity._CLIENT`, `calendars.primary_provider`, `involvement.of` and `chronicle.read_chronicle` to raise; `GET /api/todo` is 200 with the chores.
  - `test_continuity_items_link_to_their_group_and_id` (Review Focus 1): an overlap item's `fix` is exactly `/campaigns/run/ledger/continuity/overlaps/<candidate id>`, a resolution item's group segment is `resolutions`, and `label` names both titles. (Candidate ids are hashes, so encoding is pinned in `ledgerPaths.test.ts`, not here.)
  - `test_a_resolutions_only_closures_chore_opens_resolutions`.
  - `test_continuity_item_details_use_hedged_labels`: a cached `duplicate` proposal gives a detail containing "Suggested: same business (merge)" and never the bare word.
  - `test_owed_items_link_to_their_commitment_row`: the chore's `fix` is `/campaigns/run/ledger/commitments`, and an item's is `/campaigns/run/ledger/commitments/` plus `quote(id, safe="")`.
  - `test_a_hand_edited_cache_leaves_the_todo_page_200`: a cache record with `signals: []` and `proposal: {"decision": 3}` beside a good one; `GET /api/todo` is 200 and counts the good one.
  - `test_chore_and_items_agree` for both continuity chores.
  - `test_every_group_a_builder_can_emit_is_declared` stays green, since the builders are named `_chore_*`.
  - `test_unpriced_fix_opens_pricing`: the chore and its items.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and the docs guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py tests/test_docs_guard.py tests/test_shell_route.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `feat(todo): semantic-matching setup note and continuity review chores`

---

### Task 15: Frontend — API, Ledger addresses, highlight, Merged link and force delete

**Files:**
- Create: `frontend/src/ledgerPaths.ts`, `frontend/src/ledgerPaths.test.ts`
- Modify:
  - `frontend/src/api/types.ts`, `frontend/src/api/client.ts`;
  - `frontend/src/App.tsx`: `/campaigns/:cid/ledger/*`;
  - `frontend/src/routes/LedgerView.tsx`, `frontend/src/components/ledger/LedgerRowEditor.tsx`;
  - `frontend/src/routes/ConfigView.tsx`;
  - `frontend/src/index.css`: `.ledger-table tr.highlighted`.
- Create (test scaffolding, outside coverage per CLAUDE.md): `frontend/src/testkit/ledgerMocks.tsx` (the `vi.mock("../api/client")` factory for LedgerView's whole surface, continuity calls included, reached through a dynamic `import()` as `campaignMocks.tsx` is) and `frontend/src/testkit/ledgerHarness.tsx` (the empty `Ledger`, empty `ContinuityCandidates` and `ContinuityState`, `beforeEach` defaults, and `renderLedger(path)` with `<Route path="/campaigns/:cid/ledger/*">`). `LedgerView.test.tsx` moves onto them; Task 16's suite uses them too. Fixtures only one suite needs stay in that suite.
- Test: `frontend/src/routes/LedgerView.test.tsx`, `frontend/src/shell/rail.test.ts`, `frontend/src/routes/ConfigView.test.tsx`, `frontend/src/api/client.test.ts`

**Interfaces:**
- `types.ts`:
  - `CandidateKind`, `ContinuityGroup = "overlaps" | "closures" | "resolutions" | "reviewed" | "dismissed"`;
  - `CandidateSignals`, `CandidateProposal`, `CandidateRecord`, `ContinuityCandidate`, `ContinuityCandidates`, all with Task 13's fields;
  - `ContinuityApply` (§21 keys, all optional except `op`);
  - `ReconcileResult` (Task 8's result);
  - `ContinuityState`, the `GET /continuity` shape with suppressions' `live` and `titles`.
- `client.ts`, inside `api`:
  - `getContinuity(cid)` and `continuityCandidates(cid)`, both `fresh`;
  - `reconcileContinuity(cid, signal?)`: `draftRun<ReconcileResult>({at: "campaign", id: cid}, (attempt) => request<{run: RunHandle}>("POST", \`/api/campaigns/${encodeSegment(cid)}/continuity/reconcile\`, undefined, {attempt, signal}), {signal})`;
  - `awaitCampaignRun<T>(cid, handle: RunHandle, signal?)`: `awaitRunAt(draftBase({at: "campaign", id: cid}), handle, <refind by run id, null-safe>, signal)`. It follows a run this client did not start (Decision 24); `awaitRunAt` and `draftBase` stay module-private;
  - `staleCurrent(err: unknown): {fingerprint: string; records: CandidateRecord[]} | null`: an exported reader that validates `ApiError.body.current`'s shape (a string fingerprint and an array of objects with a string `ref`) and returns `null` otherwise. `ApiError.body` is `Record<string, unknown>`, so passing `body.current` on unchecked would not typecheck, and a bare `as` would skip the check;
  - `applyCandidate(cid, id, body)`, `dismissCandidate(cid, id, decision)` and `restoreSuppression(cid, fp)`, each `.then(notifyShell)`;
  - `removeAlias(cid, ref)` (`?ref=` encoded) and `removeLink(cid, lid)`;
  - `ledgerDeleteThread(cid, pid, force = false)` and `ledgerDeleteCommitment(cid, mid, force = false)`, appending `?force=true` when `force`.
- `ledgerPaths.ts` (leaf; no React):
  - `LEDGER_SECTIONS` (the eight), `CONTINUITY_GROUPS`, `GROUP_OF: Record<CandidateKind, ContinuityGroup>`;
  - `type LedgerTarget = { section: Exclude<LedgerSection, "continuity">; row?: string } | { section: "continuity"; group: ContinuityGroup; candidate?: string }`;
  - `ledgerHref(cid: string, target: LedgerTarget): string`: the facts section with no row gives the bare `/ledger` (Decision 22);
  - `parseLedgerTail(tail: string): LedgerTarget | null`: drops empty segments, decodes with `try`, takes at most three segments, and gives `null` for an unknown section or group.
- `LedgerView`:
  - The section comes from `parseLedgerTail(location.pathname after "/campaigns/<cid>/ledger")`. A `null` tail gives `<Navigate replace to={ledgerHref(cid, {section: "facts"})} />`.
  - Section buttons and palette items `navigate(ledgerHref(...))`.
  - A `row` highlights the matching row: `data-row-key` on `<tr>`, class `highlighted`, and `scrollIntoView?.({block: "center"})`. `rows` is memoized (`useMemo` over `ledger`, `changeRows`, `standingRows`, `showRetired` and `section`; today it is rebuilt on every render), and the scroll fires **once per address**: a ref holds the last row scrolled to, and the effect scrolls only when `row` differs from it and the row is rendered. Re-renders (busy toggles, epoch re-reads, continuity state) never pull the page back. The editor is not opened.
  - An addressed row that exists but is filtered out (a closed thread, a fulfilled commitment) sets `showRetired(true)` once, so a closure applied from review, or a Story Graph link, can still show and highlight it.
  - An aliased physical id resolves through `aliases[].ref` (Decision 22).
  - The threads and commitments note renders "Merged: …" as a `<Link to={ledgerHref(cid, {section: "continuity", group: "reviewed"})}>`. Its text content equals Slice B's string.
  - `remove(spec)` keeps the ApiError. On kind `has_merged_records` it shows the Global Constraints sentence, and `LedgerRowEditor` gains `errorAction?: { label: string; run: () => void }`, rendered as a button beside the error. "Delete anyway" calls the same delete with `force = true`.
- `ConfigView`: `useSearchParams()`. A `section` value in `SectionId` is used initially and followed by an effect; anything else leaves `"storage"`.
- [ ] **Step 1: Write the failing tests:**
  - `ledgerPaths.test.ts`:
    - `parseLedgerTail` round-trips every section, every group, a candidate id, and a row id containing "/" and ":" (Review Focus 1);
    - `ledgerHref(cid, {section: "facts"})` is the bare `/ledger`;
    - an unknown section or group gives `null`.
  - `LedgerView.test.tsx`:
    - The test router becomes `<Route path="/campaigns/:cid/ledger/*">`.
    - The mock factory gains `continuityCandidates` and `getContinuity`, with empty defaults in `beforeEach` (`{generated: "", matching: "basic", diagnostics: {…}, run: null, candidates: []}`, plus an empty `ContinuityState`).
    - Existing tests stay green, and `column()` queries `getByRole("complementary", {name: "Ledger sections"})`. `LedgerView` already passes `columnLabel="Ledger sections"` to `PageShell` (LedgerView.tsx:587); the name is needed now because the finding detail adds a second `complementary` (`aria-label="Finding actions"`).
    - New tests:
      - `a section address opens that section`;
      - `clicking a section changes the address`;
      - `a row address highlights that row without opening its editor`;
      - `a row address with a slash in its id highlights that row`;
      - `an alias source's address highlights its canonical row`;
      - `a row address to a closed thread shows and highlights it`;
      - `re-renders do not re-scroll` (`scrollIntoView` mock called once across a busy toggle);
      - `an unknown section redirects to the facts`;
      - `the Merged note links to Reviewed links / merges`;
      - `a delete refused for merged records offers Delete anyway` (the second call is `ledgerDeleteThread("run", id, true)`).
  - `rail.test.ts`: `activeIn(CAMPAIGN_ROWS, "/campaigns/c1/ledger/continuity/overlaps/possible_duplicate-0123456789abcdef")` → `["ledger"]`. This is a **regression pin**: `isUnder` (`librarySections.ts`) already accepts any `base + "/"` prefix, so it passes in Step 2 and is expected to.
  - `src/api/client.test.ts`: `staleCurrent` returns the shape for a good body and `null` for a missing or malformed `current`.
  - `ConfigView.test.tsx`:
    - `?section=semantic opens Embeddings` (render with `initialEntries={["/config?section=semantic"]}`);
    - `an unknown section falls back to Storage`;
    - `a changed query follows`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/ledgerPaths.test.ts src/routes/LedgerView.test.tsx src/shell/rail.test.ts src/routes/ConfigView.test.tsx src/api/client.test.ts`. Expected: FAIL, except the `rail.test.ts` regression pin, which PASSES.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Same command. Expected: PASS. Then `npx tsc --noEmit -p .`.
- [ ] **Step 5: Run the eslint ratchet.** From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS. If `LedgerView.tsx`'s recorded `react/no-array-index-key` resolves, run `make baseline` and commit the smaller file.
- [ ] **Step 6: Commit:** `feat(ledger): addressable sections and rows, merged link, delete anyway`

---

### Task 16: Frontend — the Continuity review column section and lists

**Files:**
- Create: `frontend/src/components/continuity/useContinuityReview.ts`, `frontend/src/components/continuity/ContinuityReview.tsx`, `frontend/src/components/continuity/ReviewedGroup.tsx`, `frontend/src/components/continuity/DismissedGroup.tsx`, `frontend/src/components/continuity/labels.ts` (Decision 25's client table)
- Modify: `frontend/src/routes/LedgerView.tsx`, `frontend/src/index.css`
- Test: `frontend/src/routes/LedgerContinuity.test.tsx` (new; it uses Task 15's `testkit/ledgerMocks.tsx` and `testkit/ledgerHarness.tsx`, with local fixtures using Mara, Winifred and Saltmarch only)

**Interfaces:**
- `useContinuityReview(cid: string, epoch: number, onWrote: () => void): { candidates: ContinuityCandidates | null | "failed"; state: ContinuityState | null | "failed"; counts: Record<ContinuityGroup, number> | null; refreshing: boolean; refreshNote: string | null; refresh(): Promise<void>; reread(): void; wrote(): void; applyCurrent(id: string, current: {fingerprint: string; records: CandidateRecord[]}): void }`.
  - It holds two reads, each with its own failure state, keyed on `[cid, epoch]` with the `live` flag pattern. `epoch` is **LedgerView's** epoch (Decision 24, "One epoch"): a ledger hand edit re-reads the continuity section, and every continuity mutator calls `wrote()`, which calls `onWrote` (LedgerView bumps its epoch, re-reading both).
  - One `AbortController` per `cid`, aborted in the effect cleanup; every run call gets its signal, and nothing (no `setState`, no follow-up Refresh) happens after it aborts.
  - When a read returns a non-null `run`, the hook follows it with `api.awaitCampaignRun` (single-flight with Refresh) and re-reads when it ends, whatever its state.
  - `counts`: overlaps, closures and resolutions count the visible candidates per group. `reviewed` is aliases plus links plus `raw_links` with `state === "broken"`. `dismissed` counts suppressions with `live`.
  - `refresh` follows Decision 24, and is single-flight. The extra Refresh fires when the outcome's `sweep` is `"incremental"`: `result.sweep` when landed, `(err as ApiError).body?.sweep` when failed. Never a third.
- `LedgerView`'s column gains `<ColumnSection label="Continuity review">`:
  - one `.column-row` button per group, with the Global Constraints labels and counts ("—" while reading);
  - a `<p className="field-hint">` matching line;
  - a "Refresh continuity review" button, disabled while refreshing, with a "Refreshing…" hint. When `candidates.run` is non-null on load: "A continuity sweep is running.", shown while the hook follows it
  - Dismissed findings is collapsed: it shows its count, and its list opens on selection.
- `ContinuityReview` (main pane, for `section === "continuity"`): a `.shelf-head` with the group label and the same "Refresh continuity review" button (wired to the hook's single-flight `refresh`), because on a phone (≤720px) `PageShell` hides the column behind "‹ Ledger sections" and a stale finding's only way forward is a Refresh. For the three finding groups, one button per finding (`pending.label`-style titles joined with " / ", the kind phrase "Possible overlap" / "May be finished" / "Needs resolution review", the hedged proposal label from `labels.ts` when present, and the visible stale text "Records changed" or "Evidence moved"). `reviewed` renders `ReviewedGroup`; `dismissed` renders `DismissedGroup`.
- `ReviewedGroup`: every alias (`Unmerge` → `removeAlias`) and link (`Remove link` → `removeLink`). Dangling aliases and broken raw links show the chip "Broken" with the same actions (§12.6).
- `DismissedGroup`: live suppressions only, each labelled with `titles` and its decision ("Dismissed" / "Kept open"), with `Restore` → `restoreSuppression` (§12.7).
- [ ] **Step 1: Write the failing tests** (§28.9):
  - `the section and its group counts render in the column`: counts per group, then "—" while a read is pending (a never-resolving mock).
  - `the matching line says Basic matching active`.
  - `selecting a group lists its findings` (the address changes too).
  - `a refresh shows progress and re-reads when it lands`: `reconcileContinuity` resolves `{sweep: "full", …}` and `continuityCandidates` is called twice.
  - `a refresh that adopted an incremental sweep runs once more` (Review Focus 4): two calls, never three; and the same when the adopted run **fails** with `body.sweep === "incremental"`.
  - `a failed refresh still re-reads and explains`.
  - `refresh is enabled with no connection`, and a landed `llm: "off"` shows "No model connection — findings are listed without a suggested decision."
  - `a sweep running on load is followed and its findings appear`: the first read returns `run`, the `awaitCampaignRun` mock lands, and `continuityCandidates` is called twice.
  - `switching campaign mid-refresh starts no follow-up`: rerender at another cid while `reconcileContinuity` is pending, then resolve it with `sweep: "incremental"`; no second `reconcileContinuity` call, and nothing renders for the old campaign.
  - `a continuity write re-reads the ledger`: after a closure apply, `campaignLedger` is called again and the Threads count drops.
  - `a stale finding offers Refresh in main on a phone`: `innerWidth` 375; the main pane has the Refresh button.
  - `findings show hedged proposal labels and visible stale text`: "Suggested: same business (merge)" and "Records changed" are in the list; the bare word `duplicate` is not.
  - `reviewed links offer Unmerge and Remove link, and mark broken ones`.
  - `a dismissed finding is restorable`.
  - `the candidates read failing costs only its section`: the ledger sections still render.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/LedgerContinuity.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them, the ledger suite and the typecheck.** `npx vitest run src/routes/LedgerContinuity.test.tsx src/routes/LedgerView.test.tsx`, then `npx tsc --noEmit -p .`. Expected: PASS. `testkit/` is already excluded from coverage; confirm no shared scaffolding was left under `src/routes/`.
- [ ] **Step 5: Run the eslint ratchet.** From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`void` every floating promise; buttons, not clickable rows).
- [ ] **Step 6: Commit:** `feat(ledger): Continuity review section with groups, reviewed and dismissed lists`

---

### Task 17: Frontend — finding detail and its actions

**Files:**
- Create: `frontend/src/components/continuity/CandidateDetail.tsx`
- Modify: `frontend/src/components/continuity/ContinuityReview.tsx`, `frontend/src/index.css`
- Test: `frontend/src/routes/LedgerContinuity.test.tsx`

**Interfaces:**
- `CandidateDetail({cid, candidate, names, scenes, busy, error, onApply(body: ContinuityApply), onDismiss(decision), onBack, onRefresh})`. It renders `.detail-view`:
  - **`.detail-main`:** the "‹ All findings" button, an `<h3>`, then per record its title as a `chip` button linking to `ledgerHref(cid, {section: "threads"|"commitments", row: <physical id>})` (CLAUDE.md: metadata naming another record navigates to it), status, due, `last_scene` title and beats; each evidence scene and beat scene is a `<Link>` to `/campaigns/{cid}/scenes/{encodeSegment(sid)}` labelled from the read's `names`. Signals: "Same title", "Same slug", "Shared characters: …" and "Shared dates: …" (both from `names`, never a raw ref), "Shared scenes: n", and the meaning score "Meaning overlap 0.NN — a discovery signal, not confidence". Then the hedged proposal label (`labels.ts`) and reason when present.
  - **`<aside className="detail-sidebar" aria-label="Finding actions">`:** a `.form-actions` holding the actions, plus `.side-section` metadata (kind, pressure, found date).
  - **Stale** (from the read): every action is disabled under "Records have changed since this was found." (or, for `stale_reason "evidence"`, "An evidence scene has been renamed or removed since this was found."), with the Refresh button beside it. After a 409 `stale_candidate` with `reason "records"`, the detail re-renders from the 409's current records and the actions re-enable, bound to `current.fingerprint` (§12.9's resubmission); after one with `reason "evidence"`, they stay disabled.
- Buttons, by exact name:
  - thread pair: `Keep <A>`, `Keep <B>`, `<A> continues <B>`, `<B> continues <A>`, `<A> is a subthread of <B>`, `<B> is a subthread of <A>`, `Related`, `Dismiss`;
  - commitment pair: `Keep <A>`, `Keep <B>`, `Related`, `Dismiss` (§5.3: `continues` and `subthread_of` join threads only);
  - cross-type pair: `Pays off`, `Related`, `Dismiss`;
  - temporal pair: `Accept` (reveals a `<select aria-label="Relation">` with before/on/after/by, defaulted to the proposal, then `Apply`), `Related`, `Dismiss`;
  - thread lifecycle: `Close thread`, `Keep open`, `Dismiss finding`;
  - commitment lifecycle: `Fulfilled`, `Broken`, `Expired`, `Keep open`, `Dismiss finding`.
  - A closure or resolution button reveals its form: an optional beat `<textarea aria-label="Closing beat">`, an evidence scene `<select aria-label="Evidence scene">` whose options are the read's `scenes` (by title, newest first), defaulted to the proposal's first evidence scene and required only when a beat is written, then `Apply` / `Cancel` (§12.4).
  - **Due copy:** when a merge's source has a due and the canonical has none, a checkbox "Also copy the due date “<due>” to <canonical>" (§5.1).
- Errors, read from `ApiError.kind`:
  - `liveness_mismatch` shows Decision 21's sentence with `Merge anyway`, which resubmits the same body plus `accept_status_change: true`;
  - `stale_candidate`: `const current = api.staleCurrent(error)`; when non-null, `applyCurrent(id, current)`; then `reread()` either way, never `refresh()`;
  - `not_found` re-reads and shows "This finding is no longer pending.";
  - a 2xx apply or dismiss calls `wrote()`, replaces the address with the group's (`ledgerHref(cid, {section: "continuity", group})`) and shows the one-line confirmation (Decision 24);
  - anything else shows `errorText`.
- A candidate address whose id is not among the visible candidates shows its group's list with "This finding is no longer pending." (§12.1), but only once a candidates read has settled (`candidates !== null`): a deep link from Todo never flashes the message while reading.
- [ ] **Step 1: Write the failing tests** (§28.9):
  - `a candidate opens a read-only detail`: no `textarea`, the records and signals are present, and the sidebar is labelled.
  - `apply requires an explicit action`: opening makes no `applyCandidate` call.
  - `the canonical side is selectable`: `Keep Winifred's chart` sends `{op: "alias", canonical: "thread:winifred-s-chart", expect_fingerprint}`, and `Keep Mara's map` the other.
  - `a liveness mismatch confirmation resubmits with the flag`.
  - `the due copy is sent when ticked`.
  - `a closure reveals its form and sends the beat with its evidence scene`.
  - `a stale 409 re-renders current records and does not start a run`: the new title shows, `reconcileContinuity` is not called, and a resubmitted `Keep …` carries the 409's `current.fingerprint` as `expect_fingerprint`.
  - `an evidence 409 keeps the actions disabled and offers Refresh`.
  - `a 409 with a malformed current body falls back to a re-read` (`staleCurrent` returns null).
  - `a successful apply returns to the group list`: the address is the group's and the confirmation shows.
  - `a deep link does not claim not-pending while reading`: a never-resolving candidates read shows no such message.
  - `commitment pairs never offer continues`: no "continues" or "subthread" button on a commitment pair.
  - `record titles link to their ledger rows, and no raw id is shown`: the title chip's `href` is the row address, and no rendered text matches `/^\d{3}--/` or `/^characters:/`.
  - `deep links open the right group and candidate`.
  - `an apply answered not_found shows the finding is no longer pending` (Review Focus 5).
  - `cross-type pairs never offer a merge`: no `Keep …` button.
  - `stale findings disable every action`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/LedgerContinuity.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and the typecheck.** Same command, then `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5: Run the eslint ratchet.** `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(ledger): read-only finding detail with explicit review actions`

---

### Task 18: Eval case `continuity-reconcile` (§28.10 cases 2–8, reconcile half)

**Files:**
- Modify: `evals/graders.py`, `evals/cases.py`, `evals/README.md`
- Create: `evals/recordings/continuity-reconcile.compliant.json`, `continuity-reconcile.undecodable.json`, `continuity-reconcile.merged.json`, `continuity-reconcile.eager.json`, `continuity-reconcile.unfounded.json`
- Test: `tests/test_eval_graders.py`; `tests/test_evals.py` (unchanged)

**Interfaces:**
- `graders.grade_reconcile(text: str, expected: dict[str, dict], vocab: dict[str, tuple[str, ...]], known: set[str]) -> list[Check]`. It scores the raw `extract_object` result, like `grade_absorb`. Checks:
  - `reconcile.json` (short-circuits the rest);
  - `reconcile.shape`;
  - `reconcile.enum`: every raw decision is in its candidate's vocabulary;
  - `reconcile.covers`;
  - `reconcile.evidence`: every `close`/`fulfilled`/`broken`/`expired` has a non-empty reason and a known scene;
  - `reconcile.distinct` (case 2, `c2` → `distinct` or `related`; deviation 26);
  - `reconcile.continuation` (case 3, `c3` → `continuation` or `subthread` with `from` = the concrete record; deviation 26);
  - `reconcile.cross_type` (case 4, `c4` → `pays_off` or `related`);
  - `reconcile.close` (case 5, `c5` → `close`);
  - `reconcile.keep_open` (case 6, `c6` → `keep_open`);
  - `reconcile.fulfilled` (case 7, `c7` → `fulfilled`);
  - `reconcile.unproven` (case 8, `c8` → `keep_open` or `uncertain`).
- `cases.build_continuity_reconcile() -> dict`: a campaign whose records play each case's role, with every title and beat written verbatim in the case (evals does not import tests):
  - c2 (`possible_duplicate`, two **threads**, `same_thread`): "Who runs the Saltmarch smuggling" and "Who bribes the Saltmarch harbourmaster";
  - c3 (`possible_duplicate`, two **threads**, `same_thread`, since `continuation` is thread-only): "Seraphine's debts" and "What Seraphine's debt to Mara costs her", the second the concrete one;
  - c4 (`possible_relation`, `cross`): the thread "Find the ledger" and the commitment "Pay Mara for finding the ledger";
  - c5 (`possible_thread_closure`, `thread`): "Mara's map", whose last beats answer it, touched in the last scene;
  - c6 (`possible_thread_closure`, `thread`): "Winifred's chart", old and unanswered;
  - c7 (`possible_commitment_resolution`, `commitment`): "Mara's oath" with a passed due and a beat showing it kept;
  - c8 (`possible_commitment_resolution`, `commitment`): a passed-due commitment with no outcome beat.

  It returns `ctx` with `cid` and hand-specified `candidates` (kind and refs per case), which `_reconcile_prompt` sends through the production `reconcile.build_payload` / `build_prompt`, storing `ctx["payload"]`, `ctx["vocab"]`, `ctx["known"]` and `ctx["expected"]`. It asserts that each built candidate's vocabulary is the one its case needs.
- `grade_continuity_reconcile(ctx, output)` is `graders.grade_prompt(ctx["messages"], {...})`, with one needle per `DECISIONS` word (`f'"{w}"'`) and one per system-prompt phrase from Task 5, plus `grade_reconcile(...)`.
- Recordings and their exact declared failures:
  - `compliant`: none;
  - `undecodable`: `("reconcile.json",)`;
  - `merged`: c2 and c3 both `duplicate`, each with valid `from`/`to` letters so `reconcile.shape` and `reconcile.enum` stay green → `("reconcile.distinct", "reconcile.continuation")`;
  - `eager`: c6 `close` with evidence and c8 `fulfilled` with evidence → `("reconcile.keep_open", "reconcile.unproven")`;
  - `unfounded`: c5 `close` with `evidence_scenes: []` → `("reconcile.evidence",)`.
- [ ] **Step 1: Write the failing grader tests:** `test_reconcile_prose_fails_json_only`, `test_reconcile_cross_type_duplicate_fails_enum`, `test_reconcile_closure_without_evidence_fails_evidence`, `test_reconcile_missing_candidate_fails_covers`, `test_reconcile_compliant_passes`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** the grader, the case and the five recordings. In `evals/README.md`: add the case row; the pass/fail count becomes eight; extend the counterexample list. Every new command line spells both the `.venv/bin/python` and the `.venv\Scripts\python.exe` forms (`test_install_scripts`).
- [ ] **Step 4: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py tests/test_evals.py tests/test_install_scripts.py -q -p no:cacheprovider`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 5: Commit:** `test(evals): continuity-reconcile case for pair and lifecycle decisions`

---

### Task 19: Slice gate

- [ ] **Step 1:** From the repo root, run `make check-lint check-mypy check-eslint check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 2:** Run `make check-py PY=$PWD/backend/.venv/bin/python`: the full backend suite under coverage, including the eval replay (`tests/test_evals*`) and `test_llm_fakes`. Expected: PASS, apart from failures in the slice ledger's base record.
- [ ] **Step 3:** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS (the `dict` body and `ContinuityDismiss` under pydantic 1.10).
- [ ] **Step 4:** Run `make check-web`. Expected: PASS (typecheck plus every vitest file under coverage).
- [ ] **Step 5: Re-read the docs.**
  - `docs/store-guarantees.md` lists no campaign files today (`grep -n '\.json' docs/store-guarantees.md` finds none), so it needs no edit; record that in the gate commit.
  - Confirm that `CLAUDE.md`, `CONTRIBUTING.md`, `templates/README.md`, `evals/README.md`, `revision.py`, `chores.py`, `todo.py`, `routes/__init__.py` and `store/continuity/__init__.py` say what the code does.
- [ ] **Step 6: Amend the spec.** In `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`, add a bullet for each "Deliberate deviation" below, citing its Decision. For deviation 22, also edit §7.0's table: replace the `reconcile` and `review` "May import" cells and add a `pending` row with exactly those edges. Then run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py -q -p no:cacheprovider` and confirm, by reading each module's import block, that the table names every `grimoire` edge. Commit: `docs(spec): record slice D deviations`.
- [ ] **Step 7: Implementation → done.** Run `/codex:review` against the slice diff. Where Codex is unavailable, record an independent reviewer that checks claims against the code. Resolve the findings, then commit.
- [ ] **Step 8: Done → actually done.** Run `/codex:adversarial-review` against the diff **and** the spec. Ask specifically whether the diff implements §31 Slice D: §6, §9.4's sweep half, §11, §12, §18.1–18.2, §21's new routes, §22, §23–§25 for reconcile, §26's rows, §28.4/§28.7/§28.9/§28.10 cases 2–8, §29, and C's backlog (a)–(d). Look for gaps, drift and quietly dropped requirements. Record a substitute if Codex is unavailable. Resolve the findings, then commit. Confirm the slice ledger lists any hand-off to Slice E, F or G.

---

## Self-review notes

**Spec coverage (Slice D):**

| Spec section | Task |
|---|---|
| §5.5 ids, fingerprints, suppressions, keep-open label | 2, 12, 13 |
| §5.6 cache not repointed; gone evidence scene stale; next reconcile rebuilds it | 2, 6, 12, 13 |
| §5.7 force delete in the Ledger (Slice A F6) | 15 |
| §6 cache shape, malformed reads empty, revision bump at persist, revision.py list | 1, 6 |
| §6.1 / §6.2 kinds, groups, chores | 1, 2, 14, 16 |
| §7.0 `candidates` (leaf, DOMAIN_MODULES), `reconcile`, `review` | 1, 3–6, 12 |
| §9.4 sweep half: threadpool, per-chunk saves, `RECONCILE_WARM_LIMIT`, `warm_window`, width, failure, unmetered | 4 |
| §11.1 run model, `reserve_campaign_background`, triggers, steps 1–4, data-dir | 3, 6, 7, 8, 9 |
| §11.2 bounded input, known scene set, no transcripts | 5 |
| §11.3 vocabulary, directions, temporal | 5 |
| §11.4 positive evidence | 5 |
| §11.5 nothing but the cache changes (plot, commitments, aliases, links, events.json, scene ideas) | 8 (`test_a_duplicate_proposal_mutates_nothing_until_apply`, with a temporal pair), 11 |
| §7.0 required projection `candidates.records`; §27 frozen sweep gains the continuity projections | 2 (Step 5) |
| §11.6 writer guard and CONTRIBUTING row | 11 |
| §12.1 placement, addresses, missing findings, rail | 15, 16, 17 |
| §12.2 joined read, stale, diagnostics, detail contents | 13, 17 |
| §12.3 overlap actions, liveness confirmation, due copy, temporal accept | 12, 13, 17 |
| §12.4 closure actions, `last_scene` rule, lock, shared helper | 12, 13, 17 |
| §12.5 Merged note links to the Reviewed group | 15 |
| §12.6 Reviewed links / merges, Broken | 16 |
| §12.7 Dismissed, live flag, Restore | 13, 16 |
| §12.8 suppressions and cache not journalled; alias, link and status journalled | 12, 13 |
| §12.9 refresh progress, no-connection refresh, stale 409 without a run | 16, 17 |
| §18 preamble docstrings | 14 |
| §18.1 embeddings chore, `?section=` | 14, 15 |
| §18.2 continuity chores, live filter, ITEMS | 2, 14 |
| §21 new routes and `PUT /chronicle` change | 8, 9, 13 |
| §22 apply order, stale 409, partial write, dismiss, persist race | 6, 12, 13 |
| §23 template family, verify_templates, README, `_rendered_prompts`, cassette | 5 |
| §24 parsing | 5, 8 |
| §25.1 one call per run; §25.2 worker thread, changed×all vs all×all, cap, `pairs_capped`; §25.4 | 3, 8, 14 |
| §26 reconcile LLM fails, reservation fails, cache malformed, concurrent runs, stale apply, embedding unavailable | 4, 6, 7, 8, 9, 13 |
| §28.4 every bullet (the reviewed temporal link's pressure in `test_temporal_accept_writes_the_link`) | 3, 5, 6, 8, 9, 12, 13 |
| §28.7 every bullet in scope | 14 (`owed` is Slice B's) |
| §28.9 Continuity review bullets | 16, 17 |
| §28.10 cases 2–8 (reconcile half) | 18 |
| §29 task, route, meter, log counts, docs note | 5, 8 |
| §30 wording | Global Constraints, 16, 17 |
| AC3, AC4, AC13, AC15, AC16 | 14, 8, 14, 12–13, 13 |
| C backlog (a), (b), (c), (d) | 10, 8, 4, 14 |
| CLAUDE.md inventory | 9 |

**Deliberate deviations from the spec text** (amended in Task 19 Step 6):

1. A seventh module, `continuity.pending`, holds the current fingerprint, the verdicts and the live filter (Decision 1). It goes beyond §7.0's table, and `review` and `reconcile` import it.
2. `reserve_campaign_background` returns `tuple[Run, bool] | None`, not `Run | None`. `start_or_existing` gains `single_live` (Decision 5).
3. The automatic trigger also fires on a journalled **resume**, because §11.1 excludes only the idempotent replay (Decision 6).
4. Model-only nominations, meaning temporal pairs and touched-record lifecycle re-checks, are persisted only with a proposal. A declined one is cached as `settled` and hidden (Decision 9). §6's "`proposal` is `null` when no model adjudicated" still holds for every persisted deterministic finding.
5. `warm_window` is seeded with `cid` plus the run stamp, not `cid` alone. `similarity.semantic` gains `warm_limit` and `loaded`, though C planned to reuse it unchanged (Decision 12).
6. §11.2's active and unresolved records are sent as the records the capped candidates name (Decision 13).
7. Event sides of temporal pair fingerprints use `{title: name, due: date}` (Decision 3).
8. The generation fence ignores a stored generation from this machine's future (Decision 4).
9. The apply body is a `dict`, not a `BaseModel` (Decision 15).
10. `copy_due` is honoured inside apply, as a separate journalled write through `routes/ledger.py` (Decision 16), instead of a second client PUT (§5.1).
11. Dismiss refuses a stale finding unless the optional `expect_fingerprint` it gains (beyond §21's `{decision}`) equals the current one (Decision 17).
12. The candidates read adds `beats`, `due`, `stale_reason`, `names`, `scenes` and `run`. `GET /continuity` suppressions add `live` and `titles`. A failed reconcile run's `error` carries `sweep` (Decisions 23, 24).
13. A review staged before a merge is refused at save with `edits_target_merged`, not re-staged (Decision 19; Slice C deviation 12).
14. The Todo live filter also reads `events.json`, for temporal link ids and event sides, beyond §18.2's "three small JSON reads". It still does no involvement, chronicle, calendar or embedding work.
15. `unpriced`'s fix adopts `/config?section=pricing` (§18.1 "may").
16. The Ledger's facts address stays the bare `/ledger`, and the group slugs are this plan's (Decision 22), since the spec names neither.
17. `reconcile` is not in `locks.DOMAIN_MODULES` although §7.0 names every module that writes continuity_candidates.json: it writes only through `candidates.write`, and `test_lock_domain_guard` rejects a listed module with no direct write. Its persists still hold `campaign_lock` (`test_persists_hold_the_campaign_lock`).
18. §11.3's same-type vocabulary and §12.3's "A continues B" are read per type against §5.3: `continuation` and `subthread` are offered and accepted for thread pairs only; a commitment pair's vocabulary is `duplicate, related, distinct, uncertain`.
19. Apply and dismiss answer 404 `not_found` ("no longer pending"), not 409 `stale_candidate`, for a finding that is suppressed, satisfied, gone (deleted or merged away) or settled: those are hidden from the GET, and the reader's view should say the finding is gone rather than offer to resubmit. A stale finding resubmitted against its current fingerprint is applied (§22 step 5, §12.9).
20. `basis.scored` (`{ref: generation}`) is added beside §6's basis keys, and the identity hash is salted with the embedding space, so a capped sweep rotates and a space change reaches every ref (Decisions 8, 10).
21. While continuity.json's file, aliases, links or suppressions are malformed, every cached finding is `unknown`: hidden, kept, and never written over, and discovery adds nothing (Decision 2).
22. §7.0's "May import" column is amended to the rows `test_import_guard` enforces. `candidates` stays a leaf as §7.0 says (local `_split`, no `canon`). The other rows are:
    - `pending` (new): `fieldtext`, `candidates`, `canon`, `doc`, `effective`;
    - `reconcile`: `embeddings`, `prompts`, `aging`, `calendars`, `chronicle`, `clock`, `embed_space`, `errors`, `fieldtext`, `locks`, `paths`, `relationships`, `revision`, `scene_ids`, `vectors`, `absorb.parse`, `campaigns.paths`, `scenes.read`, `candidates`, `canon`, `doc`, `effective`, `involvement`, `pending`, `pressure`, `similarity`. It still never imports a writer the §11.6 guard forbids;
    - `review`: the Slice A edges already in the tree (`commitments`, `events`, `fieldtext`, `locks`, `paths`, `plot`, `undo`, `canon`, `doc`, `effective`) plus `candidates`, `pending` and `scenes.read`.

    Slice A's ledger once had to fix a stale §7.0 table (F13), so Task 19 Step 6 replaces those three rows and adds `pending`'s, instead of only adding a bullet.
23. The writer guard's `FORBIDDEN` also covers the public mutators of `events` and `scene_ideas`, beyond §11.6's list, because §11.5 names events.json and scene ideas among what reconciliation never changes (Task 11).
24. Model-only nominations are filtered by verdict before adjudication. A cached temporal finding is retracted once the temporal source was read and no longer nominates it (Decisions 8 and 11). §11.1 step 3 and §11.3 are kept, not changed; this is recorded because §6.2 and §11.1 say nothing about retracting a pair kind.
25. Dismiss writes the suppression before dropping the cache record, and a dismiss that fails after the suppression landed answers 500 `partial_dismiss` and bumps the revision itself (Decisions 16 and 17). This orders §22's "suppression/cache cleanup" step internally.
26. §28.10 cases 2 and 3 are graded as "not merged" rather than by one word (Task 18, amended by the slice ledger's Task 18 review fix): `reconcile.distinct` accepts `distinct` or `related` for c2, and `reconcile.continuation` accepts `continuation` or `subthread` for c3, each with the concrete record as `from`. The system prompt offers `related` for questions that bear on each other and names `continuation` and `subthread` with no rule between them; a `duplicate` still fails both checks.
27. A failed reconcile run's `error` also carries `saved` (whether persist 1 landed) and `follow_on` (whether the follow-on pass failed after the first landed), and the Refresh note is chosen from them rather than always reading "basic findings are listed" (refined during implementation by the slice review fix pass; spec §31 "Additive response fields"). The kind alone cannot say which persist failed, and a POST's own refusal (`RefreshRefused`) is the only error known to have run nothing. Cost if wrong: one note's wording on a rare path.

**Open questions (none block execution):**

- After Slice F lands, `graph` must satisfy the writer guard. It is listed now and skipped while absent.
- Whether the Story Graph's "Open ledger entry" uses `ledgerHref` is Slice F's call; `ledgerPaths.ts` is ready for it.

**Type consistency:**

- `candidates.KINDS`, `PAIR_KINDS`, `LIFECYCLE_KINDS` are defined in Task 1 and used in Tasks 2, 3, 5, 6 and 14.
- `pending.Current`, `fingerprint`, `verdict`, `findings`, `rows`, `label`, `evidence_ok`, `DECISION_LABELS`, `GROUP_OF`, `CHORE_OF` and `VISIBLE` are defined in Task 2 and used in Tasks 3, 6, 12, 13 and 14.
- `effective.later_scene` is defined in Task 2 and used in Task 12.
- `reconcile.Sweep`, `discover`, `generation` and `pressure_by_ref` are defined in Task 3 (embeddings in 4) and used in Tasks 6, 8 and 13. `select`, `build_payload`, `build_prompt` and `parse_output` are defined in Task 5 and used in Tasks 8 and 18. `persist_found` and `persist_proposals` are defined in Task 6 and used in Task 8.
- `runs.reserve_campaign_background` is defined in Task 7 and used in Task 8. `continuity_routes.schedule_reconcile` is defined in Task 8 and used in Task 9.
- `ledger.move_thread` / `move_commitment` and the `review.*` apply functions are defined in Task 12 and used in Task 13.
- The TS `ContinuityCandidates`, `ContinuityApply`, `ReconcileResult` and `ContinuityState` (Task 15) match Task 13's and Task 8's JSON, including `stale_reason`, `names`, `scenes` and a failed run's `error.sweep`. `api.awaitCampaignRun` and `api.staleCurrent` (Task 15) are used in Tasks 16 and 17. `ledgerHref` and `parseLedgerTail` (Task 15) are used in Tasks 16 and 17, and the chore and item `fix` strings (Task 14) follow the same grammar.

---

## Plan-gate review resolution

Each finding was checked against the code on the base (Slice A as implemented, the B and C plans) and against the spec. Duplicates reported under several lenses share one resolution and are listed together.

| # | Finding (lens) | Verdict | Resolution |
|---|---|---|---|
| 1 | Writer guard names `review.settle`/`dismiss`/`restore_suppression` before Task 12 creates them (guards; also behaviour, tests) | Confirmed: review.py defines only `describe` … `forget_ref` | Applied. Task 11's `FORBIDDEN` lists the five review mutators that exist; Task 12 adds the three in the commit that creates them and runs the guard file in its Step 4. |
| 2 | `start_or_existing` is at C901 10 already (guards) | Confirmed with ruff at max-complexity 3: 10; `max-complexity = 10`; no `runs.py` baseline entry | Applied. Decision 5 puts the scan in `_live_single` and the indexing in `_adopt`, one added branch; Task 7 Step 4 checks no C901 for runs.py. |
| 3 | Adopted attempt invisible to `GET .../runs?attempt=`, alias never reaped (guards; behaviour; tests) | Confirmed: `_subject_runs` filters on `r.attempt_id`; `reap` drops only the run's own key | Applied. `Run.adopted_attempts`; `_subject_runs` matches it; `reap` drops each alias; tests for the HTTP read, a two-draft regression and the reap. |
| 4 | `schedule_reconcile` copying `_start_background` leaves the reservation outside the `try` (guards) | Confirmed: scenes.py reserves before its `try` | Applied. Task 8 spells `schedule_reconcile` out: reservation, touched computation and start in one `try`, release only for a fresh, unstarted run. |
| 5 | `errors` missing from reconcile's imports; probe passes the space dict (guards) | Confirmed: `vectors.load(space: str, …)`, `resolve()` returns a dict | Applied. `errors` (plus `involvement`, `relationships` for #14) in the import line; probe is `vectors.load(space["space"], …)`; `Sweep.space`/`model` from the dict. |
| 6 | `error.body.current` is `unknown`, so `applyCurrent(…)` fails `tsc` (guards) | Confirmed: `body?: Record<string, unknown>` | Applied. Exported `api.staleCurrent(err)` validates the shape; Task 17 falls back to `reread()` alone on `null`; a client test pins it. |
| 7 | Files lists offer alternative test files the commands do not run (guards) | Confirmed | Applied. Task 7 uses `tests/test_campaign_background.py` (new); Task 10 uses `tests/test_continuity_review.py`. |
| 8 | `continuation` offered and parsed for commitment pairs, but §5.3 makes `continues` thread-only (spec; behaviour) | Confirmed: `effective.RELATIONS["continues"]` is thread→thread | Applied. `same_thread` / `same_commitment` vocabularies; the parser downgrades `continuation`/`subthread` (and any `RELATIONS`-refused relation) on commitments; `plan_apply` test for `continues` on commitments; Task 17 shows continues/subthread for thread pairs only; deviation 18; eval c3 is two threads. |
| 9 | Apply skips §22's strict canonicalization (spec) | Confirmed: `canon._aliases(strict=True)` exists; `doc.read` reads a malformed section as empty | Applied with #23: `check_candidate` and `dismiss` refuse 409 `malformed` through `_require_wellformed` first; tests for both. |
| 10 | §12.9 resubmission and §21's `expect_fingerprint` made meaningless (spec; behaviour; tests) | Confirmed: §22 step 5 compares the recomputed fingerprint with `expect_fingerprint` | Applied option (a): a `stale` verdict passes when `expect_fingerprint` equals the current fingerprint; the UI re-enables actions bound to `current.fingerprint` after a records 409; route, store and UI tests. Dismiss gains an optional `expect_fingerprint` for the same case. |
| 11 | Capped sweeps rescore the same prefix once the basis is full; a space change never reaches the tail (spec; behaviour) | Confirmed by walking Decisions 8/10 | Applied with a different mechanism than proposed: `basis.scored` orders full sweeps least-recently-scored first, and the identity hash is salted with the space, so refs not rescored after a space change stay changed for incremental sweeps too. Rejected parts: dropping unfinished entries (walked through: it livelocks between the first refs once ordering falls back to ref) and keeping the old space until an uncapped finish (a campaign too large ever to finish uncapped would never record it). Tests rewritten (#51) and a space-change test added; deviation 20. |
| 12 | Touched re-checks lost when a save adopts a live sweep (spec; behaviour) | Confirmed | Applied both proposals. `_PENDING_TOUCHED` collects an adopter's refs; the live run runs one extra incremental pass with them, and every fresh run starts by taking the set (Task 8 step 9; the runner has no completion callback, so a successor cannot be started from inside). Decision 11 also treats refs whose existing basis entry changed (same space) as touched, which catches a skipped automatic run. |
| 13 | Todo's `owed` chore/items and unpriced items do not use the new addresses (spec) | Confirmed: todo.py:340, :819, :829 | Applied. `_ledger_href`; `owed` → `/ledger/commitments[/<id>]`; `_items_unpriced` → `?section=pricing`; tests. Slice B pins the owed items' id/label/detail, not `fix`. |
| 14 | §11.2's involvement actors missing from the payload (spec) | Confirmed | Applied. Per-record `actors` from `involvement.of`, named by `relationships.actor_name`, bounded by `RECONCILE_ACTORS`; rendered in `user.j2`; test. |
| 15 | Cached vectors loaded twice per sweep (spec) | Confirmed: C's `semantic` loads `cached` itself | Applied. `semantic(..., loaded=)`; spy asserts one `vectors.load` per sweep. |
| 16 | `reconcile` outside `DOMAIN_MODULES` unrecorded (spec) | Confirmed | Applied. Deviation 17 and `test_persists_hold_the_campaign_lock`. |
| 17 | CLAUDE.md's write-token paragraph not updated (spec) | Confirmed | Applied. Task 6 adds the two persists to its detached-writer sentence, Task 13 the partial-write 500 to its non-2xx sentence; both run `test_docs_guard`. |
| 18 | Writer guard does not scan `pending` (spec) | Confirmed | Applied. `pending` in `SCANNED` and `REQUIRED_PRESENT`, docstring says why. |
| 19 | §28.4 "a reviewed temporal link contributes to pressure" not pinned (spec; tests) | Confirmed | Applied. `test_temporal_accept_writes_the_link` asserts a `linked_deadline` item with `relation "before"` and that the next sweep no longer nominates the pair. |
| 20 | A renamed or deleted evidence scene leaves a finding stuck (behaviour; ui; tests) | Confirmed: fingerprints exclude scene ids; carry-forward keeps the proposal; `select` skips it | Applied. `pending.evidence_ok`; persist 1 voids such proposals so `select` re-asks them (§5.6); the GET flags them `stale` with `stale_reason "evidence"`; dismiss skips the evidence check; tests in Tasks 2, 6, 12, 13 and 17. |
| 21 | A malformed continuity.json resurrects dismissals and rediscovers merges; `events` unreadability not mapped; `put_suppression`'s `ContinuityError` is a 500 (behaviour) | Confirmed: `doc.read` empties malformed sections; `_mutable` raises | Applied. `Current.continuity_malformed` makes every finding `unknown`; discovery adds nothing; persists leave the cache untouched; apply/dismiss 409 `malformed`; `Current.unreadable` maps `Ledgers.unreadable` including `events`; dismiss maps `ContinuityError`; deviation 21. |
| 22 | Deterministic stale/overdue findings retained after the condition clears (behaviour) | Confirmed: `_days_since` can go non-stale on a rewound clock without moving the fingerprint | Applied. `Sweep.lifecycle_checked`; persist 1 drops an unrenominated `stale`/`overdue` finding when its source was read; tests for the rewind and for a raising plugin. |
| 23 | Embedding failure or the warm limit records hashes for vectorless refs, dropping semantic pairs (behaviour) | Confirmed against Decision 8 | Applied. With a space configured, a vectorless ref is not `rescored`: its basis entry stays and its pairs are retained; two tests. |
| 24 | A hand-edited cache with bad nested fields 500s the GET and the global To do page (behaviour) | Confirmed: Todo builders run without a per-builder catch | Applied. `candidates.read` normalizes `signals`, `proposal` fields and ref prefixes per kind; `pending.findings` guards each record; tests in Tasks 1, 2, 13 and 14. |
| 25 | A lone surrogate crashes every sweep and the GET (behaviour) | Confirmed: `canon._digest` encodes strict UTF-8 | Applied. Identity hashes use `surrogatepass`; `pending.fingerprint` catches `UnicodeEncodeError` (verdict `unknown`); discovery skips such a record; Slice A's `canon` is untouched; tests. |
| 26 | `_touched_refs` runs on client edits outside the never-raise guard; applied ids are not unique (behaviour) | Confirmed: `apply_edits` records outcomes by position | Applied. Computed inside `schedule_reconcile`'s `try` from `progress["edits"]` by position, with shape checks; `test_a_malformed_edit_still_saves`. |
| 27 | `single_live` adopts a run already being cancelled (behaviour) | Confirmed: `runner.cancel` sets `cancel_requested` and the run stays `running` | Applied. `_live_single` skips `cancel_requested`; test case added. |
| 28 | "A continuity sweep is running." is never followed; a failed adopted incremental loses `sweep` (behaviour; ui; tests) | Confirmed: `awaitRunAt` throws away `result` for a non-landed run, and it and `draftBase` are private | Applied. `api.awaitCampaignRun`; the hook follows a run seen on load; failed runs carry `error.sweep`; tests for both. |
| 29 | `continuity-closures` always opens Possible closures (behaviour; ui; tests) | Confirmed | Applied. `closures` when a thread closure is live, else `resolutions`; test. |
| 30 | The candidates read may run calendar-plugin code under the campaign lock (behaviour) | Confirmed: `best_effort_campaign_lock` holds when it gets the lock | Applied. `pressure_by_ref` and the scene map are computed before the hold; test. |
| 31 | Persists that change nothing still bump the revision (behaviour) | Confirmed: `ClockPanel` treats a moved token as `campaign_moved` | Applied. Decision 7 step 6 skips the write and the bump when records and basis are unchanged; test. |
| 32 | Row highlight re-scrolls on every render; closed rows cannot be highlighted (ui) | Confirmed: `rows` is not memoized; closed rows are filtered unless `showRetired` | Applied. Memoized rows, scroll once per address, `showRetired(true)` for a filtered addressed row; two tests. |
| 33 | Continuity writes and Ledger edits do not refresh each other (ui) | Confirmed | Applied. One shared epoch with `onWrote`; test. |
| 34 | A successful apply ends on "no longer pending"; deep links flash it (ui) | Confirmed | Applied. Navigate to the group with a confirmation; the message only after a settled read; two tests. |
| 35 | The detail shows raw ids and its records are not navigable (ui) | Confirmed: rows carry scene ids; signals carry refs | Applied. The GET adds `names` and `scenes`; record titles are chips to their ledger rows; test that no raw id renders. |
| 36 | Raw decision enums in copy; stale marker unspecified; `llm: "off"` unexplained (ui) | Confirmed | Applied. Decision 25's hedged label table on both sides, visible stale text, the no-connection line; tests. |
| 37 | On a phone the only way past a stale finding is hidden in the column (ui) | Confirmed: `PageShell` hides the column at ≤720px | Applied. A Refresh button in the main pane and beside the stale message; test at 375px. |
| 38 | A refresh is not cancelled on unmount or campaign switch (ui) | Confirmed: LedgerView is not keyed on `cid` | Applied. One `AbortController` per `cid`; test. |
| 39 | Two suites with hand-copied mock factories, not `testkit/` (ui) | Confirmed: LedgerView.test.tsx holds the whole factory | Applied. `testkit/ledgerMocks.tsx` and `testkit/ledgerHarness.tsx`, used by both suites. |
| 40 | The capped-sweep test cannot fail (tests) | Confirmed: at 3 pairs over 5 records no ref finishes | Applied. Rewritten with `RECONCILE_MAX_PAIRS = 5` over a fully populated basis: exact `rescored`, the next first ref, and full coverage over successive sweeps (see #11). |
| 41 | `column()` queries "Ledger sections", but LedgerView never gets that label (tests) | **Rejected**: `LedgerView.tsx:587` already passes `columnLabel="Ledger sections"` (in HEAD, not Slice B's working edits) | The plan now says so, and why the name is needed: the finding detail adds a second `complementary` landmark. |
| 42 | The Todo encoded-address test cannot fail on encoding (tests) | Confirmed: candidate ids are hashes | Applied. Review Focus 1 narrowed; the continuity item test asserts group and id; encoding is pinned in `ledgerPaths.test.ts` and in the new `owed` row-address test. |
| 43 | `rail.test.ts` is listed as RED but already passes (tests) | Confirmed: `isUnder` accepts any `base + "/"` prefix | Applied. Marked as a regression pin expected to pass in Step 2. |
| 44 | `test_warm_window_rotates_between_runs` is flaky (tests) | Confirmed: crc32 offsets collide one time in ten | Applied. Literal stamps whose offsets the test asserts differ. |
| 45 | Whole-body equality cannot hold (`absorbed` timestamp) (tests) | Confirmed: chronicle.py stamps `absorbed` | Applied. Status, `applied`, `failures` and the key set. |
| 46 | `live` on suppressions may break `test_get_continuity_reads_each_ledger_once` (tests) | Confirmed: `effective.records` re-reads plot.json | Applied. `Current` built only when suppressions exist, from the route's `Ledgers`; a bounded-reads variant. |
| 47 | `test_verdicts` expects `satisfied` for an aliased pair, but `gone` matches first (tests) | Confirmed | Applied. The case is `gone`; the unreachable "share a live canonical" clause is removed from Decision 2. |
| 48 | Eval cases c2/c3 have no stated record types (tests) | Confirmed | Applied. Every case names its kind, types and vocabulary; c3 is two threads; `merged` carries valid `from`/`to`. |
| 49 | Blocking and absorbing route tests name no `llm_fakes` double (tests) | Confirmed: `HeldCassette` exists, and its `complete` consumes `stream` | Applied. `HeldCassette` for held tests; `from_cassette("campaign_flow")` or C's bodies for absorb-then-save; Task 10 seeds both records first. |

**Second round.** Each gap was checked against the plan text, the spec (§7.0, §11.1, §11.3, §11.5, §22, §27) and the code on the base (`routes/runs.py`, `routes/config.py`, `store/events.py`, `store/scene_ideas.py`, `tests/fixtures/frozen_campaign/sweep.py`, `frontend/src/components/review/useSceneReview.ts`).

| # | Gap (spec) | Verdict | Resolution |
|---|---|---|---|
| 50 | Suppressed or satisfied model-only nominations are re-sent every sweep, and cached temporal findings outlive their nomination (§11.1 step 3, §25.1, §11.3) | Confirmed. `select` sent every `model_only` entry not cached with a proposal, and persist 2's verdict filter drops a suppressed or satisfied one, so it never stays cached and is re-asked each End Scene. A `related_to` link does not stop the temporal nomination, which only excludes before/on/after/by links. A pair side carries no links and an event side's date does not move with time, so a passed event or a temporal link to another event leaves the fingerprint and verdict `live`. | Applied. Decision 11: `discover` keeps a model-only nomination only when `pending.verdict` is `live`, and records `temporal_ids` before the filter. `select` re-checks against the cache persist 1 left (Decision 13, Task 5). Decision 8 retracts a cached temporal pair when `lifecycle_checked["temporal"]` is true and the pair is not in `temporal_ids`. Tests: `test_model_only_nominations_are_verdict_filtered` (Task 3), `test_select_skips_suppressed_and_satisfied_model_only_nominations` (Task 5), and `test_a_temporal_finding_is_retracted_when_its_condition_clears`, with event-passed, link-to-another-event and raising-plugin variants (Task 6). Deviation 24. |
| 51 | `_PENDING_TOUCHED` at module scope leaks between apps, survives campaign delete and a storage move (CLAUDE.md Detached runs; §5.7 slug reuse) | Confirmed. `app.state.runs` is per app (`install_registry`) precisely so runs do not leak. `RunRegistry.forget_subject` exists for slug reuse, but the module dict was outside it. `put_data_dir` refuses only while a run is live, and refs can wait with no run. | Applied. Decision 6: the map is `RunRegistry._pending_touched` keyed by subject, under the registry lock (`pend_touched`/`take_touched`). `forget_subject` drops the subject's entry, and `put_data_dir` calls `runs.drop_pending_touched(app)` after a successful move. A `forgotten` run, like a `cancel_requested` one, never takes the set (Task 8 step 9). Tests: two registry and data-dir tests in Task 7, and `test_pending_touched_is_per_app_and_forgotten_with_the_campaign` (a new app; DELETE and recreate) in Task 9. Supersedes row 12's `_PENDING_TOUCHED`. |
| 52 | Dismiss drops the cache before writing the suppression, and a failed dismiss after a landed write leaves the token unmoved (§22; CLAUDE.md "a write answered non-2xx stamps for itself too") | Confirmed for dismiss and for `keep_open` apply. Partly rejected for restore: `restore_suppression` is one atomic `doc.drop_suppression` write, so a failure lands nothing and there is nothing to stamp. | Applied. `settle` writes the suppression first, then drops, and raises `PartialSettleError(landed)` when a later part fails (Decision 16, Task 12). The dismiss route bumps the revision on it and answers 500 `partial_dismiss`; a `ContinuityError` from the suppression now lands nothing and stays 409 `malformed` with no bump (Decision 17, Task 13). The Global Constraints revision line and Task 13's CLAUDE.md sentence name it. Tests: `test_dismiss_with_a_failing_cache_drop_still_suppresses` and `test_a_suppression_failure_lands_nothing` (Task 12), and `test_a_failed_dismiss_after_a_landed_write_moves_the_token` (Task 13). Deviation 25. |
| 53 | events.json and scene ideas are unpinned for §11.5 (§11.5, §11.6) | Confirmed. §11.5 names both; Task 8's test covered plot, commitments, aliases and links only, and `FORBIDDEN` had no events or scene-ideas names. | Applied. Task 8's test seeds a temporal pair, gets a `before` proposal from the cassette, asserts the proposal is cached (not vacuous), and compares events.json and the scene-ideas store byte for byte. Task 11's `FORBIDDEN` gains `events.{create, update, delete, fire, unfire}` and `scene_ideas.{add, set_status, mark_used, repoint_scenes}`, the public mutators in today's modules, which `test_the_guard_names_functions_that_exist` pins. The detector test and CONTRIBUTING row name them. Slice E's `scene_ideas.add` caller is `routes/scenes.py`, which is not scanned. Deviation 23. |
| 54 | `candidates.records` and `pending.findings` are absent from the frozen sweep (§7.0 projections, §27) | Confirmed. `sweep.py` has no continuity keys on the base, and B Task 10 adds only effective, pressure and drivers keys. | Applied. Task 2 Step 5 adds `continuity.candidates.records[{cid}]` and `continuity.pending.findings[{cid}]`, regenerates `snapshot.json` deliberately, and proves only keys with those two prefixes were added (B Task 10 Step 4's script, narrowed). It then runs `test_frozen_campaign.py`, including `test_the_read_only_sweep_writes_nothing`. File-structure and coverage rows added. |
| 55 | `candidates` is not a leaf, and `pending`/`reconcile`/`review` imports exceed §7.0's table (§7.0) | Confirmed. §7.0 says `candidates` imports "as `doc`" (atomic, locks, paths, campaigns.paths). `reconcile` and `review` already reach well past their rows; `review` did so in Slice A (`events`, `fieldtext`, `paths`, `canon`, `effective`). | Applied both options. `candidates` validates refs with a private `_split` and imports no `canon`, which `test_candidates_is_a_leaf` pins (Task 1). Deviation 22 lists the real rows for `pending`, `reconcile` and `review`, Slice A's existing `review` edges included. Task 19 Step 6 replaces those §7.0 cells and adds a `pending` row, rather than leaving a stale table behind a bullet. |
| 56 | Decision 19's "the generic save-error path is enough" has no frontend pin (Slice C hand-off (a)) | Confirmed. `useSceneReview.saveAbsorb` special-cases only `edit_conflicts` and otherwise sets `saveError` from `err.detail`. `ReviewPanel` renders "Could not save this review:" and "Try saving again", and no test drives a 409 `edits_target_merged`. | Applied. Task 10 adds a `SceneReview.test.tsx` case through `testkit/campaignHarness.tsx`: the detail naming "Mara's map" and "Winifred's chart" renders, no conflict rows appear, `Reject Mara's map` then `Try saving again` posts a batch without that row under the same commit token, and the save completes. It is a regression pin expected to pass at Step 2. The optional `edits[].index` row marking is rejected: the index counts the non-rejected batch, the detail already names the rows, and a second positional mapping beside `edit_conflicts`' adds a translation to get wrong for no gain. |
