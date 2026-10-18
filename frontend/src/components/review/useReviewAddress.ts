// Which of a scene's two addresses the screen is at, kept in step with what
// it shows.
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import type { SceneReview } from "./useSceneReview";

const scenePath = (cid: string, sid: string) =>
  `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;

/** Move the URL to `/wrap-up` while this scene's review is on screen, and back
 *  to the scene when it closes there.
 *
 *  The review replaces the play view whichever of the two addresses it was
 *  reached at, but only `/wrap-up` says so: the rail's Wrap-up row lights for
 *  it and a link to it can be shared. End scene, and a stored review adopted on
 *  arrival, opened it at the scene's own URL -- so the reader was judging a
 *  review while the rail said Scenes. Replaced rather than pushed, because it
 *  is one place called by its name, and Back should not step between the two.
 *
 *  The other direction only on the CLOSE: a save or a Discard at `/wrap-up`
 *  would otherwise land on "Nothing to wrap up" for the review just finished,
 *  when the reader's next question is the scene. A `/wrap-up` arrived at with
 *  nothing waiting -- a bookmark, a link to a review somebody already decided
 *  -- is left alone: saying so is what that page is for.
 *
 *  Only for the scene on screen. A review outlives a switch to another scene
 *  (`absorbSid`), and the address of the scene being read is not the review's.
 */
export function useReviewAddress({ cid, activeId, wrapUp, review }: {
  cid: string; activeId: string | null; wrapUp: boolean; review: SceneReview;
}) {
  const navigate = useNavigate();
  const showing = activeId !== null && review.absorb !== null && review.absorbSid === activeId;
  // Which scene's review was on screen at the last pass, so a close is told
  // apart from arriving at another scene with a review still open elsewhere.
  const shownFor = useRef<string | null>(null);
  useEffect(() => {
    // Closed: no review at all now, not merely this scene's no longer on top.
    const closedHere = shownFor.current !== null && shownFor.current === activeId
      && review.absorb === null;
    shownFor.current = showing ? activeId : null;
    if (!activeId) return;
    if (showing && !wrapUp) {
      void navigate(`${scenePath(cid, activeId)}/wrap-up`, { replace: true });
    } else if (closedHere && wrapUp && !review.absorbing) {
      void navigate(scenePath(cid, activeId), { replace: true });
    }
  }, [cid, activeId, showing, wrapUp, review.absorb, review.absorbing, navigate]);
}
