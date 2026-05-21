# Per-turn cost breakdown debug view

> Implements issue #353 (§8 of the observability remaining-design backlog).
> Source: `docs/superpowers/specs/2026-05-18-observability-COMPLETED.md` §8.

## Goal

Surface the per-task cost split for a single turn so that — for any model post — a user can answer "what did this turn actually cost me, and where did the money go?". Split is by `cost_records.task`: primary generation, extraction, drift check, image prompt rewriter, embedding, etc.

The backend already records the data; the service helper and HTTP route exist but return a thin shape with only `total_usd` and `call_count`. This work refines those to carry tokens too and adds a small frontend affordance.

## Non-goals

- Session/campaign-wide cost rollups (§9; already has routes, frontend deferred).
- Budget enforcement (§15; orchestrator side).
- Anything dependent on per-message source attribution (§7).

## Backend

### `CostTrackerService.by_turn(turn_id) -> dict[str, CostTotal]`

Replace the current `dict[str, dict[str, Any]]` return with `dict[str, CostTotal]` (matches the issue's stated signature). Query mirrors `total()`'s LEFT JOIN against `llm_requests` so tokens are populated:

```sql
SELECT cr.task AS task,
       COALESCE(SUM(cr.cost_usd), 0.0) AS total_usd,
       COALESCE(SUM(lr.prompt_tokens), 0) AS input_tokens,
       COALESCE(SUM(lr.completion_tokens), 0) AS output_tokens,
       COUNT(cr.id) AS call_count
FROM cost_records cr
LEFT JOIN llm_requests lr
    ON lr.turn_id = cr.turn_id AND lr.task = cr.task AND lr.model = cr.model
WHERE cr.turn_id = ?
GROUP BY cr.task
```

Notes:
- A NULL or empty `task` collapses under the empty-string key — consistent with `by_task` / `by_model`.
- `LEFT JOIN` ensures cost rows without a matching `llm_requests` row still contribute `total_usd` / `call_count` (tokens default to 0).

### Route

`GET /api/observability/turns/{turn_id}/costs` (already exists). Convert the service dict into a sorted list and serialize each `CostTotal` to JSON:

```json
[
  { "task": "primary",    "total_usd": 0.012, "input_tokens": 800, "output_tokens": 350, "call_count": 1 },
  { "task": "extraction", "total_usd": 0.001, "input_tokens": 400, "output_tokens":  50, "call_count": 1 }
]
```

Order: `total_usd` descending, then `task` ascending for stable ordering when totals tie. Empty turn → `[]`.

## Frontend

### `frontend/src/api/observability.ts` (new)

Minimal API client module. Types and one method:

```ts
export interface TaskCostRow {
  task: string;
  total_usd: number;
  input_tokens: number;
  output_tokens: number;
  call_count: number;
}

export const observabilityApi = {
  turnCosts(turnId: string): Promise<TaskCostRow[]> {
    return api.get(`/api/observability/turns/${encodeURIComponent(turnId)}/costs`);
  },
};
```

### `frontend/src/routes/campaign/CostBreakdown.tsx` (new)

Small fetch-on-mount component:

- Props: `{ turnId: string }`.
- Loads via `observabilityApi.turnCosts` on mount; renders loading / error / table.
- Table columns: Task, Calls, Input tokens, Output tokens, USD. Footer row totals.
- Pure presentational; no internal toggle (parent owns visibility).

### `PostItem.tsx` change

For model-authored posts where `campaignId` is set, add a "Cost" button to the action row (next to Retcon/Rewrite). Toggling it mounts `<CostBreakdown turnId={post.turn_id} />` below the action row. PC posts and rows without a `turn_id` are unchanged.

## Tests

### Backend

- `backend/tests/observability/test_costs.py` — update `test_by_turn` (or add `test_by_turn_groups_by_task`) to assert the new `dict[str, CostTotal]` shape; add a case that also inserts an `llm_requests` row to verify tokens flow through the join.
- `backend/tests/api/test_observability_routes.py` — add a test that seeds two cost rows (one with a matching `llm_requests` row, one without) and asserts the route returns a sorted list with the expected shape.

### Frontend

- `frontend/src/routes/campaign/__tests__/PostItem.test.tsx` — add a test that clicking the "Cost" button calls `observabilityApi.turnCosts(post.turn_id)` and renders the returned rows. Mock the fetch the same way the existing aux tests do.

## Migration / compatibility

The current `dict[str, dict[str, Any]]` route response is undocumented and only consumed by the (not-yet-written) frontend. Changing it to a list is not a compatibility break.

## Out of scope

- Streaming/live updates: the breakdown reflects whatever is in `cost_records` at fetch time. The route is idempotent and cheap; users re-open the panel to refresh.
- Embedding-specific cost: embeddings are written with `task="embedding"` per the existing convention; no new task taxonomy.
