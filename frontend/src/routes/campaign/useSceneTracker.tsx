import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type TrackerSummary
} from "../../api/client";
import { shareSummary } from "../../components/tracker/share";
import { lastOkKey, pendingAfter, type NowTracker } from "../../components/tracker/TrackerNow";

export function useSceneTracker({ loaded, ctxKey, selectedActor, activeId, cid }: {
  loaded: { cid: string; sid: string; token: number; } | null;
  ctxKey: number;
  selectedActor: { kind: string; id: string; } | null;
  activeId: string | null;
  cid: string;
}) {

  // The scene state tracker (routes/tracker.py), read for `loaded` like the
  // costs above and for the same reason: it describes the posts on screen, so
  // it is held WITH the scene it was read for and only shown where that is
  // still the transcript on screen. Re-read on every `ctxKey` beat, which is
  // what a landed turn bumps; `trackerTick` is the disclosures asking for one
  // (a retry, a re-run, an edit). Swallowed on failure: a tracker that cannot
  // be read is not a reason to put an error over a transcript.
  const [trackerRead, setTrackerRead] =
    useState<{ cid: string; sid: string; summary: TrackerSummary } | null>(null);
  const [trackerTick, setTrackerTick] = useState(0);
  const trackerCid = loaded?.cid ?? null;
  const trackerSid = loaded?.sid ?? null;
  // Reads can land out of order -- the main read and the poll are separate
  // requests -- and an older answer that lacks a post the newer one has would
  // overwrite it, read as "never tracked", and (nothing pending) end the poll
  // for good. So each request is stamped when it is ISSUED, and an answer older
  // than the newest one already landed is dropped, as is one for a scene that
  // is no longer the loaded one.
  const trackerSeq = useRef({ issued: 0, landed: 0 });
  const trackerScene = useRef<{ cid: string | null; sid: string | null }>({ cid: null, sid: null });
  trackerScene.current = { cid: trackerCid, sid: trackerSid };
  const readTracker = useCallback((tcid: string, tsid: string) => {
    const seq = ++trackerSeq.current.issued;
    return api.getTracker(tcid, tsid).then((summary) => {
      if (seq < trackerSeq.current.landed) return;
      if (trackerScene.current.cid !== tcid || trackerScene.current.sid !== tsid) return;
      trackerSeq.current.landed = seq;
      setTrackerRead((prev) => {
        const before = prev && prev.cid === tcid && prev.sid === tsid ? prev.summary : null;
        const shared = shareSummary(before, summary);
        // Nothing moved: keep the very state object, so no render follows.
        if (prev && before === shared) return prev;
        return { cid: tcid, sid: tsid, summary: shared };
      });
    });
  }, []);
  useEffect(() => {
    if (!trackerCid || !trackerSid) return;
    readTracker(trackerCid, trackerSid).catch(() => { /* see above */ });
  }, [trackerCid, trackerSid, ctxKey, trackerTick, readTracker]);
  const tracker = trackerRead && trackerRead.cid === trackerCid && trackerRead.sid === trackerSid
    ? trackerRead.summary : null;
  const trackerKeys = useMemo(
    () => Object.fromEntries((tracker?.keys ?? []).map((k) => [k.index, k.key])),
    [tracker?.keys]);
  // How many tracked posts "Re-run tracker from here" covers at each key --
  // that one and every one after it -- so the confirm can name the cost.
  const trackerRerun = useMemo(() => {
    const keys = tracker?.keys ?? [];
    return Object.fromEntries(keys.map((k, i) => [k.key, keys.length - i]));
  }, [tracker?.keys]);
  // What the dossier's "Now" section reads: the latest post whose tracking
  // landed, for the open actor. Memoized -- a literal here would hand the
  // memoized dossier a new object on every keystroke and streamed delta.
  const nowKey = tracker ? lastOkKey(tracker.keys, tracker.entries) : null;
  const nowEntry = nowKey && tracker ? tracker.entries[nowKey] : undefined;
  const nowEnabled = tracker?.enabled ?? false;
  const nowUpdating = tracker ? pendingAfter(tracker.keys, tracker.entries, nowKey) : false;
  const dossierTracker = useMemo<NowTracker | null>(
    () => selectedActor && activeId
      ? {
        cid, sid: activeId, key: nowKey, ref: `${selectedActor.kind}:${selectedActor.id}`,
        enabled: nowEnabled, entry: nowEntry, updating: nowUpdating
      }
      : null,
    [cid, activeId, selectedActor, nowKey, nowEnabled, nowEntry, nowUpdating]);
  // While a post is being tracked, ask again every two seconds: one timeout
  // chain, re-armed only once the last read has settled so a slow server is
  // never asked twice at once, and cleared with the scene or the view. A read
  // that fails doubles the wait (to 30 s) rather than hammering a server that
  // is struggling; the next success goes back to two seconds.
  const trackerPending = Object.values(tracker?.entries ?? {}).some((e) => e.status === "pending");
  useEffect(() => {
    if (!trackerPending || !trackerCid || !trackerSid) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = (delay: number) => {
      timer = setTimeout(() => {
        void readTracker(trackerCid, trackerSid)
          .then(() => 2000, () => Math.min(delay * 2, 30000))
          .then((next) => { if (live) arm(next); });
      }, delay);
    };
    arm(2000);
    return () => { live = false; clearTimeout(timer); };
  }, [trackerPending, trackerCid, trackerSid, readTracker]);
  return { setTrackerTick, tracker, trackerKeys, trackerRerun, dossierTracker };
}
