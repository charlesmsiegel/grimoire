"""A staged file that vanishes after the commit point never half-publishes a head move.

Another program (a cleanup tool, a person deleting "stray .new files") removes one staged file
of a committed journal. While nothing is published the whole journal is abandoned; once some
entries are, the rest finish, except a manifest whose revision record will never be written:
its staged bytes are kept under ``.conflicts`` and it is reported incomplete.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.store import CommitIncomplete, LibraryStore, ProcessLocker, RecordEnvelope
from grimoire.testing import Crash, Interleave, RootedFileOps, Target

from store_helpers import HARBOR, WREN, harbor_manifest, owner_handle, wren
from test_store_recovery import JOURNAL, LIB, _setup, _template as _template_files

R2 = "sources/world/harbor/_source/records/character/wren/r2.json"
MANIFEST = "sources/world/harbor/_source/manifest.json"
OTHER = InstanceRef("other", "character", "late")


def _root(tmp_path, locker):
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    return root


def _head_move(store, *, other=False):
    """Stage wren@r2 and the manifest moving its head (optionally after an unrelated instance)."""
    source = store.read_source(HARBOR)
    txn = store.transaction()
    if other:
        txn.put(RecordEnvelope(OTHER, {"n": 1}), None)
    txn.put(wren("r2", "a captain"), None)
    revision = next(data for key, data in txn.preview().items() if key.endswith("r2.json"))
    txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
    return txn, revision


def _remove_staged(root, data):
    for path in (root / LIB / ".txn").iterdir():
        if path.name.endswith(".new") and path.read_bytes() == data:
            path.unlink()
            return
    raise AssertionError("the staged revision was not found")


def _assert_still_r1(root, locker):
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert (root / LIB / MANIFEST).read_bytes() == dict(_template_files())[f"{LIB}/{MANIFEST}"]
        assert "r2" not in dict(store.read_source(HARBOR).manifest.heads).values()
        view = store.load()
        assert view.problems == ()
        assert view.index.resolve(WREN).envelope.payload["bio"] == "a sailor"
        return store.last_recovery, store.conflicts()


def test_recovery_abandons_a_journal_whose_revision_vanished_while_down(tmp_path):
    locker = ProcessLocker()
    root = _root(tmp_path, locker)
    with pytest.raises(BaseException):
        with LibraryStore.open(root / LIB, ops=RootedFileOps(root, plan=[Crash(JOURNAL, "after")], record=False),
                               locker=locker) as store:
            txn, revision = _head_move(store)
            txn.commit(owner_handle())
    _remove_staged(root, revision)  # "the stray .new file" of the revision
    with LibraryStore.open(root / LIB, locker=locker) as store:
        report = store.last_recovery
    assert report.aborted and R2 in report.incomplete and report.published == ()
    _, conflicts = _assert_still_r1(root, locker)
    assert any(path.startswith(".conflicts/") for path in conflicts)  # the manifest's bytes are kept


def test_a_live_commit_whose_revision_vanished_publishes_nothing(tmp_path):
    locker = ProcessLocker()
    root = _root(tmp_path, locker)
    state = {}
    ops = RootedFileOps(root, plan=[Interleave(JOURNAL, lambda o, r: _remove_staged(r, state["revision"]))],
                        record=False)
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        txn, state["revision"] = _head_move(store)
        with pytest.raises(CommitIncomplete, match="not written") as raised:
            txn.commit(owner_handle())
    assert R2 in str(raised.value)
    _assert_still_r1(root, locker)


def test_a_partly_published_journal_never_publishes_a_head_without_its_revision(tmp_path):
    locker = ProcessLocker()
    root = _root(tmp_path, locker)
    other = f"{LIB}/campaigns/other/instances/character/late.json"
    crash = Crash(Target("replace", target=other), "after")  # the unrelated entry is published, then the crash
    with pytest.raises(BaseException):
        with LibraryStore.open(root / LIB, ops=RootedFileOps(root, plan=[crash], record=False),
                               locker=locker) as store:
            txn, revision = _head_move(store, other=True)
            txn.commit(owner_handle())
    _remove_staged(root, revision)
    with LibraryStore.open(root / LIB, locker=locker) as store:
        report = store.last_recovery
        assert store.read(OTHER).envelope.payload == {"n": 1}  # what was published stays published
    assert not report.aborted
    assert set(report.incomplete) == {R2, MANIFEST}
    assert len(report.preserved) == 1 and report.preserved[0].startswith(".conflicts/")
    _, conflicts = _assert_still_r1(root, locker)
    assert report.preserved[0] in conflicts


def test_a_manifest_put_first_is_still_published_last(tmp_path):
    """Sync copies in path order, so a manifest is usually put before its revision. The journal
    still publishes it last: a crash after an unrelated entry plus a vanished revision never
    leaves a head on a revision that is not there."""
    locker = ProcessLocker()
    root = _root(tmp_path, locker)
    other = f"{LIB}/campaigns/other/instances/character/late.json"
    crash = Crash(Target("replace", target=other), "after")
    with pytest.raises(BaseException):
        with LibraryStore.open(root / LIB, ops=RootedFileOps(root, plan=[crash], record=False),
                               locker=locker) as store:
            source = store.read_source(HARBOR)
            txn = store.transaction()
            txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)  # the manifest first
            txn.put(RecordEnvelope(OTHER, {"n": 1}), None)
            txn.put(wren("r2", "a captain"), None)
            revision = next(data for key, data in txn.preview().items() if key.endswith("r2.json"))
            txn.commit(owner_handle())
    _remove_staged(root, revision)
    with LibraryStore.open(root / LIB, locker=locker) as store:
        report = store.last_recovery
    assert set(report.incomplete) == {R2, MANIFEST}
    _assert_still_r1(root, locker)


def test_a_crash_after_a_manifest_put_first_lands_finds_its_revision_in_place(tmp_path):
    locker = ProcessLocker()
    root = _root(tmp_path, locker)
    crash = Crash(Target("replace", target=f"{LIB}/{MANIFEST}"), "after")
    with pytest.raises(BaseException):
        with LibraryStore.open(root / LIB, ops=RootedFileOps(root, plan=[crash], record=False),
                               locker=locker) as store:
            source = store.read_source(HARBOR)
            txn = store.transaction()
            txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
            txn.put(wren("r2", "a captain"), None)
            txn.commit(owner_handle())
    assert not any(path.name.endswith(".new") for path in (root / LIB / ".txn").iterdir())  # all landed
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert dict(store.read_source(HARBOR).manifest.heads)["character/wren"] == "r2"
        view = store.load()
        assert view.problems == ()
        assert view.index.resolve(WREN).envelope.payload["bio"] == "a captain"
