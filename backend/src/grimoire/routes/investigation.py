"""Bounded agentic investigation (roadmap 12): the loop driver and the entry
points a reader starts.

Theory: a tool-calling model is used only where the evidence a correct answer
needs cannot be named in advance -- and here only when a person asks. The
store package (`store/investigation/`) owns the tools, the budgets, the trace
and the checking of a final call; this module owns the model calls, the house
split. The loop is 01g's `run_tools`: 12 adds no tool plumbing of its own.

- **E2, continuity investigation** (`post_candidate_investigate`): a
  `background` run on the campaign, one live per campaign, about one live
  candidate. Its one write is `reconcile.persist_investigation`: one proposal
  on that candidate, fenced on its fingerprint and prior proposal, never
  touching a sweep's generation.
- **E3, history question** (`post_history_question`): a computing `draft`,
  its answer held on the run and reaped, never stored.
- **E1, RP escalation** is 12-S6's and waits on spec 10's `PlanTrace`.

Every entry point is off until its switch is on (`investigation_continuity`,
`investigation_question`), refuses before reserving what it could only fail
at (an unknown or settled candidate, a model known unable to call tools, a
model unpriceable under the spend ceiling), and ends in one of `STATES`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .. import inference as operations
from .. import prompts, store, tool_calls
from ..embeddings import AsyncEmbeddingsClient
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.continuity import candidates, pending, reconcile
from ..store.inference.resolved import ResolvedInference
from ..store.investigation import budget as investigation_budget
from ..store.investigation import final as investigation_final
from ..store.investigation import state as investigation_state
from ..store.investigation import tools as investigation_tools
from ..store.investigation import trace as investigation_trace
from . import decision_capture, runs, tool_decision
from .common import (
    UsableInference,
    _soft_resolved,
    computes_only,
    get_embeddings,
    get_llm,
    require_inference,
)
from .models import HistoryQuestion

router = APIRouter()
log = logging.getLogger(__name__)

#: How a run can end (spec 5).
STATES: tuple[str, ...] = ("completed", "no_change", "insufficient_evidence",
                           "budget_exhausted", "refused", "failed", "cancelled")

#: The run kinds.
CONTINUITY_KIND = "continuity-investigate"
QUESTION_KIND = "history-question"

#: The switches (config keys), each off by default.
CONTINUITY_SWITCH = "investigation_continuity"
QUESTION_SWITCH = "investigation_question"

#: The longest question a reader may ask.
QUESTION_CHARS = 500

#: The label a continuity run carries its candidate under (`runs` adoption).
CANDIDATE_LABEL = "candidate"


@dataclass(frozen=True)
class Outcome:
    """How one investigation ended (spec 5).

    `limit` names what stopped a `budget_exhausted` run (01g's `turns`,
    `tool_calls`, `decisions`, `wall`, `spend`, `result_chars`, or 12's own
    `context`); `reason` why a run was `refused` (`unpriceable`); `error` the
    `LLMError` kind a `failed` run stopped on, never provider text. `result`
    is the checked final payload, `trace` the run's trace (spec 8.1)."""
    state: str
    limit: str = ""
    reason: str = ""
    error: str = ""
    result: dict | None = None
    trace: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Seed:
    """A run's opening messages -- no tool result and no hidden post in them
    -- and, for E2, the candidate a proposal is rebuilt against."""
    messages: list[dict]
    candidate: dict | None = None
    #: The bound context a decide tool's question is asked against.
    context: str = ""


# ---- switches ----
def switched_on(key: str) -> bool:
    return store.config.read_config().get(key) == "on"


def _refuse_off(key: str) -> None:
    if not switched_on(key):
        raise HTTPException(status_code=400, detail={
            "kind": "off", "detail": f"{key.replace('_', ' ')} is switched off in Settings"})


def _refuse_unpriceable(task: str, resolved: ResolvedInference,
                        budget: tool_calls.RunBudget) -> None:
    """409 `unpriceable` before anything is reserved (spec 7.2): with a spend
    ceiling, every attempt on the chain must have a price this side knows."""
    refused = operations.tool_run_refusal(task, resolved, budget)
    if refused is not None:
        raise HTTPException(status_code=409, detail={
            "kind": refused.kind,
            "detail": f"{refused} Setting rates for the model, or clearing the "
                      "investigation spend ceiling, lifts this."})


# ---- seeds ----
def continuity_seed(cid: str, candidate_id: str) -> Seed:
    """E2's opening: the candidate as the sweep's own item shows it
    (`reconcile.build_payload`, records named by letter) and its criteria.
    Reads the store: run it off the event loop."""
    record = candidates.read(cid)["records"][candidate_id]
    payload = reconcile.build_payload(cid, [{"id": candidate_id, "kind": record["kind"],
                                             "refs": record["refs"],
                                             "signals": record["signals"],
                                             "fingerprint": record["fingerprint"]}])
    cand = payload["candidates"][0]
    vocab = cand["vocabulary"]
    item = prompts.render("continuity_reconcile/item.j2", now=payload["now"],
                          chronicle=payload["chronicle"],
                          c={"label": reconcile.LABELS[vocab], "records": cand["records"],
                             "signal_text": cand["signal_text"]})
    messages = [{"role": "system", "content": prompts.render("investigation/continuity/system.j2")},
                {"role": "user", "content": prompts.render(
                    "investigation/continuity/seed.j2", item=item, vocabulary=vocab,
                    decisions=list(reconcile.DECISIONS[vocab]))}]
    return Seed(messages, cand, context=item)


def question_seed(cid: str, question: str) -> Seed:
    """E3's opening: the reader's question and the campaign date."""
    now = store.clock.now(cid)
    messages = [{"role": "system", "content": prompts.render("investigation/question/system.j2")},
                {"role": "user", "content": prompts.render(
                    "investigation/question/seed.j2", question=question, now=now)}]
    return Seed(messages, context=f"The reader asked: {question}")


# ---- the loop ----
async def _loop(task: str, messages: list[dict], *, resolved: ResolvedInference,
                **options) -> tool_calls.LoopResult:
    """01g's `run_tools` under `task`, spelled per task so each call site names
    its task and its resolution (`test_operation_guard`)."""
    if task == "investigation-continuity":
        return await operations.run_tools("investigation-continuity", messages,
                                          resolved=resolved, **options)
    if task == "investigation-question":
        return await operations.run_tools("investigation-question", messages,
                                          resolved=resolved, **options)
    raise ValueError(f"not an investigation task: {task!r}")


def _keep_delivered(state: investigation_state.RunState,
                    result: tool_calls.LoopResult) -> None:
    """`inspected` held to the reads the model was actually handed: a tool's
    thread the loop stopped waiting on (`timed out`) can still finish and note
    its scene, but the model was told it failed, so that scene was never read.
    The loop's own trace says which reading calls delivered, and their refs
    name the scenes they returned text of (`tools.READS`). The state is
    frozen in the same hold, so a thread that finishes later -- during the
    check of the final call, or after -- adds neither a read nor a step."""
    delivered = {ref.removeprefix("scene:") for e in result.trace
                 if e.kind == "tool" and e.ok and e.name in investigation_tools.READS
                 for ref in e.refs if ref.startswith("scene:")}
    calls = {e.call_id for e in result.trace if e.kind == "tool" and e.ok and e.call_id}
    returned: Counter[tuple[object, object]] = Counter(
        (e.turn, e.name) for e in result.trace if e.kind == "tool" and e.ok)
    with state.lock:
        state.frozen = True
        state.inspected &= delivered
        # A step a timed-out thread noted before the freeze is no delivered
        # call either: a step stands only against a call the loop handed
        # back -- by that call's id, so two calls to one tool in one turn are
        # never mistaken for each other. A step with no id (none of the
        # loop's tools leaves one) falls back to turn and tool.
        kept = []
        for step in state.steps:
            call = step.get(investigation_state.CALL_KEY)
            key = (step.get("turn"), step.get("tool"))
            if call:
                if call in calls:
                    kept.append(step)
            elif returned[key] > 0:
                returned[key] -= 1
                kept.append(step)
        state.steps[:] = kept


def _model_of(rows: Sequence[dict], resolved: ResolvedInference) -> str:
    """The model that served the loop's last turn, by its row -- the loop's
    own or, when it raised, the one it filed (a fallback that served a
    failed first turn is that turn's model) -- else the primary configured."""
    for row in reversed(rows):
        if row.get("model"):
            return f"{row.get('provider_id', '')}/{row['model']}"
    primary = resolved.chain.primary if resolved.chain is not None else None
    return f"{primary.provider_id}/{primary.model}" if primary is not None else ""


def _filed(cid: str, run_id: str) -> tuple[dict, ...] | None:
    """Every ledger row this run filed: the loop's turns, the decide tool's
    calls (answered or failed), `search_history`'s embedding calls -- each
    metered apart, all under the run's id. The ledger is the one place they
    all are: a loop that raises hands back none of its turns, a decide that
    fails is a tool result with no rows, and 09's retrieval returns none of
    its own. The window is two days, which a run bounded by its wall cannot
    outlast across midnight. None when the ledger cannot be read."""
    try:
        return tuple(r for r in store.usage.calls(days=2, campaign=cid)
                     if r.get("run_id") == run_id)
    except OSError:
        log.warning("Investigation %s could not read back its ledger rows", run_id)
        return None


#: A tool call's trace note when the call took a slot of the budget: run
#: (no note) or refused at the loop's own checks (`refused`: a schema-invalid,
#: unknown or cut-off call, which still reached the model as an error). A
#: call `not_run` or declined took none.
COUNTED_NOTES = frozenset({"", "refused"})


def _counts(result: tool_calls.LoopResult | None, steps: Sequence[dict],
            filed: Sequence[dict] = ()) -> tuple[int, int, int]:
    """(model turns, tool calls, decisions) from the loop's own trace -- or,
    when the loop raised, the turns its filed rows show and the calls and
    decisions its retained steps do. The calls count the terminal one: the
    loop hands it back unexecuted, but the model made it, and it is what the
    run ended on."""
    if result is None:
        return (len(filed), len(steps),
                sum(1 for s in steps if s.get("tool") == "decide"))
    entries = result.trace
    turns = sum(1 for e in entries if e.kind == "model")
    calls = sum(1 for e in entries if e.kind == "tool" and e.note in COUNTED_NOTES)
    decisions = sum(1 for e in entries if e.kind == "decide")
    return turns, calls + (result.final_call is not None), decisions


#: A terminal call's arguments a trace step keeps: its structure, never its
#: prose (an answer's text, a reason, a note are the model's words, and the
#: validated ones are the trace's `final` already).
FINAL_ARGS = ("decision", "from", "to", "relation", "status", "evidence_scenes",
              "citations", "ids")


def _final_step(state: investigation_state.RunState, call: tool_calls.ToolCall | None,
                turn: int) -> None:
    """The terminal call as the run's last step, so the steps say what the
    run ended on: `ok` when it was a finish this mode takes. What stood of
    it after the checks is the trace's `final`, not this step."""
    if call is None:
        return
    args = call.arguments if isinstance(call.arguments, dict) else {}
    taken = call.name in investigation_tools.TERMINAL.get(state.mode, frozenset())
    state.note(investigation_trace.step(
        turn, call.name, {k: args[k] for k in FINAL_ARGS if k in args}, ok=taken,
        error="" if taken else "not_in_mode"), final=True)


async def _settled(state: investigation_state.RunState, result: tool_calls.LoopResult,
                   seed: Seed) -> tuple[dict, investigation_final.Final | None]:
    """How a run the loop returned ended, and its final call checked when it
    ended on one -- with the state held to what the loop delivered first."""
    _keep_delivered(state, result)
    if result.status == "failed":
        return {"state": "failed", "error": getattr(result.error, "kind", "") or "error"}, None
    if result.status == "budget_exhausted":
        return {"state": "budget_exhausted", "limit": result.limit}, None
    def check() -> investigation_final.Final:
        investigation_tools.revalidate(state)
        return investigation_final.settle(state, result.final_call, candidate=seed.candidate)
    checked = await anyio.to_thread.run_sync(check)
    return {"state": checked.state}, checked


async def investigate(app, *, state: investigation_state.RunState, task: str, seed: Seed,
                      client: LLMClient, resolved: ResolvedInference,
                      budget: tool_calls.RunBudget, run_id: str, post: int | None = None,
                      cancelled: Callable[[], bool] | None = None,
                      extra: tuple[tool_calls.ToolSpec, ...] = ()) -> Outcome:
    """One bounded investigation (spec 5): `seed` sent with `state.mode`'s
    toolset (and `extra`, the decide tool), each turn metered under `task`
    with `run_id` and its loop turn (01g-C3), stopped by the first terminal
    call, a budget limit (01g-C4, plus the context stop of spec 7.3), a
    cancel (`cancelled()`), or a failed call. Never raises for any of those:
    each is an `Outcome`. A cancellation of the task itself still propagates.

    `tool_choice` is `required`: every turn calls, and a run ends on a
    terminal tool. A last turn that ends in prose anyway is an implicit
    `finish_insufficient_evidence`, its prose dropped.

    The trace's money is every row the run filed under `run_id` (`_filed`):
    the loop's turns, `extra`'s own calls and the search's embeddings, so
    what a decision or an embedding cost is part of the run's."""
    started, t0 = store.paths.now_iso(), time.monotonic()
    toolset = investigation_tools.toolset(state, extra=extra)
    tokens = await anyio.to_thread.run_sync(investigation_budget.prompt_tokens, resolved, budget)
    budget = investigation_budget.with_context(budget, tokens)
    result: tool_calls.LoopResult | None = None
    stopped: dict = {}
    execute = tool_calls.registered(toolset)
    try:
        # Each model turn, into one campaign-level prompt-log entry of its own
        # pool, fenced on the run (a cancelled or forgotten one files nothing).
        async with decision_capture.capturing(
                state.cid, decision_capture.NO_SCENE, task, operation=decision_capture.TOOLS,
                fence=lambda: cancelled is None or not cancelled()) as scope:
            result = await _loop(task, seed.messages, toolset=toolset,
                                 execute=execute, client=client,
                                 resolved=resolved, budget=budget, run_id=run_id,
                                 campaign=state.cid, scene=state.sid, post=post,
                                 cancelled=cancelled, tool_choice="required",
                                 capture=scope.hook())
    except tool_calls.RunRefused as exc:
        stopped = {"state": "refused", "reason": exc.kind}
    except LLMError as exc:
        stopped = {"state": "failed", "error": exc.kind}
    except asyncio.CancelledError:
        me = asyncio.current_task()
        if cancelled is None or not cancelled() or (me is not None and me.cancelling()):
            raise
        stopped = {"state": "cancelled"}
    checked: investigation_final.Final | None = None
    if result is not None:
        stopped, checked = await _settled(state, result, seed)
    else:
        # Nothing to check against, but a tool thread may still be running.
        state.freeze()
    # A tool thread the loop stopped waiting on still reads the store through
    # `store.home()`. The run holds the root still while it does (a move is
    # refused while a run is live): frozen, it records nothing, but it must
    # not read another library. Bounded, so a stalled filesystem cannot hold
    # the run and its campaign forever (`DRAIN_S`); past it the ROOT stays
    # pinned on its own until the thread returns (`runs.pin_root_until`).
    if not await anyio.to_thread.run_sync(execute.drain, investigation_budget.DRAIN_S):
        log.warning("investigation %s ended with a tool thread still running", run_id)
        if app is not None:
            runs.pin_root_until(app, run_id, execute.drain)
    filed = await anyio.to_thread.run_sync(_filed, state.cid, run_id)
    # An unreadable ledger leaves the loop's own turns, which it handed back.
    rows: Sequence[dict] = filed if filed is not None else (
        result.rows if result is not None else ())
    turned = [r for r in rows if r.get("task") == task]
    turns, calls, decided = _counts(result, list(state.steps), turned)
    if checked is not None and result is not None:
        _final_step(state, result.final_call, turns)
    elapsed = time.monotonic() - t0

    def usage_block() -> dict:
        return investigation_trace.usage_of(
            rows, model_turns=turns, tool_calls=calls, decisions=decided, seconds=elapsed,
            rates=store.usage.Rates.current())

    trace = investigation_trace.build(
        run=run_id, mode=state.mode, task=task, model=_model_of(turned, resolved),
        started=started, outcome=stopped["state"], limit=stopped.get("limit", ""),
        steps=list(state.steps), inspected=set(state.inspected),
        final=checked.payload if checked is not None else None,
        usage_block=await anyio.to_thread.run_sync(usage_block))
    return Outcome(**stopped, result=checked.result if checked is not None else None,
                   trace=trace)


# ---- the decide tool (12-S4, spec 4.4) ----
async def investigate_with_decide(
        app, *, state: investigation_state.RunState, task: str, seed: Seed,
        client: LLMClient, resolved: ResolvedInference, budget: tool_calls.RunBudget,
        run_id: str, cancelled: Callable[[], bool] = lambda: False,
        decide_resolution: Callable[[], UsableInference] | None = None) -> Outcome:
    """A maintenance-mode run as both entry points start one: `investigate`
    with the decide tool (`_decide_tool`), its calls captured in one
    campaign-level decision scope fenced on `cancelled`, and their cost in
    the trace's usage (`_filed`). `decide_resolution` stands in for the
    seam's own resolution of `investigation-decide` -- the eval resolves it
    from the real store before it moves the home."""
    async with decision_capture.capturing(
            state.cid, decision_capture.NO_SCENE, "investigation-decide",
            fence=lambda: not cancelled()) as scope:
        extra = await _decide_tool(state.cid, client, budget, seed, scope, state,
                                   resolve=decide_resolution)
        # One decision of headroom for the loop: the decide tool keeps the
        # run's real cap itself (`_traced`), so taking the last decision
        # leaves the model a turn to finish on.
        looped = replace(budget, max_decisions=budget.max_decisions + 1) if extra else budget
        return await investigate(app, state=state, task=task, seed=seed, client=client,
                                 resolved=resolved, budget=looped, run_id=run_id,
                                 cancelled=cancelled, extra=extra)


async def _decide_tool(cid: str, client: LLMClient, budget: tool_calls.RunBudget,
                       seed: Seed, scope: decision_capture.Scope,
                       state: investigation_state.RunState, *,
                       resolve: Callable[[], UsableInference] | None = None
                       ) -> tuple[tool_calls.ToolSpec, ...]:
    """The `decide` tool for a maintenance-mode run, on 12's own
    `investigation_decide` route, or none: a run whose budget allows no
    decision, a resolution that softly refuses, or -- under a spend ceiling
    -- a decision that would answer on a stage the ceiling cannot price
    (01g 3.9), each leaves the tool out and the run proceeds without it."""
    if budget.max_decisions < 1:
        return ()
    resolved, why, _kind = await anyio.to_thread.run_sync(
        _soft_resolved,
        resolve or (lambda: require_inference("investigation-decide", cid, operation="decide")))
    spec, _why = await anyio.to_thread.run_sync(lambda: tool_decision.decision_tool(
        "investigation-decide", resolved, client, cid=cid, why=why,
        context=seed.context[:tool_decision.MAX_DECIDE_CONTEXT // 2],
        ceiling=budget.spend_ceiling_usd, capture=scope.hook()))
    return (_traced(spec, state, budget.max_decisions),) if spec is not None else ()


#: What the decide tool says when the run's own decisions are spent: the
#: words `tool_decision` refuses with at its cap.
DECISIONS_SPENT = "not run: the run's decision budget is spent"


def _answered(text: str) -> bool:
    """Whether a decide result the tool returned is an answer: its status is
    `answered`. An abstention, a refusal or an unanswered call comes back as
    a result too, and is no usable decision."""
    try:
        found = json.loads(text)
    except ValueError:
        return False
    return isinstance(found, dict) and found.get("status") == "answered"


def _traced(spec: tool_calls.ToolSpec, state: investigation_state.RunState,
            cap: int) -> tool_calls.ToolSpec:
    """The decide tool, noting a trace step per call as the reading tools do
    -- the question and options asked (clipped), and whether it answered --
    and holding the run to its own `cap` of decisions.

    The cap is kept here rather than by the loop: 01g's loop stops the run as
    soon as the last allowed decision is taken, and an investigation reserves
    no finalize turn, so a run that used its last decision could never call
    its terminal tool. The loop is given one decision more
    (`investigate_with_decide`); past `cap` this refuses before the tool
    takes one, as the tool refuses at its own cap."""
    inner = spec.fn
    taken = [0]

    async def fn(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        options = args.get("options")
        asked = {"question": args.get("question"),
                 "options": [o.get("id") for o in options if isinstance(o, dict)]
                 if isinstance(options, list) else []}
        if taken[0] >= cap:
            if ctx.run is not None:
                ctx.run.note_decision("cap")
            state.note(investigation_trace.step(ctx.turn, "decide", asked, ok=False,
                                                error="refused"), call_id=ctx.call_id)
            raise tool_calls.ToolError(DECISIONS_SPENT)
        taken[0] += 1
        try:
            out = await inner(args, ctx)  # type: ignore[misc]
        except tool_calls.ToolError:
            state.note(investigation_trace.step(ctx.turn, "decide", asked, ok=False,
                                                error="refused"), call_id=ctx.call_id)
            raise
        ok = _answered(out.text)
        state.note(investigation_trace.step(ctx.turn, "decide", asked, ok=ok,
                                            chars=len(out.text),
                                            error="" if ok else "unanswered"),
                   call_id=ctx.call_id)
        return out

    return replace(spec, fn=fn)


# ---- E2: continuity investigation (12-S3) ----
def _live_candidate(cid: str, candidate_id: str) -> dict:
    """The cached candidate, refused 404 when there is none and 409
    `not_live` unless it reads `live` now."""
    record = candidates.read(cid)["records"].get(candidate_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "kind": "not_found", "detail": "no such continuity finding"})
    if not _live(cid, record):
        raise HTTPException(status_code=409, detail={
            "kind": "not_live",
            "detail": "this finding has moved since it was found; refresh the review"})
    return record


def _live(cid: str, record: dict) -> bool:
    try:
        return pending.verdict(pending.Current.load(cid), record) == "live"
    except Exception:  # noqa: BLE001 -- a verdict that cannot be read is not live
        return False


def _still_live(cid: str, candidate_id: str, started: dict) -> bool:
    """The finding the start refused nothing for, re-read when the run
    begins: still cached, with the fingerprint and the proposal the start
    saw, and live. A background run can begin after an apply, a dismiss, a
    sweep that adjudicated it or an edit of a record it names, and a finding
    that moved is not worth a model call -- the persist, fenced on the same
    two, would drop its answer anyway."""
    record = candidates.read(cid)["records"].get(candidate_id)
    return (record is not None and record.get("fingerprint") == started.get("fingerprint")
            and record.get("proposal") == started.get("proposal") and _live(cid, record))


def _campaign_or_404(cid: str) -> None:
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail={"kind": "not_found",
                                                     "detail": "no such campaign"})


def _stillborn(run: runs.Run) -> bool:
    return run.cancel_requested or run.forgotten


async def _continuity_work(app, run: runs.Run, cid: str, candidate_id: str,
                           client: LLMClient, embed_client: AsyncEmbeddingsClient | None,
                           resolved: ResolvedInference, budget: tool_calls.RunBudget,
                           record: dict) -> dict:
    """The run: investigate, then the one write. Its result is `{"outcome",
    "persisted", "candidate"}`, polled through the campaign run routes. A
    finding that is gone, moved or no longer live when the run begins
    (`_still_live`) is a `failed` outcome (`gone`) that sent nothing."""
    try:
        if not await anyio.to_thread.run_sync(_still_live, cid, candidate_id, record):
            raise KeyError(candidate_id)
        seed = await anyio.to_thread.run_sync(continuity_seed, cid, candidate_id)
    except KeyError:
        gone = Outcome(state="failed", error="gone")
        return {"state": "landed", "result": {"outcome": gone.as_dict(), "persisted": False,
                                              "candidate": candidate_id}}
    state = investigation_state.RunState(cid=cid, mode="continuity", embed_client=embed_client)
    outcome = await investigate_with_decide(
        app, state=state, task="investigation-continuity", seed=seed, client=client,
        resolved=resolved, budget=budget, run_id=run.id, cancelled=lambda: _stillborn(run))
    persisted = False
    if outcome.state == "completed" and outcome.result is not None and not _stillborn(run):
        proposal = {**outcome.result, "investigation": outcome.trace}
        persisted = await anyio.to_thread.run_sync(lambda: reconcile.persist_investigation(
            cid, candidate_id, proposal, started_fingerprint=record["fingerprint"],
            started_proposal=record.get("proposal"), stillborn=lambda: _stillborn(run)))
    body = {"outcome": outcome.as_dict(), "persisted": persisted, "candidate": candidate_id}
    return {"state": "cancelled" if outcome.state == "cancelled" else "landed",
            "result": body}


@router.post("/campaigns/{cid}/continuity/candidates/{candidate_id}/investigate",
             status_code=202)
@computes_only  # 202 before anything is written; the persist stamps for itself
def post_candidate_investigate(cid: str, candidate_id: str, request: Request,
                               client: LLMClient = Depends(get_llm),
                               embed_client: AsyncEmbeddingsClient = Depends(get_embeddings),
                               x_grimoire_attempt: str | None = Header(default=None)):
    """Investigate one continuity finding (roadmap 12, E2): a `background`
    run on the campaign, answered 202 with the run to poll.

    Refused before anything is reserved: 404 for an unknown campaign or
    finding, 400 `off` while the switch is off, 409 `not_live` for a finding
    that is not live, the seam's 409 `incapable` for a model known unable to
    call tools, and 409 `unpriceable` under a spend ceiling. One live
    investigation per campaign: a start for the same finding adopts the live
    run, a start for another is refused 409 `busy` naming it."""
    _campaign_or_404(cid)
    _refuse_off(CONTINUITY_SWITCH)
    record = _live_candidate(cid, candidate_id)
    resolved = require_inference("investigation-continuity", cid)
    budget = investigation_budget.for_entry("continuity")
    _refuse_unpriceable("investigation-continuity", resolved, budget)
    try:
        reserved = runs.reserve_campaign_background(
            request.app, cid, CONTINUITY_KIND, x_grimoire_attempt,
            labels={CANDIDATE_LABEL: candidate_id},
            match=lambda live: live.labels.get(CANDIDATE_LABEL) == candidate_id)
    except runs.RunInFlightError as exc:
        live = request.app.state.runs.get(exc.run_id, runs.campaign_subject(cid))
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": "another finding is being investigated",
            "run": runs.run_payload(live) if live is not None else None}) from exc
    if reserved is None:
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": "the storage location is being changed; try again"})
    run, fresh = reserved
    if fresh:
        with runs.reservation(request.app, run):
            runs.start_computing(request.app, run, lambda: _continuity_work(
                request.app, run, cid, candidate_id, client, embed_client, resolved, budget,
                record))
    return {"run": runs.run_payload(run)}


# ---- E3: a history question (12-S5) ----
async def _question_work(app, run: runs.Run, cid: str, question: str, client: LLMClient,
                         embed_client: AsyncEmbeddingsClient | None,
                         resolved: ResolvedInference, budget: tool_calls.RunBudget) -> dict:
    seed = await anyio.to_thread.run_sync(question_seed, cid, question)
    state = investigation_state.RunState(cid=cid, mode="question", embed_client=embed_client)
    outcome = await investigate_with_decide(
        app, state=state, task="investigation-question", seed=seed, client=client,
        resolved=resolved, budget=budget, run_id=run.id, cancelled=lambda: _stillborn(run))
    return {"state": "cancelled" if outcome.state == "cancelled" else "landed",
            "result": {"outcome": outcome.as_dict(), "question": question}}


@router.post("/campaigns/{cid}/history/question", status_code=202)
@computes_only  # a draft: nothing it produces is stored
def post_history_question(cid: str, body: HistoryQuestion, request: Request,
                          client: LLMClient = Depends(get_llm),
                          embed_client: AsyncEmbeddingsClient = Depends(get_embeddings),
                          x_grimoire_attempt: str | None = Header(default=None)):
    """Ask the campaign's history a question (roadmap 12, E3): a computing
    `draft`, answered 202; the answer is held on the run and never stored.
    The same refusals as E2, before anything is reserved, plus 400 for an
    empty or overlong question."""
    _campaign_or_404(cid)
    _refuse_off(QUESTION_SWITCH)
    question = (body.question or "").strip()
    if not question or len(question) > QUESTION_CHARS:
        raise HTTPException(status_code=400, detail={
            "kind": "bad_question",
            "detail": f"a question is 1-{QUESTION_CHARS} characters"})
    resolved = require_inference("investigation-question", cid)
    budget = investigation_budget.for_entry("question")
    _refuse_unpriceable("investigation-question", resolved, budget)
    return runs.run_draft(request.app, runs.campaign_subject(cid), QUESTION_KIND,
                          x_grimoire_attempt,
                          lambda run: _question_work(request.app, run, cid, question, client,
                                                     embed_client, resolved, budget),
                          pass_run=True)
