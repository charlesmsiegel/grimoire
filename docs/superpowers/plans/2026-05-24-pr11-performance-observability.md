# PR 11: Performance Observability and Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-section metrics to backend hot paths, cache stable context sections, enable concurrent context providers, and optimize frontend data flow.

**Architecture:** Backend metrics use the existing `MetricsRegistryProtocol`. Context provider caching uses in-memory dicts with TTL + event-bus invalidation. Concurrent providers use `asyncio.gather()` behind a config flag. Frontend reduces redundant REST fetches by dispatching WebSocket event payloads directly to the reducer.

**Tech Stack:** Python 3.12+, asyncio, React, TypeScript

**Depends on:** PR 5 (ContextBuilder split), PR 6 (Gateway split), PR 8 (cache invalidation events), PR 9 (typed events), PR 10 (usePlayState extraction)

---

### Task 1: Add per-section context metrics

**Files:**
- Modify: `backend/src/grimoire/context/cast.py`
- Modify: `backend/src/grimoire/context/world_context.py`
- Modify: `backend/src/grimoire/context/continuity_context.py`
- Modify: `backend/src/grimoire/context/archive.py`
- Modify: `backend/src/grimoire/context/assembler.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Add metrics timer to each provider's resolve()**

Each provider takes a `metrics` parameter in its constructor (passed from `ContextBuilderService`). Wrap `resolve()` in a timer:

```python
async def resolve(self, request: ContextBuildRequest) -> list[ContextSection]:
    start = time.perf_counter()
    try:
        result = await self._resolve_inner(request)
        return result
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._metrics.record("context.cast.resolve_ms", elapsed_ms)
        self._metrics.record("context.cast.items", sum(len(s.items) for s in result))
```

Add summary metric on `ContextBuilderService.build()`:
```python
self._metrics.record("context.build.total_ms", elapsed_ms)
```

- [ ] **Step 2: Run tests, commit**

```
git commit -m "feat(context): add per-section timing metrics to context providers"
```

---

### Task 2: Add LLM gateway metrics

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/route_resolver.py` (or `gateway.py` if PR 6 hasn't split yet)
- Modify: `backend/src/grimoire/llm_gateway/completion_client.py`
- Modify: `backend/src/grimoire/llm_gateway/stream_client.py`
- Modify: `backend/src/grimoire/llm_gateway/embedding_client.py`

- [ ] **Step 1: Add metrics to each gateway component**

| Metric | Location |
|--------|----------|
| `gateway.route.resolve_ms` | RouteResolver |
| `gateway.completion.first_token_ms` | StreamClient |
| `gateway.completion.total_ms` | CompletionClient / StreamClient |
| `gateway.completion.retry_count` | CompletionClient |
| `gateway.embedding.batch_size` | EmbeddingClient |
| `gateway.embedding.cache_hit_rate` | EmbeddingClient |
| `gateway.audit.write_ms` | GatewayAuditLog |

Same `time.perf_counter()` pattern as Task 1.

- [ ] **Step 2: Run tests, commit**

```
git commit -m "feat(gateway): add route, completion, embedding, and audit metrics"
```

---

### Task 3: Add state store transaction metrics

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py`

- [ ] **Step 1: Add timing to _txn() context manager**

```python
@asynccontextmanager
async def _txn(self):
    wait_start = time.perf_counter()
    async with self.db.acquire() as conn:
        wait_ms = (time.perf_counter() - wait_start) * 1000
        self._metrics.record("store.txn.wait_ms", wait_ms)
        exec_start = time.perf_counter()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            yield conn
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
        finally:
            exec_ms = (time.perf_counter() - exec_start) * 1000
            self._metrics.record("store.txn.exec_ms", exec_ms)
```

Adapt to match the actual `_txn()` implementation — the above is the pattern, not the exact code.

- [ ] **Step 2: Run tests, commit**

```
git commit -m "feat(state_store): add transaction wait/exec timing metrics"
```

---

### Task 4: Cache stable context provider outputs

**Files:**
- Modify: `backend/src/grimoire/context/world_context.py`
- Modify: `backend/src/grimoire/context/archive.py`

- [ ] **Step 1: Add a simple TTL cache to WorldContextResolver**

```python
class WorldContextResolver:
    def __init__(self, ...):
        self._cache: dict[str, tuple[list[ContextSection], float]] = {}
        self._ttl = 300.0  # 5 minutes

    async def resolve(self, request: ContextBuildRequest) -> list[ContextSection]:
        key = f"{request.campaign_id}:{hash(str(request.composition))}"
        cached = self._cache.get(key)
        if cached and (time.time() - cached[1]) < self._ttl:
            return cached[0]
        result = await self._resolve_inner(request)
        self._cache[key] = (result, time.time())
        return result

    def invalidate(self, ref: str | None = None) -> None:
        if ref is None:
            self._cache.clear()
        else:
            self._cache = {k: v for k, v in self._cache.items() if ref not in k}
```

- [ ] **Step 2: Subscribe to library_entity_changed for invalidation**

Wire up via event bus in `ContextBuilderService.__init__` or during bootstrap:

```python
if event_bus:
    event_bus.subscribe(events.LIBRARY_ENTITY_CHANGED, self._world.invalidate)
```

- [ ] **Step 3: Same pattern for ArchiveRetriever's lore triggers cache**

- [ ] **Step 4: Run golden tests to verify output is identical**

Run: `cd backend && uv run pytest -m golden -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```
git commit -m "feat(context): cache stable world and archive provider outputs with TTL + event invalidation"
```

---

### Task 5: Enable concurrent context provider execution

**Files:**
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Add concurrent_providers config flag**

In `ContextBuilderConfig` (or wherever config lives), add:
```python
concurrent_providers: bool = True
```

- [ ] **Step 2: Run providers with asyncio.gather**

```python
async def _build_context(self, request: ContextBuildRequest) -> ContextSections:
    if self._config.concurrent_providers:
        results = await asyncio.gather(
            self._safe_resolve(self._cast, request),
            self._safe_resolve(self._world, request),
            self._safe_resolve(self._continuity, request),
            self._safe_resolve(self._archive, request),
            return_exceptions=True,
        )
        cast, world, continuity, archive = results
    else:
        cast = await self._safe_resolve(self._cast, request)
        world = await self._safe_resolve(self._world, request)
        continuity = await self._safe_resolve(self._continuity, request)
        archive = await self._safe_resolve(self._archive, request)
    # ... assemble ...

async def _safe_resolve(self, provider, request):
    try:
        return await provider.resolve(request)
    except Exception:
        logger.exception("context provider %s failed", type(provider).__name__)
        return []
```

- [ ] **Step 3: Run golden tests**

Run: `cd backend && uv run pytest -m golden -x -q`
Expected: All pass (output is deterministic regardless of execution order)

- [ ] **Step 4: Commit**

```
git commit -m "feat(context): enable concurrent context provider execution via asyncio.gather"
```

---

### Task 6: Frontend — avoid redundant refresh

**Files:**
- Modify: `frontend/src/routes/campaign/usePlayStreamEvents.ts`

- [ ] **Step 1: Dispatch event payloads directly for data-carrying events**

For events that carry enough data to update the store directly:

```typescript
case "deltas_applied":
  dispatch({ type: "deltas_applied", payload: event.payload });
  break;
case "alternate_added":
  dispatch({ type: "alternate_added", payload: event.payload });
  break;
case "primary_switched":
  dispatch({ type: "primary_switched", payload: event.payload });
  break;
case "image_ready":
  dispatch({ type: "image_ready", payload: event.payload });
  break;
```

Keep full refresh as fallback for `turn_complete` and other events that don't carry enough data.

- [ ] **Step 2: Add corresponding reducer cases in playReducer.ts**

```typescript
case "deltas_applied":
  return { ...state, deltaCount: state.deltaCount + action.payload.count };
case "image_ready":
  return { ...state, images: [...state.images, action.payload] };
```

- [ ] **Step 3: Verify in browser**

Open browser network tab. Submit a turn. Verify that `deltas_applied` events do NOT trigger a REST re-fetch.

- [ ] **Step 4: Commit**

```
git commit -m "perf(frontend): dispatch WS event payloads directly instead of full refresh"
```

---

### Task 7: Frontend — memoized selectors and resource caches

**Files:**
- Modify: `frontend/src/routes/campaign/settings/*.tsx`
- Modify: `frontend/src/api/campaign/settings.ts`

- [ ] **Step 1: Add useMemo to expensive derived state in settings tabs**

Wrap filtered/sorted lists in `useMemo` so they don't recompute on every render.

- [ ] **Step 2: Add resource cache for settings endpoints**

```typescript
const settingsCache = new Map<string, { data: unknown; fetchedAt: number }>();
const SETTINGS_TTL_MS = 30_000;

export async function getCampaignRouting(campaignId: string) {
  const key = `${campaignId}/routing`;
  const cached = settingsCache.get(key);
  if (cached && Date.now() - cached.fetchedAt < SETTINGS_TTL_MS) {
    return cached.data;
  }
  const data = await api.get(`/api/campaigns/${campaignId}/routing`);
  settingsCache.set(key, { data, fetchedAt: Date.now() });
  return data;
}
```

Invalidate on write: any PUT to the same endpoint calls `settingsCache.delete(key)`.

- [ ] **Step 3: Commit**

```
git commit -m "perf(frontend): add memoized selectors and settings resource cache"
```

---

### Task 8: Frontend — render marks

**Files:**
- Create: `frontend/src/util/renderMarks.ts`
- Modify key route components

- [ ] **Step 1: Create render marks utility**

```typescript
export function markRender(name: string): () => void {
  performance.mark(`${name}-start`);
  return () => {
    performance.mark(`${name}-end`);
    performance.measure(name, `${name}-start`, `${name}-end`);
  };
}
```

- [ ] **Step 2: Add to key render paths**

In campaign settings tab switch, play view load, scene jump, and library table components, add:

```typescript
useEffect(() => {
  const end = markRender("campaign-settings-render");
  return end;
});
```

- [ ] **Step 3: Commit**

```
git commit -m "feat(frontend): add performance.mark/measure render instrumentation"
```

---

### Task 9: Final verification

- [ ] **Step 1: Backend full suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 2: Frontend lint + typecheck + tests**

Run: `cd frontend && pnpm lint && pnpm typecheck && pnpm test`
Expected: All pass

- [ ] **Step 3: Manual smoke test**

Run both servers. Submit a turn. Verify:
- Context build metrics appear in observability endpoint
- WebSocket events don't trigger redundant fetches (browser network tab)
- Settings tabs don't re-fetch on tab switch (browser network tab)
