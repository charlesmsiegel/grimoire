"""WebSocket route for per-campaign event streams.

Wraps :class:`grimoire.api.stream.StreamManager`. The Orchestrator pushes
streaming tokens here via its ``ws_push`` callback; the event bus bridge
forwards drift / contradiction / image / scene / library events.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

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
        # Keep the connection open. We do not consume client messages today,
        # but draining the socket keeps the underlying transport alive and
        # lets us detect disconnects.
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await stream.disconnect(campaign_id, websocket)


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


__all__ = ["router"]
