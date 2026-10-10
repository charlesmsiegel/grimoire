"""Decision-as-tool (01g-C5, spec 3.12): a `decide` a generating model can
call from inside a tool loop.

A model meets a bounded choice Grimoire did not know to ask in advance --
does Mara tell the truth, lie, or evade? -- and asks the Decision role
rather than settling it in prose; the Primary keeps the prose. The caller
names the task and resolves it at its own call site, where the literal sits
for the guards to read (`test_operation_guard.py` counts a
`decision_tool(...)` call as a decide call site), and this module builds the
`ToolSpec` the loop offers.

Each call asks one `Choice` through `operations.decide`, metered under the
caller's task with the run's id and turn, the caller's capture passed
through, output-capped, and checked against the run's decision cap and
spend ceiling before it is sent. A refused request is a result for the
model, never an exception. Nothing is fabricated: a structured answer
carries no probability because none was reported, and the rationale is
never returned -- the tool exists so the model does not reason the choice
in prose. It cannot recurse: `decide` takes no tools, and `run_tools`
refuses to start inside a loop.

No route is added here: the `tool_decision` route lands with its first
consumer (12 or 02-C4), since a route nothing uses fails the routing guard.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .. import deadline, decisions, tool_calls
from .. import inference as operations
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference.resolved import ResolvedInference

#: The most options one decide-tool call may offer: past a dozen a model is
#: enumerating rather than choosing, and an unbounded list would let one call
#: carry a chunk's worth of enum values (spec 3.12). Structural.
MAX_TOOL_OPTIONS = 16
#: The longest question a consumer may allow.
MAX_QUESTION_CHARS = 1000
#: The most context one decision item carries, the model's part truncated
#: first.
MAX_DECIDE_CONTEXT = 4000
#: The decide call's output cap: one choice's JSON, with room to spare.
DECIDE_MAX_TOKENS = 512
#: The one question every decide-tool item asks.
QUESTION_ID = "choice"


@dataclass(frozen=True)
class ToolShape:
    """What a consumer shapes, within fixed bounds: the tool's name, whether
    the model supplies a `context`, the question length, the options range
    (inside `2..MAX_TOOL_OPTIONS`), what the model gets back (`full`: the
    answer with its status and any reported probability; `selection`: the
    selected id or why none), and what a call past the run's decision cap
    gets (`error`, or `result`: `{"selected": null, "reason": "cap"}`)."""
    name: str = "decide"
    description: str = ("Ask a closed question with a fixed set of options and get the "
                        "chosen option back, instead of deciding it yourself.")
    context: bool = True
    max_question_chars: int = 500
    min_options: int = 2
    max_options: int = 8
    result: Literal["full", "selection"] = "full"
    on_cap: Literal["error", "result"] = "error"

    def __post_init__(self) -> None:
        if not 1 <= self.max_question_chars <= MAX_QUESTION_CHARS:
            raise ValueError(f"max_question_chars is 1..{MAX_QUESTION_CHARS}")
        if not 2 <= self.min_options <= self.max_options <= MAX_TOOL_OPTIONS:
            raise ValueError(f"the options range is inside 2..{MAX_TOOL_OPTIONS}")
        if self.result not in ("full", "selection") or self.on_cap not in ("error", "result"):
            raise ValueError("result is full or selection, and on_cap error or result")


@dataclass(frozen=True)
class SpendGuard:
    """Says the loop this tool is offered to runs under a spend ceiling: the
    tool is then offered only where every stage of its decision can be
    priced and capped (spec 3.9), and each call is checked against the room
    the run has left (`tool_calls.RunView.room_usd`)."""
    ceiling_usd: float


def parameters(shape: ToolShape) -> dict:
    """The tool's parameters, inside the portable subset (spec 3.12's
    default), `context` dropped when the shape gives the model none."""
    props: dict = {
        "question": {"type": "string"},
        "allow_none": {"type": "boolean"},
        "options": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "description"],
            "properties": {"id": {"type": "string"}, "description": {"type": "string"}}}}}
    if shape.context:
        props["context"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False,
            "required": ["question", "options", "allow_none",
                         *(["context"] if shape.context else [])],
            "properties": props}


def _offer_refusal(task: str, resolved: ResolvedInference | None,
                   spend: SpendGuard | None, max_tokens: int,
                   prices: dict | None) -> str:
    if resolved is None:
        return "no resolution"
    if resolved.task != task:
        return f"a resolution of {resolved.task!r} cannot decide {task!r}"
    if resolved.operation != "decide" or resolved.chain is None:
        return f"{task!r} did not resolve to a decision"
    if spend is None:
        return ""
    for stage in operations.stages(resolved):
        if stage.mode != operations.STRUCTURED:
            return "a native decision cannot be priced under a spend ceiling"
    probe = decisions.Item("", (decisions.Choice(QUESTION_ID, "?", (
        decisions.Option("a", "a"), decisions.Option("b", "b"))),))
    if operations.decide_projection(resolved, probe, max_tokens, prices or {}) is None:
        return "a stage of the decision is unpriceable or uncapped under a spend ceiling"
    return ""


def _context(args: dict, shape: ToolShape, context: Callable[[dict], str] | str) -> str:
    """The item's context: the caller's (fixed or built from the arguments)
    and the model's, capped at `MAX_DECIDE_CONTEXT` with the model's part
    truncated first."""
    base = context(args) if callable(context) else context
    own = args.get("context", "") if shape.context else ""
    own = own if isinstance(own, str) else ""
    room = max(0, MAX_DECIDE_CONTEXT - len(base) - (2 if base and own else 0))
    own = own[:room]
    joined = "\n\n".join(part for part in (base, own) if part)
    return joined[:MAX_DECIDE_CONTEXT]


def _item(args: dict, shape: ToolShape, context: Callable[[dict], str] | str
          ) -> decisions.Item:
    """The one `Choice` item a call asks, or `DecideRequestError` naming the
    first problem."""
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        raise decisions.DecideRequestError("the question is empty")
    if len(question) > shape.max_question_chars:
        raise decisions.DecideRequestError(
            f"the question is longer than {shape.max_question_chars} characters")
    raw = args.get("options")
    raw = raw if isinstance(raw, list) else []
    if not shape.min_options <= len(raw) <= shape.max_options:
        raise decisions.DecideRequestError(
            f"offer between {shape.min_options} and {shape.max_options} options")
    options = tuple(decisions.Option(str(o.get("id", "")), str(o.get("description", "")))
                    for o in raw if isinstance(o, dict))
    choice = decisions.Choice(QUESTION_ID, question, options,
                              allow_none=bool(args.get("allow_none")))
    item = decisions.Item(_context(args, shape, context), (choice,))
    decisions.validate([item])
    return item


def _status(answer: decisions.Answer) -> str:
    if answer.answer is not None:
        return "answered"
    return answer.reason if answer.reason in ("abstained", "refused") else "unanswered"


def _result(result: decisions.ItemResult, shape: ToolShape) -> dict:
    """What the model is sent: only what the backend reported."""
    answer = result.answers.get(QUESTION_ID) or decisions.Answer(None, "unreadable")
    status = _status(answer)
    if shape.result == "selection":
        return ({"selected": answer.answer} if status == "answered"
                else {"selected": None,
                      "reason": "abstained" if status == "abstained" else "unanswered"})
    out: dict = {"answer": answer.answer, "status": status}
    if answer.probability is not None:
        out["probability"] = answer.probability
    if answer.distribution is not None:
        out["distribution"] = answer.distribution
    return out


def _refusal(shape: ToolShape, reason: str, said: str) -> tool_calls.ToolOutput:
    if shape.result == "selection":
        return tool_calls.ToolOutput(json.dumps({"selected": None, "reason": reason}))
    raise tool_calls.ToolError(said)


def _around(ctx: tool_calls.ToolContext) -> operations.Around | None:
    """The run's remaining wall clock, around each decide call."""
    if ctx.deadline == float("inf"):
        return None

    def bound(call, _holder):
        left = ctx.deadline - time.monotonic()
        return deadline.bounded(call, max(left, 0.001), lambda s: LLMError(
            "timeout", f"the run's wall clock ran out ({s:g}s left)"))
    return bound


@dataclass(frozen=True)
class _Asking:
    """One decide tool's fixed half: what every call is asked with."""
    resolved: ResolvedInference
    client: LLMClient
    cid: str
    scene: str
    round_id: str
    response_id: str
    shape: ToolShape
    context: Callable[[dict], str] | str
    select: Callable[[decisions.ItemResult], dict] | None
    capture: operations.Capture | None
    prices: dict
    max_tokens: int


async def _projected(asking: _Asking, item: decisions.Item,
                     ctx: tool_calls.ToolContext) -> tuple[bool, float | None]:
    """`(fits, projection)` against the run's room under its ceiling (spec
    3.9): with no ceiling it fits and nothing is projected."""
    room = ctx.run.room_usd() if ctx.run is not None else None
    if room is None:
        return True, None
    projection = await asyncio.to_thread(operations.decide_projection, asking.resolved, item,
                                         asking.max_tokens, asking.prices)
    return projection is not None and projection <= room, projection


async def _asked(asking: _Asking, args: dict, ctx: tool_calls.ToolContext
                 ) -> tool_calls.ToolOutput:
    """One decide-tool call (spec 3.12): the run's decision cap, the one
    `Choice` item validated, the spend check, then `decide` -- each refusal a
    result for the model, never an exception the loop must catch."""
    shape = asking.shape
    if ctx.run is not None and not ctx.run.take_decision():
        if shape.on_cap == "result":
            return tool_calls.ToolOutput(json.dumps({"selected": None, "reason": "cap"}))
        raise tool_calls.ToolError("not run: the run's decision budget is spent")
    try:
        item = _item(args, shape, asking.context)
    except decisions.DecideRequestError as exc:
        return _refusal(shape, "invalid_request", f"invalid request: {exc}")
    fits, projection = await _projected(asking, item, ctx)
    if not fits:
        return _refusal(shape, "cap", "not run: the run's spend budget is spent")
    using = asking.resolved
    try:
        decision = await operations.decide(  # capture-ok: the caller's capture, passed through to the inner decide (01g 3.12; 01b-C1)
            using.task, [item], client=asking.client, resolved=using, campaign=asking.cid,
            scene=asking.scene, round_id=asking.round_id, response_id=asking.response_id,
            run_id=ctx.run_id, loop_turn=ctx.turn, capture=asking.capture,
            max_tokens=asking.max_tokens, around=_around(ctx))
    except decisions.DecideRequestError as exc:
        return _refusal(shape, "invalid_request", f"invalid request: {exc}")
    except LLMError as exc:
        if ctx.run is not None and projection is not None:
            # A failed call can still bill: its projection stands in.
            ctx.run.charge(projection)
        return _refusal(shape, "unanswered", f"the decision could not be made: {exc.kind}")
    if ctx.run is not None and projection is not None:
        spent = operations.rows_cost(decision.usage, asking.prices)
        ctx.run.charge(spent if spent is not None else projection)
    (result,) = decision.items
    shown = asking.select(result) if asking.select is not None else _result(result, shape)
    return tool_calls.ToolOutput(json.dumps(shown))


def decision_tool(task: str, resolved: ResolvedInference | None, client: LLMClient, *,
                  cid: str, scene: str = "", round_id: str = "", response_id: str = "",
                  why: str = "", shape: ToolShape | None = None,
                  context: Callable[[dict], str] | str = "",
                  select: Callable[[decisions.ItemResult], dict] | None = None,
                  capture: operations.Capture | None = None,
                  spend: SpendGuard | None = None,
                  max_tokens: int = DECIDE_MAX_TOKENS
                  ) -> tuple[tool_calls.ToolSpec | None, str]:
    """The `decide` tool for one loop, metered under `task`, or `(None, why)`
    when it cannot be offered: no resolution (the caller's soft refusal,
    `why`), a resolution of another task, or -- under a spend guard -- a
    stage that is native, unpriceable or uncapped. Under a guard this reads
    the store (the stage prices): call it off the event loop.

    `select` maps the answered `ItemResult` to what the model is sent (02's
    own sampling rule, through 01c); 01g never samples."""
    shape = shape or ToolShape()
    prices = operations.prices_for(resolved) if spend is not None and resolved else {}
    refused = _offer_refusal(task, resolved, spend, max_tokens, prices)
    if refused or resolved is None:
        return None, why or refused
    asking = _Asking(resolved, client, cid, scene, round_id, response_id, shape, context,
                     select, capture, prices, max_tokens)

    async def ask(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        return await _asked(asking, args, ctx)

    return tool_calls.ToolSpec(shape.name, shape.description, parameters(shape), ask), ""
