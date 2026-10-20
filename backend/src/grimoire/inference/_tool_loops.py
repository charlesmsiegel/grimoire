"""Streamed and joined entry points over the same bounded run driver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from typing import Literal

from .. import llm_reasoning, tool_calls
from ..llm import LLMClient
from ..store.inference.resolved import ResolvedInference
from . import _pricing, _tool_policy, _tool_runtime
from ._contracts import TurnCapture as TurnCapture
from ._pricing import Price as Price


async def _loop_events(task: str, messages: list[dict], options: dict
                       ) -> AsyncGenerator[tool_calls.LoopEvent, None]:
    """`stream_tools`' body after its entry checks: the spend preflight, then
    the run's events. The consuming task is marked as running a loop."""
    budget, resolved = options["budget"], options["resolved"]
    prices: dict[tuple[str, str], Price | None] = {}
    if budget.spend_ceiling_usd is not None:
        prices = await asyncio.to_thread(_pricing.prices_for, resolved)
        refused = _pricing.tool_run_refusal(task, resolved, budget, prices)
        if refused is not None:
            raise refused
    loop = _tool_runtime._Loop(task, messages, prices=prices, **options)
    current = asyncio.current_task()
    if current is not None:
        _tool_policy._LOOPING.add(current)
    try:
        async with aclosing(loop.events()) as events:
            async for event in events:
                yield event
    finally:
        if current is not None:
            _tool_policy._LOOPING.discard(current)


def stream_tools(task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                 execute: tool_calls.Execute, client: LLMClient,
                 resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                 campaign: str = "", scene: str = "", scene_identity: str = "",
                 post: int | None = None, round_id: str = "", response_id: str = "",
                 cancelled: Callable[[], bool] | None = None,
                 final_schema: dict | None = None,
                 tool_choice: Literal["auto", "required"] = "auto",
                 capture: TurnCapture | None = None, decline_after_text: bool = False,
                 reasoning: llm_reasoning.Buffer | None = None,
                 streaming: bool = True,
                 visibility: Callable[[], Callable[[str], bool]] | None = None,
                 holders: list[dict] | None = None,
                 ) -> AsyncGenerator[tool_calls.LoopEvent, None]:
    """`run_tools`' loop as it happens (spec 3.8; 01g-C6): an async iterator
    of `tool_calls.LoopEvent`s in the order `text* turn_end (tool_start
    tool_end)* ... done`, `done` last and carrying the `LoopResult`.

    Every turn is sent through `client.stream` -- the path
    `generate(stream=True)` takes, with the same retries, idle bound and
    `""` heartbeats -- and its deltas are `text` events as they arrive
    (`tool_calls.text_deltas` turns the events back into that plain
    iterator). Between turns and while a tool runs, an empty `text` comes
    every `llm.HEARTBEAT_INTERVAL`. `reasoning`, a caller's display buffer,
    rides each turn's holder; `llm._stamp` resets it at each turn's first
    attempt, so no continuity across turns is promised. The run's wall clock
    never cuts a turn that has shown text: it only refuses the next send.

    With `decline_after_text`, visible text is final: a turn that showed text
    and ended in tool calls ends the run `completed` with that text, its calls
    unexecuted, in `LoopResult.declined` and the trace as declined
    `after_text`. A call before any visible text runs normally. "Visible" is
    the caller's to say when it hides some text from its reader (a hidden
    preparation fence, roadmap 02 §8.2): `visibility` makes a fresh
    predicate per turn, fed each delta, True once the reader would see it.
    `holders`, a caller's list, is handed each turn's usage holder as its
    meter opens, the dict the facade stamps the serving attempt into.

    The entry checks (`ValueError`) are made here, before an iterator is
    returned; the spend preflight (`RunRefused`) on the first step.
    `streaming=False` is `run_tools`' joined path."""
    _tool_policy._loop_refusal(task, messages, toolset, resolved, run_id, final_schema, tool_choice)
    options = {"toolset": toolset, "execute": execute, "client": client,
               "resolved": resolved, "budget": budget, "run_id": run_id,
               "attribution": {"campaign": campaign, "scene": scene, "post": post,
                               "round_id": round_id, "response_id": response_id},
               "scene_identity": scene_identity, "cancelled": cancelled,
               "final_schema": final_schema, "tool_choice": tool_choice, "capture": capture,
               "streaming": streaming, "decline_after_text": decline_after_text,
               "reasoning": reasoning, "visibility": visibility, "holders": holders}
    return _loop_events(task, messages, options)


async def run_tools(task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                    execute: tool_calls.Execute, client: LLMClient,
                    resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                    campaign: str = "", scene: str = "", scene_identity: str = "",
                    post: int | None = None, round_id: str = "", response_id: str = "",
                    cancelled: Callable[[], bool] | None = None,
                    final_schema: dict | None = None,
                    tool_choice: Literal["auto", "required"] = "auto",
                    capture: TurnCapture | None = None, decline_after_text: bool = False,
                    reasoning: llm_reasoning.Buffer | None = None) -> tool_calls.LoopResult:
    """Run a bounded loop of model turns and tool calls for `task` (spec 3.7;
    01g-C2a, C3, C4 less spend).

    `resolved` is the call site's resolution of `task` for `generate`, on a
    route that requires tools or lists them as optional, with a primary not
    known unable to call them. The caller supplies the tools (`toolset`), the
    executor that runs them (`execute`; `tool_calls.registered` is the
    default) and the run id (`run_id`, normally the detached run's `Run.id`):
    the loop never executes a tool itself and never mints a run id.

    Each model turn is one facade call (`client.complete`) under a meter of
    its own carrying `run_id` and `loop_turn` (and the caller's round and
    reply ids), with the turn's output capped at `budget.max_output_tokens`.
    Before each send the run's budget is checked (cancelled, turns, wall);
    before each execution, the tool-call budget and the wall. A limit makes
    the next turn the reserved finalize turn when one fits, and otherwise
    stops the run `budget_exhausted` with the limit named. A call to a
    `terminal` tool ends the run unexecuted (`final_call`). With
    `final_schema`, the answer is read and checked against it, with one
    finalize turn for one that does not conform.

    With `budget.spend_ceiling_usd` (01g-S5), each send is projected first
    (`price_for`; a guard, never accounting) and refused when it would carry
    the run past the ceiling; a chain with an unpriceable attempt is
    `tool_calls.RunRefused("unpriceable")` before anything is sent
    (`tool_run_refusal`, which a route runs before it reserves).

    Raises only for invalid input (`ValueError`, before any meter opens),
    `RunRefused`, an `LLMError` on turn 1, and cancellation -- `cancelled()` returning True
    included, as `asyncio.CancelledError`. Every later failure is a `failed`
    result with the rows filed so far.

    It is `stream_tools` drained -- one implementation -- with each turn
    joined (`client.complete`) and bounded whole."""
    events = stream_tools(
        task, messages, toolset=toolset, execute=execute, client=client, resolved=resolved,
        budget=budget, run_id=run_id, campaign=campaign, scene=scene,
        scene_identity=scene_identity, post=post, round_id=round_id,
        response_id=response_id, cancelled=cancelled, final_schema=final_schema,
        tool_choice=tool_choice, capture=capture, decline_after_text=decline_after_text,
        reasoning=reasoning, streaming=False)
    result: tool_calls.LoopResult | None = None
    async with aclosing(events) as running:
        async for event in running:
            if event.kind == "done":
                result = event.result
    assert result is not None
    return result
