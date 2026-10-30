"""Candidate-versus-baseline gate, including the sprint's proving scenario."""

import math

import pytest

from evals_helpers import call, checks, contribution, load_benchmark, mutate, parsed
from grimoire.evals.format import parse_run
from grimoire.evals.gate import compare
from grimoire.evals.policy import GatePolicy

SECRET = "the duke sold the guild"


@pytest.fixture
def bench():
    return load_benchmark()


def gate(bench, change=lambda run: None, policy=GatePolicy(), baseline_change=lambda run: None):
    scenario_raw, baseline_raw = bench
    scenario, baseline = parsed(scenario_raw, mutate(baseline_raw, baseline_change))
    candidate = parse_run(mutate(baseline_raw, change), scenario)
    return compare(scenario, baseline, candidate, policy)


def scale_tokens(factor):
    def change(run):
        for entry in run["calls"]:
            entry["input_tokens"] = round(entry["input_tokens"] * factor)
            entry["cached_input_tokens"] = round(entry["cached_input_tokens"] * factor)
            entry["output_tokens"] = round(entry["output_tokens"] * factor)
    return change


def test_baseline_against_itself_passes_but_is_marked_synthetic(bench):
    report = gate(bench)
    assert report.passed and report.findings == () and report.synthetic


def test_proving_scenario_fluent_but_omniscient_candidate_is_rejected(bench):
    def fluent_omniscient(run):
        run["judge_scores"] = {"fluency": 0.99, "agency": 1.0}
        contribution(run, 1, "npc-bellamy")["text"] = (
            f"Oh, darling Wren, a ledger! And, marvellous gossip, {SECRET}!")
    report = gate(bench, fluent_omniscient)
    assert not report.passed and checks(report) == {"knowledge_leak"}
    assert report.candidate.judge_scores["fluency"] == 0.99
    assert report.candidate.leak_rate == pytest.approx(1 / 6)


def test_proving_scenario_mislabelled_negations_are_rejected(bench):
    def negate_everything(run):
        run["judge_scores"] = {"fluency": 0.99, "agency": 1.0}
        for turn in run["turns"]:
            for entry in turn["contributions"]:
                if entry["author"] == "model":
                    entry["text"] = entry["text"].replace("uest ledger", "uest book").replace(
                        "alcony", "allroom").replace("sings", "hums").replace("song", "tune")
    report = gate(bench, negate_everything)
    assert checks(report) == {"choice_negated"}
    assert report.candidate.unrealized == 3 and report.candidate.negated_by_label == 0


def test_ensemble_generation_fails_even_though_it_is_cheaper(bench):
    def ensemble(run):
        for turn in (1, 2, 3):
            bellamy = contribution(run, turn, "npc-bellamy")
            run["calls"].remove(call(run, bellamy["call_id"]))
            bellamy["call_id"] = f"t{turn}-oda"
    report = gate(bench, ensemble)
    assert checks(report) == {"separate_generation"}
    assert report.candidate.weighted_cost < report.baseline.weighted_cost


def test_flattened_voices_regress(bench):
    def flatten(run):
        for turn in (1, 2, 3):
            contribution(run, turn, "npc-bellamy")["text"] = contribution(run, turn, "npc-oda")["text"]
    report = gate(bench, flatten)
    assert checks(report) == {"voice_regression"}
    assert report.candidate.distinguishability == pytest.approx(0.5)
    assert gate(bench, flatten, GatePolicy(max_voice_drop=0.5)).passed


@pytest.mark.parametrize(("factor", "passes"), [(1.05, True), (1.25, False)])
def test_cost_tolerance(bench, factor, passes):
    report = gate(bench, scale_tokens(factor))
    assert checks(report) == (set() if passes else {"cost_regression"})


def test_cost_boundary_is_exact(bench):
    # Exactly +10% weighted cost passes; one more token fails.
    def plus(extra):
        def change(run):
            run["calls"][0]["output_tokens"] += extra
        return change
    base = gate(bench).baseline.weighted_cost
    assert gate(bench, plus(int(base / 10))).passed
    assert checks(gate(bench, plus(int(base / 10) + 1))) == {"cost_regression"}


def test_losing_the_prompt_cache_costs_more(bench):
    def uncached(run):
        for entry in run["calls"]:
            entry["cached_input_tokens"] = 0
    report = gate(bench, uncached)
    assert report.candidate.weighted_cost > report.baseline.weighted_cost
    assert checks(report) == {"cost_regression"}


def test_zero_cost_baseline_fails_any_paid_candidate(bench):
    def free(run):
        for entry in run["calls"]:
            entry.update(input_tokens=0, cached_input_tokens=0, output_tokens=0, latency_ms=0)
    assert gate(bench, free, baseline_change=free).passed
    report = gate(bench, baseline_change=free, policy=GatePolicy(max_latency_increase=0.5))
    assert checks(report) == {"cost_regression", "latency_regression"}


def test_latency_is_gated_only_when_asked(bench):
    def slow(run):
        for entry in run["calls"]:
            entry["latency_ms"] *= 2
    assert gate(bench, slow).passed
    assert checks(gate(bench, slow, GatePolicy(max_latency_increase=0.25))) == {"latency_regression"}


@pytest.mark.parametrize("conditions", [
    {"model": "other-model", "budget": "standard"},
    {"model": "synthetic-rp-model", "budget": "generous"},
    {"model": "synthetic-rp-model", "budget": "standard", "temperature": "0.7"},
])
def test_changed_recording_conditions_are_incomparable(bench, conditions):
    assert checks(gate(bench, lambda run: run.update(conditions=conditions))) == {"incomparable"}


def test_a_leaky_baseline_cannot_certify_anything(bench):
    def leak(run):
        contribution(run, 3, "npc-oda")["text"] = f"Fine. {SECRET}. Quickly."
    report = gate(bench, baseline_change=leak)
    assert checks(report) == {"baseline_invalid"}
    assert report.findings[0].subject == "knowledge_leak"


def test_feature_flags_must_be_opted_into_by_the_gate(bench):
    def planning(run):
        run["enabled_flags"] = ["planning"]
        run["calls"].append({
            "call_id": "t1-plan", "turn": 1, "purpose": "planning", "feature_flag": "planning",
            "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10, "latency_ms": 50})
    assert checks(gate(bench, planning)) == {"flag_not_allowed"}
    allowed = gate(bench, planning, GatePolicy(allowed_flags=frozenset({"planning"})))
    assert allowed.passed and allowed.candidate.cost_by_flag["planning"] == 110


def test_empty_candidate_cannot_pass(bench):
    def empty(run):
        for turn in run["turns"]:
            turn["contributions"] = []
        run["calls"] = []
    found = checks(gate(bench, empty))
    assert found == {"script_incomplete", "player_input_altered", "choice_negated",
                     "no_npc_contributions"}


def test_judge_scores_cannot_rescue_or_fail(bench):
    assert gate(bench, lambda run: run.update(judge_scores={"fluency": 0.0})).passed


def test_findings_are_globally_sorted(bench):
    def several(run):
        run["conditions"] = {"model": "other", "budget": "standard"}
        contribution(run, 1, "npc-oda")["text"] = f"Fine. {SECRET}."
    found = gate(bench, several).findings
    assert [f.check for f in found] == sorted(f.check for f in found)


@pytest.mark.parametrize("bad", [
    {"max_cost_increase": -0.1}, {"max_voice_drop": math.nan}, {"cached_input_weight": math.inf},
    {"max_latency_increase": -1}, {"max_cost_increase": True}, {"allowed_flags": {"x"}},
    {"allowed_flags": frozenset({""})},
])
def test_policy_rejects_invalid_tolerances(bad):
    with pytest.raises(ValueError):
        GatePolicy(**bad)


def test_policy_reports_only_loosened_values():
    assert GatePolicy().non_defaults() == {}
    assert GatePolicy(max_cost_increase=0.5, allowed_flags=frozenset({"b", "a"})).non_defaults() == {
        "max_cost_increase": 0.5, "allowed_flags": ["a", "b"]}
