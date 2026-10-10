"""`grimoire.draws`: the one sampler, its record and its replay (roadmap 01c-S1).

The goldens here are literals, computed with the standard library on CPython
3.11, 3.12 and 3.13 and checked in so that a browser-side port can reproduce
them. They are never regenerated to make a change pass: a change of rule is a
new `draws.ALGORITHM`, with new goldens beside it.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import random
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

from grimoire import decisions, draws, store
from grimoire.decisions import (
    NONE_KEY,
    Answer,
    Choice,
    Item,
    ItemResult,
    Joint,
    MultiSelect,
    Option,
    Predicate,
    Rank,
    Ranking,
    Score,
    native_answer,
)
from grimoire.draws import Eligibility, ReplayError, draw, draw_from, pick, quanta, replay, unit
from tests.test_llm import _sibling_imports

MARA, SERAPHINE, WINIFRED = "characters:mara", "characters:seraphine", "characters:winifred"
GRIMOIRE = "grimoire"
SERVED = ("openai_compatible", "realm-openai", "decision-model-x")

#: §6's record keys, in §6's order.
RECORD_KEYS = ("v", "algorithm", "question", "purpose", "basis", "sampled", "why", "offered",
               "distribution", "eligibility", "mass_q", "drawn_q", "seed", "selected", "answer",
               "backend", "kind", "provider", "model")

SPEAKER = Choice("next", "Who speaks next?",
                 (Option(MARA, "Mara, who keeps the ledger"),
                  Option(SERAPHINE, "Seraphine, who hesitates at the door"),
                  Option(GRIMOIRE, "The narrator")),
                 allow_none=True)


def _native(q, **report) -> ItemResult:
    return ItemResult({q.id: native_answer(q, **report)}, backend="native", served=SERVED)


def _given(q, answer: Answer, *, backend="native", served=SERVED) -> ItemResult:
    return ItemResult({q.id: answer}, backend=backend, served=served)


# --- Task 1: the primitives --------------------------------------------------

def test_the_constants():
    assert draws.ALGORITHM == "sha256-q32-icdf/1"
    assert draws.SEED_BITS == 53
    assert draws.QUANTUM == 2**32
    assert draws.MASS_SLACK == 0.02
    assert draws.MASS_FLOOR_Q == 4209067950
    assert draws.PURPOSE_MAX == 200
    assert draws.RECORD_VERSION == 1
    assert draws.BASES == ("sampled", "answer", "none")


@pytest.mark.parametrize(("seed", "purpose", "expected"), [
    (0, "", 554538817032997),
    (1, "next", 5375726895549859),
    (2**53 - 1, "next", 4554579951240114),
    (4503599627370495, "next", 1951174975979578),
    (42, "action", 5281169727463273),
    (42, "target:strike", 5553503322267527),
    (123456789, "intent", 8731260151485154),
])
def test_unit_golden_vectors(seed, purpose, expected):
    assert unit(seed, purpose) == expected
    assert 0 <= expected < 2**53


def test_purpose_decouples_draws():
    for warm in range(20):
        unit(warm, "warm-up")
    assert unit(42, "action") == 5281169727463273
    assert unit(42, "target:strike") == 5553503322267527
    assert unit(42, "action") != unit(42, "target:strike")


def test_quanta_is_exact():
    assert quanta(0.1) == 429496729
    assert quanta(0.125) == 536870912
    assert quanta(0.0) == 0
    assert quanta(1.0) == 2**32
    assert quanta(2**-33) == 0


TEN = [(f"o{i}", 0.1) for i in range(10)]
TEN_Q = [(key, quanta(w)) for key, w in TEN]


def test_ten_tenths_are_interpreter_independent():
    """`sum([0.1] * 10)` is 0.9999999999999999 on 3.11 and 1.0 on 3.12 and
    later, so a draw over float cumulative sums can pick a different key on
    different interpreters. The integer rule cannot."""
    assert sum(q for _, q in TEN_Q) == 4294967290
    assert unit(7, "next") == 323243774254641
    assert draw_from(TEN, 7, "next") == "o0"
    assert draw_from(TEN, 2**53 - 1, "next") == "o5"
    # A float inverse CDF over 3.11's naive total gives o2 here.
    assert pick(TEN_Q, 2702159776422298) == "o3"


def test_the_reviews_boundary_case():
    """At this r53 a float inverse CDF picks d with naive left-to-right
    summation (2.3200000000000003) and c with math.fsum (2.32)."""
    weights = [("a", 0.6), ("b", 0.26), ("c", 0.76), ("d", 0.7)]
    q = [(key, quanta(w)) for key, w in weights]
    assert [n for _, n in q] == [2576980377, 1116691496, 3264175144, 3006477107]
    assert sum(n for _, n in q) == 9964324124
    assert pick(q, 6289509824431210) == "d"
    assert pick(q, 6289509823870140) == "c"
    assert pick(q, 6289509823870141) == "d"


def test_pick():
    three = [("a", 1), ("b", 0), ("c", 1)]
    assert pick(three, 0) == "a"
    assert pick(three, 2**52) == "c"
    assert pick(three, 2**53 - 1) == "c"
    assert {pick(three, r) for r in range(0, 2**53, 2**49)} == {"a", "c"}
    two = [("a", 1), ("b", 1)]
    assert pick(two, 2**52 - 1) == "a"
    assert pick(two, 2**52) == "b"
    assert pick([("a", 3), ("b", 0)], 2**53 - 1) == "a"


@pytest.mark.parametrize(("weights", "r53"), [
    ([("a", 0), ("b", 0)], 0),
    ([("a", -1), ("b", 2)], 0),
    ([("a", True)], 0),
    ([("a", 1.0)], 0),
    ([("a", 1), ("a", 1)], 0),
    ([("a", 1)], -1),
    ([("a", 1)], 2**53),
    ([("a", 1)], True),
])
def test_pick_refuses(weights, r53):
    with pytest.raises(ValueError):
        pick(weights, r53)


@pytest.mark.parametrize("kwargs", [
    {"only": ["a"]},
    {"only": ("a", 1)},
    {"exclude": ("a", 1)},
    {"exclude": ["a"]},
    {"cutoff": -0.1},
    {"cutoff": 1.5},
    {"floor": math.nan},
    {"floor": math.inf},
    {"cutoff": True},
    {"cutoff": Fraction(1, 4)},
    {"floor": Decimal("0.1")},
    {"cutoff": "0.1"},
    {"floor": 10**400},
])
def test_eligibility_validates_itself(kwargs):
    with pytest.raises(ValueError):
        Eligibility(**kwargs)


def test_eligibility_coerces_and_records():
    e = Eligibility(cutoff=0, floor=1)
    assert e.cutoff == 0.0 and type(e.cutoff) is float
    assert e.floor == 1.0 and type(e.floor) is float
    assert Eligibility().as_record() == {"only": None, "exclude": [], "cutoff": 0.0, "floor": 0.0}
    assert Eligibility() == draws.UNNARROWED


ABCN = [("a", 0.2), ("b", 0.1), (NONE_KEY, 0.7)]


def test_masks():
    excluded = Eligibility(exclude=(NONE_KEY,))
    assert {draw_from(ABCN, s, "next", excluded) for s in range(200)} == {"a", "b"}
    assert NONE_KEY in {draw_from(ABCN, s, "next") for s in range(200)}
    only = Eligibility(only=("a", "b"))
    assert {draw_from(ABCN, s, "next", only) for s in range(200)} <= {"a", "b"}
    assert draw_from(ABCN, 1, "next", Eligibility(only=())) is None
    for bad in (Eligibility(only=("z",)), Eligibility(exclude=("z",))):
        with pytest.raises(ValueError):
            draw_from(ABCN, 1, "next", bad)


def test_cutoff():
    under = (2**30 - 1) / 2**32
    weights = [("a", under), ("b", 0.25), ("c", 0.5)]
    cut = Eligibility(cutoff=0.25)
    drawn = {draw_from(weights, s, "next", cut) for s in range(300)}
    assert drawn == {"b", "c"}
    assert "a" in {draw_from(weights, s, "next") for s in range(300)}
    assert draw_from(weights, 1, "next", Eligibility(cutoff=0.9)) is None


def test_cutoff_then_floor():
    weights = [("a", 0.05), ("b", 0.05), ("c", 0.05), ("d", 0.45), ("e", 0.4)]
    both = Eligibility(cutoff=0.1, floor=0.2)
    assert {draw_from(weights, s, "next", both) for s in range(300)} == {"d", "e"}
    lifted = Eligibility(floor=0.5)
    assert "b" in {draw_from([("a", 1.0), ("b", 0.0)], s, "next", lifted) for s in range(300)}
    assert {draw_from([("a", 1.0), ("b", 0.0)], s, "next") for s in range(300)} == {"a"}


def test_the_floor_is_sized_to_the_eligible_set():
    weights = [("a", 0.05), ("b", 0.05), ("c", 0.05), ("d", 0.45), ("e", 0.4)]
    with pytest.raises(ValueError):
        draw_from(weights, 1, "next", Eligibility(cutoff=0.1, floor=0.4))
    with pytest.raises(ValueError):
        draw_from([("a", 0.5), ("b", 0.5)], 1, "next", Eligibility(floor=0.6))
    assert draw_from([("a", 0.5), ("b", 0.5)], 1, "next", Eligibility(floor=0.5)) in {"a", "b"}
    # Sized to what the masks leave, not to everything offered.
    narrowed = Eligibility(only=("d", "e"), floor=0.5)
    assert draw_from(weights, 1, "next", narrowed) in {"d", "e"}


def _random_case(rng: random.Random):
    n = rng.randint(1, 12)
    keys = [f"o{i}" for i in range(n)]
    only = None if rng.random() < 0.5 else tuple(k for k in keys if rng.random() < 0.6)
    exclude = tuple(k for k in keys if rng.random() < 0.2)
    eligible = [k for k in keys if (only is None or k in only) and k not in exclude]
    limit = 1 / len(eligible) if eligible else 1.0
    floor = limit if rng.random() < 0.3 else rng.uniform(0, limit)
    cutoff = rng.choice([0.0, rng.random(), 1.0])
    mass = rng.uniform(0, 2)
    raw = {k: rng.random() for k in keys if rng.random() < 0.7}
    total = sum(raw.values()) or 1.0
    report = {k: min(1.0, w * mass / total) for k, w in raw.items()}
    if rng.random() < 0.1:
        report = dict.fromkeys(report, cutoff / 2)
    if rng.random() < 0.1:
        report = dict.fromkeys(report, 0.0)
    e = Eligibility(only=only, exclude=exclude, cutoff=cutoff, floor=floor)
    return keys, report, e


def test_a_sized_floor_never_raises_on_any_report():
    rng = random.Random(1010)
    for _ in range(1000):
        keys, report, e = _random_case(rng)
        seed = rng.randrange(2**53)
        draw_from([(k, report.get(k, 0.0)) for k in keys], seed, "next", e)
        if len(keys) >= 2:
            q = Choice("next", "i", tuple(Option(k, k) for k in keys))
            got = draw(q, _native(q, distribution=report), seed=seed, purpose="next",
                       eligibility=e)
            assert got.basis in draws.BASES


BAD_SEEDS = [2**53, 2**63 - 1, -1, True, 1.0, None]
BAD_PURPOSES = ["é", "\x7f", "a\nb", "x" * 201, None]


@pytest.mark.parametrize("seed", BAD_SEEDS)
def test_bad_seeds_are_refused(seed):
    with pytest.raises(ValueError):
        unit(seed, "next")
    with pytest.raises(ValueError):
        draw_from(TEN, seed, "next")


@pytest.mark.parametrize("purpose", BAD_PURPOSES)
def test_bad_purposes_are_refused(purpose):
    with pytest.raises(ValueError):
        unit(1, purpose)
    with pytest.raises(ValueError):
        draw_from(TEN, 1, purpose)


def test_a_full_length_printable_purpose_is_accepted():
    purpose = "".join(chr(0x20 + i % 95) for i in range(200))
    assert 0 <= unit(1, purpose) < 2**53


@pytest.mark.parametrize("weights", [
    [("a", 1.5)], [("a", -0.1)], [("a", math.nan)], [("a", True)], [("a", 0.5), ("a", 0.5)],
    [(1, 0.5)],
])
def test_draw_from_refuses_bad_weights(weights):
    with pytest.raises(ValueError):
        draw_from(weights, 1, "next")


def test_new_seed(monkeypatch):
    for _ in range(50):
        assert 0 <= draws.new_seed() < 2**53
    monkeypatch.setattr(draws, "_seed_source", lambda: 5)
    assert draws.new_seed() == 5
    monkeypatch.setattr(draws, "_seed_source", lambda: 2**53)
    with pytest.raises(ValueError):
        draws.new_seed()


def test_draws_is_a_leaf():
    """Nothing from the package but `decisions`, bound as a module, and the
    standard library: no store, no clock, no file."""
    assert _sibling_imports("draws") == {"decisions"}
    tree = ast.parse(inspect.getsource(draws))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                assert node.module is None, ast.dump(node)
                assert [a.name for a in node.names] == ["decisions"], ast.dump(node)
            else:
                imported.add((node.module or "").split(".")[0])
    assert imported, "the walk found no imports at all"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# --- Task 2: draw and the record ---------------------------------------------

SPEC_EXAMPLE = {
    "v": 1,
    "algorithm": "sha256-q32-icdf/1",
    "question": "next",
    "purpose": "next",
    "basis": "sampled",
    "sampled": True,
    "why": "",
    "offered": ["characters:mara", "characters:seraphine", "grimoire", "<none>"],
    "distribution": [["characters:mara", 0.55], ["characters:seraphine", 0.3],
                     ["grimoire", 0.1], ["<none>", 0.05]],
    "eligibility": {"only": None, "exclude": ["<none>"], "cutoff": 0.0, "floor": 0.125},
    "mass_q": 4294967293,
    "drawn_q": 4187593112,
    "seed": 4503599627370495,
    "selected": "characters:mara",
    "answer": "characters:mara",
    "backend": "native",
    "kind": "openai_compatible",
    "provider": "realm-openai",
    "model": "decision-model-x",
}


def test_the_specs_example_record_is_reproduced_exactly():
    result = _native(SPEAKER, distribution={MARA: 0.55, SERAPHINE: 0.3, GRIMOIRE: 0.1,
                                            NONE_KEY: 0.05})
    got = draw(SPEAKER, result, seed=4503599627370495, purpose="next",
               eligibility=Eligibility(exclude=(NONE_KEY,), floor=0.125))
    assert got.record == SPEC_EXAMPLE
    assert list(got.record) == list(RECORD_KEYS)
    assert (got.basis, got.key, got.value) == ("sampled", MARA, MARA)
    assert replay(got.record) == MARA


def test_canonical_order():
    forward = {MARA: 0.4, SERAPHINE: 0.3, GRIMOIRE: 0.2, NONE_KEY: 0.1}
    backward = dict(reversed(list(forward.items())))
    assert list(forward) != list(backward)
    for seed in range(300):
        one = draw(SPEAKER, _native(SPEAKER, distribution=forward), seed=seed)
        two = draw(SPEAKER, _native(SPEAKER, distribution=backward), seed=seed)
        assert one.record == two.record
        assert one.record["distribution"] == [[k, w] for k, w in forward.items()]


def _row(q, result, **kwargs):
    kwargs.setdefault("seed", 99)
    return draw(q, result, **kwargs)


OVER = Predicate("over", "Is the scene over?")


@pytest.mark.parametrize(("q", "result", "why"), [
    (OVER, _native(OVER, probability=0.5), "abstained"),
    (SPEAKER, _native(SPEAKER, refused=True), "refused"),
    (SPEAKER, _given(SPEAKER, Answer(None, "unreadable")), "unreadable"),
    (SPEAKER, decisions.unanswered([Item("ctx", (SPEAKER,))], "error")[0], "error"),
    (SPEAKER, _native(SPEAKER, chosen=NONE_KEY), "abstained"),
    (SPEAKER, _native(SPEAKER, distribution={MARA: 0.45, SERAPHINE: 0.45, NONE_KEY: 0.1}),
     "abstained"),
])
def test_an_answer_of_none_is_never_drawn(q, result, why):
    got = _row(q, result)
    assert (got.basis, got.key, got.value) == ("none", None, None)
    record = got.record
    assert (record["why"], record["sampled"], record["selected"]) == (why, False, None)
    assert record["seed"] is None and record["mass_q"] is None and record["drawn_q"] is None
    assert record["answer"] is None
    assert replay(record) is None
    # An abstention is never spelled as a chosen none.
    assert NONE_KEY not in json.dumps({k: v for k, v in record.items()
                                       if k not in ("offered", "distribution")})


def test_a_structured_answer_is_acted_on_not_drawn():
    got = _row(SPEAKER, _given(SPEAKER, Answer(MARA), backend="structured"))
    assert (got.basis, got.key, got.value) == ("answer", MARA, MARA)
    record = got.record
    assert (record["why"], record["seed"], record["distribution"]) == ("no_report", None, None)
    assert record["selected"] == record["answer"] == MARA
    assert record["mass_q"] is None and record["drawn_q"] is None
    assert replay(record) == MARA
    excluded = _row(SPEAKER, _given(SPEAKER, Answer(MARA), backend="structured"),
                    eligibility=Eligibility(exclude=(MARA,)))
    assert (excluded.basis, excluded.record["why"], excluded.key) == ("none", "ineligible", None)
    assert excluded.record["answer"] == MARA


@pytest.mark.parametrize(("q", "answer"), [
    (OVER, native_answer(OVER, chosen=True)),
    (SPEAKER, Answer(MARA, marginals={MARA: 0.9})),
    (SPEAKER, Answer(MARA, distribution={WINIFRED: 1.0})),
    (SPEAKER, Answer(MARA, distribution={MARA: 1.5})),
    (SPEAKER, Answer(MARA, distribution={})),
    (OVER, Answer(True, probability=1.5)),
])
def test_no_usable_report_is_never_drawn(q, answer):
    got = _row(q, _given(q, answer))
    assert got.basis == "answer" and got.record["why"] == "no_report"
    assert got.record["distribution"] is None and got.record["seed"] is None
    assert got.value == answer.answer


RANK = Rank("order", "Who leads?", (Option(MARA, "Mara"), Option(SERAPHINE, "Seraphine")))
SELECT = MultiSelect("who", "Who joins?", (Option(MARA, "Mara"), Option(SERAPHINE, "Seraphine")))


@pytest.mark.parametrize(("q", "answer"), [
    (RANK, Answer(Ranking(((MARA,), (SERAPHINE,))), marginals={MARA: 0.8, SERAPHINE: 0.3})),
    (SELECT, Answer((MARA,), marginals={MARA: 0.9, SERAPHINE: 0.6})),
])
def test_rank_and_select_are_never_drawn(q, answer):
    got = _row(q, _given(q, answer))
    assert (got.basis, got.key, got.value) == ("answer", None, answer.answer)
    record = got.record
    assert record["why"] == "no_report"
    assert record["offered"] == [MARA, SERAPHINE]
    assert record["selected"] is None and record["answer"] is None
    narrowed = _row(q, _given(q, answer), eligibility=Eligibility(exclude=(SERAPHINE,)))
    assert (narrowed.basis, narrowed.record["why"], narrowed.value) == ("none", "ineligible", None)


def test_a_partial_report_is_not_normalised():
    result = _native(SPEAKER, distribution={MARA: 0.4, SERAPHINE: 0.1})
    got = _row(SPEAKER, result)
    assert (got.basis, got.record["why"], got.key) == ("answer", "partial", MARA)
    assert got.record["mass_q"] == 2147483647
    assert got.record["distribution"] == [[MARA, 0.4], [SERAPHINE, 0.1]]
    assert got.record["drawn_q"] is None and got.record["seed"] is None
    # No cutoff at the partial row (coordinator decision 1).
    cut = _row(SPEAKER, result, eligibility=Eligibility(cutoff=0.5))
    assert (cut.basis, cut.record["why"], cut.record["selected"]) == ("answer", "partial", MARA)
    masked = _row(SPEAKER, result, eligibility=Eligibility(exclude=(MARA,)))
    assert (masked.basis, masked.record["why"]) == ("none", "ineligible")


def test_the_mass_floor_is_inclusive_and_over_one_is_drawn():
    at_floor = _row(SPEAKER, _native(SPEAKER, distribution={MARA: 0.98}))
    assert at_floor.record["mass_q"] == draws.MASS_FLOOR_Q
    assert at_floor.basis == "sampled"
    over = _row(SPEAKER, _native(SPEAKER, distribution={MARA: 0.9, SERAPHINE: 0.8}))
    assert over.basis == "sampled"
    assert over.record["mass_q"] == quanta(0.9) + quanta(0.8)
    assert over.record["drawn_q"] == over.record["mass_q"]


@pytest.mark.parametrize("report", [
    {MARA: 0.98, GRIMOIRE: 0.02},
    {MARA: 0.98, SERAPHINE: 0.0, GRIMOIRE: 0.02},
])
def test_an_answer_its_report_contradicts_is_not_drawn(report):
    result = _native(SPEAKER, chosen=SERAPHINE, distribution=report)
    got = _row(SPEAKER, result)
    assert (got.basis, got.record["why"], got.key) == ("answer", "inconsistent", SERAPHINE)
    # 02's always-on cutoff shape keeps the row and its why (decision 1).
    cut = _row(SPEAKER, result, eligibility=Eligibility(cutoff=0.1))
    assert (cut.basis, cut.record["why"], cut.record["selected"]) == (
        "answer", "inconsistent", SERAPHINE)
    masked = _row(SPEAKER, result, eligibility=Eligibility(exclude=(SERAPHINE,)))
    assert (masked.basis, masked.record["why"]) == ("none", "ineligible")


def test_an_answer_naming_no_offered_key_is_never_returned():
    answer = Answer(WINIFRED, distribution={MARA: 0.98, SERAPHINE: 0.02})
    got = _row(SPEAKER, _given(SPEAKER, answer))
    assert (got.basis, got.record["why"], got.key, got.value) == ("none", "ineligible", None, None)
    assert got.record["answer"] == WINIFRED


def test_a_cutoff_that_empties_the_set_draws_nothing():
    result = _native(SPEAKER, distribution={MARA: 0.4, SERAPHINE: 0.3, GRIMOIRE: 0.3})
    got = _row(SPEAKER, result, eligibility=Eligibility(cutoff=0.5))
    assert (got.basis, got.record["why"], got.key) == ("none", "cutoff", None)
    assert got.record["seed"] is None and got.record["drawn_q"] is None
    assert got.record["mass_q"] == quanta(0.4) + 2 * quanta(0.3)


def test_the_ineligible_mass_row_becomes_ineligible():
    result = _native(SPEAKER, distribution={MARA: 0.98, SERAPHINE: 0.02, GRIMOIRE: 0.0})
    got = _row(SPEAKER, result, eligibility=Eligibility(only=(GRIMOIRE,)))
    assert (got.basis, got.record["why"], got.key) == ("none", "ineligible", None)
    assert got.record["drawn_q"] == 0
    assert got.record["seed"] is None


def test_an_answer_outside_the_narrowed_set_still_samples_the_rest():
    result = _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.3, GRIMOIRE: 0.1})
    narrowed = Eligibility(only=(SERAPHINE, GRIMOIRE))
    seen = {_row(SPEAKER, result, seed=s, eligibility=narrowed).key for s in range(200)}
    assert seen == {SERAPHINE, GRIMOIRE}


def test_a_sampled_none_is_a_selection():
    result = _native(SPEAKER, chosen=MARA,
                     distribution={MARA: 0.2, SERAPHINE: 0.1, GRIMOIRE: 0.1, NONE_KEY: 0.6})
    draws_ = [_row(SPEAKER, result, seed=s) for s in range(200)]
    nones = [d for d in draws_ if d.key == NONE_KEY]
    assert nones
    assert all(d.basis == "sampled" and d.value is None and d.record["sampled"] for d in nones)
    assert all(d.record["selected"] == NONE_KEY for d in nones)
    excluded = Eligibility(exclude=(NONE_KEY,))
    assert all(_row(SPEAKER, result, seed=s, eligibility=excluded).key != NONE_KEY
               for s in range(200))


def test_values_take_the_answers_type():
    predicate = draw(OVER, _native(OVER, probability=0.7), seed=3)
    assert predicate.basis == "sampled" and isinstance(predicate.value, bool)
    assert predicate.key == ("true" if predicate.value else "false")
    assert predicate.record["offered"] == ["true", "false"]
    assert predicate.record["distribution"] == [["true", 0.7], ["false", 1.0 - 0.7]]
    level = Score("drift", "How far?", ("none", "some", "far"))
    scored = draw(level, _native(level, distribution={"0": 0.2, "1": 0.5, "2": 0.3}), seed=3)
    assert scored.basis == "sampled" and type(scored.value) is int
    assert scored.key == str(scored.value)
    assert scored.record["offered"] == ["0", "1", "2"]
    choice = draw(SPEAKER, _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4}), seed=3)
    assert choice.basis == "sampled" and isinstance(choice.value, str)


ACT = Joint("act", "What does Winifred do?",
            (Option("wait", "Wait"), Option("strike", "Strike")),
            (("strike", (Option(MARA, "Mara"), Option(SERAPHINE, "Seraphine"))),))


def test_a_joint_draws_its_flattened_choice():
    item = Item("ctx", (ACT,))
    lowered_item, lift = decisions.native_form(item)
    flat = lowered_item.questions[0]
    keys = [o.id for o in decisions.joint_choice(ACT).options]
    report = dict(zip(keys, (0.2, 0.5, 0.3), strict=True))
    lowered = ItemResult({ACT.id: native_answer(flat, distribution=report)}, backend="native",
                         served=SERVED)
    result = decisions.native_lift(item, lowered, lift)
    seen = set()
    for seed in range(100):
        got = draw(ACT, result, seed=seed, purpose="action")
        assert got.record["offered"] == keys
        assert got.basis == "sampled"
        assert isinstance(got.value, decisions.Pair) and got.value.key == got.key
        seen.add(got.key)
    assert seen == set(keys)


def _every_basis():
    yield draw(SPEAKER, _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4}), seed=1)
    yield draw(SPEAKER, _given(SPEAKER, Answer(MARA), backend="structured"), seed=1)
    yield draw(SPEAKER, _native(SPEAKER, refused=True), seed=1)


def test_the_record_is_json_and_holds_no_prose():
    def native(value):
        if isinstance(value, list):
            return all(native(v) for v in value)
        if isinstance(value, dict):
            return all(isinstance(k, str) and native(v) for k, v in value.items())
        return value is None or isinstance(value, (str, int, float, bool))

    bases = set()
    for got in _every_basis():
        bases.add(got.basis)
        assert tuple(got.record) == RECORD_KEYS
        assert native(got.record)
        dumped = json.dumps(got.record, allow_nan=False)
        assert "hesitates" not in dumped and "ledger" not in dumped
    assert bases == set(draws.BASES)
    prose = ItemResult({"next": native_answer(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4})},
                       rationale="Seraphine hesitates at the door", backend="native")
    got = draw(SPEAKER, prose, seed=1)
    assert "hesitates" not in json.dumps(got.record)
    assert (got.record["kind"], got.record["provider"], got.record["model"]) == ("", "", "")


def test_the_eligibility_is_recorded_as_supplied():
    e = Eligibility(only=(MARA, SERAPHINE), cutoff=0.25)
    expected = {"only": [MARA, SERAPHINE], "exclude": [], "cutoff": 0.25, "floor": 0.0}
    for got in (
        draw(SPEAKER, _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4}), seed=1,
             eligibility=e),
        draw(SPEAKER, _given(SPEAKER, Answer(MARA), backend="structured"), seed=1, eligibility=e),
        draw(SPEAKER, _native(SPEAKER, refused=True), seed=1, eligibility=e),
    ):
        assert got.record["eligibility"] == expected
    zero = draw(SPEAKER, _native(SPEAKER, refused=True), seed=1, eligibility=Eligibility(cutoff=0))
    assert type(zero.record["eligibility"]["cutoff"]) is float


@pytest.mark.parametrize("result", [
    _native(SPEAKER, refused=True),
    _given(SPEAKER, Answer(MARA), backend="structured"),
    _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4}),
])
@pytest.mark.parametrize("kwargs", [
    {"seed": 2**63 - 1},
    {"seed": 1, "purpose": "é"},
    {"seed": 1, "eligibility": Eligibility(only=(WINIFRED,))},
    {"seed": 1, "eligibility": Eligibility(exclude=(WINIFRED,))},
    {"seed": 1, "eligibility": Eligibility(floor=0.3)},
])
def test_draw_raises_only_for_misuse(result, kwargs):
    with pytest.raises(ValueError):
        draw(SPEAKER, result, **kwargs)


def test_a_result_without_the_question_is_misuse():
    with pytest.raises(ValueError):
        draw(SPEAKER, ItemResult({"other": Answer(MARA)}), seed=1)


def test_draw_does_not_mutate_its_inputs():
    report = {MARA: 0.6, SERAPHINE: 0.4}
    result = _native(SPEAKER, distribution=report)
    before = (dict(result.answers["next"].distribution), result.answers["next"])
    one = draw(SPEAKER, result, seed=1)
    two = draw(SPEAKER, result, seed=1)
    assert one.record == two.record and one.record is not two.record
    one.record["offered"].append("x")
    assert two.record["offered"] == [MARA, SERAPHINE, GRIMOIRE, NONE_KEY]
    assert (dict(result.answers["next"].distribution), result.answers["next"]) == before


# --- Task 3: replay -----------------------------------------------------------

def _sweep_case(rng: random.Random):
    n = rng.randint(2, 12)
    options = tuple(Option(f"characters:c{i}", f"C{i}") for i in range(n))
    q = Choice("next", "Who speaks next?", options, allow_none=rng.random() < 0.4)
    offered = [o.id for o in options] + ([NONE_KEY] if q.allow_none else [])
    present = [k for k in offered if rng.random() < 0.75] or offered[:1]
    raw = {k: rng.random() + 0.01 for k in present}
    mass = rng.uniform(0.5, 1.4)
    total = sum(raw.values())
    report = {k: min(1.0, w * mass / total) for k, w in raw.items()}
    only = None if rng.random() < 0.6 else tuple(k for k in offered if rng.random() < 0.7)
    exclude = tuple(k for k in offered if rng.random() < 0.15)
    eligible = [k for k in offered if (only is None or k in only) and k not in exclude]
    limit = 1 / len(eligible) if eligible else 1.0
    floor = 0.0 if rng.random() < 0.5 else rng.uniform(0, limit)
    cutoff = 0.0 if rng.random() < 0.5 else rng.uniform(0, 0.3)
    e = Eligibility(only=only, exclude=exclude, cutoff=cutoff, floor=floor)
    purpose = rng.choice(["", "next", "intent", "target:strike"])
    return q, _native(q, distribution=report), e, rng.randrange(2**53), purpose


def test_replay_sweep():
    rng = random.Random(20261010)
    bases: set[str] = set()
    cut = lifted = floored = 0
    for _ in range(500):
        q, result, e, seed, purpose = _sweep_case(rng)
        got = draw(q, result, seed=seed, purpose=purpose, eligibility=e)
        record = got.record
        assert replay(record) == record["selected"]
        assert replay(json.loads(json.dumps(record))) == record["selected"]
        bases.add(got.basis)
        if got.basis == "sampled":
            cut += e.cutoff > 0
            floored += e.floor > 0
            reported = {k for k, _ in record["distribution"]}
            lifted += record["selected"] not in reported
    assert bases == set(draws.BASES)
    assert cut and floored and lifted, (cut, floored, lifted)


#: A sampled record with a cutoff, pinned whole: the replay must give the
#: same key on every supported interpreter.
CUTOFF_RECORD = {
    "v": 1, "algorithm": "sha256-q32-icdf/1", "question": "next", "purpose": "next",
    "basis": "sampled", "sampled": True, "why": "",
    "offered": ["a", "b", "c", "d"],
    "distribution": [["a", 0.1], ["b", 0.25], ["c", 0.35], ["d", 0.3]],
    "eligibility": {"only": None, "exclude": [], "cutoff": 0.2, "floor": 0.0},
    "mass_q": 4294967294, "drawn_q": 3865470565, "seed": 987654321,
    "selected": "d", "answer": "c", "backend": "native",
    "kind": "openrouter", "provider": "realm-openrouter", "model": "decision-model-x",
}


def test_a_cutoff_record_replays():
    assert unit(987654321, "next") == 8064500101399754
    assert replay(CUTOFF_RECORD) == "d"
    q = Choice("next", "i", tuple(Option(k, k) for k in "abcd"))
    result = ItemResult({"next": native_answer(q, distribution=dict(CUTOFF_RECORD["distribution"]))},
                        backend="native", served=("openrouter", "realm-openrouter",
                                                  "decision-model-x"))
    assert draw(q, result, seed=987654321, purpose="next",
                eligibility=Eligibility(cutoff=0.2)).record == CUTOFF_RECORD


def test_a_non_sampled_record_returns_its_selected():
    for got in list(_every_basis())[1:]:
        record = dict(got.record, seed="corrupt", offered=None, distribution="corrupt")
        assert replay(record) == got.record["selected"]


def _sampled(**changes):
    return {**CUTOFF_RECORD, **changes}


@pytest.mark.parametrize("record", [
    _sampled(algorithm="sha256-q32-icdf/2"),
    _sampled(v=2),
    _sampled(seed=None),
    _sampled(seed=2**53),
    _sampled(purpose="é"),
    _sampled(distribution=[["z", 0.5]]),
    _sampled(distribution=[["a", 0.5], ["a", 0.5]]),
    _sampled(distribution=[["a", 1.5]]),
    _sampled(distribution=[["a"]]),
    _sampled(distribution=[[["a"], 0.5]]),
    _sampled(distribution="a"),
    _sampled(offered=["a", "a", "b"]),
    _sampled(offered=["a", 1]),
    _sampled(basis="drawn"),
    _sampled(basis="answer", sampled=True),
    _sampled(sampled="yes"),
    _sampled(selected=1),
    _sampled(eligibility={"only": None, "exclude": [], "cutoff": 0.2}),
    _sampled(eligibility={"only": None, "exclude": ["z"], "cutoff": 0.0, "floor": 0.0}),
    _sampled(eligibility={"only": None, "exclude": [], "cutoff": 0.9, "floor": 0.0}),
    _sampled(eligibility={"only": None, "exclude": [], "cutoff": 0.0, "floor": 0.9}),
    _sampled(eligibility=None),
    ["not", "a", "mapping"],
])
def test_replay_refuses(record):
    with pytest.raises(ReplayError):
        replay(record)


def test_replay_error_is_a_value_error():
    assert issubclass(ReplayError, ValueError)


def test_the_record_rides_the_round_record(tmp_path, monkeypatch):
    """C3 §6.1 rule 1 on the write the speaker pick will use, with nothing
    wired: `responses.update_round` already holds `campaign_lock` and writes
    through `atomic`, so the record needs no store module, writer or guard
    marker of its own. Rule 4: a round written without one reads None."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    opened = store.responses.new_round(
        cid, sid, eligible=[{"ref": MARA, "name": "Mara"}], automatic=True, post=0,
        run_id="run-draw")
    assert opened.get("pick") is None
    got = draw(SPEAKER, _native(SPEAKER, distribution={MARA: 0.6, SERAPHINE: 0.4}),
               seed=draws.new_seed(), purpose="next")
    store.responses.update_round(cid, sid, opened["id"], actor_ref=got.value, pick=got.record)
    data = json.loads(store.responses._path(cid).read_text(encoding="utf-8"))
    (stored,) = [scope["rounds"][opened["id"]] for scope in data["scenes"].values()
                 if opened["id"] in scope["rounds"]]
    assert stored["pick"] == got.record
    assert replay(stored["pick"]) == stored["pick"]["selected"]
    assert stored["actor_ref"] == got.value
