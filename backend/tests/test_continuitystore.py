"""Read-only native causal previews over a synthetic accepted key transfer."""
import pytest

from grimoire.continuity import Pin
from grimoire.continuitystore import ContinuityLibrary
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import ContinuityRef
from grimoire.scene import Post, TurnRecord, TURN_SCHEMA, turn_to_payload
from grimoire.store import LibraryStore, ScopeRecord
from grimoire.store.layout import address_parts
from grimoire.testing.fileops import snapshot

from effects_fixtures import (CHAT, CONTROLS, TAM, INES, KEY1, Handles, contribution, effects_library, give, moment, seed)
from store_helpers import OWNER


def put(store, handles, record, expected=None):
    txn = store.transaction()
    txn.put(record, expected)
    txn.commit(handles.app)


def turn(store, handles, number, text):
    value = TurnRecord(CHAT, number, 'post', Post(OWNER, TAM, 'action', text, (TAM,)))
    record = ScopeRecord(value.ref, TURN_SCHEMA, turn_to_payload(value), owner=OWNER)
    prior = store.campaign_turns('c').records.get(value.ref)
    put(store, handles, record, None if prior is None else prior.etag)
    return '/'.join(address_parts(value.ref))


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store, handles)
        source = turn(store, handles, 1, 'Wren gives Tam the iron key.')
        effects = effects_library(store, handles)
        proposal = give()
        done = effects.accept(proposal, controls=CONTROLS, contributions=[contribution()],
                              endorsements=[handles.endorse(proposal)]).commit()
        effects.wrap_up(CHAT, 's1', through=moment(8), summary='Tam has the key.', cites=[done.ref.event_id],
                        written_by=DIRECTOR, requested_by=DIRECTOR).commit()
        dialogue = turn(store, handles, 2, 'Tam says: I will use the key Wren gave me.')
        unused = turn(store, handles, 3, 'Clouds gather at the harbor.')
        yield store, handles, ContinuityLibrary(store, handles.app, issued=handles.authorizer.issued, owners={OWNER}), source, dialogue, unused, done.ref


def test_native_transfer_dialogue_wrapup_and_readonly_rehearsal(world):
    store, handles, library, source, dialogue, unused, event = world
    snap = library.snapshot('c')
    event_path = '/'.join(address_parts(event))
    library.declare_dependency('key-dialogue', snap.pins[event_path], snap.pins[dialogue], 'dialogue')
    before = snapshot(store.root)
    for action in ('edit', 'cut', 'replace', 'replay', 'fork'):
        report = library.preview('c', (snap.pins[source],), action)
        assert report.complete, report.problems
        impacted = {item.target.path: item for item in report.impacts}
        assert event_path in impacted and dialogue in impacted
        assert impacted[dialogue].certainty == 'known'
        assert any('/relations/' in path for path in impacted)
        assert any('/wrapups/' in path for path in impacted)
        assert impacted[unused].certainty == 'potential'
        assert impacted[unused].reason == 'chronological'
    rehearsal = library.rehearse('c', (snap.pins[source],), 'replace', ('Wren keeps the key.',))
    assert rehearsal.rehearsal.status == 'speculative' and rehearsal.rehearsal.model_cost == 0
    assert library.corrections('c', (snap.pins[source],), 'edit')
    assert snapshot(store.root) == before
    assert library.snapshot('c').coverage == 'recorded_dependencies_only'


def test_declaration_reopens_and_stale_endpoint_is_preserved_as_problem(world):
    store, handles, library, source, dialogue, unused, event = world
    pins = library.snapshot('c').pins
    library.declare_dependency('dialogue', pins[source], pins[dialogue], 'dialogue')
    root = store.root
    store.close()
    with LibraryStore.open(root) as reopened:
        library = ContinuityLibrary(reopened, handles.app, issued=handles.authorizer.issued, owners={OWNER})
        assert any(edge.reason == 'dialogue' for edge in library.snapshot('c').dependencies)
        turn(reopened, handles, 2, 'A different claim.')
        report = library.preview('c', (pins[source],), 'edit')
        assert not report.complete and any('stale' in p for p in report.problems)
        before = snapshot(root)
        with pytest.raises(ValueError): library.declare_dependency('stale', pins[source], pins[dialogue], 'dialogue')
        assert snapshot(root) == before


def test_authority_and_unknown_schema_fail_closed(world):
    store, handles, library, source, dialogue, unused, event = world
    for perspective in ('actor:tam', 'worker', TAM):
        with pytest.raises(AuthorityError): library.snapshot('c', requested_by=perspective)
        with pytest.raises(AuthorityError): library.preview('c', (), 'edit', requested_by=perspective)
    worker = handles.authorizer.handle_for('actor:tam')
    with pytest.raises(AuthorityError): ContinuityLibrary(store, worker)
    from grimoire.security import WorkerHandle
    forged = WorkerHandle(handles.authorizer, OWNER, True)
    with pytest.raises(AuthorityError): ContinuityLibrary(store, forged)
    with pytest.raises(AuthorityError): ContinuityLibrary(store, type('Fake', (), {'trusted': True})())
    put(store, handles, ScopeRecord(ContinuityRef('c', 'newer'), 'future.continuity/9', {}, owner=OWNER))
    report = library.preview('c', (library.snapshot('c').pins[source],), 'edit')
    assert not report.complete and any('unsupported' in p for p in report.problems)


def test_evidence_subject_and_later_accepted_key_use(world):
    from grimoire.core.knowledge import Perception, Proposition, Subject, Report
    from grimoire.effects import as_knowledge_event
    from grimoire.evidencestore import EvidenceLibrary
    store, handles, library, source, dialogue, unused, event = world
    effects = effects_library(store, handles)
    accepted, _ = effects.read(event, requested_by=DIRECTOR)
    evidence = EvidenceLibrary(store, handles.app, owner=OWNER)
    evidence.append('c', [as_knowledge_event(accepted),
        Perception('c', 'saw-key', TAM, event.event_id, 'sight', 'The key changed hands.', moment(8), 'director', True),
        Proposition('c', 'key-claim', 'Tam received the key.', moment(8), 'director', (Subject('event', event.event_id),)),
        Report('c', 'heard-key', INES, 'key-claim', 'hearing', moment(9), 'director', True)], chat=CHAT).commit()
    snap = library.snapshot('c')
    assert not snap.problems, snap.problems
    event_path = '/'.join(address_parts(event))
    library.declare_dependency('key-dialogue', snap.pins[event_path], snap.pins[dialogue], 'dialogue')
    before = snapshot(store.root)
    report = library.preview('c', (snap.pins[source],), 'cut')
    assert report.complete, report.problems
    assert any('saw-key' in item.target.path for item in report.impacts)
    assert any('heard-key' in item.target.path for item in report.impacts)
    assert snapshot(store.root) == before
    # The later action actually consumes Tam's recorded possession; explicit declaration
    # records that causal use instead of inferring it from relation connectivity.
    proposal = give(giver=TAM, to=INES, backed=None, sequence=9, summary='Tam gives Ines the key.')
    second = effects.accept(proposal, controls=CONTROLS).commit()
    snap = library.snapshot('c')
    second_path = '/'.join(address_parts(second.ref))
    library.declare_dependency('key-use', snap.pins[event_path], snap.pins[second_path], 'causal_action')
    report = library.preview('c', (snap.pins[source],), 'cut')
    assert report.complete, report.problems
    assert second_path in {item.target.path for item in report.impacts}


@pytest.mark.parametrize('family', ['turns', 'continuity', 'relations', 'wrapups', 'evidence'])
def test_malformed_family_stays_incomplete_after_reload(world, family):
    import shutil
    store, handles, library, source, dialogue, unused, event = world
    root = store.root
    pin = library.snapshot('c').pins[source]
    folder = root / 'campaigns' / 'c' / family
    if folder.exists(): shutil.rmtree(folder)
    folder.write_text('malformed family')
    report = library.preview('c', (pin,), 'edit')
    assert not report.complete
    store.close()
    with LibraryStore.open(root) as reopened:
        report = ContinuityLibrary(reopened, handles.app, issued=handles.authorizer.issued, owners={OWNER}).preview('c', (pin,), 'edit')
        assert not report.complete


def test_missing_cross_campaign_duplicate_cycle_and_malformed_declarations(world):
    store, handles, library, source, dialogue, unused, event = world
    pins = library.snapshot('c').pins
    before = snapshot(store.root)
    missing = Pin('campaigns/c/turns/missing.json', pins[source].revision)
    with pytest.raises(ValueError): library.declare_dependency('missing', missing, pins[dialogue], 'dialogue')
    other = Pin('campaigns/else/turns/missing.json', pins[source].revision)
    with pytest.raises(ValueError): library.declare_dependency('cross', other, pins[dialogue], 'dialogue')
    with pytest.raises(ValueError): library.declare_dependency('self', pins[source], pins[source], 'dialogue')
    assert snapshot(store.root) == before
    library.declare_dependency('one', pins[source], pins[dialogue], 'dialogue')
    with pytest.raises(ValueError): library.declare_dependency('dup', pins[source], pins[dialogue], 'dialogue')
    with pytest.raises(ValueError): library.declare_dependency('cycle', pins[dialogue], pins[source], 'dialogue')
    put(store, handles, ScopeRecord(ContinuityRef('c', 'bad'), 'grimoire.continuity-dependency/1',
                                   {'source': 'bad'}, owner=OWNER))
    assert not library.preview('c', (pins[source],), 'edit').complete


def test_unknown_native_scene_schema_and_historical_provenance_are_incomplete(world):
    from dataclasses import replace
    store, handles, library, source, dialogue, unused, event = world
    pin = library.snapshot('c').pins[source]
    stored = next(value for value in store.campaign_turns('c').records.values()
                  if '/'.join(address_parts(value.record.address)) == source)
    put(store, handles, replace(stored.record, schema='future.scene/9'), stored.etag)
    report = library.preview('c', (pin,), 'edit')
    assert not report.complete and any('undecodable turn' in p for p in report.problems)


def test_acceptance_order_remains_potential_without_explicit_causal_use(world):
    from dataclasses import replace
    from grimoire.effects import accepted_from_payload, accepted_to_payload, accepted_schema
    store, handles, library, source, dialogue, unused, event = world
    effects = effects_library(store, handles)
    proposal = give(giver=TAM, to=INES, backed=None, sequence=9, summary='Tam passes on the key.')
    second = effects.accept(proposal, controls=CONTROLS).commit()
    stored = store.read(second.ref)
    decoded = accepted_from_payload(second.ref, stored.record.schema, stored.record.payload)
    ordered = replace(decoded, after=(event.event_id,))
    put(store, handles, replace(stored.record, schema=accepted_schema(ordered), payload=accepted_to_payload(ordered)),
        stored.etag)
    snap = library.snapshot('c')
    first_path, second_path = ('/'.join(address_parts(ref)) for ref in (event, second.ref))
    report = library.preview('c', (snap.pins[first_path],), 'edit')
    assert report.complete, report.problems
    impacted = next(item for item in report.impacts if item.target.path == second_path)
    assert impacted.certainty == 'potential' and impacted.reason == 'chronological'


def test_foreign_issuer_cannot_read_bound_or_unbound_library(world):
    from grimoire.security import Authorizer
    from grimoire.ledgers import DurableAuthority
    from ledger_helpers import fixed_clock
    store, handles, library, source, dialogue, unused, event = world
    foreign = Authorizer(['human:outsider']).handle_for('human:outsider', trusted=True)
    with pytest.raises(AuthorityError): ContinuityLibrary(store, foreign)
    with pytest.raises(AuthorityError):
        ContinuityLibrary(store, foreign, issued=handles.authorizer.issued, owners={OWNER})
    with pytest.raises(AuthorityError):
        ContinuityLibrary(store, handles.player(), issued=handles.authorizer.issued, owners={OWNER})
    authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
    with pytest.raises(AuthorityError): ContinuityLibrary(store, foreign)
    # Caller-provided fallback cannot override the bound authority.
    with pytest.raises(AuthorityError):
        ContinuityLibrary(store, foreign, issued=lambda h: True, owners={'human:outsider'})
    with pytest.raises(AuthorityError): library.snapshot('c')
    owner = authority.handle_for(OWNER, trusted=True)
    assert ContinuityLibrary(store, owner).snapshot('c').pins
    nonowner = authority.handle_for('human:guest', trusted=True)
    with pytest.raises(AuthorityError): ContinuityLibrary(store, nonowner)


def test_native_ruling_and_belief_recorded_dependencies(world):
    from grimoire.core.identity import KnowledgeRef
    from grimoire.core.knowledge import Belief, Proposition, Ruling, Report, Subject
    from grimoire.evidencestore import EvidenceLibrary
    store, handles, library, source, dialogue, unused, event = world
    evidence = EvidenceLibrary(store, handles.app, owner=OWNER)
    evidence.append('c', [
        Proposition('c', 'key', 'Tam has the key.', moment(8), 'director'),
        Proposition('c', 'unrelated', 'The sky is cloudy.', moment(8), 'director'),
        Ruling('c', 'key-ruling', 'key', 'unknown', moment(9), 'director'),
        Report('c', 'key-report', TAM, 'key', 'hearing', moment(9), 'director', True),
        Belief('c', 'key-belief', TAM, 'key', 'believed', moment(10), DIRECTOR, ('key-report',)),
    ], chat=CHAT, controls=CONTROLS).commit()
    snap = library.snapshot('c')
    paths = {name: '/'.join(address_parts(KnowledgeRef('c', family, name))) for family, name in
             [('proposition', 'key'), ('proposition', 'unrelated'), ('ruling', 'key-ruling'),
              ('evidence', 'key-report'), ('belief', 'key-belief')]}
    report = library.preview('c', (snap.pins[paths['key']],), 'edit')
    assert report.complete, report.problems
    impacted = {item.target.path for item in report.impacts}
    assert paths['key-ruling'] in impacted
    assert paths['unrelated'] not in impacted
    report = library.preview('c', (snap.pins[paths['key-report']],), 'cut')
    assert report.complete, report.problems
    assert paths['key-belief'] in {item.target.path for item in report.impacts}
    assert paths['unrelated'] not in {item.target.path for item in report.impacts}
