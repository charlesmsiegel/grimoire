"""Roadmap 02-S7: the epistemic-access decide kit (02-C5a), for 11-C1.

A kit, not a route: it names no task in a `decide()` call, and its eval case
is held outside `CASES` until 11's call site lands the route."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grimoire import decisions
from grimoire.decisions import Answer, Decision, Escalation, ItemResult
from grimoire.store import epistemic_access as ea
from grimoire.store import routing
from tests import test_task_policy as tp

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402

Q = ea.AccessQuestion("characters:mara", "Mara", "ev1", "Seraphine took the key.",
                      ("present", "asleep"))
P = ea.AccessQuestion("characters:winifred", "Winifred", "ev1", "Seraphine took the key.")


def _result(answer, reason="", detail=""):
    return ItemResult({ea.ACCESS_ID: Answer(answer, reason, detail=detail)})


def _decision(*results, escalations=()):
    return Decision(items=tuple(results), backend="structured", escalations=escalations)


def test_one_item_per_pair_with_the_classes_and_a_null():
    items = ea.build_items([Q, P])
    assert len(items) == 2
    (question,) = items[0].questions
    assert question.id == ea.ACCESS_ID and question.allow_none
    assert [o.id for o in question.options] == list(ea.CLASSES)
    assert "Mara" in items[0].context and "present, asleep" in items[0].context
    assert "What the record already says" not in items[1].context
    decisions.validate(list(items))     # nothing a caller could pass is refused


@pytest.mark.parametrize("answer,reason,detail", [
    (None, "abstained", ""), (None, "refused", ""), (None, "error", ""),
    (None, "unreadable", ""), (None, "unreadable", decisions.NOT_AN_OPTION),
    (None, "unreadable", decisions.NO_ITEM), (None, "unreadable", decisions.NO_OBJECT),
])
def test_every_failure_withholds(answer, reason, detail):
    mapped = ea.access_of([Q], _decision(_result(answer, reason, detail)))
    assert mapped == {("characters:mara", "ev1"): ea.UNKNOWN}


def test_a_read_class_stands():
    for access in ea.CLASSES:
        assert ea.access_of([Q], _decision(_result(access)))[("characters:mara", "ev1")] \
            == access


def test_an_item_the_reply_never_reached_withholds():
    assert ea.access_of([Q, P], _decision(_result("known")))[("characters:winifred", "ev1")] \
        == ea.UNKNOWN


@pytest.mark.parametrize("outcome", ["failed", "skipped"])
def test_a_low_margin_known_whose_hop_did_not_answer_withholds(outcome):
    base = _result("known")
    hop = Escalation(index=0, trigger="low_margin", margin=0.05, before=base,
                     outcome=outcome, detail="timeout")
    assert ea.access_of([Q], _decision(base, escalations=(hop,))) == {
        ("characters:mara", "ev1"): ea.UNKNOWN}


def test_a_hop_that_answered_stands():
    base = _result("narrator_only")
    hop = Escalation(index=0, trigger="refused", margin=None, before=_result(None, "refused"),
                     outcome="answered")
    assert ea.access_of([Q], _decision(base, escalations=(hop,))) == {
        ("characters:mara", "ev1"): "narrator_only"}


def test_a_pair_asked_twice_keeps_the_withholding_reading():
    mapped = ea.access_of([Q, Q], _decision(_result("known"), _result(None, "refused")))
    assert mapped == {("characters:mara", "ev1"): ea.UNKNOWN}


@pytest.mark.parametrize("first,second,kept", [
    ("narrator_only", "known", "narrator_only"),
    ("known", "narrator_only", "narrator_only"),
    ("experienced", "suspected", "suspected"),
    ("known", "experienced", "known"),
])
def test_a_pair_asked_twice_keeps_the_more_withholding_class(first, second, kept):
    mapped = ea.access_of([Q, Q], _decision(_result(first), _result(second)))
    assert mapped == {("characters:mara", "ev1"): kept}
    assert set(ea.WITHHOLDING) == {*ea.CLASSES, ea.UNKNOWN}


def test_escalation_fires_only_on_permissive_answers():
    """01d-C2a's answer filter, through the real trigger evaluation: a
    low-margin `narrator_only` is already safe and is never escalated."""
    def native(answer, dist):
        return ItemResult({ea.ACCESS_ID: Answer(answer, distribution=dist)},
                          backend="native", served=("openrouter", "openrouter", "m"))
    close = {"known": 0.45, "narrator_only": 0.4, "suspected": 0.15}
    safe = {"narrator_only": 0.45, "known": 0.4, "suspected": 0.15}
    p = ea.policy()
    fired = decisions.triggers([native("known", close), native("narrator_only", safe)],
                               question=p.question, escalate_on=p.escalate_on,
                               margins=dict(p.margins), answers=p.escalate_answers)
    assert [t.index for t in fired] == [0]


def test_the_candidate_policy_row_breaks_no_rule(monkeypatch):
    """02-C5a's candidate escalation row, checked against 01d's rules over the
    route 11-S6 landed -- never sampled, escalated only on refusal or a
    permissive low margin. Not registered: the task landed with escalation
    off, as every task does."""
    route = ea.ROUTE
    assert route is not None and route.tasks == (ea.TASK,)
    assert route.legacy == routing.NO_LEGACY and route.operation == "decide"
    assert route.key == "epistemic" and route.default_role == "decision"
    p = ea.policy()
    assert not p.samples and "abstained" not in p.escalate_on
    assert set(p.escalate_answers) == set(ea.PERMISSIVE) < set(ea.CLASSES)
    assert tp.violations({ea.TASK: p}) == []


def test_the_call_site_landed_the_route_and_the_case():
    """11-S6: the route, an escalation-off policy row and the registered eval
    case land with the call site (`routes/epistemic.py`)."""
    assert routing.TASK_ROUTE[ea.TASK] == "epistemic"
    assert routing.TASK_POLICY[ea.TASK] == routing.TaskPolicy()
    assert ea.TASK in {c.task for c in case_mod.CASES}
    assert not case_mod.KIT_CASES


CASE = next(c for c in case_mod.CASES if c.id == "decide-epistemic-access")


@pytest.mark.parametrize("recording", CASE.recordings, ids=lambda r: r.variant)
def test_the_unregistered_case_replays(monkeypatch, tmp_path, recording):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(CASE, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(f"{c.name}: {c.detail}" for c in result.failures)
