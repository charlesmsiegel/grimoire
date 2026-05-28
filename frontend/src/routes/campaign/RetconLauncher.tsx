/**
 * Inline retcon workflow (edit → leave/replay/fork decision → kick off batch).
 *
 * Owned by {@link PostItem}; opens when the user clicks "Retcon...". This
 * component handles the three-step UI from the design (spec
 * 2026-05-19-retcon-design §Flow); the actual replay walkthrough is
 * delegated to {@link RetconReplay} when the user picks "Replay".
 */

import { useState } from "react";

import { campaignApi, type ReplayBatchView } from "../../api/campaign";
import { RetconReplay } from "./RetconReplay";

interface Props {
  campaignId: string;
  postId: string;
  turnId: string;
  originalText: string;
  /** Number of model-authored posts after this one in the current scene.
   * Used to decide whether to show the fork nudge per the design (≥ 5). */
  subsequentModelPostCount: number;
  onClose: () => void;
}

type Step = "edit" | "decide" | "fork-nudge" | "replay" | "done-leave";

const FORK_NUDGE_THRESHOLD = 5;

export function RetconLauncher({
  campaignId,
  postId,
  turnId,
  originalText,
  subsequentModelPostCount,
  onClose,
}: Props) {
  const [step, setStep] = useState<Step>("edit");
  const [text, setText] = useState(originalText);
  const [batch, setBatch] = useState<ReplayBatchView | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function callRetcon(replay: boolean) {
    setBusy(true);
    setError(null);
    try {
      const result = await campaignApi.retconPost(campaignId, turnId, {
        post_id: postId,
        new_text: text,
        replay_subsequent: replay,
      });
      if (replay && result.replay_batch_id) {
        setBatchId(result.replay_batch_id);
        // Seed the modal with a synthetic initial view; the modal will
        // refresh it via GET on mount.
        setBatch(null);
        setStep("replay");
      } else {
        setStep("done-leave");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "retcon failed");
    } finally {
      setBusy(false);
    }
  }

  async function forkAndRetcon() {
    setBusy(true);
    setError(null);
    try {
      const newId = `${campaignId}-retcon-${Date.now()}`;
      await campaignApi.forkCampaign(campaignId, {
        new_campaign_id: newId,
        new_name: `Retcon from ${campaignId}`,
        fork_at_post_id: turnId,
      });
      // Stay on the source campaign; the design says "switch to the fork"
      // but that requires routing changes outside this PR's scope. Surface
      // the fork id to the user via the existing campaigns list.
      setStep("done-leave");
    } catch (e) {
      setError(e instanceof Error ? e.message : "fork failed");
    } finally {
      setBusy(false);
    }
  }

  if (step === "replay" && batchId) {
    return (
      <RetconReplay
        campaignId={campaignId}
        batchId={batchId}
        initialState={batch}
        onClose={() => onClose()}
      />
    );
  }

  if (step === "done-leave") {
    return (
      <div className="retcon-launcher-modal" role="dialog" aria-modal aria-label="Retcon">
        <p>Retcon applied. Continuity may flag downstream turns.</p>
        <button type="button" onClick={onClose} autoFocus>
          Close
        </button>
      </div>
    );
  }

  if (step === "fork-nudge") {
    return (
      <div className="retcon-launcher-modal" role="dialog" aria-modal aria-label="Fork before retcon?">
        <p>
          This is a substantial change ({subsequentModelPostCount} posts will replay). Fork the
          campaign first?
        </p>
        {error && (
          <p className="retcon-launcher-error" role="alert">
            {error}
          </p>
        )}
        <div className="retcon-launcher-actions">
          <button type="button" disabled={busy} onClick={forkAndRetcon}>
            Fork &amp; retcon there
          </button>
          <button type="button" disabled={busy} onClick={() => void callRetcon(true)}>
            Retcon here
          </button>
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (step === "decide") {
    return (
      <div className="retcon-launcher-modal" role="dialog" aria-modal aria-label="Leave or replay?">
        <p>Edit accepted. Leave subsequent turns as-is, or replay them?</p>
        {error && (
          <p className="retcon-launcher-error" role="alert">
            {error}
          </p>
        )}
        <div className="retcon-launcher-actions">
          <button type="button" disabled={busy} onClick={() => void callRetcon(false)}>
            Leave as-is
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (subsequentModelPostCount >= FORK_NUDGE_THRESHOLD) {
                setStep("fork-nudge");
              } else {
                void callRetcon(true);
              }
            }}
          >
            Replay
          </button>
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  // step === "edit"
  return (
    <div className="retcon-launcher-modal" role="dialog" aria-modal aria-label="Edit post for retcon">
      <label className="retcon-launcher-label">
        New post text
        <textarea
          className="retcon-launcher-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={10}
          autoFocus
        />
      </label>
      <div className="retcon-launcher-actions">
        <button
          type="button"
          disabled={busy || text.trim() === ""}
          onClick={() => setStep("decide")}
        >
          Accept edit
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
