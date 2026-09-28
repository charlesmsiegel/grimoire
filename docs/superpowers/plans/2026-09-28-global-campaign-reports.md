# Global and Campaign Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make To do, Costs, and Stats global from the Grimoire rail and campaign-scoped from Open campaign, with monthly Costs and daily performance and reroll trends.

**Architecture:** Canonical routes carry the scope; one selector navigates among them. Backend reads produce global campaign breakdowns in one pass over their source data, while scoped routes reuse existing store readers. Cost charts add a labelled projected total without changing spend or budget accounting.

**Tech Stack:** FastAPI and the markdown/JSON store; React, TypeScript, React Router, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-global-campaign-reports-design.md`

## Global Constraints

- Global routes are `/todo`, `/costs`, `/stats`; scoped routes are `/campaigns/:cid/todo`, `/campaigns/:cid/costs`, `/campaigns/:cid/stats`.
- Costs defaults to the current UTC month in both scopes; the chosen `YYYY-MM` travels in `?month=`. Stats keeps its daily window; To do stays live.
- The Cost chart shows charged, subscription-estimated, and modelled series plus their labelled projected sum. It marks unpriced calls as incomplete. Accounting and budgets keep the three columns separate.
- Global lists name campaigns; unassigned usage and deleted campaign ids never disappear into another campaign's row.
- Only existing placeholder names (Seraphine, Mara, Winifred, Realm, Saltmarch) enter tests or docs. Never read the user's real store for verification.
- The user deferred adversarial review checkpoints to the PR. All code and test gates still run before integration.

## Review Focus

1. A last-open campaign must not silently filter a Grimoire route: Tasks 2, 4, and 5 pin their App links, active rail rows, and global reads.
2. A late response from the old scope must not render under the new campaign: Tasks 2, 4, and 5 each pin scope-switch races.
3. A month containing only modelled calls, or any unpriced call, must show the projected Cost series with an incomplete marker rather than a blank or false zero: Task 3 tests both.
4. Crossing December to January and directly requesting an invalid or empty month must preserve the selected period and report its bounds honestly: Task 3 tests each input.
5. Ignoring a chore in one campaign must not silence another, while old bare-id ignores keep their effect until migration: Task 2 tests both.

---

### Task 1: Shared report scope selector and route helper

**Files:**
- Create: `frontend/src/components/ReportScopeSelector.tsx`, `frontend/src/components/ReportScopeSelector.test.tsx`
- Modify: `frontend/src/shell/rail.ts`

**Interfaces:**
- Produce `reportHref(report: "todo" | "costs" | "stats", cid: string | null): string` in `shell/rail.ts`.
- Produce `<ReportScopeSelector report={report} cid={cid} />`, using `api.listCampaigns()` and `useNavigate()`; it shows All campaigns and readable campaign names, and keeps a selected-but-missing cid visible as unavailable.
- Later tasks mount each report's two routes when its data is ready; no page may infer report scope from the shell's open-campaign hint.

- [ ] **Step 1: Write failing selector and href tests.** Assert All campaigns and readable campaign options, selecting each yields its canonical path, a selected-but-missing cid is visible as unavailable, and encoded ids cannot produce a broken path.
- [ ] **Step 2: Run `npx vitest run src/components/ReportScopeSelector.test.tsx` from `frontend/`; confirm the new assertions fail.**
- [ ] **Step 3: Add `reportHref` and the selector, using `api.listCampaigns()` and React Router navigation.** Keep it independent of the shell's open-campaign hint.
- [ ] **Step 4: Run the focused tests and `npm run typecheck`; confirm both pass.**
- [ ] **Step 5: Commit the selector and helper.**

### Task 2: Live To do rows at both scopes

**Files:**
- Modify: `backend/src/grimoire/routes/todo.py`, `backend/src/grimoire/store/chores.py`, `backend/src/grimoire/routes/shell.py`, `backend/tests/test_todo_route.py`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/routes/TodoView.tsx`, `frontend/src/routes/TodoView.test.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/shell/rail.ts`, `frontend/src/components/AppRail.test.tsx`

**Interfaces:**
- `GET /api/todo` returns library rows and per-campaign rows, each campaign row carrying `campaign_id` and `campaign_name`; `?campaign=<cid>` returns only that campaign's rows. `GET /api/todo/{id}/items?campaign=<cid>` expands exactly that row.
- `PUT /api/todo/{id}/ignored` accepts `{ignored: boolean, campaign?: string}`. Scoped keys live in the existing ignore set; migration expands legacy campaign-type bare keys for existing campaign ids on the first write. Library keys remain bare.
- `TodoView` receives route scope rather than `openCid`. The global rail's old open-campaign badge is omitted; `/api/shell` returns `todo: null` rather than a count for the wrong scope.

- [ ] **Step 1: Write failing backend tests.** Assert global rows name two campaigns separately plus library chores; `?campaign=` excludes library and other campaigns; item expansion uses its row cid; ignore and Restore affect only one campaign; legacy bare ignores migrate on write; shell does not report a misleading global count.
- [ ] **Step 2: Run `test_todo_route.py` and confirm the new assertions fail.** Use the isolated pytest temp setup described in `CONTRIBUTING.md` for this Windows workspace.
- [ ] **Step 3: Implement the route and store contracts.** Reuse the existing campaign and library builders, with one `_Ctx` per campaign. Keep counts live and only fetch items on expansion.
- [ ] **Step 4: Write failing frontend tests for route and rail scope, campaign labels, scoped requests and ignore bodies, empty states, and a response from an old scope arriving late; run them and confirm failure.**
- [ ] **Step 5: Mount both To do routes, update its rail rows and API/view contract; run backend To do tests, frontend To do/App/rail tests, and typecheck until green.**
- [ ] **Step 6: Commit To do scope behavior.**

### Task 3: Monthly Cost aggregation and accounting contract

**Files:**
- Modify: `backend/src/grimoire/store/usage.py`, `backend/src/grimoire/routes/usage.py`, `backend/tests/test_usage_store.py`, `backend/tests/test_usage_routes.py`, `CLAUDE.md`

**Interfaces:**
- Add `usage.monthly_campaigns(month: str = "") -> dict` and `GET /api/usage/monthly?month=YYYY-MM`. Return `month`, UTC `since`/`until`, `available_months`, selected-month `totals`, `campaigns`, `unassigned`, and a twelve-month `trend`. Each trend bucket carries the three existing money values, `estimated_total_usd`, and `unpriced_calls`.
- Extend `usage.campaign_scenes(..., month: str = "")` and `GET /api/campaigns/{cid}/usage/scenes?month=`. Empty month preserves the existing all-time contract; a supplied month limits the scan, totals, sorting, and scene rows to that UTC month.
- Validate a supplied month strictly as `YYYY-MM`; a malformed month returns 400. Include current month in `available_months` even without calls. Derive `estimated_total_usd` from unrounded bucket values; never use it in budgets or the ordinary cost formatter.

- [ ] **Step 1: Write failing store and route tests.** Cover current-month default, December/January boundaries, absent month, malformed month, campaign versus unassigned/deleted-id rows, month-only scenes and sort-before-cap, modelled-only projected total, mixed-source sum, unpriced incompleteness, and unchanged all-time calls without `month`.
- [ ] **Step 2: Run focused usage store/route tests and confirm the new assertions fail.**
- [ ] **Step 3: Implement month parsing, one-pass monthly buckets, bounded trend, and the route responses.** Apply the existing `_add`/`_rounded` money semantics; the new projection is only a separately named graph field.
- [ ] **Step 4: Update `CLAUDE.md` to distinguish the approved projected graph total from spend; run focused tests and `scripts/verify_templates.py` until green.**
- [ ] **Step 5: Commit monthly Cost data and convention.**

### Task 4: Monthly Cost pages and graph

**Files:**
- Create: `frontend/src/routes/GlobalCostsView.tsx`, `frontend/src/routes/GlobalCostsView.test.tsx`, `frontend/src/components/CostTrend.tsx`, `frontend/src/components/CostTrend.test.tsx`
- Modify: `frontend/src/routes/CostsView.tsx`, `frontend/src/routes/CostsView.test.tsx`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/shell/rail.ts`, `frontend/src/components/AppRail.test.tsx`

**Interfaces:**
- `api.getMonthlyCosts(month: string)` consumes Task 3's global response; `api.getCampaignSceneCosts(cid, order, month?: string)` passes an explicit month for the page while retaining the old no-month API behavior for other callers.
- The global page renders a twelve-month accessible Cost graph and one table row per active campaign plus unassigned calls. Both pages share a `?month=YYYY-MM` selector and `ReportScopeSelector`; changing month or scope changes the URL.

- [ ] **Step 1: Write failing frontend tests.** Assert global and campaign rail links/activation, current month on first load despite an open campaign, month navigation and year rollover, global campaign rows and unassigned row, modelled-only graph plus projected total, incomplete marker, campaign scene month query, empty/failed reads, and stale response rejection after a scope or month switch.
- [ ] **Step 2: Run global Costs, CostTrend, and existing CostsView tests; confirm the new assertions fail.**
- [ ] **Step 3: Mount the global Costs route and implement its rail row, page, graph, month controls, and scoped page updates.** Preserve existing `MoneyColumns`, scene sorting, and reported-price wording; name the graph sum only "Estimated total".
- [ ] **Step 4: Run focused frontend tests and typecheck until green.**
- [ ] **Step 5: Commit monthly Cost UI.**

### Task 5: Campaign breakdowns and daily reroll signal in Stats

**Files:**
- Modify: `backend/src/grimoire/store/metrics.py`, `backend/src/grimoire/store/errors.py`, `backend/tests/test_metrics.py`, `backend/tests/test_observability_routes.py`
- Modify: `frontend/src/api/types.ts`, `frontend/src/routes/StatsView.tsx`, `frontend/src/routes/StatsView.test.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/shell/rail.ts`, `frontend/src/components/AppRail.test.tsx`

**Interfaces:**
- `/api/stats` adds `by_campaign` performance buckets and per-day `rerolls`, `eligible_turns`, and nullable `reroll_rate`; `/api/errors` adds a campaign breakdown. Unassigned activity has an explicit bucket. Manual rerolls are `retry` and `regenerate`; `replay` is excluded. The rate denominator is the day's `chat + retry + regenerate` usage calls.
- `StatsView` takes route scope and sends it to `getStats`, `getErrorSummary`, `getLogs`, and `streamLogTail`. Global performance and error sections show campaign rows; global log rows show their campaign when present. Existing daily latency and failure graphs remain; add reroll count/rate graph with its denominator.

- [ ] **Step 1: Write failing backend tests.** Assert campaign and unassigned rows partition global calls/errors, scoped reads exclude other campaigns, replay is not a manual reroll, and a day without eligible attempts has `reroll_rate: null`.
- [ ] **Step 2: Run focused metrics and observability tests and confirm failure.**
- [ ] **Step 3: Add one-pass campaign and daily reroll buckets to metrics and error summaries; run focused backend tests until green.**
- [ ] **Step 4: Write failing frontend tests.** Cover global and campaign rail links/activation, global campaign rows despite an open campaign, the denominator and empty-day graph label, all four scoped API calls, tail restart, and late responses from the old scope.
- [ ] **Step 5: Mount the scoped Stats route, update its rail row and UI/API types; run StatsView/App/rail tests and typecheck until green.**
- [ ] **Step 6: Commit Stats scope and trends.**

### Task 6: Integration and verification

**Files:**
- Modify only documentation or tests required by the integrated behavior.

**Interfaces:** All three global pages, three scoped shortcuts, and their selectors must agree with the route, server, and rail contracts above.

- [ ] **Step 1: Review the spec requirement by requirement against the branch diff and repair any gap.** Include exact route URLs, scoped read calls, and the estimated-total graph definition in the review.
- [ ] **Step 2: Run `make check` with the documented interpreter and capture each target's result.** Resolve failures caused by this branch; report unrelated baseline or environment failures with their exact output. Run frontend and backend focused tests again on the final tree.
- [ ] **Step 3: Exercise the six routes against the isolated `verify` skill store, including month and scope changes, if the browser harness is available.** Never use a real library for screenshots.
- [ ] **Step 4: Run `git diff --check`, inspect the staged diff for private names, and prepare the branch for PR review.** Per the user's direction, defer adversarial review to the PR.
