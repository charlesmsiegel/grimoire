# Post Cost Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cost tracking work everywhere — primary LLM calls, auxiliary tasks, embeddings, and image generation all write to `cost_records` with token counts, and the UI shows real cost data on posts.

**Architecture:** Remove the silent-skip guard in `CostTrackerService.record()`, add token columns to `cost_records` via migration 034, drop the `llm_requests` LEFT JOIN in cost queries, add pricing overrides to the gateway config, pass `turn_id` through auxiliary tasks, add cost to embedding and imagegen events.

**Tech Stack:** Python 3.12 / FastAPI / aiosqlite (backend). No frontend changes needed.

Source spec: `docs/superpowers/specs/2026-05-27-post-cost-tracking-design.md`. Issue: #468.

---

## File Structure

**Backend — modify:**

| File | Responsibility |
|------|----------------|
| `backend/src/grimoire/storage/migrations/034_cost_records_tokens.sql` | Add `input_tokens`, `output_tokens` columns |
| `backend/src/grimoire/types/llm.py` | `LLMCallRecord.cost_usd` → `float = 0.0` |
| `backend/src/grimoire/types/imagegen.py` | Add `cost_usd: float = 0.0` to `GenerationResult` |
| `backend/src/grimoire/observability/costs.py` | Remove None guard, add token params to INSERT, simplify queries |
| `backend/src/grimoire/observability/service.py` | Default cost to 0.0, subscribe to embedding + imagegen events |
| `backend/src/grimoire/llm_gateway/config.py` | Add `pricing_overrides` field to `GatewayConfig` |
| `backend/src/grimoire/llm_gateway/settings.py` | Add `PricingOverride` model + `pricing_overrides` to `GatewaySettings` |
| `backend/src/grimoire/llm_gateway/gateway.py` | Check pricing overrides in `_get_pricing()`, add cost to embed event |
| `backend/src/grimoire/auxiliary/types.py` | Add `turn_id: str \| None = None` to `AuxiliaryTask` |
| `backend/src/grimoire/orchestrator/auxiliary_runner.py` | Pass `turn_id=task.turn_id` to `gateway.stream()` |
| `backend/src/grimoire/imagegen/service.py` | Add `cost_usd` to `IMAGE_READY` event payload |

**Tests — modify/create:**

| File | What |
|------|------|
| `backend/tests/observability/test_costs.py` | Update for always-record + token columns + simplified queries |
| `backend/tests/llm_gateway/test_cost_fill.py` | Add pricing override tests |
| `backend/tests/auxiliary/test_runner.py` | Verify turn_id passed through |
| `backend/tests/auxiliary/conftest.py` | Capture `turn_id` on FakeGateway |

---

## Task 1: Migration — add token columns to cost_records

**Files:**
- Create: `backend/src/grimoire/storage/migrations/034_cost_records_tokens.sql`

- [ ] **Step 1: Create the migration file**

Create `backend/src/grimoire/storage/migrations/034_cost_records_tokens.sql`:

```sql
ALTER TABLE cost_records ADD COLUMN input_tokens INTEGER DEFAULT 0;
ALTER TABLE cost_records ADD COLUMN output_tokens INTEGER DEFAULT 0;
```

- [ ] **Step 2: Verify migration applies cleanly**

Run: `cd backend && uv run python -c "import asyncio; from grimoire.storage import Database, apply_migrations; db = Database(':memory:', pool_size=1); asyncio.run(db.connect()); asyncio.run(apply_migrations(db)); asyncio.run(db.close()); print('OK')"`
Expected: `OK` with no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/storage/migrations/034_cost_records_tokens.sql
git commit -m "feat(storage): migration 034 adds token columns to cost_records (#468)"
```

---

## Task 2: Type changes — LLMCallRecord and GenerationResult

**Files:**
- Modify: `backend/src/grimoire/types/llm.py:114`
- Modify: `backend/src/grimoire/types/imagegen.py:54-62`

- [ ] **Step 1: Change `LLMCallRecord.cost_usd` to non-optional**

In `backend/src/grimoire/types/llm.py`, change line 114:

```python
# Before:
    cost_usd: float | None
# After:
    cost_usd: float = 0.0
```

- [ ] **Step 2: Add `cost_usd` to `GenerationResult`**

In `backend/src/grimoire/types/imagegen.py`, add after line 62 (`error: str | None = None`):

```python
    cost_usd: float = 0.0
```

- [ ] **Step 3: Run existing tests to check nothing breaks**

Run: `cd backend && uv run pytest tests/observability/test_costs.py tests/llm_gateway/test_cost_fill.py -v`
Expected: all pass (tests that pass `cost_usd=None` still work because the test helper `_call()` defaults to `0.01`).

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/types/llm.py backend/src/grimoire/types/imagegen.py
git commit -m "feat(types): LLMCallRecord.cost_usd defaults to 0.0, GenerationResult gains cost_usd (#468)"
```

---

## Task 3: Always-record cost rows + token columns in cost_records

**Files:**
- Modify: `backend/src/grimoire/observability/costs.py:26-41`
- Test: `backend/tests/observability/test_costs.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/observability/test_costs.py`, replace `test_record_skips_none_cost` (lines 45-50) with two new tests. Delete the old test and add:

```python
async def test_record_writes_even_with_zero_cost(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.0))
    total = await tracker.total(campaign_id="c1")
    assert total.total_usd == 0.0
    assert total.call_count == 1


async def test_record_stores_tokens_in_cost_records(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.05, turn="t1"))
    row = await db.fetchone(
        "SELECT input_tokens, output_tokens FROM cost_records WHERE turn_id = ?",
        ("t1",),
    )
    assert row is not None
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/observability/test_costs.py -v -k "test_record_writes_even_with_zero_cost or test_record_stores_tokens"`
Expected: FAIL — `record()` doesn't insert token columns, and `test_record_writes_even_with_zero_cost` might fail since `_call(cost=0.0)` produces `cost_usd=0.0` which technically passes the `is None` guard but the tokens check will fail because the columns don't exist in the INSERT.

- [ ] **Step 3: Update `record()` to always write with tokens**

In `backend/src/grimoire/observability/costs.py`, replace lines 26-41 with:

```python
    async def record(self, call: LLMCallRecord) -> None:
        await self._db.execute(
            "INSERT INTO cost_records "
            "(campaign_id, turn_id, task, model, cost_usd, input_tokens, output_tokens, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call.campaign_id,
                call.turn_id,
                call.task,
                call.model,
                call.cost_usd,
                call.input_tokens,
                call.output_tokens,
                datetime.now(UTC).isoformat(),
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/observability/test_costs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/observability/costs.py backend/tests/observability/test_costs.py
git commit -m "feat(observability): always record cost rows with token columns (#468)"
```

---

## Task 4: Simplify cost queries — drop llm_requests JOIN

**Files:**
- Modify: `backend/src/grimoire/observability/costs.py:43-95,126-159`
- Test: `backend/tests/observability/test_costs.py`

- [ ] **Step 1: Write the failing test**

In `backend/tests/observability/test_costs.py`, update `test_by_turn_returns_cost_totals_by_task` (lines 102-119) to assert tokens come from `cost_records` directly. Replace lines 117-119 with:

```python
    # Tokens come from cost_records directly — no llm_requests needed.
    assert by_turn["primary"].input_tokens == 200  # 2 calls × 100 input tokens each
    assert by_turn["primary"].output_tokens == 100  # 2 calls × 50 output tokens each
```

Also delete `test_by_turn_pulls_tokens_from_llm_requests` (lines 122-155) — it tests the old LEFT JOIN behavior that we're removing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/observability/test_costs.py::test_by_turn_returns_cost_totals_by_task -v`
Expected: FAIL — old query still uses LEFT JOIN and doesn't pick up tokens from `cost_records`.

- [ ] **Step 3: Simplify `total()` — drop the LEFT JOIN**

In `backend/src/grimoire/observability/costs.py`, replace the `total()` method (lines 43-95) with:

```python
    async def total(
        self,
        campaign_id: CampaignId | None = None,
        provider: str | None = None,
        model: str | None = None,
        task: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CostTotal:
        clauses: list[str] = []
        params: list[Any] = []
        if campaign_id:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if task:
            clauses.append("task = ?")
            params.append(task)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("recorded_at < ?")
            params.append(until.isoformat())
        if provider:
            clauses.append("provider = ?")
            params.append(provider)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT
                COALESCE(SUM(cost_usd), 0.0) AS total_usd,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COUNT(id) AS call_count
            FROM cost_records
            {where}
        """
        row = await self._db.fetchone(sql, tuple(params))
        if row is None:
            return CostTotal(total_usd=0.0)
        return CostTotal(
            total_usd=float(row["total_usd"] or 0.0),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            call_count=int(row["call_count"] or 0),
        )
```

Note: the `provider` filter now references `cost_records.provider` — but `cost_records` doesn't have a `provider` column. Since no existing code passes `provider=` to `total()`, remove that parameter entirely:

```python
    async def total(
        self,
        campaign_id: CampaignId | None = None,
        model: str | None = None,
        task: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CostTotal:
```

And remove the `if provider:` clause block.

- [ ] **Step 4: Simplify `by_turn()` — drop the LEFT JOIN**

In `backend/src/grimoire/observability/costs.py`, replace the `by_turn()` method (lines 126-159) with:

```python
    async def by_turn(self, turn_id: str) -> dict[str, CostTotal]:
        rows = await self._db.fetchall(
            """
            SELECT task,
                   COALESCE(SUM(cost_usd), 0.0) AS total_usd,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COUNT(id) AS call_count
            FROM cost_records
            WHERE turn_id = ?
            GROUP BY task
            """,
            (turn_id,),
        )
        return {
            (row["task"] or ""): CostTotal(
                total_usd=float(row["total_usd"] or 0.0),
                input_tokens=int(row["input_tokens"] or 0),
                output_tokens=int(row["output_tokens"] or 0),
                call_count=int(row["call_count"] or 0),
            )
            for row in rows
        }
```

- [ ] **Step 5: Remove unused `from typing import Any` if applicable**

Check if `Any` is still used. The `total()` method still uses `list[Any]` for `params`, so it stays.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/observability/test_costs.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/observability/costs.py backend/tests/observability/test_costs.py
git commit -m "refactor(observability): drop llm_requests JOIN, read tokens from cost_records (#468)"
```

---

## Task 5: Default cost to 0.0 in observability event handler

**Files:**
- Modify: `backend/src/grimoire/observability/service.py:133-152`

- [ ] **Step 1: Update `_on_llm_response` to default cost**

In `backend/src/grimoire/observability/service.py`, change line 144:

```python
# Before:
                cost_usd=payload.get("cost_estimate_usd"),
# After:
                cost_usd=payload.get("cost_estimate_usd") or 0.0,
```

- [ ] **Step 2: Run existing tests**

Run: `cd backend && uv run pytest tests/observability/ -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/observability/service.py
git commit -m "fix(observability): default cost_usd to 0.0 in LLM response handler (#468)"
```

---

## Task 6: Pricing overrides in gateway config + settings

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/config.py`
- Modify: `backend/src/grimoire/llm_gateway/settings.py`
- Modify: `backend/src/grimoire/llm_gateway/gateway.py:181-206`
- Test: `backend/tests/llm_gateway/test_cost_fill.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/llm_gateway/test_cost_fill.py`:

```python
from grimoire.llm_gateway.config import PricingOverride


async def test_pricing_override_used_when_provider_returns_none(db, plugins) -> None:
    """Provider has no pricing; override supplies it."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(id="model-a", name="Model A"),
        ],
    )
    plugins.add_llm(provider)
    config = _config(
        pricing_overrides={"model-a": PricingOverride(input_cost_per_1k=0.01, output_cost_per_1k=0.02)}
    )
    gw = LLMGatewayService(plugins, db, config)

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert resp.cost_estimate_usd == pytest.approx(expected)


async def test_pricing_override_takes_precedence_over_provider(db, plugins) -> None:
    """Override wins even when the provider reports pricing."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a", name="Model A",
                input_cost_per_1k=999.0, output_cost_per_1k=999.0,
            ),
        ],
    )
    plugins.add_llm(provider)
    config = _config(
        pricing_overrides={"model-a": PricingOverride(input_cost_per_1k=0.01, output_cost_per_1k=0.02)}
    )
    gw = LLMGatewayService(plugins, db, config)

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert resp.cost_estimate_usd == pytest.approx(expected)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/llm_gateway/test_cost_fill.py -v -k "pricing_override"`
Expected: FAIL — `PricingOverride` doesn't exist, `pricing_overrides` not accepted by `_config()`.

- [ ] **Step 3: Add `PricingOverride` to config.py**

In `backend/src/grimoire/llm_gateway/config.py`, add before the `GatewayConfig` class:

```python
@dataclass(frozen=True)
class PricingOverride:
    input_cost_per_1k: float
    output_cost_per_1k: float
```

Then add to `GatewayConfig`:

```python
    pricing_overrides: dict[str, PricingOverride] = field(default_factory=dict)
```

The full `GatewayConfig` becomes:

```python
@dataclass(frozen=True)
class GatewayConfig:
    default_routes: dict[str, str] = field(default_factory=dict)
    fallback_routes: dict[str, str] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    embedding_cache: EmbeddingCacheConfig = field(default_factory=EmbeddingCacheConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    pricing_overrides: dict[str, PricingOverride] = field(default_factory=dict)
```

- [ ] **Step 4: Add `_PricingOverrideSettings` to settings.py**

In `backend/src/grimoire/llm_gateway/settings.py`, add before `GatewaySettings`:

```python
class _PricingOverrideSettings(BaseModel):
    input_cost_per_1k: float
    output_cost_per_1k: float
```

Add the import of `PricingOverride`:

```python
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
    PricingOverride,
)
```

Add to `GatewaySettings`:

```python
    pricing_overrides: dict[str, _PricingOverrideSettings] = {}
```

Update `to_gateway_config()` to include:

```python
            pricing_overrides={
                model: PricingOverride(
                    input_cost_per_1k=p.input_cost_per_1k,
                    output_cost_per_1k=p.output_cost_per_1k,
                )
                for model, p in self.pricing_overrides.items()
            },
```

- [ ] **Step 5: Update `_get_pricing()` to check overrides first**

In `backend/src/grimoire/llm_gateway/gateway.py`, replace the `_get_pricing()` method (lines 181-206) with:

```python
    async def _get_pricing(self, provider_id: str, model: str) -> ModelInfo | None:
        """Return cached ModelInfo for (provider_id, model), or None if unavailable.

        Checks ``pricing_overrides`` first (keyed by model name); if found,
        returns a synthetic ``ModelInfo`` with the override values. Otherwise
        calls ``provider.list_models()`` on the first miss and caches the
        result. Exceptions from ``list_models()`` are swallowed; ``None`` is
        cached so we do not re-attempt on every subsequent call.
        """
        key = (provider_id, model)
        if key in self._pricing_cache:
            return self._pricing_cache[key]
        override = self._config.pricing_overrides.get(model)
        if override is not None:
            info = ModelInfo(
                id=model,
                name=model,
                input_cost_per_1k=override.input_cost_per_1k,
                output_cost_per_1k=override.output_cost_per_1k,
            )
            self._pricing_cache[key] = info
            return info
        provider = self._plugins.get_llm_provider(provider_id)
        if provider is None:
            self._pricing_cache[key] = None
            return None
        try:
            models = await provider.list_models()
        except Exception:
            logger.debug(
                "llm_gateway: list_models() failed for provider=%s; pricing unavailable",
                provider_id,
            )
            self._pricing_cache[key] = None
            return None
        info = next((m for m in models if m.id == model), None)
        self._pricing_cache[key] = info
        return info
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/llm_gateway/test_cost_fill.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/llm_gateway/config.py backend/src/grimoire/llm_gateway/settings.py backend/src/grimoire/llm_gateway/gateway.py backend/tests/llm_gateway/test_cost_fill.py
git commit -m "feat(gateway): user-configurable pricing overrides in _get_pricing (#468)"
```

---

## Task 7: Auxiliary tasks pass originating turn_id

**Files:**
- Modify: `backend/src/grimoire/auxiliary/types.py:54-63`
- Modify: `backend/src/grimoire/orchestrator/auxiliary_runner.py:101-105`
- Modify: `backend/tests/auxiliary/conftest.py:119-158`
- Test: `backend/tests/auxiliary/test_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/auxiliary/test_runner.py`:

```python
async def test_aux_passes_turn_id_to_gateway(orchestrator, seeded_state, fake_gateway):
    task = AuxiliaryTask(
        kind=TaskKind.REWRITE_POST,
        target_post_id="p_0001",
        edit_instruction="make it darker",
        turn_id="t_0001",
    )
    await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert fake_gateway.seen_turn_ids[-1] == "t_0001"


async def test_aux_turn_id_none_when_not_set(orchestrator, seeded_state, fake_gateway):
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="ideas")
    await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert fake_gateway.seen_turn_ids[-1] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/auxiliary/test_runner.py -v -k "turn_id"`
Expected: FAIL — `turn_id` not a field on `AuxiliaryTask`, and `FakeGateway` has no `seen_turn_ids`.

- [ ] **Step 3: Add `turn_id` to `AuxiliaryTask`**

In `backend/src/grimoire/auxiliary/types.py`, add to the `AuxiliaryTask` dataclass (after line 63):

```python
    turn_id: str | None = None
```

So the class becomes:

```python
@dataclass
class AuxiliaryTask:
    kind: TaskKind
    target_character_ref: str | None = None
    target_post_id: str | None = None
    edit_instruction: str | None = None
    snippet: str | None = None
    steering_hint: str | None = None
    target_language: str | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
```

- [ ] **Step 4: Pass `turn_id` in auxiliary runner**

In `backend/src/grimoire/orchestrator/auxiliary_runner.py`, change lines 101-105:

```python
# Before:
        stream = orchestrator._gateway.stream(
            route_task,
            request,
            campaign_id=campaign_id,
        )
# After:
        stream = orchestrator._gateway.stream(
            route_task,
            request,
            campaign_id=campaign_id,
            turn_id=task.turn_id,
        )
```

- [ ] **Step 5: Capture `turn_id` in FakeGateway**

In `backend/tests/auxiliary/conftest.py`, add a `seen_turn_ids` field to `FakeGateway` (after line 124):

```python
    seen_turn_ids: list[str | None] = field(default_factory=list)
```

And in the `stream()` method (after line 156, `self.seen_tasks.append(task)`), add:

```python
        self.seen_turn_ids.append(turn_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/auxiliary/test_runner.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/auxiliary/types.py backend/src/grimoire/orchestrator/auxiliary_runner.py backend/tests/auxiliary/conftest.py backend/tests/auxiliary/test_runner.py
git commit -m "feat(auxiliary): pass originating turn_id through to gateway.stream (#468)"
```

---

## Task 8: Embedding cost in event payload

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py:1394-1423`
- Modify: `backend/src/grimoire/observability/service.py:86-94`

- [ ] **Step 1: Add cost fields to embedding event payload**

In `backend/src/grimoire/llm_gateway/gateway.py`, change the `EMBEDDING_RESPONSE_RECEIVED` emission (lines 1407-1423). After line 1406 (the `_log.record()` call) and before the `await self._emit(` call, add cost calculation:

```python
            embed_cost: float = 0.0
            embed_input_tokens = max(1, sum(len(t) // 4 for t in missing))
            info = await self._get_pricing(route.provider_id, model_id)
            if info is not None and info.input_cost_per_1k is not None:
                embed_cost = embed_input_tokens / 1000.0 * info.input_cost_per_1k
```

Then add to the event payload dict (inside the `await self._emit(...)` call), after `"timeout_override"`:

```python
                    "usage": {
                        "input_tokens": embed_input_tokens,
                        "output_tokens": 0,
                        "total_tokens": embed_input_tokens,
                    },
                    "cost_estimate_usd": embed_cost,
                    "finish_reason": "complete",
```

- [ ] **Step 2: Subscribe to embedding events in ObservabilityService**

In `backend/src/grimoire/observability/service.py`, add a second subscription in `start()` (after line 94):

```python
        if self._event_bus is not None and self._embed_subscription is None:
            self._embed_subscription = self._event_bus.subscribe(
                "embedding_response_received", self._on_llm_response
            )
```

Add the field in `__init__` (after line 83):

```python
        self._embed_subscription: Subscription | None = None
```

And clean it up in `shutdown()` (after line 107):

```python
        if self._embed_subscription is not None:
            self._embed_subscription.unsubscribe()
            self._embed_subscription = None
```

The existing `_on_llm_response` handler already extracts `usage`, `cost_estimate_usd`, `task`, `turn_id`, etc. from the event payload — it will work unchanged for embedding events.

- [ ] **Step 3: Run existing tests**

Run: `cd backend && uv run pytest tests/observability/ tests/llm_gateway/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py backend/src/grimoire/observability/service.py
git commit -m "feat(gateway): add cost to embedding event, subscribe in observability (#468)"
```

---

## Task 9: Image generation cost tracking

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py:1266-1275`
- Modify: `backend/src/grimoire/observability/service.py`

- [ ] **Step 1: Add `cost_usd` to IMAGE_READY event payload**

In `backend/src/grimoire/imagegen/service.py`, update the non-cached `IMAGE_READY` emission (lines 1266-1275) to include cost and model:

```python
# Before:
        await self._emit(
            events.IMAGE_READY,
            {
                "image_id": image_id,
                "campaign_id": job.campaign_id,
                "scene_id": job.scene_id,
                "post_id": job.post_id,
                "cached": False,
            },
        )
# After:
        await self._emit(
            events.IMAGE_READY,
            {
                "image_id": image_id,
                "campaign_id": job.campaign_id,
                "scene_id": job.scene_id,
                "post_id": job.post_id,
                "cached": False,
                "cost_usd": result.cost_usd,
                "model": result.model,
                "backend": result.backend,
            },
        )
```

Also update the cached `IMAGE_READY` emission (lines 1221-1228) to include `cost_usd: 0.0` (cached images have zero incremental cost):

```python
# Before:
                await self._emit(
                    events.IMAGE_READY,
                    {
                        "image_id": existing_image_id,
                        "campaign_id": job.campaign_id,
                        "cached": True,
                    },
                )
# After:
                await self._emit(
                    events.IMAGE_READY,
                    {
                        "image_id": existing_image_id,
                        "campaign_id": job.campaign_id,
                        "cached": True,
                        "cost_usd": 0.0,
                        "model": "",
                        "backend": "",
                    },
                )
```

- [ ] **Step 2: Subscribe to IMAGE_READY in ObservabilityService**

In `backend/src/grimoire/observability/service.py`, add a handler and subscription.

Add field in `__init__` (next to the other subscription fields):

```python
        self._imagegen_subscription: Subscription | None = None
```

In `start()`, add after the embedding subscription:

```python
        if self._event_bus is not None and self._imagegen_subscription is None:
            self._imagegen_subscription = self._event_bus.subscribe(
                "image_ready", self._on_image_ready
            )
```

In `shutdown()`, add:

```python
        if self._imagegen_subscription is not None:
            self._imagegen_subscription.unsubscribe()
            self._imagegen_subscription = None
```

Add the handler method (after `_on_llm_response`):

```python
    async def _on_image_ready(self, event: Event) -> None:
        try:
            payload = event.payload or {}
            if payload.get("cached"):
                return
            call = LLMCallRecord(
                id=uuid.uuid4().hex,
                task="imagegen",
                provider_id=str(payload.get("backend") or ""),
                model=str(payload.get("model") or ""),
                input_tokens=0,
                output_tokens=0,
                cost_usd=float(payload.get("cost_usd") or 0.0),
                latency_ms=0,
                finish_reason="complete",
                campaign_id=payload.get("campaign_id"),
                turn_id=None,
            )
            await self.costs_tracker.record(call)
        except Exception:
            logger.exception("failed to record cost from image_ready")
```

- [ ] **Step 3: Run existing tests**

Run: `cd backend && uv run pytest tests/observability/ tests/imagegen/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/imagegen/service.py backend/src/grimoire/observability/service.py
git commit -m "feat(imagegen): track image generation cost in cost_records (#468)"
```

---

## Task 10: Full test suite + lint

- [ ] **Step 1: Run backend tests**

Run: `cd backend && uv run pytest tests/observability/ tests/llm_gateway/ tests/auxiliary/ -v`
Expected: all pass.

- [ ] **Step 2: Run ruff**

Run: `cd backend && uv run ruff check src/grimoire/observability/ src/grimoire/llm_gateway/ src/grimoire/auxiliary/ src/grimoire/types/ src/grimoire/orchestrator/auxiliary_runner.py src/grimoire/imagegen/service.py`
Expected: no errors.

- [ ] **Step 3: Run ruff format check**

Run: `cd backend && uv run ruff format --check src/grimoire/observability/ src/grimoire/llm_gateway/ src/grimoire/auxiliary/ src/grimoire/types/ src/grimoire/orchestrator/auxiliary_runner.py src/grimoire/imagegen/service.py`
Expected: no reformatting needed.

- [ ] **Step 4: Run broader test suite**

Run: `cd backend && uv run pytest --timeout=60 -x`
Expected: all pass. If any test relied on the old `cost_usd=None` skip behavior or the old LEFT JOIN, it will fail here and needs updating.
