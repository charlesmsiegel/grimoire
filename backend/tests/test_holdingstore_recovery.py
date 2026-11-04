"""Counted-holdings writes under crashes, I/O errors and a real partial publish (Sprint 023, A6/A7).

A move writes two holder files in one store transaction. Under the 006 fault harness recovery
always yields the state before the move or the completed one, and re-running the same operation
afterwards lands it exactly once. A human edit racing the second file's publication (commit
step 11) leaves a genuinely partial operation, which a retry refuses as ``partial_op``.
"""

from __future__ import annotations

import functools
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from grimoire.holdings import HOLDINGS_SCHEMA, HoldingsRefused, Line, Operation, holdings_from_payload
from grimoire.store import LibraryStore, ProcessLocker, WriteConflict
from grimoire.testing import (
    CrashScenario, Interleave, Invariant, RootedFileOps, Target, atomic_outcome, completes_after,
    explore_crashes, idempotent_recovery, no_residue, untouched,
)
from grimoire.testing.fileops import snapshot

from holdings_fixtures import MARA, ORDINARY, STRONG, TOMA, holdings_library, seed_holders
from store_helpers import drop_empty_dirs

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
MARA_FILE = "lib/campaigns/c1/holdings/character/mara-1.json"
TOMA_FILE = "lib/campaigns/c1/holdings/character/toma-1.json"
MOVE = Operation("m1", (Line.move(MARA, TOMA, ORDINARY, 2),))


@functools.cache
def _template(toma_has_a_file: bool) -> tuple[tuple[str, bytes | None], ...]:
    """Two holders; mara holds 5 ordinary, and toma optionally 1 strong. Built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            seed_holders(store, (MARA, TOMA))
            library = holdings_library(store)
            library.apply(Operation("a0", (Line.add(MARA, ORDINARY, 5),))).commit()
            if toma_has_a_file:
                library.apply(Operation("b0", (Line.add(TOMA, STRONG, 1),))).commit()
        return tuple(snapshot(folder).items())


def _setup(toma_has_a_file: bool):
    def setup(ops, root):
        for key, data in _template(toma_has_a_file):
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)
    return setup


def _move(locker):
    def action(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            holdings_library(store).apply(MOVE).commit()
    return action


def _counts(tree, path: str, holder) -> tuple[dict[str, int], int]:
    document = json.loads(tree[path])
    holdings = holdings_from_payload(holder, document["schema"], document["payload"])
    return dict(holdings.stacks), sum(entry.op == "m1" for entry in holdings.log)


def _landed_once(outcome):
    mara, mara_logs = _counts(outcome.recovered, MARA_FILE, MARA)
    toma, toma_logs = _counts(outcome.recovered, TOMA_FILE, TOMA)
    if mara.get(ORDINARY) != 3 or toma.get(ORDINARY) != 2 or (mara_logs, toma_logs) != (1, 1):
        return f"after the retry mara={mara} ({mara_logs}x m1), toma={toma} ({toma_logs}x m1)"
    return None


def move_scenario(locker) -> CrashScenario:
    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name="holdings move", setup=_setup(False), action=_move(locker), recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                    untouched("lib/campaigns/c1/instances/*")),
        normalize=drop_empty_dirs)


def retry_scenario(locker) -> CrashScenario:
    def recover_and_retry(ops, root):  # reopen (recovery), then re-run the same operation
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            holdings_library(store).apply(MOVE).commit()

    return CrashScenario(
        name="holdings move retried", setup=_setup(True), action=_move(locker), recover=recover_and_retry,
        invariants=(Invariant("landed_once", _landed_once), idempotent_recovery(), RESIDUE),
        normalize=drop_empty_dirs)


# Split as in test_chatstore_recovery so no test nears the 120 s watchdog on slower Windows runners.
@pytest.mark.parametrize("mode", ["errors", "recovery-crashes"])
def test_a_move_survives_every_crash_error_and_recovery_crash(tmp_path, mode):
    report = explore_crashes(move_scenario(ProcessLocker()), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 20
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])


def test_retry_after_recovery_is_exactly_once(tmp_path):
    report = explore_crashes(retry_scenario(ProcessLocker()), tmp_path, errors=True)
    report.assert_ok()
    assert report.points_explored > 20


def _materialize(root: Path) -> None:
    root.mkdir()
    for key, data in _template(True):
        path = root / key
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def test_a_real_partial_publish_is_refused_as_partial_op(tmp_path):
    locker = ProcessLocker()
    probe = tmp_path / "probe"
    _materialize(probe)
    recorder = RootedFileOps(probe)
    _move(locker)(recorder, probe)
    reads = [op for op in recorder.trace if op.kind == "read_bytes" and op.path == TOMA_FILE]
    commit = next(op.index for op in recorder.trace if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    after = [op for op in reads if op.index > commit]
    assert len(after) >= 2  # the pre-publish guard, then toma's own per-entry publish guard
    nth = reads.index(after[1]) + 1

    root = tmp_path / "live"
    _materialize(root)
    human = json.loads((root / TOMA_FILE).read_bytes())
    human["payload"]["stacks"] = [{"item": STRONG, "count": 7}]  # a valid recount, made while publishing

    def recount(ops, live_root):
        ops.write_bytes(live_root / TOMA_FILE, json.dumps(human).encode())

    ops = RootedFileOps(root, plan=[Interleave(Target("read_bytes", path=TOMA_FILE, nth=nth), recount)],
                        record=False)
    with pytest.raises(WriteConflict):
        _move(locker)(ops, root)

    with LibraryStore.open(root / LIB, locker=locker) as store:
        library = holdings_library(store)
        assert library.read(MARA).counts() == {ORDINARY: 3}  # mara's half was published
        assert library.read(TOMA).counts() == {STRONG: 7}  # the human's recount was kept
        with pytest.raises(HoldingsRefused) as error:
            library.apply(MOVE)
        assert error.value.reason == "partial_op" and "toma-1" in error.value.detail
        library.apply(Operation("fix-m1", (Line.adjust(TOMA, ORDINARY, 0, 2),), note="m1 by hand")).commit()
        assert library.read(TOMA).counts() == {STRONG: 7, ORDINARY: 2}
        with pytest.raises(HoldingsRefused):
            library.apply(MOVE)
        assert json.loads((root / MARA_FILE).read_bytes())["schema"] == HOLDINGS_SCHEMA
    shutil.rmtree(probe)
