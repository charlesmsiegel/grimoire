"""WebSocket routes for live streams (campaign events, log tail).

Per-campaign streams wrap :class:`grimoire.api.stream.StreamManager`: the
Orchestrator pushes streaming tokens here via its ``ws_push`` callback;
the event bus bridge forwards drift / contradiction / image / scene /
library events.

The ``/observability/log`` route opens a live tail of structured debug
events from :class:`grimoire.observability.log.LogStore`, with optional
in-process filters supplied as query parameters.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect

from grimoire.observability.log import LEVEL_ORDER, LogStore, LogSubscription
from grimoire.types.observability import LogEvent, LogLevel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/campaigns/{campaign_id}/stream")
async def campaign_stream(websocket: WebSocket, campaign_id: str) -> None:
    container = getattr(websocket.app.state, "container", None)
    stream = getattr(container, "stream", None) if container is not None else None
    if stream is None:
        await websocket.close(code=1011)
        return

    await stream.connect(campaign_id, websocket)
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await stream.disconnect(campaign_id, websocket)


@router.websocket("/observability/log")
async def observability_log_tail(
    websocket: WebSocket,
    level: str | None = Query(None),
    module: str | None = Query(None),
    operation: str | None = Query(None),
    turn_id: str | None = Query(None),
    free_text: str | None = Query(None),
) -> None:
    """Live tail of the structured debug log.

    Filters are evaluated client-side per event so different consumers can
    share the same producer without coordination. ``module`` and
    ``operation`` accept comma-separated lists. ``level`` is a minimum
    (``WARNING`` yields ``WARNING`` and ``ERROR``).
    """
    container = getattr(websocket.app.state, "container", None)
    obs = getattr(container, "observability", None) if container is not None else None
    log_store = getattr(obs, "log_store", None) if obs is not None else None
    if not isinstance(log_store, LogStore):
        await websocket.close(code=1011)
        return

    try:
        matches = _build_log_matcher(
            level=level,
            modules=_parse_csv(module),
            operations=_parse_csv(operation),
            turn_id=turn_id,
            free_text=free_text,
        )
    except ValueError as exc:
        await websocket.close(code=1008, reason=str(exc))
        return

    await websocket.accept()
    subscription = log_store.subscribe()
    sender = asyncio.create_task(_stream_log_events(websocket, subscription, matches))
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        subscription.unsubscribe()
        sender.cancel()
        with contextlib.suppress(BaseException):
            await sender


@router.get("/health")
def ws_health(request: Request) -> dict[str, object]:
    container = getattr(request.app.state, "container", None)
    stream = getattr(container, "stream", None) if container is not None else None
    if stream is None:
        return {"status": "unavailable"}
    return {
        "status": "ok",
        "campaigns": list(stream.campaigns()),
        "subscriber_count": sum(stream.subscriber_count(c) for c in stream.campaigns()),
    }


def _parse_csv(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    parts = tuple(p.strip() for p in value.split(",") if p.strip())
    return parts or None


def _build_log_matcher(
    *,
    level: str | None,
    modules: tuple[str, ...] | None,
    operations: tuple[str, ...] | None,
    turn_id: str | None,
    free_text: str | None,
) -> Callable[[LogEvent], bool]:
    min_priority: int | None = None
    if level:
        try:
            min_priority = LEVEL_ORDER[LogLevel(level.upper())]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid level: {level!r}") from exc

    needle = free_text.lower() if free_text else None

    def matches(event: LogEvent) -> bool:
        if min_priority is not None and LEVEL_ORDER[event.level] < min_priority:
            return False
        if modules is not None and event.module not in modules:
            return False
        if operations is not None and event.operation not in operations:
            return False
        if turn_id is not None and event.turn_id != turn_id:
            return False
        if needle is not None:
            payload = event.payload if isinstance(event.payload, dict) else {}
            message = str(payload.get("message", ""))
            if needle in message.lower():
                return True
            try:
                blob = json.dumps(payload, default=str)
            except (TypeError, ValueError):
                blob = ""
            if needle not in blob.lower():
                return False
        return True

    return matches


async def _stream_log_events(
    websocket: WebSocket,
    subscription: LogSubscription,
    matches: Callable[[LogEvent], bool],
) -> None:
    while True:
        event = await subscription.queue.get()
        if not matches(event):
            continue
        try:
            await websocket.send_json(event.model_dump(mode="json"))
        except Exception:
            # Client disconnected or the send pipeline is broken — bail so
            # the outer route handler can run its cleanup. Receive loop will
            # also notice the disconnect and exit.
            return


__all__ = ["router"]
