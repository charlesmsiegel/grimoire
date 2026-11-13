"""Director-free /2 records and explicitly synthetic comparison plumbing.

Price estimates are separate from the interchange's billed costs. The current meter
supplies no invoice evidence: its cost_evidence boolean is not an actual bill.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from grimoire.core.inference import TOKEN_FIELDS, UsageTotals, Measurement
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.checks import hard_findings, scorecard
from grimoire.evals.policy import GatePolicy
from grimoire.generation import GenerationError
from grimoire.tracing.cost import usage_cost
from .run import validate_record


@dataclass(frozen=True, slots=True)
class MoneyEstimate:
    amount: Decimal | None
    state: str
    exact_usage: bool
    actual_billing: None = None
    basis: str = 'PriceTable usage estimate; not provider-billed'


@dataclass(frozen=True, slots=True)
class QualityEvidence:
    voice_distinguishability: Fraction | None
    secrecy_leak_rate: Fraction | None
    findings: tuple
    characterization_score: None = None
    limits: str = ('Existing lexical voice and marker invariants only; semantic characterization, '
                   'secrecy and player-authorship guarantees are unmeasured.')


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    raw: dict
    run: object
    baseline_usage: UsageTotals
    optional_usage: UsageTotals
    money: MoneyEstimate
    timings: tuple
    attempt_reports: tuple
    quality: QualityEvidence


def record_experiment(scenario, meter, outcome, *, run_id, turn, conditions,
                      player_lines=(), choice_outcomes=None, prices=None):
    """Copy planned calls and every attempt; optional text never enters the transcript.

    Whole-call times include retries/backoff. Attempt timing remains separate in
    attempt_reports. Conditions are caller-supplied capture metadata, never inferred.
    Missing usage stays None; an unissued call retains an empty attempt list.
    """
    validate_record(meter, outcome)
    plan = meter.ledger.plan
    ids = {call.call_id: f'c{index}' for index, call in enumerate(plan.calls, 1)}
    timings = {c.call_id: c for c in outcome.calls}
    calls = []
    for call in plan.calls:
        attempts = [a for a in meter.ledger.attempts if a.call_id == call.call_id]
        elapsed = timings[call.call_id].elapsed_s
        calls.append({'call_id': ids[call.call_id], 'turn': turn, 'phase': call.call_id.phase,
                      'stage': call.stage.value, 'actor_id': call.actor_id, 'purpose': call.purpose,
                      'latency_ms': None if elapsed is None else round(elapsed * 1000),
                      'attempts': [{'attempt': a.attempt, 'outcome': a.outcome,
                                    'measurement': a.measurement.value,
                                    'usage': {name: getattr(a.usage, name) for name in TOKEN_FIELDS},
                                    **({'route': a.route} if a.route is not None else {})}
                                   for a in attempts]})
    contributions = [{'speaker_id': actor, 'author': 'player', 'text': text} for actor, text in player_lines]
    contributions += [{'speaker_id': line.actor.instance_id, 'author': 'model', 'text': line.text,
                       'call_id': ids[line.call_id]} for line in outcome.lines]
    raw = {'format': 'grimoire.story-quality/2', 'run_id': run_id, 'scenario_id': scenario.scenario_id,
           'label': 'Actor experiment; unreviewed proposals',
           'source': {'kind': 'v2-runtime', 'harness': 'grimoire.actor-inference/1'},
           'conditions': dict(conditions),
           'policy': {'per_turn_call_caps': {s.value: cap for s, cap in plan.policy.per_turn_call_caps},
                      'max_attempts_per_call': plan.policy.max_attempts_per_call},
           'turns': [{'turn': turn, 'contributions': contributions}], 'calls': calls,
           'choice_outcomes': dict(choice_outcomes or {})}
    run = parse_run_v2(raw, scenario)
    baseline = tuple(a for a in meter.ledger.attempts if a.call_id.stage.is_baseline)
    optional = tuple(a for a in meter.ledger.attempts if not a.call_id.stage.is_baseline)
    amount, state, exact = usage_cost(meter.ledger.attempts, prices)
    # The helper prices booked attempts only; unissued calls must not appear fully costed.
    if any(not meter.reports(c.call_id) for c in plan.calls):
        exact = False
        if amount is not None:
            state = 'partial'
        if not meter.ledger.attempts:
            amount = None
    view = run.scoring_view()
    quality = scorecard(scenario, view, GatePolicy())
    evidence = QualityEvidence(quality.distinguishability, quality.leak_rate,
                               tuple(hard_findings(scenario, view)))
    return ExperimentReport(raw, run, UsageTotals.of(baseline), UsageTotals.of(optional),
                            MoneyEstimate(amount, state, exact), outcome.calls,
                            tuple(r for c in plan.calls for r in meter.reports(c.call_id)), evidence)


def compare_experiments(scenario, baseline, candidate, *, policy: GatePolicy):
    """Existing /2 quality + weighted-token + latency gates, with explicit allowed flags.

    Weighted-token cost is not a dollar or session spending ceiling. No judge score
    is invented: the existing marker/voice checks report their own limits.
    """
    return compare_v2(scenario, baseline.run, candidate.run, policy)


@dataclass(frozen=True, slots=True)
class BenchmarkInput:
    runner: object
    meter: object
    briefs: tuple
    controls: tuple
    present: tuple
    budget: object


@dataclass(frozen=True, slots=True)
class SyntheticBenchmark:
    baseline: ExperimentReport
    candidate: ExperimentReport
    comparison: object
    origin: str = 'scripted synthetic; not live parity'


def synthetic_benchmark(scenario, factory, *, conditions, policy: GatePolicy, turn=1,
                        player_lines=(), choice_outcomes=None, prices=None):
    """Run a matched deterministic fixture injected as factory(variant).

    The factory supplies isolated runner/meter/budget and scripted provider inputs for
    'baseline' and 'planner_critic'. No provider transport or fixture is created here.
    Both cases must have identical routes/output bounds/cast and synthetic attempt
    usage and timing; baseline has no extras, candidate has planning and critique.
    The caller supplies scenario/conditions and tolerances; no real parity is claimed.
    """
    cases = [factory(name) for name in ('baseline', 'planner_critic')]
    if not all(isinstance(c, BenchmarkInput) for c in cases):
        raise ValueError('factory must return BenchmarkInput')
    base, candidate = cases
    def settings(case):
        return (case.runner._routes, case.runner._max_tokens, case.meter.ledger.plan.present_refs,
                case.meter.ledger.plan.generated_actor_ids, case.meter.ledger.plan.control_snapshot,
                case.briefs)
    if settings(base) != settings(candidate):
        raise ValueError('benchmark model, output, cast, speakers and briefs must match')
    stages = [{c.stage.value for c in case.meter.ledger.plan.calls if not c.stage.is_baseline} for case in cases]
    if stages[0] or stages[1] != {'planning', 'critique'}:
        raise ValueError('benchmark requires baseline and explicitly enabled planner/critic variants')
    # A benchmark is synthetic-only before sending, not merely labelled after paid execution.
    if any(not getattr(c.runner._adapter, '_transport', None) for c in cases):
        raise ValueError('benchmark needs an adapter with a synthetic transport')
    if any(c.runner._adapter._measurement() is not Measurement.SYNTHETIC for c in cases):
        raise ValueError('benchmark refuses non-synthetic provider transport')
    reports = []
    for name, case in zip(('baseline', 'planner_critic'), cases):
        out = case.runner.run(case.meter, case.briefs, controls=case.controls, present=case.present, budget=case.budget)
        report = record_experiment(scenario, case.meter, out, run_id=name, turn=turn, conditions=conditions,
                                   player_lines=player_lines, choice_outcomes=choice_outcomes, prices=prices)
        if (any(a.measurement is not Measurement.SYNTHETIC for a in case.meter.ledger.attempts)
                or any(c.elapsed_s is not None and c.latency_origin != 'synthetic' for c in out.calls)):
            raise ValueError('benchmark requires scripted synthetic measurements')
        reports.append(report)
    comparison = compare_experiments(scenario, *reports, policy=policy)
    return SyntheticBenchmark(*reports, comparison)
