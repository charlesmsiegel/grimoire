"""One narrator history phase, outside the locks that freeze a turn (09-S3).

Theory: selected evidence belongs to a compose, then to its frozen snapshot.
Round drivers cache it by round id; NPCs, appended roll continuations and snapshot
replays never gather. An outside-lock replay peek can stale: only the locked
_prepare decision chooses whether to compose or replay, and replay ignores the
supplied evidence. The synchronous bridge tests the calling worker's lock before
crossing to the loop, whose own thread cannot observe that ownership. Errors
(including a closing portal after a player's append) degrade to empty evidence;
cancellation propagates. File stages may overrun the deadline; later network
stages must use its remaining monotonic time. No campaign lock is acquired here.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import CancelledError as PortalCancelledError
from dataclasses import replace
from functools import partial

from starlette.concurrency import run_in_threadpool

from .. import store
from ..store.context import history as history_section
from ..store.context import layout
from ..store.history import budget, model, query, settings
from . import streaming

HISTORY_PHASE_SECONDS = 4
HISTORY_PHASE_PLANNED_SECONDS = 12
_log = logging.getLogger("grimoire")
_QUERY_POST = object()


def _error(cid, sid):
    # No exception text: a corrupt historical file can carry private prose.
    _log.warning("History retrieval failed for campaign %s scene %s", cid, sid)
    return model.Evidence(coverage=model.Coverage("error", semantic="off"))


def _prepare(cid, sid, seed, resolved):
    cfg = store.config.read_config()
    if settings.read(cfg) is None or "history_recall" not in layout.enabled_ids(("history_recall",)):
        return None
    cap = budget.ceiling(cfg, resolved)
    if not cap:
        return None
    prepared = query.prepare(cid, sid, seed=seed)
    # The private legacy archive window includes director notes/image URLs;
    # the query's cleaned scan cannot stand in for it or be sent as its proxy.
    omissions = history_section.summary_omissions(cid, sid, prepared.archive_scan_text)
    # Cost follows the same player/director-note boundary as generation. The
    # query's scan boundary may end in an assistant reply on a legacy retry.
    answering = streaming._answering_post(store.scenes.read_scene(cid, sid)["messages"])
    return replace(prepared.query, turn_index=answering), cap, omissions


async def gather(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                 post=_QUERY_POST, run_id=""):
    """Gather completed evidence before a turn's locked snapshot decision."""
    deadline = time.monotonic() + HISTORY_PHASE_SECONDS if deadline is None else deadline
    try:
        prepared = await run_in_threadpool(_prepare, cid, sid, seed, resolved)
        if prepared is None:
            return model.Evidence()
        q, cap, omissions = prepared
        if post is not _QUERY_POST:
            q = replace(q, turn_index=post)
        return await store.history.retrieve(
            cid, sid, q, ceiling=cap, embed_client=embed_client, deadline=deadline,
            summary_omissions=omissions, run_id=run_id,
        )
    except PortalCancelledError:
        raise
    except Exception:  # noqa: BLE001 -- optional history cannot strand an appended player post
        return _error(cid, sid)


def gather_sync(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                post=_QUERY_POST, run_id=""):
    """Bridge a def route; ownership is checked in its worker before the portal."""
    if store.locks.holds_campaign(cid):
        return model.Evidence(coverage=model.Coverage("exhausted", semantic="skipped:locked"))
    try:
        return app.state.run_portal.call(partial(
            gather, app, cid, sid, seed=seed, resolved=resolved,
            llm_client=llm_client, embed_client=embed_client, deadline=deadline,
            post=post, run_id=run_id,
        ))
    except PortalCancelledError:
        raise
    except Exception:  # noqa: BLE001 -- shutdown can close the portal after the player's append
        return _error(cid, sid)
