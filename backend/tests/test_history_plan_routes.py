"""Roadmap 10-S1: the planner in the turn's history phase, and one planned round.

The repair hop (10-S2) is switched off here unless a test says otherwise:
these tests are about the first plan and its round."""

import asyncio
import json
import threading
import time

import pytest

from grimoire import store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, common, history_recall
from grimoire.store import chronicle, routing, scenes
from grimoire.store.context import assemble, epistemic
from grimoire.store.history import model, query
from tests.history_fixtures import cid as cid
from tests.history_fixtures import played
from tests.llm_fakes import FakeLLM

QUESTION = "Who counted the lanterns at the pier?"


def absorbed(cid, sid, summary, keywords=()):
    chronicle.absorb(cid, {"id": sid, "one_line": summary, "summary": summary,
                           "keywords": list(keywords), "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, summary, summary)


def setup_thin(cid, current="Where is the silver ledger?", repair="off"):
    """One scene the turn's words find, one only a planned question finds,
    and two unrelated: coverage `thin` (or `empty` for a turn naming none)."""
    found = played(cid, text="Mara talks of the silver ledger.")
    absorbed(cid, found, "Mara talks of the silver ledger.")
    lanterns = played(cid, text="The ferryman counts the lanterns at the pier.")
    absorbed(cid, lanterns, "The ferryman counts the lanterns.")
    for text in ("Rain on the roofs.", "Gulls wheel overhead."):
        absorbed(cid, played(cid, text=text), text)
    sid = played(cid, text=current)
    store.config.write_config(history_recall_depth="4", history_plan="auto", recap_depth="0",
                              history_plan_repair=repair)
    store.llm_connections.update_connection("openrouter", api_key="sk-fake")
    return sid, found, lanterns


def plan_reply(**kw):
    base = {"needs_history": True, "subjects": [], "questions": [QUESTION],
            "terms": ["lanterns", "ferryman"], "scopes": [], "max_scenes": 2}
    return json.dumps({**base, **kw})


def gather(cid, sid, fake, **kw):
    return asyncio.run(history_recall.gather(None, cid, sid, seed="", resolved=None,
                                             llm_client=fake, embed_client=None, **kw))


def rows(task="history-query-plan"):
    return [r for r in store.usage.calls(days=1) if r["task"] == task]


def test_off_sends_no_planner_request_and_adds_no_trace(cid):
    sid, *_ = setup_thin(cid)
    store.config.write_config(history_plan="off")
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake)
    assert fake.calls == 0 and ev.plan is None and "plan" not in ev.detail()
    assert ev.coverage.verdict == "thin"


def test_off_keeps_the_four_second_phase(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    store.config.write_config(history_plan="off")
    seen = []
    real = store.history.retrieve

    async def spy(*a, deadline, **kw):
        seen.append(deadline - time.monotonic())
        return await real(*a, deadline=deadline, **kw)
    monkeypatch.setattr(store.history, "retrieve", spy)
    gather(cid, sid, FakeLLM([[plan_reply()]]))
    store.config.write_config(history_plan="auto")
    gather(cid, sid, FakeLLM([[plan_reply()]]))
    assert seen[0] <= history_recall.HISTORY_PHASE_SECONDS
    assert seen[1] > history_recall.HISTORY_PHASE_SECONDS


def test_thin_coverage_plans_once_and_merges_a_round(cid):
    sid, found, lanterns = setup_thin(cid)
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake, post=4, run_id="run-1")
    trace = ev.plan
    assert fake.calls == 1
    assert (trace.terminal, trace.trigger, trace.sufficiency, trace.sufficiency_source) == (
        "planned", "thin", "not_asked", "not_asked")
    assert not trace.cheap_retrieval_failed
    selected = [i.scene.sid for i in ev.items]
    assert lanterns in selected and found in selected
    assert len(trace.rounds) == 1
    record = trace.rounds[0]
    assert record.questions == (QUESTION,) and record.terms == ("lanterns", "ferryman")
    assert record.scene_keys == tuple(i.scene.key for i in ev.items)
    assert record.evidence_ids == tuple(e for i in ev.items for e in i.evidence_ids)
    assert lanterns not in [k.split(":")[-1] for k in trace.e0.scene_keys]
    assert trace.gain and all(k not in trace.e0.scene_keys for k in trace.gain)
    assert [s.task for s in trace.steps] == ["history-query-plan"]
    assert trace.steps[0].outcome == "ok" and trace.steps[0].rows == 1
    # Attributed to the post it served; the plan was asked for structured.
    (row,) = rows()
    assert (row["campaign"], row["scene"], row["post"], row["run_id"]) == (cid, sid, 4, "run-1")
    assert fake.schemas[0] is not None
    assert fake.schemas[0]["required"][0] == "needs_history"
    assert len(ev.items) <= 4
    detail = ev.detail()
    assert detail["plan"]["terminal"] == "planned"
    json.dumps(detail)


def test_an_empty_first_retrieval_gains_items(cid):
    sid, _found, lanterns = setup_thin(cid, current="Hello there.")
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.plan.trigger == "empty" and ev.plan.e0.scene_keys == ()
    assert lanterns in [i.scene.sid for i in ev.items]
    assert ev.plan.terminal == "planned"


def test_the_planner_is_offered_only_filtered_seeds_by_handle(cid):
    sid, *_ = setup_thin(cid)
    fake = FakeLLM([[plan_reply()]])
    gather(cid, sid, fake)
    user = fake.messages[1]["content"]
    assert "Where is the silver ledger?" in user
    assert "Mara talks of the silver ledger." in user   # evidence already found
    assert '"enum"' not in json.dumps(fake.schemas[0]["properties"]["subjects"]) or "s1" in user


@pytest.mark.parametrize("verdict", ["sufficient", "exhausted", "error"])
def test_non_thin_coverage_never_plans(cid, verdict):
    sid, *_ = setup_thin(cid)
    first = model.Evidence(coverage=model.Coverage(verdict))
    fake = FakeLLM([[plan_reply()]])
    turn = history_recall.Turn(model.Query(("x",)), None, 4, {})
    ev, trace = asyncio.run(history_recall.plan_and_repair(
        None, cid, sid, turn, first, client=fake, ceiling=1200,
        deadline=time.monotonic() + 5))
    assert ev is first and trace.terminal == "not_run:" + verdict
    assert trace.trigger == "" and fake.calls == 0 and trace.rounds == ()


def test_a_forced_gather_plans_past_the_free_gate(cid):
    sid, *_ = setup_thin(cid)
    store.config.write_config(history_plan="off")
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake, force=True)
    assert fake.calls == 1 and ev.plan.trigger == "forced" and ev.plan.terminal == "planned"


def test_a_declined_plan_returns_the_first_evidence(cid):
    sid, *_ = setup_thin(cid)
    fake = FakeLLM([[plan_reply(needs_history=False, questions=[], terms=[])]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "declined" and ev.plan.rounds == ()
    assert [i.scene.key for i in ev.items] == list(ev.plan.e0.scene_keys)


@pytest.mark.parametrize("text", ["no plan at all", plan_reply(questions=[], terms=[])])
def test_an_unusable_plan_returns_the_first_evidence(cid, text):
    sid, *_ = setup_thin(cid)
    ev = gather(cid, sid, FakeLLM([[text]]))
    assert ev.plan.terminal == "unusable"
    assert ev.plan.steps[0].outcome in ("unreadable", "empty")


def test_resolution_runs_off_the_loop(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    loop = []
    seen = []
    real = common.require_inference

    def spy(task, *a, **k):
        seen.append((task, threading.get_ident()))
        return real(task, *a, **k)
    monkeypatch.setattr(common, "require_inference", spy)

    async def run():
        loop.append(threading.get_ident())
        return await history_recall.gather(None, cid, sid, seed="", resolved=None,
                                           llm_client=FakeLLM([[plan_reply()]]),
                                           embed_client=None)
    asyncio.run(run())
    plans = [t for task, t in seen if task == "history-query-plan"]
    assert plans and all(t != loop[0] for t in plans)


def test_an_unresolvable_planner_route_skips_with_its_kind(cid):
    sid, *_ = setup_thin(cid)
    store.llm_connections.update_connection("openrouter", base_url="https://example.test/v1")
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "skipped:missing_key" and fake.calls == 0
    assert ev.items and ev.coverage.verdict == "thin"


def test_a_planner_timeout_is_unusable_and_noted_against_the_connection(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    monkeypatch.setattr(history_recall, "PLAN_CEILING", 0.2)
    fake = FakeLLM([[plan_reply()]], stall=True)
    started = time.monotonic()
    ev = gather(cid, sid, fake)
    assert time.monotonic() - started < 3
    assert ev.plan.terminal == "unusable" and ev.plan.steps[0].outcome == "timeout"
    assert fake.noted and fake.noted[0][1].kind == "timeout"
    (row,) = rows()
    assert row["error"] == "timeout"
    assert [i.scene.key for i in ev.items] == list(ev.plan.e0.scene_keys)


def test_a_failed_planner_call_is_unusable(cid):
    sid, *_ = setup_thin(cid)
    fake = FakeLLM([[""]], error=LLMError("rate_limit", "slow down"))
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "unusable" and ev.plan.steps[0].outcome == "rate_limit"


def test_an_expired_deadline_never_resolves_or_calls(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    monkeypatch.setattr(common, "require_inference",
                        lambda *a, **k: pytest.fail("a spent phase resolved"))
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake, deadline=time.monotonic() - 1)
    assert ev.plan.terminal == "skipped:deadline" and fake.calls == 0


def test_a_late_step_is_bounded_by_what_is_left(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    fake = FakeLLM([[plan_reply()]], stall=True)
    started = time.monotonic()
    ev = gather(cid, sid, fake, deadline=started + 0.6)
    assert time.monotonic() - started < history_recall.PLAN_CEILING
    assert ev.plan.terminal == "unusable" and ev.plan.steps[0].outcome == "timeout"


def test_the_step_cap_refuses_a_step(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    monkeypatch.setattr(history_recall, "MAX_PLAN_STEPS", 0)
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "skipped:step_cap" and fake.calls == 0


def test_planning_failure_degrades_to_the_first_evidence(cid, monkeypatch):
    sid, *_ = setup_thin(cid)

    async def broken(*a, **k):
        raise RuntimeError("private plan text")
    monkeypatch.setattr(history_recall, "plan_and_repair", broken)
    ev = gather(cid, sid, FakeLLM([[plan_reply()]]))
    assert ev.plan.terminal == "skipped:error" and ev.items


def test_a_group_round_plans_once(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    calls = []
    real = history_recall.plan_and_repair

    async def counted(*a, **k):
        calls.append(1)
        return await real(*a, **k)
    monkeypatch.setattr(history_recall, "plan_and_repair", counted)
    turn = character_turns._Progress({"id": "round-1", "note": ""}, (), None)
    fake = FakeLLM([[plan_reply()]])

    async def run():
        first = await character_turns._round_history(None, cid, sid, fake, None, None, turn,
                                                     "grimoire")
        again = await character_turns._round_history(None, cid, sid, fake, None, None, turn,
                                                     "grimoire")
        assert first is again and first.evidence.plan.terminal == "planned"
    asyncio.run(run())
    assert calls == [1] and fake.calls == 1


def test_reasons_never_reach_the_prompt_with_a_plan(cid):
    sid, *_ = setup_thin(cid)
    fake = FakeLLM([[plan_reply(terms=["lanterns", "ferryman", "zebrafishmarker"])]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "planned"
    messages, detail = assemble.compose_turn(cid, sid,
                                             history_view=epistemic.narrator_view(cid, sid, ev))
    sent = json.dumps(messages)
    assert "The ferryman counts the lanterns." in sent    # the planned evidence is there
    for text in (QUESTION, "zebrafishmarker", "planned", "s1", "history-query-plan",
                 "needs_history", "not_asked", "terminal"):
        assert text not in sent, text
    row = next(r for r in detail["sections"] if r["id"] == "history_recall")
    assert row["history"]["plan"]["terminal"] == "planned"
    assert row["history"]["plan"]["rounds"][0]["questions"] == [QUESTION]


def test_the_route_is_born_at_format_two_on_fast():
    route = routing.route("history-query-plan")
    assert (route.key, route.operation, route.default_role, route.legacy) == (
        "history_plan", "generate", "fast", routing.NO_LEGACY)
    assert route.campaign_scoped and "history-query-plan" in routing.NO_LEGACY_TASKS
    assert route not in routing.LEGACY_ROUTES
    assert routing.config_key("history_plan") not in routing.CONFIG_KEYS
    assert route not in routing.routes_for("campaign")
    assert history_recall.PLAN_MAX_TOKENS == 1024 and history_recall.PLAN_CEILING == 6
    assert history_recall.MAX_PLAN_STEPS == 3


def test_a_format_one_store_resolves_the_planner_through_fast(legacy_client):
    """No legacy key names `history_plan`: a store the migration has not
    reached resolves it through the Fast role, and a legacy `route_summary`
    pin -- made for summaries, before the planner existed -- never reaches it."""
    legacy_client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    plan = common.require_inference("history-query-plan", cid)
    tracker = common.require_inference("tracker-update", cid)
    assert plan.task == "history-query-plan" and plan.operation == "generate"
    assert plan.chain.primary.model == tracker.chain.primary.model
    store.config.write_config(route_summary="openai", route_summary_preset="")
    again = common.require_inference("history-query-plan", cid)
    assert again.chain.primary.provider_id == plan.chain.primary.provider_id


def _late(monkeypatch, calls=(1,)):
    """`_plan_step`, made to return only once the phase deadline has passed
    on the named calls (1 = the first plan, 2 = the repair)."""
    real = history_recall._plan_step
    seen = []

    async def late(*a, **k):
        got = await real(*a, **k)
        seen.append(1)
        if len(seen) in calls:
            await asyncio.sleep(max(0.0, k["deadline"] - time.monotonic()) + 0.01)
        return got
    monkeypatch.setattr(history_recall, "_plan_step", late)


def _spy_retrieve(monkeypatch):
    calls = []
    real = store.history.retrieve

    async def spy(*a, **k):
        calls.append(a[2])
        return await real(*a, **k)
    monkeypatch.setattr(store.history, "retrieve", spy)
    return calls


def test_a_round_never_starts_past_the_deadline(cid, monkeypatch):
    sid, *_ = setup_thin(cid)
    _late(monkeypatch)
    retrieved = _spy_retrieve(monkeypatch)
    ev = gather(cid, sid, FakeLLM([[plan_reply()]]), deadline=time.monotonic() + 1.5)
    assert ev.plan.terminal == "skipped:deadline" and len(retrieved) == 1
    assert ev.plan.rounds == () and ev.plan.steps[0].outcome == "ok"


def test_a_repair_round_never_starts_past_the_deadline(cid, monkeypatch):
    sid, *_ = setup_thin(cid, repair="on")
    _late(monkeypatch, calls=(2,))
    retrieved = _spy_retrieve(monkeypatch)
    check = json.dumps({"0": {"answers": {"sufficient": False}}})
    repair = plan_reply(questions=["Where did the gulls wheel?"], terms=["gulls", "overhead"])
    ev = gather(cid, sid, FakeLLM([[plan_reply()], [check], [repair]]),
                deadline=time.monotonic() + 2.5)
    assert ev.plan.terminal == "unrepaired" and len(retrieved) == 2
    assert len(ev.plan.rounds) == 1


def test_an_actor_perspective_is_refused_not_relabelled(cid):
    """Until 11-C1 classifies what an actor may know, planning an actor's
    query fails closed rather than planning it as the narrator."""
    sid, *_ = setup_thin(cid)
    turn = history_recall.Turn(model.Query(("Where?",), perspective="actor:characters:mara"),
                               query.catalog(cid, sid), 4, {})
    first = model.Evidence(coverage=model.Coverage("thin"))
    fake = FakeLLM([[plan_reply()]])
    with pytest.raises(ValueError):
        asyncio.run(history_recall.plan_and_repair(
            None, cid, sid, turn, first, client=fake, ceiling=1200,
            deadline=time.monotonic() + 5))
    assert fake.calls == 0


def test_an_actor_step_never_plans(cid):
    """11's NPC steps retrieve from their own perspective; the planner is
    shown the narrator's seeds, so an actor's thin retrieval is never
    planned -- the same rule that keeps the rerank off an actor's step."""
    from tests.history_fixtures import actor
    sid, *_ = setup_thin(cid)
    aid, _vid = actor(cid, sid, "Mara")
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake, actor_ref=f"characters:{aid}")
    assert fake.calls == 0 and ev.plan is None



def test_the_first_rerank_status_survives_the_merge(cid):
    """The first retrieval's paid rerank is reapplied over the merge, and its
    status stays on the merged coverage rather than reading `off`."""
    sid, *_ = setup_thin(cid)
    store.config.write_config(history_recall_rerank="on")
    grade = json.dumps({str(i): {"answers": {"relevance": 3}} for i in range(4)})
    fake = FakeLLM([[grade], [plan_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "planned"
    assert ev.coverage.rerank not in ("off", "")


def test_a_planned_step_counts_every_retrieval_and_embedding():
    from types import SimpleNamespace
    trace = model.PlanTrace("repaired", rounds=(model.RoundRecord(), model.RoundRecord()),
                            semantic=("ok", "failed:network", "skipped:deadline"))
    view = SimpleNamespace(evidence=model.Evidence(
        coverage=model.Coverage("thin", semantic="mixed"), plan=trace))
    assert history_recall.retrievals(view) == 3 and history_recall.embeds(view) == 2
    plain = SimpleNamespace(evidence=model.Evidence(coverage=model.Coverage("thin", semantic="ok")))
    assert history_recall.retrievals(plain) == 1 and history_recall.embeds(plain) == 1
    assert history_recall.retrievals(None) == 0 and history_recall.embeds(None) == 0


def test_the_trace_rides_a_frozen_history_view(cid):
    """A roll resume composes with the view stored on the response: the
    planning trace comes back with it."""
    sid, *_ = setup_thin(cid)
    ev = gather(cid, sid, FakeLLM([[plan_reply()]]))
    view = epistemic.narrator_view(cid, sid, ev)
    again = epistemic.from_record(json.loads(json.dumps(epistemic.to_record(view))))
    assert again.evidence.plan == ev.plan
    record = epistemic.to_record(view)
    del record["evidence"]["plan"]
    assert epistemic.from_record(record).evidence.plan is None



def test_a_retrieval_that_raises_still_traces_a_planned_phase(cid, monkeypatch):
    """With planning on, a first retrieval that raises is `not_run:error`,
    never a missing trace that reads as planning switched off -- and no
    planner call is made."""
    sid, *_ = setup_thin(cid)

    async def broken(*a, **k):
        raise OSError("unreadable archive")
    monkeypatch.setattr(store.history, "retrieve", broken)
    fake = FakeLLM([[plan_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.coverage.verdict == "error" and ev.plan.terminal == "not_run:error"
    assert fake.calls == 0
    store.config.write_config(history_plan="off")
    assert gather(cid, sid, fake).plan is None
