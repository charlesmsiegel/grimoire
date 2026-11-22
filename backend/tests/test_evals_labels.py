"""Choice outcomes labelled by a reader decide every choice in place of phrase markers (068)."""

import copy
import json

import pytest
from evals_helpers import contribution, guild_run_v2, mutate

from grimoire.evals.format import BenchmarkFormatError, parse_scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.labels import (
    FORMAT_KEY,
    FORMAT_LABELS,
    LABELS,
    Play,
    bind_labels,
    choice_evidence,
    label_sheet,
)
from grimoire.evals.report import render_samples
from grimoire.evals.sampled import compare_samples

RAW_SCENARIO, RAW = guild_run_v2()
SCENARIO = parse_scenario(RAW_SCENARIO)
CHOICES = [choice.choice_id for choice in SCENARIO.choices]


def refuse_ledger(raw):  # Oda answers the ask in character, in no words the markers know
    contribution(raw, 1, "npc-oda")["text"] = "Ledger's guild business. Members only."


def sample(run_id, change=None):
    def edit(raw):
        raw["run_id"] = run_id
        if change:
            change(raw)
    return parse_run_v2(mutate(RAW, edit), SCENARIO)


def outcomes(**overrides):
    return {choice_id: overrides.get(choice_id.replace("-", "_"), "honored") for choice_id in CHOICES}


def found(report, check):
    return [finding for finding in report.findings if finding.check == check]


def test_an_in_character_refusal_labelled_complicated_gives_the_choice_effect():
    baselines = [sample(f"b{i}", refuse_ledger) for i in range(3)]
    candidates = [sample(f"c{i}", refuse_ledger) for i in range(3)]
    by_markers = compare_samples(SCENARIO, baselines, candidates)
    assert by_markers.realized["ask-ledger"] == (0, 0) and not by_markers.passed
    labelled = [outcomes(ask_ledger="complicated")] * 3
    report = compare_samples(SCENARIO, baselines, candidates, baseline_labels=labelled,
                             candidate_labels=labelled)
    assert report.passed, report.findings
    assert report.realized["ask-ledger"] == (3, 3) and report.choice_basis == "labels"
    assert "decided by a reader's labels" in render_samples(report)
    assert "decided by phrase markers" in render_samples(by_markers)
    assert "choices unrealized by phrase markers (not used)" in render_samples(report)
    assert "choices unrealized by phrase markers" not in render_samples(by_markers)


def test_a_marker_phrase_gives_no_effect_once_labels_decide():
    runs = [sample(f"r{i}") for i in range(3)]  # every reply names the guest ledger
    ignored = [outcomes(ask_ledger="ignored")] * 2 + [outcomes()]
    report = compare_samples(SCENARIO, runs, runs, baseline_labels=[outcomes()] * 3,
                             candidate_labels=ignored)
    [finding] = found(report, "choice_negated")
    assert (finding.subject, finding.evidence, finding.basis) == ("ask-ledger", "unrealized", "label")
    assert "the labels give effect to the choice in 1 of 3 samples, not a majority" in finding.message
    report = compare_samples(SCENARIO, runs, runs, baseline_labels=ignored,
                             candidate_labels=[outcomes()] * 3)
    [finding] = found(report, "baseline_invalid")
    assert finding.subject == "choice_negated" and "not a majority" in finding.message


def test_a_choice_labelled_negated_in_any_one_sample_is_a_hard_finding_on_either_side():
    runs = [sample(f"r{i}") for i in range(3)]
    one_negated = [outcomes(), outcomes(), outcomes(ask_song="negated")]
    report = compare_samples(SCENARIO, runs, runs, baseline_labels=[outcomes()] * 3,
                             candidate_labels=one_negated)
    [finding] = found(report, "choice_negated")
    assert finding.message.startswith("sample 3: labelled negated") and finding.subject == "ask-song"
    report = compare_samples(SCENARIO, runs, runs, baseline_labels=one_negated,
                             candidate_labels=[outcomes()] * 3)
    [finding] = found(report, "baseline_invalid")
    assert (finding.message.startswith("sample 3:"), finding.subject, finding.evidence) == (
        True, "choice_negated", "ask-song")


def test_every_hard_check_still_reads_the_text_when_labels_decide_choices():
    def leak(raw):
        contribution(raw, 1, "npc-oda")["text"] = "Between us, the duke sold the guild."
    def negation_marker(raw):
        contribution(raw, 3, "npc-oda")["text"] = "Bellamy refuses to sing. Rules are rules."
    for change in (leak, negation_marker):
        candidates = [sample("c1"), sample("c2", change), sample("c3")]
        report = compare_samples(SCENARIO, [sample("b1")] * 1, candidates,
                                 baseline_labels=[outcomes()], candidate_labels=[outcomes()] * 3)
        assert not report.passed, change.__name__


@pytest.mark.parametrize("baseline_labels, candidate_labels, message", [
    ([outcomes()], None, "both sides or on neither"),
    ([outcomes(), outcomes()], [outcomes()], "one mapping per sample"),
    ([{"ask-ledger": "honored"}], [outcomes()], "every choice"),
    ([outcomes(ask_ledger="refused")], [outcomes()], "every choice"),
])
def test_labels_must_cover_every_choice_of_every_sample_on_both_sides(baseline_labels,
                                                                       candidate_labels, message):
    with pytest.raises(ValueError, match=message):
        compare_samples(SCENARIO, [sample("b1")], [sample("c1")], baseline_labels=baseline_labels,
                        candidate_labels=candidate_labels)


def plays():
    return [Play(f"{index:064x}", sample(f"run-{index}").scoring_view()) for index in range(1, 5)]


def test_the_sheet_is_blind_and_shows_each_choice_the_text_the_check_reads():
    sheet, key = label_sheet(SCENARIO, plays(), salt="one")
    text = json.dumps(sheet)
    assert sheet["format"] == FORMAT_LABELS and set(sheet["definitions"]) == set(LABELS)
    assert "run-1" not in text and "run-2" not in text  # no run id, label or side on the sheet
    assert not any(play.digest in text for play in plays())  # nor any run's digest
    assert all(set(play) == {"play", "choices"} for play in sheet["plays"])
    assert [play["play"] for play in sheet["plays"]] == ["p01", "p02", "p03", "p04"]
    assert key["format"] == FORMAT_KEY and sorted(key["plays"]) == ["p01", "p02", "p03", "p04"]
    assert set(key["plays"].values()) == {play.digest for play in plays()}
    for play in sheet["plays"]:
        assert set(play["choices"]) == set(CHOICES)
        assert all(entry["outcome"] is None for entry in play["choices"].values())
    run = plays()[0].run
    evidence = choice_evidence(SCENARIO, run)
    assert [line["speaker"] for line in evidence["ask-ledger"]] == ["npc-oda", "npc-bellamy"]
    [first_id] = [play_id for play_id, digest in key["plays"].items() if digest == plays()[0].digest]
    [first] = [play for play in sheet["plays"] if play["play"] == first_id]
    assert [line["speaker"] for line in first["choices"]["ask-ledger"]["text"]] == ["Oda", "Bellamy"]
    orders = {tuple(label_sheet(SCENARIO, plays(), salt=salt)[1]["plays"].values())
              for salt in ("one", "two", "three", "four", "five")}
    assert len(orders) > 1  # the salt shuffles the plays


def test_a_sheet_needs_distinct_runs_and_a_salt():
    with pytest.raises(ValueError, match="same run"):
        label_sheet(SCENARIO, [plays()[0], plays()[0]], salt="x")
    with pytest.raises(ValueError, match="salt"):
        label_sheet(SCENARIO, plays(), salt="")


def filled(outcome="complicated"):
    sheet, key = label_sheet(SCENARIO, plays(), salt="x")
    for play in sheet["plays"]:
        for entry in play["choices"].values():
            entry["outcome"] = outcome
    return json.loads(json.dumps(sheet)), json.loads(json.dumps(key))  # as read back from files


def test_a_filled_sheet_and_its_key_read_back_as_outcomes_by_run():
    labels = bind_labels(*filled(), SCENARIO, plays())
    assert set(labels) == {play.digest for play in plays()}
    assert all(outcomes == {choice_id: "complicated" for choice_id in CHOICES}
               for outcomes in labels.values())


@pytest.mark.parametrize("spoil, message", [
    (lambda sheet, key: sheet.update(format="other"), "format"),
    (lambda sheet, key: sheet.update(scenario_id="other"), "scenario_id"),
    (lambda sheet, key: sheet["plays"][0]["choices"]["ask-song"].update(outcome=None), "outcome"),
    (lambda sheet, key: sheet["plays"][0]["choices"]["ask-song"].update(outcome="refused"), "outcome"),
    (lambda sheet, key: sheet["plays"][0]["choices"].pop("ask-song"), "expected exactly"),
    (lambda sheet, key: sheet["plays"][1].update(play=sheet["plays"][0]["play"]), "twice"),
    (lambda sheet, key: sheet["plays"][0].update(play="p99"), "not a play id"),
    (lambda sheet, key: sheet["plays"].pop(), "not labelled"),
    (lambda sheet, key: sheet.update(plays=[]), "non-empty"),
    (lambda sheet, key: sheet["choices"][0].update(act="Something else."), "acts"),
    (lambda sheet, key: sheet["plays"][0]["choices"]["ask-ledger"]["text"].pop(), "current scenario"),
    (lambda sheet, key: key.update(format="other"), "key format"),
    (lambda sheet, key: key.update(scenario_id="other"), "key scenario_id"),
    (lambda sheet, key: key["plays"].update(p01="0" * 64), "not this comparison's key"),
    (lambda sheet, key: key["plays"].update(p01="not a digest"), "sha256"),
])
def test_a_sheet_with_any_choice_unlabelled_or_misbound_is_refused(spoil, message):
    sheet, key = filled()
    spoil(sheet, key)
    with pytest.raises(BenchmarkFormatError, match=message):
        bind_labels(sheet, key, SCENARIO, plays())


def test_labels_do_not_outlive_a_change_to_what_a_choice_reads():
    raw = copy.deepcopy(RAW_SCENARIO)
    [ledger] = [choice for choice in raw["choices"] if choice["choice_id"] == "ask-ledger"]
    ledger["window"] = ledger.get("window", 0) + 2  # now reads turns the reader never saw
    wider = parse_scenario(raw)
    sheet, key = filled()
    widened = [Play(play.digest, parse_run_v2(mutate(RAW, lambda r, i=index: r.update(
        run_id=f"run-{i}")), wider).scoring_view()) for index, play in enumerate(plays(), start=1)]
    with pytest.raises(BenchmarkFormatError, match="current scenario"):
        bind_labels(sheet, key, wider, widened)


def test_the_sheet_shows_exactly_the_text_the_marker_check_reads():
    from collections import Counter

    from grimoire.evals.checks import _evidence_text
    raw = copy.deepcopy(RAW_SCENARIO)
    for choice in raw["choices"]:
        choice["window"] = choice.get("window", 0) + 1
    for scenario in (SCENARIO, parse_scenario(raw)):
        for change in (None, refuse_ledger):
            run = parse_run_v2(mutate(RAW, change or (lambda r: None)), scenario).scoring_view()
            evidence, seen = choice_evidence(scenario, run), Counter()
            for choice in scenario.choices:
                key = (choice.turn, choice.actor_id, choice.text)
                expected = _evidence_text(run, choice.turn, choice.actor_id, choice.text,
                                          choice.window, seen[key])
                seen[key] += 1
                assert [line["text"] for line in evidence[choice.choice_id]] == expected
