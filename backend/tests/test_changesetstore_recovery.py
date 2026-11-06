"""Applying a World Change Set is all-or-nothing under every crash and I/O error (Sprint 043).

The proving change set writes two definition revisions and the source manifest in one store
transaction. Under the 006 fault harness, recovery always yields either the library before the
apply or with the whole change set, never one record without the other, with no residue; the
unrelated record and the replaced revision are never touched.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from changeset_fixtures import library, move_the_guild, seed_world
from store_helpers import drop_empty_dirs, explore_mode

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            seed_world(store)
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def test_applying_a_change_set_survives_every_crash_and_error(tmp_path):
    locker = ProcessLocker()

    def apply(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            lib = library(store)
            lib.apply(lib.review(move_the_guild(lib)))

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    scenario = CrashScenario(
        name="change set apply", setup=_setup, action=apply, recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
                    completes_after(JOURNAL),
                    untouched("lib/sources/world/harbor/_source/records/character/*"),
                    untouched("lib/sources/world/harbor/_source/records/organization/ashen-guild/r1.json")),
        normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, **explore_mode("errors"))
    report.assert_ok()
    assert report.points_explored > 10
