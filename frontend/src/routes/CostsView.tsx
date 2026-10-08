import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CampaignSceneCosts, type SceneCostRow } from "../api/client";
import type { MonthlyCosts } from "../api/types";
import {
  Footnotes, MoneyColumns, UNPRICED, about, bound, bucketPrice, headlineIsEstimate, money,
  tokenTotal,
} from "../components/cost";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";
import ReportScopeSelector from "../components/ReportScopeSelector";
import ReportMonth, { useReportMonth } from "../components/ReportMonth";
import CostTrend from "../components/CostTrend";
import { modelRatesPath } from "../providerPaths";

/** What a campaign has cost, scene by scene, in a selected UTC month (#153).
 *
 *  The scene inspector's Cost section answers "what is this scene costing me"
 *  while you are in it. This answers the question you cannot ask from inside
 *  one scene: which scenes were expensive, and what has the campaign cost in
 *  total. Both read the same ledger; the selected month is part of the URL.
 *
 *  A page rather than another section of the inspector, and that is the one
 *  design call here worth stating. The inspector is read mid-turn and its
 *  sections have to be glanceable; a table of every scene a campaign has ever
 *  had is not, and putting it in the rail would have made the rail the wrong
 *  shape for both.
 *
 *  **Nothing on this page renders an unreported price as zero.** See
 *  `components/cost.tsx`, which is where that rule lives for all three cost
 *  surfaces.
 */

type Sort = "cost" | "recent" | "turns";

const SORTS: { key: Sort; label: string; hint: string }[] = [
  { key: "cost", label: "Most spent", hint: "WHERE THE MONEY WENT" },
  { key: "recent", label: "Most recent", hint: "NEWEST ACTIVITY FIRST" },
  { key: "turns", label: "Most turns", hint: "BY GENERATION COUNT" },
];

/** A scene's name for the table. The id when the title is empty, and the id
 *  alone when the scene is gone — a deleted scene's spend is still in the
 *  total, so it keeps a row, and the row has to say what it is rather than
 *  showing a blank cell. */
function sceneName(row: SceneCostRow): string {
  if (!row.scene) return "Outside any scene";
  return row.title || row.scene;
}

/** The stamp is UTC (`…Z`, written by the store); a date column is read against
 *  the reader's own calendar, so an instant is localized. */
function day(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts.slice(0, 10) : d.toLocaleDateString();
}

export function CostsView() {
  const { cid = "" } = useParams();
  const [month, chooseMonth] = useReportMonth();
  // Held WITH its cid, like the report below: this route is not keyed on `cid`,
  // so a campaign switch keeps the component mounted, and a name read whose
  // response settles after the new one's would label this campaign's spend --
  // and the shell's status context -- with the other's, permanently.
  const [named, setNamed] = useState<{ cid: string; name: string } | null>(null);
  // Sent to the SERVER rather than applied here. The list is capped, and the
  // cap is applied after the order: re-sorting the response on the client would
  // make every ordering but the default mean "…of the most expensive N", so a
  // campaign past the cap would be missing a recent cheap scene from a list
  // headed "most recent".
  const [sort, setSort] = useState<Sort>("cost");
  // Held WITH the campaign and month the rows came from, the way `LedgerView` holds its
  // ledger: this route is not keyed on `cid`, so a campaign switch keeps the
  // component mounted and a bare `CampaignSceneCosts | null` would show one
  // game's spend under the other's name until the new read settled.
  const [loaded, setLoaded] = useState<{ cid: string; month: string; data: CampaignSceneCosts } | null>(null);
  /** The read failed, so there is no report — as distinct from a report saying
   *  the campaign has spent nothing. Degrading to `EMPTY` printed a `$0.00`
   *  headline and "Nothing has been generated" over a campaign with real
   *  spend, which is this feature's cardinal error wearing a different hat: a
   *  figure nobody measured, rendered as zero. */
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [trend, setTrend] = useState<MonthlyCosts["trend"] | null>(null);
  const [trendFailed, setTrendFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setTrend(null);
    setTrendFailed(false);
    api.getMonthlyCosts(month, cid)
      .then((result) => { if (live) setTrend(result.trend); })
      .catch(() => { if (live) setTrendFailed(true); });
    return () => { live = false; };
  }, [cid, month, reload]);

  const name = named && named.cid === cid ? named.name : "";
  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  useEffect(() => {
    let live = true;
    api.getCampaign(cid)
      .then((c) => { if (live) setNamed({ cid, name: c.meta.name }); })
      .catch(() => { if (live) setNamed({ cid, name: cid }); });
    return () => { live = false; };
  }, [cid]);

  useEffect(() => {
    let live = true;
    setFailed(false);
    api.getCampaignSceneCosts(cid, sort, month)
      .then((d) => { if (live) setLoaded({ cid, month, data: d }); })
      .catch(() => { if (!live) return; setLoaded(null); setFailed(true); });
    return () => { live = false; };
  }, [cid, month, sort, reload]);

  const report = loaded && loaded.cid === cid && loaded.month === month ? loaded.data : null;
  const rows = report?.scenes ?? [];

  /** The providers that exist now, id → name, or null until read. Only asked
   *  for when an unpriced model names one: a line whose provider is gone (a
   *  delete re-prices its history, so exactly those rows land here) has no
   *  rates page to open, and points at the pricing table instead, the way
   *  Housekeeping's item does (`routes/todo.py`). A failed read is read as
   *  none, which costs the link and never sends anyone to a dead page. */
  const [providers, setProviders] = useState<Map<string, string> | null>(null);
  const namesProvider = !!report?.unpriced_models?.some((m) => m.provider_id);
  useEffect(() => {
    if (!namesProvider) return;
    let live = true;
    api.listConnections()
      .then((list) => { if (live) setProviders(new Map(list.map((p) => [p.id, p.name]))); })
      .catch(() => { if (live) setProviders(new Map()); });
    return () => { live = false; };
  }, [namesProvider]);

  const paletteSource = useCallback((): PaletteItem[] =>
    SORTS.map((s) => ({
      id: `costs:${s.key}`, group: "IN THIS CAMPAIGN", label: `Costs · ${s.label}`,
      meta: "costs", run: () => setSort(s.key),
    })), []);
  usePaletteSource(paletteSource);

  const totals = report?.totals;
  const column = (
    <>
      <Link className="column-back" to={`/campaigns/${cid}`}>‹ {name || "The campaign"}</Link>
      <div className="ledger-ident">
        <div className="eyebrow">What this campaign has cost</div>
        <h2 className="ledger-ident-name">{name || cid}</h2>
      </div>
      {/* The headline is NOT here any more. This is the one page whose whole
          subject is the money, and the three columns were being read at 274px
          in the slot that is meant to answer "what am I navigating" — so they
          were both cramped and in the wrong place. They are across the top of
          the body now (`<Money/>` below), and what is left in the column is
          the campaign, the order, and a summary line that says whether the
          headline can be trusted at all. */}
      <ColumnSection label={month}>
        {failed && <p className="column-empty">Unread — no total to show.</p>}
        {!failed && totals === undefined
          && <p className="column-empty">Reading the ledger…</p>}
        {!failed && totals !== undefined && (
          <div className="ctx-tokens">
            {totals.calls.toLocaleString()}{" "}
            {totals.calls === 1 ? "generation" : "generations"}
            {" · "}{tokenTotal(totals)}
          </div>
        )}
      </ColumnSection>
      <ColumnSection label="Order">
        {SORTS.map((s) => (
          <button key={s.key}
                  className={"column-row" + (sort === s.key ? " active" : "")}
                  onClick={() => setSort(s.key)}>
            <span className="column-row-label">{s.label}</span>
          </button>
        ))}
      </ColumnSection>
    </>
  );

  const footer = (
    <div className="field-hint">
      {report && report.since
        ? <>Ledger scanned from {bound(report.since)} to {bound(report.until)}.</>
        : <>Costs come from what each provider reported, per call.</>}
    </div>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Cost report">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            {/* Keyed to what came BACK, not to what was just clicked. An
                monthly rescan can be slow, and until it lands the rows are
                still in the previous order — a heading following `sort` would
                describe them wrongly for the length of the request. */}
            <div className="eyebrow">
              {SORTS.find((s) => s.key === (report?.order ?? sort))?.hint}
              {report && report.order !== sort && " · REORDERING…"}
            </div>
            <h1 className="screen-title">Costs by scene</h1>
            <ReportScopeSelector report="costs" cid={cid} suffix={`?month=${month}`} />
            <ReportMonth month={month} available={report?.available_months ?? [month]}
              onChange={chooseMonth} />
          </div>
        </div>

        {trend && <CostTrend rows={trend} />}
        {trendFailed && <p className="field-hint">Monthly trend could not be read.</p>}

        {/* Across the body, above the table it is the total of. Three separate
            claims about money and no total, exactly as everywhere else — this
            is the same `MoneyColumns` the hub's card and the shell payload
            draw, which is what stops this page and the rail disagreeing about
            what a call cost. */}
        {!failed && totals !== undefined && (
          <div className="costs-headline">
            <MoneyColumns bucket={totals} />
            <Footnotes bucket={totals} />
            {/* The count says how many calls nobody priced; this says WHY, and
                the why is almost always a model string that does not match.
                Without it the only way to find a typo'd key is to compare a
                rollup against a table by eye. */}
            {!!report?.unpriced_models?.length && (
              <div className="unpriced-models">
                <div className="money-label">No rate matches these</div>
                <ul>
                  {/* One entry per call shape -- provider, the model asked
                      for, the model that answered -- so a model two providers
                      serve, or one snapshot asked for under two names, is two
                      lines, keyed and labelled by all three. */}
                  {report.unpriced_models.map((m) => {
                    const asked = m.facts_model || m.model;
                    const name = m.provider_id ? providers?.get(m.provider_id) : undefined;
                    return (
                      <li key={`${m.provider_id}:${m.model}:${asked}`}>
                        <code>{m.model}</code>
                        {asked !== m.model && <> asked for as <code>{asked}</code></>}
                        <span className="field-hint">
                          {" "}on {name ?? (m.provider_id
                            ? <code>{m.provider_id}</code>
                            : "no recorded provider")}
                          {" "}— {m.calls} call{m.calls === 1 ? "" : "s"} that could be priced
                        </span>
                        {/* The model's own rates are stated under the model
                            that was asked for, on a provider that still
                            exists. A deleted provider, or a row filed before
                            providers were named, has only the table -- and so
                            does every line while those rates cannot be
                            written (`rates_editable`: the store is not yet at
                            the current model-settings format). */}
                        {" "}
                        {name !== undefined && report.rates_editable === true
                          ? <Link to={modelRatesPath(m.provider_id, asked)}>Set its rates</Link>
                          : (providers !== null || !m.provider_id
                             || report.rates_editable !== true)
                            && <Link to="/config?section=pricing">Add a pricing entry</Link>}
                      </li>
                    );
                  })}
                </ul>
                <p className="field-hint">
                  A model's own rates on its provider page price it on that
                  provider{report.rates_editable === true ? ""
                    : report.rates_newer === true
                      ? ", but a newer version of grimoire wrote this library's model"
                        + " settings, so this version cannot set them"
                      : ", and can be set after the upgrade to the new model settings"}.
                  {" "}Otherwise a pricing entry is matched on the model
                  string exactly: add one under{" "}
                  <Link to="/config">Settings → Pricing</Link>, or a wildcard
                  like <code>vendor/*</code>.
                </p>
              </div>
            )}
          </div>
        )}

        {failed && (
          <p className="empty-state">
            <span className="empty-what">
              Could not read this campaign's costs. Nothing is shown rather than
              a total, because an unread ledger is not a ledger saying zero.
            </span>{" "}
            <button className="subtle" onClick={() => setReload((n) => n + 1)}>
              Try again
            </button>
          </p>
        )}

        {!failed && report === null && <p className="column-empty">Reading the ledger…</p>}

        {!failed && report !== null && rows.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">Nothing has been generated in this campaign this month.</span>{" "}
            <Link to={`/campaigns/${cid}`}>Back to play →</Link>
          </p>
        )}

        {rows.length > 0 && (
          <div className="ledger-table-wrap">
            <table className="ledger-table cost-table">
              <thead>
                <tr>
                  <th scope="col">SCENE</th>
                  <th scope="col">COST</th>
                  <th scope="col">TURNS</th>
                  <th scope="col">TOKENS</th>
                  <th scope="col">LAST USED</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.scene || "(none)"}>
                    <td>
                      {/* A live scene links into play; a deleted one and the
                          no-scene bucket cannot, and are plain text rather than
                          a link that would 404. */}
                      <div className="ledger-what">
                        {row.scene && !row.missing ? (
                          <Link to={`/campaigns/${cid}/scenes/${row.scene}`}>
                            {sceneName(row)}
                          </Link>
                        ) : sceneName(row)}
                      </div>
                      <div className="ledger-note">
                        {row.missing
                          ? <>{row.scene} · deleted, and its spend still counted</>
                          : row.scene || "cast suggestions, intent, and other campaign-level calls"}
                      </div>
                    </td>
                    <td className="cost-cell">
                      <div className={bucketPrice(row) === UNPRICED ? "money-unpriced" : undefined}>
                        {bucketPrice(row)}
                      </div>
                      {/* The parentheticals, per row: what was NOT billed per
                          token, priced at what it would have been — one line
                          each, never totalled. The two rest on different
                          evidence (the provider's own arithmetic vs the user's
                          table), so a merged figure reconciles to neither
                          column. `bucketPrice` refuses the same merge above. */}
                      {!headlineIsEstimate(row) && row.estimated_usd > 0 && (
                        <div className="field-hint">
                          + {about(row.estimated_usd)} subscription
                        </div>
                      )}
                      {!headlineIsEstimate(row) && row.modelled_usd > 0 && (
                        <div className="field-hint">
                          + {about(row.modelled_usd)} estimated
                        </div>
                      )}
                      {row.unpriced_calls > 0 && (
                        <div className="field-hint">{row.unpriced_calls} unpriced</div>
                      )}
                    </td>
                    <td className="cost-cell">{row.calls.toLocaleString()}</td>
                    <td className="cost-cell">{tokenTotal(row)}</td>
                    <td className="ledger-asof">{day(row.last_ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {report?.truncated && (
          <p className="ledger-lead">
            Showing the {report.listed} scenes that came first by{" "}
            {SORTS.find((s) => s.key === report.order)?.label.toLowerCase()
              ?? "spend"}. The monthly total beside them covers every one.
          </p>
        )}

        {/* Gated on a call having actually been BILLED, not merely made. With
            only subscription, modelled or unpriced calls under it `cost_usd` is
            0.0, and this sentence would say providers reported a charge of
            $0.00 — asserting a reported zero where nobody reported anything.
            That is the claim this whole feature exists to avoid, made in prose
            rather than in a figure. */}
        {!failed && totals !== undefined
          && totals.priced_calls > totals.subscription_calls && (
          <p className="ledger-lead">
            {money(totals.cost_usd)} is what providers said they charged. Anything
            billed against a subscription, and anything a provider priced at
            nothing, is counted separately — an estimate is never added to a bill.
          </p>
        )}
        {!failed && totals !== undefined && totals.calls > 0
          && totals.priced_calls <= totals.subscription_calls && (
          <p className="ledger-lead">
            No provider reported a charge for this campaign this month — everything here was
            billed to a subscription, estimated from your own rates, or came back
            with no price at all. None of it is money anybody was invoiced for.
          </p>
        )}
      </div>
    </PageShell>
  );
}

export default CostsView;
