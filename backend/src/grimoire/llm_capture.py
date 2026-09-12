"""Incoming provider bodies, captured before the text/usage projections.

The gateway supplies a sink and one identity per logical call. Each attempt
gets its own clock and sequence; adapters report what they actually received,
including SSE framing and unknown fields. This module has no store dependency.
Capture is diagnostic: a broken sink disables that attempt's recorder without
changing generation, retries, or cancellation. It never buffers a whole reply.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass

KEY = "_incoming_capture"
Sink = Callable[[dict], None]


def _fields(value):
    # The SDK exposes dataclasses; vars also supports its older plain objects.
    # Preserve every exposed field rather than enumerate message/block types.
    return asdict(value) if is_dataclass(value) else vars(value)


class Capture:
    def __init__(self, sink: Sink, call_id: str, attempt: int, model: str, provider: str):
        self.sink = sink
        self.meta = {"call_id": call_id, "attempt": attempt, "model": model, "provider": provider}
        self.started = time.monotonic()
        self.sequence = 0
        self.failed = False

    def record(self, event: str, payload: object) -> None:
        if self.failed:
            return
        try:
            elapsed = (time.monotonic() - self.started) * 1000
            if event == "sdk_message":
                payload = {"type": type(payload).__name__,
                           "fields": json.loads(json.dumps(payload, default=_fields))}
            self.sink({**self.meta, "sequence": self.sequence,
                       "elapsed_ms": elapsed, "event": event, "payload": payload})
            self.sequence += 1
        except Exception:  # noqa: BLE001 - diagnostics must not replace a provider outcome
            self.failed = True


def emit(usage: dict | None, event: str, payload: object) -> None:
    recorder = usage.get(KEY) if usage is not None else None
    if isinstance(recorder, Capture):
        recorder.record(event, payload)
