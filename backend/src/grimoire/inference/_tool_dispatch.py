"""A tool run's tool calls: validated, bounded, run through the caller's executor."""

from __future__ import annotations

import asyncio
import math
import time

from .. import deadline, tool_calls
from ..tool_calls import _runs as tool_runs
from . import _contracts, _tool_policy
from ._tool_guard import _SendGuard
from ._tool_state import _RunState, _Turn


class _RunView:
    """What a tool may ask of the run it is in (`tool_calls.RunView`): a
    decision against the run's cap, the spend left under its ceiling, a
    charge for what a tool's own LLM calls cost (01g-S7), and a decision's
    outcome for the trace."""

    def __init__(self, tools: _ToolDispatch):
        self._tools = tools

    def take_decision(self) -> bool:
        tools = self._tools
        cap = tools.state.options.budget.max_decisions
        if tools.decisions_used >= cap:
            # Reached, a zero cap included: the run stops after this turn.
            tools.decisions_capped = True
            return False
        tools.decisions_used += 1
        if tools.decisions_used >= cap:
            tools.decisions_capped = True
        return True

    def room_usd(self) -> float | None:
        return self._tools.guard.room()

    def charge(self, usd: float) -> None:
        self._tools.guard.charged += max(0.0, usd)

    def note_decision(self, status: str, option: str | None = None) -> None:
        """A decide-tool call's outcome, as the trace's `decide` entry (spec
        3.12): its status in `note`, the option chosen in `name`."""
        state = self._tools.state
        state.add_trace(state.turns, "decide", option or "", ok=status == "answered", note=status)


class _ToolDispatch:
    """One run's tool calls (spec 3.6, 3.9): each answered -- run, refused or
    failed -- so every call id gets a result, under the run's tool-call,
    result-size and decision budgets, with what `propose` tools proposed."""

    def __init__(self, state: _RunState, guard: _SendGuard):
        self.state = state
        self.guard = guard
        self.calls_used = 0
        self.result_chars = 0
        self.proposals: list[dict] = []
        #: The decide tool's share (01g-S7): decisions taken, and whether the
        #: cap was reached; what its calls cost is the guard's `charged`.
        self.decisions_used = 0
        self.decisions_capped = False

    def terminal(self, turn: _Turn) -> tool_calls.ToolCall | None:
        """A valid call to a terminal tool, which ends the run unexecuted:
        the turn's other calls recorded `not_run`. None when there is none."""
        toolset = self.state.options.toolset
        for call in turn.calls:
            spec = toolset.get(call.name)
            if spec is None or not spec.terminal or self._problem(call, spec):
                continue
            for other in turn.calls:
                if other is not call:
                    self.state.add_trace(turn.turn, "tool", other.name, other.id, ok=False,
                                         note="not_run")
            return call
        return None

    async def answer(self, turn: _Turn) -> list[dict]:
        """Every call of `turn` answered, in order; a budget the calls used
        up becomes the run's limit."""
        results = [await self._one(turn, call) for call in turn.calls]
        if self.calls_used >= self.state.options.budget.max_tool_calls:
            self.state.hold("tool_calls")
        if self.decisions_capped:
            self.state.hold("decisions")
        return results

    @staticmethod
    def _problem(call: tool_calls.ToolCall, spec: tool_calls.ToolSpec | None) -> str:
        if spec is None:
            return f"there is no tool named {call.name!r}"
        if call.arguments is None:
            return "your arguments were not a JSON object"
        return tool_calls.violation(call.arguments, spec.parameters)

    def _refused_call(self) -> str:
        """The limit that refuses the next execution (spec 3.9), or ""."""
        if self.state.limit:
            return self.state.limit
        if self.calls_used + 1 > self.state.options.budget.max_tool_calls:
            return "tool_calls"
        if self.state.clock.wall_short(0):
            return "wall"
        return ""

    async def _one(self, turn: _Turn, call: tool_calls.ToolCall) -> dict:
        """One call answered: run, refused or failed, every call id gets a
        result (every provider requires one)."""
        self.state.check_cancelled()
        refused = self._refused_call()
        if refused:
            self.state.limit = refused
            self.state.add_trace(turn.turn, "tool", call.name, call.id, ok=False, note="not_run")
            return self._result_message(call, tool_calls.NOT_RUN, error=True)
        self.calls_used += 1
        spec = self.state.options.toolset.get(call.name)
        problem = (tool_calls.CUT_OFF if turn.finish == "length"
                   else self._problem(call, spec))
        if problem or spec is None:
            self.state.add_trace(turn.turn, "tool", call.name, call.id, ok=False, note="refused")
            return self._result_message(call, problem, error=True)
        return await self._executed(turn.turn, call, spec)

    async def _executed(self, k: int, call: tool_calls.ToolCall,
                        spec: tool_calls.ToolSpec) -> dict:
        state = self.state
        state.emit(tool_calls.LoopEvent("tool_start", turn=k, call_id=call.id, name=call.name))
        started = time.monotonic()
        output, ok = await self.run_tool(call, spec)
        room = state.options.budget.max_result_chars_total - self.result_chars
        text = tool_calls.truncate(output.text, min(spec.max_result_chars, room))
        if room < spec.max_result_chars and len(output.text) > room:
            state.hold("result_chars")
        self.result_chars += len(text)
        if ok and spec.effect == "propose" and isinstance(output.proposal, dict):
            self.proposals.append(output.proposal)
        state.add_trace(k, "tool", call.name, call.id, ok=ok, refs=output.refs, chars=len(text),
                        started=started)
        state.emit(tool_calls.LoopEvent("tool_end", turn=k, call_id=call.id, name=call.name,
                                        ok=ok, chars=len(text), refs=output.refs))
        return self._result_message(call, text, error=not ok)

    def _context(self, call: tool_calls.ToolCall) -> tool_calls.ToolContext:
        """What `call`'s executor is handed: the run's attribution, its
        deadline and root, and a view of the run itself (`_RunView`)."""
        state, clock, budget = self.state, self.state.clock, self.state.options.budget
        reserve = (tool_runs.MIN_TURN_SECONDS
                   if clock.wall > 0 and budget.reserve_final and not state.finalized
                   else 0.0)
        return tool_calls.ToolContext(campaign=state.options.campaign,
                                      scene_identity=state.options.scene_identity,
                                      run_id=state.options.run_id, deadline=clock.end(),
                                      root=state.root, turn=state.turns, run=_RunView(self),
                                      call_budget=clock.call_budget, reserve=reserve,
                                      call_id=call.id)

    async def run_tool(self, call: tool_calls.ToolCall, spec: tool_calls.ToolSpec
                       ) -> tuple[tool_calls.ToolOutput, bool]:
        """The caller's executor, bounded: a wait, never a kill (spec 3.6). A
        `ToolError` reaches the model as itself; anything else as a generic
        failure, logged by type only (a message can quote record text)."""
        ctx = self._context(call)
        seconds: float | None = min(spec.timeout, ctx.deadline - time.monotonic())
        if seconds == math.inf:
            # No wait on either side: the decide tool, in a run with no wall,
            # which bounds each of its calls by `ctx.call_budget` itself.
            seconds = None
        execute = self.state.options.execute

        async def marked() -> tool_calls.ToolOutput:
            # The wait runs the executor in a task of its own: marked as a
            # loop's, so a tool cannot start a loop inside this one.
            me = asyncio.current_task()
            if me is not None:
                _tool_policy._LOOPING.add(me)
            return await execute(call, ctx)

        try:
            output = await deadline.bounded(marked(), seconds,
                                            lambda _s: _tool_policy._ToolTimeoutError())
        except _tool_policy._ToolTimeoutError:
            return tool_calls.ToolOutput(tool_calls.TIMED_OUT), False
        except tool_calls.ToolError as exc:
            return tool_calls.ToolOutput(str(exc) or tool_calls.TOOL_FAILED), False
        except Exception as exc:  # noqa: BLE001 - a tool's failure is the model's to hear
            _contracts.log.error("tool %s failed in run %s: %s", call.name,
                                 self.state.options.run_id, type(exc).__name__)
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        if not isinstance(output, tool_calls.ToolOutput) or not isinstance(output.text, str):
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        return output, True

    @staticmethod
    def _result_message(call: tool_calls.ToolCall, text: str, *, error: bool) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name,
                "content": text, "is_error": error}
