import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type WorldCampaignPending } from "../api/client";

/** The three counts in reading order: what a campaign has not seen, what it can
 *  take for free, and what it cannot. */
const COUNTS: { key: keyof WorldCampaignPending["pending"]; label: string }[] = [
  { key: "new", label: "new" },
  { key: "update", label: "update" },
  { key: "conflict", label: "conflict" },
];

function Row({ row }: { row: WorldCampaignPending }) {
  const conflicts = row.pending.conflict > 0;
  const total = COUNTS.reduce((n, c) => n + row.pending[c.key], 0);
  return (
    <Link className={"push-row" + (conflicts ? " has-conflict" : "")}
          to={`/campaigns/${row.id}`}>
      <span className="push-name">{row.name}</span>
      <span className="push-counts">
        {COUNTS.map((c) => (
          // Only the count that needs a decision is painted, the way the absorb
          // band chip is: three accented badges say nothing about which one to
          // read, and a conflict is the only one of the three a reader cannot
          // resolve by accepting.
          <span key={c.key}
                className={"chip" + (row.pending[c.key] === 0 ? " push-zero" : "")
                           + (c.key === "conflict" && conflicts ? " push-conflict" : "")}>
            {row.pending[c.key]} {c.label}
          </span>
        ))}
      </span>
      <span className="field-hint">
        {total === 0 ? "up to date" : "review in the campaign →"}
      </span>
    </Link>
  );
}

/** The world side of push/sync (#8): every campaign descended from this world
 *  and how much of it each one has not taken yet.
 *
 *  Read-only on purpose. Accepting is a campaign's decision and belongs to the
 *  campaign's own copy, so each row is a link to where `IncomingReview` lives
 *  rather than a button that reaches across into it. */
export function WorldPushPanel({ wid }: { wid: string }) {
  const [rows, setRows] = useState<WorldCampaignPending[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setErr(null);
    setRows(null);
    api.worldCampaigns(wid)
      .then((r) => { if (live) setRows(r); })
      // Reported rather than discarded: "no campaigns" and "the read failed"
      // are different answers, and only one of them is a reason to stop
      // looking for pending changes.
      .catch((e: unknown) => {
        if (!live) return;
        setErr(e instanceof Error ? e.message : String(e));
        setRows([]);
      });
    return () => { live = false; };
  }, [wid]);

  return (
    <div className="push-panel">
      {err && <p className="banner error-banner">{err}</p>}
      {rows === null && <p className="field-hint">Counting pending changes…</p>}
      {rows !== null && rows.length === 0 && !err && (
        <p className="field-hint">No campaigns are played in this world yet.</p>
      )}
      {rows !== null && rows.length > 0 && (
        <>
          <p className="field-hint">
            Edits to this world reach a campaign only when that campaign accepts
            them. These are waiting.
          </p>
          <div className="push-rows">
            {rows.map((row) => <Row key={row.id} row={row} />)}
          </div>
        </>
      )}
    </div>
  );
}
