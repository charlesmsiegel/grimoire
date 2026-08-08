import { useEffect, useState } from "react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import type { SceneDraft } from "./sceneDraft";

/** Mode → pick → confirm → create. Props are unchanged from the
 *  commit-on-click version, so CampaignView's usage is untouched. */
export function NewSceneChooser({ cid, afterSid, ready, onClose, onCreated }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  ready: boolean;
  onClose: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  // scene mode is picked first; nothing is fetched until then
  const [mode, setMode] = useState<"pc" | "offscreen" | null>(null);
  const [draft, setDraft] = useState<SceneDraft | null>(null);
  // Bumped every time a new draft is set. A late `onPicked` (an extraction
  // that resolves after the user already clicked a card) can replace `draft`
  // while SceneConfirmForm is already mounted; without a changing `key` React
  // reuses the existing instance and its useState initializers never re-run,
  // so the pane ends up mixing controls from the stale draft with state from
  // the new one. Keying on this counter forces a remount on every replacement.
  const [draftGen, setDraftGen] = useState(0);
  // A warning from the picker (an extraction that failed) has to outlive the
  // picker, which unmounts the moment a draft is emitted.
  const [notice, setNotice] = useState<string | null>(null);

  // The confirm form's create sequence is several writes long, and unmounting
  // it does not cancel them — a dismissal mid-sequence would strand a scene
  // nobody is told about. So the form reports when it is writing and the
  // orchestrator refuses to close.
  const [writing, setWriting] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !writing) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, writing]);

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={() => { if (!writing) onClose(); }}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>

        {mode === null ? (
          <>
            <div className="role">What kind of scene?</div>
            <button className="chooser-card" onClick={() => setMode("pc")}>
              <span className="chooser-card-title">With your PC</span>
              <span className="chooser-card-premise">Your player character takes part.</span>
            </button>
            <button className="chooser-card" onClick={() => setMode("offscreen")}>
              <span className="chooser-card-title">Offscreen (NPCs only)</span>
              <span className="chooser-card-premise">
                What happens away from your PC — NPC plans, motivations, and events you don't witness.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={onClose}>Cancel</button>
            </div>
          </>
        ) : draft === null ? (
          <SceneIdeaPicker cid={cid} afterSid={afterSid} ready={ready}
                           pcless={mode === "offscreen"}
                           onPicked={(d, warning) => {
                             setDraft(d); setNotice(warning ?? null);
                             setDraftGen((n) => n + 1);
                           }}
                           onCancel={onClose} />
        ) : (
          <SceneConfirmForm key={draftGen} cid={cid} draft={draft} notice={notice}
                            onBack={() => setDraft(null)} onCancel={onClose} onCreated={onCreated}
                            onWriting={setWriting} />
        )}
      </div>
    </div>
  );
}
