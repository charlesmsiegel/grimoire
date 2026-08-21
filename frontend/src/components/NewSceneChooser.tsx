import { useEffect, useState } from "react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import { SceneImport } from "./SceneImport";
import type { SceneDraft } from "./sceneDraft";
import { useSceneSuggestions } from "./useSceneSuggestions";

/** Mode → pick → confirm → create. Props are unchanged from the
 *  commit-on-click version, so CampaignView's usage is untouched. */
export function NewSceneChooser({ cid, afterSid, ready, onClose, onCreated }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  ready: boolean;
  /** `createdSid` is set only when a scene was actually created before this
   *  dismissal (a soft-failure "salvaged" scene abandoned via Escape/backdrop
   *  rather than "Continue to scene") -- see `salvagedSid` below. */
  onClose: (createdSid?: string) => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  // scene mode is picked first; nothing is fetched until then. "import" is
  // the one mode that never reaches the picker or the confirm form: an
  // imported scene brings its own title, cast, moment and transcript, so
  // there is nothing to suggest and nothing to open it with (#92).
  const [mode, setMode] = useState<"pc" | "offscreen" | "import" | null>(null);
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

  // Set once SceneConfirmForm salvages a soft failure into a real, created
  // scene (see its `salvaged` state). `writing` goes false at that point, so
  // Escape and the backdrop can dismiss the modal from here -- and unlike
  // every other dismissal, a scene now exists that CampaignView's scene list
  // does not know about yet. Read at dismiss time so `onClose` can report it;
  // CampaignView reloads its list only when this is non-null, which is
  // narrower than reloading on every dismissal (most dismissals wrote
  // nothing).
  const [salvagedSid, setSalvagedSid] = useState<string | null>(null);

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
  // SceneIdeaPicker only ever mounted post-mode before this move). Once a
  // PLAYABLE mode is set it stays set until a `cid` change resets it below, so
  // this only ever toggles the hook's `ready` from false to true, never back.
  //
  // "import" is deliberately not one of them: suggestions are an LLM ranking,
  // and an imported scene has a title, a cast and a transcript of its own, so
  // picking that card must not spend a call on ideas nothing will read (#92).
  const playable = mode === "pc" || mode === "offscreen";
  const suggestionsState = useSceneSuggestions(cid, afterSid, ready && playable, mode === "offscreen");

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
    // `writing` must reset here too. SceneConfirmForm's own create() sequence
    // stops issuing writes once its `live` ref notices this same switch (see
    // its comment) -- but every `setWriting(false)` on that abandoned path is
    // now guarded by that same `live` check and so never runs. Left alone,
    // `writing` would stay stuck true forever, and `dismiss()` below refuses
    // Escape, the backdrop, and every Cancel button while it is true -- for
    // the NEW campaign's freshly reset (mode-select) chooser, not just the
    // old one, since `writing` is not itself reset by anything else. That
    // would permanently lock the modal until a whole new create cycle
    // happened to flip it back through a live component (Critical, review).
    setWriting(false);
    // A soft failure may have salvaged a real scene (`salvagedSid`) that the
    // reader never dismissed before switching campaigns. There is no safe
    // way to report it from here the way `dismiss()` does: `onClose` (like
    // `cid`) is already bound to the NEW campaign by the time this branch
    // runs -- CampaignView redefines both together in the same render that
    // changed `cid`, so there is no live closure left pointing at the
    // campaign the reader just left. Even if there were, CampaignView's own
    // `installScenes` refuses to install a scene list for any campaign other
    // than the one it is currently showing, by design, so a call here could
    // not make that campaign's rail reflect the scene anyway. The scene is
    // not lost -- it exists on the backend and surfaces normally the next
    // time the reader navigates back to that campaign and it does its own
    // mount read -- it is just not pushed there proactively (Important,
    // review).
    setSalvagedSid(null);
  }

  // The single path every dismissal (Cancel, Escape, the backdrop) goes
  // through, so `salvagedSid` is reported consistently rather than only from
  // the two sites (Escape/backdrop) that can still fire once a scene is
  // salvaged -- the other dismissals just always carry `null`.
  function dismiss() {
    if (writing) return;
    onClose(salvagedSid ?? undefined);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") dismiss(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, writing, salvagedSid]);

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={dismiss}>
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
            <button className="chooser-card" onClick={() => setMode("import")}>
              <span className="chooser-card-title">Import a transcript</span>
              <span className="chooser-card-premise">
                A scene you already have — a grimoire scene file, or a chapter of a Markdown
                export — read in as a scene here.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={dismiss}>Cancel</button>
            </div>
          </>
        ) : mode === "import" ? (
          /* No draft, no picker, no confirm form: the file IS the draft, and
             `SceneImport` runs its own read → review → import over it. Back
             returns to the mode cards rather than to a picker that was never
             shown. `onCreated` takes no initial prompt — an imported scene
             opens on a transcript that is already written. */
          <SceneImport cid={cid} onBack={() => setMode(null)} onCancel={dismiss}
                       onImported={(sid) => onCreated(sid)} onWriting={setWriting} />
        ) : draft === null ? (
          <SceneIdeaPicker cid={cid} afterSid={afterSid} ready={ready}
                           pcless={mode === "offscreen"}
                           direction={direction} onDirectionChange={setDirection}
                           {...suggestionsState}
                           onPicked={(d, warning) => {
                             setDraft(d); setNotice(warning ?? null);
                             setDraftGen((n) => n + 1);
                           }}
                           onCancel={dismiss} />
        ) : (
          <SceneConfirmForm key={draftGen} cid={cid} draft={draft} notice={notice} ready={ready}
                            onBack={() => setDraft(null)} onCancel={dismiss} onCreated={onCreated}
                            onWriting={setWriting} onSalvaged={setSalvagedSid} />
        )}
      </div>
    </div>
  );
}
