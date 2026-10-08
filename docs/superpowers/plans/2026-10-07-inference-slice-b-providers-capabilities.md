# Inference refactor — Slice B: providers, capabilities, controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend everything slice C's screens will ask for — the provider preset table, per-model facts, a capability resolver with provenance and picker grouping, the capability check before a call, one controls function that both builds the wire parameters and describes them, a direct Anthropic API adapter, OpenRouter embeddings for the new layout, and a confirm-first model test call — while every existing store keeps resolving, sending and showing exactly what it does today.

**Architecture:**
- **Gateway side (no store imports, #239):** `llm_sampling` grows into the one place that decides, per connection dict, what each control sends (`effective(conn)`); `split`, `sent_names` and `report` become views over it, and `LLMClient._provider` computes the wire per attempt from the connection dict — so the facade's fallback, re-resolved per generation, gets the right wire automatically. The new `anthropic.py` adapter speaks the Messages API over raw `httpx`.
- **Store side:** new modules under `store/inference/` — `providers` (the preset table), `facts` (per-model user facts), `capabilities` (sources, precedence, provenance, picker grouping) and `controls` (wraps `llm_sampling.effective` with capability provenance for the screens). The resolver attaches the catalog facts the gateway needs onto the lowered connection dict (`model_params`, which exists, plus `model_features`), and fills the slice-B `Attempt` fields.

**Tech Stack:** Python ≥3.11, FastAPI, httpx, pytest; TypeScript type additions only (no new screens).

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` (§4.1–4.3, §5.3–5.4, §6, §8, §14 B row). Task 0 amends the spec where it predates the current Anthropic API and where this plan's rulings settle it.

**Gate record:** Codex unavailable; a Claude adversarial review stood in for the plan gate (14 findings, folded into this revision; rulings listed at the end).

## Global Constraints

- **No behaviour change for any existing store** (spec rule 3): slice A's frozen snapshot (`backend/tests/fixtures/inference_baseline.json`) keeps passing untouched; `llm_sampling.report`, `split`, `sent_names` and `table()` return exactly what they return today for the three existing kinds; existing model pickers list exactly today's models; post images go where they go today; existing 409/400 texts are unchanged except the one named in Task 5.
- **Nothing spends money without asking first** (rule 1): the test call never runs without `confirm: true`, and runs with **no retries and no fallback**.
- **One resolver** (rule 2): per-kind control logic lives only in `llm_sampling.effective`; capability logic only in `store/inference/capabilities`; grouping for pickers is computed server-side.
- **Gateway modules import nothing from the store** (#239): `llm_sampling`, `llm.py`, `catalog.py`, adapters. Store modules may import gateway leaves (as `sampler_presets` imports `llm_sampling` today).
- **No `anthropic` SDK**: raw `httpx` (base deps must stay Android-installable; the SDK needs pydantic v2 + compiled `jiter`). Module `backend/src/grimoire/anthropic.py` (checked: shadows nothing; no `anthropic` package installed).
- **Anthropic API facts** (claude-api reference, cached 2026-09-25) as listed in `.superpowers/sdd/slice-b-notes/anthropic-api.md` and Task 7.
- **No real network in tests** (`httpx.MockTransport`).
- **Imports, pydantic v1/v2, atomic writes, path resolvers, guards** per CLAUDE.md.
- **Privacy:** invented names and fake keys only.
- **Test command:** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/<file> -q`; gate `make check`; known root-only failure `tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.

## Review Focus

1. **An existing `openai_compatible` connection at api.openai.com with `send_images: on`** still gets 0 post images (the preset table never asserts "yes" for a per-model capability). Task 4.
2. **Today's pickers** (connection detail `models`, the refresh run's result, `POST /model-catalog`) list only text-output models after `output_modalities=all`. Task 3.
3. **The test call** never reaches the fallback or retries, and a probe that completes without text still counts as accepted. Task 9.
4. **A Claude 4.7+-family model on the Anthropic adapter with a `temperature` preset** sends no `temperature`; the report says why. Tasks 6–7.
5. **A legacy config naming an OpenRouter connection for embeddings** still embeds nothing (OpenRouter embeddings are for the new layout only). Task 8.

---

## File structure

| File | Responsibility |
|---|---|
| `llm_sampling.py` (modify, gateway leaf) | `effective(conn)`: per-control state + wire, incl. reasoning; `split`/`sent_names`/`report` as views |
| `anthropic.py` (new, gateway) | Messages API adapter |
| `catalog.py` (modify, leaf) | `outputs`, Anthropic rows → `features`, `listable()` |
| `openrouter.py`, `llm.py` (modify) | `output_modalities=all`; dispatch + `aclose` for `anthropic`; `LLMClient.single()` |
| `store/inference/providers.py` (new, pure) | Preset table; `infer(conn)` |
| `store/inference/facts.py` (new) | `llm_connections/<id>.facts.json` |
| `store/inference/capabilities.py` (new) | Sources, precedence, `fits`, `group_for` |
| `store/inference/controls.py` (new) | Screens' view: `llm_sampling.effective` + capability provenance |
| `store/inference/resolve.py`, `resolved.py` (modify) | `model_features` on lowered dicts; slice-B `Attempt` fields; §5.3 check data |
| `store/inference/probes.py` (new, pure) | Probe payloads |
| `store/post_images.py`, `store/embed_space.py`, `store/image_drafts.py`, `store/llm_connections.py`, `store/sampler_presets.py` (modify) | As tasks say |
| `routes/common.py`, `routes/config.py`, `routes/models.py` (modify) | Capability refusal; catalog filtering; test call; read APIs; `_connection_ready` |
| `frontend/src/api/types.ts`, `client.ts`, `components/ConnectionEditor.tsx` (`LISTABLE`) | Types and the kind mirror only |

---

### Task 0: Amend the spec

**Files:** Modify the spec (§4.1 `Attempt`/§5.4 naming, §6.1 Anthropic row and preset semantics, §6.2 source 3–4, §6.4 probes, §8 reasoning + response shape, §7.3 OpenRouter embeddings scope)

- [ ] **Step 1:** §6.1: the table's last column means **"not ruled out"** (affects only §6.3's Unverified grouping); a preset asserts `yes` only for what is true of every model on it (`generate`, `stream` on generative presets). Anthropic row: generate, stream; vision / structured_output per catalog; no embed, no decide_native; sampling parameters and prefill per model (current models reject both).
- [ ] **Step 2:** §6.2: source 3 includes Anthropic's `/v1/models` capability tree (`image_input`, `structured_outputs`, `thinking.types`, `effort`, `max_tokens`); source 4 is "the preset's always-true facts". The legacy connection `vision` field is a **post-image** setting, not a vision assertion (§4.2's `vision` in facts is the new-layout assertion).
- [ ] **Step 3:** §8: response shape `{requested, effective, controls}` (the plan's former `wire` is `effective`); `anthropic` reasoning: `low|medium|high` → `thinking: {"type":"adaptive"}` + `output_config: {"effort": <same>}` where adaptive is supported, else fixed `budget_tokens` 1024/4096/16000 (≥1024, < effective `max_tokens`); `off` on an adaptive model sends `thinking: {"type":"disabled"}` where the catalog's `disabled_thinking` is true, is `unsupported` where it is false, `unknown` where unstated — except Claude Sonnet 5.5, chosen by id, which is always sent `thinking: {"type":"between_tools"}` whatever `disabled_thinking` says or omits; on a budget-only or non-thinking model it sends nothing (`supported`). Anthropic sampling (`temperature`/`top_p`/`top_k`) is `unsupported` when the catalog says `enabled` thinking is unsupported, **or** whenever thinking is being sent. OpenAI preset: `max_tokens` is `translated` → `max_completion_tokens`.
- [ ] **Step 4:** §6.4: probes run once — no retries, no fallback; success means "request accepted and the response completed"; the `decide_native` probe lands with the native adapters (slice H); an `embed` probe's ledger row carries no token counts until slice D meters embeddings. §7.3: OpenRouter embeddings serve the new-layout Embedding role (slice C makes it selectable); the legacy `embeddings_connection_id` stays `openai_compatible`-only.
- [ ] **Step 5:** §5.4: slice B adds `provider_kind`, `base_url`, `rev`, `billing`, `provider_preset` (renamed from "preset" to avoid the sampler-preset `preset_id`), `facts`, `capabilities`, `controls`; an incapable fallback is **reported** in `ResolvedInference.fallback_missing` and enforced by the facade only from slice C.
- [ ] **Step 6:** Commit — `docs(spec): settle slice B against the current Anthropic API and its plan review`

---

### Task 1: Provider presets (pure)

**Files:** Create `store/inference/providers.py`; Test `tests/test_inference_providers.py`

**Interfaces:**
- `class Preset(NamedTuple): id: str; label: str; kind: str; base_url: str; url_locked: bool; billing: str; reports_price: bool; always: frozenset[str]; possible: frozenset[str]; never: frozenset[str]` over capability names `generate, stream, vision, embed, decide_native, structured_output, prefill`.
- `PRESETS` (ids, base URLs as spec §6.1). Exact sets:
  - `openrouter`: always `{generate, stream}`; possible `{vision, embed, decide_native, structured_output, prefill}`; never `{}`.
  - `anthropic`: always `{generate, stream}`; possible `{vision, structured_output, prefill}`; never `{embed, decide_native}`, plus prefill per model (`providers.never_for`: Claude 4.6 and later, or no version).
  - `claude`: always `{generate, stream}`; possible `{}`; never `{vision, embed, decide_native, structured_output, prefill}`.
  - `openai`: always `{generate, stream}`; possible `{vision, embed, decide_native, structured_output, prefill}`; never `{}`.
  - `zai`, `zai_coding`: always `{generate, stream}`; possible `{vision, structured_output, prefill}`; never `{embed, decide_native}` (vision moved to possible during execution: spec §6.1 never rules it out, and `never` changed `post_images.reach` for existing z.ai connections).
  - `ollama`, `lmstudio`, `custom`: always `{}`; possible everything except `decide_native`; never `{decide_native}`.
- `infer(conn: dict) -> Preset` per spec §11.2 step 2 (explicit valid `conn["preset"]` wins; unknown host → `custom`). `billing(conn) -> str`.
- [ ] **Step 1: Failing tests:** `infer` per preset (trailing slashes, ports, unknown host, explicit preset, bogus explicit preset); the exact sets above; every preset's `kind` is in `llm_connections.KINDS + ("anthropic",)` (Task 7 tightens it to `KINDS`).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** — `feat(inference): the provider preset table`

---

### Task 2: Model facts

**Files:** Create `store/inference/facts.py`; Modify `store/llm_connections.py` (`facts_path`, `delete_connection` unlinks it); Test `tests/test_inference_facts.py`

**Interfaces:**
- `read(provider_id) -> dict[str, dict]` (defensive: mangled/scalar/unsafe → `{}`).
- `of(provider_id, model, rev) -> dict` — `{"vision": ""|"on"|"off", "prefill": bool|None, "post_process": str, "rates": dict|None, "verified": {cap: {"ok", "at", "error"?}}, "overrides": {cap: "yes"|"no"}}`; verified entries from another `rev` dropped.
- `record_verified(provider_id, model, rev, results)` and `set_overrides(provider_id, model, overrides)` — read-merge-write under a module-level `threading.Lock` (concurrent test calls on one provider never lose a result), atomic write; `record_verified` (amended after review) writes only while `rev` is still the connection's own, checked and written under `llm_connections.LOCK` (held by every connection write; taken before the facts lock), and returns whether it wrote; `set_overrides` validates names/values (`ValueError`).
- [ ] **Step 1: Failing tests:** round-trip; stale-rev verified hidden; mangled → `{}`; unsafe id; delete removes it; two threads recording different models both land; `test_atomic_guard`, `test_paths_guard`, `test_lock_domain_guard` green (classify as global, outside the campaign domain, if asked).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** — `feat(inference): per-model facts beside each provider`

---

### Task 3: Catalog entries say what models output; today's pickers don't change

**Files:** Modify `catalog.py`, `openrouter.py` (`list_models`), `routes/config.py` (connection detail, refresh result, `POST /model-catalog`), `frontend/src/api/types.ts` (`Model.outputs?`, `Model.features?`); Test `tests/test_model_catalog.py`, `tests/test_openrouter.py`, `tests/test_routes.py`, `tests/test_draft_runs.py`

**Interfaces:**
- `catalog.entry(raw)` adds `outputs` (list, only when `architecture.output_modalities` is a list) and, for an Anthropic models-API row (`type: "model"` + `display_name`, whatever its `capabilities` -- that tree is nullable, and a null one states no capability): `name`←`display_name`, `context`←`max_input_tokens`, `vision`←`capabilities.image_input.supported`, `outputs=["text"]`, `features={"structured_output", "adaptive_thinking", "enabled_thinking", "disabled_thinking", "effort": [levels], "max_tokens": int}` — only keys the row states.
- `catalog.listable(entries: list[dict]) -> list[dict]` — rows whose `outputs` contains `"text"` or that state no `outputs`.
- `openrouter.list_models` sends `params={"output_modalities": "all"}`.
- The three picker-facing responses return `catalog.listable(...)`; the sidecar keeps every row.
- [ ] **Step 1: Failing tests:** `entry` mapping (OpenRouter outputs; absent stays absent; Anthropic row); `list_models` query param; each of the three responses excludes an embeddings-only and a rerank-only row while the sidecar keeps them (Review Focus 2).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** — `feat(catalog): entries carry output modalities; pickers still list text models`

---

### Task 4: The capability resolver

**Files:** Create `store/inference/capabilities.py`; Modify `store/post_images.py` (`capability`); Test `tests/test_inference_capabilities.py`, `tests/test_post_images.py`

**Interfaces:**
- `NAMES`, `class Cap(NamedTuple): value: str; source: str` (`yes|no|unknown`; `adapter|test|user|catalog|preset|name|unknown`).
- `resolve_caps(preset: Preset, model: str, *, catalog_row: dict | None, facts: dict) -> dict[str, Cap]` (pure). Precedence: (1) `preset.never` → `no` (`adapter`), never overridden; (2) `facts.verified` (`test`) then `facts.overrides` and `facts.vision` (`user`) — **not** the legacy connection `vision` field; (3) catalog (`outputs` text/embeddings/decisions; `vision`; `params` containing `structured_outputs`/`response_format`; Anthropic `features.structured_output`); (4) `preset.always` → `yes` (`preset`); (5) name rule (id contains `embed` → embed `yes`, generate `no`) — applies only where 1–4 said nothing; (6) `unknown`. `preset.possible` never yields `yes`.
- `caps_for(conn: dict, model: str | None = None) -> dict[str, Cap]` (impure; never raises).
- `fits(caps, need) -> str` for `generate|vision|embed|decide` (`decide` = `decide_native` yes or `generate` yes).
- `group_for(caps, need, preset) -> tuple[str, str]` → `("fits"|"unverified"|"hidden", reason)`; `unverified` when `fits` is `unknown` and `need`'s capability is not in `preset.never`; reason text for `hidden` e.g. `"z.ai serves no embeddings"` built from `preset.label`.
- `post_images.capability(conn)` keeps its exact answers: legacy `vision` on/off first (it is the post-image setting), then `caps_for(conn)["vision"]` (`adapter` no for non-image kinds, catalog, facts).
- [ ] **Step 1: Failing tests:** each source; precedence (adapter no beats every yes; test beats catalog; stale rev ignored); `possible` never yields yes; OpenAI-preset model with no catalog `vision` → `unknown` and **0 post images with `send_images: on`** (Review Focus 1); name rule blocked by catalog `outputs`; `fits("decide")`; `group_for` cases incl. z.ai embed `hidden` with reason; `test_post_images.py` unmodified and green.
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_post_images*.py tests/test_inference_equivalence.py`.
- [ ] **Step 5: Commit** — `feat(inference): one capability resolver, with provenance and grouping`

---

### Task 5: Attempts carry slice-B fields; the capability check

**Files:** Modify `store/inference/resolved.py`, `resolve.py`, `routes/common.py` (`require_inference`, `override_inference`, `image_draft_prompt`), `store/image_drafts.py` (`UNSUPPORTED` text), `store/routing.py` (`NON_ROUTE_TASKS`), `routes/config.py` (`_connection_ready`); Test `tests/test_inference_resolve.py`, `tests/test_image_description_draft.py`, `tests/test_routing_guard.py`, `tests/test_routes.py`

**Interfaces:**
- `Attempt` gains (defaulted): `provider_kind`, `base_url`, `rev`, `billing`, `provider_preset`, `facts: dict`, `capabilities: dict[str, Cap]`, `controls: dict` (`{}` until Task 6).
- `resolve._lower` also attaches `model_features` (the catalog row's `features`, when any) beside `model_params`.
- `ResolvedInference.missing: tuple[str, ...]` (primary known `no` for the route's operation/`requires`) and `.fallback_missing: tuple[str, ...]` (reported only; the facade's fallback is unchanged until slice C).
- Refusal order in `require_inference` / `override_inference` (and so `_soft_inference`): today's missing-key/no-connection 409s first, **then** the capability 409: an `adapter`-sourced `no` on a `vision` route → `{"detail": image_drafts.UNSUPPORTED, "kind": "missing_key"}` exactly as today; any other known `no` → `{"detail": "<route label> runs on <model> (<provider name>), which cannot <capability>.", "kind": "incapable"}`. `image_draft_prompt`'s own kind check is removed.
- `image_drafts.UNSUPPORTED` names the Anthropic API alongside OpenRouter and OpenAI-compatible (the one changed text; ruling).
- `routing.NON_ROUTE_TASKS = ("model-test",)`; guard accepts it.
- `_connection_ready`: `anthropic` needs a key.
- [ ] **Step 1: Failing tests:** frozen snapshot green; fields populated; catalog-says-no-vision OpenRouter model on the image route → `incapable` 409 after (not before) a missing-key 409 would fire; claude-kind image route → today's 409 shape with the updated text; the override and soft paths refuse the same way; an incapable fallback appears in `fallback_missing` while `build_llm()._routes(conn)` still lists it (pinning that the facade is unchanged); keyless anthropic connection not ready.
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_image_description_*.py tests/test_routing_*.py`.
- [ ] **Step 5: Commit** — `feat(inference): attempts know what they can do; the seam refuses what they can't`

---

### Task 6: One controls function, in the gateway

**Files:** Modify `llm_sampling.py`, `llm.py` (`_provider`, `_preset_refusal`), `llm_reasoning.py` (`glm_effort` reused), `store/sampler_presets.py`, `store/inference/resolve.py` (`Attempt.controls`); Create `store/inference/controls.py`; Test `tests/test_llm_sampling.py`, `tests/test_inference_controls.py`, `tests/test_sampler_presets_store.py`, `tests/test_reasoning_display.py`, `tests/test_llm.py`

**Interfaces:**
- `llm_sampling.REASONING = ("off", "low", "medium", "high")`; `validate` accepts `reasoning_effort` with an explicit choice branch in `_check`; **`table()` still returns exactly the nine existing rows** (the editor gains the field in slice C).
- `llm_sampling.effective(conn: dict) -> dict` → `{"requested": {...}, "effective": {wire_name: value}, "controls": {name: {"state", "wire", "why"}}}`, reading only the connection dict: `kind`, `model`, `sampling.params`, `sampler_support`, `model_params`, `model_features`, legacy `reasoning_effort`. Rules:
  - The nine existing params on `openrouter` / `openai_compatible` / `claude`: identical decisions to today's `_why_not`, wire names and the `repeat_penalty` duplicate (OpenRouter with no params list → `unknown`).
  - `openai_compatible` whose `base_url` is api.openai.com: `max_tokens` → `max_completion_tokens` (`translated`).
  - `anthropic`: `max_tokens` (default 16000, capped at `model_features.max_tokens`), `stop` → `stop_sequences` (`translated`); `temperature`/`top_p`/`top_k` `unsupported` when the model id does not name a Claude version below 4.7 (amended after review: the catalog's `enabled_thinking` is no proof a sampler is accepted) **or** thinking is being sent; the four penalty/min_p params `unsupported`.
  - `reasoning_effort`: per spec §8 (Task 0); when the preset sets none, the legacy connection field applies exactly as `llm_reasoning.glm_effort` does today.
- `split`, `sent_names`, `report` read `effective` (same outputs for existing kinds; `report` lists anthropic's `stop` under its canonical name).
- `LLMClient._provider` passes `effective["effective"]` to the `anthropic` adapter and keeps today's call shapes for the other three (it computes per attempt, so the facade's fallback is covered).
- `store/inference/controls.preview(preset_id, conn, model) -> dict` — `llm_sampling.effective` over the lowered dict plus `source` per control from capabilities; `Attempt.controls = llm_sampling.effective(attempt.conn)`.
- `sampler_presets.from_sillytavern` ignores any reasoning field.
- [ ] **Step 1: Failing tests:** `test_llm_sampling.py` and `test_sampler_presets_*` unmodified and green; `table()` nine rows; `validate` choices; anthropic temperature case (Review Focus 4); reasoning per adapter; GLM legacy unchanged (`test_reasoning_display.py`); OpenAI `max_completion_tokens`; `_preset_refusal` still matches on sent names, and on every field each sent control put on the wire (`llm_sampling.sent_fields`: top-level keys, dotted nested paths such as `output_config.effort`, and the nested leaf, a `type` discriminator only inside a path).
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_inference_equivalence.py tests/test_llm.py`.
- [ ] **Step 5: Commit** — `feat(llm): one function decides what each control sends`

---

### Task 7: The Anthropic API adapter

**Files:** Create `anthropic.py`; Modify `llm.py` (`__init__`, `_provider`, `list_models`, `check`, `aclose`, `LISTABLE_KINDS`), `routes/common.py` (`build_llm`), `store/llm_connections.py` (`KINDS`), `store/image_drafts.py` (`SUPPORTED_KINDS`), `store/inference/resolve.py` (`problem`: key required), `routes/models.py` (kind literals), `frontend/src/api/types.ts` (`LLMConnectionKind`), `frontend/src/components/ConnectionEditor.tsx` (`LISTABLE`); Test `tests/test_anthropic.py`, `tests/test_llm.py`, `tests/test_image_description_draft.py`, `tests/test_llm_error_status.py`, `tests/test_inference_providers.py`

**Interfaces — `AnthropicClient`:**
- `stream(messages, model, key, usage=None, effective=None)`:
  - Body: `model`, `messages`, `stream: true`, `max_tokens` (from `effective`, default 16000), plus the rest of `effective` (`stop_sequences`, `thinking`, `output_config`, sampling).
  - Message rules: leading `system` messages → top-level `system` (joined with blank lines); any later `system` message is folded into the next user turn as a leading text block; consecutive same-role turns merged; empty text blocks and empty messages dropped; `image_url` data-URI parts → `{"type":"image","source":{"type":"base64","media_type","data"}}`.
  - SSE: `yield ""` for every line received (proof of life through silent thinking), text deltas yielded, `thinking_delta` → `llm_reasoning.feed`; `event: error` frames raise (overloaded → `rate_limit`, others `bad_response`/`network`); usage from `message_start` (`prompt_tokens` = input + cache read + cache write; cache split) and `message_delta` (`output_tokens`); `stop_reason: "refusal"` with no text → `bad_response` "the model declined (<category>)".
  - Every raw SSE line and error body → `llm_capture.emit`; every error message through `content_parts.scrub`.
- `complete` joins `stream`; `list_models(key)` follows `has_more`/`last_id` (≤ 20 pages) → `catalog.entries`; `probe(key)` = `GET /v1/models?limit=1` (free).
- Errors: `AnthropicError(LLMError)` with `status=`; 401/403 `auth`; 429/529 `rate_limit` + `retry_after_seconds`; other ≥500 `network`; other ≥400 `bad_response` with `error.message`; transport → `network`.
- Registration as listed in Files; `LLMClient.aclose` closes it; Task 1's kind assertion tightened to `KINDS`.
- [ ] **Step 1: Failing tests** (MockTransport): body/headers for the folding cases, image block, effective merge, default and capped `max_tokens`; SSE incl. pings, thinking, usage, error frame, refusal; each status mapping incl. `status=` and `retry-after`; capture called; scrub applied; pagination; probe GET; dispatch from `LLMClient`; partition and mirror tests green.
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_inference_equivalence.py`.
- [ ] **Step 5: Commit** — `feat(llm): a direct Anthropic Messages API adapter`

---

### Task 8: OpenRouter embeddings for the new layout

**Files:** Modify `store/embed_space.py`; Test `tests/test_embed_space_role.py`

**Interfaces:** when the config is current-format (`translate.is_current(cfg)`), an `openrouter` Embedding provider resolves with `base_url = providers.PRESETS["openrouter"].base_url` and its key (keyless → None); `space` unchanged. A legacy config naming an OpenRouter connection still resolves to None (Review Focus 5).
- [ ] **Step 1: Failing tests:** both cases; MockTransport request goes to `/api/v1/embeddings`; frozen snapshot green.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** — `feat(embeddings): OpenRouter can serve the Embedding role`

---

### Task 9: The confirm-first model test call

**Files:** Create `store/inference/probes.py`; Modify `llm.py` (`LLMClient.single`), `routes/config.py`, `routes/models.py`; Test `tests/test_model_test_call.py`, `tests/test_llm.py`

**Interfaces:**
- `LLMClient.single(messages, conn, usage=None) -> str` — exactly one attempt on `conn`: no retries, no fallback, no degrade sibling; errors raise as from `stream`.
- `probes.PROBES`: `generate` (user "Reply with the single word: ok", `max_tokens` 64), `embed` (one fixed string), `vision` (1×1 PNG data-URI + "What colour is this pixel? One word."). Success = no error raised and the response completed (text not required).
- `POST /api/llm-connections/{id}/test/preview` `{model, capabilities}` → `{provider, model, sends: [{capability, description}], estimated_cost_usd: float|null}` (nothing sent).
- `POST /api/llm-connections/{id}/test` `{model, capabilities, confirm}`: `confirm` not `true` → 400 `"This test sends a request to the provider and may cost money — confirm to run it."`, nothing sent or metered; a capability in the preset's `never` → 400 before sending; else a 202 `draft` (`GLOBAL_SUBJECT`, kind `model-test`) that runs each probe via `LLMClient.single` (embed via `EmbeddingsClient` against the resolved endpoint) under `usage.meter("model-test")` (no campaign; the embed row carries no token counts), then `facts.record_verified(id, model, rev_at_start, results)` unless the provider's `rev` moved.
- CLAUDE.md "Detached runs" inventory updated: one more `draft` (the computing previews count and the handler count move).
- [ ] **Step 1: Failing tests** (`llm_fakes`): preview sends nothing; run without confirm → 400, no ledger row; with confirm → one `model-test` row per probe; a configured fallback is never called and a 429 is not retried (Review Focus 3); an empty-text completion counts `ok: true`; provider error → `ok: false` with scrubbed message; key edit hides results; `never` capability refused; `test_docs_guard` green.
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_routing_guard.py tests/test_usage_guard.py tests/test_draft_runs.py`.
- [ ] **Step 5: Commit** — `feat(config): a model test call that asks before it spends`

---

### Task 10: Read APIs for slice C, docs, and the gate

**Files:** Modify `routes/config.py`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `CLAUDE.md` ("Adding an LLM call site?": the capability check); Test `tests/test_inference_read_api.py`

**Interfaces:**
- `GET /api/llm-connections/{id}/capabilities?need=<generate|vision|embed|decide>&model=<id>` → `{preset: {...}, groups: {fits: [...], unverified: [...]}, hidden: [{id, reason}], reason: str|null}`, each row a catalog entry plus `capabilities: {name: {value, source}}`; `reason` set when the whole provider is ruled out (e.g. z.ai for `embed`).
- `POST /api/inference/controls` `{preset_id, provider, model}` → `store/inference/controls.preview(...)` (`requested`, `effective`, `controls` with `source`); 404 for an unknown provider or preset.
- [ ] **Step 1: Failing tests:** grouping per need (OpenRouter embed excludes text models; z.ai embed empty with reason); the controls preview equals `Attempt.controls` for the same provider/model/preset.
- [ ] **Step 2:** Implement; CLAUDE.md; `pytest tests/test_docs_guard.py`.
- [ ] **Step 3:** `make check` (baselines shrink → `make baseline`).
- [ ] **Step 4: Commit** — `feat(config): capabilities and controls, readable for the screens`
- [ ] **Step 5: Gates:** final whole-branch review + adversarial review against the spec (Claude stand-ins).

---

## Rulings from the plan review (stand-in adversarial review, 14 findings)

1. Controls live in the gateway leaf `llm_sampling.effective(conn)`; the store wraps it; the wire is computed per attempt in `_provider` from the connection dict (so the facade's fallback is covered); the resolver lowers `model_features` onto the dict.
2. A preset asserts `yes` only for always-true facts; "can do at all" means "not ruled out".
3. The test call runs once (no retries, no fallback); success = completed; generate probe cap 64.
4. All three picker-facing catalog responses are filtered to text-output rows.
5. Legacy `vision` is a post-image setting only; capability refusal after missing-key; adapter-no keeps today's message (now naming the Anthropic API); override and soft paths refuse alike.
6. An incapable fallback is reported in B and enforced in C; a test pins that the facade still sends.
7. Anthropic adapter: error frames, per-line proof of life, `status=`, capture + scrub, thinking ⇒ no sampling, `max_tokens` capped by catalog, exact folding rule, empty blocks dropped.
8. `_connection_ready`, `aclose`, `UNSUPPORTED` text covered; `RerollRoute` gives anthropic no catalog list until slice C (accepted).
9. `reasoning_effort` validated with a choice branch; `table()` unchanged until slice C.
10. OpenRouter embeddings only for the new-layout role.
11. Task 1 asserts against `KINDS + ("anthropic",)`; Task 7 tightens.
12. `Attempt.facts` added; grouping server-side (`group_for`); response key `effective`; `provider_preset`; CLAUDE.md detached-runs inventory updated in Task 9.
13. `facts` writes serialized by a process lock.
14. The embed probe's ledger row has no token counts until slice D.
