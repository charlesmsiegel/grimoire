/** "What the sweep asked" (roadmap 01b §3.8): the continuity reconcile
 *  sweep's decision captures, read-only.
 *
 *  The sweep runs on the campaign rather than a scene, so its captures are
 *  filed at campaign level (`GET /campaigns/{cid}/prompts`), and this pane is
 *  the one place the sweep is visible -- the list sits under Refresh, which is
 *  what starts one. Collapsed by default, and read only when opened: it is a
 *  question a reader comes to ask ("why did it suggest that?"), not a list to
 *  load on every visit to the ledger.
 *
 *  The list/detail rule, without its edit step: a row opens the entry
 *  read-only through `ContextBreakdown`, the same renderer the scene
 *  inspector uses for a frozen prompt -- the request as sent, and the
 *  decision's outcome as "outcome · not sent". A capture is never editable.
 *
 *  Every answer is held with the campaign it was read for, so one that lands
 *  after the reader moved to another campaign is never drawn under it.
 */
import { useState } from "react";
import { api, type PromptEntry, type PromptSnapshot } from "../../api/client";
import { ContextBreakdown } from "../ContextBreakdown";
import { taskLabel, whenLabel } from "../turnLabels";

type Listed = { cid: string; entries: PromptEntry[] | "failed" };
type Shown = { cid: string; eid: string; data: PromptSnapshot | "failed" | null };

export function SweepCaptures({ cid }: { cid: string }) {
  const [open, setOpen] = useState(false);
  const [listed, setListed] = useState<Listed | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (!next) return;
    // Re-read on every opening: a sweep may have landed since the last one.
    api.listCampaignPrompts(cid)
      .then((r) => setListed({ cid, entries: r.entries }))
      .catch(() => setListed({ cid, entries: "failed" }));
  }

  function show(eid: string) {
    setShown({ cid, eid, data: null });
    api.getCampaignPrompt(cid, eid)
      .then((data) => setShown((s) => (s && s.cid === cid && s.eid === eid
        ? { cid, eid, data } : s)))
      .catch(() => setShown((s) => (s && s.cid === cid && s.eid === eid
        ? { cid, eid, data: "failed" } : s)));
  }

  const entries = listed && listed.cid === cid ? listed.entries : null;
  const here = shown && shown.cid === cid ? shown : null;

  return (
    <div className="continuity-captures">
      <button type="button" className="subtle" aria-expanded={open} onClick={toggle}>
        What the sweep asked
      </button>
      {open && (
        entries === null ? <p className="field-hint">Reading…</p>
        : entries === "failed" ? <p className="field-hint">The sweep's captures could not be read.</p>
        : entries.length === 0 ? <p className="field-hint">No sweep has been captured yet.</p>
        : (
          <>
            {entries.map((e) => (
              <button key={e.id} type="button"
                      className={"inspector-row" + (here?.eid === e.id ? " on" : "")}
                      onClick={() => show(e.id)}>
                <span className="inspector-name">{taskLabel(e.task)}</span>
                <span className="ctx-meta">{whenLabel(e.ts)}</span>
              </button>
            ))}
            {here && (here.data === null ? <p className="field-hint">Reading…</p>
              : here.data === "failed"
                ? <p className="field-hint">That capture has aged out of the log.</p>
                : <ContextBreakdown ctx={here.data} models={[]} />)}
          </>
        )
      )}
    </div>
  );
}
