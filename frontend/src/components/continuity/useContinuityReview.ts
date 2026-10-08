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
  api, ApiError, RefreshRefused, type CandidateRecord, type ContinuityCandidates, type ContinuityState,
  type ReconcileRunError,
} from "../../api/client";
import type { RunHandle } from "../../api/stream";
import type { ContinuityGroup, ReconcileResult } from "../../api/types";
import { errorText } from "../../api/errors";
import { dismissedUnreadable, findingsUnreadable, reviewedUnreadable } from "./labels";

/** What a landed sweep with no model connection did (§12.9). */
export const NO_MODEL_NOTE =
  "No model connection — findings are listed without a suggested decision.";
/** A sweep whose model answered some of its findings (M6). It does not promise
 *  the others are asked again: a model-only nomination in a failed chunk is not
 *  stored (M7). */
export const PARTIAL_NOTE =
  "The model check answered only some of the findings; the others have no suggested decision yet.";
/** A sweep whose model answered none of them (N4): "only some" would read wrong. */
export const NONE_NOTE =
  "The model check gave no suggested decisions this time; the findings are listed without one.";

/** What a landed sweep says about its model check, or null when there is
 *  nothing to say. A failed run never reaches this: it throws, and
 *  `failedNote` words it. */
export function landedNote(result: Pick<
    ReconcileResult, "llm" | "reason" | "reason_kind" | "adjudicated" | "unanswered">
): string | null {
  if (result.llm === "off") {
    // A configured model that cannot serve continuity is never "no connection" (M8).
    return result.reason_kind === "incapable" && result.reason
      ? `${result.reason} Findings are listed without a suggested decision.`
      : NO_MODEL_NOTE;
  }
  if (result.llm === "ok" && result.unanswered > 0) {
    return result.adjudicated === 0 ? NONE_NOTE : PARTIAL_NOTE;
  }
  return null;
}

/** What a failed sweep still did: persist 1 landed before the model call. */
export const FAILED_NOTE = "The model check did not finish — basic findings are listed.";
/** A sweep that saved nothing: the start was refused, or persist 1 was. */
export const NOT_SAVED_NOTE = "The refresh did not finish — nothing it found was saved.";
/** A failure that does not say which of the two it was -- a run reaped or a
 *  server restarted before it was read back may well have landed, so this
 *  claims neither that it finished nor that it did not. */
export const UNFINISHED_NOTE = "The refresh's outcome could not be read back.";
/** The pass a run adds for refs End Scene left failed, after the first
 *  pass landed and saved: the first pass's findings stand. */
export const FOLLOW_ON_NOTE =
  "The follow-on pass did not finish — the first pass's findings are listed.";

/** The note for a failed Refresh (§26). A failed reconcile run's `error`
 *  carries `saved` -- whether persist 1 landed -- `follow_on` -- whether the
 *  failure was the follow-on pass's -- and `sweep`
 *  (`routes/continuity._reconcile_work`). Only a `RefreshRefused` -- the
 *  POST's own 4xx -- is known to have run nothing. Anything else -- a poll's
 *  `run_gone` 404 (a run reaped, a server restarted), a poll that gave up, a
 *  cancelled run, no answer at all -- may or may not have saved, and says
 *  neither. */
export function failedNote(err: unknown): string {
  if (err instanceof RefreshRefused) return NOT_SAVED_NOTE;
  const body = err instanceof ApiError
    ? err.body as Partial<ReconcileRunError> | undefined : undefined;
  // A first pass another generation superseded landed without saving, so
  // only a saved first pass makes the findings on screen this run's.
  if (body?.follow_on === true && body?.saved === true) return FOLLOW_ON_NOTE;
  if (body?.saved === true) return FAILED_NOTE;
  if (body?.saved === false) return NOT_SAVED_NOTE;
  return UNFINISHED_NOTE;
}

type Current = { fingerprint: string; records: CandidateRecord[] };

export type ContinuityReview = {
  /** null while reading, "failed" when the read did not answer. */
  candidates: ContinuityCandidates | null | "failed";
  state: ContinuityState | null | "failed";
  /** Per group; null where the read behind that group has not answered --
   *  still reading, or failed -- or answered without being able to read what
   *  the group lists (a malformed continuity.json or findings cache, §26),
   *  which the column draws as "—", never 0. */
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
  /** A `409 stale_candidate` said an evidence scene moved: the finding stays
   *  disabled, whatever a re-read says, until a Refresh or a write. */
  evidenceMoved(id: string): void;
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

/** `map` without `id`, or `map` itself when it has none (no re-render). */
function without<T>(map: Record<string, T>, id: string): Record<string, T> {
  if (!(id in map)) return map;
  const rest = { ...map };
  delete rest[id];
  return rest;
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
   *  the read until the next write (any epoch move) or switch. */
  const [overrides, setOverrides] = useState<Record<string, Current>>({});
  /** Findings whose cited evidence a 409 said has moved, by candidate id. */
  const [moved, setMoved] = useState<Record<string, true>>({});

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
    setMoved({});
    return () => { ctl.abort(); };
  }, [cid]);

  // What a 409 laid over the read holds until the page's next write, and a
  // Ledger hand edit is one: it moves the epoch without passing `wrote()`, and
  // the re-read it causes is what the finding must show -- stale again, say,
  // rather than the 409's records under a fingerprint already superseded.
  useEffect(() => {
    setOverrides({});
    setMoved({});
  }, [epoch]);

  const reread = useCallback(() => setNonce((n) => n + 1), []);

  /** Wait on a sweep this client did not start, then re-read, whatever it
   *  ended as: a sweep that failed after persist 1 still landed its
   *  deterministic findings. */
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
        let outcome: { result?: ReconcileResult; error?: unknown };
        try {
          outcome = { result: await api.reconcileContinuity(cid, signal) };
        } catch (error) {
          outcome = { error };
        }
        if (signal.aborted) return;
        setRefreshNote(outcome.result
          ? landedNote(outcome.result)
          : `${failedNote(outcome.error)} ${errorText(outcome.error)}`);
        // Landed or failed alike, re-read: a failure after persist 1 still
        // landed its findings, and one before it changed nothing. What a 409 laid over the last read is the sweep's to answer now: it
        // re-found each finding against the records as they are, and voided
        // any proposal whose evidence moved.
        setOverrides({});
        setMoved({});
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
    setMoved({});
    onWrote();
  }, [onWrote]);

  const applyCurrent = useCallback((id: string, current: Current) => {
    setOverrides((o) => ({ ...o, [id]: current }));
    setMoved((m) => without(m, id));
  }, []);

  const evidenceMoved = useCallback((id: string) => {
    setMoved((m) => ({ ...m, [id]: true }));
    setOverrides((o) => without(o, id));
  }, []);

  const rawCandidates = cands && cands.cid === cid ? cands.data : null;
  const state = cont && cont.cid === cid ? cont.data : null;

  const candidates = useMemo((): ContinuityCandidates | null | "failed" => {
    if (!rawCandidates || rawCandidates === "failed"
        || (!Object.keys(overrides).length && !Object.keys(moved).length)) {
      return rawCandidates;
    }
    // A 409 that carried the records as they are now: the detail re-renders
    // from them at once, bound to the new fingerprint, so the reader can look
    // and resubmit (§12.9). The read's own copy is still `stale` -- its
    // fingerprint is the cached one -- so the override wins until the next
    // write re-reads.
    return {
      ...rawCandidates,
      // An evidence 409 is the other way round: the fingerprint still holds,
      // but the proposal cites a scene that is gone, and only a Refresh
      // re-asks it -- so the finding stays disabled even if a re-read raced
      // the rename and still calls it fresh.
      candidates: rawCandidates.candidates.map((c) => {
        if (moved[c.id]) return { ...c, stale: true, stale_reason: "evidence" as const };
        const o = overrides[c.id];
        return o
          ? { ...c, fingerprint: o.fingerprint, records: o.records, stale: false,
              stale_reason: null }
          : c;
      }),
    };
  }, [rawCandidates, overrides, moved]);

  const counts = useMemo((): Record<ContinuityGroup, number | null> => {
    const read = candidates && candidates !== "failed" ? candidates : null;
    const found = read && !findingsUnreadable(read.diagnostics) ? read.candidates : null;
    const inGroup = (g: ContinuityGroup) =>
      found ? found.filter((c) => c.group === g).length : null;
    const st = state && state !== "failed" ? state : null;
    const reviewed = st && !reviewedUnreadable(st.malformed) ? st : null;
    const dismissed = st && !dismissedUnreadable(st.malformed) ? st : null;
    return {
      overlaps: inGroup("overlaps"),
      closures: inGroup("closures"),
      resolutions: inGroup("resolutions"),
      // Dangling aliases are in `aliases`; a broken link is not an effective
      // one, so it is only in `raw_links` (§12.6 lists both, marked Broken).
      reviewed: reviewed
        ? reviewed.aliases.length + reviewed.links.length
          + reviewed.raw_links.filter((l) => l.state === "broken").length
        : null,
      dismissed: dismissed ? dismissed.suppressions.filter((s) => s.live).length : null,
    };
  }, [candidates, state]);

  return {
    candidates, state, counts,
    refreshing: sweep !== null, following: sweep === "follow",
    refreshNote, refresh, reread, wrote, applyCurrent, evidenceMoved,
  };
}
