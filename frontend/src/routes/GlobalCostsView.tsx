import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MonthlyCosts } from "../api/types";
import { MoneyColumns, about, bound, estimatedTokensTitle, money } from "../components/cost";
import CostTrend from "../components/CostTrend";
import { ColumnSection, PageShell } from "../components/PageShell";
import ReportMonth, { useReportMonth } from "../components/ReportMonth";
import ReportScopeSelector from "../components/ReportScopeSelector";

export default function GlobalCostsView() {
  const [month, choose] = useReportMonth();
  const [report, setReport] = useState<MonthlyCosts | null>(null);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(0);
  useEffect(() => {
    let active = true;
    setReport(null);
    setFailed(false);
    api.getMonthlyCosts(month).then((data) => { if (active) setReport(data); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [month, reading]);
  const rows = report?.campaigns ?? [];
  // The column names what the page is about, as the campaign report's names
  // its campaign; the page's own head names the report. Both used to say
  // "Costs", one over the other.
  const column = <>
    <div className="ledger-ident">
      <div className="eyebrow">What the library has cost</div>
      <h2 className="ledger-ident-name">All campaigns</h2>
    </div>
    <ColumnSection label="Selected month">
      <p className="field-hint column-note">{month}</p>
      <p className="field-hint column-note">{report ? `${report.totals.calls} generation calls`
        : failed ? "Ledger unreadable" : "Reading the ledger…"}</p>
    </ColumnSection>
  </>;
  return <PageShell column={column} columnLabel="Cost report">
    <div className="page-wide view-anim">
      {/* The campaign report's head, so the two pages a scope select moves
          between read as one report at two scopes. */}
      <div className="shelf-head">
        <div>
          <div className="eyebrow">Every campaign, month by month</div>
          <h1 className="screen-title">Costs by campaign</h1>
          <div className="report-controls">
            <ReportScopeSelector report="costs" cid={null} suffix={`?month=${month}`} />
            <ReportMonth month={month} available={report?.available_months ?? []} onChange={choose} />
          </div>
        </div>
      </div>
      {/* Days in the reader's own calendar, as the campaign report writes the
          same window ("Ledger scanned from … to …"): one report, one date
          format. The month itself keeps its `YYYY-MM` -- it is the key the
          select and the address carry. */}
      <p className="field-hint">
        {month}: {bound(report?.since ?? `${month}-01`)} through {report ? bound(report.until) : "…"} (UTC)
      </p>
      {failed && <p className="empty-state">Costs could not be read. <button className="subtle" onClick={() => setReading((n) => n + 1)}>Try again</button></p>}
      {!failed && !report && <p className="column-empty">Reading the ledger…</p>}
      {report && <>
        <CostTrend rows={report.trend} />
        <div className="costs-headline">
          <MoneyColumns bucket={report.totals} />
          {/* A projection over calls; with none there is nothing to project,
              and "≈ $0.00" beside "No calls in this month" read as a figure. */}
          {report.totals.calls > 0 && <p className="field-hint">Estimated total {report.totals.unpriced_calls > 0
            && report.totals.estimated_total_usd === 0 ? "incomplete" : about(report.totals.estimated_total_usd)}
            {report.totals.unpriced_calls > 0 && ` · Incomplete: ${report.totals.unpriced_calls} unpriced calls`}
          </p>}
        </div>
        {rows.length === 0 && report.unassigned.calls === 0 &&
          <p className="empty-state">No calls in this month.</p>}
        {(rows.length > 0 || report.unassigned.calls > 0) &&
          <div className="ledger-table-wrap"><table className="ledger-table cost-table">
            <thead><tr><th>Campaign</th><th>Calls</th><th>Provider charges</th>
              <th>Subscription estimates</th><th>Modelled</th><th>Estimated total</th></tr></thead>
            <tbody>{[...rows, ...(report.unassigned.calls ? [{
              ...report.unassigned, campaign_id: "", campaign_name: "Outside a campaign",
            }] : [])].map((row) => <tr key={row.campaign_id || "unassigned"}>
              <td>{row.campaign_id
                ? <Link to={`/campaigns/${encodeURIComponent(row.campaign_id)}/costs?month=${month}`}>{row.campaign_name}</Link>
                : row.campaign_name}</td>
              <td>{row.calls}</td>
              <td>{row.priced_calls > row.subscription_calls ? money(row.cost_usd) : "—"}</td>
              <td>{row.subscription_calls > 0 ? about(row.estimated_usd) : "—"}</td>
              <td title={estimatedTokensTitle(row)}>
                {row.modelled_calls > 0 ? about(row.modelled_usd) : "—"}</td>
              {/* A projection, never spend: `≈`, so a zero-rated row reads
                  `≈ $0.00` rather than a bare `$0.00`. */}
              <td title={estimatedTokensTitle(row)}>{row.unpriced_calls > 0
                && row.estimated_total_usd === 0
                ? "Incomplete" : about(row.estimated_total_usd)}{row.unpriced_calls > 0 &&
                <span className="field-hint"> incomplete: {row.unpriced_calls} unpriced</span>}</td>
            </tr>)}</tbody>
          </table></div>}
      </>}
    </div>
  </PageShell>;
}
