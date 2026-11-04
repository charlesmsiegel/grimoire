"""Knowledge appends survive every crash, I/O error and recovery crash (Sprint 020, F20).

An append writes records plus one log batch in one store transaction. Under the 006 fault
harness, recovery always yields the state before the append or the completed one, with no
residue, and recovering again changes nothing. Errors and crashes are explored on a 3-record
append (4 files with its batch); recovery crashes on a 1-record append (2 files), which settles
through the same engine table, keeping each test well under the Windows watchdog.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR, Event, InstanceRef, Perception, Proposition, Report
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, RecoveryFailed
from grimoire.testing import (
    Crash, CrashScenario, RootedFileOps, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, untouched,
)
from grimoire.testing.fileops import snapshot

from evidence_fixtures import m
from store_helpers import OWNER, drop_empty_dirs, owner_handle

LIB = "lib"
C = "c1"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
MARA, INES, DUKE = (InstanceRef(C, "character", name) for name in ("mara", "ines", "duke"))


def _library(store: LibraryStore) -> EvidenceLibrary:
    return EvidenceLibrary(store, owner_handle(), owner=OWNER)


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """Three actors and one prior batch (a proposition), built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            txn = store.transaction()
            for actor in (MARA, INES, DUKE):
                txn.put(RecordEnvelope(actor, {}), None)
            txn.commit(owner_handle())
            _library(store).append(C, [Proposition(C, "p1", "the duke lied", m(1), "director")]).commit()
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def three_records(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _library(store).append(C, [
            Event(C, "e1", "a charter changes hands", m(2), "director"),
            Perception(C, "v1", MARA, "e1", "witnessed", "a sealed charter", m(2), "director", confirmed=True),
            Report(C, "v2", INES, "p1", "told", m(3), "director", confirmed=True, sender=DUKE, addressed=True,
                   sent_at=m(2)),
        ]).commit()


def one_record(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _library(store).append(C, [Event(C, "e1", "rain", m(2), "director")]).commit()


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"evidence {action.__name__}", setup=_setup, action=lambda ops, root: action(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                                     untouched("lib/campaigns/c1/instances/*")),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, mode", [
    (three_records, "crashes"), (three_records, "errors"), (one_record, "recovery-crashes"),
], ids=["three-crashes", "three-errors", "one-recovery-crashes"])
def test_appends_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 20
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])


def test_a_build_that_cannot_read_knowledge_paths_can_discard_the_journal(tmp_path, monkeypatch):
    """A pre-020 build cannot recover a journal naming knowledge paths; discard_journal resolves it."""
    root = tmp_path / "root"
    root.mkdir()
    _setup(_PlainOps(), root)
    locker = ProcessLocker()
    ops = RootedFileOps(root, plan=[Crash(JOURNAL, "after")], record=False)
    with pytest.raises(BaseException):
        one_record(ops, root, locker)
    import grimoire.store.journal as journal
    real = journal.classify
    monkeypatch.setattr(journal, "classify", lambda parts: None if "events" in parts or "evidence" in parts
                        else real(parts))
    with pytest.raises(RecoveryFailed):
        LibraryStore.open(root / LIB, locker=ProcessLocker())
    LibraryStore.discard_journal(root / LIB, locker=ProcessLocker())
    monkeypatch.setattr(journal, "classify", real)
    with LibraryStore.open(root / LIB, locker=ProcessLocker()) as store:
        evidence = _library(store)
        assert [e.event_id for e in evidence.read(C, requested_by=DIRECTOR).knowledge.events] == []
        assert any("discarded" in path for path in store.conflicts())
        evidence.append(C, [Event(C, "e1", "rain", m(2), "director")]).commit()  # a fresh append works
        assert [e.event_id for e in evidence.read(C, requested_by=DIRECTOR).knowledge.events] == ["e1"]


class _PlainOps:
    def make_dirs(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)

    def write_bytes(self, path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)

