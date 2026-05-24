# Performance Observability and Optimization

Date: 2026-05-23
Status: Approved
PR: 11 of ~11 in the Grimoire code quality refactor series
Depends on: PR 5 (ContextBuilder split), PR 6 (Large service splits), PR 9 (Event bus formalization), PR 10 (Frontend restructuring)

## Problem

Performance bottlenecks are invisible. Context building runs serially without per-section timing. The LLM gateway has no metrics for route resolution latency, retry counts, or cache hit rates. The frontend does full `refresh()` calls after WebSocket events that already carry updated state, and settings/library views rebuild large view models on every render.

## Solution

Add per-section metrics to backend hot paths, cache stable context sections independently, and optimize frontend data flow to avoid redundant fetches and renders.

## Detailed Design

### Step 1: Per-Section Context Metrics

**Depends on:** PR 5 (ContextBuilderService split into providers)

Each `ContextProvider` emits timing metrics via the existing `MetricsRegistryProtocol`:

```python
async def resolve(self, request: ContextBuildRequest) -> list[ContextSection]:
    with self._metrics.timer("context.cast.resolve_ms"):
        return await self._resolve_inner(request)
```

Metrics to emit per provider:
- `context.{provider}.resolve_ms` — wall-clock time for the provider
- `context.{provider}.tokens` — estimated tokens produced
- `context.{provider}.items` — number of context items produced

Add a summary metric on `ContextBuilderService.build()`:
- `context.build.total_ms` — total wall-clock time for full build
- `context.build.tier_overflow_count` — items dropped due to budget

### Step 2: LLM Gateway Metrics

**Depends on:** PR 6 (LLMGatewayService split)

Add metrics to the extracted `RouteResolver`, `CompletionClient`, `StreamClient`, and `EmbeddingClient`:

| Metric | Source | Purpose |
|--------|--------|---------|
| `gateway.route.resolve_ms` | `RouteResolver` | Time to resolve task → provider/model |
| `gateway.completion.first_token_ms` | `StreamClient` | Time to first streamed token |
| `gateway.completion.total_ms` | `CompletionClient`/`StreamClient` | Total request duration |
| `gateway.completion.retry_count` | `CompletionClient` | Number of retries before success |
| `gateway.embedding.batch_size` | `EmbeddingClient` | Texts per embedding request |
| `gateway.embedding.cache_hit_rate` | `EmbeddingClient` | Cache hits / total lookups |
| `gateway.audit.write_ms` | `GatewayAuditLog` | Time to persist audit record |

### Step 3: State Store Transaction Metrics

Add metrics to `StateStore._txn()` context manager:

- `store.txn.wait_ms` — time waiting to acquire connection from pool
- `store.txn.exec_ms` — time holding the connection (actual SQL execution)
- `store.txn.queue_depth` — pool queue depth at acquisition time

### Step 4: Cache Stable Context Sections

**Depends on:** PR 5 (provider extraction), PR 8 (event-driven cache invalidation)

Cache outputs of providers that change rarely:

| Provider | Cache Key | Invalidation Event |
|----------|-----------|-------------------|
| `WorldContextResolver` | `(campaign_id, composition_hash)` | `library_entity_changed` |
| `ContinuityContextResolver` (commitments block) | `(campaign_id, branch_id)` | `commitment_*`, `fact_recorded` |
| `ArchiveRetriever` (lore triggers) | `(campaign_id, composition_hash)` | `library_entity_changed` |

Volatile providers (`CastResolver`, recent posts, transient state) are never cached.

Cache implementation: simple `dict[key, (result, timestamp)]` on each provider. TTL as a safety net (5 minutes). Primary invalidation through event bus subscriptions.

### Step 5: Concurrent Context Provider Execution

**Depends on:** PR 5 (provider extraction)

Enable `asyncio.gather()` for independent providers in `ContextBuilderService._build_context()`:

```python
cast, world, continuity, archive = await asyncio.gather(
    self._cast.resolve(request),
    self._world.resolve(request),
    self._continuity.resolve(request),
    self._archive.resolve(request),
    return_exceptions=True,
)
```

Each provider that raises is logged and produces an empty section (graceful degradation). Gate behind `config.concurrent_providers: bool = True` so it can be disabled if issues arise.

### Step 6: Frontend — Avoid Redundant Refresh

**Depends on:** PR 10 (usePlayState extraction)

In `usePlayStreamEvents.ts`, events that carry full updated state should dispatch directly to the reducer instead of triggering a REST re-fetch:

| Event | Current Behavior | New Behavior |
|-------|-----------------|-------------|
| `deltas_applied` | Full refresh | Dispatch `deltas_applied` action with payload |
| `alternate_added` | Full refresh | Dispatch `alternate_added` with new alternate data |
| `primary_switched` | Full refresh | Dispatch `primary_switched` with new primary ID |
| `image_ready` | Full refresh | Dispatch `image_ready` with image metadata |

Keep full refresh as fallback for events that don't carry enough data (e.g., `turn_complete` which requires reloading scene state).

### Step 7: Frontend — Memoized Selectors

Add `useMemo`-based selectors for expensive derived state in settings and library views:

- `CampaignSettings`: memoize the active tab's data derivation
- Library tables: memoize filtered/sorted lists (currently recomputed on every render)
- `CalendarsView`: memoize holiday/event lists

### Step 8: Frontend — Resource Caches for Static Settings

Campaign settings (routing, tiers, imagegen config) change rarely but are re-fetched on every settings panel mount. Add a resource cache in the API layer:

```typescript
const settingsCache = new Map<string, { data: unknown; fetchedAt: number }>();
const SETTINGS_TTL_MS = 30_000; // 30 seconds

export async function getCampaignRouting(campaignId: string): Promise<RoutingConfig> {
  const key = `${campaignId}/routing`;
  const cached = settingsCache.get(key);
  if (cached && Date.now() - cached.fetchedAt < SETTINGS_TTL_MS) {
    return cached.data as RoutingConfig;
  }
  const data = await api.get<RoutingConfig>(`/api/campaigns/${campaignId}/routing`);
  settingsCache.set(key, { data, fetchedAt: Date.now() });
  return data;
}
```

Invalidate on write (any PUT to the same endpoint clears its cache entry).

## Scope

### Step 9: Frontend Render Marks

Add `performance.mark()` / `performance.measure()` instrumentation to key frontend render paths so slow renders are visible in browser DevTools:

- Campaign settings tab switch
- Play view initial load and refresh
- Scene jump (switching active scene)
- Library table render (large entity lists)

Wrap in a utility:

```typescript
function measureRender(name: string, fn: () => void): void {
  performance.mark(`${name}-start`);
  fn();
  performance.mark(`${name}-end`);
  performance.measure(name, `${name}-start`, `${name}-end`);
}
```

### In scope
- Per-section metrics for context building (7 metrics)
- LLM gateway metrics (7 metrics)
- State store transaction metrics (3 metrics)
- Cache stable context provider outputs (3 providers)
- Enable concurrent context providers via `asyncio.gather()`
- Frontend: avoid redundant refresh for 4 event types
- Frontend: memoized selectors for settings/library views
- Frontend: resource caches for campaign settings
- Frontend: render marks for key render paths

### Not in scope
- Distributed caching (Redis, etc.)
- Backend startup concurrency (covered in PR 3)
- Query optimization or SQL index changes

## Verification

1. `pytest` full suite passes.
2. Context build metrics are emitted and visible in observability endpoint.
3. Gateway metrics are emitted for completion, streaming, and embedding calls.
4. Cached context providers return cached results on second call (test with mock clock).
5. Concurrent providers run in parallel (test with artificial delay, verify wall-clock < sum of individual times).
6. Frontend: WebSocket events with payload don't trigger REST re-fetch (verify in browser network tab).
7. Frontend: settings cache prevents duplicate fetches on tab switch (verify in browser network tab).
