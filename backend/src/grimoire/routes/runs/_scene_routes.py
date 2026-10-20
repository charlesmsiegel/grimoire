"""Discover and cancel scene work by immutable identity and attempt."""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from ... import runner
from ...store import attempts as scenes_attempts
from ...store import scenes
from ...store.campaigns.paths import CampaignNotFound
from . import _contracts, _events, _record, _subjects
from ._router import router as router


def _resolve(app, cid: str, sid: str, run_id: str) -> _record.Run:
    subject, identity = _subjects._scene_subject(cid, sid)
    run = app.state.runs.get(run_id, subject, identity=identity)
    if run is None:
        raise _subjects._gone("run_gone", "no such run for this scene")
    return run


@router.get("/campaigns/{cid}/scene-by-identity")
def get_scene_by_identity(cid: str, identity: str) -> dict:
    """The scene an identity names right now, or 404.

    What a notification tap resolves through: the intent carries the identity
    precisely because a `sid` goes stale on rename, so without this the tap
    could only open a stale route or fall back to the campaign.

    The identity is a QUERY parameter, and the path deliberately does not sit
    under `/scenes/`. `/scenes/by-identity/{identity}` puts a literal where a
    `sid` goes and a parameter where a literal goes, so it crosses EVERY
    `/scenes/{sid}/<name>` route -- a dozen ambiguous pairs, each needing its
    own entry in `test_route_order.CROSSING_PAIRS`. Twelve exemptions to add
    one lookup is the guard reporting that the URL is shaped wrong, not that
    the guard is in the way.
    """
    try:
        sid = scenes.find_by_identity(cid, identity)
    except CampaignNotFound as exc:
        raise _subjects._gone("campaign_gone", "no such campaign") from exc
    except OSError as exc:
        # The scan could not read every candidate, so it cannot rule the
        # identity out. A 404 here tells a notification tap the scene is gone
        # and sends it back to the campaign, which is a wrong answer the user
        # sees; contention is retryable and is what this actually is.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": str(exc)}) from exc
    if sid is None:
        raise _subjects._gone("scene_gone", "no scene carries that identity")
    return {"id": sid}


@router.get("/campaigns/{cid}/scenes/{sid}/run")
def get_scene_run(cid: str, sid: str, request: Request,
                  attempt: str | None = None) -> dict:
    """Discovery. With `attempt`, an exact match; without, the newest run.

    "Newest" is a decision, not an implementation detail. The subject index
    routinely holds several -- a terminal run stays readable for the whole
    retention window while a new one is already live -- and answering with the
    older, terminal one makes the client settle and miss the live reply
    entirely, which is the failure this endpoint exists to prevent.

    A scene with no runs answers `{"run": None}`, not 404: that is the ordinary
    case on every mount, and an error there would make every quiet scene look
    broken.
    """
    subject, identity = _subjects._scene_subject(cid, sid)
    found = request.app.state.runs.for_subject(subject, identity=identity)
    if attempt is not None:
        found = [r for r in found if r.attempt_id == attempt]
    else:
        # An id the caller named is answered whatever its class -- it asked
        # about that run. Without one this is "the newest thing I could be
        # waiting for", and only `ATTACHABLE` is ever that; see there.
        found = [r for r in found if r.cls in _contracts.ATTACHABLE]
    if not found:
        return {"run": None}
    # Reservation order, NOT `max(started_at)`. `for_subject` preserves the
    # order runs were indexed in, and that order is real; `started_at` is a wall
    # clock, so a backward correction between two reservations can give the
    # newer live run a LOWER stamp -- and answering with the older terminal one
    # makes the client settle and miss the active reply, which is the single
    # failure this endpoint exists to prevent.
    return {"run": _events.run_payload(found[-1])}


@router.get("/campaigns/{cid}/scenes/{sid}/runs/{run_id}")
def get_run(cid: str, sid: str, run_id: str, request: Request) -> dict:
    return {"run": _events.run_payload(_resolve(request.app, cid, sid, run_id))}


@router.post("/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel")
def post_run_cancel(cid: str, sid: str, run_id: str, request: Request) -> dict:
    """Ask a run to stop, and answer once it really has.

    A terminal run is NOT an error here: Stop races the reply landing, and
    reporting a failure for a turn that succeeded a moment earlier is worse
    than doing nothing.
    """
    run = _resolve(request.app, cid, sid, run_id)
    if run.state == "running":
        runner.cancel(request.app, run)
        # Wait for the abort hook, not just for the cancel to be delivered:
        # answering earlier lets a fast re-send race the partial-persist, which
        # is the ordering the handshake exists to guarantee.
        run.terminal.wait(timeout=_contracts.CANCEL_TIMEOUT_SECONDS)
    return {"run": _events.run_payload(run)}


@router.post("/campaigns/{cid}/scenes/{sid}/attempt-cancel")
def post_attempt_cancel(cid: str, sid: str, request: Request,
                        attempt: str = Query(...)) -> dict:
    """Stop the turn this attempt id names, whether or not it has a run yet.

    What Stop calls when discovery came back empty. The POST it is stopping may
    have been accepted and then blocked in synchronous setup -- the campaign
    lock is held by something else -- so "no run for this attempt" does not mean
    "nothing is going to happen": the route can still reserve and detach a turn
    after the lookup. Recording the cancel against the ATTEMPT closes that,
    because the reservation consumes it (`reserve_turn`).

    Idempotent, and safe to call for an attempt that never reserves: the record
    is capped and forgotten oldest-first.

    The attempt is a QUERY parameter, not a path segment, because the header
    contract accepts a client's id VERBATIM -- `X-Grimoire-Attempt: client/42`
    is legal, and percent-encoding does not save it: the ASGI router matches on
    the decoded path, so the slash splits the segment and the request reaches no
    route at all. The very clients most likely to use a structured id would be
    the ones unable to stop their own turns.
    """
    attempt_id = attempt
    subject, identity = _subjects._scene_subject(cid, sid)
    # One call, because looking up and then recording is two acquisitions with a
    # reservation-shaped gap between them -- see `cancel_or_precancel`.
    run = request.app.state.runs.cancel_or_precancel(subject, attempt_id, identity)
    if run is None:
        return {"run": None}
    if run.state == "running":
        runner.cancel(request.app, run)
        run.terminal.wait(timeout=_contracts.CANCEL_TIMEOUT_SECONDS)
    return {"run": _events.run_payload(run)}


@router.get("/campaigns/{cid}/scenes/{sid}/attempt-state")
def get_attempt_state(cid: str, sid: str, request: Request,
                      attempt: str = Query(...)) -> dict:
    """Whether this attempt's post is still in the scene, and its run if one
    is still known.

    The question a client asks when it comes back after the run record expired.
    Every other route here reads the in-memory registry, which by definition no
    longer has a reaped run -- so without this the client cannot ask the only
    decisive question at all, and #95's ambiguity returns the moment
    `REAP_SECONDS` passes.

    `retained` is the durable half and comes from `store.attempts`. It answers
    False for everything unresolved -- no identity, no record, an unreadable
    file -- because the caller's rule is that ambiguity keeps the player's
    text: wrong this way costs one duplicate they can see and delete, wrong the
    other way costs them their words with no trace.

    Scene-scoped like every route here, so an attempt belonging to another
    scene answers about nothing rather than about that scene.
    """
    subject, identity = _subjects._scene_subject(cid, sid)
    run = request.app.state.runs.for_attempt(subject, attempt, identity)
    return {
        "attempt": attempt,
        "retained": scenes_attempts.retained(
            cid, None if identity == _contracts.UNRESOLVED else identity, attempt),
        "run": _events.run_payload(run) if run is not None else None,
    }


@router.get("/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream")
def get_run_stream(cid: str, sid: str, run_id: str, request: Request,
                   from_: int = Query(0, alias="from")):
    """Replay this run's frames from `from_`, INCLUSIVE.

    A client that consumed through N asks for N+1. Exclusive would drop one
    frame per reconnect -- invisible until someone reads the text and finds a
    word missing mid-sentence.

    Every frame goes out with its absolute index on an SSE `id:` line. The
    client cannot derive that by counting what it decoded, because a comment
    frame (the heartbeat) carries no event at all -- so a per-event cursor lags
    the server's position and `consumed + 1` replays text already rendered.
    """
    # 400 rather than FastAPI's 422: `routes/scenes/_reads.py:get_scene` already answers a bad
    # `limit`/`before` this way, and one shape of bad-parameter error is easier
    # for a client to handle than two.
    if from_ < 0:
        raise HTTPException(status_code=400, detail="from must not be negative")
    run = _resolve(request.app, cid, sid, run_id)
    return _events.tail_response(run, from_)
