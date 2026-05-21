# Per-turn cost breakdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface per-task cost split for a single turn — refine `CostTrackerService.by_turn` to return `dict[str, CostTotal]` with tokens, have the route serialize a sorted list, and add a "Cost" toggle to `PostItem` that opens a breakdown panel.

**Architecture:** Backend join is `cost_records LEFT JOIN llm_requests` (mirrors `total()`); the route flattens the dict into a list sorted by `total_usd` desc, then `task` asc. Frontend adds a small fetch-on-mount component mounted from `PostItem`'s action row.

**Tech Stack:** Python 3.12 / FastAPI / aiosqlite (backend), React 18 / Vitest / React Testing Library (frontend).

Source spec: `docs/superpowers/specs/2026-05-20-cost-breakdown-design.md`. Issue: #353.

---

## File Structure

**Backend:**
- Modify: `backend/src/grimoire/observability/costs.py` — `by_turn` signature and SQL.
- Modify: `backend/src/grimoire/api/observability.py` — `/turns/{turn_id}/costs` handler.
- Modify: `backend/tests/observability/test_costs.py` — replace existing `dict[str, dict]` assertions; add token-join test.
- Modify: `backend/tests/api/test_observability_routes.py` — add route test seeding `cost_records` + `llm_requests`.

**Frontend:**
- Create: `frontend/src/api/observability.ts` — `observabilityApi.turnCosts(turnId)` + `TaskCostRow` type.
- Create: `frontend/src/routes/campaign/CostBreakdown.tsx` — fetch-on-mount table component.
- Modify: `frontend/src/routes/campaign/PostItem.tsx` — add "Cost" button + collapsible mount.
- Modify: `frontend/src/routes/campaign/__tests__/PostItem.test.tsx` — add Cost button + render test.

---

## Task 1: Backend — refactor `by_turn` to return `dict[str, CostTotal]` with tokens

**Files:**
- Modify: `backend/src/grimoire/observability/costs.py:126-138`
- Test: `backend/tests/observability/test_costs.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/observability/test_costs.py`:

```python
from grimoire.types.observability import CostTotal


async def test_by_turn_returns_cost_totals_by_task(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.01, task="primary", turn="t1"))
    await tracker.record(_call(cost=0.02, task="primary", turn="t1"))
    await tracker.record(_call(cost=0.005, task="extraction", turn="t1"))
    await tracker.record(_call(cost=0.99, task="primary", turn="t2"))  # other turn

    by_turn = await tracker.by_turn("t1")

    assert set(by_turn.keys()) == {"primary", "extraction"}
    assert isinstance(by_turn["primary"], CostTotal)
    assert abs(by_turn["primary"].total_usd - 0.03) < 1e-9
    assert by_turn["primary"].call_count == 2
    assert by_turn["extraction"].total_usd == 0.005
    assert by_turn["extraction"].call_count == 1
    # No matching llm_requests rows — tokens default to 0.
    assert by_turn["primary"].input_tokens == 0
    assert by_turn["primary"].output_tokens == 0


async def test_by_turn_pulls_tokens_from_llm_requests(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.04, task="primary", model="m-1", turn="t9"))
    # Matching llm_requests row carries the tokens for that (turn, task, model).
    await db.execute(
        "INSERT INTO llm_requests ("
        "id, campaign_id, turn_id, task, provider, model, "
        "prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, "
        "retries, fallback_used, request_hash, response_excerpt, error, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r1", "c1", "t9", "primary", "p", "m-1", 250, 90, 340, 0.04, 100, 0, 0, None, None, None,
         datetime.now(UTC).isoformat()),
    )

    by_turn = await tracker.by_turn("t9")
    assert by_turn["primary"].input_tokens == 250
    assert by_turn["primary"].output_tokens == 90
    assert by_turn["primary"].call_count == 1


async def test_by_turn_unknown_returns_empty(db) -> None:
    tracker = CostTrackerService(db)
    assert await tracker.by_turn("never_seen") == {}
```

Also delete (or rewrite) any existing `test_by_turn*` that asserts the old `dict[str, dict[str, Any]]` shape. Use `Grep` for `by_turn` in that file first; if a stale test exists, remove it as part of this step.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/observability/test_costs.py -v -k by_turn`
Expected: FAIL (current `by_turn` returns dicts, not `CostTotal`).

- [ ] **Step 3: Replace `by_turn` implementation**

In `backend/src/grimoire/observability/costs.py`, replace the `by_turn` method (currently at lines ~126-138) with:

```python
    async def by_turn(self, turn_id: str) -> dict[str, CostTotal]:
        """Per-task cost breakdown for one turn.

        Joins ``cost_records`` against ``llm_requests`` on
        ``(turn_id, task, model)`` so the per-task totals carry token counts
        from the gateway's request log. Tasks with no matching
        ``llm_requests`` row still get a total — tokens default to 0.
        """
        rows = await self._db.fetchall(
            """
            SELECT cr.task AS task,
                   COALESCE(SUM(cr.cost_usd), 0.0) AS total_usd,
                   COALESCE(SUM(lr.prompt_tokens), 0) AS input_tokens,
                   COALESCE(SUM(lr.completion_tokens), 0) AS output_tokens,
                   COUNT(cr.id) AS call_count
            FROM cost_records cr
            LEFT JOIN llm_requests lr
                ON lr.turn_id = cr.turn_id
                AND lr.task = cr.task
                AND lr.model = cr.model
            WHERE cr.turn_id = ?
            GROUP BY cr.task
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

Also remove the now-unused `from typing import Any` import if it's no longer referenced anywhere in the file. Check with: `grep "Any" backend/src/grimoire/observability/costs.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/observability/test_costs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/observability/costs.py backend/tests/observability/test_costs.py
git commit -m "feat(observability): by_turn returns dict[str, CostTotal] with tokens (#353)"
```

---

## Task 2: Backend — route serializes sorted list

**Files:**
- Modify: `backend/src/grimoire/api/observability.py:82-84`
- Test: `backend/tests/api/test_observability_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/api/test_observability_routes.py`:

```python
from datetime import UTC, datetime as _dt


@pytest.mark.asyncio
async def test_turn_costs_returns_sorted_list(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    db = container_with_obs.db
    # Two cost rows for turn t_cost: extraction (cheap) and primary (expensive).
    now = _dt.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO cost_records (campaign_id, turn_id, task, model, cost_usd, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "t_cost", "extraction", "m-2", 0.001, now),
    )
    await db.execute(
        "INSERT INTO cost_records (campaign_id, turn_id, task, model, cost_usd, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "t_cost", "primary", "m-1", 0.05, now),
    )
    # Matching llm_requests row for the primary call only — tokens flow through.
    await db.execute(
        "INSERT INTO llm_requests ("
        "id, campaign_id, turn_id, task, provider, model, "
        "prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, "
        "retries, fallback_used, request_hash, response_excerpt, error, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r1", "c1", "t_cost", "primary", "p", "m-1", 800, 350, 1150, 0.05,
         200, 0, 0, None, None, None, now),
    )

    resp = client.get("/api/observability/turns/t_cost/costs")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert [r["task"] for r in rows] == ["primary", "extraction"]
    assert rows[0]["total_usd"] == 0.05
    assert rows[0]["input_tokens"] == 800
    assert rows[0]["output_tokens"] == 350
    assert rows[0]["call_count"] == 1
    assert rows[1]["task"] == "extraction"
    assert rows[1]["input_tokens"] == 0  # no matching llm_requests row


@pytest.mark.asyncio
async def test_turn_costs_unknown_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/api/observability/turns/no_such_turn/costs")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/api/test_observability_routes.py -v -k turn_costs`
Expected: FAIL — the route currently returns a dict, not a list.

- [ ] **Step 3: Update the route handler**

In `backend/src/grimoire/api/observability.py`, replace the existing route (lines ~82-84):

```python
@router.get("/turns/{turn_id}/costs")
async def get_turn_costs(turn_id: str, observability: ObservabilityDep) -> Any:
    breakdown = await observability.costs().by_turn(turn_id)
    rows = [
        {"task": task, **total.model_dump()}
        for task, total in breakdown.items()
    ]
    rows.sort(key=lambda r: (-r["total_usd"], r["task"]))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/api/test_observability_routes.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/observability.py backend/tests/api/test_observability_routes.py
git commit -m "feat(api): /turns/{turn_id}/costs returns sorted list shape (#353)"
```

---

## Task 3: Frontend — `observabilityApi` client + `CostBreakdown` component

**Files:**
- Create: `frontend/src/api/observability.ts`
- Create: `frontend/src/routes/campaign/CostBreakdown.tsx`
- Create: `frontend/src/routes/campaign/__tests__/CostBreakdown.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/campaign/__tests__/CostBreakdown.test.tsx`:

```tsx
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { CostBreakdown } from "../CostBreakdown";
import { observabilityApi } from "../../../api/observability";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CostBreakdown", () => {
  it("renders one row per task plus a totals footer", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.05, input_tokens: 800, output_tokens: 350, call_count: 1 },
      { task: "extraction", total_usd: 0.001, input_tokens: 400, output_tokens: 50, call_count: 1 },
    ]);

    render(<CostBreakdown turnId="t1" />);

    expect(await screen.findByText("primary")).toBeInTheDocument();
    expect(screen.getByText("extraction")).toBeInTheDocument();
    // Totals footer sums both rows.
    const totalCell = screen.getByTestId("cost-total-usd");
    expect(totalCell).toHaveTextContent("$0.0510");
  });

  it("renders an empty-state message when there are no rows", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<CostBreakdown turnId="t_empty" />);
    expect(await screen.findByText(/no recorded cost/i)).toBeInTheDocument();
  });

  it("surfaces the error when the fetch fails", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockRejectedValue(new Error("boom"));
    render(<CostBreakdown turnId="t_err" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/campaign/__tests__/CostBreakdown.test.tsx`
Expected: FAIL — neither `observabilityApi` nor `CostBreakdown` exists.

- [ ] **Step 3: Create the API client**

Create `frontend/src/api/observability.ts`:

```ts
/**
 * Observability REST client.
 *
 * Backs debug surfaces that read from the per-turn audit / cost tables.
 * Spec: docs/superpowers/specs/2026-05-20-cost-breakdown-design.md.
 */

import { api } from "./client";

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

- [ ] **Step 4: Create the component**

Create `frontend/src/routes/campaign/CostBreakdown.tsx`:

```tsx
import { useEffect, useState } from "react";

import { observabilityApi, type TaskCostRow } from "../../api/observability";

interface Props {
  turnId: string;
}

function fmtUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function CostBreakdown({ turnId }: Props) {
  const [rows, setRows] = useState<TaskCostRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    observabilityApi
      .turnCosts(turnId)
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [turnId]);

  if (error) {
    return (
      <p className="cost-breakdown-error" role="alert">
        {error}
      </p>
    );
  }
  if (rows === null) {
    return <p className="cost-breakdown-loading">Loading cost breakdown…</p>;
  }
  if (rows.length === 0) {
    return <p className="cost-breakdown-empty">No recorded cost for this turn.</p>;
  }

  const totalUsd = rows.reduce((acc, r) => acc + r.total_usd, 0);
  const totalIn = rows.reduce((acc, r) => acc + r.input_tokens, 0);
  const totalOut = rows.reduce((acc, r) => acc + r.output_tokens, 0);
  const totalCalls = rows.reduce((acc, r) => acc + r.call_count, 0);

  return (
    <table className="cost-breakdown" aria-label="Per-task cost breakdown">
      <thead>
        <tr>
          <th>Task</th>
          <th>Calls</th>
          <th>Input tokens</th>
          <th>Output tokens</th>
          <th>USD</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.task || "(unspecified)"}>
            <td>{r.task || "(unspecified)"}</td>
            <td>{r.call_count}</td>
            <td>{r.input_tokens}</td>
            <td>{r.output_tokens}</td>
            <td>{fmtUsd(r.total_usd)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td>Total</td>
          <td>{totalCalls}</td>
          <td>{totalIn}</td>
          <td>{totalOut}</td>
          <td data-testid="cost-total-usd">{fmtUsd(totalUsd)}</td>
        </tr>
      </tfoot>
    </table>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/campaign/__tests__/CostBreakdown.test.tsx`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/observability.ts frontend/src/routes/campaign/CostBreakdown.tsx frontend/src/routes/campaign/__tests__/CostBreakdown.test.tsx
git commit -m "feat(frontend): CostBreakdown component + observability API client (#353)"
```

---

## Task 4: Frontend — wire "Cost" toggle into `PostItem`

**Files:**
- Modify: `frontend/src/routes/campaign/PostItem.tsx`
- Modify: `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`

- [ ] **Step 1: Write the failing test**

Append a new test inside the existing `describe("PostItem chevron strip", ...)` block (or create a new `describe("PostItem cost toggle", ...)` block) in `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`:

```tsx
import { observabilityApi } from "../../../api/observability";

describe("PostItem cost toggle", () => {
  it("Cost button is absent for player posts", () => {
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.queryByRole("button", { name: /^cost$/i })).toBeNull();
  });

  it("clicking Cost fetches and renders the per-task breakdown", async () => {
    const spy = vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.012, input_tokens: 800, output_tokens: 350, call_count: 1 },
      { task: "extraction", total_usd: 0.001, input_tokens: 400, output_tokens: 50, call_count: 1 },
    ]);
    const post = makePost();
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: /^cost$/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("t1"));
    expect(await screen.findByText("primary")).toBeInTheDocument();
  });

  it("clicking Cost twice toggles the panel off", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    const post = makePost();
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" />);
    const btn = screen.getByRole("button", { name: /^cost$/i });
    fireEvent.click(btn);
    expect(await screen.findByText(/no recorded cost/i)).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.queryByText(/no recorded cost/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/campaign/__tests__/PostItem.test.tsx`
Expected: FAIL — there is no "Cost" button on `PostItem` yet.

- [ ] **Step 3: Add the Cost toggle to PostItem**

Edit `frontend/src/routes/campaign/PostItem.tsx`:

1. Add the import at the top with the other route-local imports:

```tsx
import { CostBreakdown } from "./CostBreakdown";
```

2. Add a `costOpen` state next to the other useState hooks (near `setRetconOpen`):

```tsx
const [costOpen, setCostOpen] = useState(false);
```

3. Add a derived flag near `canRetcon` / `canRewrite`:

```tsx
const canShowCost = !!campaignId && post.author_kind !== "pc" && !post.is_player && !!post.turn_id;
```

4. Inside the `post-actions` block (the existing `<div className="post-actions">`), add a new button next to the other action buttons. Place it after the `canRetcon`/`canRewrite` buttons and before the `Continue as` button. The gating condition on the wrapper `(canRetcon || canRewrite || campaignId)` already covers it.

```tsx
{canShowCost && (
  <button
    type="button"
    className="post-cost-toggle"
    aria-label="Toggle cost breakdown"
    aria-expanded={costOpen}
    onClick={() => setCostOpen((v) => !v)}
  >
    Cost
  </button>
)}
```

5. Mount the panel just below the `auxResult` render and above the `retconOpen` render (i.e., near the bottom of the JSX, inside the `<article>`):

```tsx
{costOpen && canShowCost && <CostBreakdown turnId={post.turn_id} />}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/campaign/__tests__/PostItem.test.tsx`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/campaign/PostItem.tsx frontend/src/routes/campaign/__tests__/PostItem.test.tsx
git commit -m "feat(frontend): PostItem Cost toggle mounts CostBreakdown (#353)"
```

---

## Task 5: Verify the full suite + typecheck

- [ ] **Step 1: Backend tests**

Run: `pytest backend/tests/observability/ backend/tests/api/test_observability_routes.py -v`
Expected: all pass, no warnings about deprecated shapes.

- [ ] **Step 2: Frontend tests**

Run: `cd frontend && npx vitest run src/routes/campaign/`
Expected: all pass.

- [ ] **Step 3: Frontend typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Backend typecheck (if mypy is configured)**

Run: `cd backend && python -m mypy src/grimoire/observability/costs.py src/grimoire/api/observability.py` (skip if mypy isn't configured for the repo).
Expected: no new errors.

- [ ] **Step 5: Open PR**

Once everything is green:

```bash
git push -u origin issue-353
gh pr create --title "Per-turn cost breakdown debug view (#353)" --body "$(cat <<'EOF'
## Summary
- `CostTrackerService.by_turn` now returns `dict[str, CostTotal]` with tokens pulled from `llm_requests` (LEFT JOIN on turn_id/task/model).
- `GET /api/observability/turns/{turn_id}/costs` returns a list sorted by `total_usd` desc, then task asc.
- New `CostBreakdown` component + "Cost" toggle on model `PostItem`s.

Closes #353.

## Test plan
- [ ] `pytest backend/tests/observability/ backend/tests/api/test_observability_routes.py`
- [ ] `cd frontend && npx vitest run src/routes/campaign/`
- [ ] `cd frontend && npx tsc --noEmit`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
