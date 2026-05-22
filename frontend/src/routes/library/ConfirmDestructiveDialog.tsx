import { useState } from "react";

import type { CampaignRef } from "../../api/library";

interface Props {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  /** `undefined` = lookup in flight; `[]` = no dependents. */
  dependents?: CampaignRef[];
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
  if (!open) return null;

  const dependentsLoading = dependents === undefined;
  const typedOk = !typedConfirmation || typed === typedConfirmation.expected;
  const confirmDisabled = busy || dependentsLoading || !typedOk;

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-destructive-title"
    >
      <div className="modal">
        <h4 id="confirm-destructive-title">{title}</h4>
        {body && <div>{body}</div>}
        {dependents && dependents.length > 0 && (
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
      </div>
    </div>
  );
}
