"""Compare a candidate run with a baseline: hard invariants first, then regressions."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .checks import Finding, Scorecard, finding_key, hard_findings, scorecard
from .format import Run, Scenario
from .policy import GatePolicy


@dataclass(frozen=True, slots=True)
class GateReport:
    passed: bool
    findings: tuple[Finding, ...]
    baseline_id: str
    candidate_id: str
    baseline: Scorecard
    candidate: Scorecard
    policy: GatePolicy
    #: True when either run is synthetic: a pass is then not a cost/quality certification.
    synthetic: bool


def _increase_exceeds(candidate: Fraction | int, baseline: Fraction | int, tolerance: Fraction) -> bool:
    if baseline == 0:
        return candidate > 0
    return candidate > baseline * (1 + tolerance)


def compare(scenario: Scenario, baseline: Run, candidate: Run,
            policy: GatePolicy = GatePolicy()) -> GateReport:
    findings: list[Finding] = []
    if not baseline.scenario_id == candidate.scenario_id == scenario.scenario_id:
        findings.append(Finding("incomparable", "comparison", "runs are for different scenarios"))
    for key in sorted(set(baseline.conditions) | set(candidate.conditions)):
        if baseline.conditions.get(key) != candidate.conditions.get(key):
            findings.append(Finding("incomparable", "comparison",
                                    "recording condition differs from the baseline", subject=key))
    opted_in = set(baseline.enabled_flags) | policy.allowed_flags
    for flag in sorted(set(candidate.enabled_flags) - opted_in):
        findings.append(Finding("flag_not_allowed", "comparison",
                                "candidate enables a feature flag the gate did not allow",
                                subject=flag))
    for finding in hard_findings(scenario, baseline):
        findings.append(Finding("baseline_invalid", "comparison",
                                "the baseline itself violates a hard invariant", finding.turn,
                                finding.check, finding.subject))
    findings.extend(hard_findings(scenario, candidate))

    base, cand = scorecard(scenario, baseline, policy), scorecard(scenario, candidate, policy)
    if base.distinguishability is None:
        findings.append(Finding("baseline_invalid", "comparison",
                                "the baseline has no NPC lines, so voice cannot be compared",
                                subject="no_npc_contributions"))
    if cand.distinguishability is None:
        findings.append(Finding("no_npc_contributions", "comparison",
                                "candidate has no NPC lines to score"))
    elif (base.distinguishability is not None
          and cand.distinguishability < base.distinguishability - policy.exact("max_voice_drop")):
        findings.append(Finding("voice_regression", "comparison",
                                f"cast distinguishability fell from "
                                f"{float(base.distinguishability):.3f} to "
                                f"{float(cand.distinguishability):.3f}"))
    if _increase_exceeds(cand.weighted_cost, base.weighted_cost, policy.exact("max_cost_increase")):
        findings.append(Finding("cost_regression", "comparison",
                                f"weighted billed tokens rose from {float(base.weighted_cost):.1f} "
                                f"to {float(cand.weighted_cost):.1f}"))
    if policy.max_latency_increase is not None and _increase_exceeds(
            cand.latency_ms, base.latency_ms, policy.exact("max_latency_increase")):
        findings.append(Finding("latency_regression", "comparison",
                                f"total call latency rose from {base.latency_ms} ms to "
                                f"{cand.latency_ms} ms"))

    ordered = tuple(sorted(findings, key=finding_key))
    return GateReport(
        passed=not ordered, findings=ordered, baseline_id=baseline.run_id,
        candidate_id=candidate.run_id, baseline=base, candidate=cand, policy=policy,
        synthetic="synthetic" in (baseline.provenance, candidate.provenance),
    )
