"""A V1 import apply survives every crash, I/O error and recovery crash (Sprint 026).

The first apply of the base V1 fixture writes one world's definitions, the campaign scope, its actors, the scene's chat,
the pictures and the receipt as one store transaction. Under the 006 fault harness, recovery
always yields either the empty library or the completed import, with no residue, recovering
again changes nothing, and the V1 source is never touched. The manifest is reviewed once,
against the harness's fixed root, so every run plans from the same reviewed file.
"""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path

import pytest

from grimoire.import_v1 import Selection, plan_import
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    untouched,
)
from grimoire.testing.fileops import snapshot

from image_fixtures import handle
from import_fixtures import CAMPAIGN_ID, CLOCK, SALTMARCH, reviewed_manifest
from store_helpers import EXHAUSTIVE_RECOVERY, FAIL_RECOVERY, OWNER, drop_empty_dirs, explore_mode
from v1_fixture import build_library

LIB, V1 = "lib", "v1"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
#: One world and its campaign: snapshots and fault points scale with the tree, and Realm adds no new path.
SELECTION = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    """The V1 library and an empty V2 library, built once by the real writers."""
    with tempfile.TemporaryDirectory() as folder:
        build_library(Path(folder) / V1)
        LibraryStore.create(Path(folder) / LIB).close()
        return tuple(snapshot(folder).items())


@functools.cache
def _manifest(root: str, home: str) -> str:
    """011's reviewed manifest for the template, with the harness root's library as destination."""
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / V1
        for key, data in _template():
            if key.startswith(V1 + "/") and data is not None:
                path = Path(folder).joinpath(*key.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        return json.dumps(reviewed_manifest(source, Path(root) / LIB, Path(home)))


def _setup(ops, root):
    for key, data in _template():
        if data is None:
            ops.make_dirs(root / key)
        else:
            ops.write_bytes(root / key, data)


def scenario(home: Path):
    locker = ProcessLocker()

    def apply(ops, root):
        manifest = json.loads(_manifest(str(root), str(home)))
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            plan_import(store, root / V1, manifest, handle=handle(), owner=OWNER, selection=SELECTION,
                        clock=lambda: CLOCK, home=home, environ={}).apply()

    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()

    return CrashScenario(
        name="v1 import apply", setup=_setup, action=apply, recover=recover,
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL), untouched("v1/*")),
        normalize=drop_empty_dirs)


#: ``crashes`` and ``fails`` split ``errors`` (crash and I/O-error plans) in two disjoint halves so neither
#: nears the 120 s watchdog on Windows; recovery crashes run only with GRIMOIRE_EXHAUSTIVE_RECOVERY=1.
MODES = {
    "crashes": explore_mode("crashes"),
    "fails": {"errors": True, "select": lambda op: {"fail"}},
    "recovery-crashes": explore_mode("recovery-crashes"),
    FAIL_RECOVERY: explore_mode(FAIL_RECOVERY),
}


@pytest.mark.parametrize("mode", [
    "crashes", "fails", *(pytest.param(mode, marks=EXHAUSTIVE_RECOVERY) for mode in ("recovery-crashes", FAIL_RECOVERY)),
])
def test_an_import_apply_survives_every_crash_error_and_recovery_crash(tmp_path, mode):
    home = tmp_path / "home"
    home.mkdir()
    report = explore_crashes(scenario(home), tmp_path / "work", **MODES[mode])
    report.assert_ok()
    assert report.points_explored > 20
    if mode in ("recovery-crashes", FAIL_RECOVERY):
        assert any(run.recover_trace for run in report.runs[1:])
