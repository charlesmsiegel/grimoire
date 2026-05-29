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

import { useCallback, useState } from "react";

import { campaignApi, type PendingCastChange } from "../../api/campaign";
import { ApiError } from "../../api/client";
import { useCampaignEvent } from "../../state/useCampaignEvent";

interface Props {
  campaignId: string;
  sceneId: string;
}

function label(c: PendingCastChange): string {
  const name = c.character_ref.split("/").pop() || c.character_ref;
  return c.change === "enter" ? `${name} enters the scene` : `${name} leaves the scene`;
}

export function CastChangePrompt({ campaignId, sceneId }: Props) {
  const [pending, setPending] = useState<PendingCastChange[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const handleEvent = useCallback((m: { type: string } & Record<string, unknown>) => {
    if (m.type !== "turn_complete") return;
    const changes = m.pending_cast_changes;
    if (Array.isArray(changes)) setPending(changes as PendingCastChange[]);
  }, []);

  useCampaignEvent("turn_complete", handleEvent);

  const remove = (id: string) => setPending((p) => p.filter((c) => c.id !== id));

  async function act(id: string, kind: "confirm" | "dismiss") {
    setBusy(id);
    try {
      if (kind === "confirm") await campaignApi.confirmCastChange(campaignId, sceneId, id);
      else await campaignApi.dismissCastChange(campaignId, sceneId, id);
      remove(id);
    } catch (err) {
      // 404/400 mean the change was already resolved or the scene moved on —
      // it's no longer actionable, so drop it from the list silently.
      if (err instanceof ApiError && (err.status === 404 || err.status === 400)) {
        remove(id);
        return;
      }
    } finally {
      setBusy(null);
    }
  }

  if (pending.length === 0) return null;

  return (
    <div className="cast-change-prompt" role="region" aria-label="Pending cast changes">
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
