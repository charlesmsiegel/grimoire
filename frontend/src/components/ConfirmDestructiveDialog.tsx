/**
 * The app-wide confirmation for destructive actions (deletes, removals).
 * Supports an async dependents listing and an optional type-the-id
 * confirmation for high-stakes targets. `window.confirm` / `window.prompt`
 * are banned by lint — use this instead.
 */

import { useState } from "react";

import type { CampaignRef } from "../api/library";
import { Dialog } from "./Dialog";

interface Props {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  /**
   * Omit when the action has no dependents concept. Pass `"loading"` while a
   * lookup is in flight (confirm stays disabled), then the resolved list.
   */
  dependents?: CampaignRef[] | "loading";
  typedConfirmation?: { expected: string; label: string };
  confirmLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDestructiveDialog({
  open,
  title,
  body,
  dependents,
  typedConfirmation,
  confirmLabel = "Delete",
  busyLabel = "Deleting…",
  busy = false,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const [typed, setTyped] = useState("");

  const dependentsLoading = dependents === "loading";
  const typedOk = !typedConfirmation || typed === typedConfirmation.expected;
  const confirmDisabled = busy || dependentsLoading || !typedOk;

  return (
    <Dialog open={open} onClose={onCancel} title={title}>
      {body && <div>{body}</div>}
      {Array.isArray(dependents) && dependents.length > 0 && (
        <>
          <p>
            Affects {dependents.length} campaign
            {dependents.length === 1 ? "" : "s"}:
          </p>
          <ul>
            {dependents.map((c) => (
              <li key={c.id}>{c.name || c.id}</li>
            ))}
          </ul>
        </>
      )}
      {typedConfirmation && (
        <label>
          <span>{typedConfirmation.label}</span>
          <input
            type="text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            autoFocus
          />
        </label>
      )}
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" onClick={onConfirm} disabled={confirmDisabled}>
          {busy ? busyLabel : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
