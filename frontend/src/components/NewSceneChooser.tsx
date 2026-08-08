import { useEffect, useState } from "react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import type { SceneDraft } from "./sceneDraft";
import { useSceneSuggestions } from "./useSceneSuggestions";

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

  // Lifted out of SceneIdeaPicker (issue #319): that component unmounts the
  // instant a draft is picked, and again on Back (which clears `draft`
  // below). While this hook lived inside it, either unmount threw the
  // in-flight ranking away, and Back's remount re-ran the hook's mount
  // effect at `rank=true` -- a fresh, expensive, re-shufflable LLM call for
  // what the user experiences as "go back", discarding the typed direction
  // with it. Living here, the hook survives both: Back only swaps which pane
  // is shown. `direction` moves up for the same reason -- it has to survive
  // the picker unmounting on Back too.
  const [direction, setDirection] = useState("");
  // Nothing should fetch before a mode is chosen (unchanged behavior --
  // SceneIdeaPicker only ever mounted post-mode before this move). Once
  // `mode` is set it stays set until a `cid` change resets it below, so this
  // only ever toggles the hook's `ready` from false to true, never back.
  const suggestionsState = useSceneSuggestions(cid, afterSid, ready && mode !== null, mode === "offscreen");

  // CampaignView reuses this component across a `cid` navigation -- it stays
  // mounted, `chooserOpen` is untouched by the switch, so without an explicit
  // reset a draft picked in campaign A survives into campaign B and Create
  // would send A's title/location/cast/greeting id there. Adjusting state
  // during render (the documented React pattern for "reset state when a prop
  // changes") means the stale draft never gets a chance to paint against the
  // new cid, unlike resetting from an effect. `draftGen` only ever moves
  // forward here, same as `onPicked` already does -- never fed a fixed value
  // that could later collide with a key SceneConfirmForm has already used.
  const [seenCid, setSeenCid] = useState(cid);
  if (cid !== seenCid) {
    setSeenCid(cid);
    setMode(null);
    setDraft(null);
    setNotice(null);
    setDraftGen((n) => n + 1);
    // Direction now lives here rather than inside SceneIdeaPicker, so it no
    // longer resets for free when the picker unmounts on a `mode` reset --
    // a campaign switch must not leave campaign A's typed steer sitting in
    // campaign B's box. (`suggestionsState` itself needs no explicit reset:
    // `mode` going back to null drops the hook's `ready` argument to false,
    // and `cid` changing gives `run` a new identity, so the mount effect
    // re-fires and fetches fresh once a mode is chosen again.)
    setDirection("");
  }

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
                           direction={direction} onDirectionChange={setDirection}
                           {...suggestionsState}
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
