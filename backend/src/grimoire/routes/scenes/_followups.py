"""A landed turn schedules independent background readers after its terminal frames."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ...llm import LLMClient
from .. import runs
from . import _breaks, _review_state, _rolling
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


# ---- what a landed turn asks for next (#397) --------------------------------
#
# The two routes above are the `background` class: no exclusion key, no
# notification, their own stores, fire-and-forget. Until now they were fired by
# `CampaignView.askAfterPost`, once the streaming promise settled -- which is
# exactly the thing that does not happen in the case detached runs exist for.
# The phone locks, the JavaScript is suspended or the page is gone, the turn
# lands server-side, and nobody is left to POST either one: the summary falls
# behind by however many turns were taken with the app backgrounded, and the
# scene-break prompt never appears -- indefinitely, since the next opportunity
# only comes with the next send.
#
# So the turn schedules them, at the point its terminal write is done, where
# the trigger cannot be detached from the event that should cause it
# (`streaming._fire_follow_up`). Three properties keep that from costing the
# turn anything, and each is structural rather than a rule to remember:
#
# * `background` declares no exclusion key, so neither of these can refuse a
#   chat with `run_in_flight` nor hold the scene against an edit, a cut or an
#   End Scene (`scene_busy`);
# * the call is outside the turn's own outcome and its failures are swallowed,
#   so a summary that could not even be reserved is not a failed turn;
# * each run gets `runner._guarded`'s failure boundary, so one that dies takes
#   no sibling with it.
#
# What this does NOT cover is every other transcript write -- an edit, a cut, a
# retcon, a manual roll, a check, a replay. Those are still asked for by the
# client, and correctly so: each is a request the player made and is waiting
# on, so there is a client there by construction. It is only the turn whose
# completion the client can miss.


def schedule_follow_ups(app, cid: str, sid: str, upto: int,
                        client: LLMClient) -> None:
    """Start this scene's rolling-summary fold and scene-break check as runs.

    `upto` is the boundary the client used to supply as `seen`: how long the
    transcript was when the turn finished. Read server-side under the campaign
    lock the terminal write held (`streaming._tail_length`), which is strictly
    better than the client's read-after-the-fact -- there is no window left for
    a fast next send to append a post the fold would then swallow.

    `client` is the turn's own gateway client rather than a fresh resolve of
    `get_llm`: there is no request here to resolve a dependency against, and
    the facade dispatches per connection anyway, so the turn's is the same
    object either route would have been handed -- including the fake a test
    injected at `routes.get_llm`.

    Nothing is awaited and nothing comes back. Both halves decide for
    themselves whether anything is due and answer "no" without reaching a
    provider, which is what makes firing after every turn cheap enough to be
    unconditional.
    """
    _start_background(app, cid, sid, "rolling-summary",
                      lambda: _rolling_follow_up(cid, sid, upto, client))
    _start_background(app, cid, sid, "scene-break",
                      lambda: _break_follow_up(cid, sid, upto, client))


def _start_background(app, cid: str, sid: str, kind: str, work) -> None:
    """One `background` run, or nothing at all.

    The release is not defensive noise. `runner.start` raises when the app has
    no lifespan running -- a bare `TestClient`, or a shutdown that closed the
    portal between the reservation and the handoff -- and a run reserved but
    never entered stays `running` for the life of the process. Only terminal
    runs are reaped, so that one would keep `PUT /config/data-dir` answering
    `run_in_flight` forever. `runs.reservation` makes the same guarantee for a
    route; this is the same duty for a caller that is not one.
    """
    run = runs.reserve_background(app, cid, sid, kind)
    if run is None:
        return
    try:
        runs.start_computing(app, run, work)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("could not start the %s follow-up for %s/%s -- %s",
                    kind, cid, sid, exc)
        runs.release_before_start(app, run, "failed",
                                  {"kind": "run_failed", "detail": str(exc)})


async def _rolling_follow_up(cid: str, sid: str, upto: int,
                             client: LLMClient) -> dict:
    """The automatic rolling-summary refresh, as a run's outcome.

    `force=False`, always: this is the per-turn question, so the whole "is it
    worth a call" decision stays exactly where it already lives. That is also
    what makes the single pass right -- the coalescing branch answers for
    itself for an automatic call, and the retry loop in `post_rolling_summary`
    belongs to the panel's button, which has a request to hold open and a
    player waiting on it.

    A refusal comes back as the run's error rather than as an exception, so a
    scene with no `rolling-summary` connection reads as a background run that
    said why, not as a traceback in the log after every turn.
    """
    try:
        answer = await _rolling._rolling_once(cid, sid, False, upto, client)
    except HTTPException as exc:
        return {"state": "failed", "error": _review_state._run_error(exc)}
    return {"state": "landed",
            "result": {"refreshed": bool(answer and answer["refreshed"])}}


async def _break_follow_up(cid: str, sid: str, upto: int,
                           client: LLMClient) -> dict:
    """The automatic scene-break question, as a run's outcome.

    `_rolling_follow_up`'s shape, for its reasons. Fired separately rather than
    chained onto it, exactly as the client did: neither answer is a
    precondition for the other, and chaining would make a failed summary
    silently skip the break question.
    """
    try:
        answer = await _breaks._break_once(cid, sid, False, upto, client)
    except HTTPException as exc:
        return {"state": "failed", "error": _review_state._run_error(exc)}
    return {"state": "landed", "result": {"asked": bool(answer["asked"])}}


def _follow_up_hook(app, cid: str, sid: str, client: LLMClient):
    """The callback a turn producer fires once its terminal write is done.

    One argument, the boundary, because that is the only thing the producer
    knows that this side does not -- and it is exactly the thing that used to
    travel from the client.
    """
    return lambda upto: schedule_follow_ups(app, cid, sid, upto, client)
