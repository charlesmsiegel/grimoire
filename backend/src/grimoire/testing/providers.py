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
                 fault: Step | None, cancel: CancelToken) -> None:
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
                             None, self._cancel)
        reply = step if isinstance(step, Reply) else step.reply
        return _Response(200, {"content-type": "text/event-stream"}, reply.lines(),
                         None if isinstance(step, Reply) else step, self._cancel)

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


# -- exhaustive fault exploration ---------------------------------------------

#: The provider fault invariants (catalogued in docs/TESTING.md).
PROVIDER_INVARIANTS = (
    "no unaccepted reply", "every send booked", "single acceptance", "bounded attempts",
    "no retry after delivery", "unknown is not zero", "secret never leaks", "deterministic",
    "no resend after poisoning", "bounded exploration",
)
#: The fake key every explored world uses; it must never surface anywhere.
EXPLORER_KEY = "sk-explorer-SECRET-0000"

_INDEXED = {
    "read_timeout": lambda k: ReadFail(k, "timeout"),
    "read_reset": lambda k: ReadFail(k, "network"),
    "truncate": Truncate,
    "cancel": CancelAt,
    "garbage": Garbage,
    "interrupt": Interrupt,
}
_UNINDEXED = {
    "connect_timeout": ConnectFail("timeout"),
    "connect_refused": ConnectFail("network"),
    "write_fail": WriteFail(),
    "status_401": Status(401),
    "status_429": Status(429),
    "status_503": Status(503),
    "finish_length": Reply(finish_reason="length"),
    "no_usage": Reply(usage=None),
}
_FIRSTS = ("connect_timeout", "read_timeout@0", "truncate@0", "status_503", "status_429",
           "garbage@0")
_RETRY_AFTER = ("2", "9999", "-1", "soon")


def _faults() -> dict[str, Step]:
    faults: dict[str, Step] = {}
    for name, make in _INDEXED.items():
        for index in range(len(REFERENCE_REPLY.lines()) + 1):
            faults[f"{name}@{index}"] = make(index)
    faults.update(_UNINDEXED)
    return faults


@dataclass(frozen=True)
class _Point:
    name: str
    steps: tuple[Step, ...]
    attempts: int
    consumer: bool = False
    approve: frozenset[int] | None = None  # None: a trusted handle needing no approval
    fail_booking: tuple[str, ...] = ()


def _points() -> list[_Point]:
    faults = _faults()
    points = []
    for name, fault in faults.items():
        for consumer in (True, False):
            mode = "consumer" if consumer else "no_consumer"
            points.append(_Point(f"d1:{name}:{mode}", (fault, REFERENCE_REPLY), 2, consumer))
    for first in _FIRSTS:
        for second in [*faults, "deny"]:
            for consumer in (True, False):
                mode = "consumer" if consumer else "no_consumer"
                if second == "deny":
                    steps, approve = (faults[first], REFERENCE_REPLY), frozenset({1})
                else:
                    steps, approve = (faults[first], faults[second], REFERENCE_REPLY), \
                        frozenset({1, 2, 3})
                points.append(_Point(f"d2:{first}>{second}:{mode}", steps, 3, consumer, approve))
    points.append(_Point("booking:precise", (REFERENCE_REPLY,), 2, fail_booking=("precise",)))
    points.append(_Point("booking:fallback", (REFERENCE_REPLY,), 2,
                         fail_booking=("precise", "fallback")))
    for value in _RETRY_AFTER:
        points.append(_Point(f"retry_after:{value}", (Status(429, retry_after=value),
                                                      REFERENCE_REPLY), 2))
    return points


@dataclass(frozen=True)
class ProviderFaultReport:
    seed: int
    runs: int
    points: tuple[str, ...]
    traces: Mapping[str, tuple]
    violations: tuple

    def assert_ok(self) -> None:
        if self.violations:
            raise AssertionError("\n".join(v.format() for v in self.violations))


def _world(point: _Point, adapter_factory, transport_factory):
    from grimoire.core.inference import InferencePolicy, TurnUsageLedger, plan_turn
    from grimoire.providers import (
        ChatMessage, ModelRequest, ProviderEndpoint, ProviderRegistry, RouteChoice,
        StaticCredentials,
    )
    from grimoire.providers.adapter import ProviderAdapter
    from grimoire.providers.meter import TurnMeter
    from grimoire.security import (
        Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest,
    )

    authz = Authorizer({"human:owner"})
    principal = "app:runtime" if point.approve is None else "app:worker"
    authz.add_grant(Grant("g", principal, frozenset({Permission.EXECUTE}),
                          ResourcePath(("providers",)), "human:owner"))
    handle = authz.handle_for(principal, trusted=point.approve is None)

    def approvals(label: str, attempt: int, payload: bytes) -> str | None:
        if point.approve is None or attempt not in point.approve:
            return None
        provider = label.split("@", 1)[0]
        authz.register_approval(Approval(f"ap-{attempt}", principal, Permission.EXECUTE,
                                         ResourcePath(("providers", provider)),
                                         payload_digest(payload), "human:owner"))
        return f"ap-{attempt}"

    transport = (transport_factory or ScriptedTransport)(list(point.steps) + [REFERENCE_REPLY])
    clock = ScriptedClock()
    registry = ProviderRegistry([ProviderEndpoint("primary", "https://primary.example/v1"),
                                 ProviderEndpoint("backup", "https://backup.example/v1")])
    adapter = (adapter_factory or ProviderAdapter)(
        registry, StaticCredentials({"primary": EXPLORER_KEY, "backup": EXPLORER_KEY}), authz,
        transport, clock=clock, wait=clock.wait)
    plan = plan_turn("explore", "turn", present_actor_ids=["npc"], speaking_npc_ids=["npc"],
                     policy=InferencePolicy(max_attempts_per_call=point.attempts))
    meter = TurnMeter(TurnUsageLedger(plan),
                      _fail_booking=(lambda stage: stage in point.fail_booking)
                      if point.fail_booking else None)
    routes = (RouteChoice("primary", "model-a"), RouteChoice("backup", "model-b"))[:point.attempts]
    request = ModelRequest([ChatMessage("user", "explore")], max_output_tokens=16)

    def execute():
        return adapter.execute(meter, plan.calls[0], request, routes, handle,
                               approvals=approvals if point.approve is not None else None,
                               on_delta=(lambda delta: None) if point.consumer else None)
    return adapter, transport, clock, meter, authz, execute


def _run_point(point: _Point, adapter_factory, transport_factory, max_sends: int,
               max_output_tokens: int):
    import traceback

    from grimoire.providers import CallRefused
    from grimoire.testing.explore import Violation

    adapter, transport, clock, meter, authz, execute = _world(
        point, adapter_factory, transport_factory)
    result, raised, leaked_text = None, None, ""
    try:
        result = execute()
    except BaseException as error:  # an Interrupt step, or a broken adapter
        raised = type(error).__name__
        leaked_text = "".join(traceback.TracebackException.from_exception(
            error, capture_locals=True).format())
    connects = len(transport.connects)
    sends = len(transport.sends)
    reports = meter.reports(meter.ledger.plan.calls[0].call_id)
    status = meter.status(meter.ledger.plan.calls[0].call_id)
    again = None
    if (result is not None and result.status == "accepted") or status == "poisoned":
        before = len(transport.connects)
        try:
            execute()
            again = "executed"
        except CallRefused:
            again = "refused"
        except BaseException as error:
            again = type(error).__name__
        again = (again, len(transport.connects) - before)

    trace = (tuple(transport.connects), tuple(s.body_digest for s in transport.sends),
             tuple((r.attempt, r.outcome, r.error_kind, r.usage) for r in reports),
             None if result is None else result.status, tuple(clock.waits), raised, again)
    violations = []

    def fail(invariant: str, detail: str) -> None:
        violations.append(Violation(invariant, point.name, detail,
                                    tuple(str(part) for part in trace[2])))

    booked = meter.ledger.attempts
    if result is not None and result.reply is not None:
        last = reports[-1] if reports else None
        if (result.status != "accepted" or last is None or last.outcome != "completed"
                or last.finish_reason != "stop"):
            fail("no unaccepted reply", f"reply returned with status {result.status}")
    if status != "poisoned":
        if sends != sum(1 for a in booked if a.outcome != "not_sent") or connects != len(booked):
            fail("every send booked", f"{connects} connects, {sends} sends, {len(booked)} booked")
    if again is not None:
        if status == "poisoned" and again[1]:
            fail("no resend after poisoning", f"re-execute after poisoning connected {again[1]}x")
        elif status != "poisoned" and again[1]:
            fail("single acceptance", f"re-execute after acceptance connected {again[1]}x")
    if connects > point.attempts:
        fail("bounded attempts", f"{connects} connects for {point.attempts} attempts")
    if any(r.delivered for r in reports[:-1]):
        fail("no retry after delivery", "an attempt followed one that delivered text")
    for attempt in booked:
        if attempt.outcome != "not_sent" and 0 in (attempt.usage.input_tokens,
                                                   attempt.usage.output_tokens):
            fail("unknown is not zero", f"attempt {attempt.attempt} booked a zero count")
    texts = [repr(result), str(getattr(result, "error", None)), repr(meter),
             repr(meter.summary()), repr(meter.ledger), repr(authz.audit()), repr(reports),
             repr(adapter), repr(transport.sends), leaked_text]
    if any(EXPLORER_KEY in text for text in texts):
        fail("secret never leaks", "the provider key appeared in a result, report or error")
    consumed = point.steps[:connects]
    tokens = sum(step.output_tokens() for step in consumed if isinstance(step, Reply))
    if sends > max_sends or tokens > max_output_tokens:
        fail("bounded exploration", f"{sends} sends, {tokens} scripted output tokens")
    return trace, violations


def explore_provider_faults(*, seed: int = 0, max_runs: int = 2000, max_sends: int = 4,
                            max_output_tokens: int = 64, adapter_factory=None,
                            transport_factory=None) -> ProviderFaultReport:
    """Run every point (each twice, for determinism) and check every invariant.

    Deterministic: the seed only shuffles the run order, and is recorded.
    """
    import random

    from grimoire.testing.explore import Violation

    points = _points()
    random.Random(seed).shuffle(points)
    traces: dict[str, tuple] = {}
    violations: list = []
    runs = 0
    for point in points:
        if runs >= max_runs:
            violations.append(Violation("bounded exploration", point.name,
                                        f"more than max_runs={max_runs} points"))
            break
        runs += 1
        trace, found = _run_point(point, adapter_factory, transport_factory, max_sends,
                                  max_output_tokens)
        repeat, _ = _run_point(point, adapter_factory, transport_factory, max_sends,
                               max_output_tokens)
        if repeat != trace:
            found.append(Violation("deterministic", point.name, "a rerun produced another trace"))
        traces[point.name] = trace
        violations.extend(found)
    return ProviderFaultReport(seed, runs, tuple(p.name for p in points[:runs]), traces,
                               tuple(violations))
