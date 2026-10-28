import { api } from "../../api/client";
import type { SceneKey } from "./campaignTypes";

// Ask the server whether the scene's running summary is now due (#85).
//
// Deliberately NOT awaited by any caller: the player's next action must never
// queue behind a summarization, which is the whole meaning of "non-blocking"
// here. Sent without `force`, so the decision — and the cost — stay on the
// server; a scene short of the threshold answers `refreshed: false` having
// reached no provider, which is why it is cheap enough to fire after every
// transcript write.
//
// Every rejection is swallowed. A missing key, a dead provider, a busy store:
// none is a reason to put a banner over a turn that landed, and the panel's
// own Refresh button reports the failure when the player actually asks.
//
// The `ctxKey` bump is guarded on the reader still being here, like every
// other post-await write in this component: a summary written for the scene
// they just left must not re-read the panel for the scene they are on.
//
// A function rather than a line inside `runStream`, because review caught
// that generated turns are not the only writer: a manual dice roll and a
// check both append narrator posts, so a mechanics-heavy stretch of play
// could cross the threshold repeatedly with nothing ever asking. Named for
// the SIGNAL rather than for one of its consumers, since #84 joined #85 here
// and a name that promised only a summary would be a lie the next reader
// trips over.
// `upto` is REQUIRED, and a negative one means "no boundary was read" — the
// re-read that would have supplied it was retired by a newer select, or the
// reader left. Review caught what the earlier `undefined` fallback did there:
// it sent an UNBOUNDED request, so the one case that most needed the bound —
// a newer turn already in flight — was the case that dropped it, and the fold
// could cover a player post whose reply had not been written yet. That reply
// is an append, so it would stay out of the "current" summary until another
// threshold. Skipping costs nothing: whatever superseded this read is itself
// a transcript write and asks again with a boundary it actually verified.
export function askAfterPost(scope: SceneKey, upto: number, selected: () => SceneKey | null, changed: () => void) {
  const { cid, sid: id } = scope;
  if (upto < 0) return;
  api.refreshRollingSummary(cid, id, false, upto)
    .then((r) => { if (r.refreshed && selected()?.cid === cid && selected()?.sid === id) changed(); })
    .catch(() => { });
  // The scene-break question rides the same signal (#84) — every call site
  // here is already "a post landed, at a boundary I verified", which is
  // exactly when both gates want re-evaluating. Fired separately rather than
  // chained, because neither answer is a precondition for the other and
  // chaining would make a failed summary silently skip the break question.
  // Not awaited and its rejection swallowed, for the summary's reason: no
  // action should fail because a suggestion could not be written.
  api.askSceneBreak(cid, id, false, upto)
    .then((r) => { if (r.asked && selected()?.cid === cid && selected()?.sid === id) changed(); })
    .catch(() => { });
}
