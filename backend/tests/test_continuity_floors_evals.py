"""The continuity candidate floors, measured offline as part of the ordinary
test run: the labelled pair corpus keeps its rules, the filter it is scored
through is production's own, and the figures at the floors in force are
pinned -- so a change to a floor, a signal or a clause has to say here what
it did to recall and precision, rather than move them unseen. Nothing here
changes a floor; `COSINE_FLOOR` takes a real model and is not measured.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import run as eval_run  # noqa: E402
from evals.continuity_floors import corpus as floors_corpus  # noqa: E402
from evals.continuity_floors import metrics  # noqa: E402
from grimoire.store.continuity import reconcile, similarity  # noqa: E402

CORPUS = floors_corpus.load()


def test_the_corpus_keeps_its_rules():
    assert floors_corpus.problems(CORPUS) == []


def test_every_answer_the_judge_can_give_about_a_pair_has_one():
    """Read off the judge's own vocabularies, not the corpus's list of them:
    a word the judge can answer about a pair -- `subthread` between two
    threads, say -- with no pair labelled so is a kind of pair whose fate at
    the floors nobody measures. `uncertain` is the one word that labels no
    pair."""
    present = {(floors_corpus.VOCABULARY[(p.a["record"], p.b["record"])], p.relation)
               for p in CORPUS.pairs}
    for vocabulary in set(floors_corpus.VOCABULARY.values()):
        for word in reconcile.DECISIONS[vocabulary]:
            if word != "uncertain":
                assert (vocabulary, word) in present, f"no {vocabulary} pair is {word!r}"


def test_the_rules_catch_a_paraphrase_that_shares_a_word_and_a_decoy_that_shares_none():
    by_id = {p.id: p for p in CORPUS.pairs}
    shares = replace(by_id["dup-harbour-ledger"], shape="paraphrase")
    unlike = replace(by_id["decoy-harbour-tavern"],
                     b={"record": "thread", "title": "Mend the nets",
                        "beats": ["Fishermen knot torn rope at dawn."]})
    mislabelled = replace(by_id["pay-ferry-fare"], relation="continuation")
    found = floors_corpus.problems(floors_corpus.Corpus((shares, unlike, mislabelled)))
    assert ("dup-harbour-ledger: a paraphrase shares ['harbour', 'ledger', 'missing', "
            "'office']") in found
    assert "decoy-harbour-tavern: a decoy pair shares no word" in found
    assert "pay-ferry-fare: 'continuation' is not an answer about cross" in found
    assert "no cross pair is 'pays_off'" in found


def test_the_floors_in_force_measure_what_they_measured():
    """The figures this corpus gave at the floors that shipped. A change that
    moves them is not wrong for it -- the floors are to be tuned -- but it
    updates these with the reason, as `COV_FLOOR` is moved."""
    assert (similarity.TOKEN_FLOOR, similarity.CHAR_FLOOR,
            similarity.WEAK_TOKEN, similarity.WEAK_CHAR) == (0.25, 0.35, 0.1, 0.15)
    alone = metrics.measure(CORPUS, "alone")
    assert (alone.found, alone.kept) == ((4, 28), (4, 9))
    cast = metrics.measure(CORPUS, "shared cast")
    assert (cast.found, cast.kept) == ((21, 28), (21, 31))


def test_a_paraphrase_reaches_the_judge_on_no_lexical_evidence():
    """The lexical blind spot, by construction: a duplicate that shares no
    content word is let through at no swept floor alone, nor in a shared cast
    at the floors in force. That is the semantic clause's job. (A shared cast
    at a weak 3-gram floor of 0.05 does let them through -- on the trigram
    noise any two English sentences share, beside nearly everything else.)"""
    paraphrases = {p.id for p in CORPUS.pairs if p.shape == "paraphrase"}
    row, col = metrics.SWEPT["alone"]
    scored = metrics.signals(CORPUS, "alone")
    for r in metrics.sweep_values(row):
        for c in metrics.sweep_values(col):
            assert not paraphrases & metrics.admitted(scored, **{row: r, col: c})
    assert not paraphrases & metrics.measure(CORPUS, "shared cast").admitted


def test_a_lower_floor_never_lets_through_less():
    """The sweep reads production's clause with one constant moved, and
    lowering any floor can only add pairs: a check that the patching reaches
    the clause rather than a copy of it."""
    for condition, names in metrics.SWEPT.items():
        scored = metrics.signals(CORPUS, condition)
        for name in names:
            values = metrics.sweep_values(name)
            admitted = [metrics.admitted(scored, **{name: v}) for v in values]
            assert all(low >= high for low, high in itertools.pairwise(admitted))
            assert admitted[0] != admitted[-1], f"{name} moved nothing on {condition}"
    assert similarity.TOKEN_FLOOR == 0.25          # the patch did not outlive its call


def test_a_share_of_nothing_reads_na_never_zero():
    none_through = metrics.Measure(CORPUS, frozenset())
    assert none_through.precision is None
    assert none_through.recall == 0.0
    assert metrics._ratio(*none_through.kept) == "n/a (none let through)"


def test_the_cli_prints_the_report(capsys):
    assert eval_run.main(["--continuity-floors"]) == 0
    out = capsys.readouterr().out
    assert "alone: recall 0.14 (4/28), precision 0.44 (4/9)" in out
    assert "shared cast: recall 0.75 (21/28), precision 0.68 (21/31)" in out
    assert "*0.14/0.44" in out and "*0.75/0.68" in out


@pytest.mark.parametrize("argv", [["--live"], ["--embed"], ["--out", "x.json"],
                                  ["--investigate", "continuity"],
                                  ["--scene", "nonsense"], ["--decision", "ignored"]])
def test_the_cli_refuses_what_the_floors_do_not_take(argv, capsys):
    """A flag the floors would ignore is refused by name, never dropped: a
    run that exits 0 beside it reads as though it had been honoured."""
    with pytest.raises(SystemExit) as exc:
        eval_run.main(["--continuity-floors", *argv])
    assert exc.value.code == 2
    assert f"it takes no {argv[0]}" in capsys.readouterr().err
