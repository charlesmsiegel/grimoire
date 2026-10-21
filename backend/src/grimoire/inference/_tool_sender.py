"""One model turn of a tool run through the facade: metered, bounded, captured."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing

from .. import deadline, llm, llm_reasoning, store, tool_calls, wire
from ..llm_errors import LLMError
from . import _contracts, _requests, _tool_policy
from ._tool_guard import _SendGuard
from ._tool_state import _RunState, _Turn


class _TurnSender:
    """Each model turn of one run (01g-C3, C6): sent through the facade under
    a meter of its own, joined or streamed, bounded by the run's clock, its
    ledger row kept (`rows`) and noted against the spend guard, and handed to
    the caller's `TurnCapture`. Tracks whether the turn in flight has shown
    text (`shown`) and text the caller's reader would see (`visible`)."""

    def __init__(self, state: _RunState, guard: _SendGuard, defs: tuple[dict, ...], *,
                 streaming: bool):
        self.state = state
        self.guard = guard
        self.defs = defs
        #: 01g-C6: each turn through `client.stream`.
        self.streaming = streaming
        self.rows: list[dict] = []
        self.shown = False
        self.visible = False
        self._sees: Callable[[str], bool] | None = None
        #: The latest turn's usage holder, which the facade stamps with the
        #: attempt that went out (`llm.ATTEMPTED`): what a failed turn's
        #: capture names, a fallback's included.
        self._holder: dict | None = None

    def begin(self) -> None:
        """A new turn: nothing shown yet, and the caller's fresh predicate
        for what its reader sees (02 §8.2)."""
        visibility = self.state.options.visibility
        self.shown = False
        self.visible = False
        self._sees = visibility() if visibility is not None else None

    def joined(self, text: str) -> None:
        """A joined turn's text, shown whole once it settles, so it is
        visible text too."""
        if text and not self.streaming:
            self.shown = True
            self.visible = self.visible or self._seen(text)

    async def metered(self, k: int, chain: wire.Chain, sending: list[dict], choice: str,
                      collector: tool_calls.Collector, final: bool,
                      projection: float | None = None) -> tuple[str, dict]:
        """One turn through the facade, under its own meter carrying the run
        id and the turn (01g-C3), bounded by `turn_seconds`."""
        options, client = self.state.options, self.state.options.client
        schema = options.final_schema if final else None
        seconds, by_wall = self.state.clock.turn_seconds()
        meter = store.usage.meter(self.state.task, **options.attribution(),
                                  run_id=options.run_id, loop_turn=k)
        try:
            with meter as m:
                m.usage[tool_calls.KEY] = collector
                self._holder = m.usage
                if options.holders is not None:
                    options.holders.append(m.usage)

                def overrun(limit: float) -> LLMError:
                    if by_wall:
                        return _tool_policy.WallSpentError("timeout", "the run's wall clock ran out")
                    error = LLMError("timeout", f"the reply did not finish within "
                                                f"{limit:g}s — giving up")
                    self._note_overrun(m.usage, chain, error)
                    return error

                async def resend(again: wire.Chain) -> str:
                    with _requests._counting_earlier(m.usage):
                        return await client.complete(sending, again, m.usage, schema=schema,
                                                     tools=self.defs, tool_choice=choice)

                async def reopen(again: wire.Chain) -> AsyncGenerator[str, None]:
                    with _requests._counting_earlier(m.usage):
                        async with aclosing(client.stream(
                                sending, again, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice)) as deltas:
                            async for delta in deltas:
                                yield delta

                if options.reasoning is not None:
                    m.usage[llm_reasoning.KEY] = options.reasoning
                try:
                    if self.streaming:
                        source = (client.stream(sending, chain, m.usage, tools=self.defs,
                                                tool_choice=choice) if schema is None
                                  else _requests._streamed_structured(client.stream(
                                      sending, chain, m.usage, schema=schema, tools=self.defs,
                                      tool_choice=choice), reopen))
                        text = await self._streamed(k, source, seconds, overrun)
                    else:
                        work: Awaitable[str] = (
                            client.complete(sending, chain, m.usage, tools=self.defs,
                                            tool_choice=choice) if schema is None
                            else _requests._joined_structured(client.complete(
                                sending, chain, m.usage, schema=schema, tools=self.defs,
                                tool_choice=choice), resend))
                        text = await deadline.bounded(work, seconds, overrun)
                finally:
                    m.tool_calls = len(collector.calls())
        finally:
            if meter.row is not None:
                self.rows.append(meter.row)
            self.guard.note(projection, meter.row)
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
                self.state.emit(tool_calls.LoopEvent("text", turn=k, delta=delta))
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
            self.state.options.client.note_outcome(
                attempted if isinstance(attempted, wire.Target) else chain.primary, error)
        except Exception as exc:  # noqa: BLE001 - bookkeeping on the failure path
            _contracts.log.warning("could not file a tool turn's timeout: %s", type(exc).__name__)

    async def captured(self, sending: list[dict], turn: _Turn,
                       primary: wire.Target | None) -> None:
        """The caller's `TurnCapture`, guarded: a capture never fails a turn."""
        capture = self.state.options.capture
        if capture is None:
            return
        try:
            shown = [{"role": "system", "content": json.dumps(list(self.defs))}, *list(sending)]
            outcome = {"finish": turn.finish, "text_chars": len(turn.text),
                       "calls": [{"name": c.name, "arguments": c.arguments} for c in turn.calls]}
            target = turn.target or primary
            if target is not None:
                await capture(shown, outcome, target)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            _contracts.log.warning("could not capture tool turn %d of run %s: %s", turn.turn,
                        self.state.options.run_id, type(exc).__name__)

    async def captured_failure(self, sending: list[dict], exc: LLMError,
                               primary: wire.Target | None) -> None:
        """A turn that failed, for the caller's `TurnCapture`: the request as
        it would have gone, the attempt that failed last (a fallback's
        included; the primary when none was stamped), and the failure's kind,
        status and code -- never the provider's words, which can echo the
        input or a key. Guarded like `captured`."""
        capture = self.state.options.capture
        if capture is None or primary is None:
            return
        try:
            shown = [{"role": "system", "content": json.dumps(list(self.defs))}, *list(sending)]
            outcome: dict = {"finish": "error", "error_kind": exc.kind,
                             "error_status": exc.status}
            if exc.code:
                outcome["code"] = exc.code
            attempted = (self._holder or {}).get(llm.ATTEMPTED)
            target = attempted if isinstance(attempted, wire.Target) else primary
            await capture(shown, outcome, target)
        except Exception as error:  # noqa: BLE001 - see `captured`
            _contracts.log.warning("could not capture failed tool turn %d of run %s: %s",
                        self.state.turns, self.state.options.run_id, type(error).__name__)
