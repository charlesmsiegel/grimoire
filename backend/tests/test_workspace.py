"""Bounded, read-only workspace queries over synthetic canonical navigation."""
import os
import sys

import pytest
from textapi_fixtures import MARA_VELL, desktop_library, Device
from grimoire.chats import Launch
from grimoire.store import StoreError
from grimoire.textapi import ApiRefused
from grimoire.textapi.navcache import build_basis


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
    monkeypatch.setattr(importlib.import_module('grimoire.textapi.navcache'), 'compose_in', compose)  # the basis composes
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


def test_navigation_basis_is_reused_until_the_library_changes_in_or_outside_this_process(tmp_path):
    """065's large-library browsing: paging, search and the legacy listing share one validated basis, which a
    commit here or a rewrite from outside (even one that keeps the file's size and mtime) invalidates."""
    table, card_chat = desktop_library(tmp_path / 'desk')
    with Device(table) as api:
        cache = api.api.navigation_cache
        first = api('chats.workspace')
        assert cache.builds == 1
        loads = []
        real = api.api.store.load
        api.api.store.load = lambda: loads.append(1) or real()
        try:
            api('chats.workspace', q='mara')
            api('chats.workspace', filter='character', source=str(MARA_VELL), match='derived')
            api('chats', filter='recent', limit=50)
            assert api('chats.workspace', offset=0, snapshot=first['snapshot'])['chats'] == first['chats']
        finally:
            api.api.store.load = real
        assert not loads and cache.builds == 1, 'an unchanged library is not reparsed'
        fresh = build_basis(api.api, api.api.store.load())
        held = cache.basis(api.api)
        assert (held.fingerprint, held.index, held.rows) == (fresh.fingerprint, fresh.index, fresh.rows)

        # a commit in this process
        started = api.api.chats.start_chat(Launch('character', MARA_VELL)).commit().chat
        after = api('chats.workspace')
        assert cache.builds == 2 and after['total'] == first['total'] + 1
        assert str(started) in {row['chat'] for row in after['chats']}
        with pytest.raises(ApiRefused) as caught:
            api('chats.workspace', offset=0, snapshot=first['snapshot'])
        assert caught.value.reason == 'changed'

        # a hand edit from outside this process that keeps the file's size (and, where ctime records the last
        # change rather than creation, its modification time too)
        chat_file = next(path for path in (api.api.store.root / 'campaigns').rglob('*.json')
                         if b'The tide comes in' in path.read_bytes())
        info = chat_file.stat()
        chat_file.write_bytes(chat_file.read_bytes().replace(b'The tide comes in', b'The tide comes up'))
        if sys.platform != 'win32':  # Windows' st_ctime is the creation time: there mtime is the signal
            os.utime(chat_file, ns=(info.st_atime_ns, info.st_mtime_ns))
        assert chat_file.stat().st_size == info.st_size
        labels = {row['label'] for row in api('chats.workspace')['chats']}
        assert 'The tide comes up' in labels and 'The tide comes in' not in labels
        assert cache.builds == 3, 'ctime moves on any rewrite, so the basis is rebuilt'
        api('chats.workspace', q='tide')
        assert cache.builds == 3


def test_scope_choosers_list_campaigns_and_worlds_with_their_chats(tmp_path):
    table, card_chat = desktop_library(tmp_path / 'desk')
    with Device(table) as api:
        assert api('chats.workspace')['campaigns'] == []  # the choosers are filled only where they are drawn
        everything = api('chats.workspace', filter='campaign')
        campaigns = {item['campaign']: item for item in everything['campaigns']}
        assert campaigns[card_chat.campaign_id]['chats'] == 1 and campaigns[card_chat.campaign_id]['world'] is None
        imported = next(item for item in campaigns.values() if item['world'] is not None)
        assert everything['worlds'] == [{'world': imported['world'], 'campaigns': 1, 'chats': imported['chats']}]
        assert everything['campaign_total'] == len(campaigns) and everything['world_total'] == 1
        narrowed = api('chats.workspace', filter='campaign', scope_q=imported['campaign'][:6].upper())
        assert [item['campaign'] for item in narrowed['campaigns']] == [imported['campaign']]
        world = api('chats.workspace', filter='world', world=imported['world'])
        assert world['total'] == imported['chats'] and all(row['scope']['world'] == imported['world'] for row in world['chats'])


def test_a_closed_store_refuses_even_with_a_held_basis(tmp_path):
    table, _chat = desktop_library(tmp_path / 'desk')
    with Device(table) as api:
        api('chats.workspace')
        assert api.api.navigation_cache.builds == 1
        api.api.store.close()
        for op in ('chats.workspace', 'chats'):
            with pytest.raises(ApiRefused):  # the op boundary turns the store's refusal into its typed refusal
                api(op)
        with pytest.raises(StoreError):
            api.api.listed_chats()
