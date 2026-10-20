"""Discover, cancel and stream campaign, world and global work."""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from ... import runner
from . import _contracts, _events, _record, _subjects
from ._router import router as router


def _subject_run(app, subject: _contracts.Subject, run_id: str) -> _record.Run:
    """One run on this subject, or 404. The subject check IS the isolation: a
    run id belonging to another world answers 'gone', never that world's
    state."""
    run = app.state.runs.get(run_id, subject)
    if run is None:
        raise _subjects._gone("run_gone", "no such run for this subject")
    return run


def _subject_runs(app, subject: _contracts.Subject, attempt: str | None) -> dict:
    """Every run on this subject inside the retention window, oldest first.

    A LIST, not the newest one, and that is the difference from
    `get_scene_run`. Every class mounted here declares no exclusion key, so
    several legitimately overlap -- two image descriptions being drafted at
    once in one world is the ordinary case -- and answering with one of them
    would leave the others discoverable by nothing.

    `attempt` narrows it to the caller's own run, which is how a client that
    lost its 202 finds the work it started rather than somebody else's.
    Filtering here rather than making the caller scan is not a convenience: the
    run ids are the only other handle, and a client that lost the response does
    not have one.
    """
    found = app.state.runs.for_subject(subject)
    if attempt is not None:
        # An attempt that ADOPTED a live run (`single_live`) finds that run too.
        found = [r for r in found
                 if r.attempt_id == attempt or attempt in r.adopted_attempts]
    return {"runs": [_events.run_payload(r) for r in found]}


def _subject_cancel(app, subject: _contracts.Subject, run_id: str) -> dict:
    """`post_run_cancel` for a non-scene subject; see there for why a terminal
    run is not an error."""
    run = _subject_run(app, subject, run_id)
    if run.state == "running":
        runner.cancel(app, run)
        run.terminal.wait(timeout=_contracts.CANCEL_TIMEOUT_SECONDS)
    return {"run": _events.run_payload(run)}


def _subject_stream(app, subject: _contracts.Subject, run_id: str, from_: int):
    """`get_run_stream` for a non-scene subject.

    The negative-cursor refusal is repeated rather than left to the shared
    helper because it has to happen before the lookup: a malformed cursor is
    the caller's mistake and answering it with a 404 about a run that exists
    would send them looking in the wrong place.
    """
    if from_ < 0:
        raise HTTPException(status_code=400, detail="from must not be negative")
    return _events.tail_response(_subject_run(app, subject, run_id), from_)


@router.get("/campaigns/{cid}/runs")
def get_campaign_runs(cid: str, request: Request,
                      attempt: str | None = None) -> dict:
    return _subject_runs(request.app, _subjects.campaign_subject(cid), attempt)


@router.get("/campaigns/{cid}/runs/{run_id}")
def get_campaign_run(cid: str, run_id: str, request: Request) -> dict:
    return {"run": _events.run_payload(_subject_run(request.app, _subjects.campaign_subject(cid), run_id))}


@router.get("/campaigns/{cid}/runs/{run_id}/stream")
def get_campaign_run_stream(cid: str, run_id: str, request: Request,
                            from_: int = Query(0, alias="from")):
    return _subject_stream(request.app, _subjects.campaign_subject(cid), run_id, from_)


@router.post("/campaigns/{cid}/runs/{run_id}/cancel")
def post_campaign_run_cancel(cid: str, run_id: str, request: Request) -> dict:
    return _subject_cancel(request.app, _subjects.campaign_subject(cid), run_id)


@router.get("/worlds/{wid}/runs")
def get_world_runs(wid: str, request: Request,
                   attempt: str | None = None) -> dict:
    return _subject_runs(request.app, _subjects.world_subject(wid), attempt)


@router.get("/worlds/{wid}/runs/{run_id}")
def get_world_run(wid: str, run_id: str, request: Request) -> dict:
    return {"run": _events.run_payload(_subject_run(request.app, _subjects.world_subject(wid), run_id))}


@router.get("/worlds/{wid}/runs/{run_id}/stream")
def get_world_run_stream(wid: str, run_id: str, request: Request,
                         from_: int = Query(0, alias="from")):
    return _subject_stream(request.app, _subjects.world_subject(wid), run_id, from_)


@router.post("/worlds/{wid}/runs/{run_id}/cancel")
def post_world_run_cancel(wid: str, run_id: str, request: Request) -> dict:
    return _subject_cancel(request.app, _subjects.world_subject(wid), run_id)


@router.get("/runs")
def get_global_runs(request: Request, attempt: str | None = None) -> dict:
    return _subject_runs(request.app, _subjects.GLOBAL_SUBJECT, attempt)


@router.get("/runs/{run_id}")
def get_global_run(run_id: str, request: Request) -> dict:
    return {"run": _events.run_payload(_subject_run(request.app, _subjects.GLOBAL_SUBJECT, run_id))}


@router.get("/runs/{run_id}/stream")
def get_global_run_stream(run_id: str, request: Request,
                          from_: int = Query(0, alias="from")):
    return _subject_stream(request.app, _subjects.GLOBAL_SUBJECT, run_id, from_)


@router.post("/runs/{run_id}/cancel")
def post_global_run_cancel(run_id: str, request: Request) -> dict:
    return _subject_cancel(request.app, _subjects.GLOBAL_SUBJECT, run_id)
