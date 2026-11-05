"""The 030 proving scenario with the real journal, and completion/adoption under every crash (Sprint 030).

A process accepts Tam's throw (one ball spent, the wisp an ally) and crashes after the journal's commit
point; before restart a cleanup tool removes the remaining ``.txn/*.new`` staging, so recovery can publish
only a prefix of the publish order (relations, then the event, then holder files). The two reachable partial
states are shape B (the companion without its event, the ball not spent) and shape A (event and companion,
the ball not spent). Completing or adopting then recovers one coherent outcome, exactly once: under every
crash, I/O error and recovery crash the state is the partial one or the finished one, and a retry from
there never charges the ball twice.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    Crash, CrashScenario, Invariant, RootedFileOps, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, untouched,
)
from grimoire.testing.fileops import snapshot

from capture_fixtures import allies, balls, problems, recovery, seed_capture, throw
from effects_fixtures import C, CONTROLS, Handles, effects_library
from store_helpers import drop_empty_dirs, explore_mode

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
EVENT = AcceptedRef(C, "e-1")
#: the last file published before the crash: the relation (shape B) or the event file after it (shape A)
CRASH_AFTER = {"B": "lib/campaigns/c/relations/r-1.json", "A": "lib/campaigns/c/accepted/e-1.json"}


def _accept(store: LibraryStore) -> None:
    effects_library(store, Handles()).accept(throw(), controls=CONTROLS).commit()


def _crashed(root: Path, shape: str) -> None:
    """The partial state the proving scenario names, made with the real journal: accept, crash after the
    commit point once ``CRASH_AFTER[shape]`` is published, remove the remaining staging, reopen (recovery)."""
    locker = ProcessLocker()
    with LibraryStore.create(root / LIB, locker=locker) as store:
        seed_capture(store, Handles())
    with pytest.raises(BaseException):
        ops = RootedFileOps(root, plan=[Crash(Target("replace", target=CRASH_AFTER[shape]), "after")], record=False)
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            _accept(store)
    staged = list((root / LIB / ".txn").glob("*.new"))
    assert staged  # the crash left the rest of the transaction staged
    for temp in staged:  # a cleanup tool removes it before restart
        temp.unlink()
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert store.last_recovery.incomplete  # recovery says what it could not publish


@functools.cache
def _template(shape: str) -> tuple[tuple[str, bytes | None], ...]:
    with tempfile.TemporaryDirectory() as folder:
        _crashed(Path(folder), shape)
        return tuple(snapshot(folder).items())


def _restore(state, folder: Path) -> None:  # type: ignore[no-untyped-def]
    for key, data in state:
        path = folder / key
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def _finish(store: LibraryStore, shape: str) -> None:
    """The recovery for the shape: adopt the orphaned changes (B) or complete the event (A)."""
    handles = Handles()
    if shape == "B":
        recovery(store, handles).adopt(throw(), controls=CONTROLS).commit()
    else:
        _, etag = effects_library(store, handles).read(EVENT, requested_by=DIRECTOR)
        recovery(store, handles).complete(EVENT, expected=etag).commit()


def _coherent(store: LibraryStore) -> str | None:
    """Completed: Tam has spent exactly one ball, the wisp is an ally once, and continuity is clean."""
    found = (balls(store), len(allies(store)), problems(store, Handles()))
    return None if found == (2, 1, []) else f"after a retry: balls, allies, problems = {found}"


@pytest.mark.parametrize("shape", ["A", "B"])
def test_the_crashed_capture_recovers_one_coherent_outcome_exactly_once(tmp_path, shape):
    _restore(_template(shape), tmp_path)
    with LibraryStore.open(tmp_path / LIB) as store:
        assert balls(store) == 3 and len(allies(store)) == 1  # the companion landed, the ball was not spent
        assert problems(store, Handles()) == (["receipt_mismatch"] if shape == "A" else ["orphan_effect"])
        _finish(store, shape)
        assert _coherent(store) is None
        _finish(store, shape)  # the retry: a no-op that changes nothing
        _accept(store)  # a late retry of the original acceptance is the duplicate too
        assert _coherent(store) is None


def test_the_crashed_capture_aborts_coherently(tmp_path):
    _restore(_template("A"), tmp_path)
    with LibraryStore.open(tmp_path / LIB) as store:
        handles = Handles()
        library = effects_library(store, handles)
        _, etag = library.read(EVENT, requested_by=DIRECTOR)
        library.revoke(EVENT, reason="crashed mid-publish", expected=etag, partial=True).commit()
        assert (balls(store), allies(store), problems(store, handles)) == (3, [], [])


def charged_once(shape: str) -> Invariant:
    """From each recovered state, finishing again (a no-op once it landed) leaves exactly one ball spent."""
    seen: dict[tuple, str | None] = {}

    def check(outcome) -> str | None:  # type: ignore[no-untyped-def]
        state = tuple(sorted(outcome.recovered.items()))
        if state not in seen:
            seen[state] = _retried(state)
        return seen[state]

    def _retried(state: tuple) -> str | None:
        with tempfile.TemporaryDirectory() as folder:
            _restore(state, Path(folder))
            with LibraryStore.open(Path(folder) / LIB) as store:
                _finish(store, shape)
                return _coherent(store)
    return Invariant("charged_once", check)


def _setup(shape: str):  # type: ignore[no-untyped-def]
    def setup(ops, root):  # type: ignore[no-untyped-def]
        for key, data in _template(shape):
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)
    return setup


def scenario(shape: str) -> CrashScenario:
    locker = ProcessLocker()

    def action(ops, root):  # type: ignore[no-untyped-def]
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            _finish(store, shape)

    def recover(ops, root):  # type: ignore[no-untyped-def]
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"finish shape {shape}", setup=_setup(shape), action=action, recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                    untouched("lib/campaigns/c/instances/*"), untouched("lib/campaigns/c/chats/*"),
                    charged_once(shape)),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("shape, mode", [("A", "errors"), ("A", "recovery-crashes"), ("B", "errors"),
                                         ("B", "recovery-crashes")],
                         ids=["complete-errors", "complete-recovery-crashes", "adopt-errors",
                              "adopt-recovery-crashes"])
def test_finishing_survives_every_crash_error_and_recovery_crash(tmp_path, shape, mode):
    report = explore_crashes(scenario(shape), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 5
    if mode != "errors":
        assert any(run.recover_trace for run in report.runs[1:])
