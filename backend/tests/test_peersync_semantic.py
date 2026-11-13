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


def peer_art(device):
    from grimoire.core.identity import ImageRef, PlacementRef, OwnerRef
    from grimoire.images import Placement
    from effects_fixtures import TAM
    from image_fixtures import PNG
    image = ImageRef('campaign:c', 'semantic-art')
    placement = PlacementRef(TAM, 'portrait')
    scope_art = PlacementRef(OwnerRef('campaign:c'), 'portrait')
    batch = device.images.change()
    batch.add_image('campaign:c', image.image_id, PNG)
    for ref in (placement, scope_art):
        batch.place(ref.subject, ref.slot, Placement('set', 'portrait', image), None)
    batch.commit()
    return image, placement, scope_art


def peer_declare_art(device, ref):
    from grimoire.continuitystore import ContinuityLibrary
    from grimoire.store.layout import address_parts
    review = ContinuityLibrary(device.store, device.handle)
    pins = review.snapshot('c').pins
    review.declare_dependency('art', pins[PATH], pins['/'.join(address_parts(ref))], 'asset')


def peer_edit_placement(device, ref, note='offline edit'):
    from dataclasses import replace
    from grimoire.core.identity import ImageRef
    if isinstance(ref, ImageRef):
        import json
        from grimoire.store.layout import address_parts
        file = device.store.root.joinpath(*address_parts(ref))
        value = json.loads(file.read_text())
        value['payload']['provenance'] = {'synthetic': note}
        file.write_text(json.dumps(value))
        return
    value, revision = device.images.read_placement(ref)
    device.images.place(ref.subject, ref.slot, replace(value, provenance={'synthetic': note}), revision).commit()


@pytest.mark.parametrize('target', ['image', 'placement', 'scope_art'])
@pytest.mark.parametrize('initiator', [DESKTOP, PHONE])
def test_peer_holds_declared_asset_edit(pair, target, initiator):
    from grimoire.store.layout import address_parts
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    refs = peer_art(desk)
    ref = refs[['image', 'placement', 'scope_art'].index(target)]
    peer_declare_art(desk, ref)
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    peer_edit_placement(desk, ref)
    path = '/'.join(address_parts(ref))
    before = {name: d.store.read_exact(path) for name, d in pair.items()}
    client = pair[initiator]
    remote = pair[PHONE if initiator == DESKTOP else DESKTOP]
    with serving(remote) as server:
        report = transfer(client.store, client.authority, remote.name, server.address)
        assert any(c.path == path and c.code == 'semantic_review' for c in report.conflicts), report
        expected = {name: etag(data) for name, data in before.items()}
        resolution = resolve_conflict(client.store, client.authority, remote.name, server.address,
                                      path, expected[initiator], expected)
        assert resolution.status == 'refused'
    assert {name: d.store.read_exact(path) for name, d in pair.items()} == before


@pytest.mark.parametrize('target', ['placement', 'scope_art'])
@pytest.mark.parametrize('initiator', [DESKTOP, PHONE])
def test_serving_late_declaration_stops_incoming_asset_copy(pair, monkeypatch, target, initiator):
    from grimoire.store.layout import address_parts
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    refs = peer_art(desk)
    ref = refs[1 if target == 'placement' else 2]
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    client = pair[initiator]
    remote = pair[PHONE if initiator == DESKTOP else DESKTOP]
    peer_edit_placement(client, ref)
    path = '/'.join(address_parts(ref))
    before = remote.store.read_exact(path)
    changed = []
    class Busy(StepBuffer):
        def commit(self, store, handle, **options):
            if not changed and any(h.get('op') == 'put' and h.get('path') == path for h, _ in self.ops):
                changed.append(True)
                peer_declare_art(remote, ref)
            return super().commit(store, handle, **options)
    monkeypatch.setattr(server_module, 'StepBuffer', Busy)
    with serving(remote) as server:
        report = transfer(client.store, client.authority, remote.name, server.address)
    assert changed and report.status == 'conflicts', report
    assert remote.store.read_exact(path) == before


@pytest.mark.parametrize('target', ['image', 'placement', 'scope_art'])
def test_manual_peer_cannot_omit_asset_semantic_listing(pair, target):
    from grimoire.peersync.errors import PeerRefused
    from grimoire.store.layout import address_parts
    desk, phone = pair[DESKTOP], pair[PHONE]
    refs = peer_art(desk)
    ref = refs[['image', 'placement', 'scope_art'].index(target)]
    path = '/'.join(address_parts(ref))
    step = StepBuffer(DESKTOP)
    step.add({'op': 'put', 'path': path, 'expected': None}, desk.store.read_exact(path))
    with pytest.raises(PeerRefused, match='semantic'):
        step.commit(phone.store, phone.handle)
    assert phone.store.read_exact(path) is None


def test_unrelated_asset_generic_resolution_remains_available_with_accepted_event(pair):
    from grimoire.store.layout import address_parts
    desk, phone = pair[DESKTOP], pair[PHONE]
    scene(desk)
    _, placement, _ = peer_art(desk)
    with serving(phone) as server:
        assert transfer(desk.store, desk.authority, PHONE, server.address).status == 'synced'
    _accept(desk, pay(chat=CHAT))
    peer_edit_placement(desk, placement, 'desktop variant')
    peer_edit_placement(phone, placement, 'phone variant')
    path = '/'.join(address_parts(placement))
    with serving(desk) as server:
        report = transfer(phone.store, phone.authority, DESKTOP, server.address)
        assert report.status == 'conflicts'
        assert all(c.code != 'semantic_review' for c in report.conflicts)
    expected = {name: etag(d.store.read_exact(path)) for name, d in pair.items()}
    with serving(desk) as server:
        result = resolve_conflict(phone.store, phone.authority, DESKTOP, server.address,
                                  path, expected[PHONE], expected)
    assert result.status == 'resolved', result
