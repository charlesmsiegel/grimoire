"""Multi-record chat writes survive every crash, I/O error and recovery crash (Sprint 107, F10).

``start_chat`` writes a play scope, a chat and an actor instance; ``attach_source`` rewrites a
chat and creates an instance. Under the 006 fault harness, recovery always yields either the
state before the action or the completed one, with no residue, and recovering again changes
nothing. Clocks and ids are rebuilt inside every run, so the action is deterministic.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from grimoire.store import RecordEnvelope, SourceManifest

from chat_fixtures import ANYA, CARDS, LORE, MARA, Clock, Ids
from store_helpers import OWNER, drop_empty_dirs, owner_handle

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
EXISTING = ChatRef("q-1", "t-1")


def _library(store: LibraryStore) -> ChatLibrary:
    return ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """Three definitions plus one existing chat (q-1/t-1), built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            txn = store.transaction()  # the smallest tree that covers the action: snapshots scale with it
            txn.put(SourceManifest(CARDS, OWNER), None)
            for ref in (MARA, ANYA, LORE):
                txn.put(RecordEnvelope(ref, {}), None)
            txn.commit(owner_handle())
            assert _library(store).start_chat(Launch("character", MARA)).commit().chat == EXISTING
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def start_chat(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        library = _library(store)
        library._new_id.counts["q"] = 1  # q-1 exists in the template
        library.start_chat(Launch("character", ANYA), attach=[(LORE, "lorebook")]).commit()


def attach_character(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        library = _library(store)
        _, etag = library.read_chat(EXISTING)
        library.attach_source(EXISTING, ANYA, "character", expected=etag).commit()


def scenario(action):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"chat {action.__name__}", setup=_setup, action=lambda ops, root: action(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                                     untouched("lib/sources/*")),
        normalize=drop_empty_dirs)


# Split as in test_store_recovery so no test nears the 120 s watchdog on slower Windows runners.
# Recovery crashes are explored on the two-record attach; it settles through the same engine
# table as the three-record start, whose errors and crashes are explored separately.
@pytest.mark.parametrize("action, mode", [
    (start_chat, "errors"), (attach_character, "errors"), (attach_character, "recovery-crashes"),
], ids=["start-errors", "attach-errors", "attach-recovery-crashes"])
def test_chat_writes_survive_every_crash_error_and_recovery_crash(tmp_path, action, mode):
    report = explore_crashes(scenario(action), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 20
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])
