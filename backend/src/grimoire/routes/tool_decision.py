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

A decide is a model call, not a store read, so the tool does not take the
loop's per-tool wait (`tool_calls.TOOL_TIMEOUT_S`): each facade call it
makes is bounded as a model turn is, by `min(llm_call_budget, wall left)`
(`_around`), with the wall's share ending early enough to leave a reserved
finalize turn its room and the outcome noted before the loop stops waiting.
Whatever stops the wait after the spend check -- a failed call, the run's
wall, a cancel -- charges the call's projection, since a request that went
out can still bill; a request `decide` refuses before sending charges
nothing. Each call notes its outcome on the run (`RunView.note_decision`),
which the loop's trace records as a `decide` entry (spec 3.12).

No route is added here: the `tool_decision` route lands with its first
consumer (12 or 02-C4), since a route nothing uses fails the routing guard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .. import deadline, decisions, store, tool_calls
from .. import inference as operations
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference.resolved import ResolvedInference

log = logging.getLogger(__name__)

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
#: The loop's wait on the tool: none past the run's wall. A decision can
#: take longer than a store read's ten seconds, so the tool bounds itself:
#: `_around` holds each facade call of the decide chain to the run's
#: per-call ceiling (`ToolContext.call_budget`) and the wall left. The chain
#: has a fixed number of stages, so that bounds the whole decide.
DECIDE_TIMEOUT = math.inf
#: How long before the run's wall (less any finalize reserve) a decide's
#: own bound fires: early enough that the decide unwinds and notes its
#: outcome on the run before the loop's own wait on the tool returns and
#: the run's result is built. Structural.
DECIDE_MARGIN_S = 0.5


@dataclass(frozen=True)
class ToolShape:
    """What a consumer shapes, within fixed bounds: the tool's name, whether
    the model supplies a `context`, the question length, the options range
    (inside `2..MAX_TOOL_OPTIONS`), what the model gets back (`full`: the
    answer with its status and any reported probability; `selection`: the
    selected id or why none), and what a call past the run's decision cap
    gets (`error`, or `result`: `{"selected": null, "reason": "cap"}`).

    A `selection` reason is one of spec 3.12's four: `abstained`,
    `unanswered`, `cap` and `invalid_request`. `cap` covers both budgets: the
    run's decisions, and its spend ceiling -- room spent, or a decision the
    ceiling cannot price at all (noted `unpriceable` on the trace)."""
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
    if store.routing.policy(task).escalate_to:
        # The tool hands `decide` no escalator, which such a policy needs.
        return f"{task!r}'s policy escalates, and the decide tool takes no escalator"
    if spend is None:
        return ""
    return _unpriceable(resolved, max_tokens, prices or {})


def _unpriceable(resolved: ResolvedInference, max_tokens: int, prices: dict) -> str:
    """Why a decision on `resolved` cannot be priced under a spend ceiling
    (spec 3.9), or "": a native stage, or one unpriceable or uncapped. Renders
    a template: off the event loop."""
    for stage in operations.stages(resolved):
        if stage.mode != operations.STRUCTURED:
            return "a native decision cannot be priced under a spend ceiling"
    probe = decisions.Item("", (decisions.Choice(QUESTION_ID, "?", (
        decisions.Option("a", "a"), decisions.Option("b", "b"))),))
    if operations.decide_projection(resolved, probe, max_tokens, prices) is None:
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
    """Each decide facade call's bound, as a model turn's (`_Loop._turn_seconds`):
    `min(llm_call_budget, wall left)`, either absent when it is off. The wall
    left is less the finalize reserve and `DECIDE_MARGIN_S`, so the bound
    fires before the loop's wait on the tool; a wall already spent abandons
    the call unsent (`deadline.bounded`)."""
    ceiling = ctx.call_budget if ctx.call_budget > 0 else None
    walled = ctx.deadline != math.inf
    if ceiling is None and not walled:
        return None

    def bound(call, _holder):
        left = (ctx.deadline - ctx.reserve - DECIDE_MARGIN_S - time.monotonic()
                if walled else math.inf)
        if ceiling is not None and ceiling <= left:
            return deadline.bounded(call, ceiling, lambda s: LLMError(
                "timeout", f"no decision within the per-call ceiling ({s:g}s)"))
        return deadline.bounded(call, left, lambda _s: LLMError(
            "timeout", "the run's wall clock ran out"))
    return bound


@dataclass(frozen=True)
class _Asking:
    """One decide tool's fixed half: what every call is asked with. `prices`
    were read at the build when it was guarded (`guarded`); an unguarded
    tool reads them at a call the run's ceiling has to price (`_pricing`)."""
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
    guarded: bool = False


def _late_pricing(asking: _Asking) -> tuple[dict, str]:
    prices = operations.prices_for(asking.resolved)
    return prices, _unpriceable(asking.resolved, asking.max_tokens, prices)


async def _pricing(asking: _Asking, ctx: tool_calls.ToolContext) -> tuple[dict, str]:
    """`(prices, why not)` a call is checked with: the build's, or none with
    no ceiling on the run. A tool built without a guard and run under a
    ceiling is priced now, as the guard would have priced it at the build
    -- and refused, naming why, where it cannot be (F2: never "the spend
    budget is spent" when nothing was)."""
    if asking.guarded or ctx.run is None or ctx.run.room_usd() is None:
        return asking.prices, ""
    return await asyncio.to_thread(_late_pricing, asking)


async def _projected(asking: _Asking, item: decisions.Item, prices: dict,
                     ctx: tool_calls.ToolContext) -> tuple[bool, float | None]:
    """`(fits, projection)` against the run's room under its ceiling (spec
    3.9): with no ceiling it fits and nothing is projected."""
    room = ctx.run.room_usd() if ctx.run is not None else None
    if room is None:
        return True, None
    projection = await asyncio.to_thread(operations.decide_projection, asking.resolved, item,
                                         asking.max_tokens, prices)
    return projection is not None and projection <= room, projection


def _noted(ctx: tool_calls.ToolContext, status: str, option: str | None = None) -> None:
    if ctx.run is not None:
        ctx.run.note_decision(status, option)


def _charge(ctx: tool_calls.ToolContext, usd: float | None) -> None:
    if ctx.run is not None and usd is not None:
        ctx.run.charge(usd)


async def _asked(asking: _Asking, args: dict, ctx: tool_calls.ToolContext
                 ) -> tool_calls.ToolOutput:
    """One decide-tool call (spec 3.12): whether the run's ceiling can price
    it at all, the run's decision cap, the one `Choice` item validated, the
    spend check, then `decide` -- each refusal a result for the model, never
    an exception the loop must catch, and each outcome noted on the run."""
    shape = asking.shape
    prices, unpriced = await _pricing(asking, ctx)
    if unpriced:
        # Before the cap: a call no ceiling could price takes no decision. On
        # the selection shape it is the spend budget's `cap` (spec 3.12).
        _noted(ctx, "unpriceable")
        return _refusal(shape, "cap", f"not run: {unpriced}")
    if ctx.run is not None and not ctx.run.take_decision():
        _noted(ctx, "cap")
        if shape.on_cap == "result":
            return tool_calls.ToolOutput(json.dumps({"selected": None, "reason": "cap"}))
        raise tool_calls.ToolError("not run: the run's decision budget is spent")
    try:
        item = _item(args, shape, asking.context)
    except decisions.DecideRequestError as exc:
        _noted(ctx, "invalid_request")
        return _refusal(shape, "invalid_request", f"invalid request: {exc}")
    fits, projection = await _projected(asking, item, prices, ctx)
    if not fits:
        _noted(ctx, "spend")
        return _refusal(shape, "cap", "not run: the run's spend budget is spent")
    using = asking.resolved
    try:
        decision = await operations.decide(  # capture-ok: the caller's capture, passed through to the inner decide (01g 3.12; 01b-C1)
            using.task, [item], client=asking.client, resolved=using, campaign=asking.cid,
            scene=asking.scene, round_id=asking.round_id, response_id=asking.response_id,
            run_id=ctx.run_id, loop_turn=ctx.turn, capture=asking.capture,
            max_tokens=asking.max_tokens, around=_around(ctx))
    except decisions.DecideRequestError as exc:
        _noted(ctx, "invalid_request")
        return _refusal(shape, "invalid_request", f"invalid request: {exc}")
    except ValueError as exc:
        # `decide` refused the resolution before any meter opened: nothing
        # went out, so nothing is charged. A wiring fault, not the model's.
        log.error("decide tool for %s refused before sending: %s", using.task, exc)
        _noted(ctx, "failed")
        return _refusal(shape, "unanswered", "the decision could not be made: not sent")
    except LLMError as exc:
        # A failed call can still bill: its projection stands in.
        _charge(ctx, projection)
        _noted(ctx, "failed")
        return _refusal(shape, "unanswered", f"the decision could not be made: {exc.kind}")
    except BaseException:
        # The wait stopped with the request out (the run's wall, a cancel, a
        # fault): it can still bill, so its projection stands in too (F1).
        _charge(ctx, projection)
        _noted(ctx, "abandoned")
        raise
    spent = operations.rows_cost(decision.usage, prices) if projection is not None else None
    _charge(ctx, spent if spent is not None else projection)
    (result,) = decision.items
    answer = result.answers.get(QUESTION_ID)
    chosen = answer.answer if answer is not None and isinstance(answer.answer, str) else None
    _noted(ctx, _status(answer) if answer is not None else "unanswered", chosen)
    shown = asking.select(result) if asking.select is not None else _result(result, shape)
    return tool_calls.ToolOutput(json.dumps(shown))


def decision_tool(task: str, resolved: ResolvedInference | None, client: LLMClient, *,
                  cid: str, scene: str = "", round_id: str = "", response_id: str = "",
                  why: str = "", shape: ToolShape | None = None,
                  context: Callable[[dict], str] | str = "",
                  select: Callable[[decisions.ItemResult], dict] | None = None,
                  capture: operations.Capture | None = None,
                  spend: SpendGuard | None = None,
                  ceiling: float | None = None,
                  max_tokens: int = DECIDE_MAX_TOKENS
                  ) -> tuple[tool_calls.ToolSpec | None, str]:
    """The `decide` tool for one loop, metered under `task`, or `(None, why)`
    when it cannot be offered: no resolution (the caller's soft refusal,
    `why`), a resolution of another task, a task whose code policy escalates
    (the tool takes no escalator), or -- under a spend guard -- a stage that
    is native, unpriceable or uncapped. `ceiling` is the run's own spend
    ceiling, None included (`RunBudget.spend_ceiling_usd`), as a guard when
    it is set: a caller that hands it never offers a tool every call of
    which the run would refuse. Under a guard this reads the store (the
    stage prices): call it off the event loop. Built without one and run
    under a ceiling, each call is priced then (`_pricing`) and, where it
    cannot be, refused as `unpriceable` without taking a decision.

    `select` maps the answered `ItemResult` to what the model is sent (02's
    own sampling rule, through 01c); 01g never samples."""
    shape = shape or ToolShape()
    if spend is not None and ceiling is not None and spend.ceiling_usd != ceiling:
        raise ValueError("decision_tool takes one spend ceiling: spend= or ceiling=")
    if spend is None and ceiling is not None:
        spend = SpendGuard(ceiling)
    prices = operations.prices_for(resolved) if spend is not None and resolved else {}
    refused = _offer_refusal(task, resolved, spend, max_tokens, prices)
    if refused or resolved is None:
        return None, why or refused
    asking = _Asking(resolved, client, cid, scene, round_id, response_id, shape, context,
                     select, capture, prices, max_tokens, guarded=spend is not None)

    async def ask(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        return await _asked(asking, args, ctx)

    return tool_calls.ToolSpec(shape.name, shape.description, parameters(shape), ask,
                               timeout=DECIDE_TIMEOUT), ""
