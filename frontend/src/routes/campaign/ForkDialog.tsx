/**
 * Campaign-level fork dialog (spec 2026-05-19-fork).
 *
 * Takes a source campaign + (optionally) a cutoff post id. Image handling
 * is not surfaced — per the spec the backend probes and silently falls
 * back to a deep copy if hardlinks are not available.
 */

import { useEffect, useState } from "react";

import { campaignApi, type ForkCampaignResult } from "../../api/campaign";
import { ApiError } from "../../api/client";

interface ForkDialogProps {
  open: boolean;
  sourceCampaignId: string;
  sourceCampaignName: string;
  /** When provided, defaults to a fork-from-earlier mode at this post. */
  defaultPostId?: string | null;
  onClose: () => void;
  onForked?: (result: ForkCampaignResult) => void;
}

export function ForkDialog({
  open,
  sourceCampaignId,
  sourceCampaignName,
  defaultPostId,
  onClose,
  onForked,
}: ForkDialogProps) {
  const [mode, setMode] = useState<"current" | "earlier">(defaultPostId ? "earlier" : "current");
  const [postId, setPostId] = useState<string>(defaultPostId ?? "");
  const [newName, setNewName] = useState<string>("");
  const [newId, setNewId] = useState<string>("");
  const [description, setDescription] = useState<string>("");
  const [makeActive, setMakeActive] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    // Suggest a fork id placeholder; user can override.
    setNewId(`${sourceCampaignId}-fork-${Date.now().toString(36).slice(-4)}`);
    setNewName("");
    setDescription("");
    setMakeActive(false);
    setError(null);
    setMode(defaultPostId ? "earlier" : "current");
    setPostId(defaultPostId ?? "");
  }, [open, sourceCampaignId, defaultPostId]);

  if (!open) {
    return null;
  }

  const canSubmit =
    !submitting &&
    newName.trim().length > 0 &&
    newId.trim().length > 0 &&
    (mode === "current" || postId.trim().length > 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await campaignApi.forkCampaign(sourceCampaignId, {
        new_campaign_id: newId.trim(),
        new_name: newName.trim(),
        fork_at_post_id: mode === "earlier" ? postId.trim() : null,
        description: description.trim() || null,
        make_active: makeActive,
      });
      onForked?.(result);
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`Campaign id "${newId}" already exists. Pick a different id.`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="fork-dialog-title"
    >
      <form className="modal fork-dialog" onSubmit={handleSubmit}>
        <h2 id="fork-dialog-title">Fork campaign</h2>
        <p className="muted">
          Source: <strong>{sourceCampaignName}</strong> ({sourceCampaignId})
        </p>

        <fieldset>
          <legend>Fork from</legend>
          <label>
            <input
              type="radio"
              name="fork-mode"
              checked={mode === "current"}
              onChange={() => setMode("current")}
            />
            Current state
          </label>
          <label>
            <input
              type="radio"
              name="fork-mode"
              checked={mode === "earlier"}
              onChange={() => setMode("earlier")}
            />
            Earlier post
          </label>
          {mode === "earlier" && (
            <label className="fork-dialog-field">
              <span>Post id</span>
              <input
                type="text"
                value={postId}
                onChange={(e) => setPostId(e.target.value)}
                placeholder="p_…"
                required
              />
            </label>
          )}
        </fieldset>

        <label className="fork-dialog-field">
          <span>
            New campaign name <em>(required)</em>
          </span>
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            required
            autoFocus
          />
        </label>

        <label className="fork-dialog-field">
          <span>New campaign id</span>
          <input type="text" value={newId} onChange={(e) => setNewId(e.target.value)} required />
        </label>

        <label className="fork-dialog-field">
          <span>Describe the divergence (optional)</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
        </label>

        <label className="fork-dialog-checkbox">
          <input
            type="checkbox"
            checked={makeActive}
            onChange={(e) => setMakeActive(e.target.checked)}
          />
          Make this the active campaign after forking
        </label>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!canSubmit}>
            {submitting ? "Forking…" : "Fork"}
          </button>
        </div>
      </form>
    </div>
  );
}
