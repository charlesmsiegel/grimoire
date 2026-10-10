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
import json
from dataclasses import replace

import pytest

import grimoire.store as store
from grimoire import decisions, inference, tool_calls
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
        self.left, self.room, self.charged = decisions_left, room, []

    def take_decision(self):
        if self.left <= 0:
            return False
        self.left -= 1
        return True

    def room_usd(self):
        return self.room

    def charge(self, usd):
        self.charged.append(usd)


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
