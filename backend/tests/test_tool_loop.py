"""`inference.run_tools`, the bounded tool loop (01g-S4; spec 3.6-3.11, §6
"The loop" and its gate additions, less spend and streaming).

The model is `llm_fakes.FakeToolTurns`, scripted by call order; the tools are
written here, read-only, and run through the caller's executor -- the loop's
own `tool_calls.registered` or a recording one. No route calls the loop yet,
so the route a test runs it on is opted in by `routing.TOOLS_OPTIONAL`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import replace

import pytest

import grimoire.store as store
from grimoire import deadline, inference, model_guidance, schemas, tool_calls, wire
from grimoire.llm_errors import LLMError
from grimoire.store.inference.capabilities import Cap
from grimoire.tool_calls import RunBudget, ToolOutput, Toolset, ToolSpec
from tests import frozen_copy, wire_kit
from tests.llm_fakes import FakeToolTurns, ToolTurn

RUN = "run0123456789abcdef"
PRIMARY = wire_kit.target(provider_id="openrouter", model="vendor/active", api_key="sk-test")
SPARE = wire_kit.target(provider_id="spare", kind="anthropic", model="claude-spare",
                        api_key="sk-spare")
PROMPT = [{"role": "system", "content": "You are the archivist of Saltmarch."},
          {"role": "user", "content": "Who keeps the harbour ledger?"}]

QUERY = {"type": "object", "properties": {"query": {"type": "string"}},
         "required": ["query"], "additionalProperties": False}
NONE = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
ANSWER = {"type": "object", "properties": {"keeper": {"type": "string"}},
          "required": ["keeper"], "additionalProperties": False}


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    route = store.routing.route("chat")
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL", frozenset({route.key}))


def _search(args, ctx):
    return ToolOutput(text=f"Mara keeps the {args['query']}.", refs=("characters:mara",))


def _read(args, ctx):
    return ToolOutput(text="Mara has kept it since the flood.", refs=("lore:flood",))


def _toolset(*extra: ToolSpec) -> Toolset:
    return Toolset((ToolSpec("search", "Searches the library.", QUERY, _search),
                    ToolSpec("read", "Reads one record.", NONE, _read), *extra))


def _resolved(chain: wire.Chain | wire.Target = PRIMARY, **caps: str):
    resolved = wire_kit.resolution(chain)
    if caps:
        resolved = replace(resolved, attempts=tuple(
            replace(a, capabilities={k: Cap(v, "catalog") for k, v in caps.items()})
            for a in resolved.attempts))
    return resolved


def _run(fake, *, toolset=None, execute=None, budget=None, resolved=None, **kw):
    toolset = toolset or _toolset()
    return asyncio.run(inference.run_tools(
        "chat", kw.pop("messages", PROMPT), toolset=toolset,
        execute=execute or tool_calls.registered(toolset), client=fake,
        resolved=resolved or _resolved(), budget=budget or RunBudget(), run_id=RUN,
        campaign="saltmarch", **kw))


# ---- the happy path ----
def test_search_then_read_then_answer():
    fake = FakeToolTurns(("", [("search", {"query": "ledger"})]),
                         ("Let me read it.", [("read", {})]),
                         ("Mara keeps it.", []))
    result = _run(fake)
    assert result.status == "completed" and result.text == "Mara keeps it."
    assert [(r["run_id"], r["loop_turn"]) for r in result.rows] == [(RUN, 1), (RUN, 2), (RUN, 3)]
    assert [r.get("tool_calls") for r in result.rows] == [1, 1, None]
    assert all(r["campaign"] == "saltmarch" for r in result.rows)
    assert [e.kind for e in result.trace] == ["model", "tool", "model", "tool", "model", "stop"]
    assert result.trace[1].refs == ("characters:mara",) and result.trace[1].chars > 0
    # Each turn offered the definitions and was capped at the budget's output.
    first = fake.requests[0]
    assert first["tools"] == _toolset().definitions() and first["tool_choice"] == "auto"
    assert first["chain"].primary.sampling.call_cap == RunBudget().max_output_tokens
    # Turn 3 was sent the whole history, in the neutral shapes.
    sent = fake.requests[2]["messages"]
    assert sent[2] == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_0_0", "name": "search", "arguments": {"query": "ledger"}}]}
    assert sent[3] == {"role": "tool", "tool_call_id": "call_0_0", "name": "search",
                       "content": "Mara keeps the ledger.", "is_error": False}
    assert result.messages == tuple(sent[2:])
    assert result.run_id == RUN


def test_the_caller_executes_every_tool():
    seen = []

    async def execute(call, ctx):
        seen.append((call.name, call.arguments, ctx.run_id, ctx.campaign))
        return ToolOutput(text="done")

    _run(FakeToolTurns(("", [("search", {"query": "x"}), ("read", {})]), ("ok", [])),
         execute=execute)
    assert seen == [("search", {"query": "x"}, RUN, "saltmarch"), ("read", {}, RUN, "saltmarch")]


def test_a_capture_sees_each_turn_with_its_definitions():
    captured = []

    async def capture(messages, outcome, target):
        captured.append((messages, outcome, target.model))

    _run(FakeToolTurns(("", [("read", {})]), ("ok", [])), capture=capture)
    (first, second) = captured
    assert json.loads(first[0][0]["content"]) == list(_toolset().definitions())
    assert first[1] == {"finish": "tool_calls", "text_chars": 0,
                        "calls": [{"name": "read", "arguments": {}}]}
    assert second[1]["calls"] == [] and first[2] == "vendor/active"


def test_a_failing_capture_costs_only_itself():
    async def capture(messages, outcome, target):
        raise RuntimeError("the prompt log is unwell")

    assert _run(FakeToolTurns(("ok", [])), capture=capture).status == "completed"


# ---- entry refusals ----
@pytest.mark.parametrize("change", [
    {"run_id": ""},
    {"toolset": Toolset(())},
    {"final_schema": {"type": "object", "properties": {}}},
    {"messages": [*PROMPT, {"role": "assistant", "content": "Mara"}]},
    {"tool_choice": "none"},
])
def test_refused_before_anything_is_sent(change):
    fake = FakeToolTurns(("ok", []))
    args = {"toolset": _toolset(), "run_id": RUN, "messages": PROMPT, **change}
    with pytest.raises(ValueError):
        asyncio.run(inference.run_tools(
            "chat", args.pop("messages"), execute=tool_calls.registered(_toolset()),
            client=fake, resolved=_resolved(), budget=RunBudget(), **args))
    assert fake.calls == 0
    assert list(store.usage.calls(days=1)) == []


def test_an_unfrozen_prepared_prompt_is_refused_at_entry():
    prepared = model_guidance.PreparedMessages("vendor/active", lambda model: (PROMPT, None))
    fake = FakeToolTurns(("ok", []))
    with pytest.raises(ValueError, match="frozen"):
        _run(fake, messages=prepared)
    assert fake.calls == 0


def test_a_frozen_prepared_prompt_is_extended_turn_by_turn():
    profiles = {"": (PROMPT, None)}
    prepared = model_guidance.PreparedMessages("vendor/active", lambda model: (PROMPT, None),
                                               profiles=profiles)
    prepared.settings = {"length": "short"}
    fake = FakeToolTurns(("", [("read", {})]), ("ok", []))
    assert _run(fake, messages=prepared).status == "completed"
    assert len(fake.requests[1]["messages"]) == len(PROMPT) + 2


def test_a_route_that_neither_requires_nor_offers_tools_is_refused(monkeypatch):
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL", frozenset())
    with pytest.raises(ValueError, match="neither requires tools"):
        _run(FakeToolTurns(("ok", [])))


def test_an_optional_route_runs_on_unknown_and_refuses_a_known_no():
    assert _run(FakeToolTurns(("ok", [])),
                resolved=_resolved(tools="unknown")).status == "completed"
    fake = FakeToolTurns(("ok", []))
    with pytest.raises(ValueError, match="known unable"):
        _run(fake, resolved=_resolved(tools="no"))
    assert fake.calls == 0


def test_a_resolution_of_another_task_is_refused():
    with pytest.raises(ValueError):
        asyncio.run(inference.run_tools(
            "chat", PROMPT, toolset=_toolset(), execute=tool_calls.registered(_toolset()),
            client=FakeToolTurns(("ok", [])), resolved=wire_kit.resolution(PRIMARY, "tagline"),
            budget=RunBudget(), run_id=RUN))


def test_a_loop_does_not_start_inside_another():
    inner_error = []

    async def execute(call, ctx):
        try:
            await inference.run_tools(
                "chat", PROMPT, toolset=_toolset(), execute=tool_calls.registered(_toolset()),
                client=FakeToolTurns(("ok", [])), resolved=_resolved(), budget=RunBudget(),
                run_id="inner")
        except ValueError as exc:
            inner_error.append(str(exc))
        return ToolOutput(text="ok")

    _run(FakeToolTurns(("", [("read", {})]), ("done", [])), execute=execute)
    assert inner_error == ["a tool loop cannot start inside another"]


# ---- terminal calls, validation, errors ----
FINISH = ToolSpec("finish", "Hands in the answer.", ANSWER, _read, terminal=True)


def test_a_terminal_call_ends_the_run_unexecuted():
    ran = []

    async def execute(call, ctx):
        ran.append(call.name)
        return ToolOutput(text="x")

    result = _run(FakeToolTurns(("", [("finish", {"keeper": "Mara"}), ("read", {})])),
                  toolset=_toolset(FINISH), execute=execute)
    assert result.status == "completed" and ran == []
    assert result.final_call.name == "finish" and result.final_call.arguments == {"keeper": "Mara"}
    assert ("read", "not_run") in [(e.name, e.note) for e in result.trace]


def test_a_non_conforming_terminal_call_is_an_error_result():
    fake = FakeToolTurns(("", [("finish", {"keeper": 3})]),
                         ("", [("finish", {"keeper": "Mara"})]))
    result = _run(fake, toolset=_toolset(FINISH))
    assert result.final_call.arguments == {"keeper": "Mara"}
    error = fake.requests[1]["messages"][-1]
    assert error["is_error"] and "keeper" in error["content"]


@pytest.mark.parametrize("call, needle", [
    (("nope", {}), "no tool named 'nope'"),
    (("search", {"query": 3}), "arguments.query: expected a string"),
    (("search", "{not json"), "not a JSON object"),
])
def test_bad_calls_get_error_results_and_count(call, needle):
    fake = FakeToolTurns(("", [call]), ("ok", []))
    result = _run(fake)
    reply = fake.requests[1]["messages"][-1]
    assert reply["is_error"] and needle in reply["content"]
    assert result.status == "completed"


def test_a_tool_error_reaches_the_model_and_an_exception_does_not(caplog):
    def broken(args, ctx):
        raise RuntimeError("Seraphine's secret diary text")

    def refusing(args, ctx):
        raise tool_calls.ToolError("that record is sealed")

    toolset = Toolset((ToolSpec("broken", "b", NONE, broken), ToolSpec("refusing", "r", NONE,
                                                                         refusing)))
    fake = FakeToolTurns(("", [("broken", {}), ("refusing", {})]), ("ok", []))
    with caplog.at_level(logging.ERROR, logger="grimoire.inference"):
        _run(fake, toolset=toolset)
    broken_reply, refusing_reply = fake.requests[1]["messages"][-2:]
    assert broken_reply["content"] == tool_calls.TOOL_FAILED
    assert refusing_reply["content"] == "that record is sealed"
    assert "RuntimeError" in caplog.text and "diary" not in caplog.text


def test_arguments_cut_off_by_the_length_are_answered_so():
    fake = FakeToolTurns(ToolTurn("", (("search", '{"query": "led'),), finish="length"),
                         ("ok", []))
    _run(fake)
    assert fake.requests[1]["messages"][-1]["content"] == tool_calls.CUT_OFF


def test_a_turn_one_failure_raises_and_a_later_one_is_failed():
    with pytest.raises(LLMError):
        _run(FakeToolTurns(LLMError("network", "down")))
    fake = FakeToolTurns(("", [("read", {})]), ("", [("read", {})]),
                         LLMError("rate_limit", "slow down"))
    result = _run(fake)
    assert result.status == "failed" and result.error.kind == "rate_limit"
    assert [r["status"] for r in result.rows] == ["ok", "ok", "error"]


def test_cancelled_stops_before_the_next_send():
    asked = []

    def cancelled():
        asked.append(1)
        return len(asked) > 2

    fake = FakeToolTurns(("", [("read", {})]), ("ok", []))
    with pytest.raises(asyncio.CancelledError):
        _run(fake, cancelled=cancelled)
    assert fake.calls == 1


# ---- the final record ----
def test_a_final_schema_reads_a_conforming_answer():
    result = _run(FakeToolTurns(('{"keeper": "Mara"}', [])), final_schema=ANSWER)
    assert result.status == "completed" and result.final == {"keeper": "Mara"}


def test_a_non_conforming_answer_gets_one_finalize_turn():
    fake = FakeToolTurns(("Mara, I think.", []), ('{"keeper": "Mara"}', []))
    result = _run(fake, final_schema=ANSWER)
    assert result.final == {"keeper": "Mara"} and result.status == "completed"
    final = fake.requests[1]
    assert final["tool_choice"] == "none" and final["tools"] == _toolset().definitions()
    assert final["schema"] == ANSWER
    assert schemas.render(ANSWER) in final["messages"][-1]["content"]


def test_a_still_unreadable_answer_fails_final_unreadable():
    result = _run(FakeToolTurns(("Mara, I think.", [])), final_schema=ANSWER)
    assert result.status == "failed" and result.error.code == "final_unreadable"
    assert result.text == "Mara, I think."


# ---- budget limits ----
def test_the_turn_limit_runs_the_reserved_finalize_turn():
    fake = FakeToolTurns(("", [("read", {})]), ("", [("read", {})]), ("Mara.", []))
    result = _run(fake, budget=RunBudget(max_turns=3))
    assert result.status == "budget_exhausted" and result.limit == "turns"
    assert fake.calls == 3 and fake.requests[2]["tool_choice"] == "none"
    assert result.text == "Mara." and result.trace[-1].name == "turns"


def test_the_tool_call_limit_answers_every_call_and_finalizes():
    fake = FakeToolTurns(("", [("read", {}), ("read", {}), ("read", {})]), ("Mara.", []))
    result = _run(fake, budget=RunBudget(max_tool_calls=2))
    assert result.limit == "tool_calls" and result.status == "budget_exhausted"
    replies = fake.requests[1]["messages"][-4:-1]      # the finalize note is last
    assert [r["content"] == tool_calls.NOT_RUN for r in replies] == [False, False, True]
    assert fake.requests[1]["tool_choice"] == "none"


def test_without_a_reserved_finalize_a_limit_stops_the_run():
    fake = FakeToolTurns(("", [("read", {})]))
    result = _run(fake, budget=RunBudget(max_turns=1, reserve_final=False))
    assert (result.status, result.limit, fake.calls) == ("budget_exhausted", "turns", 1)


def test_the_result_size_limit():
    def big(args, ctx):
        return ToolOutput(text="x" * 500)

    fake = FakeToolTurns(("", [("big", {})]), ("Mara.", []))
    result = _run(fake, toolset=Toolset((ToolSpec("big", "b", NONE, big),)),
                  budget=RunBudget(max_result_chars_total=100))
    assert result.limit == "result_chars"
    sent = fake.requests[1]["messages"][-2]["content"]
    # The marker fits inside the allowance: the run's total is never passed.
    assert len(sent) == 100 and sent.endswith("\n[truncated: 433 more characters]")
    assert sent == "x" * 67 + "\n[truncated: 433 more characters]"


def test_a_tool_result_is_held_to_its_own_cap_marker_included():
    def big(args, ctx):
        return ToolOutput(text="y" * 500)

    fake = FakeToolTurns(("", [("big", {}), ("big", {})]), ("Mara.", []))
    toolset = Toolset((ToolSpec("big", "b", NONE, big, max_result_chars=60),))
    result = _run(fake, toolset=toolset, budget=RunBudget(max_result_chars_total=150))
    first, second = fake.requests[1]["messages"][-2:]
    assert len(first["content"]) == 60 and first["content"].endswith("more characters]")
    assert len(second["content"]) == 60
    assert sum(e.chars for e in result.trace if e.kind == "tool") <= 150


def test_the_runs_wall_is_a_budget_stop_with_an_aborted_row(monkeypatch):
    monkeypatch.setattr(tool_calls, "MIN_TURN_SECONDS", 0.05)
    fake = FakeToolTurns(ToolTurn("", (("read", {}),)), ToolTurn("late", (), delay=3))
    result = _run(fake, budget=RunBudget(wall_seconds=0.4, reserve_final=False))
    assert result.status == "budget_exhausted" and result.limit == "wall"
    assert [r["status"] for r in result.rows] == ["ok", "aborted"]


def test_no_wall_at_zero():
    fake = FakeToolTurns(("", [("read", {})]), ("ok", []))
    assert _run(fake, budget=RunBudget(wall_seconds=0)).status == "completed"


def test_a_slow_tool_times_out_and_the_executor_is_bounded():
    gate = threading.Event()

    def stuck(args, ctx):
        gate.wait(5)
        return ToolOutput(text="late")

    toolset = Toolset((ToolSpec("stuck", "s", NONE, stuck, timeout=0.2),))
    fake = FakeToolTurns(("", [("stuck", {}), ("stuck", {})]), ("ok", []))
    try:
        _run(fake, toolset=toolset, execute=tool_calls.registered(toolset, workers=1))
    finally:
        gate.set()
    first, second = fake.requests[1]["messages"][-2:]
    assert first["content"] == tool_calls.TIMED_OUT
    assert second["content"] == tool_calls.CAPACITY


def test_a_wall_spent_before_a_tool_starts_never_awaits_it():
    """A wall that runs out between the call budget check and the tool's own
    bound is a timeout at once, never an unbounded wait (`deadline.bounded`
    reads a spent remainder as spent)."""
    started = []

    async def wedged(call, ctx):
        started.append(call.name)
        await asyncio.Event().wait()

    loop = _bare_loop(execute=wedged)
    loop.wall, loop.t0 = 1.0, loop.t0 - 2         # the wall ran out a second ago
    spec = loop.toolset.get("read")
    call = tool_calls.ToolCall("call_1", "read", {}, "{}")

    async def go():
        return await asyncio.wait_for(loop._run_tool(call, spec), 2)
    output, ok = asyncio.run(go())
    assert (output.text, ok, started) == (tool_calls.TIMED_OUT, False, [])


def test_a_spent_wait_is_an_overrun_and_none_is_no_bound():
    async def answer():
        return "ok"

    async def go(seconds):
        return await deadline.bounded(answer(), seconds, TimeoutError)
    assert asyncio.run(go(None)) == "ok"
    for spent in (0, -0.5):
        with pytest.raises(TimeoutError):
            asyncio.run(go(spent))


def test_an_unreadable_answer_after_the_wall_gets_no_repair_turn(monkeypatch):
    """The repair turn is a send like any other: admitted on the wall too,
    not on `max_turns` alone."""
    monkeypatch.setattr(tool_calls, "MIN_TURN_SECONDS", 0.05)
    fake = FakeToolTurns(ToolTurn(deltas=("Mara, ", "I think."), pause=0.4),
                         ('{"keeper": "Mara"}', []))

    async def go():
        return [e async for e in inference.stream_tools(
            "chat", PROMPT, toolset=_toolset(), execute=tool_calls.registered(_toolset()),
            client=fake, resolved=_resolved(), budget=RunBudget(wall_seconds=0.2),
            run_id=RUN, final_schema=ANSWER)]
    result = asyncio.run(go())[-1].result
    assert fake.calls == 1
    assert result.status == "failed" and result.error.code == "final_unreadable"


def test_a_joined_turn_with_text_declines_its_calls():
    """`run_tools` shows a joined turn's text whole as it settles, so with
    `decline_after_text` its calls are declined exactly as a streamed turn's
    are."""
    ran = []

    async def execute(call, ctx):
        ran.append(call.name)
        return ToolOutput(text="done")

    fake = FakeToolTurns(("Let me check the ledger.", [("read", {})]), ("never", []))
    result = _run(fake, execute=execute, decline_after_text=True)
    assert result.status == "completed" and result.text == "Let me check the ledger."
    assert [c.name for c in result.declined] == ["read"]
    assert fake.calls == 1 and ran == []
    # A joined turn with no text still runs its calls.
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []))
    assert _run(fake, execute=execute, decline_after_text=True).status == "completed"
    assert ran == ["read"]


def test_a_zero_decision_cap_stops_the_run_on_decisions():
    """`max_decisions=0` refuses every decide call; reaching the cap -- at
    zero too -- stops the run on `decisions` after that turn (spec 3.9)."""
    def decide(args, ctx):
        if not ctx.run.take_decision():
            raise tool_calls.ToolError("not run: the run's decision budget is spent")
        return ToolOutput(text="truth")

    toolset = _toolset(ToolSpec("decide", "Decides.", NONE, decide))
    fake = FakeToolTurns(("", [("decide", {})]), ("", [("decide", {})]), ("Mara.", []))
    result = _run(fake, toolset=toolset, budget=RunBudget(max_decisions=0))
    assert (result.status, result.limit) == ("budget_exhausted", "decisions")
    assert fake.calls == 2 and fake.requests[1]["tool_choice"] == "none"


def _bare_loop(*, budget=None, execute=None, resolved=None):
    return inference._Loop("chat", PROMPT, toolset=_toolset(), execute=execute, client=None,
                           resolved=resolved or _resolved(), budget=budget or RunBudget(),
                           run_id=RUN,
                           attribution={"campaign": "saltmarch", "scene": "", "post": None,
                                        "round_id": "", "response_id": ""},
                           scene_identity="", cancelled=None, final_schema=None,
                           tool_choice="auto", capture=None)


# ---- fallback across a loop (spec 3.11) ----
def test_once_the_fallback_serves_it_serves_alone_and_ids_are_the_loops():
    fake = FakeToolTurns(("", [("read", {})]),
                         ToolTurn("", (("search", {"query": "ledger"}),), fallback=True),
                         ("Mara.", []))
    result = _run(fake, resolved=_resolved(wire.Chain(PRIMARY, SPARE)))
    assert result.status == "completed"
    third = fake.requests[2]
    assert third["chain"].fallback is None and third["chain"].primary.model == "claude-spare"
    ids = [c["id"] for m in third["messages"] for c in m.get("tool_calls", ())]
    results = [m["tool_call_id"] for m in third["messages"] if m["role"] == "tool"]
    assert ids == results and all(i.startswith("gc_run01234_") for i in ids)
    # It inherits tool turns the primary wrote: thinking off.
    assert third["chain"].primary.sampling.params["reasoning_effort"] == "off"
    assert any("fell back" in e.note for e in result.trace)


def test_a_reused_provider_id_is_renamed_per_occurrence_on_the_switch():
    """A provider that reuses one id across turns still hands the fallback a
    history whose ids are distinct, each result paired with its own call."""
    loop = _bare_loop(resolved=_resolved(wire.Chain(PRIMARY, SPARE)))
    for n in (1, 2):
        loop.appended += [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_0", "name": "read", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "call_0", "name": "read",
             "content": f"page {n}", "is_error": False}]
    loop._note_switch(SPARE)
    ids = [c["id"] for m in loop.appended for c in m.get("tool_calls", ())]
    results = [(m["tool_call_id"], m["content"]) for m in loop.appended
               if m["role"] == "tool"]
    assert len(set(ids)) == 2 and all(i.startswith("gc_run01234_") for i in ids)
    assert results == [(ids[0], "page 1"), (ids[1], "page 2")]


def test_a_degrade_sibling_is_not_a_switch():
    resolved = _resolved(wire.Chain(PRIMARY, SPARE))
    loop = inference._Loop("chat", PROMPT, toolset=_toolset(), execute=None, client=None,
                           resolved=resolved, budget=RunBudget(), run_id=RUN,
                           attribution={"campaign": "", "scene": "", "post": None,
                                        "round_id": "", "response_id": ""},
                           scene_identity="", cancelled=None, final_schema=None,
                           tool_choice="auto", capture=None)
    assert not loop._switched_to(replace(PRIMARY, degrade=True))
    assert loop._switched_to(SPARE)


def test_required_is_sent_as_auto_beside_thinking():
    thinking = wire_kit.target(provider_id="anthropic", kind="anthropic", model="claude-x",
                               api_key="k", model_features={"adaptive_thinking": True})
    fake = FakeToolTurns(("ok", []))
    result = _run(fake, resolved=_resolved(thinking), tool_choice="required")
    assert fake.requests[0]["tool_choice"] == "auto"
    assert "required sent as auto" in result.trace[0].note
    plain = FakeToolTurns(("ok", []))
    _run(plain, tool_choice="required")
    assert plain.requests[0]["tool_choice"] == "required"


def test_opaque_state_rides_the_assistant_turn():
    def opaque_turns():
        fake = FakeToolTurns(("", [("read", {})]), ("ok", []))
        original = fake.complete

        async def complete(messages, conn, usage=None, **kw):
            text = await original(messages, conn, usage, **kw)
            found = usage[tool_calls.KEY]
            found.reasoning_detail({"type": "reasoning.encrypted", "data": "sealed"})
            return text

        fake.complete = complete
        return fake

    fake = opaque_turns()
    _run(fake)
    said = fake.requests[1]["messages"][2]
    assert said["_opaque"]["items"] == [{"type": "reasoning.encrypted", "data": "sealed"}]
    assert said["_opaque"]["provider_id"] == "openrouter"


# ---- a scripted investigation over a copy of the frozen campaign ----
def test_a_three_turn_run_over_a_frozen_copy(monkeypatch, tmp_path):
    home = frozen_copy.copy_home(tmp_path / "frozen")
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    cid = store.campaigns.list_campaigns()[0]["id"]

    def campaign(args, ctx):
        found = store.campaigns.read_campaign(ctx.campaign)
        return ToolOutput(text=str(found.get("name") or found.get("title") or ""),
                          refs=(f"campaigns:{ctx.campaign}",))

    def scenes(args, ctx):
        listed = store.scenes.list_scenes(ctx.campaign)
        return ToolOutput(text=json.dumps([s.get("id") for s in listed]),
                          refs=tuple(f"scenes:{s.get('id')}" for s in listed))

    toolset = Toolset((ToolSpec("campaign", "The campaign.", NONE, campaign),
                       ToolSpec("scenes", "Its scenes.", NONE, scenes)))
    fake = FakeToolTurns(("", [("campaign", {})]), ("", [("scenes", {})]), ("Done.", []))
    result = asyncio.run(inference.run_tools(
        "chat", PROMPT, toolset=toolset, execute=tool_calls.registered(toolset), client=fake,
        resolved=_resolved(), budget=RunBudget(), run_id=RUN, campaign=cid))
    assert result.status == "completed"
    assert [(e.kind, e.name) for e in result.trace if e.kind == "tool"] == [
        ("tool", "campaign"), ("tool", "scenes")]
    assert result.trace[1].refs == (f"campaigns:{cid}",)
    assert all(r.startswith("scenes:") for r in result.trace[3].refs) and result.trace[3].refs


def test_a_claude_chain_refuses_a_name_too_long_for_its_prefix():
    claude = wire_kit.target(provider_id="claude", kind="claude", model="sonnet")
    toolset = Toolset((ToolSpec("r" * 50, "Reads.", NONE, _read),))
    fake = FakeToolTurns(("ok", []))
    with pytest.raises(ValueError, match="at most 49"):
        _run(fake, toolset=toolset, resolved=_resolved(claude))
    assert fake.calls == 0


# ---- brutal review round 1 (F4, F5, F9) ----
def test_the_finalize_turn_asks_an_auto_only_provider_for_auto():
    """F4: z.ai takes `tool_choice: auto` only (`probes.AUTO_ONLY_PRESETS`),
    and the finalize turn's `none` is no exception: it is sent `auto`, the
    tools still offered and any call it makes declined, as on every target."""
    zai = wire_kit.target(provider_id="zai", model="glm-5", api_key="k")
    resolved = wire_kit.resolution(zai)
    resolved = replace(resolved, attempts=tuple(replace(a, provider_preset="zai")
                                                for a in resolved.attempts))
    fake = FakeToolTurns(("", [("read", {}), ("read", {})]), ("Mara.", [("read", {})]))
    result = _run(fake, resolved=resolved, budget=RunBudget(max_tool_calls=1),
                  tool_choice="required")
    assert [r["tool_choice"] for r in fake.requests] == ["auto", "auto"]
    assert fake.requests[-1]["tools"] is not None
    assert (result.status, result.limit, result.text) == ("budget_exhausted", "tool_calls",
                                                          "Mara.")
    final = [e for e in result.trace if e.kind == "model"][-1]
    assert "none sent as auto" in final.note
    # Everywhere else the finalize turn is still `none`.
    plain = FakeToolTurns(("", [("read", {}), ("read", {})]), ("Mara.", []))
    _run(plain, budget=RunBudget(max_tool_calls=1))
    assert plain.requests[-1]["tool_choice"] == "none"


def _anthropic_spare(**features):
    return replace(SPARE, model_features=features)


def test_a_fallback_that_would_think_anyway_does_not_inherit_tool_turns():
    """F5: thinking off cannot be SENT to an adaptive model whose catalog
    does not say it takes `disabled` -- left unset, it thinks -- so it is not
    the fallback of a turn that inherits another model's tool turns (the
    Anthropic API refuses that history beside thinking, spec 3.11 rule 4),
    and the trace never claims thinking was turned off."""
    spare = _anthropic_spare(adaptive_thinking=True)
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []))
    result = _run(fake, resolved=_resolved(wire.Chain(PRIMARY, spare)))
    assert fake.requests[0]["chain"].fallback is not None
    assert fake.requests[1]["chain"].fallback is None
    notes = " ".join(e.note for e in result.trace)
    assert "thinking off on" not in notes
    assert "claude-spare" in notes and "cannot be sent thinking off" in notes


def test_thinking_off_is_noted_only_where_it_is_sent():
    taken = _anthropic_spare(adaptive_thinking=True, disabled_thinking=True)
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []))
    result = _run(fake, resolved=_resolved(wire.Chain(PRIMARY, taken)))
    sent = fake.requests[1]["chain"].fallback
    assert sent is not None and sent.sampling.params["reasoning_effort"] == "off"
    second = [e for e in result.trace if e.kind == "model"][1]
    assert "thinking off on claude-spare" in second.note
    # A model whose catalog says nothing either way keeps riding, sent off,
    # and the trace says the off is not confirmed rather than that it held.
    unknown = FakeToolTurns(("", [("read", {})]), ("Mara.", []))
    result = _run(unknown, resolved=_resolved(wire.Chain(PRIMARY, SPARE)))
    assert unknown.requests[1]["chain"].fallback is not None
    second = [e for e in result.trace if e.kind == "model"][1]
    assert "thinking off on" not in second.note
    assert "not confirmed on claude-spare" in second.note


def test_a_fallback_known_unable_to_call_tools_never_rides_a_loop():
    """F9: on a `TOOLS_OPTIONAL` route the resolver does not drop it (tools
    are not `requires`), so the loop does, and says so."""
    resolved = _resolved(wire.Chain(PRIMARY, SPARE))
    primary, spare = resolved.attempts
    resolved = replace(resolved, attempts=(
        primary, replace(spare, capabilities={"tools": Cap("no", "catalog")})))
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []))
    result = _run(fake, resolved=resolved)
    assert result.status == "completed"
    assert all(r["chain"].fallback is None for r in fake.requests)
    assert "claude-spare cannot call tools" in result.trace[0].note


# ---- brutal review round 2 (R2-2, R2-4) ----
def test_an_auto_only_finalize_turns_calls_are_declined_and_keep_the_text():
    """R2-2: sent `auto`, a finalize turn may call anyway. Its calls are
    declined on the record -- in `declined` and the trace -- never run, and
    a turn that only called leaves the text the run already had."""
    zai = wire_kit.target(provider_id="zai", model="glm-5", api_key="k")
    resolved = wire_kit.resolution(zai)
    resolved = replace(resolved, attempts=tuple(replace(a, provider_preset="zai")
                                                for a in resolved.attempts))
    fake = FakeToolTurns(("Mara looks around the quay.", [("read", {}), ("read", {})]),
                         ("", [("read", {})]))
    result = _run(fake, resolved=resolved, budget=RunBudget(max_tool_calls=1))
    assert [r["tool_choice"] for r in fake.requests] == ["auto", "auto"]
    assert (result.status, result.limit) == ("budget_exhausted", "tool_calls")
    assert result.text == "Mara looks around the quay."
    assert [c.name for c in result.declined] == ["read"]
    (entry,) = [e for e in result.trace if e.kind == "tool" and e.turn == 2]
    assert (entry.name, entry.ok, entry.note) == ("read", False, "declined: final")


def test_the_spend_preflight_never_prices_a_fallback_the_loop_drops(monkeypatch):
    """R2-4: a fallback known unable to call tools is never sent (F9), so a
    run under a ceiling is not refused for want of its price."""
    local = wire_kit.target(provider_id="local", model="tiny", api_key="")
    resolved = _resolved(wire.Chain(PRIMARY, local))
    primary, spare = resolved.attempts
    resolved = replace(resolved, attempts=(
        primary, replace(spare, capabilities={"tools": Cap("no", "user")})))
    monkeypatch.setattr(inference, "cap_sent", lambda target: True)
    prices = {("openrouter", "vendor/active"): inference.Price("catalog", 1e-6, 2e-6)}
    assert inference.tool_run_refusal("chat", resolved, RunBudget(spend_ceiling_usd=1.0),
                                      prices) is None
    # A fallback that can call tools still needs its price.
    rides = replace(resolved, attempts=(primary, spare))
    refused = inference.tool_run_refusal("chat", rides, RunBudget(spend_ceiling_usd=1.0),
                                         prices)
    assert refused is not None and "tiny" in str(refused)
