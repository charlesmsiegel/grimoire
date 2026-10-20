"""Bounded tool-run contracts and worker ownership, including abandoned threads."""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from . import _definitions
from ._definitions import ToolCall as ToolCall
from ._definitions import ToolContext as ToolContext
from ._definitions import ToolOutput as ToolOutput
from ._definitions import Toolset as Toolset

# ---- the loop's shapes (01g-S4; spec 3.7, 3.9, 3.10) ----
#: The shortest window a turn could plausibly answer in: a turn is not sent
#: when the run's wall clock has less than this left (spec 3.9).
MIN_TURN_SECONDS = 5.0

#: The default executor's own bounded thread pool (spec 3.6): never the
#: shared default executor, which also serves httpx's DNS lookups.
TOOL_WORKERS = 4

#: The limits a run can stop on (`LoopResult.limit`). `spend` is 01g-S5's.
LIMITS: tuple[str, ...] = ("turns", "tool_calls", "decisions", "wall", "spend",
                           "result_chars", "context")

#: The results a model is sent for a call the loop did not run as asked.
NOT_RUN = "not run: the run's tool budget is spent"
CUT_OFF = "your arguments were cut off; call again with a shorter request"
TOOL_FAILED = "the tool failed"
TIMED_OUT = "timed out"
CAPACITY = "tool capacity exhausted"

#: How far a counted prompt is scaled up before it is priced (spec 3.9): a
#: local count is not an upper bound -- chars/4 under-counts CJK text, and a
#: cl100k-style encoder under-counts Claude's tokenizer. Structural; to be
#: tuned against real runs.
PROJECTION_MARGIN = 1.5

#: A tool-call id every provider accepts (spec 3.11).
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@dataclass(frozen=True)
class RunBudget:
    """What one run may spend, checked before every send and execution (spec
    3.9). The defaults are structural and to be tuned against real runs.
    `wall_seconds` None is `llm_call_budget`; `<= 0` is no wall."""
    max_turns: int = 6
    max_tool_calls: int = 12
    max_decisions: int = 2
    wall_seconds: float | None = None
    #: Each turn's output cap (`inference.call_chain(max_tokens=)`). None
    #: sends each turn the preset's own cap, and none where it sets none, as
    #: a plain generation is sent (roadmap 02 §8.3) -- never with a spend
    #: ceiling, whose projection needs a bound.
    max_output_tokens: int | None = 2048
    max_result_chars_total: int = 48_000
    reserve_final: bool = True
    #: The most a run's projected spend may reach, in USD (01g-S5); None is
    #: no spend axis. A projection, never accounting (`inference.price_for`).
    spend_ceiling_usd: float | None = None
    #: Whether the finalize turn is sent `tools/finalize.j2`'s instruction.
    #: False sends the run's messages as they stand (tools still withheld): a
    #: creative turn whose continuation IS the answer (roadmap 02 §8.5),
    #: where "give your final answer now" would be prompt text in the story.
    finalize_note: bool = True
    #: The most tokens a turn's prompt may hold, counted as the spend guard
    #: counts one (`tokens.count_if_loaded`: the encoder when loaded, else
    #: four characters a token) over the messages and the tool definitions; a
    #: send past it is refused and the run stops on `context`. None is no
    #: stop. A caller derives it from 01i's `prompt_ceiling` (roadmap 12 §7.3);
    #: the loop never guesses a window.
    max_prompt_tokens: int | None = None

    def __post_init__(self) -> None:
        ceiling = self.spend_ceiling_usd
        if ceiling is not None and (isinstance(ceiling, bool)
                                    or not isinstance(ceiling, (int, float))
                                    or not math.isfinite(ceiling) or ceiling < 0):
            raise ValueError("RunBudget.spend_ceiling_usd is a finite amount of at least 0")
        for name in ("max_turns", "max_tool_calls", "max_output_tokens",
                     "max_result_chars_total"):
            if name == "max_output_tokens" and self.max_output_tokens is None:
                if ceiling is not None:
                    raise ValueError("RunBudget.max_output_tokens bounds a spend ceiling's "
                                     "projection, so it is not None beside one")
                continue
            if not _positive_int(getattr(self, name)):
                raise ValueError(f"RunBudget.{name} is a positive whole number")
        if isinstance(self.max_decisions, bool) or not isinstance(self.max_decisions, int) \
                or self.max_decisions < 0:
            raise ValueError("RunBudget.max_decisions is a whole number")
        if self.max_prompt_tokens is not None and not _positive_int(self.max_prompt_tokens):
            raise ValueError("RunBudget.max_prompt_tokens is a positive whole number or None")


@dataclass(frozen=True)
class TraceEntry:
    """One thing a run did (spec 3.10): a model turn, a tool call, a decision
    or the stop -- with refs and sizes, never argument or result text."""
    turn: int
    kind: Literal["model", "tool", "decide", "stop"]
    name: str = ""
    call_id: str = ""
    ok: bool = True
    refs: tuple[str, ...] = ()
    chars: int = 0
    elapsed_ms: int = 0
    note: str = ""


@dataclass(frozen=True)
class LoopResult:
    """How a run ended (spec 3.7). `error` is the `LLMError` a `failed` run
    stopped on.

    `declined` holds calls the model made that were never run, of two kinds
    that never share a run, told apart by the note on each call's `tool`
    trace entry. `declined: after_text` (C6, `decline_after_text`): calls a
    turn ended with after it had shown visible text, and the run is
    `completed` with that text. `declined: final` (R2-2): calls a finalize
    turn made anyway -- in practice on a chain that sends `tool_choice:
    none` as `auto` (z.ai) -- whatever the run's status, and possibly
    before any visible text. A consumer that wants only C6's meaning reads the trace notes."""
    status: Literal["completed", "budget_exhausted", "failed"]
    limit: str = ""
    text: str = ""
    final: dict | None = None
    final_call: ToolCall | None = None
    declined: tuple[ToolCall, ...] = ()
    proposals: tuple[dict, ...] = ()
    messages: tuple[dict, ...] = ()
    trace: tuple[TraceEntry, ...] = ()
    rows: tuple[dict, ...] = ()
    error: Exception | None = None
    run_id: str = ""


@dataclass(frozen=True)
class LoopEvent:
    """One step of a run as it happens: `text` (a turn's delta), `turn_end`
    (`interstitial` for a turn that ended in calls), `tool_start`,
    `tool_end` and, last, `done` with the result."""
    kind: Literal["text", "turn_end", "tool_start", "tool_end", "done"]
    turn: int = 0
    delta: str = ""
    finish: str = ""
    interstitial: bool = False
    call_id: str = ""
    name: str = ""
    ok: bool = True
    chars: int = 0
    refs: tuple[str, ...] = ()
    result: LoopResult | None = None


class RunRefused(Exception):  # noqa: N818 - the spec's name for it (01g 3.9)
    """A run refused before anything is sent (spec 3.9): `kind` says why
    (`unpriceable`), the message what to do about it."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _cut_marker(omitted: int) -> str:
    return f"\n[truncated: {omitted} more characters]"


def truncate(text: str, limit: int) -> str:
    """`text` held to `limit` characters, the marker saying how much was cut
    included: room for it (at the widest count it could carry) comes out of
    the kept text, so the result never passes `limit`. A limit too small for
    the marker keeps the first `limit` characters and no marker -- the cap is
    the promise, the marker a courtesy."""
    limit = max(0, limit)
    if len(text) <= limit:
        return text
    keep = limit - len(_cut_marker(len(text)))
    if keep < 0:
        return text[:limit]
    return text[:keep] + _cut_marker(len(text) - keep)


def loop_id(run_id: str, n: int) -> str:
    """The loop's own call id: `gc_<run8>_<n>`, inside `SAFE_ID`."""
    return f"gc_{re.sub(r'[^A-Za-z0-9]', '', run_id)[:8]}_{n}"


def rewrite_ids(messages: list[dict], mint: Callable[[], str]) -> list[dict]:
    """`messages` with every call id renamed to a fresh `mint()`, call and
    result alike, on new messages; the rest as they were. Renamed per
    occurrence, never by id: a provider may reuse an id from one turn to the
    next, and two calls must not come out sharing one. A result is paired
    with the call of that id in the latest assistant turn before it."""
    out: list[dict] = []
    current: dict[str, str] = {}
    for message in messages:
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        renamed = message
        if isinstance(calls, list):
            current = {}
            fresh = []
            for c in calls:
                if isinstance(c, dict) and c.get("id"):
                    current[c["id"]] = new = mint()
                    fresh.append({**c, "id": new})
                else:
                    fresh.append(c)
            renamed = {**message, "tool_calls": fresh}
        elif (isinstance(message, dict) and message.get("role") == "tool"
              and message.get("tool_call_id") in current):
            renamed = {**message, "tool_call_id": current[message["tool_call_id"]]}
        out.append(renamed)
    return out


class _Running:
    """Tool threads running in any executor's pool, and the condition a
    caller waits on for none (`idle`)."""

    def __init__(self) -> None:
        self.count = 0
        self.changed = threading.Condition()

    def started(self) -> None:
        with self.changed:
            self.count += 1

    def finished(self, _future: Future) -> None:
        with self.changed:
            self.count -= 1
            self.changed.notify_all()


_RUNNING = _Running()


def idle(timeout: float | None = None) -> bool:
    """True once no tool thread of any executor is running -- one a
    timed-out wait abandoned included -- or False at `timeout`. For a caller
    that must not move what a thread could still read: an eval leaving its
    throwaway home, which a late read would otherwise resolve against
    whatever `GRIMOIRE_HOME` names next."""
    with _RUNNING.changed:
        return _RUNNING.changed.wait_for(lambda: _RUNNING.count == 0, timeout)


class _Workers:
    """A bounded thread pool that says when it is full rather than queueing:
    a thread a timed-out wait abandoned keeps its worker until it returns."""

    def __init__(self, size: int):
        self._pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="grimoire-tool")
        self._size = size
        self._busy = 0
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)

    def submit(self, fn: Callable, *args: object) -> Future | None:
        with self._lock:
            if self._busy >= self._size:
                return None
            self._busy += 1
        _RUNNING.started()
        future = self._pool.submit(fn, *args)
        future.add_done_callback(self._release)
        future.add_done_callback(_RUNNING.finished)
        return future

    def _release(self, _future: Future) -> None:
        with self._lock:
            self._busy -= 1
            self._idle.notify_all()

    def drain(self, timeout: float | None = None) -> bool:
        """True once none of this pool's threads is running -- one a
        timed-out wait abandoned included -- or False at `timeout`."""
        with self._idle:
            return self._idle.wait_for(lambda: self._busy == 0, timeout)


class Registered:
    """The default executor (`registered`): an `Execute`, plus `drain`, which
    waits out the threads its pool still runs."""

    def __init__(self, toolset: Toolset, workers: int):
        self._toolset = toolset
        self._pool = _Workers(workers)

    async def __call__(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        spec = self._toolset.get(call.name)
        if spec is None:
            raise _definitions.ToolError(f"there is no tool named {call.name!r}")
        args = call.arguments or {}
        if inspect.iscoroutinefunction(spec.fn):
            return await spec.fn(args, ctx)
        future = self._pool.submit(spec.fn, args, ctx)
        if future is None:
            raise _definitions.ToolError(CAPACITY)
        result = await asyncio.wrap_future(future)
        return await result if inspect.isawaitable(result) else result

    def drain(self, timeout: float | None = None) -> bool:
        """True once no thread this executor started is running, or False at
        `timeout`. Blocks: call it off the loop. For a run that holds what a
        late thread could still read -- the store root -- until it returns."""
        return self._pool.drain(timeout)


def registered(toolset: Toolset, *, workers: int = TOOL_WORKERS) -> Registered:
    """The default executor (spec 3.6): each call dispatched by name to its
    `ToolSpec.fn`. An async tool is awaited; a sync one runs on this
    executor's own bounded pool, and once every worker is held -- by threads
    the loop stopped waiting on -- a call is refused with `CAPACITY` rather
    than queued. The loop bounds the wait; nothing here kills a thread, and
    `Registered.drain` waits out the ones it left."""
    return Registered(toolset, workers)


async def text_deltas(events: AsyncIterator[LoopEvent],
                      on_done: Callable[[LoopResult], None] | None = None
                      ) -> AsyncIterator[str]:
    """A loop's events (`inference.stream_tools`) as the plain iterator of
    text deltas `generate(stream=True)` yields -- heartbeats (`""`) included
    -- so a streaming caller keeps its display and its watcher as they are
    (01g-C6). `on_done` is handed the run's result. Closing this closes the
    run."""
    try:
        async for event in events:
            if event.kind == "text":
                yield event.delta
            elif event.kind == "done" and on_done is not None and event.result is not None:
                on_done(event.result)
    finally:
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()
