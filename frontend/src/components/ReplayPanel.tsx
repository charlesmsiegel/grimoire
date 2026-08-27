import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type ReplayPreview, type ReplaySession } from "../api/client";
import { newAttemptId, type ChatEvent } from "../api/stream";
import { useRunRegistry } from "../runs/RunRegistryProvider";
import { ErrorNote } from "./ErrorNote";

/** The retcon replay's own surface (#79/#80).
 *
 *  It owns three things the transcript cannot: what a replay from a given post
 *  would cost before anything is cut, the walk's position once one is running,
 *  and the fork nudge that sits between the two. Everything it does lands in
 *  the scene, so every action ends in `onChanged` — the transcript, the rail
 *  and the ledger are the caller's to refresh.
 *
 *  The generated reply is NOT streamed into the transcript here. `replayTurn`
 *  streams server-side exactly as an ordinary turn does, but this panel only
 *  needs to know when it finished: the reviewer reads the reply in the
 *  transcript, which `onChanged` re-reads once the stream closes. Rerolling it
 *  is the same `regenerate` the gutter offers, because the fresh reply is the
 *  trailing run.
 */
export function ReplayPanel({ cid, sid, startAt, onStartHandled, onChanged, onForked,
                             disabled, latch, onUnanswered }: {
  cid: string;
  sid: string;
  /** The post the reader asked to replay from, or null. Set by the transcript
   *  gutter; the panel prices it and asks before anything is cut. */
  startAt: number | null;
  onStartHandled: () => void;
  /** The transcript moved. `asked` means the backend already scheduled this
   *  write's follow-ups, so the handler must refresh without asking again
   *  (#397) — see `refresh`. */
  onChanged: (asked?: boolean) => void;
  /** Where a fork lands. The replay is meant to run in the copy, so the caller
   *  navigates there rather than this panel starting a walk in a campaign the
   *  reader is not looking at. */
  onForked: (cid: string) => void;
  /** True while the transcript is busy with something else — a turn in flight,
   *  a swap. Every button here writes to the same transcript. */
  disabled?: boolean;
  /** Take the transcript's write latch for the duration of one of this panel's
   *  requests, releasing it with the returned function. The same latch retry,
   *  reroll, edit and the roll paths take — and this panel needs it for the
   *  same reason they do: a replayed turn is a generation into the scene on
   *  screen, so while one runs the composer, the gutter and Retry must not
   *  offer to start a second one. Optional so the component stands alone in
   *  its own tests. */
  latch?: () => () => void;
  /** Hand this scene's unresolved send to the parent's recovery pass.
   *
   *  A stream that ended with neither `done` nor `error` leaves a run that may
   *  still be generating and still holding the scene -- but this panel's latch
   *  and busy state are released the moment its request settles, so Replay,
   *  Accept and the composer all come back and every one of them is then
   *  refused. Waiting for the next mount or `visibilitychange` to notice is
   *  what review caught: the parent already knows how to adopt such a run, and
   *  the answer is to tell it now rather than to duplicate that logic here. */
  onUnanswered?: () => void;
}) {
  // The same provider the composer's turns are recorded in, so a replayed turn
  // is discoverable by attempt after the WebView is suspended. `useRunRegistry`
  // degrades to a no-op stand-in with no provider mounted, which is what lets
  // this panel keep standing alone in its own tests.
  const registry = useRunRegistry();
  const [session, setSession] = useState<ReplaySession | null>(null);
  // The price, stamped with the post it was taken for. Stamped rather than
  // cleared when `startAt` changes: a clearing effect has to run on every
  // render of an idle panel, and one that also depends on the value it clears
  // re-fetches itself. Reading `at` is what keeps a stale price off a different
  // post -- it is simply not rendered.
  const [preview, setPreview] = useState<{ at: number; data: ReplayPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  // The rejection or the stream frame itself where there is one, so a
  // replay the model could not be reached for says so rather than showing a
  // bare socket error (#210) -- a replay re-runs model turns, so it fails
  // offline exactly like the composer does. The invented fallbacks below
  // stay strings; `ErrorNote` renders one unchanged.
  const [error, setError] = useState<unknown>(null);
  // Whether a replayed reply is waiting on a verdict. The SESSION's answer, not
  // a flag this component sets when it runs a turn: a reload, or a second tab,
  // would lose that flag and offer "Replay next turn" for a turn already run —
  // and the second generation would land beside the first. The server refuses
  // that outright (`replay.stage`); this is the same fact, so the button the
  // reviewer sees matches the one the server will accept.
  const ran = !!session?.pending;

  // The session as last installed, and whether this panel is still mounted.
  // Both exist for the same reason: this component mounts on EVERY scene the
  // reader opens, and almost always finds no replay. An unconditional
  // `setSession` would then schedule a null-to-null update per scene open --
  // which renders nothing, but does land after the read resolves, so it turns
  // every unrelated test of the transcript into one with a stray update in it
  // and every unmount into a possible update on a dead component. Comparing
  // first means the quiet case schedules nothing at all.
  const alive = useRef(true);
  const held = useRef<ReplaySession | null>(null);
  useEffect(() => () => { alive.current = false; }, []);

  const load = useCallback(async () => {
    let next: ReplaySession | null = null;
    try {
      // `?? null` rather than the value as it arrives: "no replay" has to be
      // ONE value here or the comparison below cannot recognise it, and the
      // quiet case would schedule an update per scene open after all.
      next = (await api.getReplay(cid, sid)) ?? null;
    } catch {
      next = null;        // a scene that is gone is not an error to report here
    }
    if (!alive.current || JSON.stringify(next) === JSON.stringify(held.current)) return;
    held.current = next;
    setSession(next);
  }, [cid, sid]);

  useEffect(() => { void load(); }, [load]);

  // Through a ref, and NOT in the effect's dependencies. The caller passes an
  // inline arrow, which is a new function on every render of a view that
  // re-renders on every streamed token — depending on it would re-price the
  // post several times a second for as long as the dialog is open.
  const handled = useRef(onStartHandled);
  useEffect(() => { handled.current = onStartHandled; });

  useEffect(() => {
    if (startAt === null) return;
    let live = true;
    api.replayPreview(cid, sid, startAt)
       .then((p) => { if (live) setPreview({ at: startAt, data: p }); })
       .catch((err: unknown) => {
         if (!live) return;
         setError(err instanceof ApiError ? err : "that post could not be priced");
         handled.current();
       });
    return () => { live = false; };
  }, [cid, sid, startAt]);

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    // Held across the whole request, not just the write inside it: what the
    // latch keeps out is a second generation into this scene, and that window
    // is the request.
    const release = latch?.();
    try {
      await fn();
    } catch (err: unknown) {
      // `alive`, on every state update past an await: a reader who switches
      // scene mid-turn unmounts this panel while its request is still open, and
      // the request is the server's to finish either way.
      if (alive.current) {
        setError(err instanceof ApiError ? err : "that step could not be taken");
      }
    } finally {
      release?.();
      if (alive.current) setBusy(false);
    }
  }

  /** Re-read the session, then tell the caller its transcript moved — but only
   *  while this panel is still on screen. A reader who switches scene mid-request
   *  unmounts it, and `onChanged` refreshes the scene this panel was for, which
   *  by then is not the one they are looking at.
   *
   *  `asked` says the BACKEND already scheduled this write's rolling-summary
   *  and scene-break work, so the caller must refresh without asking again
   *  (#397). Exactly one action here sets it: `again`, which goes through
   *  `/regenerate` — an ordinary turn producer, which schedules its own
   *  follow-ups wherever it is called from. `/replay/turn` deliberately does
   *  not, and a cut, an accept or a cancel is no turn at all, so every other
   *  caller still asks. Without this the reroll fired both, and the scene-break
   *  question has no in-flight coalescing to collapse them: two calls reach the
   *  provider and one answer is thrown away as superseded, billed. */
  async function refresh(asked = false) {
    await load();
    if (alive.current) onChanged(asked);
  }

  const start = () => guard(async () => {
    if (startAt === null) return;
    await api.startReplay(cid, sid, startAt);
    if (alive.current) onStartHandled();
    await refresh();
  });

  /** Asked BEFORE `guard`, deliberately. `window.prompt` is modal and
   *  synchronous, so anything the guard is holding — `busy`, and the
   *  transcript's write latch, which greys out the composer and the gutter for
   *  the whole app — would be held for as long as the reader looks at the
   *  dialog, or forever if they walk away from it. Same reason `stop` asks
   *  first. */
  function fork() {
    const name = window.prompt("Name for the forked campaign")?.trim();
    if (!name) return;
    return guard(async () => {
      const forked = await api.forkCampaign(cid, name);
      if (!alive.current) return;
      onStartHandled();
      onForked(forked.id);
    });
  }

  /** Run a streaming call and surface what it streamed BACK, not merely whether
   *  it threw. A generation that fails mid-stream reports an `error` frame and
   *  the request still completes — so a caller that ignores frames reads a
   *  failed turn as a finished one, and offers Accept for a reply that was
   *  never written. */
  async function streamed(
    run: (onEvent: (e: ChatEvent) => void, attempt: string,
          onIndex: (i: number) => void) => Promise<void>,
  ) {
    // REGISTERED BEFORE THE REQUEST GOES OUT, exactly as the composer's turns
    // are. `/replay/turn` and `/regenerate` are detached server-side now, so a
    // WebView suspended mid-walk leaves the backend generating while `guard`
    // releases this panel's latch -- the panel cannot cancel it, cannot
    // reattach when the tab comes back, never reaches `refresh()`, and shows
    // replay state that is quietly stale until a manual reload (codex, P2).
    //
    // No `text`: a replayed turn submits nothing of the reviewer's own, so
    // there is nothing to hand back. What the record buys here is the attempt
    // -- the id the server sees, which is what makes the run addressable and
    // idempotent if this request is re-sent.
    const attempt = newAttemptId();
    registry.begin({ cid, sid, attempt, text: "", runId: null });
    // The frame whole, not its `detail`: the kind rides on it, and this is the
    // path a replay takes when the provider is unreachable (#210).
    let failed: ChatEvent["error"] = undefined;
    let answered = false;
    // The resume cursor is keyed by RUN id, which only the leading frame
    // supplies -- so indexes seen before it are dropped rather than filed under
    // the attempt, where nothing would ever look for them.
    let runId: string | null = null;
    try {
      await run((e) => {
        if (e.run) { runId = e.run.id; registry.attach(cid, sid, e.run.id); }
        if (e.error) { failed = e.error; answered = true; }
        if (e.done) answered = true;
      }, attempt, (i) => { if (runId) registry.consume(runId, i); });
    } finally {
      // A stream that ended WITHOUT an answer deliberately stays pending: the
      // run may well still be generating, and that is the case the registry
      // exists for. Anything that got an answer is resolved and settles here,
      // the same rule `runStream` follows.
      if (answered) registry.settle(cid, sid);
      // And an unanswered one is handed straight to the parent's adoption
      // pass, which reattaches if the run is live and takes the scene back
      // under its own busy state. Without this the panel simply re-enabled
      // itself over a run that still owned the scene.
      else onUnanswered?.();
    }
    if (failed && alive.current) setError(failed);
  }

  const runTurn = () => guard(async () => {
    await streamed((on, attempt, onIndex) =>
      api.replayTurn(cid, sid, on, undefined, attempt, onIndex));
    await refresh();
  });

  const again = () => guard(async () => {
    await streamed((on, attempt, onIndex) =>
      api.regenerate(cid, sid, on, undefined, undefined, attempt, onIndex));
    await refresh(true);   // `/regenerate` asked for itself — see `refresh`
  });

  const accept = () => guard(async () => {
    await api.acceptReplay(cid, sid);
    await refresh();
  });

  /** The question comes first and outside `guard`, for `fork`'s reason: this
   *  one is a modal the reader may sit on, and the latch it would otherwise be
   *  holding belongs to the whole transcript.
   *
   *  The default is the non-destructive answer, and it is asked rather than
   *  assumed: the originals are only on file until this call. */
  function stop() {
    const restore = window.confirm(
      "Put the rest of the original scene back?\n\n" +
      "Cancel keeps the scene as the replay has left it and discards the posts " +
      "that have not been replayed yet.");
    return guard(async () => {
      await api.cancelReplay(cid, sid, restore);
      await refresh();
    });
  }

  if (startAt !== null && !session) {
    const priced = preview && preview.at === startAt ? preview.data : null;
    if (!priced) return null;
    return (
      <div className="replay-panel" role="group" aria-label="Replay from this post">
        {priced.blocked ? (
          <>
            <p className="field-hint">{priced.blocked}</p>
            <div className="form-actions">
              <button className="subtle" onClick={onStartHandled}>Close</button>
            </div>
          </>
        ) : (
          <>
            <h4>Replay {priced.turns} turn{priced.turns === 1 ? "" : "s"}</h4>
            <p className="field-hint">
              The {priced.posts} post{priced.posts === 1 ? "" : "s"} after this one are
              cut and held. Your own posts go back as they were; each model turn is
              generated again against the edited history, and you accept or reroll it
              before the next one runs.
            </p>
            {priced.fork && (
              <p className="field-hint">
                That is more than {priced.threshold} turns. Forking first leaves this
                campaign exactly as it is and replays in the copy.
              </p>
            )}
            {error != null && <p className="field-hint"><ErrorNote err={error} /></p>}
            <div className="form-actions">
              <button className="subtle" onClick={onStartHandled}>Cancel</button>
              <button className={priced.fork ? "primary" : "subtle"} disabled={busy}
                      onClick={fork}>Fork first…</button>
              <button className={priced.fork ? "subtle" : "primary"}
                      disabled={busy || disabled} onClick={start}>Replay in place</button>
            </div>
          </>
        )}
      </div>
    );
  }

  if (!session) return null;

  return (
    <div className="replay-panel" role="group" aria-label="Retcon replay">
      <h4>Replaying — {session.turns_left} turn{session.turns_left === 1 ? "" : "s"} left</h4>
      {session.gone ? (
        <>
          <p className="field-hint">
            The scene this replay belongs to is gone. The posts it was holding cannot
            be put back into it.
          </p>
          <div className="form-actions">
            <button className="subtle" disabled={busy}
                    onClick={() => guard(async () => {
                      await api.cancelReplay(cid, sid, false);
                      await load();
                      onChanged();
                    })}>Discard</button>
          </div>
        </>
      ) : (
        <>
          <p className="field-hint">
            {ran ? "Keep this reply and move on, or run it again."
                 : "Run the next turn against the history as it now reads."}
          </p>
          {error != null && <p className="field-hint"><ErrorNote err={error} /></p>}
          <div className="form-actions">
            <button className="subtle" disabled={busy} onClick={stop}>Stop</button>
            {ran && (
              <button className="subtle" disabled={busy || disabled}
                      onClick={again}>Try again</button>
            )}
            {ran ? (
              <button className="primary" disabled={busy || disabled}
                      onClick={accept}>Accept</button>
            ) : (
              <button className="primary" disabled={busy || disabled}
                      onClick={runTurn}>{busy ? "Replaying…" : "Replay next turn"}</button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
