"""The runner's tracing hook (048): one trace per public call, handed to the caller's sink.

A runner given a ``tracer`` (any callable taking a ``grimoire.tracing.Trace``) times each public call as a
root span and the work inside it as child spans; without one, every span here is a no-op and nothing is
timed. The trace being built is held in a context variable, so concurrent calls on one runner (threads, or
tasks) each build their own, and helpers open spans without being handed the trace.

Tracing never changes what a call does or returns. A sink that raises loses that trace, never the call's
result; a clock that raises or gives no number loses the trace being built, and the call goes on untraced;
a refusal is recorded as the root span's status before it propagates unchanged.

A trace names no actor or campaign: each model call's ids are replaced by its place in the turn's plan
(``npc-1``, ``npc-2``... in plan order), because an imported actor's instance id can be its character's name.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextvars import ContextVar

from grimoire.core.inference import CallAttempt, CallId, TokenUsage
from grimoire.providers.meter import TurnMeter
from grimoire.tracing import CallSample, Trace, TraceBuilder, TraceError


class _Run:
    """The trace one call is building; ``broken`` once the builder failed (the trace is then dropped)."""

    __slots__ = ("builder", "broken")

    def __init__(self, builder: TraceBuilder) -> None:
        self.builder = builder
        self.broken = False


_CURRENT: ContextVar[_Run | None] = ContextVar("grimoire_play_trace", default=None)


class _NoSpan:
    """What ``span`` gives when nothing is being traced: it accepts counts and statuses and records nothing."""

    def count(self, **values: int) -> None:
        return None

    def fail(self, status: str) -> None:
        return None

    def __enter__(self) -> _NoSpan:
        return self

    def __exit__(self, *exc) -> None:
        return None


_NO_SPAN = _NoSpan()


class _Guarded:
    """A span whose every failure (a bad clock, a bad count) breaks the trace and never reaches the call."""

    __slots__ = ("_run", "_handle")

    def __init__(self, run: _Run, handle) -> None:
        self._run = run
        self._handle = handle

    def count(self, **values: int) -> None:
        self._try(lambda: self._handle.count(**values))

    def fail(self, status: str) -> None:
        self._try(lambda: self._handle.fail(status))

    def _try(self, step: Callable[[], object]) -> None:
        if self._run.broken:
            return
        try:
            step()
        except Exception:  # the trace is lost, never the call
            self._run.broken = True

    def __enter__(self) -> _Guarded:
        return self

    def __exit__(self, kind, error, traceback) -> None:
        self._try(lambda: self._handle.__exit__(kind, error, traceback))
        return None  # never swallows the call's own exception


def _open(run: _Run, operation: str, counts: dict[str, int]):
    if run.broken:
        return _NO_SPAN
    try:
        return _Guarded(run, run.builder.span(operation, **counts))
    except Exception:
        run.broken = True
        return _NO_SPAN


def span(operation: str, **counts: int):
    """A child span of the call being traced, or a no-op when none is."""
    run = _CURRENT.get()
    return _NO_SPAN if run is None else _open(run, operation, counts)


def traced(operation: str):
    """Decorate a public ``SceneRunner`` method: with a tracer set, the call is one trace whose root span is
    ``operation``. A call made while another is being traced (a runner method calling another) is a span of it."""

    def wrap(method):
        @functools.wraps(method)
        def run(self, *args, **kwargs):
            sink: Callable[[Trace], None] | None = self.tracer
            if sink is None:
                return method(self, *args, **kwargs)
            if _CURRENT.get() is not None:
                with span(operation):
                    return method(self, *args, **kwargs)
            current = _Run(TraceBuilder(self.clock))
            token = _CURRENT.set(current)
            try:
                with _open(current, operation, {}):
                    return method(self, *args, **kwargs)
            finally:
                _CURRENT.reset(token)
                trace = None if current.broken else current.builder.trace
                if trace is not None:
                    try:
                        sink(trace)
                    except Exception:  # an observer's bug loses its trace, never the call's result
                        pass
        return run
    return wrap


def _anonymous(attempt: CallAttempt, actors: dict[str | None, str]) -> CallAttempt:
    """``attempt`` with no campaign, turn or actor id: the actor is its place in the plan."""
    cid = attempt.call_id
    actor = None if cid.actor_id is None else actors.setdefault(cid.actor_id, f"npc-{len(actors) + 1}")
    return CallAttempt(CallId("campaign", "turn", cid.phase, cid.stage, actor, cid.ordinal), attempt.attempt,
                       attempt.usage, attempt.measurement, attempt.outcome, attempt.route)


def record_calls(meter: TurnMeter) -> None:
    """One sample per booked attempt of the meter's plan, with the provider timing the adapter reported.

    An attempt whose report is missing (its booking fell back) is ``unreported``; timing against a scripted
    or test clock is ``synthetic`` (the adapter says which). A call whose booking failed entirely after it was
    sent (poisoned: that attempt is not on the ledger, though earlier attempts of the call may be) gets one
    ``lost`` sample for that attempt with every count unreported, so it is never missing from the trace and
    its cost is never complete."""
    current = _CURRENT.get()
    if current is None or current.broken:
        return
    try:
        _record(current.builder, meter)
    except Exception:  # a ledger or report the trace cannot read: the trace is lost, never the call
        current.broken = True


def _record(builder: TraceBuilder, meter: TurnMeter) -> None:
    plan = meter.ledger.plan
    stages = {call.call_id: call.stage.value for call in plan.calls}
    actors: dict[str | None, str] = {}
    for call in plan.calls:  # numbered in plan order, so the same speaker order gives the same labels
        if call.call_id.actor_id is not None:
            actors.setdefault(call.call_id.actor_id, f"npc-{len(actors) + 1}")
    booked: dict[CallId, int] = {}  # each call's last booked attempt number
    for attempt in meter.ledger.attempts:
        booked[attempt.call_id] = max(booked.get(attempt.call_id, 0), attempt.attempt)
        report = next((each for each in meter.reports(attempt.call_id) if each.attempt == attempt.attempt), None)
        if report is None or report.total_s is None:
            latency, first, total = "unreported", None, None
        else:
            latency, total = report.latency_measurement, report.total_s
            first = report.first_byte_s if report.first_byte_s is not None and report.first_byte_s <= total else None
        _sample(builder, _anonymous(attempt, actors), stages.get(attempt.call_id, "unplanned"), latency, first,
                total)
    for cid in sorted(meter.poisoned_call_ids, key=str):
        # the attempt whose booking failed: the first, or a retry after attempts that were booked
        lost = CallAttempt(cid, booked.get(cid, 0) + 1, TokenUsage(None, None), "estimated", "failed")
        _sample(builder, _anonymous(lost, actors), stages.get(cid, "unplanned"), "unreported", None, None,
                lost=True)


def _sample(builder: TraceBuilder, attempt: CallAttempt, stage: str, latency, first, total, *,
            lost: bool = False) -> None:
    actor = attempt.call_id.actor_id
    try:
        sample = CallSample(attempt, stage, actor, latency, first, total, lost=lost)
    except TraceError:  # a timing the adapter could not give sensibly: say it is unknown, never fail the call
        sample = CallSample(attempt, stage, actor, "unreported", lost=lost)
    builder.sample(sample)
