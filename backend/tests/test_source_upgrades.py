"""079 acceptance: preview removing or updating a source by qualified id only. Synthetic fixtures."""
import shutil
from dataclasses import replace

import pytest

from chat_fixtures import Clock, Ids
from portrayal_fixtures import (
    BASE, CAST, ELSEWHERE, MECHA, MODERN, MODERN_MARA, NAMESAKE, NOTES, VAMPIRE, base, envelope, manifest,
    seed_four_portrayals,
)
from store_helpers import OWNER, owner_handle

from grimoire.catalogs import Catalog, compose_catalogs, publish_catalog
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import SourceRef
from grimoire.sourceupgrades import (
    DEPENDENCIES_KEY, SourceUpgradeError, dependency_report, pin_dependency, preview_source_removal,
    preview_source_update, source_version,
)
from grimoire.sourceupgrades.preview import _owner, _references
from grimoire.store import LibraryStore, RecordEnvelope, StaleWrite, StoreError
from grimoire.testing.fileops import snapshot as files_of

ALPHA = SourceRef(CAST, 'catalog', 'alpha', 'r1')
BETA = SourceRef(CAST, 'catalog', 'beta', 'r1')
WORLD_PACK = SourceRef(MODERN, 'catalog', 'available', 'r1')
VAMPIRE_PACK = SourceRef(VAMPIRE, 'catalog', 'available', 'r1')  # the same two, listed the other way
LOOKALIKE = 'catalog:elderfen-cast:extra'  # a separate source stored under CAST's folder
LOOKALIKE_MARA = SourceRef(LOOKALIKE, 'character', 'mara', 'r1')


def publish(store, catalog, name=None):
    publish_catalog(store, owner_handle(), catalog, expected=store.read_source(catalog.ref.source).etag, name=name)


def seed(store):
    """Shared cast with two catalogs, four world portrayals of its Mara, two world catalogs that
    pin both cast catalogs in opposite orders, namesake Maras elsewhere, and two chats: one pinned,
    one following."""
    seed_four_portrayals(store, owner_handle())
    tx = store.transaction()
    tx.put(manifest(LOOKALIKE), None)
    tx.put(envelope(replace(base(), ref=LOOKALIKE_MARA)), None)
    tx.commit(owner_handle())
    publish(store, Catalog(ALPHA, (BASE,)), 'Alpha pack')
    publish(store, Catalog(BETA, (NOTES,)), 'Beta pack')
    publish(store, Catalog(WORLD_PACK, (MODERN_MARA,), (ALPHA, BETA)))
    publish(store, Catalog(VAMPIRE_PACK, (), (BETA, ALPHA), (BASE.unpinned(),)))
    chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
    pinned = chats.start_chat(Launch('character', BASE.unpinned()), attach=[(NOTES, 'lorebook')]).commit()
    following = chats.start_chat(Launch('character', BASE.unpinned()), follow=True).commit()
    chats.attach_world(following.scope, MODERN, expected=chats.read_scope(following.scope)[1]).commit()
    actor = store.read(pinned.actors[0])
    tx = store.transaction()  # a campaign override of the inherited actor
    tx.put(replace(actor.envelope, payload={**actor.envelope.payload, 'local_name': 'Mara the traveller'}),
           actor.etag)
    tx.commit(owner_handle())
    return pinned, following


def candidate_of(store, tmp_path, change):
    """A copy of ``store`` with ``change`` applied: an unpacked update, opened read-only after."""
    root = tmp_path / 'candidate'
    shutil.copytree(store.root, root)
    with LibraryStore.open(root) as copy:
        change(copy)
    return LibraryStore.open(root, writable=False)


def by_text(preview):
    rows = {}
    for row in preview.references:
        rows.setdefault(row.text, []).append(row)
    return rows


def test_removal_reports_missing_references_and_never_a_namesake(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        pinned, _ = seed(store)
        before = files_of(store.root)
        preview = preview_source_removal(store, CAST)
        assert files_of(store.root) == before  # a preview never writes
        assert preview.complete and preview.action == 'remove' and preview.after is None
        rows = by_text(preview)
        # The pinned chat, its actor, every world portrayal (derivation and link) and the world catalog.
        held = {row.path for row in rows[str(BASE)]}
        assert any(path.startswith(f'campaigns/{pinned.scope.campaign_id}/chats/') for path in held)
        assert any(path.startswith(f'campaigns/{pinned.scope.campaign_id}/instances/') for path in held)
        for world in (MODERN, VAMPIRE, MECHA):
            assert any(path.startswith('sources/' + world.replace(':', '/') + '/') for path in held)
        assert {row.status for row in preview.references} == {'missing'}
        assert {str(ALPHA), str(BETA), str(NOTES), str(BASE.unpinned())} <= set(rows)
        # An exclusion names the entity only, and is still listed as missing on removal.
        assert any(row.path.startswith('sources/world/elderfen-vampire/') for row in rows[str(BASE.unpinned())])
        assert all(row.after is None and row.newer_head is None for row in preview.references)
        # Names never establish identity: two other sources hold a Mara, neither is offered.
        assert store.load().index.find_by_name('Mara', 'character')
        texts = {row.text for row in preview.references} | {row.path for row in preview.references}
        assert not any(ELSEWHERE in text or str(NAMESAKE) in text for text in texts)
        assert not any(row.text.startswith(LOOKALIKE) for row in preview.references)
        # The lookalike's own files are referrers, not the removed source's: its Mara stays put.
        assert all(entity.after is None for entity in preview.entities)
        assert LOOKALIKE_MARA.unpinned() not in {entity.ref for entity in preview.entities}
        assert MODERN not in rows  # the following chat's world is not the removed source


def test_removing_a_world_reports_its_attachment(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        preview = preview_source_removal(store, MODERN)
        scope_rows = by_text(preview)[MODERN]
        assert [row.record for row in scope_rows] == ['scope']
        assert scope_rows[0].status == 'missing' and scope_rows[0].before == store.read_source(MODERN).etag


def test_update_reports_by_id_when_catalog_names_and_head_order_swap(tmp_path):
    """Reordering and renaming two catalogs does not swap identity."""
    with LibraryStore.create(tmp_path / 'lib') as store:
        _, following = seed(store)

        def update(copy):
            source = copy.read_source(CAST)
            tx = copy.transaction()
            # alpha now carries beta's old name and members, and the other way round
            tx.put(RecordEnvelope(ALPHA.at('r2'), {'members': [str(NOTES)], 'dependencies': [], 'exclusions': []},
                                  name='Beta pack', extra={'schema': 'grimoire.catalog/1'}), None)
            tx.put(RecordEnvelope(BETA.at('r2'), {'members': [str(BASE)], 'dependencies': [], 'exclusions': []},
                                  name='Alpha pack', extra={'schema': 'grimoire.catalog/1'}), None)
            tx.put(envelope(replace(base(), ref=BASE.at('r2'), description='Revised')), None)
            heads = dict(reversed(list({**source.manifest.heads, 'catalog/alpha': 'r2', 'catalog/beta': 'r2',
                                        'character/mara': 'r2'}.items())))
            tx.put(replace(source.manifest, heads=heads), source.etag)
            tx.commit(owner_handle())

        with candidate_of(store, tmp_path, update) as candidate:
            before = files_of(store.root)
            preview = preview_source_update(store, CAST, candidate)
            assert files_of(store.root) == before
        rows = by_text(preview)
        for ref in (ALPHA, BETA):
            # Both world catalogs list both packs, in opposite orders: each row keeps its own id.
            assert {row.path.split('/')[2] for row in rows[str(ref)]} == {'elderfen-modern', 'elderfen-vampire'}
            assert {(row.status, row.before, row.after, row.newer_head) for row in rows[str(ref)]} == {
                ('unchanged', 'r1', 'r1', 'r2')}
        changes = {entity.ref: entity for entity in preview.entities}
        assert (changes[ALPHA.unpinned()].name_before, changes[ALPHA.unpinned()].name_after) == ('Alpha pack', 'Beta pack')
        assert changes[ALPHA.unpinned()].renamed and changes[ALPHA.unpinned()].status == 'moved'
        # Pinned actors and chats stay on r1 and are offered r2; the following chat and its actor move.
        assert {(row.status, row.newer_head) for row in rows[str(BASE)]} == {('unchanged', 'r2')}
        moving = [row for row in rows[str(BASE.unpinned())] if row.status == 'follows_head']
        assert {row.record for row in moving} == {'chat', 'instance'}
        assert all(row.path.startswith(f'campaigns/{following.scope.campaign_id}/') for row in moving)
        assert {(row.status, row.before, row.after) for row in moving} == {('follows_head', 'r1', 'r2')}
        # Art layers and the exclusion name Mara only: listed, unchanged, never a follow.
        assert {row.status for row in rows[str(BASE.unpinned())] if row not in moving} == {'unchanged'}
        assert set(moving) <= set(preview.attention) and preview.edited == ()
        # The vampire catalog's exclusion of Mara reaches no content, so it never reads as a follow.
        assert not any(row.path.startswith('sources/world/elderfen-vampire/_source/records/catalog/')
                       for row in moving)
        [exclusion] = [row for row in preview.references if row.text == str(BASE.unpinned())
                       and row.path.startswith('sources/world/elderfen-vampire/')]
        assert exclusion.status == 'unchanged' and not exclusion.attention


def test_update_reports_a_binding_reattached_to_follow(tmp_path):
    """A chat that detached a pinned lorebook and reattached it following its head still moves."""
    with LibraryStore.create(tmp_path / 'lib') as store:
        pinned, _ = seed(store)
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        chats.detach_source(pinned.chat, NOTES, 'lorebook', expected=chats.read_chat(pinned.chat)[1]).commit()
        chats.attach_source(pinned.chat, NOTES.unpinned(), 'lorebook', expected=chats.read_chat(pinned.chat)[1],
                            follow=True).commit()

        def update(copy):
            source = copy.read_source(CAST)
            tx = copy.transaction()
            tx.put(RecordEnvelope(NOTES.at('r2'), {'entries': ['the ferry is gone']}, name='River notes'), None)
            tx.put(replace(source.manifest, heads={**source.manifest.heads, 'lorebook/river-notes': 'r2'}),
                   source.etag)
            tx.commit(owner_handle())

        with candidate_of(store, tmp_path, update) as candidate:
            preview = preview_source_update(store, CAST, candidate)
        chat_path = f'campaigns/{pinned.chat.campaign_id}/chats/{pinned.chat.chat_id}.json'
        found = {(row.text, row.status) for row in preview.references if row.path == chat_path}
        assert (str(NOTES.unpinned()), 'follows_head') in found


def test_update_flags_edited_and_dropped_revisions(tmp_path):
    """Pins protect against moving heads, not against bytes changed at the same revision."""
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)

        def update(copy):
            path = copy.root / 'sources/catalog/elderfen-cast/_source/records/lorebook/river-notes/r1.json'
            path.write_text(path.read_text().replace('the ferry runs at dusk', 'the ferry never runs'))
            (copy.root / 'sources/catalog/elderfen-cast/_source/records/catalog/beta/r1.json').unlink()

        with candidate_of(store, tmp_path, update) as candidate:
            preview = preview_source_update(store, CAST, candidate)
        rows = by_text(preview)
        assert {row.status for row in rows[str(NOTES)]} == {'edited'}
        assert preview.edited == (NOTES,)
        assert {row.status for row in rows[str(BETA)]} == {'missing'}


def test_update_needs_the_source_in_the_candidate(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store, \
            LibraryStore.create(tmp_path / 'empty') as empty:
        seed(store)
        with pytest.raises(SourceUpgradeError, match='candidate_missing_source'):
            preview_source_update(store, CAST, empty)
        with pytest.raises(SourceUpgradeError, match='invalid_source'):
            preview_source_removal(store, 'Not A Source')


def test_previewing_an_already_removed_source_lists_leftovers(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        preview = preview_source_removal(store, 'catalog:gone')
        assert preview.references == () and preview.before is None
        shutil.rmtree(store.root / 'sources/world/elderfen-modern')  # removed by hand, attachment left
        [leftover] = preview_source_removal(store, MODERN).references
        assert (leftover.record, leftover.text, leftover.status) == ('scope', MODERN, 'still_missing')
        assert preview_source_removal(store, MECHA).references == ()  # only MECHA itself names its Mara


def test_pin_and_report_an_optional_dependency(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        assert dependency_report(store.load(), MODERN) == ()
        pin_dependency(store, owner_handle(), MODERN, CAST, expected=store.read_source(MODERN).etag)
        declared = store.read_source(MODERN).manifest
        assert declared.extra[DEPENDENCIES_KEY] == ({'source': CAST, 'version': source_version(store.load(), CAST)},)
        assert declared.links  # the portrayal link is kept
        assert [s.status for s in dependency_report(store.load(), MODERN)] == ['pinned']
        # Mutual pins hold, and the cast's own declarations and title are not its version.
        pin_dependency(store, owner_handle(), CAST, MODERN, expected=store.read_source(CAST).etag)
        source = store.read_source(CAST)
        tx = store.transaction()
        tx.put(replace(source.manifest, title='Renamed cast'), source.etag)
        tx.commit(owner_handle())
        assert [s.status for s in dependency_report(store.load(), MODERN)] == ['pinned']
        assert [s.status for s in dependency_report(store.load(), CAST)] == ['pinned']
        # Bytes edited at a head revision drift it, with no manifest change.
        notes = store.root / 'sources/catalog/elderfen-cast/_source/records/lorebook/river-notes/r1.json'
        original = notes.read_text()
        notes.write_text(original.replace('the ferry runs at dusk', 'the ferry never runs'))
        assert [s.status for s in dependency_report(store.load(), MODERN)] == ['drifted']
        notes.write_text(original)
        # The cast moves a head: the declaration drifts, and the removal preview names it.
        source = store.read_source(CAST)
        tx = store.transaction()
        tx.put(envelope(replace(base(), ref=BASE.at('r2'))), None)
        tx.put(replace(source.manifest, heads={**source.manifest.heads, 'character/mara': 'r2'}), source.etag)
        tx.commit(owner_handle())
        [status] = dependency_report(store.load(), MODERN)
        assert status.status == 'drifted' and status.current == source_version(store.load(), CAST)
        manifest_rows = [row for row in preview_source_removal(store, CAST).references
                         if row.record == 'manifest' and row.text == CAST]
        assert [row.path for row in manifest_rows] == ['sources/world/elderfen-modern/_source/manifest.json']
        # Re-pinning is the deliberate upgrade of the declaration.
        pin_dependency(store, owner_handle(), MODERN, CAST, expected=store.read_source(MODERN).etag)
        assert [s.status for s in dependency_report(store.load(), MODERN)] == ['pinned']
        assert len(store.read_source(MODERN).manifest.extra[DEPENDENCIES_KEY]) == 1
        # A hand-written declaration without a pin follows the dependency.
        source = store.read_source(MODERN)
        tx = store.transaction()
        tx.put(replace(source.manifest, extra={DEPENDENCIES_KEY: [{'source': CAST, 'version': None}]}),
               source.etag)
        tx.commit(owner_handle())
        assert [s.status for s in dependency_report(store.load(), MODERN)] == ['following']
        # Optional: with the cast gone, the world still loads and reports it missing.
        shutil.rmtree(store.root / 'sources/catalog/elderfen-cast/_source')
        view = store.load()
        assert MODERN in view.sources
        assert [s.status for s in dependency_report(view, MODERN)] == ['missing']


def test_pin_refuses_stale_self_missing_and_unauthorized_writes(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        expected = store.read_source(MODERN).etag
        before = files_of(store.root)
        with pytest.raises(StaleWrite):
            pin_dependency(store, owner_handle(), MODERN, CAST, expected='sha256:' + '0' * 64)
        with pytest.raises(SourceUpgradeError, match='self_dependency'):
            pin_dependency(store, owner_handle(), MODERN, MODERN, expected=expected)
        with pytest.raises(SourceUpgradeError, match='missing_dependency'):
            pin_dependency(store, owner_handle(), MODERN, 'catalog:gone', expected=expected)
        with pytest.raises(StoreError):
            pin_dependency(store, owner_handle('campaigns'), MODERN, CAST, expected=expected)
        assert files_of(store.root) == before


@pytest.mark.parametrize('declared, reason', [
    ('not a list', 'invalid_dependency'),
    ([{'source': 'Bad Id'}], 'invalid_dependency'),
    ([{'source': CAST, 'version': 'r1'}], 'invalid_dependency'),
    ([{'source': CAST, 'manifest_etag': None}], 'invalid_dependency'),
    ([{'source': CAST, 'extra': 1}], 'invalid_dependency'),
    ([{'source': CAST}, {'source': CAST}], 'invalid_dependency'),
    ([{'source': MODERN}], 'self_dependency'),
    ([{'source': f'catalog:s{n}'} for n in range(33)], 'limit_exceeded'),
])
def test_bad_declarations_refuse(tmp_path, declared, reason):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        source = store.read_source(MODERN)
        tx = store.transaction()
        tx.put(replace(source.manifest, extra={DEPENDENCIES_KEY: declared}), source.etag)
        tx.commit(owner_handle())
        with pytest.raises(SourceUpgradeError, match=reason):
            dependency_report(store.load(), MODERN)


def test_catalog_composition_order_is_identity_free(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        view = store.load()
        assert compose_catalogs(view, (ALPHA, BETA)).entries == compose_catalogs(view, (BETA, ALPHA)).entries


def test_raw_scan_matches_exact_source_ids_only():
    payload = {
        'a': f'{CAST}/character/mara@r1', 'b': [f'{CAST}/character/mara', f'{CAST}#image/portrait'],
        'world': {'source': CAST, 'observed_manifest_etag': 'sha256:' + '0' * 64},
        'near': [f'{LOOKALIKE}/character/mara@r1', f'{CAST}-2/character/mara@r1', f'{LOOKALIKE}#image/portrait',
                 {'source': LOOKALIKE}, {'title': CAST}, 'Mara', f'see {CAST}/character/mara@r1'],
        f'{CAST}/lorebook/key@r1': 'a key names it too',
    }
    assert _references(payload, CAST)[0] == {
        ('definition', f'{CAST}/character/mara@r1'), ('definition', f'{CAST}/character/mara'),
        ('image', f'{CAST}#image/portrait'), ('source', CAST), ('definition', f'{CAST}/lorebook/key@r1')}
    art = {'art_pin': {'layers': [{'entity': f'{CAST}/character/mara'}]}}
    assert _references(art, CAST)[0] == {('identity', f'{CAST}/character/mara')}
    exclusion = {'schema': 'grimoire.catalog/1', 'payload': {'exclusions': [f'{CAST}/character/mara']}}
    assert _references(exclusion, CAST)[0] == {('identity', f'{CAST}/character/mara')}
    assert _references({**exclusion, 'schema': 'other/1'}, CAST)[0] == {('definition', f'{CAST}/character/mara')}
    assert _owner('sources/catalog/elderfen-cast/extra/_source/manifest.json') == LOOKALIKE
    assert _owner('sources/catalog/elderfen-cast/_source/records/x/y/r1.json') == CAST
    assert _owner('campaigns/q-1/scope.json') is None


def test_stray_strings_deep_nesting_and_a_damaged_candidate_do_not_break_a_preview(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed(store)
        nested: object = str(BASE)
        for _ in range(90):
            nested = [nested]
        tx = store.transaction()
        tx.put(RecordEnvelope(SourceRef(ELSEWHERE, 'lorebook', 'odd', 'r1'),
                              {'art': f'{CAST}#image/con', 'deep': nested}, name='Odd'), None)
        tx.commit(owner_handle())
        rows = {row.text: row for row in preview_source_removal(store, CAST).references
                if row.path.startswith('sources/world/elsewhere/')}
        assert rows[f'{CAST}#image/con'].status == 'still_missing'  # no file can hold that id
        assert rows[str(BASE)].status == 'missing'

        def damage(copy):
            (copy.root / 'sources/catalog/elderfen-cast/_source/manifest.json').write_text('{not json')

        with candidate_of(store, tmp_path, damage) as candidate:
            preview = preview_source_update(store, CAST, candidate)
        assert preview.after is None and preview.complete
        assert any(problem.side == 'after' for problem in preview.problems)
        assert len(set(preview.problems)) == len(preview.problems)
        assert {row.status for row in preview.references} == {'missing', 'still_missing'}
