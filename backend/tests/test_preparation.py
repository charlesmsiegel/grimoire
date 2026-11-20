from dataclasses import replace

import pytest

from grimoire.core.identity import InstanceRef, KnowledgeRef, SourceRef
from grimoire.preparation import Draft, Evidence, Note, PreparationSession, PreparedScene, Review, Snapshot


@pytest.fixture
def situation():
    mara = InstanceRef('realm', 'person', 'mara')
    town = InstanceRef('realm', 'location', 'town')
    choice = Evidence('choice', 'choice', KnowledgeRef('realm', 'evidence', 'choice'), '1', 'Meet at town')
    snapshot = Snapshot('realm', '1', (choice,), (mara,), (town,))
    current = [snapshot]
    calls = []

    def propose(state, brief):
        calls.append(state)
        note = Note(state.evidence[0].text, ('choice',))
        return Draft(note, state.cast, town, (note,), (), (note,), (note,),
                     (Note('Offer a conversation', ('choice',)), Note('Leave the invitation open', ('choice',))))

    return current, calls, PreparationSession('realm', lambda: current[0], propose)


def test_meeting_adapts_to_departure_without_recording_unplayed_fiction(situation):
    current, calls, session = situation
    meeting = session.prepare('meeting', 'Prepare an optional meeting')
    assert meeting.snapshot.missing == ('calendar', 'goal', 'secret')
    reviewed = Review(meeting, True, True, True)
    accepted = session.accept(reviewed)
    assert isinstance(accepted, PreparedScene)
    assert session.accept(reviewed) is accepted
    current[0] = replace(current[0], evidence=(replace(current[0].evidence[0], text='Leave town'),))
    with pytest.raises(ValueError, match='stale'):
        session.accept(reviewed)
    departure = session.prepare('departure', 'Adapt to leaving')
    assert calls[-1].evidence[0].text == 'Leave town'
    assert departure.draft.premise.text == 'Leave town'
    assert meeting.draft.premise.text == 'Meet at town'
    assert current[0].evidence[0].text == 'Leave town'


def test_replay_conflict_and_rejection(situation):
    current, calls, session = situation
    proposal = session.prepare('meeting', 'Prepare')
    assert session.prepare('meeting', 'Prepare') is proposal
    assert len(calls) == 1
    with pytest.raises(ValueError, match='request_conflict'):
        session.prepare('meeting', 'Changed')
    session.reject(proposal)
    session.reject(proposal)
    with pytest.raises(ValueError, match='rejected'):
        session.prepare('meeting', 'Prepare')
    with pytest.raises(ValueError, match='rejected'):
        session.accept(Review(proposal, True, True, True))


def test_changed_draft_and_unreviewed_stakes_refused(situation):
    _, _, session = situation
    proposal = session.prepare('meeting', 'Prepare')
    forged = replace(proposal, draft=replace(proposal.draft, premise=Note('Forced ending', ('choice',))))
    with pytest.raises(ValueError, match='unknown_proposal'):
        session.accept(Review(forged, True, True, True))
    with pytest.raises(ValueError, match='review_required'):
        session.accept(Review(proposal, True, False, True))


def test_removed_evidence_and_cross_campaign_refused(situation):
    current, _, session = situation
    proposal = session.prepare('meeting', 'Prepare')
    current[0] = replace(current[0], evidence=())
    with pytest.raises(ValueError, match='stale'):
        session.accept(Review(proposal, True, True, True))
    with pytest.raises(ValueError, match='wrong_campaign'):
        replace(current[0], cast=(InstanceRef('other', 'person', 'mara'),))
    with pytest.raises(ValueError, match='campaign_evidence_required'):
        Evidence('choice', 'choice', SourceRef('realm', 'person', 'mara', '1'), '1', 'Leave')


def test_wrong_evidence_and_location_are_not_name_matched(situation):
    current, _, session = situation
    proposal = session.prepare('meeting', 'Prepare')
    draft = replace(proposal.draft, premise=Note('Secret', ('unknown',)))
    bad = PreparationSession('realm', lambda: current[0], lambda *_: draft)
    with pytest.raises(ValueError, match='unknown_evidence'):
        bad.prepare('bad', 'Prepare')
    draft = replace(proposal.draft, location=InstanceRef('other', 'location', 'town'))
    with pytest.raises(ValueError, match='unknown_instance'):
        bad.prepare('bad', 'Prepare')


def test_wrong_campaign_reader_and_proposer_failure_allow_clean_retry(situation):
    current, _, good = situation
    draft = good.prepare('template', 'Prepare').draft
    responses = [RuntimeError('script failed'), draft]

    def proposer(*_):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    session = PreparationSession('realm', lambda: current[0], proposer)
    with pytest.raises(RuntimeError, match='script failed'):
        session.prepare('meeting', 'Prepare')
    proposal = session.prepare('meeting', 'Prepare')
    assert session.accept(Review(proposal, True, True, True)).review.proposal == proposal
    foreign = PreparationSession('other', lambda: current[0], proposer)
    with pytest.raises(ValueError, match='wrong_campaign'):
        foreign.prepare('meeting', 'Prepare')


def test_review_cannot_name_an_unregistered_proposal_or_erase_choice(situation):
    current, _, session = situation
    proposal = session.prepare('meeting', 'Prepare')
    with pytest.raises(ValueError, match='unknown_proposal'):
        session.accept(Review(replace(proposal, request_id='different'), True, True, True))
    current[0] = replace(current[0], evidence=(replace(current[0].evidence[0], revision='2'),))
    with pytest.raises(ValueError, match='request_conflict'):
        session.prepare('meeting', 'Prepare')


def test_grounded_meeting_becomes_optional_roadside_warning(situation):
    current, _, _ = situation
    state = current[0]
    town = state.locations[0]
    road = InstanceRef('realm', 'location', 'road')
    secret = Evidence('secret', 'secret', SourceRef('realm', 'lore', 'bridge', '1'), '1',
                      'The bridge supports are cracked')
    goal = Evidence('goal', 'goal', InstanceRef('realm', 'person', 'mara'), '1',
                    'Mara wants travelers to avoid the damaged bridge')
    current[0] = replace(state, evidence=(*state.evidence, secret, goal), locations=(town, road))
    seen = []

    def propose(snapshot, brief):
        seen.append(snapshot)
        leaving = snapshot.evidence[0].text == 'Leave town'
        premise = Note('Mara offers a roadside warning' if leaving else 'Mara invites a bridge-safety meeting',
                       ('choice', 'goal', 'secret'))
        return Draft(premise, snapshot.cast, road if leaving else town,
                     (Note('Will anyone investigate the damage?', ('secret',)),),
                     (Note('Visible cracks can be inspected', ('secret',)),),
                     (Note('The warning competes with the travelers plans', ('choice', 'goal')),),
                     (Note('Crossing the damaged bridge risks injury; delay costs time', ('secret',)),),
                     (Note('Offer a detour discussion on the road' if leaving else 'Offer a bridge inspection',
                           ('secret', 'choice')),
                      Note('Leave a warning available without requiring a response', ('goal', 'choice'))))

    session = PreparationSession('realm', lambda: current[0], propose)
    meeting = session.prepare('meeting', 'Prepare')
    assert meeting.snapshot.missing == ('calendar',)
    session.reject(meeting)
    current[0] = replace(current[0], evidence=(replace(state.evidence[0], text='Leave town'), secret, goal))
    departure = session.prepare('departure', 'Adapt')
    prepared = session.accept(Review(departure, True, True, True))
    assert prepared.review.proposal.draft.location == road
    assert departure.draft.premise != meeting.draft.premise
    assert departure.draft.directions != meeting.draft.directions
    assert departure.draft.stakes[0].evidence == ('secret',)
    assert seen[-1].evidence[0].text == 'Leave town'
    assert current[0].evidence == (replace(state.evidence[0], text='Leave town'), secret, goal)
