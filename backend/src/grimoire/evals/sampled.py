"""Gate several samples of each side at once, so one noisy reply cannot decide the verdict (068).

A billed play is one draw from a model that answers differently each time: with six NPC lines, one
line voiced off its profile moves cast distinguishability by a sixth, and one reply that happens not
to name a choice's effect fails the choice. This gate reads the same requirement as
:func:`grimoire.evals.gate2.compare_v2` over every sample of both sides:

* every comparability check (scenario, recording conditions, opted-in stages, supplied evidence)
  holds for every sample against the first baseline sample;
* every hard invariant holds in **every** sample: a leak, a model line that negates a choice, a
  missing scripted speaker or any other invariant in any one sample fails the gate (on the baseline
  side the baseline is invalid);
* a choice is given effect when model text realizes it in a strict majority of a side's samples;
* voice regresses when the candidate's mean distinguishability is below the baseline's mean by more
  than ``max_voice_drop`` plus a noise allowance of twice the standard error of the difference of
  the two means. The allowance is computed only when both sides have at least
  :data:`MIN_SAMPLES_FOR_NOISE` samples; otherwise it is zero;
* cost and latency compare the sides' means with the policy's tolerances; a sample with unreported
  counts makes cost incomparable, as it does for one run.

With one sample on each side every rule is the one-run rule, so the verdict is ``compare_v2``'s.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from .checks import Finding, finding_key, hard_findings
from .format import Scenario
from .format2 import RunV2
from .format3 import FORMAT_V3
from .gate import _increase_exceeds
from .gate2 import ScorecardV2, baseline_invalid, pair_findings, scorecard_v2
from .policy import GatePolicy
from .v021 import EvidenceCheck

#: Fewest samples per side before the voice rule allows for sampling noise.
MIN_SAMPLES_FOR_NOISE = 5
#: Standard errors of the difference of the means that the voice rule allows for noise.
NOISE_STANDARD_ERRORS = 2
#: The evidence id of the choice check's "no model text gives effect" finding.
UNREALIZED = "unrealized"


@dataclass(frozen=True, slots=True)
class VoiceSummary:
    baseline: tuple[Fraction, ...]
    candidate: tuple[Fraction, ...]
    baseline_mean: Fraction | None
    candidate_mean: Fraction | None
    #: Twice the standard error of the difference of the means; 0.0 below MIN_SAMPLES_FOR_NOISE.
    noise_allowance: float


@dataclass(frozen=True, slots=True)
class SampledReport:
    passed: bool
    findings: tuple[Finding, ...]
    baseline_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    baselines: tuple[ScorecardV2, ...]
    candidates: tuple[ScorecardV2, ...]
    #: choice id -> (baseline samples realizing it, candidate samples realizing it)
    realized: dict[str, tuple[int, int]]
    voice: VoiceSummary
    baseline_cost: Fraction | None
    candidate_cost: Fraction | None
    policy: GatePolicy


def _in(sample: int, finding: Finding) -> Finding:
    return Finding(finding.check, finding.basis, f"sample {sample}: {finding.message}", finding.turn,
                   finding.subject, finding.evidence)


def _standard_error(values: Sequence[Fraction]) -> Fraction:
    """The squared standard error of the mean of ``values`` (sample variance over n)."""
    mean = sum(values, Fraction(0)) / len(values)
    variance = sum(((value - mean) ** 2 for value in values), Fraction(0)) / (len(values) - 1)
    return variance / len(values)


def noise_allowance(baseline: Sequence[Fraction], candidate: Sequence[Fraction]) -> float:
    """What the voice rule allows for sampling noise: 0.0 unless both sides have enough samples."""
    if min(len(baseline), len(candidate)) < MIN_SAMPLES_FOR_NOISE:
        return 0.0
    squared = _standard_error(baseline) + _standard_error(candidate)
    return NOISE_STANDARD_ERRORS * math.sqrt(squared)


def _mean(values: Sequence[Fraction]) -> Fraction:
    return sum(values, Fraction(0)) / len(values)


def compare_samples(scenario: Scenario, baselines: Sequence[RunV2], candidates: Sequence[RunV2],
                    policy: GatePolicy = GatePolicy(), *,
                    baseline_evidence: Sequence[EvidenceCheck | None] = (),
                    candidate_evidence: Sequence[EvidenceCheck | None] = ()) -> SampledReport:
    """Gate ``candidates`` against ``baselines``, each one or more samples of the same play.

    ``baseline_evidence`` and ``candidate_evidence``, when given, hold one check per sample, in
    order. Runs of different formats are refused (``ValueError``), as ``compare_v2`` refuses them.
    """
    if not baselines or not candidates:
        raise ValueError("each side needs at least one sample")
    for side, runs, evidence in (("baseline", baselines, baseline_evidence),
                                 ("candidate", candidates, candidate_evidence)):
        if evidence and len(evidence) != len(runs):
            raise ValueError(f"{side} evidence must hold one check per sample")
    first = baselines[0]
    findings: list[Finding] = []
    for sample, run in enumerate(baselines[1:], start=2):
        for finding in pair_findings(scenario, first, run, policy):
            findings.append(_in(sample, baseline_invalid(finding))
                            if finding.check != "incomparable" else _in(sample, finding))
    for sample, run in enumerate(candidates, start=1):
        findings.extend(_in(sample, finding) for finding in pair_findings(scenario, first, run, policy))
    for side, evidences in (("baseline", baseline_evidence), ("candidate", candidate_evidence)):
        for sample, evidence in enumerate(evidences, start=1):
            if evidence is not None and not evidence.ok:
                findings.append(Finding("evidence_unverified", "comparison",
                                        f"sample {sample}: supplied evidence does not verify: "
                                        f"{evidence.reason}", subject=side))

    realized = {choice.choice_id: [0, 0] for choice in scenario.choices}
    for index, (side, runs) in enumerate((("baseline", baselines), ("candidate", candidates))):
        missed: dict[str, Finding] = {}
        for sample, run in enumerate(runs, start=1):
            seen: set[str] = set()
            for finding in hard_findings(scenario, run.scoring_view(),
                                         director_narration=run.format == FORMAT_V3):
                if finding.check == "choice_negated" and finding.evidence == UNREALIZED:
                    seen.add(finding.subject or "")
                    missed.setdefault(finding.subject or "", finding)
                    continue
                findings.append(_in(sample, baseline_invalid(finding)) if side == "baseline"
                                else _in(sample, finding))
            for choice_id, counts in realized.items():
                counts[index] += choice_id not in seen
        for choice_id, finding in missed.items():
            given = realized[choice_id][index]
            if 2 * given > len(runs):
                continue
            share = f"model text gives effect to the choice in {given} of {len(runs)} samples"
            if side == "baseline":
                findings.append(Finding("baseline_invalid", "comparison",
                                        f"the baseline's {share}, not a majority", finding.turn,
                                        finding.check, finding.subject))
            else:
                findings.append(Finding(finding.check, finding.basis, f"{share}, not a majority",
                                        finding.turn, finding.subject, finding.evidence))

    base_cards = tuple(scorecard_v2(scenario, run, policy, evidence) for run, evidence
                       in zip(baselines, baseline_evidence or [None] * len(baselines)))
    cand_cards = tuple(scorecard_v2(scenario, run, policy, evidence) for run, evidence
                       in zip(candidates, candidate_evidence or [None] * len(candidates)))
    voice = _voice(scenario, baselines, candidates, base_cards, cand_cards, policy, findings)
    base_cost, cand_cost = _cost(base_cards, cand_cards, policy, findings)
    _latency(base_cards, cand_cards, policy, findings)

    ordered = tuple(sorted(findings, key=finding_key))
    return SampledReport(
        passed=not ordered, findings=ordered,
        baseline_ids=tuple(run.run_id for run in baselines),
        candidate_ids=tuple(run.run_id for run in candidates),
        baselines=base_cards, candidates=cand_cards,
        realized={choice_id: (counts[0], counts[1]) for choice_id, counts in realized.items()},
        voice=voice, baseline_cost=base_cost, candidate_cost=cand_cost, policy=policy)


def _voice(scenario: Scenario, baselines: Sequence[RunV2], candidates: Sequence[RunV2],
           base_cards: Sequence[ScorecardV2], cand_cards: Sequence[ScorecardV2],
           policy: GatePolicy, findings: list[Finding]) -> VoiceSummary:
    # As in compare_v2: a scenario-only chat (/3) with no NPC line on either side has no cast.
    scene_only = all(card.npc_lines == 0 and card.narration_lines > 0
                     for card in (*base_cards, *cand_cards))
    values: list[list[Fraction]] = [[], []]
    for index, (runs, cards) in enumerate(((baselines, base_cards), (candidates, cand_cards))):
        for sample, card in enumerate(cards, start=1):
            if card.distinguishability is not None:
                values[index].append(card.distinguishability)
            elif not scene_only and index == 0:
                findings.append(Finding("baseline_invalid", "comparison",
                                        f"sample {sample}: the baseline has no NPC lines, so voice "
                                        f"cannot be compared", subject="no_npc_contributions"))
            elif not scene_only:
                findings.append(Finding("no_npc_contributions", "comparison",
                                        f"sample {sample}: candidate has no NPC lines to score"))
    base, cand = values
    complete = len(base) == len(baselines) and len(cand) == len(candidates)
    base_mean = _mean(base) if complete and base else None
    cand_mean = _mean(cand) if complete and cand else None
    allowance = noise_allowance(base, cand) if complete and base and cand else 0.0
    if base_mean is not None and cand_mean is not None:
        floor = base_mean - policy.exact("max_voice_drop")
        regressed = (cand_mean < floor if allowance == 0.0
                     else float(cand_mean) < float(floor) - allowance)
        if regressed:
            findings.append(Finding(
                "voice_regression", "comparison",
                f"mean cast distinguishability fell from {float(base_mean):.3f} "
                f"({len(base)} samples) to {float(cand_mean):.3f} ({len(cand)} samples), "
                f"beyond the allowed drop {policy.max_voice_drop:.3f} and noise allowance "
                f"{allowance:.3f}"))
    return VoiceSummary(tuple(base), tuple(cand), base_mean, cand_mean, allowance)


def _cost(base_cards: Sequence[ScorecardV2], cand_cards: Sequence[ScorecardV2], policy: GatePolicy,
          findings: list[Finding]) -> tuple[Fraction | None, Fraction | None]:
    means: list[Fraction | None] = []
    for who, cards in (("baseline", base_cards), ("candidate", cand_cards)):
        missing = [sample for sample, card in enumerate(cards, start=1) if card.weighted_cost is None]
        for sample in missing:
            findings.append(Finding("usage_incomplete", "comparison",
                                    f"sample {sample}: input, output or cache-read counts are "
                                    f"unreported, or a call is unmetered, so cost cannot be "
                                    f"compared", subject=who))
        means.append(None if missing else _mean([card.weighted_cost for card in cards]))
    base, cand = means
    if base is not None and cand is not None and _increase_exceeds(
            cand, base, policy.exact("max_cost_increase")):
        findings.append(Finding("cost_regression", "comparison",
                                f"mean weighted billed tokens rose from {float(base):.1f} "
                                f"to {float(cand):.1f}"))
    return base, cand


def _latency(base_cards: Sequence[ScorecardV2], cand_cards: Sequence[ScorecardV2], policy: GatePolicy,
             findings: list[Finding]) -> None:
    if policy.max_latency_increase is None:
        return
    if any(card.latency_unreported for card in (*base_cards, *cand_cards)):
        findings.append(Finding("usage_incomplete", "comparison",
                                "a call's latency is unreported, so latency cannot be gated",
                                subject="latency"))
        return
    base = _mean([Fraction(card.latency_ms_reported) for card in base_cards])
    cand = _mean([Fraction(card.latency_ms_reported) for card in cand_cards])
    if _increase_exceeds(cand, base, policy.exact("max_latency_increase")):
        findings.append(Finding("latency_regression", "comparison",
                                f"mean total call latency rose from {float(base):.0f} ms "
                                f"to {float(cand):.0f} ms"))
