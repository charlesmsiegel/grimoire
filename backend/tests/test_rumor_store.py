import pytest
from grimoire.calendars import CalendarDefinition, Duration
from grimoire.rumors import CampaignTime as Day
from grimoire.calendarstore import CalendarLibrary
from grimoire.chats import PlayScope, SCOPE_SCHEMA, scope_to_payload
from grimoire.core.identity import ScopeRef
from grimoire.store import ScopeRecord
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.core import DIRECTOR
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.rumors import Rumor, DeliveryConditions
from grimoire.core import Proposition
from grimoire.rumorledger import output_id
from grimoire.rumorstore import RumorLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore
from ledger_helpers import OWNER, open_authority
from evidence_fixtures import C, MARA, INES, m
from test_evidencestore_scenarios import seed_actors, append_u
from store_helpers import owner_handle

ALL = DeliveryConditions(True, True, True, True)
BLOCK = DeliveryConditions(True, True, True, False)
SCOPE = ScopeRef(C)
DAY = 1440
START = Day(CalendarDefinition().parse('1203-03-01'))


def rumor():
    return Rumor('news', MARA, INES, 'v-mara', 'a distorted account', 'told', MARA, 70, 'changed', 'authored', m(8), START, START.advance(2 * DAY), ALL)


def seeded(store, *, days=2):
    """The evidence fixtures' actors and ledger, and the campaign's calendar ``days`` after ``START``: by default
    the day ``rumor()`` falls due."""
    seed_actors(store)
    append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
    txn = store.transaction()
    txn.put(ScopeRecord(SCOPE, SCOPE_SCHEMA, scope_to_payload(PlayScope(SCOPE, '2026-01-01T00:00:00Z')), owner=OWNER), None)
    txn.commit(owner_handle())
    calendar = CalendarLibrary(store, owner_handle())
    calendar.initialize(SCOPE, 'start', CalendarDefinition(), '1203-03-01', expected=calendar.read(SCOPE).etag).commit()
    if days:
        later(store, owner_handle(), 'seeded', days * DAY)


def later(store, handle, operation, minutes):
    calendar = CalendarLibrary(store, handle)
    return calendar.advance(SCOPE, operation, Duration(minutes), expected=calendar.read(SCOPE).etag).commit().state.now

def test_delay_outbox_and_stable_projection_survive_reopen(tmp_path):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seeded(store)
        with open_authority(store) as authority:
            authority.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            owner = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), owner)
            lib = RumorLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            outbox = lib.outbox(MARA, requested_by=DIRECTOR)
            before = lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR)
            assert not any(x.content == 'a distorted account' for x in lib.actor_knowledge(C, INES, as_of=20, requested_by=DIRECTOR).evidence)
            lib.advance(C, moment=m(10), conditions=BLOCK, requested_by=DIRECTOR)
            assert lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR) == before
            lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            assert lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR) == before
            assert lib.outbox(MARA, requested_by=DIRECTOR) == outbox
            assert any(x.content == 'a distorted account' for x in lib.actor_knowledge(C, INES, as_of=20, requested_by=DIRECTOR).evidence)
    with LibraryStore.open(root) as store:
        with open_authority(store) as authority:
            lib = RumorLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            assert lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR) == before
            assert lib.outbox(MARA, requested_by=DIRECTOR) == outbox


def test_pc_speech_requires_exact_current_controller_and_codec_replays(tmp_path):
    from dataclasses import replace
    from grimoire.core import PlayerContribution, AuthorityError
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store)
        with open_authority(store) as a:
            a.add_grant(Grant('g', OWNER, frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', OWNER), a.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, a, a.handle_for(OWNER, trusted=True))
            r = replace(rumor(), authored_at=m(8, 'scene', 'turn'))
            c = PlayerContribution(C, 'speech', 'scene', 'turn', 'mara', OWNER, 'speech', r.text)
            with pytest.raises(AuthorityError):
                lib.enqueue(r, moment=m(9), requested_by=OWNER, contribution=replace(c, text='different'))
            lib.enqueue(r, moment=m(9), requested_by=OWNER, contribution=c)
            lib.enqueue(r, moment=m(9), requested_by=OWNER, contribution=c)
            assert lib.outbox(MARA, requested_by=OWNER)[0].text == r.text
            before = lib.actor_knowledge(C, MARA, as_of=20, requested_by=OWNER)
            a.add_grant(Grant('director-g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            service = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            service.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            assert lib.actor_knowledge(C, MARA, as_of=20, requested_by=OWNER) == before


def test_campaign_calendar_times_sends_and_deliveries_and_ids_collide(tmp_path):
    from dataclasses import replace
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store, days=0)
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            handle = a.handle_for('app:adjudicator', trusted=True)
            lib = RumorLibrary(store, a, handle)
            with pytest.raises(ValueError, match='future_send'):  # campaign time has not reached the send
                lib.enqueue(replace(rumor(), sent_on=START.advance(1)), moment=m(9), requested_by=DIRECTOR)
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='operation_conflict'):
                lib.enqueue(replace(rumor(), text='different'), moment=m(9), requested_by=DIRECTOR)
            assert lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)['delivered'] == ()
            later(store, handle, 'almost', 2 * DAY - 1)  # one minute short of due
            assert lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)['delivered'] == ()
            later(store, handle, 'due', 1)
            assert lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)['delivered'] == ('news',)
            assert lib.trace(C, requested_by=DIRECTOR)[1] == START.advance(2 * DAY)  # the delivering watermark


def test_a_campaign_without_a_calendar_queues_nothing(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        txn = store.transaction()
        txn.put(ScopeRecord(SCOPE, SCOPE_SCHEMA, scope_to_payload(PlayScope(SCOPE, '2026-01-01T00:00:00Z')), owner=OWNER), None)
        txn.commit(owner_handle())
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            with pytest.raises(ValueError, match='calendar_uninitialized'):
                lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            assert lib.trace(C, requested_by=DIRECTOR) == ({}, None)
            with pytest.raises(ValueError, match='calendar_uninitialized'):
                lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)


def test_two_hops_belief_does_not_change_truth_and_limit_order(tmp_path):
    from dataclasses import replace
    from grimoire.core import Belief
    from grimoire.rumorstore.library import _id
    from evidence_fixtures import TAVI
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store)
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            for actor in (MARA, INES):
                SovereigntyLedger(a).record(C, ControlChange(C, actor.instance_id, DIRECTOR), a.handle_for(OWNER, trusted=True))
            handle = a.handle_for('app:adjudicator', trusted=True)
            lib, ev = RumorLibrary(store, a, handle), EvidenceLibrary(store, handle)
            original = ev.read(C, requested_by=DIRECTOR).knowledge
            first = replace(rumor(), text='a faithful account', distortion='none')
            lib.enqueue(first, moment=m(9), requested_by=DIRECTOR)
            lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            second = replace(first, communication_id='second', sender=INES, recipient=TAVI, source_evidence_id=_id(C, 'news', 'report'), text='a misleading account', distortion='altered', authored_at=m(10), due_on=START.advance(4 * DAY))
            third = replace(second, communication_id='third')
            lib.enqueue(third, moment=m(11), requested_by=DIRECTOR)
            lib.enqueue(second, moment=m(11), requested_by=DIRECTOR)
            later(store, handle, 'two-more-days', 2 * DAY)
            result = lib.advance(C, moment=m(12), conditions=ALL, requested_by=DIRECTOR, limit=1)
            assert result == {'delivered': ('second',), 'pending': 1}
            view = lib.actor_knowledge(C, TAVI, as_of=12, requested_by=DIRECTOR)
            assert [x.content for x in view.evidence] == ['a misleading account']
            assert not view.current
            belief = Belief(C, 'tavi-belief', TAVI, _id(C, 'second', 'proposition'), 'believed', m(12), DIRECTOR, (_id(C, 'second', 'report'),))
            ev.append(C, [belief], controls=[__import__('grimoire.core', fromlist=['ControlAssignment']).ControlAssignment(C, 'tavi', DIRECTOR)]).commit()
            assert lib.actor_knowledge(C, TAVI, as_of=12, requested_by=DIRECTOR).current[0].stance == 'believed'
            after = ev.read(C, requested_by=DIRECTOR).knowledge
            assert after.events == original.events and after.rulings == original.rulings


def test_outbox_excludes_director_only_trace_and_blocked_edge_does_not_starve(tmp_path):
    from dataclasses import replace, asdict
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store)
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            blocked = replace(rumor(), communication_id='a', provenance='SECRET-PROVENANCE', distortion='SECRET-DISTORTION')
            ready = replace(rumor(), communication_id='b', route=DeliveryConditions(False, False, False, False))
            lib.enqueue(blocked, moment=m(9), requested_by=DIRECTOR)
            lib.enqueue(ready, moment=m(9), requested_by=DIRECTOR)
            box = repr(asdict(lib.outbox(MARA, requested_by=DIRECTOR)[0]))
            assert 'SECRET' not in box and 'route' not in box and 'due_on' not in box
            assert lib.advance(C, moment=m(10), conditions=BLOCK, requested_by=DIRECTOR, limit=1)['delivered'] == ('b',)


def test_actor_id_reservation_and_damaged_ledger_fail_closed(tmp_path):
    from grimoire.rumorstore import rumor_actor_ids
    from grimoire.store.errors import LedgerBroken, LedgerInvalid
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seeded(store)
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True)).enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
        sent = EvidenceLibrary(store, owner_handle()).actor_knowledge(C, MARA, as_of=100).sent
        assert [(s.evidence_id, s.recipient) for s in sent] == [(output_id(C, 'news', 'report'), INES)]
        (store.root / 'campaigns' / C / 'instances' / 'character' / 'ines.json').unlink()
        assert {'mara', 'ines'} <= rumor_actor_ids(store, C)
    next((root / 'authority' / 'rumors' / C).glob('*.json')).write_text('{}')
    with LibraryStore.open(root) as store:
        # An untrustworthy rumor ledger hides every send rather than showing the raw ones it can no longer vouch for.
        evidence = EvidenceLibrary(store, owner_handle())
        assert evidence.actor_knowledge(C, MARA, as_of=100).sent == ()
        assert f'rumors/{C}:unreadable' in evidence.read(C, requested_by=DIRECTOR).problems
        with open_authority(store) as a:
            with pytest.raises((LedgerBroken, LedgerInvalid)):
                RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True)).trace(C, requested_by=DIRECTOR)


def test_actor_projection_requires_issued_current_controller_and_matching_scope(tmp_path):
    from grimoire.core import AuthorityError, InstanceRef
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store)
        with open_authority(store) as a:
            owner = a.handle_for(OWNER, trusted=True)
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', OWNER), owner)
            human = RumorLibrary(store, a, owner)
            assert human.actor_knowledge(C, MARA, as_of=8, requested_by=OWNER).actor == MARA
            with pytest.raises(AuthorityError):
                human.actor_knowledge(C, INES, as_of=8, requested_by=OWNER)
            with pytest.raises(AuthorityError):
                human.actor_knowledge(C, MARA, as_of=8, requested_by=DIRECTOR)
            stranger = RumorLibrary(store, a, a.handle_for('human:stranger', trusted=True))
            with pytest.raises(AuthorityError):
                stranger.actor_knowledge(C, MARA, as_of=8, requested_by='human:stranger')
            untrusted = RumorLibrary(store, a, a.handle_for(OWNER, trusted=False))
            with pytest.raises(AuthorityError):
                untrusted.actor_knowledge(C, MARA, as_of=8, requested_by=OWNER)
            with LibraryStore.create(tmp_path / 'foreign') as other_store:
                with open_authority(other_store) as other_authority:
                    foreign = RumorLibrary(store, a, other_authority.handle_for(OWNER, trusted=True))
                    with pytest.raises(AuthorityError):
                        foreign.actor_knowledge(C, MARA, as_of=8, requested_by=OWNER)
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', 'human:new'), owner)
            with pytest.raises(AuthorityError):
                human.actor_knowledge(C, MARA, as_of=8, requested_by=OWNER)
            new_controller = RumorLibrary(store, a, a.handle_for('human:new', trusted=True))
            assert new_controller.actor_knowledge(C, MARA, as_of=8, requested_by='human:new').actor == MARA
            director = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            assert director.actor_knowledge(C, MARA, as_of=8, requested_by=DIRECTOR).actor == MARA
            with pytest.raises(AuthorityError):
                director.actor_knowledge('other', MARA, as_of=8, requested_by=DIRECTOR)
            with pytest.raises(AuthorityError):
                director.actor_knowledge(C, InstanceRef('other', 'character', 'mara'), as_of=8, requested_by=DIRECTOR)


def test_human_projection_separates_inherited_and_own_beliefs_across_transfer(tmp_path):
    from grimoire.core import Belief, ControlAssignment, PlayerContribution
    with LibraryStore.create(tmp_path / 'lib') as store:
        seeded(store)
        with open_authority(store) as a:
            a.add_grant(Grant('g', OWNER, frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            owner = a.handle_for(OWNER, trusted=True)
            controls = SovereigntyLedger(a)
            controls.record(C, ControlChange(C, 'mara', OWNER), owner)
            lib = RumorLibrary(store, a, owner)
            view = lib.actor_knowledge(C, MARA, as_of=8, requested_by=OWNER)
            assert view.current == ()
            assert [b.belief_id for b in view.inherited] == ['b-mara-2']
            contribution = PlayerContribution(C, 'interiority', 's', 't', 'mara', OWNER, 'interiority', 'the guild sold its own charter')
            belief = Belief(C, 'own-belief', MARA, 'p-mis', 'believed', m(9, 's', 't'), OWNER, ('v-mara',), supersedes='b-mara-2', contribution_id='interiority')
            EvidenceLibrary(store, owner).append(C, [belief], controls=[ControlAssignment(C, 'mara', OWNER)], contributions={'own-belief': contribution}).commit()
            view = lib.actor_knowledge(C, MARA, as_of=9, requested_by=OWNER)
            assert [b.belief_id for b in view.current] == ['own-belief']
            controls.record(C, ControlChange(C, 'mara', 'human:new'), owner)
            other = RumorLibrary(store, a, a.handle_for('human:new', trusted=True))
            transferred = other.actor_knowledge(C, MARA, as_of=9, requested_by='human:new')
            assert transferred.current == ()
            assert [b.belief_id for b in transferred.inherited] == ['own-belief']


def test_a_delivered_rumor_without_its_ledger_shows_no_sends(tmp_path):
    """A device holding the delivered report but not the (unsynced) rumor ledger cannot tell when it was sent."""
    import shutil
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seeded(store)
        with open_authority(store) as authority:
            authority.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), authority.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            assert lib.advance(C, moment=m(10), conditions=ALL, requested_by=DIRECTOR)['delivered'] == ('news',)
    shutil.rmtree(root / 'authority' / 'rumors' / C)
    with LibraryStore.open(root) as store:
        evidence = EvidenceLibrary(store, owner_handle())
        assert evidence.actor_knowledge(C, MARA, as_of=100).sent == ()
        assert f'rumors/{C}:unreadable' in evidence.read(C, requested_by=DIRECTOR).problems
        # what Ines heard is still hers, and the evidence ledger still takes writes
        assert any(x.content == 'a distorted account' for x in evidence.actor_knowledge(C, INES, as_of=100).evidence)
        with open_authority(store) as authority:
            writer = EvidenceLibrary(store, authority.handle_for('app:adjudicator', trusted=True))
            writer.append(C, [Proposition(C, 'later', 'the tide turned', m(11), 'test')]).commit()
