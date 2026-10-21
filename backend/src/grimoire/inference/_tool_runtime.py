"""One bounded tool run: its transitions and driver cleanup, over the parts
that hold its state (`_tool_state`), history (`_tool_transcript`), targets
(`_tool_targets`), send guards (`_tool_guard`), model turns
(`_tool_sender`) and tool calls (`_tool_dispatch`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable
from functools import partial
from typing import Literal

from .. import llm, schemas, store, tool_calls, wire
from ..llm import _lifecycle as llm_lifecycle
from ..llm_errors import LLMError
from ..tool_calls import _runs as tool_runs
from . import _contracts, _tool_policy
from ._contracts import LoopOptions
from ._contracts import TurnCapture as TurnCapture
from ._pricing import Price as Price
from ._tool_dispatch import _RunView as _RunView
from ._tool_dispatch import _ToolDispatch
from ._tool_guard import _SendGuard
from ._tool_sender import _TurnSender
from ._tool_state import _RunState
from ._tool_state import _Turn as _Turn
from ._tool_targets import _Targets
from ._tool_transcript import _Transcript


class _Loop:
    """One run; `events` runs it (spec 3.7). The loop decides each step and
    its parts hold the rest: where the run stands (`state`), its history
    (`transcript`), where each turn is sent (`targets`), what refuses a send
    (`guard`), each model turn through the facade (`sender`) and the tool
    calls (`tools`)."""

    def __init__(self, task: str, messages: list[dict], options: LoopOptions, *,
                 prices: dict[tuple[str, str], Price | None] | None = None,
                 streaming: bool = False):
        defs = options.toolset.definitions()
        self.state = _RunState(task, options)
        self.transcript = _Transcript(messages, options.run_id)
        self.targets = _Targets(options)
        self.guard = _SendGuard(options.budget, defs, prices or {})
        self.sender = _TurnSender(self.state, self.guard, defs, streaming=streaming)
        self.tools = _ToolDispatch(self.state, self.guard)
        #: Calls a finalize turn made anyway (R2-2): declined, never run.
        self.final_declined: tuple[tool_calls.ToolCall, ...] = ()

    # ---- the run ----
    async def events(self) -> AsyncGenerator[tool_calls.LoopEvent, None]:
        """The run, as events (spec 3.8): `text` deltas as they arrive, a
        `turn_end` per turn, `tool_start`/`tool_end` per call, and `done`
        last, after every meter has filed. The run itself is driven in a task
        of its own (`_drive`), so that while a turn or a tool is in flight
        this yields an empty `text` every `llm.HEARTBEAT_INTERVAL` -- a stream
        is never silent past the interval an SSE consumer's guard protects."""
        state, clock = self.state, self.state.clock
        state.root, clock.wall, clock.call_budget = await asyncio.to_thread(self._pin)
        queue: asyncio.Queue = asyncio.Queue()
        state.emit = queue.put_nowait
        runner = asyncio.create_task(self._drive())
        try:
            while True:
                item = await _next_or_beat(queue)
                if item is None:
                    yield tool_calls.LoopEvent("text", turn=state.turns, delta="")
                    continue
                if isinstance(item, BaseException):
                    raise item
                assert isinstance(item, tool_calls.LoopEvent)
                yield item
                if item.kind == "done":
                    return
        finally:
            await _stopped(runner)

    async def _drive(self) -> None:
        """Every step of the run, each event handed to `events`; its result as
        `done`, or the exception that ended it."""
        state = self.state
        me = asyncio.current_task()
        if me is not None:
            _tool_policy._LOOPING.add(me)
        try:
            result: tool_calls.LoopResult | None = None
            while result is None:
                result = await self._step()
        except BaseException as exc:
            # Handed to the consumer, which raises it there.
            state.emit(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        _contracts.log.info("tool run %s (%s) ended %s%s: %d turns, %d tool calls, %.1fs",
                 state.options.run_id, state.task, result.status,
                 f" on {result.limit}" if result.limit else "", state.turns,
                 self.tools.calls_used, state.clock.elapsed())
        state.emit(tool_calls.LoopEvent("done", turn=state.turns, result=result))

    def _pin(self) -> tuple[str, float, float]:
        """The store root, the run's wall and the per-call ceiling, read once
        off the event loop (`store.home` can read the bootstrap pointer)."""
        wall = self.state.options.budget.wall_seconds
        call = store.config.llm_call_budget()
        return str(store.home()), (call if wall is None else wall), call

    def _root_held(self) -> bool:
        return str(store.home()) == self.state.root

    async def _step(self) -> tool_calls.LoopResult | None:
        self.state.check_cancelled()
        stop = self._turn_refusal()
        if stop:
            self.state.limit = stop
            return await self._finalize_or_stop()
        if not await asyncio.to_thread(self._root_held):
            return self._result("failed", error=LLMError(
                "bad_response", "the store moved during the run", code="store_moved"))
        turn = await self._turn(final=False)
        if turn is None:
            return await self._finalize_or_stop()
        if isinstance(turn, tool_calls.LoopResult):
            return turn
        return await self._settle(turn)

    def _turn_refusal(self) -> str:
        """The limit that refuses the next tool turn (spec 3.9), or ""."""
        state, budget = self.state, self.state.options.budget
        if state.limit:
            return state.limit
        reserve = 1 if budget.reserve_final and not state.finalized else 0
        if state.turns + 1 + reserve > budget.max_turns:
            return "turns"
        if state.clock.wall_short(tool_runs.MIN_TURN_SECONDS):
            return "wall"
        return ""

    def _final_fits(self) -> bool:
        state, budget = self.state, self.state.options.budget
        return (budget.reserve_final and not state.finalized
                and state.turns + 1 <= budget.max_turns
                and not state.clock.wall_short(tool_runs.MIN_TURN_SECONDS))

    async def _finalize_or_stop(self) -> tool_calls.LoopResult:
        if self._final_fits():
            return await self._finalize(stopped=True)
        return self._result("budget_exhausted")

    async def _finalize(self, *, stopped: bool) -> tool_calls.LoopResult:
        """The finalize turn (spec 3.7 step 7)."""
        options = self.state.options
        self.state.finalized = True
        note = (await asyncio.to_thread(partial(_tool_policy.finalize_message,
                                                options.final_schema, stopped=stopped))
                if options.budget.finalize_note or options.final_schema is not None else None)
        turn = await self._turn(final=True, extra=note)
        if turn is None:
            return self._result("budget_exhausted")
        if isinstance(turn, tool_calls.LoopResult):
            return turn
        status: Literal["completed", "budget_exhausted", "failed"] = (
            "budget_exhausted" if stopped else "completed")
        if options.final_schema is None:
            return self._result(status)
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, options.final_schema):
            return self._result(status, final=value)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    async def _settle(self, turn: _Turn) -> tool_calls.LoopResult | None:
        """A turn's calls answered, or the run ended by its answer."""
        if not turn.calls:
            return await self._answered(turn)
        if self.state.options.decline_after_text and turn.visible:
            # Visible text is final (01g-C6): the calls it ended with are
            # declined, and no turn follows it.
            for call in turn.calls:
                self.state.add_trace(turn.turn, "tool", call.name, call.id, ok=False,
                                     note="declined: after_text")
            return self._result("completed", declined=turn.calls)
        if turn.finish != "length":
            # A valid call to a terminal tool ends the run, unexecuted:
            # handed back as `final_call`.
            ended = self.tools.terminal(turn)
            if ended is not None:
                return self._result("completed", final_call=ended)
        results = await self.tools.answer(turn)
        self.transcript.append(turn, results)
        self.targets.wrote(turn.target)
        self._note_switch(turn.target)
        return None

    async def _answered(self, turn: _Turn) -> tool_calls.LoopResult:
        state, options = self.state, self.state.options
        if options.final_schema is None:
            return self._result("completed")
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, options.final_schema):
            return self._result("completed", final=value)
        if (not state.finalized and state.turns + 1 <= options.budget.max_turns
                and not state.clock.wall_short(tool_runs.MIN_TURN_SECONDS)):
            self.transcript.extend([{"role": "assistant", "content": turn.text}])
            return await self._finalize(stopped=False)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    # ---- one model turn ----
    async def _turn(self, *, final: bool, extra: dict | None = None
                    ) -> _Turn | tool_calls.LoopResult | None:
        """One model turn, or None when the spend ceiling or the context stop
        refused it unsent (`limit` says `spend` or `context`)."""
        state, targets = self.state, self.targets
        chain, notes = targets.chain(final)
        choice = targets.final_choice(chain, notes) if final else targets.choice(chain, notes)
        sending = self.transcript.outgoing(extra)
        if await self.guard.over_context(sending):
            # The caller's context stop (roadmap 12 §7.3): refused unsent.
            state.hold("context")
            return None
        projection = await self.guard.projected(chain, sending, self.transcript.unsent(extra))
        if self.guard.over_ceiling(projection):
            state.hold("spend")
            return None
        k = state.turns = state.turns + 1
        self.sender.begin()
        self.transcript.sent(extra)
        collector = tool_calls.Collector()
        started = time.monotonic()
        try:
            text, holder = await self.sender.metered(k, chain, sending, choice, collector, final,
                                                     projection)
        except _tool_policy.WallSpentError:
            state.add_trace(k, "model", ok=False, note="wall", started=started)
            state.limit = "wall"
            return self._result("budget_exhausted")
        except LLMError as exc:
            state.add_trace(k, "model", ok=False, note=exc.kind, started=started)
            await self.sender.captured_failure(sending, exc, targets.primary())
            if k == 1:
                raise
            return self._result("failed", error=exc)
        return await self._settled_turn(k, text, holder, collector, final, notes, sending,
                                        started)

    async def _settled_turn(self, k: int, text: str, holder: dict,
                            collector: tool_calls.Collector, final: bool, notes: list[str],
                            sending: list[dict], started: float) -> _Turn:
        state, sender = self.state, self.sender
        target = holder.get(llm.ATTEMPTED)
        target = target if isinstance(target, wire.Target) else None
        made = tuple(self.transcript.named(c) for c in collector.calls())
        calls = made if not final else ()
        # A joined turn's text is shown whole below, so it is visible text too.
        sender.joined(text)
        turn = _Turn(k, text, calls, collector.finish_reason, collector.opaque(), target,
                     sender.shown, sender.visible)
        if text or not (final and made):
            # A finalize turn that only called (a chain sent `none` as
            # `auto`) leaves the text the run already had.
            state.text = text
        if target is not None and self.targets.switched_to(target):
            notes.append("fell back")
        state.add_trace(k, "model", target.model if target else "", chars=len(text),
                        note=", ".join(notes), started=started)
        if final and made:
            # Declined, as a finalize turn's calls are on every target.
            self.final_declined = made
            for call in made:
                state.add_trace(k, "tool", call.name, call.id, ok=False, note="declined: final")
        await sender.captured(sending, turn, self.targets.primary())
        if text and not sender.streaming:
            state.emit(tool_calls.LoopEvent("text", turn=k, delta=text))
        state.emit(tool_calls.LoopEvent("turn_end", turn=k, finish=turn.finish,
                                        interstitial=bool(calls)))
        return turn

    def _note_switch(self, target: wire.Target | None) -> None:
        """Once the fallback has served, it serves alone (spec 3.11 rule 1),
        and every id in the history is the loop's own from then on (rule 2)."""
        if target is None or not self.targets.switched_to(target):
            return
        self.targets.fallen = True
        self.transcript.rename_ids()

    # ---- bookkeeping ----
    def _result(self, status: Literal["completed", "budget_exhausted", "failed"], *,
                final: dict | None = None, final_call: tool_calls.ToolCall | None = None,
                error: Exception | None = None,
                declined: tuple[tool_calls.ToolCall, ...] = ()) -> tool_calls.LoopResult:
        state = self.state
        limit = state.limit if status == "budget_exhausted" else ""
        state.add_trace(state.turns, "stop", limit, note=status)
        return tool_calls.LoopResult(
            status=status, limit=limit, text=state.text, final=final, final_call=final_call,
            declined=declined or self.final_declined,
            proposals=tuple(self.tools.proposals), messages=tuple(self.transcript.appended),
            trace=tuple(state.trace), rows=tuple(self.sender.rows), error=error,
            run_id=state.options.run_id)


def _next_or_beat(queue: asyncio.Queue) -> Awaitable[object | None]:
    """The queue's next item, or None after `llm.HEARTBEAT_INTERVAL` of
    silence (none with an interval of 0)."""
    async def wait() -> object | None:
        interval = llm_lifecycle.HEARTBEAT_INTERVAL
        if interval <= 0:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), interval)
        except TimeoutError:
            return None
    return wait()


#: How long a closed or cancelled run's driver is given to unwind (its turn's
#: meter files `aborted`) before it is left to finish on its own.
_DRIVER_UNWIND_S = 5.0


async def _stopped(runner: asyncio.Task) -> None:
    """`runner` cancelled if still running, and briefly awaited, so a closed
    or cancelled run's open meter files before the caller moves on -- never
    longer than `_DRIVER_UNWIND_S`."""
    if not runner.done():
        runner.cancel()
        await asyncio.wait({runner}, timeout=_DRIVER_UNWIND_S)
    runner.add_done_callback(lambda t: None if t.cancelled() else t.exception())
