"""streaming: followups responsibilities."""

from __future__ import annotations

import logging

import anyio
from starlette.concurrency import run_in_threadpool

from ...store import overview
from . import _state

_log = logging.getLogger('grimoire.routes.streaming')


async def _fire_follow_up(after_turn, box: _state.StreamOutcome, cid: str = "", sid: str = "") -> None:
    """Ask for this turn's background work, once the turn is over.

    The rolling summary and the scene-break check are scheduled here rather
    than by the client that sent the turn (#397). `CampaignView.askAfterPost`
    fired them when the streaming promise settled, which is precisely what does
    not happen in the case detached runs exist for: the phone locks, the
    JavaScript is suspended, the turn lands server-side, and nobody is left to
    ask. The summary then falls behind by however many turns were taken with
    the app backgrounded and the scene-break prompt never appears.

    Three things keep this from costing the turn anything, and all three are
    deliberate:

    * it runs AFTER the outcome is decided and outside every write path, so
      nothing here can change what the turn reports;
    * its failures are swallowed -- a summary that could not even be
      *scheduled* is not a failed turn, and there is nobody to tell;
    * it is gated on a boundary having been recorded, which is the same
      question as "did this turn write anything". A turn that persisted
      nothing has moved neither the summary's coverage nor the scene-break
      count, so asking would spend two reservations to be told nothing is due.

    What it schedules is `background` class work, which by `runs.exclusion_key`
    holds no key on the scene -- so neither of these can ever refuse a later
    turn with `run_in_flight` or hold the scene against an edit or an End Scene.

    IN A WORKER THREAD, and both halves of that matter. Reserving a run takes
    the campaign lock and reads the scene's identity off disk, and this is the
    lifespan's event loop -- inline, a contended campaign would freeze every
    unrelated request and open stream on the backend for as long as it took to
    resolve. And the runner's own handoff is built for a thread that is not the
    loop: `runner._PortalEvent` calls through the portal to build a run's
    events, which raises outright when called from the loop thread.

    Awaited AFTER the frames have gone out, never before. It is the last thing
    a turn does, so nothing a subscriber is waiting for can queue behind it --
    the only thing it delays is the run's own terminal state, by the few
    milliseconds two reservations cost.

    It also warms the overview's `scene_turns` for the scene the turn wrote
    (`store.overview.warm_scene`, spec 04 section 5.2), so the shell read that
    follows a turn hashes the transcript rather than parsing it. Same gate,
    same conditions: after the outcome, on a worker thread, holding no lock or
    exclusion key, and swallowing its own failures -- a warm that fails is a
    later miss and nothing else.
    """
    if box.at is None:
        return
    if after_turn is not None:
        try:
            await run_in_threadpool(after_turn, box.at)
        except Exception:                                        # noqa: BLE001
            _log.exception("could not schedule the follow-ups for a finished turn")
    if cid and sid:
        try:
            await anyio.to_thread.run_sync(overview.warm_scene, cid, sid)
        except Exception:                                        # noqa: BLE001
            _log.exception("could not warm the overview for a finished turn")
