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
 *  Draws nothing until a provider is chosen. */
export function ControlsReadout({ presetId, provider, model }:
  { presetId: string; provider: string; model: string }) {
  const [preview, setPreview] = useState<ControlsPreview | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setPreview(null);
    setError(null);
    if (!provider) return;
    // The newest question only: a reader flicking through models must not be
    // shown the answer about the one before.
    let current = true;
    api.previewControls({ preset_id: presetId, provider, model })
      .then((p) => { if (current) setPreview(p); })
      .catch((err: unknown) => { if (current) setError(err); });
    return () => { current = false; };
  }, [presetId, provider, model]);

  if (!provider) return null;
  if (error) return <p className="field-hint">Couldn't read what this sends: {errorText(error)}</p>;
  if (!preview) return null;
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
