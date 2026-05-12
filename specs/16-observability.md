# 16 — Observability

## Purpose

The Observability module provides the audit trail, debug views, metrics, and replay machinery that let the user (and the developer) understand what the system actually did. Drift is rarely visible in chat; it shows up in the audit trail. Cost surprises don't appear in prose; they appear in metrics. When a turn produces a bad response, the "what did the model see?" view is what makes the problem fixable.

This module is read-mostly: it consumes events and deltas already produced by other modules, indexes them, and exposes them through query and replay APIs. It owns no domain state — only the lenses on it.

## Responsibilities

- Capture every turn's full audit record (input, assembled prompt, model response, extracted deltas, applied state changes, errors, timing)
- Track cost per provider, model, task, campaign, day
- Track performance metrics per module (latency, success rate, retries)
- Provide a "what did the model see?" debug view per turn
- Provide a "what changed?" delta inspector per turn
- Support deterministic turn replay against recorded fixtures
- Run health checks against connected external services (LLM providers, ImageGen backends)
- Surface errors and warnings with attribution to the originating module
- Maintain a structured debug log queryable by turn, scene, character, module

## Non-responsibilities

- Does not own state changes (State Store does; Observability reads from the delta log)
- Does not decide policy (e.g., does not enforce cost budgets — it only tracks; the LLM Gateway and Orchestrator enforce)
- Does not generate insights or summaries beyond raw metrics
- Does not implement crash reporting to a remote service (out of scope; user can wire one in via a plugin)

## The turn audit record

For every turn, a structured record is captured:

```python
@dataclass
class TurnAudit:
    turn_id: str
    campaign_id: str
    branch_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]

    # Input
    player_input: str
    options: dict

    # Composition snapshot at turn time
    composition_snapshot: CompositionSnapshot   # asset refs + versions + mechanics module
    # Captures: which rosters and settings were referenced, what versions were bound,
    # which mechanics module was active. Lets us answer "what library state did this
    # turn see?" even if the library has since changed.

    # Scene context at turn start
    scene_id: str
    scene_break_decision: BreakDecision

    # Context assembly
    context_summary: ContextSummary
    context_sources: list[ContextSource]        # each source carries scope + owner_id
    context_budget_used: dict[Tier, int]
    context_messages_hash: str                  # for replay equivalence checks

    # Mechanics
    proposed_rolls: list[ProposedRoll]
    resolved_rolls: list[MechanicsResult]

    # LLM call
    llm_provider: str
    llm_model: str
    llm_params: dict
    llm_prompt_tokens: int
    llm_completion_tokens: int
    llm_cost_usd: Optional[float]
    llm_latency_ms: int
    llm_finish_reason: str
    llm_retries: int

    # Output
    response_text: str

    # Extraction
    extraction_strategies_run: list[str]
    extraction_duration_ms: int
    extracted_deltas: list[StateDelta]
    extraction_flags: list[ExtractionFlag]

    # State changes
    applied_deltas: list[AppliedDelta]
    queued_for_review: list[ReviewItem]

    # Scene
    scene_appended: bool
    scene_closed: bool

    # Side effects
    images_scheduled: list[GenJobId]
    time_advanced: Optional[TimeAdvanceResult]

    # Errors and warnings
    errors: list[ErrorRecord]
    warnings: list[WarningRecord]
```

The audit record is indexed in the State Store under a `turn_audits` table. It is the single source of truth for "what happened on this turn." Older entries can be compressed (response text → blob storage) but never deleted unless the user explicitly purges history.

## Turn replay

A turn is replayable if:

1. The audit record contains the assembled prompt (verbatim)
2. The LLM call's `seed` was recorded
3. All inputs to extraction are recorded
4. The State Store can be rolled back to the pre-turn state on a fork

Replay is implemented by:

```python
class TurnReplayer(Protocol):
    async def replay(
        self,
        turn_id: str,
        on_fork: bool = True,         # default: replay on a fork, don't mutate main
        substitute: Optional[ReplaySubstitution] = None,
    ) -> ReplayResult: ...

@dataclass
class ReplaySubstitution:
    model: Optional[str]              # try a different model
    temperature: Optional[float]
    extra_context: Optional[str]
    prompt_edit: Optional[str]        # full-prompt override
```

Replay is the foundation for two user workflows:

- **Regression testing**: "this turn worked last week; does it still work after the update?"
- **A/B comparison**: "what would Claude Sonnet 4.6 have said here?"

Replay does not re-run extraction against new state — it replays the LLM call and records what *would* have been extracted, presenting deltas as a diff against the original.

## Cost tracking

Every LLM call records cost (where the provider exposes it; some local providers report zero). Costs roll up:

```python
class CostTracker(Protocol):
    async def record(self, call: LLMCallRecord) -> None: ...
    async def total(
        self,
        campaign_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        task: Optional[str] = None,    # "primary", "extraction", "drift_check", etc.
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> CostTotal: ...

    async def by_day(self, campaign_id: str, days: int = 30) -> list[DailyCost]: ...
    async def by_task(self, campaign_id: str) -> dict[str, float]: ...
    async def by_model(self, campaign_id: str) -> dict[str, float]: ...
```

The Frontend's status bar surfaces session cost in real time. The Settings panel surfaces 30-day rollups. Budget alerts are configurable per campaign (warn at $X, hard-stop at $Y); the Orchestrator owns enforcement, Observability owns the data.

## Performance metrics

Per-module, per-operation:

| Module | Metric | Granularity |
|---|---|---|
| Orchestrator | turn duration, end-to-end | per turn |
| Context Builder | build duration, budget utilization | per turn |
| LLM Gateway | request latency, retries, error rate | per call |
| Extractor | extraction duration, deltas per turn, review-queue rate | per turn |
| State Store | query latency, write latency, delta volume | per op |
| Scene Manager | summary generation duration | per scene close |
| Time Engine | tick duration, NPC ticks per advance | per advance |
| ImageGen | queue depth, generation duration, success rate | per job |

Metrics are stored in a rolling window (configurable, default 30 days). The Frontend exposes a Performance tab in Settings showing percentile latencies, error counts, and trend lines.

Metric collection is sample-based for hot paths (every Nth call) and exhaustive for cold paths (every call).

## Health checks

Connected external services are probed:

- LLM providers: minimal completion request every N minutes
- ImageGen backends: model-list request
- Embedding providers: small embed request

Failed health checks surface in the Frontend as warning indicators. The user can manually re-probe.

```python
class HealthMonitor(Protocol):
    async def probe(self, target: HealthTarget) -> HealthStatus: ...
    async def probe_all(self) -> list[HealthStatus]: ...
    def subscribe(self, handler: Callable) -> SubscriptionId: ...
    def latest(self) -> dict[str, HealthStatus]: ...
```

## Debug views

### "What did the model see?"

For any turn, render the exact prompt sent to the model:

- Each message in order, with tier annotation
- Token counts per message
- Source attribution per entity: scope (library / campaign-local), owning asset (e.g., "from wod-london v7"), whether an override was applied
- Diff against the previous turn's prompt (highlights what changed)
- The composition snapshot — which library assets were referenced, at what versions

This is the most-used debug surface. It diagnoses drift, missing context, wrong character cards, contradictory facts, and helps answer "why did the model see this version of Alistair?"

### "What changed?"

For any turn, render the delta diff:

- Facts added/retired
- Character state changes
- Location changes
- Commitments opened/resolved
- Inventory changes
- Mechanical events
- Time advanced
- Filtered by confidence and source

Each delta shows: original evidence text, confidence, source strategy, whether auto-applied or queued.

### "Why this character?"

For any character in a turn's context, surface why they were included:

- Present in scene (spotlight)
- Mentioned in last N posts (background)
- Has open commitment to PC
- User-pinned
- Promoted by family/household membership

Useful when a character "shouldn't be there" or when one is suspiciously missing.

### "Cost breakdown"

For any turn, show the cost split:

- Primary generation
- Extraction
- Drift check (if run)
- Image prompt rewriter (if run)
- Embedding (if posts/facts were embedded)

## Structured debug log

A separate, lightweight log of fine-grained events:

```python
@dataclass
class LogEvent:
    timestamp: datetime
    level: LogLevel                   # DEBUG, INFO, WARNING, ERROR
    module: str
    operation: str
    turn_id: Optional[str]
    payload: dict
    duration_ms: Optional[int]
    error: Optional[str]
```

Events are written to a structured log store (SQLite table, JSONL file, or both). Queryable by:

- Time range
- Module / operation
- Turn ID
- Level
- Free text

The Frontend's debug log view supports live tailing with filters.

## Error reporting

Errors are attributed to their originating module:

```python
@dataclass
class ErrorRecord:
    timestamp: datetime
    turn_id: Optional[str]
    module: str
    operation: str
    error_kind: str                   # "llm_timeout", "extraction_parse_failure", etc.
    message: str
    traceback: Optional[str]
    context: dict                     # serializable details
    user_visible: bool                # was this surfaced to the user?
    user_action_taken: Optional[str]  # "retried", "ignored", "edited"
```

The Frontend's Health panel surfaces recent errors grouped by module. Repeated errors of the same kind aggregate into a single entry with a count.

No automatic remote reporting in v1. A plugin hook (`turn_hook: ERROR_REPORTED`) allows users to wire Sentry or similar themselves.

## Interface

```python
class Observability(Protocol):
    # Audit
    async def record_turn_audit(self, audit: TurnAudit) -> None: ...
    async def get_turn_audit(self, turn_id: str) -> TurnAudit: ...
    async def list_turn_audits(
        self,
        campaign_id: str,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> list[TurnAudit]: ...

    # Replay
    async def replay_turn(self, turn_id: str, opts: ReplayOptions) -> ReplayResult: ...

    # Metrics
    def metrics(self) -> MetricsAPI: ...
    def costs(self) -> CostTracker: ...
    def health(self) -> HealthMonitor: ...

    # Debug log
    async def log(self, event: LogEvent) -> None: ...
    async def query_log(self, query: LogQuery) -> list[LogEvent]: ...

    # Errors
    async def record_error(self, err: ErrorRecord) -> None: ...
    async def recent_errors(self, limit: int = 50) -> list[ErrorRecord]: ...
```

## Storage

Observability data is stored in the same SQLite database as state, in separate tables (`turn_audits`, `cost_records`, `metric_samples`, `log_events`, `error_records`, `health_status`).

For long-running campaigns, audit data can dominate database size. Retention policy:

```yaml
observability:
  retention:
    turn_audits: 365d                 # full audits for 1 year
    turn_audits_compressed: forever   # after 1 year, drop full prompts, keep summary
    cost_records: forever
    metric_samples: 90d
    log_events:
      DEBUG: 7d
      INFO: 30d
      WARNING: 180d
      ERROR: forever
    error_records: forever
```

A maintenance task runs nightly to apply retention. The user can disable retention entirely.

## Configuration

```yaml
observability:
  audit:
    enabled: true
    capture_full_prompt: true
    capture_response: true
    capture_extracted_deltas: true
  metrics:
    enabled: true
    sample_rate_hot_path: 0.1         # sample 10% of high-frequency ops
    sample_rate_cold_path: 1.0        # sample all infrequent ops
  health:
    probe_interval_seconds: 300
    targets: auto                     # auto-discover from registered plugins
  debug_log:
    default_level: INFO
    levels_per_module:
      orchestrator: INFO
      llm_gateway: INFO
      extractor: DEBUG                # extraction is the most-debugged module
  cost:
    surface_in_status_bar: true
    daily_budget_warn_usd: 5.00
    daily_budget_alert_usd: 20.00
```

## Open questions

- **Replay determinism.** Many providers do not honor seed reliably across deployments. The realistic guarantee is "same prompt, same model version, often-but-not-always the same response." Replay UI should make this expectation clear.
- **Privacy / PII in audit.** Audit records contain everything: character prose, player input, generated content. Should there be a "scrubbed export" of audit data for sharing diagnostics? Probably yes for community bug reports.
- **Performance overhead.** Capturing full audits per turn has a non-trivial write cost. Async batching is the implementation; benchmark before committing.
- **Distributed tracing.** If we ever move to a multi-service architecture (separate inference server, separate ImageGen server), OpenTelemetry-style trace IDs would help. v2+.
- **User-facing analytics.** Aggregate stats for the user ("you've written 320k words in this campaign over 47 sessions") are pleasant. Out of core scope but easy to bolt on.
- **Audit-driven test generation.** Use real audit records as test fixtures: "given this prompt, expect this kind of extraction." Powerful but needs careful curation. Bridge to spec 17.
