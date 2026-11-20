"""Recording a roll under crashes, I/O errors and crashed recoveries (Sprint 075).

``RollLibrary.roll`` appends one authority-ledger batch, and only returns the faces once it is on disk.
Under the 006 fault harness, recovery always leaves one of two states, read back through the real library:
no roll (nothing anyone saw was drawn) or the roll that was drawn, never another face and never a torn
batch read as a roll. A retry after recovery reads the recorded roll back and draws nothing.
"""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import SourceRef
from grimoire.rollstore import RollLibrary
from grimoire.rulesets import ActionRequest, D20Check, ModuleBinding, RulesetModule
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import CrashScenario, Invariant, atomic_outcome, explore_crashes, idempotent_recovery, no_residue
from grimoire.testing.fileops import snapshot

from adjudication_fixtures import ANA, MARA, Q, Table, build_library, vault
from store_helpers import FAIL_RECOVERY, drop_empty_dirs, explore_mode

LIB = "lib"
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
D20 = ModuleBinding("d20", "5e-2024")
MODULE = RulesetModule(SourceRef("synthetic", "ruleset", "checks", "v1"), (D20,))
SNEAK = ActionRequest("c10", MARA, MODULE, D20, D20Check("dexterity", "stealth", 15, 3))


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """The seated table, built once by the real store."""
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / LIB, locker=ProcessLocker()) as store:
            build_library(store)
            Table(store).seat().close()
        return tuple(snapshot(folder).items())


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def _library(table: Table, faces: int) -> RollLibrary:
    def draw(sides: int) -> int:
        return faces
    return RollLibrary(table.store, table.authority, table.authority.handle_for("app:roller", trusted=True),
                       draw=draw)


@pytest.mark.parametrize("mode", ["errors", "recovery-crashes", FAIL_RECOVERY])
def test_a_roll_is_on_record_whole_or_not_at_all(tmp_path, mode):
    locker = ProcessLocker()
    observed: list[object] = []
    seen: set[object] = set()

    def action(ops, root):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = Table(store)
            try:
                _library(table, 4).roll(MODULE, SNEAK, contribution=vault(), author=table.player(ANA))
            finally:
                table.close()

    def recover(ops, root):  # reopening recovers the store; the read writes nothing
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            table = Table(store)
            try:
                roll = _library(table, 20).rolled(Q, "c10")
            finally:
                table.close()
        observed.clear()
        observed.append(None if roll is None else (roll.faces, roll.outcome))

    def drawn_or_nothing(outcome):
        if not observed:
            return "recovery read nothing back"
        if observed[-1] not in (None, ((4,), "failure")):
            return f"the library read back {observed[-1]}"
        seen.add(observed[-1])
        return None

    scenario = CrashScenario(
        name="roll", setup=_setup, action=action, recover=recover,
        invariants=(Invariant("drawn_or_nothing", drawn_or_nothing), atomic_outcome(), idempotent_recovery(),
                    RESIDUE),
        normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored > 10
    if mode == "errors":  # each state really occurs: an error before the batch lands, and after it
        assert seen == {None, ((4,), "failure")}
