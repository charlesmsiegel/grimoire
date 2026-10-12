"""`POST /api/cache/sync`: the compiled-cache warm-up as a run (spec 05-C2).

The HTTP door to `store.cache_sync.run`, for what the CLI cannot serve:
Android, a client that only speaks HTTP, and a tab that should forget its
remembered reads when the sync ends. One `background` run, kind `cache-sync`,
on the global subject, answered **202** and reached through that subject's
four run routes (list, poll, stream, cancel).

* One at a time and never merged: a request while one is live answers 409
  `sync_in_flight` naming it, whatever its options -- adopting it would lose
  a dry run's or an `embed: false`'s meaning. A repeat of the live run's own
  `attempt_id` is handed that run back.
* No exclusion key, as for every `background` run: it neither holds a scene
  nor is refused by one. A live run makes `PUT /config/data-dir` answer 409,
  like any run, which is right: the batch is pinned to the root it started on.
* Cancel sets `run.cancel_requested`, which the batch reads between paths,
  hooks and chunks; the work is a synchronous batch in a worker thread the
  run never abandons (`runner.start_thread`), and lifespan shutdown sets the
  same flag (`cache_warm.stop`).
* Not a write the activity middleware stamps (the path is not under
  `/api/campaigns/`): the batch bumps the campaigns' write tokens itself,
  where it writes.
* Progress frames carry counts only; the final frame and the run's result
  carry the report, which holds names and counts and never record content.

The handler is `def`: reserving a run builds its events through the lifespan's
portal, which raises from the loop thread (CLAUDE.md, "Detached runs").
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import runner
from ..store import cache_sync, paths
from . import runs
from .common import _dump

router = APIRouter()


class CacheSyncRequest(BaseModel):
    paths: list[str] = []
    campaigns: list[str] = []
    worlds: list[str] = []
    all_records: bool = False
    #: Each `{"old": ..., "new": ...}`: a rename the caller made.
    renamed: list[dict] = []
    deleted: list[str] = []
    embed: bool = True
    dry_run: bool = False
    verify: bool = False
    attempt_id: str | None = None


def _renames(rows: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        old, new = (row.get("old"), row.get("new")) if isinstance(row, dict) else (None, None)
        if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
            raise HTTPException(status_code=400, detail={
                "kind": "bad_rename", "detail": 'each rename is {"old": PATH, "new": PATH}'})
        out[old] = new
    return out


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _progress(run: runs.Run, counts: dict) -> None:
    """One progress frame: the phase and counts, never a path."""
    run.append_frame(_frame({"progress": counts}))


@router.post("/cache/sync", status_code=202)
def post_cache_sync(body: CacheSyncRequest, request: Request) -> dict:
    asked = _dump(body)
    if not (body.paths or body.campaigns or body.worlds or body.all_records
            or body.deleted or body.renamed):
        raise HTTPException(status_code=400, detail={
            "kind": "nothing_to_sync",
            "detail": "name at least one path, campaign, world, deleted path or rename, "
                      "or all_records"})
    renamed = _renames(body.renamed)
    app = request.app
    run, fresh = runs.reserve_cache_sync(app, body.attempt_id)
    if not fresh:
        return {"run": runs.run_payload(run)}
    with runs.reservation(app, run):
        # Pinned inside the reservation: no move can begin while the run is
        # live, so this is the root the batch keeps for its whole life.
        root = paths.home()

        def work(r: runs.Run) -> dict:
            registry = app.state.runs
            with registry.sync_hold() as held:
                if not held:
                    raise RuntimeError("the store root is moving")
                report = cache_sync.run(
                    asked["paths"], campaigns=asked["campaigns"], worlds=asked["worlds"],
                    all_records=body.all_records, renamed=renamed, deleted=asked["deleted"],
                    embed=body.embed, dry_run=body.dry_run, verify=body.verify, root=root,
                    stop=lambda: r.cancel_requested,
                    progress=lambda counts: _progress(r, counts))
            out = report.as_dict()
            r.append_frame(_frame({"report": out}))
            return out

        # `run.started` after the handoff, for `start_computing`'s reason: a
        # handoff that raises is still released by `reservation`.
        runner.start_thread(app, run, work)
        run.started = True
    return {"run": runs.run_payload(run)}
