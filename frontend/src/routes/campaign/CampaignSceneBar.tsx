import type { Dispatch, SetStateAction } from "react";
import { Link } from "react-router-dom";
import type { SceneAbsorb } from "../../api/client";
import { SceneMenu } from "../../components/play/SceneMenu";
import type { SceneReview } from "../../components/review/useSceneReview";
import { SceneRenameInput } from "./SceneRenameInput";

export function CampaignSceneBar({
  absorb, name, renamingScene, setRenamingScene, commitSceneRename, review, activeId, absorbTitle, focus, cid, noteCount, showNotes, setShowNotes, newScene, showChanges, setShowChanges, showIncoming, setShowIncoming, showComposition, setShowComposition, showMechanics, setShowMechanics, showCalendar, setShowCalendar, showStyle, setShowStyle, showCover, setShowCover, showTracker, setShowTracker, openPanel, closePanels, activeClosed, fail, busy, rolling, streamingId, sceneLocked
}: {
  absorb: SceneAbsorb | null;
  name: string;
  renamingScene: { id: string; title: string; } | null;
  setRenamingScene: Dispatch<SetStateAction<{ id: string; title: string; } | null>>;
  commitSceneRename: () => Promise<void>;
  review: SceneReview;
  activeId: string | null;
  absorbTitle: string;
  focus: boolean;
  cid: string;
  noteCount: number;
  showNotes: boolean;
  setShowNotes: Dispatch<SetStateAction<boolean>>;
  newScene: () => void;
  showChanges: boolean;
  setShowChanges: Dispatch<SetStateAction<boolean>>;
  showIncoming: boolean;
  setShowIncoming: Dispatch<SetStateAction<boolean>>;
  showComposition: boolean;
  setShowComposition: Dispatch<SetStateAction<boolean>>;
  showMechanics: boolean;
  setShowMechanics: Dispatch<SetStateAction<boolean>>;
  showCalendar: boolean;
  setShowCalendar: Dispatch<SetStateAction<boolean>>;
  showStyle: boolean;
  setShowStyle: Dispatch<SetStateAction<boolean>>;
  showCover: boolean;
  setShowCover: Dispatch<SetStateAction<boolean>>;
  showTracker: boolean;
  setShowTracker: Dispatch<SetStateAction<boolean>>;
  openPanel: { label: "Changes" | "World updates" | "Composition" | "Mechanics" | "Calendar" | "Response" | "Cover" | "Tracker"; };
  closePanels: () => void;
  activeClosed: { sid: string; title: string; } | null;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  busy: boolean;
  rolling: boolean;
  streamingId: string | null;
  sceneLocked: boolean;
}) {
  return (
    absorb ? (
      <div className="scene-actions review-actions">
        <span className="eyebrow">{name}</span>
        {/* Rename lives here during a review, and is the one scene control
              that does. The rest act on a transcript this screen is not
              showing, but a scene's id is derived from its title — so a rename
              mints a new id, and `renameScene` migrates the open review's
              `absorbSid` and every staged edit's scene ref onto it. Dropping
              the control with the scene head would leave that migration with no
              way to be reached, and take with it the only chance to fix a title
              at the moment you are reading what the scene actually contained. */}
        {renamingScene ? (
          <SceneRenameInput value={renamingScene.title}
            onChange={(title) => setRenamingScene({ id: renamingScene.id, title })}
            onCommit={() => { commitSceneRename().catch((err: unknown) => fail(err, false)); }}
            onCancel={() => setRenamingScene(null)} />
        ) : (
          <button className="review-absorbing" aria-label="Rename scene"
            title="Rename this scene"
            onClick={() => setRenamingScene({
              id: review.absorbSid ?? activeId ?? "",
              title: absorbTitle
            })}>
            Absorbing {absorbTitle}
          </button>
        )}
        <span className="header-spacer" />
        {/* The audit's verdict, and deliberately not a count of proposals:
              the column already carries "18 edits · 14 approved · 1 rejected ·
              3 left" and the footer the tally again. This is the one fact about
              the absorb that neither of them states. */}
        <span className="review-audit">Audit {absorb.mechanics.status}</span>
      </div>
    ) : focus ? null : (
      <div className="scene-actions">
        {/* The campaign's name is a link home, because the hub is now what
              "this campaign" means and the scene is one thing inside it. */}
        <Link className="eyebrow scene-home" to={`/campaigns/${cid}`}
          aria-label={`${name} — back to the campaign`}>
          <span aria-hidden>‹ </span><span>{name}</span>
        </Link>
        <span className="header-spacer" />
        {/* Ledger, Timeline, Costs and Sheets used to sit here as well.
              They are rail rows and hub links now, and having them in three
              places at once was the "second navigation surface" the rail was
              built to retire -- the reader saw the same four destinations in
              the chrome, on the page, and in the palette. What is left on this
              bar is what is about the SCENE in front of you; what belongs to
              the campaign moved to the hub. */}
        {/* Eight panels behind one control (`SceneMenu` says why). What
              stays outside it is what the bar is FOR: the way back to the
              campaign, and End scene. */}
        {/* The director-notes toggle. On the bar rather than in the menu:
              it is a way of LOOKING at what is on screen, and everything in
              the menu opens something over the transcript. It renders only
              where there is a note to show, so a scene played without them
              never grows a control for them. */}
        {noteCount > 0 && (
          <button className="scene-action" aria-pressed={showNotes}
            onClick={() => setShowNotes((v) => !v)}>
            {showNotes ? "Hide" : "Show"} director notes · {noteCount}
          </button>
        )}
        <SceneMenu onNewScene={newScene} panels={[
          { label: "Changes", open: showChanges, toggle: () => setShowChanges((v) => !v) },
          { label: "World updates", open: showIncoming, toggle: () => setShowIncoming((v) => !v) },
          { label: "Composition", open: showComposition, toggle: () => setShowComposition((v) => !v) },
          { label: "Mechanics", open: showMechanics, toggle: () => setShowMechanics((v) => !v) },
          { label: "Calendar", open: showCalendar, toggle: () => setShowCalendar((v) => !v) },
          { label: "Response", open: showStyle, toggle: () => setShowStyle((v) => !v) },
          { label: "Cover", open: showCover, toggle: () => setShowCover((v) => !v) },
          { label: "Tracker", open: showTracker, toggle: () => setShowTracker((v) => !v) },
        ]} />
        {/* The one control the menu cannot carry. A panel is open ABOVE the
              transcript and the button that closes it is behind a disclosure,
              so closing costs two clicks and a hunt for which row has the tick.
              It renders only while something is open, which is exactly when it
              is worth the width. */}
        {openPanel && (
          <button className="scene-action" onClick={closePanels}>
            Close {openPanel.label}
          </button>
        )}
        {/* `busy` is not the whole of "a turn can still write here": it clears
              when the socket dies, and the backend's shielded abort write lands
              seconds later — which is the window `streamingId` covers. Absorb
              inside it and the chronicle summarises a transcript the partial
              has not reached yet, then the partial lands underneath a scene
              already marked absorbed. That one does not come back: the review
              is committed against a transcript that no longer matches (#95). */}
        {/* `settlesScene`: a Discard still on its way to the server holds the
              scene for the play controls above, and End scene is the one
              operation that waits it out instead of being refused by it (see
              `endScene`). Disabling it here would make the reader click twice
              for a wait the code already does.
              `streamingId` is spelled out again rather than left to
              `sceneLocked`, because that waiver must not reach it: the two
              halves of `sceneLocked` are a review holding the scene, which a
              Discard on THIS scene is and which End scene waits out, and the
              shielded-abort window above, which it is not and which End scene
              must never be pressed inside. Folded together, a Discard settling
              here would open the #95 door. */}
        {/* Not on a closed branch: a sibling's absorb closed it, and the
              server refuses to absorb it (`branch_closed`). */}
        {!activeClosed && (
          <button className="scene-action end" onClick={() => { review.endScene().catch((err: unknown) => fail(err, false, "review")); }}
            disabled={!activeId || review.absorbing || busy || rolling
              || activeId === streamingId
              || (sceneLocked && !review.settlesScene(activeId))}>
            {review.absorbing ? "Ending…" : "End scene"}
          </button>
        )}
        {/* The way out of an absorb that is still running (#396). A review
              holds the scene against play for as long as it runs and
              `absorb_budget = 0` means nothing bounds that, so without this a
              wedged End scene locks the scene until the process restarts --
              and until the review lands there is no panel, and so no Cancel.
              The same argument the panel's Cancel already carries for a wedged
              retry, one step earlier. */}
        {review.absorbing && (
          <button className="scene-action" onClick={() => void review.stopAbsorb()}>
            Stop
          </button>
        )}
        {/* A review that is on disk and unusable (#396). The scene was played
              on after the absorb landed -- appended, cut, retconned -- and the
              stored review summarises posts that are no longer there. Saying so
              here is what lets the reader re-run instead of opening a panel
              whose save is going to be refused; the notice is dismissible
              because it is information, not a state to be stuck in. */}
        {review.staleReview && (
          <p className="field-hint" role="status">
            The scene changed after its review was prepared
            {" "}({review.staleReview.prepared_posts} → {review.staleReview.current_posts}
            {" "}posts). End the scene again to review what is there now.
            {" "}<button className="subtle" onClick={review.dismissStale}>Dismiss</button>
          </p>
        )}
      </div>
    )
  );
}
