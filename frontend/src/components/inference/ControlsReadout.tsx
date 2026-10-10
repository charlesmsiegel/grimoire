import { useEffect, useState } from "react";
import { api, type ControlsPreview } from "../../api/client";
import { errorText } from "../../api/errors";

/** A control's name as a reader would say it: `reasoning_effort` → "Reasoning
 *  effort". Wording only -- which controls exist, and what each does on this
 *  model, is the server's to say. */
function spoken(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** What a sampler preset sends on one provider's model, control by control
 *  (spec 8): "Reasoning effort: translated → thinking · Min p: unsupported,
 *  not sent".
 *
 *  It renders the server's `state`, `wire` and `why` for each control that is
 *  not sent exactly as written, and computes nothing itself -- the frontend
 *  keeps no capability table, so the readout and what a turn sends cannot
 *  disagree. `presetId` "" is no preset; `model` "" is the provider's own.
 *  `operation="decide"` asks about a decision, which a model answered natively
 *  sends no sampling for: the server then calls every control `n/a`, and that
 *  is one line, in the server's words, rather than ten. Draws nothing until a provider is chosen. */
export function ControlsReadout({ presetId, provider, model, operation }:
  { presetId: string; provider: string; model: string; operation?: "decide" }) {
  const [preview, setPreview] = useState<ControlsPreview | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setPreview(null);
    setError(null);
    if (!provider) return;
    // The newest question only: a reader flicking through models must not be
    // shown the answer about the one before.
    let current = true;
    api.previewControls({ preset_id: presetId, provider, model, ...(operation && { operation }) })
      .then((p) => { if (current) setPreview(p); })
      .catch((err: unknown) => { if (current) setError(err); });
    return () => { current = false; };
  }, [presetId, provider, model, operation]);

  if (!provider) return null;
  if (error) return <p className="field-hint">Couldn't read what this sends: {errorText(error)}</p>;
  if (!preview) return null;
  // A decision answered natively: the server says why nothing is sent, once
  // per control and the same for each, so it is said once.
  const controls = Object.values(preview.controls);
  if (operation === "decide" && controls.length > 0
      && controls.every((c) => c.state === "n/a")) {
    return <p className="field-hint">Not sent: {controls[0].why}.</p>;
  }
  const notAsWritten = Object.entries(preview.controls)
    .filter(([, c]) => c.state !== "supported");
  if (notAsWritten.length === 0) {
    return <p className="field-hint">Every control is sent as written.</p>;
  }
  return (
    <ul className="controls-readout" aria-label="Controls">
      {notAsWritten.map(([name, c]) => (
        <li key={name} title={`Source: ${c.source}`}>
          <strong>{spoken(name)}</strong>: {c.state}
          {c.wire ? <> → <code>{c.wire}</code></> : ", not sent"}
          {c.why && <span className="field-hint"> — {c.why}</span>}
        </li>
      ))}
    </ul>
  );
}

/** "What this sends", folded, asking the server only once it is opened: a
 *  form of many rows would otherwise send one preview per row on load, for
 *  answers nobody unfolded. */
export function WhatItSends(props: { presetId: string; provider: string; model: string;
                                     operation?: "decide" }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="what-it-sends" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>What this sends</summary>
      {open && <ControlsReadout {...props} />}
    </details>
  );
}
