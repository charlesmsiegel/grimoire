# LLM Gateway — Design (Shipped)

> Captures the LLM Gateway as actually built. The matching "remaining" spec at `2026-05-16-llm-gateway-remaining-design.md` covers everything from the original `specs/05-llm-gateway.md` that did **not** land in this work.

**Commit:** `04c69d6` — "Build LLM Gateway core (task 13)" (follow-up: `b35e219`)
**Module:** `backend/src/grimoire/llm_gateway/`
**Tests:** `backend/tests/llm_gateway/` (`test_gateway.py`, `test_cache.py`, `test_request_log.py`, `test_retry.py`, `test_routing.py`)

## Purpose

`LLMGatewayService` is the one place every consumer module (Context Builder, Extractor, Continuity LLM judge, Observability replayer, Orchestrator streaming) goes to call an LLM or embedding provider. It owns per-task routing, retries with fallback, the embedding cache, and the per-call audit row written to `llm_requests`.

The gateway treats both LLM and embedding providers as plugins loaded by the Plugins module (`grimoire.plugins`). It looks them up by id at every call — no internal registry, no warm-up — so a freshly installed provider plugin is usable on the next request.

## Module surface

`LLMGatewayService` (`llm_gateway/gateway.py:39`) is constructed with three collaborators:

- `plugins: Plugins` — provider lookup (`get_llm_provider`, `get_embedding_provider`, `llm_providers`, `embedding_providers`)
- `db: Database` — the gateway owns two tables: `llm_requests` (audit) and `embedding_cache` (LRU)
- `config: GatewayConfig | None` — defaults are fine; an empty `default_routes` dict means every call will raise `RouteNotFoundError` until `set_route` is called

Three helpers are constructed inside `__init__` and not exposed:

- `RouteResolver` (`routing.py:36`) — defaults + per-campaign overrides + per-task fallback
- `EmbeddingCache` (`cache.py:34`) — async-safe wrapper around `embedding_cache`
- `LLMRequestLog` (`request_log.py:36`) — one INSERT per call

## Public API

```python
class LLMGatewayService:
    # Completion
    async def complete(task, request, campaign_id=None, *, turn_id=None) -> CompletionResponse
    async def stream(task, request, campaign_id=None, *, turn_id=None) -> AsyncIterator[CompletionChunk]

    # Embedding
    async def embed(task, texts, campaign_id=None, *, turn_id=None) -> list[list[float]]

    # Introspection
    async def list_llm_providers() -> list[LLMProvider]
    async def list_embedding_providers() -> list[EmbeddingProvider]
    async def list_routes(campaign_id=None) -> dict[str, str]
    async def set_route(task, route, campaign_id=None) -> None

    # Estimation
    async def estimate_tokens(text, provider_id=None) -> int
    async def estimate_cost(task, request, campaign_id=None) -> float | None

    # Health (on-demand only)
    async def health_check(provider_id) -> HealthStatus
    async def health_check_all() -> dict[str, HealthStatus]
```

The protocol the gateway satisfies lives at `types/protocols.py:208` (`LLMGateway`); the provider protocols it consumes are `LLMProvider` (`types/protocols.py:178`) and `EmbeddingProvider` (`types/protocols.py:194`).

## Routing

`Route` (`routing.py:21`) is a dotted `provider.model` string. `Route.parse("anthropic.claude-opus-4-7")` splits at the first `.` — anything without a `.` (or with empty halves) raises `ValueError`. Models with literal dots survive because partition only splits once.

`RouteResolver` keeps three maps:

- `_defaults: dict[task, raw]` — seeded from `GatewayConfig.default_routes`
- `_fallbacks: dict[task, raw]` — seeded from `GatewayConfig.fallback_routes`
- `_campaigns: dict[CampaignId, dict[task, raw]]` — populated lazily by `set_route(..., campaign_id=...)`

`resolve(task, campaign_id)` looks at the per-campaign map first, then the default. Miss raises `RouteNotFoundError`. `fallback(task)` is consulted only after the primary path has exhausted retries (see below). `routes_for(campaign_id)` merges defaults with that campaign's overrides for introspection.

There is **no persistence** of routes: `set_route` mutates in-process dicts. A restart loses every per-campaign override unless the host app re-seeds them from somewhere durable.

## Completion flow (`complete`)

1. Resolve `primary` and `fallback` routes for the task
2. Call `_invoke_complete(primary)`:
    - Look up the provider (`_require_llm` → `ProviderNotFoundError` if missing)
    - `request.model_copy(update={"model": route.model})` — the route's model wins; callers can pass `model=""` and the gateway fills it in
    - Wrap the provider call in `asyncio.wait_for(provider.complete(scoped), timeout=total_seconds)`
    - Drive it through `run_with_retries(..., policy=config.retry)`
    - On success: backfill `usage.total_tokens` if the provider returned 0 but split tokens are non-zero; backfill `latency_ms` if the provider didn't set it; INSERT one `llm_requests` row when `observability.log_all_requests` is `True`
3. On `PermanentError`: write a failure row with `retries=0` and re-raise — no fallback, no retry (`b35e219` fixed an earlier bug where the retry counter was logged here)
4. On retriable exhaustion: write a failure row with `retries=config.retry.max_retries`, then try the fallback route. If `fallback is None` or `fallback.raw == route.raw`, re-raise. Fallback attempts log their own row with `fallback_used=1`

`_record_failure` swallows its own logging exceptions — a write failure on the audit row never masks the real provider error.

## Streaming flow (`stream`)

`stream` is **not** retried — partial responses can't be safely re-fetched. The gateway resolves the route once, scopes the model, and yields chunks from the provider.

Two timeouts apply per chunk:

- First chunk: `config.timeout.first_token_seconds` (default 30.0)
- Subsequent chunks: whatever is left of `config.timeout.total_seconds` (default 120.0) since stream start

A budget of `<= 0` raises `TimeoutError(f"stream exceeded total timeout of {N}s")`. The generator accumulates `chunk.delta` into `text_parts` and grabs `chunk.usage` from any chunk that carries one (usually the final). On normal completion (`StopAsyncIteration` or `is_final=True`) the gateway logs the assembled response. On `PermanentError` or any retriable exception it logs a failure row and re-raises. The provider stream's `aclose()` is called in `finally` (best-effort; exceptions suppressed).

## Embedding flow (`embed`)

1. Resolve the route, look up the embedding provider (`_require_embedding`)
2. Pull cache hits via `EmbeddingCache.get_many(texts, provider.model_id)` (skipped if `embedding_cache.enabled=False`)
3. Compute the `missing` list — first-seen order preserves caller ordering
4. If `missing` is non-empty:
    - Single batch call to `provider.embed(missing)` with the same retry policy as completions
    - Validate that the returned vector count matches the input count (`GatewayError` otherwise)
    - `EmbeddingCache.set_many` writes the new vectors and runs eviction
    - Log one row using `TokenUsage(input_tokens=max(1, sum(len(t)//4 for t in missing)))`. The `len(t)//4` token estimate (rather than raw char count) is the fix from `b35e219`
5. Return vectors in the original input order, reusing the cache map for hits

Empty input lists short-circuit to `[]` before any provider lookup.

## Embedding cache

`EmbeddingCache` (`cache.py:34`) stores vectors as little-endian float32 blobs (the encoding `sqlite-vec` expects). Keys are `(sha256(text_utf8), model_id)`.

Operations:

- `get_many(texts, model_id)` deduplicates by hash, fetches in a single `IN (...)` query, and updates `cached_at` for every hit via `_touch_many` so the column doubles as LRU access timestamp
- `set_many(items, model_id)` runs inside `self._write_lock` + a `db.acquire()` connection so the INSERTs + eviction happen on one connection; each entry is `INSERT OR REPLACE`
- Eviction (`_evict_if_needed`) is "delete oldest by `cached_at` until count <= `max_entries`". Only runs after writes — reads never evict
- `clear()` truncates the table; `count()` reports current size

There is no in-process tier — every lookup hits SQLite. `max_entries` defaults to 100 000 and must be `>= 1` (validated in `__init__`).

The `embedding_cache` table is created in migration `008_llm_observability.sql:26`. The schema diverges slightly from spec 05: the column is named `cached_at` (not `created_at`), and there is no `last_used_at` column — `cached_at` is updated on every hit and serves as the LRU key.

## Retry policy (`retry.py`)

`run_with_retries(fn, policy)`:

- Returns `(result, retries_used)` — `0` means first attempt succeeded
- Retries on `TransientError`, `RateLimitError`, builtin `TimeoutError`, and `asyncio.TimeoutError`
- `PermanentError` and its subclasses (`AuthenticationError`, `InvalidRequestError`, `ContentFilterError`) raise immediately
- Backoff: `delay = initial_delay_ms / 1000`, multiplied by `backoff_factor` after each sleep; the `sleep` callable is injectable for tests
- After `max_retries` retriable failures it re-raises the most recent exception

The actual `RETRIABLE_EXCEPTIONS` tuple is exported and reused by the gateway when catching for fallback / failure logging.

## Error hierarchy (`errors.py`)

```
GatewayError
├── RouteNotFoundError          (no default + no campaign override for task)
├── ProviderNotFoundError       (route points at an unloaded provider id)
├── TransientError              (retriable)
│   └── RateLimitError
└── PermanentError              (surfaced immediately)
    ├── AuthenticationError
    ├── InvalidRequestError
    └── ContentFilterError
```

`TimeoutError` (builtin) and `asyncio.TimeoutError` are also retriable but live outside the hierarchy.

## Request log (`request_log.py`)

Writes one row per call (`llm_requests`, migration `008_llm_observability.sql:3`). Columns differ from spec 05:

- `prompt_tokens` / `completion_tokens` / `total_tokens` (not `input_tokens` / `output_tokens`)
- `request_hash` (`sha256` over `model + system + messages + max_tokens + temperature + stop_sequences`) instead of the full `request_payload`
- `response_excerpt` (first `response_excerpt_chars` of the response, default 200, suppressed unless `observability.log_response_text=True`) instead of the full `response_text`
- `retries` and `fallback_used` columns added so per-call retry behavior is queryable
- No `branch_id` column

Per-campaign and per-task indexes are created in the same migration (`idx_llmreq_campaign`, `idx_llmreq_task`).

`request_hash(request)` (`request_log.py:20`) is also used by callers wanting to dedupe identical requests.

## Estimation

- `estimate_tokens(text, provider_id=None)`: calls `provider.estimate_tokens(text)` if the provider exposes one, else falls back to `max(1, len(text) // 4)`. The cheap default is also used for the `embed` audit row's token count
- `estimate_cost(task, request, campaign_id=None)`: looks at the provider's `list_models()`, finds the entry for the routed model, and computes `prompt_tokens/1000 * input_cost + max_tokens/1000 * output_cost`. Prompt tokens use the same `chars // 4` heuristic. Returns `None` if the provider, model, or pricing fields are missing

Neither estimator hits the network.

## Health checks

The gateway exposes two on-demand methods:

- `health_check(provider_id)`: looks up the provider (LLM first, then embedding). Unknown id → `HealthLevel.UNCONFIGURED`. Provider without a `health_check` attribute → `HEALTHY` (presence is enough). Probe exception → `UNHEALTHY` with `f"{type(exc).__name__}: {exc}"` as the message. If the probe returns a `HealthStatus`, it's passed through verbatim
- `health_check_all()`: union of every LLM + embedding provider id, sorted, each probed serially

There is **no periodic monitoring loop in the gateway**. Continuous health probing + the `provider_health_changed` event live in `observability/health.py` (`HealthMonitorService`), which is a separate service the host wires up independently — see the remaining-design spec §periodic health monitoring.

## Configuration (`GatewayConfig`)

`GatewayConfig` is a frozen dataclass with the spec 05 §Configuration shape:

```python
GatewayConfig(
    default_routes={},                   # task -> "provider.model"
    fallback_routes={},                  # task -> "provider.model"
    retry=RetryConfig(max_retries=3, initial_delay_ms=500, backoff_factor=2.0),
    timeout=TimeoutConfig(total_seconds=120.0, first_token_seconds=30.0),
    embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100_000),
    observability=ObservabilityConfig(
        log_all_requests=True,
        log_response_text=False,         # privacy default
        response_excerpt_chars=200,
    ),
)
```

The host (`backend/src/grimoire/main.py:163`) currently constructs the gateway with `GatewayConfig()` defaults — no routes are seeded from app settings or campaign YAML, so the orchestrator's first turn raises `RouteNotFoundError` until something (a setup wizard, a test fixture, an HTTP call) populates routes via `set_route`. See the remaining-design spec §config loading.

## Concurrency

- Completion and streaming methods don't take any locks — N concurrent calls on N different routes/providers run in parallel; serialization is the provider's problem
- The embedding cache has a single `asyncio.Lock` around writes (`set_many` / `clear`). Reads are lock-free
- The request log's INSERT goes through `Database.execute` which manages its own connection pool

## Test surface

`backend/tests/llm_gateway/conftest.py` ships `FakeLLMProvider`, `FakeEmbeddingProvider`, and `FakePlugins` with the minimal shape the gateway needs (`llm_providers`, `embedding_providers`, `get_llm_provider`, `get_embedding_provider`). `db` is a per-test temporary SQLite database with full migrations applied.

`test_gateway.py` covers: route + model override, missing-route / missing-provider errors, transient-retry counting, fallback after exhaustion (with two audit rows), stream chunk passthrough + final-chunk usage logging, embedding cache hit/miss / order preservation / disabled-cache passthrough, per-campaign `set_route`, cost estimation with pricing, token heuristic, health check passthrough + unknown + aggregate, `log_all_requests=False` suppression, `PermanentError` audit row, and the `len(t)//4` token estimate for embeddings.

`test_cache.py`, `test_request_log.py`, `test_retry.py`, `test_routing.py` cover their respective helpers in isolation.
