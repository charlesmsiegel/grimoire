/**
 * Scene-break medium-confidence prompt (spec 01 §Scene break decisions).
 *
 * The orchestrator emits a `scene_break_suggested` event when the boundary
 * detector's confidence is in the `[prompt_threshold, auto_threshold)` band
 * and pauses the turn. This component listens for that event, opens a modal
 * with continue / start-new-scene choices, and POSTs the answer back via
 * `resolve_scene_break`. If the player does nothing the backend times out
 * after `scene_break.prompt_resume_timeout_seconds` (default 60s) and
 * continues the current scene; the modal then closes when the turn moves on.
 */

import { useCallback, useState } from "react";

import {
  campaignApi,
  type SceneBreakChoice,
  type SceneBreakSuggestedEvent,
} from "../../api/campaign";
import { ApiError } from "../../api/client";
import { useCampaignEvent } from "../../state/useCampaignEvent";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

function describeReason(reason: string): string {
  switch (reason) {
    case "time_gap":
      return "a time gap";
    case "location_change":
      return "a location change";
    case "cast_change":
      return "a change in who's present";
    case "tonal_shift":
      return "a tonal shift";
    case "explicit":
      return "an explicit cue in your input";
    default:
      return reason;
  }
}

interface Props {
  campaignId: string;
}

export function SceneBreakPrompt({ campaignId }: Props) {
  const [pending, setPending] = useState<SceneBreakSuggestedEvent | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEvent = useCallback((m: { type: string } & Record<string, unknown>) => {
    if (m.type !== "scene_break_suggested") return;
    setPending(m as unknown as SceneBreakSuggestedEvent);
    setError(null);
  }, []);

  useCampaignEvent("scene_break_suggested", handleEvent);

  // Close the prompt when the turn finishes — the backend either resolved
  // it (our POST), timed out and continued, or the turn was cancelled.
  // Any of those paths emit a turn-lifecycle event with the same turn_id.
  const handleLifecycle = useCallback(
    (m: { type: string } & Record<string, unknown>) => {
      if (!pending) return;
      const turnId = typeof m.turn_id === "string" ? m.turn_id : null;
      if (!turnId || turnId !== pending.turn_id) return;
      if (
        m.type === "turn_complete" ||
        m.type === "turn_failed" ||
        m.type === "turn_cancelled" ||
        m.type === "turn_timed_out"
      ) {
        setPending(null);
        setSubmitting(false);
        setError(null);
      }
    },
    [pending],
  );

  useCampaignEvent(
    ["turn_complete", "turn_failed", "turn_cancelled", "turn_timed_out"],
    handleLifecycle,
  );

  if (!pending) return null;

  async function submit(choice: SceneBreakChoice) {
    if (!pending) return;
    setSubmitting(true);
    setError(null);
    try {
      await campaignApi.resolveSceneBreak(campaignId, pending.turn_id, choice);
      setPending(null);
    } catch (err) {
      // 404 means the backend timed out / cancelled before our click landed —
      // the prompt is no longer actionable, so drop the modal silently.
      if (err instanceof ApiError && err.status === 404) {
        setPending(null);
        return;
      }
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const confidencePct = Math.round(pending.confidence * 100);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="scene-break-title"
    >
      <div className="modal scene-break-modal">
        <header>
          <h3 id="scene-break-title">Start a new scene?</h3>
          <p className="wizard-step-help">
            The orchestrator detected {describeReason(pending.reason)} (
            <span title="Confidence score">{confidencePct}%</span> confidence). Continue in this
            scene, or close it and open a new one?
          </p>
        </header>
        {error && (
          <p className="wizard-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            onClick={() => void submit("continue")}
            disabled={submitting}
          >
            Continue here
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void submit("new_scene")}
            disabled={submitting}
          >
            {submitting ? "Submitting…" : "Start a new scene"}
          </button>
        </div>
      </div>
    </div>
  );
}
