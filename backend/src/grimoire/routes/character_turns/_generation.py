"""Optional decisions and tools precede visible contribution text and preserve the plain-stream fallback."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import anyio
from starlette.concurrency import run_in_threadpool

from ... import deadline, decisions, draws, llm_reasoning, prompts, store, tool_calls
from ... import inference as operations
from ...llm_errors import LLMError
from ...store.inference import resolve as inference_resolve
from .. import decision_capture, streaming, tool_decision
from ..common import _soft_resolved, plan_ceiling, plan_deadline, require_inference
from ._router import router as router

_log = logging.getLogger('grimoire.character_turns')


#: The task each turn-plan mode asks under (roadmap 02 §6.4, §7.2): the plan
#: REPLACES the intent call, so a contribution never pays for both.
_PLAN_TASKS = {"intent": "turn-intent", "plan": "turn-plan"}


def _plan_mode(cid, round_record, actor, appended):
    """The turn decision this contribution asks ("intent" or "plan"), or ""
    for none (roadmap 02 §6.3): the switch is off; the actor is the narrator,
    who has no stance; the round resumes a pending response (a retry replays
    its snapshot, a roll resume carries the stored record); a roll
    continuation is appended; or the player typed the round's direction,
    which IS the direction a sampled stance could contradict. An empty send's
    app-worded note is not the player's, so it does not count.

    Reads the same state `_prepare` does, outside the lock: the run owns the
    round, so the two disagree only when the fence is about to fail anyway.
    Fail-soft: an optional step that cannot read its own switch asks
    nothing, rather than failing the turn (§3.3)."""
    if actor in (None, "grimoire") or appended:
        return ""
    if round_record.get("pending_response") or round_record.get("typed_note"):
        return ""
    try:
        mode = store.play_decisions.turn_plan_mode(cid)
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn
        _log.error("turn plan switch unreadable for %s: %s", cid, type(exc).__name__)
        return ""
    return mode if mode in _PLAN_TASKS else ""


def _turn_inputs(cid, sid, actor):
    """`(name, conversation, brief)` for a turn decision about `actor`: their
    name, the observable transcript the speaker pick reads (through the
    prompt-phase regex view) and their OWN card brief, through the locked
    version the writer reads ("" when it cannot be read). Renders templates,
    so it runs in the threadpool."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    conversation = store.response_protocol.observable_conversation(
        messages, lambda posts, offset, total: store.regex.view.view(
            posts, cid=cid, phase="prompt", offset=offset, total=total))
    entry = next(a for a in store.appearances.scene_cast(cid, sid)
                 if f"{a['kind']}:{a['id']}" == actor)
    try:
        card = store.characters.read_card(
            store.appearances.locked_actor_root(cid), entry["id"],
            store.appearances.locked_version(cid, entry["kind"], entry["id"]))["data"]
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound):
        card = {}
    return entry["name"], conversation, store.turn_plan.brief_of(card)


def _plan_extras(cid, sid, round_record, actor) -> tuple[store.turn_plan.Extra, ...]:
    """The items another feature rides on a turn plan's call (§7.3: 13-C3's
    NPC Action choice). 02 adds none; each owner keeps every 02-C6 rule."""
    return ()


async def _turn_plan(cid, sid, client, round_record, actor, response_id, mode="intent"):
    """The turn decision's record for one contribution (roadmap 02 §6.4,
    §7), which never raises an `Exception`: the stance (or none, with why) --
    and, for a plan, the disclosure, the tension and any extra items' results
    -- or what skipped it.

    Soft (§3.2): a resolution the seam refuses -- a missing key, an incapable
    Decision model -- skips with the refusal's kind and calls nothing. Bounded
    (§3.4): one `plan_deadline` across every call `decide` makes. And caught
    whole (§3.3): a failed or refused call skips with its kind (a spent
    deadline is `timeout`), and anything else -- a builder, a template, the
    mapping -- with its class name, logged at ERROR with that name only. A
    cancel is not an `Exception`, so Stop passes through as ever. An abstained
    stance is an answer of "no particular stance", recorded and never asked
    again elsewhere."""
    task = _PLAN_TASKS[mode]
    record: dict = {"task": task, "skipped": ""}
    try:
        if mode == "plan":
            resolved, _why, kind = await run_in_threadpool(
                _soft_resolved,
                lambda: require_inference("turn-plan", cid, operation="decide"))
        else:
            resolved, _why, kind = await run_in_threadpool(
                _soft_resolved,
                lambda: require_inference("turn-intent", cid, operation="decide"))
        if resolved is None:
            return {**record, "skipped": kind or "unresolved"}
        ceiling = await run_in_threadpool(plan_ceiling)
        name, conversation, brief = await run_in_threadpool(_turn_inputs, cid, sid, actor)
        extra: tuple[store.turn_plan.Extra, ...] = ()
        if mode == "plan":
            extra = await run_in_threadpool(_plan_extras, cid, sid, round_record, actor)
            items = await run_in_threadpool(
                lambda: store.turn_plan.plan_items(
                    store.turn_plan.plan_item(name, conversation, brief), extra))
            async with decision_capture.capturing(cid, sid, task) as scope:
                decision = await operations.decide(
                    "turn-plan", list(items), client=client, resolved=resolved,
                    campaign=cid, scene=sid, post=round_record["post"],
                    round_id=round_record["id"], response_id=response_id,
                    capture=scope.hook(), around=plan_deadline(ceiling))
        else:
            items = (await run_in_threadpool(
                store.turn_plan.intent_item, name, conversation, brief),)
            async with decision_capture.capturing(cid, sid, task) as scope:
                decision = await operations.decide(
                    "turn-intent", list(items), client=client, resolved=resolved,
                    campaign=cid, scene=sid, post=round_record["post"],
                    round_id=round_record["id"], response_id=response_id,
                    capture=scope.hook(), around=plan_deadline(ceiling))
        stance, reason, drawn = _drawn_stance(cid, sid, items[0], decision.items[0])
        out = {**record, "stance": stance, **({"reason": reason} if reason else {}),
               **({"draw": drawn} if drawn is not None else {})}
        if mode == "plan":
            out.update(store.turn_plan.plan_answers(decision.items[0]))
            if extra:
                out["extra"] = store.turn_plan.extra_records(extra, decision.items[1:])
        return out
    except LLMError as exc:
        return {**record, "skipped": exc.kind or "error"}
    except decisions.DecideRequestError:
        return {**record, "skipped": "invalid_request"}
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn (§3.3)
        _log.error("turn decision for %s/%s skipped: %s", cid, sid, type(exc).__name__)
        return {**record, "skipped": f"error:{type(exc).__name__}"}


async def _pre_generation(cid, sid, client, turn, actor):
    """`(plan, response_id)`: the turn decision this contribution asks, if
    any (roadmap 02 §6.3), and the response id it was metered under -- or
    `(None, "")`, reading nothing past the switch, minting and asking
    nothing."""
    mode = await run_in_threadpool(_plan_mode, cid, turn.round_record, actor, turn.appended)
    if not mode:
        return None, ""
    response_id = store.responses.mint_id()
    return (await _turn_plan(cid, sid, client, turn.round_record, actor, response_id, mode),
            response_id)


async def _contribution_tool(cid, sid, client, round_record, actor, *, response_id, task,
                             messages, resolved, run):
    """`(tool, record)` for a contribution the decide tool may be offered to
    (§8.4): the opened loop, or None; and the variant's record of it, or
    None when the switch and the scene route offer nothing."""
    if not await run_in_threadpool(_offers_tool, cid, actor, resolved):
        return None, None
    tool, record = await _play_tool(cid, sid, client, round_record, actor, response_id)
    if tool is not None:
        tool = await _opened(tool, task, messages, client, resolved, run)
    return tool, record


def _drawn_stance(cid, sid, item, result):
    """The stance, drawn where the report allows (`turn_plan.drawn_stance`,
    roadmap 02 §6.5). A `ValueError` from the draw is a defect: the plain
    answer stands with no record, logged at ERROR by class only."""
    try:
        return store.turn_plan.drawn_stance(item, result, draws.new_seed())
    except ValueError as exc:
        _log.error("stance draw for %s/%s failed, the answer stands: %s",
                   cid, sid, type(exc).__name__)
        stance, reason = store.turn_plan.stance_of(result)
        return stance, reason, None


# --- Decision as a tool from play (roadmap 02-C4) ----------------------------

#: The tool's one shape (§8.1): its name, no model-supplied context (the item
#: is built over the observable window, never the writer's whole prompt), a
#: question of at most 300 characters, 2..6 options, the selection only --
#: never the distribution, which would invite the model to argue with the
#: draw -- and a cap answered as a result rather than an error that would
#: fail the stream.
PLAY_TOOL_SHAPE = tool_decision.ToolShape(
    name="decide_choice",
    description=("Ask a closed question about what happens next, with two to six "
                 "options, and get the chosen option back instead of deciding it "
                 "yourself. Only before you write any of your reply."),
    context=False, max_question_chars=300, min_options=2, max_options=6,
    result="selection", on_cap="result")

#: Tool calls one contribution may make (§8.3): one, so a used call costs at
#: most a second loop turn -- each re-sends the whole contribution prompt.
PLAY_TOOL_CALLS = 1

#: The contribution's run budget (§8.3): two turns, one call, one decision.
#: No output cap of the loop's own -- each turn is sent the scene preset's
#: own, or none, as a plain contribution is -- and no finalize instruction:
#: after a call, turn 2 is the contribution itself, continued from the tool
#: result. No wall of the run's own either, as a plain contribution has none.
PLAY_TOOL_BUDGET = tool_calls.RunBudget(max_turns=2, max_tool_calls=PLAY_TOOL_CALLS,
                                        max_decisions=1, wall_seconds=0,
                                        max_output_tokens=None, finalize_note=False)


@dataclass
class _PlayTool:
    """One contribution's decide tool: the toolset the loop is offered, the
    variant's `tool` record it fills, the capture scope its decision is
    filed in, and the loop's result once it is done."""

    toolset: tool_calls.Toolset
    record: dict
    scope: contextlib.AsyncExitStack
    perception: bool
    attribution: dict
    result: tool_calls.LoopResult | None = None
    events: Any = None
    buffer: llm_reasoning.Buffer | None = None
    #: Each loop turn's usage holder, as its meter opens (`stream_tools`).
    holders: list = dataclasses.field(default_factory=list)


def _offers_tool(cid, actor, resolved):
    """Whether this contribution may be offered the decide tool (§8.4), read
    off the switch and the scene route's primary: the switch on, an NPC
    (never the narrator), and a primary not KNOWN unable to call tools (an
    `unknown` offers, as every capability does). Fail-soft."""
    if actor in (None, "grimoire") or resolved is None:
        return False
    try:
        return (store.play_decisions.play_tool(cid)
                and not inference_resolve.known_lacks(resolved, "tools"))
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn
        _log.error("play tool switch unreadable for %s: %s", cid, type(exc).__name__)
        return False


def _tool_selection(record: dict, args: dict, result: decisions.ItemResult) -> dict:
    """What the model is sent for its decision, and the record of it (§8.1,
    §8.6): the selection drawn where a report allows (§6.5's rule: the none
    excluded, a cutoff at half of indifference), the plain answer otherwise,
    or none with why. Never the distribution."""
    answer = result.answers.get(tool_decision.QUESTION_ID)
    record.update(backend=result.backend)
    if answer is None or answer.answer is None:
        reason = "abstained" if answer is not None and answer.reason == "abstained" \
            else "unanswered"
        record.update(selected=None, reason=reason)
        return {"selected": None, "reason": reason}
    options = tuple(decisions.Option(str(o.get("id", "")), str(o.get("description", "")))
                    for o in args.get("options") or () if isinstance(o, dict))
    question = decisions.Choice(tool_decision.QUESTION_ID, str(args.get("question", "")),
                                options, allow_none=bool(args.get("allow_none")))
    selected = answer.answer
    try:
        # The none is excluded only where it was offered (a mask names
        # offered keys alone): whether to answer at all is the model's.
        drawn = draws.draw(question, result, seed=draws.new_seed(), purpose="tool",
                           eligibility=draws.Eligibility(
                               exclude=(decisions.NONE_KEY,) if question.allow_none else (),
                               cutoff=1 / (2 * len(options))))
        if drawn.basis in ("sampled", "answer") and isinstance(drawn.value, str):
            selected = drawn.value
        record["draw"] = drawn.record
    except ValueError as exc:
        _log.error("tool draw failed, the answer stands: %s", type(exc).__name__)
    record.update(selected=selected)
    return {"selected": selected}


async def _play_tool(cid, sid, client, round_record, actor, response_id):
    """The decide tool for one contribution (§8), or None when it is not
    offered. Soft (§3.2): a Decision resolution the seam refuses offers
    nothing and is recorded on the variant as `skipped`. The decision is
    filed through one capture scope, held open until the loop is done."""
    record: dict = {"task": "turn-tool-decision", "offered": False}
    try:
        resolved, _why, kind = await run_in_threadpool(
            _soft_resolved,
            lambda: require_inference("turn-tool-decision", cid, operation="decide"))
        if resolved is None:
            return None, {**record, "skipped": kind or "unresolved"}
        ceiling = await run_in_threadpool(plan_ceiling)
        name, conversation, _brief = await run_in_threadpool(_turn_inputs, cid, sid, actor)
        context = await run_in_threadpool(lambda: prompts.render(
            "turn_plan/tool_item.j2", name=name, conversation=conversation))
        stack = contextlib.AsyncExitStack()
        scope = await stack.enter_async_context(
            decision_capture.capturing(cid, sid, "turn-tool-decision"))
        asked: dict = {}
        try:
            spec, why = tool_decision.decision_tool(
                "turn-tool-decision", resolved, client, cid=cid, scene=sid,
                post=round_record.get("post"), round_id=round_record["id"],
                response_id=response_id, shape=PLAY_TOOL_SHAPE,
                context=context, select=lambda result: _tool_selection(record, asked, result),
                capture=scope.hook())
        except Exception:
            await stack.aclose()
            raise
        if spec is None:
            await stack.aclose()
            return None, {**record, "skipped": why or "unresolved"}
        inner = spec.fn

        async def ask(args, ctx):
            # The question and its options, recorded as asked (bounded by
            # the shape); one deadline for the whole decision (§3.4), and a
            # decision that outlives it is unanswered, never a failed stream.
            asked.clear()
            asked.update(args)
            raw = args.get("options")
            record.update(question=str(args.get("question", ""))[:PLAY_TOOL_SHAPE.max_question_chars],
                          options=[str(o.get("id", "")) for o in raw if isinstance(o, dict)]
                          if isinstance(raw, list) else [])
            try:
                return await deadline.bounded(inner(args, ctx), ceiling,
                                              lambda _s: TimeoutError())
            except TimeoutError:
                record.update(selected=None, reason="unanswered")
                return tool_calls.ToolOutput(json.dumps({"selected": None,
                                                         "reason": "unanswered"}))

        tool = dataclasses.replace(spec, fn=ask)
        record["offered"] = True
        attribution = {"campaign": cid, "scene": sid, "post": round_record.get("post"),
                       "round_id": round_record["id"] or "", "response_id": response_id}
        return _PlayTool(tool_calls.Toolset((tool,)), record, stack,
                         perception=actor != "grimoire", attribution=attribution), record
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn (§3.3)
        _log.error("play tool for %s/%s skipped: %s", cid, sid, type(exc).__name__)
        return None, {**record, "skipped": f"error:{type(exc).__name__}"}


def _visibility(perception):
    """A fresh predicate per loop turn (§8.2): has this turn begun its reply?
    Only a leading hidden perception fence is not, so a tool call after one
    is still honoured; anything past it -- prose, or a state, roll or handoff
    fence -- is final (`response_protocol.opens_reply`)."""
    return lambda: store.response_protocol.opens_reply(perception)


class _LoopMeter:
    """A contribution's meter while it runs as a tool loop: the loop meters
    every turn itself under the run's id (01g-C3), so this files nothing --
    `done` is a no-op -- and its `usage` is the live holder of the loop's
    latest turn (`stream_tools(holders=)`), which the facade stamps in place.
    So the display rules (`streaming._Display`), `_made_by` and
    `streaming._served` read the attempt that served, a fallback included,
    as they read a plain contribution's meter -- also when the loop is
    closed before it finished (a roll fence, a failed turn)."""

    def __init__(self, task, holders):
        self.task = task
        self._holders = holders
        self._none: dict = {}
        self.row = None

    @property
    def usage(self) -> dict:
        return self._holders[-1] if self._holders else self._none

    def done(self, *_args, **_kwargs):
        return None


async def _opened(tool, task, messages, client, resolved, run):
    """`tool`'s loop opened over the contribution's prompt (01g-C6), or None
    -- its scope closed and its record `skipped` -- when the loop refuses to
    start (its entry checks are made here, before anything is sent), so the
    contribution runs as a plain generation instead of failing."""
    tool.buffer = llm_reasoning.Buffer()
    options = {"toolset": tool.toolset, "execute": tool_calls.registered(tool.toolset),
               "client": client, "budget": PLAY_TOOL_BUDGET, "run_id": run.id,
               "scene_identity": run.scene_identity,
               "cancelled": lambda: run.cancel_requested, "decline_after_text": True,
               "reasoning": tool.buffer, "visibility": _visibility(tool.perception),
               "holders": tool.holders, **tool.attribution}
    try:
        # A fresh contribution is `chat` and a reroll `regenerate` (§8.4),
        # each named as the literal the operation guard reads.
        if task == "regenerate":
            tool.events = operations.stream_tools("regenerate", messages, resolved=resolved,
                                                  **options)
        elif task == "chat":
            tool.events = operations.stream_tools("chat", messages, resolved=resolved,
                                                  **options)
        else:
            raise ValueError(f"{task!r} is never offered the decide tool")
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn (§3.3)
        _log.error("play tool loop refused for %s: %s", task, type(exc).__name__)
        tool.record.update(offered=False, skipped=f"error:{type(exc).__name__}")
        await tool.scope.aclose()
        return None
    return tool


async def _tool_events(tool, watcher):
    """The contribution's display events as a tool loop yields them, in
    `llm_reasoning.stream`'s shape: reasoning drained before each delta,
    every turn streamed, a tool call honoured only before visible text
    (`_visibility`), the watcher handed a fresh reply between a tool turn
    and the next, and the loop's result kept on `tool`. A loop that failed
    is the contribution's failure, as any stream failure is."""
    try:
        async with aclosing(tool.events) as running:
            async for event in running:
                if event.kind == "text":
                    for item in tool.buffer.drain():
                        yield item
                    yield {"delta": event.delta}
                elif event.kind == "turn_end" and event.interstitial:
                    watcher.next_turn()
                elif event.kind == "done":
                    tool.result = event.result
        for item in tool.buffer.drain():
            yield item
    finally:
        await tool.scope.aclose()
    result = tool.result
    if result is not None:
        if any("after_text" in e.note for e in result.trace if e.kind == "tool"):
            tool.record["declined"] = "after_text"
        tool.record["called"] = any(e.kind == "tool" and "declined" not in e.note
                                    for e in result.trace)
        if result.status == "failed" and isinstance(result.error, LLMError):
            raise result.error


async def _loop_or_plain(tool, watcher, task, messages, client, resolved):
    """The contribution through its tool loop (`_tool_events`), or -- when
    the loop failed or ended before the reader saw any reply text -- through
    a plain generation instead (§3.3: the optional tool never fails, or
    empties, a turn a plain contribution would have played). The fallback is
    metered as a plain contribution is, its holder joining the loop's so the
    contribution's meter reads it, and recorded on the variant as
    `fell_back` (the failure's kind, or `empty`). Visible text already shown
    is final: a failure after it is the contribution's, as any stream's.
    "Shown" is the reply's own watcher's (`ResponseWatcher.opened`), which
    the consumer has fed every delta yielded, each loop turn's leading
    preparation fence excluded."""
    try:
        async with aclosing(_tool_events(tool, watcher)) as events:
            async for event in events:
                yield event
    except LLMError as exc:
        if watcher.opened:
            raise
        tool.record["fell_back"] = exc.kind
    else:
        if watcher.opened:
            return
        tool.record["fell_back"] = "empty"
    _log.error("play tool loop for %s fell back to a plain generation: %s", task,
               tool.record["fell_back"])
    watcher.next_turn()
    with store.usage.meter(task, **tool.attribution) as m:
        tool.holders.append(m.usage)
        async with aclosing(llm_reasoning.stream(m.usage, lambda: operations.generate(
                task, messages, client=client, resolved=resolved, usage=m.usage))) as plain:
            try:
                async for event in plain:
                    yield event
            except GeneratorExit:
                if watcher.roll.complete:
                    m.done()   # the roll fence ended the reply: filed as a plain one is
                raise


def _reasoning_frame(event, watcher, liveness):
    """The frame for a reasoning event, recording it on the watcher; None for an
    event that carries reply text."""
    if event.get("thinking_reset"):
        watcher.reasoning = ""
    elif "thinking_delta" in event:
        watcher.reasoning += event["thinking_delta"]
    else:
        return None
    liveness.sent()
    return streaming._sse(event)


async def _stream_events(client, messages, resolved, meter, watcher, run, display,
                         task="chat", tool=None):
    # Heartbeats are throttled here, at the producer, for `streaming._Liveness`'s
    # reason: every SSE line arrives as an empty delta. Liveness records frames
    # that went out, so a display frame the throttle held back is not one.
    liveness = streaming._Liveness()
    # The generation is opened by `llm_reasoning.stream` once its display
    # buffer is in the holder, so the adapters find it from the first frame.
    source_of = (_loop_or_plain(tool, watcher, task, messages, client, resolved)
                 if tool is not None
                 else llm_reasoning.stream(meter.usage, lambda: operations.generate(
                     task, messages, client=client, resolved=resolved, usage=meter.usage)))
    async with aclosing(source_of) as source:
        async for event in source:
            if run.cancel_requested:
                raise anyio.get_cancelled_exc_class()()
            reasoning = _reasoning_frame(event, watcher, liveness)
            if reasoning:
                yield reasoning
                continue
            delta = event["delta"]
            frames = await display.frames(watcher.feed(delta))
            if frames:
                liveness.sent()
                for frame in frames:
                    yield frame
            elif (not delta or display.active) and liveness.due():
                # An empty delta is the facade waiting on the model; with a
                # display rule in force, a non-empty one whose text is hidden
                # sends nothing either (`_fence_stream` says why it is only then).
                yield streaming._HEARTBEAT
            if watcher.roll.complete:
                break


async def _stream_contribution(client, messages, resolved, meter, watcher, run, cid=None,
                               task="chat", tool=None):
    # Display rules for the connection that serves the call, settled on the
    # first visible text (`streaming._Display`), so a fallback's reply is shaped
    # by the rules it is saved under. Frames replace `delta` frames one for
    # one, so `keep` counts from this contribution's `response_start`. `task`
    # is the one the meter files and `generate` is asked for, and `resolved`
    # that task's resolution.
    display = streaming._Display(cid, streaming._asked(resolved), meter)
    try:
        async with aclosing(
            _stream_events(client, messages, resolved, meter, watcher, run, display,
                           task=task, tool=tool)
        ) as events:
            async for frame in events:
                yield frame
    except LLMError:
        # What the throttle held back is on screen after a refresh and must be
        # before it: `_rescue` saves the partial whole. A delta stream has
        # nothing held back, so it sends nothing here.
        await display.build()
        if display.active:
            for frame in await display.frames(watcher.finish(), last=True):
                yield frame
        raise
    finally:
        if tool is not None:
            # A loop closed before its first event never ran `_tool_events`'s
            # own close; a second close is a no-op.
            await tool.scope.aclose()
    visible = watcher.finish()
    for frame in await display.frames(visible, last=True):
        yield frame
    if run.cancel_requested:
        # Stop can arrive while the final visible delta is being delivered.
        # Rescue must retain this pending slot just as for midstream Stop.
        raise anyio.get_cancelled_exc_class()()
