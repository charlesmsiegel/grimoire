"""Streaming the tool loop (01g-S6; 01g-C6, spec 3.8 and §6's 01g-C6 tests).

`inference.stream_tools` sends every turn through `client.stream` and yields
its deltas as `text` events, in the order `text* turn_end (tool_start
tool_end)* ... done`; heartbeats come between turns and during tools; a
caller's reasoning buffer rides every turn; visible text is never cut by the
wall; and with `decline_after_text` a call after visible text is declined.
"""

from __future__ import annotations

import asyncio

import pytest

import grimoire.store as store
from grimoire import inference, llm, llm_reasoning, tool_calls
from grimoire.tool_calls import RunBudget, ToolOutput, Toolset, ToolSpec
from tests import wire_kit
from tests.llm_fakes import FakeLLM, FakeToolTurns, ToolTurn

RUN = "runstream0001"
TARGET = wire_kit.target(provider_id="openrouter", model="vendor/active", api_key="k")
PROMPT = [{"role": "user", "content": "Who keeps the Saltmarch ledger?"}]
NONE = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
ran: list[str] = []


def _read(args, ctx):
    ran.append("read")
    return ToolOutput(text="Mara keeps it.")


async def _slow(args, ctx):
    await asyncio.sleep(0.3)
    return ToolOutput(text="late")


TOOLS = Toolset((ToolSpec("read", "Reads one record.", NONE, _read),
                 ToolSpec("slow", "Takes its time.", NONE, _slow)))


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    ran.clear()


def _events(fake, **kw) -> list[tool_calls.LoopEvent]:
    async def go():
        return [event async for event in inference.stream_tools(
            "chat", PROMPT, toolset=TOOLS, execute=tool_calls.registered(TOOLS),
            client=fake, resolved=wire_kit.resolution(TARGET),
            budget=kw.pop("budget", RunBudget()), run_id=RUN, **kw)]
    return asyncio.run(go())


def test_events_come_in_order_with_interstitial_set_on_tool_turns():
    fake = FakeToolTurns(ToolTurn("", (("read", {}),)),
                         ToolTurn(deltas=("Mara ", "keeps it.")))
    events = [e for e in _events(fake) if not (e.kind == "text" and e.delta == "")]
    assert [(e.kind, e.interstitial) for e in events] == [
        ("turn_end", True), ("tool_start", False), ("tool_end", False),
        ("text", False), ("text", False), ("turn_end", False), ("done", False)]
    assert [e.delta for e in events if e.kind == "text"] == ["Mara ", "keeps it."]
    assert events[-1].result.text == "Mara keeps it." and fake.calls == 2


def test_a_call_after_visible_text_is_declined_with_no_further_turn():
    fake = FakeToolTurns(ToolTurn("Let me check.", (("read", {}),)), ("never", []))
    result = _events(fake, decline_after_text=True)[-1].result
    assert result.status == "completed" and result.text == "Let me check."
    assert [c.name for c in result.declined] == ["read"]
    assert fake.calls == 1 and ran == []
    assert ("read", "declined: after_text") in [(e.name, e.note) for e in result.trace]


def test_a_call_before_any_text_runs_and_the_next_turn_streams():
    fake = FakeToolTurns(("", [("read", {})]), ToolTurn(deltas=("Mara.",)))
    events = _events(fake, decline_after_text=True)
    assert events[-1].result.status == "completed" and ran == ["read"]
    assert [e.delta for e in events if e.kind == "text" and e.delta] == ["Mara."]


def test_text_deltas_are_what_generate_streams():
    deltas = ("", "Ma", "", "ra.")

    async def looped():
        fake = FakeToolTurns(ToolTurn(deltas=deltas))
        return [d async for d in tool_calls.text_deltas(inference.stream_tools(
            "chat", PROMPT, toolset=TOOLS, execute=tool_calls.registered(TOOLS),
            client=fake, resolved=wire_kit.resolution(TARGET), budget=RunBudget(),
            run_id=RUN))]

    async def generated():
        return [d async for d in inference.generate(
            "chat", PROMPT, client=FakeLLM([list(deltas)]),
            resolved=wire_kit.resolution(TARGET), usage={})]

    assert asyncio.run(looped()) == asyncio.run(generated()) == list(deltas)


def test_text_deltas_hands_on_the_result():
    seen = []

    async def go():
        fake = FakeToolTurns(("Mara.", []))
        events = inference.stream_tools(
            "chat", PROMPT, toolset=TOOLS, execute=tool_calls.registered(TOOLS),
            client=fake, resolved=wire_kit.resolution(TARGET), budget=RunBudget(),
            run_id=RUN)
        return [d async for d in tool_calls.text_deltas(events, seen.append)]

    assert asyncio.run(go()) == ["Mara."]
    assert seen[0].status == "completed"


def test_heartbeats_arrive_during_a_slow_tool(monkeypatch):
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0.05)
    events = _events(FakeToolTurns(("", [("slow", {})]), ("done", [])))
    kinds = [(e.kind, e.delta) for e in events]
    start = kinds.index(("tool_start", ""))
    end = kinds.index(("tool_end", ""))
    assert ("text", "") in kinds[start:end]


def test_the_reasoning_buffer_rides_every_turn():
    class Recording(llm_reasoning.Buffer):
        def __init__(self):
            super().__init__()
            self.seen: list[str] = []
            self.resets = 0

        def begin(self):
            self.seen.extend(self.chunks)
            self.resets += 1
            super().begin()

    buffer = Recording()
    fake = FakeToolTurns(ToolTurn("", (("read", {}),), thinking="Mara would know. "),
                         ToolTurn("Mara.", (), thinking="She does."))
    _events(fake, reasoning=buffer)
    assert "".join(buffer.seen + buffer.chunks) == "Mara would know. She does."
    assert buffer.resets == 2


def test_the_wall_never_cuts_a_turn_that_has_shown_text(monkeypatch):
    monkeypatch.setattr(tool_calls, "MIN_TURN_SECONDS", 0.01)
    fake = FakeToolTurns(ToolTurn(deltas=("Mara ", "keeps it."), pause=0.4))
    result = _events(fake, budget=RunBudget(wall_seconds=0.2))[-1].result
    assert result.status == "completed" and result.text == "Mara keeps it."


def test_before_visible_text_the_turn_is_bounded(monkeypatch):
    monkeypatch.setattr(tool_calls, "MIN_TURN_SECONDS", 0.01)
    fake = FakeToolTurns(("", [("read", {})]),
                         ToolTurn(deltas=("", "", "late"), pause=0.4))
    result = _events(fake, budget=RunBudget(wall_seconds=0.2, reserve_final=False))[-1].result
    assert (result.status, result.limit) == ("budget_exhausted", "wall")
    assert [r["status"] for r in result.rows] == ["ok", "aborted"]


def test_entry_checks_are_raised_before_an_iterator_is_returned():
    with pytest.raises(ValueError):
        inference.stream_tools("chat", PROMPT, toolset=TOOLS,
                               execute=tool_calls.registered(TOOLS),
                               client=FakeToolTurns(("ok", [])),
                               resolved=wire_kit.resolution(TARGET), budget=RunBudget(),
                               run_id="")


def test_closing_the_stream_stops_the_run():
    fake = FakeToolTurns(ToolTurn("", (("slow", {}),)), ("done", []))

    async def go():
        events = inference.stream_tools(
            "chat", PROMPT, toolset=TOOLS, execute=tool_calls.registered(TOOLS),
            client=fake, resolved=wire_kit.resolution(TARGET), budget=RunBudget(),
            run_id=RUN)
        async for event in events:
            if event.kind == "tool_start":
                break
        await events.aclose()
        await asyncio.sleep(0.5)

    asyncio.run(go())
    assert fake.calls == 1
