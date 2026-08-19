import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type ReplayPreview, type ReplaySession } from "../api/client";

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
export function ReplayPanel({ cid, sid, startAt, onStartHandled, onChanged, onForked, disabled }: {
  cid: string;
  sid: string;
  /** The post the reader asked to replay from, or null. Set by the transcript
   *  gutter; the panel prices it and asks before anything is cut. */
  startAt: number | null;
  onStartHandled: () => void;
  onChanged: () => void;
  /** Where a fork lands. The replay is meant to run in the copy, so the caller
   *  navigates there rather than this panel starting a walk in a campaign the
   *  reader is not looking at. */
  onForked: (cid: string) => void;
  /** True while the transcript is busy with something else — a turn in flight,
   *  a swap. Every button here writes to the same transcript. */
  disabled?: boolean;
}) {
  const [session, setSession] = useState<ReplaySession | null>(null);
  // The price, stamped with the post it was taken for. Stamped rather than
  // cleared when `startAt` changes: a clearing effect has to run on every
  // render of an idle panel, and one that also depends on the value it clears
  // re-fetches itself. Reading `at` is what keeps a stale price off a different
  // post -- it is simply not rendered.
  const [preview, setPreview] = useState<{ at: number; data: ReplayPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState(false);
  const [error, setError] = useState("");

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
         setError(err instanceof ApiError ? err.detail : "that post could not be priced");
         handled.current();
       });
    return () => { live = false; };
  }, [cid, sid, startAt]);

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.detail : "that step could not be taken");
    } finally {
      setBusy(false);
    }
  }

  const start = () => guard(async () => {
    if (startAt === null) return;
    await api.startReplay(cid, sid, startAt);
    onStartHandled();
    setRan(false);
    await load();
    onChanged();
  });

  const fork = () => guard(async () => {
    const name = window.prompt("Name for the forked campaign");
    if (!name || !name.trim()) return;
    const forked = await api.forkCampaign(cid, name.trim());
    onStartHandled();
    onForked(forked.id);
  });

  const runTurn = () => guard(async () => {
    await api.replayTurn(cid, sid, () => {});
    setRan(true);
    await load();
    onChanged();
  });

  const again = () => guard(async () => {
    await api.regenerate(cid, sid, () => {});
    await load();
    onChanged();
  });

  const accept = () => guard(async () => {
    await api.acceptReplay(cid, sid);
    setRan(false);
    await load();
    onChanged();
  });

  const stop = () => guard(async () => {
    // The default is the non-destructive answer, and the question is asked
    // rather than assumed: the originals are only on file until this call.
    const restore = window.confirm(
      "Put the rest of the original scene back?\n\n" +
      "Cancel keeps the scene as the replay has left it and discards the posts " +
      "that have not been replayed yet.");
    await api.cancelReplay(cid, sid, restore);
    setRan(false);
    await load();
    onChanged();
  });

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
            {error && <p className="field-hint">{error}</p>}
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
          {error && <p className="field-hint">{error}</p>}
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
