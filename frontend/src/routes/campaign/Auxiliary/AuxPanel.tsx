/**
 * AuxPanel — render one auxiliary result with Accept / Discard / Edit / Copy /
 * Try-again controls.
 *
 * Distinct visual treatment per spec: dotted border, muted background, a
 * label badge for the task kind. The panel manages its own edit/text state
 * so the user can tweak before accepting.
 */

import { useCallback, useState } from "react";

import {
  auxiliaryApi,
  type AcceptAuxiliaryResponse,
  type AuxiliaryResult,
} from "../../../api/auxiliary";

const KIND_LABEL: Record<AuxiliaryResult["kind"], string> = {
  impersonate_pc: "Draft",
  rewrite_post: "Rewrite",
  continue_as: "Continuation",
  what_would_x_say: "Suggestion",
  brainstorm: "Brainstorm",
  edit_prose: "Polish",
  translate: "Translation",
};

export interface AuxPanelProps {
  campaignId: string;
  result: AuxiliaryResult;
  onAccepted?: (response: AcceptAuxiliaryResponse) => void;
  onDiscarded?: (resultId: string) => void;
  onTryAgain?: (result: AuxiliaryResult) => void;
}

export function AuxPanel({
  campaignId,
  result,
  onAccepted,
  onDiscarded,
  onTryAgain,
}: AuxPanelProps) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(result.text);
  const [busy, setBusy] = useState<"accept" | "discard" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const accept = useCallback(async () => {
    setBusy("accept");
    setError(null);
    try {
      const edited = editing && text !== result.text ? text : undefined;
      const response = await auxiliaryApi.accept(campaignId, result.id, edited);
      onAccepted?.(response);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }, [campaignId, editing, onAccepted, result.id, result.text, text]);

  const discard = useCallback(async () => {
    setBusy("discard");
    setError(null);
    try {
      await auxiliaryApi.discard(campaignId, result.id);
      onDiscarded?.(result.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }, [campaignId, onDiscarded, result.id]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [text]);

  return (
    <section
      className="aux-panel"
      data-aux-kind={result.kind}
      aria-label={`${KIND_LABEL[result.kind]} suggestion`}
      style={{
        border: "1px dashed currentColor",
        background: "var(--aux-bg, #f6f6f4)",
        padding: "0.75rem",
        borderRadius: "0.5rem",
        margin: "0.5rem 0",
      }}
    >
      <header className="aux-panel-header">
        <span className="aux-badge">{KIND_LABEL[result.kind]}</span>
        <span className="aux-model" aria-label="Model used">
          {result.model_used || "—"}
        </span>
        {result.warnings.length > 0 && (
          <span className="aux-warning" role="alert">
            {result.warnings.join(", ")}
          </span>
        )}
      </header>

      {editing ? (
        <textarea
          className="aux-panel-text aux-panel-text-editable"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          aria-label="Edit auxiliary text"
        />
      ) : (
        <pre className="aux-panel-text" style={{ whiteSpace: "pre-wrap" }}>
          {text}
        </pre>
      )}

      {error && (
        <p className="aux-panel-error" role="alert">
          {error}
        </p>
      )}

      <div className="aux-panel-actions">
        <button type="button" onClick={accept} disabled={busy !== null}>
          {busy === "accept" ? "Accepting…" : "Accept"}
        </button>
        <button type="button" onClick={discard} disabled={busy !== null}>
          {busy === "discard" ? "Discarding…" : "Discard"}
        </button>
        {onTryAgain && (
          <button type="button" onClick={() => onTryAgain(result)} disabled={busy !== null}>
            Try again
          </button>
        )}
        <button type="button" onClick={() => setEditing((v) => !v)} disabled={busy !== null}>
          {editing ? "Done editing" : "Edit"}
        </button>
        <button type="button" onClick={copy} disabled={busy !== null}>
          Copy
        </button>
      </div>
    </section>
  );
}
