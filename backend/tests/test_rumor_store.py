import pytest
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.core import DIRECTOR
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.rumors import Rumor, DeliveryConditions
from grimoire.rumorstore import RumorLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore
from ledger_helpers import OWNER, open_authority
from evidence_fixtures import C, MARA, INES, m
from test_evidencestore_scenarios import seed_actors, append_u
from store_helpers import owner_handle

ALL = DeliveryConditions(True, True, True, True)
BLOCK = DeliveryConditions(True, True, True, False)

def rumor():
    return Rumor('news', MARA, INES, 'v-mara', 'a distorted account', 'told', MARA, 70, 'changed', 'authored', m(8), 'calendar', 'day', 1, 3, ALL)

def test_delay_outbox_and_stable_projection_survive_reopen(tmp_path):
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as authority:
            authority.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            owner = authority.handle_for(OWNER, trusted=True)
            SovereigntyLedger(authority).record(C, ControlChange(C, 'mara', DIRECTOR), owner)
            lib = RumorLibrary(store, authority, authority.handle_for('app:adjudicator', trusted=True))
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            outbox = lib.outbox(MARA, requested_by=DIRECTOR)
            before = lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR)
            assert not any(x.content == 'a distorted account' for x in lib.actor_knowledge(C, INES, as_of=20, requested_by=DIRECTOR).evidence)
            lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=BLOCK, requested_by=DIRECTOR)
            assert lib.actor_knowledge(C, MARA, as_of=20, requested_by=DIRECTOR) == before
            lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
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
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
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


def test_watermark_clock_and_id_collisions(tmp_path):
    from dataclasses import replace
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            lib.enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='operation_conflict'):
                lib.enqueue(replace(rumor(), text='different'), moment=m(9), requested_by=DIRECTOR)
            lib.advance(C, clock_id='calendar', clock_unit='day', tick=2, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='clock_backwards'):
                lib.advance(C, clock_id='calendar', clock_unit='day', tick=1, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            with pytest.raises(ValueError, match='clock_mismatch'):
                lib.advance(C, clock_id='other', clock_unit='day', tick=2, moment=m(10), conditions=ALL, requested_by=DIRECTOR)


def test_two_hops_belief_does_not_change_truth_and_limit_order(tmp_path):
    from dataclasses import replace
    from grimoire.core import Belief
    from grimoire.rumorstore.library import _id
    from evidence_fixtures import TAVI
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            for actor in (MARA, INES):
                SovereigntyLedger(a).record(C, ControlChange(C, actor.instance_id, DIRECTOR), a.handle_for(OWNER, trusted=True))
            handle = a.handle_for('app:adjudicator', trusted=True)
            lib, ev = RumorLibrary(store, a, handle), EvidenceLibrary(store, handle)
            original = ev.read(C, requested_by=DIRECTOR).knowledge
            first = replace(rumor(), text='a faithful account', distortion='none')
            lib.enqueue(first, moment=m(9), requested_by=DIRECTOR)
            lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=ALL, requested_by=DIRECTOR)
            second = replace(first, communication_id='second', sender=INES, recipient=TAVI, source_evidence_id=_id(C, 'news', 'report'), text='a misleading account', distortion='altered', authored_at=m(10), sent_tick=3, due_tick=5)
            third = replace(second, communication_id='third')
            lib.enqueue(third, moment=m(11), requested_by=DIRECTOR)
            lib.enqueue(second, moment=m(11), requested_by=DIRECTOR)
            result = lib.advance(C, clock_id='calendar', clock_unit='day', tick=5, moment=m(12), conditions=ALL, requested_by=DIRECTOR, limit=1)
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
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            lib = RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True))
            blocked = replace(rumor(), communication_id='a', provenance='SECRET-PROVENANCE', distortion='SECRET-DISTORTION')
            ready = replace(rumor(), communication_id='b', route=DeliveryConditions(False, False, False, False))
            lib.enqueue(blocked, moment=m(9), requested_by=DIRECTOR)
            lib.enqueue(ready, moment=m(9), requested_by=DIRECTOR)
            box = repr(asdict(lib.outbox(MARA, requested_by=DIRECTOR)[0]))
            assert 'SECRET' not in box and 'route' not in box and 'due_tick' not in box
            assert lib.advance(C, clock_id='calendar', clock_unit='day', tick=3, moment=m(10), conditions=BLOCK, requested_by=DIRECTOR, limit=1)['delivered'] == ('b',)


def test_actor_id_reservation_and_damaged_ledger_fail_closed(tmp_path):
    from grimoire.rumorstore import rumor_actor_ids
    from grimoire.store.errors import LedgerBroken, LedgerInvalid
    root = tmp_path / 'lib'
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        with open_authority(store) as a:
            a.add_grant(Grant('g', 'app:adjudicator', frozenset({Permission.APPLY}), ResourcePath.parse('campaigns'), OWNER))
            SovereigntyLedger(a).record(C, ControlChange(C, 'mara', DIRECTOR), a.handle_for(OWNER, trusted=True))
            RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True)).enqueue(rumor(), moment=m(9), requested_by=DIRECTOR)
        (store.root / 'campaigns' / C / 'instances' / 'character' / 'ines.json').unlink()
        assert {'mara', 'ines'} <= rumor_actor_ids(store, C)
    next((root / 'authority' / 'rumors' / C).glob('*.json')).write_text('{}')
    with LibraryStore.open(root) as store:
        with open_authority(store) as a:
            with pytest.raises((LedgerBroken, LedgerInvalid)):
                RumorLibrary(store, a, a.handle_for('app:adjudicator', trusted=True)).trace(C, requested_by=DIRECTOR)


def test_actor_projection_requires_issued_current_controller_and_matching_scope(tmp_path):
    from grimoire.core import AuthorityError, InstanceRef
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
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
