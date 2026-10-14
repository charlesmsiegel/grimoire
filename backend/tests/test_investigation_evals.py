"""Roadmap 12-S7: the adoption gate's offline arms and graders run in
`pytest backend` (spec 10; 14 "Eval").

The investigation arm's model is a canned script: these prove the loop, the
final-call checks and the graders over the synthetic corpus, never a model's
quality. The live arms are `evals/run.py --investigate MODE --live`.
"""

from __future__ import annotations

import pytest

from evals import run as eval_run
from evals.investigation import cases, graders, runner


@pytest.fixture(scope="module")
def records():
    _results, found = runner.run_all()
    return found


def test_every_built_entry_point_runs_both_arms_over_the_corpus(records):
    seen = {(r["case"], r["arm"]) for r in records}
    for case in cases.CASES:
        for arm in cases.ARMS[case.mode]:
            assert (case.id, arm) in seen
    assert not [r for r in records if r["error"]]


def test_the_scripted_investigation_is_correct_and_the_offline_incumbent_abstains(records):
    for r in records:
        if r["arm"] == "investigation":
            assert r["grade"] == graders.CORRECT, r
            assert r["usage"]["model_turns"] >= 2
        else:
            assert r["grade"] == graders.ABSTAINED, r


def test_no_leakage_line_fires_and_the_hidden_route_is_clean(records):
    assert all(not r["text_leaks"] and not r["steered"] for r in records)
    hidden = [r for r in records if r["case"] == "unnamed-debt-resolution"]
    assert hidden and all(r["hidden_route"] is True for r in hidden)


def test_the_gate_reads_per_entry_point_and_e1_is_absent(records):
    verdict = runner.gate(records)
    assert verdict == {"rp": False, "continuity": True, "question": True}
    assert "absent until 12-S6" in runner.summary(records, live=False)


def test_the_gate_fails_without_the_margin(records):
    worse = [{**r, "grade": graders.ABSTAINED} if r["arm"] == "investigation" else r
             for r in records]
    assert runner.gate(worse)["continuity"] is False
    leaky = [{**r, "steered": ["scene:x"]} if r["arm"] == "investigation" else r
             for r in records]
    assert runner.gate(leaky)["question"] is False


# ---- the graders, on planted input ----
def test_a_proposal_is_graded_with_uncertain_as_an_abstention():
    gold = {"s3"}
    assert graders.proposal("fulfilled", gold, None) == graders.ABSTAINED
    assert graders.proposal("fulfilled", gold, {"decision": "uncertain"}) == graders.ABSTAINED
    assert graders.proposal("fulfilled", gold,
                            {"decision": "fulfilled", "evidence_scenes": ["s3"]}) == graders.CORRECT
    assert graders.proposal("fulfilled", gold,
                            {"decision": "fulfilled", "evidence_scenes": ["s1"]}) == graders.WRONG
    assert graders.proposal("fulfilled", gold,
                            {"decision": "broken", "evidence_scenes": ["s3"]}) == graders.WRONG


def test_an_answer_needs_its_needle_and_valid_citations():
    gold = {"s3"}
    assert graders.answer(("compass",), gold, {"text": "The compass came back.",
                                               "citations": ["scene:s3"]}) == (graders.CORRECT, True)
    assert graders.answer(("compass",), gold, {"text": "The compass came back.",
                                               "citations": ["scene:s1"]}) == (graders.WRONG, False)
    assert graders.answer(("compass",), gold, None) == (graders.ABSTAINED, True)


def _step(query, refs):
    return {"turn": 1, "tool": "search_history", "args": {"query": query},
            "result": {"ok": True, "refs": refs, "chars": 1, "truncated": False}}


def test_a_selection_only_a_forbidden_word_led_to_is_steered():
    trace = {"steps": [_step("quittance", ["scene:s2"])]}
    assert graders.steered(trace, ["s2"], ("quittance",)) == ["scene:s2"]
    also = {"steps": [_step("quittance", ["scene:s2"]), _step("boatman coins", ["scene:s2"])]}
    assert graders.steered(also, ["s2"], ("quittance",)) == []
    assert graders.steered(trace, ["s2"], ()) == []
    assert graders.text_leaks(["the quittance note"], ("Quittance",)) == ["Quittance"]


def test_the_cli_refuses_e1_until_it_lands(capsys):
    assert eval_run.main(["--investigate", "rp"]) == 1
    assert "absent until 12-S6" in capsys.readouterr().out
