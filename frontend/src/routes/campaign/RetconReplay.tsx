/**
 * Full-screen retcon replay modal (spec 2026-05-19-retcon-design).
 *
 * Owned by {@link PostItem}'s "Retcon..." action. Lifecycle:
 *   1. Parent kicks off `retconPost(..., replay_subsequent: true)` and gets
 *      back a `batch_id`.
 *   2. This component polls / subscribes to that batch and walks the user
 *      through Accept / Try again / Cancel for each subsequent post.
 *   3. When `state.completed` becomes true, parent closes the modal.
 *
 * Contradiction surfacing is intentionally light: we render the IDs the
 * backend reports; clicking one navigates to the continuity ledger (where
 * the full report lives) rather than embedding a parallel reader here.
 */

import { useCallback, useEffect, useState } from "react";

import { campaignApi, type ReplayBatchView } from "../../api/campaign";

interface Props {
  campaignId: string;
  batchId: string;
  /** Optional initial state — when the parent already has the response from
   * the kickoff call, pass it in so we don't show a loading flash. */
  initialState?: ReplayBatchView | null;
  onClose: (finalState: ReplayBatchView | null) => void;
}

type Action = "accept" | "try-again" | "cancel" | null;

export function RetconReplay({ campaignId, batchId, initialState, onClose }: Props) {
  const [state, setState] = useState<ReplayBatchView | null>(initialState ?? null);
  const [pending, setPending] = useState<Action>(null);
  const [error, setError] = useState<string | null>(null);

  // Pull initial state if the parent didn't seed us with one.
  useEffect(() => {
    if (initialState) return;
    let cancelled = false;
    void campaignApi
      .getRetconReplay(campaignId, batchId)
      .then((view) => {
        if (!cancelled) setState(view);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "failed to load batch");
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, batchId, initialState]);

  const runAction = useCallback(
    async (which: Exclude<Action, null>, fn: () => Promise<ReplayBatchView>) => {
      if (pending) return;
      setPending(which);
      setError(null);
      try {
        const next = await fn();
        setState(next);
      } catch (e) {
        setError(e instanceof Error ? e.message : `${which} failed`);
      } finally {
        setPending(null);
      }
    },
    [pending],
  );

  const onAccept = () =>
    runAction("accept", () => campaignApi.acceptRetconReplay(campaignId, batchId));
  const onTryAgain = () =>
    runAction("try-again", () => campaignApi.tryAgainRetconReplay(campaignId, batchId));
  const onCancel = () =>
    runAction("cancel", () => campaignApi.cancelRetconReplay(campaignId, batchId));

  if (state === null) {
    return (
      <div className="retcon-replay-modal" role="dialog" aria-modal aria-label="Retcon replay">
        <div className="retcon-replay-loading">Loading replay batch…</div>
      </div>
    );
  }

  const total = state.subsequent_post_ids.length;
  const indicator = total === 0 ? "no subsequent posts" : `${state.current_index + 1} of ${total}`;

  return (
    <div className="retcon-replay-modal" role="dialog" aria-modal aria-label="Retcon replay">
      <header className="retcon-replay-header">
        <h2>Replay subsequent posts</h2>
        <span className="retcon-replay-indicator" aria-live="polite">
          [{indicator}]
        </span>
      </header>

      <ol className="retcon-replay-list">
        {state.subsequent_post_ids.map((postId, idx) => {
          const isCurrent = idx === state.current_index && !state.completed;
          const isAccepted = state.accepted_post_ids.includes(postId);
          const isCancelled = state.cancelled_at_post_id === postId && state.completed;
          return (
            <li
              key={postId}
              className={[
                "retcon-replay-row",
                isCurrent ? "retcon-replay-current" : "",
                isAccepted ? "retcon-replay-accepted" : "",
                isCancelled ? "retcon-replay-cancelled" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              aria-current={isCurrent ? "step" : undefined}
            >
              <span className="retcon-replay-post-id">{postId}</span>
              {isAccepted && <span className="retcon-replay-tag">accepted</span>}
              {isCancelled && <span className="retcon-replay-tag">cancelled here</span>}
              {isCurrent && (
                <>
                  {state.current_alternate_id ? (
                    <span className="retcon-replay-alt">alt: {state.current_alternate_id}</span>
                  ) : (
                    <span className="retcon-replay-alt">generating…</span>
                  )}
                  {state.contradictions.length > 0 && (
                    <details className="retcon-replay-contras">
                      <summary>
                        {state.contradictions.length} contradiction
                        {state.contradictions.length === 1 ? "" : "s"}
                      </summary>
                      <ul>
                        {state.contradictions.map((id) => (
                          <li key={id}>{id}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ol>

      {error && (
        <p className="retcon-replay-error" role="alert">
          {error}
        </p>
      )}

      <footer className="retcon-replay-actions">
        {state.completed ? (
          <button type="button" onClick={() => onClose(state)} autoFocus>
            Close
          </button>
        ) : (
          <>
            <button
              type="button"
              className="retcon-accept"
              disabled={pending !== null || !state.current_alternate_id}
              onClick={onAccept}
            >
              Accept
            </button>
            <button
              type="button"
              className="retcon-try-again"
              disabled={pending !== null || !state.current_alternate_id}
              onClick={onTryAgain}
            >
              Try again
            </button>
            <button
              type="button"
              className="retcon-cancel"
              disabled={pending !== null}
              onClick={onCancel}
            >
              Cancel
            </button>
          </>
        )}
      </footer>
    </div>
  );
}
