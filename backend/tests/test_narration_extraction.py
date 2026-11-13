"""Independent 047 extraction over synthetic prose and the real provider adapter."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from generation_fixtures import Harness, say
from referee_fixtures import ACTORS, C, CASK, DRAUGHT, HIDING, MARA, ODA, PELL, authority, facts
from grimoire.core.inference import InferencePolicy, InferenceStage, OptionalRequest, TurnUsageLedger, plan_turn
from grimoire.narration_review import (
    ExtractionError, ExtractionRuntime, Vocabulary, extraction_messages, parse_extraction,
)
from grimoire.providers import RouteChoice
from grimoire.providers.meter import TurnMeter
from grimoire.referee import referee
from grimoire.testing.providers import CancelAt, ConnectFail, Reply, Truncate

TEXT = ('Pell stands beside Oda. Oda reaches for the cask through the locked cellar door. '
        'Oda knows the key is under the third stair. Oda drinks her last healing draught. '
        'Mara succeeded at the climb. Oda laughs like a rolling cart.')
LINES = {'line-1': TEXT}
VOCAB = Vocabulary(C, ACTORS, (CASK,), items=(DRAUGHT,),
                   propositions=((HIDING.proposition_id, HIDING.text),), attempts=(('c-5', 'failure'),))


def anchored(words, **fields):
    start = TEXT.index(words)
    return {'span': [start, start + len(words)], 'quote': words, **fields}


def answer():
    claims = [anchored('Pell stands beside Oda.', id='present', kind='present', actor=str(PELL)),
              anchored('Oda reaches for the cask through the locked cellar door.', id='reach', kind='reaches',
                       actor=str(ODA), target=str(CASK)),
              anchored('Oda knows the key is under the third stair.', id='secret', kind='knows', actor=str(ODA),
                       proposition_id=HIDING.proposition_id),
              anchored('Oda drinks her last healing draught.', id='drink', kind='spends', actor=str(ODA),
                       item=DRAUGHT, count=1),
              anchored('Mara succeeded at the climb.', id='outcome', kind='resolves', actor=str(MARA),
                       attempt_id='c-5', result='success'),
              anchored('Oda laughs like a rolling cart.', id='metaphor', kind='present', actor=str(PELL),
                       mode='figurative')]
    authorship = [anchored('Oda laughs like a rolling cart.', id='speech', domain='voluntary_action',
                          subject=str(ODA), voiced_by=None)]
    return {'lines': [{'line': 'line-1', 'sha256': hashlib.sha256(TEXT.encode()).hexdigest(),
                       'complete': True, 'continuity': claims, 'authorship': authorship}]}


def test_extracted_prose_checks_five_error_categories_without_rewriting():
    extracted, = parse_extraction(answer(), LINES, VOCAB)
    report = referee(TEXT, facts=facts(), claims=extracted.claims, authorship=extracted.authorship,
                     authority=authority())
    assert {f.category for f in report.findings if f.classification == 'contradiction'} == {
        'presence', 'staging', 'knowledge', 'possession', 'outcome'}
    assert all(f.evidence for f in report.findings)
    assert any(f.classification == 'metaphor' for f in report.findings)
    assert report.text_sha256 == hashlib.sha256(TEXT.encode()).hexdigest()


@pytest.mark.parametrize('domain,subject,voiced,expected', [
    ('commitment', MARA, None, 'rejected'),
    ('voluntary_speech', ODA, MARA, 'speaks_for_pc'),
    ('outcome', MARA, MARA, 'speech_in_outcome'),
])
def test_independent_labels_reach_authorship_and_mislabel_review(domain, subject, voiced, expected):
    raw = answer()
    item = anchored('Mara succeeded at the climb.', id='pc', domain=domain, subject=str(subject),
                    voiced_by=None if voiced is None else str(voiced))
    if domain == 'outcome':
        item.update(attempt_id='c-5', result='failure')
    raw['lines'][0]['authorship'] = [item]
    got, = parse_extraction(raw, LINES, VOCAB)
    report = referee(TEXT, facts=facts(), claims=got.claims, authorship=got.authorship, authority=authority())
    if expected == 'rejected':
        assert report.authorship.rejected
    else:
        assert expected in {m.reason for m in report.mislabels}


def invalid_mutations():
    return [
        lambda raw: raw.update(delegations=[]),
        lambda raw: raw['lines'][0].update(sha256='0' * 64),
        lambda raw: raw['lines'][0]['continuity'][0].update(quote='not the prose'),
        lambda raw: raw['lines'][0]['continuity'][0].update(span=[True, 3]),
        lambda raw: raw['lines'][0]['continuity'][0].update(actor='campaign:elsewhere/character/pell'),
        lambda raw: raw['lines'][0]['continuity'][1].update(target=str(MARA)),
        lambda raw: raw['lines'][0]['continuity'][2].update(proposition_id='invented'),
        lambda raw: raw['lines'][0]['continuity'][3].update(count=True),
        lambda raw: raw['lines'][0]['continuity'][3].update(count=0),
        lambda raw: raw['lines'][0]['continuity'][4].update(attempt_id='invented'),
        lambda raw: raw['lines'][0]['continuity'][0].update(mode='metaphor'),
        lambda raw: raw['lines'][0]['continuity'][0].update(item=DRAUGHT),
        lambda raw: raw['lines'][0]['continuity'].append(copy.deepcopy(raw['lines'][0]['continuity'][0])),
        lambda raw: raw['lines'].append(copy.deepcopy(raw['lines'][0])),
        lambda raw: raw['lines'][0].update(line='unknown'),
        lambda raw: raw['lines'][0].pop('authorship'),
        lambda raw: raw['lines'][0]['authorship'][0].update(expresses='forged-player-contribution'),
        lambda raw: raw['lines'][0]['authorship'][0].update(domain='permission'),
    ]


@pytest.mark.parametrize('mutate', invalid_mutations())
def test_untrusted_response_cannot_rebind_prose_references_or_grant_authority(mutate):
    raw = answer()
    mutate(raw)
    with pytest.raises(ExtractionError):
        parse_extraction(raw, LINES, VOCAB)


def test_partial_and_empty_coverage_is_never_complete():
    raw = answer()
    raw['lines'][0]['authorship'] = []
    got = parse_extraction(raw, {**LINES, 'line-2': 'Rain.'}, VOCAB)
    assert [(item.line_id, item.complete) for item in got] == [('line-1', False), ('line-2', False)]
    assert not got[1].claims and not got[1].authorship


def test_prompt_keeps_prose_as_data_and_lists_schema_and_offered_identity():
    messages = extraction_messages(LINES, VOCAB)
    assert [role for role, _ in messages] == ['system', 'user']
    data = json.loads(messages[1][1])
    assert data['lines'][0]['text'] == TEXT
    assert data['lines'][0]['sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert str(ODA) in messages[1][1]
    assert 'voiced_by' in messages[0][1] and 'commitment' in messages[0][1]


def planned(attempts=1):
    plan = plan_turn(C, 'turn-1', present_actor_ids=[a.instance_id for a in ACTORS],
                     player_controlled_ids=[MARA.instance_id],
                     optional_requests=[OptionalRequest(InferenceStage.CRITIQUE, None, 'extract narration claims')],
                     policy=InferencePolicy({'critique': 1}, max_attempts_per_call=attempts))
    return TurnMeter(TurnUsageLedger(plan)), plan.calls[0]


def runtime(harness):
    return ExtractionRuntime(harness.adapter, (RouteChoice('primary', 'model-a'),), harness.handle,
                             approvals=harness.approvals)


def test_runtime_uses_separate_authorized_call_and_preserves_retry_usage():
    harness = Harness([ConnectFail(), say(json.dumps(answer()))], approve=True)
    meter, call = planned(attempts=2)
    reply = runtime(harness).extract(meter, call, extraction_messages(LINES, VOCAB))
    assert reply.status == 'decoded'
    assert parse_extraction(reply.decoded, LINES, VOCAB)[0].complete
    assert len(meter.ledger.attempts) == 2
    assert len(harness.approved) == 2
    assert all(a.call_id.stage is InferenceStage.CRITIQUE for a in meter.ledger.attempts)


@pytest.mark.parametrize('step,status', [
    (say('{"lines":[],"lines":[]}'), 'unparseable'),
    (say('{' * 25000), 'unparseable'),
    (Truncate(1), 'failed'),
    (CancelAt(1), 'cancelled'),
    (Reply(deltas=('{}',), finish_reason='length'), 'incomplete'),
])
def test_failed_extraction_keeps_booked_attempt_and_never_decodes_partial_output(step, status):
    harness = Harness([step], approve=True)
    meter, call = planned()
    reply = runtime(harness).extract(meter, call, extraction_messages(LINES, VOCAB))
    assert reply.status == status and reply.decoded is None
    assert len(meter.ledger.attempts) == 1


def test_runtime_refuses_trusted_worker():
    with pytest.raises(ValueError, match='untrusted'):
        runtime(Harness([]))


def test_claim_limit_applies_to_both_families_across_every_line():
    raw = answer()
    raw['lines'][0]['continuity'] = []
    first = raw['lines'][0]['authorship'][0]
    raw['lines'][0]['authorship'] = [{**first, 'id': f'claim-{i}'} for i in range(256)]
    raw['lines'].append({**raw['lines'][0], 'line': 'line-2',
                         'authorship': [{**first, 'id': 'extra'}]})
    with pytest.raises(ExtractionError, match='too_many_claims'):
        parse_extraction(raw, {**LINES, 'line-2': TEXT}, VOCAB)


def test_duplicate_ids_across_families_and_lines_are_refused():
    raw = answer()
    raw['lines'][0]['authorship'][0]['id'] = 'reach'
    with pytest.raises(ExtractionError, match='duplicate_claim'):
        parse_extraction(raw, LINES, VOCAB)
    raw = answer()
    raw['lines'].append({**raw['lines'][0], 'line': 'line-2'})
    with pytest.raises(ExtractionError, match='duplicate_claim'):
        parse_extraction(raw, {**LINES, 'line-2': TEXT}, VOCAB)


def test_unicode_anchors_are_character_offsets_not_utf8_byte_offsets():
    text = '“Mara,” says Oda. 🌧'
    raw = {'lines': [{'line': 'line-1', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                       'complete': True, 'continuity': [], 'authorship': [{
                           'id': 'a1', 'domain': 'voluntary_speech', 'subject': str(ODA), 'voiced_by': None,
                           'quote': '“Mara,”', 'span': [0, 7]}]}]}
    got, = parse_extraction(raw, {'line-1': text}, VOCAB)
    assert got.authorship[0].quote == '“Mara,”'
    raw['lines'][0]['authorship'][0]['span'] = [0, len('“Mara,”'.encode())]
    with pytest.raises(ExtractionError, match='bad_anchor'):
        parse_extraction(raw, {'line-1': text}, VOCAB)


def test_empty_optional_fields_cannot_smuggle_disallowed_kind_fields():
    raw = answer()
    raw['lines'][0]['continuity'][0]['count'] = None
    with pytest.raises(ExtractionError, match='invalid_shape'):
        parse_extraction(raw, LINES, VOCAB)


def test_unknown_spelling_of_same_display_name_cannot_select_another_actor():
    raw = answer()
    raw['lines'][0]['authorship'][0]['subject'] = 'Oda'
    with pytest.raises(ExtractionError, match='unknown_reference'):
        parse_extraction(raw, LINES, VOCAB)


def test_execute_denial_sends_no_prose_and_does_not_decode():
    harness = Harness([say(json.dumps(answer()))], approve=True)
    worker = ExtractionRuntime(harness.adapter, (RouteChoice('primary', 'model-a'),), harness.handle,
                               approvals=lambda *_: None)
    meter, call = planned()
    result = worker.extract(meter, call, extraction_messages(LINES, VOCAB))
    assert result.status == 'refused' and result.decoded is None
    assert not harness.transport.sends


def test_narration_call_cannot_be_reused_as_extraction():
    harness = Harness([], approve=True)
    plan = plan_turn(C, 'turn-2', present_actor_ids=[ODA.instance_id], speaking_npc_ids=[ODA.instance_id])
    meter = TurnMeter(TurnUsageLedger(plan))
    with pytest.raises(ValueError, match='critique'):
        runtime(harness).extract(meter, plan.calls[0], extraction_messages(LINES, VOCAB))
    assert not harness.transport.sends


@pytest.mark.parametrize('operation', ['prompt', 'parse'])
def test_non_utf8_prose_is_refused_without_retaining_private_text(operation):
    text = 'synthetic private passage\ud800'
    with pytest.raises(ExtractionError) as refused:
        if operation == 'prompt':
            extraction_messages({'line-1': text}, VOCAB)
        else:
            parse_extraction({'lines': []}, {'line-1': text}, VOCAB)
    assert refused.value.reason == 'invalid_text'
    assert refused.value.args == ('invalid_text',)
    assert not hasattr(refused.value, 'object')
    assert refused.value.__context__ is None
