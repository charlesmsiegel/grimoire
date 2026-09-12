"""Optional reasoning side-channel for scene display, separate from text.

Only a display consumer installs a Buffer in the existing usage holder.
Adapters enqueue reasoning there; the gateway wakes that consumer even when
the provider has no visible text. Ordinary stream/complete callers still see
only prose. Retrying clears the old attempt's reasoning before new text lands.
"""
from __future__ import annotations

KEY = "_reasoning_display"


class Buffer:
    def __init__(self):
        self.chunks: list[str] = []
        self.reset = False

    def begin(self):
        self.chunks.clear()
        self.reset = True

    def drain(self):
        events = []
        if self.reset:
            events.append({"thinking_reset": True})
        if self.chunks:
            events.append({"thinking_delta": "".join(self.chunks)})
        self.reset = False
        self.chunks.clear()
        return events


def pending(usage):
    buffer = usage.get(KEY) if usage is not None else None
    return isinstance(buffer, Buffer) and bool(buffer.reset or buffer.chunks)


def feed(usage, text):
    buffer = usage.get(KEY) if usage is not None else None
    if isinstance(buffer, Buffer) and isinstance(text, str) and text:
        buffer.chunks.append(text)


def from_chunk(obj, usage):
    buffer = usage.get(KEY) if usage is not None else None
    if not isinstance(buffer, Buffer) or not isinstance(obj, dict):
        return
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return
    # Match the same choice the prose adapters select. Alternate candidates
    # remain available in raw capture but do not belong to this displayed reply.
    for choice in choices[:1]:
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            continue
        # Providers sometimes supply both aliases; choose one, never duplicate.
        text = delta.get("reasoning_content") or delta.get("reasoning")
        details = delta.get("reasoning_details")
        if not text and isinstance(details, list):
            text = "".join(d.get("text", "") for d in details
                           if isinstance(d, dict) and isinstance(d.get("text"), str))
        feed(usage, text)


def glm_effort(conn):
    model = str(conn.get("model", "")).lower().split("/")[-1]
    effort = conn.get("reasoning_effort", "")
    return effort if model in ("glm-5.3", "glm-5.3-flash") and effort in ("low", "high", "max") else ""


async def stream(client, messages, conn, usage):
    """Yield separate display events; closing this wrapper closes generation."""
    buffer = Buffer()
    usage[KEY] = buffer
    source = client.stream(messages, conn, usage)
    try:
        async for delta in source:
            for event in buffer.drain():
                yield event
            yield {"delta": delta}
        for event in buffer.drain():
            yield event
    finally:
        try:
            await source.aclose()
        finally:
            usage.pop(KEY, None)
