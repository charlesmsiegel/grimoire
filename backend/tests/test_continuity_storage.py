"""Synthetic immutable continuity envelopes, native discovery and sync admission."""
import pytest

from grimoire.core.identity import ContinuityRef, ScopeRef
from grimoire.store import ImmutableRevision, InvalidEnvelope, LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.layout import address_parts, classify
from grimoire.sync import identify, sync, syncable
from store_helpers import OWNER, owner_handle
from sync_helpers import DESKTOP, PHONE, handles


def record(value="original", campaign="c", record_id="review"):
    return ScopeRecord(ContinuityRef(campaign, record_id), "synthetic.future/99", {"value": value}, owner=OWNER)


def dependency_record(value='dialogue', record_id='review'):
    from grimoire.continuity import scene_revision
    from grimoire.continuity.declarations import DEPENDENCY_SCHEMA
    from grimoire.scene import turn_to_payload
    rows = dependency_turns()
    payload = {'source': {'path': '/'.join(address_parts(rows[0].ref)),
                          'revision': scene_revision(turn_to_payload(rows[0]))},
               'dependent': {'path': '/'.join(address_parts(rows[1].ref)),
                             'revision': scene_revision(turn_to_payload(rows[1]))},
               'reason': value, 'certainty': 'known'}
    return ScopeRecord(ContinuityRef('c', record_id), DEPENDENCY_SCHEMA, payload, owner=OWNER)


def dependency_turns():
    from grimoire.scene import Post, TurnRecord
    from effects_fixtures import CHAT, TAM
    return tuple(TurnRecord(CHAT, n, 'post', Post(OWNER, TAM, 'action', text, (TAM,)))
                 for n, text in ((1, 'The key is handed over.'), (2, 'Tam mentions the key.')))


def seed_dependency_endpoints(store, handle=None):
    from grimoire.scene import TURN_SCHEMA, turn_to_payload
    from grimoire.chats import Launch, PlayScope, SCOPE_SCHEMA, scope_to_payload
    from grimoire.chatstore import ChatLibrary
    from chat_fixtures import Clock, Ids
    from effects_fixtures import TAM
    from graph_fixtures import instance_record
    key = handle or owner_handle()
    tx = store.transaction()
    tx.put(ScopeRecord(ScopeRef('c'), SCOPE_SCHEMA,
                       scope_to_payload(PlayScope(ScopeRef('c'), '2026-01-01T00:00:00Z')), owner=OWNER), None)
    tx.put(instance_record(TAM), None)
    tx.commit(key)
    ChatLibrary(store, key, clock=Clock(), new_id=Ids(), owner=OWNER).start_chat(
        Launch('premise', premise='A synthetic key transfer.'), scope=ScopeRef('c')).commit()
    tx = store.transaction()
    for turn in dependency_turns():
        tx.put(ScopeRecord(turn.ref, TURN_SCHEMA, turn_to_payload(turn), owner=OWNER), None)
    tx.commit(key)


def put(store, row, expected=None):
    tx = store.transaction()
    tx.put(row, expected)
    tx.commit(owner_handle())


def test_identity_and_create_read_reopen(tmp_path):
    row = record()
    ref = row.address
    assert str(ref) == "campaign:c#continuity/review"
    assert ContinuityRef.parse(str(ref)) == ref
    assert ref.scope == ScopeRef("c")
    assert address_parts(ref) == ("campaigns", "c", "continuity", "review.json")
    assert classify(address_parts(ref)) == ("continuity", ref)
    with LibraryStore.create(tmp_path / "lib") as store:
        put(store, row)
        assert store.read(ref).record == row
        assert store.scan().files.keys() >= {"campaigns/c/continuity/review.json"}
        assert store.load().problems == ()
    with LibraryStore.open(tmp_path / "lib") as store:
        found = store.campaign_continuity("c")
        assert found.problems == () and found.records[ref].record == row
        assert store.campaign_continuity("other").records == {}
        original = store.read(ref).etag
        with pytest.raises(ImmutableRevision):
            put(store, record("replacement"), original)
        with pytest.raises(StaleWrite):
            put(store, record("replacement"))
        tx = store.transaction()
        data = store.scan().files["campaigns/c/continuity/review.json"]
        with pytest.raises(ImmutableRevision):
            tx.put_exact("campaigns/c/continuity/review.json", data, original)
        with pytest.raises(ImmutableRevision):
            tx.put_exact("campaigns/c/continuity/review.json", data, None, replace_unreferenced=True)
        with pytest.raises(InvalidEnvelope):
            tx.delete_exact("campaigns/c/continuity/review.json", original)
        assert store.read(ref).etag == original


@pytest.mark.parametrize("text", ["campaign:c#continuity/../bad", "campaign:C#continuity/a", "campaign:c#continuity/", 4])
def test_invalid_identity(text):
    with pytest.raises(ValueError):
        ContinuityRef.parse(text)


def test_scan_contains_malformed_and_reports_links_lookalikes(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        put(store, record())
        folder = store.root / "campaigns/c/continuity"
        (folder / "broken.json").write_text("{bad")
        (folder / "stray.txt").write_text("stray")
        (folder / "linked.json").symlink_to(folder / "review.json")
        (folder.parent / "Continuity").mkdir()
        (folder.parent / "Continuity/hidden.json").write_text("{}")
        found = store.campaign_continuity("c")
        assert set(found.records) == {record().address}
        assert {p.path.rsplit("/", 1)[-1] for p in found.problems} >= {"broken.json", "stray.txt", "linked.json", "Continuity"}
        assert "campaigns/c/continuity/broken.json" in store.scan().blocked


def test_sync_copies_and_same_id_collision_preserves_both(tmp_path):
    with LibraryStore.create(tmp_path / DESKTOP) as left, LibraryStore.create(tmp_path / PHONE) as right:
        identify(left, DESKTOP)
        identify(right, PHONE)
        seed_dependency_endpoints(left)
        put(left, dependency_record())
        path = "campaigns/c/continuity/review.json"
        assert syncable(path) == "continuity"
        assert sync(left, right, handles()).conflicts == ()
        assert left.scan().files[path] == right.scan().files[path]
        put(left, dependency_record("summary", record_id="collision"))
        put(right, dependency_record("asset", record_id="collision"))
        before = (left.read(dependency_record(record_id="collision").address), right.read(dependency_record(record_id="collision").address))
        report = sync(left, right, handles())
        assert report.conflicts
        assert before == (left.read(dependency_record(record_id="collision").address), right.read(dependency_record(record_id="collision").address))


def test_external_deletion_never_propagates(tmp_path):
    with LibraryStore.create(tmp_path / DESKTOP) as left, LibraryStore.create(tmp_path / PHONE) as right:
        identify(left, DESKTOP)
        identify(right, PHONE)
        seed_dependency_endpoints(left)
        put(left, dependency_record())
        assert sync(left, right, handles()).conflicts == ()
        path = "campaigns/c/continuity/review.json"
        original = right.scan().files[path]
        (left.root / path).unlink()
        sync(left, right, handles())
        assert right.scan().files[path] == original


def test_authenticated_peer_transfers_native_dependency(tmp_path):
    from grimoire.peersync import transfer
    from peersync_helpers import paired, serving

    left, right, _ = paired(tmp_path)
    try:
        seed_dependency_endpoints(left.store, left.handle)
        tx = left.store.transaction()
        tx.put(dependency_record(), None)
        tx.commit(left.handle)
        with serving(right) as server:
            report = transfer(left.store, left.authority, PHONE, server.address)
        assert report.status == "synced"
        assert right.store.read(dependency_record().address).record == dependency_record()
    finally:
        left.close()
        right.close()


def test_address_mismatch_is_reported_without_losing_other_records(tmp_path):
    from grimoire.store.errors import AddressMismatch
    with LibraryStore.create(tmp_path / "lib") as store:
        put(store, record())
        folder = store.root / "campaigns/c/continuity"
        (folder / "different.json").write_bytes((folder / "review.json").read_bytes())
        found = store.campaign_continuity("c")
        assert set(found.records) == {record().address}
        assert any(p.path.endswith("different.json") for p in found.problems)
        with pytest.raises(AddressMismatch):
            store.read(ContinuityRef("c", "different"))


@pytest.mark.parametrize('row', [record(), ScopeRecord(ContinuityRef('c', 'review'),
                         'grimoire.continuity-dependency/1', {'invalid': True}, owner=OWNER)])
def test_unknown_and_malformed_continuity_require_semantic_review(tmp_path, row):
    with LibraryStore.create(tmp_path / DESKTOP) as left, LibraryStore.create(tmp_path / PHONE) as right:
        identify(left, DESKTOP)
        identify(right, PHONE)
        put(left, row)
        before = left.read(row.address)
        report = sync(left, right, handles())
        assert report.conflicts and any(c.code == 'semantic_review' for c in report.conflicts)
        assert left.read(row.address) == before
        assert 'campaigns/c/continuity/review.json' not in right.scan().files


def test_authenticated_peer_refuses_unknown_continuity_schema(tmp_path):
    from grimoire.peersync import transfer
    from peersync_helpers import paired, serving
    left, right, _ = paired(tmp_path)
    try:
        tx = left.store.transaction()
        tx.put(record(), None)
        tx.commit(left.handle)
        with serving(right) as server:
            report = transfer(left.store, left.authority, PHONE, server.address)
        assert report.status in {'conflicts', 'incomplete'}
        assert 'campaigns/c/continuity/review.json' not in right.store.scan().files
    finally:
        left.close()
        right.close()
