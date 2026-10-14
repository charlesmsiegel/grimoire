"""Roadmap 12-S2: the loop driver, budgets and trace (spec 5, 7, 8; 14 "Loop
and budgets").

The model is `llm_fakes.FakeToolTurns`, scripted by call order; the store is
synthetic and placeholder-named.
"""

import asyncio
import json
from dataclasses import replace

import pytest

from grimoire import tool_calls
from grimoire.llm_errors import LLMError
from grimoire.routes import investigation
from grimoire.store import config, llm_connections
from grimoire.store.inference import limits
from grimoire.store.investigation import budget as inv_budget
from grimoire.store.investigation import state, trace
from tests import wire_kit
from tests.history_fixtures import absorbed, played
from tests.history_fixtures import cid as cid
from tests.llm_fakes import FakeToolTurns

RUN = "runinvestigate01"
TARGET = wire_kit.target(provider_id="openrouter", model="vendor/reader", api_key="sk-test")
TASK = "investigation-question"
SEED = investigation.Seed([{"role": "system", "content": "Answer from the history."},
                           {"role": "user", "content": "Who buried the brass compass?"}])
CATALOG = {("openrouter", "vendor/reader"): {"prompt": "0.001", "completion": "0.001"}}


def _resolved(target=TARGET):
    return wire_kit.resolution(target, TASK)


def _budget(**changes):
    return replace(inv_budget.for_entry("question"), **{"wall_seconds": 0, **changes})


def _run(cid, fake, *, budget=None, cancelled=None, resolved=None):
    run_state = state.RunState(cid=cid, mode="question")
    outcome = asyncio.run(investigation.investigate(
        None, state=run_state, task=TASK, seed=SEED, client=fake,
        resolved=resolved or _resolved(), budget=budget or _budget(), run_id=RUN,
        cancelled=cancelled))
    return outcome, run_state


@pytest.fixture
def scene(cid):
    sid = played(cid, text="Mara buries the brass compass under the pier.")
    absorbed(cid, sid, "Mara buries the brass compass under the pier.")
    return sid


def _read_then_answer(sid, citations=None):
    return FakeToolTurns(
        ("", [("search_history", {"query": "brass compass", "max": 3})]),
        ("", [("get_scene_excerpt", {"scene": f"scene:{sid}", "query": "compass",
                                     "max_posts": 4})]),
        ("", [("answer", {"text": "Mara buried it under the pier.",
                          "citations": citations or [f"scene:{sid}"]})]))


def test_a_run_that_reads_and_answers_completes_with_its_trace(cid, scene):
    fake = _read_then_answer(scene)
    outcome, _state = _run(cid, fake)
    assert outcome.state == "completed"
    assert outcome.result == {"text": "Mara buried it under the pier.",
                              "citations": [f"scene:{scene}"]}
    t = outcome.trace
    assert trace.valid(t)
    assert t["run"] == RUN and t["mode"] == "question" and t["task"] == TASK
    assert t["model"] == "openrouter/vendor/reader"
    assert [s["tool"] for s in t["steps"]] == ["search_history", "get_scene_excerpt", "answer"]
    assert t["steps"][0]["args"] == {"query": "brass compass", "max": 3}
    # The terminal call is the last step, on the turn that made it, with its
    # structure and never its prose (the validated answer is `final`).
    assert t["steps"][-1] == {"turn": 3, "tool": "answer",
                              "args": {"citations": [f"scene:{scene}"]},
                              "result": {"ok": True, "refs": [], "chars": 0,
                                         "truncated": False}}
    assert t["inspected"] == [f"scene:{scene}"]
    assert t["final"] == outcome.result
    assert t["usage"]["model_turns"] == 3 and t["usage"]["tool_calls"] == 3
    # No ledger row carried a price, so no column claims one.
    assert t["usage"]["cost"] == {"cost_usd": None, "estimated_usd": None,
                                  "modelled_usd": None}
    # No result text in the trace: only refs and sizes.
    assert "pier" not in json.dumps(t["steps"])
    # Every turn is a required tool call, metered under the run id.
    assert all(r["tool_choice"] == "required" for r in fake.requests)


def test_a_prose_final_turn_is_insufficient_evidence(cid, scene):
    fake = FakeToolTurns(("", [("search_history", {"query": "compass", "max": None})]),
                         ("I think it was Mara.", []))
    outcome, _ = _run(cid, fake)
    assert outcome.state == "insufficient_evidence" and outcome.result is None
    assert outcome.trace["final"] is None
    # No terminal call, so no terminal step and none counted.
    assert [s["tool"] for s in outcome.trace["steps"]] == ["search_history"]
    assert outcome.trace["usage"]["tool_calls"] == 1


def test_every_row_the_run_filed_is_part_of_its_usage(cid, scene):
    """The decide tool's calls -- a failed one included, which hands back no
    rows -- are metered apart from the loop's turns; the trace's money is
    every row filed under the run's id, and no other run's."""
    from grimoire.store import usage
    usage.record(task="investigation-decide", campaign=cid, run_id=RUN, operation="decide",
                 model="vendor/judge", provider="openrouter", status="error",
                 error="timeout", prompt_tokens=10, cost_usd=0.25)
    usage.record(task="investigation-decide", campaign=cid, run_id="another-run",
                 operation="decide", model="vendor/judge", provider="openrouter",
                 prompt_tokens=10, cost_usd=9.0)
    run_state = state.RunState(cid=cid, mode="question")
    outcome = asyncio.run(investigation.investigate(
        None, state=run_state, task=TASK, seed=SEED, client=_read_then_answer(scene),
        resolved=_resolved(), budget=_budget(), run_id=RUN))
    assert outcome.trace["usage"]["cost"]["cost_usd"] == 0.25
    # The decision's row is not a model turn of the loop.
    assert outcome.trace["usage"]["model_turns"] == 3


@pytest.mark.parametrize("budget,limit", [
    ({"max_turns": 2}, "turns"),
    ({"max_tool_calls": 1}, "tool_calls"),
    ({"max_result_chars_total": 10}, "result_chars"),
])
def test_each_limit_ends_the_run_with_01gs_name_and_sends_nothing_further(
        cid, scene, budget, limit):
    fake = FakeToolTurns(("", [("search_history", {"query": "compass", "max": None}),
                               ("get_timeline", {"from": None, "to": None})]))
    outcome, _ = _run(cid, fake, budget=_budget(**budget))
    assert outcome.state == "budget_exhausted" and outcome.limit == limit
    assert outcome.trace["limit"] == limit
    sent = len(fake.requests)
    assert sent <= budget.get("max_turns", 6)


def test_the_wall_stops_before_a_turn_that_cannot_fit(cid, scene):
    fake = _read_then_answer(scene)
    outcome, _ = _run(cid, fake, budget=_budget(wall_seconds=1.0))
    assert (outcome.state, outcome.limit) == ("budget_exhausted", "wall")
    assert fake.requests == []


def test_the_context_stop_uses_the_fallback_when_no_window_is_known(cid, scene, monkeypatch):
    monkeypatch.setattr(inv_budget, "FALLBACK_PROMPT_TOKENS", 5)
    fake = _read_then_answer(scene)
    outcome, _ = _run(cid, fake)
    assert (outcome.state, outcome.limit) == ("budget_exhausted", "context")
    assert fake.requests == []


def test_the_context_stop_reads_prompt_ceiling_with_the_turns_own_cap(monkeypatch):
    seen = {}

    def ceiling(resolved, *, reserve=None, max_tokens=None):
        seen["max_tokens"] = max_tokens
        return limits.Ceiling(tokens=3000, window=4000, reserve=1000, complete=True,
                              binding=None, reason="")
    monkeypatch.setattr(limits, "prompt_ceiling", ceiling)
    budget = inv_budget.for_entry("question")
    assert inv_budget.prompt_tokens(_resolved(), budget) == 3000
    assert seen["max_tokens"] == budget.max_output_tokens == 2048
    monkeypatch.setattr(limits, "prompt_ceiling", lambda *a, **k: limits.Ceiling(
        tokens=None, window=None, reserve=0, complete=False, binding=None, reason=""))
    assert inv_budget.prompt_tokens(_resolved(), budget) == inv_budget.FALLBACK_PROMPT_TOKENS


def test_a_cancel_stops_the_run(cid, scene):
    flag = {"stop": False}

    def stop_after_first():
        return flag["stop"]
    fake = FakeToolTurns(("", [("search_history", {"query": "compass", "max": None})]))
    original = fake.complete

    async def complete(*a, **k):
        flag["stop"] = True
        return await original(*a, **k)
    fake.complete = complete
    outcome, _ = _run(cid, fake, cancelled=stop_after_first)
    assert outcome.state == "cancelled"
    assert len(fake.requests) == 1


def test_a_loop_that_raises_still_reports_the_turns_it_filed(cid, scene):
    """A turn-1 failure raises out of the loop with no `LoopResult`; the
    row its meter filed is read back, so the trace says one turn ran."""
    outcome, _ = _run(cid, FakeToolTurns(LLMError("rate_limit", "slow down")))
    assert outcome.state == "failed"
    usage = outcome.trace["usage"]
    assert usage["model_turns"] == 1 and usage["unpriced_calls"] == 1


def test_the_searchs_embedding_rows_are_part_of_the_runs_usage(cid, scene):
    """09's retrieval meters its embedding under `history-recall` with the
    run's id and hands back no rows: the trace reads them from the ledger."""
    from grimoire.store import usage
    usage.record(task="history-recall", campaign=cid, run_id=RUN, operation="embed",
                 model="vendor/embed", provider="openrouter", prompt_tokens=12,
                 cost_usd=0.125)
    usage.record(task="history-recall", campaign=cid, run_id="another-run",
                 operation="embed", model="vendor/embed", provider="openrouter",
                 prompt_tokens=12, cost_usd=9.0)
    run_state = state.RunState(cid=cid, mode="question", embed_client=object())
    outcome = asyncio.run(investigation.investigate(
        None, state=run_state, task=TASK, seed=SEED, client=_read_then_answer(scene),
        resolved=_resolved(), budget=_budget(), run_id=RUN))
    assert outcome.trace["usage"]["cost"]["cost_usd"] == 0.125


def test_a_failed_first_turn_is_attributed_to_the_model_that_served_it(cid, scene):
    """A turn-1 failure raises with no `LoopResult`; the trace names the
    model its filed row names (a fallback that served), not the primary."""
    from grimoire.store import usage
    usage.record(task=TASK, campaign=cid, run_id=RUN, model="vendor/backup",
                 provider="openrouter", provider_id="backup", status="error",
                 error="rate_limit")

    class Raising(FakeToolTurns):
        async def complete(self, *a, **k):
            raise LLMError("rate_limit", "slow down")
    outcome, _ = _run(cid, Raising())
    assert outcome.state == "failed"
    assert outcome.trace["model"] == "backup/vendor/backup"


def test_a_fallback_that_cannot_call_tools_bounds_no_context(monkeypatch):
    """A tool run never sends a fallback known unable to call tools, so its
    window -- unknown here -- is no reason to stop on `context`."""
    from grimoire.store.inference import resolve as inference_resolve
    seen = {}

    def ceiling(resolved, *, reserve=None, max_tokens=None):
        seen["rides"] = resolved.rides
        return limits.Ceiling(tokens=3000, window=4000, reserve=1000,
                              complete=not resolved.rides, binding=None, reason="")
    monkeypatch.setattr(limits, "prompt_ceiling", ceiling)
    monkeypatch.setattr(inference_resolve, "known_lacks",
                        lambda resolved, cap, fallback=False: fallback and cap == "tools")
    riding = replace(_resolved(), rides=True)
    assert inv_budget.prompt_tokens(riding, inv_budget.for_entry("question")) == 3000
    assert seen["rides"] is False


def test_a_thread_that_finishes_after_the_check_writes_nothing(cid):
    """A tool thread the loop stopped waiting on can finish late: what it
    notes was never delivered, before the freeze or after it."""
    run_state = state.RunState(cid=cid, mode="question")
    run_state.inspect("001--a")
    run_state.note(trace.step(1, "get_scene_excerpt", {}, ok=True, refs=("scene:001--a",)))
    # The timed-out call's thread noted its step before the check...
    run_state.note(trace.step(2, "read_scene_window", {}, ok=True, refs=("scene:002--b",)))
    result = tool_calls.LoopResult(status="completed", trace=(
        tool_calls.TraceEntry(1, "tool", "get_scene_excerpt", ok=True, refs=("scene:001--a",)),
        tool_calls.TraceEntry(2, "tool", "read_scene_window", ok=False),
    ))
    investigation._keep_delivered(run_state, result)
    assert [s["tool"] for s in run_state.steps] == ["get_scene_excerpt"]
    # ...and one finishing after it adds nothing at all.
    run_state.inspect("002--b")
    run_state.note(trace.step(2, "read_scene_window", {}, ok=True))
    assert run_state.inspected == {"001--a"}
    assert [s["tool"] for s in run_state.steps] == ["get_scene_excerpt"]


@pytest.mark.parametrize("script", [
    (LLMError("rate_limit", "slow down, says the provider"),),
    (("", [("search_history", {"query": "compass", "max": None})]),
     LLMError("timeout", "the provider said something private")),
])
def test_a_failed_call_is_failed_with_its_kind_only(cid, scene, script):
    outcome, _ = _run(cid, FakeToolTurns(*script))
    assert outcome.state == "failed"
    assert outcome.error in ("rate_limit", "timeout")
    assert "says" not in json.dumps(outcome.as_dict())
    assert "private" not in json.dumps(outcome.as_dict())


def test_an_unpriced_model_under_a_ceiling_is_refused_having_sent_nothing(
        cid, scene, monkeypatch):
    monkeypatch.setattr(llm_connections, "cached_row", lambda conn, model: None)
    fake = _read_then_answer(scene)
    outcome, _ = _run(cid, fake, budget=_budget(spend_ceiling_usd=1.0))
    assert (outcome.state, outcome.reason) == ("refused", "unpriceable")
    assert fake.requests == []


def test_a_priced_run_under_a_ceiling_stops_on_spend(cid, scene, monkeypatch):
    monkeypatch.setattr(llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))
    fake = FakeToolTurns(("", [("search_history", {"query": "compass", "max": None})]))
    outcome, _ = _run(cid, fake, budget=_budget(spend_ceiling_usd=0.0001))
    assert (outcome.state, outcome.limit) == ("budget_exhausted", "spend")


def test_the_budgets_are_the_specs():
    config.write_config(llm_call_budget="300")
    turn = inv_budget.for_entry("turn")
    assert (turn.max_turns, turn.max_tool_calls, turn.max_decisions,
            turn.max_output_tokens, turn.max_result_chars_total) == (3, 4, 0, 512, 12_000)
    assert turn.wall_seconds == inv_budget.E1_WALL_S == 20.0
    cont = inv_budget.for_entry("continuity")
    assert (cont.max_turns, cont.max_tool_calls, cont.max_decisions, cont.wall_seconds) \
        == (6, 12, 2, 900.0)
    question = inv_budget.for_entry("question")
    assert (question.max_turns, question.max_tool_calls, question.max_decisions,
            question.wall_seconds, question.max_result_chars_total) == (5, 10, 1, 600.0, 36_000)
    assert cont.spend_ceiling_usd is None
    config.write_config(llm_call_budget="10", investigation_spend_ceiling="0.5")
    assert inv_budget.for_entry("turn").wall_seconds == 10.0
    assert inv_budget.for_entry("question").spend_ceiling_usd == 0.5
    for bad in ("", "0", "-1", "nan", "inf", "lots"):
        config.write_config(investigation_spend_ceiling=bad)
        assert inv_budget.spend_ceiling() is None


@pytest.fixture(autouse=True)
def _home_for_budgets(monkeypatch, tmp_path):
    # `test_the_budgets_are_the_specs` writes config with no campaign fixture.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_a_budget_refuses_a_bad_context_stop():
    with pytest.raises(ValueError):
        tool_calls.RunBudget(max_prompt_tokens=0)
    assert tool_calls.RunBudget(max_prompt_tokens=10).max_prompt_tokens == 10
    assert "context" in tool_calls.LIMITS


def test_only_reads_the_loop_delivered_count_as_read(cid):
    run_state = state.RunState(cid=cid, mode="question")
    run_state.inspect("001--a")
    run_state.inspect("002--b")
    result = tool_calls.LoopResult(status="completed", trace=(
        tool_calls.TraceEntry(1, "tool", "get_scene_excerpt", ok=True, refs=("scene:001--a",)),
        # Timed out: the model was told so, though its thread noted the read.
        tool_calls.TraceEntry(2, "tool", "read_scene_window", ok=False),
        tool_calls.TraceEntry(2, "tool", "search_history", ok=True, refs=("scene:002--b",)),
    ))
    investigation._keep_delivered(run_state, result)
    assert run_state.inspected == {"001--a"}


def test_a_window_known_on_some_attempts_only_falls_back(monkeypatch):
    monkeypatch.setattr(limits, "prompt_ceiling", lambda *a, **k: limits.Ceiling(
        tokens=190_000, window=200_000, reserve=10_000, complete=False, binding=None,
        reason=""))
    budget = inv_budget.for_entry("question")
    assert inv_budget.prompt_tokens(_resolved(), budget) == inv_budget.FALLBACK_PROMPT_TOKENS
