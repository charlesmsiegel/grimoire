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
  onDirect: (text?: string) => Promise<void>;
  advanceEnabled: boolean;
  advanceReason: string;
  onNextSpeaker: () => Promise<void>;
  nextSpeakerEnabled: boolean;
  speakerRoundActive: boolean;
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
  onDirect,
  advanceEnabled,
  advanceReason,
  onNextSpeaker,
  nextSpeakerEnabled,
  speakerRoundActive,
  busy,
}: Props) {
  const [emotion, setEmotion] = useState("neutral");
  const [submitting, setSubmitting] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestion, setSuggestion] = useState<AuxiliaryResult | null>(null);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const [polishInstr, setPolishInstr] = useState<string | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [polishResult, setPolishResult] = useState<AuxiliaryResult | null>(null);
  const [polishError, setPolishError] = useState<string | null>(null);
  const [lastPolishInstr, setLastPolishInstr] = useState<string>("");
  const [lastPolishText, setLastPolishText] = useState<string>("");
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

  const runPolish = useCallback(
    async (snippet: string, instruction: string) => {
      if (polishing || !snippet.trim() || !instruction.trim()) return;
      setPolishing(true);
      setPolishError(null);
      setLastPolishInstr(instruction);
      setLastPolishText(snippet);
      try {
        const result = await auxiliaryApi.editProse(campaignId, snippet, instruction);
        setPolishResult(result);
        setPolishInstr(null);
      } catch (e) {
        setPolishError(e instanceof Error ? e.message : String(e));
      } finally {
        setPolishing(false);
      }
    },
    [campaignId, polishing],
  );

  const isMultiPC = (scene?.present_pc_refs.length ?? 0) >= 2;
  const isPcAbsent = (scene?.present_pc_refs.length ?? 0) === 0;
  const canSubmit = isPcAbsent
    ? text.trim().length > 0 && !submitting && !busy
    : !!activePcRef && text.trim().length > 0 && !submitting && !busy;
  const showAdvance = isMultiPC;

  const submit = useCallback(async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    // Clear the textarea before awaiting the network round-trip so the
    // user sees their submission disappear immediately and can start
    // typing the next message. The captured snapshot is what we POST;
    // if the request fails the entry-box stays cleared (the error is
    // surfaced separately) — matching how Discord/Slack/etc. behave.
    const snapshot = text;
    const snapshotEmotion = emotion;
    onTextChange("");
    setEmotion("neutral");
    taRef.current?.focus();
    try {
      await onSubmit(snapshot, snapshotEmotion);
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, onSubmit, onTextChange, text, emotion]);

  const directSubmit = useCallback(async () => {
    if (submitting || busy) return;
    setSubmitting(true);
    const snapshot = text;
    onTextChange("");
    taRef.current?.focus();
    try {
      await onDirect(snapshot || undefined);
    } finally {
      setSubmitting(false);
    }
  }, [submitting, busy, onDirect, onTextChange, text]);

  const directContinue = useCallback(async () => {
    if (submitting || busy) return;
    setSubmitting(true);
    try {
      await onDirect();
    } finally {
      setSubmitting(false);
    }
  }, [submitting, busy, onDirect]);

  const advance = useCallback(async () => {
    if (!advanceEnabled || advancing || busy) return;
    setAdvancing(true);
    try {
      await onAdvance();
    } finally {
      setAdvancing(false);
    }
  }, [advanceEnabled, advancing, busy, onAdvance]);

  const [requestingNext, setRequestingNext] = useState(false);

  const nextSpeaker = useCallback(async () => {
    if (!nextSpeakerEnabled || requestingNext || busy) return;
    setRequestingNext(true);
    try {
      await onNextSpeaker();
    } finally {
      setRequestingNext(false);
    }
  }, [nextSpeakerEnabled, requestingNext, busy, onNextSpeaker]);

  // Autofocus on initial mount only. The submit() handler refocuses the
  // textarea explicitly after a successful submit; refocusing on every
  // busy→idle (e.g. Regenerate / Undo / Skip) yanked focus off the button
  // the user was about to click.
  useEffect(() => {
    taRef.current?.focus();
  }, []);

  return (
    <form
      className="input-area"
      onSubmit={(e) => {
        e.preventDefault();
        if (isPcAbsent) {
          void directSubmit();
        } else {
          void submit();
        }
      }}
      aria-label="Compose post"
    >
      <div className="input-meta">
        {!isPcAbsent && (
          <PCSwitcher
            pcs={pcs}
            activePcRef={activePcRef}
            onChange={onChangePC}
            campaignId={campaignId}
          />
        )}
        {isPcAbsent && (
          <span className="input-director-hint" aria-live="polite">
            NPC-only scene — directing as narrator.
          </span>
        )}
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
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (isPcAbsent) {
              void directSubmit();
            } else {
              void submit();
            }
          }
        }}
        placeholder={
          isPcAbsent
            ? "Direct the scene..."
            : activePcRef
              ? `Posting as ${pcs.find((p) => p.character_ref === activePcRef)?.name ?? activePcRef}`
              : "Add a PC to begin posting."
        }
        rows={4}
        aria-label="Post body (Enter to submit, Shift+Enter for newline)"
      />
      <div className="input-actions">
        {!isPcAbsent && (
          <ExpressionPicker value={emotion} onChange={setEmotion} disabled={submitting || busy} />
        )}
        <button type="submit" disabled={!canSubmit} className="input-submit">
          {submitting ? "Submitting…" : isPcAbsent ? "Direct" : "Submit"}
        </button>
        {isPcAbsent && (
          <button
            type="button"
            onClick={() => void directContinue()}
            disabled={submitting || busy}
            className="input-continue"
            title="Continue the scene without specific direction"
          >
            {submitting ? "Continuing…" : "Continue"}
          </button>
        )}
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
        {speakerRoundActive && (
          <button
            type="button"
            onClick={nextSpeaker}
            disabled={!nextSpeakerEnabled || requestingNext || busy}
            className="input-next-speaker"
            title="Let the next character speak"
          >
            {requestingNext ? "Calling…" : "Next"}
          </button>
        )}
        {!isPcAbsent && (
          <>
            <button
              type="button"
              onClick={() => void requestSuggestion()}
              disabled={!activePcRef || suggesting || busy}
              className="input-suggest"
              title="Generate a draft post in the active PC's voice"
            >
              {suggesting ? "Drafting…" : "Suggest a post"}
            </button>
            <button
              type="button"
              onClick={() => setPolishInstr("")}
              disabled={!text.trim() || polishing || busy}
              className="input-polish"
              title="Polish or rewrite the current draft"
            >
              {polishing ? "Polishing…" : "Polish"}
            </button>
          </>
        )}
      </div>
      {polishInstr !== null && (
        <form
          className="input-polish-form"
          onSubmit={(e) => {
            e.preventDefault();
            void runPolish(text, polishInstr);
          }}
        >
          <input
            type="text"
            value={polishInstr}
            onChange={(e) => setPolishInstr(e.target.value)}
            placeholder="How should this be polished? (e.g. tighten prose, fix grammar)"
            aria-label="Polish instruction"
            autoFocus
          />
          <button type="submit" disabled={polishing || !polishInstr.trim() || !text.trim()}>
            {polishing ? "Polishing…" : "Polish"}
          </button>
          <button type="button" onClick={() => setPolishInstr(null)} disabled={polishing}>
            Cancel
          </button>
        </form>
      )}
      {suggestError && (
        <p className="input-suggest-error" role="alert">
          {suggestError}
        </p>
      )}
      {polishError && (
        <p className="input-polish-error" role="alert">
          {polishError}
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
      {polishResult && (
        <AuxPanel
          campaignId={campaignId}
          result={polishResult}
          onAccepted={(response) => {
            if (response.text !== undefined) onTextChange(response.text);
            setPolishResult(null);
          }}
          onDiscarded={() => setPolishResult(null)}
          onTryAgain={() => {
            setPolishResult(null);
            if (lastPolishText && lastPolishInstr) {
              void runPolish(lastPolishText, lastPolishInstr);
            }
          }}
        />
      )}
    </form>
  );
}
