"""Bounded, read-only workspace queries over synthetic canonical navigation."""
import pytest
from textapi_fixtures import desktop_library, Device
from grimoire.textapi import ApiRefused


def test_workspace_is_bounded_and_rejects_invalid_pages(tmp_path):
    table, chat = desktop_library(tmp_path / 'desk')
    with Device(table) as api:
        from grimoire.app.appapi import AppApi
        result = AppApi(api.api).call('chats.workspace')
        assert str(chat) in {row['chat'] for row in result['chats']}
        assert len(result['chats']) <= 50
        assert len(result['sources']) <= 50
        assert result['snapshot']
        assert api('chats.workspace', q='not a synthetic title')['total'] == 0
        with pytest.raises(ApiRefused):
            api('chats.workspace', offset=1)
        for args in ({'limit':51}, {'limit':True}, {'offset':True}, {'source_offset':-1}):
            with pytest.raises(ApiRefused):
                api('chats.workspace', **args)
        with pytest.raises(ApiRefused) as caught:
            api('chats.workspace', snapshot='stale')
        assert caught.value.reason == 'changed'


def test_full_library_search_order_bounded_pages_and_stale_activity():
    from types import SimpleNamespace
    from grimoire.chats.navigation import NavEntry, NavigationIndex, ScopeEntry
    from grimoire.core.identity import ChatRef, ScopeRef
    from grimoire.textapi.workspace import workspace
    entries = [NavEntry(ChatRef('synthetic', f'chat-{i:04}'), label=f'Room {i}') for i in range(1101)]
    def index():
        return NavigationIndex.build(entries, [ScopeEntry(ScopeRef('synthetic'))])
    view = SimpleNamespace(sources={}, problems=[], index=SimpleNamespace(definitions=lambda: ()))
    class Api:
        store = SimpleNamespace(load=lambda: view)
        chats = SimpleNamespace(navigation=lambda **kw: index())
        def _mention_names(self, view, entries):
            return {}
        def _entries(self, view, idx, refs, names):
            return [{'chat':str(ref), 'label':idx.entry(ref).label, 'launch':None} for ref in refs]
    api = Api()
    first = workspace(api, {})
    assert first['total'] == 1101 and len(first['chats']) == 50
    assert first['next'] == 50
    second = workspace(api, {'offset':50, 'snapshot':first['snapshot']})
    assert not ({row['chat'] for row in first['chats']} & {row['chat'] for row in second['chats']})
    assert workspace(api, {'q':'Room 1100'})['total'] == 1
    entries[-1] = NavEntry(entries[-1].ref, label='Renamed')
    with pytest.raises(ApiRefused) as caught:
        workspace(api, {'offset':50, 'snapshot':first['snapshot']})
    assert caught.value.reason == 'changed'


def test_exact_and_validated_descendants_exclude_namesakes_and_broken_ancestry(monkeypatch):
    from types import SimpleNamespace as NS
    from grimoire.chats.navigation import NavEntry, NavigationIndex, ScopeEntry
    from grimoire.core.identity import ChatRef, ScopeRef, SourceRef
    from grimoire.core.portrayal import PortrayalError
    import importlib
    mod = importlib.import_module('grimoire.textapi.workspace')
    refs = [SourceRef('card:one', 'character', 'same', revision='r1'),
            SourceRef('card:one', 'character', 'same', revision='r2'),
            SourceRef('world:derived', 'character', 'same', revision='r1'),
            SourceRef('card:namesake', 'character', 'same', revision='r1'),
            SourceRef('card:broken', 'character', 'same', revision='r1')]
    entries = [NavEntry(ChatRef('synthetic', f'c{i}'), mentions=(('character', ref.entity_key, ref.revision, True),)*2)
               for i, ref in enumerate(refs)]
    idx = NavigationIndex.build(entries, [ScopeEntry(ScopeRef('synthetic'))])
    paths = {ref:[ref] for ref in refs}
    paths[refs[2]] = [refs[2], refs[0]]
    def compose(view, ref):
        if ref == refs[-1]:
            raise ValueError('malformed ancestry')
        return NS(chain=[NS(ref=node, etag='etag') for node in paths[ref]])
    monkeypatch.setattr(mod, 'compose_in', compose)
    view = NS(sources={}, problems=[], index=NS(definitions=lambda: ()))
    class Api:
        store = NS(load=lambda: view)
        chats = NS(navigation=lambda **kw: idx)
        def _mention_names(self, view, entries): return {str(ref.unpinned()):'Namesake' for ref in refs}
        def _entries(self, view, index, found, names):
            return [{'chat':str(ref), 'label':None, 'launch':None} for ref in found]
    api = Api()
    exact = mod.workspace(api, {'filter':'character', 'source':str(refs[0])})
    assert exact['total'] == 1
    derived = mod.workspace(api, {'filter':'character', 'source':str(refs[0]), 'match':'derived'})
    assert derived['total'] == 2
    assert len({row['chat'] for row in derived['chats']}) == 2
    assert derived['diagnostics'][0]['ref'] == str(refs[-1])
    assert mod.workspace(api, {'filter':'character', 'source':str(refs[0].unpinned())})['total'] == 2
    entries[0] = NavEntry(entries[0].ref, mentions=(('character', refs[0].entity_key, refs[0].revision, False),))
    idx = NavigationIndex.build(entries, [ScopeEntry(ScopeRef('synthetic'))])
    with pytest.raises(ApiRefused):
        mod.workspace(api, {'snapshot':derived['snapshot']})
    idx = NavigationIndex.build([NavEntry(entries[0].ref, mentions=(('character', refs[0].entity_key, refs[0].revision, True),))] + entries[1:], [ScopeEntry(ScopeRef('synthetic'))])
    paths[refs[2]] = [refs[2], refs[1]]
    with pytest.raises(ApiRefused) as caught:
        mod.workspace(api, {'snapshot':derived['snapshot']})
    assert caught.value.reason == 'changed'


def test_real_store_validated_portrayals_and_removed_link_are_read_only(tmp_path):
    from portrayal_fixtures import seed_elderfen, BASE, MODERN_MARA, NAMESAKE, MODERN, CAST, manifest, derives
    from store_helpers import authorizer, OWNER
    from grimoire.store import LibraryStore
    from grimoire.textapi import TextApi
    from grimoire.chats import Launch
    from grimoire.sync import identify
    from grimoire.testing.fileops import snapshot
    with LibraryStore.create(tmp_path / 'real') as store:
        authority = authorizer()
        handle = authority.handle_for(OWNER, trusted=True)
        seed_elderfen(store, handle)
        identify(store, 'synthetic-device')
        from grimoire.chatstore import ChatLibrary
        from grimoire.textapi.workspace import workspace
        import threading
        api = TextApi.__new__(TextApi)
        api.store = store
        from chat_fixtures import Clock, Ids
        api.chats = ChatLibrary(store, handle, owner=OWNER, clock=Clock(), new_id=Ids())
        api._lock = threading.RLock()
        api._ops = {'chats.workspace':lambda args: workspace(api,args)}
        refs = []
        for ref in (BASE, MODERN_MARA, NAMESAKE):
            change = api.chats.start_chat(Launch('character', ref))
            change.commit()
            refs.append(change.chat)
        before = snapshot(store.root)
        exact = api.call('chats.workspace', {'filter':'character', 'source':str(BASE)})
        derived = api.call('chats.workspace', {'filter':'character', 'source':str(BASE), 'match':'derived'})
        assert exact['total'] == 1 and derived['total'] == 2
        assert next(item for item in derived['comparisons'] if item['ref'] == str(MODERN_MARA))['path'] == [str(MODERN_MARA), str(BASE)]
        assert snapshot(store.root) == before
        txn = store.transaction()
        txn.put(manifest(MODERN), store.read_source(MODERN).etag)
        txn.commit(handle)
        broken = api.call('chats.workspace', {'filter':'character', 'source':str(BASE), 'match':'derived'})
        assert broken['total'] == 1
        assert any(item['ref'] == str(MODERN_MARA) and item['code'] == 'unlinked_derivation' for item in broken['diagnostics'])
        with pytest.raises(ApiRefused):
            api.call('chats.workspace', {'snapshot':derived['snapshot']})

        # Restore the link, then introduce a reverse cycle. The loader/composer must not broaden it.
        txn = store.transaction()
        txn.put(manifest(MODERN, derives(MODERN_MARA)), store.read_source(MODERN).etag)
        txn.commit(handle)
        txn = store.transaction()
        txn.put(manifest(CAST, derives(BASE, MODERN_MARA)), store.read_source(CAST).etag)
        txn.commit(handle)
        cyclic = api.call('chats.workspace', {'filter':'character', 'source':str(BASE), 'match':'derived'})
        assert cyclic['total'] == 1
        assert any(item['ref'] == str(MODERN_MARA) for item in cyclic['diagnostics'])
        txn = store.transaction()
        txn.put(manifest(CAST), store.read_source(CAST).etag)
        txn.commit(handle)
        store.root.joinpath('sources', 'catalog', 'elderfen-cast', '_source', 'records', 'character', 'mara', 'r1.json').unlink()
        missing = api.call('chats.workspace', {'filter':'character', 'source':str(BASE), 'match':'derived'})
        assert missing['total'] == 1  # immutable exact provenance is still repairable
        assert any(item['ref'] == str(MODERN_MARA) and item['code'] == 'missing_base' for item in missing['diagnostics'])


def test_workspace_node_contracts():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable')
    test = Path(__file__).with_name('js') / 'workspace.test.mjs'
    result = subprocess.run([node, '--test', str(test)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
