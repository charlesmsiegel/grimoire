"""One image batch is all-or-nothing under every crash, I/O error and recovery crash (Sprint 018, F12).

The errors-mode batch is exactly F12's: a manifest, a definition, a new blob, its image object
and a placement, so a crash can never leave a placement without its image or blob. Recovery
crashes are explored on a two-put batch (blob + image): it settles through the same engine table
as the five-put batch, as in 107's recovery test, and keeps each run far from the Windows
watchdog (recovery crashes on the five-put batch take ~35 s on Linux, several times that on Windows).
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import ImageRef, SourceRef
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, SourceManifest
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from image_fixtures import PNG, handle
from store_helpers import OWNER, drop_empty_dirs

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
CARD = "card:x"
HERO = SourceRef(CARD, "character", "hero")


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """An empty library, built once by the real store (snapshots scale with the tree)."""
    with tempfile.TemporaryDirectory() as folder:
        LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()).close()
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def import_card(ops, root, locker):
    """F12: manifest + definition + blob + image + placement in one batch."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        batch = ImageLibrary(store, handle(), owner=OWNER).change()
        batch.put(SourceManifest(CARD, OWNER), None)
        batch.put(RecordEnvelope(HERO.at("r1"), {}), None)
        image = batch.add_image(CARD, "hero", PNG)
        batch.place(HERO, "avatar", Placement("set", "avatar", image), None)
        batch.commit()


def add_picture(ops, root, locker):
    """Blob + image only (the source exists): the recovery-crash scenario."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        ImageLibrary(store, handle(), owner=OWNER).add_image(CARD, "hero", PNG).commit()


@functools.cache
def _with_source() -> tuple[tuple[str, bytes | None], ...]:
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            txn = store.transaction()
            txn.put(SourceManifest(CARD, OWNER), None)
            txn.commit(handle())
        return tuple(snapshot(folder).items())


def _setup_with_source(ops, root):
    for key, data in _with_source():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def scenario(action, setup):
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name=f"images {action.__name__}", setup=setup, action=lambda ops, root: action(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
                                     untouched("lib/campaigns/*")),
        normalize=drop_empty_dirs)


@pytest.mark.parametrize("action, setup, mode", [
    (import_card, _setup, "errors"), (add_picture, _setup_with_source, "recovery-crashes"),
], ids=["import-errors", "picture-recovery-crashes"])
def test_image_batches_survive_every_crash_error_and_recovery_crash(tmp_path, action, setup, mode):
    report = explore_crashes(scenario(action, setup), tmp_path, errors=mode == "errors",
                             recovery_crashes=mode == "recovery-crashes")
    report.assert_ok()
    assert report.points_explored > 20
    if mode == "recovery-crashes":
        assert any(run.recover_trace for run in report.runs[1:])


def test_the_completed_import_shows_its_avatar(tmp_path):
    from grimoire.testing import RootedFileOps
    locker = ProcessLocker()
    ops = RootedFileOps(tmp_path, record=False)
    _setup(ops, tmp_path)
    import_card(ops, tmp_path, locker)
    with LibraryStore.open(tmp_path / LIB, locker=ProcessLocker()) as store:
        shown = ImageLibrary(store, handle()).effective_placements(HERO).slot("avatar")
        assert shown.image == ImageRef(CARD, "hero")
