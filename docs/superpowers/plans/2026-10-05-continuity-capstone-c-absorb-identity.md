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
- Slices E, F and G.

**Branching:** Work on a branch stacked on the Slice A tip, e.g. `git switch -c claude/continuity-capstone-slice-c 1f92936`, or on the tip of Slice A's review fixes if those have landed. Slice B is planned in parallel and nothing here waits on it; everything is rebased at landing. **Before Task 1,** run `git status`. If the tree holds edits that are not this slice's (Slice A follow-ups in `store/continuity/doc.py`, `review.py` and their tests have been seen uncommitted), stop and ask the owner. Never sweep them into a Slice C commit.

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
- **Imports inside `store/`:** at module scope, acyclic, binding submodules (`from ..absorb import parse as absorb_parse`, `from .. import embed_space, vectors`, `from ... import embeddings`), never names (`test_import_guard`). `store/continuity/__init__.py` stays import-free.
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
2. **A resolver that answers wrongly in a well-formed way.** It may say `existing` for a closed thread, for a resolved commitment, for an id another row already moves, or for an id it was never offered. Each is downgraded to `uncertain` and band `low`. Nothing is reopened, and the save still writes only the new row. Pinned in Task 7 (`test_existing_naming_a_closed_candidate_is_downgraded`, `test_second_row_mapping_to_the_same_record_is_downgraded`, `test_existing_naming_an_unoffered_id_is_uncertain`) and Task 12 (`test_resolver_naming_a_closed_thread_never_reopens_it`).
3. **A merged record named by the extraction.** Until Slice B switches the absorb snapshot, the snapshot is physical, so the model can name the hidden alias source by id or by an exactly matching title.
   - Staging redirects to the canonical and labels the row “→ merged into <title>”.
   - A source and its canonical in one batch dedupe to one edit.
   - Identity candidates never list a hidden source.

   Pinned in Task 8 (`test_materialize_redirects_an_alias_source_id`, `test_materialize_redirects_an_alias_source_slug`, `test_source_and_canonical_in_one_batch_stage_once`) and Task 5 (`test_candidates_never_include_a_hidden_alias_source`).
4. **An embeddings endpoint that is down, or that changed width under the same model id.**
   - Absorb lands.
   - The identity phase is `degraded`, with lexical candidates intact.
   - Off-width cached vectors are forgotten and treated as misses.
   - A chunk that failed keeps every earlier chunk saved.

   Pinned in Task 4 (`test_width_mismatch_forgets_off_width_vectors`, `test_partial_batch_failure_keeps_earlier_chunks`) and Task 12 (`test_embedding_failure_degrades_the_phase_and_keeps_lexical_candidates`).
5. **A save refused with `edit_conflicts` while an untouched uncertain row sits before the conflicted row.** Uncertain rows are exactly untouched `low` rows. Today the client maps conflict indices through *approved* rows while the batch is every *non-rejected* row, so the conflict lands on the wrong row or is dropped. Pinned in Task 13 (`a conflict after an untouched low row lands on its own row`).

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
| `tests/test_routes.py` | One pinned test updated |
| `tests/test_pending_reviews_store.py` | Updated |
| `tests/test_eval_graders.py` | Appended |
| `frontend/src/components/review/SceneReview.test.tsx`, `editRows.test.ts`, `frontend/src/routes/ConfigView.test.tsx` | Updated |

### Decisions taken here, with reasons

- **Module layout and import edges.**
  - `similarity` imports `effective`, `involvement`, `canon`, `embed_space`, `vectors`, `...embeddings` and `...llm_errors`.
  - `identity` imports `similarity`, `effective`, `canon`, `involvement`, `..prompts`, `..absorb.parse` (as `absorb_parse`), `..absorb.materializer` (as `absorb_materializer`), `..commitments` and `..plot`.
  - `absorb.materializer` gains `..continuity.effective`.

  These edges were checked against `tests/test_import_guard._collect()` on the Slice A tip:
  - neither `continuity.effective` nor `continuity.involvement` reaches any `store.absorb` module;
  - `store.absorb` today reaches only `continuity.doc`;
  - nothing in `absorb` will import `identity` or `similarity`.

  So the graph stays acyclic. `identity → absorb.materializer` goes beyond spec §7.0's “may import” column (deviation 1). It exists because proposed-new detection must ask the exact allocator materialize uses, or the two disagree about which slug collisions are honoured.
- **Where detection runs:** between `parse_output` and `materialize`, inside the extraction coroutine. `identity.examine` reads the store in a worker thread. `Examination.rewritten(parsed)` returns a new parsed dict. Examined rows are replaced by rewritten rows carrying `identity_check`, without alternatives. An accepted row also carries the private key `"_identity_as_new"`, a copy of the original row, which materialize pops to build the as-new alternative. `materialize` then stages as usual.
- **Exact-title honoured collisions are not proposed-new.** An id-less row whose slug collision the §10.5 predicate honours (open thread or unresolved commitment, same casefolded title) is the model naming a shown record by its exact title. Materialize will stage it onto that record whatever a resolver says, so paying a call to ask would be incoherent: a `new` verdict could not be honoured. Such a row is a non-proposed target, like an explicit id (deviation 2).
- **Alternatives are built by materialize, not by identity**, in a second pass after every primary row is staged. They go through the same `_plot_edit` / `_commitment_edit` helpers primary rows use, so labels, payloads and `before` tokens are byte-identical to what a primary row for that target would carry. Those tokens are `conflicts.plot_line` / `commitment_line` of the PHYSICAL target. The second pass is what makes “not targeted elsewhere in the batch” and the as-new id allocation see the whole batch.
- **A missing per-row decision** in a decodable reply becomes `unchecked` / `hint_only`, at the row's routing band. `uncertain` is reserved for a verdict, the model's or the acceptance guard's, and §24 says a decodable reply with nothing usable yields no proposals. The row still shows its candidates and alternatives.
- **Rows with no plausible candidate carry no `identity_check`.** There is nothing to show. It also keeps every existing exact-dict materialize assertion, and every review that proposes nothing close, unchanged.
- **Phase row placement:** `identity` sits second, right after `extraction`, in run order (it is chained onto it). When nothing was proposed, or nothing was close, it is `skipped` with `attempted: false`.
- **Constants** (`similarity.py`), all candidate-generation parameters, not truth thresholds. Each is justified by the identity-text layout and the cost model, and to be tuned against real prompts later.
  - `CONTINUITY_IDENTITY_BYTES = 2000`. The identity text is a label, a title, at most three one-sentence beats and a due phrase. 2000 bytes holds that even in a three-byte script, and sits far below `semantic.DOC_BYTES = 6000`, so an identity text is never the input a provider rejects.
  - `IDENTITY_TOP_K = 3` (spec).
  - `PREVIOUS_BEATS = 2` (spec §9.1).
  - `TOKEN_FLOOR = 0.25` and `CHAR_FLOOR = 0.35`. A loose floor:
    - on a short text the title is a large share of the tokens, so sharing a title phrase alone approaches it;
    - two records sharing only incidental words, or only a single long word, fall well below it;
    - a false positive costs at most one shared resolver call, and a false negative costs a duplicate record.
  - `COSINE_FLOOR = 0.5`. Loose by design. §9.3 says the recall threshold is not the duplicate threshold, and models differ in scale.
  - `WEAK_TOKEN = 0.1`, `WEAK_CHAR = 0.15`, `WEAK_COSINE = 0.35`. The floor a structural signal must be paired with. Shared actors alone are a weak signal in a small cast, where every record shares someone. These floors are also the lexical pre-filter that picks which uncached neighbours to warm.
  - `IDENTITY_WARM_LIMIT = 32`. The identity step sits on the extraction's critical path, so the common case must be one embedding round trip. With up to `embeddings.BATCH − 32` proposed rows, the proposals plus the warm run fit one batch.
  - `REASON_CHARS = 280`. A resolver reason is display text. Clipping it keeps a runaway reply out of the stored review.
- **How embeddings are faked:**
  - `similarity` owns `_CLIENT = embeddings.EmbeddingsClient()`. Tests monkeypatch it with a `FakeProvider` of the shape `test_context_semantic.py` uses: it records `calls` and `deadlines`, raises `error` when set, and maps text to a vector, with an `index` callable for per-text vectors.
  - Embeddings are configured by creating an `openai_compatible` connection and `config.write_config(embeddings_model=..., embeddings_connection_id=...)`.
  - The space is read from `embed_space.resolve()["space"]`, never written as a literal.
- **Routing deviation:** only `"continuity-identity"` is registered in Slice C. `test_routing_guard.py::test_every_registered_task_is_actually_named_by_a_call_site` fails a registered task no `routes/` literal names, and `continuity-reconcile` has no call site until Slice D. Slice D appends that task and widens the hint to §29's text (deviation 3).
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
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py -q`. Expected: the four new tests FAIL (`AttributeError: IDENTITY_FIELDS`, missing keys, missing prompt text).
- [ ] **Step 3: Implement.**
  - Add `IDENTITY_FIELDS` beside `CITATION_FIELDS`, with a comment: these are inside rows, so `evals` must ask for them explicitly.
  - Add `_identity` and apply it to both row builders.
  - Re-export it.
  - Add the paragraph.
  - In `evals/cases.py` `grade_absorb`, extend the needle source with `+ absorb_store.IDENTITY_FIELDS`. This yields `prompt.asks_why_new` and `prompt.asks_distinguished_from`.
  - In `verify_templates.py`'s absorb loop, assert `'"why_new"' in exp[0]["content"]`.
  - Add one sentence to the README's absorb section naming `absorb.parse.IDENTITY_FIELDS` and the instruction.
- [ ] **Step 4: Run the absorb store tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py -q`. Expected: PASS.
- [ ] **Step 5: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py tests/test_eval_graders.py -q`. Expected: PASS. The absorb recordings are graded against the new prompt; `absorb.compliant.json` needs no change.
- [ ] **Step 6: Run the template checks.** From the repo root: `make check-templates PY=$PWD/backend/.venv/bin/python`. Expected: all checks pass.
- [ ] **Step 7: Commit:** `feat(absorb): ask for and carry why_new / distinguished_from on new-record rows`

---

### Task 2: Plot slug collisions use the commitment predicate (§10.5)

**Files:**
- Modify: `store/absorb/materializer.py`, `store/absorb/apply.py` (plot branch)
- Test: `tests/test_absorb_store.py` (one existing test inverted, new tests appended)

**Interfaces:**
- Produces:
  - `materializer._new_record_id(stored: dict, staged: dict[str, str], slug: str, title: str, *, settled) -> str`. This is today's `_new_commitment_id` body with the resolved check made a parameter. `settled(record: dict) -> bool` is true for a closed thread or a resolved commitment.
  - `_new_commitment_id(owed, staged, slug, title)` keeps its name and signature (apply.py calls it) and delegates with `settled = status.lower() in commitments.RESOLVED`.
  - New `_new_thread_id(threads, staged, slug, title)` delegates with `settled = _text(status).lower() == "closed"`.
  - The plot block in `materialize` gains `staged_plot_titles: dict[str, str]` beside `seen_pids`:
    - an explicit id is reserved under `(title or stored title).strip().casefold()`, mirroring the commitment block's reservation;
    - an id-less row gets `pid = _new_thread_id(threads, staged_plot_titles, slugify(title), title)` and is reserved.
  - `apply._apply_one` plot branch: when `str(e.get("before", "")).strip() == ""`, the write id becomes `materializer._new_thread_id(plot.read(cid), {}, p["id"], p.get("title", ""))`. This is the commitment branch's parity, and drops the reversal snapshot the same way. Otherwise it is unchanged.
- [ ] **Step 1: Write the failing tests**
  - Replace `test_materialize_plot_new_title_colliding_existing_id_merges` with `test_materialize_plot_new_title_differing_from_colliding_thread_gets_a_suffix`. Stored `the-map` is titled "The map", and the row's id-less title is "The Map!". The row is staged as `plot:the-map-2` with `before == ""`, and the stored thread is untouched.
  - `test_materialize_plot_same_title_open_thread_is_honoured`: an id-less "The map" against stored open "The map" stages `plot:the-map`, with `before` starting `"open — "`.
  - `test_materialize_plot_same_title_closed_thread_is_not_reopened`: stored "The map" is `closed`, so the row is staged as `plot:the-map-2`.
  - `test_materialize_plot_untitled_titles_never_merge`: given a stored open thread `untitled` titled "海図" and an id-less row titled "灯台", the row goes to `untitled-2`. A second id-less "灯台" in the same batch dedupes onto it, so one edit results. (Review Focus 1.)
  - `test_materialize_plot_explicit_id_is_reserved_against_a_later_slug`: given `{"id": "the-map", "title": "The map"}` and then an id-less "The Map?", the second row gets `the-map-2`, not dropped by the seen check.
  - `test_apply_plot_new_row_whose_id_was_taken_is_reallocated`: stage a new "The map" (`before ""`). Then another write creates `the-map` titled "A different map". `apply_edits` with `resolve: "replace"` writes `the-map-2`, and `the-map`'s beats are unchanged.
  - Keep `test_materialize_plot_dedupes_same_pid` and the commitment `untitled-2` tests unmodified. They must still pass.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py -q`. Expected: the new tests FAIL; today's code merges.
- [ ] **Step 3: Implement** as specified. Update `_new_commitment_id`'s docstring to point at `_new_record_id`, and the plot block's comment (“existing thread (by id, or a new title that collides)”) to say a collision is honoured only under the shared predicate.
- [ ] **Step 4: Run the absorb tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_absorb_conflicts.py tests/test_absorb_routing.py -q`. Expected: PASS.
- [ ] **Step 5: Run the ingest tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ingest_scene.py tests/test_frozen_campaign.py -q`. Expected: PASS. Both stage plot rows with explicit ids only.
- [ ] **Step 6: Commit:** `fix(absorb): a plot slug collision merges only onto an open thread of the same title`

---

### Task 3: `continuity.similarity` — identity texts and deterministic signals

**Files:**
- Create: `store/continuity/similarity.py`
- Test: `tests/test_continuity_similarity.py` (new). Fixtures: `client` and `cid` copied from `tests/test_continuity_doc.py`; threads and commitments seeded through `plot.set_movement` / `commitments.set_movement`.

**Interfaces:**
- Produces (all pure or read-only):
  - Constants: `CONTINUITY_IDENTITY_BYTES = 2000`, `PREVIOUS_BEATS = 2`, `IDENTITY_TOP_K = 3`, `TOKEN_FLOOR = 0.25`, `CHAR_FLOOR = 0.35`, `COSINE_FLOOR = 0.5`, `WEAK_TOKEN = 0.1`, `WEAK_CHAR = 0.15`, `WEAK_COSINE = 0.35`. Each carries a structural justification comment and “tune against real prompts later”.
  - `thread_identity_text(rec: dict) -> str` and `commitment_identity_text(rec: dict) -> str`.
    - `rec` is an effective record (`effective.records` shape) or a pseudo-record `{title, beats: [{text}], kind?, due?}`.
    - Lines are joined with `"\n"` and blank lines skipped.
    - Thread lines: `"plot thread"`, title, the latest beat (`rec["latest_beat"]`, or the last beat's text), then up to `PREVIOUS_BEATS` earlier beat texts, newest first, skipping any equal to an already-included beat.
    - Commitment lines: `f"{kind or 'promise'} commitment"`, title, latest beat, `f"due {due}"` when `due` is non-blank, then up to two previous beats.
    - No ids appear anywhere. The result is clipped with `embed_space.clip(text, CONTINUITY_IDENTITY_BYTES)`.
  - `normalize(text: str) -> str`: `unicodedata.normalize("NFKC", text).casefold()`, every char where `not ch.isalnum()` becomes a space, then whitespace is collapsed and stripped.
  - `title_key(title) -> str` is `normalize(title)`, and `titles_equal(a, b) -> bool` is `title_key(a) == title_key(b) != ""`.
  - `slug_equal(a_title, b_title) -> bool`: `paths.slugify(a) == paths.slugify(b)`, and False when either is `"untitled"`.
  - `tokens(text) -> frozenset[str]`: `normalize(text).split()`.
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
    - `[]` when the ledger is unreadable (any exception from `effective.records`).
  - `lexical(a: Subject, b: Subject) -> dict` returns the signals:

    ```
    {"title_equal": bool, "slug_equal": bool, "tokens": float, "chars": float,
     "cosine": None, "actors": sorted list, "scenes": sorted list, "anchors": sorted list}
    ```

    Floats are rounded to 4 places. The lists are intersections.
  - `with_cosine(signals: dict, a: Subject, b: Subject, vecs: dict[str, list[float]]) -> dict` sets `cosine` to `round(vectors.dot(...), 4)` when both texts have equal-width unit vectors in `vecs`, and leaves it `None` otherwise.
  - `weak(signals) -> bool`: `tokens >= WEAK_TOKEN or chars >= WEAK_CHAR or (cosine is not None and cosine >= WEAK_COSINE)`.
  - `plausible(signals) -> bool` holds when any of these is true:
    - `title_equal`;
    - `slug_equal`;
    - `tokens >= TOKEN_FLOOR`;
    - `chars >= CHAR_FLOOR`;
    - `cosine >= COSINE_FLOOR`;
    - `(actors or scenes or anchors) and weak(signals)`.
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
  - `test_unrelated_records_are_not_plausible_even_with_shared_actors`: records about Mara's boat and Winifred's lantern, sharing actor `characters:mara` and no words, give `plausible == False`.
  - `test_structural_signal_needs_a_weak_lexical_partner`: the same pair with one shared title word is plausible via the structural clause.
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
- [ ] **Step 4: Run the similarity tests and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): identity texts and deterministic similarity signals`

---

### Task 4: `continuity.similarity` — embedding mechanics

**Files:**
- Modify: `store/continuity/similarity.py`, `main.py` (lifecycle docstring)
- Test: `tests/test_continuity_similarity.py`

**Interfaces:**
- Produces:
  - `IDENTITY_WARM_LIMIT = 32`.
  - `_CLIENT = embeddings.EmbeddingsClient()`.
  - `available() -> dict | None`: `embed_space.resolve()`, never `semantic.settings()`.
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
  - `semantic(required: list[str], warm: list[str], *, deadline: float | None, space: dict | None = None) -> Semantic`. It takes no `cid`, and is the only writer in the module. Flow:
    1. `space = space or available()`. If it is `None`, return mode `"off"` with empty vectors.
    2. `loaded = vectors.load(space["space"], required + warm)`.
    3. `to_embed = [t for t in required if t not in loaded] + [t for t in warm if t not in loaded][:IDENTITY_WARM_LIMIT]`.
    4. If `deadline is None`, nothing is embedded.
    5. Otherwise call `embed_missing`.
    6. Apply the width rule. The reference width comes from the vectors embedded in this run, or from the loaded vectors when nothing was embedded. Every loaded vector of another width gets `vectors.forget` and is dropped.
    7. `mode` is `"failure"` if `embed_missing` reported an error, else `"configured"`.
- `main.py`: the blind-spot paragraph names the `EmbeddingsClient` singletons in `store/semsearch`, `store/context/semantic`, `store/context/art` and `store/continuity/similarity`. Docstring only.
- [ ] **Step 1: Write the failing tests** with a `FakeProvider` (`calls`, `deadlines`, `error`, `vector_for(text)`). It is installed via `monkeypatch.setattr(similarity, "_CLIENT", fake)`, with embeddings configured as in `test_context_semantic.configure`.
  - `test_unconfigured_is_off_and_never_calls`: `semantic(["x"], [], deadline=...)` gives `mode == "off"` and `fake.calls == []`, even when `semantic_recall_depth` is `"0"` and the connection is blank.
  - `test_availability_ignores_recall_depth`: with a connection and model set and depth `"0"`, `available()` is not None and `semantic` embeds.
  - `test_required_texts_embed_and_cache`: the first run embeds; a second identical run makes no call, and the vectors come from the cache.
  - `test_warm_limit_is_honoured`: given 40 uncached warm texts, exactly `IDENTITY_WARM_LIMIT` are embedded, plus the required ones.
  - `test_width_mismatch_forgets_off_width_vectors` (Review Focus 4): a cached 3-wide vector for neighbour text `n`, against fresh 2-wide required vectors, is dropped from `.vectors`, and `vectors.load(space, [n]) == {}` afterwards.
  - `test_fully_cached_run_uses_the_most_common_loaded_width`: two 2-wide cached vectors and one 3-wide, with no embedding (`deadline=None`), keep the 2-wide pair and forget the 3-wide.
  - `test_partial_batch_failure_keeps_earlier_chunks` (Review Focus 4): with `monkeypatch.setattr(embeddings, "BATCH", 2)`, five texts, and a fake that raises `EmbeddingsError("network", ...)` on its second call:
    - mode is `"failure"`;
    - the first two texts are in `.vectors` and in the cache;
    - the rest are absent.
  - `test_embedding_failure_mode_on_oserror`: an `OSError` gives mode `"failure"`, with nothing raised.
  - `test_deadline_is_shared_and_bounded`: every recorded `deadline` is the same value, and is at most `time.monotonic() + embeddings.TIMEOUT` at assertion time.
  - `test_deadline_helper`: `deadline(None)` is within `TIMEOUT` of now; `deadline(0)` and `deadline(-1)` are `None`; `deadline(5)` is less than or equal to `time.monotonic() + 5`.
  - `test_no_deadline_means_cache_only`: `deadline=None` makes no call and still uses cached vectors, with mode `"configured"`.
  - `test_nearest_uses_cosine_when_both_vectors_present`: a pair with no shared words but cosine 0.9 is plausible.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, and update the `main.py` docstring.
- [ ] **Step 4: Run the similarity tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_similarity.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py -q`. Expected: PASS. If the lock-domain guard names `similarity`, a cid-taking function writes. Move the write into `semantic` rather than classifying the module.
- [ ] **Step 5: Commit:** `feat(continuity): shared embedding mechanics for continuity similarity`

---

### Task 5: `continuity.identity` — proposed-new detection and neighbours

**Files:**
- Create: `store/continuity/identity.py`
- Test: `tests/test_continuity_identity.py` (new; fixtures as in Task 3)

**Interfaces:**
- Consumes:
  - `similarity.pool`, `similarity.subject`, `similarity.nearest`, `similarity.semantic`, `similarity.weak`, `similarity.lexical`;
  - `effective.Ledgers`, `effective.live_canon`;
  - `involvement.scene_actors`;
  - `canon.actor_ref`;
  - `absorb_materializer._new_thread_id` / `_new_commitment_id`;
  - `paths.slugify`.
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
    - `embedded: int`;
    - `targets: set[tuple[str, str]]`, the `(kind, canonical id)` that non-proposed rows will stage onto.
  - `examine(cid: str, sid: str, parsed: dict, facts: dict, *, embed_deadline: float | None) -> Examination`, which runs in a worker thread.
    - **Proposed-new rule** (spec §10.2 plus deviation 2). Only rows materialize would stage are considered: a non-blank beat, and for commitments a readable `commitments.read`.
    - A row is proposed-new when it is either:
      - (a) id-less with an alphanumeric title, **unless** the §10.5 allocator honours its slug (`_new_*_id(stored, {}, slugify(title), title) == slugify(title)` and the slug names a stored dict record); or
      - (b) its id names no stored record of that type.
    - Every other row contributes `(kind, live.get(ref, ref))` to `targets`.
    - The proposed subject is `similarity.subject(kind, f"proposed:{key}", {title, beats: [{text: beat, scene: sid}], kind, due}, actors=…, scenes={sid})`. Its actors are `{canon.actor_ref(c) for c in facts["cast"]} ∪ involvement.scene_actors(cid).get(sid, set())`.
    - `distinguished_from` keeps the ids naming a stored record of the row's type, mapped through `live_canon` and deduped. Unknown ids are dropped.
    - Neighbours come from `similarity.pool(cid, kind)`.
    - When `similarity.available()` is set, warm texts are the pool texts that pass `weak(lexical(...))` against any proposed subject, ranked by their best `rank_key`. Then `semantic([proposed texts], warm, deadline=embed_deadline)` runs, and `nearest(..., vecs)`.
    - A row is examined iff `nearest` returns at least one candidate.
    - `matching` is `"semantic"` iff `available()` is non-None (§3.3), and `embedding` / `embedded` mirror the `Semantic` result.
- [ ] **Step 1: Write the failing tests** (`tests/test_continuity_identity.py`):
  - `test_no_rows_no_proposals`: `has_proposals({"plot_movements": [], "commitment_movements": []})` is false.
  - `test_idless_row_with_no_close_record_is_proposed_but_not_examined`: given stored "The Tea" and a row titled "The Saltmarch tithe", `proposed == 1` and `rows == []`.
  - `test_reworded_duplicate_is_examined_with_the_record_as_candidate`: given stored `find-the-ledger` "Find the ledger" and a row "Recover the harbour ledger" whose beat names the ledger, the row is examined and its first candidate ref is `thread:find-the-ledger`.
  - `test_exact_title_open_record_is_a_target_not_a_proposal` (deviation 2): an id-less "Find the ledger" row gives `proposed == 0` and `("thread", "find-the-ledger") in targets`.
  - `test_exact_title_closed_record_is_a_proposal_with_the_closed_neighbour`: the closed thread is offered as a candidate with `live False`.
  - `test_row_with_unknown_id_is_proposed_and_titled_by_title_or_id`: given `{"id": "maras-debt", "title": ""}`, `title == "maras-debt"`.
  - `test_explicit_alias_source_id_targets_the_canonical`: given an alias `thread:a → thread:b`, a row with id `a` yields a `targets` entry `("thread", "b")`.
  - `test_candidates_never_include_a_hidden_alias_source` (Review Focus 3): the source `a` is never a candidate ref, though the canonical `b` may be.
  - `test_distinguished_from_drops_unknown_and_canonicalizes`: `["nope", "a", "b"]` becomes `["b"]`.
  - `test_cross_type_never_a_candidate`: a commitment row titled exactly like a thread has no thread candidate.
  - `test_commitments_unreadable_examines_no_commitment_rows`.
  - `test_semantic_matching_finds_a_wordless_paraphrase`: with embeddings configured and the fake mapping both texts to near vectors, a row sharing no words with the record is examined, and `embedding == "configured"`.
  - `test_embedding_failure_keeps_lexical_candidates`: the fake raises. `embedding == "failure"`, and the lexical candidate is still found.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py -q`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `has_proposals`, `Examined`, `Examination` (fields only) and `examine`.
- [ ] **Step 4: Run the identity tests and the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): detect proposed-new absorb rows and their plausible neighbours`

---

### Task 6: The resolver prompt family and its parser

**Files:**
- Create: `templates/continuity_identity/system.j2`, `templates/continuity_identity/user.j2`
- Modify: `store/continuity/identity.py`, `scripts/verify_templates.py`, `templates/README.md`, `tests/test_llm_fakes.py` (`_rendered_prompts`), `tests/fixtures/llm/campaign_flow.json`
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
    - Each element that is a dict with a non-blank `str` `row` becomes `{"row", "decision", "id", "reason"}`. `decision` is lowercased; anything outside `DECISIONS` becomes `"uncertain"`. `id` is a stripped `str`, or `""`. `reason` is a stripped `str`, clipped to `REASON_CHARS`, or `""`.
    - On a duplicate `row` key, the first wins.
    - Nothing raises on bad JSON.
- System prompt (static):
  - It opens with the unique phrase **`You are checking whether newly proposed story records`**. It must not contain `You are absorbing a completed role-play scene` or `You are auditing a completed role-play scene`.
  - It explains:
    - one decision per row;
    - `"existing"` only when a listed candidate is the same narrative question or obligation, naming that candidate's `"id"`;
    - `"new"` for a different question, a continuation, or a related subplot;
    - `"uncertain"` when the transcript evidence cannot tell;
    - that a closed or resolved candidate is shown so the model knows it was settled, and a later development is not that record;
    - that `"distinguished_from"` and similarity signals are hints, not proof.
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
    - undecodable means the phase is `failed`.
- [ ] **Step 4: Run the identity and cassette tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py tests/test_llm_fakes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the template checks.** `make check-templates PY=$PWD/backend/.venv/bin/python` from the repo root. Expected: all checks pass, including the new ones.
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
    - **`existing`:**
      - If `id` is not one of the row's candidate ids: `uncertain` / `downgraded`, reason `"named a record that was not offered"`.
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
  - `counts() -> dict[str, int]`, with flat keys: `proposed`, `examined`, `candidates`, `semantic` (candidates whose cosine is not None), `embedded`, `existing`, `new`, `uncertain`, `unchecked`, `downgraded`, `hint_only`.
- [ ] **Step 1: Write the failing tests**
  - `test_accepted_existing_rewrites_plot_row_status_never_open`: with original statuses `"open"`, `"advanced"` and `"closed"`, the rewritten statuses are `advanced`, `advanced` and `closed`. In each case `id` is the canonical id, `title == ""`, and `AS_NEW_KEY` holds the original.
  - `test_accepted_existing_rewrites_commitment_fields`:
    - original `kind "threat"`, `status "open"`, no `due` gives `kind ""`, `status ""`, and no `due` key;
    - original `status "fulfilled"` with `due "midnight"` keeps both.
  - `test_existing_naming_an_unoffered_id_is_uncertain` (Review Focus 2).
  - `test_existing_naming_a_closed_candidate_is_downgraded` (Review Focus 2): `decision == "uncertain"`, `status == "downgraded"`, and the row id is unchanged.
  - `test_second_row_mapping_to_the_same_record_is_downgraded` (Review Focus 2): row r1 is accepted and row r2 is downgraded.
  - `test_existing_naming_a_record_an_explicit_row_already_moves_is_downgraded`: the target is in `targets` from a non-proposed row.
  - `test_missing_decision_is_unchecked_hint_only`.
  - `test_unknown_decision_word_arrives_as_uncertain_accepted`: the parsed `"maybe"` has become `uncertain`.
  - `test_hint_only_discards_decide`.
  - `test_citations_survive_rewrite`: `quote`, `speaker` and `certainty` are equal before and after.
  - `test_counts_are_flat_ints`: every value is an `int`, and the keys are exactly the list above.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the identity tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_identity.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): accept, downgrade and rewrite identity decisions`

---

### Task 8: Absorb staging follows merges (§7.2)

**Files:**
- Modify: `store/absorb/materializer.py` (import `from ..continuity import effective as continuity_effective`)
- Test: `tests/test_absorb_store.py`

**Interfaces:**
- Produces, inside `materialize`:
  - `live = _live_canon(cid)`: `continuity_effective.live_canon(cid)`, or `{}` on any exception. A malformed `continuity.json` reads as no aliases everywhere, so not redirecting hides nothing.
  - **Plot.** After the pid is chosen (explicit, or via `_new_thread_id`) and **before** the `seen_pids` check: if `f"thread:{pid}"` is in `live`, set `merged_from = (pid, source title)` and replace `pid` with the canonical id.
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
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the absorb tests and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_import_guard.py -q`. Expected: PASS.
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
    - when its `decision == "uncertain"`, sets `edit["review"] = {**edit["review"], "band": "low"}`.

    `routing.band`, the weights and `apply` are untouched.
  - A second pass after the commitment block, `_attach_alternatives(sid, out, rows_by_edit, threads, owed, staged_plot_titles, staged_titles)`:
    - Let `taken` be every `("plot", id)` / `("commitments", id)` an edit in `out` targets.
    - For each edit with `identity_check`, in order:
      - **(a)** One alternative per candidate whose `status` is live (`effective.is_live`) and whose target is neither in `taken` nor this edit's own target. It is built from the §10.2 existing-rewrite of the original row onto the candidate id. The rewrite is the same rules as `Examination.rewritten`, restated in materializer as `_onto_existing(kind, row, id)`, because materializer cannot import identity.
      - **(b)** When `decision == "existing"` and `status == "accepted"`, the as-new variant: the `AS_NEW_KEY` row staged at its original explicit id when that is free, or else at `_new_*_id(stored, staged_map, slugify(title), title)`. That id is reserved in `staged_map` so two as-new variants never share an id.
    - Every alternative gets `review = copy of the primary edit's review` and no `identity_check`.
  - `pending_reviews._follow_stamps`: for each edit, the same `payload.scene` / `before` / `resolve_from` repoint is applied to every dict in `edit.get("identity_check", {}).get("alternatives", [])`.
- [ ] **Step 1: Write the failing tests**
  - In `tests/test_absorb_store.py`, call `materialize` with hand-built rewritten parsed rows:
    - `test_uncertain_row_is_band_low_and_keeps_its_citation_score`: the `review.band` of an `identity_check.decision == "uncertain"` row is `"low"`; the `score` and `quote` are unchanged.
    - `test_row_without_identity_check_has_no_identity_key`: the edit dicts for plain rows are byte-identical to the pre-change output, captured by building the same row with no `identity_check`.
    - `test_alternatives_are_complete_rows_with_valid_before_tokens`:
      - for an uncertain new row with two live candidates, there are two alternatives;
      - each has `kind`, `target`, `label`, `payload.scene == sid` and `before == plot_line(stored)`;
      - `check_conflicts(cid, [alt]) == []` for each;
      - the private key is absent from every edit and payload.
    - `test_accepted_row_offers_the_as_new_variant`: an alternative has `before == ""`, a fresh id, and the model's original title.
    - `test_alternatives_skip_closed_and_batch_taken_candidates`.
    - `test_two_as_new_variants_never_share_an_id`.
    - `test_commitment_alternative_before_is_commitment_line`.
    - `test_alternatives_never_carry_identity_check`.
  - In `tests/test_pending_reviews_store.py`, `test_follow_stamps_repoints_alternatives`: an alternative's `payload.scene` and commitment `before` suffix move from old to new.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement**, extracting the helpers first in a behaviour-preserving step with the old tests still green, then adding the new behaviour.
- [ ] **Step 4: Run the absorb store, conflict and pending-review tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_store.py tests/test_absorb_conflicts.py tests/test_pending_reviews_store.py tests/test_retcon_store.py -q`. Expected: PASS.
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
  - `embeddingsOn(draft: {embeddings_connection_id: string; embeddings_model: string}, connections: LLMConnection[]) -> boolean`. It mirrors `embed_space.resolve()`, with a comment naming it: the id is set, the connection is found, `kind === "openai_compatible"`, `base_url` is non-empty, and `embeddings_model.trim()` is non-empty. It is a client mirror, so the chip follows the unsaved draft like every other chip.
  - `SECTIONS` entry `{ id: "semantic", …, label: "Embeddings" }`.
  - `valueOf("semantic")`:
    - `!embeddingsOn` gives `"off"`;
    - depth `"0"` or blank gives `"on · recall off"`;
    - otherwise `` `on · recall ${depth}` ``.
  - The intro paragraph:
    - keeps the recall explanation;
    - replaces “Leave the connection blank, or set entries to `0`, to turn it off.” with “Set recalled entries to `0` to turn recall off.”;
    - then adds a new `config-copy` paragraph with the §9.5 copy verbatim.
  - The NumField caption becomes `"0 = recall off"`.
  - The disclosure paragraph keeps “**This sends text to the endpoint above.**”. It names recent scene text and the world info being searched (recall), image descriptions (the art catalogue), and plot-thread and commitment summaries after each wrap-up (finding possible overlaps), as “a second place your campaign is read”, and keeps the local-endpoint advice.
- [ ] **Step 1: Write the failing tests**
  - In `embeddingsOn.test.ts`:
    - each missing condition gives false: a blank id, an unknown id, an `openrouter` kind, an empty `base_url`, a blank model;
    - all present gives true.
  - In `ConfigView.test.tsx`:
    - update `/^Semantic recall/` to `/^Embeddings/` in the three existing tests;
    - `the embeddings chip reports embedding separately from recall depth`: with a connection, a model and depth `"0"`, the row's accessible name matches `/^Embeddings.*on · recall off/`;
    - `the copy says recall depth does not control continuity embedding`: the text `Recall depth does not control this.` and `Set the connection to Off to stop all embedding.` are present;
    - `the disclosure names thread and commitment summaries`: it matches `/plot-thread and commitment summaries/`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/routes/ConfigView.test.tsx src/routes/embeddingsOn.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** `npx vitest run src/routes/ConfigView.test.tsx src/routes/embeddingsOn.test.ts`. Expected: PASS.
- [ ] **Step 5: Run the typecheck.** `npm run typecheck`. Expected: PASS.
- [ ] **Step 6: Run ESLint.** From the repo root: `make check-eslint`. Expected: PASS, with no baseline growth.
- [ ] **Step 7: Commit:** `feat(settings): Embeddings section discloses automatic continuity embedding`

---

### Task 11: The identity phase inside absorb

**Files:**
- Modify: `store/routing.py`, `routes/scenes.py`
- Test: `tests/test_absorb_identity.py` (new), `tests/test_routing_routes.py`, `tests/test_routes.py` (`test_absorb_reports_every_phase_attempted`), `tests/test_pending_reviews_store.py` (`test_a_phase_row_carries_exactly_what_the_phase_report_puts_in_one`)

**Interfaces:**
- Produces:
  - In `store/routing.py`, after the `dossier` route: `Route("continuity", "Continuity checks", "The duplicate check beside absorb: one call, only when a proposed new thread or commitment closely resembles an existing one.", ("continuity-identity",), True)`.
  - In `routes/scenes.py`, imports: `from starlette.concurrency import run_in_threadpool`, `from ..store.continuity import identity, similarity`.
  - `async def _identify(cid, sid, client, conn, why, parsed, prepared, budget) -> tuple[dict, dict]`. It never raises except `Abandoned` and cancellation. It returns `(parsed_for_materialize, block)`, where `block` is:

    ```
    {"status", "reason", "attempted", "budget_exhausted", "matching", "counts"}
    ```

    `matching` is an addition (§3.3).
    1. If `not identity.has_proposals(parsed)`, return `skipped` with reason `"no new records proposed"`, `attempted False`, and `counts {}`. There is no worker hop.
    2. `exam = await run_in_threadpool(identity.examine, cid, sid, parsed, prepared.facts, embed_deadline=similarity.deadline(budget.remaining()))`.
    3. If `not exam.rows`, the status is `degraded` (reason `"semantic matching unavailable — basic matching used"`) when `exam.embedding == "failure"`, else `skipped` (reason `"no close existing records"`).
    4. Else, if `conn is None`, call `exam.hint_only(why or "no connection")`. The status is `failed` with that reason.
    5. Else:

       ```
       with store.usage.meter("continuity-identity", campaign=cid, scene=sid) as m:
           reply = await budget.run(
               client.complete(identity.build_prompt(exam.prompt_rows()), conn, m.usage),
               lambda: block.__setitem__("attempted", True),
               on_timeout=_noting(client, conn, m.usage))
       ```

       Then `decisions = identity.parse_output(reply)`. If it is `None`, call `exam.hint_only("the duplicate check returned no readable answer")` with status `failed`. Otherwise call `exam.decide(decisions)`, with status `degraded` if `exam.embedding == "failure"` and `ok` otherwise.
    6. `except Abandoned: raise`.
    7. `except BudgetRefused`: `exam.hint_only(...)` if `exam`. The status is `failed`, `budget_exhausted True`, and the reason is `"the absorb time budget ran out before the duplicate check could run"`.
    8. `except Exception as exc`, which deliberately also covers `EmbeddingsError` (an `LLMError`) so it can never reach `_absorb_work`'s fatal `except LLMError`:
       - `store.errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)`;
       - `exam.hint_only(...)` if `exam`;
       - status `failed`, `budget_exhausted=_budget_overrun(exc)`, reason `f"duplicate check failed: {exc}"`.
    9. The result is `exam.rewritten(parsed) if exam else parsed`, and `counts` is `exam.counts() if exam else {}`.
    10. `store.logs.record("info", __name__, "continuity identity check", kind="continuity-identity", campaign=cid, scene=sid, status=…, matching=…, embedding=…, **counts)`. Scalars only.
  - `async def _extract_and_identify(cid, sid, client, conn, usage, prepared, budget, ident_conn, ident_why) -> tuple[dict, dict]`:
    - `text = await budget.run(client.complete(prepared.messages, conn, usage), on_timeout=_noting(client, conn, usage))`. It raises, and that stays fatal.
    - Then `return await _identify(cid, sid, client, ident_conn, ident_why, store.absorb.parse_output(text), prepared, budget)`.
  - In `_absorb_work`:
    - `ident_conn, ident_why = _soft_connection(lambda: _require_connection("continuity-identity", cid))` beside the other three;
    - the first `_gather_phases` argument becomes `_extract_and_identify(cid, sid, client, conn, m.usage, prepared, budget, ident_conn, ident_why)`;
    - the unpack becomes `extraction, dossier_result, voice_result, audit_result = results`;
    - the `isinstance(extraction, BaseException)` check stays;
    - `parsed, identity_block = extraction`, and the old `parse_output` line is deleted;
    - the review gains `"identity": identity_block`;
    - `"phases": _phase_report(dossiers, voice, mechanics, identity_block)`.
  - `_phase_report(dossiers, voice, mechanics, identity)` gives rows in order `extraction, identity, dossiers, voice, audit`, with identity projected through `PHASE_KEYS`. The docstring is updated: run order; identity is chained onto extraction and has no retry route.
- [ ] **Step 1: Write the failing tests.** `tests/test_absorb_identity.py` uses fixtures modelled on `test_routing_routes._seed`: a key on the active connection, a world "Realm", Mara, a campaign, and a scene "Saltmarch" with two posts. It also seeds threads and commitments through `plot.set_movement` / `commitments.set_movement`. The LLM is a `llm_fakes.from_entries` fake with two entries:
  - extraction: `system_contains: "You are absorbing a completed role-play scene"`;
  - identity: `system_contains: "You are checking whether newly proposed story records"`.

  `identity_requests(fake)` filters `fake.requests` by the identity phrase.

  Tests:
  - `test_a_new_record_with_no_close_neighbour_makes_no_identity_call` (§28.3): stored "Find the ledger", an extraction proposing "The Saltmarch tithe", zero identity requests, and an identity phase row of `skipped`.
  - `test_close_candidate_mapped_to_existing_rewrites_the_row`: given stored open "Find the ledger", a proposed "Recover the harbour ledger" with status `open`, and a resolver answering `existing find-the-ledger`:
    - one staged plot edit targets `find-the-ledger`;
    - `payload.status == "advanced"`;
    - `identity_check.decision == "existing"` and `status == "accepted"`;
    - an as-new alternative is present.
  - `test_one_batched_call_for_several_ambiguous_rows`: two ambiguous rows (a thread and a commitment) give exactly one identity request containing `Row r1` and `Row r2`.
  - `test_resolver_naming_an_unknown_id_marks_the_row_uncertain_and_low`.
  - `test_resolver_naming_a_closed_thread_never_reopens_it` (Review Focus 2). After saving the review via `PUT /chronicle`, `plot.get(cid, "find-the-ledger")["status"] == "closed"` and a new thread exists.
  - `test_two_rows_mapped_to_one_record_downgrade_the_second`.
  - `test_undecodable_resolver_reply_fails_the_phase_and_keeps_rows_with_hints` (§26): the reply is `"I cannot tell."`. The phase is `failed`, the row is staged new with `identity_check.status == "hint_only"`, alternatives are non-empty, and the absorb is 200.
  - `test_resolver_error_fails_only_the_phase`: the identity entry raises `LLMError("network", …)` via a fake subclass. Absorb is 200, and identity is `failed` with `attempted True`.
  - `test_budget_refused_identity_reports_budget_exhausted`: monkeypatch `routes.scenes._clock`, and use a fake whose extraction reply advances it past `absorb_budget`. Identity is `failed`, `budget_exhausted True`, `attempted False`, and rows are `hint_only`.
  - `test_embedding_failure_degrades_the_phase_and_keeps_lexical_candidates` (Review Focus 4): embeddings are configured and `similarity._CLIENT` raises `EmbeddingsError`. The phase is `degraded`, the lexical candidate is present, the resolver is still called, and absorb is 200.
  - `test_identity_embedding_deadline_never_exceeds_the_embed_timeout`: the fake provider records its `deadline`, which is at most `time.monotonic() + embeddings.TIMEOUT`.
  - `test_misrouted_identity_reports_itself_and_leaves_absorb_standing`: the continuity route points at a keyless connection. Identity is `failed` with a reason mentioning a key, rows are `hint_only`, and absorb is 200.
  - `test_alternatives_pass_check_conflicts_at_save`: saving the review with an alternative swapped in for the row at its index (alternatives stripped) answers 200, and the alternative's target gains the beat.
  - `test_a_save_body_still_carrying_alternatives_is_accepted`: the server tolerates an un-stripped body, and the fingerprint is stable across two identical PUTs (replay returns the first result).
  - `test_identity_log_row_carries_counts_only`: read the month's log rows. The `continuity identity check` row has integer counts, and no field value contains the proposed title or beat.
  - `test_identity_meter_files_under_its_own_task`: the usage ledger has a row with `task == "continuity-identity"` when the resolver ran.

  Other test files:
  - `tests/test_routing_routes.py`: add `_drive_continuity` and the `"continuity"` key in `DRIVERS`. The driver:
    1. seeds `plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open", "Winifred learned it exists.", sid)`;
    2. sets `fake = client.app.dependency_overrides[routes.get_llm]()` with `fake.turns = [[EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER]]`;
    3. runs `review_runs.absorb(client, cid, sid)`;
    4. sets `fake.requests[:] = identity_requests(fake)`;
    5. asserts the list is non-empty.

    The resolver receives the extraction JSON again. That is decodable, with no decisions, so rows are `unchecked`; the assertion is only about which connection was used.
  - `tests/test_routes.py::test_absorb_reports_every_phase_attempted`: assert names `== ["extraction", "identity", "dossiers", "voice", "audit"]`. Assert `attempted` and `status == "ok"` for every row except `identity`, and for `identity` assert `status == "skipped"` and `attempted is False`. The docstring gains one sentence on why identity is skipped there: ABSORB_JSON proposes no thread or commitment.
  - `tests/test_pending_reviews_store.py::test_a_phase_row_carries_exactly_what_the_phase_report_puts_in_one`: call `_phase_report(block, block, block, block)` and expect the five names.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_identity.py tests/test_routing_routes.py tests/test_pending_reviews_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the identity and routing tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_absorb_identity.py tests/test_routing_routes.py tests/test_routing_guard.py tests/test_routing.py tests/test_usage_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Audit for call-count stability.** From `backend/`, run `grep -rn "fake.requests\|fake.calls\|\.calls ==\|len(.*requests)" tests | grep -v test_absorb_identity`. For every hit that drives absorb, confirm one of two things: its extraction reply proposes no thread or commitment row, or it proposes only rows with no plausible stored neighbour. The latter is the case for the `"The Tea"` tests in `test_routes.py`, which seed no thread. Record the audit in the commit body as a list of files, not prose.

  The tests the audit must cover are:
  - in `test_routes.py`, the anchorless/ambiguous-NPC tests, the dossier, voice and audit count tests, `_OverlapRecorder`, `ClockEatingFake`, `_SlowPhaseFake`, the budget tests and the cancelled-dossier test;
  - `test_review_detach.py`, `test_tracker_absorb.py` and `test_frozen_campaign.py`.

  The frozen campaign's cassette moves `the-debt` and `salt-owed` by existing id, so it is not proposed-new.
- [ ] **Step 6: Run the absorb and route suites.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py tests/test_review_detach.py tests/test_tracker_absorb.py tests/test_frozen_campaign.py tests/test_pending_reviews_store.py -q`. Expected: PASS, with only the one pinned test edited.
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
- `cases.build_continuity_identity() -> dict`. It creates a campaign with:
  - an open thread `find-the-ledger` "Find the ledger", with beats about Winifred learning of the ledger;
  - an open thread `the-saltmarch-smuggling` "Who runs the Saltmarch smuggling";
  - a broad thread `seraphines-debts` "Seraphine's debts";
  - a commitment `the-midnight-deadline`.

  It returns `ctx` with `cid`, `sid` and a synthetic `parsed`:
  - `r1` "Recover the harbour ledger": the same obligation, reworded;
  - `r2` "Who bribes the Saltmarch harbourmaster": the same topic, a distinct question;
  - `r3` "Seraphine's debt to Mara comes due": a concrete continuation of the broad thread;
  - a commitment "Pay Mara for finding the ledger": case 4, whose only lexically close records are threads.

  `build` asserts every thread row is examined, so a floor change fails loudly here rather than as a vacuous eval.
- `cases._identity_prompt(ctx)`: `exam = identity.examine(cid, sid, parsed, chronicle.scene_facts(cid, sid), embed_deadline=None)`, stores `ctx["offered"]` and `ctx["expected"]`, and returns `identity.build_prompt(exam.prompt_rows())`. It uses the production builder.
- `cases.grade_continuity_identity(ctx, output)` is the sum of:
  - `graders.grade_prompt(ctx["messages"], {f"asks_{d}": f'"{d}"' for d in identity.DECISIONS})`;
  - one `Check("prompt.same_type_only", ok, detail)`, where `ok` means the prompt contains no `proposed commitment` row. The commitment's only lexically close records are threads, so with same-type pools it must not have been examined at all;
  - `grade_identity(...)`.

  Case 4's identity half is structural, since a commitment never receives a thread candidate. The relation half (`pays_off` / `related_to`) is Slice D's reconcile case.
- `CASES` entry:

  ```
  Case(id="continuity-identity", hypothesis="the identity resolver maps a reworded
  duplicate to the existing record and keeps a same-topic question and a concrete
  continuation new", recordings=(
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
- [ ] **Step 4: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py tests/test_evals.py tests/test_install_scripts.py -q`. Expected: PASS. `test_no_orphan_recordings` and `test_every_case_has_a_recordable_baseline` must both hold.
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
    - `"Possible existing record"` when `identity_check` has `decision` in (`uncertain`, `unchecked`), or `status === "hint_only"`, and at least one alternative;
    - `"Matched an existing record"` for accepted `existing`;
    - otherwise `null`.
- `useSceneReview.ts`:
  - The save body is `editRows.filter((e) => !e.rejected).map(wireEdit)`.
  - The conflict mapping uses `const batchIdx = editRows.flatMap((r, i) => (!r.rejected ? [i] : []))`.
  - The `conflictByRow` filter is `!editRows[row]?.rejected`. Keep stored already rejects, so its behaviour is unchanged.
  - `switchToAlternative(i: number, k: number)`. It is not named `use…`. It replaces row `i` wholesale with alternative `k`:
    - `review` falls back to the original's;
    - `identity_check` keeps its candidates, and its alternatives become the others plus the original row, stripped of `approved`, `rejected`, `judged`, `resolve`, `resolve_from` and `identity_check`;
    - the row is `approved: true, rejected: false, judged: true`;
    - conflicts for row `i` are cleared.

    It is returned from the hook.
  - `sceneRenamed` applies the same `payload.scene` / `before` / `resolve_from` repoint to each `identity_check.alternatives[*]`.
- `AbsorbEditRow.tsx`:
  - The head chip `span.chip.absorb-identity-badge` sits after the contradiction badge, with `title={ic.reason || undefined}`.
  - Below the evidence block, an `.absorb-identity` block holds:
    - the hint (`.field-hint`) when `decision === "uncertain"`;
    - the candidates (title, status, latest beat);
    - `.form-actions` with one `button.subtle` per alternative:
      - `key={alt.id}`;
      - text `Use {alt.payload?.title || alt.label} instead`, or `Keep as a new record` for an alternative whose `target.id` matches no candidate ref;
      - `aria-label={`Use ${title} instead of ${e.label}`}`;
      - disabled when another non-rejected row has the same `kind` and `target.id`.
  - When `absorb.identity?.status === "failed"`, a `.mechanics-notice` in `ReviewPanel` reads: “The existing-record check could not run; rows still list their possible matches.”
- [ ] **Step 1: Write the failing tests**
  - `editRows.test.ts`:
    - `wireEdit strips approved and alternatives and keeps the rest`;
    - `a low uncertain row is not approved by default`;
    - `identityChip wording per decision`.
  - `SceneReview.test.tsx`, with fixtures using “Mara's debt to the Saltmarch guild”, “The Saltmarch tithe”, “Find the ledger”:
    1. `an uncertain row arrives unticked in the low drawer wearing the chip, hint and a Use … instead button`.
    2. `Use … instead swaps the row in place and the save sends the alternative's id, target, before and payload in the same position`.
    3. `the save body never carries identity_check.alternatives, swapped or not`.
    4. `undo after a swap offers the original back and swapping back restores it`.
    5. `a swap clears that row's unanswered conflict`.
    6. `Use … instead is disabled when another row already targets that record`.
    7. `rows without identity_check, and reviews stored before this slice, show no chip`.
    8. `a budget-cut identity phase is listed as the existing-record check under Cut short`.
    9. `a conflict after an untouched low row lands on its own row` (Review Focus 5). A conflict on the untouched low row itself is shown, and its drawer opens.
    10. `renaming the scene repoints alternatives, then swapping and saving sends the new scene`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/components/review/SceneReview.test.tsx src/components/review/editRows.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** `npx vitest run src/components/review/SceneReview.test.tsx src/components/review/editRows.test.ts`. Expected: PASS.
- [ ] **Step 5: Run the typecheck.** `npm run typecheck`. Expected: PASS.
- [ ] **Step 6: Run ESLint.** From the repo root: `make check-eslint`. Expected: PASS. If a touched file's count dropped, run `make baseline` and include the smaller file.
- [ ] **Step 7: Commit:** `feat(review): possible-existing-record chip, row swap, and batch-index conflict mapping`

---

### Task 14: Slice gate

- [ ] **Step 1:** From the repo root, run `make check-lint check-mypy check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with any new finding fixed in code.
- [ ] **Step 2:** Run `PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend -q`. This includes `tests/test_evals*` (the replay) and `test_llm_fakes`.
  - Expected: PASS, apart from failures recorded on the base commit before this slice started.
  - `test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and is environmental.
- [ ] **Step 3:** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4:** Run `make check-web`. Expected: PASS, covering the typecheck plus every vitest file, including the touched ones.
- [ ] **Step 5:** Run `make check-eslint`. Expected: PASS.
- [ ] **Step 6: Implementation → done.** Run `/codex:review` against the slice diff, or record an independent reviewer in its place when Codex is unavailable. Resolve the findings, then commit.
- [ ] **Step 7: Done → actually done.** Run `/codex:adversarial-review` against the slice diff **and** the spec. Ask specifically whether the diff implements spec Slice C (§9, §10, §7.2's absorb half, §9.5/AC18, §23, §24, §28.2–28.3, §28.10 cases 1–4, §29), and look for gaps, drift and quietly dropped requirements. Record a substitute reviewer if Codex is unavailable. Resolve the findings, then commit.

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
| §29 task, route, meter, logs (counts only) | 11 |
| §3.3 `matching` on the identity block | 11 |

**Deliberate deviations from the spec text:**

1. `identity` also imports `absorb.materializer`, `effective`, `involvement`, `canon`, `plot` and `commitments`, beyond §7.0's “similarity, prompts, absorb.parse”. Detection must use materialize's own allocator. The graph was checked acyclic.
2. An id-less row whose slug collision the §10.5 predicate honours (open record, same casefolded title) is a target, not proposed-new. Materialize would stage it onto that record regardless of any verdict.
3. Routing registers `("continuity-identity",)` only, with a narrower hint. `test_routing_guard`'s phantom check would fail `continuity-reconcile` until Slice D's call site exists, and Slice D appends it and restores §29's hint.
4. Lexical signals are computed over the identity text minus its type-label line. The line is constant across same-type pairs.
5. The identity block carries `matching` beside the five spec'd keys (§3.3).
6. Several statuses in one block:
   - a missing per-row decision is `unchecked` / `hint_only`;
   - rows with no candidate carry no `identity_check`;
   - a row without a decision keeps its routing band (only `uncertain` forces `low`).
7. `apply` gains plot reallocation for a row staged as new whose id was taken before save. This is parity with commitments, and closes the same silent merge §10.5 closes at staging.
8. The Settings disclosure also names image descriptions. `store/context/art.py` already embeds them whenever `resolve()` is set, regardless of depth, so “Set the connection to Off to stop all embedding” is only true if the disclosure says so.
9. The review chip has a second wording, “Matched an existing record”, for an accepted retarget, so the as-new escape hatch is discoverable. §30 avoids certainty wording; “matched” describes what was staged, not a truth claim.
10. A pre-existing client bug is fixed here: the conflict index was mapped through approved rows rather than the non-rejected batch. Slice C makes untouched `low` rows routine.

**Coordination notes:**

1. The absorb snapshot's switch to canonical ids (§7.3) is Slice B's. Slice C is correct with either form, and the Task 8 redirect covers the physical one.
2. Expect textual rebase conflicts with Slice B in `scripts/verify_templates.py` and `templates/README.md` only.
3. Slice D reuses, without changes, `similarity.pool`, `lexical`, `with_cosine`, `plausible`, `nearest`, `semantic` and `embed_missing`. D adds `RECONCILE_WARM_LIMIT`, the `warm_window` rotation seeded by `cid`, and its own floors and top-k.
4. A review staged *before* a merge and saved after it still writes the physical source. A merge does not change plot.json, so `conflicts` cannot see it. This is recorded for Slice D's merge-apply path, which can refuse or re-stage open reviews.

**Type consistency:**
- `similarity.Subject`, `lexical`, `nearest` and `semantic` are defined in Tasks 3–4 and consumed in Task 5.
- `identity.Examination` is defined in Task 5, extended in Tasks 6–7, and consumed in Task 11.
- `identity.AS_NEW_KEY` (`"_identity_as_new"`) is written in Task 7 and popped in Task 9. The materializer spells the literal and says why in a comment: materializer cannot import identity.
- `identity_check` (Task 7, alternatives added in Task 9) matches `IdentityCheck` (Task 13).
- The phase block (Task 11) matches `SceneAbsorb.identity` (Task 13).
- `_phase_report`'s new fourth parameter (Task 11) is updated in its only other caller, `test_pending_reviews_store` (Task 11).
