# Continuity Capstone — Slice C: Similarity and Absorb Identity Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before absorb treats a proposed plot thread or commitment as new, compare it against plausible same-type existing records. Do this deterministically, with embeddings when configured, and with one batched resolver call when something is plausibly the same. Retarget the rows the resolver accepts. Flag the uncertain ones low. Give every examined row server-rendered alternatives the reviewer can swap to.

Alongside that, the slice:
- closes plot's silent slug-collision merge;
- makes absorb staging follow merges (aliases);
- ships the Settings embeddings disclosure before the first automatic continuity embedding call.

**Architecture:** Two new store modules sit on Slice A's substrate.

- `store/continuity/similarity.py` holds identity texts, lexical and structural signals, and embedding mechanics. It is shared, and Slice D's reconcile sweep reuses it unchanged.
- `store/continuity/identity.py` holds proposed-new detection, neighbour selection, the resolver prompt build and parse, decision acceptance, and the row rewrite.

The routed LLM call, its meter and its connection live in `routes/scenes.py`. The identity step is chained onto the extraction coroutine inside `_gather_phases`, and both its store passes run in a worker thread. `absorb.materializer` stages what the identity step decided. That covers `identity_check`, the `low` band, server-rendered alternatives, the §10.5 allocator and the §7.2 alias redirect. The review panel gains a chip and a swap. The Settings section is relabelled **Embeddings**.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), Jinja2 (StrictUndefined), pytest + `TestClient`; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements spec §31 Slice C. It covers these sections:

- §3.2, §3.3, §3.10 and the `matching` field;
- §7.0, the `similarity` and `identity` rows;
- §7.2, the absorb half, which Slice A deferred;
- §9 (all of it), §10 (all of it);
- §23 for the `continuity_identity` family and the absorb change;
- §24, §25.1 for the identity call, and §26 for the identity and embedding rows;
- §28.2, §28.3, and §28.10 cases 1–4 for the identity half;
- §29 for `continuity-identity`, and AC1, AC2, AC16 and AC18;
- Appendix A entries A2–A6, A10–A15 and T1.

Out of scope:
- Slice B (pressure, drivers, the consumer switch). This plan does not depend on it.
- Slice D: candidates, the reconcile sweep, `RECONCILE_WARM_LIMIT`, `warm_window` rotation, review UI, and `/config?section=`.
- AC3 and the §18.1 embeddings setup chore: Todo is Slice D's (§31). Slice C's only matching-mode text is the review panel's basic-matching line (Task 13).
- Refusing or re-staging a pending review whose targets became alias sources at merge-apply time: Slice D (deviation 12).
- Slices E, F and G.

**Branching:** Each slice gets its own branch stacked on the previous slice's tip, and the whole stack is rebased onto `main` at landing (the owner's direction). Work on `claude/continuity-capstone-slice-c` created from the **current Slice B tip** (`claude/continuity-capstone-slice-b`). Slice C does not depend on anything Slice B adds; it stacks on B only to keep one linear stack. If Slice A or B gains fixes after this branch is cut, rebase onto the new B tip. **Before Task 1:**
1. Run `git status`. If the tree holds edits that are not this slice's, stop and ask the owner. Never sweep them into a Slice C commit.
2. Record the base commit's gate. From the repo root, run `make check-py PY=$PWD/backend/.venv/bin/python` and `make check-web`, and write any pre-existing failure (test id and one-line cause) into the slice ledger (`.superpowers/sdd/2026-10-05-continuity-capstone-c-absorb-identity/progress.md`). Task 14 may only excuse failures listed there.
3. Give the same ledger a `## Slice D backlog` section, one line per hand-off this plan makes, so none lives only in this plan:
   - (a) refuse or re-stage a pending review whose plot/commitment targets became alias sources at merge-apply time (deviation 12);
   - (b) append `"continuity-reconcile"` to the `continuity` Route and widen its hint to §29's text (deviation 3);
   - (c) `RECONCILE_WARM_LIMIT` and the `warm_window` rotation, over `similarity.semantic` / `embed_missing` (§9.4 sweep half);
   - (d) AC3 and the §18.1 embeddings setup chore.

## Global Constraints

- **Privacy:** fixtures, eval recordings, docstrings and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch, phrases built from them, or ids already used as placeholders in the tree (`find-the-ledger`, `the-midnight-deadline`, `the-debt`, `salt-owed`). Never read `~/.grimoire`. No committed text describes store contents, counts or distributions. Every threshold below is justified structurally and marked "to be tuned against real prompts later".
- **Identity fields:** `absorb.parse.IDENTITY_FIELDS = ("why_new", "distinguished_from")`.
- **Resolver decisions:** `existing`, `new`, `uncertain`.
- **`identity_check` vocabulary:** decision `"existing" | "new" | "uncertain" | "unchecked"`; status `"accepted" | "downgraded" | "hint_only"`.
- **Phase block:** `{status: ok|degraded|failed|skipped, reason, attempted, budget_exhausted, counts}`, plus a fifth `phases` row named `identity`.
- **Task name:** `continuity-identity`. It is a literal in `_require_connection("continuity-identity", cid)` and in `store.usage.meter("continuity-identity", campaign=cid, scene=sid)`, both in `routes/scenes.py`.
- **Embedding deadline:** `deadline = min(monotonic() + embeddings.TIMEOUT, absorb budget deadline)`.
- **Neighbours:** retrieve at most the nearest 3 same-type neighbors for each proposed-new record.
- **Plot slug collision** (§10.5): a slug collision counts only when the stored thread is not closed **and** has the same casefolded title. Otherwise allocate `slug-N`.
- **Accepted `existing` rewrite:**
  - `id` := the canonical existing id;
  - plot `status` := the model's original `closed` or `advanced` if it gave one, else `advanced`;
  - commitment `kind` := `""`;
  - commitment `status` is kept only if it is in `commitments.RESOLVED`, else `""`;
  - `due` is kept only if the original row carried it.
- **Uncertain hint copy:** “Possible existing record — reject this row, or switch it to the existing record, if it is the same business.”
- **Review chip:** “Possible existing record”, with the action “Use <title> instead”.
- **Merge label suffix:** “→ merged into <title>”.
- **Settings section label:** **Embeddings**. The section id stays `"semantic"`.
- **Settings copy:** “With a connection and model set, Grimoire also embeds plot-thread and commitment summaries after each wrap-up to find possible overlaps. Recall depth does not control this. Set the connection to Off to stop all embedding.”
- **Embedding availability:** semantic matching is available iff `store.embed_space.resolve()` is non-None. `semantic_recall_depth` is never consulted.
- **Embedding calls:** stay unmetered, and from async code go through `run_in_threadpool`.
- **Logging:** log rows (`store.logs.record`) carry counts and modes only. No titles, beats, identity texts, reasons or prompts.
- **Imports inside `store/`:** at module scope, acyclic, binding submodules (`from ..absorb import parse as absorb_parse`, `from .. import embed_space, vectors`, `from ... import embeddings, prompts`), never names (`test_import_guard`). `store/continuity/__init__.py` stays import-free; its docstring's module list gains `similarity` and `identity`.
- **Imports in `routes/scenes.py`:** `run_in_threadpool` is already imported there (line 25) — do not add it again (ruff F811). The new store modules are bound as `from ..store.continuity import identity as continuity_identity, similarity as continuity_similarity`, because `scenes.py` already uses `identity` as a parameter name (the scene identity fence).
- **Broad catches:** every new `except Exception` carries the house-style inline reason, `# noqa: BLE001 -- <reason>`, or is narrowed to what the reader actually raises. A bare broad catch is a new (file, `BLE001`) finding and fails the ratchet.
- **Complexity:** every new function stays at or under ruff's `max-complexity = 10` (C901 is counted per file). `_identify`, `examine`, `decide`, `assign_ids` and `_attach_alternatives` are split into helpers (for example `_identity_status(exam, decisions)`, `_classify_row`) wherever they would cross it.
- **Per-task lint gate:** every backend task (1–9, 11, 12) ends its verification with, from the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`; every frontend task (10, 13) with `make check-eslint PY=$PWD/backend/.venv/bin/python`. These are not left to Task 14.
- **Lock domain:** no cid-taking public function in `similarity` or `identity` writes anything. Only the cid-free `similarity.semantic` writes, and it writes only the global vector cache. So neither module enters `locks.DOMAIN_MODULES`, `OUTSIDE_DOMAIN` or `UNREVIEWED`.
- **Pydantic:** no new models (`ChronicleSave.edits` is already `list[dict]`).
- **Lint ratchet:** `make check-lint`, `check-mypy` and `check-eslint` must not grow `lint-baselines/*.json`. An improvement is re-baselined (`make baseline`) in the same commit. Exception classes end in `Error` (ruff N818).
- **Running tests:**
  - backend, from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <paths> -q`;
  - frontend, from `frontend/`: `npx vitest run <files>`.

  One command per purpose. A `-k` filter never deselects a file listed with it.

## Review Focus

1. **Non-ASCII titles.** Two different CJK-titled threads both slug to `untitled`. Today the second is silently merged into the first.
   - The plot allocator gives it `untitled-2`.
   - `similarity.slug_equal` ignores the `untitled` fallback.
   - Character 3-grams still surface a genuinely reworded CJK duplicate as a candidate.

   Pinned in Task 2 (`test_materialize_plot_untitled_titles_never_merge`) and Task 3 (`test_cjk_reword_is_a_trigram_candidate_and_untitled_slugs_are_not_equal`).
2. **A resolver that answers wrongly in a well-formed way.** It may say `existing` for a closed thread, for a resolved commitment, for an id another row already moves, or for an id it was never offered. Each is downgraded to `uncertain` and band `low`. Nothing is reopened, and the save still writes only the new row. A record closed *between* the decision and staging is caught at staging too. Pinned in Task 7 (`test_existing_naming_a_closed_candidate_is_downgraded`, `test_second_row_mapping_to_the_same_record_is_downgraded`, `test_existing_naming_an_unoffered_id_is_uncertain`), Task 9 (`test_accepted_target_closed_before_staging_is_downgraded`) and Task 11 (`test_resolver_naming_a_closed_thread_never_reopens_it`).
3. **A merged record named by the extraction.** Until Slice B switches the absorb snapshot, the snapshot is physical, so the model can name the hidden alias source by id or by an exactly matching title.
   - Staging redirects to the canonical and labels the row “→ merged into <title>”.
   - A source and its canonical in one batch dedupe to one edit.
   - Identity candidates never list a hidden source.
   - A slug collision on an open source whose canonical is closed or resolved is **not** redirected (that would reopen the canonical unexamined); the row is allocated `slug-N`, becomes proposed-new, and the identity step sees the closed canonical as a candidate.

   Pinned in Task 8 (`test_materialize_redirects_an_alias_source_id`, `test_materialize_redirects_an_alias_source_slug`, `test_source_and_canonical_in_one_batch_stage_once`, `test_slug_collision_on_a_source_with_closed_canonical_is_not_redirected`, and `test_examine_treats_source_title_with_closed_canonical_as_proposed` in the identity tests) and Task 5 (`test_candidates_never_include_a_hidden_alias_source`).
4. **An embeddings endpoint that is down, or that changed width under the same model id.**
   - Absorb lands.
   - The identity phase is `degraded`, with lexical candidates intact.
   - Off-width cached vectors are forgotten and re-embedded as misses in the same run, deadline permitting.
   - A chunk that failed keeps every earlier chunk saved.
   - The failure reaches the error store as one counts-only row.

   Pinned in Task 4 (`test_width_mismatch_forgets_and_re_embeds_off_width_vectors`, `test_partial_batch_failure_keeps_earlier_chunks`) and Task 11 (`test_embedding_failure_degrades_the_phase_and_keeps_lexical_candidates`).
5. **A save refused with `edit_conflicts` while an untouched uncertain row sits before the conflicted row.** Uncertain rows are exactly untouched `low` rows. Today the client maps conflict indices through *approved* rows while the batch is every *non-rejected* row, so the conflict lands on the wrong row or is dropped. Pinned in Task 13 (`a conflict after an untouched low row lands on its own row`).
6. **A wrong but accepted `existing` is visible and reversible.** A well-formed `existing` naming a live, offered candidate that is actually different business is accepted and keeps its routing band (spec §10.2 forces `low` only for `uncertain`; this plan does not add a band cap, deviation 6). What keeps it from being a silent merge is that it is visible and one click from undone: the row wears “Matched an existing record”, and its as-new alternative restores the model's own title and id. Pinned in Task 9 (`test_accepted_row_offers_the_as_new_variant`), Task 11 (`test_close_candidate_mapped_to_existing_rewrites_the_row` asserts the as-new alternative has `before == ""`) and Task 13 (`an accepted retarget wears "Matched an existing record" and "Keep as a new record" restores the model's title and id`).

---

## File structure

Backend paths are relative to `backend/src/grimoire/`; tests to `backend/tests/`; templates, evals and scripts to the repo root.

| File | Responsibility |
|---|---|
| `store/absorb/parse.py`, `store/absorb/__init__.py` | `IDENTITY_FIELDS`; carry `why_new` / `distinguished_from` when well typed |
| `templates/absorb/system.j2` | The move / close / new instruction, naming `"why_new"` and `"distinguished_from"` |
| `store/absorb/materializer.py` | `_new_record_id` / `_new_thread_id`; per-row staging helpers; `identity_check` carriage, `low` band and alternatives; §7.2 alias redirect |
| `store/absorb/apply.py` | A plot row staged as new is reallocated when its id has been taken since, mirroring commitments |
| `store/continuity/similarity.py` | Identity texts, normalization, lexical and structural signals, pools, plausibility and ranking, embedding mechanics (shared with Slice D) |
| `store/continuity/identity.py` | Proposed-new detection, `examine`, `Examination` (decide, hint_only, rewritten, counts), `build_prompt`, `template_rows`, `parse_output` |
| `templates/continuity_identity/{system,user}.j2` | The resolver prompt |
| `store/routing.py` | `Route("continuity", …, ("continuity-identity",), True)` |
| `store/pending_reviews.py` | `_follow_stamps` walks `identity_check.alternatives` |
| `routes/scenes.py` | `_extract_and_identify` / `_identify`; the fifth phase row; `"identity"` block; logging |
| `main.py` | Lifecycle docstring names every `EmbeddingsClient` singleton |
| `scripts/verify_templates.py` | Identity builder-vs-render block; absorb asks for the identity fields |
| `templates/README.md` | `continuity_identity/` section; absorb section mentions the identity fields |
| `tests/test_llm_fakes.py`, `tests/fixtures/llm/campaign_flow.json` | Rendered identity prompt and its cassette entry |
| `evals/cases.py`, `evals/graders.py`, `evals/recordings/continuity-identity.*.json`, `evals/README.md` | Absorb needles; the `continuity-identity` case |
| `frontend/src/api/types.ts` | `IdentityCheck`, `IdentityCandidate`, `StagedEdit.identity_check`, the `"identity"` phase, the `SceneAbsorb.identity` block |
| `frontend/src/components/review/editRows.ts` | `PHASE_LABELS.identity`, `wireEdit`, `IDENTITY_UNCERTAIN_HINT`, `identityChip` |
| `frontend/src/components/review/useSceneReview.ts` | Strip on save; `switchToAlternative`; rename repoint of alternatives; conflict-index fix |
| `frontend/src/components/review/AbsorbEditRow.tsx`, `frontend/src/index.css` | Chip and identity block |
| `frontend/src/testkit/campaignHarness.tsx` | `PHASES_NONE_CUT` gains the identity row |
| `frontend/src/routes/ConfigView.tsx` (+ `embeddingsOn` helper) | The Embeddings label, chip, copy and disclosure |

Tests:

| File | Kind |
|---|---|
| `tests/test_absorb_store.py` | Appended |
| `tests/test_continuity_similarity.py` | New |
| `tests/test_continuity_identity.py` | New, store level |
| `tests/test_absorb_identity.py` | New, route level |
| `tests/test_routing_routes.py` | Driver added |
| `tests/test_routes.py` | Two pinned tests updated |
| `tests/test_pending_reviews_store.py` | Updated |
| `tests/test_eval_graders.py` | Appended |
| `frontend/src/components/review/SceneReview.test.tsx`, `editRows.test.ts`, `frontend/src/routes/ConfigView.test.tsx` | Updated |

### Decisions taken here, with reasons

- **Module layout and import edges.**
  - `similarity` imports `effective`, `involvement`, `canon`, `embed_space`, `vectors`, `...embeddings` and `...llm_errors`.
  - `identity` imports `similarity`, `effective`, `canon`, `involvement`, `from ... import prompts` (the module is `grimoire.prompts`; there is no `grimoire.store.prompts`), `..absorb.parse` (as `absorb_parse`), `..absorb.materializer` (as `absorb_materializer`), `..commitments` and `..plot`.
  - `absorb.materializer` gains `..continuity.effective`.

  These edges were checked against `tests/test_import_guard._collect()` on the Slice A tip:
  - neither `continuity.effective` nor `continuity.involvement` reaches any `store.absorb` module;
  - `store.absorb` today reaches only `continuity.doc`;
  - nothing in `absorb` will import `identity` or `similarity`.

  So the graph stays acyclic. `identity → absorb.materializer` goes beyond spec §7.0's “may import” column (deviation 1). It exists because proposed-new detection must use the exact id assignment materialize uses (`materializer.assign_ids`, Task 2), or the two disagree about which slug collisions are honoured.
- **Where detection runs:** between `parse_output` and `materialize`, inside the extraction coroutine. `identity.examine` reads the store in a worker thread. `Examination.rewritten(parsed)` returns a new parsed dict. Examined rows are replaced by rewritten rows carrying `identity_check`, without alternatives. An accepted row also carries the private key `"_identity_as_new"`, a copy of the original row, which materialize pops to build the as-new alternative. `materialize` then stages as usual.
- **One id assignment, shared by `examine` and `materialize`.** `materializer.assign_ids` (Task 2, extended in Task 8) walks both sections in order with the same `seen` / staged-title maps (explicit-id reservations included), the same `_new_*_id` calls and the same alias redirect, and `materialize` consumes its result. A row is proposed-new iff its assigned id names no stored dict record. Comparing an allocation to the bare slug, or allocating against an empty staged map, would disagree with materialize: the allocator walks `slug`, `slug-2`, … to the first free-or-honoured id (so the second CJK thread lives at `untitled-2`), and earlier explicit ids in the batch hold reservations.
- **Exact-title honoured collisions are not proposed-new.** An id-less row whose assigned id is an existing record (the §10.5 predicate honoured an open thread or unresolved commitment with the same casefolded title, at `slug` or at any `slug-N`) is the model naming a shown record by its exact title. Materialize will stage it onto that record whatever a resolver says, so paying a call to ask would be incoherent: a `new` verdict could not be honoured. Such a row is a non-proposed target, like an explicit id (deviation 2).
- **Alternatives are built by materialize, not by identity**, in a second pass after every primary row is staged. They go through the same `_plot_edit` / `_commitment_edit` helpers primary rows use, so labels, payloads and `before` tokens are byte-identical to what a primary row for that target would carry. Those tokens are `conflicts.plot_line` / `commitment_line` of the PHYSICAL target. The second pass is what makes “not targeted elsewhere in the batch” and the as-new id allocation see the whole batch.
- **A missing per-row decision** in a decodable reply becomes `unchecked` / `hint_only`, at band `low` like an `uncertain` row (Task 9; revised after the Codex review on #466 — every examined row has a plausible candidate, so an unanswered one is a duplicate nobody ruled out). `uncertain` is reserved for a verdict, the model's or the acceptance guard's, and §24 says a decodable reply with nothing usable yields no proposals. The row still shows its candidates and alternatives.
- **Rows with no plausible candidate carry no `identity_check`.** There is nothing to show. It also keeps every existing exact-dict materialize assertion, and every review that proposes nothing close, unchanged.
- **Phase row placement:** `identity` sits second, right after `extraction`, in run order (it is chained onto it). When nothing was proposed, or nothing was close, it is `skipped` with `attempted: false`.
- **Constants** (`similarity.py`), all candidate-generation parameters, not truth thresholds. Each is justified by the identity-text layout and the cost model, and to be tuned against real prompts later.
  - `CONTINUITY_IDENTITY_BYTES = 2000`. The identity text is a label, a title, at most three one-sentence beats and a due phrase. 2000 bytes holds that even in a three-byte script, and sits far below `semantic.DOC_BYTES = 6000`, so an identity text is never the input a provider rejects.
  - `IDENTITY_TOP_K = 3` (spec).
  - `PREVIOUS_BEATS = 2` (spec §9.1).
  - `STOPWORDS`: a small fixed set of English function words (articles, common prepositions and conjunctions, possessive pronouns, and the `s` / `t` residue `normalize` leaves of `'s` and `n't`), removed before token Jaccard only. Every English sentence shares them, so they say nothing about identity; left in, two unrelated records sharing only `the` clear `WEAK_TOKEN` and the structural clause makes nearly every same-cast pair plausible. Character 3-grams keep them, and CJK text (no spaces, so no such tokens) is unaffected (deviation 11).
  - `TOKEN_FLOOR = 0.25` and `CHAR_FLOOR = 0.35`. A loose floor:
    - on a short text the title is a large share of the tokens, so sharing a title phrase alone approaches it;
    - two records sharing only incidental words, or only a single long word, fall well below it;
    - a false positive costs at most one shared resolver call, and a false negative costs a duplicate record.
  - `COSINE_FLOOR = 0.5`. Loose by design. §9.3 says the recall threshold is not the duplicate threshold, and models differ in scale.
  - `WEAK_TOKEN = 0.1`, `WEAK_CHAR = 0.15`, `WEAK_COSINE = 0.35`. The floor a structural signal must be paired with. Shared actors alone are a weak signal in a small cast, where every record shares someone. These floors are also the lexical pre-filter that picks which uncached neighbours to warm.
  - `IDENTITY_WARM_LIMIT = 32`. The identity step sits on the extraction's critical path, so the common case must be one embedding round trip. With up to `embeddings.BATCH − 32` proposed rows, the proposals plus the warm run fit one batch.
  - `REASON_CHARS = 280`. A resolver reason is display text. Clipping it keeps a runaway reply out of the stored review.
- **How embeddings are faked:**
  - `similarity` owns `_CLIENT = embeddings.EmbeddingsClient()`. Tests monkeypatch it with ONE shared double, `FakeEmbeddings`, added to `tests/llm_fakes.py` under “provider doubles” (Task 4) and used by `test_continuity_similarity.py`, `test_continuity_identity.py` and `test_absorb_identity.py`; no suite writes its own. Exact surface: `__init__(self, vector_for=lambda t: [1.0, 0.0], error=None, fail_after=0)`; attributes `calls: list[list[str]]` and `deadlines: list[float | None]`; `embed(texts, model, key, base_url, deadline=None)` appends the call and the deadline, raises `error` once `error is not None and len(self.calls) > fail_after`, and otherwise returns `[vector_for(t) for t in texts]`.
  - Embeddings are configured by creating an `openai_compatible` connection and `config.write_config(embeddings_model=..., embeddings_connection_id=...)`.
  - The space is read from `embed_space.resolve()["space"]`, never written as a literal.
- **Fixture texts and seeding scenes are part of every candidate test.** Whether a stored record is a candidate depends on the exact title and beat and on which scene the beats were seeded in (`involvement.of` derives scenes and actors from the scenes a record's beats touched, and the proposed subject's scenes are `{sid}`). So:
  - Stored records are seeded in a separate earlier scene (`s0 = scenes.create_scene(cid, "Saltmarch docks")`), never the scene being absorbed, unless the test is about the structural clause and says so.
  - The shared fixture texts live in `tests/review_runs.py` (already the shared absorb helper module) so the store, route and routing tests use one spelling:
    - `LEDGER_THREAD = ("find-the-ledger", "Find the ledger", "Winifred learned the harbour ledger exists.")`;
    - `RECOVER_THE_LEDGER = {"title": "Recover the harbour ledger", "beat": "Winifred went looking for the harbour ledger.", "status": "open"}` — expected to clear `TOKEN_FLOOR` and `CHAR_FLOOR` lexically, whatever the scene;
    - `SALTMARCH_TITHE = {"title": "The Saltmarch tithe", "beat": "The guild demanded Mara pay the Saltmarch tithe.", "status": "open"}` — expected below both weak floors against `LEDGER_THREAD`;
    - `EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER`, the extraction body `'{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [], "plot_movements": [<RECOVER_THE_LEDGER>]}'` as a string;
    - `identity_requests(fake)`, the requests whose system message contains `You are checking whether newly proposed story records`.
  - Every candidate and non-candidate test first asserts the signal that is supposed to decide it, e.g. `sig = similarity.lexical(a, b); assert sig["tokens"] >= similarity.TOKEN_FLOOR`, or `assert sig["tokens"] < similarity.WEAK_TOKEN and sig["scenes"] == []`. A RED then fails for its stated reason, and a later floor change fails loudly at that assertion rather than silently elsewhere.
- **Routing deviation:** only `"continuity-identity"` is registered in Slice C. `test_routing_guard.py::test_every_registered_task_is_actually_named_by_a_call_site` fails a registered task no `routes/` literal names, and `continuity-reconcile` has no call site until Slice D. Slice D appends that task and widens the hint to §29's text (deviation 3).
- **A staged-before-merge review is not redirected at save (owned by Slice D).** The Task 8 redirect works at materialize time, so it covers reviews staged *after* a merge only. A review staged *before* a merge and saved after it still writes the physical source, and a merge does not change plot.json, so `conflicts` cannot see it. Closing it here would mean redirecting inside `apply._apply_one`, which §7.3 classifies as a physical writer. The Slice D plan must refuse or re-stage a pending review whose plot/commitment targets became alias sources at merge-apply time; this is recorded as deviation 12 and added to the Slice D backlog in the slice ledger.
- **Malformed `continuity.json` during staging:** the redirect degrades silently to none. This is not a hidden write. A malformed aliases section makes `live_canon` empty for every effective reader too, so the source is not hidden anywhere, and writing to it is visible.
- **The absorb snapshot stays physical in Slice C.** Switching `absorb.snapshots.plot_snapshot` / `commitment_snapshot` to effective ids is in §7.3's consumer list, which is Slice B's. Slice C is written to be correct either way:
  - `distinguished_from` is filtered against stored same-type ids, canonicalized;
  - the materializer redirect covers the alias sources a physical snapshot still shows.

  Nothing here waits on B (coordination note 1 in the self-review).

---

### Task 1: The absorb contract asks for identity fields and carries them

**Files:**
- Modify: `store/absorb/parse.py`, `store/absorb/__init__.py` (the re-export block), `templates/absorb/system.j2`, `evals/cases.py` (`grade_absorb` needles), `scripts/verify_templates.py` (one assertion in the absorb loop), `templates/README.md` (`### absorb/`)
- Test: `tests/test_absorb_store.py`

**Interfaces:**
- Produces:
  - `parse.IDENTITY_FIELDS = ("why_new", "distinguished_from")`, re-exported as `store.absorb.IDENTITY_FIELDS`.
  - `parse._identity(e: dict) -> dict`:
    - `why_new` is carried iff it is a `str` whose strip is non-empty, and it is carried stripped.
    - `distinguished_from` is carried iff it is a `list`. The carried value is its `str` elements, stripped, blanks dropped, deduped in first-seen order. The key is present only when that leaves at least one id.
  - Plot and commitment rows become `{... } | _cite(e) | _identity(e)`. Id filtering is not done here: `parse_output` has no snapshot, so `identity.examine` drops unknown ids (Task 5).
- `system.j2`: a new paragraph immediately before `{% if steering -%}`. It is static (no new variable), so `absorb.build_prompt` and `verify_templates` need no new argument. Its text:

  > Before opening a NEW plot thread or commitment, read the "Current plot threads:" and "Open commitments:" lists. For every movement choose exactly one: move an existing id, resolve or close an existing id, or open a new record with no "id". Open a new one only when no listed record already stands for the same narrative question or obligation — a new development in a listed question is a movement on that id. A row that opens a new record may add "why_new" (one sentence: why no listed record covers it) and "distinguished_from" (the ids of listed records you considered and judged to be different business).

- [ ] **Step 1: Write the failing tests** in `tests/test_absorb_store.py`:
  - `test_parse_carries_well_typed_identity_fields`: a plot row with `"why_new": " different debt "` and `"distinguished_from": ["the-debt", " ", 3, "the-debt", "salt-owed"]` parses to `why_new == "different debt"` and `distinguished_from == ["the-debt", "salt-owed"]`. The same assertion runs for a commitment row.
  - `test_parse_drops_absent_blank_or_mistyped_identity_fields`: each of these leaves the key absent:
    - `why_new` of `""`, `None`, `3` or `["x"]`;
    - `distinguished_from` of `"the-debt"` (a string), `[1, None]` or `{}`.
  - `test_identity_fields_live_inside_rows_not_the_top_level`: `set(parse_output("{}"))` is unchanged (the graders' derived contract).
  - `test_absorb_system_prompt_asks_for_the_identity_fields`: `absorb.build_prompt(...)[0]["content"]` contains `'"why_new"'`, `'"distinguished_from"'`, `"Current plot threads:"` and `"Open commitments:"`, and still contains `"You are absorbing a completed role-play scene"`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py -q`. Expected: three tests FAIL (`AttributeError: IDENTITY_FIELDS`, missing keys, missing prompt text). `test_identity_fields_live_inside_rows_not_the_top_level` is a regression pin and passes before and after.
- [ ] **Step 3: Implement.**
  - Add `IDENTITY_FIELDS` beside `CITATION_FIELDS`, with a comment: these are inside rows, so `evals` must ask for them explicitly.
  - Add `_identity` and apply it to both row builders.
  - Re-export it.
  - Add the paragraph.
  - In `evals/cases.py` `grade_absorb`, extend the needle source with `+ absorb_store.IDENTITY_FIELDS`. This yields `prompt.asks_why_new` and `prompt.asks_distinguished_from`. The quoted field names alone survive deleting the instruction, so also add `asks_identity_contract` with a phrase unique to the new paragraph — `"Open a new one only when no listed record already stands for the same narrative question or obligation"` — in the style of `asks_steering_contract`.
  - In `verify_templates.py`'s absorb loop, assert `'"why_new"' in exp[0]["content"]`.
  - Add one sentence to the README's absorb section naming `absorb.parse.IDENTITY_FIELDS` and the instruction.
- [ ] **Step 4: Run the absorb store tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py -q`. Expected: PASS.
- [ ] **Step 5: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py tests/test_eval_graders.py -q`. Expected: PASS. The absorb recordings are graded against the new prompt; `absorb.compliant.json` needs no change.
- [ ] **Step 6: Run the template checks and the lint ratchets.** From the repo root: `make check-templates check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: all checks pass, no baseline growth.
- [ ] **Step 7: Commit:** `feat(absorb): ask for and carry why_new / distinguished_from on new-record rows`

---

### Task 2: Plot slug collisions use the commitment predicate (§10.5)

**Files:**
- Modify: `store/absorb/materializer.py`, `store/absorb/apply.py` (plot branch)
- Test: `tests/test_absorb_store.py` (one existing test inverted, new tests appended), `tests/test_undo_store.py` (one test appended)

**Interfaces:**
- Produces:
  - `materializer._new_record_id(stored: dict, staged: dict[str, str], slug: str, title: str, *, settled) -> str`. This is today's `_new_commitment_id` body with the resolved check made a parameter. `settled(record: dict) -> bool` is true for a closed thread or a resolved commitment.
  - `_new_commitment_id(owed, staged, slug, title)` keeps its name and signature (apply.py calls it) and delegates with `settled = _text(record.get("status")).lower() in commitments.RESOLVED` — the same `_text` coercion today's body uses, so a hand-edited non-string `status` degrades rather than raising out of a paid-for absorb (Codex review on #466).
  - New `_new_thread_id(threads, staged, slug, title)` delegates with `settled = _text(status).lower() == "closed"`.
  - `assign_ids(threads: dict, owed: dict | None, parsed: dict, live: dict[str, str] | None = None) -> dict[tuple[str, int], Assigned | None]` — the ONE id assignment, shared with `identity.examine` (Task 5). `Assigned` is a plain `NamedTuple(id: str, existing: bool, merged_from: str | None)`. `live` is accepted and ignored until Task 8 implements the alias redirect and fills `merged_from`. It walks `plot_movements` then `commitment_movements` in order, applying exactly the rules the materialize loops apply today, and returns `None` for a row materialize drops (blank beat; no id and no alphanumeric title; commitments when `owed is None`; a later row whose id an earlier row already took — the one-edit-per-record dedup):
    - plot: `staged_plot_titles: dict[str, str]`, new beside `seen_pids`. An explicit id is reserved under `(title or stored title).strip().casefold()`, mirroring the commitment block's reservation; an id-less row gets `_new_thread_id(threads, staged_plot_titles, slugify(title), title)` and is reserved;
    - commitments: today's `staged_titles` logic, moved here unchanged;
    - `existing` is `isinstance(stored.get(id), dict)`.
  - The two `materialize` loops consume `assign_ids`' result instead of allocating inline; their staging bodies are otherwise unchanged in this task.
  - `apply._apply_one` plot branch, matching the commitment branch: the write id is `target["id"]` (not `p["id"]`). When `str(e.get("before", "")).strip() == ""`, it becomes `new = materializer._new_thread_id(plot.read(cid), {}, target["id"], p.get("title", ""))`; when `new != target["id"]`, set `reversal = None`, `target = {**target, "id": new}` and `p = {**p, "id": new}`, so the journal entry is keyed by the id actually written and Undo can never revert somebody else's thread. Otherwise it is unchanged.
- [ ] **Step 1: Write the failing tests**
  - Replace `test_materialize_plot_new_title_colliding_existing_id_merges` with `test_materialize_plot_new_title_differing_from_colliding_thread_gets_a_suffix`. Stored `the-map` is titled "The map", and the row's id-less title is "The Map!". The row is staged as `plot:the-map-2` with `before == ""`, and the stored thread is untouched.
  - `test_materialize_plot_same_title_open_thread_is_honoured`: an id-less "The map" against stored open "The map" stages `plot:the-map`, with `before` starting `"open — "`.
  - `test_materialize_plot_same_title_closed_thread_is_not_reopened`: stored "The map" is `closed`, so the row is staged as `plot:the-map-2`.
  - `test_materialize_plot_untitled_titles_never_merge`: given a stored open thread `untitled` titled "海図" and an id-less row titled "灯台", the row goes to `untitled-2`. A second id-less "灯台" in the same batch dedupes onto it, so one edit results. (Review Focus 1.)
  - `test_materialize_plot_explicit_id_is_reserved_against_a_later_slug`: given `{"id": "the-map", "title": "The map"}` and then an id-less "The Map?", the second row gets `the-map-2`, not dropped by the seen check.
  - `test_apply_plot_new_row_whose_id_was_taken_is_reallocated`: stage a new "The map" (`before ""`). Then another write creates `the-map` titled "A different map". `apply_edits` with `resolve: "replace"` writes `the-map-2`, and `the-map`'s beats are unchanged.
  - `test_materialize_slug_collision_with_non_string_status_does_not_raise` (both sections): stored `mara-s-oath` titled "Mara's oath" with `"status": ["fulfilled"]`, and stored thread `mara-s-map` titled "Mara's map" with `"status": 7`. Id-less rows with those titles stage onto the stored ids (a non-string status reads as `""`, i.e. unsettled) and `materialize` does not raise.
  - `test_assign_ids_matches_materialize`: for a batch mixing explicit ids, id-less rows, a blank-beat row and a duplicate, every non-`None` assignment equals the `target.id` of the edit materialize stages for that row, and every `None` row stages nothing.
  - `tests/test_undo_store.py::test_a_plot_thread_whose_id_was_reallocated_carries_no_reversal`, mirroring the commitment test at :493: `entry["ref"]["id"] == "the-map-2"`, `entry["undo"] is None`, and the stored `the-map` keeps one beat.
  - Keep `test_materialize_plot_dedupes_same_pid` and the commitment `untitled-2` tests unmodified. They must still pass.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_undo_store.py -q`. Expected: every new test FAILS (today's code merges, `assign_ids` does not exist, the plot write is journalled under the staged id) except `test_materialize_plot_same_title_open_thread_is_honoured`, which pins existing behaviour the new predicate must keep and passes before and after.
- [ ] **Step 3: Implement** as specified. Update `_new_commitment_id`'s docstring to point at `_new_record_id`, and the plot block's comment (“existing thread (by id, or a new title that collides)”) to say a collision is honoured only under the shared predicate.
- [ ] **Step 4: Run the absorb tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_absorb_conflicts.py tests/test_absorb_routing.py tests/test_undo_store.py -q`. Expected: PASS.
- [ ] **Step 5: Run the ingest tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ingest_scene.py tests/test_frozen_campaign.py -q`. Expected: PASS. Both stage plot rows with explicit ids only.
- [ ] **Step 5b: Lint ratchets.** From the repo root: `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`assign_ids` split into per-section helpers to stay under C901).
- [ ] **Step 6: Commit:** `fix(absorb): a plot slug collision merges only onto an open thread of the same title`

---

### Task 3: `continuity.similarity` — identity texts and deterministic signals

**Files:**
- Create: `store/continuity/similarity.py`
- Test: `tests/test_continuity_similarity.py` (new). Fixtures: `client` and `cid` copied from `tests/test_continuity_doc.py`; threads and commitments seeded through `plot.set_movement` / `commitments.set_movement`.

**Interfaces:**
- Produces (all pure or read-only):
  - Constants: `CONTINUITY_IDENTITY_BYTES = 2000`, `PREVIOUS_BEATS = 2`, `IDENTITY_TOP_K = 3`, `STOPWORDS` (a `frozenset`, see Decisions), `TOKEN_FLOOR = 0.25`, `CHAR_FLOOR = 0.35`, `COSINE_FLOOR = 0.5`, `WEAK_TOKEN = 0.1`, `WEAK_CHAR = 0.15`, `WEAK_COSINE = 0.35`. Each carries a structural justification comment and “tune against real prompts later”.
  - `thread_identity_text(rec: dict) -> str` and `commitment_identity_text(rec: dict) -> str`.
    - `rec` is an effective record (`effective.records` shape) or a pseudo-record `{title, beats: [{text}], kind?, due?}`.
    - Lines are joined with `"\n"` and blank lines skipped.
    - Thread lines: `"plot thread"`, title, the latest beat (`rec["latest_beat"]`, or the last beat's text), then up to `PREVIOUS_BEATS` earlier beat texts, newest first, skipping any equal to an already-included beat.
    - Commitment lines: `f"{kind or 'promise'} commitment"`, title, latest beat, `f"due {due}"` when `due` is non-blank, then up to two previous beats.
    - No ids appear anywhere. The result is clipped with `embed_space.clip(text, CONTINUITY_IDENTITY_BYTES)`.
  - `normalize(text: str) -> str`: `unicodedata.normalize("NFKC", text).casefold()`, every char where `not ch.isalnum()` becomes a space, then whitespace is collapsed and stripped.
  - `title_key(title) -> str` is `normalize(title)`, and `titles_equal(a, b) -> bool` is `title_key(a) == title_key(b) != ""`.
  - `slug_equal(a_title, b_title) -> bool`: `paths.slugify(a) == paths.slugify(b)`, and False when either is `"untitled"`.
  - `tokens(text) -> frozenset[str]`: `normalize(text).split()` minus `STOPWORDS`.
  - `trigrams(text) -> frozenset[str]`: the 3-grams of `normalize(text)` with spaces kept. A text shorter than 3 yields `{text}` when non-empty.
  - `jaccard(a: frozenset, b: frozenset) -> float`: `0.0` when the union is empty. No `difflib` anywhere.
  - `body(text) -> str`: the identity text minus its first (type-label) line. This is the input to `tokens` / `trigrams`. The label is identical across every same-type pair, so leaving it in would only inflate every score by a constant (clarification of §9.2).
  - `class Subject` (plain, `__slots__`): `ref: str`, `kind: Literal["thread", "commitment"]`, `title: str`, `status: str`, `live: bool`, `text: str`, `actors: frozenset[str]`, `scenes: frozenset[str]`, `anchors: frozenset[str]`, `record: dict`.
  - `subject(kind, ref, record, *, actors=(), scenes=(), anchors=()) -> Subject` computes `text` via the kind's identity-text function and `live` via `effective.is_live`.
  - `pool(cid, kind) -> list[Subject]`:
    - every effective record of `kind`, closed and resolved included, keyed by canonical ref (`effective.records`);
    - actors and scenes from one `involvement.of(cid, refs)` call;
    - anchors are the `event:*` endpoints of `effective.links(cid)` touching the ref;
    - sorted by ref;
    - `[]` when the ledger is unreadable. The catch is narrowed to what the readers raise (`OSError`, `ValueError`, `TypeError`, `KeyError`, `AttributeError`); if a store error class outside those must be caught, the line carries `# noqa: BLE001 -- <reason>` instead.
  - `lexical(a: Subject, b: Subject) -> dict` returns the signals:

    ```
    {"title_equal": bool, "slug_equal": bool, "tokens": float, "chars": float,
     "cosine": None, "actors": sorted list, "scenes": sorted list, "anchors": sorted list}
    ```

    Floats are rounded to 4 places. The lists are intersections.
  - `with_cosine(signals: dict, a: Subject, b: Subject, vecs: dict[str, list[float]]) -> dict` sets `cosine` to `round(vectors.dot(...), 4)` when both texts have equal-width unit vectors in `vecs`, and leaves it `None` otherwise.
  - `weak(signals) -> bool`: `tokens >= WEAK_TOKEN or chars >= WEAK_CHAR or (cosine is not None and cosine >= WEAK_COSINE)`.
  - `admitted_by(signals) -> str | None`: which clause admits the pair, checked in this order, or `None` when none does:
    - `"lexical"`: `title_equal`, `slug_equal`, `tokens >= TOKEN_FLOOR` or `chars >= CHAR_FLOOR`;
    - `"structural"`: `(actors or scenes or anchors)` and (`tokens >= WEAK_TOKEN` or `chars >= WEAK_CHAR`);
    - `"semantic"`: `cosine >= COSINE_FLOOR`, or `(actors or scenes or anchors)` and `cosine >= WEAK_COSINE`.

    So `"semantic"` means the pair was admitted ONLY through a cosine clause. `nearest` stores it as `signals["via"]`, which is what `counts()` splits deterministic from semantic candidates by (§29).
  - `plausible(signals) -> bool` is `admitted_by(signals) is not None`.
  - `rank_key(signals, ref) -> tuple`: `(-title_equal, -slug_equal, -max(tokens, chars, cosine or 0.0), -(len(actors)+len(scenes)+len(anchors)), ref)`.
  - `nearest(subject, candidates: list[Subject], vecs=None, *, top_k=IDENTITY_TOP_K) -> list[tuple[Subject, dict]]`:
    - same `kind` only, excluding `subject.ref`;
    - computes `lexical`, plus cosine when `vecs` is given;
    - keeps `plausible`, sorts by `rank_key`, truncates to `top_k`.
- [ ] **Step 1: Write the failing tests** (`tests/test_continuity_similarity.py`):
  - `test_thread_identity_text_order_and_no_ids`: given a thread with id `find-the-ledger` and beats `A`, `B`, `C`, `C`, the text equals `"plot thread\nFind the ledger\nC\nB\nA"` and does not contain `find-the-ledger`.
  - `test_commitment_identity_text_blank_kind_is_promise_and_due_present`: a blank kind gives `"promise commitment"` as the first line, and `"due midnight"` follows the latest beat.
  - `test_identity_text_is_clipped_to_the_byte_bound`: a 5000-byte beat yields `len(text.encode()) <= CONTINUITY_IDENTITY_BYTES`.
  - `test_titles_equal_after_nfkc_and_casefold`: `titles_equal("ＭＡＲＡ'S DEBT", "mara's debt")` is true, and `titles_equal("", "")` is false.
  - `test_cjk_reword_is_a_trigram_candidate_and_untitled_slugs_are_not_equal` (Review Focus 1): `slug_equal("海図", "灯台")` is false. A CJK title plus beat sharing most characters with a stored record has `chars >= CHAR_FLOOR` and `tokens` at or near 0, and `plausible` is true.
  - `test_stopwords_never_count_as_shared_tokens`: `tokens("The map of the harbour")` has no `the` / `of`, and `tokens("Mara's boat")` has no `s`.
  - `test_unrelated_records_are_not_plausible_even_with_shared_actors`: realistic sentences sharing only function words — "Mara's boat" / "Mara patched the hull of her boat at the pier." and "Winifred's lantern" / "Winifred lit the lantern in the window." — plus a shared actor `characters:mara`. First assert `sig["tokens"] < WEAK_TOKEN` and `sig["chars"] < WEAK_CHAR`; then `plausible == False`.
  - `test_structural_signal_needs_a_weak_lexical_partner`: the same pair with the first title changed to "Mara's lantern" (one shared content word). Assert the weak signal first, then `admitted_by(sig) == "structural"`.
  - `test_admitted_by_names_the_clause`: a title-equal pair is `"lexical"`; a pair with no shared words, a shared actor and cosine `0.4` is `"semantic"`; cosine `0.9` alone is `"semantic"`.
  - `test_top_k_keeps_the_three_best_plausible_and_never_pads`: five plausible candidates give exactly 3, in `rank_key` order. With one plausible candidate and four implausible ones, the result has length 1.
  - `test_cross_type_never_enters_nearest`: a commitment with an identical title is absent from a thread subject's `nearest`.
  - `test_pool_includes_closed_and_canonicalizes_aliases`:
    - a closed thread is present with `live == False`;
    - given an alias `thread:a → thread:b`, the pool holds `thread:b` and not `thread:a`;
    - `actors` comes from `involvement.of`.
  - `test_pool_of_an_unreadable_ledger_is_empty`: plot.json `"{ no"` gives `pool(cid, "thread") == []`.
  - `test_anchors_come_from_reviewed_event_links`: a link `thread:x on event:e1`, created through `continuity.review.create_link`, puts `event:e1` in the subject's anchors.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py -q`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement `similarity.py`** (Task 3 part only). Its module docstring states:
  - §3.2: scores rank candidates and never write;
  - the label-line exclusion;
  - the constants' justification;
  - that Slice D's sweep reuses `pool`, `lexical`, `nearest` and (Task 4) `semantic`.
- [ ] **Step 4: Run the similarity tests and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_import_guard.py -q`. Expected: PASS. Then, from the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (the new file has no baseline entry, so any finding fails).
- [ ] **Step 5: Commit:** `feat(continuity): identity texts and deterministic similarity signals`

---

### Task 4: `continuity.similarity` — embedding mechanics

**Files:**
- Modify: `store/continuity/similarity.py`, `main.py` (lifecycle docstring), `tests/llm_fakes.py` (`FakeEmbeddings`, under “provider doubles”)
- Test: `tests/test_continuity_similarity.py`

**Interfaces:**
- Produces:
  - `IDENTITY_WARM_LIMIT = 32`.
  - `_CLIENT = embeddings.EmbeddingsClient()`.
  - `available() -> dict | None`: `embed_space.resolve()`, never `semantic.settings()`.
  - `matching() -> str`: Slice B's `drivers.matching()` contract exactly — `"semantic"` iff `embed_space.resolve()` is non-None, and `"basic"` on any exception (`# noqa: BLE001 -- a matching label must never fail the read that shows it`). It is the one definition of the `matching` field in this slice; `identity.examine` and `routes/scenes._identify` call it and never spell the expression (coordination note 5).
  - `deadline(remaining: float | None) -> float | None`: the absolute monotonic instant an absorb-side embed must finish by, or `None` for “do not call the provider” (budget already spent).
    - `remaining is None` gives `time.monotonic() + embeddings.TIMEOUT`.
    - `remaining <= 0` gives `None`.
    - Otherwise it gives `time.monotonic() + min(embeddings.TIMEOUT, remaining)`.
  - `reference_width(fresh: list[list[float]], loaded: dict[str, list[float]]) -> int | None`: the most common width among `fresh` if it is non-empty, else among `loaded`. Ties go to the larger count, then the smaller width. `None` when both are empty.
  - `embed_missing(space: dict, texts: list[str], *, deadline: float) -> tuple[dict[str, list[float]], str]`:
    - deduped `texts` are embedded in chunks of `embeddings.BATCH` (read at call time), one `_CLIENT.embed(chunk, space["model"], space["key"], space["base_url"], deadline=deadline)` per chunk;
    - after each chunk, `vectors.save` runs for every vector;
    - it returns `({text: unit_vector}, "")` on success. On `EmbeddingsError` / `OSError` it returns what earlier chunks produced plus the error's `kind` (or `"network"` for an `OSError`). Any other exception propagates.
    - A response of the wrong length counts as `bad_response`.
  - `class Semantic` (plain): `vectors: dict[str, list[float]]`, `mode: Literal["off", "configured", "failure"]`, `embedded: int`, `error: str`.
  - `semantic(required: list[str], warm: list[str], *, deadline: float | None, space: dict | None = None, cached: Iterable[str] = ()) -> Semantic`. It takes no `cid`, and is the only writer in the module. `warm` is what MAY be embedded (the prefilter-kept neighbours, best first); `cached` is what may only be READ — every pool text, so a lexically unrelated neighbour whose vector is already cached (by an earlier warm, or by Slice D's sweep) still gets a cosine. §9.4 bounds what is embedded, not which cached vectors are read, and `vectors.load` is a per-text file read already run per candidate on every turn. Flow:
    1. `space = space or available()`. If it is `None`, return mode `"off"` with empty vectors.
    2. `loaded = vectors.load(space["space"], required + warm + list(cached))`.
    3. `to_embed = [t for t in required if t not in loaded] + [t for t in warm if t not in loaded][:IDENTITY_WARM_LIMIT]`.
    4. If `deadline is None`, nothing is embedded.
    5. Otherwise call `embed_missing`.
    6. Apply the width rule to **every** vector the run holds, loaded and fresh alike. The reference width comes from the vectors embedded in this run, or from the loaded vectors when nothing was embedded (`reference_width`). Every vector of another width — a loaded one, or a fresh one from a chunk the endpoint answered at a different width mid-run (`embed_missing` has already cached it) — gets `vectors.forget` and is dropped, so no incompatible subset is scored or left in the shared cache (Codex review on #466).
    7. **Forgotten texts are misses** (§9.4, §26 “forgotten and re-embedded”). If step 6 forgot any text that is in `required` or among the warm-selected texts, and `deadline` is not `None` and not yet passed, embed them in a second `embed_missing` call under the same deadline: every forgotten required text, plus forgotten warm texts up to what is left of `IDENTITY_WARM_LIMIT`. This round trip happens only after an endpoint changed width under the same model id, so the common case stays one call. Forgotten `cached`-only texts stay misses (they were never eligible to embed), and so do texts whose FRESH vector was forgotten in step 6: the endpoint answered them at the off width in this very run, so asking again would not help. The step-7 results go through step 6's filter against the same reference width.
    8. `mode` is `"failure"` if either `embed_missing` call reported an error, else `"configured"`; `error` carries that error's `kind`.
- `main.py`: the blind-spot paragraph names the `EmbeddingsClient` singletons in `store/semsearch`, `store/context/semantic`, `store/context/art` and `store/continuity/similarity`. Docstring only.
- [ ] **Step 1: Write the failing tests** with `llm_fakes.FakeEmbeddings` (the exact surface is under Decisions; add it to `tests/llm_fakes.py` first). It is installed via `monkeypatch.setattr(similarity, "_CLIENT", fake)`, with embeddings configured as in `test_context_semantic.configure`.
  - `test_unconfigured_is_off_and_never_calls`: `semantic(["x"], [], deadline=...)` gives `mode == "off"` and `fake.calls == []`, even when `semantic_recall_depth` is `"0"` and the connection is blank.
  - `test_availability_ignores_recall_depth`: with a connection and model set and depth `"0"`, `available()` is not None and `semantic` embeds.
  - `test_matching_label_and_its_exception_policy`: `matching()` is `"basic"` unconfigured and `"semantic"` configured; with `embed_space.resolve` monkeypatched to raise `RuntimeError`, it is `"basic"` and nothing propagates.
  - `test_required_texts_embed_and_cache`: the first run embeds; a second identical run makes no call, and the vectors come from the cache.
  - `test_warm_limit_is_honoured`: given 40 uncached warm texts, exactly `IDENTITY_WARM_LIMIT` are embedded, plus the required ones.
  - `test_width_mismatch_forgets_and_re_embeds_off_width_vectors` (Review Focus 4): a cached 3-wide vector for warm neighbour text `n`, against fresh 2-wide required vectors. `n` is re-embedded in a second call (`fake.calls[1] == [n]`), comes back in `.vectors` at width 2, and `vectors.load(space, [n])[n]` is 2-wide afterwards.
  - `test_mixed_width_fresh_chunks_are_filtered_and_forgotten`: a `FakeEmbeddings` scripted to answer the first chunk 2-wide and the second 3-wide (force two chunks with a small chunk size). The minority width's texts are absent from `.vectors` and from `vectors.load(space, ...)` afterwards, and the majority width's texts are present in both.
  - `test_off_width_cached_only_text_is_forgotten_not_embedded`: the same 3-wide vector passed only through `cached` is forgotten, absent from `.vectors`, and never sent to the provider.
  - `test_cached_vectors_are_read_for_texts_never_embedded`: a pre-seeded vector (`vectors.save(space, text, v)`) for a text passed only through `cached` is in `.vectors`, and `fake.calls` holds only the required text.
  - `test_fully_cached_run_uses_the_most_common_loaded_width`: two 2-wide cached vectors and one 3-wide, with no embedding (`deadline=None`), keep the 2-wide pair and forget the 3-wide.
  - `test_partial_batch_failure_keeps_earlier_chunks` (Review Focus 4): with `monkeypatch.setattr(embeddings, "BATCH", 2)`, five texts, and `FakeEmbeddings(error=EmbeddingsError("network", ...), fail_after=1)`, which raises on its second call:
    - mode is `"failure"`;
    - the first two texts are in `.vectors` and in the cache;
    - the rest are absent.
  - `test_embedding_failure_mode_on_oserror`: an `OSError` gives mode `"failure"`, with nothing raised.
  - `test_deadline_is_shared_and_bounded`: every recorded `deadline` is the same value, and is at most `time.monotonic() + embeddings.TIMEOUT` at assertion time.
  - `test_deadline_helper`: `deadline(None)` is within `TIMEOUT` of now; `deadline(0)` and `deadline(-1)` are `None`; with `before = time.monotonic()` taken first, `before + 5 - 0.5 <= deadline(5) <= time.monotonic() + 5`; and the clamp holds: `deadline(embeddings.TIMEOUT * 10) <= time.monotonic() + embeddings.TIMEOUT`.
  - `test_no_deadline_means_cache_only`: `deadline=None` makes no call and still uses cached vectors, with mode `"configured"`.
  - `test_nearest_uses_cosine_when_both_vectors_present`: a pair with no shared words but cosine 0.9 is plausible.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, and update the `main.py` docstring.
- [ ] **Step 4: Run the similarity tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_llm_fakes.py -q`. Expected: PASS. If the lock-domain guard names `similarity`, a cid-taking function writes. Move the write into `semantic` rather than classifying the module. Then, from the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): shared embedding mechanics for continuity similarity`

---

### Task 5: `continuity.identity` — proposed-new detection and neighbours

**Files:**
- Create: `store/continuity/identity.py`
- Modify: `tests/review_runs.py` (adds `LEDGER_THREAD`, `RECOVER_THE_LEDGER`, `SALTMARCH_TITHE`; see Decisions)
- Test: `tests/test_continuity_identity.py` (new; `client` / `cid` fixtures as in Task 3)

**Interfaces:**
- Consumes:
  - `similarity.pool`, `similarity.subject`, `similarity.nearest`, `similarity.semantic`, `similarity.weak`, `similarity.lexical`;
  - `effective.Ledgers`, `effective.live_canon`;
  - `involvement.scene_actors`;
  - `canon.actor_ref`;
  - `absorb_materializer.assign_ids` (Task 2, with Task 8's `live` parameter).
- Produces:
  - `SECTIONS = {"plot_movements": "thread", "commitment_movements": "commitment"}`.
  - `AS_NEW_KEY = "_identity_as_new"`.
  - `has_proposals(parsed: dict) -> bool`: any row in either section. It is cheap, and it is what lets an absorb with neither section skip the worker thread.
  - `class Examined` (plain):
    - `section`, `index` (position in its section), `key` (`"r1"`, `"r2"`, … in examination order), `kind`, `row` (the parsed row as given), `title` (title, or else id);
    - `candidates: list[tuple[Subject, dict]]`;
    - `distinguished_from: list[str]`, filtered;
    - `decision="unchecked"`, `status="hint_only"`, `reason=""`, `target: str | None` (an accepted canonical id).
  - `class Examination` (plain):
    - `rows: list[Examined]`, only proposed-new rows with at least one candidate;
    - `proposed: int`;
    - `matching: Literal["basic", "semantic"]`;
    - `embedding: Literal["off", "configured", "failure"]`;
    - `embedding_error: str` (the `Semantic.error` kind, `""` when none);
    - `embedded: int`;
    - `targets: set[tuple[str, str]]`, the `(kind, canonical id)` that non-proposed rows will stage onto;
    - `live: dict[str, str]`, the `live_canon` map `examine` loaded, kept so `decide` (Task 7) can canonicalize a named id.
  - `examine(cid: str, sid: str, parsed: dict, facts: dict, *, embed_deadline: float | None) -> Examination`, which runs in a worker thread.
    - **Proposed-new rule** (spec §10.2 plus deviation 2), through the shared assignment: `assigned = absorb_materializer.assign_ids(threads, owed, parsed, live)`. A row whose assignment is `None` is one materialize drops, and is ignored.
    - A row is proposed-new iff its assigned id names no stored dict record of that type (`not assigned.existing`). This covers an id-less row allocated a free `slug` or `slug-N`, and an explicit id naming nothing stored.
    - Every other row contributes `(kind, assigned.id)` to `targets`. `assign_ids` has already applied the alias redirect, so that id is canonical, and a slug collision on a source whose canonical is closed or resolved has already been pushed to `slug-N` (Task 8) — such a row is proposed-new, with the closed canonical among its neighbours.
    - The proposed subject is `similarity.subject(kind, f"proposed:{key}", {title, beats: [{text: beat, scene: sid}], kind, due}, actors=…, scenes={sid})`. Its actors are `{canon.actor_ref(c) for c in facts["cast"]} ∪ involvement.scene_actors(cid).get(sid, set())`.
    - `distinguished_from` keeps the ids naming a stored record of the row's type, mapped through `live_canon` and deduped. Unknown ids are dropped.
    - Neighbours come from `similarity.pool(cid, kind)`.
    - When `similarity.available()` is set, warm texts are the pool texts that pass `weak(lexical(...))` against any proposed subject, ranked by their best `rank_key`. Then `semantic([proposed texts], warm, deadline=embed_deadline, cached=[every pool text])` runs, and `nearest(..., vecs)`. In Slice C a lexically unrelated (wordless) paraphrase is therefore found only once its neighbour's vector is cached; Slice D's sweep is what warms the ledger. The module docstring says so.
    - A row is examined iff `nearest` returns at least one candidate.
    - `matching` is `similarity.matching()` (§3.3), and `embedding` / `embedded` mirror the `Semantic` result.
- [ ] **Step 1: Write the failing tests** (`tests/test_continuity_identity.py`):
  - `test_no_rows_no_proposals`: `has_proposals({"plot_movements": [], "commitment_movements": []})` is false.
  Fixtures follow Decisions (“Fixture texts and seeding scenes”): `LEDGER_THREAD` seeded in `s0`, the absorbed scene `sid` separate, and each test asserts its deciding signal first.
  - `test_idless_row_with_no_close_record_is_proposed_but_not_examined`: given `LEDGER_THREAD` and a `SALTMARCH_TITHE` row, `proposed == 1` and `rows == []`.
  - `test_reworded_duplicate_is_examined_with_the_record_as_candidate`: given `LEDGER_THREAD` and a `RECOVER_THE_LEDGER` row, the row is examined and its first candidate ref is `thread:find-the-ledger` with `signals["via"] == "lexical"`.
  - `test_exact_title_open_record_is_a_target_not_a_proposal` (deviation 2): an id-less "Find the ledger" row gives `proposed == 0` and `("thread", "find-the-ledger") in targets`.
  - `test_exact_title_closed_record_is_a_proposal_with_the_closed_neighbour`: the closed thread is offered as a candidate with `live False`.
  - `test_exact_title_on_a_suffixed_record_is_a_target`: stored `untitled` "海図" and open `untitled-2` "灯台"; an id-less "灯台" row gives `proposed == 0` and `("thread", "untitled-2") in targets`.
  - `test_batch_reservation_matches_materialize`: an explicit `{id: "the-map", title: "Renamed map"}` row followed by an id-less "The map" (stored `the-map` "The map", open). The second row is proposed-new and examined, and its assigned id equals the `target.id` materialize stages for it.
  - `test_row_with_unknown_id_is_proposed_and_titled_by_title_or_id`: given `{"id": "maras-debt", "title": ""}`, `title == "maras-debt"`.
  - `test_explicit_alias_source_id_targets_the_canonical`: given an alias `thread:a → thread:b`, a row with id `a` yields a `targets` entry `("thread", "b")`.
  - `test_candidates_never_include_a_hidden_alias_source` (Review Focus 3): the source `a` is never a candidate ref, though the canonical `b` may be.
  - `test_distinguished_from_drops_unknown_and_canonicalizes`: `["nope", "a", "b"]` becomes `["b"]`.
  - `test_cross_type_never_a_candidate`: a commitment row titled exactly like a thread has no thread candidate.
  - `test_commitments_unreadable_examines_no_commitment_rows`.
  - `test_semantic_matching_finds_a_wordless_paraphrase`: with embeddings configured, the neighbour's vector pre-seeded with `vectors.save(space, text, v)` (standing in for an earlier warm or Slice D's sweep), and the fake mapping the proposed text to a near vector, a row sharing no words with the record (assert `tokens == 0` and `chars < WEAK_CHAR` first) is examined with `signals["via"] == "semantic"`, and `embedding == "configured"`. Control: the same fixture with embeddings unconfigured gives `rows == []`.
  - `test_an_uncached_lexically_unrelated_neighbour_is_not_embedded`: the same fixture without the pre-seeded vector; `fake.calls` contains only the proposed text, and `rows == []`.
  - `test_embedding_failure_keeps_lexical_candidates`: the fake raises. `embedding == "failure"`, and the lexical candidate is still found.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py -q`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `has_proposals`, `Examined`, `Examination` (fields only) and `examine`.
- [ ] **Step 4: Run the identity tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): detect proposed-new absorb rows and their plausible neighbours`

---

### Task 6: The resolver prompt family and its parser

**Files:**
- Create: `templates/continuity_identity/system.j2`, `templates/continuity_identity/user.j2`
- Modify: `store/continuity/identity.py`, `scripts/verify_templates.py`, `templates/README.md`, `docs/incoming-llm-capture.md` (one sentence, §29), `tests/test_llm_fakes.py` (`_rendered_prompts`), `tests/fixtures/llm/campaign_flow.json`
- Test: `tests/test_continuity_identity.py`, `tests/test_llm_fakes.py`

**Interfaces:**
- Produces:
  - `DECISIONS = ("existing", "new", "uncertain")`, `CHECK_DECISIONS = DECISIONS + ("unchecked",)`, `STATUSES = ("accepted", "downgraded", "hint_only")`, `REASON_CHARS = 280`.
  - `Examination.prompt_rows() -> list[dict]`. One dict per examined row:

    ```
    {"key", "kind", "title", "beat", "status", "commitment_kind", "due",
     "quote", "speaker", "certainty": float | None, "why_new",
     "distinguished_from": [ids],
     "candidates": [{"id", "title", "status", "kind", "due", "latest_beat",
                     "earlier": [up to 2 earlier beat texts, newest first],
                     "signals": {...}}]}
    ```

    `kind` is `"thread"` or `"commitment"`. Candidate ids are bare canonical ids. Within one row every candidate is the same type, so a bare id is unambiguous.
  - `template_rows(rows: list[dict]) -> list[dict]`: the template variables. Each candidate gains:
    - `line`: `prompts.render("snippets/plot_thread_line/absorb.j2", t=…)` or `prompts.render("snippets/commitment_line/absorb.j2", c=…)`, with a blank commitment kind passed as `promise`;
    - `signal_text`: `"; "`-joined phrases in fixed order: `same title`, `same slug`, `word overlap 0.NN`, `text overlap 0.NN`, `meaning 0.NN`, `shared characters: <ids>`, `shared scenes: <n>`, `shared dates: <ids>`.

    Snippets are rendered by Python, per the templates README convention.
  - `build_prompt(rows: list[dict]) -> list[dict]`: `[{"role": "system", "content": prompts.render("continuity_identity/system.j2")}, {"role": "user", "content": prompts.render("continuity_identity/user.j2", rows=template_rows(rows))}]`.
  - `parse_output(text: str) -> list[dict] | None`:
    - `None` iff `absorb_parse.extract_object(text)` is `None` (undecodable).
    - Otherwise it reads `obj.get("decisions")`; a non-list gives `[]`.
    - Each element that is a dict with a non-blank `str` `row` becomes `{"row", "decision", "id", "reason"}`. `row` is normalized to the key form: stripped, casefolded, and a leading `row` word removed (`"Row r1"`, `"ROW R1"` and `"r1"` all become `"r1"`), because the user prompt prints `Row <key>`. `decision` is lowercased; anything outside `DECISIONS` becomes `"uncertain"`. `id` is a stripped `str`, or `""`. `reason` is a stripped `str`, clipped to `REASON_CHARS`, or `""`.
    - On a duplicate `row` key, the first wins.
    - Nothing raises on bad JSON.
- System prompt (static):
  - It opens with the unique phrase **`You are checking whether newly proposed story records`**. It must not contain `You are absorbing a completed role-play scene` or `You are auditing a completed role-play scene`.
  - It explains the following, and each decision instruction carries the exact phrase in quotes below, unique in the template, because the eval's `prompt.*` needles (Task 12) pin each one verbatim (§28.10):
    - one decision per row;
    - `"existing"` only when a listed candidate is the same narrative question or obligation, naming that candidate's `"id"` — phrase: `only when a listed candidate is the same narrative question or obligation`;
    - `"new"` for a different question, a continuation, or a related subplot — phrase: `a continuation, or a related subplot`;
    - `"uncertain"` when the transcript evidence cannot tell — phrase: `when the transcript cannot tell`;
    - that a closed or resolved candidate is shown so the model knows it was settled, and a later development is not that record — phrase: `A closed or resolved candidate is never "existing"`;
    - that `"distinguished_from"` and similarity signals are hints, not proof — phrase: `are hints, not proof`.
  - It states the reply shape: ONLY `{"decisions": [{"row": "<row key>", "decision": "existing" | "new" | "uncertain", "id": "<candidate id, for existing>", "reason": "<one short sentence>"}]}`.
  - It writes all three decision words in double quotes.
- `user.j2`:
  - A `{#- ... -#}` header documents its variables.
  - Per row: `Row <key> — proposed <plot thread|commitment>: <title>`, the beat, the status (and commitment kind and due when given), the citation when present (`"<quote>" — <speaker>`, `certainty <n>`), `why_new` when present, `distinguished_from` when non-empty, then `Candidates:` with each `line`, its earlier beats, and `signals: <signal_text>`.
  - Every optional piece is guarded with `{% if %}` on keys that are always passed (StrictUndefined).
- `campaign_flow.json` entry: `{"when": {"system_contains": "You are checking whether newly proposed story records"}, "reply": "{\"decisions\": [{\"row\": \"r1\", \"decision\": \"new\", \"id\": \"\", \"reason\": \"A different debt from the one already owed.\"}]}"}`. The reply is a string.
- [ ] **Step 1: Write the failing tests**
  - In `tests/test_continuity_identity.py`:
    - `test_parse_undecodable_is_none`: `parse_output("I think so.") is None`.
    - `test_parse_decodable_empty_is_empty_list`: both `"{}"` and `'{"decisions": 3}'` give `[]`.
    - `test_parse_accepts_row_labels_as_printed`: `"Row r1"`, `" R1 "` and `"r1"` all normalize to `"r1"`.
    - `test_parse_normalizes_and_never_raises`:
      - a mixed list (a non-dict, a missing row, `"EXISTING"`, `"maybe"`, duplicate rows, an over-long reason, a non-str id) gives exactly the expected normalized list;
      - `"maybe"` becomes `uncertain`;
      - the reason has length `REASON_CHARS`.
    - `test_build_prompt_shows_rows_candidates_and_signals`, built from a two-row `prompt_rows()` fixture (thread and commitment):
      - the user content contains both row keys, each candidate's snippet line, `signals: same title`, the citation, and `why_new`;
      - the system content contains `'"existing"'`, `'"new"'` and `'"uncertain"'`.
    - `test_build_prompt_with_no_optional_fields_renders`: a row with no citation, no `why_new`, no `distinguished_from` and a candidate with no beats renders without `StrictUndefined` errors, and without the empty labels.
  - In `tests/test_llm_fakes.py`:
    - add `prompts.render("continuity_identity/system.j2")` to `_rendered_prompts()`;
    - add `test_the_identity_body_is_the_shape_the_parser_expects`: the cassette reply for the identity system phrase parses via `identity.parse_output` to one decision.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py tests/test_llm_fakes.py -q`. Expected: FAIL (missing template, cassette coverage).
- [ ] **Step 3: Implement**
  - The templates, `prompt_rows`, `template_rows`, `build_prompt`, `parse_output`, and the cassette entry.
  - `scripts/verify_templates.py`: a pure block before `# --- store fixture`. It imports `from grimoire.store.continuity import identity  # noqa: E402`. For three representative inputs (`thread+commitment` with every optional field, `bare` with none, `closed-neighbour`), it checks:
    - `check("continuity identity system (<label>)", exp[0]["content"], render("continuity_identity/system.j2"))`;
    - `check("continuity identity user (<label>)", exp[1]["content"], render("continuity_identity/user.j2", rows=identity.template_rows(rows)))`;
    - for every candidate, `line == render("snippets/plot_thread_line/absorb.j2", t=...)` or the commitment snippet.

    Inputs use `find-the-ledger` / `the-midnight-deadline` / Seraphine / Winifred only.
  - `templates/README.md`: a `### continuity_identity/ — the duplicate check inside POST …/absorb` section after `### audit/`. It is written as “Mirrors `store/continuity/identity.py:build_prompt`. Messages: system, user.”, followed by:
    - the variables;
    - the reply shape;
    - “parsed by `identity.parse_output` through `absorb.extract_object`”;
    - undecodable means the phase is `failed`;
    - one sentence (§29): the resolver's replies, including each row's `reason`, are captured at Debug level like every other LLM response, under the existing Settings disclosure, while its info-level log row carries counts and modes only.
  - `docs/incoming-llm-capture.md`: the same §29 sentence, naming the `continuity-identity` task.
- [ ] **Step 4: Run the identity and cassette tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py tests/test_llm_fakes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the template checks, the docs guard and the lint ratchets.** From the repo root: `make check-templates check-lint check-mypy PY=$PWD/backend/.venv/bin/python`, and from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_guard.py -q`. Expected: all pass, including the new template checks.
- [ ] **Step 6: Commit:** `feat(continuity): continuity_identity prompt family and tolerant parser`

---

### Task 7: Deciding, downgrading and rewriting rows

**Files:**
- Modify: `store/continuity/identity.py`
- Test: `tests/test_continuity_identity.py`

**Interfaces:**
- Produces, on `Examination`:
  - `decide(decisions: list[dict]) -> None`. Rows are visited in examination order, with a `taken` set seeded from `self.targets`. Per row, using its decision by key:
    - **missing:** `unchecked` / `hint_only`, reason `"the check gave no answer for this row"`.
    - **`existing`:** the named id is first canonicalized (§10.2 “resolves, canonicalized”): a leading `thread:` / `commitment:` matching the row's kind is stripped, then the bare id is mapped through `self.live` (`live.get(f"{kind}:{id}")`, bare id of the result). Then:
      - If that id is not one of the row's candidate ids: `uncertain` / `downgraded`, reason `"named a record that was not offered"`.
      - Else, if the candidate is not live: `uncertain` / `downgraded`, reason `"that record is already closed or resolved"`.
      - Else, if `(kind, id)` is in `taken`: `uncertain` / `downgraded`, reason `"another row in this scene already moves that record"`.
      - Otherwise: `existing` / `accepted`, `target = id`, and `taken.add`.
    - **`new` / `uncertain`:** as given, status `accepted`, with the model's reason.
  - `hint_only(reason: str) -> None`: every row becomes `unchecked` / `hint_only` with this reason, `target = None`. Idempotent; it discards any earlier `decide`.
  - `rewritten(parsed: dict) -> dict`: a shallow copy of `parsed` with fresh lists for the two sections. Each examined row is replaced by:
    - **accepted `existing`:** `{**row, "id": target, "title": "", "identity_check": ic, AS_NEW_KEY: dict(row)}`. Plot `status` becomes `row["status"] if row["status"] in ("closed", "advanced") else "advanced"`. Commitment `kind` becomes `""` and `status` becomes `row["status"] if row["status"] in commitments.RESOLVED else ""`. `due` is left exactly as present or absent.
    - **otherwise:** `{**row, "identity_check": ic}`.

    Citation keys (`quote`, `speaker`, `certainty`) are untouched. Here `ic` is:

    ```
    {"decision", "status", "reason",
     "proposed": {"title": row title-or-id, "why_new": row.get("why_new", ""),
                  "distinguished_from": filtered list},
     "candidates": [{"ref", "title", "status", "latest_beat", "signals"}]}
    ```

    No `alternatives` key yet; materialize adds it.
  - `counts() -> dict[str, int]`, with flat keys: `proposed`, `examined`, `candidates`, `deterministic` (candidates whose `signals["via"]` is `"lexical"` or `"structural"`), `semantic` (candidates admitted only through a cosine clause, `via == "semantic"`), `embedded`, `existing`, `new`, `uncertain`, `unchecked`, `downgraded`, `hint_only`. `deterministic + semantic == candidates` (§29's “deterministic vs semantic candidate counts”).
  - `all_unchecked() -> bool`: every examined row is `unchecked` (Task 11 reports that as `degraded`).
- [ ] **Step 1: Write the failing tests**
  - `test_accepted_existing_rewrites_plot_row_status_never_open`: with original statuses `"open"`, `"advanced"` and `"closed"`, the rewritten statuses are `advanced`, `advanced` and `closed`. In each case `id` is the canonical id, `title == ""`, and `AS_NEW_KEY` holds the original.
  - `test_accepted_existing_rewrites_commitment_fields`:
    - original `kind "threat"`, `status "open"`, no `due` gives `kind ""`, `status ""`, and no `due` key;
    - original `status "fulfilled"` with `due "midnight"` keeps both.
  - `test_existing_naming_an_unoffered_id_is_uncertain` (Review Focus 2).
  - `test_existing_naming_a_closed_candidate_is_downgraded` (Review Focus 2): `decision == "uncertain"`, `status == "downgraded"`, and the row id is unchanged.
  - `test_second_row_mapping_to_the_same_record_is_downgraded` (Review Focus 2): row r1 is accepted and row r2 is downgraded.
  - `test_existing_naming_a_record_an_explicit_row_already_moves_is_downgraded`: the target is in `targets` from a non-proposed row.
  - `test_existing_named_by_alias_source_or_ref_form_is_accepted`: with alias `thread:a → thread:b` and candidate `b`, both `id: "a"` and `id: "thread:b"` are accepted with `target == "b"`.
  - `test_missing_decision_is_unchecked_hint_only`.
  - `test_unknown_decision_word_arrives_as_uncertain_accepted`: the parsed `"maybe"` has become `uncertain`.
  - `test_hint_only_discards_decide`.
  - `test_citations_survive_rewrite`: `quote`, `speaker` and `certainty` are equal before and after.
  - `test_counts_are_flat_ints`: every value is an `int`, the keys are exactly the list above, and `deterministic + semantic == candidates`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the identity tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py -q`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root (`decide` split per decision word to stay under C901). Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): accept, downgrade and rewrite identity decisions`

---

### Task 8: Absorb staging follows merges (§7.2)

**Files:**
- Modify: `store/absorb/materializer.py` (import `from ..continuity import effective as continuity_effective`)
- Test: `tests/test_absorb_store.py`, `tests/test_continuity_identity.py` (one test)

**Interfaces:**
- Produces, inside `materialize`:
  - `live = _live_canon(cid)`: `continuity_effective.live_canon(cid)`, or `{}` when it raises. The catch is narrowed to what the reader raises (`OSError`, `ValueError`, `TypeError`, `KeyError`), or, if a broad catch is genuinely needed, carries `# noqa: BLE001 -- a garbled continuity.json reads as no aliases everywhere`. A malformed `continuity.json` reads as no aliases everywhere, so not redirecting hides nothing. `materialize` passes `live` to `assign_ids`, and `identity.examine` passes the same map, so both see one redirect.
  - **The redirect lives in `assign_ids`**, so `examine` and `materialize` agree on it:
    - **Explicit id** naming a live alias source: redirected to the canonical whatever the canonical's status (an explicit id is a reference, §10.5). When the canonical is closed (thread) or resolved (commitment), the label adds the canonical's stored status — `… → merged into <title> (closed)` — so the reviewer sees that approving the row reopens it.
    - **Slug-derived id** that lands on a live alias source: honoured (redirected) only when the canonical passes the same predicate the allocator applies — `effective.is_live(kind, canonical status)` and the canonical's casefolded title equals the row title or the source's title. Otherwise `_free` treats that candidate as taken and the allocator continues to the next `slug-N`, so the row is proposed-new and the identity step examines it with the closed canonical as a candidate. Redirecting it instead would reopen a closed or resolved canonical with no identity check — exactly the route §10.5 closes, and against §10.2's “identity resolution never reopens a record”.
  - **Plot.** After the pid is assigned and **before** the `seen_pids` check: if `f"thread:{pid}"` was redirected, set `merged_from = (pid, source title)` and replace `pid` with the canonical id.
    - The canonical's stored title becomes `disp_title`.
    - `before = conflicts.plot_line(threads[canonical])`, the PHYSICAL canonical.
    - `label = f"{source title} — {status} → merged into {canonical title}"`.
    - Payload `id` and `title` are the canonical's.
  - **Commitment.** An explicit `given` id is redirected before the `staged_titles` reservation, and the reservation uses the canonical's stored title. A slug-derived id is redirected after allocation and before `seen_mids`. The label is `f"{source title} — {disp_kind}, {disp_status}[, due …] → merged into {canonical title}"`, and `before` is `commitment_line` of the physical canonical.
- [ ] **Step 1: Write the failing tests.** Aliases are created through `continuity.review.create_alias` with Slice A's signature, as its tests do.
  - `test_materialize_redirects_an_alias_source_id` (Review Focus 3): given an alias `thread:maras-map → thread:winifreds-chart`, a row with id `maras-map` stages `plot:winifreds-chart`. The label ends `"→ merged into Winifred's chart"`, `before == conflicts.plot_line(plot.get(cid, "winifreds-chart"))`, and `check_conflicts(cid, [row]) == []`.
  - `test_materialize_redirects_an_alias_source_slug`: an id-less row titled exactly like the open source stages onto the canonical.
  - `test_source_and_canonical_in_one_batch_stage_once`: rows naming `maras-map` and `winifreds-chart` give one edit.
  - `test_commitment_alias_source_redirects_and_reserves_canonical_title`.
  - `test_dangling_alias_does_not_redirect`: an alias to a deleted target leaves the row on the source.
  - `test_malformed_continuity_file_stages_physically`: `continuity.json` `"{ no"` makes the row target the source, with no exception.
  - `test_slug_collision_on_a_source_with_closed_canonical_is_not_redirected`: alias `thread:maras-map → thread:winifreds-chart`, canonical closed, source open; an id-less row titled exactly like the source stages as `plot:maras-map-2` with `before == ""`, and `plot.get(cid, "winifreds-chart")["status"]` stays `closed` after approving it.
  - `test_explicit_source_id_with_closed_canonical_is_labelled_closed`: the same alias, a row with id `maras-map`; it stages on `winifreds-chart` and the label ends `"→ merged into Winifred's chart (closed)"`.
  - In `tests/test_continuity_identity.py`, `test_examine_treats_source_title_with_closed_canonical_as_proposed`: the same alias and row; the row is proposed-new and `thread:winifreds-chart` is among its candidates with `live False`.
- [ ] **Step 2: Run them and confirm they fail.** Expected: every new test FAILS except `test_dangling_alias_does_not_redirect` and `test_malformed_continuity_file_stages_physically`, which are guards: today's materialize never redirects, so they pass before and after.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the absorb tests, their dependents and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_continuity_identity.py tests/test_import_guard.py tests/test_absorb_routing.py tests/test_undo_store.py tests/test_ingest_scene.py tests/test_frozen_campaign.py tests/test_tracker_absorb.py tests/test_evals.py -q` (the dependents Task 2 named, plus `test_evals`, whose absorb replay grades `absorb.materialize`). Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(absorb): stage movements on an alias source onto its canonical record`

---

### Task 9: Staging identity checks — band, chip data and alternatives

**Files:**
- Modify: `store/absorb/materializer.py`, `store/pending_reviews.py` (`_follow_stamps`)
- Test: `tests/test_absorb_store.py`, `tests/test_pending_reviews_store.py`

**Interfaces:**
- Produces, in `materializer.py`:
  - `_plot_edit(sid, row, pid, threads, *, merged=None) -> dict` and `_commitment_edit(sid, row, mid, owed, *, merged=None) -> dict`. Today's per-row staging bodies are moved out unchanged: label, payload, physical `before`, display fallbacks and Task 8's label suffix. Each returns the edit without `review`.
  - `_staged(edit, row, *subjects)` additionally:
    - pops `AS_NEW_KEY` from a **copy** of the row before reading anything;
    - when `row.get("identity_check")` is a dict, sets `edit["identity_check"] = {**ic, "alternatives": []}`;
    - when its `decision` is `"uncertain"` **or `"unchecked"`**, sets `edit["review"] = {**edit["review"], "band": "low"}`. Every examined row has at least one plausible candidate (Task 5), so a row the check never answered — a partial reply, a failed call, a refused budget — is a possible duplicate nobody ruled out, and keeping its routing band would pre-approve it outside NEEDS YOU (Codex review on #466; this supersedes the "at the row's routing band" wording under Decisions).

    `routing.band`, the weights and `apply` are untouched.
  - `_recheck_accepted(parsed, threads, owed, live) -> dict`, a pre-pass run before `assign_ids`. `materialize` runs only after `_gather_phases` returns — after the slowest phase — so a record the resolver's accepted `existing` named can be closed or resolved in that window (a Ledger edit, another scene's review save; neither is refused by this scene's `scene_busy`). For each row whose `identity_check` is `existing` / `accepted`, it re-reads the target's stored status (after mapping it through `live`). If the target is gone or not live (`effective.is_live`), the row is replaced by its `AS_NEW_KEY` original carrying the same `identity_check` with `decision "uncertain"`, `status "downgraded"`, reason `"that record was closed while the review was being prepared"` — so it stages new, at band `low`.
  - A second pass after the commitment block, `_attach_alternatives(sid, out, rows_by_edit, threads, owed, live, staged_plot_titles, staged_titles)`:
    - Let `taken` be every `("plot", id)` / `("commitments", id)` an edit in `out` targets.
    - For each edit with `identity_check`, in order:
      - **(a)** One alternative per candidate, **revalidated against the state materialize read, never the examination's snapshot** (Codex review on #466): the candidate id is mapped through the current `live` map to its canonical, the canonical must name a stored dict record in `threads` / `owed` whose own status is live (`effective.is_live`), and that canonical must be neither in `taken` nor this edit's own target. A candidate closed, resolved or merged away while the other phases ran is skipped, exactly as `_recheck_accepted` skips the accepted target; two candidates that now share a canonical yield one alternative. It is built from the §10.2 existing-rewrite of the original row onto the candidate id. The rewrite is the same rules as `Examination.rewritten`, restated in materializer as `_onto_existing(kind, row, id)`, because materializer cannot import identity.
      - **(b)** When `decision == "existing"` and `status == "accepted"`, the as-new variant: the `AS_NEW_KEY` row staged at its original explicit id only when that id is free **and slug-shaped** (`slugify(id) == id`), or else at `_new_*_id(stored, staged_map, slugify(title or id), title or id)`. A model-written id such as `plot/the-map` would create a record the Ledger's `PUT/DELETE /ledger/threads/{pid}` cannot address. That id is reserved in `staged_map` so two as-new variants never share an id.
    - Every alternative gets `review = copy of the primary edit's review` and no `identity_check`.
    - `_attach_alternatives` stays under C901's limit by delegating (a) and (b) to `_candidate_alternatives` and `_as_new_alternative`.
  - **Failure boundary for the identity-only passes.** `materialize` runs in `_absorb_work` outside every phase boundary, where only `Abandoned` and `LLMError` are caught, so a defect in an identity-only pass must not turn a paid-for extraction into a failed absorb. `materialize` wraps each of `_recheck_accepted` and `_attach_alternatives` in `try` / `except Exception as exc:  # noqa: BLE001 -- an identity-only pass; a defect stages the rows without it, never fails the absorb`. On failure: `_recheck_accepted` leaves `parsed` as given; `_attach_alternatives` leaves every `identity_check.alternatives` at `[]`. Each records via `store.errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)` and then calls `materialize`'s new optional keyword `on_identity_error: Callable[[BaseException], None] | None = None`, so `_absorb_work` (Task 11) can mark the identity block `failed` without changing `materialize`'s return shape or touching its other callers.
  - `pending_reviews._follow_stamps`: for each edit, the same `payload.scene` / `before` / `resolve_from` repoint is applied to every dict in `edit.get("identity_check", {}).get("alternatives", [])`.
- [ ] **Step 1: Write the failing tests**
  - In `tests/test_absorb_store.py`, call `materialize` with hand-built rewritten parsed rows:
    - `test_uncertain_row_is_band_low_and_keeps_its_citation_score`: the `review.band` of an `identity_check.decision == "uncertain"` row is `"low"`; the `score` and `quote` are unchanged.
    - `test_unchecked_row_is_band_low`: a row whose `identity_check` is `unchecked` / `hint_only` (citation band `high` before) stages at band `"low"`; a `new` / `accepted` row keeps its routing band.
    - `test_row_without_identity_check_has_no_identity_key`: for a plain id-less plot row and a plain commitment row, the full edit equals a spelled-out literal dict (`id`, `kind`, `target`, `label`, `field`, `before`, `after`, `authored`, `payload`, `review`), in the style of `test_materialize_plot_new_and_advance`, and `"identity_check" not in edit`. Literal, so it can catch a regression in the extracted `_plot_edit` / `_commitment_edit`.
    - `test_alternatives_are_complete_rows_with_valid_before_tokens`:
      - for an uncertain new row with two live candidates, there are two alternatives;
      - each has `kind`, `target`, `label`, `payload.scene == sid` and `before == plot_line(stored)`;
      - `check_conflicts(cid, [alt]) == []` for each;
      - the private key is absent from every edit and payload.
    - `test_accepted_row_offers_the_as_new_variant`: an alternative has `before == ""`, a fresh id, and the model's original title.
    - `test_alternatives_skip_closed_and_batch_taken_candidates`.
    - `test_alternatives_are_revalidated_against_the_current_store`: the row's `identity_check.candidates` say both `mara-s-map` and `winifred-s-chart` are `open`, but before `materialize` runs `mara-s-map` is closed in the store and `winifred-s-chart` is aliased into a third open thread `seraphine-s-map`. The only alternative targets `seraphine-s-map`; none targets the closed thread or the alias source.
    - `test_two_as_new_variants_never_share_an_id`.
    - `test_as_new_variant_never_keeps_a_non_slug_id`: an accepted row whose original id was `"plot/the-map"` gets an as-new variant at a slug-shaped id derived from its title.
    - `test_accepted_target_closed_before_staging_is_downgraded`: a rewritten accepted row whose target is closed in the store before `materialize` runs stages as new (`before == ""`, the model's title), with `identity_check.decision == "uncertain"`, `status == "downgraded"`, band `low`, and the closed thread untouched.
    - `test_commitment_alternative_before_is_commitment_line`.
    - `test_alternatives_never_carry_identity_check`.
    - `test_a_failing_alternatives_pass_stages_the_rows_without_alternatives`: monkeypatch `materializer._attach_alternatives` to raise `RuntimeError`; `materialize` returns, the examined row is staged with `identity_check.alternatives == []`, and the `on_identity_error` callback received the exception.
  - In `tests/test_pending_reviews_store.py`, `test_follow_stamps_repoints_alternatives`: an alternative's `payload.scene` and commitment `before` suffix move from old to new.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement**, extracting the helpers first in a behaviour-preserving step with the old tests still green, then adding the new behaviour.
- [ ] **Step 4: Run the absorb store, conflict and pending-review tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_absorb_conflicts.py tests/test_pending_reviews_store.py tests/test_retcon_store.py tests/test_absorb_routing.py tests/test_undo_store.py tests/test_ingest_scene.py tests/test_frozen_campaign.py tests/test_tracker_absorb.py tests/test_evals.py -q`. Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS (`materializer.py` already holds one C901 finding; no new function may add a second).
- [ ] **Step 5: Commit:** `feat(absorb): stage identity checks with low band and server-rendered alternatives`

---

### Task 10: Settings discloses automatic embedding (§9.5, AC18)

This task lands before Task 11, so no commit on the branch embeds continuity text before the disclosure exists.

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`, `frontend/src/api/types.ts` (the comment on the embeddings keys)
- Create: `frontend/src/routes/embeddingsOn.ts`
- Test: `frontend/src/routes/ConfigView.test.tsx`, `frontend/src/routes/embeddingsOn.test.ts`

**Interfaces:**
- Produces:
  - `embeddingsOn(draft: {embeddings_connection_id: string; embeddings_model: string}, connections: LLMConnection[]) -> boolean`. It mirrors `embed_space.resolve()`, with a comment naming it: the id is set, the connection is found, `kind === "openai_compatible"`, `base_url` is non-empty, and `embeddings_model.trim()` is non-empty. The row chip only ever shows SAVED state — a dirty row shows the unsaved dot instead of its value (“the dot wins”, `ConfigView.tsx` around line 484) — so the mirror is evaluated over the draft, which equals the saved config whenever the value is visible.
  - `EMBEDDINGS_COPY`, exported from `embeddingsOn.ts`: the §9.5 copy from Global Constraints, verbatim, rendered as the whole text of its own `<p>`.
  - `SECTIONS` entry `{ id: "semantic", …, label: "Embeddings" }`.
  - `valueOf("semantic")`:
    - `!embeddingsOn` gives `"off"`;
    - depth `"0"` or blank gives `"on · recall off"`;
    - otherwise `` `on · recall ${depth}` ``.
  - The intro paragraph:
    - keeps the recall explanation;
    - replaces “Leave the connection blank, or set entries to `0`, to turn it off.” with “Set recalled entries to `0` to turn recall off.”;
    - then adds a new `config-copy` paragraph whose text is `{EMBEDDINGS_COPY}`.
  - The NumField caption becomes `"0 = recall off"`.
  - The disclosure paragraph keeps “**This sends text to the endpoint above.**”. It names every embedder that runs whenever `resolve()` is set: recent scene text and the world info being searched (recall), image descriptions (the art catalogue), the scenes and records being searched when you search the library by meaning (`store/semsearch.py`, which embeds regardless of recall depth), and plot-thread and commitment summaries after each wrap-up (finding possible overlaps), as “a second place your campaign is read”, and keeps the local-endpoint advice.
- [ ] **Step 1: Write the failing tests**
  - In `embeddingsOn.test.ts`:
    - each missing condition gives false: a blank id, an unknown id, an `openrouter` kind, an empty `base_url`, a blank model;
    - all present gives true.
  - In `ConfigView.test.tsx`:
    - update `/^Semantic recall/` to `/^Embeddings/` in the three existing tests;
    - `the embeddings chip reports embedding separately from recall depth`: state is set through `api.getConfig.mockResolvedValue({...cfg, embeddings_connection_id: "local", embeddings_model: "text-embedding-3-small", semantic_recall_depth: "0"})` (with a matching `openai_compatible` connection in the connections mock), never by editing the form — an edited row shows “unsaved”, not its value. Wait with `findByRole`; the row's accessible name matches `/^Embeddings.*on · recall off/`;
    - `the embeddings chip reads off without a model`: the same with `embeddings_model: ""` reads `off`;
    - `the copy is the spec text verbatim`: `screen.getByText(EMBEDDINGS_COPY)` — an exact full-string match on one `<p>`;
    - `the disclosure names every embedded payload`: `const p = screen.getByText(/This sends text to the endpoint above/).closest("p")!`; `p.textContent` contains `recent scene text`, `world info`, `image descriptions`, `search the library by meaning` and `plot-thread and commitment summaries`, and `p.textContent !== EMBEDDINGS_COPY`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/ConfigView.test.tsx src/routes/embeddingsOn.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** `npx vitest run src/routes/ConfigView.test.tsx src/routes/embeddingsOn.test.ts`. Expected: PASS.
- [ ] **Step 5: Run the typecheck.** `npm run typecheck`. Expected: PASS.
- [ ] **Step 6: Run ESLint.** From the repo root: `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with no baseline growth.
- [ ] **Step 7: Commit:** `feat(settings): Embeddings section discloses automatic continuity embedding`

---

### Task 11: The identity phase inside absorb

**Files:**
- Modify: `store/routing.py`, `routes/scenes.py`
- Test: `tests/test_absorb_identity.py` (new), `tests/review_runs.py` (adds `EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER` and `identity_requests`, see Decisions), `tests/test_routing_routes.py`, `tests/test_routes.py` (`test_absorb_reports_every_phase_attempted`, `test_absorb_phases_mirror_the_dossier_and_mechanics_blocks`), `tests/test_pending_reviews_store.py` (`test_a_phase_row_carries_exactly_what_the_phase_report_puts_in_one`)

**Interfaces:**
- Produces:
  - In `store/routing.py`, after the `dossier` route: `Route("continuity", "Continuity checks", "The duplicate check beside absorb: one call, only when a proposed new thread or commitment closely resembles an existing one.", ("continuity-identity",), True)`.
  - In `routes/scenes.py`, one import: `from ..store.continuity import identity as continuity_identity, similarity as continuity_similarity` (`run_in_threadpool` is already imported; `identity` is already a parameter name in this module). The snippets below write `continuity_identity.` / `continuity_similarity.` for these.
  - `async def _identify(cid, sid, client, conn, why, parsed, prepared, budget) -> tuple[dict, dict]`. It never raises except `Abandoned` and cancellation. It returns `(parsed_for_materialize, block)`, where `block` is:

    ```
    {"status", "reason", "attempted", "budget_exhausted", "matching", "counts"}
    ```

    `matching` is an addition (§3.3), and it is set on **every** return path, step 1 included: `continuity_similarity.matching()`, or `exam.matching` once `exam` exists (the same function, so the two cannot disagree).
    1. If `not continuity_identity.has_proposals(parsed)`, return `skipped` with reason `"no new records proposed"`, `attempted False`, and `counts {}`. There is no worker hop.
    2. `exam = await run_in_threadpool(continuity_identity.examine, cid, sid, parsed, prepared.facts, embed_deadline=continuity_similarity.deadline(budget.remaining()))`.
    2b. If `exam.embedding == "failure"` **and `budget.spent()`**, the embed was cut by this absorb's own clock, not by the provider (`EmbeddingsClient` reports a passed deadline as `EmbeddingsError("network", …)`, and the deadline may be the budget's). Write no error row; set `budget_exhausted True` and use the reason `"the absorb time budget ran out during semantic matching — basic matching used"` wherever steps 3 and 5 would use the semantic-unavailable reason. This is the same line `BudgetRefused.llm_call_failed = False` and `_budget_overrun` draw for LLM calls. Otherwise, if `exam.embedding == "failure"`, write one counts-only ERROR row so a dead endpoint reaches the error store (#156; §26 “log failure”): `store.errors.record("continuity-identity", exam.embedding_error or "network", "semantic matching failed; basic matching used", campaign=cid, scene=sid, task="continuity-identity")`. The detail is a constant: no titles, beats or identity texts.
    3. If `not exam.rows`, the status is `degraded` (reason `"semantic matching unavailable — basic matching used"`, or step 2b's budget reason) when `exam.embedding == "failure"`, else `skipped` (reason `"no close existing records"`).
    4. Else, if `conn is None`, call `exam.hint_only(why or "no connection")`. The status is `failed` with that reason.
    5. Else:

       ```
       with store.usage.meter("continuity-identity", campaign=cid, scene=sid) as m:
           reply = await budget.run(
               client.complete(continuity_identity.build_prompt(exam.prompt_rows()), conn, m.usage),
               lambda: block.__setitem__("attempted", True),
               on_timeout=_noting(client, conn, m.usage))
       ```

       Then `decisions = continuity_identity.parse_output(reply)`. If it is `None`, call `exam.hint_only("the duplicate check returned no readable answer")` with status `failed`. Otherwise call `exam.decide(decisions)`; the status comes from a helper, `_identity_status(exam)`: `degraded` with reason `"the duplicate check answered none of the rows"` when `exam.all_unchecked()`, else `degraded` with reason `"the duplicate check left some rows unanswered"` when `exam.counts()["unchecked"] > 0` (a partial reply is a partial failure, not `ok` — Codex review on #466), else `degraded` with the step-3 reason when `exam.embedding == "failure"`, else `ok`. (Splitting the status out keeps `_identify` under C901's limit.)
    6. `except Abandoned: raise`.
    7. `except BudgetRefused`: `exam.hint_only(...)` if `exam`. The status is `failed`, `budget_exhausted True`, and the reason is `"the absorb time budget ran out before the duplicate check could run"`.
    8. `except Exception as exc:  # noqa: BLE001 -- LLMError, EmbeddingsError, store errors: a failed phase, never a failed absorb`, which deliberately also covers `EmbeddingsError` (an `LLMError`) so it can never reach `_absorb_work`'s fatal `except LLMError`:
       - `store.errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)`;
       - `exam.hint_only(...)` if `exam`;
       - status `failed`, `budget_exhausted=_budget_overrun(exc)`, reason `f"duplicate check failed: {exc}"`.
    9. The result is `exam.rewritten(parsed) if exam else parsed`, and `counts` is `exam.counts() if exam else {}`.
    10. `store.logs.record("info", __name__, "continuity identity check", kind="continuity-identity", campaign=cid, scene=sid, status=…, matching=…, embedding=…, embedding_error=…, **counts)`. Scalars only.

    Steps 9 and 10 sit **inside** the `try` whose handlers are steps 6–8, not after it, so a defect in `rewritten`, `counts` or the log write is a failed phase too: on an exception there, the result falls back to the unmodified `parsed`, the status is `failed`, `counts` is `{}`, and the exception is recorded as in step 8. The `except` clauses produce the block; only the final `return` follows them.
  - `async def _extract_and_identify(extraction, cid, sid, client, prepared, budget, ident_conn, ident_why) -> tuple[dict, dict]`, where `extraction` is the already-built extraction awaitable:
    - `text = await extraction`. It raises, and that stays fatal.
    - Then `return await _identify(cid, sid, client, ident_conn, ident_why, store.absorb.parse_output(text), prepared, budget)`.

    The extraction's `client.complete(...)` call stays in `_absorb_work`, spelled `m.usage`, because `test_usage_guard._is_metered` accepts only an `ast.Attribute` named `usage` as the holder: a helper receiving a bare `usage` name and calling `client.complete(..., usage)` fails the guard, and a `# usage-ok:` marker is wrong for a call that is metered (the guard caps them).
  - In `_absorb_work`:
    - `ident_conn, ident_why = _soft_connection(lambda: _require_connection("continuity-identity", cid))` beside the other three;
    - the first `_gather_phases` argument becomes `_extract_and_identify(budget.run(client.complete(prepared.messages, conn, m.usage), on_timeout=_noting(client, conn, m.usage)), cid, sid, client, prepared, budget, ident_conn, ident_why)` — the extraction awaitable is built exactly as today and handed in;
    - the unpack becomes `extraction, dossier_result, voice_result, audit_result = results`;
    - the `isinstance(extraction, BaseException)` check stays;
    - `parsed, identity_block = extraction`, and the old `parse_output` line is deleted;
    - `materialize(..., on_identity_error=...)` (Task 9) gets a callback that sets `identity_block["status"] = "failed"` and `identity_block["reason"] = "the duplicate check's staging step failed; rows staged without alternatives"`, so a defect in an identity-only staging pass reports on the identity row instead of failing the absorb;
    - the review gains `"identity": identity_block`;
    - `"phases": _phase_report(dossiers, voice, mechanics, identity_block)`.
  - `_phase_report(dossiers, voice, mechanics, identity_block)` (not `identity`, which is the scene-identity fence's name elsewhere in the module) gives rows in order `extraction, identity, dossiers, voice, audit`, with identity projected through `PHASE_KEYS`. The docstring is updated: run order; identity is chained onto extraction and has no retry route.
- [ ] **Step 1: Write the failing tests.** `tests/test_absorb_identity.py` uses fixtures modelled on `test_routing_routes._seed`: a key on the active connection, a world "Realm", Mara, a campaign, and a scene "Saltmarch" with two posts. It also seeds threads and commitments through `plot.set_movement` / `commitments.set_movement`, in an earlier scene `s0` per Decisions. The LLM is a `llm_fakes.from_entries` fake with two entries:
  - extraction: `system_contains: "You are absorbing a completed role-play scene"`;
  - identity: `system_contains: "You are checking whether newly proposed story records"`.

  `identity_requests`, `LEDGER_THREAD`, `RECOVER_THE_LEDGER`, `SALTMARCH_TITHE` and `EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER` come from `tests/review_runs.py` (the first three from Task 5, the last two added here); no test module imports another. No inline fake is written: every LLM double is `llm_fakes.from_entries`, and every embeddings double is `llm_fakes.FakeEmbeddings`.

  Tests:
  - `test_a_new_record_with_no_close_neighbour_makes_no_identity_call` (§28.3): `LEDGER_THREAD` seeded in `s0`, an extraction proposing `SALTMARCH_TITHE`, zero identity requests, an identity phase row of `skipped`, and `body["identity"]["matching"] == "basic"`.
  - `test_an_absorb_proposing_no_records_skips_identity_with_matching_set`: an extraction with empty `plot_movements` and `commitment_movements` (the step-1 skip, no worker hop) gives identity `skipped`, reason `"no new records proposed"`, and `body["identity"]["matching"] == "basic"`.
  - `test_identity_matching_agrees_with_get_continuity`: under one config, unconfigured and then configured, `body["identity"]["matching"] == client.get(f"/api/campaigns/{cid}/continuity").json()["matching"]` (§3.3: one definition; guards the rebase with Slice B, coordination note 5).
  - `test_identity_fields_never_reach_the_ledgers` (§10.1): one extraction row carries `why_new` and `distinguished_from` with no plausible candidate; another carries them and is examined. Neither staged edit's `payload` has either key. After `PUT /chronicle` approving both, no record in `plot.read(cid)` or `commitments.read(cid)` has either key. A regression pin: today's payloads are spelled out field by field, so it passes once the rows stage at all.
  - `test_a_failing_identity_staging_pass_fails_only_the_phase`: monkeypatch `materializer._attach_alternatives` to raise. Absorb is 200, the identity phase is `failed`, and the examined row is staged with `identity_check.alternatives == []`.
  - `test_a_budget_cut_embed_is_the_budget_not_the_provider`: `absorb_budget` `"60"`, `_clock` patched as in the budget-refused test, and a `FakeEmbeddings` whose `vector_for` jumps the clock past the budget and then raises `EmbeddingsError("network", "embeddings deadline passed before the request")` (`vector_for` runs inside `embed`, so the raise is the provider double's own). Identity is `degraded` with `budget_exhausted True` and the step-2b budget reason, and `store.errors.summary(...)` holds no `continuity-identity` row.
  - `test_close_candidate_mapped_to_existing_rewrites_the_row`: given `LEDGER_THREAD` (open) and a proposed `RECOVER_THE_LEDGER`, and a resolver answering `existing find-the-ledger`:
    - one staged plot edit targets `find-the-ledger`;
    - `payload.status == "advanced"`;
    - `identity_check.decision == "existing"` and `status == "accepted"`;
    - an as-new alternative is present with `before == ""` and the model's title (Review Focus 6).
  - `test_one_batched_call_for_several_ambiguous_rows`: two ambiguous rows (a thread and a commitment) give exactly one identity request containing `Row r1` and `Row r2`.
  - `test_resolver_naming_an_unknown_id_marks_the_row_uncertain_and_low`.
  - `test_resolver_naming_a_closed_thread_never_reopens_it` (Review Focus 2). After saving the review via `PUT /chronicle`, `plot.get(cid, "find-the-ledger")["status"] == "closed"` and a new thread exists.
  - `test_two_rows_mapped_to_one_record_downgrade_the_second`.
  - `test_undecodable_resolver_reply_fails_the_phase_and_keeps_rows_with_hints` (§26): the reply is `"I cannot tell."`. The phase is `failed`, the row is staged new with `identity_check.status == "hint_only"`, alternatives are non-empty, and the absorb is 200.
  - `test_resolver_error_fails_only_the_phase`: `llm_fakes.from_entries([extraction_entry, {"when": {"system_contains": "You are checking whether newly proposed story records"}, "error": {"kind": "network", "message": "connection reset"}}])` — the cassette's own `error` entry, no subclass. Absorb is 200, and identity is `failed` with `attempted True`.
  - `test_budget_refused_identity_reports_budget_exhausted`: a `from_entries` fake and no new fake. Set `absorb_budget` to `"60"`; `clock = [0.0]; monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])`; wrap the store module's `examine` (imported in the test as `from grimoire.store.continuity import identity`; the route looks it up at call time) so it jumps the clock before returning: `real = identity.examine; monkeypatch.setattr(identity, "examine", lambda *a, **k: (clock.__setitem__(0, 1e6), real(*a, **k))[1])`. `examine` runs before `budget.run`, so the call is refused. Identity is `failed`, `budget_exhausted True`, `attempted False`, and rows are `hint_only` (they were examined).
  - `test_partially_answered_batch_is_degraded`: two examined rows, and the resolver answers only `r1`. Identity is `degraded` with reason `"the duplicate check left some rows unanswered"`; `r2` is `unchecked` / `hint_only` at band `low`.
  - `test_decodable_reply_answering_no_row_is_degraded`: the resolver replies `{"decisions": []}`. Identity is `degraded` with reason `"the duplicate check answered none of the rows"`, and every examined row is `unchecked` / `hint_only`.
  - `test_embedding_failure_degrades_the_phase_and_keeps_lexical_candidates` (Review Focus 4): embeddings are configured and `similarity._CLIENT` is `FakeEmbeddings(error=EmbeddingsError("network", ...))`. The phase is `degraded`, `body["identity"]["matching"] == "semantic"`, the lexical candidate is present, the resolver is still called, and absorb is 200. The error store holds one row with `task == "continuity-identity"` and `kind == "network"`, and none of its field values contains the proposed title.
  - `test_identity_embedding_deadline_never_exceeds_the_absorb_budget`: `client.put("/api/config", json={"absorb_budget": "5"})` (smaller than `embeddings.TIMEOUT`); every deadline `FakeEmbeddings` recorded is `<= time.monotonic() + 5`. This pins the `min(..., absorb budget deadline)` half of §9.4 end to end, which a `deadline(None)` call would fail. Because `FakeEmbeddings` was called here, the same test also pins §9.4's “embedding calls stay unmetered”: the scene's rows in `store.usage.calls(campaign=cid)` number exactly `len(fake.requests)`, and no row's `task` or `model` names the embeddings task or `embeddings_model`.
  - `test_misrouted_identity_reports_itself_and_leaves_absorb_standing`: the continuity route points at a keyless connection. Identity is `failed` with a reason mentioning a key, rows are `hint_only`, and absorb is 200.
  - `test_alternatives_pass_check_conflicts_at_save`: saving the review with an alternative swapped in for the row at its index (alternatives stripped) answers 200, and the alternative's target gains the beat.
  - `test_a_save_body_still_carrying_alternatives_is_accepted`: the server tolerates an un-stripped body, and the fingerprint is stable across two identical PUTs (replay returns the first result).
  - `test_identity_log_row_carries_counts_only`: read the month's log rows. The `continuity identity check` row has integer counts, and no field value contains the proposed title or beat.
  - `test_identity_meter_files_under_its_own_task`: the usage ledger has a row with `task == "continuity-identity"` when the resolver ran.

  Other test files:
  - `tests/test_routing_routes.py`: add `_drive_continuity` and the `"continuity"` key in `DRIVERS`. The driver:
    1. seeds `plot.set_movement(cid, *LEDGER_THREAD[:2], "open", LEDGER_THREAD[2], sid)` — in `sid` itself, so the structural clause also applies, though `RECOVER_THE_LEDGER` clears the lexical floors on its own;
    2. sets `fake = client.app.dependency_overrides[routes.get_llm]()` with `fake.turns = [[review_runs.EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER]]`;
    3. runs `review_runs.absorb(client, cid, sid)`;
    4. sets `fake.requests[:] = review_runs.identity_requests(fake)`;
    5. asserts the list is non-empty before the harness's connection assertion runs.

    The resolver receives the extraction JSON again. That is decodable, with no decisions, so rows are `unchecked` and the phase `degraded`; the assertion is only about which connection was used.
  - `tests/test_routes.py::test_absorb_reports_every_phase_attempted`: assert names `== ["extraction", "identity", "dossiers", "voice", "audit"]`. Assert `attempted` and `status == "ok"` for every row except `identity`, and for `identity` assert `status == "skipped"` and `attempted is False`. The docstring gains one sentence on why identity is skipped there: ABSORB_JSON proposes no thread or commitment.
  - `tests/test_routes.py::test_absorb_phases_mirror_the_dossier_and_mechanics_blocks` (the second edited pinned test): its tuple gains `("identity", "identity")`, so `status`, `reason`, `attempted` and `budget_exhausted` are compared between `body["phases"]` and `body["identity"]` as they are for the other two projections.
  - `tests/test_pending_reviews_store.py::test_a_phase_row_carries_exactly_what_the_phase_report_puts_in_one`: call `_phase_report(block, block, block, block)` and expect the five names.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_identity.py tests/test_routing_routes.py tests/test_pending_reviews_store.py tests/test_routes.py::test_absorb_reports_every_phase_attempted tests/test_routes.py::test_absorb_phases_mirror_the_dossier_and_mechanics_blocks -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the identity and routing tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_identity.py tests/test_routing_routes.py tests/test_routing_guard.py tests/test_routing.py tests/test_usage_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q` (the guards cover the new module-scope imports in `routes/scenes.py`). Expected: PASS. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS (no new `BLE001` or `C901` finding in `routes/scenes.py`).
- [ ] **Step 5: Audit for call-count stability.** Scripted fakes answer by call order, so an extra identity call shifts every later reply in an absorb test whether or not it asserts a count. Select the audit set by what drives absorb, not by assertion shape. From `backend/`:
  1. `grep -ln 'review_runs.absorb\|/absorb"' tests/*.py` — today: `test_frozen_campaign`, `test_observability_routes`, `test_retcon_routes`, `test_review_detach`, `test_routes`, `test_routing_routes`, `test_scene_import_routes`, `test_tracker_absorb`, `test_usage_routes` (re-run the grep; do not trust this list).
  2. For each hit whose extraction reply carries `plot_movements` or `commitment_movements`, confirm every proposed row is either not proposed-new (explicit stored id) or has no plausible seeded same-type record (no shared title words, not seeded in the absorbed scene). The `"The Tea"` tests in `test_routes.py` seed no thread; the frozen campaign's cassette moves `the-debt` and `salt-owed` by existing id.
  3. Second pass over the same files for the ways the extra call can be observed: `grep -n 'fake\.requests\|fake\.calls\|\.calls ==\|len(.*requests)\|\.messages\b\|\.conn\b\|requests\[-1\]\|\.systems\|usage\.calls\|errors\.summary'` (the last request becomes the identity call once it is chained after extraction; the usage ledger gains a `continuity-identity` task row; a `CassetteMiss` inside identity is recorded as an error). Include `ClockEatingFake` and `_PHASE_ORDER` in `test_routes.py` (an identity request would get the last reply in its list) and the clock-advancing fake in `test_usage_routes.py`, which asserts the set of filed tasks.

  Record the audit in the commit body as a list of files, not prose.
- [ ] **Step 6: Run the absorb and route suites.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py tests/test_review_detach.py tests/test_tracker_absorb.py tests/test_frozen_campaign.py tests/test_pending_reviews_store.py tests/test_usage_routes.py tests/test_observability_routes.py tests/test_scene_import_routes.py tests/test_retcon_routes.py -q`. Expected: PASS, with only the two pinned `test_routes.py` tests edited.
- [ ] **Step 7: Commit:** `feat(absorb): batched identity check chained onto the extraction`

---

### Task 12: Eval case `continuity-identity` (§28.10 cases 1–4, identity half)

**Files:**
- Modify: `evals/graders.py`, `evals/cases.py`, `evals/README.md`
- Create: `evals/recordings/continuity-identity.compliant.json`, `continuity-identity.undecodable.json`, `continuity-identity.merged.json`, `continuity-identity.unknown-id.json`
- Test: `tests/test_eval_graders.py`; `tests/test_evals.py` (unchanged, enforces recordings)

**Interfaces:**
- `graders.IDENTITY_DECISIONS = identity.DECISIONS`.
- `graders.grade_identity(text: str, expected: dict[str, dict], offered: dict[str, set[str]]) -> list[Check]`.
  - `expected` maps `row key → {"decision": str, "id": str}`, and `offered` maps `row key → candidate ids`.
  - The checks are:
    - `identity.json`, which short-circuits the rest when it fails;
    - `identity.shape`: `decisions` is a list of objects with a str `row`;
    - `identity.enum`: every raw decision is in `DECISIONS`;
    - `identity.known_ids`: every `existing` names an id in `offered[row]`;
    - `identity.covers_rows`: every expected row has a decision;
    - `identity.same_obligation`: case 1, row `r1` is `existing` with the expected id;
    - `identity.distinct`: case 2, row `r2` is `new`;
    - `identity.continuation`: case 3, row `r3` is `new`.
  - Scoring is on the RAW `extract_object` result, as `grade_absorb` does.
- `cases.build_continuity_identity() -> dict`. Every seeded beat and every parsed row's title and beat is written verbatim in the case (the implementer writes them once; the plan fixes their roles). It creates a campaign with, all in the absorbed scene `sid` with Seraphine, Winifred and Mara present, so the structural clause is in play:
  - an open thread `find-the-ledger` "Find the ledger" with the same beat as `tests/review_runs.LEDGER_THREAD` (spelled out in the case: `evals` does not import `tests`);
  - an open thread `the-saltmarch-smuggling` "Who runs the Saltmarch smuggling";
  - a broad thread `seraphines-debts` "Seraphine's debts";
  - and, in a separate scene `s0` whose cast shares no one with `sid`, a commitment `the-midnight-deadline` whose title and beat share no content word with the commitment row below.

  It returns `ctx` with `cid`, `sid` and a synthetic `parsed`:
  - `r1` with `RECOVER_THE_LEDGER`'s title and beat (spelled out): the same obligation, reworded — expected to pass by the lexical floors;
  - `r2` "Who bribes the Saltmarch harbourmaster": the same topic, a distinct question — expected to pass by the structural clause;
  - `r3` "Seraphine's debt to Mara comes due": a concrete continuation of the broad thread — expected to pass by the structural clause;
  - a commitment "Pay Mara for finding the ledger": case 4, whose only lexically close records are threads.

  `build` runs `examine` once and asserts the exact offered sets, written as literals once the implementer has computed them (at minimum `find-the-ledger ∈ offered["r1"]`, `the-saltmarch-smuggling ∈ offered["r2"]`, `seraphines-debts ∈ offered["r3"]`), and that the commitment row is NOT examined. So a floor change fails loudly here rather than as a vacuous eval, and the recordings can be authored against known offers.
- `cases._identity_prompt(ctx)`: `exam = identity.examine(cid, sid, parsed, chronicle.scene_facts(cid, sid), embed_deadline=None)`, stores `ctx["exam"]`, `ctx["offered"]` and `ctx["expected"]`, and returns `identity.build_prompt(exam.prompt_rows())`. It uses the production builder.
- `cases.grade_continuity_identity(ctx, output)` is the sum of:
  - `graders.grade_prompt(ctx["messages"], …)` with the enum needles `{f"asks_{d}": f'"{d}"' for d in identity.DECISIONS}` **plus** one needle per decision instruction, each the unique phrase Task 6 fixes in `system.j2` (§28.10 “render the enum and decision instructions verbatim”): `asks_existing_rule` (`only when a listed candidate is the same narrative question or obligation`), `asks_closed_is_not_existing` (`A closed or resolved candidate is never "existing"`), `asks_continuation_is_new` (`a continuation, or a related subplot`), `asks_uncertain_rule` (`when the transcript cannot tell`), `asks_signals_are_hints` (`are hints, not proof`). The quoted enum words alone also appear in the reply-shape line and would survive deleting every rule;
  - one `Check("prompt.same_type_only", ok, detail)` over `ctx["exam"]`, not the prompt text: `ok = all(s.kind == row.kind for row in exam.rows for s, _ in row.candidates)` — every candidate of every examined row has the row's own type. That is the property case 4 claims, and it holds whether or not a floor change makes the commitment examined against a same-type record;
  - `grade_identity(...)`.

  Case 4's identity half is structural, since a commitment never receives a thread candidate. The relation half (`pays_off` / `related_to`) is Slice D's reconcile case.
- Recordings: `compliant` answers r1 `existing find-the-ledger`, r2 `new`, r3 `new`. `merged` maps r2 to `the-saltmarch-smuggling` and r3 to `seraphines-debts` (both offered, so `identity.known_ids` still passes and the declared failures are exactly `identity.distinct` and `identity.continuation`). `unknown-id` maps r1 to `existing maras-map`, an id offered nowhere. `undecodable` is prose.
- `CASES` entry, with every field the frozen `Case` dataclass requires (`evals/cases.py`: `id, hypothesis, build, prompt, grade, recordings`, no defaults):

  ```
  Case(id="continuity-identity",
       hypothesis="the identity resolver maps a reworded duplicate to the existing "
                  "record and keeps a same-topic question and a concrete continuation new",
       build=build_continuity_identity,
       prompt=_identity_prompt,
       grade=grade_continuity_identity,
       recordings=(
           Recording(BASELINE, ext="json"),
           Recording("undecodable", ("identity.json",), "json"),
           Recording("merged", ("identity.distinct", "identity.continuation"), "json"),
           Recording("unknown-id", ("identity.known_ids", "identity.same_obligation"), "json")))
  ```

- [ ] **Step 1: Write the failing grader tests** in `tests/test_eval_graders.py`, one per failure mode in the `_absorb_json` / `failed()` style:
  - `test_identity_prose_fails_json_only`;
  - `test_identity_unknown_enum_fails_enum`;
  - `test_identity_unoffered_id_fails_known_ids`;
  - `test_identity_missing_row_fails_covers_rows`;
  - `test_identity_compliant_passes`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the grader, the case, and the four hand-authored recordings. Use names only from the placeholder set. Update `evals/README.md`:
  - the case table;
  - the “six pass/fail questions” count, which becomes seven;
  - the counterexample variant list.

  Any new command line spells both the `.venv/bin/python` and the `.venv\Scripts\python.exe` forms (`test_install_scripts`).
- [ ] **Step 4: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py tests/test_evals.py tests/test_install_scripts.py -q`. Expected: PASS. `test_no_orphan_recordings` and `test_every_case_has_a_recordable_baseline` must both hold. Then `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: PASS.
- [ ] **Step 5: Commit:** `test(evals): continuity-identity case with compliant and counterexample recordings`

---

### Task 13: Review panel — chip, swap, strip on save, and the conflict-index fix

**Files:**
- Modify:
  - `frontend/src/api/types.ts`;
  - `frontend/src/components/review/editRows.ts`;
  - `frontend/src/components/review/useSceneReview.ts`;
  - `frontend/src/components/review/AbsorbEditRow.tsx`;
  - `frontend/src/index.css` (`.absorb-identity-badge` and `.absorb-identity`, beside `.absorb-contradiction-badge`);
  - `frontend/src/testkit/campaignHarness.tsx` (`PHASES_NONE_CUT` gains `{name: "identity", status: "skipped", reason: null, attempted: false, budget_exhausted: false}`).
- Test: `frontend/src/components/review/SceneReview.test.tsx`, `frontend/src/components/review/editRows.test.ts`

**Interfaces:**
- Types:
  - `IdentityCandidate = {ref: string; title: string; status: string; latest_beat: string; signals: IdentitySignals}`, where `IdentitySignals` is typed to Task 3's dict;
  - `IdentityCheck = {decision: "existing"|"new"|"uncertain"|"unchecked"; status: "accepted"|"downgraded"|"hint_only"; reason: string; proposed: {title: string; why_new: string; distinguished_from: string[]}; candidates: IdentityCandidate[]; alternatives?: StagedEdit[]}`;
  - `StagedEdit.identity_check?: IdentityCheck`;
  - `AbsorbPhase["name"]` gains `"identity"`;
  - `SceneAbsorb.identity?: PhaseAttempt & {status: "ok"|"degraded"|"failed"|"skipped"; reason: string|null; matching: "basic"|"semantic"; counts: Record<string, number>}`.

  `identity_check` and `identity` are optional because reviews stored before this slice lack them.
- `editRows.ts`:
  - `PHASE_LABELS.identity = "the existing-record check"`. The `Record` type over the union makes a missing label a `tsc` error.
  - `IDENTITY_UNCERTAIN_HINT`: the Global Constraints copy, verbatim.
  - `wireEdit(row: EditRow) -> StagedEdit`: strips `approved` and `identity_check.alternatives`, and keeps `rejected` and `judged` exactly as today's body does, so the fingerprint input is unchanged for rows with no identity check.
  - `identityChip(e) -> string | null`:
    - `"Possible existing record"` whenever `identity_check` has `decision` in (`uncertain`, `unchecked`), or `status === "hint_only"` — **whether or not it has alternatives**. The rows with none are exactly the downgrades (the only candidate is closed, or another row already holds it), and they are the ones most in need of the chip;
    - `"Matched an existing record"` for accepted `existing`;
    - otherwise (`new` / `accepted`) `null` — the `.absorb-identity` block below still renders its candidates and alternatives (deviation 9).
  - `isCandidateAlternative(alt: StagedEdit, ic: IdentityCheck) -> boolean`: whether an alternative stages onto one of the row's candidates. Candidate refs are prefixed canonical refs (`thread:find-the-ledger`) while `target.id` is bare and `target.kind` is `plot` / `commitments`, so the comparison is spelled out: `` const ref = `${alt.target.kind === "plot" ? "thread" : "commitment"}:${alt.target.id}`; return ic.candidates.some((c) => c.ref === ref) ``. It also classifies the original row pushed back into the alternatives after a swap (an accepted retarget's original targets a candidate; an as-new original does not).
  - `IDENTITY_NO_ALTERNATIVE_HINT`: “The matching record is closed or already used by another row in this scene; this stays a new record unless you reject it.”
- `useSceneReview.ts`:
  - The save body is `editRows.filter((e) => !e.rejected).map(wireEdit)`.
  - Before sending, the save refuses locally when two non-rejected `plot` / `commitment` rows share `(kind, target.id)` — the same predicate the swap button's `disabled` uses. A swap can reach that state the button cannot see (reject row B targeting X, swap row A onto X, un-reject B), and the server would apply both: `check_conflicts` judges every row against the pre-write store, so both pass, and the later status wins. The refusal names the clashing rows and opens their drawer, like a conflict.
  - The conflict mapping uses `const batchIdx = editRows.flatMap((r, i) => (!r.rejected ? [i] : []))`.
  - The `conflictByRow` filter is `!editRows[row]?.rejected`. Keep stored already rejects, so its behaviour is unchanged.
  - `switchToAlternative(i: number, k: number)`. It is not named `use…`. It replaces row `i` wholesale with alternative `k`:
    - the reviewer's beat carries over: when the current row's `after` differs from the beat it was staged with, the swapped-in row takes that `after`. The `before`, target and payload stay the server-rendered ones, so no token is computed on the client;
    - `review` falls back to the original's;
    - `identity_check` keeps its candidates, and its alternatives become the others plus the original row, stripped of `approved`, `rejected`, `judged`, `resolve`, `resolve_from` and `identity_check`;
    - the row is `approved: true, rejected: false, judged: true`;
    - conflicts for row `i` are cleared.

    It is returned from the hook.
  - `sceneRenamed` applies the same `payload.scene` / `before` / `resolve_from` repoint to each `identity_check.alternatives[*]`.
- `AbsorbEditRow.tsx`:
  - The head chip `span.chip.absorb-identity-badge` sits after the contradiction badge, with `title={ic.reason || undefined}`.
  - Below the evidence block, an `.absorb-identity` block holds:
    - `ic.reason`, when non-empty, as visible `.field-hint` text (not only the chip's tooltip);
    - when `decision === "uncertain"`: the spec hint `IDENTITY_UNCERTAIN_HINT` if the row has at least one alternative, else `IDENTITY_NO_ALTERNATIVE_HINT` (there is nothing to switch to, so the spec sentence would point at an impossible action; deviation 9);
    - the candidates (title, status — so a closed one reads as closed — and latest beat);
    - `.form-actions` with one `button.subtle` per alternative:
      - `key={alt.id}`;
      - visible text `Use {alt.payload?.title || alt.label} instead` when `isCandidateAlternative(alt, ic)`, else `Keep as a new record` (deviation 9);
      - `aria-label` built from the same visible string: `` `Use ${title} instead of ${e.label}` `` or `` `Keep ${title} as a new record instead of ${e.label}` ``;
      - disabled when another non-rejected row has the same `kind` and `target.id`.
  - In `ReviewPanel`:
    - when `absorb.identity?.status === "failed"`, a `.mechanics-notice` reads “The existing-record check could not run; rows still list their possible matches.” if `absorb.edits.some((e) => e.identity_check)`, else “The existing-record check could not run.” (`examine` itself may have failed, leaving no row with a check);
    - when `absorb.identity?.matching === "basic"` and some edit carries `identity_check`, one `.field-hint` reads “Basic matching active — semantic matching not configured” (§3.3), so the reviewer knows where the possible-match lists came from.
- [ ] **Step 1: Write the failing tests**
  - `editRows.test.ts`:
    - `wireEdit strips approved and alternatives and keeps the rest`;
    - `a low uncertain row is not approved by default`;
    - `identityChip wording per decision`, including an uncertain row with zero alternatives (chip shown);
    - `isCandidateAlternative compares prefixed refs`: a `plot` alternative targeting `find-the-ledger` against a candidate `thread:find-the-ledger` is true; an as-new alternative is false.
  - `SceneReview.test.tsx`, with fixtures using “Mara's debt to the Saltmarch guild”, “The Saltmarch tithe”, “Find the ledger”:
    1. `an uncertain row arrives unticked in the low drawer wearing the chip, hint and a Use … instead button`. Its fixture uses real prefixed refs (`ref: "thread:find-the-ledger"`) and asserts the button by name `Use Find the ledger instead of …`.
    1b. `an uncertain row whose only candidate is closed shows the chip, the reason, the candidate's closed status and the no-alternative hint, and no Use … instead button`; and the same for the second of two rows mapped to one record.
    1c. `an accepted retarget wears "Matched an existing record" and "Keep as a new record" restores the model's title and id` (Review Focus 6).
    2. `Use … instead swaps the row in place and the save sends the alternative's id, target, before and payload in the same position`.
    3. `the save body never carries identity_check.alternatives, swapped or not`.
    4. `undo after a swap offers the original back and swapping back restores it`.
    5. `a swap clears that row's unanswered conflict`.
    6. `Use … instead is disabled when another row already targets that record`.
    7. `rows without identity_check, and reviews stored before this slice, show no chip`.
    8. `a budget-cut identity phase is listed as the existing-record check under Cut short`.
    9. `a conflict after an untouched low row lands on its own row` (Review Focus 5). A conflict on the untouched low row itself is shown, and its drawer opens.
    10. `renaming the scene repoints alternatives, then swapping and saving sends the new scene`.
    11. `a swap keeps the reviewer's edited beat`: edit the beat, swap, save; the PUT row has the alternative's id, target and `before` together with the edited `after`.
    12. `un-rejecting a row whose record a swapped row now targets blocks the save`.
    13. `a failed identity phase with no checked rows says only that the check could not run`.
    14. `basic matching is named when the possible-match lists came from basic matching`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/components/review/SceneReview.test.tsx src/components/review/editRows.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** `npx vitest run src/components/review/SceneReview.test.tsx src/components/review/editRows.test.ts`. Expected: PASS.
- [ ] **Step 5: Run the typecheck.** `npm run typecheck`. Expected: PASS.
- [ ] **Step 6: Run ESLint.** From the repo root: `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS. If a touched file's count dropped, run `make baseline PY=$PWD/backend/.venv/bin/python` and include the smaller file.
- [ ] **Step 7: Commit:** `feat(review): possible-existing-record chip, row swap, and batch-index conflict mapping`

---

### Task 14: Slice gate

- [ ] **Step 1:** From the repo root, run `make check-lint check-mypy check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with any new finding fixed in code.
- [ ] **Step 2:** Run `make check-py PY=$PWD/backend/.venv/bin/python` — the CI gate: the whole backend suite under `--cov-fail-under=$(COV_FLOOR)`, including `tests/test_evals*` (the replay) and `test_llm_fakes`.
  - Expected: PASS, apart from failures listed in the slice ledger by the pre-Task-1 baseline step. A coverage drop from the new modules fails here, not only in CI.
  - `test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and is environmental (it must appear in the baseline record to be excused).
- [ ] **Step 3:** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4:** Run `make check-web`. Expected: PASS, covering the typecheck plus every vitest file, including the touched ones.
- [ ] **Step 5:** Run `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS. (Steps 1–5 together are `make check PY=$PWD/backend/.venv/bin/python`, which may be run once instead.)
- [ ] **Step 6: Implementation → done.** Run `/codex:review` against the slice diff, or record an independent reviewer in its place when Codex is unavailable. Resolve the findings, then commit.
- [ ] **Step 7: Done → actually done.** Run `/codex:adversarial-review` against the slice diff **and** the spec. Ask specifically whether the diff implements spec Slice C (§9, §10, §7.2's absorb half, §9.5/AC18, §23, §24, §28.2–28.3, §28.10 cases 1–4, §29), and look for gaps, drift and quietly dropped requirements. Record a substitute reviewer if Codex is unavailable. Resolve the findings, then commit. Before calling the slice done, confirm the slice ledger's `## Slice D backlog` section (pre-Task-1 step 3) is present and still lists hand-offs (a)–(d), plus any new hand-off the reviews produced.

---

## Self-review notes

**Spec coverage (Slice C):**

| Spec section | Task |
|---|---|
| §9.1 identity text, clip, blank kind → promise, no ids | 3 |
| §9.2 title (NFKC + casefold), slug (ignoring `untitled`), token and 3-gram Jaccard (no autojunk SequenceMatcher), actors, scenes, anchors | 3 |
| §9.3 availability via `resolve()`, shared cache and space, top-3, loose floor, structural inclusion, no top-k padding | 3, 4, 5 |
| §9.4 threadpool, `IDENTITY_WARM_LIMIT`, deadline, width rule, failure mode, unmetered (absorb half) | 4, 5, 11 |
| §9.4 sweep half (`RECONCILE_WARM_LIMIT`, `warm_window`) | Slice D, which reuses `similarity.embed_missing` / `semantic` (per-chunk saves already built and tested here) |
| §9.5 / AC18 Settings disclosure | 10 (lands before 11) |
| §10.1 prompt contract and `IDENTITY_FIELDS`, eval needles | 1; unknown-id drop in 5 |
| §10.2 proposed-new, neighbours, one batched call, decisions, acceptance, rewrite, uncertain → `low` | 5, 6, 7, 9, 11 |
| §10.3 `identity_check`, server alternatives with valid `before`, client strip, chip and “Use <title> instead” | 7, 9, 13 |
| §10.4 placement, `_soft_connection`, `budget.run`, nested meter, phase block and row, failure matrix, call-count stability | 11 |
| §10.5 plot slug predicate | 2 |
| §7.2 absorb materialization redirect | 8 |
| §23 template family, verify_templates, README, `_rendered_prompts`, cassette | 6 |
| §24 undecodable → failed; empty → no proposals; unknown → uncertain or dropped | 6, 7, 11 |
| §25.1 at most one identity call, only when ambiguous | 11 |
| §26 identity and embedding rows | 4, 11 |
| §28.2 similarity tests | 2, 3, 4 |
| §28.3 absorb identity tests | 7, 9, 11 |
| §28.10 cases 1–4 (identity half) | 12 |
| §29 task, route, meter, logs (counts only, deterministic vs semantic split) | 7, 11 |
| §29 docs note: Debug-level capture still records resolver replies | 6 |
| §26 embedding failure logged | 11 |
| §3.3 `matching` on the identity block (one definition, `similarity.matching()`), and the “Basic matching active” wording | 4, 11, 13 |

**Deliberate deviations from the spec text:**

1. `identity` also imports `absorb.materializer`, `effective`, `involvement`, `canon`, `plot` and `commitments`, beyond §7.0's “similarity, prompts, absorb.parse”. Detection must use materialize's own allocator. The graph was checked acyclic.
2. An id-less row whose slug collision the §10.5 predicate honours (open record, same casefolded title) is a target, not proposed-new. Materialize would stage it onto that record regardless of any verdict.
3. Routing registers `("continuity-identity",)` only, with a narrower hint. `test_routing_guard`'s phantom check would fail `continuity-reconcile` until Slice D's call site exists, and Slice D appends it and restores §29's hint.
4. Lexical signals are computed over the identity text minus its type-label line. The line is constant across same-type pairs.
5. The identity block carries `matching` beside the five spec'd keys (§3.3).
6. Several statuses in one block:
   - a missing per-row decision is `unchecked` / `hint_only`;
   - rows with no candidate carry no `identity_check`;
   - a row without a decision keeps its routing band (only `uncertain` forces `low`), and so does an accepted `existing`: no band cap is added beyond the spec (Review Focus 6 says what keeps a wrong acceptance visible instead).
7. `apply` gains plot reallocation for a row staged as new whose id was taken before save. This is parity with commitments, and closes the same silent merge §10.5 closes at staging.
8. The Settings disclosure also names image descriptions. `store/context/art.py` already embeds them whenever `resolve()` is set, regardless of depth, so “Set the connection to Off to stop all embedding” is only true if the disclosure says so.
9. Review UI departures from §10.3's chip and button wording:
   - the chip has a second wording, “Matched an existing record”, for an accepted retarget, so the as-new escape hatch is discoverable. §30 avoids certainty wording; “matched” describes what was staged, not a truth claim;
   - an accepted `new` row shows no chip (the check found nothing to flag), but its block still lists candidates and “Use <title> instead” buttons;
   - the as-new alternative's button reads “Keep as a new record”, since it has no existing title to name;
   - an uncertain row with no alternative (its only candidate is closed, or already held by another row) shows the chip and a different hint, because the spec hint's “switch it to the existing record” would name an impossible action.
10. A pre-existing client bug is fixed here: the conflict index was mapped through approved rows rather than the non-rejected batch. Slice C makes untouched `low` rows routine.
11. Token Jaccard is computed after removing a small fixed set of English function words (`similarity.STOPWORDS`). §9.2 says “token Jaccard” without a tokenizer; without this, a shared `the` alone clears the weak floor and the structural clause makes nearly every same-cast pair plausible, which would trigger a resolver call on almost every scene against §10.4. Character 3-grams are unchanged and carry non-space-delimited scripts.
12. §7.2's motivating case — a review staged *before* a merge and saved after it — is not closed here. The materialize redirect covers reviews staged after a merge; `apply` stays the physical writer §7.3 classifies it as. Owner: the Slice D plan, which must refuse or re-stage a pending review whose plot/commitment targets became alias sources at merge-apply time. Added to the Slice D backlog in the slice ledger.
13. A slug collision on an open alias source whose canonical is closed or resolved is not redirected; it is allocated `slug-N` and examined. §7.2 says staging redirects; redirecting here would reopen the canonical with no identity check, against §10.2 and the purpose of §10.5. An explicit id naming such a source is still redirected, with the canonical's status in the label.

**Coordination notes:**

1. The absorb snapshot's switch to canonical ids (§7.3) is Slice B's. Slice C is correct with either form, and the Task 8 redirect covers the physical one.
2. Expect textual rebase conflicts with Slice B in `scripts/verify_templates.py` and `templates/README.md` only.
3. Slice D reuses, without changes, `similarity.pool`, `lexical`, `with_cosine`, `plausible`, `nearest`, `semantic` and `embed_missing`. D adds `RECONCILE_WARM_LIMIT`, the `warm_window` rotation seeded by `cid`, and its own floors and top-k.
4. The staged-before-merge review is deviation 12, owned by Slice D.
5. `matching` (§3.3) has one definition. Slice A's `routes/continuity._matching` computes it today; Slice B's plan makes `drivers.matching()` that definition and deletes `_matching`; Slice C adds `similarity.matching()` with B's exact contract (semantic iff `embed_space.resolve()` is non-None, basic on any exception). Whichever of B and C lands second makes its own function a one-line delegate to the one already landed, and points `routes/continuity` (and `_matching`, if it still exists) at that one, so exactly one function computes it — checking the import edge against `test_import_guard` before choosing the direction. `test_identity_matching_agrees_with_get_continuity` (Task 11) fails across the rebase if they drift.

**Type consistency:**
- `similarity.Subject`, `lexical`, `nearest` and `semantic` are defined in Tasks 3–4 and consumed in Task 5.
- `identity.Examination` is defined in Task 5, extended in Tasks 6–7, and consumed in Task 11.
- `identity.AS_NEW_KEY` (`"_identity_as_new"`) is written in Task 7 and popped in Task 9. The materializer spells the literal and says why in a comment: materializer cannot import identity.
- `identity_check` (Task 7, alternatives added in Task 9) matches `IdentityCheck` (Task 13).
- The phase block (Task 11) matches `SceneAbsorb.identity` (Task 13).
- `_phase_report`'s new fourth parameter, `identity_block` (Task 11), is updated in its only other caller, `test_pending_reviews_store` (Task 11).
- `materializer.assign_ids` (Task 2, `live` filled in Task 8) is consumed by `materialize` and by `identity.examine` (Task 5); `Examination.live` (Task 5) is consumed by `decide` (Task 7).
- `signals["via"]` (Task 3) feeds `counts()`'s `deterministic` / `semantic` (Task 7) and the log row (Task 11).
- `FakeEmbeddings` (Task 4, `tests/llm_fakes.py`) and the fixture texts in `tests/review_runs.py` (Task 5, plus Task 11's extraction body and `identity_requests`) are the only doubles and fixture spellings.

---

## Plan-gate review resolution

Every finding from the adversarial plan review (four lenses: guards, spec, absorb behaviour, privacy/UI, tests) was checked against the code and spec before it was resolved. All were confirmed and applied; none was rejected. Duplicates across lenses are resolved once and cross-referenced.

**Guards lens**

1. *(major)* Bare `usage` passed to `client.complete` fails `test_usage_guard` — confirmed (`_is_metered` accepts only an `ast.Attribute` named `usage`). Task 11: the extraction awaitable is built in `_absorb_work` with `m.usage` and handed to `_extract_and_identify(extraction, …)`; no `# usage-ok:` marker.
2. *(major)* New broad excepts trip BLE001; lint runs only at the end; C901 per file — confirmed (`routes/scenes.py` and `materializer.py` carry no BLE001 baseline; C901 counts 5 and 1). Global Constraints gain “Broad catches”, “Complexity” and “Per-task lint gate”; `similarity.pool` and `_live_canon` narrow their catches or carry the house-style `# noqa: BLE001 -- <reason>`; `_identify`'s catch carries one; every backend task now ends with `make check-lint check-mypy`, and `_identity_status`, `_candidate_alternatives` / `_as_new_alternative` and per-decision `decide` helpers keep functions under C901.
3. *(major)* `prompt.same_type_only` tests the wrong property — confirmed. Task 12 grades type isolation over `ctx["exam"]` (every candidate has its row's kind), seeds `the-midnight-deadline` in `s0` with a disjoint cast, and `build` asserts the commitment row is not examined.
4. *(minor)* Wrong relative import for prompts — confirmed (`grimoire.prompts`; no `store/prompts`). Decisions and Global Constraints say `from ... import prompts`; the `continuity/__init__.py` docstring list gains `similarity` and `identity`.
5. *(minor)* Duplicate `run_in_threadpool` import and `identity` shadowing — confirmed (scenes.py:25; parameter at :2234). Task 11 imports `identity as continuity_identity, similarity as continuity_similarity`, drops the duplicate import, and names the `_phase_report` parameter `identity_block`.
6. *(minor)* Inline LLM fakes and cross-module test helpers — confirmed (cassette `error` entries exist, llm_fakes.py:128-131). Task 11's resolver-error test uses a `from_entries` error entry; the budget test patches `_clock` and `examine`; shared helpers and texts live in `tests/review_runs.py`. Same as finding 43.
7. *(minor)* Slice gate skips the CI pytest gate; no base-commit record — confirmed (Makefile:131 `--cov-fail-under`). A pre-Task-1 step records `make check-py` / `make check-web` failures in the ledger; Task 14 Step 2 runs `make check-py`. Same as finding 52.
8. *(minor)* Red-phase claims wrong in Tasks 1 and 2 — confirmed. Task 1: three fail, one regression pin. Task 2: all fail except the honoured-collision pin. See also finding 50.
9. *(minor)* CASES snippet omits required fields — confirmed (frozen dataclass, no defaults). Task 12 writes the full entry. Same as finding 42.
10. *(minor)* Audit grep selects by assertion shape — confirmed (scripted turns are consumed by order). Task 11 Step 5 selects by what drives absorb, then checks proposed rows for plausible neighbours, then a widened observation grep. Same as finding 48.
11. *(minor)* Stale base commit — confirmed (Slice A tip is `8376f59`). Branching names “the current Slice A tip” and gives `8376f59` as of writing.

**Spec lens**

12. *(major)* Cached vectors loaded only for prefilter-kept neighbours — confirmed: semantic matching could never reach a lexically unrelated record. Task 4: `semantic(..., cached=…)` loads every pool text, embedding stays bounded to required plus `IDENTITY_WARM_LIMIT` warm misses; Task 5 passes the whole pool; the paraphrase test pre-seeds the neighbour's vector and gains a control and `test_an_uncached_lexically_unrelated_neighbour_is_not_embedded`; the docstring states Slice D's sweep warms the ledger. Same as finding 29.
13. *(minor)* Off-width vectors forgotten but never re-embedded — confirmed against §9.4 / §26. Task 4 step 7 re-embeds forgotten required and warm-selected texts in a second call under the same deadline and within the remaining warm limit (only after a width change, so the common case stays one call); the test asserts the fresh width. Same as finding 23.
14. *(minor)* No deterministic vs semantic split in the counts — confirmed (§29). Task 3 adds `admitted_by` / `signals["via"]`; Task 7's `counts()` emits `deterministic` and `semantic` summing to `candidates`.
15. *(minor)* §29 docs note dropped — confirmed. Task 6 adds the sentence to the `continuity_identity/` README section and to `docs/incoming-llm-capture.md`, and runs `test_docs_guard.py`. Same as finding 36.
16. *(minor)* `prompt.*` needles cover only the enum words — confirmed (§28.10). Task 6 fixes one unique phrase per decision instruction in `system.j2`; Task 12 pins each; Task 1 adds `asks_identity_contract`.
17. *(minor)* AC3 never deferred; “Basic matching active” not rendered; `matching` missing on the skip path — confirmed (AC3 is Todo, Slice D per §31). Out of scope names AC3 / §18.1 → Slice D; Task 13 renders the basic-matching line in `ReviewPanel`; Task 11 sets `matching` on every return path. Same as finding 40.
18. *(minor)* §7.2's staged-before-merge failure left as a coordination note — confirmed. Moved to Decisions and deviation 12 with Slice D named as owner and a ledger backlog entry; `apply` stays the physical writer (§7.3).
19. *(minor)* Two UI departures from §10.3 not listed — confirmed. Deviation 9 now lists the suppressed chip on accepted `new` (whose block still offers swaps), the “Keep as a new record” label and the no-alternative hint; the chip itself is no longer suppressed on uncertain rows without alternatives (finding 25).

**Absorb-behaviour lens**

20. *(major)* Honoured-collision test compares with the bare slug and an empty staged map — confirmed (`_free` walks `slug-N`; explicit ids reserve). Task 2 adds `materializer.assign_ids`, consumed by both `materialize` and `examine`; a row is proposed-new iff its assigned id is not a stored record. Task 5 adds `test_exact_title_on_a_suffixed_record_is_a_target` and `test_batch_reservation_matches_materialize`; Task 2 adds `test_assign_ids_matches_materialize`.
21. *(major)* Slug collision on an alias source reopens a closed canonical unexamined — confirmed (`live_canon` ignores the canonical's status; parse defaults plot status to `open`; `set_movement` overwrites it). Task 8 puts the redirect in `assign_ids`: a slug-derived redirect is honoured only when the canonical is live and its title matches, else allocation continues to `slug-N`; an explicit id is still redirected with `(closed)` in the label. Tests added in Task 8; deviation 13.
22. *(minor)* `existing` id not canonicalized — confirmed (§10.2). Task 7 strips a matching ref prefix and maps through `Examination.live` before the membership check; `test_existing_named_by_alias_source_or_ref_form_is_accepted`.
23. *(minor)* Width re-embed — see finding 13.
24. *(minor)* Accepted retarget not re-checked at staging — confirmed (`materialize` runs after `_gather_phases`). Task 9 adds `_recheck_accepted`, which swaps a no-longer-live target back to the as-new row at `uncertain` / `downgraded`, band `low`; `test_accepted_target_closed_before_staging_is_downgraded`.
25. *(minor)* A downgraded row with no alternative gets the switch hint but no chip — confirmed. Task 13 shows the chip on every uncertain / unchecked / hint-only row, shows the reason and candidate statuses, and uses a no-alternative hint there (deviation 9). Same as findings 34 and 19.
26. *(minor)* “Keep as a new record” compares a bare id with a prefixed ref — confirmed (`canon.KIND_OF_PREFIX`; materializer targets are bare). Task 13 adds `isCandidateAlternative` with the prefix spelled out and a unit test using real refs. A server-side stamp was considered and not taken: the client comparison also classifies the original row pushed back after a swap, with no extra key to strip. Same as finding 31.
27. *(minor)* Two rows can target one record after a swap — confirmed (`check_conflicts` judges against the pre-write store). Task 13's save refuses locally on a duplicate `(kind, target.id)` among non-rejected rows; test 12.
28. *(minor)* A reply that decides nothing reports `ok` — confirmed. Task 7 adds `all_unchecked()`; Task 11's `_identity_status` reports `degraded`; Task 6's parser normalizes `"Row r1"` / `"R1"`; `test_decodable_reply_answering_no_row_is_degraded` and `test_parse_accepts_row_labels_as_printed`.
29. *(minor)* Paraphrase test passes by chance — see finding 12.
30. *(minor)* As-new variant reuses a model-written id — confirmed (Ledger routes cannot address `/`). Task 9 keeps an explicit id only when slug-shaped; `test_as_new_variant_never_keeps_a_non_slug_id`.

**Privacy / UI lens**

31. *(major)* “Keep as a new record” comparison — see finding 26. The `aria-label` is now built from the visible string.
32. *(major)* Disclosure regex also matches the new copy; copy not pinned whole — confirmed. Task 10 exports `EMBEDDINGS_COPY` and asserts it as an exact `<p>`; the disclosure test is scoped to its own paragraph and asserts it differs from the copy.
33. *(minor)* Chip does not follow the unsaved draft — confirmed (ConfigView.tsx ~484: “the dot wins”). Rationale corrected; the chip tests set state through `api.getConfig` and add an `off` case. Reporting `embeddings_active` from `GET /config` was optional and is not taken.
34. *(minor)* Uncertain row with no live alternative — see finding 25.
35. *(minor)* Failed notice claims rows list matches when none carry a check — confirmed. Task 13's notice drops the second clause when no edit carries `identity_check`; test 13.
36. *(minor)* §29 docs note — see finding 15.
37. *(minor)* Disclosure omits library search — confirmed (`semsearch.search_semantic` embeds whenever `resolve()` is set). Task 10's disclosure names it and the test asserts it.
38. *(minor)* A swap discards the reviewer's edited beat — confirmed. `switchToAlternative` carries an edited `after`; test 11.
39. *(minor)* Embedding failures never reach the error store — confirmed (§26 “log failure”). Task 11 step 2b writes one constant-detail ERROR row via `store.errors.record`; the log row gains `embedding_error`; the test asserts the row and that it holds no title.
40. *(minor)* `matching` not rendered — see finding 17.

**Tests lens**

41. *(major)* Fixture texts not pinned to the floors; stopwords — confirmed by re-implementing the plan's normalization on synthetic text (no store data). Decisions add pinned fixture texts in `tests/review_runs.py`, seeding in a separate scene `s0`, and signal-first assertions; Task 3 adds `STOPWORDS` (deviation 11), rewrites the unrelated-records test with realistic sentences that share only function words, and adds `test_stopwords_never_count_as_shared_tokens`; Task 12 states which clause each eval row passes by.
42. *(major)* Eval case not authorable — confirmed. Full `Case` entry; `build` pins texts and offered sets; the ids each counterexample uses are stated; `same_type_only` is graded over `ctx["exam"]` (finding 3).
43. *(major)* Route tests need fakes `llm_fakes` lacks — see finding 6; the budget test is spelled out with `_clock` and a wrapped `examine`.
44. *(minor)* Embedding fake described inconsistently — confirmed. One `FakeEmbeddings` in `tests/llm_fakes.py` with an exact surface including `fail_after`, referenced from Tasks 4, 5 and 11.
45. *(minor)* Byte-identity test compares new code with itself — confirmed. Task 9 asserts literal dicts.
46. *(minor)* Plot reallocation has no undo test and writes `p["id"]` — confirmed (apply.py:370 vs the commitment branch). Task 2 writes `target["id"]`, moves `target` / `p` and drops the reversal on reallocation, adds `test_a_plot_thread_whose_id_was_reallocated_carries_no_reversal`, and runs `test_undo_store.py`.
47. *(minor)* Deadline tests do not pin the clamp or the budget — confirmed. Task 4 adds the clamp and lower-bound assertions; Task 11's test sets `absorb_budget` to 5 and is renamed `…_never_exceeds_the_absorb_budget`.
48. *(minor)* Audit misses files and observation paths — see finding 10; Step 6 now runs `test_usage_routes`, `test_observability_routes`, `test_scene_import_routes` and `test_retcon_routes`.
49. *(minor)* Review Focus cites Task 12 for Task 11 tests; no accepted-existing focus — confirmed. Citations fixed; Review Focus 6 added, with the decision not to cap the band stated in deviation 6.
50. *(minor)* RED claims in Tasks 1, 8, 11 — confirmed. Guards are labelled as passing before and after; Task 11 Step 2 adds `test_routes.py::test_absorb_reports_every_phase_attempted`.
51. *(minor)* Routing driver uses another module's helper and an undefined constant — confirmed. Both live in `tests/review_runs.py`, the extraction body is spelled, the seeding scene is stated, and the driver asserts a non-empty request list first.
52. *(minor)* Gate skips coverage; `check-eslint` omits `PY` — see finding 7; every `make check-eslint` / `make baseline` now passes `PY=$PWD/backend/.venv/bin/python`.

**Completeness lens** (second pass, against the Slice C scope list; each gap was checked against the plan text and the code before it was applied, and none was rejected)

53. *(minor)* The Slice D hand-offs had no durable record — confirmed (no step wrote the backlog the plan cited for deviation 12). Pre-Task-1 step 3 creates the ledger's `## Slice D backlog` with (a) deviation 12, (b) `continuity-reconcile` and §29's hint (deviation 3), (c) `RECONCILE_WARM_LIMIT` / `warm_window`, (d) AC3 / §18.1; Task 14 Step 7 checks the section.
54. *(minor)* `matching` would be computed in three places with different exception policies — confirmed (`routes/continuity._matching` today; Slice B's `drivers.matching()` returns `basic` on any exception; Slice C spelled the expression inline). Task 4 adds `similarity.matching()` with B's contract and a test of its exception policy; `examine` and `_identify` use it; coordination note 5 says the second lander delegates; Task 11 adds `test_identity_matching_agrees_with_get_continuity`.
55. *(minor)* A budget-cut embed was reported as a provider failure — confirmed (`EmbeddingsClient._fetch` raises `EmbeddingsError("network", "embeddings deadline passed before the request")`; the deadline may be the budget's; `BudgetRefused.llm_call_failed = False` and `_budget_overrun` draw this line for LLM calls). Task 11 step 2b writes no error row when `budget.spent()`, sets `budget_exhausted True` and uses a budget reason; `test_a_budget_cut_embed_is_the_budget_not_the_provider`.
56. *(minor)* No test that `why_new` / `distinguished_from` never reach the ledgers — confirmed. Task 11 adds `test_identity_fields_never_reach_the_ledgers` over the staged payloads and the stored records after `PUT /chronicle`, examined and unexamined rows both; labelled a regression pin, since `materialize` spells payloads field by field and `set_movement` writes named fields only.
57. *(minor)* “Embedding calls stay unmetered” was unpinned — confirmed. Task 11's budget-deadline test also asserts the scene's usage rows number exactly the fake's LLM requests and none names the embeddings task or model.
58. *(minor)* No backend test read `identity.matching` — confirmed. Task 11 asserts `basic` in the no-neighbour test and in a new step-1 skip test (`test_an_absorb_proposing_no_records_skips_identity_with_matching_set`), and `semantic` in the embedding-failure test.
59. *(minor)* The phase projection pin did not cover identity — confirmed (`test_absorb_phases_mirror_the_dossier_and_mechanics_blocks` checks dossiers and audit only). Task 11 extends its tuple with `("identity", "identity")`; Files, Step 1, Step 2 and Step 6 now name two edited pinned tests.
60. *(minor)* Two failure boundaries were open — confirmed (`_absorb_work` catches only `Abandoned` and `LLMError` around `materialize`). Task 11 places steps 9–10 inside the phase `try` with a fallback to `parsed` and status `failed`; Task 9 wraps `_recheck_accepted` and `_attach_alternatives`, records via `store.errors.record_exception`, and reports through a new optional `materialize(..., on_identity_error=...)` callback that `_absorb_work` uses to mark the identity block `failed`; tests at store level (Task 9) and route level (Task 11).
61. *(minor)* Per-task gates omitted dependents — confirmed (Tasks 8 and 9 rewrite staging that `test_absorb_routing`, `test_undo_store`, `test_ingest_scene`, `test_frozen_campaign`, `test_tracker_absorb` and `test_evals`' absorb replay exercise; Task 11 adds module-scope imports to `routes/scenes.py`). Task 8 Step 4 and Task 9 Step 4 run those six; Task 11 Step 4 runs `test_import_guard` and `test_lock_domain_guard`.
