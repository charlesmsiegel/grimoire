"""035's ``turns/`` writer under the fault harness: a turn create (a post) and a turn compare-and-swap (a take
kept) survive every crash point, torn write, injected I/O error and recovery crash.

The same exploration as the other store families' ``*_recovery.py`` tests, over ``SceneLibrary.write``.
Synthetic only.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.scene import Post, SceneLine, Take, TurnRecord
from grimoire.scenestore import SceneLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
)
from grimoire.testing.fileops import snapshot

from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode, owner_handle

LIB = "lib"
CHAT = ChatRef("q-1", "t-1")
PC, NPC = InstanceRef("q-1", "character", "pc"), InstanceRef("q-1", "character", "npc")
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")


def turn1(kept: bool = False) -> TurnRecord:
    post = Post("human:ana", PC, "speech", "Hello?", (NPC, PC), "speech")
    line = SceneLine("t-1.000001.1.1", NPC, "Npc", "Evening.", (NPC, PC))
    take = Take(1, "kept" if kept else "proposed", "generated", (NPC, PC), lines=(line,),
                by="human:ana" if kept else None)
    return TurnRecord(CHAT, 1, "post", post, (take,))


@functools.cache
def template():
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            SceneLibrary(store, owner_handle(), owner=OWNER).write(turn1(), None)
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def keep(ops, root, locker):
    """A compare-and-swap: the proposed take of turn 1 is kept."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        scenes = SceneLibrary(store, owner_handle(), owner=OWNER)
        scenes.write(turn1(kept=True), scenes.turns(CHAT).last)


def post_next(ops, root, locker):
    """A create: turn 2 is posted after turn 1, as read."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        scenes = SceneLibrary(store, owner_handle(), owner=OWNER)
        stored = scenes.turns(CHAT).last
        following = TurnRecord(CHAT, 2, "post", Post("human:ana", PC, "speech", "And?", (NPC, PC), "speech"))
        scenes.write(following, None, after=stored)


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(name=f"turn {action.__name__}", setup=_setup,
                         action=lambda ops, root: action(ops, root, locker), recover=recover,
                         invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL)),
                         normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, mode", [
    (keep, "errors"), (keep, "recovery-crashes"), (post_next, "errors"), (post_next, "recovery-crashes"),
    *(pytest.param(action, FAIL_RECOVERY, marks=EXHAUSTIVE_RECOVERY) for action in (keep, post_next)),
], ids=["keep-errors", "keep-recovery-crashes", "post-errors", "post-recovery-crashes", "keep-fail-recovery-crashes",
        "post-fail-recovery-crashes"])
def test_turn_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 20
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])

