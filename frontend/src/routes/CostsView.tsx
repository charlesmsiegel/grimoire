import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CampaignSceneCosts, type SceneCostRow } from "../api/client";
import {
  Footnotes, MoneyColumns, about, bound, bucketPrice, headlineIsEstimate, money,
} from "../components/cost";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";

/** What a campaign has cost, scene by scene, over its whole life (#153).
 *
 *  The scene inspector's Cost section answers "what is this scene costing me"
 *  while you are in it. This answers the question you cannot ask from inside
 *  one scene: which scenes were expensive, and what has the campaign cost in
 *  total. Both read the same ledger; only the window differs — this one is not
 *  windowed at all, because "over all time" is the question.
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
  // Held WITH the campaign the rows came from, the way `LedgerView` holds its
  // ledger: this route is not keyed on `cid`, so a campaign switch keeps the
  // component mounted and a bare `CampaignSceneCosts | null` would show one
  // game's spend under the other's name until the new read settled.
  const [loaded, setLoaded] = useState<{ cid: string; data: CampaignSceneCosts } | null>(null);
  /** The read failed, so there is no report — as distinct from a report saying
   *  the campaign has spent nothing. Degrading to `EMPTY` printed a `$0.00`
   *  headline and "Nothing has been generated" over a campaign with real
   *  spend, which is this feature's cardinal error wearing a different hat: a
   *  figure nobody measured, rendered as zero. */
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);

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
    api.getCampaignSceneCosts(cid, sort)
      .then((d) => { if (live) setLoaded({ cid, data: d }); })
      .catch(() => { if (!live) return; setLoaded(null); setFailed(true); });
    return () => { live = false; };
  }, [cid, sort, reload]);

  const report = loaded && loaded.cid === cid ? loaded.data : null;
  const rows = report?.scenes ?? [];

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
      <ColumnSection label="All time">
        {/* The headline this page exists for. Rendered only once the read has
            landed: a `$0.00` under "All time" while a request is in flight is
            the one figure a cost page must not print casually. */}
        {failed && <p className="column-empty">Unread — no total to show.</p>}
        {!failed && totals === undefined
          && <p className="column-empty">Reading the ledger…</p>}
        {!failed && totals !== undefined && (
          <>
            {/* The three columns ARE the headline now. One combined figure
                beside them would be a fourth number for the reader to
                reconcile against three that are already exact. */}
            <MoneyColumns bucket={totals} />
            <div className="ctx-tokens">
              {totals.calls.toLocaleString()}{" "}
              {totals.calls === 1 ? "generation" : "generations"}
              {" · "}{totals.total_tokens.toLocaleString()} tok
            </div>
            <Footnotes bucket={totals} />
          </>
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
                all-time rescan can be slow, and until it lands the rows are
                still in the previous order — a heading following `sort` would
                describe them wrongly for the length of the request. */}
            <div className="eyebrow">
              {SORTS.find((s) => s.key === (report?.order ?? sort))?.hint}
              {report && report.order !== sort && " · REORDERING…"}
            </div>
            <h1 className="screen-title">Costs by scene</h1>
          </div>
        </div>

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
            <span className="empty-what">Nothing has been generated in this campaign yet.</span>{" "}
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
                      <div>{bucketPrice(row)}</div>
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
                    <td className="cost-cell">{row.total_tokens.toLocaleString()}</td>
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
              ?? "spend"}. The all-time total beside them covers every one.
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
            No provider reported a charge for this campaign — everything here was
            billed to a subscription, estimated from your own rates, or came back
            with no price at all. None of it is money anybody was invoiced for.
          </p>
        )}
      </div>
    </PageShell>
  );
}

export default CostsView;
