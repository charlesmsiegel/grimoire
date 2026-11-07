"""One call's trace: where its wall time went, operation by operation, and what each model call did.

A trace is built by one ``TraceBuilder`` for one runner call (``open``, ``post``, ``generate``...), with a
clock the caller passes in: this package reads no clock itself. Spans nest; a span's *self* time is its
wall time less its direct children's, so a breakdown never counts a second twice.

A trace holds operation names from a fixed vocabulary, durations, integer counts and, for each model call
attempt, its usage and provider timing. It never holds prompt, reply or post text, names, or paths. It
holds route labels (the provider and model a call used, never a key) and whatever ids the caller puts in
a call's ``CallAttempt``: the scene runner puts none (an actor is its place in the turn's plan). It is
handed to the caller and never persisted.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from grimoire.core.inference import CallAttempt

#: How a call's provider latency is known: timed against the real clock on a provider-reported attempt
#: (``measured``), timed against a test or scripted clock (``synthetic``: never evidence of real latency),
#: or not timed at all (``unreported``: the attempt's report was lost, or it was never sent).
Latency = Literal["measured", "synthetic", "unreported"]
LATENCIES: frozenset[str] = frozenset({"measured", "synthetic", "unreported"})
#: A span or trace that ended normally. Anything else is the refusal reason or the exception's type name.
OK = "ok"


class TraceError(ValueError):
    """A span or sample that cannot be recorded as given (a caller bug, raised at once)."""


def _name(value: object, what: str) -> str:
    if (type(value) is not str or not value or len(value) > 64 or not value.isascii() or not value.isprintable()
            or " " in value):
        raise TraceError(f"{what} must be a short printable ASCII name with no spaces")
    return value


def _seconds(value: object, what: str) -> float:
    if type(value) not in (int, float) or value != value or value < 0:  # NaN fails ``value != value``
        raise TraceError(f"{what} must be a non-negative number of seconds")
    return float(value)


def _counts(values: Mapping[str, object]) -> Mapping[str, int]:
    checked = {}
    for key, value in values.items():
        if type(value) is not int or value < 0:
            raise TraceError(f"count {key!r} must be a non-negative int")
        checked[_name(key, "a count's name")] = value
    return MappingProxyType(dict(sorted(checked.items())))


@dataclass(frozen=True, slots=True)
class Span:
    """One timed operation. ``parent`` is the index of the enclosing span in ``Trace.spans`` (``None`` for the
    root); ``started`` is seconds since the trace began. ``status`` is ``ok``, a refusal reason, or the type
    name of an exception that left it. ``counts`` are what it handled (files read, lines heard...)."""

    operation: str
    parent: int | None
    started: float
    seconds: float
    status: str = OK
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class CallSample:
    """One attempt at one planned model call: its booked usage (``attempt``, as on the turn's ledger) and how
    long the provider took. ``first_byte_s`` and ``total_s`` are ``None`` when ``latency`` is ``unreported``.

    ``lost`` marks a call that was sent but whose booking failed: nothing about it is known (its ``attempt``
    stands in, with every count unreported), so it is never left out and its cost is never complete."""

    attempt: CallAttempt
    stage: str
    actor_id: str | None
    latency: Latency
    first_byte_s: float | None = None
    total_s: float | None = None
    lost: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, CallAttempt):
            raise TraceError("a call sample holds the CallAttempt its ledger booked")
        _name(self.stage, "a call's stage")
        if self.latency not in LATENCIES:
            raise TraceError(f"latency is one of {sorted(LATENCIES)}")
        if type(self.lost) is not bool or (self.lost and self.latency != "unreported"):
            raise TraceError("a lost call is a bool, and a lost call was never timed")
        timed = (self.first_byte_s, self.total_s)
        if self.latency == "unreported":
            if timed != (None, None):
                raise TraceError("an unreported latency has no timings")
        else:
            if self.total_s is None:
                raise TraceError("a timed attempt has a total time")
            _seconds(self.total_s, "total_s")
            if self.first_byte_s is not None and _seconds(self.first_byte_s, "first_byte_s") > self.total_s:
                raise TraceError("the first byte cannot come after the end")

    @property
    def call(self) -> str:
        """The planned call this attempt was for: ``<phase>:<stage>:<actor or scene>:<ordinal>``."""
        cid = self.attempt.call_id
        return f"{cid.phase}:{self.stage}:{cid.actor_id or 'scene'}:{cid.ordinal}"


@dataclass(frozen=True, slots=True)
class OperationTime:
    """All spans of one operation in a trace: how many, their wall time, and their self time."""

    operation: str
    count: int
    seconds: float
    self_seconds: float


@dataclass(frozen=True)
class Trace:
    """A finished trace: its spans in start order (the root first) and its model call attempts in order."""

    spans: tuple[Span, ...]
    calls: tuple[CallSample, ...] = ()

    def __post_init__(self) -> None:
        if not self.spans or self.spans[0].parent is not None:
            raise TraceError("a trace starts with its root span")
        for index, span in enumerate(self.spans[1:], 1):
            if span.parent is None or not 0 <= span.parent < index:
                raise TraceError("every span but the root has an earlier parent")

    @property
    def operation(self) -> str:
        return self.spans[0].operation

    @property
    def status(self) -> str:
        return self.spans[0].status

    @property
    def seconds(self) -> float:
        return self.spans[0].seconds

    def children(self, index: int) -> tuple[int, ...]:
        return tuple(i for i, span in enumerate(self.spans) if span.parent == index)

    def breakdown(self) -> tuple[OperationTime, ...]:
        """Each operation's count, wall and self time, the slowest self time first (ties by name). Self times
        add up to the root's wall time exactly, so nothing is counted twice and nothing is lost."""
        child_seconds = [0.0] * len(self.spans)
        for span in self.spans[1:]:
            child_seconds[span.parent] += span.seconds  # type: ignore[index]
        totals: dict[str, list[float]] = {}
        for index, span in enumerate(self.spans):
            entry = totals.setdefault(span.operation, [0, 0.0, 0.0])
            entry[0] += 1
            entry[1] += span.seconds
            entry[2] += max(0.0, span.seconds - child_seconds[index])
        found = [OperationTime(name, int(count), seconds, own) for name, (count, seconds, own) in totals.items()]
        return tuple(sorted(found, key=lambda each: (-each.self_seconds, each.operation)))

    def counts(self, operation: str) -> Mapping[str, int]:
        """Every count recorded on ``operation``'s spans, summed."""
        summed: dict[str, int] = {}
        for span in self.spans:
            if span.operation == operation:
                for key, value in span.counts.items():
                    summed[key] = summed.get(key, 0) + value
        return MappingProxyType(dict(sorted(summed.items())))


class _Open:
    """A span being timed, used as a context manager: ``count`` adds to its counts and ``fail`` sets its
    status. Leaving it by an exception sets the status to the refusal's reason or the exception's type."""

    __slots__ = ("_builder", "index", "counts", "status", "_started")

    def __init__(self, builder: TraceBuilder, index: int, started: float) -> None:
        self._builder = builder
        self.index = index
        self.counts: dict[str, int] = {}
        self.status = OK
        self._started = started

    def count(self, **values: int) -> None:
        for key, value in values.items():
            if type(value) is not int or value < 0:
                raise TraceError(f"count {key!r} must be a non-negative int")
            self.counts[key] = self.counts.get(key, 0) + value

    def fail(self, status: str) -> None:
        self.status = _name(status, "a status")

    def __enter__(self) -> _Open:
        return self

    def __exit__(self, kind, error, traceback) -> None:
        if error is not None and self.status == OK:
            self.status = error.reason if _reason(error) else type(error).__name__
        self._builder._close(self)


class TraceBuilder:
    """Builds one trace with ``clock`` (seconds, monotonic). Not shared between threads: a runner makes one
    per call. ``span`` nests; the first span opened is the root, and closing it finishes the trace."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._origin: float | None = None
        self._spans: list[list[object]] = []  # operation, parent, started, seconds, status, counts
        self._stack: list[int] = []
        self._calls: list[CallSample] = []
        self.trace: Trace | None = None

    def _now(self) -> float:
        value = self._clock()
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
            raise TraceError("the clock gives seconds")
        if self._origin is None:
            self._origin = float(value)
        return max(0.0, float(value) - self._origin)

    def span(self, operation: str, **counts: int) -> _Open:
        """A new span inside the innermost open one, to be used as ``with builder.span(...) as span:``."""
        if self.trace is not None:
            raise TraceError("this trace is finished")
        _name(operation, "an operation")
        parent = self._stack[-1] if self._stack else None
        if parent is None and self._spans:
            raise TraceError("a trace has one root span")
        started = self._now()
        index = len(self._spans)
        self._spans.append([operation, parent, started, 0.0, OK, {}])
        handle = _Open(self, index, started)
        handle.count(**counts)
        self._stack.append(index)
        return handle

    def _close(self, handle: _Open) -> None:
        if not self._stack or self._stack[-1] != handle.index:
            raise TraceError("spans close innermost first")
        self._stack.pop()
        record = self._spans[handle.index]
        record[3] = max(0.0, self._now() - handle._started)
        record[4] = handle.status
        record[5] = dict(handle.counts)
        if not self._stack:
            self._finish()

    def sample(self, sample: CallSample) -> None:
        if not isinstance(sample, CallSample):
            raise TraceError("a call sample is a CallSample")
        self._calls.append(sample)

    def _finish(self) -> None:
        spans = tuple(Span(op, parent, started, seconds, status, _counts(counts))  # type: ignore[arg-type]
                      for op, parent, started, seconds, status, counts in self._spans)
        self.trace = Trace(spans, tuple(self._calls))


def _reason(error: BaseException) -> bool:
    """A refusal carries a short reason (``SceneRefused``, ``GenerationError``): that is the span's status."""
    reason = getattr(error, "reason", None)
    try:
        _name(reason, "a reason")
    except TraceError:
        return False
    return True


class TraceLog:
    """A sink that keeps the last ``keep`` traces, newest last: give ``log.record`` to a runner as its tracer."""

    def __init__(self, keep: int = 64) -> None:
        if type(keep) is not int or keep < 1:
            raise TraceError("keep at least one trace")
        self._traces: deque[Trace] = deque(maxlen=keep)

    def record(self, trace: Trace) -> None:
        if not isinstance(trace, Trace):
            raise TraceError("a trace log holds Trace values")
        self._traces.append(trace)

    @property
    def traces(self) -> tuple[Trace, ...]:
        return tuple(self._traces)

    def of(self, operation: str) -> tuple[Trace, ...]:
        return tuple(trace for trace in self._traces if trace.operation == operation)


def merge_counts(traces: Iterable[Trace], operation: str) -> Mapping[str, int]:
    """``operation``'s counts summed over several traces."""
    summed: dict[str, int] = {}
    for trace in traces:
        for key, value in trace.counts(operation).items():
            summed[key] = summed.get(key, 0) + value
    return MappingProxyType(dict(sorted(summed.items())))
