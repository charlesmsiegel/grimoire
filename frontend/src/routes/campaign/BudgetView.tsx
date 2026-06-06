/**
 * Budget view — surfaces cost-tracking data for a campaign (issue #354).
 *
 * Header shows the configured warn/alert thresholds plus today's spend and the
 * campaign-since-startup total. Below, a 30-day rollup table groups cost by
 * UTC day. All values come from `/api/observability/costs/*` and the global
 * `/api/observability/config/cost` (warn/alert thresholds live on the
 * deployment, not the campaign — per-campaign overrides are not yet modelled).
 */

import { useCallback } from "react";
import { useParams } from "react-router-dom";

import { observabilityApi, type CostConfig, type DailyCost } from "../../api/observability";
import { useApi } from "../../api/useApi";
import { Loading } from "./common";

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatDay(raw: string): string {
  // The backend returns an ISO datetime keyed on the UTC day. Strip the time
  // portion for display; fall back to the raw string if parsing fails.
  const parsed = Date.parse(raw);
  if (Number.isNaN(parsed)) return raw;
  return new Date(parsed).toISOString().slice(0, 10);
}

interface BudgetSummary {
  config: CostConfig;
  sessionUsd: number;
  todayUsd: number;
  rollup: DailyCost[];
}

async function loadBudget(campaignId: string): Promise<BudgetSummary> {
  const [config, session, today, rollup] = await Promise.all([
    observabilityApi.getCostConfig(),
    observabilityApi.getSessionCost(campaignId),
    observabilityApi.getTotalToday(campaignId),
    observabilityApi.getCostRollup(campaignId, 30),
  ]);
  return {
    config,
    sessionUsd: session.total_usd,
    todayUsd: today.total_usd,
    rollup,
  };
}

export function BudgetView() {
  const { campaignId = "" } = useParams();
  const state = useApi(useCallback(() => loadBudget(campaignId), [campaignId]));

  return (
    <section className="route campaign-budget" aria-labelledby="budget-heading">
      <header className="route-header">
        <h2 id="budget-heading">Budget</h2>
      </header>
      <Loading state={state}>
        {(data) => (
          <>
            <ThresholdsCard data={data} />
            <RollupTable rollup={data.rollup} />
          </>
        )}
      </Loading>
    </section>
  );
}

function ThresholdsCard({ data }: { data: BudgetSummary }) {
  const { config, sessionUsd, todayUsd } = data;
  const severity =
    todayUsd >= config.daily_budget_alert_usd
      ? "alert"
      : todayUsd >= config.daily_budget_warn_usd
        ? "warn"
        : null;
  return (
    <dl className="budget-summary">
      <div>
        <dt>Campaign total</dt>
        <dd>{formatUsd(sessionUsd)}</dd>
      </div>
      <div
        data-warn={severity === "warn" ? "true" : undefined}
        data-alert={severity === "alert" ? "true" : undefined}
      >
        <dt>Today</dt>
        <dd>{formatUsd(todayUsd)}</dd>
      </div>
      <div>
        <dt>Daily warn</dt>
        <dd>{formatUsd(config.daily_budget_warn_usd)}</dd>
      </div>
      <div>
        <dt>Daily alert</dt>
        <dd>{formatUsd(config.daily_budget_alert_usd)}</dd>
      </div>
    </dl>
  );
}

function RollupTable({ rollup }: { rollup: DailyCost[] }) {
  if (rollup.length === 0) {
    return <p className="muted">No cost records in the last 30 days.</p>;
  }
  const newestFirst = [...rollup].sort((a, b) => (a.date < b.date ? 1 : -1));
  const total = rollup.reduce((acc, r) => acc + r.total_usd, 0);
  return (
    <div className="budget-rollup">
      <h3>Last 30 days</h3>
      <table className="budget-rollup-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Calls</th>
            <th scope="col">Cost</th>
          </tr>
        </thead>
        <tbody>
          {newestFirst.map((row) => (
            <tr key={row.date}>
              <td>{formatDay(row.date)}</td>
              <td>{row.call_count.toLocaleString()}</td>
              <td>{formatUsd(row.total_usd)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total</th>
            <td />
            <td>{formatUsd(total)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
