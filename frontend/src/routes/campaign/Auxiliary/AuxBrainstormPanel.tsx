/**
 * AuxBrainstormPanel — sidebar entry point for brainstorm tasks.
 *
 * The user types a prompt; the panel kicks off `auxiliaryApi.brainstorm`
 * and renders the resulting `AuxiliaryResult` in an `AuxPanel`. Try-again
 * re-runs the prompt and replaces the visible result (no stacking by
 * default).
 */

import { useCallback, useState } from "react";

import { auxiliaryApi, type AuxiliaryResult } from "../../../api/auxiliary";

import { AuxPanel } from "./AuxPanel";

export interface AuxBrainstormPanelProps {
  campaignId: string;
}

export function AuxBrainstormPanel({ campaignId }: AuxBrainstormPanelProps) {
  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState<AuxiliaryResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (overridePrompt?: string) => {
      const text = (overridePrompt ?? prompt).trim();
      if (!text) return;
      setRunning(true);
      setError(null);
      try {
        const next = await auxiliaryApi.brainstorm(campaignId, text);
        setResult(next);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setRunning(false);
      }
    },
    [campaignId, prompt],
  );

  const tryAgain = useCallback(() => {
    void submit();
  }, [submit]);

  return (
    <section className="aux-brainstorm" aria-label="Brainstorm">
      <h3>Brainstorm</h3>
      <textarea
        className="aux-brainstorm-prompt"
        placeholder="What do you want to think through?"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={3}
        aria-label="Brainstorm prompt"
      />
      <div className="aux-brainstorm-actions">
        <button type="button" onClick={() => submit()} disabled={running || prompt.trim() === ""}>
          {running ? "Thinking…" : "Brainstorm"}
        </button>
      </div>
      {error && (
        <p className="aux-brainstorm-error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <AuxPanel
          campaignId={campaignId}
          result={result}
          onTryAgain={tryAgain}
          onDiscarded={() => setResult(null)}
          onAccepted={() => setResult(null)}
        />
      )}
    </section>
  );
}
