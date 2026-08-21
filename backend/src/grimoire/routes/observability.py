"""The three read views over what the backend has been doing: performance
(#154), the structured log (#155) and the error store (#156).

One module for three issues because they are three questions about two files,
and splitting them would put the same window parsing in three places. Every
route here is a pure read -- nothing in this file writes to the store -- and
each answers with zeroes or an empty page rather than a 404 when there is
nothing to report, because "nothing has gone wrong yet" is an answer.

`GET /logs/tail` is the one exception to "pure read" in shape rather than in
effect: it is an SSE stream, and it reuses the transport the chat routes
already established (`StreamingResponse` over `text/event-stream`, consumed by
`frontend/src/api/stream.ts`) rather than introducing a WebSocket for one
panel. It is a *poll* under the hood -- the log is a file, and there is no
in-process notification to subscribe to -- so the design question it has to
answer is not "how do we push" but "how does a poll avoid missing a row", which
`store.logs.tail`'s byte cursor is the answer to.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from .. import store

router = APIRouter()

_DAYS = Query(30, description="Calendar days to read, counting back inclusive of today "
                              "(UTC). Clamped to [1, 366].")

#: How often the tail looks for new rows. A log line is not a token stream --
#: nobody is reading it letter by letter -- so a second is well inside "live"
#: for a human watching a panel, and it keeps an idle tail at one `stat` and at
#: most one short read per second rather than a spin.
TAIL_INTERVAL = 1.0

#: How often the tail emits a keep-alive when there is nothing to send. Same
#: job as `_HEARTBEAT` on the chat stream: a proxy (or a phone putting the tab
#: to sleep) drops a connection that has been silent, and a log that is quiet
#: because the app is healthy is exactly when this stream is silent longest.
TAIL_HEARTBEAT = 15.0


@router.get("/stats")
def get_stats(days: int = _DAYS, campaign: str = Query("")):
    """Latency percentiles, failure counts and their daily trend (#154).

    `totals`, `by_task`, `by_model` and `by_day` each carry `p50`/`p90`/`p99`
    with `calls` beside them, because a percentile over four calls is a number
    and not a fact -- the count is what lets a reader see which is which.

    Two error counts come back and they are different questions, not a
    disagreement: the `errors` field of each latency bucket counts *calls that
    failed* (from the usage ledger, which is the only thing that also knows how
    many succeeded, so it is the only thing that can give a rate a denominator),
    while the top-level `errors` block counts *failures recorded anywhere*
    (from the error store, including the ones that were never a call at all).
    `store.metrics` spells the split out in full.
    """
    return store.metrics.performance(days=days, campaign=campaign)


@router.get("/errors")
def get_errors(days: int = _DAYS, module: str = Query(""),
               campaign: str = Query(""),
               limit: int = Query(store.errors.DEFAULT_ROWS)):
    """Recorded failures over the window, aggregated per module (#156).

    `total` and every grouping are computed over *every* row in the window;
    `rows` is only the newest `limit` of them, and `truncated` says when those
    two differ. A rollup that silently counted one page would report its
    smallest number exactly when the real one mattered most.
    """
    return store.errors.summary(days, module=module, campaign=campaign, rows=limit)


@router.get("/logs")
def get_logs(level: str = Query("debug"), module: str = Query(""),
             since: str = Query(""), until: str = Query(""),
             q: str = Query(""), campaign: str = Query(""),
             limit: int = Query(store.logs.DEFAULT_LIMIT)):
    """The structured log, filtered, newest first (#155).

    `level` is a FLOOR, not an equality: `level=warning` is warnings and worse,
    which is what a severity dropdown means everywhere else and what makes the
    control useful with five options instead of thirty-one combinations.

    `modules`, `counts` and `levels` describe the whole window rather than the
    page, so the filter controls the client builds out of them do not lose an
    option every time something else gets chatty.
    """
    return store.logs.read(level=level, module=module, since=since, until=until,
                           contains=q, campaign=campaign, limit=limit)


@router.get("/logs/level")
def get_log_level():
    """What is being recorded, and what could be.

    Its own endpoint rather than a field on `GET /config` because the log view
    needs it and the config page is not where it is read; `PUT /config` with
    `log_level` is still the one way to change it, so there is exactly one
    writer.
    """
    return {"level": store.logs.level(), "levels": list(store.logs.LEVELS)}


@router.get("/logs/tail")
def get_logs_tail(request: Request, cursor: str = Query(""),
                  level: str = Query("debug"), module: str = Query(""),
                  q: str = Query(""), campaign: str = Query("")):
    """Live tail of the log as Server-Sent Events (#155).

    The first frame is always a `cursor` frame and never rows: a client opens
    this to watch what happens *next*, and gets its backlog from `GET /logs` --
    which is also what keeps the two from having to agree about where "now" is.
    Pass the cursor back on reconnect and no row is missed or repeated across
    the gap; `store.logs.tail` explains why a byte offset is the only cursor
    that can promise both.

    Every frame carries the cursor, so a client that drops one still resumes
    from a position it holds rather than from the beginning of the month.

    Disconnects are how this ends. Nothing here writes, so there is no partial
    state to reconcile -- unlike a scene turn, which is why this stream is a
    plain generator and not a run (`runner`). `request.is_disconnected` is
    checked every tick so a closed tab stops the poll rather than leaving it
    reading a file for nobody until the process ends.
    """
    filters = {"level": level, "module": module, "contains": q, "campaign": campaign}

    async def event_stream():
        position = cursor or store.logs.cursor()
        yield _sse({"cursor": position})
        quiet = 0.0
        while True:
            if await request.is_disconnected():
                return
            # Off the event loop: this stats a file and may read to its end,
            # and the loop it would block is the one serving a live scene turn.
            out = await asyncio.to_thread(store.logs.tail, position, **filters)
            position = out["cursor"]
            if out["rows"]:
                quiet = 0.0
                yield _sse({"rows": out["rows"], "cursor": position})
            else:
                quiet += TAIL_INTERVAL
                if quiet >= TAIL_HEARTBEAT:
                    quiet = 0.0
                    # A comment frame, not a data frame: SSE ignores it, so a
                    # client never has to special-case a keep-alive that looks
                    # like an empty batch of rows.
                    yield ": keep-alive\n\n"
            await asyncio.sleep(TAIL_INTERVAL)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             # A proxy that buffers this stream turns a live
                             # tail into a page that arrives all at once when
                             # the connection finally closes.
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
