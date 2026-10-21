import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { isAbortError, type TaglineBatchEvent } from "../../api/stream";
import type { VisitFence } from "./useCharacterRoster";

/** What a tagline run did, counted from its frames. */
type TaglineRun = {
  written: number;
  reasons: Record<string, number>;
  failed: { detail: string; kind: string } | null;
  started: boolean;
  ended: boolean;
};

/** The one-line report a finished (or stopped) run leaves in the toolbar. */
function taglineReport(run: TaglineRun): string {
  const parts = [`Derived ${run.written} tagline${run.written === 1 ? "" : "s"}`];
  for (const [reason, n] of Object.entries(run.reasons)) parts.push(`${n} ${reason}`);
  if (run.failed) parts.push(`stopped — ${run.failed.detail}`);
  if (run.failed || !run.ended) parts.push("run it again to pick up the rest");
  return parts.join(" · ");
}

/** Deriving a tagline for every character in a world that has none (#57):
 *  the run's progress, its Stop, and the report it leaves behind. */
export function useTaglineBatch(
  { wid, untaggedCount, reload, setError, fence: { visit, liveVisit } }: {
    wid: string;
    untaggedCount: number;
    reload: () => Promise<void>;
    setError: (err: unknown) => void;
    fence: VisitFence;
  },
) {
  const [taglineBatch, setTaglineBatch] =
    useState<{ done: number; total: number; name: string } | null>(null);
  const [taglineBatchMsg, setTaglineBatchMsg] = useState<string | null>(null);
  const taglineAbort = useRef<AbortController | null>(null);

  // A report is a claim about one library ("Derived 12 taglines"); left
  // standing it becomes a claim about whichever library is showing now.
  useEffect(() => { setTaglineBatchMsg(null); }, [visit]);

  // A derive dies with the view that started it — nothing here is a detached
  // run, so leaving takes the progress line and the Stop button with it, and a
  // stream nobody can see or stop, still spending a provider call per
  // character, is the one outcome worse than having to click Derive again.
  useEffect(() => () => taglineAbort.current?.abort(), []);

  /** Derive a tagline for every character in the world that has none (#57).
   *
   *  The route writes each sentence as it lands, so there is nothing to save
   *  afterwards and nothing to lose if the run dies part-way. A provider
   *  failure arrives as a FRAME, not a rejection: the response is a 200 the
   *  moment the first character is attempted, so the run reports what it
   *  managed and names what stopped it. */
  async function deriveTaglines() {
    setError(null);
    setTaglineBatchMsg(null);
    setTaglineBatch({ done: 0, total: untaggedCount, name: "" });
    const ctl = new AbortController();
    taglineAbort.current = ctl;
    const at = visit;
    const here = () => liveVisit.current === at;
    // Counted from the frames rather than the summary alone: a run the user
    // stops never sends one, and "you spent forty calls, here is nothing" is
    // not an acceptable answer to Stop.
    const run: TaglineRun = { written: 0, reasons: {}, failed: null, started: false, ended: false };
    try {
      await api.generateWorldTaglines(wid, (e: TaglineBatchEvent) => {
        run.started = true;
        if (e.total !== undefined) setTaglineBatch({ done: 0, total: e.total, name: "" });
        if (e.done !== undefined) {
          const done = e.done, name = e.name ?? "";
          setTaglineBatch((p) => ({ done, total: p?.total ?? done, name }));
        }
        if (e.tagline) run.written += 1;
        if (e.skipped) run.reasons[e.skipped] = (run.reasons[e.skipped] ?? 0) + 1;
        if (e.error) run.failed = e.error;
        if (e.summary) run.ended = true;
      }, ctl.signal);
    } catch (err: unknown) {
      // An abort is the user's own Stop, not a failure — everything already
      // written is still written, and the report below says how much.
      if (!isAbortError(err)) {
        setError(err);
        if (!run.started) return;
      }
    } finally {
      taglineAbort.current = null;
      setTaglineBatch(null);
      // Swallowed rather than surfaced: the taglines are already written, and
      // letting a failed roster GET throw out of `finally` would take the
      // report with it.
      if (here()) await reload().catch(() => {});
    }
    if (!here()) return;
    setTaglineBatchMsg(taglineReport(run));
  }

  return {
    taglineBatch, taglineBatchMsg, deriveTaglines,
    stopTaglines: () => taglineAbort.current?.abort(),
  };
}

export type TaglineBatch = ReturnType<typeof useTaglineBatch>;
