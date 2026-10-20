"""A review is built from one captured scene, epoch, transcript view and membership state."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from pydantic import BaseModel

from ... import store
from ...store.absorb import membership as absorb_membership
from .. import streaming
from ..common import _require_scene
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


def _already_absorbed(scene: dict) -> bool:
    """Whether THIS scene was absorbed, read from its own frontmatter.

    Deliberately not `sid in chronicle`: scene numbers are derived from the files
    on disk and `delete_scene` leaves the chronicle entry behind, so deleting the
    highest-numbered absorbed scene and remaking it under the same title hands the
    new scene the same id. A chronicle lookup would then refuse to absorb a
    brand-new scene. `done` is written only by mark_absorbed, into the scene file
    itself, so a recycled id starts clean."""
    return str(scene.get("meta", {}).get("done", "")).lower() == "true"


def _absorb_snapshot(cid: str, sid: str,
                     identity: str | None) -> tuple[int, dict, list[dict]]:
    """The scene, its commit epoch and its final tracked state as of one
    instant, under one lock hold.

    Split out of `post_absorb` so the whole of it is one critical section.
    Raises `_require_scene`'s 404 like any other handler code.

    Taken in the HANDLER and handed to the run, never read when the result is
    collected. Detached, those two moments are minutes apart, and an epoch read
    at collection would date the response rather than the snapshot -- letting a
    review built from pre-save state pass its own supersession check, which is
    exactly what #271 warns against. `_already_absorbed` is judged from this
    same snapshot for the other half of the pair: it has to raise its 409
    before a token is spent.

    `identity` is the one the reservation captured. Checked here rather than
    trusted, because a scene deleted and replaced between the reservation and
    this read lands on the same `sid` -- ids are recycled by design -- and the
    review would then be built from the replacement's transcript and published
    onto it.

    The tracked state is the latest `ok` tracker record at the transcript this
    review is built from, read in the same hold so a turn landing in between
    cannot hand absorb a state the transcript it reads does not reach.
    """
    with store.locks.campaign_lock(cid):
        scene = _require_scene(cid, sid)
        if streaming._scene_moved(cid, sid, identity):
            raise HTTPException(status_code=404, detail="scene not found")
        return store.commits.scene_epoch(cid, sid), scene, _absorb_tracked(cid, sid)


def _absorb_tracked(cid: str, sid: str) -> list[dict]:
    """The scene tracker's final state as the narrator reads it -- every value,
    private ones marked -- for absorb, which writes canonical state rather than
    speaking for anyone. Characters with nothing recorded are left out; one
    who left the scene keeps their last state, marked as having left (a
    snapshot can still hold someone present who left after the last tracked
    post, so presence is taken from the cast as it stands). Each line carries
    the actor's `id` in the "Present:" line's `<kind>/<id>` form: a character
    who left is on no Present line, so this block is the only place absorb
    can read the id a `character_state_edits` row names them by.

    `[]` when the tracker is off for the campaign, and `[]` (logged) when it
    cannot be read: `walk` raises on a malformed response ledger, and a review
    that loses this section is a far better outcome than one that never starts.
    """
    try:
        if not store.tracker.settings.enabled(cid):
            return []
        _key, snapshot = store.tracker.walk.current(cid, sid)
        if not snapshot:
            return []
        here = store.tracker.walk.roster(cid, sid, departed=False)
        snapshot = {ref: {**ent, "present": bool(ent.get("present")) and ref in here}
                    for ref, ent in snapshot.items()}
        lines = store.tracker.view.lines_for(
            snapshot, store.tracker.fields.effective(cid, sid), None,
            store.tracker.walk.roster(cid, sid), include_departed=True)
        return [{**line, "id": line["ref"].replace(":", "/", 1)}
                for line in lines if line["values"]]
    except Exception:
        log.warning("absorb: tracker read failed for %s/%s; absorbing without it",
                    cid, sid, exc_info=True)
        return []


def _contradictions(cid: str, sid: str, edits: list) -> list[dict]:
    """`retcon.contradictions`, and never a reason to lose an extraction.

    The badges are advisory by design, and this runs at the very end of a
    handler that has just made several model calls: a garbled `plot.json` or a
    hand-edited `changes.json` costing the reviewer the whole review would be a
    worse failure than showing it with no badges on it. Logged, because a
    reader who cannot see the badges cannot report their absence.
    """
    try:
        return store.retcon.contradictions(cid, sid, edits)
    except Exception:  # an advisory pass, over hand-editable files
        log.warning("contradiction pass failed for %s/%s", cid, sid, exc_info=True)
        return []


# ---- the review family, detached (#396) -------------------------------------
#
# `absorb`, `audit` and `dossiers` are the `review` class: their value is a
# payload nobody has written down, and losing it costs the longest generation
# in the app rather than a cheap retry. So each starts a run, answers 202 with
# the run's id, and persists its result to `store.pending_reviews`; the client
# polls `GET .../runs/{id}` and then reads the review back off the store, which
# is what makes it survive a locked phone, a closed tab and a process restart
# alike.
#
# Three refusals guard the record, and each has its own kind because a client
# does something different with each:
#
# * `review_cancelled` -- the reviewer pressed Cancel. Recorded on the run
#   under the campaign lock BEFORE the record is deleted, and read by the
#   terminal persist under that same hold, so the run cannot recreate a review
#   that has just been dismissed.
# * `scene_gone` -- the scene this run captured is no longer at that id. An
#   existence check is not enough: `scenes/serialize._numbering` derives the
#   next number from the files on disk, so a deleted scene's id comes back, and
#   publishing onto it would put one scene's review on another's transcript.
# * `review_replaced` / `review_missing` -- a retry had nothing of its own to
#   fold into. Writing `{mechanics, edits}` or `{dossiers, edits}` whole would
#   destroy the absorb's prose, its staged edits and its commit token.


class _Prepared(BaseModel):
    """Everything an absorb decides before it starts generating.

    One value rather than seven parameters, and it is not only tidiness: these
    are the snapshot -- read under one campaign-lock hold, at one instant, and
    the review is built from all of them or none. Threaded individually through
    the handoff they read as seven unrelated arguments that a later change is
    free to source from somewhere fresher, which is the exact mistake #271
    warns about (`epoch` read at collection instead of at snapshot).
    """

    epoch: int
    scene: dict
    #: The scene tracker's final state (`_absorb_tracked`), part of the snapshot
    #: because it is read under the same hold as `scene`.
    tracked: list
    facts: dict
    transcript: str
    messages: list
    watermark: dict
    #: The name this snapshot's transcript put on the player's unstamped posts.
    #: Part of the snapshot for the same reason `scene` is: it is an input to
    #: the render the model was shown, and a PC renamed while the call is in
    #: flight would otherwise have every citation of those lines judged against
    #: a name the prompt never used.
    player_label: str
    #: `scene["messages"]` through the prompt phase (`store/regex`): what
    #: `transcript` was rendered from, so the citations are judged against the
    #: text the model was shown rather than the raw text the rules cleaned.
    shown: list
    groups: absorb_membership.Capture | None = None
