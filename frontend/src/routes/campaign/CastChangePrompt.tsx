/**
 * Cast-change review prompt (#464).
 *
 * The Extractor detects characters entering/leaving a scene during play.
 * The Orchestrator resolves each known character into a *pending cast change*
 * (never auto-applied) and ships the list in the `turn_complete` event. This
 * component renders one confirm/dismiss control per pending change and POSTs
 * the player's choice back; confirming applies it to the scene cast through
 * the Scene Manager, dismissing leaves the cast untouched.
 */

import { useCallback, useEffect, useState } from "react";

import { campaignApi, type PendingCastChange } from "../../api/campaign";
import { ApiError } from "../../api/client";
import { PendingCastChangeArraySchema } from "../../api/schemas/castChange";
import { useCampaignEvent } from "../../state/useCampaignEvent";

interface Props {
  campaignId: string;
  sceneId: string;
  /** Called after a confirmed cast change is applied, so the caller can
   *  reload scene state (Side HUD cast, PC-dependent controls). */
  onApplied?: () => void;
}

function label(c: PendingCastChange): string {
  const name = c.character_ref.split("/").pop() || c.character_ref;
  return c.change === "enter" ? `${name} enters the scene` : `${name} leaves the scene`;
}

export function CastChangePrompt({ campaignId, sceneId, onApplied }: Props) {
  const [pending, setPending] = useState<PendingCastChange[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load any persisted pending changes on mount / scene change so prompts
  // queued before a reload or navigation remain actionable. Reset first so a
  // scene switch never shows the previous scene's pending items.
  useEffect(() => {
    let cancelled = false;
    setPending([]);
    void campaignApi
      .listCastChanges(campaignId, sceneId)
      .then((items) => {
        // Only seed from the fetch when it actually returns items, so a
        // late-resolving empty response can't clobber a turn_complete that
        // arrived first.
        if (!cancelled && items.length > 0) setPending(items);
      })
      .catch(() => {
        /* nothing persisted yet, or scene not found — leave empty */
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, sceneId]);

  const handleEvent = useCallback(
    (m: { type: string } & Record<string, unknown>) => {
      // turn_complete carries the payload for normal turns; pending_cast_changes
      // is pushed mid-turn (speaker-loop rounds) and after a scene analysis. Both
      // carry scene_id — ignore any event aimed at a different open scene so a turn
      // or analysis elsewhere can't replace this mounted prompt's state.
      if (typeof m.scene_id === "string" && m.scene_id !== sceneId) return;
      // The WS payload is untyped at runtime; validate before trusting it.
      const parsed = PendingCastChangeArraySchema.safeParse(m.pending_cast_changes);
      if (parsed.success) setPending(parsed.data);
    },
    [sceneId],
  );

  useCampaignEvent(["turn_complete", "pending_cast_changes"], handleEvent);

  const remove = (id: string) => setPending((p) => p.filter((c) => c.id !== id));

  async function act(id: string, kind: "confirm" | "dismiss") {
    setBusy(id);
    setError(null);
    try {
      if (kind === "confirm") {
        await campaignApi.confirmCastChange(campaignId, sceneId, id);
        remove(id);
        // Applying changed the scene cast — reload so the HUD/PC controls
        // reflect the new present_*_refs rather than stale membership.
        onApplied?.();
        return;
      }
      await campaignApi.dismissCastChange(campaignId, sceneId, id);
      remove(id);
    } catch (err) {
      // 404/400 mean the change was already resolved or the scene moved on —
      // it's no longer actionable, so drop it from the list silently.
      if (err instanceof ApiError && (err.status === 404 || err.status === 400)) {
        remove(id);
        return;
      }
      // Any other failure (500, network) must be visible, not swallowed.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  if (pending.length === 0) return null;

  return (
    <div className="cast-change-prompt" role="region" aria-label="Pending cast changes">
      {error && (
        <p className="cast-change-error" role="alert">
          {error}
        </p>
      )}
      {pending.map((c) => (
        <div key={c.id} className="cast-change-row">
          <span className="cast-change-label">{label(c)}</span>
          <button
            type="button"
            className="primary"
            disabled={busy === c.id}
            onClick={() => void act(c.id, "confirm")}
          >
            Confirm
          </button>
          <button type="button" disabled={busy === c.id} onClick={() => void act(c.id, "dismiss")}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
