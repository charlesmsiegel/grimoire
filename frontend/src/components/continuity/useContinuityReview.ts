/** The continuity review's reads, its Refresh, and the sweep it follows
 *  (capstone §12.1, §12.9, Decision 24).
 *
 *  Two reads, each with its own failure: the candidates read (the findings
 *  groups) and `GET /continuity` (Reviewed links / merges, Dismissed
 *  findings). A broken one costs its own groups and nothing else -- the same
 *  rule the Ledger's changes read lives by.
 *
 *  ONE EPOCH. The reads are keyed on the Ledger's own epoch, not one of their
 *  own: a closure applied here moves the Threads count, and a thread closed in
 *  the table drops its closure finding, so a write on either side re-reads
 *  both. `wrote()` is how a continuity write says so -- it hands the bump to
 *  the page (`onWrote`), which owns the epoch.
 *
 *  ONE SWEEP AT A TIME. Refresh, and following a sweep that was already
 *  running when the section loaded (End Scene's, typically), share one latch:
 *  the server keeps one live reconcile per campaign anyway, and two loops here
 *  waiting on it would re-read twice and could each start a follow-up.
 *
 *  A CAMPAIGN SWITCH ENDS IT. The route is not keyed on `cid`, so this hook
 *  outlives a switch. One `AbortController` per campaign, aborted when it
 *  changes; every run call carries its signal, and nothing -- no state, no
 *  follow-up Refresh -- happens after it aborts. A sweep for the campaign
 *  left behind keeps running on the server, and is simply nobody's to show.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, ApiError, type CandidateRecord, type ContinuityCandidates, type ContinuityState,
} from "../../api/client";
import type { RunHandle } from "../../api/stream";
import type { ContinuityGroup } from "../../api/types";
import { errorText } from "../../api/errors";

/** What a landed sweep with no model connection did (§12.9). */
export const NO_MODEL_NOTE =
  "No model connection — findings are listed without a suggested decision.";
/** What a failed sweep still did: persist 1 landed before the model call. */
export const FAILED_NOTE = "The model check did not finish — basic findings are listed.";

type Current = { fingerprint: string; records: CandidateRecord[] };

export type ContinuityReview = {
  /** null while reading, "failed" when the read did not answer. */
  candidates: ContinuityCandidates | null | "failed";
  state: ContinuityState | null | "failed";
  /** Per group; null where the read behind that group has not answered --
   *  still reading, or failed -- which the column draws as "—", never 0. */
  counts: Record<ContinuityGroup, number | null>;
  /** A sweep is in hand: a Refresh, or a followed run. Disables Refresh. */
  refreshing: boolean;
  /** The sweep in hand is one this client did not start. */
  following: boolean;
  /** What the last sweep did, when that is worth a line. */
  refreshNote: string | null;
  refresh(): Promise<void>;
  /** Re-read both, without starting anything (a 409, a not_found). */
  reread(): void;
  /** A continuity write landed: re-read the ledger and the review. */
  wrote(): void;
  /** Show a finding as a `409 stale_candidate` says it is now (§12.9). */
  applyCurrent(id: string, current: Current): void;
};

/** The sweep a run ended with, landed or failed. A failed reconcile copies its
 *  `sweep` into the run's error (`routes/continuity._reconcile_work`), because
 *  a failed run carries no result. */
function sweepOf(outcome: { result?: { sweep?: string }; error?: unknown }): string | undefined {
  if (outcome.result) return outcome.result.sweep;
  if (outcome.error instanceof ApiError) {
    const sweep = outcome.error.body?.sweep;
    return typeof sweep === "string" ? sweep : undefined;
  }
  return undefined;
}

export function useContinuityReview(cid: string, epoch: number,
                                    onWrote: () => void): ContinuityReview {
  // Held with the campaign they came from, so a switch shows "—" rather than
  // the last campaign's numbers under the new name.
  const [cands, setCands] =
    useState<{ cid: string; data: ContinuityCandidates | "failed" } | null>(null);
  const [cont, setCont] =
    useState<{ cid: string; data: ContinuityState | "failed" } | null>(null);
  const [nonce, setNonce] = useState(0);
  const [sweep, setSweep] = useState<"refresh" | "follow" | null>(null);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  /** The current records a 409 said a finding has, by candidate id, laid over
   *  the read until the next write or switch. */
  const [overrides, setOverrides] = useState<Record<string, Current>>({});

  const control = useRef<AbortController | null>(null);
  /** The sweep in hand, single-flight across Refresh and follow. */
  const latch = useRef<Promise<void> | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    control.current = ctl;
    latch.current = null;
    setSweep(null);
    setRefreshNote(null);
    setOverrides({});
    return () => { ctl.abort(); };
  }, [cid]);

  const reread = useCallback(() => setNonce((n) => n + 1), []);

  /** Wait on a sweep this client did not start, then re-read, whatever it
   *  ended as: a failed sweep still landed its deterministic findings. */
  const follow = useCallback((handle: RunHandle) => {
    const signal = control.current?.signal;
    if (!signal || signal.aborted || latch.current) return;
    setSweep("follow");
    latch.current = (async () => {
      try {
        await api.awaitCampaignRun(cid, handle, signal);
      } catch {
        // Ended badly or could not be followed: either way, read what is there.
      }
      if (signal.aborted) return;
      latch.current = null;
      setSweep(null);
      reread();
    })();
  }, [cid, reread]);

  useEffect(() => {
    let live = true;
    api.continuityCandidates(cid)
      .then((data) => {
        if (!live) return;
        setCands({ cid, data });
        if (data.run && data.run.state === "running") follow(data.run);
      })
      .catch(() => { if (live) setCands({ cid, data: "failed" }); });
    api.getContinuity(cid)
      .then((data) => { if (live) setCont({ cid, data }); })
      .catch(() => { if (live) setCont({ cid, data: "failed" }); });
    return () => { live = false; };
  }, [cid, epoch, nonce, follow]);

  const refresh = useCallback((): Promise<void> => {
    if (latch.current) return latch.current;
    const signal = control.current?.signal;
    if (!signal || signal.aborted) return Promise.resolve();
    setSweep("refresh");
    setRefreshNote(null);
    const run = (async () => {
      // At most two passes: a Refresh that adopted End Scene's incremental
      // sweep asks once more, which is then a full one (Review Focus 4). An
      // adopted sweep is the only way to get an incremental answer, so a
      // second one is a race with another End Scene -- and never a third.
      for (let pass = 0; pass < 2; pass++) {
        let outcome: { result?: { sweep?: string; llm?: string }; error?: unknown };
        try {
          outcome = { result: await api.reconcileContinuity(cid, signal) };
        } catch (error) {
          outcome = { error };
        }
        if (signal.aborted) return;
        setRefreshNote(outcome.result
          ? (outcome.result.llm === "off" ? NO_MODEL_NOTE : null)
          : `${FAILED_NOTE} ${errorText(outcome.error)}`);
        // Landed or failed alike: persist 1 lands before the model is asked.
        reread();
        if (sweepOf(outcome) !== "incremental") break;
      }
      latch.current = null;
      setSweep(null);
    })();
    latch.current = run;
    return run;
  }, [cid, reread]);

  const wrote = useCallback(() => {
    setOverrides({});
    onWrote();
  }, [onWrote]);

  const applyCurrent = useCallback((id: string, current: Current) => {
    setOverrides((o) => ({ ...o, [id]: current }));
  }, []);

  const rawCandidates = cands && cands.cid === cid ? cands.data : null;
  const state = cont && cont.cid === cid ? cont.data : null;

  const candidates = useMemo((): ContinuityCandidates | null | "failed" => {
    if (!rawCandidates || rawCandidates === "failed" || !Object.keys(overrides).length) {
      return rawCandidates;
    }
    // A 409 that carried the records as they are now: the detail re-renders
    // from them at once, bound to the new fingerprint, so the reader can look
    // and resubmit (§12.9). The read's own copy is still `stale` -- its
    // fingerprint is the cached one -- so the override wins until the next
    // write re-reads.
    return {
      ...rawCandidates,
      candidates: rawCandidates.candidates.map((c) => {
        const o = overrides[c.id];
        return o
          ? { ...c, fingerprint: o.fingerprint, records: o.records, stale: false,
              stale_reason: null }
          : c;
      }),
    };
  }, [rawCandidates, overrides]);

  const counts = useMemo((): Record<ContinuityGroup, number | null> => {
    const found = candidates && candidates !== "failed" ? candidates.candidates : null;
    const inGroup = (g: ContinuityGroup) =>
      found ? found.filter((c) => c.group === g).length : null;
    const st = state && state !== "failed" ? state : null;
    return {
      overlaps: inGroup("overlaps"),
      closures: inGroup("closures"),
      resolutions: inGroup("resolutions"),
      // Dangling aliases are in `aliases`; a broken link is not an effective
      // one, so it is only in `raw_links` (§12.6 lists both, marked Broken).
      reviewed: st
        ? st.aliases.length + st.links.length
          + st.raw_links.filter((l) => l.state === "broken").length
        : null,
      dismissed: st ? st.suppressions.filter((s) => s.live).length : null,
    };
  }, [candidates, state]);

  return {
    candidates, state, counts,
    refreshing: sweep !== null, following: sweep === "follow",
    refreshNote, refresh, reread, wrote, applyCurrent,
  };
}
