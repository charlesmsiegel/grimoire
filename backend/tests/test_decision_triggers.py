"""Trigger evaluation (roadmap 01d-S2; spec 01d §5.1, §6.1, §9).

`decisions.answer_key`, `decisions.margin` and `decisions.triggers` are pure:
they read what a backend reported on an answer and which server stamped an
item (`ItemResult.served`), and they need no store and no client. The answers
are built the way the backends build them -- `decisions.native_answer`, the
structured parser, and an OpenAI-shaped decisions body read through
`openai_compatible`'s reader -- or by hand where only a reported value is
the subject. Values are dyadic wherever a test compares with `==`, so the
comparison is exact.

Invented option ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import pytest

from grimoire import decisions, openai_compatible
from grimoire.decisions import (
    NO_ITEM,
    NONE_KEY,
    NOT_AN_OPTION,
    TRIGGERS,
    Answer,
    Choice,
    Item,
    ItemResult,
    Option,
    Pair,
    Predicate,
    Ranking,
    Trigger,
    joint_key,
    margin,
    triggers,
)
from grimoire.store import routing

KNOWN = Choice("q", "What does Mara know?",
               (Option("known", "She knows it"), Option("narrator_only", "Only the narrator")))
AB = Choice("q", "Which?", (Option("a", "A"), Option("b", "B")), allow_none=True)
OVER = Predicate("q", "Is the scene over?")


def _result(answer: Answer, kind: str = "openrouter", backend: str = "native") -> ItemResult:
    served: tuple[()] | tuple[str, str, str] = (kind, "p", "m") if kind else ()
    return ItemResult({"q": answer}, backend=backend, served=served)


def _p(probability: float) -> Answer:
    return decisions.native_answer(OVER, probability=probability)


def _found(results, *, margins=None, escalate_on=TRIGGERS, answers=()):
    return triggers(results, question="q", escalate_on=escalate_on,
                    margins={"openrouter": 0.5} if margins is None else margins,
                    answers=answers)


def _fields(found) -> list[tuple]:
    return [(t.index, t.trigger, t.margin) for t in found]


# ---- answer_key ----
def test_answer_key_spells_each_answer_as_a_record_does():
    assert decisions.answer_key(Answer(True)) == "true"
    assert decisions.answer_key(Answer(False)) == "false"
    assert decisions.answer_key(Answer(2)) == "2"
    assert decisions.answer_key(Answer("characters:mara")) == "characters:mara"
    assert (decisions.answer_key(Answer(Pair("strike", "characters:mara")))
            == joint_key("strike", "characters:mara"))
    assert decisions.answer_key(Answer(None, "abstained")) is None
    assert decisions.answer_key(Answer(Ranking(tiers=(("a",),)))) is None
    assert decisions.answer_key(Answer(("a",))) is None


# ---- margin ----
def test_margin_is_a_float():
    assert margin(Answer(True, probability=1)) == 1.0
    assert type(margin(Answer(True, probability=1))) is float
    assert type(margin(Answer("a", distribution={"a": 1}))) is float


def test_margin_of_a_full_report_is_the_answers_lead_over_its_rival():
    assert margin(Answer("a", distribution={"a": 0.625, "b": 0.25, "c": 0.125})) == 0.375


def test_margin_over_a_mass_above_one_is_normalised():
    assert margin(Answer("a", distribution={"a": 0.75, "b": 0.5})) == pytest.approx(0.2)


def test_margin_of_a_partial_report_assumes_the_missing_mass_is_a_rival():
    assert margin(Answer("a", distribution={"a": 0.6})) == pytest.approx(0.2)
    assert margin(Answer("a", distribution={"a": 0.5, "b": 0.25})) == 0.25
    assert margin(Answer("a", distribution={"a": 0.375, "b": 0.5})) == -0.125


def test_margin_is_negative_when_the_report_ranks_a_rival_first():
    """Review B1: an endpoint's explicit choice is the answer whatever its
    report says, so the margin is measured from it, not from the top."""
    answer = decisions.native_answer(KNOWN, chosen="known",
                                     distribution={"known": 0.1, "narrator_only": 0.9})
    assert answer.answer == "known"
    assert margin(answer) == pytest.approx(-0.8)


def test_margin_of_an_answer_absent_from_its_report_is_negative():
    assert margin(Answer("a", distribution={"b": 1.0})) == -1.0
    assert margin(Answer("a", distribution={"b": 0.5})) == -0.5


def test_margin_of_a_predicate_both_ways():
    assert _p(0.75).answer is True and margin(_p(0.75)) == 0.5
    assert _p(0.25).answer is False and margin(_p(0.25)) == 0.5
    # A chosen True its own report doubts.
    assert margin(Answer(True, probability=0.25)) == -0.5


def test_margin_counts_the_reserved_none_as_a_rival():
    assert margin(Answer("a", distribution={"a": 0.5, NONE_KEY: 0.5})) == 0.0


def test_margin_of_a_score_counts_adjacent_levels_as_rivals():
    """Spec M6: a 2-versus-3 split is unsure, whatever their distance."""
    assert margin(Answer(2, distribution={"1": 0.25, "2": 0.375, "3": 0.375})) == 0.0


def test_margin_of_a_joint_reads_the_pairs_key():
    pair = Pair("strike", "characters:mara")
    other = joint_key("heal", "characters:winifred")
    assert margin(Answer(pair, distribution={pair.key: 0.75, other: 0.25})) == 0.5


def test_margin_reads_raw_keys_never_regrouped():
    """§11 Q1: two candidates splitting the mass is ambiguity about which."""
    answer = Answer("existing:a1", distribution={"existing:a1": 0.5, "existing:b2": 0.5})
    assert margin(answer) == 0.0


def test_no_margin_without_a_report():
    for answer in (Answer(True), Answer("a"), Answer(2),
                   Answer("a", distribution={}),
                   Answer(True, probability=None),
                   Answer(None, "abstained", distribution={"a": 0.5, "b": 0.5}),
                   Answer(Ranking(tiers=(("a",), ("b",))), marginals={"a": 0.9, "b": 0.1}),
                   Answer(("a",), marginals={"a": 0.9, "b": 0.1})):
        assert margin(answer) is None, answer


@pytest.mark.parametrize("bad", [float("-inf"), float("inf"), float("nan"), -0.25, 1.5, True])
def test_no_margin_from_a_value_that_is_not_a_probability(bad):
    """`native_answer` never keeps such a report; a hand-built one gets no
    margin, so `triggers` -- and `Trigger`'s finite-margin rule -- never
    meets one."""
    assert margin(Answer(True, probability=bad)) is None
    assert margin(Answer("a", distribution={"a": 0.5, "b": bad})) is None
    assert margin(Answer("a", distribution={"a": bad})) is None
    results = [_result(Answer(True, probability=bad)),
               _result(Answer("a", distribution={"a": bad, "b": 0.25}))]
    assert _found(results) == ()


# ---- triggers ----
def test_the_trigger_vocabulary_is_routings():
    """One vocabulary, spelled in two leaves: `routing` (the policy, 01d-S1)
    and `decisions` (which may not import it)."""
    assert routing.TRIGGERS == decisions.TRIGGERS
    assert set(TRIGGERS) == {"low_margin", "abstained", "refused"}


def test_a_refusal_triggers():
    """An OpenAI-shaped per-question refusal, read as the adapter reads it."""
    item = Item("Mara closes the door behind her.", (OVER,))
    read = openai_compatible.decision_result(
        {"answers": [{"type": "refusal", "name": "q"}]}, item)
    assert read.answers["q"] == Answer(None, "refused")
    result = _result(read.answers["q"])
    assert _fields(_found([result], escalate_on=("refused",))) == [(0, "refused", None)]
    assert _found([result], escalate_on=("abstained", "low_margin")) == ()


def test_an_abstention_triggers_native_and_structured():
    tie = decisions.native_answer(AB, distribution={"a": 0.5, "b": 0.5})
    assert tie.reason == "abstained"
    item = Item("Seraphine shrugs.", (AB,))
    (parsed,) = decisions.parse('{"0": {"answers": {"q": null}}}', [item], explain=False)
    assert parsed.answers["q"].reason == "abstained"
    structured = ItemResult(parsed.answers, backend="structured",
                            served=("openrouter", "p", "m"))
    found = _found([_result(tie), structured], escalate_on=("abstained",))
    assert _fields(found) == [(0, "abstained", None), (1, "abstained", None)]


def test_low_margin_fires_just_under_the_threshold_and_not_at_it():
    sure = [_result(_p(0.625))]                     # margin 0.25, exactly
    assert _found(sure, margins={"openrouter": 0.25}) == ()
    assert _fields(_found(sure, margins={"openrouter": 0.25 + 1e-6})) == [
        (0, "low_margin", 0.25)]


def test_low_margin_at_a_threshold_float_error_missed_does_not_fire():
    """P = 0.6 computes a margin of 0.19999999999999996: on the 0.2
    threshold, not under it (`MASS_TIE`)."""
    assert margin(_p(0.6)) != 0.2 and margin(_p(0.4)) != 0.2
    on = [_result(_p(0.6)), _result(_p(0.4))]
    assert _found(on, margins={"openrouter": 0.2}) == ()
    assert [t.index for t in _found([_result(_p(0.59))], margins={"openrouter": 0.2})] == [0]


def test_low_margin_needs_a_threshold_for_the_items_kind():
    low = _p(0.55)
    assert _found([_result(low)], margins={"openai_compatible": 0.5}) == ()
    assert _found([_result(low, kind="")], margins={"openrouter": 0.5}) == ()
    assert [t.index for t in _found([_result(low)])] == [0]


def test_unreadable_and_error_never_trigger():
    results = [_result(Answer(None, "unreadable")),
               _result(Answer(None, "unreadable", detail=NO_ITEM)),
               _result(Answer(None, "unreadable", detail=NOT_AN_OPTION,
                              distribution={"a": 0.5, "b": 0.5})),
               _result(Answer(None, "error"))]
    assert _found(results) == ()


def test_a_structured_answer_never_triggers_low_margin():
    assert _found([_result(Answer("a"), backend="structured")]) == ()


def test_the_answer_filter_narrows_low_margin():
    known = _result(decisions.native_answer(
        KNOWN, chosen="known", distribution={"known": 0.1, "narrator_only": 0.9}))
    assert [t.index for t in _found([known], answers=("known",))] == [0]
    assert _found([known], answers=("narrator_only",)) == ()
    assert [t.index for t in _found([known])] == [0]


def test_an_answer_absent_from_its_report_triggers():
    """§9 B1: a negative margin is below any threshold."""
    absent = _result(Answer("a", distribution={"b": 1.0}))
    for answers in ((), ("a",)):
        assert _fields(_found([absent], margins={"openrouter": 0.2}, answers=answers)) == [
            (0, "low_margin", -1.0)]


def test_a_prefix_entry_matches_per_row_options():
    merge = _result(Answer("existing:a1", distribution={"existing:a1": 0.5, "new": 0.5}))
    new = _result(Answer("new", distribution={"existing:a1": 0.5, "new": 0.5}))
    assert [t.index for t in _found([merge, new], answers=("existing:",))] == [0]


def test_the_filter_never_narrows_a_refusal_or_an_abstention():
    results = [_result(Answer(None, "refused")), _result(Answer(None, "abstained"))]
    assert _fields(_found(results, answers=("known",))) == [
        (0, "refused", None), (1, "abstained", None)]


def test_triggers_come_in_priority_order():
    doubted = Answer(True, probability=0.35)        # margin -0.3
    results = [_result(_p(0.55)),                   # 0: margin 0.1
               _result(doubted),                    # 1: margin -0.3
               _result(_p(0.525)),                  # 2: margin 0.05
               _result(Answer(None, "abstained")),  # 3
               _result(Answer(None, "refused")),    # 4
               _result(Answer(None, "abstained")),  # 5
               _result(_p(0.95)),                   # 6: confident
               _result(Answer(None, "refused")),    # 7
               _result(doubted),                    # 8: margin -0.3
               _result(Answer(None, "error"))]      # 9
    found = _found(results)
    assert [t.index for t in found] == [4, 7, 3, 5, 1, 8, 2, 0]
    assert [t.trigger for t in found] == ["refused"] * 2 + ["abstained"] * 2 + [
        "low_margin"] * 4


def test_each_trigger_is_a_trigger_record():
    found = _found([_result(Answer(None, "refused")), _result(_p(0.55))])
    assert [(t.trigger, t.margin is None) for t in found] == [
        ("refused", True), ("low_margin", False)]
    for bad in (lambda: Trigger(0, "low_margin"), lambda: Trigger(0, "refused", 0.1),
                lambda: Trigger(0, "doubt"), lambda: Trigger(0, "low_margin", float("nan"))):
        with pytest.raises(ValueError):
            bad()


@pytest.mark.parametrize("threshold", [0, -0.1, float("nan"), float("inf"), True, "0.2"])
def test_triggers_refuses_a_threshold_that_is_not_a_positive_number(threshold):
    with pytest.raises(ValueError):
        _found([_result(_p(0.55))], margins={"openrouter": threshold})


def test_triggers_refuses_a_callers_mistake():
    with pytest.raises(ValueError):
        _found([_result(_p(0.55))], escalate_on=("low-margin",))
    with pytest.raises(ValueError):
        triggers([ItemResult({"over": Answer(True)})], question="q",
                 escalate_on=TRIGGERS, margins={})


def test_triggers_refuses_a_bare_string():
    """A `str` is a collection of its characters: `"low_margin"` would list
    no trigger at all, and `"known"` would filter on single letters."""
    with pytest.raises(TypeError):
        _found([_result(_p(0.55))], escalate_on="low_margin")
    with pytest.raises(TypeError):
        _found([_result(_p(0.55))], answers="known")


def test_no_trigger_listed_finds_nothing():
    results = [_result(Answer(None, "refused")), _result(Answer(None, "abstained")),
               _result(_p(0.55))]
    assert _found(results, escalate_on=()) == ()


def test_triggers_is_pure():
    results = [_result(_p(0.55)), _result(Answer(None, "refused"))]
    before = [(r.answers, r.backend, r.served) for r in results]
    _found(results)
    assert [(r.answers, r.backend, r.served) for r in results] == before
