"""Accepted-effect writes survive every crash, I/O error and recovery crash (Sprint 021, F11).

``accept`` writes the accepted-event file and its relation changes in one transaction; ``revoke``
(also partial) rewrites the event and retracts/reopens relations; 017's ``reopen`` rewrites one
relation. Under the 006 fault harness, recovery always yields the state before the action or the
completed one, with no residue, and recovering again changes nothing. Clocks, ids and handles are
rebuilt inside every run, so each action is deterministic; instances and chats are never touched.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, RelationRef
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from chat_fixtures import Clock
from effects_fixtures import C, CONTROLS, Handles, contribution, effects_library, give, seed
from store_helpers import OWNER, drop_empty_dirs

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
EVENT = AcceptedRef(C, "e-1")
SEED_KEY1 = RelationRef(C, "r-seed-1")


def _accept(store: LibraryStore, handles: Handles) -> None:
    proposal = give()
    effects_library(store, handles).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                           endorsements=[handles.endorse(proposal)]).commit()


def _break(store: LibraryStore, handles: Handles) -> None:
    relations = RelationLibrary(store, handles.app, clock=Clock(), new_id=lambda prefix: "r-hand", owner=OWNER)
    relations.reopen(SEED_KEY1, provenance="editor", expected=relations.read(SEED_KEY1)[1]).commit()


@functools.cache
def _template(stage: str) -> tuple[tuple[str, bytes | None], ...]:
    """``base``: the seeded campaign; ``accepted``: plus e-1; ``broken``: plus a hand reopen of e-1's ending."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            handles = Handles()
            seed(store, handles)
            if stage != "base":
                _accept(store, handles)
            if stage == "broken":
                _break(store, handles)
        return tuple(snapshot(folder).items())


def _setup(stage):
    def setup(ops, root):
        for key, data in _template(stage):
            if data is None:
                ops.make_dirs(root / key)
            else:
                ops.write_bytes(root / key, data)
    return setup


def accept(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _accept(store, Handles())


def _revoke(ops, root, locker, partial):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        handles = Handles()
        library = effects_library(store, handles)
        _, etag = library.read(EVENT, requested_by=DIRECTOR)
        library.revoke(EVENT, reason="rewind", expected=etag, endorsements=[handles.undo(EVENT)],
                       partial=partial).commit()


def revoke(ops, root, locker):
    _revoke(ops, root, locker, False)


def revoke_partial(ops, root, locker):
    _revoke(ops, root, locker, True)


def reopen(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        _break(store, Handles())


STAGES = {accept: "base", revoke: "accepted", revoke_partial: "broken", reopen: "accepted"}


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"effect {action.__name__}", setup=_setup(STAGES[action]),
        action=lambda ops, root: action(ops, root, locker), recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                    untouched("lib/campaigns/c/instances/*"), untouched("lib/campaigns/c/chats/*")),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, mode", [
    (accept, "errors"), (accept, "recovery-crashes"), (revoke, "errors"), (revoke_partial, "errors"),
    (reopen, "errors"),
], ids=["accept-errors", "accept-recovery-crashes", "revoke-errors", "revoke-partial-errors", "reopen-errors"])
def test_effect_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 10
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])
