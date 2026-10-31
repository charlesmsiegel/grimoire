"""Durable authority: restart, durability before effect, poisoning, tampering (010b S1–S9)."""

from __future__ import annotations

import functools
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from grimoire.ledgers import AuthorityUnavailable, DurableAuthority, LedgerBroken, LedgerInvalid
from grimoire.ledgers.codec import encode_decision
from grimoire.ledgers.security import _replay
from grimoire.security import Approval, AuditRecord, Authorizer, Grant, Permission, Reason, ResourcePath
from grimoire.store import (
    CommitIncomplete, LibraryStore, LinkRefused, ProcessLocker, RecordEnvelope, StoreError, WriteDenied,
)
from grimoire.store.jsonfmt import parse_strict
from grimoire.store.ledger import render_batches
from grimoire.store.library import _Commit
from grimoire.testing import (
    CrashScenario, Fail, Invariant, RootedFileOps, Target, explore_crashes, idempotent_recovery, no_residue,
)
from grimoire.testing.fileops import snapshot
from grimoire.core.identity import InstanceRef

from ledger_helpers import (
    ADDRESS, IMPORTER, OWNER, RECORD, approve, fixed_clock, grant_importer, open_authority, record_bytes,
)
from store_helpers import drop_empty_dirs, make_dir_link

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
        (path.relative_to(root.parent).as_posix(), path.read_bytes()) for path in sorted((root / "authority" / "security").glob("*.json")))
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
    grant_importer(authority, scope="worlds", gid="g2")
    approve(authority, b"pending", aid="a2")
    approve(authority, b"elsewhere", aid="a3", address="worlds/w/x.json")  # pinned to g2
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
    assert [a.approval_id for a in authority.pending_approvals()] == ["a2"]  # a3 closed with g2; a2 is on g1
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
    store.close()  # the writer lock goes with the process
    reopened = LibraryStore.open(store.root, locker=locker)
    with DurableAuthority.open(reopened, [OWNER], clock=fixed_clock()) as replayed:
        assert replayed.pending_approvals() == ()  # consumed, and it stays consumed
        assert not replayed.handle_for(IMPORTER).authorize(Permission.APPLY, ADDRESS, b"bytes", "a1").allowed
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
        published = (outcome.recovered.get("lib/" + ADDRESS) is not None
                     and outcome.before.get("lib/" + ADDRESS) is None)
        if published:
            if not any(d["allowed"] and d["resource"] == ADDRESS and d["approval_id"] == "a1" for d in decisions):
                return "a record was published without its allow decision on disk"
            if "a1" in _pending_after_replay(outcome.recovered):
                return "the approval that authorized published bytes is still pending"  # N9(c)
        known = {}
        for item in seen:
            item = encode_decision(item) if isinstance(item, AuditRecord) else dict(item)
            known[item["sequence"]] = item
        for decision in decisions:
            if decision["sequence"] not in known:
                return f"decision {decision['sequence']} on disk was never made"
            if known[decision["sequence"]] != decision:
                return f"decision {decision['sequence']} on disk differs from the one made"
        return None

    return CrashScenario("durable authority save", setup, action, recover,
                         (Invariant("durable_before_effect", durable_before_effect), idempotent_recovery(),
                          no_residue("lib/.txn/*.new", "lib/.txn/journal.json")), normalize=drop_empty_dirs)


def _pending_after_replay(tree) -> set[str]:
    """Approval ids still pending once the ``security`` ledger in ``tree`` is replayed."""
    entries = []
    for key in sorted(k for k in tree if k.startswith("lib/authority/security/") and tree[k] is not None):
        entries.extend(parse_strict(tree[key])["entries"])
    kernel = Authorizer([OWNER])
    _replay(kernel, frozenset([OWNER]), entries)
    return {approval.approval_id for approval in kernel.pending_approvals()}


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


@pytest.mark.parametrize("step", ["_check_paths", "_check_revisions", "_check_names"])
def test_another_threads_execute_waits_through_a_commits_read_phase(library, monkeypatch, step):
    store, authority, locker = library
    grant_importer(authority, permissions=(Permission.APPLY, Permission.EXECUTE), scope="campaigns")
    grant_importer(authority, permissions=(Permission.EXECUTE,), scope="providers", gid="gp")
    approve(authority, b"call", aid="ap", address="providers/model", permission=Permission.EXECUTE)
    results, workers = [], []
    real = getattr(_Commit, step)

    def hooked(self):  # one of steps 3-6, while the commit reads with cached listings
        if workers or any(put.ledger for put in self.puts):
            return real(self)
        worker = threading.Thread(target=lambda: results.append(authority.handle_for(IMPORTER).authorize(
            Permission.EXECUTE, "providers/model", b"call", "ap")))
        worker.start()
        workers.append(worker)
        time.sleep(0.2)
        assert not results
        return real(self)
    approve(authority, record_bytes(store))
    txn = store.transaction()
    txn.put(RECORD, None)
    monkeypatch.setattr(_Commit, step, hooked)  # after preview(), which reads outside any commit
    txn.commit(authority.handle_for(IMPORTER), {ADDRESS: "a1"})
    workers[0].join(5)
    assert results[0].allowed and not authority.poisoned


def test_a_load_in_another_thread_does_not_poison_a_write(library):
    store, authority, locker = library
    seed_txn = store.transaction()
    seed_txn.put(RECORD, None)
    grant_importer(authority)
    approve(authority, record_bytes(store))
    seed_txn.commit(authority.handle_for(IMPORTER), {ADDRESS: "a1"})
    inside, release = threading.Event(), threading.Event()
    real = store._access.ops.list_dir

    def list_dir(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread() and not inside.is_set():
            inside.set()
            release.wait(0.3)  # loads hold the lock, so the write waits for this to time out
        return real(*args, **kwargs)
    store._access.ops.list_dir = list_dir
    reader = threading.Thread(target=store.load)
    reader.start()
    assert inside.wait(5)
    grant_importer(authority, gid="g2")
    release.set()
    reader.join(5)
    store._access.ops.list_dir = real
    assert not authority.poisoned and "g2" in {g.grant_id for g in authority.active_grants()}


def test_one_approval_is_spent_once_across_threads(library):
    store, authority, locker = library
    grant_importer(authority)
    approve(authority, b"bytes")
    barrier = threading.Barrier(8)
    results = []

    def spend():
        handle = authority.handle_for(IMPORTER)
        barrier.wait()
        results.append(handle.authorize(Permission.APPLY, ADDRESS, b"bytes", "a1"))
    threads = [threading.Thread(target=spend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sum(r.allowed for r in results) == 1 and not authority.poisoned
    sequences = [record.sequence for record in authority.audit()]
    assert sequences == list(range(1, len(sequences) + 1))
    store, authority = _reopen(store, authority, locker)
    assert [record.sequence for record in authority.audit()] == sequences
    authority.close()
    store.close()


class _Subclass(Approval):
    pass


@pytest.mark.parametrize("call", ["subclass-approval", "clock-on-grant", "clock-on-approval",
                                  "revoke-unknown-grant", "revoke-unknown-approval"])
def test_a_refused_lifecycle_call_changes_nothing_and_does_not_poison(library, call):
    store, authority, locker = library
    grant_importer(authority)
    before = (authority.active_grants(), authority.pending_approvals(), store.read_ledger("security").head)

    def broken_clock():
        raise RuntimeError("clock broke")
    approval = Approval("a9", IMPORTER, Permission.APPLY, ResourcePath.parse(ADDRESS), "sha256:" + "0" * 64, OWNER)
    if call == "subclass-approval":
        action = lambda: authority.register_approval(_Subclass(*vars_of(approval)))  # noqa: E731
    elif call.startswith("clock"):
        authority._clock = broken_clock
        action = ((lambda: grant_importer(authority, gid="g2")) if call == "clock-on-grant"
                  else (lambda: authority.register_approval(approval)))
    elif call == "revoke-unknown-grant":
        action = lambda: authority.revoke_grant("nope")  # noqa: E731
    else:
        action = lambda: authority.revoke_approval("nope")  # noqa: E731
    with pytest.raises((ValueError, RuntimeError)):
        action()
    assert not authority.poisoned
    assert (authority.active_grants(), authority.pending_approvals(), store.read_ledger("security").head) == before


def vars_of(approval: Approval) -> tuple:
    return (approval.approval_id, approval.principal, approval.permission, approval.resource,
            approval.payload_digest, approval.approved_by)


def test_an_interrupt_inside_the_context_writes_nothing_more(library):
    store, authority, locker = library
    grant_importer(authority)
    head = store.read_ledger("security").head
    with pytest.raises(KeyboardInterrupt):
        with authority:
            authority.handle_for(IMPORTER).authorize(Permission.INSPECT, "campaigns/c1")  # pending, not flushed
            raise KeyboardInterrupt
    assert store.read_ledger("security").head == head
    with pytest.raises(AuthorityUnavailable):
        authority.handle_for(IMPORTER)


# -- authority/ is never read or written through a link -------------------------------------------

@pytest.mark.parametrize("swap", ["authority-link", "security-link", "batch-fifo"])
def test_a_linked_or_special_security_ledger_is_refused(tmp_path, swap):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / LIB, locker=locker)
    with open_authority(store) as authority:
        grant_importer(authority)
    store.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = tmp_path / LIB / ("authority" if swap == "authority-link" else "authority/security")
    if swap == "batch-fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("this OS has no FIFOs in the filesystem")
        (folder / "0000000002.json").unlink()
        os.mkfifo(folder / "0000000002.json")  # opening it could block
    else:
        folder.rename(outside / folder.name)
        make_dir_link(folder, outside / folder.name)
    before = snapshot(outside)
    store = LibraryStore.open(tmp_path / LIB, locker=locker)
    with pytest.raises((LedgerBroken, LinkRefused)):
        open_authority(store)
    store.close()
    assert snapshot(outside) == before


@pytest.mark.parametrize("reads", ["accessors", "load"])
def test_readers_in_other_threads_see_whole_states(library, reads):
    store, authority, locker = library
    grant_importer(authority, permissions=(Permission.EXECUTE,), scope="campaigns")
    interval = sys.getswitchinterval()
    failures, done = [], threading.Event()

    def read_loop():
        while not done.is_set():
            try:
                if reads == "accessors":
                    authority.pending_approvals()
                    authority.describe_policy()
                else:
                    store.load()  # would see a flush's journal unless reads wait for commits
            except Exception as error:  # noqa: BLE001
                failures.append(error)
                return
    sys.setswitchinterval(1e-6)  # interleave as often as possible
    try:
        reader = threading.Thread(target=read_loop)
        reader.start()
        for number in range(150):  # each registration is a commit that grows the approvals
            approve(authority, b"%d" % number, aid=f"a{number}", permission=Permission.EXECUTE)
            if failures:
                break
        done.set()
        reader.join(10)
    finally:
        sys.setswitchinterval(interval)
    assert failures == [] and not authority.poisoned


def test_a_deliberate_exit_still_flushes(library):
    store, authority, locker = library
    grant_importer(authority)
    with pytest.raises(SystemExit):
        with authority:
            authority.handle_for(IMPORTER).authorize(Permission.INSPECT, "campaigns/c1")
            raise SystemExit(0)
    assert _decisions_on_disk(store.root)[-1]["permission"] == "inspect"


@pytest.mark.parametrize("closed", ["revoked-grant", "revoked-approval", "consumed-approval", "grant-revoked-under-it"])
def test_a_closed_grant_or_approval_cannot_be_revoked_again(library, closed):
    store, authority, locker = library
    grant_importer(authority)
    approve(authority, b"bytes")
    if closed == "revoked-grant":
        authority.revoke_grant("g1")
        again = lambda: authority.revoke_grant("g1")  # noqa: E731
    else:
        if closed == "revoked-approval":
            authority.revoke_approval("a1")
        elif closed == "consumed-approval":
            assert authority.handle_for(IMPORTER).authorize(Permission.APPLY, ADDRESS, b"bytes", "a1").allowed
        else:
            authority.revoke_grant("g1")
        again = lambda: authority.revoke_approval("a1")  # noqa: E731
    authority.flush()
    head = store.read_ledger("security").head
    with pytest.raises(ValueError):
        again()  # a second revocation on disk would make the ledger unreplayable
    assert store.read_ledger("security").head == head and not authority.poisoned
    store, authority = _reopen(store, authority, locker)  # still opens
    authority.close()
    store.close()


def test_a_scope_left_by_an_error_still_flushes_its_decisions(library):
    store, authority, locker = library
    grant_importer(authority, permissions=(Permission.EXECUTE,), scope="providers", gid="gp")
    approve(authority, b"call", aid="ap", address="providers/model", permission=Permission.EXECUTE)
    with pytest.raises(RuntimeError):
        with authority.consequential_scope():
            assert authority.handle_for(IMPORTER).authorize(Permission.EXECUTE, "providers/model", b"call", "ap").allowed
            assert _decisions_on_disk(store.root) == []  # deferred to the scope's end
            raise RuntimeError("the save failed after deciding")
    assert [d["approval_id"] for d in _decisions_on_disk(store.root)] == ["ap"] and not authority.poisoned


def test_a_save_racing_the_first_open_cannot_use_a_foreign_handle(tmp_path):
    from store_helpers import owner_handle
    store = LibraryStore.create(tmp_path / LIB, locker=ProcessLocker())
    foreign = owner_handle()  # a bare Authorizer's handle: valid only while the store is unbound
    results, opened = [], []

    def save():
        txn = store.transaction()
        txn.put(RECORD, None)
        try:
            results.append(txn.commit(foreign))
        except Exception as error:  # noqa: BLE001
            results.append(error)
    with store.lock:  # the first open binds while the save waits
        worker = threading.Thread(target=save)
        worker.start()
        time.sleep(0.2)
        opened.append(open_authority(store))
    worker.join(5)
    assert isinstance(results[0], WriteDenied) and not (store.root / ADDRESS).exists()
    opened[0].close()
    store.close()


@pytest.mark.parametrize("what", ["grant", "approval"])
def test_a_path_replay_could_not_read_back_is_refused_before_any_change(library, what):
    store, authority, locker = library
    long_path = ResourcePath(("campaigns",) + tuple("s" * 60 + str(n) for n in range(17)))
    assert len(str(long_path)) > 1024
    if what == "grant":
        call = lambda: authority.add_grant(Grant("g9", IMPORTER, frozenset({Permission.APPLY}), long_path, OWNER))  # noqa: E731
    else:
        grant_importer(authority)
        call = lambda: authority.register_approval(  # noqa: E731
            Approval("a9", IMPORTER, Permission.APPLY, long_path, "sha256:" + "0" * 64, OWNER))
    before = (authority.active_grants(), authority.pending_approvals(), store.read_ledger("security").head)
    with pytest.raises(ValueError, match="replay"):
        call()
    assert (authority.active_grants(), authority.pending_approvals(), store.read_ledger("security").head) == before
    assert not authority.poisoned
    store, authority = _reopen(store, authority, locker)  # still opens
    authority.close()
    store.close()
