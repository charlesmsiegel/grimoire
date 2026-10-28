import type { Dispatch, SetStateAction } from "react";
import type { SceneAbsorb } from "../../api/client";
import {
  type IncomingRef
} from "../../api/client";
import { CalendarConfig } from "../../components/CalendarConfig";
import { ChangesPanel } from "../../components/ChangesPanel";
import { CompositionPanel } from "../../components/CompositionPanel";
import { CampaignCover } from "../../components/CoverPanel";
import { IncomingReview } from "../../components/IncomingReview";
import MechanicsConfig from "../../components/MechanicsConfig";
import {
  type TranscriptActions
} from "../../components/play/TranscriptPost";
import { ResponseTargetsPicker } from "../../components/ResponseTargetsPicker";
import type { SceneReview } from "../../components/review/useSceneReview";
import { SceneTrackerPanel } from "../../components/tracker/SceneTrackerPanel";
import type { StableHandlers } from "./campaignShared";

// The compounding silent failure this closes: one slow-but-healthy extraction
// can eat the whole shared budget, and the trailing steps then come back with
// nothing to show — which reads exactly like a model that had nothing to
// suggest. Named up front, before the reviewer reads the (short) edit list.
export function CampaignPanels({
  absorb, focus, showCalendar, cid, showMechanics, readModuleBound, showStyle, activeId, refreshSavedScene, showCover, showTracker, transcriptActions, showChanges, showIncoming, reviewRef, pinEpoch, setResolvedEpoch, showComposition, resolvedEpoch, setPinEpoch, setReviewRef, setShowIncoming, review
}: {
  absorb: SceneAbsorb | null;
  focus: boolean;
  showCalendar: boolean;
  cid: string;
  showMechanics: boolean;
  readModuleBound: (reset?: boolean) => void;
  showStyle: boolean;
  activeId: string | null;
  refreshSavedScene: (id: string, operation: string) => void;
  showCover: boolean;
  showTracker: boolean;
  transcriptActions: StableHandlers<TranscriptActions>;
  showChanges: boolean;
  showIncoming: boolean;
  reviewRef: IncomingRef | null;
  pinEpoch: number;
  setResolvedEpoch: Dispatch<SetStateAction<number>>;
  showComposition: boolean;
  resolvedEpoch: number;
  setPinEpoch: Dispatch<SetStateAction<number>>;
  setReviewRef: Dispatch<SetStateAction<IncomingRef | null>>;
  setShowIncoming: Dispatch<SetStateAction<boolean>>;
  review: SceneReview;
}) {
  return (
    !absorb && (<>
      {/* Every one of these eight is opened AND closed from the scene bar,
              which focus mode does not render — so one left open when focus
              starts would be a panel above the transcript with no control that
              can shut it. They keep their state and come back with the bar. The
              inspector below is deliberately not in this list: its toggle lives
              in the composer, which focus mode keeps. */}
      {!focus && showCalendar && (
        <div className="panel-slot">
          <CalendarConfig scope={{ kind: "campaign", id: cid }} />
        </div>
      )}
      {!focus && showMechanics && (
        <div className="panel-slot">
          {/* Guarded against a save that settles after the reader has moved
                  on: the callback this panel holds is the one it was handed, so
                  it can name a campaign that is no longer on screen. */}
          {/* No cid check on the callback. A save settling after the reader
                  moved on does fire a stale `onChanged`, but `readModuleBound`
                  keys off `liveCid`, so it reads and commits the campaign on
                  SCREEN -- harmless, and the correct answer. Filtering by the
                  saved cid would only skip a request MechanicsConfig's own
                  `load()` has already made anyway, while masking that keying in
                  any test (Codex review round 2). */}
          {/* `key` remounts it per campaign. Its own save is a PUT followed
                  by a re-read, both against the `cid` its render captured, and
                  every commit in that chain is unguarded -- so a save started in
                  one campaign and settling after a switch would write the old
                  campaign's module into the new campaign's editor, and the next
                  Save would then persist that selection to the wrong campaign.
                  A fresh instance has no state for the stale chain to land in
                  (Codex review round 2). */}
          <MechanicsConfig key={cid} cid={cid} onChanged={() => readModuleBound()} />
        </div>
      )}
      {!focus && showStyle && (
        <div className="panel-slot">
          <ResponseTargetsPicker scope="campaign" cid={cid}
            onChanged={() => { if (activeId) refreshSavedScene(activeId, "Response targets saved"); }} />
        </div>
      )}
      {!focus && showCover && (
        <div className="panel-slot">
          <CampaignCover cid={cid} />
        </div>
      )}
      {/* Keyed by scene as well as campaign: it holds one scene's layer, and
              a rename or a switch must not show it another scene's fields. */}
      {!focus && showTracker && activeId && (
        <div className="panel-slot">
          <SceneTrackerPanel key={`${cid}:${activeId}`} cid={cid} sid={activeId}
            onChanged={transcriptActions.refreshTracker} />
        </div>
      )}
      {!focus && showChanges && <ChangesPanel cid={cid} />}
      {/* Keyed by cid for the reason MechanicsConfig is: this route keeps
              its instance across a campaign switch, so an unkeyed panel would
              show one campaign's pending list while another is on screen. */}
      {!focus && showIncoming && (
        <IncomingReview key={cid} cid={cid} focus={reviewRef} refreshKey={pinEpoch}
          onResolved={() => setResolvedEpoch((n) => n + 1)} />
      )}
      {/* Keyed by cid for `IncomingReview`'s reason: this route keeps its
              instance across a campaign switch, so an unkeyed panel would show
              one campaign's composition while another is on screen. */}
      {!focus && showComposition && (
        <CompositionPanel key={cid} cid={cid} refreshKey={resolvedEpoch}
          onPinned={() => setPinEpoch((n) => n + 1)}
          // Re-boxed rather than passed through: the panel hands back the
          // same `row.ref` object each time, and `IncomingReview`'s focus
          // effect keys on identity -- so without this, returning to a row
          // already reviewed once would not re-select it.
          onReview={(ref) => { setReviewRef({ ...ref }); setShowIncoming(true); }} />
      )}
      {review.editFailures.length > 0 && (
        <div className="mechanics-notice">
          <p>{review.editFailures.length} change{review.editFailures.length === 1 ? "" : "s"} did not apply</p>
          {review.editFailures.map((f) => (
            <p className="field-hint" key={f.id}>{f.label}: {f.reason} ({f.kind})</p>
          ))}
          <button className="subtle" onClick={review.dismissFailures}>Dismiss</button>
        </div>
      )}
      {review.publishFailures.length > 0 && (
        <div className="mechanics-notice">
          <p>{review.publishFailures.length} record{review.publishFailures.length === 1 ? "" : "s"} could
            not be published to the world library — saved to this campaign instead</p>
          {review.publishFailures.map((f) => (
            <p className="field-hint" key={`${f.kind}/${f.id}`}>{f.label}: {f.reason}</p>
          ))}
          {/* Spec 9.4: the reviewer is told how to finish the job, not only
                  that it stopped. Rendered outside the review (`!absorb`), so
                  it is still here once the save has closed it. */}
          <p className="field-hint">
            {review.publishFailures.length === 1
              ? "It is kept in this campaign. Resolve the conflict, then publish it with Publish to library in the record's editor."
              : "Each is kept in this campaign. Resolve each conflict, then publish it with Publish to library in that record's editor."}
          </p>
          <button className="subtle" onClick={review.dismissPublishFailures}>Dismiss</button>
        </div>
      )}
    </>)
  );
}
