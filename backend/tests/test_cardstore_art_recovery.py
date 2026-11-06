"""A card import that carries art survives every crash, I/O error and recovery crash (Sprint 038).

The import writes the card's source, its character and its import record together with a
portrait, an inline sprite (named in a script with no ASCII letters, so its slot is the name's
hash), a sprite whose picture is remote (an ``unresolved`` placement) and their blobs, as one
image batch. Under the 006 fault harness, recovery always yields either the library before the
import or the completed import, with no residue, and recovering again changes nothing: a crash
never leaves a placement without its picture, or a picture without its blob.
"""

from __future__ import annotations

import base64
import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import SourceRef
from grimoire.images import Shown, Unavailable
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, RootedFileOps, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue,
)
from grimoire.testing.fileops import snapshot

from card_fixtures import PORTRAIT, card, encode, land
from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, drop_empty_dirs, explore_mode, owner_handle

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
MARA = SourceRef("card:mara-vell", "character", "mara-vell")
SMILE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic smile"
SMILE = "笑顔"  # a sprite name with no ASCII letter or digit
ASSETS = [
    {"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
    {"type": "emotion", "uri": f"data:image/png;base64,{base64.b64encode(SMILE_PNG).decode('ascii')}",
     "name": SMILE, "ext": "png"},
    {"type": "emotion", "uri": "https://example.invalid/shy.png", "name": "shy", "ext": "png"},
]
CARD = encode(card(assets=ASSETS))


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


def _import(ops, root, locker):
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        land(store, CARD, portrait=PORTRAIT)  # an untrusted worker, approved path by path


def scenario():
    locker = ProcessLocker()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name="card import with art", setup=_setup, action=lambda ops, root: _import(ops, root, locker),
        recover=recover, invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL)),
        normalize=drop_empty_dirs)


#: ``crashes`` and ``fails`` split ``errors`` so neither nears the Windows watchdog; recovery crashes
#: run only with GRIMOIRE_EXHAUSTIVE_RECOVERY=1.
MODES = {
    "crashes": explore_mode("crashes"),
    "fails": {"errors": True, "select": lambda op: {"fail"}},
    "recovery-crashes": explore_mode("recovery-crashes"),
    FAIL_RECOVERY: explore_mode(FAIL_RECOVERY),
}


@pytest.mark.parametrize("mode", [
    "crashes", "fails", *(pytest.param(mode, marks=EXHAUSTIVE_RECOVERY) for mode in ("recovery-crashes", FAIL_RECOVERY)),
])
def test_a_card_import_with_art_survives_every_crash_error_and_recovery_crash(tmp_path, mode):
    report = explore_crashes(scenario(), tmp_path / "work", **MODES[mode])
    report.assert_ok()
    assert report.points_explored > 20
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])


def test_the_completed_import_shows_its_art(tmp_path):
    """The baseline the explorer recovers to: every slot placed, the missing sprite unresolved."""
    ops = RootedFileOps(tmp_path, record=False)
    _setup(ops, tmp_path)
    _import(ops, tmp_path, ProcessLocker())
    with LibraryStore.open(tmp_path / LIB, locker=ProcessLocker()) as store:
        images = ImageLibrary(store, owner_handle("sources", "assets"))
        shown = images.effective_placements(MARA)
        sprites = {slot.slot: slot for slot in shown.slots if slot.slot.startswith("expr.")}
        assert set(sprites) == {"expr.default.emotion-b80c40ec", "expr.default.shy"}
        smile = sprites["expr.default.emotion-b80c40ec"]
        assert isinstance(smile, Shown) and images.read_image_bytes(smile.image) == SMILE_PNG
        assert isinstance(sprites["expr.default.shy"], Unavailable) and sprites["expr.default.shy"].reason == "unresolved"
        assert images.read_image_bytes(shown.slot("avatar").image) == PORTRAIT
