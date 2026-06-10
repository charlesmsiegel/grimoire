/**
 * Promote a campaign-emergent entity into a library world (issue #601).
 * Shared between the Cast detail panel (characters) and the World view's
 * entity cards (items / locations / lore / factions / monsters / greetings) —
 * characters route through their two-step confirm endpoint, everything else
 * through the generic kind endpoint.
 */

import { useCallback, useState } from "react";

import { errorMessage } from "../../api/client";
import { ENTITY_KIND_PLURAL, type EntityKind } from "../../api/library";
import { viewsApi } from "../../api/views";
import { useResource } from "../../api/useResource";
import { AsyncSection } from "../../components/AsyncSection";
import { Dialog } from "../../components/Dialog";

interface PromoteToLibraryDialogProps {
  campaignId: string;
  /** Singular entity kind: "character", "item", "location", … */
  kind: string;
  entityId: string;
  name?: string;
  onClose: () => void;
  onPromoted: () => void;
}

export function PromoteToLibraryDialog({
  campaignId,
  kind,
  entityId,
  name,
  onClose,
  onPromoted,
}: PromoteToLibraryDialogProps) {
  const worlds = useResource(useCallback(() => viewsApi.listWorlds(), []));
  const [target, setTarget] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectiveTarget = target || worlds.data?.[0]?.id || "";

  async function promote() {
    if (!effectiveTarget) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "character") {
        await viewsApi.promoteCharacterToLibrary(campaignId, entityId, {
          target_world_id: effectiveTarget,
          confirm: true,
        });
      } else {
        const plural = ENTITY_KIND_PLURAL[kind as EntityKind] ?? `${kind}s`;
        await viewsApi.promoteEntityToLibrary(campaignId, plural, entityId, {
          target_world_id: effectiveTarget,
        });
      }
      onPromoted();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={onClose} title={`Promote ${name || entityId} to library`}>
      <p className="muted">
        Writes this campaign-emergent {kind} into a library world; the emergent copy is cleaned up
        (divergent edits stay behind as a campaign override).
      </p>
      <AsyncSection state={worlds} emptyMessage="No worlds available. Create a world first.">
        {(rows) => (
          <label className="form-field field">
            <span>Target world</span>
            <select value={effectiveTarget} onChange={(e) => setTarget(e.target.value)}>
              {rows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name || w.id}
                </option>
              ))}
            </select>
          </label>
        )}
      </AsyncSection>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" onClick={() => void promote()} disabled={busy || !effectiveTarget}>
          {busy ? "Promoting…" : "Promote"}
        </button>
      </div>
    </Dialog>
  );
}
