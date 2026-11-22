"""Sampled /2 gate (068): invariants hold in every sample, choices by majority, voice by mean."""

from fractions import Fraction

import pytest
from evals_helpers import contribution, guild_run_v2, mutate

from grimoire.evals.format import parse_scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.report import render_samples
from grimoire.evals.sampled import MIN_SAMPLES_FOR_NOISE, compare_samples, noise_allowance


def guild():
    raw_scenario, raw_run = guild_run_v2()
    return parse_scenario(raw_scenario), raw_run


SCENARIO, RAW = guild()


def sample(run_id, change=None):
    def edit(raw):
        raw["run_id"] = run_id
        if change:
            change(raw)
    return parse_run_v2(mutate(RAW, edit), SCENARIO)


def unrealize_ledger(raw):
    contribution(raw, 1, "npc-oda")["text"] = "Coin's good. Read it here."


def negate_song(raw):
    contribution(raw, 3, "npc-oda")["text"] = "Bellamy refuses to sing. Rules are rules."


def swap_turn_one(raw):
    oda, bellamy = contribution(raw, 1, "npc-oda"), contribution(raw, 1, "npc-bellamy")
    oda["text"], bellamy["text"] = bellamy["text"], oda["text"]


def found(report, check):
    return [finding for finding in report.findings if finding.check == check]


def test_a_choice_is_given_effect_by_a_strict_majority_of_samples():
    baselines = [sample(f"b{i}") for i in range(3)]
    two_of_three = [sample("c1"), sample("c2"), sample("c3", unrealize_ledger)]
    report = compare_samples(SCENARIO, baselines, two_of_three)
    assert report.passed, report.findings
    assert report.realized["ask-ledger"] == (3, 2)
    one_of_three = [sample("c1"), sample("c2", unrealize_ledger), sample("c3", unrealize_ledger)]
    report = compare_samples(SCENARIO, baselines, one_of_three)
    [finding] = found(report, "choice_negated")
    assert (finding.subject, finding.evidence, finding.turn) == ("ask-ledger", "unrealized", 1)
    assert "1 of 3 samples, not a majority" in finding.message
    half = [sample("c1"), sample("c2", unrealize_ledger)]  # a tie is not a majority
    assert found(compare_samples(SCENARIO, baselines, half), "choice_negated")


def test_an_invariant_broken_in_any_one_sample_fails_on_either_side():
    baselines = [sample(f"b{i}") for i in range(3)]
    candidates = [sample("c1"), sample("c2"), sample("c3", negate_song)]
    [finding] = found(compare_samples(SCENARIO, baselines, candidates), "choice_negated")
    assert finding.evidence.startswith("negation#") and finding.message.startswith("sample 3:")
    report = compare_samples(SCENARIO, [*baselines[:2], sample("b3", negate_song)],
                             [sample(f"c{i}") for i in range(3)])
    [finding] = found(report, "baseline_invalid")
    assert finding.subject == "choice_negated" and finding.message.startswith("sample 3:")


def test_a_baseline_choice_given_effect_only_by_a_minority_invalidates_the_baseline():
    baselines = [sample("b1"), sample("b2", unrealize_ledger), sample("b3", unrealize_ledger)]
    report = compare_samples(SCENARIO, baselines, [sample("c1")])
    [finding] = found(report, "baseline_invalid")
    assert (finding.turn, finding.subject) == (1, "choice_negated")


@pytest.mark.parametrize("change", [None, unrealize_ledger, negate_song, swap_turn_one])
def test_one_sample_each_gives_the_one_run_verdict(change):
    baseline, candidate = sample("b"), sample("c", change)
    single = compare_v2(SCENARIO, baseline, candidate)
    sampled = compare_samples(SCENARIO, [baseline], [candidate])
    assert sampled.passed == single.passed
    assert ({(f.check, f.turn, f.subject, f.evidence) for f in sampled.findings}
            == {(f.check, f.turn, f.subject, f.evidence) for f in single.findings})


def test_voice_compares_means_and_allows_for_noise_only_with_enough_samples():
    n = MIN_SAMPLES_FOR_NOISE
    baselines = [sample(f"b{i}") for i in range(n)]
    one_swapped = [*(sample(f"c{i}") for i in range(n - 1)), sample("cx", swap_turn_one)]
    report = compare_samples(SCENARIO, baselines, one_swapped)
    assert report.voice.candidate_mean < report.voice.baseline_mean
    assert report.voice.noise_allowance > 0 and not found(report, "voice_regression")
    # The same drop with no spread is no noise: every sample is lower, so voice regressed.
    all_swapped = [sample(f"c{i}", swap_turn_one) for i in range(n)]
    report = compare_samples(SCENARIO, baselines, all_swapped)
    assert report.voice.noise_allowance == 0.0 and found(report, "voice_regression")
    # Below MIN_SAMPLES_FOR_NOISE a side gets no allowance, as one run gets none.
    report = compare_samples(SCENARIO, baselines[1:], one_swapped)
    assert report.voice.noise_allowance == 0.0 and found(report, "voice_regression")


def test_noise_allowance_is_twice_the_standard_error_of_the_difference():
    base = [Fraction(1)] * 5
    cand = [Fraction(1)] * 4 + [Fraction(1, 2)]
    # candidate variance: mean 9/10, squared deviations 4 x 1/100 + 16/100 = 1/5, over 4 = 1/20
    assert noise_allowance(base, cand) == pytest.approx(2 * (1 / 20 / 5) ** 0.5)
    assert noise_allowance(base[:4], cand) == 0.0


def test_every_sample_is_checked_for_comparability_against_the_first_baseline():
    def other_model(raw):
        raw["conditions"]["model"] = "another/model"

    report = compare_samples(SCENARIO, [sample("b1"), sample("b2", other_model)],
                             [sample("c1"), sample("c2", other_model)])
    messages = sorted(f.message for f in found(report, "incomparable"))
    assert messages == ["sample 2: recording condition differs from the baseline"] * 2
    with pytest.raises(ValueError):
        compare_samples(SCENARIO, [], [sample("c")])


def test_the_sampled_report_states_its_rule_and_every_samples_numbers_never_text():
    report = compare_samples(SCENARIO, [sample(f"b{i}") for i in range(3)],
                             [sample("c1"), sample("c2"), sample("c3", unrealize_ledger)])
    text = render_samples(report)
    assert text.startswith("story-quality gate: PASS\nsamples: 3 baseline, 3 candidate")
    assert "strict majority" in text and "  - ask-ledger: 3/3 / 2/3" in text
    assert "baseline samples: sample 1=b0" in text and "candidate samples: sample 1=c1" in text
    for turn in RAW["turns"]:
        for line in turn["contributions"]:
            assert line["text"] not in text
