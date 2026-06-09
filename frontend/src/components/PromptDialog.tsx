/**
 * Single-input prompt dialog — the in-app replacement for `window.prompt`
 * (banned by lint). The caller owns submission: keep the dialog mounted with
 * an `error` to reject a value, unmount/close it to accept.
 */

import { useState } from "react";

import { Dialog } from "./Dialog";

interface PromptDialogProps {
  open: boolean;
  title: string;
  label: string;
  initialValue?: string;
  placeholder?: string;
  hint?: React.ReactNode;
  confirmLabel?: string;
  busy?: boolean;
  error?: string | null;
  inputType?: "text" | "number";
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  title,
  label,
  initialValue = "",
  placeholder,
  hint,
  confirmLabel = "OK",
  busy = false,
  error,
  inputType = "text",
  onSubmit,
  onCancel,
}: PromptDialogProps) {
  const [value, setValue] = useState(initialValue);

  return (
    <Dialog open={open} onClose={busy ? () => undefined : onCancel} title={title}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(value);
        }}
      >
        <label className="field">
          <span>{label}</span>
          <input
            type={inputType}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            autoComplete="off"
            autoFocus
          />
        </label>
        {hint && <p className="muted">{hint}</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="submit" disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
