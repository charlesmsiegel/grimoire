# LLM Gateway — Remaining Work

> Everything from the original `specs/05-llm-gateway.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-llm-gateway-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-llm-gateway-design.md`
**Module:** `backend/src/grimoire/llm_gateway/`

## 1. Event-bus emissions

Spec 05 §Events emitted promises five lifecycle events:

- `llm_request_started` (task, provider, model)
- `llm_response_received` (with usage and latency)
- `llm_request_failed` (with error)
- `embedding_request_started` / `embedding_response_received`
- `provider_health_changed` (id, old → new status)

All five enum values exist in `backend/src/grimoire/types/orchestrator.py:66-71` but the gateway has no `EventBus` dependency — `llm_gateway/gateway.py` never imports or emits anything. Consumers see audit rows in `llm_requests` after the fact but can't subscribe to live progress.

Design: add `event_bus: EventBus | None = None` to `LLMGatewayService.__init__`, emit `llm_request_started` at the top of `_invoke_complete` / `_stream_one` / `embed`, emit `llm_response_received` (resp. `embedding_response_received`) just before returning, and `llm_request_failed` in both `_record_failure` paths. Include the same payload fields used by the audit log (task, provider, model, latency, retries, fallback_used). `provider_health_changed` is owned by the periodic monitor in §3.

## 2. Config loading from app settings + campaign YAML

Spec 05 §Routing: "Routing can be set per-campaign (in `campaign.yaml`) or globally (in app config); per-campaign overrides global."

Today the gateway is constructed in `backend/src/grimoire/main.py:163` with a bare `GatewayConfig()` — empty `default_routes`, no fallbacks, no campaign overrides loaded. The very first `gateway.complete("main", ...)` raises `RouteNotFoundError`. The `RouteResolver.set_route` mutation is in-memory and lost on restart.

Needs:

1. A `llm_gateway:` section in `grimoire.config.settings` (likely a Pydantic model mirroring `GatewayConfig`) populated from env + a YAML/TOML file, then passed into `LLMGatewayService(config=...)` at startup
2. Per-campaign YAML loading: when a campaign is loaded, read its `model_routing:` block and call `gateway.set_route(task, route, campaign_id=...)` for each entry. The natural home is wherever campaigns are loaded (currently lives near `api/campaigns.py` and the orchestrator boot path)
3. Persistence of `set_route` mutations made at runtime (e.g., from a settings UI) — either write back to the campaign YAML or to a new `campaign_route_overrides` table

## 3. Periodic health monitoring + `provider_health_changed` event

Spec 05 §Health monitoring: "The Gateway runs `health_check()` periodically on active providers. UI shows green/yellow/red. Failed providers fall back to alternates if configured."

The gateway exposes `health_check` and `health_check_all` (on-demand only). A periodic `HealthMonitorService` exists at `backend/src/grimoire/observability/health.py:39` but nothing registers the gateway's providers with it. Symptoms:

- The UI cannot show provider status without explicitly polling `health_check_all` on every render
- `provider_health_changed` is never emitted
- The spec's claim that "failed providers fall back to alternates" is half-true — fallback fires on a *request failure*, not on a *health-state transition*

Needs: at gateway startup (or whenever the plugin set changes), iterate `plugins.llm_providers() + plugins.embedding_providers()` and call `health_monitor.register_probeable(HealthTarget(...), provider)`. Subscribe a handler that emits `provider_health_changed` when the level changes. Decide whether "auto-fallback on UNHEALTHY" should switch the resolver's active route (probably no — keep fallback request-driven to avoid flapping).

## 4. Wire `turn_id` from orchestrator + extractor callers

`gateway.complete`, `stream`, `embed` already accept `turn_id` as a keyword-only argument and the `llm_requests` table has a `turn_id` column. But:

- `orchestrator/service.py:588` calls `self._gateway.stream(task, request, campaign_id=...)` without a `turn_id`
- `extractor/llm_strategy.py:418` calls `gateway.complete(task, request, campaign_id=campaign_id)` without one
- `continuity/llm_judge.py:96` and `context/builder.py:704` likewise

Pass `turn_id` from every site that has one in scope so per-turn requests are queryable. Mechanical change but needed before the Observability replayer can group requests by turn reliably.

## 5. `cost_estimate_usd` populated on completion responses

`CompletionResponse.cost_estimate_usd` exists in `types/llm.py:76` and the gateway respects whatever the provider sets, but `_invoke_complete` does not fill it in when the provider leaves it `None`. The `estimate_cost(task, request)` helper already knows how to compute it from `ModelInfo.input_cost_per_1k` / `output_cost_per_1k`.

Needs: after the response comes back, if `cost_estimate_usd is None`, look up the model's pricing and compute `prompt_tokens/1000 * input_cost + completion_tokens/1000 * output_cost` from the *actual* `TokenUsage` (not the heuristic) and stash it on the response + the `llm_requests.cost_usd` column. Avoid one `list_models()` call per request by caching pricing per `(provider_id, model)`.

## 6. Streaming retry / fallback policy decision

Today `_stream_one` does not retry at all and does not consult `fallback`. The spec is silent on the right behavior because partial responses can't be cleanly re-fetched, but two real failure modes deserve handling:

- **First-chunk timeout / first-chunk transient error**: zero bytes delivered, safe to retry or fall back. Today `first_token_seconds` fires a `TimeoutError`, gets logged, and propagates uncaught to the caller
- **Mid-stream provider crash**: not safely retriable; the caller decides whether to surface the partial. Current behavior is "raise; partial is lost" because `_stream_main_response` in the orchestrator doesn't catch from the gateway iterator

Pick one of:

(a) Retry / fall back **only** if zero chunks have been delivered (track `first` already on the iterator)
(b) Always raise; document that streaming is single-attempt and require callers to retry at their level

(a) is more useful for the cloud→local fallback case described in spec 05 §Routing. Either way, write the rule into the docstring.

## 7. Per-call retry / timeout overrides

`CompletionRequest.metadata` is the spec's escape hatch for "provider-specific extras", but a common need is **per-call** override of `RetryConfig` / `TimeoutConfig` — e.g., the drift-check task wants a tighter timeout than the main turn. Today the gateway always uses the global `config.retry` / `config.timeout`.

Design: optional `retry: RetryConfig | None = None`, `timeout: TimeoutConfig | None = None` keyword args on `complete` / `stream` / `embed` that fall through to the global config when unset. Threading them into `_invoke_complete` is trivial; the audit row should record any override so post-hoc analysis is honest.

## 8. Embedding batch-size honoring

Spec 05 §Embedding provider protocol: "The Gateway batches embedding requests (provider-specific batch sizes), caches results by text+model hash, and returns vectors in input order."

Today `embed` sends every missing text in a **single** call to `provider.embed(missing)`. A 10 000-text reindex against an OpenAI provider with a 2 048-input limit will fail. The provider protocol exposes `dimensions` but not `max_batch_size`.

Needs:

1. Add an optional `max_batch_size: int | None` attribute to the `EmbeddingProvider` protocol (`types/protocols.py:194`). Providers that omit it get sent in one shot, preserving today's behavior
2. In `embed`, chunk `missing` into batches of `max_batch_size` (when set), call `provider.embed` per batch, concatenate, validate aggregate count, then `set_many` once at the end
3. Decide whether failure of one batch fails the whole request or commits the successful ones (lean toward all-or-nothing for simpler semantics)

## 9. `RetryPolicy` / `TimeoutPolicy` pydantic types vs. dataclass configs

`types/llm.py:86-97` defines `RetryPolicy` and `TimeoutPolicy` as pydantic models with `retry_on: list[str]` — a typed surface meant for API / config serialization. `llm_gateway/config.py:16-25` defines `RetryConfig` / `TimeoutConfig` as frozen dataclasses with no `retry_on` field (the retriable list is hardcoded as `RETRIABLE_EXCEPTIONS` in `retry.py:16`).

Two artifacts modeling the same concept will rot. Pick one:

(a) Use the pydantic types throughout and parse `retry_on` strings into exception classes at construction time
(b) Delete the pydantic types and expose the dataclass config in the public API surface

(a) is the only way to make `retry_on` actually configurable from YAML.

## 10. Health check normalization

`gateway.health_check(provider_id)` returns the provider's raw `HealthStatus` if it returns one, or fabricates a `HEALTHY` if the provider has no `health_check` method. But:

- The provider may set `HealthStatus.target_id` to something different from `provider_id` (e.g., a model name); today that's passed through verbatim and breaks downstream lookups
- `checked_at` is never populated by the gateway's own fabricated statuses

Force `target_id = provider_id` and set `checked_at = datetime.now(UTC).isoformat()` on every return path. Trivial fix; deferred only because nothing currently relies on it.

## 11. Cross-provider routing within a single turn (v2; deferred)

Spec 05 §Open questions: "Use a cheap model for the first draft, then a high-quality model for the rewrite?" Recorded so it doesn't get re-litigated; no design needed in v1.

## 12. Reasoning model surfacing (v2; deferred)

Spec 05 §Open questions: explicit thinking tokens / reasoning steps in the audit trail. Defer until at least one bundled provider has the capability we care about.

## 13. First-class multi-modal inputs (v2; deferred)

Spec 05 §Open questions: vision-capable models with image inputs. Today's `CompletionRequest.metadata` is the documented escape hatch; promote to typed fields only when there is a concrete consumer.

## 14. Cost budget enforcement (v2; deferred)

Spec 05 §Open questions: pause turns when a per-campaign budget is exceeded. Needs cost rollups (the `cost_records` table from migration 008 already exists, owned by Observability) and a check inside `complete` / `stream` before invocation. Out of scope for v1.

## 15. Provider auto-selection (v2; deferred)

Spec 05 §Open questions: pick the best route based on task + cost + latency. Requires §3 health, §5 cost-fill, and a policy engine; defer.

## 16. Tool use (v2; deferred)

Spec 05 §Open questions: function calling. Useful for mechanics integration but not required for v1. The `ProviderCapabilities.tools: bool` flag exists but no completion plumbing surfaces tool definitions.

## 17. Multi-channel response object (rejected)

Spec 05 only ever returns `CompletionResponse.text`. Not requested anywhere in the original spec; tool results / images / structured output would all be additive features and live under §16 above. Treat as **rejected** — do not add multi-content typing without a concrete need.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §2 — config loading. Without it the gateway is unusable past the first turn. Smallest blocker
2. §4 — thread `turn_id` from the four existing callers. Mechanical and unlocks per-turn audit queries the Observability replayer wants
3. §1 — event-bus emissions. Builds on §4 (events carry `turn_id`)
4. §5 + §10 — fill in `cost_estimate_usd` and normalize `HealthStatus`. Both are tiny correctness fixes that touch the same shipped code paths
5. §8 — embedding batch-size honoring. Required before any production-scale reindex against a cloud embedding provider
6. §3 — periodic health monitoring + `provider_health_changed`. Largest of the wiring items; depends on §1 for emission
7. §6 — streaming retry / fallback decision. Requires a design call; ship after §3 so a real degradation signal exists
8. §7 — per-call retry / timeout overrides. Quality-of-life; defer until the drift-check / extractor tasks complain
9. §9 — collapse the duplicate retry/timeout types. Refactor only; do it once everything above is stable
