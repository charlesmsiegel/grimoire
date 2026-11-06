"""Sprint 042's two commits survive every crash, I/O error and recovery crash.

A rebase writes the actor's instance file and re-pins every chat that pins it (two chats of one
campaign here); a canon proposal writes a new world's manifest and its definitions. Under the 006
fault harness, recovery always yields either the state before the action or the completed one, with
no residue; recovering again changes nothing, and neither writes outside its own records. Clocks are
rebuilt inside every run, so each action is deterministic. Synthetic Wren; no user content.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest
from chat_fixtures import Clock, Ids
from rebase_fixtures import R1, R2, SALTMARCH, SECTIONS, WORLD, envelope, publish, put, wren
from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode, owner_handle

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.rebasestore import CanonPick, CanonProposer, SourceRebase
from grimoire.store import LibraryStore, ProcessLocker, SourceManifest
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
TAKE = {"section:biography": "take"}


@functools.cache
def _template() -> tuple[tuple[tuple[str, bytes | None], ...], ChatRef, InstanceRef]:
    """Wren r1 played in two chats of one campaign, and r2 published with a corrected biography."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            put(store, SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r1"}), envelope(wren()))
            chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
            started = chats.start_chat(Launch("character", R1)).commit()
            [actor] = started.actors
            chats.start_chat(Launch("character", R1), scope=started.scope,
                             reuse_actors={("character", R1.entity_key): actor}).commit()
            publish(store, wren(R2, sections={**SECTIONS, "biography": "Born in the harbor town."}))
        return tuple(snapshot(folder).items()), started.chat, actor


def _setup(ops, root):
    for key, data in _template()[0]:
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def rebase(ops, root, locker):
    _, _, actor = _template()
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        service = SourceRebase(store, owner_handle(), clock=Clock())
        review = service.review(actor)
        assert len(review.repinned) == 2
        service.apply(review, TAKE).commit()


def canon(ops, root, locker):
    _, chat, actor = _template()
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        CanonProposer(store, owner_handle(), owner=OWNER).propose(chat, SALTMARCH, [CanonPick(actor, "wren")]).commit()


def scenario(action):
    locker = ProcessLocker()
    _template()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    # a rebase never writes a definition; a canon proposal never writes a campaign
    keep = "lib/sources/*" if action is rebase else "lib/campaigns/*"
    return CrashScenario(
        name=f"042 {action.__name__}", setup=_setup, action=lambda ops, root: action(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                                     untouched(keep)),
        normalize=drop_empty_dirs)


# Split by mode as in test_chatstore_recovery so no test nears the 120 s watchdog on slower runners.
@pytest.mark.parametrize("action, mode", [
    (rebase, "errors"), (rebase, "recovery-crashes"), (canon, "errors"), (canon, "recovery-crashes"),
    *(pytest.param(action, FAIL_RECOVERY, marks=EXHAUSTIVE_RECOVERY) for action in (rebase, canon)),
], ids=["rebase-errors", "rebase-recovery-crashes", "canon-errors", "canon-recovery-crashes",
        "rebase-fail-recovery-crashes", "canon-fail-recovery-crashes"])
def test_rebase_and_canon_commits_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 20
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])
