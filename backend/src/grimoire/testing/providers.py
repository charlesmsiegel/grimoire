"""A scripted provider transport and clock: byte-level faults through the real parser.

Test infrastructure only; production packages must not import it. Each
``connect`` consumes the next scripted step. A step that streams emits the
same OpenAI-compatible lines a real server does, including
``"finish_reason": null`` on content chunks, so the adapter's real SSE parser
and accounting run unchanged. Usage booked through this transport is labelled
``synthetic`` by the adapter: a fixture cannot claim to be provider-reported.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import Secret, Timeouts
from grimoire.providers.errors import TransportError

REFERENCE_USAGE = {"prompt_tokens": 20, "completion_tokens": 3}


@dataclass(frozen=True)
class Reply:
    """A streamed answer: content chunks, a finish chunk, a usage chunk, ``[DONE]``."""

    deltas: tuple[str, ...] = ("Hel", "lo", "!")
    usage: Mapping | None = field(default_factory=lambda: dict(REFERENCE_USAGE))
    finish_reason: str | None = "stop"
    model: str = "synthetic-model"
    done: bool = True

    def lines(self) -> list[bytes]:
        def data(obj: object) -> bytes:
            return b"data: " + json.dumps(obj).encode() + b"\n"
        out = [data({"model": self.model, "choices": [{"delta": {"content": d},
                                                       "finish_reason": None}]})
               for d in self.deltas]
        if self.finish_reason is not None:
            out.append(data({"model": self.model,
                             "choices": [{"delta": {}, "finish_reason": self.finish_reason}]}))
        if self.usage is not None:
            out.append(data({"model": self.model, "choices": [], "usage": dict(self.usage)}))
        if self.done:
            out.append(b"data: [DONE]\n")
        return out

    def output_tokens(self) -> int:
        tokens = (self.usage or {}).get("completion_tokens", 0)
        return tokens if isinstance(tokens, int) else 0


REFERENCE_REPLY = Reply()


@dataclass(frozen=True)
class Status:
    """A non-streaming HTTP response (an error status, or a 200 that is not SSE)."""

    code: int
    body: bytes = b""
    retry_after: str | None = None


@dataclass(frozen=True)
class ConnectFail:
    kind: str = "network"  # "network", "timeout" or "tls": nothing was sent


@dataclass(frozen=True)
class WriteFail:
    kind: str = "network"  # the request may have been written: counts as sent


@dataclass(frozen=True)
class ReadFail:
    after_lines: int
    kind: str = "timeout"
    reply: Reply = REFERENCE_REPLY


@dataclass(frozen=True)
class Truncate:
    after_lines: int
    reply: Reply = REFERENCE_REPLY


@dataclass(frozen=True)
class CancelAt:
    """Cancel the call's token after ``after_lines`` lines; the stream then ends."""

    after_lines: int
    reply: Reply = REFERENCE_REPLY


@dataclass(frozen=True)
class Garbage:
    after_lines: int
    reply: Reply = REFERENCE_REPLY


@dataclass(frozen=True)
class Interrupt:
    """``KeyboardInterrupt`` while reading after ``after_lines`` lines."""

    after_lines: int
    reply: Reply = REFERENCE_REPLY


Step = Reply | Status | ConnectFail | WriteFail | ReadFail | Truncate | CancelAt | Garbage | Interrupt


@dataclass(frozen=True)
class Send:
    """One request that reached the fake. Header values are never recorded."""

    url: str
    body: bytes = field(repr=False)
    body_digest: str
    header_names: tuple[str, ...]
    keyed: bool


class _Response:
    def __init__(self, status: int, headers: Mapping[str, str], lines: Sequence[bytes],
                 fault: Step | None, cancel: CancelToken, key: Secret | None) -> None:
        self.status = status
        self.headers = dict(headers)
        self._lines = list(lines)
        self._index = 0
        self._fault = fault
        self._cancel = cancel

    def read_line(self, max_bytes: int, timeout: float) -> bytes:
        fault, index = self._fault, self._index
        if self._cancel.cancelled:
            return b""  # a cancelled connection reads as closed
        if fault is not None and index == getattr(fault, "after_lines", -1):
            if isinstance(fault, ReadFail):
                raise TransportError(fault.kind, "read")
            if isinstance(fault, Truncate):
                return b""
            if isinstance(fault, CancelAt):
                self._cancel.cancel()
                return b""
            if isinstance(fault, Garbage):
                self._fault = None
                return b"data: {garbage\n"
            if isinstance(fault, Interrupt):
                raise KeyboardInterrupt
        if index >= len(self._lines):
            return b""
        self._index += 1
        line = self._lines[index]
        if len(line) > max_bytes:
            raise TransportError("bad_response", "read")
        return line

    def error_body(self, limit: int) -> bytes:
        rest = b"".join(self._lines[self._index:])
        self._index = len(self._lines)
        return rest[:limit]


class _Connection:
    def __init__(self, transport: ScriptedTransport, url: str, step: Step,
                 cancel: CancelToken) -> None:
        self._transport = transport
        self._url = url
        self._step = step
        self._cancel = cancel
        self.peer_loopback = transport.peer_loopback
        self.closed = False

    def send(self, body: bytes, headers: Mapping[str, str], key: Secret | None) -> _Response:
        self._transport.sends.append(Send(
            self._url, body, hashlib.sha256(body).hexdigest(),
            tuple(sorted(headers)) + (("Authorization",) if key is not None else ()),
            key is not None))
        step = self._step
        if isinstance(step, WriteFail):
            raise TransportError(step.kind, "send")
        if isinstance(step, Status):
            headers_out = {"content-type": "application/json"}
            if step.retry_after is not None:
                headers_out["retry-after"] = step.retry_after
            return _Response(step.code, headers_out, step.body.splitlines(keepends=True) or [],
                             None, self._cancel, key)
        reply = step if isinstance(step, Reply) else step.reply
        return _Response(200, {"content-type": "text/event-stream"}, reply.lines(),
                         None if isinstance(step, Reply) else step, self._cancel, key)

    def close(self) -> None:
        self.closed = True
        self._transport.closed += 1


class ScriptedTransport:
    """Plays ``steps`` in order, one per ``connect``. Running out is a test error."""

    def __init__(self, steps: Sequence[Step], *, peer_loopback: bool = False) -> None:
        self._steps = list(steps)
        self.peer_loopback = peer_loopback
        self.connects: list[str] = []
        self.sends: list[Send] = []
        self.closed = 0

    def connect(self, url: str, timeouts: Timeouts, cancel: CancelToken) -> _Connection:
        if not self._steps:
            raise AssertionError("scripted transport ran out of steps: an unexpected call")
        self.connects.append(url)
        step = self._steps.pop(0)
        if isinstance(step, ConnectFail):
            raise TransportError(step.kind, "connect")
        return _Connection(self, url, step, cancel)

    @property
    def remaining(self) -> int:
        return len(self._steps)


class ScriptedClock:
    """A deterministic clock and wait: time advances only when read; waits are recorded."""

    def __init__(self, step: float = 0.01) -> None:
        self.now = 0.0
        self.step = step
        self.waits: list[float] = []

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    def wait(self, token: CancelToken, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        return token.cancelled
