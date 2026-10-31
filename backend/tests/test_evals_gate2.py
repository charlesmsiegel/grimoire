"""The /2 gate: hard checks as /1, usage judged from the rebuilt ledger and verified evidence."""

import copy

from evals_helpers import (
    attempt_v2, bundle, guild_run_v2, live_source, minimal_run_v2, minimal_scenario, mutate,
)
from grimoire.evals.format import parse_scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.gate2 import compare_v2, usage_status
from grimoire.evals.loader import evidence_digest
from grimoire.evals.policy import GatePolicy
from grimoire.evals.report import (
    MEASURED_NOTICE, NOT_VERIFIED_NOTICE, SYNTHETIC_NOTICE, UNVERIFIED_NOTICE, render_v2,
)
from grimoire.evals.v021 import convert, verify_evidence

MINI = parse_scenario(minimal_scenario())


def guild():
    raw_scenario, raw_run = guild_run_v2()
    return parse_scenario(raw_scenario), raw_run


def checks(report):
    return {finding.check for finding in report.findings}


def subjects(report, check):
    return {finding.subject for finding in report.findings if finding.check == check}


def test_v2_self_compare_passes_with_synthetic_notice():
    scenario, raw = guild()
    run = parse_run_v2(raw, scenario)
    report = compare_v2(scenario, run, run)
    assert report.passed, report.findings
    assert report.baseline.status == "synthetic"
    assert report.baseline.weighted_cost is not None
    text = render_v2(report)
    assert text.startswith("story-quality gate: PASS") and SYNTHETIC_NOTICE in text
    assert MEASURED_NOTICE not in text


def live(raw, measurement="provider_reported"):
    def change(r):
        r["source"] = live_source()
        r["choice_outcomes"] = {k: "unlabelled" for k in r["choice_outcomes"]}
        for call in r["calls"]:
            for each in call["attempts"]:
                each["measurement"] = measurement
    return mutate(raw, change)


def test_live_without_evidence_is_declared_unverified():
    scenario, raw = guild()
    run = parse_run_v2(live(raw), scenario)
    assert run.usage.provider_reported_only
    assert usage_status(run, None) == "provider_reported_unverified"
    report = compare_v2(scenario, run, run)
    text = render_v2(report)
    assert UNVERIFIED_NOTICE in text and MEASURED_NOTICE not in text
    assert "usage status                  provider-reported (declared by capture, unverified)" in text
    assert "measured:" not in text


def test_v2_runtime_source_without_verifier_is_unverified():
    run = parse_run_v2(mutate(minimal_run_v2(), lambda r: (
        r.update(source={"kind": "v2-runtime", "harness": "grimoire.runtime/1"}),
        r["calls"][0]["attempts"][0].update(measurement="provider_reported"))), MINI)
    assert usage_status(run, None) == "provider_reported_unverified"


def captured():
    data = bundle(mode="live", choice_outcomes={"c1": "unlabelled"})
    digest = evidence_digest(data)
    run = parse_run_v2(convert(data, MINI, digest=digest, run_id="cap", label="capture"), MINI)
    return data, digest, run


def test_verified_evidence_reports_measured():
    data, digest, run = captured()
    evidence = verify_evidence(run, data, digest, MINI)
    assert usage_status(run, evidence) == "measured"
    report = compare_v2(MINI, run, run, baseline_evidence=evidence, candidate_evidence=evidence)
    text = render_v2(report)
    assert report.passed, report.findings
    assert MEASURED_NOTICE in text and SYNTHETIC_NOTICE not in text and UNVERIFIED_NOTICE not in text


def test_failed_evidence_is_never_measured():
    data, digest, run = captured()
    tampered = copy.deepcopy(data)
    tampered["usage_rows"][0]["prompt_tokens"] += 1
    evidence = verify_evidence(run, tampered, evidence_digest(tampered), MINI)
    assert usage_status(run, evidence) == "provider_reported_unverified"
    report = compare_v2(MINI, run, run, candidate_evidence=evidence)
    assert subjects(report, "evidence_unverified") == {"candidate"} and not report.passed


def test_status_vocabulary():
    _, raw = guild()
    scenario, _ = guild()
    assert usage_status(parse_run_v2(live(raw, "estimated"), scenario), None) == "estimated"
    unreported = mutate(raw, lambda r: r["calls"][0]["attempts"][0]["usage"].update(
        output_tokens=None))
    assert usage_status(parse_run_v2(unreported, scenario), None) == "incomplete"


def test_unreported_cache_read_is_usage_incomplete_not_cost_regression():
    scenario, raw = guild()
    base = parse_run_v2(raw, scenario)
    # Without cache reads every prompt token looks uncached: a hidden regression either way.
    blind = parse_run_v2(mutate(raw, lambda r: r["calls"][0]["attempts"][0]["usage"].update(
        cache_read_tokens=None)), scenario)
    assert blind.usage.complete  # core's "complete" needs only input and output
    report = compare_v2(scenario, base, blind)
    assert "usage_incomplete" in checks(report) and "cost_regression" not in checks(report)
    assert subjects(report, "usage_incomplete") == {"candidate"}
    assert report.candidate.weighted_cost is None
    assert subjects(compare_v2(scenario, blind, base), "usage_incomplete") == {"baseline"}


def test_unmetered_call_is_usage_incomplete():
    def change(r):
        r["calls"][0]["attempts"] = []
        r["turns"][0]["contributions"].pop()
    run = parse_run_v2(mutate(minimal_run_v2(), change), MINI)
    report = compare_v2(MINI, parse_run_v2(minimal_run_v2(), MINI), run)
    assert "usage_incomplete" in checks(report) and report.candidate.unmetered == 1


def test_failed_attempt_tokens_count_toward_cost():
    def retried(r):
        r["policy"]["max_attempts_per_call"] = 2
        r["calls"][0]["attempts"] = [attempt_v2(100, 0, outcome="failed"), attempt_v2(attempt=2)]
    base = parse_run_v2(minimal_run_v2(), MINI)
    cand = parse_run_v2(mutate(minimal_run_v2(), retried), MINI)
    report = compare_v2(MINI, base, cand)
    assert report.baseline.weighted_cost == 105 and report.candidate.weighted_cost == 205
    assert "cost_regression" in checks(report)


def test_weighted_cost_uses_cache_reads_exactly():
    run = parse_run_v2(mutate(minimal_run_v2(), lambda r: r["calls"][0]["attempts"][0][
        "usage"].update(cache_read_tokens=40)), MINI)
    report = compare_v2(MINI, run, run, GatePolicy(cached_input_weight=0.1))
    assert report.baseline.weighted_cost == 60 + 4 + 5


def test_cost_regression_v2():
    scenario, raw = guild()
    base = parse_run_v2(raw, scenario)

    def dearer(r):
        for call in r["calls"]:
            usage = call["attempts"][0]["usage"]
            usage["input_tokens"] = int(usage["input_tokens"] * 1.2)
            usage["output_tokens"] = int(usage["output_tokens"] * 1.2)
    report = compare_v2(scenario, base, parse_run_v2(mutate(raw, dearer), scenario))
    assert "cost_regression" in checks(report) and not report.passed


def test_extra_optional_stage_is_flag_not_allowed_unless_allowed():
    def with_decision(r):
        r["policy"]["per_turn_call_caps"] = {"decision": 1}
        r["calls"].append({"call_id": "d1", "turn": 1, "phase": "select", "stage": "decision",
                           "actor_id": None, "purpose": "who answers", "latency_ms": 10,
                           "attempts": [attempt_v2(0, 0)]})
    base = parse_run_v2(minimal_run_v2(), MINI)
    cand = parse_run_v2(mutate(minimal_run_v2(), with_decision), MINI)
    assert subjects(compare_v2(MINI, base, cand), "flag_not_allowed") == {"decision"}
    allowed = compare_v2(MINI, base, cand, GatePolicy(allowed_flags=frozenset({"decision"})))
    assert "flag_not_allowed" not in checks(allowed)


def test_incomparable_conditions_and_hard_findings_carry_over():
    scenario, raw = guild()
    base = parse_run_v2(raw, scenario)
    other = parse_run_v2(mutate(raw, lambda r: r["conditions"].update(model="other")), scenario)
    assert subjects(compare_v2(scenario, base, other), "incomparable") == {"model"}
    leaky = mutate(raw, lambda r: r["turns"][0]["contributions"][1].update(
        text="The duke sold the guild, fine."))
    report = compare_v2(scenario, base, parse_run_v2(leaky, scenario))
    assert "knowledge_leak" in checks(report)


def test_latency_gate_needs_reported_latency():
    base = parse_run_v2(minimal_run_v2(), MINI)
    blind = parse_run_v2(mutate(minimal_run_v2(), lambda r: r["calls"][0].update(
        latency_ms=None)), MINI)
    report = compare_v2(MINI, base, blind, GatePolicy(max_latency_increase=0.5))
    assert subjects(report, "usage_incomplete") == {"latency"}
    assert "usage_incomplete" not in checks(compare_v2(MINI, base, blind))


def test_report_cites_no_contribution_text():
    scenario, raw = guild()
    run = parse_run_v2(raw, scenario)
    text = render_v2(compare_v2(scenario, run, run))
    for turn in raw["turns"]:
        for entry in turn["contributions"]:
            assert entry["text"] not in text


def test_report_shows_unreported_beside_sums_and_unlabelled_choices():
    scenario, raw = guild()
    unreported = mutate(live(raw), lambda r: r["calls"][0]["attempts"][0]["usage"].update(
        output_tokens=None))
    run = parse_run_v2(unreported, scenario)
    text = render_v2(compare_v2(scenario, run, run))
    assert "output tokens" in text and "(1 unreported)" in text
    assert "choices unlabelled            3" in text
    assert "weighted billed tokens        n/a (usage incomplete)" in text


def test_passing_estimated_run_is_not_called_synthetic():
    scenario, raw = guild()
    run = parse_run_v2(live(raw, "estimated"), scenario)
    text = render_v2(compare_v2(scenario, run, run))
    assert SYNTHETIC_NOTICE not in text and NOT_VERIFIED_NOTICE in text
    unverified = parse_run_v2(live(raw), scenario)
    text = render_v2(compare_v2(scenario, unverified, unverified))
    assert SYNTHETIC_NOTICE not in text and NOT_VERIFIED_NOTICE in text


def test_measured_label_admits_the_bundle_is_unauthenticated():
    assert "not authenticated" in MEASURED_NOTICE
    data, digest, run = captured()
    evidence = verify_evidence(run, data, digest, MINI)
    text = render_v2(compare_v2(MINI, run, run, baseline_evidence=evidence,
                                candidate_evidence=evidence))
    assert "origin and provider receipt unverified" in text


def test_an_unsent_attempt_does_not_make_cost_incomplete():
    def unsent_first(r):
        r["policy"]["max_attempts_per_call"] = 2
        r["calls"][0]["attempts"] = [attempt_v2(0, 0, cache_read=None, outcome="not_sent"),
                                     attempt_v2(attempt=2)]
    run = parse_run_v2(mutate(minimal_run_v2(), unsent_first), MINI)
    report = compare_v2(MINI, run, run)
    assert report.baseline.weighted_cost == 105
    assert "usage_incomplete" not in checks(report)
