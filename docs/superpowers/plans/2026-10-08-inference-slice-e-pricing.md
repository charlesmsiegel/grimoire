# Inference slice E — pricing: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every ledger row says what served it and how it was billed, a model's own rates price its calls before `pricing.json` does, calls whose provider counted nothing are counted locally and flagged, and a Housekeeping chore names the models in use that nothing prices.

**Architecture:**

- **Who served a call is stamped where the attempt is known, not at call sites.** The resolver lays a small account block (`operation`, `role`, `billing`) on every lowered connection dict. The facade's `_stamp` copies it, the provider id, the sampler preset sent and the requested model into the usage holder. `Meter.done` files them. No call site and no `Meter(...)` signature changes.
- **Rates have two layers, decided in one function.** `pricing.rate_for_call` reads a model's facts rates first, then `pricing.json`. The ledger rollups, the stale-pricing chore, the new Housekeeping chore and the test-call estimate all ask it.
- **Local counting happens in the facade, off the event loop, and only for an attempt whose stream ended on its own.** The facade parks a reference to the attempt's messages and the streamed text (prose and reasoning) on the holder. When the provider's stream ends by itself and a count is missing, `_resilient` counts on a worker thread with a counter injected into `LLMClient`, and stamps the counts and the flag onto the holder. `Meter.done` only reads numbers; it never counts.
- **Labels follow the rows.** Rollups gain three breakdown counts. `components/cost.tsx`, which every cost surface formats through, renders "subscription — not billed" and "tokens estimated" from them.

**Tech Stack:** FastAPI + file-backed store (Python 3.11+, pydantic v1/v2-agnostic), React + TypeScript + Vite, vitest + RTL, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`.

- Slice E is §14 row E: "Ledger fields, rates in model facts, subscription tagging, local token estimation + flag, the Housekeeping chore".
- It draws on §0 rule 5, §4.2 (`rates`), §5.4, §5.5, §6.1 (billing, "reports price", the local-endpoint sentence), §6.4 step 1 (the estimate), §9, §14.1 (CLAUDE.md Costs) and §15 Pricing.
- Task 0 folds the design rulings at the end of this plan into the spec.
- Base: `claude/inference-slice-e` at `6a87cd4`, which is slice C, nearly finished and not yet merged.

**Gate record:**

- The plan → implementation Codex adversarial review ran: `.superpowers/sdd/plan-reviews/slice-e-plan-review.md`, verdict "Ready with changes".
- This revision answers every finding (C1, I1–I6, M1–M17). Each answer is one line under "Plan-review rulings" at the end.
- The controller's rulings from slice D's plan review are folded in too (same section, "D-a" to "D-m4").

## Global Constraints

**The Costs rules in CLAUDE.md hold verbatim:**

- **Three money columns, and no two may be added.** `cost_usd` is spend and the only thing a budget measures. `estimated_usd` is a subscription-equivalent price the provider reported. `modelled_usd` is this side's arithmetic over rates the user typed. This slice adds no fourth column and no sum.
- **A price nobody reported is never rendered as zero.** An absent count or price stays absent in the ledger (never `0`), and every surface formats through `components/cost.tsx`.
- **A price somebody did report is never rendered as absent.** A zero rate the user entered is a price. A bucket whose figures are all present renders a figure, never "not reported" (I4).
- **The Estimated total projection is unchanged.** It is the one sum, it lives only in the monthly trend (`usage._projected`), it is never spend and never enters a budget. Subscription rows modelled from user rates are part of `modelled_usd` and so part of that projection, as §9.1 says.
- `modelled_usd` keeps its definition, "arithmetic this side did". It now also covers subscription calls priced from user rates.
- A budget still uses `Rates.off()`. Nothing in this slice reaches `usage.budget`'s figure.

**Spec rules.** Rule 2: one resolver, and one rate-precedence function (`pricing.rate_for_call`). Rule 5: a price nobody reported is never rendered as zero; a zero rate the user entered *is* a price (§6.1: a local endpoint is free only once the user enters zero rates).

**The ledger is append-only and read by older builds.** New fields are additive and written only when they have a value. No existing field changes meaning, and `provider` stays the adapter kind (ruling 1). Older rows, which lack the new fields, roll up exactly as they do today.

**Bookkeeping never fails a call, and never stalls the loop.**

- **Nothing counts tokens on the event loop.** `store.tokens.count_tokens` can start a network load of its encoder (`store/tokens.py`, `_Loader.get`). The only place this slice counts is a worker thread started by `_resilient` (Task 3).
- **`Meter.done` never counts and never loads an encoder.** It reads numbers from the holder.
- **Every new piece of accounting work is guarded with `except Exception`.** That covers `Meter.done`'s new reads, the facade's count, and the prompt-text extraction. A failure costs the field or the estimate, never the row or the turn. `usage.record` and `logs.record` already work this way.

**An account block is never mutated in place.** Shallow copies of a conn (`{**conn}`) share the `ACCOUNT_KEY` block, and so do `fallback_sampling`'s copies. A writer always builds a fresh block. `llm_usage.with_account` is the one helper for that (Task 2).

**User rules:**

- **Never run the full test suite, and never `make check`.** Each task runs only the test files it names, plus the relevant lint checks:
  - backend tasks: `make check-lint check-mypy`;
  - frontend tasks: `make check-eslint` and `cd frontend && npx tsc --noEmit -p .`.
- **CI runs the full suite.** Spec §14 requires each slice to land green under `make check`; CI's `make check` is that gate. **Merging waits for CI green.**
- **Ask before spending money.** No live LLM calls, no `evals/run.py --live`. Tests use `backend/tests/llm_fakes.py`, or adapter stubs passed to `LLMClient(...)`.

**CLAUDE.md conventions:**

- Imports at module scope, and the graph stays acyclic. Store cross-package imports bind submodules.
  - **`store/pricing.py` and `store/usage.py` never import `store.inference`.** `usage → pricing → inference/__init__ → resolve → campaigns → lifecycle → usage` is a runtime cycle, even where the static guard sees an edge to a leaf. It is the same reason slice C moved `keys.py` to `store/inference_keys.py`.
  - **`store/inference/in_use.py` never imports `settings`.** `settings` imports `in_use`.
- Gateway modules (`llm.py`, `llm_usage.py`, `llm_reasoning.py`) import nothing from the store (#239). A key both sides use is restated, and a test holds the two spellings equal (the `FALLBACK_KEY` precedent). A store function the gateway needs arrives as a constructor callable (the `images=` / `load_image=` precedent).
- pydantic v1/v2-agnostic: plain fields, `Any` where the store validates.
- `store.atomic` for every write; path resolvers for every path.
- The list/detail pattern for the facts panel: read-only until **Edit**.
- In frontend tests, `await` means the page has settled. Shared scaffolding goes in `frontend/src/testkit/`.
- Lint ratchets: `make baseline` when a count shrinks, committed with the fix.

**Privacy:** invented names only (Saltmarch, Mara, Seraphine, Winifred, Realm), fake keys, and models such as `vendor/model-a`. Docs and comments never describe a real library's contents, including how many models are unpriced; constants are justified structurally.

**Tests.**

- Backend: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`. `$PY` is `.venv/bin/python` in the main checkout. In a worktree it is the main checkout's venv (CLAUDE.md, Working notes).
- Frontend: `cd frontend && npx vitest run <file>`.
- Known root-only failure: `tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
- `conftest` sets `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`. A test that needs a format-2 store stamps the marker itself.

**Parallel worktrees.**

- Reset to the slice branch first; the harness bases worktrees on `main`.
- Run `npm ci` inside the worktree. Never symlink `node_modules`.
- Report in the final message; agents cannot write the shared checkout.

## Review Focus

1. **A call that fell back is filed as the fallback.** It needs the fallback's provider id, billing and preset, and it is priced at the fallback's rates. Example: the primary on the OpenAI preset refuses, and a z.ai Coding Plan fallback answers. The row must say `subscription` and carry the fallback's provider. → Task 2 `test_the_fallback_row_names_the_fallbacks_provider_and_billing`.
2. **A provider answers under a dated snapshot of the model it was asked for.** Example: rates are stated on `gpt-4o` and the response names `gpt-4o-2024-08-06`. The call is priced from the stated rates, never left unpriced. → Task 2 `test_a_dated_snapshot_reply_keeps_the_requested_model` (at `_stamp`, through the real facade); Task 4 `test_a_dated_snapshot_is_priced_by_the_requested_models_rates`.
3. **A call that did not end on its own gets no invented counts.** This covers a cancelled turn, a failed call, and a consumer that broke out early (stop-after-fence). Each stays unmetered, as today, even when the route files it `ok`. → Task 3 `test_a_consumer_that_breaks_early_gets_no_estimate`, `test_an_aborted_or_failed_call_is_not_estimated`.
4. **A hand-mangled `facts.json` `rates`** (one base rate, a string, a negative) leaves that model unpriced, falling back to `pricing.json`. It is never `$0` and never a 500. The editor refuses a partial rate or an unknown rate field with 400 rather than dropping it. → Task 1 `test_a_mangled_rates_entry_reads_as_none`, `test_put_facts_refuses_a_partial_rate`, `test_put_facts_refuses_an_unknown_rate_field`; Task 4 `test_a_mangled_facts_file_costs_the_estimates_not_the_report`.
5. **Zero rates entered for a local model.** The headline reads `≈ $0.00`, not a bare `$0.00` that looks like spend and not "not reported". Both chores clear. A zero-rated local Fast beside a subscription Primary headlines the subscription estimate. → Task 4 `test_zero_rates_model_a_zero_not_unpriced`; Task 5 `test_zero_rates_clear_it`; Task 8 `a stated zero rate reads as an estimate of zero, never a bare $0.00` and `a zero-rated kind beside a subscription estimate headlines the estimate`.
6. **Counting never blocks the loop and never fails a call.** A cold encoder, a raising counter or a malformed message costs the estimate only. → Task 3 `test_counting_runs_off_the_event_loop`, `test_meter_done_never_loads_the_encoder`, `test_a_counter_that_raises_costs_the_estimate_not_the_call`, `test_malformed_messages_never_fail_the_call`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec, `CLAUDE.md` (the `estimated_usd` bullet only) | rulings below folded in | 0 |
| `store/pricing.py` | `entry` (public normaliser), `check_entry`, `provider_rates`, `rate_for_call`: the one precedence | 1 |
| `store/llm_connections.py` | `facts_files()`: each provider's facts file, for `provider_rates` | 1 |
| `store/inference/facts.py`, `routes/models.py` (`FactsUpdate`), `routes/config.py` (facts routes) | `rates` stated per model, read and written | 1 |
| `llm_usage.py` | `ACCOUNT_KEY`, `ACCOUNT_FIELDS`, `account()`, `with_account()` (2); `ESTIMATE_KEY`, `ENDED_KEY`, `ESTIMATED`, `Estimate`, `note_prompt`, `note_reply`, `fill` (3) | 2, 3 |
| `store/inference/resolve.py` | account block on every lowered conn | 2 |
| `llm.py` | `_stamp` files the account and the requested model (2); `LLMClient(count_tokens=)`, `_dispatch`/`_lowered` note the prompt, `_resilient` notes the reply and counts after a natural end (3) | 2, 3 |
| `llm_reasoning.py` | `from_chunk` extracts reasoning without a display buffer; `feed` notes it once | 3 |
| `routes/common.py` (`build_llm`) | passes `count_tokens=store.tokens.count_tokens` | 3 |
| `routes/config.py` (model-test probes) | each probe's row carries its `operation` | 2 |
| `store/usage.py` | `record`/`Meter.done` fields, guarded (2); `Meter.done` reads the flag and drops the private keys (3); provider-aware `Rates`, breakdown counts, `_turn` fields, `unpriced_models` by provider (4) | 2, 3, 4 |
| `store/usage_rollup.py` | fingerprint covers facts rates; new aggregate file `rollup-v3.json`; `_FIELDS` carries the three counts | 4 |
| `backend/tests/llm_fakes.py` | `FakeLLM` stamps the account like `_stamp`; `ScriptedProvider(usage=)` | 2 |
| `store/inference/in_use.py` (new), `store/inference/__init__.py` | every stored selection; which in-use ones nothing prices | 5 |
| `store/inference/settings.py` | `used_by` becomes a filter over `in_use.selections`; `_campaign_meta`/`_text` move to `in_use` | 5 |
| `routes/todo.py` | stale-pricing items keyed by provider (4); the Housekeeping chore and its items (5) | 4, 5 |
| `store/inference/probes.py`, `routes/config.py` (preview) | the test-call estimate falls back to rates | 6 |
| `frontend/src/components/inference/TestCallDialog.tsx` | says when the estimate is from your rates | 6 |
| `frontend/src/components/RateFields.tsx` (new), `PricingEditor.tsx`, `routes/ProvidersView.tsx`, `api/types.ts` | rates on the model facts panel | 7 |
| `frontend/src/components/cost.tsx`, `CostPanel.tsx`, `routes/CostsView.tsx`, `api/types.ts` | subscription and estimated-token labels; the estimate-kind rule | 8 |
| `CLAUDE.md`, module docstrings, `backend/tests/test_docs_guard.py` | Costs paragraph, held to the code | 9 |

---

### Task 0: Settle the spec

**Files:** the spec (§6.4 step 1, §9.1, §9.2, §9.3), and in `CLAUDE.md` the `estimated_usd` bullet of the Costs section only.

- Fold design rulings 1–14 into the sections they touch:
  - §9.3: `provider_id`, `requested_model`, `role` and `preset` (rulings 1, 2, 9, 13).
  - §9.1: rulings 3–8, 11 and 14.
  - §9.2: ruling 10.
  - §6.4: ruling 12.
- **§9.1, the budget sentence (M1).** Replace "they never count against a budget (they never did: only `cost_usd` does)" with ruling 4's rule:
  - a subscription-tagged row counts against a budget only when its provider reported a *billed* price;
  - the tag is a label, and `cost_basis` alone decides the column.
- **§9.2, the file name.** "`chores.py` gains …" becomes "`routes/todo.py` gains …". `store/chores.py` only holds the ignore set.
- **CLAUDE.md, the `estimated_usd` bullet (M1).** Narrow it to what ruling 4 makes true. The figure is a price the provider reported as a subscription equivalent (`cost_basis: equivalent`). A billed price on a subscription-tagged provider stays `cost_usd`. Change nothing else in that section here; Task 9 adds the new paragraph.
- No other edits.

- [ ] **Step 1:** Amend the spec and the bullet, and commit `docs(spec): settle slice E's ledger fields, rate precedence and estimates`.

---

### Task 1: Rates live in model facts, and one function decides precedence

**Files:**
- Modify: `store/pricing.py`, `store/llm_connections.py`, `store/inference/facts.py`, `routes/models.py` (`FactsUpdate`), `routes/config.py` (`put_connection_facts`, `_facts_body`).
- Test: `tests/test_model_rates.py` (new). Also run the existing `tests/test_pricing_store.py`, which must stay green unmodified.

**Interfaces:**

*`pricing.py`:*
- `entry(value: object) -> dict | None` is today's `_entry`, made public. Update its internal callers. It keeps both-base-rates-or-nothing.
- `check_entry(value: object) -> dict` is the editor's strict form. It raises `ValueError` in three cases, checked in this order:
  1. `value` is not a dict, or it names a key outside `pricing.FIELDS`: `ValueError(f"unknown rate field(s): {', '.join(sorted(unknown))}")` (M15).
  2. A present field `_rate` rejects: `ValueError(f"{field} must be a non-negative number")`.
  3. `entry(value)` drops it: `ValueError("rates need both an input and an output rate")`.
  - Otherwise it returns `entry(value)`.
- `provider_rates() -> dict[str, dict[str, dict]]` returns `{provider_id: {model: entry}}`.
  - It covers every provider facts file (`llm_connections.facts_files()`), keeping only usable entries.
  - **It never raises.** An unreadable or mangled file contributes nothing.
  - Each file is memoized on `statcache.signature(path)` in a pool of its own, like `usage._UNPRICED_POOL`. The rail reads this on every navigation through the rollup fingerprint (Task 4).
  - It reads the facts JSON shape itself (`{model: {"rates": …}}`) rather than importing `store.inference.facts` (Global Constraints: runtime cycle). Its docstring says so and names `facts.py` as the writer.
- `rate_for_call(table: dict[str, dict], providers: dict[str, dict[str, dict]], *, provider_id: str = "", model: str = "", requested_model: str = "") -> dict | None`, in this order:
  1. `providers[provider_id][requested_model]`, when both are set;
  2. `providers[provider_id][model]`;
  3. `rate_for(table, model)`: exact, wildcard, then `""`.
  - Facts rates are exact per model and take no wildcards. `pricing.json` keeps matching the *recorded* model, so every existing table prices exactly what it prices today.

*`llm_connections.py`:* `facts_files() -> dict[str, Path]` maps each `safe_id` connection id to its `<id>.facts.json` in `_dir()`. It globs, and reads no connection file. `delete_connection` already unlinks a deleted connection's facts file, so this lists live providers.

*`facts.py`:*
- `state(..., rates: object = None)`:
  - `None` leaves the rates as they are.
  - `{}` removes them.
  - Anything else goes through `pricing.check_entry`. A `ValueError` is raised before the file is touched, like the other fields.
  - **`rates` joins the early-return check.** Today `state` returns without writing when nothing is stated and no override changed (`if not stated and not changed: return`). It now returns only when `rates is None` as well; otherwise a rates-only PUT is a silent no-op.
- `of(...)["rates"]` is `pricing.entry(raw)`: a usable entry, or `None`. Rates are stated facts, so they survive a rev change.

*API:*
- `FactsUpdate.rates: Any = None`.
- `PUT /llm-connections/{conn_id}/facts` passes `rates` to `facts.state`. A partial, invalid or unknown-field rate is a 400 with the store's message. `GET` returns `rates` through `_facts_body` (already spread from `facts.of`).
- Delete the docstring's "Rates are slice E's."

- [ ] **Step 1: Failing tests** (`tests/test_model_rates.py`):
  - `test_facts_rates_round_trip`: `state("saltmarch", "vendor/model-a", rates={"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002})`. Then `of(...)["rates"] == {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}`.
  - `test_a_rates_only_write_is_not_skipped`: a `state(...)` call with `rates` alone (no other field) changes the file.
  - `test_a_partial_rate_is_refused_and_nothing_written`: `{"prompt_usd_per_1k": 0.001}` raises `ValueError`, and the file bytes are unchanged.
  - `test_an_unknown_rate_field_is_refused`: `{"prompt_usd_per_1K": 0.001, "completion_usd_per_1k": 0.002}` raises `ValueError` naming the key, and nothing is written.
  - `test_empty_rates_clear_them`
  - `test_rates_survive_a_rev_change`
  - `test_a_mangled_rates_entry_reads_as_none`: a hand-written `{"prompt_usd_per_1k": "x", "completion_usd_per_1k": 0.002}` and a `-1`. `of(...)["rates"] is None`, and `provider_rates()` omits the model. (Review Focus 4.)
  - `test_provider_rates_never_raises_on_a_mangled_file`: non-JSON, a list, and a non-dict entry each read as `{}` for that provider.
  - `test_provider_rates_sees_a_rates_edit`: written **through `facts.state`**, not by hand, so the writer and this reader are held to one schema. The memo follows the file's signature.
  - `test_rate_for_call_precedence`, one assert per tier, in order:
    1. facts under the requested model;
    2. facts under the model;
    3. table exact;
    4. table `prefix*`;
    5. table `""`;
    6. `None`.
  - `test_zero_rates_are_a_price`: a `{0, 0}` entry is returned. `estimate(entry, prompt_tokens=10, completion_tokens=5) == 0.0`.
  - `test_put_facts_refuses_a_partial_rate`: 400, and `GET` still shows the old rates. (Review Focus 4.)
  - `test_put_facts_refuses_an_unknown_rate_field`: 400. (Review Focus 4.)
  - `test_put_facts_rates_then_get`
  - `test_put_facts_rates_on_an_unknown_provider_is_404`
  - `test_pricing_imports_no_inference`: the module's AST has no import of `store.inference`, and `python -c "import grimoire.store.usage"` succeeds.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_pricing_store.py tests/test_provider_api.py tests/test_import_guard.py tests/test_paths_guard.py tests/test_atomic_guard.py tests/test_pydantic_guard.py`, then `make check-lint check-mypy`.
- [ ] **Step 5: Commit** `feat(pricing): a model's own rates, stated on its provider, outrank the pricing table`

---

### Task 2: Every ledger row says what served it

**Files:**
- Modify: `llm_usage.py`, `llm.py` (`_stamp`), `store/inference/resolve.py`, `store/usage.py` (`record`, `Meter.done`), `routes/config.py` (the two model-test probe meters), `backend/tests/llm_fakes.py` (`FakeLLM.stream`'s stamp; `ScriptedProvider(usage=)`).
- Test: `tests/test_usage_fields.py` (new); additions to `tests/test_model_test_call.py`.
- Test change, named in the commit body: `tests/test_inference_resolve.py` (~277). Its whole-dict comparison excludes `resolve.ACCOUNT_KEY` beside `FALLBACK_KEY`. Do the same for any other test that compares a whole lowered dict, and list each one.

**Interfaces:**

*`llm_usage.py` (gateway leaf):*
- `ACCOUNT_KEY = "_account"`.
- `ACCOUNT_FIELDS = ("operation", "role", "billing", "decision_mode")`.
- `account(usage: dict | None, conn: dict) -> None`. It copies into the holder:
  - `provider_id` ← `conn["id"]`;
  - `preset` ← `conn["sampling"]["preset_id"]`;
  - each `ACCOUNT_FIELDS` key of `conn[ACCOUNT_KEY]`.
  - Each value is copied only when it is a non-empty `str`.
  - It reads `conn` and never writes it. It never raises.
- `with_account(conn: dict, **fields: str) -> dict` returns `{**conn, ACCOUNT_KEY: {**(conn.get(ACCOUNT_KEY) or {}), **fields}}`.
  - It is the one way to change an account block.
  - It never mutates `conn` or the block `conn` carries.
  - Slice F uses it to stamp `decision_mode` per call; this task uses it for the probe's `operation`.
- `decision_mode` is listed in `ACCOUNT_FIELDS` now, so F files it through `with_account` with no change here.

*`resolve.py`:*
- `ACCOUNT_KEY` is restated, and a test holds the two spellings equal.
- `_lowered` sets `out[ACCOUNT_KEY] = {"billing": providers.billing(out)}` on every lowered dict. So `lower()`'s callers carry billing too (the model test, `controls.preview`).
- `resolve()` then **replaces** each attempt's block with a fresh dict carrying `operation` and, when stamped, `role`. It never calls `update()` on a block. It does this **before** attaching the fallback, so the attached dict is the stamped one.
- **`role`** is `choice.role`, on both attempts, when non-empty. It is omitted in two cases:
  - a pin (`choice.role` is empty; ruling 9);
  - **an override that changed the provider or the model** (M2). Test this by comparing `_overridden`'s selection with `choice.selection` on `(provider, model)`. A preset-only override keeps the role.

*`llm._stamp`:* after its `usage.update`, it sets `usage["requested_model"] = effective_model(conn)` and calls `llm_usage.account(usage, conn)`. Each attempt is stamped from its own dict, so a fallback, a degrade sibling or a retry describes itself. `_stamp` clears the holder first, as today.

*`preset` (ruling 13, M3):* the ledger's `preset` is the sampler preset actually sent. That is `conn["sampling"]["preset_id"]` on the dict `_resilient` attempts, which for a fallback is after `fallback_sampling`. It is never the §6.1 provider preset (`provider_preset`).

*`store/usage.py`:*
- `record(...)`:
  - **`operation: str = ""` is slice D's parameter** (D-a). It goes first after `images`, and `Meter.done` passes `self.usage.get("operation", "")` through.
    - If D has landed, it is already there.
    - If E lands first, E adds it exactly that way, and D's plan drops it.
  - E appends, after `operation`, as its own block: `provider_id: str = ""`, `requested_model: str = ""`, `role: str = ""`, `preset: str = ""`, `billing: str = ""`, `decision_mode: str = ""`, `tokens_estimated: bool = False`.
  - Strings are written only when non-empty, in the existing identity loop.
  - `requested_model` is written only when it differs from `model`.
  - `tokens_estimated: true` is written only when true.
- `Meter.done` gathers each new field from `self.usage` (`str` values only, else `""`) and passes it. `tokens_estimated` is `False` until Task 3.
  - **The gathering is inside `try/except Exception` (I3).** On any failure it files the row with the new fields at their defaults. Existing reads are unchanged.
- **`Meter.__init__` and `meter()` do not change.**

*Model-test probes (`routes/config.py`, M9):*
- The chat probe sends `llm_usage.with_account(conn, operation=probe.operation)` to `client.single`. `conn` is `inference.lower(...)`'s, so it already carries billing.
- `_embed_probe` is handed the probe's lowered `conn` too. Beside its hand stamp it calls `llm_usage.account(m.usage, llm_usage.with_account(conn, operation="embed"))`. Slice D may rewrite this probe's metering; see Coordination.

*`FakeLLM.stream`:* its stamp also sets `requested_model` and calls `llm_usage.account(usage, conn)`, so route tests see what the real facade files. It does **not** count (Task 3 says why).
- `ModelessHolder`, `HeldOpenRouter` and `HeldCassette` need no change (M16): each delegates to `super().stream`, so `FakeLLM`'s stamp already runs for them.

*`ScriptedProvider(chunks, error=None, usage: dict | None = None)`:* after its last chunk (and only if it raised nothing), it merges `usage` into the `usage=` holder it was handed, as a provider's final frame would. No new fake.

- [ ] **Step 1: Failing tests** (`tests/test_usage_fields.py` unless noted):
  - `test_record_writes_the_new_fields_only_when_set`: a call with defaults writes none of the new keys (D's `operation` included when E adds it).
  - `test_requested_model_is_written_only_when_it_differs`
  - `test_a_resolved_call_files_operation_role_provider_preset_and_billing`.
    - Setup: a format-2 store whose Primary is an OpenAI-preset provider with preset `p1`; a real `LLMClient(openai_compatible=ScriptedProvider(...))`; `require_inference("chat", "")`; `usage.meter("chat")` around `client.complete`.
    - The row has `operation == "generate"`, `role == "primary"`, `provider_id == <id>`, `preset == "p1"` and `billing == "metered"`.
  - `test_the_fallback_row_names_the_fallbacks_provider_and_billing` (Review Focus 1).
    - Setup: the Primary is an OpenAI-preset provider on `vendor/model-a`, and its fallback is a z.ai Coding Plan provider on `vendor/model-b`. Both are `openai_compatible`, so one `RefusingProvider(failing={"vendor/model-a"})` (`llm_fakes`) serves both and refuses the primary. Its defaults carry no status, so no preset refusal stops the fallback.
    - The row has `provider_id == <fallback id>`, `billing == "subscription"` and `attempts == 2`.
  - `test_a_fallback_row_files_the_route_preset_it_was_sent` (M3): a route-scoped preset follows the primary onto the fallback (`fallback_sampling`), and the row's `preset` is that route preset.
  - `test_a_dated_snapshot_reply_keeps_the_requested_model` (Review Focus 2): `ScriptedProvider(usage={"model": "vendor/model-a-2026-08"})` through the real facade. The row has `model == "vendor/model-a-2026-08"` and `requested_model == "vendor/model-a"`.
  - `test_a_pinned_route_files_no_role`
  - `test_an_override_that_changes_the_model_files_no_role` (M2), and `test_a_preset_only_override_keeps_the_role`.
  - `test_a_lowered_conn_carries_billing_without_a_resolution`: `resolve.lower(raw, NO_SAMPLING)[ACCOUNT_KEY] == {"billing": "metered"}`.
  - `test_a_decision_mode_in_the_account_is_filed`: `with_account(conn, decision_mode="structured")` through the facade files `decision_mode == "structured"`.
  - `test_account_blocks_are_never_mutated_in_place` (I6). It checks three things:
    1. after `resolve()`, the primary's and the fallback's blocks are distinct objects;
    2. `with_account(conn, decision_mode="native")` leaves `conn`'s block, and the block of a shallow copy `{**conn}`, unchanged, and returns a new block;
    3. `_stamp` and `account` leave the conn they read equal to a deep copy taken before.
  - `test_meter_done_survives_a_malformed_holder` (I3): holder values `provider_id=object()`, `billing=3`, `role=None`. The row is written, without those fields.
  - `test_account_key_is_one_spelling`: `resolve.ACCOUNT_KEY == llm_usage.ACCOUNT_KEY`.
  - `tests/test_model_test_call.py::test_a_model_test_row_carries_its_probe_operation` (M9): a generate probe files `operation == "generate"`; an embed probe files `operation == "embed"`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_usage_store.py tests/test_usage_routes.py tests/test_llm.py tests/test_llm_fakes.py tests/test_inference_resolve.py tests/test_inference_fallback.py tests/test_inference_override.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_model_test_call.py tests/test_import_guard.py`, then `make check-lint check-mypy`.
- [ ] **Step 5: Commit** `feat(usage): each ledger row names its provider, preset, role, operation and billing`

---

### Task 3: Count locally what a provider did not, and say so

The count moves into the facade (C1, I1). `_resilient` is the one place that knows an attempt's stream ended on its own, and it is already async, so it can hand the count to a worker thread. `Meter.done` is synchronous and runs on the event loop and on worker threads alike, so it only reads numbers.

**Files:**
- Modify: `llm_usage.py`, `llm.py` (`LLMClient.__init__`, `stream`, `single`, `_dispatch`, `_lowered`, `_resilient`), `llm_reasoning.py` (`from_chunk`, `feed`), `routes/common.py` (`build_llm`), `store/usage.py` (`Meter.done`).
- Test: `tests/test_usage_estimate.py` (new).

**Interfaces:**

*`llm_usage.py`:*
- `ESTIMATE_KEY = "_estimate"`: the attempt's `Estimate`.
- `ENDED_KEY = "_ended"`: `True` when the attempt's stream ended on its own.
- `ESTIMATED = "tokens_estimated"`: the holder key, spelled as the ledger field.
- `class Estimate`:
  - `__init__(self, messages: list)` keeps a **reference**, not a copy.
  - `add(self, text: str) -> None`.
  - `prompt_text(self) -> str` joins every `str` content and every `{"type": "text"}` part whose `text` is a `str`. It skips anything else without raising: a non-dict message, a `None` content, a non-dict part, a non-`str` text, and image parts.
  - `completion_text(self) -> str`.
  - `count(self, counter, *, completion: bool) -> tuple[int, int | None]` returns `(counter(prompt_text()), counter(completion_text()) if completion else None)`. It is pure and runs on a worker thread. It may raise; the caller guards it.
- `note_prompt(usage: dict | None, messages: list) -> None` installs a fresh `Estimate`, replacing any.
- `note_reply(usage: dict | None, text: str) -> None` adds to the installed one. A missing holder or estimate, or a non-`str`/empty text, is a no-op.
- `fill(usage: dict, prompt: int, completion: int | None) -> None` runs on the loop.
  - It writes each count only when the holder has none (`tokens(usage.get(k)) is None`). A reported count is never replaced.
  - It **never writes `completion_tokens` when `usage.get("operation") == "embed"`** (I6, ruling 8).
  - It sets `usage[ESTIMATED] = True` only when it wrote at least one count.

*`llm.py`:*
- `LLMClient.__init__(..., count_tokens=None)` is appended **last**, so no positional caller shifts. It is a `str -> int` callable, the `images=` / `load_image=` precedent. With `None`, nothing is counted: a hand-built client and every existing suite behave as today.
- `stream` and `single` pass `counter=self._count_tokens` to `_resilient`.
- `_dispatch` calls `llm_usage.note_prompt(usage, messages)` after the per-attempt `for_connection` selection. `_stamp` has already cleared the holder, so each attempt counts only itself.
- `_lowered` calls `note_prompt` again with the lowered messages after `_lower` (M10), so a text-lowered route counts the text it sent.
- `_resilient(..., counter=None)`:
  - It calls `llm_usage.note_reply(usage, chunk)` for each non-empty chunk it yields.
  - **After the attempt's `async for` ends on its own** (where `outcome = "complete"` is set), and before it returns, it does three things when it has a holder:
    1. It sets `usage[ENDED_KEY] = True`.
    2. If `counter` is set, an `Estimate` is installed, and a count is missing (completion counts only when the operation is not `embed`), it runs `await asyncio.wait_for(asyncio.to_thread(estimate.count, counter, completion=...), COUNT_TIMEOUT_S)`.
    3. On success it calls `llm_usage.fill(usage, p, c)`.
  - The count sits in its own `try/except Exception`, apart from the `except LLMError` handler. A raise or a timeout logs one warning and leaves the holder without counts. The reply is never failed by it.
  - **A consumer that breaks out early never reaches this point** (I1). Neither does a cancelled or failed attempt. So none of them gets `ENDED_KEY` or an estimate.
  - The attempt's `finally` pops `ESTIMATE_KEY`, so the prompt reference does not outlive the attempt.
- `COUNT_TIMEOUT_S = 5.0`, with this reason in a comment:
  - a warm count is far below this bound;
  - a cold count is an encoder download, and the reply's end must not wait on it;
  - a timed-out thread finishes on its own, and the loader memoizes the result.

*`llm_reasoning.py` (I2):*
- `from_chunk` returns early only when `usage is None` or `obj` is not a dict. It no longer returns when no display `Buffer` is installed. It extracts the reasoning text exactly as today and calls `feed(usage, text)`.
- `feed` calls `llm_usage.note_reply(usage, text)` first, then appends to the `Buffer` if one is installed.
- So reasoning is noted exactly once per text, with or without a display consumer. The direct callers (`anthropic`, `claude_agent`) keep calling `feed`. Reasoning is billed as completion.

*`routes/common.build_llm`:* passes `count_tokens=_count_tokens`. That is a module-level late-bound wrapper over `store.tokens.count_tokens`, like `_post_images_for`, so a test patching `store.tokens` intercepts it.

*`store/usage.py`:*
- `ESTIMATE_KEY`, `ENDED_KEY` and `ESTIMATED` are restated, and a test holds them equal.
- `Meter.done` passes `tokens_estimated = usage.get(ESTIMATED) is True and usage.get(ENDED_KEY) is True`, inside Task 2's guarded gathering.
- After filing, it pops `ESTIMATE_KEY` and `ENDED_KEY` from the holder (I3). A detached run's meter can outlive the call, and an estimate left by an early break would pin the whole prompt.
- **It counts nothing and imports no counter.** `store/usage.py` does not import `store.tokens`.
- The module docstring says that images are not estimated: there is no portable per-image count, and the row's `images` already says they rode along.

*Fakes:* `FakeLLM` replaces the whole `LLMClient`, so it counts nothing. Its default answers "an endpoint that reports no usage", and existing route tests assert the counts absent. The estimate is proven against the real facade here.

- [ ] **Step 1: Failing tests** (`tests/test_usage_estimate.py`: a real `LLMClient(..., count_tokens=tokens.count_tokens)` with adapter stubs, unless noted):
  - `test_a_provider_that_reports_no_counts_gets_local_counts_and_the_flag`. The row has:
    - `prompt_tokens == tokens.count_tokens(<the prompt's text>)`;
    - `completion_tokens == tokens.count_tokens("".join(chunks))`;
    - `tokens_estimated is True`.
  - `test_reported_counts_are_never_replaced`: `ScriptedProvider(usage={"prompt_tokens": 7, "completion_tokens": 3})`. No flag, and the counts are 7 and 3.
  - `test_only_the_missing_count_is_estimated`
  - `test_a_consumer_that_breaks_early_gets_no_estimate` (I1, Review Focus 3). The consumer breaks after the first chunk and then calls `m.done()` (status `ok`). There are two providers:
    - one reporting no usage;
    - `ScriptedProvider(usage={...})`, whose trailing usage the break skips.
    - Both rows have no `prompt_tokens`, no `completion_tokens` and no flag, and the holder holds no `ESTIMATE_KEY` after `done`.
  - `test_an_aborted_or_failed_call_is_not_estimated` (Review Focus 3). It covers:
    - a stream the caller closes after one chunk (`aclose()`, then `m.done("aborted")`);
    - a `ScriptedProvider(error=LLMError(...))` under `with meter`;
    - the early break above.
    - Every row has no counts and no flag.
  - `test_counting_runs_off_the_event_loop` (C1): a counter that records `threading.get_ident()`. Every recorded id differs from the loop thread's.
  - `test_meter_done_never_loads_the_encoder` (C1): patch `tokens._loader.get` to record its caller's thread and raise. A completed estimate-eligible call still files its row, with heuristic counts and the flag. No recorded call came from the loop thread. Then patch `count_tokens` to raise and call `Meter.done` on a holder carrying an `Estimate`: the row is filed and the patch never fires.
  - `test_a_counter_that_raises_costs_the_estimate_not_the_call`: the call returns its text, the row is filed, and it has no counts and no flag.
  - `test_a_slow_count_costs_the_estimate_not_the_reply`: `COUNT_TIMEOUT_S` patched small and a counter that sleeps past it. Same outcome.
  - `test_malformed_messages_never_fail_the_call` (I3): one call per shape, each completing with its row written:
    - `content: None`;
    - a parts list holding a non-dict;
    - a text part whose `text` is not a `str`.
  - `test_the_estimate_is_dropped_from_the_holder` (I3): after a natural end the facade has already popped `ESTIMATE_KEY`. After `m.done()`, neither `ESTIMATE_KEY` nor `ENDED_KEY` is in `m.usage`.
  - `test_image_parts_are_not_counted_as_text`
  - `test_a_text_lowered_prompt_counts_what_was_sent` (M10)
  - `test_a_retried_attempt_counts_only_the_attempt_that_answered`
  - `test_reasoning_counts_toward_completion_without_a_display_buffer` (I2). It runs through `OpenAICompatibleClient(http=httpx.AsyncClient(transport=httpx.MockTransport(...)))`. The transport streams `reasoning_content` deltas, then prose, with no usage block, and no `Buffer` is installed. `completion_tokens` equals the count of reasoning plus prose.
  - `test_reasoning_is_counted_once_with_a_display_buffer`: the same body through `llm_reasoning.stream` gives the same `completion_tokens`.
  - `test_an_embed_operation_never_gets_a_completion_estimate` (I6): a conn whose account block says `operation: "embed"`. The prompt is filled; `completion_tokens` stays absent. A direct `fill(usage, 5, 3)` on a holder with `operation: "embed"` also writes no completion.
  - `test_a_client_without_a_counter_estimates_nothing`: `LLMClient(...)` with no `count_tokens` files no counts and no flag.
  - `test_usage_imports_no_counter`: the AST of `store/usage.py` has no import of `tokens`.
  - `test_estimate_keys_are_one_spelling`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_llm.py tests/test_reasoning_display.py tests/test_anthropic.py tests/test_openai_compatible.py tests/test_incoming_capture.py tests/test_usage_store.py tests/test_usage_routes.py tests/test_post_images_usage.py tests/test_import_guard.py`, then `make check-lint check-mypy`.
- [ ] **Step 5: Commit** `feat(usage): count locally what a provider did not, off the loop, and flag the row`

---

### Task 4: Rollups price by provider, and say what they estimated

**Files:**
- Modify: `store/usage.py` (`Rates`, `_add`, `_turn`, `unpriced_models`, `_month_unpriced`), `store/usage_rollup.py`, `routes/todo.py` (`_items_unpriced`, `_chore_unpriced`).
- Test: `tests/test_usage_pricing.py` (new). Additions to `tests/test_usage_rollup_store.py` and `tests/test_todo_route.py`.
- Test change, named in the commit body: `tests/test_usage_rollup_store.py::test_rollup_json_is_not_mistaken_for_a_ledger_month` asserts the file name `rollup.json`. It now asserts `rollup-v3.json`.

**Interfaces:**

*`usage.Rates`:*
- `Rates(table, providers: dict | None = None)`.
- `current()` reads `pricing.read_pricing()` and `pricing.provider_rates()` once. `off()` is both empty.
- `entry(model, *, provider_id: object = "", requested_model: object = "") -> dict | None` is memoized on the triple and answered by `pricing.rate_for_call`. A row with no `provider_id` (every row before this slice) prices by `pricing.json` alone (ruling 3).
- `estimate(row)`:
  - passes the row's `provider_id` and `requested_model`;
  - **an `operation: "embed"` row with a prompt count and no completion count is priced with completion 0** (ruling 8);
  - otherwise both counts are required, as today.

*`_add`:*
- Three **breakdown** counts, never money. Each is added lazily, like `images` (not in `_ZERO`):
  - `estimated_token_calls`: a row with `tokens_estimated is True`;
  - `modelled_subscription_calls`: a row that landed in `modelled_usd` with `billing == "subscription"`;
  - `unpriced_subscription_calls`: a row that landed in `unpriced_calls` with `billing == "subscription"` (M13).
- The unmetered test applies ruling 8's embed rule too.
- `cost_basis` alone decides `cost_usd` versus `estimated_usd`. A billed price on a subscription-tagged provider stays spend (ruling 4).

*`_turn`:* gains `"billing"` (the row's string, else `""`) and `"tokens_estimated"` (`row.get("tokens_estimated") is True`).

*`usage_rollup` (M8):*
- `VERSION = 3`, and the aggregate moves to `rollup-v3.json`.
  - `rollup.json` is left alone: it is never read, written or deleted.
  - Two builds sharing a synced library then each keep their own aggregate, instead of discarding each other's and rebuilding all history on every navigation.
  - `_month_files` already ignores any name that is not a month.
- `_rates_fingerprint` digests `{"table": read_pricing(), "providers": provider_rates()}`. A rates edit on a model discards the aggregate, as a table edit does.
- `_FIELDS` gains all three counts.

*`unpriced_models(months)`:*
- `_month_unpriced` memoizes `((provider_id, requested_model or model, model), n)` pairs.
- The judgement, still applied after the memo, is `rate_for_call(table, providers, ...) is None`.
- It returns `[{"model", "facts_model", "provider_id", "calls"}]`. `model` is the recorded string, which `pricing.json` must match; `facts_model` is the key a model's rates are stated under.

*`routes/todo.py` (M6):*
- `_items_unpriced`:
  - the item id is `f"{provider_id}:{model}"`, so one model on two providers gives two distinct ids;
  - an item links to `/providers/<id>/models/<facts_model>?edit=rates` when its `provider_id` names a provider that exists, else to `/config?section=pricing`. Each model segment is encoded as `ProvidersView.modelPath` encodes it.
- `_chore_unpriced`:
  - its `names` text lists each distinct `model` string once, in first-seen order;
  - its `why` says that either a model's own rates or the table can price these.

- [ ] **Step 1: Failing tests** (`tests/test_usage_pricing.py` unless noted). Rows are written with `usage.record`:
  - `test_facts_rates_price_before_the_pricing_table`: the facts rate 0.001/0.002 wins over a table entry 0.01/0.02.
  - `test_a_dated_snapshot_is_priced_by_the_requested_models_rates` (Review Focus 2): `model="gpt-4o-2024-08-06"`, `requested_model="gpt-4o"`, rates on `gpt-4o`.
  - `test_a_row_without_provider_id_uses_only_the_pricing_table`
  - `test_a_billed_price_on_a_subscription_provider_stays_spend`
  - `test_subscription_modelled_rows_are_counted_and_never_budgeted`: `modelled_subscription_calls == 1`, and `budget(...)["spent_usd"]` is unchanged by them.
  - `test_unpriced_subscription_rows_are_counted` (M13)
  - `test_estimated_token_rows_are_counted`
  - `test_an_embed_row_with_no_completion_count_is_priced`
  - `test_an_account_only_holder_files_the_meters_model` (I6): a `Meter(..., model="vendor/embed-a")` whose holder only `account()` and a prompt count filled files `model == "vendor/embed-a"` and is priced at that model's facts rates.
  - `test_an_embed_row_with_a_campaign_reaches_its_budget` (I6): an embed row with `campaign` and `cost_usd` counts in `budget(cid)`.
  - `test_a_generate_row_with_one_count_stays_unpriced`: today's rule, unchanged.
  - `test_turn_rows_carry_billing_and_the_estimate_flag`
  - `test_zero_rates_model_a_zero_not_unpriced` (Review Focus 5): `modelled_calls == 1`, `modelled_usd == 0.0` and `unpriced_calls == 0`.
  - `test_a_mangled_facts_file_costs_the_estimates_not_the_report` (Review Focus 4)
  - `test_unpriced_models_judges_with_model_rates`
  - `tests/test_usage_rollup_store.py::test_editing_model_rates_reprices_the_aggregate`
  - `tests/test_usage_rollup_store.py::test_the_breakdown_counts_reach_campaign_totals`
  - `tests/test_usage_rollup_store.py::test_an_older_builds_rollup_is_left_alone` (M8): a `rollup.json` written with `version: 2` has the same bytes after a read and a rebuild.
  - `tests/test_todo_route.py::test_an_unpriced_item_opens_the_models_rates`, plus one for a model id containing `/`.
  - `tests/test_todo_route.py::test_an_unpriced_item_without_a_provider_opens_the_pricing_table`
  - `tests/test_todo_route.py::test_one_model_on_two_providers_is_named_once_with_distinct_item_ids` (M6)
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_usage_store.py tests/test_usage_routes.py tests/test_usage_prefilter.py tests/test_usage_rollup_store.py tests/test_pricing_store.py tests/test_todo_route.py tests/test_continuity_read_cost.py`, then `make check-lint check-mypy`.
- [ ] **Step 5: Commit** `feat(usage): price each call at its own model's rates, and count what was estimated`

---

### Task 5: The Housekeeping chore — models in use with no price

**Files:**
- Create: `store/inference/in_use.py`.
- Modify:
  - `store/inference/__init__.py`: register `in_use`, so `store.inference.in_use` resolves from `routes/todo.py` (M12).
  - `store/inference/settings.py`: `used_by`; delete `_uses`; `_campaign_meta` and `_text` move to `in_use`.
  - `routes/todo.py`: the builder, its items, `LIBRARY_BUILDERS`, `ITEMS`.
  - `store/locks.py`, if `test_lock_domain_guard` names the module (`OUTSIDE_DOMAIN`, "read-only").
- Test: `tests/test_unpriced_in_use.py` (new), plus additions to `tests/test_todo_route.py`.

**Interfaces:**

*`in_use.py`* (imports `config`, `inference_keys`, `llm_connections`, `pricing`, `routing`, `statcache`, `campaigns.paths`/`campaigns.read`, and `.facts`, `.providers`, `.resolve`, `.translate`; **never `.settings`**):
- `campaign_meta(cid, *, strict=True) -> dict` and `text(view, key) -> str` are moved from `settings` (`_campaign_meta`, `_text`), unchanged in behaviour. `settings` calls them through the module.
- **`campaign_meta` memoizes the frontmatter parse only** (I5). The key is `statcache.signature(campaign.md)`, in a pool of its own, and callers get a shallow copy. Nothing derived from other files is memoized here.
- `class Use(NamedTuple): provider: str; model: str; kind: str; key: str; scope: str; cid: str`.
  - `kind` is `"role" | "fallback" | "route"`.
  - `key` is the role or route key, with `"embedding"` for the Embedding role.
  - `cid` is `""` at global scope.
  - `model` is the stored model as the translation reads it. It may be `""`.
- `selections() -> list[Use]` returns **every stored selection that names a provider**, in exactly the order `settings.used_by` walks today (M12):
  - global generative roles and their fallbacks;
  - the Embedding role (`translate.embedding_role(cfg)`, which D keeps as a wrapper; D-b);
  - chosen pins (`use_<k> == keys.PIN`);
  - each campaign's roles, fallbacks and chosen campaign-scoped pins.
  - It reads through the translation, so a legacy store reports what plays.
  - It keeps a selection with a blank model, or one whose provider no longer exists, as `used_by` lists it today.
  - **The translation, the connection lookup and the format marker are read fresh on every call** (I5). They are in memory, cheap, and come from files other than `campaign.md`.
  - A campaign that cannot be read names nothing. That is `used_by`'s rule today: `CampaignNotFound`, `OSError`, `UnicodeDecodeError`, `ValueError`.
  - Its docstring reconciles the cost with the "detail only" comment at `routes/config.py` `get_connection`. It is one parse per changed `campaign.md` plus in-memory work per campaign, the class `live("")` already pays.
- `unpriced() -> list[dict]` returns `[{"provider_id", "provider_name", "model", "uses": [{"kind", "key", "scope", "cid"?}]}]`, sorted by provider name then model. **It does the filtering `selections()` does not** (M12). It covers the distinct `(provider, facts model)` pairs that meet all four conditions:
  - the provider exists;
  - the model is non-empty after `facts.model_of({"kind": <provider kind>, "model": <stored model>})` (an unset Claude model reads `opus`);
  - the provider preset does not report prices (`providers.infer(conn).reports_price` is False);
  - **and** `pricing.rate_for_call(read_pricing(), provider_rates(), provider_id=..., model=...)` is `None`.
  - It is computed from configuration only and never reads the ledger (§9.2).

*`settings.used_by(provider_id)`:* returns `[{kind, key, scope, cid?} for u in in_use.selections() if u.provider == provider_id]`. The shape and order are unchanged, and its existing tests stay green unmodified.

*`routes/todo.py`:*
- `_chore_unpriced_models(ctx)` returns `None` when the list is empty, or when the walk raises `OSError`, `UnicodeDecodeError`, `ValueError` or `store.locks.StoreBusy` (M11). `config.read_config()` and the lookup can raise any of them, and one unreadable `config.md` must not make `/todo` a 500. Otherwise it returns:
  - `{"id": "unpriced-models", "scope": "library", "group": "Housekeeping", "severity": "note", "n": n,`
  - `"what": f"{n} model{'s' if n != 1 else ''} in use with no price",`
  - `"why": "Their provider reports no price, and neither the model's own rates nor your pricing table covers them, so their calls are counted rather than costed. A local model is free only once you enter zero rates for it.",`
  - `"fix": <first item's fix>, "fix_label": "Set rates"}`
- `_items_unpriced_models(cid)` catches the same four exceptions (answering `[]`) and returns one item per pair:
  - `{"id": f"{provider_id}:{model}", "label": f"{model} on {provider_name}",`
  - `"detail": "Used by " + ", ".join(<role/route labels, campaign names where scoped>),`
  - `"fix": f"/providers/{id}/models/{model}?edit=rates"}`, each segment encoded.
- Register it in `LIBRARY_BUILDERS` after `unpriced`, and in `ITEMS`. `KNOWN` and `LIBRARY_IDS` follow by derivation.

- [ ] **Step 1: Failing tests** (`tests/test_unpriced_in_use.py` unless noted):
  - `test_counts_distinct_selections_across_roles_fallbacks_pins_and_campaigns`: Primary and a campaign's Fast on the same pair give 1.
  - `test_a_provider_that_reports_prices_is_not_counted` (OpenRouter, Claude subscription)
  - `test_rates_in_facts_or_the_pricing_table_clear_it`: facts rates, a table wildcard, and a table `""`.
  - `test_zero_rates_clear_it` (Review Focus 5)
  - `test_a_dangling_provider_is_not_counted`, and `test_a_deleted_provider_leaves_the_count_on_the_next_read` (I5): a first read counts it; delete the provider; the next read does not.
  - `test_a_legacy_connection_model_edit_is_reflected` (I5): format 1, a campaign route override on a connection. Edit that connection's model between two reads; the second read names the new model.
  - `test_selections_keep_a_role_with_a_blank_model` (M12): `used_by` still lists it, and `unpriced()` does not count it.
  - `test_an_unchosen_pin_is_not_in_use`
  - `test_the_embedding_role_counts`
  - `test_a_legacy_store_reads_through_the_translation`: format 1, an active `openai_compatible` connection.
  - `test_reads_no_ledger`: `usage._read_rows` and `usage.unpriced_models` are monkeypatched to raise, and the walk still answers.
  - `test_a_campaign_parse_is_memoized_until_its_file_changes`: the parse runs once across two reads, and again after `campaign.md` changes.
  - `test_in_use_imports_no_settings`: the module's AST has no import of `.settings`.
  - `tests/test_todo_route.py::test_the_models_chore_sits_in_housekeeping_and_opens_rates`
  - `tests/test_todo_route.py::test_the_models_chore_lists_each_pair`
  - `tests/test_todo_route.py::test_the_models_chore_can_be_ignored`
  - `tests/test_todo_route.py::test_an_unreadable_config_drops_the_models_chore_not_the_page` (M11): `config.read_config` patched to raise `UnicodeDecodeError`, then `StoreBusy`. `/todo` answers 200 without the chore.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_inference_settings.py tests/test_provider_api.py tests/test_todo_route.py tests/test_lock_domain_guard.py tests/test_import_guard.py tests/test_routing_guard.py`, then `make check-lint check-mypy`.
- [ ] **Step 5: Commit** `feat(todo): name the models in use that nothing prices`

---

### Task 6: The test-call estimate joins the user's rates

**Files:**
- Modify: `store/inference/probes.py`, `routes/config.py` (`post_connection_test_preview`), `frontend/src/api/types.ts` (`ModelTestPreview`), `frontend/src/components/inference/TestCallDialog.tsx`.
- Tests: `tests/test_model_test_call.py` (additions), `TestCallDialog.test.tsx`.

**Interfaces:**
- `probes.estimate_from_rates(entry: dict | None, caps: Iterable[str]) -> float | None` is the sum over the probes of `pricing.estimate(entry, prompt_tokens=probe.prompt_tokens, completion_tokens=probe.completion_tokens)`. It is `None` when any one is `None`. The vision probe is priced from its token guess, because user rates price image input as prompt tokens, as the ledger does (ruling 12).
- The preview:
  1. The catalog estimate comes first; `estimate_basis: "catalog"`.
  2. Otherwise it uses `rate_for_call(read_pricing(), provider_rates(), provider_id=conn_id, model=model)` and `estimate_from_rates`; `estimate_basis: "rates"`.
  3. Otherwise `estimated_cost_usd: null`, `estimate_basis: null`.
- `TestCallDialog` renders `Estimated cost: ≈ $X at your rates` for `rates`. The catalog and null renderings are unchanged (`COST_UNKNOWN`).

- [ ] **Step 1: Failing tests:**
  - `test_preview_prices_from_model_rates_when_the_catalog_states_none`
  - `test_a_catalog_price_outranks_rates`
  - `test_no_price_anywhere_is_null`
  - `TestCallDialog says when the estimate comes from your rates`
  - The existing `never shows a zero cost for an unknown price` stays green.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `tests/test_model_test_call.py tests/test_import_guard.py`, then `npx vitest run src/components/inference/TestCallDialog.test.tsx`, then `make check-lint check-mypy check-eslint` and tsc.
- [ ] **Step 5: Commit** `feat(models): the test-call estimate uses your rates when the catalog names no price`

---

### Task 7: Rates on the model facts panel

**Files:**
- Create: `frontend/src/components/RateFields.tsx` (+ test).
- Modify: `frontend/src/components/PricingEditor.tsx`, `frontend/src/routes/ProvidersView.tsx` (`ModelFactsPanel`), `frontend/src/api/types.ts`.
- Tests: `RateFields.test.tsx`, `ProvidersView.test.tsx`. `PricingEditor.test.tsx` stays green unmodified.

**Interfaces:**
- `RateFields({ value, onChange, idPrefix }: { value: Record<keyof PricingEntry, string>; onChange: (next: Record<keyof PricingEntry, string>) => void; idPrefix: string })`.
  - It renders the four boxes in `PricingEditor`'s order, with the labels and the per-million hint.
  - It is extracted from `PricingEditor`, which then uses it, along with the helpers `perMillion` and `complete`.
- Types:
  - `ModelFacts.rates: PricingEntry | null`.
  - `ModelFactsUpdate.rates?: PricingEntry | Record<string, never>`.

*`ModelFactsPanel`:*
- **View.** A `side-section` titled "Rates":
  - when stated, one non-clickable `chip on` per stated rate (`Input $0.001 / 1K`);
  - otherwise `field-hint` "None stated — your pricing table is used if it covers this model." (M14).
- **Edit.** A "Rates" block with `RateFields`.
  - Save sends `rates` only when the boxes changed: all empty sends `{}`, otherwise numbers.
  - **Save is disabled whenever any rate box is filled but the two base rates are not both filled** (M14), with the hint "Input and output are both needed." This covers one base rate alone and cache rates alone.
  - A 400 shows through `ErrorNote`.
- **URL.** `?edit=rates` opens the panel in edit mode with focus on the Input box. It is how both chores link here. **Save and Cancel both clear `edit` from the URL** (M14, `replace: true`), so a refresh does not reopen the form.

*`PricingEditor` copy:* one sentence in its intro: "A model's own rates, set on its provider's page, are used before this table."

- [ ] **Step 1: Failing tests:**
  - `RateFields shows the per-million figure`
  - `a model's rates show in its sidebar`
  - `a model with no rates says the pricing table is used if it covers the model`
  - `rates are read-only until Edit, and Save sends them`
  - `clearing every rate sends an empty object`
  - `a half-filled rate cannot be saved`
  - `cache rates alone cannot be saved`
  - `?edit=rates opens the form on the rates`
  - `?edit=rates is cleared on Save and on Cancel`
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `npx vitest run src/components/PricingEditor.test.tsx src/routes/ProvidersView.test.tsx`, then `make check-eslint` and tsc.
- [ ] **Step 5: Commit** `feat(web): state a model's rates on its provider page`

---

### Task 8: Cost surfaces label subscription calls and estimated tokens

**Files:**
- Modify: `frontend/src/components/cost.tsx`, `components/CostPanel.tsx`, `routes/CostsView.tsx`, `api/types.ts`.
- Tests: `cost.test.tsx`, `CostPanel.test.tsx`, `CostsView.test.tsx`.

**Interfaces:**

*Types:*
- `UsageBucket` gains optional `modelled_subscription_calls?`, `unpriced_subscription_calls?` and `estimated_token_calls?` (all `number`).
- `UsageTurn` gains optional `billing?: string` and `tokens_estimated?: boolean`.
- `MoneySpread` picks `modelled_calls`, `modelled_subscription_calls` and `estimated_token_calls` too.

*`cost.tsx`:*
- New constants:
  - `SUBSCRIPTION_NOT_BILLED = "subscription — not billed"`;
  - `TOKENS_ESTIMATED = "tokens estimated"`.
- **The estimate-kind rule (I4).** It applies in `bucketPrice`, `headlineIsEstimate`, `PostCost` and `MoneyColumns`, through one helper.
  - **Present.** A kind is present when its calls are: `estimated_usd` when `subscription_calls > 0`, and `modelled_usd` when `modelled_calls > 0`.
  - **Conflicting.** A present kind conflicts with another only when **both figures are non-zero**.
  - With nothing billed, the headline is:
    - **two non-zero kinds:** `UNPRICED`, as today, and `Footnotes` prints each one;
    - **exactly one non-zero kind:** `≈` that figure, even beside present-but-zero kinds. Adding `$0` to it reconciles to both columns;
    - **only zero kinds present:** `≈ $0.00`, an estimate of zero. It is never spend and never "not reported";
    - **no kind present:** as today.
- `Footnotes`:
  - Under the modelled line: `Of these, {N call(s)} ran on a subscription — not billed.` when `modelled_subscription_calls > 0`.
  - Under the unpriced line: `Of these, {N call(s)} ran on a subscription — not billed.` when `unpriced_subscription_calls > 0` (M13).
  - A new line: `{N call(s)} had token counts estimated here — the provider reported none.` when `estimated_token_calls > 0`.
- `PostCost` title adds `N on a subscription — not billed` and `N with tokens estimated`.
- `MoneyColumns` (M5):
  - the "Modelled" hint reads "Priced against your rates. Arithmetic, not a receipt.";
  - it adds `{N} on a subscription — not billed.` when `modelled_subscription_calls > 0`;
  - it adds `Some token counts were estimated here.` when `estimated_token_calls > 0`.
- `turnTags(turn: UsageTurn) -> string[]`:
  - `SUBSCRIPTION_NOT_BILLED` when `billing === "subscription"` and the turn is not billed spend (`cost_usd == null || cost_basis === EQUIVALENT`);
  - `TOKENS_ESTIMATED` when `tokens_estimated`.
  - `CostPanel` renders each tag as a `<span className="chip">` beside `turnPrice`.
- `tokenTotal(bucket: Pick<UsageBucket, "total_tokens" | "estimated_token_calls">) -> string` gives `"≈ 1,234 tok"` when `estimated_token_calls > 0`, else `"1,234 tok"`. It replaces the three hand-written token totals: `CostPanel` ~126, `CostsView` ~149, and its table cell ~310.

- [ ] **Step 1: Failing tests:**
  - `a stated zero rate reads as an estimate of zero, never a bare $0.00` (Review Focus 5): `bucketPrice({…, modelled_calls: 2, modelled_usd: 0}) === "≈ $0.00"`.
  - `a zero-rated kind beside a subscription estimate headlines the estimate` (I4): `bucketPrice({…, subscription_calls: 3, estimated_usd: 0.4, modelled_calls: 2, modelled_usd: 0}) === "≈ $0.40"`, and `headlineIsEstimate` is true for it.
  - `two non-zero estimate kinds still read not reported`
  - `Footnotes names subscription calls in the modelled figure`
  - `Footnotes names subscription calls among the unpriced` (M13)
  - `Footnotes says when token counts were estimated`
  - `a subscription turn is tagged subscription — not billed`
  - `a billed turn on a subscription provider is not tagged`
  - `estimated tokens are marked wherever a token total is shown`, across CostPanel and CostsView.
  - `MoneyColumns says how much of the modelled column ran on a subscription, and when tokens were estimated` (M5)
  - The existing "never summed" and "not reported" tests stay green.
- [ ] **Step 2–4:** FAIL → implement → PASS. Also run `npx vitest run src/components/cost.test.tsx src/components/CostPanel.test.tsx src/routes/CostsView.test.tsx src/routes/GlobalCostsView.test.tsx src/routes/CampaignHub.test.tsx src/routes/ScenesView.test.tsx`, then `make check-eslint` and tsc.
- [ ] **Step 5: Commit** `feat(web): cost surfaces say which calls ran on a subscription and which tokens were estimated`

---

### Task 9: Docs, guard, and the gates

**Files:** `CLAUDE.md`, the `store/usage.py` and `store/pricing.py` module docstrings, `backend/tests/test_docs_guard.py`.

- **CLAUDE.md, Costs.** Leave the existing paragraphs word for word, apart from the `estimated_usd` bullet Task 0 narrowed. Add one paragraph covering:
  - Rows carry `provider_id`, `requested_model`, `operation`, `role`, `preset` and `billing`. `Meter.done` files them from what `llm._stamp` copies off the resolved conn; they are never passed at call sites.
  - `billing` is a label, and only `cost_basis` moves a figure between columns. **A billed price beats the subscription tag** and counts against a budget (ruling 4, M1).
  - Rates are a model's facts first, then `pricing.json`, decided by `pricing.rate_for_call`.
  - `tokens_estimated` marks counts this side made. The facade makes them off the event loop, only for an attempt whose stream ended on its own; `Meter.done` never counts.
  - Subscription rows modelled from user rates are `modelled_usd`.
- **Module docstrings (M5, M7).**
  - `usage.py`, the opening paragraph: the module records what the provider reported and, where it reported no counts, what this side counted, flagged `tokens_estimated`.
  - `usage.py`, the absent-not-zero paragraph: drop "a usage block is all-or-nothing". A token total is a floor whenever `unmetered_calls` is non-zero, and a total resting on estimated counts says so through `estimated_token_calls`.
  - `usage.py`, the "Pricing has three sources" section: name the two rate layers and the estimate flag.
  - `pricing.py`: name `rate_for_call` and why it reads facts files directly. Add that deleting a provider unlinks its facts, so its history re-prices from `pricing.json` or reads unpriced: "at current rates", consistently. The provider-delete confirmation lists no consequences, so no copy changes there.
- **`test_docs_guard.py`:** `test_claude_md_costs_names_real_ledger_fields`.
  - Every backticked ledger field the Costs section names (`provider_id`, `requested_model`, `tokens_estimated`, `billing`) must be a parameter of `usage.record`.
  - `rate_for_call` must exist in `pricing`.
- No detached-run handler changes, so the inventory needs no recount.

- [ ] **Step 1:** Write the docs. Run `tests/test_docs_guard.py`.
- [ ] **Step 2:** Run `make check-lint check-mypy check-eslint`. Run `make baseline` if a count shrank, and commit it. **Do not run `make check` or the full suite** (Global Constraints).
- [ ] **Step 3: Commit** `docs(costs): ledger fields, rate layers and estimated tokens`
- [ ] **Step 4: Gates** (CLAUDE.md):
  - `/codex:review` against the diff;
  - then the final `/codex:adversarial-review` against the diff and the spec's §9 and §14 row E.
  - Address every finding of each gate, or record why not, before moving on. There is no cap on fix rounds.
- [ ] **Step 5:** Push, and **wait for CI green** (its `make check` is the spec §14 gate) before merging.

---

## Coordination with D and F

Slices D (embedding operation) and F (`decide()`) are planned in parallel on sibling branches off slice C. All three meter.

**Shared files, and how E stays out of the way:**

| File | D | F | E |
|---|---|---|---|
| `store/usage.py` | **owns `record(operation=)`**, first after `images`, passed through by `Meter.done` (D-a); meters embed calls through a `Meter` | meters decide calls through a `Meter` | appends its own block **after D's `operation`**. `Meter.done` reads more holder keys, guarded. **`Meter.__init__` and `meter()` are unchanged.** If E lands first, E adds `operation` exactly as D specifies and D drops it. |
| `tests/test_usage_guard.py` | "embed is metered" | "decide is metered" | **not touched**. E's tests do not assume "embed is metered" when E lands first. |
| `llm_usage.py` | uses `account` in `embed._stamp` | uses `with_account` for `decision_mode` | new names only (`ACCOUNT_*`, `account`, `with_account`, `ESTIMATE_KEY`, `ENDED_KEY`, `ESTIMATED`, `Estimate`, `note_*`, `fill`) |
| `store/inference/resolve.py` | `space_id`; `resolve.embedding()` stamps the embed account block (D-c) | `decision_mode` on `ResolvedInference` | the account block in `_lowered` and at the **tail of `resolve()`**, before the fallback attach |
| `store/inference/resolved.py` | `space_id` | `decision_mode` | **not touched** |
| `store/inference/embed.py` | `embed._stamp`, the one `llm_usage.account(...)` line (D-c) | — | **not touched** (see the contract) |
| `store/inference/settings.py` | edits `used_by`'s body (the Embedding line, ~301); keeps `translate.embedding_role` as a one-line wrapper (D-b) | possibly the Decision card's route list | rewrites `used_by` over `in_use.selections()` (Task 5). **The second to land merges** D's Embedding change into `in_use.selections()`. |
| `routes/config.py` (`_embed_probe`) | may move the embed probe onto D's metering | — | files `operation: "embed"` on the probe row (Task 2, M9). The second to land keeps it. |
| `llm.py` | — | `schema=` in `_provider` and the adapters | `_stamp` (two lines), `LLMClient(count_tokens=)`, `_dispatch`/`_lowered` (one each), `_resilient` (the reply note and the count after a natural end) |
| `CLAUDE.md` | "Adding an LLM call site?" | the same | the Costs section only |

**The contract E offers.** Every metered call files its fields through the usage holder, so D and F never change a `Meter` signature. The resolver stamps `operation`, `role` and `billing` into `ACCOUNT_KEY` after Task 2.

**D's obligations (embed):**

1. **The account block.** `resolve.embedding()` stamps `operation: "embed"` and `role: "embedding"` into the endpoint conn's account block, as a fresh dict (`with_account`), beside the `billing` `_lowered` stamps. `endpoint()` carries that attempt's connection dict (D-c).
2. **One `account` call.** `embed._stamp` (`store/inference/embed.py`) is the one place that calls `llm_usage.account(holder, endpoint.conn)` (D-c).
3. **The model.** D passes `model=` to `usage.meter(...)` (or sets `holder["model"]`). `account()` does not copy `model`. Without it, `rate_for_call` looks for facts under `""`.
4. **The count.** D puts the embeddings response's `usage.prompt_tokens` into the holder, read through `llm_usage.tokens`.
5. **The campaign.** An embed row carries the campaign id where the caller has one (semantic recall inside a turn), so its spend counts against that campaign's budget. Library-scope callers carry none (controller ruling).
6. **No completion estimate, ever.** An embed row's completion count is structural, not estimated (ruling 8). If D counts a missing prompt locally, it does so off the loop with `Estimate.count(counter, completion=False)` and writes it with `llm_usage.fill`, which never writes completion for `operation == "embed"`. That is optional for D.
7. **Who wires it.** If D lands first without E's names, E's Task 2 adds the account block in `resolve.embedding()` and the `account` line in `embed._stamp`. If E lands first, D's plan adds them with E's names.

**What D's metering does to totals (D-m4).** Once D meters embeddings, every recall or art turn on an endpoint that reports no price adds an unpriced embed row. Totals and campaign Costs tails then read "incomplete" until the user sets rates for the embedding model.
- E's ruling 8 is what lets those rates price an embed row: completion 0, prompt from the provider's count.
- E's Housekeeping chore names the Embedding role's model when nothing prices it, and its action opens that model's rates.

**F's obligations (decide):**

1. **The mode, per call.** F stamps `decision_mode` per facade call, on a **copied** account block: `llm_usage.with_account(conn, decision_mode=m)`.
   - The resolver cannot know an attempt's mode. §5.5 runs native and then structured on the *same* selection, and `llm._same_route` drops a second route on the same provider id.
2. **Never in place.** An account block is never mutated in place. `{**conn}` copies share it, so one in-place write would rewrite the primary's block and the fallback's `fallback_sampling` copy. E's `test_account_blocks_are_never_mutated_in_place` pins this.
3. **Everything else is free.** F's decide path goes through `generate(schema=)` and `llm._stamp`, so it files every other field from a `resolve(..., operation="decide")` conn. A decide route's rows say `generate` until F flips the route's `operation`, which is §14's safety rule seen in the ledger.

**Tests E owns for this contract:**
- `test_account_blocks_are_never_mutated_in_place` (Task 2);
- `test_an_embed_operation_never_gets_a_completion_estimate` (Task 3);
- `test_an_embed_row_with_no_completion_count_is_priced` (Task 4);
- `test_an_account_only_holder_files_the_meters_model` (Task 4): a `Meter(..., model="vendor/embed-a")` whose holder only `account()` and a prompt count filled files `model == "vendor/embed-a"` and is priced at that model's facts rates;
- `test_an_embed_row_with_a_campaign_reaches_its_budget` (Task 4): an embed row with `campaign` and `cost_usd` counts in `budget(cid)`.

**Rebase.** Any order works; E's backend changes are additive. Expect textual conflicts at:
- **the `usage.record` signature:** D's `operation`, then E's block;
- **the tail of `resolve()`** (`store/inference/resolve.py`, the fallback attach and the `ResolvedInference(...)` return): E's account-block stamp, beside F's `decision_mode` and D's `space_id`;
- **`settings.used_by`** (D-b): the second to land merges.

---

## Parallelism

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | Spec first |
| 1 | 1, 2 | 1 is `pricing`/`facts`/facts routes. 2 is `llm_usage`/`llm._stamp`/`resolve`/`usage.record`/the probe meters. Both touch `routes/config.py` in different functions (facts routes vs probes). |
| 2 | 3, 4 (both after 2; 4 also after 1) | 3 is the facade, `llm_reasoning`, `build_llm`, and `Meter.done`'s flag read. 4 is `Rates`, `_add`, `_turn`, `unpriced_models`, the rollup and `todo.py`'s stale-pricing items. Their only shared file is `store/usage.py`, in different functions. Merge by hand. |
| 2' | 5, 6 (after 1) | 5 is `in_use.py`/`settings.used_by`/`todo.py`. 6 is `probes.py`/the preview route and the dialog. 5 and 4 both touch `todo.py` (different functions). |
| 3 | 7 (after 1), 8 (after 4) | Frontend. Disjoint files. |
| 4 | 9 | Last |

## Rulings (Task 0 folds each design ruling into the spec)

### Design rulings

1. **The provider goes in `provider_id`; `provider` stays the adapter kind.** Every existing row already writes `provider` as the kind (`llm._stamp`). An append-only ledger cannot change a field's meaning under rows older builds still read. `connection` stays the display name.
2. **`requested_model` keys the model's rates.** `model` is what answered, and a provider may name a dated snapshot of what was asked for. Facts rates are stated under the selection's model (`facts.model_of`), so the row records that too, only when it differs. `pricing.json` keeps matching `model`, so existing tables price exactly what they did.
3. **Facts rates price only rows that name a provider.** Rows filed before this slice have only `connection` (a display name, neither unique nor stable), so they price from `pricing.json` alone. No name matching.
4. **`billing` is a label.** `cost_basis` alone moves a figure: `billed` → `cost_usd`, `equivalent` → `estimated_usd`. A provider that reports a *billed* price stays spend, and counts against a budget, even when tagged subscription, because the provider said it charged. The tag changes what a surface says, never which column a figure lands in. This amends §9.1's "never count against a budget" and narrows CLAUDE.md's `estimated_usd` bullet (Task 0).
5. **Subscription rows are a breakdown count, not a figure.** `modelled_subscription_calls` sits inside `modelled_calls`, and `unpriced_subscription_calls` sits inside `unpriced_calls` (the cache-tokens precedent). There is no subscription dollar figure, since a fourth dollar figure is one more thing to be added to the other three.
6. **Local estimation runs only for an attempt whose stream ended on its own.** The facade decides this, not the row's status. A route can file `ok` after breaking out early (stop-after-fence), and nobody knows what such a call billed. The same holds for an aborted or failed call, so an estimate would invent cost. Only the count the provider omitted is filled. Image parts are not estimated, and reasoning text is counted as completion.
7. **Estimated counts go in `prompt_tokens`/`completion_tokens`, flagged `tokens_estimated: true`.** That way every existing reader sums them and every rate can price them; the flag drives the labels, and buckets count `estimated_token_calls`. An older build reading a synced ledger sees them as counts. Accepted: it also prices nothing new.
8. **An `embed` row with no completion count is priced with completion 0.** An embedding generates nothing, so this is a structural fact rather than a guess. No estimate ever fills an embed row's completion. Every other operation still needs both counts.
9. **`role` is the slot that supplied the resolution, on both attempts.**
   - It is absent for a pin.
   - It is absent when a per-call override changed the provider or the model, because then the user supplied the selection (§9.3, "when a role supplied the selection").
   - A pinned route's fallback does come from `default_role`'s fallback (§5.5), but it files no role. That is a deliberate simplification: the row names the resolution's slot, not a separate guess at which slot the fallback came from.
10. **The Housekeeping chore counts distinct `(provider, model)` pairs.**
    - It counts across global roles, fallbacks and chosen pins, the Embedding role, and each campaign's overrides.
    - It is library-scoped, because campaign overrides are part of it.
    - It skips dangling references, blank models and unchosen pins.
    - "Reports a price" is the provider preset's `reports_price`, so OpenRouter and the Claude subscription never count.
    - Zero rates are a price.
    - It sits beside the existing ledger-based `unpriced` chore. That one asks which recorded strings no rate matches; this one asks which configured models nothing would price.
    - It is built in `routes/todo.py` (§9.2 said `chores.py`).
11. **Writing rates is strict; reading them is fail-soft.** `PUT …/facts` refuses a partial, invalid or unknown-field entry with 400, and `{}` clears. A mangled file reads as no rates, falling back to `pricing.json`.
12. **The test-call estimate.**
    - The catalog's reported price comes first.
    - Otherwise it is the model's rates, then `pricing.json`. `estimate_basis` says which.
    - With user rates, the vision probe is priced from its token guess, because rates price image input as prompt tokens.
13. **`preset` is the sampler preset actually sent.** It is `conn["sampling"]["preset_id"]` on the attempted dict, after `fallback_sampling`. It is never the §6.1 provider preset, which §5.4 names apart as `provider_preset`.
14. **Estimates are counted by the facade, off the event loop.**
    - The counter is injected into `LLMClient`, because the gateway imports no store.
    - The count runs on a worker thread, bounded by `COUNT_TIMEOUT_S`.
    - `Meter.done` only reads numbers.

### Plan-review rulings (one line each; review: `.superpowers/sdd/plan-reviews/slice-e-plan-review.md`)

- **C1:** counting moves into `_resilient`, on `asyncio.to_thread` with an injected `LLMClient(count_tokens=)`; `Meter.done` never counts (Task 3, ruling 14).
- **I1:** the gate is a natural end of the attempt's stream (`ENDED_KEY`), not `status == "ok"`; the early-break test is added (Task 3, ruling 6).
- **I2:** `from_chunk` extracts reasoning without a display buffer, and `feed` notes it once; tested through `OpenAICompatibleClient` on a mock transport (Task 3).
- **I3:** `Meter.done`'s new reads and the facade's count are guarded with `except Exception`; malformed shapes are tested; `ESTIMATE_KEY`/`ENDED_KEY` are popped (Tasks 2, 3).
- **I4:** an estimate kind conflicts only when its figure is non-zero; the zero-rated local Fast plus subscription Primary test is added (Task 8).
- **I5:** only the `campaign.md` frontmatter parse is memoized; translation, lookup and format are read fresh each call (Task 5).
- **I6:** the coordination section names D's obligations (model, count, campaign, account line) and F's (copied block per call); E pins no-mutation and no-embed-completion (Coordination, Tasks 2–4).
- **M1:** ruling 4 kept; Task 0 amends spec §9.1's budget sentence and narrows CLAUDE.md's `estimated_usd` bullet; Task 9's paragraph says a billed price beats the tag.
- **M2:** `role` is omitted when an override changed the provider or the model; the pinned-fallback simplification is stated (Task 2, ruling 9).
- **M3:** ruling 13 says `preset` is the sampler preset sent (Task 2).
- **M4:** Task 3's run list names `test_reasoning_display.py`, `test_anthropic.py` and `test_openai_compatible.py`; the non-matching glob is gone.
- **M5:** `usage.py`'s two stale docstring passages are amended, `MoneyColumns` reads "your rates", and it hints at estimated tokens (Tasks 8, 9).
- **M6:** stale-pricing item ids are `provider:model`, and the chore's names are deduplicated (Task 4).
- **M7:** `pricing.py`'s docstring says deleting a provider re-prices its history; the delete confirmation lists no consequences, so it is unchanged (Task 9).
- **M8:** the aggregate moves to `rollup-v3.json` at `VERSION = 3`, and an older build's `rollup.json` is left alone (Task 4).
- **M9:** model-test rows carry the probe's `operation` via `with_account` (Task 2).
- **M10:** `_lowered` re-notes the prompt after `_lower` (Task 3).
- **M11:** the Housekeeping chore and its items catch `StoreBusy`, `UnicodeDecodeError`, `ValueError` and `OSError` (Task 5).
- **M12:** `selections()` returns every stored selection and `unpriced()` filters; `in_use` is registered in `store/inference/__init__.py` and never imports `settings` (Task 5).
- **M13:** `unpriced_subscription_calls` is counted and footnoted (Tasks 4, 8).
- **M14:** `?edit=rates` is cleared on Save and Cancel, the no-rates copy is hedged, and a cache-only entry cannot be saved (Task 7).
- **M15:** `check_entry` refuses unknown keys with 400 (Task 1).
- **M16:** the three other `stream` fakes delegate to `FakeLLM.stream`, so they already stamp; no change (Task 2).
- **M17:** the fix-wave cap is gone; tasks run only named tests plus lint, mypy and eslint, never `make check`; merging waits for CI green (Global Constraints, Task 9).
- **Trap (`facts.state` returns early):** `rates` joins the early-return check, with a rates-only-write test (Task 1).
- **Spec §9.2:** `chores.py` → `routes/todo.py`, in Task 0.
- **Q6 (Testability):** the dated-snapshot case is proven at `_stamp` through the real facade (Task 2).
- **D-a:** D owns `record(operation=)`, first after `images`; E appends after it, or adds it that way if E lands first (Task 2, Coordination).
- **D-b:** D keeps `translate.embedding_role` as a wrapper and edits `used_by`; `settings.py` is a shared touch-point, and the second to land merges (Coordination).
- **D-c:** the contract names `endpoint()`'s conn, `resolve.embedding()`'s stamp and `embed._stamp` as the one `account` call (Coordination).
- **D-m4:** unpriced embed rows make totals read "incomplete" until rates are set; ruling 8 and the chore are the answer (Coordination).
