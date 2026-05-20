import { useCallback, useEffect, useRef, useState } from "react";

import { auxiliaryApi, type AuxiliaryResult } from "../../api/auxiliary";
import type { ApiScene, PCEntry } from "../../api/campaign";
import { AuxPanel } from "./Auxiliary/AuxPanel";
import { ExpressionPicker } from "./ExpressionPicker";
import { PCSwitcher } from "./PCSwitcher";

interface Props {
  campaignId: string;
  scene: ApiScene | null;
  pcs: PCEntry[];
  activePcRef: string | null;
  text: string;
  onTextChange: (text: string) => void;
  onChangePC: (ref: string) => void;
  onSubmit: (text: string, emotion?: string) => Promise<void>;
  onAdvance: () => Promise<void>;
  advanceEnabled: boolean;
  advanceReason: string;
  busy: boolean;
}

export function InputArea({
  campaignId,
  scene,
  pcs,
  activePcRef,
  text,
  onTextChange,
  onChangePC,
  onSubmit,
  onAdvance,
  advanceEnabled,
  advanceReason,
  busy,
}: Props) {
  const [emotion, setEmotion] = useState("neutral");
  const [submitting, setSubmitting] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestion, setSuggestion] = useState<AuxiliaryResult | null>(null);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const requestSuggestion = useCallback(async () => {
    if (!activePcRef || suggesting) return;
    setSuggesting(true);
    setSuggestError(null);
    try {
      const result = await auxiliaryApi.impersonatePC(campaignId, text.trim() || undefined);
      setSuggestion(result);
    } catch (e) {
      setSuggestError(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggesting(false);
    }
  }, [activePcRef, campaignId, suggesting, text]);

  const isMultiPC = (scene?.present_pc_refs.length ?? 0) >= 2;
  const canSubmit = !!activePcRef && text.trim().length > 0 && !submitting && !busy;
  const showAdvance = isMultiPC;

  const submit = useCallback(async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit(text, emotion);
      onTextChange("");
      setEmotion("neutral");
      taRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, onSubmit, onTextChange, text, emotion]);

  const advance = useCallback(async () => {
    if (!advanceEnabled || advancing || busy) return;
    setAdvancing(true);
    try {
      await onAdvance();
    } finally {
      setAdvancing(false);
    }
  }, [advanceEnabled, advancing, busy, onAdvance]);

  useEffect(() => {
    if (!busy && taRef.current) taRef.current.focus();
  }, [busy]);

  return (
    <form
      className="input-area"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      aria-label="Compose post"
    >
      <div className="input-meta">
        <PCSwitcher pcs={pcs} activePcRef={activePcRef} onChange={onChangePC} />
        {isMultiPC && (
          <span className="input-multi-hint" aria-live="polite">
            Multi-PC scene — posts queue locally, click Advance to call the narrator.
          </span>
        )}
      </div>
      <textarea
        ref={taRef}
        className="input-textarea"
        value={text}
        onChange={(e) => onTextChange(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            void submit();
          }
        }}
        placeholder={
          activePcRef
            ? `Posting as ${pcs.find((p) => p.character_ref === activePcRef)?.name ?? activePcRef}`
            : "Add a PC to begin posting."
        }
        rows={4}
        aria-label="Post body (Ctrl/Cmd+Enter to submit)"
      />
      <div className="input-actions">
        <ExpressionPicker
          value={emotion}
          onChange={setEmotion}
          disabled={submitting || busy}
        />
        <button type="submit" disabled={!canSubmit} className="input-submit">
          {submitting ? "Submitting…" : "Submit"}
        </button>
        {showAdvance && (
          <button
            type="button"
            onClick={advance}
            disabled={!advanceEnabled || advancing || busy}
            className="input-advance"
            title={advanceEnabled ? "Run the narrator on queued posts" : advanceReason}
          >
            {advancing ? "Advancing…" : "Advance"}
          </button>
        )}
        <button
          type="button"
          onClick={() => void requestSuggestion()}
          disabled={!activePcRef || suggesting || busy}
          className="input-suggest"
          title="Generate a draft post in the active PC's voice"
        >
          {suggesting ? "Drafting…" : "Suggest a post"}
        </button>
      </div>
      {suggestError && (
        <p className="input-suggest-error" role="alert">
          {suggestError}
        </p>
      )}
      {suggestion && (
        <AuxPanel
          campaignId={campaignId}
          result={suggestion}
          onAccepted={() => {
            setSuggestion(null);
            onTextChange("");
          }}
          onDiscarded={() => setSuggestion(null)}
          onTryAgain={() => {
            setSuggestion(null);
            void requestSuggestion();
          }}
        />
      )}
    </form>
  );
}
