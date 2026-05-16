# Observability — Design (Shipped)

> Captures the Observability design as actually built. The matching "remaining" spec at `2026-05-16-observability-remaining-design.md` covers everything from the original `specs/16-observability.md` that did **not** land in this work.

**Commit:** `ad25a1b` — "Build Observability module (task 27)" (followed by `3ad66a8` AttributeError fix)
**Module:** `backend/src/grimoire/observability/`
**Tests:** `backend/tests/observability/`
**Schema:** `backend/src/grimoire/storage/migrations/008_llm_observability.sql`, `011_observability_health.sql`

## Purpose

Read-mostly module that captures the per-turn audit trail, tracks LLM costs, samples module-level performance metrics, probes connected providers for health, exposes a queryable debug log and error store, and replays past turns through the LLM gateway. It consumes events from the in-process bus (and direct calls from any module) and indexes them into SQLite. It owns no domain state — only the lenses on it.

## Module surface

`ObservabilityService` (`observability/service.py`) is a façade that wires constituent stores behind a single API. Construction:

```python
ObservabilityService(
    db: Database,
    config: ObservabilityConfig | None = None,
    event_bus: EventBus | None = None,         # required for TurnAuditor
    state_store: object | None = None,         # required for replay forks
    llm_gateway: object | None = None,         # required for replay completions
)
```

Constituent services (all attributes on the façade):

- `audit_store: AuditStore` — `turn_audits` reader/writer
- `costs_tracker: CostTrackerService` — `cost_records` reader/writer
- `metrics_registry: MetricsRegistry` — `metric_samples` reader/writer
- `health_monitor: HealthMonitorService` — `health_status` plus subscriber fan-out
- `log_store: LogStore` — `log_events` reader/writer
- `errors_store: ErrorStore` — `error_records` reader/writer
- `retention: RetentionMaintainer` — nightly purge / compress
- `turn_auditor: TurnAuditor | None` — bus subscriber that assembles audits (only if `event_bus` was passed)
- `replayer: TurnReplayerService | None` — turn replay (only if `llm_gateway` was passed)

`start()` subscribes the `TurnAuditor` to the bus and rehydrates the last-known health map from disk. `shutdown()` unsubscribes and stops the periodic loops. Both are idempotent.

## Public API (`Observability` protocol)

The façade implements `grimoire.types.protocols.Observability`:

```python
class ObservabilityService:
    # Audit
    async def record_turn_audit(audit: TurnAudit) -> None
    async def get_turn_audit(turn_id: TurnId) -> TurnAudit            # raises KeyError
    async def list_turn_audits(campaign_id, since=None, limit=50) -> list[TurnAudit]

    # Replay
    async def replay_turn(turn_id, opts: ReplayOptions) -> ReplayResult  # raises if no gateway

    # Sub-APIs
    def costs() -> CostTrackerService
    def health() -> HealthMonitorService
    def metrics() -> MetricsRegistry

    # Debug log
    async def log(event: LogEvent) -> None
    async def query_log(query: LogQuery) -> list[LogEvent]

    # Errors
    async def record_error(err: ErrorRecord) -> None
    async def recent_errors(limit: int = 50) -> list[ErrorRecord]

    # Maintenance
    async def run_maintenance() -> MaintenanceReport
```

## Turn audit

### Storage

`turn_audits` (migration `008`) is keyed by `turn_id` with `campaign_id`, `branch_id`, `scene_id`, `pc_ref` indexed columns plus JSON blobs for `composition`, `context_summary`, `prompt_messages`, `prompt_budget`, `mechanics_results`, `llm_metadata`, `extraction_summary`, `applied_delta_ids`, `queued_review_ids`, `side_effects`, `errors`. The full `response_text` is stored verbatim. Insert is `ON CONFLICT(turn_id) DO UPDATE` so a re-record overwrites.

`AuditStore.record` (`audit.py:69`) walks the `TurnAudit` pydantic model, JSON-dumps nested pydantic submodels, and inserts. `_to_jsonable` / `_default` handle pydantic-`BaseModel`, dataclasses, `list`, `dict`, and `datetime`.

`AuditStore.get` returns `None` on miss; `get_turn_audit` on the façade raises `KeyError(f"unknown turn {turn_id!r}")`. `list(...)` filters by campaign + `since`, orders by `created_at DESC`, defaults limit 50.

### Re-hydration

`AuditStore._row_to_audit` (`audit.py:209`) is deliberately permissive — JSON columns come back as dicts and pydantic's `model_validate` coerces them. Unknown / missing fields fall back to defaults so older schemas still parse. `applied_deltas` and `queued_for_review` rehydrate as empty lists (we keep only ids — full rehydration is via the state store).

### Assembly via bus (TurnAuditor)

`TurnAuditor` (`turn_auditor.py`) subscribes at `start()` to:

- `turn_started` — seeds the per-turn buffer with `turn_id`, `campaign_id`, `branch_id` (default `"main"`), `scene_id`, `started_at`, `player_input`, `options`
- `context_built` — captures `budget_used`, plus optional `messages_hash`, `context_summary`, `context_sources`
- `model_response_received` — `response_text` (gated on `capture_response`), and the nine `llm_*` keys if present
- `deltas_extracted` — `extracted_deltas` (gated on `capture_extracted_deltas`), `strategies_run`, `duration_ms`, `flags`
- `turn_audit_fragment` — any payload keys (except `turn_id` / `campaign_id`) merged into the buffer; the escape hatch any module can use to push extra slots
- `turn_complete` — stamps `completed_at` + `duration_ms`, validates with `TurnAudit.model_validate`, persists. Exceptions in validation or persistence are logged at exception level; the buffer is always popped so a botched turn doesn't leak memory.

Missed `turn_started`: `_buf` creates a minimal stub from the first event so we still produce *something* on `turn_complete`.

## Cost tracking

`CostTrackerService` (`costs.py`) writes to `cost_records` (`id, campaign_id, turn_id, task, model, cost_usd, recorded_at`). Inputs that don't carry `cost_usd` are silently dropped (local providers report nothing).

Queries:

- `total(campaign_id?, provider?, model?, task?, since?, until?) -> CostTotal` — LEFT JOINs `llm_requests` to recover `prompt_tokens` / `completion_tokens` (the slim per-call log the LLM Gateway already writes; `cost_records` doesn't store tokens directly) and to filter by `provider`. Returns total_usd, input_tokens, output_tokens, call_count.
- `by_day(campaign_id, days=30) -> list[DailyCost]` — `GROUP BY substr(recorded_at, 1, 10)` (per-day rollup) over the last N days.
- `by_task(campaign_id) -> dict[str, float]` — sum keyed by task.
- `by_model(campaign_id) -> dict[str, float]` — sum keyed by model.

The LLM Gateway feeds this from its own request log — see `backend/src/grimoire/llm_gateway/request_log.py` and the `observability.log_all_requests` gateway config knob.

## Performance metrics

`MetricsRegistry` (`metrics.py`) writes to `metric_samples` (`id, module, metric, value, labels, recorded_at`). Each row is one observation; `value` is the duration_ms, `labels` is a JSON blob `{operation, success, labels}`.

Sample-based collection: a frozen set of "hot path" `(module, operation)` tuples (`metrics.py:21`) samples at `MetricsConfig.sample_rate_hot_path` (default 0.1). Everything else samples at `sample_rate_cold_path` (default 1.0). Producers pass `force=True` to override. The constructor accepts an injected `random.Random` for deterministic tests.

Hot paths today: `(orchestrator, turn)`, `(context_builder, build)`, `(llm_gateway, complete)`, `(llm_gateway, stream)`, `(state_store, query)`, `(state_store, write)`.

Queries:

- `query_recent(module?, operation?, since?, limit=500)` — raw row dump
- `summary(module, operation, window_seconds?) -> {count, successes, failures, p50_ms, p95_ms, p99_ms, max_ms}` over a rolling window (default `rolling_window_seconds = 30d`). `_percentile` is a linear-interpolation implementation; empty results return zeros.

## Health monitor

`HealthMonitorService` (`health.py`) keeps a `target_id -> (HealthTarget, async-probe-fn)` registry and a `target_id -> HealthStatus` latest map. `register(target, probe)` accepts any async callable returning `HealthStatus`; `register_probeable(target, obj)` wraps an `obj.health_check()` method. The module is duck-typed so the LLM Gateway, embedding plugins and ImageGen backends can all register without a shared base class.

`probe(target)`: unknown target → `UNCONFIGURED` status with message "no probe registered". Otherwise calls the probe, catches any `Exception` and converts to `UNHEALTHY` with the exception string. Fills in `checked_at` and `target_id` if the probe forgot them. Persists via `_persist` (UPSERT on `health_status`) and fans out to subscribers via `_notify` (each handler runs sequentially; failures are logged, not re-raised).

`probe_all()` walks all registered targets sequentially. Subscribers are keyed by a `uuid.uuid4().hex` id; `subscribe(handler)` returns the id, `unsubscribe(id)` removes it.

Persistent latest map: `load_latest()` repopulates the in-memory `_latest` dict from `health_status` so the Frontend's health panel can render last-known state before any new probe lands. Called by the façade's `start()`.

`start_periodic()` spawns a background task that calls `probe_all()` every `probe_interval_seconds` (default 300, min 1). `stop()` sets the stop event and awaits cancellation.

## Debug log

`LogStore` (`log.py`) writes to `log_events` (`id, module, operation, turn_id, level, message, payload, recorded_at`). Per-module level thresholds: `DebugLogConfig.levels_per_module` overrides `default_level`; events below the threshold are silently dropped at write time. Level order is `DEBUG=10, INFO=20, WARNING=30, ERROR=40`.

Write extracts `payload["message"]` into the indexed `message` column for free-text search; everything else goes into the JSON `payload` blob alongside `duration_ms` and `error`.

`query(LogQuery)` supports `since`, `until`, `levels`, `modules`, `operations`, `turn_id`, `free_text` (`LIKE '%needle%'` against both `message` and `payload`), and `limit` (default 500). Orders by `recorded_at DESC`.

## Error store

`ErrorStore` (`errors_store.py`) writes to `error_records` (`id, module, turn_id, kind, message, attribution, payload, recorded_at`). `attribution` and `payload` are JSON: attribution carries `operation`, `user_visible`, `user_action_taken`; payload carries `traceback` and `context`.

Queries:

- `recent(limit=50)` — newest-first
- `aggregate_by_module(since?)` — `GROUP BY module, kind ORDER BY cnt DESC`, used by the Health panel

## Turn replay

`TurnReplayerService` (`replayer.py`) takes the audit store, a `_Completer` (duck-typed `gateway.complete(task, request, campaign_id=...)`), an optional `_BranchForker` (duck-typed `state_store.fork_branch(...)`), and a task name (default `"replay"`).

`replay(turn_id, opts)` flow:
1. Load the audit; missing → `KeyError`
2. If `opts.on_fork` and a state store was provided, call `fork_branch(campaign_id=..., parent_branch_id=audit.branch_id, new_label=f"replay-{turn_id[:8]}", at_turn_id=turn_id)`. Without a state store, append a warning and continue.
3. Build a `CompletionRequest` via `_build_request`:
   - Model defaults to the audit's `llm_model`; `substitute.model` overrides
   - Temperature / max_tokens come from `audit.llm_params`; `substitute.temperature` overrides
   - **Messages are reconstructed, not verbatim.** The audit stores only the prompt hash + summaries (size reasons), so a true verbatim replay requires `substitute.prompt_edit`. Otherwise the request is a single user message of `audit.player_input` (with `substitute.extra_context` prepended if provided), defaulting to `"(replay)"` if both are empty.
4. Call `gateway.complete(task, request, campaign_id=audit.campaign_id)`. Exceptions are caught, appended as a warning, and an empty result is returned.
5. Diff: if texts match return `[{kind: unchanged, length}]`; otherwise return `[{kind: original, text}, {kind: replayed, text}]`. UIs do proper word-level diffs client-side.

Returns `ReplayResult(turn_id, new_response_text, delta_diff, forked_branch_id, warnings)`. Replay does **not** re-run extraction (per the spec).

## Retention maintenance

`RetentionMaintainer` (`maintenance.py`) applies the configured retention windows:

- `_purge_by_age(table, column, days)`: `DELETE FROM <table> WHERE <column> < cutoff` for `metric_samples`, `health_status`, `cost_records`, `error_records`, `turn_audits`
- `_purge_logs`: per-level cutoffs from `log_debug_days` / `log_info_days` / `log_warning_days` / `log_error_days`
- `_compress_audits`: after `turn_audits_compress_after_days`, `UPDATE turn_audits SET response_text=NULL, prompt_messages=NULL WHERE created_at < cutoff` (keeps the row so cost/metrics joins still work)

Defaults (`RetentionConfig`): turn_audits 365d / compress after 365d, cost_records forever (`None`), metric_samples 90d, log debug/info/warning/error 7/30/180/forever, errors forever, health_status 30d. `enabled=True` gates the whole run.

`run_once()` returns a `MaintenanceReport` with per-table deletion counts and timestamps. `start_periodic()` spawns a background loop (default interval 24h, floor 60s). `stop()` cancels it.

## Configuration (`ObservabilityConfig`)

`observability/config.py` provides frozen dataclasses with the spec 16 §Configuration shape:

```python
ObservabilityConfig(
    audit=AuditConfig(enabled=True,
                      capture_full_prompt=True,        # accepted; not yet read by anyone
                      capture_response=True,
                      capture_extracted_deltas=True),
    metrics=MetricsConfig(enabled=True,
                          sample_rate_hot_path=0.1,
                          sample_rate_cold_path=1.0,
                          rolling_window_seconds=30*24*60*60),
    health=HealthCheckConfig(probe_interval_seconds=300, targets="auto"),
    debug_log=DebugLogConfig(default_level=LogLevel.INFO,
                             levels_per_module={}),
    cost=CostConfig(surface_in_status_bar=True,
                    daily_budget_warn_usd=5.00,
                    daily_budget_alert_usd=20.00),
    retention=RetentionConfig(...),
)
```

`CostConfig.surface_in_status_bar`, `daily_budget_warn_usd`, `daily_budget_alert_usd` exist on the config but no consumer reads them yet — they're API-stable placeholders for the Frontend status-bar work.

## Storage schemas

- `008_llm_observability.sql` introduces `llm_requests` (slim per-call log written by the LLM Gateway), `embedding_cache`, `turn_audits`, `cost_records`, `metric_samples`, `log_events`, `error_records`
- `011_observability_health.sql` adds `health_status` (UPSERT-keyed on `target_id`)

All observability data lives in the same SQLite database as state.

## Wiring

`ObservabilityService` is **not yet wired** into `backend/src/grimoire/main.py` or the `ServiceContainer`. The module can be exercised end-to-end via tests (`backend/tests/observability/`) using a fresh `Database` per fixture (`conftest.py`), but no HTTP route, WebSocket subscriber, or startup hook constructs it in the running app. Wiring is one of the remaining items.

## Error handling (as implemented)

- Audit assembly: `TurnAudit.model_validate` failure → `logger.exception(...)`, buffer popped, no row written
- Audit persistence: any exception → `logger.exception(...)`, buffer still popped
- Health subscribers: each handler runs in a `try` block; failures logged via `logger.exception`, never re-raised
- Health probe: any exception → `UNHEALTHY` status with the exception string
- Periodic loops (`health._run_loop`, `retention._loop`): exceptions caught and logged so a single bad cycle doesn't kill the loop
- Replay: gateway exceptions caught, appended as warnings, empty result returned
- Cost record: `cost_usd is None` → silent no-op
- Metrics: `enabled=False` → no-op; `sample_rate <= 0` → no-op
