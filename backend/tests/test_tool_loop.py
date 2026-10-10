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
from grimoire import inference, model_guidance, schemas, tool_calls, wire
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
    assert "[truncated: 400 more characters]" in fake.requests[1]["messages"][-2]["content"]


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
