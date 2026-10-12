"""Roadmap 02-S7: the history-relevance decide kit (02-C5b), for 09 and 10.

Ungraded is never a low grade, and the kit lands no route."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grimoire import decisions
from grimoire.decisions import Answer, ItemResult, Ranking
from grimoire.store import history_rerank as hr
from grimoire.store import routing

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402

A = hr.Candidate("scene-1", "Mara hides the ledger.", "spring")
B = hr.Candidate("scene-2", "The harbour floods.")


def _result(answer, reason="", distribution=None):
    return ItemResult({hr.RELEVANCE_ID: Answer(answer, reason, distribution=distribution)})


def test_one_scored_item_per_candidate():
    items = hr.build_items("Where is the ledger?", [A, B])
    assert [q.id for item in items for q in item.questions] == [hr.RELEVANCE_ID] * 2
    assert items[0].questions[0].levels == hr.LEVELS
    assert "spring" in items[0].context and "When it happened" not in items[1].context
    decisions.validate(list(items))


@pytest.mark.parametrize("answer,reason", [(None, "abstained"), (None, "refused"),
                                           (None, "unreadable"), (None, "error")])
def test_ungraded_is_none_never_zero(answer, reason):
    assert hr.grades_of([A], [_result(answer, reason)]) == {"scene-1": hr.UNGRADED}
    assert hr.UNGRADED.level is None


def test_a_level_reads_with_its_reported_distribution_only():
    dist = {"0": 0.1, "1": 0.1, "2": 0.2, "3": 0.6}
    grades = hr.grades_of([A, B], [_result(3, distribution=dist), _result(0)])
    assert grades["scene-1"] == hr.Grade(3, dist)
    assert grades["scene-2"] == hr.Grade(0, None)


def test_an_item_the_reply_never_reached_is_ungraded():
    assert hr.grades_of([A, B], [_result(2)])["scene-2"] == hr.UNGRADED


def test_the_ranked_form_orders_up_to_a_chunk_and_never_ranks_the_unranked_last():
    item = hr.build_items_ranked("Where is the ledger?", [A, B])
    (rank,) = item.questions
    assert rank.id == hr.RANK_ID and rank.pointwise
    assert [o.id for o in rank.candidates] == ["scene-1", "scene-2"]
    decisions.validate([item])
    read = ItemResult({hr.RANK_ID: Answer(Ranking((("scene-1",),), rest=("scene-2",)))})
    assert hr.positions_of([A, B], read) == {"scene-1": 0, "scene-2": None}
    unread = ItemResult({hr.RANK_ID: Answer(None, "unreadable")})
    assert hr.positions_of([A, B], unread) == {"scene-1": None, "scene-2": None}
    with pytest.raises(ValueError):
        hr.build_items_ranked("q", [A])
    with pytest.raises(ValueError):
        hr.build_items_ranked("q", [hr.Candidate(f"s{i}", "x") for i in range(9)])


def test_the_kit_lands_no_route():
    assert hr.TASK not in routing.TASK_ROUTE
    assert hr.TASK not in routing.TASK_POLICY
    assert hr.TASK not in {c.task for c in case_mod.CASES}
    assert hr.ROUTE.key not in {r.key for r in routing.ROUTES}
    assert (hr.ROUTE.tasks, hr.ROUTE.operation, hr.ROUTE.default_role, hr.ROUTE.legacy) == (
        (hr.TASK,), "decide", "decision", routing.NO_LEGACY)


CASE = next(c for c in case_mod.KIT_CASES if c.id == "decide-history-rerank")


@pytest.mark.parametrize("recording", CASE.recordings, ids=lambda r: r.variant)
def test_the_unregistered_case_replays(monkeypatch, tmp_path, recording):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(CASE, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(f"{c.name}: {c.detail}" for c in result.failures)
