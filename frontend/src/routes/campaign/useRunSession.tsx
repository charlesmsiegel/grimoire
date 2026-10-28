import { useCallback, useEffect, useRef, useState } from "react";
import type { DirectorNote, Observation, RunHandle, SceneKey } from "./campaignTypes";

export function useRunSession() {
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  // Which scene a turn is streaming into, or null. Distinct from `activeId`,
  // which is the scene being *looked at* — a player can start a turn on A and
  // navigate to B while it runs, and the write still lands in A. Anything that
  // would move A's file has to stay locked for as long as that is true, so the
  // lock follows the stream rather than the view (review, #95). State, not a
  // ref, because the rail renders it.
  const [streamingId, setStreamingId] = useState<string | null>(null);
  // Which turn owns `streamingId`. The lock outlives `busy` — it is held across
  // the post-cancel flush poll — so a newer turn can claim it while an older
  // one is still unwinding, and only the current owner may release it.
  const streamTokenRef = useRef(0);
  // Held for the life of one turn so Cancel can reach it. A ref, not state:
  // the controller is not rendered, and rebuilding the component tree on every
  // send just to store it would remount the transcript mid-stream.
  const abortRef = useRef<AbortController | null>(null);
  // How Stop reaches the turn in flight. Closing the connection is no longer
  // the cancel, so the run has to be addressable -- and addressable BEFORE its
  // leading frame arrives, because the window where the server has accepted and
  // detached the work but no frame has reached the browser is exactly the
  // window a dying connection lands in. `attempt` is minted client-side and
  // sent with the request, so it names the turn from the first byte; `id` is
  // filled in when the leading frame confirms it, and Stop falls back to
  // discovery by attempt when it has not.
  const runRef = useRef<
    { cid: string; sid: string; attempt: string; id: string | null } | null>(null);
  // The in-flight `cancelRun`, so the stream's teardown can wait for it before
  // releasing the scene. The route answers only once the run is really
  // terminal, which is the authoritative "the partial is written and the
  // exclusion key is free" signal -- without waiting for it, Stop re-enables
  // Send while the old run still holds the scene and the next send is refused.
  const cancelRef = useRef<Promise<void> | null>(null);
  // The flush poll can outlive the view by up to its whole budget, and it calls
  // setState on every tick. Nothing else here needs an unmount guard — a stream
  // deliberately runs to completion after a navigation, and its refresh is a
  // React warning at worst — but a loop that keeps refetching a scene nobody is
  // looking at is waste with no upside.
  const mountedRef = useRef(true);
  // "Round n/of" of an automatic chain, from the `round_start` frames; cleared
  // whenever the view stops being busy.
  const [roundProgress, setRoundProgress] = useState<{ index: number; of: number } | null>(null);
  useEffect(() => { if (!busy) setRoundProgress(null); }, [busy]);
  const [streamingSpeakers, setStreamingSpeakers] = useState<SpeakerPart[]>([]);
  // The transient director note, and the scene it is steering. Codex review (P2): a
  // bare string plus the GLOBAL `busy` rendered one scene's note inside
  // another's transcript, because a turn deliberately survives navigation --
  // the reader moves, the run does not. Campaign as well as scene: ids are
  // campaign-local, so `s1` names a different scene in every campaign and a
  // scene-only guard answers true for the wrong one.
  const [directorNote, setDirectorNote] =
    useState<{ cid: string; sid: string; text: string } | null>(null);

  // Set on the way in as well as cleared on the way out. StrictMode runs the
  // setup/cleanup/setup cycle on mount in development, so a cleanup-only effect
  // leaves the flag false for the whole life of the view — and `owns()` below
  // reads it, which would have every post-cancel flush poll bow out before its
  // first look and leave a flushed partial invisible until the next refresh
  // (review, #95). Dev-only, but dev is where cancelling gets exercised.
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const observerActive = useCallback(() => abortRef.current !== null, []);
  const turnToken = useCallback(() => streamTokenRef.current, []);
  const handle = useCallback(() => runRef.current, []);
  const mounted = useCallback(() => mountedRef.current, []);
  const beginLive = useCallback((scope: SceneKey, attempt: string): Observation => {
    const controller = new AbortController();
    abortRef.current = controller;
    runRef.current = { ...scope, attempt, id: null };
    const token = ++streamTokenRef.current;
    setBusy(true);
    setStreamingId(scope.sid);
    return { signal: controller.signal, token };
  }, []);
  const beginAttach = useCallback((scope: SceneKey, attempt: string, id: string): Observation => {
    const controller = new AbortController();
    abortRef.current = controller;
    runRef.current = { ...scope, attempt, id };
    setBusy(true);
    setStreamingId(scope.sid);
    return { signal: controller.signal, token: streamTokenRef.current };
  }, []);
  const lockPending = useCallback((handle: RunHandle) => {
    setBusy(true);
    setStreamingId(handle.sid);
    runRef.current = handle;
  }, []);
  const confirmRun = useCallback((attempt: string, id: string) => {
    if (runRef.current?.attempt === attempt) runRef.current.id = id;
  }, []);
  const clearObserver = useCallback(() => { abortRef.current = null; }, []);
  const abortObserver = useCallback(() => { abortRef.current?.abort(); }, []);
  const storeCancellation = useCallback((pending: Promise<void>) => { cancelRef.current = pending; }, []);
  const takeCancellation = useCallback(() => {
    const pending = cancelRef.current;
    cancelRef.current = null;
    return pending;
  }, []);
  const forgetHandle = useCallback(() => { runRef.current = null; }, []);
  const releaseComposer = useCallback(() => { setBusy(false); }, []);
  const releaseSceneLock = useCallback(() => { setStreamingId(null); }, []);
  const clearPreview = useCallback(() => { setStreaming(""); setStreamingSpeakers([]); }, []);
  const resetParts = useCallback(() => { setStreamingSpeakers([]); setRoundProgress(null); }, []);
  const clearNoteIf = useCallback((note: DirectorNote | null) => {
    setDirectorNote((cur) => cur === note ? null : cur);
  }, []);
  const renderFrame = useCallback((update: PreviewUpdate) => {
    switch (update.kind) {
      case "text": setStreaming(update.text); break;
      case "round": setRoundProgress(update.round); break;
      case "start": setStreamingSpeakers((prior) => [...prior, update.part]); break;
      case "thinking": setStreamingSpeakers((prior) => prior.map((part, index) =>
        index === prior.length - 1 ? { ...part, thinking: update.reset ? "" : (part.thinking ?? "") + update.delta } : part)); break;
      case "end": setStreamingSpeakers((prior) => prior.map((part) =>
        part.id === update.id ? { ...part, ended: true } : part)); break;
    }
  }, []);
  return {
    busy, streamingId, streaming, streamingSpeakers, directorNote, roundProgress,
    observerActive, turnToken, handle, mounted, beginLive, beginAttach, lockPending,
    confirmRun, clearObserver, abortObserver, storeCancellation, takeCancellation,
    forgetHandle, releaseComposer, releaseSceneLock, clearPreview, resetParts,
    setNote: setDirectorNote, clearNoteIf, renderFrame
  };
}

export type RunSession = ReturnType<typeof useRunSession>;

export type SpeakerPart = {
  id: string; speaker: string; actor_ref?: string;
  offset: number; ended?: boolean; thinking?: string; seed?: string | undefined
};
export type PreviewUpdate =
  | { kind: "text"; text: string }
  | { kind: "round"; round: { index: number; of: number } }
  | { kind: "start"; part: SpeakerPart }
  | { kind: "thinking"; reset: boolean; delta: string }
  | { kind: "end"; id: string };
