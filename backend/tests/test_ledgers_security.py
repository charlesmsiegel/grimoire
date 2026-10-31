"""Durable authority: restart, durability before effect, poisoning, tampering (010b S1–S9)."""

from __future__ import annotations

import functools
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from grimoire.ledgers import AuthorityUnavailable, DurableAuthority, LedgerBroken, LedgerInvalid
from grimoire.security import Authorizer, Grant, Permission, Reason, ResourcePath
from grimoire.store import (
    CommitIncomplete, LibraryStore, ProcessLocker, RecordEnvelope, StoreError, WriteDenied,
)
from grimoire.store.ledger import render_batches
from grimoire.testing import (
    CrashScenario, Fail, Invariant, RootedFileOps, Target, explore_crashes, idempotent_recovery, no_residue,
)
from grimoire.testing.fileops import snapshot
from grimoire.core.identity import InstanceRef

from ledger_helpers import (
    ADDRESS, IMPORTER, OWNER, RECORD, approve, fixed_clock, grant_importer, open_authority, record_bytes,
)
from store_helpers import drop_empty_dirs

LIB = "lib"


@pytest.fixture
def library(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / LIB, locker=locker)
    authority = open_authority(store)
    yield store, authority, locker
    authority.close()
    store.close()


def _reopen(store, authority, locker):
    authority.close()
    store.close()
    store = LibraryStore.open(store.root, locker=locker)
    return store, open_authority(store)


def _decisions_on_disk(root, tree=None):
    files = (tree or {}).items() if tree is not None else (
        (str(path.relative_to(root.parent)), path.read_bytes()) for path in sorted((root / "authority" / "security").glob("*.json")))
    decisions = []
    for key, data in sorted(files):
        if not key.startswith("lib/authority/security/") or data is None:
            continue
        for entry in json.loads(data)["entries"]:
            if entry["kind"] == "decision":
                decisions.append(entry["decision"])
    return decisions


# -- S1 restart -------------------------------------------------------------------------------

def test_security_state_survives_a_restart(library):
    store, authority, locker = library
    grant_importer(authority)
    grant_importer(authority, gid="g2")
    approve(authority, b"pending", aid="a2")
    data = record_bytes(store)
    approve(authority, data)
    txn = store.transaction()
    txn.put(RECORD, None)
    txn.commit(authority.handle_for(IMPORTER), {ADDRESS: "a1"})
    authority.revoke_grant("g2")
    authority.handle_for(IMPORTER).authorize(Permission.INSPECT, "campaigns/c1")
    before = (authority.active_grants(), authority.pending_approvals(), authority.audit())
    store, authority = _reopen(store, authority, locker)
    assert (authority.active_grants(), authority.pending_approvals(), authority.audit()) == before
    assert [a.approval_id for a in authority.pending_approvals()] == ["a2"]  # pinned to g1, still pending
    replay = authority.handle_for(IMPORTER).authorize(Permission.APPLY, ADDRESS, data, "a1")
    assert not replay.allowed  # a consumed approval stays consumed
    authority.close()
    store.close()


def test_an_allow_is_on_disk_before_it_is_returned(library):
    store, authority, locker = library
    grant_importer(authority)
    approve(authority, b"bytes")
    decision = authority.handle_for(IMPORTER).authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")
    assert decision.allowed
    # the process dies here: nothing is closed or flushed
    decisions = _decisions_on_disk(store.root)
    assert decisions[-1]["approval_id"] == "a1" and decisions[-1]["allowed"] is True
    reopened = LibraryStore.open(store.root, locker=ProcessLocker(), writable=False)
    assert reopened.read_ledger("security").head is not None
    reopened.close()


def test_opening_and_closing_writes_nothing(library):
    store, authority, locker = library
    grant_importer(authority)
    store, authority = _reopen(store, authority, locker)
    files = sorted((store.root / "authority" / "security").iterdir())
    store, authority = _reopen(store, authority, locker)
    authority.close()
    store.close()
    assert sorted((store.root / "authority" / "security").iterdir()) == files


def test_a_save_writes_its_decisions_in_exactly_one_batch(library):
    store, authority, locker = library
    grant_importer(authority)
    txn = store.transaction()
    for name in ("one", "two", "three"):
        txn.put(RecordEnvelope(InstanceRef("c1", "character", name), {}), None)
    for number, (address, data) in enumerate(txn.preview().items()):
        approve(authority, data, aid=f"a{number}", address=address)
    before = len(store.read_ledger("security").batches)
    txn.commit(authority.handle_for(IMPORTER), {address: f"a{n}" for n, address in enumerate(txn.preview())})
    assert len(store.read_ledger("security").batches) == before + 1


# -- S2 durable before effect, crash-explored ---------------------------------------------------

@functools.cache
def _s2_template():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        store = LibraryStore.create(root / LIB, locker=ProcessLocker())
        authority = open_authority(store)
        grant_importer(authority)
        approve(authority, record_bytes(store))
        authority.close()
        store.close()
        return tuple(snapshot(root).items())


def _s2_scenario():
    locker = ProcessLocker()
    seen: list = []

    def setup(ops, root):
        for key, data in _s2_template():
            ops.make_dirs(root / key) if data is None else ops.write_bytes(root / key, data)

    def action(ops, root):
        seen.clear()
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
            try:
                txn = store.transaction()
                txn.put(RECORD, None)
                txn.commit(authority.handle_for(IMPORTER), {ADDRESS: "a1"})
                authority.close()
            finally:
                seen.extend(authority.audit())
                seen.extend(entry["decision"] for entry in authority._pending if entry["kind"] == "decision")

    def recover(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            DurableAuthority.open(store, [OWNER], clock=fixed_clock()).close()

    def durable_before_effect(outcome):
        decisions = _decisions_on_disk(None, outcome.recovered)
        record = outcome.recovered.get("lib/" + ADDRESS)
        if record is not None and outcome.before.get("lib/" + ADDRESS) is None:
            if not any(d["allowed"] and d["resource"] == ADDRESS and d["approval_id"] == "a1" for d in decisions):
                return "a record was published without its allow decision on disk"
        known = {}
        for item in seen:
            sequence = item.sequence if hasattr(item, "sequence") else item["sequence"]
            known[sequence] = item
        for decision in decisions:
            if decision["sequence"] not in known:
                return f"decision {decision['sequence']} on disk was never made"
        return None

    return CrashScenario("durable authority save", setup, action, recover,
                         (Invariant("durable_before_effect", durable_before_effect), idempotent_recovery(),
                          no_residue("lib/.txn/*.new", "lib/.txn/journal.json")), normalize=drop_empty_dirs)


@pytest.mark.parametrize("mode", ["errors", "recovery-crashes"])
def test_a_published_record_always_has_its_decision_on_disk(tmp_path, mode):
    report = explore_crashes(_s2_scenario(), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 10
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])


# -- S3 / S4 poisoning --------------------------------------------------------------------------

def _faulty(store_root, locker, fault):
    root = store_root.parent
    ops = RootedFileOps(root, plan=[fault], record=False)
    store = LibraryStore.open(store_root, ops=ops, locker=locker)
    return store, open_authority(store)


def test_a_failed_lifecycle_write_poisons_and_older_handles_are_denied(library):
    store, authority, locker = library
    grant_importer(authority)
    authority.close()
    store.close()
    store, authority = _faulty(store.root, locker, Fail(Target("write_bytes", path="lib/.txn/rec*")))
    handle = authority.handle_for(IMPORTER)  # issued before the failure
    with pytest.raises(AuthorityUnavailable):
        authority.add_grant(Grant("g2", IMPORTER, frozenset({Permission.APPLY}), ResourcePath.parse("worlds"), OWNER))
    assert handle.authorize(Permission.INSPECT, "campaigns/c1").reason is Reason.INTERNAL_ERROR
    with pytest.raises(AuthorityUnavailable):
        authority.flush()
    authority.close()
    store.close()
    store = LibraryStore.open(store.root, locker=locker)
    authority = open_authority(store)
    assert [g.grant_id for g in authority.active_grants()] == ["g1"]  # disk is the truth
    authority.close()
    store.close()


@pytest.mark.parametrize("where", ["before-commit-point", "after-commit-point"])
def test_an_execute_allow_is_never_returned_unless_durable(library, where):
    store, authority, locker = library
    grant_importer(authority, permissions=(Permission.EXECUTE,), scope="providers", gid="gp")
    approve(authority, b"call", aid="ap", address="providers/model", permission=Permission.EXECUTE)
    authority.close()
    store.close()
    fault = (Fail(Target("write_bytes", path="lib/.txn/rec*")) if where == "before-commit-point"
             else Fail(Target("replace", target="lib/authority/security/*")))
    store, authority = _faulty(store.root, locker, fault)
    decision = authority.handle_for(IMPORTER).authorize(Permission.EXECUTE, "providers/model", b"call", "ap")
    assert not decision.allowed and decision.reason is Reason.INTERNAL_ERROR
    assert [a.approval_id for a in authority.pending_approvals()] == ["ap"]
    authority.close()  # poisoned: writes nothing
    store.close()
    store = LibraryStore.open(store.root, locker=locker)  # recovery finishes an ambiguous flush
    authority = open_authority(store)
    pending = [a.approval_id for a in authority.pending_approvals()]
    if where == "before-commit-point":
        assert pending == ["ap"]
    else:  # documented: a decision on disk was made, though it never took effect
        assert pending == []
    authority.close()
    store.close()


def test_a_failing_clock_poisons_without_an_audit_gap(library):
    store, authority, locker = library
    grant_importer(authority)
    calls = []

    def clock():
        calls.append(1)
        raise RuntimeError("clock broke")
    authority._clock = clock
    decision = authority.handle_for(IMPORTER).authorize(Permission.INSPECT, "campaigns/c1")
    assert decision.reason is Reason.INTERNAL_ERROR and authority.audit() == () and authority.poisoned


def test_a_save_through_a_poisoned_authority_writes_nothing(library):
    store, authority, locker = library
    grant_importer(authority)
    approve(authority, record_bytes(store))
    handle = authority.handle_for(IMPORTER)  # issued before the authority failed
    authority._poison(RuntimeError("earlier failure"))
    txn = store.transaction()
    txn.put(RECORD, None)
    with pytest.raises(WriteDenied, match="unavailable"):
        txn.commit(handle, {ADDRESS: "a1"})
    assert not (store.root / ADDRESS).exists()


# -- S5 tampering, owners, repair ---------------------------------------------------------------

def test_interior_tampering_is_refused_and_repair_recovers(library):
    store, authority, locker = library
    grant_importer(authority)
    grant_importer(authority, gid="g2")
    authority.close()
    store.close()
    second = store.root / "authority" / "security" / "0000000002.json"
    document = json.loads(second.read_bytes())
    document["entries"][0]["grant"]["scope"] = "worlds"
    second.write_text(json.dumps(document))
    store = LibraryStore.open(store.root, locker=locker)
    with pytest.raises(LedgerBroken):
        open_authority(store)
    txn = store.transaction()
    txn.put(RECORD, None)
    with pytest.raises(StoreError, match="durable authority"):  # a failed open leaves the store refusing
        txn.commit(Authorizer([OWNER]).handle_for(OWNER, trusted=True))
    store.close()
    LibraryStore.repair_ledger(store.root, "security", 2, locker=locker)
    store = LibraryStore.open(store.root, locker=locker)
    authority = open_authority(store)
    assert authority.active_grants() == ()  # the moved history is gone, as the repair warns
    authority.close()
    store.close()


def test_a_forged_grant_in_a_valid_tail_is_invalid_and_owners_are_fixed(library):
    store, authority, locker = library
    authority.close()
    store.close()
    ledger_root = store.root / "authority" / "security"
    head_data = (ledger_root / "0000000001.json").read_bytes()
    from grimoire.store import etag
    forged = {"schema": 1, "kind": "grant_added", "recorded_at": "x",
              "grant": {"grant_id": "evil", "principal": IMPORTER, "permissions": ["apply"],
                        "scope": "campaigns", "granted_by": "human:intruder"}}
    parts, data = render_batches("security", (1, etag(head_data)), [forged])[0]
    (store.root / "/".join(parts)).write_bytes(data)
    store = LibraryStore.open(store.root, locker=locker)
    with pytest.raises(LedgerInvalid):
        open_authority(store)
    with pytest.raises(LedgerInvalid, match="owner set"):
        DurableAuthority.open(store, [OWNER, "human:other"])
    store.close()


# -- S6 / S8 / S9 ---------------------------------------------------------------------------------

def test_foreign_handles_cannot_save_through_a_bound_store(library):
    store, authority, locker = library
    txn = store.transaction()
    txn.put(RECORD, None)
    with pytest.raises(WriteDenied, match="not issued"):
        txn.commit(Authorizer([OWNER]).handle_for(OWNER, trusted=True))
    with pytest.raises(StoreError):
        store.ledger_writer("campaigns/c1")  # the authority holds every ledger


def test_an_external_append_while_open_poisons(library):
    store, authority, locker = library
    grant_importer(authority)
    head = store.read_ledger("security").head
    parts, data = render_batches("security", head, [{"kind": "other"}])[0]
    (store.root / "/".join(parts)).write_bytes(data)
    with pytest.raises(AuthorityUnavailable):
        grant_importer(authority, gid="g2")
    assert authority.poisoned


def test_many_decisions_flush_in_batches_without_poison(library):
    store, authority, locker = library
    grant_importer(authority)
    handle = authority.handle_for(IMPORTER)
    for _ in range(1_200):
        handle.authorize(Permission.INSPECT, "campaigns/c1")
    authority.flush()
    assert not authority.poisoned
    sizes = [len(batch.entries) for batch in store.read_ledger("security").batches]
    assert sizes == [1, 1, 500, 500, 200]  # genesis, grant, then decisions


def test_another_threads_execute_waits_for_a_staging_commit(library):
    store, authority, locker = library
    grant_importer(authority, permissions=(Permission.APPLY, Permission.EXECUTE), scope="campaigns")
    grant_importer(authority, permissions=(Permission.EXECUTE,), scope="providers", gid="gp")
    handle = authority.handle_for(IMPORTER)
    results, started = [], threading.Event()
    real = store._access.ops.make_temp

    def make_temp(*args, **kwargs):  # step 8 of the record commit
        if not started.is_set():
            started.set()
            worker = threading.Thread(target=lambda: results.append(
                authority.handle_for("app:other", trusted=True).authorize(Permission.INSPECT, "x")))
            worker.start()
            time.sleep(0.2)
            assert not results  # blocked on the shared lock while we stage
            make_temp.worker = worker
        return real(*args, **kwargs)
    store._access.ops.make_temp = make_temp
    approve(authority, record_bytes(store))
    txn = store.transaction()
    txn.put(RECORD, None)
    txn.commit(handle, {ADDRESS: "a1"})
    make_temp.worker.join(5)
    store._access.ops.make_temp = real
    assert results and not authority.poisoned
