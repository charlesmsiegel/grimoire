from dataclasses import replace
from decimal import Decimal
import pytest
from test_actor_inference import setup, run_setup, ODA, BELLAMY, POLICY, PRESENT
from generation_fixtures import CAMPAIGN
from grimoire.generation import GenerationError
from grimoire.core.inference import InferencePolicy, TurnUsageLedger
from grimoire.providers.meter import TurnMeter
from grimoire.testing.providers import Reply, Status
from grimoire.evals.format import parse_scenario
from evals_helpers import minimal_scenario
from grimoire.evals.policy import GatePolicy
from grimoire.tracing.cost import Price, PriceTable
from grimoire.actor_inference import record_experiment, compare_experiments, synthetic_benchmark, BenchmarkInput, ExperimentBudget, plan_experiment


def scenario():
    return parse_scenario({**minimal_scenario(), 'scenario_id': 'actors',
        'actors': [{'actor_id': a.instance_id, 'name': name, 'control': 'director', 'voice_samples': [sample]}
                   for a, name, sample in [(ODA, 'Oda', 'Count each coin in the ledger.'),
                                            (BELLAMY, 'Bellamy', 'Darling splendid evening.')]],
        'facts': [{'fact_id': 'secret', 'markers': ['hidden key'], 'known_from': {}}],
        'established_facts': [], 'choices': [], 'script': [{'turn': 1, 'speakers': [ODA.instance_id, BELLAMY.instance_id]}],
        'pc_authorship_markers': []})


def reply(text, tokens=20):
    return Reply((text,), usage={'prompt_tokens': tokens, 'completion_tokens': 3,
                                 'prompt_tokens_details': {'cached_tokens': 2}})


def capture(*, optional=False, tokens=20, elapsed=1, text='Count the silver ledger.', missing=False):
    steps = [reply(text, tokens), reply('Darling, splendid evening.', tokens)]
    if missing: steps[0] = Reply((text,), usage=None)
    if optional: steps = [reply('Use terse cadence.', tokens)] + steps + [reply('The voice is theatrical.', tokens)]
    ticks = iter(i * elapsed for i in range(12))
    data = setup(steps, planners=(ODA,) if optional else (), critics=(BELLAMY,) if optional else (),
                 policy=POLICY if optional else None, clock=lambda: next(ticks))
    meter, out = run_setup(data)
    return meter, out


def record(meter, out, **kw):
    return record_experiment(scenario(), meter, out, run_id=kw.pop('run_id', 'r'), turn=1,
                             conditions=kw.pop('conditions', {'model':'model-a', 'budget':'max_output_tokens=64'}), **kw)


def test_record_all_attempts_split_cost_and_latency_without_billing_claim():
    meter, out = capture(optional=True)
    prices = PriceTable({'primary@primary.example/model-a': Price(Decimal('1'), Decimal('2'))})
    report = record(meter, out, prices=prices)
    assert len(report.run.calls) == 4
    assert report.baseline_usage.attempts == report.optional_usage.attempts == 2
    assert report.money.state == 'estimated' and report.money.amount > 0
    assert report.money.actual_billing is None
    assert report.run.conditions == {'model':'model-a', 'budget':'max_output_tokens=64'}
    assert all(c.latency_ms == 1000 for c in report.run.calls)
    assert all(c.latency_origin == 'synthetic' for c in report.timings)
    assert len(report.attempt_reports) == 4
    assert all(a.measurement.value == 'synthetic' for a in report.attempt_reports)
    assert 'Use terse cadence.' not in str(report.raw)


def test_unknown_usage_not_zero_and_poison_or_tamper_refused():
    meter, out = capture(missing=True)
    report = record(meter, out)
    assert report.run.calls[0].attempts[0].usage.input_tokens is None
    assert report.money.amount is None and report.money.state == 'unpriced'
    base = record(*capture())
    gate = compare_experiments(scenario(), base, report, policy=GatePolicy(max_latency_increase=1))
    assert not gate.passed and 'usage_incomplete' in {f.check for f in gate.findings}
    with pytest.raises(GenerationError, match='outcome_mismatch'):
        record(meter, replace(out, lines=()))
    with pytest.raises(GenerationError, match='outcome_mismatch'):
        record(capture()[0], out)


@pytest.mark.parametrize('change, expected', [('cost','cost_regression'), ('latency','latency_regression'),
                                             ('conditions','incomparable'), ('quality','knowledge_leak')])
def test_comparison_rejects_regressions_and_unmatched_conditions(change, expected):
    base = record(*capture())
    meter, out = capture(tokens=200 if change == 'cost' else 20, elapsed=10 if change == 'latency' else 1,
                         text='The hidden key is here.' if change == 'quality' else 'Count the silver ledger.')
    candidate = record(meter, out, conditions={'model':'different', 'budget':'max_output_tokens=64'}
                       if change == 'conditions' else {'model':'model-a', 'budget':'max_output_tokens=64'})
    gate = compare_experiments(scenario(), base, candidate, policy=GatePolicy(max_latency_increase=.1))
    assert not gate.passed
    assert expected in {f.check for f in gate.findings}


def test_deterministic_injected_benchmark_runs_both_variants_and_reports_synthetic():
    def factory(variant):
        optional = variant == 'planner_critic'
        steps = [reply('Count the silver ledger.'), reply('Darling, splendid evening.')]
        if optional: steps = [reply('Use terse cadence.')] + steps + [reply('The voice is theatrical.')]
        ticks = iter(range(10))
        data = setup(steps, planners=(ODA,) if optional else (), critics=(BELLAMY,) if optional else (),
                     policy=POLICY if optional else None, clock=lambda: next(ticks))
        h, plan, runner, controls, briefs = data
        return BenchmarkInput(runner, h.meter(plan), briefs, controls, PRESENT, ExperimentBudget(10,1000))
    policy = GatePolicy(max_cost_increase=1, max_latency_increase=1, allowed_flags=frozenset({'planning','critique'}))
    result = synthetic_benchmark(scenario(), factory, conditions={'model':'model-a','budget':'max_output_tokens=64'}, policy=policy)
    assert result.comparison.passed
    assert result.origin == 'scripted synthetic; not live parity'
    assert result.baseline.run.usage.total.attempts == 2
    assert result.candidate.run.usage.total.attempts == 4


def test_failed_retry_unknown_usage_and_all_attempt_latencies_retained():
    data = setup([Status(503), reply('Use terse cadence.'), reply('Count the silver ledger.'),
                  reply('Darling splendid evening.')], planners=(ODA,),
                  policy=InferencePolicy({'planning':1}, max_attempts_per_call=2))
    meter, out = run_setup(data)
    report = record(meter, out)
    assert report.optional_usage.attempts == 2 and report.baseline_usage.attempts == 2
    assert len(report.run.calls[0].attempts) == 2
    assert report.run.calls[0].attempts[0].outcome == 'failed'
    assert report.run.calls[0].attempts[0].usage.input_tokens is None
    assert len(report.attempt_reports) == 4
    assert report.attempt_reports[0].total_s is not None


def test_poisoned_meter_refuses_recording_missing_attempts():
    data = setup([reply('Count the silver ledger.')])
    h, plan, runner, controls, briefs = data
    meter = TurnMeter(TurnUsageLedger(plan), _fail_booking=lambda _: True)
    out = runner.run(meter, briefs, controls=controls,present=PRESENT,budget=ExperimentBudget(10,1000))
    with pytest.raises(GenerationError,match='poisoned_meter'):
        record(meter,out)


def test_benchmark_matching_and_allowed_flags_fail_closed():
    base = record(*capture())
    candidate = record(*capture(optional=True))
    gate = compare_experiments(scenario(),base,candidate,policy=GatePolicy(max_cost_increase=2,max_latency_increase=2))
    assert not gate.passed and 'flag_not_allowed' in {f.check for f in gate.findings}


def test_unissued_usage_has_unknown_money_and_unknown_call_latency():
    data = setup([])
    meter, out = run_setup(data,budget=ExperimentBudget(0,0))
    report = record(meter,out,prices=PriceTable({}))
    assert report.money.amount is None
    assert not report.money.exact_usage
    assert all(c.latency_ms is None for c in report.run.calls)
    assert report.run.usage.unmetered_call_ids


def test_quality_evidence_separate_from_usage_and_semantic_characterization_unknown():
    report = record(*capture())
    assert report.quality.voice_distinguishability is not None
    assert report.quality.secrecy_leak_rate == 0
    assert report.quality.characterization_score is None
    assert 'marker' in report.quality.limits


def test_benchmark_rejects_reversed_speaking_order_before_sends():
    harnesses = []
    def factory(variant):
        optional = variant == 'planner_critic'
        data = setup([], planners=(ODA,) if optional else (), critics=(BELLAMY,) if optional else (),
                     policy=POLICY if optional else None)
        h, plan, runner, controls, briefs = data
        harnesses.append(h)
        if optional:
            plan = plan_experiment(CAMPAIGN, 't1', present=PRESENT, speakers=(BELLAMY, ODA),
                                   controls=controls, planners=(ODA,), critics=(BELLAMY,), policy=POLICY)
        return BenchmarkInput(runner, h.meter(plan), briefs, controls, PRESENT, ExperimentBudget(10,1000))
    with pytest.raises(ValueError, match='must match'):
        synthetic_benchmark(scenario(), factory,
                            conditions={'model':'model-a','budget':'max_output_tokens=64'},
                            policy=GatePolicy(allowed_flags=frozenset({'planning','critique'})))
    assert len(harnesses) == 2
    assert all(not h.transport.sends for h in harnesses)
