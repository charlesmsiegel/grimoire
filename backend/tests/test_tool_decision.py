"""Decision-as-tool (01g-S7; 01g-C5, spec 3.12, §6 "Decision-as-tool" and the
decide-tool gate additions).

`routes.tool_decision.decision_tool` builds a `decide` tool for one loop:
nothing is offered without a resolution of the caller's task, or under a
spend ceiling where a stage is native or unpriceable; each call asks one
`Choice`, metered under the caller's task with the run's ids, capped per run,
and checked against the spend ceiling before it is sent; what comes back is
only what the backend reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import threading
import time
from dataclasses import replace

import pytest

import grimoire.store as store
from grimoire import decisions, inference, tool_calls, wire
from grimoire.llm_errors import LLMError
from grimoire.routes import tool_decision
from grimoire.routes.tool_decision import SpendGuard, ToolShape, decision_tool
from grimoire.tool_calls import RunBudget, ToolContext, ToolError
from tests import wire_kit
from tests.llm_fakes import FakeLLM, FakeToolTurns, decision_reply

TASK = "scene-break"          # a task on a decide route
TARGET = wire_kit.target(provider_id="openrouter", model="vendor/judge", api_key="k")
ARGS = {"question": "Does Mara tell the truth?", "allow_none": False,
        "context": "Mara stands at the Saltmarch quay.",
        "options": [{"id": "truth", "description": "She tells the truth."},
                    {"id": "lie", "description": "She lies."}]}


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _resolved(target=TARGET, mode=""):
    resolved = wire_kit.resolution(target, TASK, operation="decide")
    if mode:
        resolved = replace(resolved, attempts=tuple(replace(a, decision_mode=mode)
                                                    for a in resolved.attempts))
    return resolved


class _View:
    """A run's view as the loop hands it to a tool."""

    def __init__(self, decisions_left=2, room=None):
        self.left, self.room, self.charged, self.noted = decisions_left, room, [], []

    def take_decision(self):
        if self.left <= 0:
            return False
        self.left -= 1
        return True

    def room_usd(self):
        return self.room

    def charge(self, usd):
        self.charged.append(usd)

    def note_decision(self, status, option=None):
        self.noted.append((status, option))


def _ctx(view=None):
    return ToolContext(campaign="saltmarch", scene_identity="s1", run_id="run-7",
                       deadline=float("inf"), root="", turn=3, run=view)


def _call(spec, args=ARGS, view=None):
    return asyncio.run(spec.fn(args, _ctx(view)))


# ---- the offer ----
def test_nothing_is_offered_without_a_resolution_of_the_task():
    assert decision_tool(TASK, None, FakeLLM([["x"]]), cid="c", why="no Decision role") == (
        None, "no Decision role")
    spec, why = decision_tool(TASK, wire_kit.resolution(TARGET, "voice-drift",
                                                        operation="decide"),
                              FakeLLM([["x"]]), cid="c")
    assert spec is None and "cannot decide" in why


def test_nothing_is_offered_under_a_ceiling_with_a_native_or_unpriceable_stage(monkeypatch):
    spec, why = decision_tool(TASK, _resolved(mode="native"), FakeLLM([["x"]]), cid="c",
                              spend=SpendGuard(1.0))
    assert spec is None and "native" in why
    monkeypatch.setattr(store.llm_connections, "cached_row", lambda conn, model: None)
    unpriced = _resolved(replace(TARGET, provider_id="nowhere"))
    unpriced = replace(unpriced, attempts=tuple(replace(a, provider_preset="openrouter")
                                                for a in unpriced.attempts))
    spec, why = decision_tool(TASK, unpriced, FakeLLM([["x"]]), cid="c",
                              spend=SpendGuard(1.0))
    assert spec is None and "unpriceable" in why
    # Without a ceiling, a native stage is offered.
    spec, _ = decision_tool(TASK, _resolved(mode="native"), FakeLLM([["x"]]), cid="c")
    assert spec is not None


def test_the_parameters_follow_the_shape():
    full, _ = decision_tool(TASK, _resolved(), FakeLLM([["x"]]), cid="c")
    assert full.name == "decide" and "context" in full.parameters["properties"]
    tool_calls.check((full.definition(),), "auto")
    bare, _ = decision_tool(TASK, _resolved(), FakeLLM([["x"]]), cid="c",
                            shape=ToolShape(name="decide_choice", context=False))
    assert bare.name == "decide_choice" and "context" not in bare.parameters["properties"]
    with pytest.raises(ValueError):
        ToolShape(max_options=17)
    with pytest.raises(ValueError):
        ToolShape(max_question_chars=1001)


# ---- a call ----
def test_a_structured_answer_comes_back_with_no_probability():
    fake = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="saltmarch", round_id="r1",
                            response_id="p1")
    out = _call(spec, view=_View())
    assert json.loads(out.text) == {"answer": "truth", "status": "answered"}
    (row,) = [r for r in store.usage.calls(days=1) if r["task"] == TASK]
    assert (row["run_id"], row["loop_turn"]) == ("run-7", 3)
    assert (row["round_id"], row["response_id"]) == ("r1", "p1")
    # The question is the decide prompt's; the rationale is never returned.
    assert "Does Mara tell the truth?" in json.dumps(fake.requests[-1]["messages"])


def test_a_native_answer_returns_its_probability():
    answered = decisions.ItemResult(
        {"choice": decisions.Answer("lie", probability=0.7,
                                    distribution={"truth": 0.3, "lie": 0.7})},
        backend="native")
    fake = FakeLLM([["unused"]], decisions=[answered])
    spec, _ = decision_tool(TASK, _resolved(mode="native"), fake, cid="c")
    assert json.loads(_call(spec).text) == {
        "answer": "lie", "status": "answered", "probability": 0.7,
        "distribution": {"truth": 0.3, "lie": 0.7}}


def test_the_selection_shape_never_carries_a_distribution():
    fake = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c",
                            shape=ToolShape(result="selection"))
    assert json.loads(_call(spec).text) == {"selected": "truth"}
    bad = {**ARGS, "options": ARGS["options"][:1]}
    assert json.loads(_call(spec, bad).text) == {"selected": None, "reason": "invalid_request"}


@pytest.mark.parametrize("bad", [
    {**ARGS, "question": ""},
    {**ARGS, "question": "x" * 501},
    {**ARGS, "options": ARGS["options"][:1]},
    {**ARGS, "options": [{"id": "Truth", "description": "a"},
                         {"id": "truth", "description": "b"}]},
])
def test_a_refused_request_is_an_error_result(bad):
    fake = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c")
    with pytest.raises(ToolError, match="invalid request"):
        _call(spec, bad)
    assert fake.calls == 0


def test_at_the_cap_an_error_or_a_result():
    fake = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c")
    with pytest.raises(ToolError, match="decision budget"):
        _call(spec, view=_View(decisions_left=0))
    lenient, _ = decision_tool(TASK, _resolved(), fake, cid="c",
                               shape=ToolShape(on_cap="result"))
    assert json.loads(_call(lenient, view=_View(decisions_left=0)).text) == {
        "selected": None, "reason": "cap"}
    assert fake.calls == 0


def test_a_failed_decision_is_a_result_and_the_run_goes_on():
    from grimoire.llm_errors import LLMError
    fake = FakeLLM([["x"]], error=LLMError("rate_limit", "slow down"))
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c")
    with pytest.raises(ToolError, match="could not be made: rate_limit"):
        _call(spec)


def test_the_callers_capture_reaches_the_inner_decide():
    seen = []

    async def capture(messages, outcome, target):
        seen.append(outcome)

    spec, _ = decision_tool(TASK, _resolved(), FakeLLM([[decision_reply({"choice": "lie"})]]),
                            cid="c", capture=capture)
    _call(spec)
    assert len(seen) == 1


def test_the_output_cap_reaches_the_structured_stage():
    fake = FakeLLM([[decision_reply({"choice": "lie"})]])
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c", max_tokens=64)
    _call(spec)
    assert fake.requests[-1]["chain"].primary.sampling.call_cap == 64


# ---- the spend ceiling ----
CATALOG = {("openrouter", "vendor/judge"): {"prompt": "0.001", "completion": "0.001"}}


def test_a_decision_that_would_cross_the_ceiling_is_not_sent(monkeypatch):
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    fake = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, why = decision_tool(TASK, _resolved(), fake, cid="c", spend=SpendGuard(1.0))
    assert spec is not None, why
    with pytest.raises(ToolError, match="spend budget"):
        _call(spec, view=_View(room=0.0001))
    assert fake.calls == 0


def test_a_decisions_rows_count_toward_spent(monkeypatch):
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    fake = FakeLLM([[decision_reply({"choice": "truth"})]],
                   usage={"prompt_tokens": 100, "completion_tokens": 10})
    spec, _ = decision_tool(TASK, _resolved(), fake, cid="c", spend=SpendGuard(10.0))
    view = _View(room=10.0)
    _call(spec, view=view)
    assert view.charged == [pytest.approx(0.11)]


# ---- in a loop ----
def test_in_a_loop_each_decision_counts_and_the_cap_stops_the_run(monkeypatch):
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    judge = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="saltmarch")
    toolset = tool_calls.Toolset((spec,))
    model = FakeToolTurns(("", [("decide", ARGS), ("decide", ARGS)]), ("She told it.", []))
    result = asyncio.run(inference.run_tools(
        "chat", [{"role": "user", "content": "Play Mara."}], toolset=toolset,
        execute=tool_calls.registered(toolset), client=model,
        resolved=wire_kit.resolution(TARGET), budget=RunBudget(max_decisions=1),
        run_id="run-9", campaign="saltmarch"))
    assert judge.calls == 1
    first, second = model.requests[1]["messages"][-3:-1]
    assert json.loads(first["content"]) == {"answer": "truth", "status": "answered"}
    assert second["is_error"] and "decision budget" in second["content"]
    assert (result.status, result.limit) == ("budget_exhausted", "decisions")
    rows = [r for r in store.usage.calls(days=1) if r.get("run_id") == "run-9"]
    assert {r["task"] for r in rows} == {"chat", TASK}
    assert tool_decision.QUESTION_ID == "choice"


# ---- brutal review round 1 (F1, F2, F6) ----
class _SlowJudge(FakeLLM):
    """A structured Decision model that takes `seconds` to answer."""

    def __init__(self, seconds, *args, **kw):
        super().__init__(*args, **kw)
        self.seconds, self.started = seconds, None
        self.asked = threading.Event()

    async def complete(self, messages, conn, usage=None, **kw):
        self.started = time.monotonic()
        self.asked.set()
        await asyncio.sleep(self.seconds)
        return await super().complete(messages, conn, usage, **kw)


def test_the_decide_tool_is_bounded_by_the_run_not_the_store_read_timeout():
    """F1: a decide is a model call, not a store read -- the ten-second tool
    bound would abandon an ordinary structured decision. The tool bounds each
    of its own calls instead (`_around`; R2-1)."""
    spec, _ = decision_tool(TASK, _resolved(), FakeLLM([["x"]]), cid="c")
    assert spec.timeout == math.inf


def test_a_decide_cut_off_mid_call_is_still_charged(monkeypatch):
    """F1: a decide the run stops waiting on -- its request already out --
    charges its projection, as a failed call does: it can still bill."""
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    monkeypatch.setattr(tool_calls, "MIN_TURN_SECONDS", 0.0)
    judge = _SlowJudge(3.0, [[decision_reply({"choice": "truth"})]],
                       usage={"prompt_tokens": 100, "completion_tokens": 10})
    spec, why = decision_tool(TASK, _resolved(), judge, cid="saltmarch", spend=SpendGuard(5.0))
    assert spec is not None, why
    charges: list[float] = []
    real = inference._RunView.charge

    def spy(self, usd):
        charges.append(usd)
        return real(self, usd)

    monkeypatch.setattr(inference._RunView, "charge", spy)
    toolset = tool_calls.Toolset((spec,))
    model = FakeToolTurns(("", [("decide", ARGS)]), ("She told it.", []))
    asyncio.run(inference.run_tools(
        "chat", [{"role": "user", "content": "Play Mara."}], toolset=toolset,
        execute=tool_calls.registered(toolset), client=model,
        resolved=wire_kit.resolution(TARGET),
        budget=RunBudget(wall_seconds=1.0, spend_ceiling_usd=5.0),
        run_id="run-9", campaign="saltmarch"))
    assert judge.started is not None
    assert len(charges) == 1 and charges[0] > 0


def test_a_cancelled_decide_charges_its_projection(monkeypatch):
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    judge = _SlowJudge(30.0, [[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="c", spend=SpendGuard(10.0))
    view = _View(room=10.0)

    async def cut():
        task = asyncio.ensure_future(spec.fn(ARGS, _ctx(view)))
        await asyncio.to_thread(judge.asked.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cut())
    assert len(view.charged) == 1 and view.charged[0] > 0


def test_a_tool_built_without_a_guard_is_priced_when_the_run_has_a_ceiling(monkeypatch):
    """F2: no `spend=` at the build, a ceiling on the run -- the call is
    priced then, never refused as a spent budget nobody spent."""
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    judge = FakeLLM([[decision_reply({"choice": "truth"})]],
                    usage={"prompt_tokens": 100, "completion_tokens": 10})
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="c")
    view = _View(room=100.0)
    assert json.loads(_call(spec, view=view).text) == {"answer": "truth", "status": "answered"}
    assert judge.calls == 1 and view.charged == [pytest.approx(0.11)]


def test_an_unpriceable_tool_under_a_ceiling_says_so_and_takes_no_decision(monkeypatch):
    """F2: a stage no guard could price (here a native one, which the
    unguarded build offers on purpose) is refused as unpriceable, sends
    nothing, and leaves the run's decisions alone -- so the run never stops
    on a decision limit no decision reached."""
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    judge = FakeLLM([["unused"]], decisions=[decisions.ItemResult(
        {"choice": decisions.Answer("truth")}, backend="native")])
    spec, why = decision_tool(TASK, _resolved(mode="native"), judge, cid="saltmarch")
    assert spec is not None, why
    view = _View(decisions_left=1, room=100.0)
    with pytest.raises(ToolError, match="cannot be priced"):
        _call(spec, view=view)
    assert view.left == 1 and view.noted == [("unpriceable", None)]
    selection, _ = decision_tool(TASK, _resolved(mode="native"), judge, cid="saltmarch",
                                 shape=ToolShape(result="selection"))
    # R2-7: on the selection shape it is the spend budget's `cap`, one of
    # spec 3.12's four reasons.
    assert json.loads(_call(selection, view=_View(room=100.0)).text) == {
        "selected": None, "reason": "cap"}
    toolset = tool_calls.Toolset((spec,))
    model = FakeToolTurns(("", [("decide", ARGS)]), ("She told it.", []))
    result = asyncio.run(inference.run_tools(
        "chat", [{"role": "user", "content": "Play Mara."}], toolset=toolset,
        execute=tool_calls.registered(toolset), client=model,
        resolved=wire_kit.resolution(TARGET),
        budget=RunBudget(spend_ceiling_usd=100.0, max_decisions=1),
        run_id="run-9", campaign="saltmarch"))
    assert judge.calls == 0
    assert result.status == "completed" and result.limit == ""
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert "spend budget" not in tool_msgs[0]["content"]


@pytest.mark.parametrize("args, view, want", [
    (ARGS, _View(), ("answered", "truth")),
    ({**ARGS, "question": ""}, _View(), ("invalid_request", None)),
    (ARGS, _View(decisions_left=0), ("cap", None)),
])
def test_every_decide_call_notes_its_outcome_on_the_run(args, view, want):
    """F6 (spec 3.12, "The trace"): a decide records its status and option."""
    spec, _ = decision_tool(TASK, _resolved(), FakeLLM([[decision_reply({"choice": "truth"})]]),
                            cid="c")
    with contextlib.suppress(ToolError):
        _call(spec, args, view=view)
    assert view.noted == [want]


def test_a_failed_decision_is_noted_as_failed():
    spec, _ = decision_tool(TASK, _resolved(), FakeLLM(
        [["x"]], error=LLMError("rate_limit", "slow down")), cid="c")
    view = _View()
    with pytest.raises(ToolError):
        _call(spec, view=view)
    assert view.noted == [("failed", None)]


def test_the_loops_trace_holds_a_decide_entry(monkeypatch):
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    judge = FakeLLM([[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="saltmarch")
    toolset = tool_calls.Toolset((spec,))
    model = FakeToolTurns(("", [("decide", ARGS)]), ("She told it.", []))
    result = asyncio.run(inference.run_tools(
        "chat", [{"role": "user", "content": "Play Mara."}], toolset=toolset,
        execute=tool_calls.registered(toolset), client=model,
        resolved=wire_kit.resolution(TARGET), budget=RunBudget(),
        run_id="run-9", campaign="saltmarch"))
    (entry,) = [e for e in result.trace if e.kind == "decide"]
    assert (entry.turn, entry.name, entry.note, entry.ok) == (1, "truth", "answered", True)


# ---- brutal review round 2 (R2-1, R2-3, R2-5, R2-6) ----
def _looped(judge, budget, **kw):
    spec, why = decision_tool(TASK, _resolved(), judge, cid="saltmarch", **kw)
    assert spec is not None, why
    toolset = tool_calls.Toolset((spec,))
    model = FakeToolTurns(("", [("decide", ARGS)]), ("She told it.", []))
    started = time.monotonic()
    result = asyncio.run(inference.run_tools(
        "chat", [{"role": "user", "content": "Play Mara."}], toolset=toolset,
        execute=tool_calls.registered(toolset), client=model,
        resolved=wire_kit.resolution(TARGET), budget=budget,
        run_id="run-9", campaign="saltmarch"))
    return result, time.monotonic() - started


def test_with_no_wall_each_decide_call_is_held_to_the_per_call_ceiling(monkeypatch):
    """R2-1: a run with no wall still holds every model turn to
    `llm_call_budget`; a decide inside it is held to the same ceiling."""
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.config, "llm_call_budget", lambda: 1.0)
    judge = _SlowJudge(3.0, [[decision_reply({"choice": "truth"})]])
    result, took = _looped(judge, RunBudget(wall_seconds=0))
    (tool,) = [m for m in result.messages if m.get("role") == "tool"]
    assert tool["is_error"] and "timeout" in tool["content"]
    assert took < 3.0 and result.status == "completed"
    ctx = replace(_ctx(), call_budget=1.0)
    assert tool_decision._around(ctx) is not None
    assert tool_decision._around(_ctx()) is None


def test_a_decide_leaves_a_reserved_finalize_turn_its_room(monkeypatch):
    """R2-1: the wall's share of a decide's bound stops short of the
    finalize reserve, and of the loop's own wait on the tool (R2-5)."""
    seen: list[float] = []

    async def spy(call, seconds, overrun):
        seen.append(seconds)
        call.close()
        raise overrun(seconds)

    monkeypatch.setattr(tool_decision.deadline, "bounded", spy)
    ctx = replace(_ctx(), deadline=time.monotonic() + 100.0, call_budget=300.0,
                  reserve=tool_calls.MIN_TURN_SECONDS)
    bound = tool_decision._around(ctx)

    async def nothing():
        return None

    with pytest.raises(LLMError):
        asyncio.run(bound(nothing(), {}))
    (seconds,) = seen
    assert seconds <= 100.0 - tool_calls.MIN_TURN_SECONDS - tool_decision.DECIDE_MARGIN_S


def test_a_decide_cut_by_the_wall_leaves_a_decide_entry(monkeypatch):
    """R2-5: the decide's own bound fires before the loop stops waiting on
    the tool, so the cut is noted in the run's trace."""
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.config, "llm_call_budget", lambda: 300.0)
    judge = _SlowJudge(30.0, [[decision_reply({"choice": "truth"})]])
    result, took = _looped(judge, RunBudget(wall_seconds=6.5))
    (entry,) = [e for e in result.trace if e.kind == "decide"]
    assert (entry.note, entry.ok) == ("failed", False)
    assert took < 6.5 and result.text == "She told it."


def test_a_task_whose_policy_escalates_is_not_offered_the_tool(monkeypatch):
    """R2-3: the tool hands `decide` no escalator, which such a policy needs."""
    escalating = store.routing.TaskPolicy(escalate_to="primary", escalate_on=("abstained",),
                                          question="choice")
    judge = FakeLLM([[decision_reply({"choice": "truth"})]])
    built, _ = decision_tool(TASK, _resolved(), judge, cid="saltmarch")
    monkeypatch.setitem(store.routing.TASK_POLICY, TASK, escalating)
    spec, why = decision_tool(TASK, _resolved(), judge, cid="saltmarch")
    assert spec is None and "escalates" in why
    # A tool built before the policy changed: `decide` refuses before
    # sending, so the call is noted failed and never charged as one that went
    # out.
    view = _View(decisions_left=2, room=None)
    with pytest.raises(ToolError, match="not sent"):
        _call(built, view=view)
    assert view.noted == [("failed", None)] and view.charged == []
    assert judge.calls == 0


def test_a_ceiling_the_tool_could_never_be_priced_under_refuses_it_at_the_build():
    """R2-6: handed the run's own ceiling, the build refuses a tool every call
    of which the run would refuse; with none, it offers it."""
    spec, why = decision_tool(TASK, _resolved(mode="native"), FakeLLM([["x"]]), cid="c",
                              ceiling=100.0)
    assert spec is None and "native" in why
    spec, _ = decision_tool(TASK, _resolved(mode="native"), FakeLLM([["x"]]), cid="c",
                            ceiling=None)
    assert spec is not None
    with pytest.raises(ValueError):
        decision_tool(TASK, _resolved(), FakeLLM([["x"]]), cid="c",
                      spend=SpendGuard(1.0), ceiling=2.0)


# ---- brutal review round 3 (R3-1, R3-2, R3-3) ----
def _task_errors():
    return [r.get("kind") for r in store.errors.summary()["rows"] if r.get("module") == TASK]


_TWO = {**CATALOG, ("other", "vendor/judge2"): CATALOG[("openrouter", "vendor/judge")]}


def _walled(view, seconds_left, call_budget=300.0):
    """A context whose wall leaves `seconds_left` before the finalize reserve."""
    return replace(_ctx(view), call_budget=call_budget, reserve=tool_calls.MIN_TURN_SECONDS,
                   deadline=time.monotonic() + tool_calls.MIN_TURN_SECONDS + seconds_left)


def test_a_decide_inside_the_finalize_reserve_is_refused_before_it_takes_a_decision(
        monkeypatch):
    """R3-1: a call whose wall share is already spent sends nothing, so it
    takes no decision, charges nothing and files no error row -- the run's
    own clock, not a provider failure -- and says so on the trace. Neither
    the primary nor its fallback is sent anything."""
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: _TWO.get((conn, model)))
    other = wire_kit.target(provider_id="other", model="vendor/judge2", api_key="k")
    resolved = wire_kit.resolution(wire.Chain(TARGET, other), TASK, operation="decide")
    judge = FakeLLM([[decision_reply({"choice": "truth"})]] * 3)
    for shape, want in ((ToolShape(), None),
                        (ToolShape(result="selection"), {"selected": None, "reason": "cap"})):
        spec, why = decision_tool(TASK, resolved, judge, cid="c", shape=shape)
        assert spec is not None, why
        view = _View(decisions_left=2, room=100.0)
        if want is None:
            with pytest.raises(ToolError, match="wall clock"):
                asyncio.run(spec.fn(ARGS, _walled(view, -2.0)))
        else:
            assert json.loads(asyncio.run(spec.fn(ARGS, _walled(view, -2.0))).text) == want
        assert view.left == 2 and view.charged == [] and view.noted == [("wall", None)]
    assert judge.calls == 0 and _task_errors() == []


def test_a_call_the_wall_refuses_unstarted_is_neither_charged_nor_an_error(monkeypatch):
    """R3-1: past the check, a wall share spent by the time the call is
    sent abandons it unstarted as the run's own stop: no charge, no error
    row, no health mark on the primary or its fallback."""
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: _TWO.get((conn, model)))
    lefts = iter([10.0])
    monkeypatch.setattr(tool_decision, "_wall_left", lambda ctx: next(lefts, -1.0))
    other = wire_kit.target(provider_id="other", model="vendor/judge2", api_key="k")
    resolved = wire_kit.resolution(wire.Chain(TARGET, other), TASK, operation="decide")
    judge = FakeLLM([[decision_reply({"choice": "truth"})]] * 3)
    spec, _ = decision_tool(TASK, resolved, judge, cid="c")
    view = _View(room=100.0)
    with pytest.raises(ToolError, match="wall clock"):
        asyncio.run(spec.fn(ARGS, _walled(view, 10.0)))
    assert view.charged == [] and view.noted == [("wall", None)]
    assert judge.calls == 0 and judge.noted == [] and _task_errors() == []


def test_a_decide_the_wall_leaves_no_room_for_never_ends_the_run_on_spend(monkeypatch):
    """R3-1: inside the finalize reserve the decide is never sent and never
    charged, so it cannot stop the run on `spend` (its projection, charged
    as a phantom, used to)."""
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    monkeypatch.setattr(store.config, "llm_call_budget", lambda: 300.0)
    judge = _SlowJudge(0.0, [[decision_reply({"choice": "truth"})]],
                       usage={"prompt_tokens": 100, "completion_tokens": 10})
    result, _ = _looped(judge, RunBudget(wall_seconds=5.3, spend_ceiling_usd=5.0),
                        spend=SpendGuard(5.0))
    (entry,) = [e for e in result.trace if e.kind == "decide"]
    assert entry.note == "wall" and result.limit != "spend"
    assert judge.calls == 0 and _task_errors() == []


def test_a_decide_cut_by_the_wall_files_no_error_row_and_marks_no_connection():
    """R3-2: the run's own wall cutting a decide mid-call is a budget stop,
    as it is for a model turn (spec 3.9): no error row, no health mark --
    but the request went out, so it is charged."""
    judge = _SlowJudge(30.0, [[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="c")
    view = _View()
    with pytest.raises(ToolError, match="timeout"):
        asyncio.run(spec.fn(ARGS, _walled(view, 1.5)))
    assert judge.started is not None and view.noted == [("failed", None)]
    assert _task_errors() == [] and judge.noted == []


def test_a_decide_past_the_per_call_ceiling_is_noted_against_its_connection():
    """R3-2: an `llm_call_budget` overrun is a provider timeout (#146): an
    error row, and the connection that held the request is told."""
    judge = _SlowJudge(3.0, [[decision_reply({"choice": "truth"})]])
    spec, _ = decision_tool(TASK, _resolved(), judge, cid="c")
    with pytest.raises(ToolError, match="timeout"):
        asyncio.run(spec.fn(ARGS, replace(_ctx(_View()), call_budget=0.3)))
    ((target, error),) = judge.noted
    assert target == TARGET and error.kind == "timeout"
    assert _task_errors() == ["timeout"]


@pytest.mark.parametrize("cap", [0, -5, 2.5, True])
def test_an_output_cap_decide_would_refuse_is_not_offered(cap):
    """R3-3: `decide` refuses a cap that is not a positive whole number
    before sending, so a tool built with one is never offered."""
    spec, why = decision_tool(TASK, _resolved(), FakeLLM([["x"]]), cid="c", max_tokens=cap)
    assert spec is None and "max_tokens" in why


def test_a_pre_send_refusal_takes_no_decision(monkeypatch):
    """R3-3: a refusal no model argument decides -- here a policy that
    changed after the build -- is checked before the call takes a decision."""
    judge = FakeLLM([[decision_reply({"choice": "truth"})]])
    built, _ = decision_tool(TASK, _resolved(), judge, cid="saltmarch")
    monkeypatch.setitem(store.routing.TASK_POLICY, TASK, store.routing.TaskPolicy(
        escalate_to="primary", escalate_on=("abstained",), question="choice"))
    view = _View(decisions_left=1)
    with pytest.raises(ToolError, match="not sent"):
        _call(built, view=view)
    assert view.left == 1 and view.noted == [("failed", None)] and judge.calls == 0
