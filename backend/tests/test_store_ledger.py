"""Hash-chained ledgers in the store: layout, reading, appending, binding, repair (010b step 2)."""

from __future__ import annotations

import contextlib
import json

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.store import (
    LibraryStore, PendingRecovery, ProcessLocker, RecoveryFailed, StaleWrite, StoreError, WriteDenied,
)
from grimoire.store.errors import LedgerBroken, LedgerBusy
from grimoire.store.layout import classify, ledger_parts
from grimoire.store.ledger import render_batches
from grimoire.testing import (
    CrashScenario, Invariant, RootedFileOps, explore_crashes, idempotent_recovery, no_residue,
)
from grimoire.testing.fileops import snapshot

from store_helpers import WREN, drop_empty_dirs, instance, owner_handle, seed

LIB = "lib"


@pytest.fixture
def store(tmp_path):
    library = LibraryStore.create(tmp_path / "lib", locker=ProcessLocker())
    yield library
    library.close()


def _entries(count, start=0):
    return [{"kind": "note", "n": start + n} for n in range(count)]


# -- layout ------------------------------------------------------------------------

def test_ledger_paths_are_an_exact_inverse_and_nothing_else_is_a_ledger():
    assert ledger_parts("security", 1) == ("authority", "security", "0000000001.json")
    assert classify(ledger_parts("campaigns/c1", 42)) == ("ledger", ("campaigns/c1", 42))
    for parts in [("authority", "security", "0000000000.json"), ("authority", "security", "1.json"),
                  ("authority", "campaigns", "0000000001.json"), ("authority", "other", "0000000001.json"),
                  ("authority", "campaigns", "C1", "0000000001.json"),
                  ("authority", "campaigns", "c1", "x", "0000000001.json")]:
        assert classify(parts) is None, parts
    with pytest.raises(ValueError):
        ledger_parts("campaigns/../x", 1)


# -- writing and reading ----------------------------------------------------------------

def test_appends_chain_and_read_back_and_split_by_count(store):
    writer = store.ledger_writer("security")
    head = writer.append(_entries(3), None)
    head = writer.append(_entries(1_000, start=3), head)
    ledger = store.read_ledger("security")
    assert [batch.seq for batch in ledger.batches] == [1, 2, 3]  # 3, then 500 + 500
    assert [len(batch.entries) for batch in ledger.batches] == [3, 500, 500]
    assert [entry["n"] for entry in ledger.entries] == list(range(1_003))
    assert ledger.head == head and ledger.ignored == ()


def test_a_stale_head_is_refused_and_a_second_writer_is_busy(store, tmp_path):
    writer = store.ledger_writer("security")
    head = writer.append(_entries(1), None)
    with pytest.raises(LedgerBusy):
        store.ledger_writer("security")
    with pytest.raises(StaleWrite):
        writer.append(_entries(1), None)  # the caller replayed an older head
    extra = render_batches("security", head, _entries(1, 9))[0]  # appended by another program
    (tmp_path / "lib" / "/".join(extra[0])).write_bytes(extra[1])
    with pytest.raises(StaleWrite):
        writer.append(_entries(1), head)


def _write_batches(root, name, count):
    head = None
    for parts, data in render_batches(name, head, _entries(count)):
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root.joinpath(*ledger_parts(name, 1)).parent


def _three_batches(store):
    writer = store.ledger_writer("security")
    head = None
    for n in range(3):
        head = writer.append(_entries(1, n), head)
    return store.root / "authority" / "security"


@pytest.mark.parametrize("tamper, seq", [
    ("edit-interior", 3), ("delete-interior", 2), ("swap", 2), ("wrong-ledger", 1), ("bad-json", 1),
    ("bad-format", 3), ("directory", 2),
])
def test_interior_tampering_breaks_the_ledger_at_the_right_batch(store, tamper, seq):
    folder = _three_batches(store)
    files = sorted(folder.iterdir())
    if tamper == "edit-interior":
        document = json.loads(files[1].read_bytes())
        document["entries"][0]["n"] = 99
        files[1].write_text(json.dumps(document))
    elif tamper == "delete-interior":
        files[1].unlink()
    elif tamper == "swap":
        first, second = files[1].read_bytes(), files[2].read_bytes()
        files[1].write_bytes(second)
        files[2].write_bytes(first)
    elif tamper == "wrong-ledger":
        document = json.loads(files[0].read_bytes())
        document["ledger"] = "campaigns/c1"
        files[0].write_text(json.dumps(document))
    elif tamper == "bad-json":
        files[0].write_text("{")
    elif tamper == "bad-format":
        document = json.loads(files[2].read_bytes())
        document["format"] = "grimoire.ledger/9"
        files[2].write_text(json.dumps(document))
    else:
        files[1].unlink()
        files[1].mkdir()
    with pytest.raises(LedgerBroken) as broken:
        store.read_ledger("security")
    assert broken.value.seq == seq


def test_tail_changes_are_not_detectable_by_the_chain_alone(store):
    """Documented limit (pinned): truncating or appending at the tail keeps a valid chain."""
    folder = _three_batches(store)
    sorted(folder.iterdir())[-1].unlink()
    assert len(store.read_ledger("security").batches) == 2


def test_a_conflicted_copy_is_reported_and_never_replayed(store):
    folder = _three_batches(store)
    (folder / "0000000002 (conflicted copy).json").write_bytes((folder / "0000000002.json").read_bytes())
    ledger = store.read_ledger("security")
    assert len(ledger.batches) == 3
    assert ledger.ignored == ("authority/security/0000000002 (conflicted copy).json",)


# -- binding -----------------------------------------------------------------------------

class StubAuthority:
    def __init__(self, handles=()):
        self.handles = set(map(id, handles))
        self.scopes = 0

    def issued(self, handle):
        return id(handle) in self.handles

    @contextlib.contextmanager
    def consequential_scope(self):
        self.scopes += 1
        yield


def test_binding_is_permanent_and_takes_the_ledger_capability(store):
    handle = owner_handle()
    authority = StubAuthority([handle])
    capability = store.bind_authority(authority)
    with pytest.raises(StoreError):
        store.bind_authority(authority)
    with pytest.raises(StoreError):
        store.ledger_writer("security")  # no capability
    writer = store.ledger_writer("security", capability=capability)
    writer.append(_entries(1), None)
    seed(store, handle)
    assert authority.scopes == 1  # step 7 ran inside the authority's scope


def test_binding_is_refused_while_a_writer_is_outstanding(store):
    store.ledger_writer("security")
    with pytest.raises(StoreError):
        store.bind_authority(StubAuthority())


def test_a_bound_store_refuses_handles_its_authority_did_not_issue(store):
    store.bind_authority(StubAuthority())
    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    with pytest.raises(WriteDenied, match="not issued"):
        txn.commit(owner_handle())
    assert not (store.root / "campaigns").exists()


def test_an_unbound_store_with_a_security_ledger_refuses_record_commits(store):
    store.ledger_writer("security").append(_entries(1), None)
    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    with pytest.raises(StoreError, match="durable authority"):
        txn.commit(owner_handle())


def test_a_ledger_cannot_be_written_while_a_record_commit_is_staging(store):
    writer = store.ledger_writer("campaigns/c9")
    real = store._access.ops.make_temp
    attempts = []

    def make_temp(*args, **kwargs):  # during step 8 of the outer record commit
        if not attempts:
            with pytest.raises(StoreError, match="staging"):
                writer.append(_entries(1), None)
            attempts.append(True)
        return real(*args, **kwargs)
    store._access.ops.make_temp = make_temp
    seed(store)
    assert attempts and store._staging is False  # restored after the commit
    store._access.ops.make_temp = real
    writer.append(_entries(1), None)


# -- crash safety ----------------------------------------------------------------------------

def test_ledger_appends_survive_every_crash(tmp_path):
    locker = ProcessLocker()

    def setup(ops, root):
        with LibraryStore.create(root / LIB, ops=ops, locker=locker) as store:
            store.ledger_writer("security").append(_entries(1), None)

    def action(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            writer = store.ledger_writer("security")
            writer.append(_entries(501, start=1), writer.head)  # two batches in one journal

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    def whole(outcome):
        batches = sorted(key for key, value in outcome.recovered.items()
                         if key.startswith("lib/authority/security/") and value is not None)
        return None if len(batches) in (1, 3) else f"a partial append is visible: {batches}"

    sc = CrashScenario("ledger append", setup, action, recover,
                       (Invariant("whole_appends", whole), idempotent_recovery(),
                        no_residue("lib/.txn/*.new", "lib/.txn/journal.json")), normalize=drop_empty_dirs)
    report = explore_crashes(sc, tmp_path, errors=True)
    report.assert_ok()
    assert report.points_explored > 10


def test_repair_moves_a_broken_suffix_aside_and_survives_every_crash(tmp_path):
    locker = ProcessLocker()

    def setup(ops, root):
        with LibraryStore.create(root / LIB, ops=ops, locker=locker) as store:
            writer = store.ledger_writer("security")
            head = None
            for n in range(3):
                head = writer.append(_entries(1, n), head)
        ops.write_bytes(root / LIB / "authority" / "security" / "0000000002.json", b"{")  # broken

    def action(ops, root):
        LibraryStore.repair_ledger(root / LIB, "security", 2, ops=ops, locker=locker)

    def recover(ops, root):  # the human re-runs the repair, then opens the library
        LibraryStore.repair_ledger(root / LIB, "security", 2, ops=ops, locker=locker)
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    def prefix_and_one_folder(outcome):
        kept = sorted(key for key, value in outcome.recovered.items()
                      if key.startswith("lib/authority/security/") and value is not None)
        moved = sorted(key for key, value in outcome.recovered.items()
                       if key.startswith("lib/.conflicts/ledger-security-") and value is not None)
        if kept != ["lib/authority/security/0000000001.json"]:
            return f"kept {kept}"
        if len(moved) != 2 or len({key.split("/")[2] for key in moved}) != 1:
            return f"moved {moved}"
        return None

    sc = CrashScenario("repair", setup, action, recover,
                       (Invariant("prefix_and_one_folder", prefix_and_one_folder), idempotent_recovery(),
                        no_residue("lib/.txn/*.new", "lib/.txn/repair.json")), normalize=drop_empty_dirs)
    report = explore_crashes(sc, tmp_path, errors=True)
    report.assert_ok()
    assert report.points_explored > 5


def test_an_unfinished_repair_blocks_reads_and_recovery_until_finished(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    with LibraryStore.create(root / LIB, locker=locker) as store:
        writer = store.ledger_writer("security")
        head = writer.append(_entries(1), None)
        writer.append(_entries(1), head)
    from grimoire.testing import Fail, Target
    failing = RootedFileOps(root, plan=[Fail(Target("replace", target="lib/.conflicts/*"))], record=False)
    with pytest.raises(OSError):
        LibraryStore.repair_ledger(root / LIB, "security", 1, ops=failing, locker=locker)
    with pytest.raises(RecoveryFailed, match="repair"):
        LibraryStore.open(root / LIB, locker=locker)
    with LibraryStore.open(root / LIB, locker=locker, writable=False) as reader:
        with pytest.raises(PendingRecovery):
            reader.read_ledger("security")
    with pytest.raises(StoreError, match="same arguments"):
        LibraryStore.repair_ledger(root / LIB, "security", 2, locker=locker)
    moved = LibraryStore.repair_ledger(root / LIB, "security", 1, locker=locker)
    assert moved and all(path.startswith(".conflicts/ledger-security-1/") for path in moved)
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert store.read_ledger("security").batches == ()
    assert not list((root / LIB / ".txn").glob("repair*"))


def test_batches_split_by_size_and_an_oversized_entry_is_refused(store):
    big = {"kind": "note", "text": "x" * (2_500_000)}
    head = store.ledger_writer("security").append([big, big], None)
    assert head[0] == 2  # two batches: together they would exceed the batch size
    with pytest.raises(ValueError):
        render_batches("security", head, [{"text": "x" * (5 * 1024 * 1024)}])


def test_repair_refuses_while_a_commit_awaits_recovery(tmp_path):
    from grimoire.testing import Crash, Target
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    with LibraryStore.create(root / LIB, locker=locker) as store:
        head = store.ledger_writer("security").append(_entries(1), None)
    crashing = RootedFileOps(root, plan=[Crash(Target("replace", target="lib/.txn/journal.json"), "after")],
                             record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(root / LIB, ops=crashing, locker=locker) as store:
            store.ledger_writer("security").append(_entries(1, start=1), head)
    assert (root / LIB / ".txn" / "journal.json").exists()
    before = snapshot(root)
    with pytest.raises(PendingRecovery):
        LibraryStore.repair_ledger(root / LIB, "security", 1, locker=locker)
    assert snapshot(root) == before


def test_a_second_repair_never_reuses_a_conflicts_folder(tmp_path):
    locker = ProcessLocker()
    path = tmp_path / LIB
    moved = []
    for round_ in range(2):
        with LibraryStore.open(path, locker=locker) if round_ else LibraryStore.create(path, locker=locker) as store:
            store.ledger_writer("security").append(_entries(1, start=round_), None)
        moved.append(LibraryStore.repair_ledger(path, "security", 1, locker=locker))
    assert moved[0] == (".conflicts/ledger-security-1/0000000001.json",)
    assert moved[1] == (".conflicts/ledger-security-2/0000000001.json",)
    first = json.loads((path / moved[0][0]).read_bytes())
    assert first["entries"][0]["n"] == 0  # the first repair's batch was not overwritten


def test_reading_a_long_ledger_lists_its_folder_once(store):
    writer = store.ledger_writer("security")
    head = None
    for number in range(60):
        head = writer.append(_entries(1, start=number), head)
    calls = []
    real = store._access.ops.list_dir
    store._access.ops.list_dir = lambda path: calls.append(path) or real(path)
    try:
        assert len(store.read_ledger("security").batches) == 60
    finally:
        store._access.ops.list_dir = real
    assert len(calls) < 10  # per ancestor, not per batch


def test_explicit_recovery_waits_for_a_commit_in_another_thread(store):
    import threading
    import time
    writer = store.ledger_writer("security")
    results, workers = [], []
    real = store._access.ops.make_temp

    def make_temp(*args, **kwargs):  # staging, before the journal exists
        if not workers:
            worker = threading.Thread(target=lambda: results.append(store.recover()))
            worker.start()
            workers.append(worker)
            time.sleep(0.2)
            assert not results  # recovery would clean this commit's staging
        return real(*args, **kwargs)
    store._access.ops.make_temp = make_temp
    try:
        writer.append(_entries(1), None)
    finally:
        store._access.ops.make_temp = real
    workers[0].join(5)
    assert results and len(store.read_ledger("security").entries) == 1


def test_an_oversized_batch_is_refused_before_it_is_read(store):
    from grimoire.store.jsonfmt import MAX_FILE_BYTES
    writer = store.ledger_writer("security")
    writer.append(_entries(1), None)
    batch = store.root / "authority" / "security" / "0000000001.json"
    with batch.open("ab") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)
    reads = []
    real = store._access.ops.read_bytes
    store._access.ops.read_bytes = lambda path: reads.append(path) or real(path)
    try:
        with pytest.raises(LedgerBroken, match="larger than"):
            store.read_ledger("security")
    finally:
        store._access.ops.read_bytes = real
    assert reads == []


def test_a_linked_ledger_folder_is_refused_even_without_batches(store, tmp_path):
    from grimoire.store import LinkRefused
    from store_helpers import make_dir_link
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.json").write_text("{}")
    (store.root / "authority").mkdir()
    make_dir_link(store.root / "authority" / "security", outside)
    with pytest.raises(LinkRefused):
        store.read_ledger("security")
