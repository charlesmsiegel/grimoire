"""Framing an attempt against an obstacle under crashes, I/O errors and crashed recoveries (Sprint 040).

``ObstacleLibrary.frame`` writes two authority-ledger batches, each in its own store commit: the
attempt's link to its obstacle, then 029's frame. Under the 006 fault harness, recovery always leaves
one of three states, read back through the real libraries: nothing written, the link alone (the attempt
waits with no frame), or both. A 029 frame never exists without its link, a torn or half-published link
batch is never read as an attempt, and a superseding link that reached the disk names the attempt it
superseded, so that one stays void whatever 029 decides later. ``record`` writes one batch and is
all-or-nothing.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Invariant, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue,
)
from grimoire.testing.fileops import snapshot

from adjudication_fixtures import ANA, BOB, Q, build_library, moment
from obstacle_fixtures import ALARM, BRIBE, SILVER, TRAINING, ArchiveTable, archive, attempt, offer, stakes_of
from store_helpers import FAIL_RECOVERY, drop_empty_dirs, explore_mode

LIB = "lib"
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
LINK_BATCH = f"lib/authority/obstacles/{Q}/"


@functools.cache
def _template(stage: str) -> tuple[tuple[str, bytes | None], ...]:
    """``empty``: the seated table; ``recorded``: the archive on record; ``handed``: Ana's bribe (c10)
    pending and Mara handed to Bob, so the bribe cannot be decided now. Built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            build_library(store)
            table = ArchiveTable(store).seat()
            library = table.obstacles()
            if stage != "empty":
                library.record(archive())
            if stage == "handed":
                frame = attempt(library.required(Q, "archive", "bribe", 10) + (SILVER,), stakes_of(BRIBE))
                library.frame("archive", frame, approach="bribe", contribution=offer(), author=table.player())
                table.control("mara-1", BOB)
            table.close()
        return tuple(snapshot(folder).items())


def _setup(stage: str):
    def setup(ops, root):
        for key, data in _template(stage):
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)
    return setup


def _bribe(table: ArchiveTable) -> None:
    library = table.obstacles()
    frame = attempt(library.required(Q, "archive", "bribe", 10) + (SILVER,), stakes_of(BRIBE))
    library.frame("archive", frame, approach="bribe", contribution=offer(), author=table.player(ANA))


def _sneak(table: ArchiveTable) -> None:
    library = table.obstacles()
    frame = attempt(library.required(Q, "archive", "sneak", 11) + (TRAINING,), (ALARM.stake,), cid="c11",
                    at=moment(11, "t11"))
    library.frame("archive", frame, approach="sneak", contribution=offer(cid="c11", turn="t11", by=BOB),
                  author=table.player(BOB))


def _frame_scenario(stage: str, act, allowed: dict[tuple, str]) -> tuple[CrashScenario, dict[str, str]]:
    """``act`` frames one attempt; after recovery, the obstacle as the libraries read it is one of ``allowed``.

    Also returns what was read back after each run, by point, as the name ``allowed`` gives it."""
    locker = ProcessLocker()
    observed: list[tuple] = []
    reads: dict[str, str] = {}

    def action(ops, root):
        observed.clear()
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = ArchiveTable(store)
            try:
                act(table)
            finally:
                table.close()

    def recover(ops, root):  # reopening recovers the store; the reads write nothing
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = ArchiveTable(store)
            try:
                library = table.obstacles()
                state = library.state(Q, "archive")
                read = (tuple((a.attempt.attempt_id, a.status, a.frame is not None) for a in state.attempts),
                        tuple(sorted(library.adjudication.state(Q).frames)),
                        tuple(a.attempt.supersedes for a in state.attempts))
            finally:
                table.close()
        observed.clear()
        observed.append(read)

    def one_of_three(outcome):
        if not observed:
            return "recovery read nothing back"
        if observed[-1] not in allowed:
            return f"the libraries read back {observed[-1]}, not one of {sorted(allowed.values())}"
        reads[outcome.point] = allowed[observed[-1]]
        return None

    return CrashScenario(
        name=f"obstacle frame ({stage})", setup=_setup(stage), action=action, recover=recover,
        invariants=(Invariant("link_before_frame", one_of_three), idempotent_recovery(), RESIDUE),
        normalize=drop_empty_dirs), reads


def _link_points(report) -> tuple[str, str]:
    """The baseline's link batch: tearing its staged write, and crashing just after its rename into place."""
    trace = report.runs[0].action_trace
    rename = next(op for op in trace if op.kind == "replace" and (op.target or "").startswith(LINK_BATCH))
    staged = max((op for op in trace if op.kind == "write_bytes" and op.path == rename.path
                  and op.index < rename.index), key=lambda op: op.index)
    return f"action:#{staged.index} write_bytes ", f"action:#{rename.index} replace "


#: (attempt id, status, framed) per attempt, the 029 frames on record, and what each link supersedes.
FIRST = {
    ((), (), ()): "nothing written",
    ((("c10", "pending", False),), (), (None,)): "the link alone",
    ((("c10", "pending", True),), ("c10",), (None,)): "link and frame",
}
SUPERSEDING = {
    ((("c10", "pending", True),), ("c10",), (None,)): "nothing written",
    ((("c10", "void", True), ("c11", "pending", False)), ("c10",), (None, "c10")): "the link alone",
    ((("c10", "void", True), ("c11", "pending", True)), ("c10", "c11"), (None, "c10")): "link and frame",
}
SCENARIOS = {"first": ("recorded", _bribe, FIRST), "superseding": ("handed", _sneak, SUPERSEDING)}


# Split by mode, as in test_holdingstore_recovery, so no test nears the watchdog on slower Windows runners.
@pytest.mark.parametrize("mode", ["errors", "recovery-crashes", FAIL_RECOVERY])
@pytest.mark.parametrize("which", sorted(SCENARIOS))
def test_a_frame_leaves_nothing_the_link_alone_or_both(tmp_path, which, mode):
    stage, act, allowed = SCENARIOS[which]
    scenario, reads = _frame_scenario(stage, act, allowed)
    report = explore_crashes(scenario, tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 20
    if mode != "errors":
        assert any(run.recover_trace for run in report.runs[1:])
    if mode == "errors":
        # Each state really occurs: a torn link batch is read as nothing at all, and a crash just after the
        # link's rename into place leaves the link alone, which recovery keeps (the attempt waits for its frame).
        assert set(reads.values()) == set(allowed.values())
        staged, renamed = _link_points(report)
        torn = [read for point, read in reads.items() if point.startswith(staged) and point.endswith("@torn")]
        after = [read for point, read in reads.items() if point.startswith(renamed) and point.endswith("@after")]
        assert (torn, after) == (["nothing written"], ["the link alone"])


def test_recording_an_obstacle_is_all_or_nothing(tmp_path):
    locker = ProcessLocker()

    def action(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = ArchiveTable(store)
            try:
                table.obstacles().record(archive())
            finally:
                table.close()

    def recover(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = ArchiveTable(store)
            try:
                table.obstacles().states(Q)  # a torn or half-published batch must not break replay
            finally:
                table.close()

    scenario = CrashScenario(
        name="obstacle record", setup=_setup("empty"), action=action, recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE,
                    completes_after(Target("replace", target="lib/.txn/journal.json"))),
        normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, **explore_mode("errors"))
    report.assert_ok()
    assert report.points_explored > 10
