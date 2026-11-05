"""Relation writes survive every crash, I/O error and recovery crash (Sprint 017, F18).

``add`` creates one relation file; ``end`` rewrites one. Under the 006 fault harness, recovery
always yields either the state before the action or the completed one, with no residue, and
recovering again changes nothing. Clocks and ids are rebuilt inside every run, so each action is
deterministic, and the instances are never touched.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core import Moment
from grimoire.core.identity import RelationRef
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from chat_fixtures import Clock, Ids
from graph_fixtures import C, GUILD, INES, WREN, seed_campaign
from store_helpers import FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode, owner_handle

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
EXISTING = RelationRef(C, "r-1")


def _library(store: LibraryStore) -> RelationLibrary:
    return RelationLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """Three instances and one relation (r-1), built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            seed_campaign(store, (WREN, INES, GUILD))  # the smallest tree that covers the actions
            change = _library(store).add(C, "member_of", WREN, GUILD, since=Moment(3), provenance="director")
            assert change.commit().relation == EXISTING
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def add(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        library = _library(store)
        library._new_id.counts["r"] = 1  # r-1 exists in the template: deterministic, no retry
        library.add(C, "has_met", WREN, INES, since=Moment(2), provenance="director").commit()


def end(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        library = _library(store)
        _, etag = library.read(EXISTING)
        library.end(EXISTING, until=Moment(9), provenance="director", expected=etag).commit()


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"relation {action.__name__}", setup=_setup, action=lambda ops, root: action(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                                     untouched("lib/campaigns/c/instances/*")),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, mode", [(add, "errors"), (end, "errors"), (end, "recovery-crashes"),
                                          (add, FAIL_RECOVERY), (end, FAIL_RECOVERY)],
                         ids=["add-errors", "end-errors", "end-recovery-crashes", "add-fail-recovery-crashes",
                              "end-fail-recovery-crashes"])
def test_relation_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 10
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])
