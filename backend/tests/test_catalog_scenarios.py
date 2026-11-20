"""056 acceptance: three synthetic worlds and quick chats reuse authored references only."""
from dataclasses import replace

import pytest

from chat_fixtures import Clock, Ids
import context_fixtures as lore
from portrayal_fixtures import (
    BASE, CAST, MODERN, MODERN_MARA, VAMPIRE, VAMPIRE_MARA, MECHA, MECHA_MARA,
    NAMESAKE, base, derived, derives, envelope, manifest, seed_four_portrayals,
)
from store_helpers import OWNER, owner_handle

from grimoire.catalogs import Catalog, CatalogError, compose_catalogs, publish_catalog
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.context import Budget, pack_actor_context
from grimoire.contextstore import stored_record
from grimoire.core.entity import EntityDefinition, EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.facets import Biological, FacetSet, Physical
from grimoire.core.identity import InstanceRef, ScopeRef, SourceRef, TranscriptRef
from grimoire.core.knowledge import Knowledge
from grimoire.core.snapshot import SelectedSource
from grimoire.effective import chat_snapshot, selection_snapshot
from grimoire.store import LibraryStore, LinkSpec, RecordEnvelope, ScopeRecord
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


# -- the inherited proving scenario: one shared bestiary, two settings ----------------------------------

HERD = 'catalog:shared-bestiary'
BEASTS = SourceRef(HERD, 'catalog', 'beasts', 'r1')
WYRM = SourceRef(HERD, 'creature', 'river-wyrm', 'r1')
HOUND = SourceRef(HERD, 'creature', 'frost-hound', 'r1')
CRYPT_WYRM = SourceRef(VAMPIRE, 'creature', 'river-wyrm', 'r1')
CREATORS = 'catalog:creator-cards'
CARD_SET = SourceRef(CREATORS, 'catalog', 'set', 'r1')
GUIDE = SourceRef(CREATORS, 'character', 'ferry-guide', 'r1')
CREATOR_LORE = SourceRef(CREATORS, 'lorebook', 'crossings', 'r1')
UNRELATED_CARDS = tuple(SourceRef(CREATORS, 'lorebook', f'card-{i}', 'r1') for i in range(2000))
SETTINGS = {MODERN: 'c-modern', VAMPIRE: 'c-vampire'}
WYRM_SECTIONS = {'habitat': 'Slow rivers and ferry crossings.', 'threat': 'Capsizes small boats at dusk.',
                 'lore': 'Bells drive it under.'}


def river_lore(entry_id, text):
    """A lorebook whose one entry the shared scene text triggers, so binding it would visibly reach a pack."""
    return {'entries': [lore.entry(entry_id, text, 'river', 'fog')]}


def bestiary_catalog(world):
    return SourceRef(world, 'catalog', 'bestiary', 'r1')


def setup_bestiary(store):
    """A shared bestiary pack used by two settings; Vampire specializes the wyrm and Modern drops the hound."""
    seed_four_portrayals(store, owner_handle())
    tx = store.transaction()
    tx.put(manifest(HERD), None)
    tx.put(manifest(CREATORS), None)
    tx.put(envelope(EntityDefinition(WYRM, 'River wyrm', 'template', FacetSet([Physical(), Biological()]),
                                     'A long grey beast under slow water.', {'sections': WYRM_SECTIONS})), None)
    tx.put(envelope(EntityDefinition(HOUND, 'Frost hound', 'template', FacetSet([Physical(), Biological()]))), None)
    tx.put(envelope(EntityDefinition(GUIDE, 'Ferry guide', 'individual', FacetSet([Physical()]),
                                     'Knows every crossing.')), None)
    tx.put(RecordEnvelope(CREATOR_LORE, river_lore('crossings', 'CREATOR-LORE-TOKEN the north crossing floods.'),
                          name='Crossings', extra={'schema': lore.LORE_SCHEMA}), None)
    for ref in UNRELATED_CARDS:  # lore this very scene text would trigger if any of it were bound
        tx.put(RecordEnvelope(ref, river_lore(ref.entity_id, 'UNRELATED-CARD-TOKEN a stranger crossing.'),
                              name='Unrelated card', extra={'schema': lore.LORE_SCHEMA}), None)
    tx.put(envelope(derived(CRYPT_WYRM, WYRM, name='River wyrm', nature='template',
                            sections={'habitat': 'The flooded crypts under the old ferry.'})), None)
    vampire = store.read_source(VAMPIRE)
    link = derives(CRYPT_WYRM, WYRM)
    tx.put(replace(vampire.manifest, links=(*vampire.manifest.links,
                                            LinkSpec(link.subject, link.relation, link.object))), vampire.etag)
    for world, campaign in SETTINGS.items():
        played = CRYPT_WYRM if world == VAMPIRE else WYRM
        actor = InstanceRef(campaign, 'creature', 'wyrm-1')
        tx.put(RecordEnvelope(actor, instance_to_payload(EntityInstance(actor, source=played)), owner=OWNER,
                              extra={'schema': ENTITY_SCHEMA}), None)
    tx.commit(owner_handle())
    publish(store, Catalog(BEASTS, (WYRM, HOUND)))
    publish(store, Catalog(CARD_SET, (GUIDE, CREATOR_LORE, *UNRELATED_CARDS)))
    publish(store, Catalog(bestiary_catalog(MODERN), (), (BEASTS,), (HOUND.unpinned(),)))
    publish(store, Catalog(bestiary_catalog(VAMPIRE), (CRYPT_WYRM,), (BEASTS,)))


def test_one_shared_bestiary_supports_two_settings_without_importing_a_card_set(tmp_path):
    """Sprint 056's proving scenario: one pinned shared creature in two settings (one specialized), and a
    campaign that opts into a 2,002-record creator card set packs none of its unselected cards into a turn."""
    with LibraryStore.create(tmp_path / 'lib') as store:
        setup_bestiary(store)
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        shared_etag = store.load().index.resolve(WYRM).etag
        started = {}
        for world, campaign in SETTINGS.items():
            roots = (bestiary_catalog(world), CARD_SET) if world == MODERN else (bestiary_catalog(world),)
            available = compose_catalogs(store.load(), roots)
            entries = {entry.ref: entry for entry in available.entries}
            # The very same pinned creature, read from the one shared file, is available in both settings.
            assert [origin.ref for origin in entries[WYRM].origins] == [BEASTS]
            assert (HOUND in entries) == (world == VAMPIRE)
            assert (CRYPT_WYRM in entries) == (world == VAMPIRE)
            assert (GUIDE in entries) == (CREATOR_LORE in entries) == (world == MODERN)
            # Modern opts into the whole populated creator set; discovery over it stays bounded.
            assert len(entries) == (2003 if world == MODERN else 3)
            assert len(available.search('card', limit=10)) == (10 if world == MODERN else 0)
            played = CRYPT_WYRM if world == VAMPIRE else WYRM
            wanted = (SelectedSource(played, 'actor'),)
            if world == MODERN:
                wanted += (SelectedSource(GUIDE, 'character'), SelectedSource(CREATOR_LORE, 'lorebook'))
            selected = available.select(wanted, world=world)
            # Discovery honours each setting's availability: no hound in Modern, no creator card in Vampire.
            # (Availability is not access control; the chat binds only what was explicitly selected.)
            refused = HOUND if world == MODERN else GUIDE
            with pytest.raises(CatalogError) as error:
                available.select((SelectedSource(refused, 'actor' if refused == HOUND else 'character'),))
            assert error.value.reason == 'not_available'
            actor = InstanceRef(campaign, 'creature', 'wyrm-1')
            change = chats.start_chat(Launch('premise', premise='Fog on the river.'), scope=ScopeRef(campaign),
                                      actors=[(played, actor)],
                                      attach=[(s.ref, s.role) for s in selected.sources if s.role != 'actor'])
            commit = change.commit()
            scope = commit.scope
            chats.attach_world(scope, world, expected=chats.read_scope(scope)[1]).commit()
            started[world] = (commit.chat, actor)
        knowledge = {world: Knowledge(campaign) for world, campaign in SETTINGS.items()}

        def packs():
            found = {}
            for world, (chat, actor) in started.items():
                captured = chat_snapshot(store.load(), chat)
                assert captured.scope.world.source == world and not captured.problems
                found[world] = (captured, pack_actor_context(
                    captured.snapshot, knowledge[world], actor, as_of=1, budget=Budget(400),
                    scene_text='Fog on the river.', readers=lore.READERS, unpack=stored_record))
            return found

        bound = {VAMPIRE: {CRYPT_WYRM}, MODERN: {WYRM, GUIDE, CREATOR_LORE}}
        before = packs()
        for world, (captured, pack) in before.items():
            assert {r.resolved for r in captured.snapshot.sources()} == bound[world]
            # Positive control: the one selected creator lorebook reaches the opted-in campaign's turn, while
            # none of the 2,000 unselected lorebooks in the same set does, though this scene would trigger them.
            assert ('CREATOR-LORE-TOKEN' in pack.render()) == (world == MODERN)
            assert {item.origin.source for item in pack.lane('lore')} == ({CREATOR_LORE} if world == MODERN else set())
            assert 'UNRELATED-CARD-TOKEN' not in pack.render()
            sections = {item.origin.name: item for item in pack.lane('portrayal') if item.origin.kind == 'section'}
            assert sections['threat'].origin.source == WYRM and sections['lore'].origin.source == WYRM
            habitat = sections['habitat']
            if world == VAMPIRE:  # a setting-specific specialization that inherits the rest of the shared card
                assert habitat.origin.source == CRYPT_WYRM and 'flooded crypts' in habitat.line
                chain = captured.snapshot.effective(CRYPT_WYRM, 'actor').chain
                assert chain[-1].ref == WYRM and chain[-1].etag == shared_etag
            else:
                assert habitat.origin.source == WYRM and 'Slow rivers' in habitat.line
            assert 'A long grey beast under slow water.' in pack.render()
        # Neither setting copied the shared creature; its one canonical file is the shared pack's.
        holders = [path for path, data in files_of(store.root).items() if data and b'Capsizes small boats' in data]
        assert len(holders) == 1 and 'shared-bestiary' in str(holders[0])

        # A later turn rereads the same bindings and packs the same context.
        for world, (captured, pack) in packs().items():
            assert {r.resolved for r in captured.snapshot.sources()} == bound[world]
            assert pack == before[world][1]
        assert store.load().index.resolve(WYRM).etag == shared_etag
