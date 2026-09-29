import type { MonthlyCosts } from "../api/types";
import { money } from "./cost";

export default function CostTrend({ rows }: { rows: MonthlyCosts["trend"] }) {
  const peak = Math.max(0.000001, ...rows.map((row) => row.estimated_total_usd));
  const series = [
    { key: "cost_usd", label: "Provider charges", color: "#64a6c8" },
    { key: "estimated_usd", label: "Subscription estimates", color: "#b188d5" },
    { key: "modelled_usd", label: "Modelled", color: "#d2a166" },
    { key: "estimated_total_usd", label: "Estimated total", color: "#8fc697" },
  ] as const;
  const amount = (row: MonthlyCosts["trend"][number], key: typeof series[number]["key"]) => {
    if (key === "cost_usd" && row.priced_calls <= row.subscription_calls) return "—";
    if (key === "estimated_usd" && row.subscription_calls === 0) return "—";
    if (key === "modelled_usd" && row.modelled_calls === 0) return "—";
    if (key === "estimated_total_usd" && row.unpriced_calls > 0
        && row.estimated_total_usd === 0) return "Incomplete";
    return money(row[key]);
  };
  return <section className="stats-block" aria-label="Monthly cost trend">
    <h2 className="section-label">Estimated cost by month</h2>
    <ol className="stats-trend cost-chart">
      {[...rows].sort((a, b) => b.month.localeCompare(a.month)).map((row) => <li key={row.month}>
        <span className="stats-trend-day" data-testid="cost-trend-month">{row.month}</span>
        <span className="cost-trend-series">{series.map(({ key, label, color }) =>
          <span key={key} className="cost-trend-series-row">
            <span className="field-hint">{label}</span>
            <span className="stats-trend-track"><span className="stats-trend-bar"
              style={{ width: `${row[key] / peak * 100}%`, backgroundColor: color }} /></span>
            <span className="stats-trend-value">{amount(row, key)}</span>
          </span>)}</span>
        {row.unpriced_calls > 0 &&
          <span className="field-hint">Incomplete: {row.unpriced_calls} unpriced calls</span>}
      </li>)}
    </ol>
  </section>;
}
