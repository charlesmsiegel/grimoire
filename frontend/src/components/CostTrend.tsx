import type { MonthlyCosts } from "../api/types";
import { money } from "./cost";

export default function CostTrend({ rows }: { rows: MonthlyCosts["trend"] }) {
  const ordered = [...rows].sort((a, b) => a.month.localeCompare(b.month));
  // A recorded call with an unknown price is still the first measurement. The
  // accompanying bars mark it incomplete instead of presenting $0 as a fact.
  const first = ordered.findIndex((row) => row.calls > 0 || row.unpriced_calls > 0
    || row.estimated_total_usd > 0);
  const months = first < 0 ? [] : ordered.slice(first);
  const peak = Math.max(0.01, ...months.map((row) => row.estimated_total_usd));
  const chartWidth = 720;
  const chartHeight = 220;
  const left = 60;
  const right = 18;
  const top = 16;
  const bottom = 40;
  const x = (index: number) => left + (months.length === 1 ? (chartWidth - left - right) / 2
    : index * (chartWidth - left - right) / (months.length - 1));
  const y = (value: number) => chartHeight - bottom
    - value / peak * (chartHeight - top - bottom);
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
    <h2 className="section-label">Estimated total cost by month</h2>
    {months.length > 0 && <div className="cost-line-wrap">
      <svg className="cost-line-chart" viewBox={`0 0 ${chartWidth} ${chartHeight}`}
        role="img" aria-label="Estimated total cost versus month line chart">
        {[0, peak / 2, peak].map((value) => <g key={value}>
          <line x1={left} x2={chartWidth - right} y1={y(value)} y2={y(value)}
            className="cost-line-grid" />
          <text x={left - 8} y={y(value) + 4} textAnchor="end"
            className="cost-line-label">{money(value)}</text>
        </g>)}
        {months.map((row, index) => <text key={row.month} x={x(index)}
          y={chartHeight - 12} textAnchor="middle" className="cost-line-label">
          {row.month}
        </text>)}
        <polyline data-testid="cost-line-estimated_total_usd" fill="none"
          stroke="var(--accent)" strokeWidth="3"
          points={months.map((row, index) => `${x(index)},${y(row.estimated_total_usd)}`).join(" ")} />
        {months.map((row, index) => <circle key={row.month} cx={x(index)}
          cy={y(row.estimated_total_usd)} r="4" fill="var(--accent)">
          <title>{row.month}: {money(row.estimated_total_usd)} estimated total
            {row.unpriced_calls > 0 ? `, incomplete: ${row.unpriced_calls} unpriced calls` : ""}</title>
        </circle>)}
      </svg>
    </div>}
    {months.length === 0 && <p className="field-hint">No cost measurements yet.</p>}
    <ol className="stats-trend cost-chart">
      {[...months].reverse().map((row) => <li key={row.month}>
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
