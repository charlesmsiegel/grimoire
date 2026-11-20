"""Every mechanics-store write (086) explored under the fault harness (docs/TESTING.md): a crash, an I/O error or a
crashing recovery leaves the old file or the new one, never a torn or partial one, and recovery leaves no temp file."""

from __future__ import annotations

import json

import pytest
from store_helpers import FAIL_RECOVERY, drop_empty_dirs, explore_mode

from grimoire.mechanics import template
from grimoire.mechanicsstore import MechanicsStore
from grimoire.testing import (
    CrashScenario, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
)

DRAFT = "config/mechanics/drafts/pool_basic.json"


def _doc(difficulty: int) -> bytes:
    document = template("pool_basic")
    document["checks"]["brawl"]["difficulty"] = difficulty  # type: ignore[index]
    return json.dumps(document).encode()


def _empty(ops, root):  # type: ignore[no-untyped-def]
    ops.write_bytes(root / "grimoire-library.json", b"{}\n")


def _seed(ops, root):  # type: ignore[no-untyped-def]
    _empty(ops, root)
    store = MechanicsStore(root, ops=ops)
    store.publish("pool_basic", store.save_draft("pool_basic", _doc(6), None))


def first_draft(ops, root):  # type: ignore[no-untyped-def]
    MechanicsStore(root, ops=ops).save_draft("pool_basic", _doc(6), None)


def replace_draft(ops, root):  # type: ignore[no-untyped-def]
    store = MechanicsStore(root, ops=ops)
    store.save_draft("pool_basic", _doc(7), store.draft("pool_basic").etag)  # type: ignore[union-attr]


def _edited(ops, root):  # type: ignore[no-untyped-def]
    _seed(ops, root)
    store = MechanicsStore(root, ops=ops)
    store.save_draft("pool_basic", _doc(8), store.draft("pool_basic").etag)  # type: ignore[union-attr]


def publish_edit(ops, root):  # type: ignore[no-untyped-def]
    store = MechanicsStore(root, ops=ops)
    store.publish("pool_basic", store.draft("pool_basic").etag)  # type: ignore[union-attr]


def _recover(ops, root):  # type: ignore[no-untyped-def]
    store = MechanicsStore(root, ops=ops)
    store.recover()
    for revision in store.revisions("pool_basic"):
        store.load("pool_basic", revision)  # every revision file that exists is whole and verifies


SCENARIOS = {
    "first-draft": (first_draft, _empty, Target("replace", target=DRAFT)),
    "replace-draft": (replace_draft, _seed, Target("replace", target=DRAFT)),
    "publish-edit": (publish_edit, _edited, Target("replace", target="config/mechanics/modules/pool_basic/*.json")),
}


def _scenario(name: str) -> CrashScenario:
    action, setup, commit = SCENARIOS[name]
    return CrashScenario(name=f"mechanics {name}", setup=setup, action=action, recover=_recover,
                         invariants=(atomic_outcome(), idempotent_recovery(), no_residue("*.tmp"),
                                     completes_after(commit)),
                         normalize=drop_empty_dirs)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("mode", ["errors", "recovery-crashes", FAIL_RECOVERY])
def test_mechanics_writes_survive_every_crash_error_and_recovery_crash(tmp_path, name, mode):
    report = explore_crashes(_scenario(name), tmp_path, **explore_mode(mode))
    report.assert_ok()
    assert report.points_explored >= 3
