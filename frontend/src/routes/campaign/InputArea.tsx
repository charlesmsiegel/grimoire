import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiScene, PCEntry } from "../../api/campaign";
import { PCSwitcher } from "./PCSwitcher";

interface Props {
  scene: ApiScene | null;
  pcs: PCEntry[];
  activePcRef: string | null;
  onChangePC: (ref: string) => void;
  onSubmit: (text: string) => Promise<void>;
  onAdvance: () => Promise<void>;
  advanceEnabled: boolean;
  advanceReason: string;
  busy: boolean;
}

export function InputArea({
  scene,
  pcs,
  activePcRef,
  onChangePC,
  onSubmit,
  onAdvance,
  advanceEnabled,
  advanceReason,
  busy,
}: Props) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const isMultiPC = (scene?.present_pc_refs.length ?? 0) >= 2;
  const canSubmit = !!activePcRef && text.trim().length > 0 && !submitting && !busy;
  const showAdvance = isMultiPC;

  const submit = useCallback(async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit(text);
      setText("");
      taRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, onSubmit, text]);

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
        onChange={(e) => setText(e.target.value)}
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
      </div>
    </form>
  );
}
