"""051 conversation proofs with synthetic private briefs and scripted providers."""
from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, ODA, QUILL, WREN, PRIVATE, Harness, brief, say, world
from voice_fixtures import CAMPAIGN

from grimoire.core import ControlAssignment, InstanceRef
from grimoire.core.inference import InferencePolicy
from grimoire.testing.providers import Status, Supersede
from grimoire.ensemble import ConversationBid, schedule, run_conversation
from grimoire.generation import GenerationError, HeardLine
from grimoire.providers.cancel import CancelToken
from grimoire.runtime import RunFence

CAST = (WREN, ODA, BELLAMY, QUILL)
QUESTION = HeardLine('Wren', 'Oda, can we leave before dawn?', WREN)


def setup_exchange(*, silent=True, private=False, held=False):
    controls, voices = world()
    bids = (
        ConversationBid(ODA, 'answer', (WREN,), CAST, 'Answer the direct question.', 'addressed'),
        ConversationBid(BELLAMY, 'interject', (ODA,), (BELLAMY, ODA) if private else CAST,
                        'Warn about the proposed departure.', 'heard', heard_index=0),
        ConversationBid(QUILL, 'silence' if silent else 'answer', (WREN,), CAST,
                        'Listen while the others answer.' if silent else 'Respond to the question.',
                        'listening' if silent else 'heard', heard_index=None if silent else 0),
    )
    plan = schedule(CAMPAIGN, 'exchange', present=CAST, controls=controls, initiator=WREN,
                    addressees=(ODA,), floor=WREN if held else ODA, player_speaking=held, bids=bids)
    briefs = [brief(actor, name, voices, heard=(QUESTION,)) for actor, name in
              ((ODA, 'Oda'), (BELLAMY, 'Bellamy'), (QUILL, 'Quill'))]
    return controls, plan, briefs


def test_direct_question_interjection_and_meaningful_silence_have_separate_private_calls():
    controls, plan, briefs = setup_exchange()
    h = Harness([say('Wait, darling. The road is flooded.'), say('Then we take the ridge. Pay first.')])
    meter = h.meter(plan.inference)
    result = run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST)
    assert result.generation.status == 'complete'
    assert [x.actor for x in result.generation.lines] == [BELLAMY, ODA]
    assert len({x.text for x in result.generation.lines}) == 2
    assert result.quiet == (plan.bids[2],)
    assert meter.summary().baseline.attempts == 2 and meter.summary().optional.attempts == 0
    prompts = h.prompts()
    assert 'brief interjection' in prompts[0] and 'direct answer' in prompts[1]
    assert PRIVATE in prompts[0] and PRIVATE not in prompts[1]
    assert plan.bids[1].reason not in prompts[1]
    assert plan.bids[2].reason not in ''.join(prompts)


def test_three_speakers_keep_three_voices_and_private_side_remark_does_not_cross_audience():
    controls, plan, briefs = setup_exchange(silent=False, private=True)
    h = Harness([say('Darling, the copper key is missing.'), say('The ridge costs extra.'), say('I shall note the toll.')])
    result = run_conversation(plan, h.runtime, h.meter(plan.inference), briefs, controls=controls, present=CAST)
    assert [line.actor for line in result.generation.lines] == [BELLAMY, ODA, QUILL]
    prompts = h.prompts()
    assert 'copper key' in prompts[1] and 'copper key' not in prompts[2]
    assert 'Counting-house' in prompts[1] and 'Counting-house' not in prompts[0]
    assert 'Darling' in prompts[0] and 'Darling' not in prompts[2]
    visible = plan.for_actor(QUILL)
    assert not any(item.actor == BELLAMY for item in visible)
    assert all(item.reason is None for item in visible if item.actor != QUILL)


def test_player_holds_floor_and_zero_call_execution_cannot_replay():
    controls, plan, briefs = setup_exchange(held=True)
    h = Harness([])
    meter = h.meter(plan.inference)
    result = run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST)
    assert result.generation.lines == () and result.quiet == () and h.transport.sends == []
    with pytest.raises(GenerationError, match='already_run'):
        run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST)


@pytest.mark.parametrize('fault', ['departed', 'control', 'foreign_brief', 'silent_brief', 'grounding', 'reason'])
def test_invalid_execution_is_refused_before_any_send(fault):
    controls, plan, briefs = setup_exchange()
    present = CAST
    if fault == 'departed':
        present = CAST[:-1]
    elif fault == 'control':
        controls = tuple(c for c in controls if c.actor_id != ODA.instance_id) + (ControlAssignment(CAMPAIGN, ODA.instance_id, 'human:other'),)
    elif fault == 'foreign_brief':
        briefs[0] = briefs[1]
    elif fault == 'silent_brief':
        briefs = briefs[:-1]
    elif fault in ('grounding', 'reason'):
        bids = list(plan.bids)
        bids[1] = replace(bids[1], heard_index=2) if fault == 'grounding' else replace(bids[1], reason="The duke's betrayal must be exposed.")
        plan = schedule(CAMPAIGN, 'exchange', present=CAST, controls=controls, initiator=WREN, addressees=(ODA,), floor=ODA, bids=bids)
    h = Harness([])
    with pytest.raises(GenerationError):
        run_conversation(plan, h.runtime, h.meter(plan.inference), briefs, controls=controls, present=present)
    assert h.transport.sends == []


def test_cancelled_and_stale_runs_keep_no_quiet_proposals_and_cannot_replay():
    controls, plan, briefs = setup_exchange()
    h = Harness([])
    cancel = CancelToken()
    cancel.cancel()
    meter = h.meter(plan.inference)
    result = run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST, cancel=cancel)
    assert result.generation.status == 'cancelled' and result.quiet == ()
    with pytest.raises(GenerationError, match='already_run'):
        run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST)
    fence = RunFence()
    token = fence.claim('chat')
    fence.claim('chat')
    result = run_conversation(plan, h.runtime, h.meter(plan.inference), briefs, controls=controls, present=CAST, token=token)
    assert result.generation.status == 'stale' and result.quiet == () and h.transport.sends == []


@pytest.mark.parametrize('change', [
    {'present': (*CAST, ODA)},
    {'present': (*CAST, InstanceRef('other', 'character', 'outsider'))},
    {'floor': InstanceRef(CAMPAIGN, 'character', 'absent')},
    {'addressees': (InstanceRef(CAMPAIGN, 'character', 'absent'),)},
])
def test_invalid_schedule_refuses(change):
    controls, plan, _ = setup_exchange()
    args = dict(present=CAST, controls=controls, initiator=WREN, addressees=(ODA,), floor=ODA, bids=plan.bids)
    args.update(change)
    with pytest.raises(GenerationError):
        schedule(CAMPAIGN, 'bad', **args)


def test_nonverbal_is_an_uncommitted_beat_not_a_model_line_and_cancel_suppresses_it():
    controls, original, briefs = setup_exchange()
    beat = ConversationBid(QUILL, 'nonverbal', (), CAST, 'Listen to the question.', 'listening',
                           nonverbal='Quill folds the unused map.')
    plan = schedule(CAMPAIGN, 'quiet', present=CAST, controls=controls, initiator=WREN,
                    addressees=(ODA,), bids=(beat,))
    h = Harness([])
    result = run_conversation(plan, h.runtime, h.meter(plan.inference), [briefs[2]], controls=controls, present=CAST)
    assert result.quiet == (beat,) and result.generation.lines == () and h.transport.sends == []
    cancel = CancelToken()
    cancel.cancel()
    cancelled = run_conversation(plan, h.runtime, h.meter(plan.inference), [briefs[2]], controls=controls, present=CAST, cancel=cancel)
    assert cancelled.generation.status == 'cancelled' and cancelled.quiet == ()


def test_private_aside_not_given_to_an_unauthorized_later_speaker():
    controls, original, briefs = setup_exchange(silent=False)
    bids = (replace(original.bids[0], kind='aside', audience=(ODA, WREN)), original.bids[2])
    plan = schedule(CAMPAIGN, 'aside', present=CAST, controls=controls, initiator=WREN,
                    addressees=(ODA,), floor=ODA, bids=bids)
    h = Harness([say('Keep the copper key close.'), say('I shall wait at the gate.')])
    result = run_conversation(plan, h.runtime, h.meter(plan.inference), [briefs[0], briefs[2]], controls=controls, present=CAST)
    assert result.generation.status == 'complete'
    assert 'short side remark' in h.prompts()[0] and 'copper key' not in h.prompts()[1]


@pytest.mark.parametrize('kwargs', [
    {'kind': 'unknown'}, {'reason': ''}, {'reason': 'x' * 1001},
    {'grounding': 'heard', 'heard_index': True},
    {'grounding': 'heard', 'heard_index': -1},
    {'grounding': 'listening'}, {'audience': (ODA,)},
    {'addressees': 'not refs'}, {'audience': (WREN, ODA, ODA)},
])
def test_malformed_bids_fail_closed(kwargs):
    values = dict(actor=ODA, kind='answer', addressees=(WREN,), audience=CAST,
                  reason='Answer the question.', grounding='addressed')
    values.update(kwargs)
    with pytest.raises(GenerationError):
        ConversationBid(**values)


def test_runtime_rejects_foreign_and_raw_modes_before_sending():
    controls, _, briefs = setup_exchange()
    h = Harness([])
    plan = h.plan(controls, present=[actor.instance_id for actor in CAST], speakers=[ODA.instance_id])
    for modes in ({ODA: 'interject'}, {BELLAMY: 'answer'}):
        with pytest.raises(GenerationError, match='bad_mode'):
            h.runtime.run_turn(h.meter(plan), [briefs[0]], controls=controls, modes=modes)
    assert h.transport.sends == []


def test_schedule_deterministic_and_player_bid_or_duplicate_bid_refused():
    controls, plan, _ = setup_exchange()
    repeated = schedule(CAMPAIGN, 'exchange', present=CAST, controls=controls, initiator=WREN,
                        addressees=(ODA,), floor=ODA, bids=plan.bids)
    assert repeated == plan
    for bids in ((plan.bids[0], plan.bids[0]), (replace(plan.bids[0], actor=WREN),)):
        with pytest.raises(GenerationError):
            schedule(CAMPAIGN, 'bad', present=CAST, controls=controls, initiator=WREN,
                     addressees=(ODA,), floor=ODA, bids=bids)


def test_interruption_retry_is_billed_without_repeating_dialogue():
    controls, original, briefs = setup_exchange()
    plan = schedule(CAMPAIGN, 'retry', present=CAST, controls=controls, initiator=WREN,
                    addressees=(ODA,), floor=ODA, bids=original.bids,
                    policy=InferencePolicy(max_attempts_per_call=2))
    h = Harness([Status(503), say('Wait, darling. Consider the tide.'), say('We leave by the ridge.')], attempts=2)
    meter = h.meter(plan.inference)
    result = run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST)
    assert [line.actor for line in result.generation.lines] == [BELLAMY, ODA]
    assert meter.summary().baseline.attempts == 3
    assert result.quiet == (plan.bids[2],)


def test_superseding_inflight_exchange_withholds_lines_and_quiet_but_keeps_charges():
    controls, plan, briefs = setup_exchange()
    h = Harness([say('Wait, darling.'), Supersede(1)])
    fence = RunFence()
    token = fence.claim('chat')
    h.transport.on_supersede = lambda: fence.claim('chat')
    meter = h.meter(plan.inference)
    result = run_conversation(plan, h.runtime, meter, briefs, controls=controls, present=CAST, token=token)
    assert result.generation.status == 'stale' and result.generation.lines == () and result.quiet == ()
    assert meter.summary().baseline.attempts == 2


def test_floor_priority_is_for_answer_not_a_side_remark():
    controls, original, briefs = setup_exchange(silent=False)
    bids = (original.bids[2], replace(original.bids[0], kind='aside'))
    plan = schedule(CAMPAIGN, 'aside-order', present=CAST, controls=controls, initiator=WREN,
                    addressees=(ODA,), floor=ODA, bids=bids)
    assert plan.speakers == (QUILL, ODA)
