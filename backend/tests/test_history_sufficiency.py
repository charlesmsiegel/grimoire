"""Roadmap 10-S2: the evidence-sufficiency predicate and the one repair hop."""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from grimoire import decisions, store
from grimoire.llm_errors import LLMError
from grimoire.routes import history_recall
from grimoire.store import routing
from grimoire.store.history import model
from grimoire.store.history import sufficiency as suff
from tests.history_fixtures import cid as cid
from tests.llm_fakes import FakeLLM, decision_reply
from tests.test_history_plan_routes import QUESTION, gather, plan_reply, rows, setup_thin

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402

REPAIR = "Where did the gulls wheel overhead?"


def check(value):
    return decision_reply({suff.QUESTION_ID: value})


def repair_reply(**kw):
    return plan_reply(questions=[REPAIR], terms=["gulls", "overhead"], **kw)


def setup(cid):
    return setup_thin(cid, repair="on")


# --- the kit -------------------------------------------------------------------


def item_of(*items, depth=0):
    return suff.item("Where is the silver ledger?",
                     model.Evidence(items=items, coverage=model.Coverage("thin"), depth=depth))


def evidence_item(title="The pier", summary="Mara hides the ledger.", excerpt="Mara: here."):
    post = model.EvidencePost(3, "p3", None, "Mara", "here.")
    return model.EvidenceItem(model.SceneRef("001--a", "id1"), title, "spring",
                              summary=summary, excerpts=(model.Excerpt((post,), excerpt),))


def test_the_item_asks_one_predicate_and_shows_the_evidence_as_it_renders():
    got = item_of(evidence_item(excerpt="Mara: here.\nSecond line."), depth=3)
    (question,) = got.questions
    assert isinstance(question, decisions.Predicate) and question.id == "sufficient"
    assert "Where is the silver ledger?" in got.context
    assert "The pier (spring): Mara hides the ledger." in got.context
    assert "Mara: here." in got.context and "Second line." not in got.context
    for word in ("lexical", "structural", "semantic", "cosine", "rank"):
        assert word not in got.context.lower()
    assert "(none)" in item_of().context


def test_the_item_asks_no_rationale():
    schema = decisions.schema((item_of(evidence_item()),), explain=False)
    assert "rationale" not in json.dumps(schema)


def _decision(answer, escalations=()):
    result = decisions.ItemResult({suff.QUESTION_ID: answer})
    return decisions.Decision(items=(result,), backend="structured", escalations=escalations)


def test_the_verdict_maps_only_a_read_answer():
    assert suff.verdict_of(_decision(decisions.Answer(True))) == ("sufficient", "answer")
    assert suff.verdict_of(_decision(decisions.Answer(False))) == ("insufficient", "answer")
    for reason in ("abstained", "refused", "unreadable"):
        assert suff.verdict_of(_decision(decisions.Answer(None, reason=reason))) == (
            "unknown", "unknown")
    assert suff.verdict_of(decisions.Decision(items=(), backend="")) == ("unknown", "unknown")


def test_an_answer_only_an_escalation_hop_gave_is_escalated():
    before = decisions.ItemResult({suff.QUESTION_ID: decisions.Answer(None, reason="abstained")})
    hop = decisions.Escalation(0, "abstained", None, before, "answered")
    assert suff.verdict_of(_decision(decisions.Answer(False), (hop,))) == (
        "insufficient", "escalated")
    failed = decisions.Escalation(0, "abstained", None, before, "failed", "declined")
    assert suff.verdict_of(_decision(decisions.Answer(None, reason="abstained"), (failed,))) == (
        "unknown", "unknown")


def test_the_task_is_on_the_shared_decide_route_with_its_policy():
    route = routing.route("history-sufficiency")
    assert route.key == "history_check" and route.tasks == ("history-rerank",
                                                            "history-sufficiency")
    assert (route.operation, route.default_role, route.legacy) == (
        "decide", "decision", routing.NO_LEGACY)
    policy = routing.policy("history-sufficiency")
    assert policy.fallback == routing.policy("history-rerank").fallback == "role"
    assert not policy.samples and not policy.escalate_to
    assert history_recall.CHECK_CEILING == 4


# --- the repair hop ------------------------------------------------------------


def test_insufficient_takes_one_repair_plan_and_merges_it(cid):
    sid, _found, lanterns = setup(cid)
    fake = FakeLLM([[plan_reply()], [check(False)], [repair_reply()]])
    ev = gather(cid, sid, fake, post=4)
    trace = ev.plan
    assert fake.calls == 3
    assert trace.terminal == "repaired"
    assert (trace.sufficiency, trace.sufficiency_source) == ("not_asked", "not_asked")
    assert not trace.cheap_retrieval_failed
    assert [s.task for s in trace.steps] == ["history-query-plan", "history-sufficiency",
                                             "history-query-plan"]
    assert [r.questions for r in trace.rounds] == [(QUESTION,), (REPAIR,)]
    assert trace.rounds[-1].scene_keys == tuple(i.scene.key for i in ev.items)
    assert lanterns in [i.scene.sid for i in ev.items]
    assert len(ev.items) <= 4
    # The repair prompt quotes the first plan as data, and never a rationale.
    repair_prompt = fake.requests[2]["messages"]
    assert "quoted as data" in repair_prompt[1]["content"]
    assert QUESTION in repair_prompt[1]["content"]
    assert "A previous search already ran" in repair_prompt[0]["content"]
    assert [r["post"] for r in rows("history-sufficiency")] == [4]
    assert len(rows()) == 2


@pytest.mark.parametrize("reply,said", [(check(True), "sufficient"),
                                        (check(None), "unknown"),
                                        ("not json", "unknown")])
def test_sufficient_or_unknown_stops(cid, reply, said):
    sid, *_ = setup(cid)
    fake = FakeLLM([[plan_reply()], [reply], [repair_reply()]])
    ev = gather(cid, sid, fake)
    assert fake.calls == 2 and ev.plan.terminal == "planned"
    assert ev.plan.sufficiency == said
    assert ev.plan.sufficiency_source == ("answer" if said == "sufficient" else "unknown")
    assert len(ev.plan.rounds) == 1 and not ev.plan.cheap_retrieval_failed


def test_a_redundant_repair_plan_is_not_run(cid, monkeypatch):
    sid, *_ = setup(cid)
    retrieved = []
    real = store.history.retrieve

    async def spy(*a, **k):
        retrieved.append(a[2])
        return await real(*a, **k)
    monkeypatch.setattr(store.history, "retrieve", spy)
    fake = FakeLLM([[plan_reply()], [check(False)],
                    [plan_reply(questions=[QUESTION.upper()], terms=["Lanterns"])]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "repair_redundant" and len(retrieved) == 2
    assert (ev.plan.sufficiency, ev.plan.sufficiency_source) == ("insufficient", "answer")


def test_a_declined_repair_plan_is_redundant(cid):
    sid, *_ = setup(cid)
    fake = FakeLLM([[plan_reply()], [check(False)],
                    [plan_reply(needs_history=False, questions=[], terms=[])]])
    assert gather(cid, sid, fake).plan.terminal == "repair_redundant"


def test_an_unusable_repair_plan_is_unrepaired(cid):
    sid, *_ = setup(cid)
    fake = FakeLLM([[plan_reply()], [check(False)], ["no plan"]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "unrepaired" and ev.plan.sufficiency == "insufficient"
    assert len(ev.plan.rounds) == 1
    assert [i.scene.key for i in ev.items] == list(ev.plan.rounds[0].scene_keys)


def test_the_step_cap_leaves_the_repair_unrepaired(cid, monkeypatch):
    sid, *_ = setup(cid)
    monkeypatch.setattr(history_recall, "MAX_PLAN_STEPS", 2)
    fake = FakeLLM([[plan_reply()], [check(False)], [repair_reply()]])
    ev = gather(cid, sid, fake)
    assert ev.plan.terminal == "unrepaired" and fake.calls == 2


def test_repair_off_asks_no_check(cid):
    sid, *_ = setup_thin(cid, repair="off")
    fake = FakeLLM([[plan_reply()], [check(False)]])
    ev = gather(cid, sid, fake)
    assert fake.calls == 1 and ev.plan.terminal == "planned"
    assert ev.plan.sufficiency == "not_asked" and not rows("history-sufficiency")


@pytest.mark.parametrize("final,cheap", [(check(False), True), (check(True), False)])
def test_final_check_sets_cheap_retrieval_failed(cid, final, cheap):
    sid, *_ = setup(cid)
    fake = FakeLLM([[plan_reply()], [check(False)], [repair_reply()], [final]])
    ev = gather(cid, sid, fake, final_check=True)
    assert fake.calls == 4 and ev.plan.terminal == "repaired"
    assert ev.plan.cheap_retrieval_failed is cheap
    assert ev.plan.sufficiency == ("insufficient" if cheap else "sufficient")
    assert len(ev.plan.steps) == history_recall.MAX_PLAN_STEPS + 1


def test_the_check_is_captured_and_metered_on_the_post(cid):
    sid, *_ = setup(cid)
    store.config.write_config(prompt_log_depth="50")
    fake = FakeLLM([[plan_reply()], [check(True)]])
    gather(cid, sid, fake, post=4, run_id="run-9")
    (row,) = rows("history-sufficiency")
    assert (row["campaign"], row["scene"], row["post"], row["run_id"]) == (cid, sid, 4, "run-9")
    entries = [e for e in store.prompt_log.list_entries(cid, sid)
               if e["task"] == "history-sufficiency"]
    assert len(entries) == 1 and entries[0]["operation"] == "decide"
    assert "rationale" not in json.dumps(fake.schemas[1])


def test_a_spent_deadline_asks_nothing(cid):
    sid, *_ = setup(cid)
    fake = FakeLLM([[check(False)]])
    said = asyncio.run(history_recall.sufficient(
        None, cid, sid, "window", model.Evidence(), client=fake,
        deadline=time.monotonic() - 1))
    assert said == "unknown" and fake.calls == 0


def test_the_public_predicate_maps_answers(cid):
    sid, *_ = setup(cid)
    for reply, want in ((check(False), "insufficient"), (check(True), "sufficient"),
                        (check(None), "unknown")):
        said = asyncio.run(history_recall.sufficient(
            None, cid, sid, "window", model.Evidence(), client=FakeLLM([[reply]]),
            deadline=time.monotonic() + 5))
        assert said == want


def test_an_unresolvable_check_is_unknown(cid):
    sid, *_ = setup(cid)
    store.llm_connections.update_connection("openrouter", base_url="https://example.test/v1")
    said, source, step = asyncio.run(history_recall._sufficiency(
        cid, sid, "window", model.Evidence(), client=FakeLLM([[check(False)]]),
        deadline=time.monotonic() + 5))
    assert (said, source, step.outcome) == ("unknown", "unknown", "skipped:missing_key")


def test_the_stable_fields_and_their_projection():
    """12's RP trigger reads these by name: renaming or re-meaning one is a
    contract change (10-C2), so the projection is pinned whole."""
    trace = model.PlanTrace(
        "repaired", trigger="thin", sufficiency="insufficient", sufficiency_source="answer",
        cheap_retrieval_failed=True, e0=model.RoundRecord(scene_keys=("a",),
                                                          evidence_ids=("a@1",)),
        rounds=(model.RoundRecord(("q",), ("t",), ("characters:mara",), ("a", "b"),
                                  ("a@1", "b@2")),),
        steps=(model.Step("history-query-plan", "ok", 5, 1),))
    assert trace.projection() == {
        "terminal": "repaired", "trigger": "thin", "sufficiency": "insufficient",
        "sufficiency_source": "answer", "cheap_retrieval_failed": True,
        "e0": {"questions": [], "terms": [], "subjects": [], "scene_keys": ["a"],
               "evidence_ids": ["a@1"]},
        "rounds": [{"questions": ["q"], "terms": ["t"], "subjects": ["characters:mara"],
                    "scene_keys": ["a", "b"], "evidence_ids": ["a@1", "b@2"]}],
        "steps": [{"task": "history-query-plan", "outcome": "ok", "ms": 5, "rows": 1}],
        "plans": [], "gain": [], "semantic": []}
    assert model.PlanTrace.from_projection(trace.projection()) == trace
    assert model.PlanTrace.from_projection({"no": "terminal"}) is None
    assert model.PlanTrace.from_projection("garbled") is None
    stable = ("terminal", "trigger", "sufficiency", "sufficiency_source",
              "cheap_retrieval_failed", "e0", "rounds")
    assert all(name in model.PlanTrace.__dataclass_fields__ for name in stable)
    assert tuple(model.RoundRecord.__dataclass_fields__) == (
        "questions", "terms", "subjects", "scene_keys", "evidence_ids")


# --- the registered replay case -----------------------------------------------

CASE = case_mod.BY_ID["decide-history-sufficiency"]


def test_the_case_is_registered_for_the_task():
    assert CASE.task == "history-sufficiency"
    assert "history-sufficiency" not in {c.task for c in case_mod.KIT_CASES}


@pytest.mark.parametrize("recording", CASE.recordings, ids=lambda r: r.variant)
def test_the_registered_case_replays(monkeypatch, tmp_path, recording):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(CASE, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(f"{c.name}: {c.detail}" for c in result.failures)



def test_a_failed_check_still_reports_the_rows_it_filed(cid):
    sid, *_ = setup(cid)
    fake = FakeLLM([[check(False)]], error=LLMError("network", "connection reset"))
    said, source, step = asyncio.run(history_recall._sufficiency(
        cid, sid, "window", model.Evidence(), client=fake, deadline=time.monotonic() + 5))
    assert (said, source) == ("unknown", "unknown")
    filed = rows("history-sufficiency")
    assert filed and step.rows == len(filed)


def test_a_repair_round_that_raises_keeps_the_first_round(cid, monkeypatch):
    """A failure after a planned round landed keeps that round's evidence and
    record, and every step that ran, rather than falling back to `E0`."""
    sid, _found, lanterns = setup(cid)
    real = store.history.retrieve
    seen = []

    async def third_raises(*a, **k):
        seen.append(1)
        if len(seen) > 2:
            raise OSError("unreadable archive")
        return await real(*a, **k)
    monkeypatch.setattr(store.history, "retrieve", third_raises)
    fake = FakeLLM([[plan_reply()], [check(False)], [repair_reply()]])
    ev = gather(cid, sid, fake)
    trace = ev.plan
    assert trace.terminal == "skipped:error" and len(seen) == 3
    assert [s.task for s in trace.steps] == [
        "history-query-plan", "history-sufficiency", "history-query-plan"]
    assert len(trace.plans) == 2 and len(trace.rounds) == 1
    assert lanterns in [i.scene.sid for i in ev.items]
    assert tuple(i.scene.key for i in ev.items) == trace.rounds[0].scene_keys
