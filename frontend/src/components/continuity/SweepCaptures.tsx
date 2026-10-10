/** "What the sweep asked" (roadmap 01b): the continuity sweep's captured
 *  decisions, under Refresh.
 *
 *  A sweep belongs to no scene, so its captures are the campaign-level
 *  prompt-log entries (`GET /campaigns/{cid}/prompts`), and this is the one
 *  surface where the sweep is visible. Collapsed by default, and read only
 *  once opened: it is what a reader comes looking for when a proposal looks
 *  wrong, not something the review should lead with.
 *
 *  The list/detail rule, with no edit step: a row opens the capture read-only
 *  through `ContextBreakdown` -- the prompt each call was sent, then the
 *  outcome, "not sent" -- and a capture is never editable.
 *
 *  Everything held is keyed by the campaign it was read for, so a campaign
 *  switch never shows one game's sweep under the other's name, and an answer
 *  arriving after the switch is dropped.
 */
import { useEffect, useRef, useState } from "react";
import { api, type PromptEntry, type PromptSnapshot } from "../../api/client";
import { errorText } from "../../api/errors";
import { ContextBreakdown } from "../ContextBreakdown";
import { taskLabel, whenLabel } from "../turnLabels";

type Held<T> = { cid: string; data: T } | null;

export function SweepCaptures({ cid, refreshing }: { cid: string; refreshing: boolean }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Held<PromptEntry[]>>(null);
  const [shown, setShown] = useState<Held<PromptSnapshot>>(null);
  const [error, setError] = useState<Held<string>>(null);
  const live = useRef(cid);
  live.current = cid;

  // Read when opened, and again once a sweep the reader started has finished
  // -- a pass files its capture as it lands.
  useEffect(() => {
    if (!open || refreshing) return;
    const asked = cid;
    api.listCampaignPrompts(asked)
      .then(({ entries }) => { if (live.current === asked) setRows({ cid: asked, data: entries }); })
      .catch((err: unknown) => {
        if (live.current === asked) setError({ cid: asked, data: errorText(err) });
      });
  }, [cid, open, refreshing]);

  async function show(eid: string) {
    const asked = cid;
    setError(null);
    try {
      const snapshot = await api.getCampaignPrompt(asked, eid);
      if (live.current === asked) setShown({ cid: asked, data: snapshot });
    } catch (err: unknown) {
      if (live.current !== asked) return;
      setShown(null);
      setError({ cid: asked, data: (err as { status?: number })?.status === 404
        ? "That sweep's capture has aged out of the log." : errorText(err) });
    }
  }

  const list = rows?.cid === cid ? rows.data : null;
  const entry = shown?.cid === cid ? shown.data : null;
  return (
    <details className="continuity-captures"
             onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>What the sweep asked</summary>
      {error?.cid === cid && <p className="continuity-error" role="alert">{error.data}</p>}
      {entry ? (
        <div className="continuity-capture">
          <button type="button" className="subtle" onClick={() => setShown(null)}>
            ‹ All sweeps
          </button>
          <div className="field-hint">
            {taskLabel(entry.task)} · {whenLabel(entry.ts)}
          </div>
          <ContextBreakdown ctx={entry} models={[]} />
        </div>
      ) : list === null ? (
        open && <p className="field-hint">Reading…</p>
      ) : list.length === 0 ? (
        <p className="field-hint">
          No sweep has been captured yet. A sweep that asks the model files what it asked here.
        </p>
      ) : (
        list.map((r) => (
          <button key={r.id} type="button" className="inspector-row"
                  onClick={() => { void show(r.id); }}>
            <span className="inspector-name">{taskLabel(r.task)}</span>
            <span className="ctx-meta">{whenLabel(r.ts)}</span>
          </button>
        ))
      )}
    </details>
  );
}
