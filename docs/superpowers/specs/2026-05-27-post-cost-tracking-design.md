# Comprehensive cost tracking across all LLM and generation calls

> Resolves issue #468. Builds on foundation from #353 (per-turn cost breakdown).

## Problem

Cost tracking silently fails across most call sites:

1. **`CostTrackerService.record()` drops records when `cost_usd` is None** (costs.py:27-28). Any call where cost calculation fails is silently lost — no tokens, no call count, nothing.

2. **Provider pricing gaps**: OpenAI-compatible and llama.cpp providers return `None` for `input_cost_per_1k`/`output_cost_per_1k` in `list_models()`. The gateway's `_get_pricing()` returns `None` → cost calculation is skipped → cost stays `None` → record dropped.

3. **Auxiliary tasks don't pass `turn_id`** (auxiliary_runner.py:101-105). Rewrite, brainstorm, translate, continue-as calls go through `gateway.stream()` without a `turn_id`, so cost events are incomplete.

4. **Embedding events don't include cost** (gateway.py:1407-1423). The `EMBEDDING_RESPONSE_RECEIVED` event payload has no `cost_estimate_usd` field.

5. **Image generation has no cost tracking**. `GenerationResult` has no cost field; `IMAGE_READY` events carry no cost data.

Net result: the frontend's `CostLabel` component works but shows $0.0000 because no cost records exist upstream.

## Non-goals

- Real-time streaming cost updates (cost is recorded after call completes).
- Budget enforcement or spend limits (separate issue).
- Retroactive cost backfill for historical data.

## Design

### 1. Always-record cost rows

**`CostTrackerService.record()`** — remove the `if call.cost_usd is None: return` guard. Default `cost_usd` to `0.0` when `None`. Every LLM call produces a `cost_records` row regardless of whether dollar cost is known.

**Migration 034** — add `input_tokens INTEGER DEFAULT 0` and `output_tokens INTEGER DEFAULT 0` columns to `cost_records`. This makes cost_records self-contained — no LEFT JOIN to `llm_requests` needed for token counts.

**`CostTotal` query simplification** — `by_turn()`, `total()`, `by_day()`, etc. read tokens directly from `cost_records` instead of joining `llm_requests`. The `llm_requests` table remains for detailed request-level debugging but is no longer needed for cost queries.

**`_on_llm_response` handler** (observability/service.py:133-152) — pass `input_tokens` and `output_tokens` through to `LLMCallRecord`. Default `cost_usd` to `0.0` instead of passing `None`.

**`LLMCallRecord`** (types/llm.py) — change `cost_usd: float | None` to `cost_usd: float = 0.0`. The type contract now guarantees a record is always written.

### 2. User-configurable fallback pricing

Add a `pricing_overrides` field to `GatewaySettings` and `GatewayConfig`:

```yaml
# In settings YAML under llm_gateway:
pricing_overrides:
  "gpt-4o":
    input_cost_per_1k: 2.50
    output_cost_per_1k: 10.00
  "my-local-model":
    input_cost_per_1k: 0.0
    output_cost_per_1k: 0.0
```

**`_get_pricing()`** (gateway.py:181-206) — check `pricing_overrides` first, keyed by model name. If found, return a synthetic `ModelInfo` with the override values. Only fall through to `provider.list_models()` if no override exists.

This lets users set pricing for any provider, including OpenAI-compatible endpoints that don't report pricing natively.

**Settings/config layer:**

- `GatewaySettings` (settings.py:84): add `pricing_overrides: dict[str, _PricingOverride] = {}` where `_PricingOverride` is a small Pydantic model with `input_cost_per_1k: float` and `output_cost_per_1k: float`.
- `GatewayConfig` (config.py:35): add `pricing_overrides: dict[str, PricingOverride] = field(default_factory=dict)` (frozen dataclass version).
- `to_gateway_config()` — convert settings overrides to config overrides.

### 3. Auxiliary tasks pass originating turn_id

**`AuxiliaryTask`** (auxiliary/types.py) — add `turn_id: str | None = None` field. Tasks that target a specific post (rewrite, continue-as, edit-prose) can carry the originating post's `turn_id` so costs are attributed to the same turn.

**API layer** (api/auxiliary.py) — for task kinds that take a `target_post_id` (REWRITE_POST, CONTINUE_AS, EDIT_PROSE), look up the post's `turn_id` and pass it through in the `AuxiliaryTask`. For task kinds without a target post (BRAINSTORM, WHAT_WOULD_X_SAY, TRANSLATE, IMPERSONATE_PC), `turn_id` stays `None` — costs are still recorded (campaign-level) but not attributed to a specific turn.

**`auxiliary_runner.py`** (line 101-105) — pass `turn_id=task.turn_id` to `gateway.stream()`:

```python
stream = orchestrator._gateway.stream(
    route_task,
    request,
    campaign_id=campaign_id,
    turn_id=task.turn_id,
)
```

### 4. Embedding cost tracking

**Gateway `embed()` method** (gateway.py:1407-1423) — apply cost-fill logic: after getting the embedding response, look up pricing via `_get_pricing()` and calculate cost from token count. Include `cost_estimate_usd` in the `EMBEDDING_RESPONSE_RECEIVED` event payload.

**Observability handler** — subscribe to `EMBEDDING_RESPONSE_RECEIVED` in addition to `LLM_RESPONSE_RECEIVED`. Build an `LLMCallRecord` from the embedding event and write to `cost_records` with `task` set to the embedding task name.

### 5. Image generation cost tracking

**`GenerationResult`** (types/imagegen.py) — add `cost_usd: float = 0.0` field. Image backends that know their pricing (e.g., DALL-E) populate it; others leave it at 0.0.

**`ImageGenService`** — after a successful generation, emit a cost event or directly write a `cost_records` row via the observability service. Use `task="imagegen"`, `model` from the result, and `cost_usd` from the result. Token fields stay 0 for images.

**IMAGE_READY event** — add `cost_usd` to the event payload. The observability handler subscribes to `IMAGE_READY` and writes a cost record.

**ImageGenBackend protocol** — no change needed. Backends that know their cost set it on `GenerationResult`; the default is 0.0.

### 6. Schema migration (034)

```sql
-- 034_cost_records_tokens.sql
ALTER TABLE cost_records ADD COLUMN input_tokens INTEGER DEFAULT 0;
ALTER TABLE cost_records ADD COLUMN output_tokens INTEGER DEFAULT 0;
```

Additive — no data loss, no table rebuild. Existing rows get 0 for both columns.

### 7. Query simplification

**`by_turn()`** — drop the LEFT JOIN to `llm_requests`. Query becomes:

```sql
SELECT task,
       COALESCE(SUM(cost_usd), 0.0) AS total_usd,
       COALESCE(SUM(input_tokens), 0) AS input_tokens,
       COALESCE(SUM(output_tokens), 0) AS output_tokens,
       COUNT(id) AS call_count
FROM cost_records
WHERE turn_id = ?
GROUP BY task
```

Same change applies to `total()`, `by_day()`, and any other query that currently joins `llm_requests` for token counts.

### 8. Frontend — no changes needed

The existing `CostLabel` and `CostBreakdown` components work correctly. They show $0.0000 because no records exist upstream. Once the backend records costs properly, the UI will start showing real data. The `by_turn()` query returns the same `CostTotal` shape — just backed by simpler SQL.

## Testing

### Backend

| Test | What it verifies |
|------|------------------|
| `test_record_always_writes_even_with_zero_cost` | `record()` no longer skips None/0 costs |
| `test_record_stores_tokens_in_cost_records` | `input_tokens` and `output_tokens` columns populated |
| `test_by_turn_uses_cost_records_tokens` | `by_turn()` returns token counts from `cost_records` directly (no JOIN) |
| `test_pricing_override_used_when_provider_has_none` | `_get_pricing()` returns override values |
| `test_pricing_override_takes_precedence` | Override wins over `list_models()` |
| `test_auxiliary_task_passes_turn_id` | `gateway.stream()` called with `turn_id` from task |
| `test_embedding_event_includes_cost` | `EMBEDDING_RESPONSE_RECEIVED` carries `cost_estimate_usd` |
| `test_imagegen_cost_recorded` | `IMAGE_READY` handler writes `cost_records` row |

### Frontend

No new frontend tests — existing `CostBreakdown` and `CostLabel` tests remain valid.

## Files changed

### Backend — modify

| File | Change |
|------|--------|
| `observability/costs.py` | Remove None guard in `record()`, add token params, simplify queries |
| `observability/service.py` | Handle embedding + imagegen events, default cost to 0.0 |
| `types/llm.py` | `LLMCallRecord.cost_usd` → `float = 0.0` |
| `types/imagegen.py` | Add `cost_usd: float = 0.0` to `GenerationResult` |
| `llm_gateway/gateway.py` | Add cost to embed event, check pricing overrides in `_get_pricing()` |
| `llm_gateway/config.py` | Add `pricing_overrides` field |
| `llm_gateway/settings.py` | Add `pricing_overrides` settings model |
| `auxiliary/types.py` | Add `turn_id: str | None = None` to `AuxiliaryTask` |
| `orchestrator/auxiliary_runner.py` | Pass `turn_id=task.turn_id` to `gateway.stream()` |
| `api/auxiliary.py` | Look up originating post's `turn_id` for applicable task kinds |
| `imagegen/service.py` | Add `cost_usd` to `IMAGE_READY` event payload |

### Backend — create

| File | Content |
|------|---------|
| `storage/migrations/034_cost_records_tokens.sql` | ALTER TABLE adds `input_tokens`, `output_tokens` |

### Tests — modify/create

| File | Change |
|------|--------|
| `tests/observability/test_costs.py` | Update for always-record behavior, token columns, simplified queries |
| `tests/llm_gateway/test_pricing_override.py` | New: pricing override resolution |
| `tests/auxiliary/test_runner.py` | Verify turn_id passed through |
| `tests/observability/test_imagegen_cost.py` | New: imagegen cost recording |
