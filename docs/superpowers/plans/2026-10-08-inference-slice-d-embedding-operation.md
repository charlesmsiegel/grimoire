# Inference slice D — the embedding operation: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every embedding call goes through one metered operation, `embed_sync` / `embed`, under a registered embed task. Its endpoint, model and space come from one reader of the Embedding role.

**Architecture:**

- **One reader.** `resolve.embedding(cfg)` resolves the global Embedding role through `cascade.role_selection("embedding", campaign={})` and returns a `ResolvedInference` carrying `space_id`. It is the Embedding role's own entry point; `resolve.resolve` refuses embed work.
- **Two views of that reader.** `embed_space.endpoint(cfg)` is the dict the four embedding callers hold. `embed_space.resolve(cfg)` keeps its exact four-key shape, because the frozen baselines observe it. `translate.embedding_role` stays, as a one-line wrapper over the new `translate.embedding_view`.
- **One door.** `store/inference/embed.py` holds the operation: `embed_sync(task, texts, *, space, client, ..., campaign="", scene="")` and its async twin `embed`.
  - It checks the task against `routing.EMBED_TASKS`.
  - It stamps a `usage.Meter` with `operation: "embed"`, under the caller's campaign when it has one.
  - It lets `EmbeddingsClient.embed(..., usage=)` fill in the prompt-token count and price.
  - A failure finishes the meter with a detail made only of the error kind and HTTP status.
  - It writes one Debug capture line that never carries text.
- **Unchanged.** The callers keep their own client, vector cache, deadlines and degradation. They change which function they call and hand over their campaign. The model-test embed probe stays on the provider it tests, and it is now metered the same way.
- **Where it lives.** `store/inference/embed.py`, not the top-level `grimoire/inference.py`: every caller is a store module, and the store never imports `llm.py` (#239). Slice F's `decide` stays in `grimoire/inference.py`; the two modules are disjoint (ruling 11).

**User-visible (§14: Small).** Every lore-recall, art-ranking, search and continuity embed now files a ledger row. On an endpoint that reports no price (a local or `openai_compatible` server), each turn with recall or art on adds an **unpriced** row, so Costs totals, and the campaign's rail tail, read "incomplete" until the user enters rates for that model. Slice E prices such a row (its ruling 8: an `embed` row with no completion count is priced with completion 0). *Amended after review:* D's rates already model it, because `usage._completion_count` reads an embed row's absent completion count as zero, so the Costs card never tells the user that no rate can price a row whose provider counted it. An OpenRouter embed reports `usage.cost`, which is real spend and now counts against the campaign's budget. A known `no` for `embed` turns the role off, and the Embedding card's `problem` (which slice C introduces) now names that reason too.

**Tech Stack:** Python 3.11+, FastAPI, httpx (`MockTransport` in tests), pytest. No frontend change: the Embedding card's `problem` field, and whatever renders it, are slice C's.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`.

- §14 row D is this slice. It also draws on §4.4 (Embedding is global only), §5.3, §5.4 (`space_id`), §6.4 step 4, §7.1, §7.3, §9.3, §9.4, §12 ("Embedding provider fails"), §14.1 and §15 "Embed".
- Task 0 folds the rulings at the end of this plan into the spec.
- Base: `claude/inference-slice-d` at `6a87cd4` (slice C, nearly finished, not merged). **Before Task 1, rebase onto slice C's final fix wave**, which adds `_embedding_card`'s `problem` field that Task 1 extends.
- **Not in D (they land in C):** `PUT /llm-connections/{id}` refusing a key or URL change on the Embedding role's provider without `confirm_embedding: true`, and model-facts writes answering 409 `not_migrated` at format 1. D plans neither.

**Gate record:** plan → implementation `/codex:adversarial-review` returned "Ready with changes" with no critical finding (`.superpowers/sdd/plan-reviews/slice-d-plan-review.md`). Every Important and Minor finding is answered in "Plan review rulings" at the end.

## Global Constraints

**Rule 3: behaviour-neutral.**

- For every frozen state, `embed_space.resolve()` and `embed_space.resolve(cfg)` return exactly what they return today.
- `backend/tests/fixtures/inference_baseline.json` and `inference_baseline_c.json` are **never regenerated**.
- `inference_baseline.py` and `inference_baseline_c.py` are not edited.
- `test_inference_equivalence.py` and `test_inference_equivalence_c.py` stay green **unmodified** after every task.
- The only behaviour changes allowed are:
  - the rows and the capture line this slice adds;
  - ruling 4 (a known `no` turns embedding off) and the reason it adds to C's card `problem`;
  - the error records ruling 6 makes visible.

**Rule 4: embeddings never mix spaces.** There is no fallback on any axis. A space is the string `f"{provider_id}\0{rev}\0{model}"`, unchanged. A vector is saved only under the space whose model, key and URL produced it.

**Rule 5: absent, never zero.** A token count or price that no provider reported stays out of the row (CLAUDE.md, Costs).

**The Embedding role is global only (§4.4); its rows are not.** No embed *resolution* reads a campaign: one space, one vector cache. A *row* carries the campaign where the caller has one, because spend is measured per campaign (ruling 5). Lore recall, art and continuity pass theirs; absorb's identity check also passes its scene; library search has none.

**Observability and privacy (CLAUDE.md; §9.4).**

- LLM failures are recorded at `usage.Meter.done`, nowhere else.
- An embed failure's recorded detail is the error kind and HTTP status only, never `str(exc)`: a provider's error body can echo the input, and a redirect's `Location` can carry a key.
- No new `logging` handler.
- Embedded text never reaches a log, capture or ledger row.

**The user's rules.**

- **Each task runs only the test files it names**, plus `make check-lint check-mypy PY=$PY` (and `make check-eslint PY=$PY` only if a task ever touches `frontend/`; none does).
- **Never `make check`, never the full suite.** CI runs it. Merging waits for CI green.
- **Never spend money.** No live LLM or embedding calls; every provider in a test is a `MockTransport` or a `llm_fakes` double. `evals/run.py --live` is never run.

**Slices D, E and F run in parallel** on sibling branches off slice C. Keep every change to the shared files additive and small. See "Coordination with E and F".

**CLAUDE.md conventions.**

- Imports at module scope and acyclic. Inside `store/`, a cross-package import binds a submodule (`from ..inference import embed`, then `embed.embed_sync(...)`).
- Writes go through `store.atomic`, paths through the resolvers.
- The pydantic v1/v2-agnostic rule applies.
- No new `cid`-taking mutator, so `store/locks.py` is untouched. Every parameter this slice adds for attribution is named `campaign` (and `scene`), never `cid`: `test_lock_domain_guard.py` reads a `cid` parameter as campaign-scoped.
- Lint ratchets: if a count shrinks, run `make baseline PY=$PY` and commit the smaller file with the fix.

**Privacy:** invented names only (Mara, Saltmarch, Seraphine, Winifred, Realm) and fake keys (`sk-fake-…`). A plan, test or commit never states anything about a real store.

**Commands.**

- `PY` is the main checkout's venv interpreter. A worktree has none of its own: `PY=/path/to/grimoire/backend/.venv/bin/python`, or `Scripts\python.exe` on Windows.
- Backend tests: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`
- Lint, from the repo root: `make check-lint check-mypy PY=$PY`
- Known root-only failure, if it appears: `tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.

**Worktrees:** reset to `claude/inference-slice-d` first; the harness bases worktrees on `main`.

## Review Focus

1. **The embedding provider goes down mid-play.** Every turn still falls back to keyword recall and keyword art ranking. `semantic` does not retry a `network` failure. Each request that went out files exactly one error row, and the turn completes. → Task 5 `test_a_down_provider_degrades_and_files_one_error_row_per_request`.
2. **The steady state is free.** A continuity run whose texts are all cached files no ledger row and no capture line, and `embed_sync(task, [])` sends nothing. Lore recall still embeds its query every turn, so it files one row per turn, as it already spends one request per turn. → Task 4 `test_nothing_to_embed_is_free`; Task 5 `test_a_fully_cached_continuity_run_files_no_row`.
3. **A spent deadline is not a failed call.** This covers semantic's query-alone retry after a slow first call, and absorb's identity check with no budget left. `embed_sync`'s own check files nothing. The client's first-batch check, reached inside the meter, files an `aborted` row and no error. Neither files a `network` error row for a request that never went out. → Task 4 `test_a_spent_deadline_sends_nothing_and_files_nothing`, `test_a_deadline_that_lapses_inside_the_meter_is_aborted`.
4. **The role moves between a caller's cache read and its embed.** For example, the user picks another Embedding model, or a key edit bumps the provider's `rev`. The vectors that come back are produced by, and saved under, the space the caller read its cache with. `embed_sync` never re-resolves. → Task 4 `test_embed_uses_the_space_it_was_handed`.
5. **One embed splits into batches, and one batch reports no usage or fails after another was read.** The row's counts are the sum of the batches read, absent once any read batch said nothing, and never `0`. → Task 2 `test_a_batch_without_usage_leaves_the_count_absent`, `test_usage_is_kept_when_a_later_batch_fails`; Task 4 `test_an_embed_files_one_row`.
6. **A provider error carries private text.** A 400 body that echoes the input, or a 307 whose `Location` holds a key, reaches neither the log file nor `errors.summary()`, while the caller (and the model test's verdict) still sees the provider's own words. → Task 4 `test_a_provider_error_never_logs_its_body_or_location`, `test_model_test_call.py::test_an_embed_probe_failure_logs_only_its_kind_and_status`.
7. **A campaign's embeds are charged to it.** Lore recall, art and a continuity sweep file rows with `campaign`; absorb's identity check adds `scene`; library search files none. → Task 5 `test_a_recall_inside_a_campaign_is_charged_to_it` and its siblings.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec | rulings 1–12 folded in | 0 |
| `store/routing.py` | `EMBED_TASKS` registry | 1 |
| `store/inference/translate.py` | `embedding_view(cfg)`; `embedding_role` becomes a one-line wrapper over it; `global_view` untouched | 1 |
| `store/inference/resolved.py` | `ResolvedInference.space_id` | 1 |
| `store/inference/resolve.py` | `embedding(cfg)`, the Embedding role's entry point; `_embed_endpoint` moved here; `resolve()` refuses embed work | 1 |
| `store/embed_space.py` | `endpoint(cfg)`; `resolve(cfg)` becomes its four-key view | 1 |
| `store/inference/settings.py` | `_embedding_card` extends C's `problem` with the known-`no` reason; `used_by` untouched | 1 |
| `embeddings.py` | `EmbeddingsClient.embed(..., usage=)` folds each batch's `usage` block; `NOT_SENT` marks a first request never sent | 2 |
| `store/usage.py` | `record(..., operation=)`; `Meter.done` files the holder's `operation` | 3 |
| `store/inference/embed.py` (new), `store/inference/__init__.py` | `embed_sync` / `embed`: task check, meter, sanitised failure, capture | 4 |
| `routes/config.py` | the model-test embed probe passes `usage=`, stamps `operation`, and records a sanitised failure | 4 |
| `tests/llm_fakes.py` | `FakeEmbeddings.embed` takes `usage=None` | 4 |
| `store/context/semantic.py`, `store/context/world_state.py`, `store/semsearch.py`, `store/context/art.py`, `store/continuity/similarity.py`, `store/continuity/reconcile.py`, `store/continuity/identity.py` | call `embed_space.endpoint` and `embed.embed_sync`; thread `campaign` (and identity's `scene`) | 5 |
| `tests/test_operation_guard.py` (new), `tests/test_usage_guard.py`, `tests/test_routing_guard.py`, `CONTRIBUTING.md` | guards and their table rows | 6 |
| `CLAUDE.md` | "Adding an LLM call site?" covers embeddings | 7 |

---

### Task 0: Settle the spec

**Files:** the spec.

- [ ] **Step 1: Amend the spec for rulings 1–12**, each in the section it touches:
  - **§7.1:** `embed` / `embed_sync` live in `store/inference/embed.py`, because every caller is a store module and the store never imports `llm.py` (#239). `decide` stays in slice F's top-level `grimoire/inference.py`. The two modules are disjoint, and neither merges into the other.
  - **§7.3:**
    - the signature `embed_sync(task, texts, *, space, client, deadline=None, campaign="", scene="", cached=None, uncached=None)`, and why the space is handed in;
    - the client is the caller's own;
    - the registry is `routing.EMBED_TASKS`;
    - a row carries the caller's campaign where it has one, and the resolution never reads one;
    - a known `no` means off;
    - the user-visible cost change of the "User-visible" paragraph above: an endpoint that reports no price files unpriced rows, so totals read "incomplete" until rates are set, and slice E prices them.
  - **§5.4:** the Embedding role has its own entry point, `resolve.embedding`, which the spec now names beside `inference.resolve` ("one function" per operation family). `resolve.resolve` refuses an embed task, `operation="embed"` and `role="embedding"`. `space_id` is set only when the role embeds.
  - **§6.4 step 4:** delete "until slice D meters embeddings". The probe row carries `operation: "embed"` and whatever counts the endpoint reported. A failure is recorded with its kind and status only.
  - **§9.3:** one row per `embed_sync` call. §9.3's `operation` field lands with D.
  - **§9.4:** the Debug capture line, with cache hits (`cached`) and misses (`uncached`) named apart from `inputs`. An embed failure's recorded detail is kind and HTTP status only.
  - **§12, "Embedding provider fails":** "caller degrades as today; no fallback; one metered error row per request that went out, its detail the kind and HTTP status only".
  - **§10 / §12, one refusal decision:** the Embedding card's `problem` (C's field) also names a known `no`.
  - **§14 row D:** settled.
  - **§14.1:** the guard is `test_operation_guard.py`. Slice F adds the `decide` half to the same file and appends to the same `CONTRIBUTING.md` row.
- [ ] **Step 2: Commit** `docs(spec): settle slice D's embed signature, rows and capture`

---

### Task 1: One reader of the Embedding role

**Files:**
- Modify:
  - `store/routing.py`, beside `NON_ROUTE_TASKS`;
  - `store/inference/translate.py` (`embedding_view` added; `embedding_role` rewritten as a wrapper);
  - `store/inference/resolved.py`, `store/inference/resolve.py`, `store/embed_space.py`;
  - `store/inference/settings.py` (`_embedding_card` only).
- Test: `tests/test_inference_embedding.py` (new), `tests/test_inference_settings.py`.
- Test that changes: `tests/test_embed_space_role.py::test_the_embedding_role_is_what_resolves`. It patches `translate.embedding_role`, which the resolution no longer reads, so it now patches `translate.embedding_view`. Name this change in the report.
- Test that must stay green unmodified: `tests/test_inference_translate.py`. Its two `embedding_role` tests (lines 58 and 63) still hold through the wrapper.

**Interfaces:**
- Produces `routing.EMBED_TASKS: tuple[str, ...] = ("semantic-recall", "semantic-search", "art-catalog", "continuity-similarity")`.
  - These are **not** in `ROUTES` or `TASK_ROUTE`; the frozen observer enumerates `TASK_ROUTE`.
  - They are disjoint from `NON_ROUTE_TASKS`.
- Produces `translate.embedding_view(cfg: dict) -> dict[str, str]`: `{role_key("embedding","provider"): …, role_key("embedding","model"): …}`, both **stripped**.
  - A current-format config reads them from the `role_embedding_*` keys; a legacy one from `embeddings_connection_id` / `embeddings_model`. That is today's `embedding_role` rule, returned in the role-key vocabulary.
- Changes `translate.embedding_role(cfg) -> tuple[str, str]` into a one-line wrapper that returns the two values of `embedding_view(cfg)`.
  - It stays because slice E's `in_use.selections()` and `settings.used_by` read it, and because it is the stored-pair accessor, not the resolution.
  - It cannot wrap `resolve.embedding` itself: `resolve` imports `translate`.
- `global_view` is **not** edited. It keeps reading `embedding_role` behind its `if provider:` gate (`translate.py:105`), so a legacy view gains no empty `role_embedding_*` keys.
- Produces `ResolvedInference.space_id: str | None = None`, appended after `fallback_missing`.
- Produces `resolve.embedding(cfg: dict | None = None) -> ResolvedInference`:
  - Reads `config.read_config()` only when `cfg` is None.
  - The selection is `cascade.role_selection("embedding", campaign={}, glob=translate.embedding_view(cfg), exists=…)` over a fresh `connection_lookup()`.
  - Nothing selected → `role=""`, `via=""`, `scope="none"`, `attempts=()`.
  - Otherwise, it builds one attempt:
    - `_attempt(provider, model, dict(NO_SAMPLING), raw, current=is_current(cfg))`;
    - its `base_url` is replaced (`dataclasses.replace`) by `_embed_endpoint(raw, current)`. That is today's `embed_space._endpoint` rule, moved verbatim with its docstring.
  - `missing = _missing(attempt, frozenset({"embed"}))`; `role="embedding"`, `via="role"`, `operation="embed"`, `task=""`.
  - `space_id = f"{raw['id']}\0{raw['rev']}\0{model}"` only when `model`, the endpoint and `not missing` all hold; otherwise None.
  - **What one resolution reads** (M5):
    - `config.md`, only when `cfg` is None;
    - the embedding provider's connection file, once through the memoised lookup (`read_connection_raw` runs `ensure_migrated`, as today; no explicit call is added);
    - that provider's cached catalog sidecar (`llm_connections.cached_row`, inside `_lowered`);
    - `facts.json` (`facts.of`, in `_model_facts`).
  - The last two reads are new on every recall, art rank and sweep. Ruling 4 needs both, and both are small and never raise. No other provider's file is read.
  - **E's account block** (ruling 12): when slice E's `ACCOUNT_KEY` exists on the lowered conn, `embedding()` also writes `operation: "embed"` and `role: "embedding"` into the attempt's block. It does so the way E's `resolve()` stamps its attempts. Whichever of D and E lands second adds that line.
- Changes `resolve.resolve(...)`. It raises `ValueError` before any read when any of these holds, so embed work can never fall through to Primary as an unknown task:
  - `operation == "embed"`;
  - `task in routing.EMBED_TASKS`;
  - `role == "embedding"` (`cascade.choose_role` would otherwise answer it).
  - No caller asks for any of the three today. The settings view resolves only `GENERATIVE_ROLES` through `resolve()`.
- Produces `embed_space.endpoint(cfg: dict | None = None) -> dict | None`, built from `resolve.embedding(cfg)`:
  - Its eight keys are `{"model", "base_url", "key", "space", "provider", "provider_name", "provider_kind", "conn"}`, where:
    - `key` is the connection's `api_key`;
    - `provider_name` is the connection's `name`, else its id;
    - `provider_kind` is its `kind`;
    - `conn` is the attempt's lowered connection dict (`attempt.conn`), so the operation's stamp can read the attempt's account block (ruling 12).
  - It is None when `space_id` is None.
  - It catches the same `except` tuple as today's `resolve`.
  - Nothing logs or serialises this dict. The capture line reads only `space["space"]`.
- Changes `embed_space.resolve(cfg=None) -> dict | None`: the four keys `model, base_url, key, space` of `endpoint(cfg)`. It calls `endpoint` by its module name, so a test patching `endpoint` moves both.
- Changes `settings._embedding_card`:
  - `stored` keeps reading `translate.embedding_role(cfg)`, and `on` stays `embed_space.resolve(cfg) is not None`.
  - It **extends C's `problem` field** (M6). C's final fix wave adds `problem` to this card, with the reasons that exist today: a missing key or an unreadable provider. D adds one reason and introduces no field:
    - C's reasons come first, because a role that cannot send at all says that before anything else.
    - Otherwise, when `resolve.embedding(cfg).missing` is non-empty, `problem` is a fixed sentence: `f"{model} on {provider_name} cannot {capabilities.CANNOT['embed']}, so embedding is off — choose another Embedding model."`.
    - Otherwise C's answer stands (None for a working role).
  - This is the card's share of §12's "one refusal decision": the same `missing` that turned the role off.
- `settings.used_by` is **not** edited (E's Task 5 rewrites its body).
- No frontend change: rendering the card's `problem` is C's.

- [ ] **Step 1: Write the failing tests** in `test_inference_embedding.py`, with a `GRIMOIRE_HOME` fixture as in `test_embed_space_role.py`:
  - `test_the_cascade_is_the_reader`: spy `cascade.role_selection`; `embed_space.resolve()` calls it with role `"embedding"` and `campaign == {}`. Spy `translate.embedding_view`; it is called once per resolution.
  - `test_space_id_is_todays_string`: format-2 config on a local `openai_compatible` provider → `resolve.embedding(cfg).space_id == f"{conn}\0{rev}\0m"` and `== embed_space.resolve(cfg)["space"]`.
  - `test_space_id_moves_with_rev_and_with_model` (§15):
    - `llm_connections.update_connection(conn, api_key="sk-fake-2")` changes it, and so does another model.
    - A rename leaves it alone, because `name` has been in `REV_NEUTRAL_FIELDS` since slice C.
  - `test_the_embedding_role_has_no_fallback` (rule 4): with `role_primary_fallback_*` set to a second provider:
    - `len(resolve.embedding().attempts) == 1`;
    - `resolve.FALLBACK_KEY not in attempts[0].conn`;
    - `fallback_missing == ()`.
  - `test_padded_ids_resolve_as_before`: legacy `embeddings_connection_id="  local  "` and a format-2 `role_embedding_provider="  local  "`, `role_embedding_model=" m "` → the same space as the unpadded pair.
  - `test_a_known_no_turns_embedding_off_without_a_request`: an `openai_compatible` provider at `https://api.z.ai/api/paas/v4` as the Embedding role → `endpoint()` is None and `resolve.embedding().missing == ("embed",)`. Ruling 4.
  - `test_an_unknown_capability_still_embeds`: a custom-URL provider → `endpoint()` is not None.
  - `test_endpoint_names_the_provider_and_resolve_keeps_four_keys`:
    - `endpoint()` has the eight keys, and `endpoint()["conn"]["id"] == conn`;
    - `set(resolve()) == {"model","base_url","key","space"}`.
  - `test_resolve_refuses_embed`: each of these raises `ValueError`:
    - `resolve.resolve("semantic-recall")`;
    - `resolve.resolve("chat", operation="embed")`;
    - `resolve.resolve("", role="embedding")`.
  - `test_embed_tasks_are_registered_apart`: `EMBED_TASKS` is disjoint from `TASK_ROUTE` and `NON_ROUTE_TASKS`, and `routing.route(t) is None` for each.
- [ ] **Step 2: Add to `test_inference_settings.py`:**
  - `test_the_embedding_card_says_why_a_known_no_turned_it_off`: the z.ai provider of the test above, chosen as the Embedding role with `confirm_embedding: true`. The card has `on is False`, and its `problem` names the model and "cannot make embeddings".
  - `test_a_working_embedding_card_has_no_problem`: a custom-URL provider → `problem is None`.
  - C's own `problem` tests (a missing key, an unreadable provider) stay as C wrote them.
- [ ] **Step 3: Run them and watch them fail:** `pytest tests/test_inference_embedding.py tests/test_inference_settings.py -q` → FAIL: there is no `embedding`, no `EMBED_TASKS`, and no known-`no` reason.
- [ ] **Step 4: Implement** the Interfaces above. Rewrite `test_the_embedding_role_is_what_resolves` to patch `translate.embedding_view`.
- [ ] **Step 5: Run the tests, then lint:**
  - `pytest tests/test_inference_embedding.py tests/test_embed_space_role.py tests/test_inference_translate.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_settings.py tests/test_inference_migrate.py tests/test_inference_resolve.py tests/test_inference_cascade.py tests/test_import_guard.py -q`
  - Expect PASS, with **both equivalence files and `test_inference_translate.py` unmodified**.
  - `make check-lint check-mypy PY=$PY`
- [ ] **Step 6: Commit** `feat(inference): the Embedding role has one reader, a space id, and says why it is off`

---

### Task 2: The embeddings client reports what each request spent

**Files:**
- Modify: `embeddings.py` (`EmbeddingsClient.embed`, `_post`, `_fetch`)
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Produces `EmbeddingsClient.embed(texts, model, key, base_url, deadline=None, usage: dict | None = None) -> list[list[float]]`, unchanged without `usage`.
- With a holder, each batch's parsed body is folded in **before** `_vectors` validates it, so a billed but malformed reply still counts. The fold:
  - `prompt_tokens`: `llm_usage.tokens(body["usage"]["prompt_tokens"])`, summed over the call's batches.
  - `cost_usd`: `llm_usage.money(body["usage"]["cost"])`, summed, with `cost_basis = llm_usage.BILLED`.
  - Once any batch that was read lacks one of these, that key is removed from the holder and **stays absent for the rest of the call**: a partial sum is not the call's count.
  - `model` is the body's `model` when it is a non-empty string.
  - It never writes `completion_tokens`: an embedding generates nothing.
- A batch that failed before its body was read contributes nothing.
- The fold never raises.
- No new `llm_usage` function: the client uses `tokens`, `money` and `BILLED`.
- Produces `embeddings.NOT_SENT = "not_sent"` (M7).
  - `_fetch`'s "deadline passed before the request" error carries `code=NOT_SENT` when it fires for the call's **first** batch, which means no request of this call went out. `embed` tells `_post`/`_fetch` whether a batch has been sent.
  - The same check on a later batch carries no code, because earlier batches were sent and billed.
  - The kind (`network`) and the wording are unchanged, so every caller's degradation is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_usage_is_summed_across_batches`:
    - Setup: `BATCH` is monkeypatched to 2, with three inputs.
    - The bodies carry `usage {prompt_tokens: 3, cost: 0.001}`, then `{prompt_tokens: 2, cost: 0.002}`.
    - The holder has `prompt_tokens == 5`, `cost_usd == pytest.approx(0.003)` and `cost_basis == "billed"`.
  - `test_a_batch_without_usage_leaves_the_count_absent`: with `BATCH=1`, the batches report `{prompt_tokens: 3}`, then nothing, then `{prompt_tokens: 4}` → `"prompt_tokens" not in holder`.
  - `test_usage_is_kept_when_a_body_is_malformed`: `usage {prompt_tokens: 3}` with `data` holding a short vector set → raises `bad_response`; holder `prompt_tokens == 3`.
  - `test_usage_is_kept_when_a_later_batch_fails`: with `BATCH=1`, batch 1 reports 3 and batch 2 answers 503 → raises `network`; holder `prompt_tokens == 3`.
  - `test_a_usage_block_of_non_numbers_is_absent`: `{"prompt_tokens": "lots", "cost": true}` → neither key.
  - `test_the_reported_model_is_kept`: body `model: "vendor/embed-2"` → `holder["model"] == "vendor/embed-2"`.
  - `test_a_first_request_never_sent_is_marked`: `deadline=time.monotonic() - 1` → `EmbeddingsError` with `kind == "network"` and `code == embeddings.NOT_SENT`; the transport saw 0 requests.
  - `test_a_later_batch_past_the_deadline_is_not_marked`:
    - Setup: `BATCH=1` and two texts. The transport answers batch 1, then advances a patched `time.monotonic` past the deadline.
    - The call raises `network` with `code is None`.
- [ ] **Step 2:** `pytest tests/test_embeddings.py -q` → the new tests FAIL (`usage` is an unexpected keyword).
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `pytest tests/test_embeddings.py tests/test_import_guard.py -q` → PASS. Then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5: Commit** `feat(embeddings): the client folds each batch's usage into a holder`

---

### Task 3: A ledger row can say its operation

**Files:**
- Modify: `store/usage.py` (`record`, `Meter.done`)
- Test: `tests/test_usage_store.py`

**Interfaces:**
- Produces `usage.record(..., images: int = 0, operation: str = "")`.
  - `operation` is the **first keyword after `images`**, the head of the block slice E appends there. E drops `operation` from its own list (ruling 12a).
  - It is written in the existing optional-identity loop, only when non-empty (absent rather than empty, as `campaign` is).
- `Meter.done` passes `operation=self.usage.get("operation", "")` as the argument right after `images=`. Nothing else in `usage.py` changes; rollups treat the row as any call.
- **If E has landed first** with `operation` in its block, this task's code is dropped and its three tests are kept. They pass against E's code.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_row_carries_a_named_operation`: `record(task="semantic-recall", operation="embed")["operation"] == "embed"`.
  - `test_a_row_names_no_operation_by_default`: `"operation" not in record(task="chat")`.
  - `test_the_meter_files_the_holders_operation`: `with usage.meter("art-catalog") as m: m.usage.update(model="m", operation="embed")`; the filed row has `operation == "embed"`.
- [ ] **Step 2:** `pytest tests/test_usage_store.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `pytest tests/test_usage_store.py tests/test_usage_rollup_store.py tests/test_usage_routes.py -q` → PASS. Then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5: Commit** `feat(usage): a ledger row can name its operation`

---

### Task 4: The embed operation, and both doors metered

**Files:**
- Create: `store/inference/embed.py`
- Modify:
  - `store/inference/__init__.py` (import and export `embed`);
  - `routes/config.py` (`_embed_probe`);
  - `tests/llm_fakes.py`: `FakeEmbeddings.embed` gains `usage=None`, and an optional `usage_for: Callable[[list[str]], dict] | None` whose result it folds into the holder.
- Test: `tests/test_inference_embed.py` (new), `tests/test_model_test_call.py`

**Interfaces:**
- Consumes:
  - `routing.EMBED_TASKS` and the endpoint dict (`embed_space.endpoint`, Task 1);
  - `EmbeddingsClient.embed(..., usage=)` and `embeddings.NOT_SENT` (Task 2);
  - the holder's `operation` (Task 3).
- Produces `embed.embed_sync(task: str, texts: list[str], *, space: dict, client: embeddings.EmbeddingsClient, deadline: float | None = None, campaign: str = "", scene: str = "", cached: int | None = None, uncached: int | None = None) -> list[list[float]]`. Its steps, in this order:
  1. `task not in routing.EMBED_TASKS` → `ValueError`, before anything else.
  2. `not texts` → `[]`. No meter, no row, no capture.
  3. `deadline is not None and time.monotonic() >= deadline` → raise `embeddings.EmbeddingsError("network", "embeddings deadline passed before the request", code=embeddings.NOT_SENT)`. That is the client's own kind and wording. It is raised outside any meter, so it files no row.
  4. `with usage.meter(task, campaign=campaign, scene=scene, model=space["model"]) as m:`. Stamp the holder with `_stamp(m.usage, space)`:
     - `model=space["model"]`;
     - `connection=space.get("provider_name") or space.get("provider", "")`;
     - `provider=space.get("provider_kind", "")`;
     - `attempts=1` and `operation="embed"`.
     - These are the identity keys `llm._stamp` writes, plus `operation`. `.get` serves the provider fields, because a test double's space may carry only the four view keys.
     - `_stamp` is **the one place** slice E adds `llm_usage.account(holder, space.get("conn") or {})` (ruling 12c).
  5. Call `client.embed(texts, space["model"], space["key"], space["base_url"], deadline=deadline, usage=m.usage)` exactly once, inside `try`. On `except Exception as exc`, finish the meter explicitly, then re-raise `exc` unchanged:
     - `getattr(exc, "code", None) == embeddings.NOT_SENT` → `m.done("aborted")` (M7). A deadline that lapsed between step 3 and the client's own check is not a network failure.
     - Otherwise:
       - `kind = getattr(exc, "kind", None) or type(exc).__name__` and `status = getattr(exc, "status", None)`;
       - `m.done("error", kind, detail=f"{kind} (HTTP {status})" if status else kind, exc=exc)` (I1).
     - **Never `str(exc)`.** `Meter.__exit__` then finds the meter done (a no-op). A `BaseException` (a cancellation) is not caught and reaches `__exit__` as `aborted`, as today.
  6. Capture, success or failure, one line:
     - `logs.record("debug", "embed", "Embedding call", kind="embed", task=task, campaign=campaign, scene=scene, inputs=len(texts), bytes=<UTF-8 bytes sent>, space=space["space"], ok=<bool>)`;
     - plus `dims=len(vectors[0])` on success and `error=<kind>` on failure;
     - plus `cached=cached` and `uncached=uncached` when given.
     - `inputs` counts what was sent, the query included. `cached` is the caller's cache hits and `uncached` its misses, texts outside the warm window included (M2).
     - **Never** the text, the provider's message or a URL.
- `embed_sync` never re-resolves the role (Review Focus 4).
- Produces `async embed.embed(task, texts, *, space, client, deadline=None, campaign="", scene="", cached=None, uncached=None) -> list[list[float]]`: `await asyncio.to_thread(embed_sync, …)` with the same arguments.
- The probe:
  - `_embed_probe` stamps `operation: "embed"` beside its existing holder keys.
  - It calls the client inside a lambda, `asyncio.to_thread(lambda: _EMBEDDINGS.embed(..., usage=m.usage))`. The guard (Task 6) refuses a method handed around as a value.
  - It catches a failure inside its meter and finishes it the way step 5 does, with the same kind-and-status detail, then re-raises. The probe's verdict still reads `exc.detail` (through `probes.scrub`), so the user still sees the provider's error. The probe's fixed string cannot be echoed, but a `Location` can still carry a key.
  - It stays on the tested provider and the `model-test` task (ruling 8).
  - Its docstring loses "until slice D meters embeddings (ruling 14)".

- [ ] **Step 1: Write the failing tests** in `test_inference_embed.py`.
  - Setup for every test:
    - a real `EmbeddingsClient` over `httpx.MockTransport`, answering each input with `[0.5, 0.25]` unless a test says otherwise;
    - the space from `embed_space.endpoint()`, on a local `openai_compatible` provider named "Saltmarch Vectors" whose model is `embed-1`.
  - `test_an_embed_files_one_row`: the body reports `usage {prompt_tokens: 7}` → one `usage.calls(days=1)` row with:
    - `task == "semantic-recall"`, `operation == "embed"`, `model == "embed-1"`;
    - `connection == "Saltmarch Vectors"`, `provider == "openai_compatible"`;
    - `prompt_tokens == 7`, `"completion_tokens" not in row`, `"campaign" not in row`, `status == "ok"`.
  - `test_a_row_carries_the_campaign_and_scene_it_is_given`: `campaign="saltmarch", scene="s1"` → the row and the capture line carry both.
  - `test_a_split_call_is_still_one_row`: `BATCH=1`, two texts → two requests, one row with `prompt_tokens` summed.
  - `test_nothing_to_embed_is_free`: `embed_sync("art-catalog", [], …) == []`. The transport saw 0 requests, and there are no ledger rows and no `embed` log row.
  - `test_a_spent_deadline_sends_nothing_and_files_nothing`: `deadline=time.monotonic() - 1` → `EmbeddingsError` with `kind == "network"`. There are 0 requests, no ledger row and no `error` log row.
  - `test_a_deadline_that_lapses_inside_the_meter_is_aborted`: patch `time.monotonic` so step 3 passes and the client's first-batch check fails. Then:
    - 0 requests;
    - one row with `status == "aborted"`;
    - no `error` log row.
  - `test_a_provider_failure_is_one_error_row`: a 503 → raises; one row with `status == "error"`, `error == "network"`. `logs.read(level="error")` holds one row with `module == "semantic-recall"` and message `"network (HTTP 503)"`.
  - `test_a_provider_error_never_logs_its_body_or_location` (I1). Two failures in turn:
    - a 400 whose body is `{"error": {"message": "cannot embed: Mara crossed the Saltmarch at dusk"}}`;
    - a 307 whose `Location` is `https://gw.example/v1/embeddings/?key=sk-fake-307`.
    - Neither `"Saltmarch at dusk"` nor `"sk-fake-307"` appears in the month log file or in `json.dumps(errors.summary())`.
    - Each raised exception's `detail` still holds the provider's words.
  - `test_an_unregistered_task_is_refused_before_anything_is_sent`: `"chat"` → `ValueError`; 0 requests.
  - `test_embed_uses_the_space_it_was_handed`:
    - Take `space = embed_space.endpoint()`, then point the role at another provider and model.
    - `embed_sync(..., space=space)` posts to the first base URL with the first model.
    - A spy on `resolve.embedding` sees no call.
  - `test_the_capture_line_never_carries_the_text`: at `logs.apply_level("debug")`, embed `"Mara crossed the Saltmarch at dusk"` with `cached=3, uncached=5`.
    - One row has `module == "embed"`, `inputs == 1`, `bytes == 34`, `dims == 2`, `cached == 3`, `uncached == 5` and `space == space["space"]`.
    - The month log file does not contain `"Saltmarch at dusk"`.
  - `test_nothing_is_captured_above_debug`: at `info`, there is no `embed` log row.
  - `test_the_async_form_is_the_same_call`: `asyncio.run(embed.embed("semantic-search", ["x"], space=…, client=…))` returns the vector and files one row.
- [ ] **Step 2: Change `test_model_test_call.py`:**
  - In `test_a_confirmed_test_…` (around line 352), drop the "Ruling 14" comment. The uncounted row now asserts `operation == "embed"`. It is still uncounted, because `_vector` reports no usage.
  - Add `test_an_embed_probe_row_carries_what_the_endpoint_reported`: the `MockTransport` body carries `usage {prompt_tokens: 4}` → the `model-test` row has `operation == "embed"` and `prompt_tokens == 4`.
  - Add `test_an_embed_probe_failure_logs_only_its_kind_and_status`:
    - a 307 to `…?key=sk-fake-307` → the error log row's message is `"missing_key"`;
    - `"sk-fake-307"` is absent from the month log file;
    - the probe's reported result still carries the provider's own error text.
  - `test_an_embed_failure_is_one_metered_row_with_no_token_counts` stays as it is.
- [ ] **Step 3:** `pytest tests/test_inference_embed.py tests/test_model_test_call.py -q` → FAIL.
- [ ] **Step 4: Implement**, including the `FakeEmbeddings` signature.
- [ ] **Step 5:** `pytest tests/test_inference_embed.py tests/test_model_test_call.py tests/test_import_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_lock_domain_guard.py -q` → PASS. Then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 6: Commit** `feat(inference): embed_sync, the one metered door to the embeddings endpoint`

---

### Task 5: The four callers embed through the operation, under their campaign

**Files:**
- Modify:
  - `store/context/semantic.py`: `settings`, `recall_scored`, `_embed`, and the module docstring's "Configuration" and "fails to keyword-only";
  - `store/context/world_state.py` (`_world_info`'s `recall=`);
  - `store/semsearch.py` (`_embed`, `search_semantic`);
  - `store/context/art.py` (`_semantic_scores`, `rank`);
  - `store/continuity/similarity.py` (`available`, `embed_missing`, `_embed_apart`, `semantic`);
  - `store/continuity/reconcile.py` (`_semantic`);
  - `store/continuity/identity.py` (`_semantic`, `examine`).
- Tests: `tests/test_context_semantic.py`, `tests/test_lore_shedding.py`, `tests/test_semsearch_store.py`, `tests/test_context_art.py`, `tests/test_continuity_similarity.py`, `tests/test_continuity_reconcile.py`, `tests/test_absorb_identity.py`.
- Tests that change, all mechanical, with the reason for each:
  - **The doubles' `embed` signature gains `usage=None`,** because `embed_sync` passes one. Affected: `test_semsearch_store.py:55`, `test_continuity_reconcile.py:954`, `test_context_semantic.py:51` and `:408`, `test_search_route.py:164`, `test_continuity_similarity.py:547`.
  - **`monkeypatch.setattr(embed_space, "resolve", …)` becomes `"endpoint"`** where it feeds an embedding caller. The caller now reads `endpoint`, and `resolve` follows it, so the old patch would silently stop reaching the caller. Affected: `test_continuity_read_cost.py:349`, `test_continuity_graph.py:1170`, `test_continuity_routes.py:346` and `:409`, `test_context_art.py:490` and `:508`.
  - **`recall_scored` fakes gain `**_`,** because `_world_info` now passes `campaign=`. Affected: `test_lore_shedding.py:273` and `:515`, `test_lore_activation.py:676` and `:687`, and `test_actor_context.py:500` (`top_one(candidates, _text, **_)`).
  - **The `_semantic_scores` fake at `test_context_art.py:493` gains `**_`,** because `rank` now passes `campaign=`.
  - **`test_continuity_read_cost.py:395`, the positive control, embeds the app's way** (M8): `embed.embed_sync("continuity-similarity", ["control"], space=embed_space.endpoint(), client=similarity._CLIENT)`. It no longer calls `similarity._CLIENT.embed(...)` directly.
  - **`test_absorb_identity.py:636–655`** (`test_identity_embedding_deadline_never_exceeds_the_absorb_budget`) asserted that embeds stay unmetered (I6). Its deadline assertions stay. The usage half becomes:
    - the scene's rows (`campaign=cid`, `scene == sid`) split into LLM rows and embed rows;
    - the LLM rows are those with `operation != "embed"`: `len(llm_rows) == len(fake.requests) == 2`, and none has `model == "embed-1"`;
    - every embed row has `task == "continuity-similarity"`, `operation == "embed"`, `campaign == cid` and `scene == sid`;
    - `len(embed_rows) == len(double.calls)`.
    - The "stay unmetered (§9.4)" comment is deleted.
  - `test_todo_route.py:1289` and `test_continuity_similarity.py:403` patch `resolve` for `todo` and `drivers.matching`, which keep reading `resolve`. They are unchanged.
  - `test_continuity_reconcile.py:846` and `:938` filter error rows on `task == "continuity-reconcile"`, so the new `continuity-similarity` error rows leave them green (ruling 6).

**Interfaces:**
- Consumes `embed_space.endpoint` (Task 1) and `embed.embed_sync` (Task 4).
- Each caller replaces `embed_space.resolve(…)` with `embed_space.endpoint(…)` where it embeds:
  - `semantic.settings`, `search_semantic`, `art.rank`, `similarity.available`.
  - On/off readers keep `resolve`: `drivers.matching`, `routes/todo.py`, `settings.py`, `migrate.py`.
- Each `_CLIENT.embed(…)` becomes `embed.embed_sync(<task>, …, space=<that dict>, client=_CLIENT, deadline=<as today>, campaign=…, [scene=…], cached=…, uncached=…)`:

  | Caller | Task | `campaign` (`scene`) | `cached=` | `uncached=` |
  |---|---|---|---|---|
  | `semantic._embed` (both calls) | `"semantic-recall"` | the campaign handed to `recall_scored` | `len(known)` | `len(uncached)` (before the warm window) |
  | `semsearch._embed` (both calls) | `"semantic-search"` | none: library-wide | omitted (see below) | `len(uncached)` from pass one, taken before its `del` |
  | `art._semantic_scores` | `"art-catalog"` | `rank`'s `cid` | `len(known)` | `len(uncached)` |
  | `similarity.embed_missing` (every chunk) | `"continuity-similarity"` | reconcile: `cid`; identity: `cid` and `scene=sid` | `len(loaded)` | the distinct texts among `required`, `warm` and `cached` with no loaded vector |

- **Threading the campaign** (I2). Every new parameter is keyword-only, defaults to `""`, and is named `campaign` / `scene`:
  - **Lore recall.**
    - `semantic.recall_scored(candidates, recent_text, *, campaign="")` passes it to `_embed(…, campaign=…)`. `semantic.recall` keeps its two-argument shape and passes none.
    - `world_state._world_info` hands `recall=lambda c, t: semantic.recall_scored(c, t, campaign=cid)`. The lambda resolves `semantic.recall_scored` off the module at each call, so the patchability its comment promises holds.
    - Recall has no scene: `_world_info` has no `sid`.
  - **Art.** `art._semantic_scores(cands, recent_text, cfg, *, campaign="")`, called from `rank(cid, …)` with `campaign=cid`. Art is campaign-scoped, not library-wide.
  - **Continuity.**
    - `similarity.semantic(…, campaign="", scene="")` computes `cached` and `uncached` once and forwards all four keywords through `_embed_apart` to every `embed_missing(space, texts, *, deadline, campaign="", scene="", cached=None, uncached=None)` call, the heal retry included.
    - `reconcile._semantic` passes `campaign=cid`.
    - `identity._semantic(proposals, pools, embed_deadline, *, campaign="", scene="")` is called from `examine(cid, sid, …)` with `campaign=cid, scene=sid`.
  - **Library search** stays unattributed: `search_semantic` is library-scoped and has no campaign. It omits `cached` because pass one keeps only the misses and drops every vector as it reads. The hit count exists only after the embed (pass two's `indexed`), so the line carries misses alone.
- Every other piece of each caller's logic is unchanged:
  - the warm windows;
  - the one-deadline-per-turn rule;
  - `semantic`/`semsearch` retrying the query alone, and only on `bad_response`;
  - the per-chunk saves;
  - every `except (LLMError, OSError)` degradation.
- The "Deliberately silent" comments now say that the caller stays silent and the meter records the failure (ruling 6).
- `semantic.py`'s docstring "Configuration" section stops naming `embeddings_connection_id` as the setting. It names the Embedding role (Models page).

- [ ] **Step 1: Write the failing tests:**
  - `test_context_semantic.py::test_a_recall_files_one_semantic_recall_row`: a recall with the `FakeProvider` → one row with `task == "semantic-recall"`, `operation == "embed"`, no token keys (the double reports none) and no `campaign`.
  - `test_context_semantic.py::test_a_recall_inside_a_campaign_is_charged_to_it`: `semantic.recall_scored([...], "text", campaign="saltmarch")` → the row has `campaign == "saltmarch"` and no `scene`.
  - `test_lore_shedding.py::test_world_info_hands_recall_its_campaign`:
    - patch `semantic.recall_scored` with a spy `(c, t, **kw)`;
    - build the campaign's context as the file's other recall tests do (`context.context_breakdown(cid, sid)`);
    - the spy saw `kw == {"campaign": cid}`.
  - `test_context_semantic.py::test_a_down_provider_degrades_and_files_one_error_row_per_request`: `error=EmbeddingsError("network", "down")` → `recall(...) == []`; `len(provider.calls) == 1` (no retry); one ledger row, `status == "error"`.
  - `test_context_semantic.py::test_a_bad_response_retries_the_query_alone_and_files_two_rows`: the first call raises `bad_response`, and the second succeeds → two rows, one per request.
  - `test_semsearch_store.py::test_a_search_files_one_semantic_search_row`: the row has no `campaign`.
  - `test_context_art.py::test_semantic_ranking_files_one_art_catalog_row`: drive `_semantic_scores(…, campaign="saltmarch")` with a double client → one row with `campaign == "saltmarch"`.
  - `test_context_art.py::test_rank_hands_its_campaign_to_the_scorer`: a spy on `_semantic_scores` sees `campaign == camp`.
  - `test_continuity_similarity.py::test_each_chunk_files_one_continuity_similarity_row`:
    - with `BATCH=2`, call `similarity.embed_missing(space, [three texts], deadline=_soon(), campaign="saltmarch", scene="s1")`;
    - expect two client calls, and two rows with `task == "continuity-similarity"`, `campaign == "saltmarch"` and `scene == "s1"`.
  - `test_continuity_similarity.py::test_the_capture_counts_hits_and_misses`:
    - at Debug, `similarity.semantic` runs over two required texts, one of them pre-saved, with no warm texts;
    - the `embed` log row has `cached == 1`, `uncached == 1` and `inputs == 1`.
  - `test_continuity_similarity.py::test_a_fully_cached_continuity_run_files_no_row`: everything is pre-saved under `embed_space.resolve()["space"]` → `similarity.semantic(...)` makes 0 client calls and files no ledger row.
  - `test_continuity_reconcile.py::test_a_sweep_files_its_embeds_under_the_campaign`: with the file's embedding-sweep setup (`_configure`, `_embedded(cid)`), every `continuity-similarity` row has `campaign == cid` and no `scene`.
- [ ] **Step 2:** `pytest tests/test_context_semantic.py tests/test_lore_shedding.py tests/test_semsearch_store.py tests/test_context_art.py tests/test_continuity_similarity.py tests/test_continuity_reconcile.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement**, plus the mechanical test edits listed under Files.
- [ ] **Step 4: Run the tests, then lint:**
  - `pytest tests/test_context_semantic.py tests/test_lore_shedding.py tests/test_lore_activation.py tests/test_actor_context.py tests/test_semsearch_store.py tests/test_search_route.py tests/test_context_art.py tests/test_continuity_similarity.py tests/test_continuity_identity.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_routes.py tests/test_continuity_routes.py tests/test_continuity_graph.py tests/test_continuity_read_cost.py tests/test_continuity_review_routes.py tests/test_absorb_identity.py tests/test_todo_route.py tests/test_vectors.py tests/test_embed_space_role.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`
  - Expect PASS.
  - `make check-lint check-mypy PY=$PY`
- [ ] **Step 5: Commit** `feat(store): lore recall, search, art and continuity embed through the metered operation`

---

### Task 6: Guards

**Files:**
- Create: `tests/test_operation_guard.py`
- Modify:
  - `tests/test_usage_guard.py`: append one test, and amend the module docstring's "Only `routes/` is scanned" bullet;
  - `tests/test_routing_guard.py` (`_unclassified`, `test_a_named_but_not_routed_task_is_classified`);
  - `CONTRIBUTING.md`: one new row, and the `test_usage_guard.py` row amended.

**Interfaces:**
- **Recognising an operation call** (I4). Walk every module under `backend/src/grimoire/`. A call is an **operation call** when either:
  - its callee is named `embed_sync`, as a `Name` (`embed_sync(...)` after `from …embed import embed_sync`) or an `Attribute` on any receiver (`embed.embed_sync(...)`, `store.inference.embed.embed_sync(...)`); or
  - it is `<expr>.embed(...)` where `<expr>` is one of:
    - an embed-module alias: a name bound by an `ImportFrom` that resolves to `grimoire.store.inference.embed`, relative or absolute, whatever its `as` name;
    - an attribute chain whose dotted tail is `inference.embed` (`store.inference.embed.embed(...)`).
- **`test_operation_guard.py` asserts:**
  - `test_every_embed_names_a_registered_embed_task`:
    - Each operation call's first argument is a `str` literal in `routing.EMBED_TASKS`.
    - `store/inference/embed.py` is exempt from this check alone: its async `embed` forwards `task`.
    - The guard has no marker family.
  - `test_the_operation_is_never_handed_around_as_a_value`: `embed_sync` loaded as a value outside `store/inference/embed.py` fails. That means a `Name` or `Attribute` named `embed_sync` that is not a call's `func`. Otherwise `asyncio.to_thread(embed.embed_sync, task, …)` would skip the literal check.
  - `test_every_embed_task_is_named_by_a_call_site`: parametrized over `EMBED_TASKS`, the registry holds no phantoms.
  - `test_only_the_operation_and_the_model_test_reach_the_embeddings_client`: any other call `X.embed(...)` (not an operation call) outside `embeddings.py` is allowed only in `store/inference/embed.py` and `routes/config.py`.
  - `test_the_client_is_never_handed_around_as_a_value`: a `.embed` attribute loaded as a value is a failure, on a receiver that is neither an embed-module alias nor an `….inference.embed` chain. Loaded as a value means neither a call's `func` nor the `.value` of a further attribute.
  - `test_the_guard_flags_planted_cases`. Each of these is **caught**:
    - `embed.embed_sync("chat", t, space=s, client=c)`;
    - `embed.embed_sync(task, t, space=s, client=c)` (not a literal);
    - `store.inference.embed.embed_sync(task_var, t, space=s, client=c)` (attribute chain, not a literal);
    - `from ..store.inference.embed import embed_sync` then `embed_sync(x, t, space=s, client=c)` (a `Name` call, not a literal);
    - `asyncio.to_thread(embed.embed_sync, task, t)` (the operation as a value);
    - `_CLIENT.embed(t, m, k, u)` in `store/semsearch.py`;
    - `asyncio.to_thread(_CLIENT.embed, t, m, k, u)`.
  - `test_the_guard_passes_planted_cases`. Each of these **passes**:
    - `from ..inference import embed as e` then `e.embed_sync("semantic-recall", …)`;
    - `store.inference.embed.embed("semantic-search", …)` (an operation call through an attribute chain; neither the client rule nor the usage rule may flag it).
  - `test_the_walk_finds_the_call_sites`: at least **6** operation calls (semantic 2, semsearch 2, art 1, similarity 1; the async `embed` has no caller, ruling 9), and both client calls (`store/inference/embed.py`, `routes/config.py`).
- **`test_usage_guard.py` appends `test_every_embeddings_request_is_metered`.**
  - Every `.embed(...)` call under `backend/src/grimoire/` that is not an operation call, outside `embeddings.py`, passes the keyword `usage=`, whose value is an `Attribute` named `usage` (the house `_is_metered` rule).
  - It recognises operation calls with the same rules as above. It imports the recogniser from `test_operation_guard`, so there is one definition.
  - Add a planted pass/fail pair.
  - This is §14.1's "embed is metered". Append only, and leave the existing tests alone.
  - The module docstring's "Only `routes/` is scanned" bullet becomes: the generation checks scan only `routes/`; the embeddings check walks the whole package, because store modules hold the embeddings client.
- **`test_routing_guard.py`:**
  - `_unclassified` also accepts `routing.EMBED_TASKS`.
  - The classification test adds `assert not set(routing.EMBED_TASKS) & set(routing.TASK_ROUTE)` and `routing.route("semantic-recall") is None`.
  - A `require_inference("semantic-recall", …)` still fails the "every task has a route" check, because that check reads `TASK_ROUTE`.
- **`CONTRIBUTING.md`, "The architecture guards" table** (M10):
  - New row, **the canonical text** for this file: `` `test_operation_guard.py` | every embed names a registered embed task, and only the operation and the model test reach the embeddings client | — ``. Slice F appends its decide half to this row's middle cell (`; every decide names a task on a decide route`) and adds no row of its own. If F lands first with its own row, D replaces that row's text with this one plus F's half.
  - The `test_usage_guard.py` row becomes: `every generation route meters what it spends, and every embeddings request passes a meter's holder`.

- [ ] **Step 1: Write the guards.**
- [ ] **Step 2: Check they bite.** Revert one caller to a direct `_CLIENT.embed` in a scratch edit and confirm the operation and usage guards fail. Restore it.
- [ ] **Step 3:** `pytest tests/test_operation_guard.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_docs_guard.py -q` → PASS. Then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 4: Commit** `test(guards): every embed names its task and only metered doors reach the client`

---

### Task 7: Docs, the slice's checks, and the gates

**Files:** `CLAUDE.md`; module docstrings already touched in Tasks 1, 4 and 5.

- **CLAUDE.md, "Adding an LLM call site?"** gains one paragraph on embeddings:
  - An embedding goes through `store.inference.embed.embed_sync(<task>, texts, space=embed_space.endpoint(), client=…, campaign=…)`, and the task must be in `routing.EMBED_TASKS`.
  - The space is resolved once by the caller from the global Embedding role (`resolve.embedding`) and handed in, because the caller keys its cache on it.
  - Every call is metered as `operation: "embed"`, one row per call.
    - The row carries the caller's campaign where it has one: lore recall, art and continuity do (absorb's identity check adds its scene), and library search does not.
    - The resolution never reads a campaign.
  - A failure records only its kind and HTTP status, and the text is never logged.
  - It never falls back.
  - **Cost:** an endpoint that reports no price files an unpriced row on every recall or art turn, so totals read "incomplete" until the user sets rates for that model (slice E prices it).
  - `test_operation_guard.py` holds this.
- **No change** to the detached-runs inventory (no handler starts a run) or to `docs/store-guarantees.md` (no new storage promise).

- [ ] **Step 1: Write the paragraph.** Run `pytest tests/test_docs_guard.py -q` → PASS.
- [ ] **Step 2: The slice's checks.** These are the files Tasks 1–6 name, not the full suite and not `make check` (the user's rule).
  - Run every test file named in Tasks 1–6 in one invocation.
  - `make check-lint check-mypy PY=$PY`
  - If a count shrank, run `make baseline PY=$PY` and commit it.
- [ ] **Step 3: Commit** `docs: the embedding call site rule`
- [ ] **Step 4: Gates (CLAUDE.md).**
  - `/codex:review` against the branch diff; address its findings.
  - Then `/codex:adversarial-review` against the diff **and** the spec, asking whether row D, §7.1, §7.3, §9.3–9.4, §12 and §15 "Embed" are implemented. Address its findings.
  - Report before any merge. **Merging waits for CI green**; CI runs the full suite.

---

## Parallelism

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | The spec first |
| 1 | 1, 2, 3 | Disjoint files: the resolver side, the gateway client, the ledger. Task 1 starts after the rebase onto C's final fix wave |
| 2 | 4 | Needs 1's `EMBED_TASKS`/`endpoint`, 2's `usage=`/`NOT_SENT`, and 3's `operation` |
| 3 | 5 | Needs 4 |
| 4 | 6 | The guards need every caller converted |
| 5 | 7 | Last |

## Coordination with E and F

Slices E (pricing) and F (`decide()`) are planned in parallel off the same base. This table lists the files more than one of them touches, what D does to each, and who merges. Whoever lands second rebases over a small, additive change.

| Shared file | D's change | E / F |
|---|---|---|
| `store/usage.py` | **D owns `record(..., operation=)`**: it is the first keyword after `images`, written only when non-empty, and `Meter.done` passes `operation=self.usage.get("operation", "")` right after `images=` | E appends the rest of its block (`provider_id`, `requested_model`, `role`, `preset`, `billing`, `decision_mode`, `tokens_estimated`) after D's `operation`. **E drops `operation` from its list.** If E lands first with it, D's Task 3 shrinks to its tests. F stamps `"decide"` through E's account block |
| `store/inference/translate.py` | `embedding_view` added; `embedding_role` becomes a one-line wrapper over it, same signature and values; `global_view` untouched | E reads `embedding_role` (`in_use.selections()`), which keeps working. Second to land merges |
| `store/inference/settings.py` | `_embedding_card` extends C's `problem` with the known-`no` reason; **`used_by` untouched** | E rewrites `used_by`'s body (Task 5). Second to land merges; the functions are disjoint |
| `store/inference/resolve.py` | New `embedding()`, `_embed_endpoint`, and a short `ValueError` at the top of `resolve()`; `OPERATION_CAPABILITY` untouched | E's account block in `_lowered` and `resolve()`. **`embedding()` also writes `operation: "embed"` and `role: "embedding"` into the attempt's block**, added by whichever of D and E lands second. F's decide wiring |
| `store/embed_space.py` / `store/inference/embed.py` | `endpoint()` carries `"conn": attempt.conn`. **`embed._stamp` is the one seam** where E adds `llm_usage.account(holder, space.get("conn") or {})`, after D's identity keys | E adds that line, whichever lands second. The store already imports gateway leaves (`llm_sampling`), so importing `llm_usage` is allowed |
| `embeddings.py` / `llm_usage.py` | The meter passes `model=space["model"]`; the client folds the response's `prompt_tokens` and `cost` into the holder, **never a completion count**; no new `llm_usage` function | E's local estimation applies to `embed` rows when a provider reports no counts (`note_prompt` is optional for D). E's ruling 8 prices an `embed` row with no completion count |
| `tests/test_usage_guard.py` | One appended test, `test_every_embeddings_request_is_metered`, its planted pair, and the docstring bullet; existing tests untouched | F adds `EXTRA_SOURCES` and appends decide's check. Keep both; a one-line conflict at most |
| `tests/test_operation_guard.py` (new) | Created with the embed half (§14.1's new guard) | **F adds the `decide` half to this file** (every `decide` names a task on a decide route) rather than creating a second guard. Whichever lands second adds its half |
| `CONTRIBUTING.md` guard table | **D's `test_operation_guard.py` row is the canonical text** (Task 6), and the `test_usage_guard.py` row is amended | F appends `; every decide names a task on a decide route` to D's row and adds no row. If F lands first, D rewrites F's row as D's text plus F's half |
| `store/inference/resolved.py` | `space_id: str | None = None` appended after `fallback_missing` | F appends `decision_mode` after it |
| `store/routing.py` | `EMBED_TASKS` after `NON_ROUTE_TASKS` | F flips route `operation`/`default_role` and registers `scene-break-title` |
| `tests/test_routing_guard.py` | `_unclassified` and one classification test | F's operation checks |
| `tests/llm_fakes.py` | `FakeEmbeddings.embed` signature | F's decide fakes |
| `grimoire/inference.py` | **Not touched.** D's operation is `store/inference/embed.py` | F creates it for `decide`. The modules are disjoint, and neither merges into the other |
| `CLAUDE.md` "Adding an LLM call site?", the spec | One paragraph (with the unpriced-row cost note); §5.4, §6.4, §7.1, §7.3, §9.3, §9.4, §12, §14 row D, §14.1 | E: Costs and §9; F: decide and §7.4. Merge by hand; each slice's text stands alone |

**The user-visible cost change, for E** (M4): from D onward, an embed on an endpoint that reports no price files an unpriced row on every recall or art turn. E's pricing (rates in model facts, ruling 8) is what turns those rows from "incomplete" into modelled figures. E's Housekeeping chore counts the Embedding role among the selections in use, which flags such a model for rates.

`embed._stamp` writes the same identity keys `llm._stamp` writes (`model`, `connection`, `provider`, `attempts`), plus `operation`. **If E changes what those keys mean, it changes both.**

## Rulings (Task 0 folds each into the spec)

1. **Embed tasks live in `routing.EMBED_TASKS`, not in a route.**
   - All of them resolve through the global Embedding role, so a route would carry no choice.
   - The frozen observer enumerates `routing.TASK_ROUTE`, so adding them there would change a fixture that is never regenerated.
2. **`embed_sync(task, texts, *, space, client, deadline=None, campaign="", scene="", cached=None, uncached=None)`, not `embed_sync(task, texts)`.**
   - Every caller reads its vector cache under a space before it embeds. Re-resolving inside the call could embed with one model and save under another space's key (rule 4).
   - So the caller hands over the space it read with, and the client it already owns. Each module's `_CLIENT` is the test seam that about 41 test references patch.
3. **The Embedding role's entry point is `resolve.embedding(cfg)`, through `cascade.role_selection("embedding", campaign={})`.**
   - It reads `translate.embedding_view(cfg)`, which keeps today's stripping at both formats. `translate.embedding_role` stays as a one-line wrapper over it, the stored-pair accessor.
   - `resolve.resolve` refuses `operation="embed"`, the embed tasks and `role="embedding"`, so embed work never lands on Primary as an unknown task. §5.4 names `resolve.embedding` as the Embedding role's own entry point.
   - `embed_space.resolve(cfg)` keeps its four keys (the frozen baselines read it). `embed_space.endpoint(cfg)` adds `provider`, `provider_name`, `provider_kind` and the attempt's `conn` for the ledger.
4. **A known `no` for `embed` turns embedding off.**
   - Known means from the adapter, preset hard `no`, user or catalog; the name rule's guess never counts.
   - This is §5.3 for an operation with no 409: no request is sent, and every caller degrades exactly as it does when the role is unset. A z.ai or chat-only catalog model chosen as the Embedding model stops sending a request that could only fail.
   - The Embedding card says so through C's `problem` field, which D extends with this reason from the same `missing` (§12, one refusal decision).
   - This is also why a legacy z.ai embeddings choice is not migrated: `migrate` asks `embed_space.resolve`.
5. **One ledger row per `embed_sync` call, carrying the campaign where the caller has one.**
   - The row covers all of the call's batches, under its embed task with `operation: "embed"`.
   - Resolution stays global (§4.4): one space, one cache, and a vector warmed in one campaign serves every other. Attribution does not: a campaign's budget is measured by `cost_usd` per campaign, and an OpenRouter embed is real spend.
   - Lore recall, art and continuity pass their campaign; absorb's identity check passes its scene too; library search passes none.
   - No row when nothing is sent: an empty input, or a deadline already spent at `embed_sync`'s check. A deadline that lapses after the meter opened files an `aborted` row, never an error.
6. **Embed failures are recorded at `Meter.done`, like every LLM call** (§9.3; CLAUDE.md, Observability).
   - The recorded detail is the kind and HTTP status only, never the provider's text.
   - The callers' "deliberately silent" rule still holds for the callers themselves: they write no line of their own and still degrade.
   - **A failed continuity sweep legitimately writes two error entries:** one `continuity-similarity` row per failed request (a failed *call*, from `Meter.done`), and reconcile's own `continuity-reconcile` row (a degraded *sweep*, `reconcile.py:482`). They answer different questions, as the ledger and the error store do. Tests that count one filter on `task`.
7. **§9.4's embed capture is one Debug-level `logs.record` line per call.**
   - It carries `inputs`, `bytes`, `dims`, `space`, `cached` (hits), `uncached` (misses, outside the warm window included), `ok`, the campaign and scene when given, and `error` (a kind) on failure.
   - It is written at Debug only, through the one writer, and never carries text, a provider message or a URL.
   - Embedding responses are not sent to the incoming-response capture: their payload is vectors, not prose.
8. **The model-test embed probe stays on the provider under test.** It is not an embed task and does not resolve the role. It is metered under `model-test` with `operation: "embed"` and whatever counts the endpoint reports. Its failure is recorded with kind and status only; its verdict still shows the provider's text.
9. **The async `embed` runs `embed_sync` in a worker thread** (`asyncio.to_thread`). No production caller needs it yet; it lands because §7.3 names it, and the guard covers it.
10. **`ResolvedInference.space_id`** (§5.4, D) is set only when the role embeds: a model, an endpoint, and no known `no`.
11. **The operation lives in `store/inference/embed.py`** (§7.1). Every caller is a store module, and the store never imports `llm.py` (#239). F's `decide` is in the top-level `grimoire/inference.py`; the two modules are disjoint.
12. **D owns `usage.record(operation=)`, and E's account seam is `embed._stamp`.** `endpoint()` carries the attempt's `conn`, and `resolve.embedding()` stamps `operation: "embed"` and `role: "embedding"` into E's account block. See the coordination table.

### Plan review rulings (`.superpowers/sdd/plan-reviews/slice-d-plan-review.md`)

- **I1:** an embed failure finishes its meter explicitly with a detail of kind and HTTP status only, then re-raises, and so does the model-test probe; `test_a_provider_error_never_logs_its_body_or_location` holds it (Task 4).
- **I2:** `embed_sync(…, campaign="", scene="")`, named `campaign` and never `cid`. Recall, art and continuity pass theirs, and only `search_semantic` stays unattributed. The five `recall_scored` fakes gain `**_`. `test_lock_domain_guard.py` joins Task 4's run list. Global Constraints, ruling 5, the CLAUDE.md paragraph and Task 4's tests are rewritten (Tasks 4, 5, 7).
- **I3:** `translate.embedding_role` stays as a one-line wrapper over `embedding_view`; `global_view`'s `if provider:` gate is untouched; `test_inference_translate.py` joins Task 1's run list, unmodified (Task 1).
- **I4:** any callee named `embed_sync`, and any `….inference.embed.embed(...)` chain, is an operation call. Planted cases cover both bypasses, the value form and the false flag. The floor is 6, and `test_usage_guard.py`'s docstring is fixed (Task 6).
- **I5a:** D owns `usage.record(operation=)`, first after `images`, passed through by `Meter.done`. E drops it; if E lands first, D drops it instead (Task 3, coordination).
- **I5b:** `settings.py`'s `used_by` (untouched by D) and `translate.py` are listed as shared touch-points, second to land merges (coordination).
- **I5c:** `endpoint()` carries the attempt's `conn`, and `resolve.embedding()` stamps `operation`/`role` into E's block. `embed._stamp` is the one place E adds `llm_usage.account(...)`. The meter passes `model=`, and the holder gets `prompt_tokens`, never a completion count (Tasks 1, 2, 4, coordination).
- **I6:** `test_absorb_identity.py:636–655` is rewritten to expect embed rows carrying campaign and scene (Task 5).
- **M1:** Task 0 amends §7.1/§7.3 (module placement, disjoint from F's `inference.py`, and the cost note), §12 (a metered error row), §5.4 (`resolve.embedding` named as the Embedding role's entry point) and the refusal of `role="embedding"`.
- **M2:** continuity passes hits (`len(loaded)`), and every caller names misses (`uncached`) apart from `inputs`. Search omits hits, for a stated reason (Tasks 4, 5).
- **M3:** ruling 6 states that the double error entry for a failed continuity sweep is legitimate.
- **M4:** the unpriced-row cost change is named in the User-visible paragraph, the CLAUDE.md paragraph, §7.3 and the coordination section.
- **M5:** Task 1 states what one resolution reads (config, the provider's file, its catalog sidecar, `facts.json`).
- **M6:** extends C's `problem` (added by C's final fix wave for a missing key or unreadable provider) with the reason a known `no` switched embedding off; no new field and no frontend change; tests in `test_inference_settings.py` (Task 1).
- **M7:** a deadline that lapses before the first request, inside the meter, records an `aborted` row via `embeddings.NOT_SENT`, not a network error (Tasks 2, 4).
- **M8:** `test_continuity_read_cost.py:395`'s positive control switches to `embed_sync` (Task 5).
- **M9:** the `_CLIENT` test-reference count is "about 41" (ruling 2).
- **M10:** D's `CONTRIBUTING.md` row is the canonical `test_operation_guard.py` text, and F appends its decide half to that row (Task 6, coordination).
- **Execution:** each task runs only its named test files plus `check-lint`/`check-mypy` (no task touches the frontend, so `check-eslint` does not arise). Never `make check` or the full suite. Merging waits for CI green. There is no fix-wave cap (Global Constraints, Task 7).
- **Coordinator, from C's spec review:** the Embedding-provider key/URL confirmation on `PUT /llm-connections/{id}` and the 409 `not_migrated` on model-facts writes at format 1 land in C; D plans neither.
