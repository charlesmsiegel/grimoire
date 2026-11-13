"""Authenticated serving peers enforce semantic premises against live records."""
from types import SimpleNamespace

import pytest

from grimoire.peersync import transfer, resolve_conflict
from grimoire.peersync import server as server_module
from grimoire.peersync.replay import StepBuffer
from grimoire.store import etag
from effects_fixtures import CHAT
from ledger_fixtures import pay
from peersync_helpers import DESKTOP, PHONE, serving
from test_peersync_ledger import pair, _accept
from test_effect_continuity import write_turn
from test_sync_ledger import ledger_files

PATH = 'campaigns/c/turns/t-1.000001.json'


def scene(device, text='The key is here.'):
    return write_turn(device.store, SimpleNamespace(app=device.handle), text=text)


@pytest.mark.parametrize('initiator', [DESKTOP, PHONE])
def test_peer_reports_semantic_review_and_generic_resolve_refuses(pair, initiator):
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    _accept(desk, pay(chat=CHAT))
    scene(phone, 'The key never existed.')
    before = {n: ledger_files(d.store) for n, d in pair.items()}
    client = pair[initiator]
    remote = pair[PHONE if initiator == DESKTOP else DESKTOP]
    with serving(remote) as server:
        report = transfer(client.store, client.authority, remote.name, server.address)
        assert report.status == 'conflicts', report
        assert any(c.path == PATH and c.code == 'semantic_review' for c in report.conflicts)
        expected = {n: etag(d.store.read_exact(PATH)) for n, d in pair.items()}
        result = resolve_conflict(client.store, client.authority, remote.name, server.address, PATH,
                                  expected[initiator], expected)
        assert result.status == 'refused', result
    assert {n: ledger_files(d.store) for n, d in pair.items()} == before


def test_serving_post_snapshot_scene_edit_stops_incoming_event(pair, monkeypatch):
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    _accept(desk, pay(chat=CHAT))
    before = ledger_files(phone.store)
    changed = []
    class Busy(StepBuffer):
        def commit(self, store, handle, **options):
            if not changed and any(h.get('op') == 'put' and '/accepted/' in h.get('path', '') for h, _ in self.ops):
                changed.append(True)
                scene(phone, 'Serving owner edits after snapshot.')
            return super().commit(store, handle, **options)
    monkeypatch.setattr(server_module, 'StepBuffer', Busy)
    with serving(phone) as server:
        report = transfer(desk.store, desk.authority, PHONE, server.address)
    assert changed and report.status == 'conflicts', report
    assert ledger_files(phone.store) == before
    assert phone.store.read_exact(PATH) != desk.store.read_exact(PATH)


def test_manual_client_cannot_omit_semantic_listing(pair):
    from grimoire.peersync.errors import PeerRefused
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    data = desk.store.read_exact(PATH)
    step = StepBuffer(DESKTOP)
    step.add({'op': 'put', 'path': PATH, 'expected': None}, data)
    with pytest.raises(PeerRefused, match='semantic'):
        step.commit(phone.store, phone.handle)
    assert phone.store.read_exact(PATH) is None


def test_manual_generic_resolve_of_semantic_scene_refused_on_serving_side(pair):
    from grimoire.peersync.resolving import ResolveRequest
    from grimoire.peersync.errors import PeerRefused
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    _accept(desk, pay(chat=CHAT))
    current = etag(desk.store.read_exact(PATH))
    request = ResolveRequest(PATH, current, current, 'sha256:' + '1' * 64, DESKTOP, PHONE)
    with pytest.raises(PeerRefused, match='semantic'):
        request.admit(desk.store)


def test_unrelated_chat_generic_resolution_remains_available_with_accepted_event(pair):
    from ledger_fixtures import OTHER
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    write_turn(desk.store, SimpleNamespace(app=desk.handle), chat=OTHER)
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    _accept(desk, pay(chat=CHAT))
    unrelated = 'campaigns/c/turns/t-2.000001.json'
    write_turn(desk.store, SimpleNamespace(app=desk.handle), chat=OTHER, text='Desk variant.')
    write_turn(phone.store, SimpleNamespace(app=phone.handle), chat=OTHER, text='Phone variant.')
    with serving(desk) as server:
        report = transfer(phone.store, phone.authority, DESKTOP, server.address)
        assert report.status == 'conflicts'
        assert all(c.code != 'semantic_review' for c in report.conflicts)
    expected = {n: etag(d.store.read_exact(unrelated)) for n, d in pair.items()}
    with serving(desk) as server:
        result = resolve_conflict(phone.store, phone.authority, DESKTOP, server.address, unrelated,
                                  expected[PHONE], expected)
    assert result.status == 'resolved', result
