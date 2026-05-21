# Observability — Performance metrics tab (issue #355)

**Status:** Design — 2026-05-20
**Issue:** [#355 — Observability: performance metrics tab](https://github.com/charlesmsiegel/grimoire/issues/355)
**Parent spec:** `docs/superpowers/specs/2026-05-18-observability-COMPLETED.md` §10
**Source:** Spec 16 §performance metrics — "The Frontend exposes a Performance tab in Worlds showing percentile latencies, error counts, and trend lines."

---

## 1. Problem

`MetricsRegistry.summary(...)` already returns `count / successes / failures / p50 / p95 / p99 / max`. The HTTP route `GET /api/observability/metrics/summary?module=...&operation=...` is in place. Three pieces are still missing:

1. **No producer ever calls `metrics.record(...)`** — the registry is write-only-when-called, so today `summary()` always returns zeros in the running app.
2. **No trend / time-bucketed endpoint** — the spec asks for trend lines, but only `summary()` and raw `query_recent()` exist.
3. **No frontend UI** — the Library has no observability section; performance metrics are global (per module/operation) so they don't belong inside a per-world detail view.

## 2. Decisions

| Decision | Choice |
|---|---|
| Frontend placement | New top-level `/observability` route, sibling of `/library` and `/campaigns`. First (and currently only) tab is `Performance`. Leaves room for Health / Errors / Costs tabs later. |
| Producer wiring scope | All 8 modules from the issue: Orchestrator, Context Builder, LLM Gateway, Extractor, State Store, Scene Manager, Time Engine, ImageGen. |
| Trend endpoint shape | Caller picks bucket + window: `GET /api/observability/metrics/trend?module=X&operation=Y&bucket=minute|hour|day&window_seconds=N`. |
| Record style | An async context manager `MetricsRegistry.measure(module, operation, ...)` — one-liner at each producer site. |

## 3. Architecture

Four parts, in dependency order:

```
┌─ MetricsRegistry.measure() ──────┐    (1) new context manager — captures
│   async with metrics.measure(    │        duration via time.perf_counter()
│       "module", "op"):           │        and routes through existing record()
│       ...                        │
└──────────────────────────────────┘
              │
              ▼
┌─ Producer wiring ────────────────┐    (2) 8 modules each get measure() at
│   Orchestrator.run_turn          │        their hot path(s); optional
│   ContextBuilder.build           │        dependency on MetricsRegistry so
│   LLMGateway.complete / stream   │        tests can omit it.
│   Extractor.extract              │
│   StateStore.query / write       │
│   SceneManager.scene_resolve     │
│   TimeEngine.advance             │
│   ImageGen.generate              │
└──────────────────────────────────┘
              │
              ▼
┌─ Trend endpoint + known pairs ───┐    (3) MetricsRegistry.trend(...) +
│   GET .../metrics/trend          │        two new routes; aggregates
│   GET .../metrics/known          │        raw rows into time buckets.
└──────────────────────────────────┘
              │
              ▼
┌─ /observability route ───────────┐    (4) New top-level page; Performance
│   PerformanceTab                 │        tab lists known (module, operation)
│   - summary rows                 │        pairs with sparklines.
│   - expandable trend charts      │
└──────────────────────────────────┘
```

## 4. Backend changes

### 4.1 `MetricsRegistry.measure(...)` — context manager

```python
@asynccontextmanager
async def measure(
    self,
    module: str,
    operation: str,
    *,
    labels: dict[str, Any] | None = None,
    force: bool = False,
) -> AsyncIterator[None]:
    if not self._config.enabled:
        yield
        return
    start = time.perf_counter()
    success = True
    try:
        yield
    except BaseException:
        success = False
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        try:
            await self.record(
                module=module, operation=operation,
                duration_ms=duration_ms, success=success,
                labels=labels, force=force,
            )
        except Exception:
            logger.exception(
                "metrics.measure: record failed for %s/%s", module, operation
            )
```

Behaviour notes:

- Sampling decision happens **inside** `record()` (existing behaviour). `measure()` does NOT pre-check, so the timer always runs but the row may be dropped at the sampling layer. Keeps producers cheap (one `perf_counter`) and the sample-rate semantics unchanged.
- A failure inside `record()` is caught and logged so an observability outage never propagates into the caller's hot path.
- `BaseException` ensures `CancelledError` and `KeyboardInterrupt` also flag as `success=False`.
- The context yields no value — if a future caller needs the duration we can switch to yielding a handle.

### 4.2 Producer wiring

Each producer takes a `metrics: MetricsRegistryProtocol = _NullMetrics()` constructor kwarg — a no-op shim by default (see §4.3) so tests that don't care about metrics get it for free. `ServiceContainer` already wires `ObservabilityService`; producer constructors gain this kwarg sourced from `observability.metrics()` at app start.

| Module | Operation(s) | Site |
|---|---|---|
| Orchestrator | `turn` | wraps the body of `run_turn(...)` |
| Context Builder | `build` | wraps the top-level `build(...)` |
| LLM Gateway | `complete`, `stream` | wraps `complete(...)` and the streaming wrapper; `labels={"provider": ..., "model": ...}` |
| Extractor | `extract` | wraps the extractor's entry point |
| State Store | `query`, `write` | one site each at the public read/write entry points (not per-SQL) |
| Scene Manager | `scene_resolve` | wraps the scene-resolution call invoked during turns |
| Time Engine | `advance` | wraps the public time-advance call |
| ImageGen | `generate` | wraps the backend's generate entry point; `labels={"backend": ...}` |

`_HOT_PATHS` in `metrics.py` grows from 6 to 10 entries — adds `(extractor, extract)`, `(scene_manager, scene_resolve)`, `(time_engine, advance)`, `(imagegen, generate)`. (`state_store/query` and `state_store/write` are already hot; same for the four LLM/orchestrator/context entries.)

Labels are minimal — provider/model on LLM Gateway, backend on ImageGen — so dashboards can split by them later without bloating the labels JSON for everyone else.

### 4.3 No-op shim

A tiny stand-in so producers never have to branch on whether metrics are configured:

```python
class _NullMetrics:
    @asynccontextmanager
    async def measure(self, *_args, **_kwargs) -> AsyncIterator[None]:
        yield
```

Producers type their kwarg against a `MetricsRegistryProtocol` (just `measure(...)` for now) so either `MetricsRegistry` or `_NullMetrics` satisfies it. Tests that *do* care about metrics pass a real `MetricsRegistry`; tests that don't get the no-op for free.

### 4.4 Trend endpoint

```python
async def trend(
    self,
    module: str,
    operation: str,
    *,
    bucket: Literal["minute", "hour", "day"],
    window_seconds: int,
) -> list[dict[str, Any]]:
```

Returns a list of buckets ordered by `bucket_start ASC`:

```json
[
  {
    "bucket_start": "2026-05-20T14:32:00+00:00",
    "count": 12,
    "successes": 11,
    "failures": 1,
    "p50_ms": 142.0,
    "p95_ms": 388.0,
    "p99_ms": 420.0
  }
]
```

**Implementation:** fetch raw rows in the window (single indexed scan on `module, metric, recorded_at`), bucket in Python via timestamp truncation, reuse the existing `_percentile()` helper. SQLite percentile aggregates aren't built in and the dataset is bounded by retention (90d cap by default), so Python aggregation is fine.

**Validation:**
- `bucket ∈ {"minute","hour","day"}` (else 400).
- `1 ≤ window_seconds ≤ 30 * 86400` (else 400).
- Resulting bucket count ≤ 5000 (else 400) — prevents a `bucket=minute&window_seconds=30d` request.

**Empty buckets:** included with `count=0` and zeroed stats so the frontend renders a continuous line. Caller-side rendering is much easier with dense buckets than sparse.

**Routes:**

```
GET /api/observability/metrics/trend
    ?module=orchestrator&operation=turn&bucket=minute&window_seconds=3600

GET /api/observability/metrics/known
    → [{"module": "...", "operation": "...", "last_recorded_at": "..."}]
```

The `known` route returns `SELECT module, metric, MAX(recorded_at) FROM metric_samples GROUP BY module, metric ORDER BY MAX(recorded_at) DESC` so the frontend doesn't hard-code the 8 pairs.

## 5. Frontend changes

### 5.1 New route

`App.tsx` gains:

```tsx
<Route path="observability/*" element={<ObservabilityRoutes />} />
```

`ObservabilityRoutes` renders `ObservabilityLayout` with tabs; for this issue only `Performance` exists.

### 5.2 Files

| File | Purpose |
|---|---|
| `frontend/src/api/observability.ts` | Typed wrappers: `getMetricsKnown()`, `getMetricsSummary(module, op, windowSeconds?)`, `getMetricsTrend(module, op, bucket, windowSeconds)` |
| `frontend/src/routes/observability/index.tsx` | Route exports |
| `frontend/src/routes/observability/ObservabilityLayout.tsx` | Shell + tabs |
| `frontend/src/routes/observability/PerformanceTab.tsx` | Main view |
| `frontend/src/routes/observability/useObservabilityPolling.ts` | Visibility-gated polling hook |
| `frontend/src/shell/AppShell.tsx` | Add top-nav link |

### 5.3 Performance tab layout

```
Window: [last 1h ▼]   Bucket: [minute ▼]      [refresh now]

┌── (module, operation) rows ─────────────────────────────┐
│ orchestrator / turn      count 142  p50 1.2s  p95 3.4s  │
│   [60-bucket sparkline of p95]                          │
│                                                         │
│ llm_gateway / complete   count 380  p50 880ms p95 2.1s  │
│   [60-bucket sparkline of p95]                          │
│ …                                                       │
└─────────────────────────────────────────────────────────┘
```

- One row per known `(module, operation)`. Click expands to a larger chart with p50/p95/p99 polylines and a failure-count bar underneath.
- Sparkline is a 60-bucket SVG `<polyline>` of p95 over the window — no chart library needed; matches the existing custom-SVG pattern (`frontend/src/sheets/widgets/HealthTrack.tsx`).
- Auto-refresh: 10s polling while `document.visibilityState === "visible"`; paused when hidden. Manual refresh button forces immediately.
- Initial window/bucket: last 1 hour, minute buckets.

### 5.4 Failure UX

A 5xx from any `/metrics/*` endpoint is treated as "metrics unavailable" — a banner replaces the row list, no broken layout, no error overlay. The page itself never loses navigation.

### 5.5 Performance budget

`markStart("observability:render")` on mount, `markEnd("observability:render")` on first paint of summaries — same pattern as `LibraryLayout.tsx` / `App.tsx`.

## 6. Tests

### Backend

- `backend/tests/observability/test_metrics_measure.py`
  - Records success on clean exit with measured duration.
  - Records `success=False` + re-raises on exception (`pytest.raises`).
  - `asyncio.CancelledError` records `success=False` and re-raises.
  - No-op when `config.enabled=False`.
  - Records-failure inside `record()` is swallowed (caller still completes).
- `backend/tests/observability/test_metrics_trend.py`
  - Empty store → empty list.
  - Three rows across two minutes → two buckets, correct counts, percentiles.
  - Empty buckets between samples are filled with zeros.
  - Invalid bucket / out-of-range window / over-cap bucket count raises `ValueError`.
- `backend/tests/api/test_observability_routes.py` (extend)
  - `GET /metrics/trend` happy path.
  - `GET /metrics/trend` 400 on bad bucket / window.
  - `GET /metrics/known` returns pairs that have rows.

### Producer wiring (one per module)

Each test constructs the module with a real `MetricsRegistry` over an in-memory DB, calls the wired hot path, and asserts one row landed in `metric_samples` with the right `module`/`operation`. Failure paths assert `success=False` is captured. The test does NOT retest `measure()` semantics — just that the producer threads it through.

### Frontend

- `frontend/src/api/__tests__/observability.test.ts` — API wrapper unit tests with fetch mock.
- `frontend/src/routes/observability/__tests__/PerformanceTab.test.tsx` — rows render from mocked summaries; bucket-change re-fetches; failure count surfaces.
- `frontend/src/routes/observability/__tests__/useObservabilityPolling.test.tsx` — polls when visible, pauses when hidden.

## 7. Error-handling principles

- A failure in metrics recording NEVER fails a producer call (caught + logged inside `measure()`).
- Frontend treats 5xx from `/metrics/*` as "metrics unavailable" and shows an empty-state banner; the rest of the page works.
- The trend route caps inputs (window ≤ 30d, buckets ≤ 5000); over-large requests return 400 rather than hang.

## 8. Out of scope

- WebSocket live-tailing for metrics (covered separately by §13 of the parent spec).
- Health / Errors / Costs tabs in the new Observability route — the layout reserves space but only Performance lands here.
- Per-world or per-campaign metric scoping. `MetricsRegistry` has no campaign/world label today; adding one is a separate design decision.
- Performance overhead benchmark of the wired producers (§18 of the parent spec).

## 9. File inventory

**New (backend):**
- None — `measure()` is added to `metrics.py`; trend method to the same; routes to the existing `api/observability.py`.

**Modified (backend):**
- `backend/src/grimoire/observability/metrics.py` — add `measure()`, `trend()`; grow `_HOT_PATHS`.
- `backend/src/grimoire/api/observability.py` — add `/metrics/trend` and `/metrics/known` routes.
- `backend/src/grimoire/orchestrator/*.py` — wire `measure()` around `run_turn`.
- `backend/src/grimoire/context/*.py` — wire around `build`.
- `backend/src/grimoire/llm_gateway/*.py` — wire around `complete` and stream.
- `backend/src/grimoire/extractor/*.py` — wire around extract.
- `backend/src/grimoire/state_store/*.py` — wire around query and write.
- `backend/src/grimoire/scenes/*.py` — wire around scene_resolve.
- `backend/src/grimoire/time_engine/*.py` — wire around advance.
- `backend/src/grimoire/imagegen/*.py` — wire around generate.
- `backend/src/grimoire/api/container.py` — pass `observability.metrics()` into each producer constructor.

**New (frontend):**
- `frontend/src/api/observability.ts`
- `frontend/src/routes/observability/index.tsx`
- `frontend/src/routes/observability/ObservabilityLayout.tsx`
- `frontend/src/routes/observability/PerformanceTab.tsx`
- `frontend/src/routes/observability/useObservabilityPolling.ts`

**Modified (frontend):**
- `frontend/src/App.tsx` — add `/observability/*` route.
- `frontend/src/shell/AppShell.tsx` — add nav link.
