"""Compare two ``/2`` runs: the ``/1`` hard checks and voice metric, then usage from the ledger.

Usage is judged only from the ledgers ``parse_run_v2`` rebuilt. A count that was not reported
is missing, never zero, so a cost comparison needs every attempt's input, output and cache-read
counts on both sides; otherwise the comparison says ``usage_incomplete`` instead of guessing.
"Measured" needs verified capture evidence as well as a provider-reported ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from grimoire.core.inference import Measurement, UsageTotals

from .checks import Finding, distinguishability, finding_key, hard_findings, scorecard
from .format import Scenario
from .format2 import RunV2
from .gate import _increase_exceeds
from .policy import GatePolicy
from .v021 import EvidenceCheck

UsageStatus = Literal["measured", "provider_reported_unverified", "estimated", "synthetic",
                      "incomplete"]
COSTED_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens")


def usage_status(run: RunV2, evidence: EvidenceCheck | None) -> UsageStatus:
    usage = run.usage
    if not usage.complete:
        return "incomplete"
    if usage.provider_reported_only:
        return "measured" if evidence is not None and evidence.ok else "provider_reported_unverified"
    if Measurement.SYNTHETIC in usage.measurements:
        return "synthetic"
    return "estimated"


@dataclass(frozen=True, slots=True)
class ScorecardV2:
    status: UsageStatus
    source: str
    npc_lines: int
    leak_rate: Fraction | None
    negated_by_label: int
    negated_by_evidence: int
    unrealized: int
    unlabelled: int
    distinguishability: Fraction | None
    calls_by_stage: Mapping[str, int]
    attempts: int
    unmetered: int
    sums: Mapping[str, int]
    unreported: Mapping[str, int]
    #: None when any attempt left input, output or cache reads unreported.
    weighted_cost: Fraction | None
    cost_usd_reported: float
    cost_unreported: int
    cost_bases: tuple[str, ...]
    latency_ms_reported: int
    latency_unreported: int
    by_actor: Mapping[str | None, UsageTotals]
    judge_scores: Mapping[str, float]


def _weighted(run: RunV2, weight: Fraction) -> Fraction | None:
    total = Fraction(0)
    for call in run.calls:
        for each in call.attempts:
            if each.outcome == "not_sent":  # used nothing; an absent cache count hides no cost
                continue
            usage = each.usage
            if any(getattr(usage, name) is None for name in COSTED_FIELDS):
                return None
            total += (usage.input_tokens - usage.cache_read_tokens
                      + usage.cache_read_tokens * weight + usage.output_tokens)
    if run.usage.unmetered_call_ids:
        return None
    return total


def scorecard_v2(scenario: Scenario, run: RunV2, policy: GatePolicy,
                 evidence: EvidenceCheck | None = None) -> ScorecardV2:
    view = run.scoring_view()
    base = scorecard(scenario, view, policy)
    stages: dict[str, int] = {}
    for call in run.calls:
        stages[call.stage.value] = stages.get(call.stage.value, 0) + 1
    costs = [cost for call in run.calls for each, cost in zip(call.attempts, call.costs)
             if each.outcome != "not_sent"]
    source = run.source.kind + (f" ({run.source.mode})" if run.source.mode else "")
    return ScorecardV2(
        status=usage_status(run, evidence),
        source=source,
        npc_lines=base.npc_lines,
        leak_rate=base.leak_rate,
        negated_by_label=base.negated_by_label,
        negated_by_evidence=base.negated_by_evidence,
        unrealized=base.unrealized,
        unlabelled=sum(1 for label in run.choice_outcomes.values() if label == "unlabelled"),
        distinguishability=distinguishability(scenario, view),
        calls_by_stage=dict(sorted(stages.items())),
        attempts=run.usage.total.attempts,
        unmetered=len(run.usage.unmetered_call_ids),
        sums=run.usage.total.sums,
        unreported=run.usage.total.unreported,
        weighted_cost=_weighted(run, policy.exact("cached_input_weight")),
        cost_usd_reported=sum(cost.usd for cost in costs if cost is not None),
        cost_unreported=sum(1 for cost in costs if cost is None),
        cost_bases=tuple(sorted({cost.basis for cost in costs if cost is not None})),
        latency_ms_reported=sum(call.latency_ms or 0 for call in run.calls),
        latency_unreported=sum(1 for call in run.calls if call.latency_ms is None),
        by_actor=run.usage.by_actor,
        judge_scores=run.judge_scores,
    )


@dataclass(frozen=True, slots=True)
class GateReportV2:
    passed: bool
    findings: tuple[Finding, ...]
    baseline_id: str
    candidate_id: str
    baseline: ScorecardV2
    candidate: ScorecardV2
    policy: GatePolicy


def compare_v2(scenario: Scenario, baseline: RunV2, candidate: RunV2,
               policy: GatePolicy = GatePolicy(), *,
               baseline_evidence: EvidenceCheck | None = None,
               candidate_evidence: EvidenceCheck | None = None) -> GateReportV2:
    findings: list[Finding] = []
    if not baseline.scenario_id == candidate.scenario_id == scenario.scenario_id:
        findings.append(Finding("incomparable", "comparison", "runs are for different scenarios"))
    for key in sorted(set(baseline.conditions) | set(candidate.conditions)):
        if baseline.conditions.get(key) != candidate.conditions.get(key):
            findings.append(Finding("incomparable", "comparison",
                                    "recording condition differs from the baseline", subject=key))
    opted_in = set(baseline.enabled_stages) | policy.allowed_flags
    for stage in sorted(set(candidate.enabled_stages) - opted_in):
        findings.append(Finding("flag_not_allowed", "comparison",
                                "candidate enables an optional stage the gate did not allow",
                                subject=stage))
    for who, evidence in (("baseline", baseline_evidence), ("candidate", candidate_evidence)):
        if evidence is not None and not evidence.ok:
            findings.append(Finding("evidence_unverified", "comparison",
                                    f"supplied evidence does not verify: {evidence.reason}",
                                    subject=who))
    for finding in hard_findings(scenario, baseline.scoring_view()):
        findings.append(Finding("baseline_invalid", "comparison",
                                "the baseline itself violates a hard invariant", finding.turn,
                                finding.check, finding.subject))
    findings.extend(hard_findings(scenario, candidate.scoring_view()))

    base = scorecard_v2(scenario, baseline, policy, baseline_evidence)
    cand = scorecard_v2(scenario, candidate, policy, candidate_evidence)
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
    missing = [who for who, card in (("baseline", base), ("candidate", cand))
               if card.weighted_cost is None]
    for who in missing:
        findings.append(Finding("usage_incomplete", "comparison",
                                "input, output or cache-read counts are unreported, or a call "
                                "is unmetered, so cost cannot be compared", subject=who))
    if not missing and _increase_exceeds(cand.weighted_cost, base.weighted_cost,
                                         policy.exact("max_cost_increase")):
        findings.append(Finding("cost_regression", "comparison",
                                f"weighted billed tokens rose from {float(base.weighted_cost):.1f} "
                                f"to {float(cand.weighted_cost):.1f}"))
    if policy.max_latency_increase is not None:
        if base.latency_unreported or cand.latency_unreported:
            findings.append(Finding("usage_incomplete", "comparison",
                                    "a call's latency is unreported, so latency cannot be gated",
                                    subject="latency"))
        elif _increase_exceeds(cand.latency_ms_reported, base.latency_ms_reported,
                               policy.exact("max_latency_increase")):
            findings.append(Finding("latency_regression", "comparison",
                                    f"total call latency rose from {base.latency_ms_reported} ms "
                                    f"to {cand.latency_ms_reported} ms"))

    ordered = tuple(sorted(findings, key=finding_key))
    return GateReportV2(passed=not ordered, findings=ordered, baseline_id=baseline.run_id,
                        candidate_id=candidate.run_id, baseline=base, candidate=cand,
                        policy=policy)
