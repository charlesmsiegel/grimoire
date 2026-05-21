# Observability — Remaining Work (COMPLETED 2026-05-18)

> Everything from the original `specs/16-observability.md` (now superseded) that did **not** land in the shipped design (`2026-05-13-observability-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-13-observability-design.md`
**Module:** `backend/src/grimoire/observability/`

## Status (2026-05-18)

Backend implementation landed. Frontend UI work (status bar, Worlds panel
debug views, Health panel, Performance tab) and the WebSocket live-tailing
pipe are deferred for a follow-up frontend pass.

| #  | Section                                              | Status |
|----|------------------------------------------------------|--------|
| 1  | Wire `ObservabilityService` into the running app     | ✅ Done |
| 2  | Producer event enrichment                            | ✅ Done |
| 3  | Verbatim assembled prompt capture                    | ✅ Done |
| 4  | Replay determinism: `seed` pass-through              | ✅ Done |
| 5  | Debug view "What did the model see?" — HTTP route    | ✅ Backend done (frontend deferred) |
| 6  | Debug view "What changed?" — HTTP route              | ✅ Backend done (frontend deferred) |
| 7  | Debug view "Why this character?"                     | ⏸ Deferred — needs ContextBuilder source-attribution upgrade |
| 8  | Debug view "Cost breakdown" — HTTP route             | ✅ Backend done |
| 9  | Cost surfacing — HTTP routes                         | ✅ Backend done (frontend deferred) |
| 10 | Performance metrics — HTTP route                     | ✅ Endpoint done; producer-side `metrics.record(...)` calls still pending in each module |
| 11 | Health-check auto-registration                       | ✅ LLM gateway already self-registers; ImageGen now does too |
| 12 | Frontend health panel — HTTP routes                  | ✅ Routes done; frontend deferred |
| 13 | Live tailing for the debug log                       | ⏸ Deferred (WebSocket work) |
| 14 | Error de-duplication                                 | ✅ Satisfied display-side via `aggregate_by_module` |
| 15 | Per-task budget enforcement: fast `total_today`      | ✅ Done |
| 16 | `error_reported` plugin hook                         | ✅ Done |
| 17 | Scrubbed PII export                                  | ⏸ v2 (out of scope) |
| 18 | Performance overhead benchmark                       | ✅ Done — harness shipped, batched writer not needed |
| 19 | OpenTelemetry                                        | ⏸ v2 (out of scope) |
| 20 | User-facing analytics                                | ⏸ Out of scope |
| 21 | Audit-driven test generation                         | ⏸ Out of scope (spec 17) |

### Where the new code landed

- `backend/src/grimoire/observability/service.py` — `start()` now subscribes
  to `llm_response_received` and writes a `cost_records` row per call;
  `record_error` fires an `error_reported` event for plugin hooks.
- `backend/src/grimoire/observability/turn_auditor.py` — subscribes to
  `llm_response_received` (translating unprefixed keys → `llm_*` audit
  fields) and stores `assembled_messages` from `context_built`.
- `backend/src/grimoire/observability/audit.py` — persists / rehydrates
  the verbatim message list; new `deltas_for_turn(turn_id)` joins
  `applied_delta_ids` against the `deltas` table.
- `backend/src/grimoire/observability/costs.py` — new `by_turn(turn_id)`
  and fast `total_today(campaign_id)` accessors.
- `backend/src/grimoire/observability/replayer.py` — prefers the stored
  verbatim messages; forwards `seed` to the gateway.
- `backend/src/grimoire/orchestrator/service.py` — enriched turn-event
  payloads (player_input, options, branch_id, messages, hash, sources,
  composition_snapshot, deltas, strategies, flags) and emits
  `turn_audit_fragment` for mechanics rolls, applied/queued deltas and
  scene-appended.
- `backend/src/grimoire/llm_gateway/gateway.py` — streaming path now
  computes cost from usage × price book; `llm_response_received`
  carries `params` (incl. `seed`) and `finish_reason`.
- `backend/src/grimoire/types/llm.py` — `CompletionRequest.seed: int | None`.
- `backend/src/grimoire/types/observability.py` — `TurnAudit.assembled_messages`.
- `backend/src/grimoire/api/observability.py` (new) — 15 endpoints under
  `/api/observability/...`: turn audits, prompt, deltas, costs (session,
  rollup, today, by-turn), metrics summary/recent, health (latest, probe),
  errors (recent, aggregate), debug log query.
- `backend/src/grimoire/imagegen/service.py` — `register_with_health_monitor`
  walks the backend registry and registers each as a probeable target.
- `backend/src/grimoire/main.py` — constructs `ObservabilityService` early,
  hands its `health_monitor` to the gateway, registers ImageGen backends,
  builds the replayer once the gateway exists, starts the auditor + health
  + retention loops, and tears them down on shutdown.

## 1. Wire `ObservabilityService` into the running app

The module is implemented and tested but never instantiated. `backend/src/grimoire/main.py` does not import it, and `ServiceContainer` (`backend/src/grimoire/api/container.py`) has no slot for it. Today the audit trail, cost log, metrics, debug log, errors, health probes and retention never run in the live process.

Needed:
- Construct an `ObservabilityService(db=db, event_bus=container.event_bus, state_store=container.state_store, llm_gateway=llm_gateway)` in `lifespan` after the gateway and state store exist
- Call `await service.start()` to subscribe the `TurnAuditor` and rehydrate the latest health map
- Add `observability: Any = None` to `ServiceContainer` so routers can reach it
- Call `await container.observability.shutdown()` in `_shutdown`
- Decide whether to `start_periodic()` on the health monitor and the retention maintainer at startup (probably yes for both, gated on config)

## 2. Producers don't emit the data the TurnAuditor expects

The TurnAuditor subscribes to `turn_started`, `context_built`, `model_response_received`, `deltas_extracted`, `turn_audit_fragment`, `turn_complete`. The Orchestrator's `_emit_turn_event` calls today send:

- `turn_started(turn_id, campaign_id, scene_id)` — no `player_input`, no `options`, no `branch_id`
- `context_built(..., budget_used)` — no `messages_hash`, no `context_summary`, no `context_sources`
- `model_response_received(..., length)` — no `response_text` and none of the nine `llm_*` keys (`provider`, `model`, `params`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `latency_ms`, `finish_reason`, `retries`)
- `deltas_extracted(..., count)` — no `deltas`, no `strategies_run`, no `duration_ms`, no `flags`
- `turn_complete(...)` — bare

Nothing in the codebase ever emits `turn_audit_fragment` (the documented escape hatch for `composition_snapshot`, `proposed_rolls`, `resolved_rolls`, `scene_appended`, `scene_closed`, `images_scheduled`, `time_advanced`, `applied_deltas`, `queued_for_review`).

As wired today, every persisted `TurnAudit` would be mostly empty.

Needed: enrich the Orchestrator emissions (and/or have the relevant downstream modules emit `turn_audit_fragment` from their own subscribers) so the audit captures the slots the spec promises. Concretely:

- Orchestrator: thread `player_input`, `options`, `branch_id` through `turn_started`; capture `messages_hash` + `context_summary` + `context_sources` on `context_built`; capture LLM metadata + response text on `model_response_received`; capture deltas/strategies/flags/duration on `deltas_extracted`
- LLM Gateway: emit a `turn_audit_fragment` (or extend `model_response_received`) carrying provider/model/cost/latency/retries; the gateway's `llm_requests` row already has all of this
- Mechanics: emit `turn_audit_fragment` with `proposed_rolls` / `resolved_rolls` after pre-roll
- Extractor: emit `turn_audit_fragment` with `extraction_flags`
- Apply routing: emit `turn_audit_fragment` with `applied_deltas` and `queued_for_review` ids
- ImageGen: emit `images_scheduled` ids
- Time Engine: emit `time_advanced`
- Scene Manager: emit `scene_appended` / `scene_closed`

## 3. Replay cannot reproduce the original prompt verbatim

`TurnReplayerService._build_request` (`replayer.py:125`) notes that the audit stores only the prompt hash + summaries, not the full assembled message list. Without `substitute.prompt_edit`, replay sends a stub `[Message(USER, audit.player_input)]` — which is almost never what the model originally saw.

Spec 16 §turn replay requires "the audit record contains the assembled prompt (verbatim)" as the first condition for replayability.

Needed:
- Add an `assembled_messages: list[Message]` (or compressed equivalent) column to `turn_audits` and write the verbatim message list through the `prompt_messages` blob
- Decide the size/cost tradeoff (probably a config knob `AuditConfig.capture_full_prompt`, which already exists as an unused flag)
- Update `TurnReplayer._build_request` to use the stored messages by default and only fall back to player-input reconstruction when `capture_full_prompt=False`

## 4. Replay determinism: seed capture

Spec 16 §turn replay condition (2): "The LLM call's `seed` was recorded." The `TurnAudit.llm_params` dict can carry it but the gateway does not currently pass through a `seed` argument and the orchestrator does not request one. The Open Questions note documents the realistic guarantee ("often-but-not-always") but the seed wiring is missing.

Needed: a `seed: int | None` slot on `CompletionRequest`, gateway pass-through for providers that honor it, and orchestrator-side capture so it lands in the audit's `llm_params`. Surface the "providers don't honor seed" caveat in the replay UI per the spec.

## 5. Debug views — "What did the model see?"

Spec 16 §debug views describes a per-turn prompt-rendering surface: each message in order with tier annotations, token counts per message, source attribution per entity (scope, owning asset, override flag), diff against the previous turn's prompt, and the composition snapshot.

Today there is no HTTP route, no aggregation helper on `AuditStore`, and (per §2/§3) no verbatim prompt to render. Needed:
- `/api/observability/turns/{turn_id}/prompt` returning the assembled messages + per-message tier + per-source attribution + token counts
- A "diff vs previous turn" helper (probably an `AuditStore.diff_prompts(turn_id_a, turn_id_b)` method)
- Frontend Worlds-panel "What did the model see?" UI

## 6. Debug views — "What changed?"

Per-turn delta diff: facts added/retired, character / location changes, commitments opened/resolved, inventory changes, mechanical events, time advanced, filtered by confidence and source.

Today `AuditStore` stores only the *ids* of applied/queued deltas. Rehydration is "via state_store" but no endpoint or helper performs that join. Needed:
- A composition helper that joins `turn_audits.applied_delta_ids` against the state store's delta log, returning full `AppliedDelta` records with evidence text / confidence / strategy / auto-vs-queued
- `/api/observability/turns/{turn_id}/deltas`
- Frontend UI

## 7. Debug views — "Why this character?"

Per-character-in-context surface explaining inclusion reason: spotlight / mentioned in last N posts / open commitment / user-pinned / family-promoted.

The Context Builder owns the data (it picks the characters). Needed: the Context Builder should attach per-character inclusion-reason metadata to `ContextSource` records, those records should land in the audit (depends on §2), and a Frontend lens should render them.

## 8. Debug views — "Cost breakdown"

Per-turn cost split: primary generation / extraction / drift check / image prompt rewriter / embedding.

`cost_records` already has a `task` column for this split. Needed:
- A `CostTrackerService.by_turn(turn_id) -> dict[task, CostTotal]` helper
- `/api/observability/turns/{turn_id}/costs`
- Frontend UI

## 9. Cost surfacing in the Frontend

Spec 16 §cost tracking: "The Frontend's status bar surfaces session cost in real time. The Worlds panel surfaces 30-day rollups. Budget alerts are configurable per campaign (warn at $X, hard-stop at $Y); the Orchestrator owns enforcement, Observability owns the data."

Today:
- `CostConfig.surface_in_status_bar` / `daily_budget_warn_usd` / `daily_budget_alert_usd` exist but no consumer reads them
- The Frontend has no cost UI (only a `CampaignSettings.tsx` mention of "Verbose debug log for this campaign")
- The Orchestrator does not consult cost totals before running a turn

Needed:
- `/api/observability/costs/session` (real-time campaign-since-startup total) and `/api/observability/costs/rollup?days=30`
- Frontend status-bar + Worlds-panel views
- Orchestrator-side budget check (lives in spec 01's domain; coordinate)

## 10. Performance metrics tab

Spec 16 §performance metrics: "The Frontend exposes a Performance tab in Worlds showing percentile latencies, error counts, and trend lines."

`MetricsRegistry.summary(...)` already returns `count/successes/failures/p50/p95/p99/max`. Needed:
- A producer-side wiring pass: confirm each module in the spec table (Orchestrator, Context Builder, LLM Gateway, Extractor, State Store, Scene Manager, Time Engine, ImageGen) actually calls `metrics.record(...)` from its hot paths (today none do — the registry is purely write-only-when-called)
- `/api/observability/metrics/summary?module=...&operation=...`
- A trend-line endpoint (probably bucket by minute/hour)
- Frontend Performance tab

## 11. Health-check auto-registration

`HealthCheckConfig.targets = "auto"` documents the intent to "auto-discover from registered plugins" but nothing registers anything. The monitor is fully functional once targets are added; what's missing is the discovery wiring.

Needed:
- LLM Gateway: on plugin registration, call `health_monitor.register_probeable(HealthTarget(id=provider_id, kind="llm_provider"), provider)`
- Embedding providers: same pattern, `kind="embedding_provider"`
- ImageGen backends: `kind="imagegen_backend"`
- The probes themselves (provider-side `health_check()` returning `HealthStatus`) — confirm each provider plugin implements one

## 12. Frontend health panel

Spec 16 §health checks + §error reporting: "Failed health checks surface in the Frontend as warning indicators. The user can manually re-probe." + "The Frontend's Health panel surfaces recent errors grouped by module."

`HealthMonitorService.latest()` and `ErrorStore.aggregate_by_module()` are ready. Needed: HTTP routes (`/api/observability/health/latest`, `/api/observability/health/probe`, `/api/observability/errors/recent`), a WebSocket subscription that fans out `HealthHandler` events to live UIs, and the Frontend panel itself.

## 13. Live tailing for the debug log

Spec 16 §structured debug log: "The Frontend's debug log view supports live tailing with filters."

`LogStore.query(...)` is poll-based. Needed: either a WebSocket endpoint that streams matching events as they're written (`LogStore` would need a subscriber hook to push new events) or an event-bus signal per write that a WebSocket handler relays.

## 14. Error de-duplication on the Health panel

Spec 16 §error reporting: "Repeated errors of the same kind aggregate into a single entry with a count." `ErrorStore.aggregate_by_module()` provides the count but no time-window dedup on writes; the table grows linearly. Confirm whether the spec wants storage-side dedup (probably not — keeping individual rows is useful for the debug log) or only display-side aggregation (already supported). If display-side only, this item is satisfied by §12.

## 15. Per-task budget enforcement integration

Spec 16 §cost tracking: "The Orchestrator owns enforcement, Observability owns the data." Today no enforcement path consults `costs().total(...)` before letting a turn run. This is partly a spec 01 item but the Observability side may need a fast `total_today(campaign_id)` accessor (the current `total(...)` query LEFT JOINs `llm_requests` which can be heavy).

## 16. Plugin hook `ERROR_REPORTED`

Spec 16 §error reporting: "A plugin hook (`turn_hook: ERROR_REPORTED`) allows users to wire Sentry or similar themselves." Today `ErrorStore.record` just inserts. Needed: a hook fire from `record` (or from the façade's `record_error`) so installed plugins can subscribe.

## 17. Privacy / PII scrubbed-export of audit data (v2; deferred)

Spec 16 §Open questions: "Should there be a 'scrubbed export' of audit data for sharing diagnostics? Probably yes for community bug reports." Out of scope until the audit pipeline is producing useful data (§2) and someone actually wants to file a bug report from it.

## 18. Performance overhead benchmarking

Spec 16 §Open questions: "Capturing full audits per turn has a non-trivial write cost. Async batching is the implementation; benchmark before committing." Today writes are one-row-per-event synchronously. Needed: a benchmark harness measuring per-turn audit-write overhead under realistic load and, if it's bad, a batched-writer for `turn_audits` / `log_events` / `metric_samples`.

**Outcome (2026-05-20, issue #363):** harness shipped at `backend/src/grimoire/observability/perf_benchmark.py` with a CLI entry (`uv run python -m grimoire.observability.perf_benchmark`) and a regression test at `backend/tests/observability/test_perf_benchmark.py`. A realistic mid-sized turn (50 log events + 30 metric samples + 1 full `TurnAudit` with verbatim assembled messages) writes synchronously in ~18 ms median on the dev box — three orders of magnitude under a typical 1–5 s LLM call. The synchronous path is not a bottleneck today and the batched writer for `turn_audits` / `log_events` / `metric_samples` is **deferred** until either (a) the metric-sampling pass per §10 lands and the steady-state sample rate grows, or (b) the benchmark crosses ~100 ms per turn. Re-run the harness as a check at that point.

## 19. Distributed tracing / OpenTelemetry (v2; deferred)

Spec 16 §Open questions explicitly tags this v2+. Not in scope; recorded here so it doesn't get re-litigated.

## 20. User-facing analytics (rejected)

Spec 16 §Open questions: "Aggregate stats for the user ('you've written 320k words in this campaign over 47 sessions') are pleasant. **Out of core scope but easy to bolt on.**" Treat as out-of-scope unless a user pulls it back in.

## 21. Audit-driven test generation (rejected)

Spec 16 §Open questions: "Bridge to spec 17." Reserved for the test-fixtures spec; do not implement here.

---

## Suggested plan ordering

1. **§1** wiring first — nothing else can be exercised in the running app until `ObservabilityService` is actually constructed
2. **§2** producer enrichment — until the audit captures real data, every consumer downstream (debug views, replay, cost breakdown) is fed empty rows
3. **§3 + §4** replay correctness — verbatim prompt capture + seed pass-through fix the foundation for §6/§8 to work usefully
4. **§11 + §12** health surfacing — small, complete, gets a user-visible win without depending on §2
5. **§9** cost surfacing — depends only on `cost_records` which is already populated by the gateway
6. **§10** metrics — needs the producer-side `metrics.record(...)` pass in each module
7. **§5 + §6 + §7 + §8** debug views — sequential UI work; share a common Worlds-panel scaffolding
8. **§13 + §14 + §15 + §16** smaller follow-ups
9. **§18** benchmark before any large rollout
10. **§17 / §19 / §20 / §21** parked
