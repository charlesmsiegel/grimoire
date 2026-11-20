"""Synthetic actor experiment acceptance; real adapter, scripted transport."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_decisions import oda_choice, brief as choice_brief
from grimoire.voice import SecretFact
from generation_fixtures import Harness, world, brief, say, CAMPAIGN, ODA, BELLAMY, QUILL, PRIVATE, WREN
from grimoire.core.identity import InstanceRef
from grimoire.core.inference import InferencePolicy
from grimoire.generation import GenerationError
from grimoire.providers import RouteChoice, CancelToken
from grimoire.runtime import RunFence
from grimoire.testing.providers import CancelAt, Supersede, Status
from grimoire.actor_inference import ActorExperimentRunner, ExperimentBudget, plan_experiment

HASK = InstanceRef(CAMPAIGN, 'character', 'npc-hask')
PRESENT = (ODA, BELLAMY, QUILL, HASK)
POLICY = InferencePolicy({'planning': 2, 'critique': 2})


def setup(steps, *, planners=(), critics=(), policy=None, attempts=1, clock=None):
    controls, voices = world()
    h = Harness(steps, attempts=attempts)
    plan = plan_experiment(CAMPAIGN, 't1', present=PRESENT, speakers=(ODA, BELLAMY),
                           controls=controls, planners=planners, critics=critics,
                           policy=policy or InferencePolicy(max_attempts_per_call=attempts))
    runner = ActorExperimentRunner(h.adapter, (RouteChoice('primary', 'model-a'),), h.handle,
                                    max_output_tokens=64, clock=clock)
    return h, plan, runner, controls, (brief(ODA, 'Oda', voices), brief(BELLAMY, 'Bellamy', voices))


def run_setup(data, **kw):
    h, plan, runner, controls, briefs = data
    meter = h.meter(plan)
    outcome = runner.run(meter, briefs, controls=controls, present=PRESENT,
                         budget=kw.pop('budget', ExperimentBudget(20, 2000)), **kw)
    return meter, outcome


def test_four_present_two_speakers_baseline_and_independent_opt_ins():
    for planners, critics, policy in [((), (), POLICY), ((ODA,), (BELLAMY,), None)]:
        data = setup([say('Count the silver.'), say('Darling, the evening shines.')],
                     planners=planners, critics=critics, policy=policy)
        meter, outcome = run_setup(data)
        assert outcome.status == 'complete'
        assert len(outcome.lines) == len(data[0].transport.sends) == 2
        assert PRIVATE not in data[0].prompts()[0]
        assert PRIVATE in data[0].prompts()[1]
        assert len(meter.ledger.attempts) == 2


def test_planner_and_critic_private_presentation_only():
    data = setup([say('Use clipped counting-house phrasing.'), say('Count the silver.'),
                  say('Darling, the evening shines.'), say('The cadence remains theatrical.')],
                 planners=(ODA,), critics=(BELLAMY,), policy=POLICY)
    meter, out = run_setup(data)
    assert out.status == 'complete'
    prompts = data[0].prompts()
    assert PRIVATE not in prompts[0]
    assert 'Use clipped counting-house phrasing.' in prompts[1]
    assert 'Use clipped counting-house phrasing.' not in prompts[2]
    assert out.lines[-1].text == 'Darling, the evening shines.'
    assert len(out.proposals) == 2
    assert 'Darling, the evening shines.' in prompts[3]
    assert 'Count the silver.' not in prompts[3]  # critique only own context/reply


def test_hidden_planner_rejected_baseline_continues_unchanged():
    data = setup([say('The duke sold the guild.'), say('Count the silver.'), say('Darling, splendid.')],
                 planners=(ODA,), policy=POLICY)
    _, out = run_setup(data)
    assert out.status == 'complete' and out.calls[0].status == 'rejected'
    assert not out.proposals
    assert 'The duke sold the guild.' not in data[0].prompts()[1]


def test_budget_worst_case_no_refunds_and_atomic_shared_allowance():
    budget = ExperimentBudget(2, 128)
    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(lambda _: budget.reserve(2, 64), range(20))) == 1
    assert budget.reserved_attempts == 2 and budget.reserved_output_tokens == 128
    data = setup([say('Count the silver.')], attempts=2)
    meter, out = run_setup(data, budget=ExperimentBudget(2, 128))
    assert out.status == 'budget_exhausted'
    assert len(meter.ledger.attempts) == 1 and len(out.lines) == 1
    assert out.calls[-1].status == 'budget_exhausted'


def test_cancelled_before_io_claims_meter_even_across_runners():
    data = setup([])
    token = CancelToken(); token.cancel()
    meter, out = run_setup(data, cancel=token)
    assert out.status == 'cancelled' and not data[0].transport.sends
    with pytest.raises(GenerationError, match='already_run'):
        data[2].run(meter, data[4], controls=data[3], present=PRESENT, budget=ExperimentBudget(2,128))


def test_stale_clears_all_proposals_and_retains_attempts():
    data = setup([say('Use clipped phrasing.'), Supersede(1)], planners=(ODA,), policy=POLICY)
    fence = RunFence(); token = fence.claim('chat')
    data[0].transport.on_supersede = lambda: fence.claim('chat')
    meter, out = run_setup(data, token=token)
    assert out.status == 'stale' and not out.lines and not out.proposals
    assert len(meter.ledger.attempts) == 2


@pytest.mark.parametrize('failure', ['cast', 'brief', 'controls', 'foreign'])
def test_validation_precedes_all_io(failure):
    data = setup([])
    h, plan, runner, controls, briefs = data
    present = PRESENT
    if failure == 'cast': present = PRESENT[:-1]
    if failure == 'brief': briefs = briefs[:1]
    if failure == 'controls': controls = ()
    if failure == 'foreign': briefs = (briefs[0], briefs[0])
    with pytest.raises(GenerationError):
        runner.run(h.meter(plan), briefs, controls=controls, present=present,
                   budget=ExperimentBudget(2,128))
    assert not h.transport.sends


def test_failure_stops_without_hidden_fallback_and_retry_retained():
    data = setup([Status(503), say('Use clipped phrasing.'), say('Count the silver.'), say('Darling, splendid.')],
                 planners=(ODA,), policy=InferencePolicy({'planning':1}, max_attempts_per_call=2))
    meter, out = run_setup(data)
    assert out.status == 'complete' and len(meter.ledger.attempts) == 4
    data = setup([CancelAt(1)], planners=(ODA,), policy=POLICY)
    meter, out = run_setup(data)
    assert out.status == 'cancelled' and len(meter.ledger.attempts) == 1
    assert len(data[0].transport.sends) == 1


@pytest.mark.parametrize('selected', [None, ('count', 'tally')])
def test_planning_refuses_any_existing_choice_before_io(selected):
    data = setup([], planners=(ODA,), policy=POLICY)
    choice = oda_choice(present=PRESENT, selected=selected)
    h, plan, runner, controls, briefs = data
    with pytest.raises(GenerationError, match='planning_choice'):
        runner.run(h.meter(plan), (choice_brief(choice), briefs[1]), controls=controls,
                   present=PRESENT, budget=ExperimentBudget(10,1000))
    assert not h.transport.sends


def test_untrusted_handle_requires_per_call_authorization():
    data = setup([])
    h = Harness([], approve=True)
    runner = ActorExperimentRunner(h.adapter, (RouteChoice('primary','model-a'),), h.handle)
    out = runner.run(h.meter(data[1]), data[4], controls=data[3], present=PRESENT,
                     budget=ExperimentBudget(10,10000))
    assert out.status == 'halted' and out.calls[0].status == 'refused'
    assert not h.transport.sends


@pytest.mark.parametrize('which', ['optional_absent', 'duplicate', 'foreign', 'pc'])
def test_plan_rejects_bad_identity_and_authority_even_when_optional_disabled(which):
    controls, _ = world()
    present, speakers, planners = PRESENT, (ODA,), ()
    if which == 'optional_absent': planners = (QUILL,)
    if which == 'duplicate': present = PRESENT + (ODA,)
    if which == 'foreign': speakers = (InstanceRef('foreign', 'character', ODA.instance_id),)
    if which == 'pc': present, speakers = PRESENT + (WREN,), (WREN,)
    with pytest.raises(GenerationError):
        plan_experiment(CAMPAIGN, 'turn', present=present, speakers=speakers, controls=controls, planners=planners)


def test_policy_cap_and_long_optional_output_no_extra_fallback():
    data = setup([say('x' * 1001), say('Count the silver.'), say('Darling, splendid.')],
                 planners=(ODA,BELLAMY), policy=InferencePolicy({'planning':1}))
    meter, out = run_setup(data)
    assert len(data[1].declined) == 1
    assert out.status == 'complete' and out.calls[0].reason == 'reply_too_long'
    assert not out.proposals and len(meter.ledger.attempts) == 3


def test_hearing_filter_and_cross_runner_meter_replay():
    data = setup([say('Count the silver.'), say('Darling, splendid.')])
    meter, out = run_setup(data, hears=lambda listener, speaker: False)
    assert 'Count the silver.' not in data[0].prompts()[1]
    other = ActorExperimentRunner(data[0].adapter, (RouteChoice('primary','model-a'),), data[0].handle)
    with pytest.raises(GenerationError, match='already_run'):
        other.run(meter,data[4],controls=data[3],present=PRESENT,budget=ExperimentBudget(20,20000))


def test_reentrant_runner_refuses_without_blocking_or_duplicate_call():
    data = setup([say('Count the silver.'),say('Darling, splendid.')])
    h, plan, runner, controls, briefs = data
    meter = h.meter(plan)
    def approvals(*args):
        with pytest.raises(GenerationError, match='busy'):
            runner.run(meter,briefs,controls=controls,present=PRESENT,budget=ExperimentBudget(10,1000))
        return None
    runner._approvals = approvals
    out = runner.run(meter,briefs,controls=controls,present=PRESENT,budget=ExperimentBudget(10,1000))
    assert out.status == 'complete' and len(h.transport.sends) == 2


def test_empty_experiment_honors_pre_cancellation_and_claims_meter():
    controls, _ = world()
    h = Harness([])
    plan = plan_experiment(CAMPAIGN,'empty',present=PRESENT,speakers=(),controls=controls)
    runner = ActorExperimentRunner(h.adapter,(RouteChoice('primary','model-a'),),h.handle)
    cancel = CancelToken(); cancel.cancel()
    meter = h.meter(plan)
    out = runner.run(meter,(),controls=controls,present=PRESENT,budget=ExperimentBudget(0,0),cancel=cancel)
    assert out.status == 'cancelled' and not out.lines and not out.calls
    with pytest.raises(GenerationError,match='already_run'):
        runner.run(meter,(),controls=controls,present=PRESENT,budget=ExperimentBudget(0,0))


@pytest.mark.parametrize('routes', [(), 'primary', (object(),)])
def test_invalid_routes_rejected_at_construction(routes):
    h = Harness([])
    with pytest.raises((GenerationError, ValueError)):
        ActorExperimentRunner(h.adapter,routes,h.handle)
    assert not h.transport.sends


@pytest.mark.parametrize('repeats_in_own_reply', [True, False])
def test_critique_exposure_is_only_own_reply_not_other_actors_speech(repeats_in_own_reply):
    controls, voices = world()
    secret = SecretFact('letter-private', (PRIVATE,))
    own = 'Count this report: ' + PRIVATE if repeats_in_own_reply else 'Count the silver.'
    critique = 'The phrasing is direct: ' + PRIVATE
    h = Harness([say(PRIVATE), say(own), say(critique)])
    plan = plan_experiment(CAMPAIGN, 'critique-exposure', present=PRESENT,
                           speakers=(BELLAMY, ODA), controls=controls, critics=(ODA,), policy=POLICY)
    briefs = (brief(BELLAMY, 'Bellamy', voices, secrets=(secret,)),
              brief(ODA, 'Oda', voices, secrets=(secret,)))
    runner = ActorExperimentRunner(h.adapter, (RouteChoice('primary', 'model-a'),), h.handle)
    outcome = runner.run(h.meter(plan), briefs, controls=controls, present=PRESENT,
                         budget=ExperimentBudget(3,1200))
    assert outcome.status == 'complete'
    assert len(outcome.lines) == 2
    if repeats_in_own_reply:
        assert outcome.calls[-1].status == 'accepted'
        assert outcome.proposals[0].text == critique
        assert PRIVATE in h.prompts()[-1]
    else:
        assert outcome.calls[-1].reason == 'unlearned_secret'
        assert not outcome.proposals
        assert PRIVATE not in h.prompts()[-1]
