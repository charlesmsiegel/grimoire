/** Where a completion-notification tap lands.
 *
 *  The Android shell knows the campaign and the scene's stable IDENTITY, not
 *  its id -- an id moves when a scene is renamed, and a notification can sit
 *  unread for a long time, so a link built from one would open a route that
 *  404s or, worse, a scene that has since taken that id. This resolves the
 *  identity and replaces itself with the real route.
 *
 *  Falls back to the campaign rather than an error page: a player who tapped
 *  a notification wants to be somewhere useful, not to be told their scene
 *  could not be found. But it says why when it gets there -- the campaign's
 *  page reads `openedFrom` -- because landing on a campaign's front door
 *  without a word, from a notification about a scene, reads as the tap having
 *  gone to the wrong place. `gone` and `busy` are different things to say: the
 *  first scene is not there, the second one is and could not be read.
 */
/** Why a notification tap was sent to its campaign instead of its scene. */
export type OpenedFrom = "gone" | "busy" | "unreadable";
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import { PlainShell } from "../components/PageShell";
import { encodeSegment } from "../urlSegment";

export default function OpenScene() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const cid = params.get("campaign") ?? "";
  const identity = params.get("identity") ?? "";

  useEffect(() => {
    let alive = true;
    if (!cid) { void navigate("/", { replace: true }); return; }
    void (async () => {
      // `scene_busy`-style 409s from this lookup are RETRYABLE and a 404 is
      // not, and folding them together sent a tap to the campaign because a
      // sharing violation happened to land on that read. The route reports an
      // unreadable header as `busy` precisely so a caller can tell the two
      // apart; the fallback is for a scene that is genuinely gone.
      let why: OpenedFrom = "gone";
      for (let tries = 0; ; tries++) {
        try {
          const r = await api.sceneByIdentity(cid, identity);
          if (alive) {
            void navigate(`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(r.id)}`,
                     { replace: true });
          }
          return;
        } catch (err) {
          const busy = err instanceof ApiError && err.kind === "busy";
          // Bounded, and short: this is a waiting screen the player is looking
          // at, so a lookup that keeps saying "busy" has to end somewhere --
          // and the campaign is a useful place to be.
          if (!busy || tries >= 2) {
            why = busy ? "busy" : err instanceof ApiError && err.status === 404 ? "gone" : "unreadable";
            break;
          }
          await new Promise((r) => setTimeout(r, 150 * (tries + 1)));
          if (!alive) return;
        }
      }
      if (alive) {
        void navigate(`/campaigns/${encodeSegment(cid)}`, { replace: true, state: { openedFrom: why } });
      }
    })();
    return () => { alive = false; };
  }, [cid, identity, navigate]);

  // Said, not blank: the lookup can take a retry or three, and an empty page
  // under the chrome looked like the tap had opened nothing.
  return (
    <PlainShell>
      <div className="page-wide view-anim">
        <p className="field-hint" role="status">Opening the scene…</p>
      </div>
    </PlainShell>
  );
}
