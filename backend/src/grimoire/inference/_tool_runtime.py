"""One bounded tool run: shared state, transitions, and driver cleanup."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, replace
from functools import partial
from typing import Literal

from .. import (
    deadline,
    llm,
    llm_reasoning,
    llm_sampling,
    model_guidance,
    schemas,
    store,
    tool_calls,
    wire,
)
from ..llm import LLMClient
from ..llm import _lifecycle as llm_lifecycle
from ..llm_errors import LLMError
from ..store.inference import probes
from ..store.inference.resolved import ResolvedInference
from ..tool_calls import _runs as tool_runs
from . import _contracts, _pricing, _requests, _tool_policy
from ._contracts import TurnCapture as TurnCapture
from ._pricing import Price as Price


class _RunView:
    """What a tool may ask of the run it is in (`tool_calls.RunView`): a
    decision against the run's cap, the spend left under its ceiling, a
    charge for what a tool's own LLM calls cost (01g-S7), and a decision's
    outcome for the trace."""

    def __init__(self, loop: _Loop):
        self._loop = loop

    def take_decision(self) -> bool:
        loop = self._loop
        if loop.decisions_used >= loop.budget.max_decisions:
            # Reached, a zero cap included: the run stops after this turn.
            loop.decisions_capped = True
            return False
        loop.decisions_used += 1
        if loop.decisions_used >= loop.budget.max_decisions:
            loop.decisions_capped = True
        return True

    def room_usd(self) -> float | None:
        ceiling = self._loop.budget.spend_ceiling_usd
        return None if ceiling is None else ceiling - self._loop._spent()

    def charge(self, usd: float) -> None:
        self._loop.charged += max(0.0, usd)

    def note_decision(self, status: str, option: str | None = None) -> None:
        """A decide-tool call's outcome, as the trace's `decide` entry (spec
        3.12): its status in `note`, the option chosen in `name`."""
        loop = self._loop
        loop._trace(loop.turns, "decide", option or "", ok=status == "answered", note=status)


@dataclass(frozen=True)
class _Turn:
    """One settled model turn."""
    turn: int
    text: str
    calls: tuple[tool_calls.ToolCall, ...]
    finish: str
    opaque: dict | None
    target: wire.Target | None
    #: Whether the turn streamed visible text (01g-C6).
    shown: bool = False
    #: Whether the turn streamed text the CALLER's reader would see -- its
    #: `visibility` predicate's answer, which a hidden fence does not meet
    #: (roadmap 02 §8.2); `shown` when the caller gave none.
    visible: bool = False


class _Loop:
    """One run's state; `events` runs it (spec 3.7)."""

    def __init__(self, task: str, messages: list[dict], *, toolset: tool_calls.Toolset,
                 execute: tool_calls.Execute, client: LLMClient,
                 resolved: ResolvedInference, budget: tool_calls.RunBudget, run_id: str,
                 attribution: dict, scene_identity: str,
                 cancelled: Callable[[], bool] | None, final_schema: dict | None,
                 tool_choice: str, capture: TurnCapture | None,
                 prices: dict[tuple[str, str], Price | None] | None = None,
                 streaming: bool = False, decline_after_text: bool = False,
                 reasoning: llm_reasoning.Buffer | None = None,
                 visibility: Callable[[], Callable[[str], bool]] | None = None,
                 holders: list[dict] | None = None):
        prices = prices or {}
        #: The caller's list each turn's usage holder is appended to as its
        #: meter opens (02 §8.5): the facade stamps the holder in place, so
        #: the caller reads which attempt is serving while the turn streams.
        self.holders = holders
        #: 01g-C6: each turn through `client.stream`, the caller's display
        #: reasoning buffer in each turn's holder, and visible text final.
        self.streaming, self.decline_after_text = streaming, decline_after_text
        self.reasoning = reasoning
        self.shown = False
        #: A fresh predicate per turn (02 §8.2): does this delta show the
        #: reader anything? None reads every non-empty delta as visible.
        self.visibility = visibility
        self.visible = False
        self._sees: Callable[[str], bool] | None = None
        self.task, self.base, self.toolset, self.execute = task, messages, toolset, execute
        self.client, self.resolved, self.budget, self.run_id = client, resolved, budget, run_id
        self.attribution, self.scene_identity = attribution, scene_identity
        self.cancelled, self.final_schema, self.capture = cancelled, final_schema, capture
        self.choice = tool_choice
        self.defs = toolset.definitions()
        self.appended: list[dict] = []
        self.rows: list[dict] = []
        self.trace: list[tool_calls.TraceEntry] = []
        self.proposals: list[dict] = []
        self.emit: Callable[[object], None] = lambda _item: None
        self.turns = self.calls_used = self.result_chars = self.minted = 0
        self.finalized = self.fallen = False
        self.writers: set[tuple[str, str]] = set()
        self.limit = self.text = self.root = ""
        self.wall = self.call_budget = 0.0
        self.t0 = time.monotonic()
        self._hist: list[dict] | None = None
        self._hist_len = 0
        #: The latest turn's usage holder, which the facade stamps with the
        #: attempt that went out (`llm.ATTEMPTED`): what a failed turn's
        #: capture names, a fallback's included.
        self._holder: dict | None = None
        #: The spend axis (01g-S5): each attempt's price, read at entry, each
        #: sent turn's `(projection, row)`, and the prompt count the next
        #: projection builds on.
        self.prices = prices
        self.spends: list[tuple[float, dict | None]] = []
        self.last_prompt: float | None = None
        self.next_prompt = 0.0
        self.sent_len = 0
        #: The decide tool's share (01g-S7): decisions taken, and what its
        #: calls cost as the guard prices them.
        self.decisions_used = 0
        self.decisions_capped = False
        self.charged = 0.0
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
        self.root, self.wall, self.call_budget = await asyncio.to_thread(self._pin)
        queue: asyncio.Queue = asyncio.Queue()
        self.emit = queue.put_nowait
        runner = asyncio.create_task(self._drive())
        try:
            while True:
                item = await _next_or_beat(queue)
                if item is None:
                    yield tool_calls.LoopEvent("text", turn=self.turns, delta="")
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
        me = asyncio.current_task()
        if me is not None:
            _tool_policy._LOOPING.add(me)
        try:
            result: tool_calls.LoopResult | None = None
            while result is None:
                result = await self._step()
        except BaseException as exc:
            # Handed to the consumer, which raises it there.
            self.emit(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        _contracts.log.info("tool run %s (%s) ended %s%s: %d turns, %d tool calls, %.1fs",
                 self.run_id, self.task, result.status,
                 f" on {result.limit}" if result.limit else "", self.turns,
                 self.calls_used, time.monotonic() - self.t0)
        self.emit(tool_calls.LoopEvent("done", turn=self.turns, result=result))

    def _pin(self) -> tuple[str, float, float]:
        """The store root, the run's wall and the per-call ceiling, read once
        off the event loop (`store.home` can read the bootstrap pointer)."""
        wall = self.budget.wall_seconds
        call = store.config.llm_call_budget()
        return str(store.home()), (call if wall is None else wall), call

    def _root_held(self) -> bool:
        return str(store.home()) == self.root

    async def _step(self) -> tool_calls.LoopResult | None:
        self._check_cancelled()
        stop = self._turn_refusal()
        if stop:
            self.limit = stop
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

    def _check_cancelled(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise asyncio.CancelledError("the run was cancelled")

    def _elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _wall_short(self, need: float) -> bool:
        return self.wall > 0 and self._elapsed() + need > self.wall

    def _turn_refusal(self) -> str:
        """The limit that refuses the next tool turn (spec 3.9), or ""."""
        if self.limit:
            return self.limit
        reserve = 1 if self.budget.reserve_final and not self.finalized else 0
        if self.turns + 1 + reserve > self.budget.max_turns:
            return "turns"
        if self._wall_short(tool_runs.MIN_TURN_SECONDS):
            return "wall"
        return ""

    def _final_fits(self) -> bool:
        return (self.budget.reserve_final and not self.finalized
                and self.turns + 1 <= self.budget.max_turns
                and not self._wall_short(tool_runs.MIN_TURN_SECONDS))

    async def _finalize_or_stop(self) -> tool_calls.LoopResult:
        if self._final_fits():
            return await self._finalize(stopped=True)
        return self._result("budget_exhausted")

    async def _finalize(self, *, stopped: bool) -> tool_calls.LoopResult:
        """The finalize turn (spec 3.7 step 7)."""
        self.finalized = True
        note = (await asyncio.to_thread(partial(_tool_policy.finalize_message, self.final_schema,
                                                stopped=stopped))
                if self.budget.finalize_note or self.final_schema is not None else None)
        turn = await self._turn(final=True, extra=note)
        if turn is None:
            return self._result("budget_exhausted")
        if isinstance(turn, tool_calls.LoopResult):
            return turn
        status: Literal["completed", "budget_exhausted", "failed"] = (
            "budget_exhausted" if stopped else "completed")
        if self.final_schema is None:
            return self._result(status)
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, self.final_schema):
            return self._result(status, final=value)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    async def _settle(self, turn: _Turn) -> tool_calls.LoopResult | None:
        """A turn's calls answered, or the run ended by its answer."""
        if not turn.calls:
            return await self._answered(turn)
        if self.decline_after_text and turn.visible:
            # Visible text is final (01g-C6): the calls it ended with are
            # declined, and no turn follows it.
            for call in turn.calls:
                self._trace(turn.turn, "tool", call.name, call.id, ok=False,
                            note="declined: after_text")
            return self._result("completed", declined=turn.calls)
        if turn.finish != "length":
            ended = self._terminal(turn)
            if ended is not None:
                return ended
        results = [await self._one(turn, call) for call in turn.calls]
        self._append(turn, results)
        if self.calls_used >= self.budget.max_tool_calls:
            self.limit = self.limit or "tool_calls"
        if self.decisions_capped:
            self.limit = self.limit or "decisions"
        self._note_switch(turn.target)
        return None

    async def _answered(self, turn: _Turn) -> tool_calls.LoopResult:
        if self.final_schema is None:
            return self._result("completed")
        value = schemas.find_value(turn.text)
        if isinstance(value, dict) and tool_calls.conforms(value, self.final_schema):
            return self._result("completed", final=value)
        if (not self.finalized and self.turns + 1 <= self.budget.max_turns
                and not self._wall_short(tool_runs.MIN_TURN_SECONDS)):
            self._extend([{"role": "assistant", "content": turn.text}])
            return await self._finalize(stopped=False)
        return self._result("failed", error=LLMError(
            "bad_response", "the final answer did not match its schema",
            code="final_unreadable"))

    def _terminal(self, turn: _Turn) -> tool_calls.LoopResult | None:
        """A valid call to a terminal tool ends the run, unexecuted: handed
        back as `final_call`, the turn's other calls recorded `not_run`."""
        for call in turn.calls:
            spec = self.toolset.get(call.name)
            if spec is None or not spec.terminal or self._problem(call, spec):
                continue
            for other in turn.calls:
                if other is not call:
                    self._trace(turn.turn, "tool", other.name, other.id, ok=False,
                                note="not_run")
            return self._result("completed", final_call=call)
        return None

    # ---- one model turn ----
    async def _turn(self, *, final: bool, extra: dict | None = None
                    ) -> _Turn | tool_calls.LoopResult | None:
        """One model turn, or None when the spend ceiling or the context stop
        refused it unsent (`limit` says `spend` or `context`)."""
        chain, notes = self._chain(final)
        choice = self._final_choice(chain, notes) if final else self._choice(chain, notes)
        sending = self._outgoing(extra)
        cap = self.budget.max_prompt_tokens
        if cap is not None and await asyncio.to_thread(
                _pricing._prompt_count, list(sending), self.defs) > cap:
            # The caller's context stop (roadmap 12 §7.3): refused unsent.
            self.limit = self.limit or "context"
            return None
        projection = await self._projected(chain, sending, extra)
        if projection is not None and self._spent() + projection > (
                self.budget.spend_ceiling_usd or 0.0):
            self.limit = self.limit or "spend"
            return None
        k = self.turns = self.turns + 1
        self.shown = False
        self.visible = False
        self._sees = self.visibility() if self.visibility is not None else None
        if extra is not None:
            self.appended.append(extra)
        self.sent_len = len(self.appended)
        collector = tool_calls.Collector()
        started = time.monotonic()
        try:
            text, holder = await self._metered(k, chain, sending, choice, collector, final,
                                               projection)
        except _tool_policy.WallSpentError:
            self._trace(k, "model", ok=False, note="wall", started=started)
            self.limit = "wall"
            return self._result("budget_exhausted")
        except LLMError as exc:
            self._trace(k, "model", ok=False, note=exc.kind, started=started)
            await self._captured_failure(sending, exc)
            if k == 1:
                raise
            return self._result("failed", error=exc)
        return await self._settled_turn(k, text, holder, collector, final, notes, sending,
                                        started)

    async def _settled_turn(self, k: int, text: str, holder: dict,
                            collector: tool_calls.Collector, final: bool, notes: list[str],
                            sending: list[dict], started: float) -> _Turn:
        target = holder.get(llm.ATTEMPTED)
        target = target if isinstance(target, wire.Target) else None
        made = tuple(self._named(c) for c in collector.calls())
        calls = made if not final else ()
        # A joined turn's text is shown whole below, so it is visible text too.
        if text and not self.streaming:
            self.shown = True
            self.visible = self.visible or self._seen(text)
        turn = _Turn(k, text, calls, collector.finish_reason, collector.opaque(), target,
                     self.shown, self.visible)
        if text or not (final and made):
            # A finalize turn that only called (a chain sent `none` as
            # `auto`) leaves the text the run already had.
            self.text = text
        if target is not None and self._switched_to(target):
            notes.append("fell back")
        self._trace(k, "model", target.model if target else "", chars=len(text),
                    note=", ".join(notes), started=started)
        if final and made:
            # Declined, as a finalize turn's calls are on every target.
            self.final_declined = made
            for call in made:
                self._trace(k, "tool", call.name, call.id, ok=False, note="declined: final")
        await self._captured(sending, turn)
        if text and not self.streaming:
            self.emit(tool_calls.LoopEvent("text", turn=k, delta=text))
        self.emit(tool_calls.LoopEvent("turn_end", turn=k, finish=turn.finish,
                                       interstitial=bool(calls)))
        return turn

    # ---- the spend ceiling (01g-S5; spec 3.9) ----
    async def _projected(self, chain: wire.Chain, sending: list[dict],
                         extra: dict | None) -> float | None:
        """What the next send could cost, as the guard prices it: None with no
        ceiling. Prompt tokens are counted in a worker thread with the margin
        -- the whole prompt on the first send, else what was appended since
        the last, on top of that send's reported (else projected) count --
        and the completion is the turn's cap; the maximum over the attempts
        that could serve it."""
        cap = self.budget.max_output_tokens
        if self.budget.spend_ceiling_usd is None or cap is None:
            # `RunBudget` refuses an uncapped run beside a ceiling.
            return None
        if self.last_prompt is None:
            counted = await asyncio.to_thread(_pricing._prompt_count, list(sending), self.defs)
            prompt = counted * tool_calls.PROJECTION_MARGIN
        else:
            fresh = [*self.appended[self.sent_len:], *([extra] if extra is not None else [])]
            counted = await asyncio.to_thread(_pricing._prompt_count, fresh, ())
            prompt = self.last_prompt + counted * tool_calls.PROJECTION_MARGIN
        self.next_prompt = prompt
        costs = [_pricing._cost(self.prices.get((t.provider_id, t.model)), prompt,
                       _requests.clamp_to_max_output(t, cap))
                 for t in chain.attempts]
        known = [c for c in costs if c is not None]
        # Every attempt was priced at entry (`tool_run_refusal`); one that
        # still is not counts as the dearest known, never as free.
        return max(known) if known else float("inf")

    def _spent(self) -> float:
        """Every sent turn's cost as the guard prices it: its row's counts at
        its attempt's price, else that turn's projection. Never a reported
        figure (`cost_usd`) -- the guard keeps one unit."""
        total = self.charged
        for projection, row in self.spends:
            cost = self._row_cost(row)
            total += cost if cost is not None else projection
        return total

    def _row_cost(self, row: dict | None) -> float | None:
        if not isinstance(row, dict):
            return None
        prompt, completion = row.get("prompt_tokens"), row.get("completion_tokens")
        if not (isinstance(prompt, int) and isinstance(completion, int)):
            return None
        provider = row.get("provider_id", "")
        price = (self.prices.get((provider, row.get("requested_model", "")))
                 or self.prices.get((provider, row.get("model", ""))))
        return _pricing._cost(price, prompt, completion)

    def _note_spend(self, projection: float | None, row: dict | None) -> None:
        if projection is None:
            return
        self.spends.append((projection, row))
        prompt = row.get("prompt_tokens") if isinstance(row, dict) else None
        self.last_prompt = (float(prompt) if isinstance(prompt, int)
                            and not isinstance(prompt, bool) else self.next_prompt)

    def _turn_seconds(self) -> tuple[float | None, bool]:
        """This turn's wait (spec 3.9, joined): `min(llm_call_budget, wall
        left)`, either absent at `<= 0`, and whether the run's wall is the
        binding one."""
        left = self.wall - self._elapsed() if self.wall > 0 else None
        if self.call_budget > 0 and (left is None or self.call_budget <= left):
            return self.call_budget, False
        return left, left is not None

    async def _metered(self, k: int, chain: wire.Chain, sending: list[dict], choice: str,
                       collector: tool_calls.Collector, final: bool,
                       projection: float | None = None) -> tuple[str, dict]:
        """One turn through the facade, under its own meter carrying the run
        id and the turn (01g-C3), bounded by `_turn_seconds`."""
        schema = self.final_schema if final else None
        seconds, by_wall = self._turn_seconds()
        meter = store.usage.meter(self.task, **self.attribution, run_id=self.run_id,
                                  loop_turn=k)
        try:
            with meter as m:
                m.usage[tool_calls.KEY] = collector
                self._holder = m.usage
                if self.holders is not None:
                    self.holders.append(m.usage)

                def overrun(limit: float) -> LLMError:
                    if by_wall:
                        return _tool_policy.WallSpentError("timeout", "the run's wall clock ran out")
                    error = LLMError("timeout", f"the reply did not finish within "
                                                f"{limit:g}s — giving up")
                    self._note_overrun(m.usage, chain, error)
                    return error

                async def resend(again: wire.Chain) -> str:
                    with _requests._counting_earlier(m.usage):
                        return await self.client.complete(sending, again, m.usage, schema=schema,
                                                          tools=self.defs, tool_choice=choice)

                async def reopen(again: wire.Chain) -> AsyncGenerator[str, None]:
                    with _requests._counting_earlier(m.usage):
                        async with aclosing(self.client.stream(
                                sending, again, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice)) as deltas:
                            async for delta in deltas:
                                yield delta

                if self.reasoning is not None:
                    m.usage[llm_reasoning.KEY] = self.reasoning
                try:
                    if self.streaming:
                        source = (self.client.stream(sending, chain, m.usage, tools=self.defs,
                                                     tool_choice=choice) if schema is None
                                  else _requests._streamed_structured(self.client.stream(
                                      sending, chain, m.usage, schema=schema, tools=self.defs,
                                      tool_choice=choice), reopen))
                        text = await self._streamed(k, source, seconds, overrun)
                    else:
                        work: Awaitable[str] = (
                            self.client.complete(sending, chain, m.usage, tools=self.defs,
                                                 tool_choice=choice) if schema is None
                            else _requests._joined_structured(self.client.complete(
                                sending, chain, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice), resend))
                        text = await deadline.bounded(work, seconds, overrun)
                finally:
                    m.tool_calls = len(collector.calls())
        finally:
            if meter.row is not None:
                self.rows.append(meter.row)
            self._note_spend(projection, meter.row)
        return text, m.usage

    async def _streamed(self, k: int, source: AsyncGenerator[str, None], seconds: float | None,
                        overrun: Callable[[float], LLMError]) -> str:
        """One streamed turn (01g-C6): each delta handed on as it arrives,
        heartbeats included, as `generate(stream=True)` yields them. The
        turn's bound holds only until visible text: a turn that has shown
        text is never cut (spec 3.9), so the wall and the per-call ceiling
        are checked at each delta before the first visible one -- as often as
        the provider or the facade's heartbeat speaks. "Visible" is the
        caller's `visibility` when it gave one: text it hides (a leading
        perception fence, roadmap 02 §8.2) has shown its reader nothing, so a
        turn that writes only that and stalls is still cut."""
        parts: list[str] = []
        started = time.monotonic()
        async with aclosing(source) as deltas:
            async for delta in deltas:
                if delta:
                    self.shown = True
                    self.visible = self.visible or self._seen(delta)
                begun = self.visible if self._sees is not None else self.shown
                if (not begun and seconds is not None
                        and time.monotonic() - started > seconds):
                    raise overrun(seconds)
                parts.append(delta)
                self.emit(tool_calls.LoopEvent("text", turn=k, delta=delta))
        return "".join(parts)

    def _seen(self, text: str) -> bool:
        """Whether `text` shows the caller's reader anything (`visibility`),
        or -- with no predicate -- whether it is text at all."""
        return self._sees(text) if self._sees is not None else bool(text)

    def _note_overrun(self, holder: dict, chain: wire.Chain, error: LLMError) -> None:
        """An `llm_call_budget` overrun, filed against the attempt that was
        running (`routes.common._noting`'s rule)."""
        try:
            attempted = holder.get(llm.ATTEMPTED)
            self.client.note_outcome(attempted if isinstance(attempted, wire.Target)
                                     else chain.primary, error)
        except Exception as exc:  # noqa: BLE001 - bookkeeping on the failure path
            _contracts.log.warning("could not file a tool turn's timeout: %s", type(exc).__name__)

    def _chain(self, final: bool) -> tuple[wire.Chain, list[str]]:
        """The turn's chain (spec 3.9, 3.11): the per-turn output cap through
        `call_chain` (with the final schema on a finalize turn), the fallback
        alone once it has served, a fallback known unable to call tools
        never (the resolver drops one only on a route that requires them),
        and thinking off on a target that inherits another model's tool
        turns -- a fallback that would think anyway left out instead."""
        notes: list[str] = []
        full = _requests.call_chain(self.resolved, schema=self.final_schema if final else None,
                          max_tokens=self.budget.max_output_tokens)
        if self.fallen and full.fallback is not None:
            full = wire.Chain(full.fallback)
        else:
            full, dropped = _requests._tool_chain(self.resolved, full)
            if dropped is not None:
                notes.append(f"fallback {dropped.model} cannot call tools: not sent")
        sent = [self._inheriting(t, notes, droppable=n > 0)
                for n, t in enumerate(full.attempts)]
        kept = [t for t in sent if t is not None]
        return wire.Chain(kept[0], kept[1] if len(kept) > 1 else None), notes

    def _inheriting(self, target: wire.Target, notes: list[str], *,
                    droppable: bool) -> wire.Target | None:
        """`target` as sent to a turn whose history may hold tool turns
        another `(provider_id, model)` wrote (spec 3.11 rule 4): with
        thinking off, noted only where off is SENT (`llm_sampling`'s
        reading of the new target). An Anthropic model that thinks when
        nothing is sent (`adaptive_thinking`) and cannot be sent off would be
        refused that history, so as a fallback it is left out (None); a
        target the catalog says nothing of is sent off and noted unconfirmed."""
        if all(w == (target.provider_id, target.model) for w in self.writers):
            return target
        off = target.with_thinking_off()
        state = llm_sampling.effective(off)["controls"].get(
            llm_sampling.REASONING_PARAM.name, {}).get("state")
        if state in (llm_sampling.SUPPORTED, llm_sampling.TRANSLATED):
            notes.append(f"thinking off on {target.model}")
            return off
        thinks = (target.kind == "anthropic" and isinstance(target.model_features, dict)
                  and target.model_features.get("adaptive_thinking") is True)
        if thinks and droppable:
            notes.append(f"fallback {target.model} cannot be sent thinking off: not sent")
            return None
        notes.append(f"thinking off not confirmed on {target.model}")
        return off

    def _choice(self, chain: wire.Chain, notes: list[str]) -> str:
        """`required` downgraded to `auto` for a chain holding an attempt that
        would be refused it -- forced tool use beside thinking, implicit
        adaptive thinking included (`probes.tool_choice`'s rule)."""
        if self.choice != "required" or not any(self._auto_only(t) for t in chain.attempts):
            return self.choice
        notes.append("required sent as auto")
        return "auto"

    def _final_choice(self, chain: wire.Chain, notes: list[str]) -> str:
        """The finalize turn's `none` (spec 3.7 step 7), sent as `auto` to a
        chain holding a provider that takes only `auto`
        (`probes.AUTO_ONLY_PRESETS`): the finalize prompt asks for no more
        calls, and any the turn makes are declined, as on every target."""
        if not any(self._preset(t) in probes.AUTO_ONLY_PRESETS for t in chain.attempts):
            return "none"
        notes.append("none sent as auto")
        return "auto"

    def _preset(self, target: wire.Target) -> str:
        return next((a.provider_preset for a in self.resolved.attempts
                     if (a.provider_id, a.model) == (target.provider_id, target.model)), "")

    def _auto_only(self, target: wire.Target) -> bool:
        sent = llm_sampling.effective(target)["effective"].get("thinking")
        return probes.tool_choice(self._preset(target), target.kind, target.model_features,
                                  sent, target.model) == "auto"

    def _named(self, call: tool_calls.ToolCall) -> tool_calls.ToolCall:
        """A call with an id every provider takes: the provider's own while it
        is one (spec 3.11 rule 2), else the loop's."""
        if tool_calls.SAFE_ID.match(call.id):
            return call
        return replace(call, id=self._mint())

    def _mint(self) -> str:
        self.minted += 1
        return tool_calls.loop_id(self.run_id, self.minted)

    def _switched_to(self, target: wire.Target) -> bool:
        primary = self.resolved.chain.primary if self.resolved.chain is not None else None
        return (not self.fallen and primary is not None
                and (target.provider_id, target.model) != (primary.provider_id, primary.model))

    def _note_switch(self, target: wire.Target | None) -> None:
        """Once the fallback has served, it serves alone (spec 3.11 rule 1),
        and every id in the history is the loop's own from then on (rule 2)."""
        if target is None or not self._switched_to(target):
            return
        self.fallen = True
        self.appended = tool_calls.rewrite_ids(self.appended, self._mint)
        self._hist = None

    def _outgoing(self, extra: dict | None) -> list[dict]:
        history = self._history()
        if extra is None:
            return history
        if isinstance(history, model_guidance.PreparedMessages):
            return history.with_appended_keeping(extra)
        return [*history, extra]

    def _history(self) -> list[dict]:
        """The prompt and every turn the loop appended, as one prompt: a
        `PreparedMessages` extended message by message so every variant gets
        the same tail (`with_appended_keeping`), cached between turns."""
        if self._hist is None:
            self._hist, self._hist_len = self.base, 0
        for message in self.appended[self._hist_len:]:
            self._hist = (self._hist.with_appended_keeping(message)
                          if isinstance(self._hist, model_guidance.PreparedMessages)
                          else [*self._hist, message])
        self._hist_len = len(self.appended)
        return self._hist

    def _extend(self, messages: list[dict]) -> None:
        self.appended.extend(messages)

    def _append(self, turn: _Turn, results: list[dict]) -> None:
        said: dict = {"role": "assistant", "content": turn.text,
                      "tool_calls": [{"id": c.id, "name": c.name,
                                      "arguments": c.arguments or {}} for c in turn.calls]}
        if turn.opaque is not None:
            said["_opaque"] = turn.opaque
        self._extend([said, *results])
        if turn.target is not None:
            self.writers.add((turn.target.provider_id, turn.target.model))

    async def _captured(self, sending: list[dict], turn: _Turn) -> None:
        """The caller's `TurnCapture`, guarded: a capture never fails a turn."""
        if self.capture is None:
            return
        try:
            shown = [{"role": "system", "content": json.dumps(list(self.defs))}, *list(sending)]
            outcome = {"finish": turn.finish, "text_chars": len(turn.text),
                       "calls": [{"name": c.name, "arguments": c.arguments} for c in turn.calls]}
            target = turn.target or (self.resolved.chain.primary
                                     if self.resolved.chain is not None else None)
            if target is not None:
                await self.capture(shown, outcome, target)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            _contracts.log.warning("could not capture tool turn %d of run %s: %s", turn.turn,
                        self.run_id, type(exc).__name__)

    async def _captured_failure(self, sending: list[dict], exc: LLMError) -> None:
        """A turn that failed, for the caller's `TurnCapture`: the request as
        it would have gone, the attempt that failed last (a fallback's
        included; the primary when none was stamped), and the failure's kind,
        status and code -- never the provider's words, which can echo the
        input or a key. Guarded like `_captured`."""
        if self.capture is None or self.resolved.chain is None:
            return
        try:
            shown = [{"role": "system", "content": json.dumps(list(self.defs))}, *list(sending)]
            outcome: dict = {"finish": "error", "error_kind": exc.kind,
                             "error_status": exc.status}
            if exc.code:
                outcome["code"] = exc.code
            attempted = (self._holder or {}).get(llm.ATTEMPTED)
            target = (attempted if isinstance(attempted, wire.Target)
                      else self.resolved.chain.primary)
            await self.capture(shown, outcome, target)
        except Exception as error:  # noqa: BLE001 - see `_captured`
            _contracts.log.warning("could not capture failed tool turn %d of run %s: %s", self.turns,
                        self.run_id, type(error).__name__)

    # ---- one tool call ----
    def _problem(self, call: tool_calls.ToolCall, spec: tool_calls.ToolSpec | None) -> str:
        if spec is None:
            return f"there is no tool named {call.name!r}"
        if call.arguments is None:
            return "your arguments were not a JSON object"
        return tool_calls.violation(call.arguments, spec.parameters)

    def _refused_call(self) -> str:
        """The limit that refuses the next execution (spec 3.9), or ""."""
        if self.limit:
            return self.limit
        if self.calls_used + 1 > self.budget.max_tool_calls:
            return "tool_calls"
        if self._wall_short(0):
            return "wall"
        return ""

    async def _one(self, turn: _Turn, call: tool_calls.ToolCall) -> dict:
        """One call answered: run, refused or failed, every call id gets a
        result (every provider requires one)."""
        self._check_cancelled()
        refused = self._refused_call()
        if refused:
            self.limit = refused
            self._trace(turn.turn, "tool", call.name, call.id, ok=False, note="not_run")
            return self._result_message(call, tool_calls.NOT_RUN, error=True)
        self.calls_used += 1
        spec = self.toolset.get(call.name)
        problem = (tool_calls.CUT_OFF if turn.finish == "length"
                   else self._problem(call, spec))
        if problem or spec is None:
            self._trace(turn.turn, "tool", call.name, call.id, ok=False, note="refused")
            return self._result_message(call, problem, error=True)
        return await self._executed(turn.turn, call, spec)

    async def _executed(self, k: int, call: tool_calls.ToolCall,
                        spec: tool_calls.ToolSpec) -> dict:
        self.emit(tool_calls.LoopEvent("tool_start", turn=k, call_id=call.id, name=call.name))
        started = time.monotonic()
        output, ok = await self._run_tool(call, spec)
        room = self.budget.max_result_chars_total - self.result_chars
        text = tool_calls.truncate(output.text, min(spec.max_result_chars, room))
        if room < spec.max_result_chars and len(output.text) > room:
            self.limit = self.limit or "result_chars"
        self.result_chars += len(text)
        if ok and spec.effect == "propose" and isinstance(output.proposal, dict):
            self.proposals.append(output.proposal)
        self._trace(k, "tool", call.name, call.id, ok=ok, refs=output.refs, chars=len(text),
                    started=started)
        self.emit(tool_calls.LoopEvent("tool_end", turn=k, call_id=call.id, name=call.name,
                                       ok=ok, chars=len(text), refs=output.refs))
        return self._result_message(call, text, error=not ok)

    async def _run_tool(self, call: tool_calls.ToolCall, spec: tool_calls.ToolSpec
                        ) -> tuple[tool_calls.ToolOutput, bool]:
        """The caller's executor, bounded: a wait, never a kill (spec 3.6). A
        `ToolError` reaches the model as itself; anything else as a generic
        failure, logged by type only (a message can quote record text)."""
        end = self.t0 + self.wall if self.wall > 0 else float("inf")
        reserve = (tool_runs.MIN_TURN_SECONDS
                   if self.wall > 0 and self.budget.reserve_final and not self.finalized
                   else 0.0)
        ctx = tool_calls.ToolContext(campaign=self.attribution["campaign"],
                                     scene_identity=self.scene_identity, run_id=self.run_id,
                                     deadline=end, root=self.root, turn=self.turns,
                                     run=_RunView(self), call_budget=self.call_budget,
                                     reserve=reserve, call_id=call.id)
        seconds: float | None = min(spec.timeout, end - time.monotonic())
        if seconds == math.inf:
            # No wait on either side: the decide tool, in a run with no wall,
            # which bounds each of its calls by `ctx.call_budget` itself.
            seconds = None

        async def marked() -> tool_calls.ToolOutput:
            # The wait runs the executor in a task of its own: marked as a
            # loop's, so a tool cannot start a loop inside this one.
            me = asyncio.current_task()
            if me is not None:
                _tool_policy._LOOPING.add(me)
            return await self.execute(call, ctx)

        try:
            output = await deadline.bounded(marked(), seconds,
                                            lambda _s: _tool_policy._ToolTimeoutError())
        except _tool_policy._ToolTimeoutError:
            return tool_calls.ToolOutput(tool_calls.TIMED_OUT), False
        except tool_calls.ToolError as exc:
            return tool_calls.ToolOutput(str(exc) or tool_calls.TOOL_FAILED), False
        except Exception as exc:  # noqa: BLE001 - a tool's failure is the model's to hear
            _contracts.log.error("tool %s failed in run %s: %s", call.name, self.run_id,
                      type(exc).__name__)
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        if not isinstance(output, tool_calls.ToolOutput) or not isinstance(output.text, str):
            return tool_calls.ToolOutput(tool_calls.TOOL_FAILED), False
        return output, True

    @staticmethod
    def _result_message(call: tool_calls.ToolCall, text: str, *, error: bool) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name,
                "content": text, "is_error": error}

    # ---- bookkeeping ----
    def _trace(self, turn: int, kind: Literal["model", "tool", "decide", "stop"],
               name: str = "", call_id: str = "", *, ok: bool = True,
               refs: tuple[str, ...] = (), chars: int = 0, note: str = "",
               started: float | None = None) -> None:
        elapsed = int((time.monotonic() - started) * 1000) if started is not None else 0
        self.trace.append(tool_calls.TraceEntry(turn, kind, name, call_id, ok, tuple(refs),
                                                chars, elapsed, note))

    def _result(self, status: Literal["completed", "budget_exhausted", "failed"], *,
                final: dict | None = None, final_call: tool_calls.ToolCall | None = None,
                error: Exception | None = None,
                declined: tuple[tool_calls.ToolCall, ...] = ()) -> tool_calls.LoopResult:
        limit = self.limit if status == "budget_exhausted" else ""
        self._trace(self.turns, "stop", limit, note=status)
        return tool_calls.LoopResult(
            status=status, limit=limit, text=self.text, final=final, final_call=final_call,
            declined=declined or self.final_declined,
            proposals=tuple(self.proposals), messages=tuple(self.appended),
            trace=tuple(self.trace), rows=tuple(self.rows), error=error, run_id=self.run_id)


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
