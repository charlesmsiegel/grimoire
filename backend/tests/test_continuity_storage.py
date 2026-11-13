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
        put(left, record())
        path = "campaigns/c/continuity/review.json"
        assert syncable(path) == "continuity"
        assert sync(left, right, handles()).conflicts == ()
        assert left.scan().files[path] == right.scan().files[path]
        put(left, record("left", record_id="collision"))
        put(right, record("right", record_id="collision"))
        before = (left.read(record(record_id="collision").address), right.read(record(record_id="collision").address))
        report = sync(left, right, handles())
        assert report.conflicts
        assert before == (left.read(record(record_id="collision").address), right.read(record(record_id="collision").address))


def test_external_deletion_never_propagates(tmp_path):
    with LibraryStore.create(tmp_path / DESKTOP) as left, LibraryStore.create(tmp_path / PHONE) as right:
        identify(left, DESKTOP)
        identify(right, PHONE)
        put(left, record())
        assert sync(left, right, handles()).conflicts == ()
        path = "campaigns/c/continuity/review.json"
        original = right.scan().files[path]
        (left.root / path).unlink()
        sync(left, right, handles())
        assert right.scan().files[path] == original


def test_authenticated_peer_transfers_opaque_continuity(tmp_path):
    from grimoire.peersync import transfer
    from peersync_helpers import paired, serving

    left, right, _ = paired(tmp_path)
    try:
        tx = left.store.transaction()
        tx.put(record(), None)
        tx.commit(left.handle)
        with serving(right) as server:
            report = transfer(left.store, left.authority, PHONE, server.address)
        assert report.status == "synced"
        assert right.store.read(record().address).record == record()
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
