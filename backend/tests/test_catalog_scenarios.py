"""056 acceptance: three synthetic worlds and quick chats reuse authored references only."""
from dataclasses import replace

from chat_fixtures import Clock, Ids
import context_fixtures as lore
from portrayal_fixtures import (
    BASE, CAST, MODERN, MODERN_MARA, VAMPIRE, VAMPIRE_MARA, MECHA, MECHA_MARA,
    NAMESAKE, base, envelope, manifest, seed_four_portrayals,
)
from store_helpers import OWNER, owner_handle

from grimoire.catalogs import Catalog, compose_catalogs, publish_catalog
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.context import Budget, pack_actor_context
from grimoire.contextstore import stored_record
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import FacetSet, Physical
from grimoire.core.identity import SourceRef, TranscriptRef
from grimoire.core.knowledge import Knowledge
from grimoire.core.snapshot import SelectedSource
from grimoire.effective import chat_snapshot, selection_snapshot
from grimoire.store import LibraryStore, RecordEnvelope, ScopeRecord
from grimoire.transcripts import TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptPage, Variant, page_from_payload, page_to_payload
from grimoire.testing.fileops import snapshot as files_of

PACK = SourceRef(CAST, 'catalog', 'shared', 'r1')
COMMON = SourceRef(CAST, 'lorebook', 'common', 'r1')
GEAR = SourceRef(CAST, 'item', 'gear', 'r1')
BEAST = SourceRef(MECHA, 'creature', 'beast', 'r1')
WORLDS = ((MODERN, MODERN_MARA), (VAMPIRE, VAMPIRE_MARA), (MECHA, MECHA_MARA))


def publish(store, catalog):
    publish_catalog(store, owner_handle(), catalog, expected=store.read_source(catalog.ref.source).etag)


def setup(store):
    seed_four_portrayals(store, owner_handle())
    tx = store.transaction()
    tx.put(RecordEnvelope(COMMON, {'entries': lore.LORE_ENTRIES}, name='Common lore',
                          extra={'schema': lore.LORE_SCHEMA}), None)
    tx.put(envelope(EntityDefinition(GEAR, 'Gear', 'template', FacetSet([Physical()]))), None)
    tx.put(envelope(EntityDefinition(BEAST, 'Beast', 'template', FacetSet([Physical()]))), None)
    tx.commit(owner_handle())
    publish(store, Catalog(PACK, (BASE, COMMON, GEAR)))
    for world, portrayal in WORLDS:
        catalog = Catalog(SourceRef(world, 'catalog', 'available', 'r1'),
                          (portrayal, BEAST) if world == MECHA else (portrayal,),
                          (PACK,), (GEAR.unpinned(),) if world == MECHA else ())
        publish(store, catalog)


def test_three_worlds_share_refs_with_local_exclusions_and_pinned_portrayals(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        setup(store)
        original = {ref: store.load().index.resolve(ref).etag for ref in (BASE, COMMON, GEAR)}
        before = files_of(store.root)
        for world, portrayal in WORLDS:
            catalog = compose_catalogs(store.load(), (SourceRef(world, 'catalog', 'available', 'r1'),))
            refs = {entry.ref for entry in catalog.entries}
            assert {BASE, COMMON, portrayal} <= refs
            assert (GEAR in refs) == (world != MECHA)
            assert (BEAST in refs) == (world == MECHA)
            selected = catalog.select((SelectedSource(portrayal, 'character'), SelectedSource(COMMON, 'lorebook')),
                                      world=world)
            effective = selection_snapshot(store.load(), selected).effective(portrayal)
            assert effective.origin('section:personality').source.ref == BASE
            assert effective.section('personality') == base().extras['sections']['personality']
            assert effective.origin('section:occupation' if world != VAMPIRE else 'section:species').source.ref == portrayal
            assert effective.section('mortal-family' if world != MODERN else 'period-customs') is None
        assert files_of(store.root) == before
        # Updating the shared base head does not change any pinned catalog or world portrayal.
        tx = store.transaction()
        tx.put(envelope(replace(base(), ref=BASE.at('r2'), description='New authored description')), None)
        source = store.read_source(CAST)
        tx.put(replace(source.manifest, heads={**source.manifest.heads, 'character/mara': 'r2'}), source.etag)
        tx.commit(owner_handle())
        assert {ref: store.load().index.resolve(ref).etag for ref in original} == original
    with LibraryStore.open(tmp_path / 'lib') as reopened:
        for world, portrayal in WORLDS:
            catalog = compose_catalogs(reopened.load(), (SourceRef(world, 'catalog', 'available', 'r1'),))
            assert BASE in {entry.ref for entry in catalog.entries}
            snap = selection_snapshot(reopened.load(), catalog.select((SelectedSource(portrayal, 'character'),)))
            assert snap.effective(portrayal).chain[-1].ref == BASE


def test_quick_chats_keep_independent_actor_state_and_no_world(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        setup(store)
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        selected = compose_catalogs(store.load(), (PACK,)).select(
            (SelectedSource(BASE, 'character'), SelectedSource(COMMON, 'lorebook')))
        one, two = [chats.start_chat(Launch(selected.sources[0].role, selected.sources[0].ref),
                                     attach=[(s.ref, s.role) for s in selected.sources[1:]]).commit() for _ in range(2)]
        assert one.scope != two.scope and one.actors[0] != two.actors[0] and one.chat != two.chat
        before_second = store.read(two.actors[0])
        shared_etag = store.load().index.resolve(BASE).etag
        history_ref = TranscriptRef(one.chat.campaign_id, one.chat.chat_id, 1)
        page = TranscriptPage(history_ref, (Post('p1', Speaker('character', one.actors[0], 'Mara'),
                                                (Variant('I will wait by the ferry.'),), 0),))
        first = store.read(one.actors[0])
        tx = store.transaction()
        tx.put(replace(first.envelope, payload={**first.envelope.payload, 'local_name': 'Mara the traveller'}), first.etag)
        tx.put(ScopeRecord(history_ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=OWNER), None)
        tx.commit(owner_handle())
        assert store.read(two.actors[0]) == before_second
        for started in (one, two):
            captured = chat_snapshot(store.load(), started.chat)
            assert captured.scope.world is None
            assert [r.resolved for r in captured.snapshot.sources()] == [BASE, COMMON]
            assert not captured.problems
        assert chat_snapshot(store.load(), one.chat).snapshot.entity(one.actors[0]).name == 'Mara the traveller'
        assert chat_snapshot(store.load(), two.chat).snapshot.entity(two.actors[0]).name == 'Mara'
    with LibraryStore.open(tmp_path / 'lib') as reopened:
        history = reopened.chat_transcript(one.chat)
        assert history.problems == () and tuple(history.records) == (history_ref,)
        stored = history.records[history_ref]
        restored = page_from_payload(history_ref, stored.record.schema, stored.record.payload)
        assert restored.posts[0].text == 'I will wait by the ferry.'
        assert reopened.chat_transcript(two.chat).records == {}
        assert reopened.load().index.resolve(BASE).etag == shared_etag


def test_thousands_of_available_records_do_not_expand_real_context(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        setup(store)
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        selected = compose_catalogs(store.load(), (PACK,)).select(
            (SelectedSource(BASE, 'character'), SelectedSource(COMMON, 'lorebook')))
        started = chats.start_chat(Launch('character', selected.sources[0].ref),
                                   attach=[(selected.sources[1].ref, 'lorebook')]).commit()
        actor = started.actors[0]
        knowledge = Knowledge(started.scope.campaign_id)

        def pack():
            return pack_actor_context(chat_snapshot(store.load(), started.chat).snapshot, knowledge, actor,
                                      as_of=1, budget=Budget(200, {'lore': 60}), scene_text='The ferry is late.',
                                      readers=lore.READERS, unpack=stored_record)

        before = pack()
        assert before.lane('lore') and before.tokens <= 200
        tx = store.transaction()
        unrelated = tuple(SourceRef(CAST, 'item', f'unrelated-{i}', 'r1') for i in range(2000))
        for ref in unrelated:
            tx.put(RecordEnvelope(ref, {'text': 'UNRELATED-CATALOG-TOKEN'}, name='Unrelated gear'), None)
        tx.commit(owner_handle())
        publish(store, Catalog(PACK.at('r2'), (BASE, COMMON, GEAR, *unrelated)))
        large = compose_catalogs(store.load(), (PACK.at('r2'),))
        assert len(large.entries) == 2003
        assert len(large.search(limit=5)) == 5
        assert large.select(selected.sources) == selected
        after = pack()
        assert after == before
        assert 'UNRELATED-CATALOG-TOKEN' not in after.render()
