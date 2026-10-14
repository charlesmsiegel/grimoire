"""Spec 11 §8.1's offline leakage suite runs inside `pytest backend`.

Deterministic and model-free: every forbidden needle absent from every
message of the reader's prompt, every allowed one present, and a placed one
in its section only. It does not exercise the Decision stage."""

import pytest

from evals.epistemic import cases, runner


def test_the_corpus_covers_the_offline_cases():
    ids = {c.id.rstrip("bc") for c in cases.CASES}
    # Every case of spec 11 §8.1: 7 and 15 (and 15b) settle offline as
    # withheld, and their Decision answers are the live gate's (11-S6).
    assert ids == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13",
                   "14", "15"}


@pytest.mark.parametrize("case", cases.CASES, ids=[c.id for c in cases.CASES])
def test_no_case_leaks(monkeypatch, case):
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    records = runner.run_case(case)
    assert records
    failed = [r for r in records if not runner.passed(r)]
    assert not failed, failed


def test_the_grader_catches_a_leak():
    from evals.epistemic import graders
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "A NEEDLE."}]
    assert graders.leaks(messages, ["NEEDLE", "OTHER"]) == ["NEEDLE"]
    assert graders.missing(messages, ["NEEDLE", "OTHER"]) == ["OTHER"]
    assert graders.misplaced({"a": "NEEDLE", "b": "NEEDLE"}, {"NEEDLE": "a"})
