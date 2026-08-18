"""The decision table in scripts/grimoire_sync.py, which decides what gets
overwritten on a phone or a PC full of campaign data.

Everything here is pure: the planner takes three dicts of hashes and returns
what to copy where. No adb, no device, no filesystem -- which is the point, as
the interesting cases (both sides edited, one side deleted, no common ancestor)
are the ones that are miserable to stage against real hardware and the ones
where being wrong costs a scene nobody can regenerate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import grimoire_sync as gs


def plan(pc, phone, base=None, conflicts=None):
    return gs.plan_sync(pc, phone, base or {}, conflicts or {})


# ---------------------------------------------------------------- the table


def test_identical_files_are_left_alone():
    p = plan({"a.md": "h1"}, {"a.md": "h1"})
    assert (p.to_pc, p.to_phone, p.conflicts) == ([], [], [])
    assert p.in_sync == 1


def test_a_file_only_the_phone_has_is_copied_to_the_pc():
    p = plan({}, {"a.md": "h1"})
    assert p.to_pc == ["a.md"]
    assert p.to_phone == []


def test_a_file_only_the_pc_has_is_copied_to_the_phone():
    p = plan({"a.md": "h1"}, {})
    assert p.to_phone == ["a.md"]
    assert p.to_pc == []


def test_only_the_phone_moved_since_the_baseline():
    p = plan({"a.md": "old"}, {"a.md": "new"}, {"a.md": "old"})
    assert p.to_pc == ["a.md"]
    assert p.conflicts == []


def test_only_the_pc_moved_since_the_baseline():
    p = plan({"a.md": "new"}, {"a.md": "old"}, {"a.md": "old"})
    assert p.to_phone == ["a.md"]
    assert p.conflicts == []


def test_both_moved_since_the_baseline_is_a_conflict():
    p = plan({"a.md": "mine"}, {"a.md": "theirs"}, {"a.md": "old"})
    assert p.conflicts == ["a.md"]
    assert (p.to_pc, p.to_phone) == ([], [])


def test_differing_with_no_baseline_is_a_conflict_not_a_guess():
    """The first sync has no common ancestor, so neither side is 'fresher'.

    Picking one would silently discard the other; the whole point of the
    baseline is that without it there is nothing to base a choice on.
    """
    p = plan({"a.md": "mine"}, {"a.md": "theirs"})
    assert p.conflicts == ["a.md"]


# --------------------------------------------------------------- deletions


def test_a_file_deleted_on_the_phone_comes_back_rather_than_deleting_on_the_pc():
    """Deletions never propagate -- 'gone here' and 'new there' are the same
    observation, and only one of the two readings is recoverable."""
    p = plan({"a.md": "h1"}, {}, {"a.md": "h1"})
    assert p.to_phone == ["a.md"]
    assert p.to_pc == []


def test_a_file_deleted_on_the_pc_comes_back_from_the_phone():
    p = plan({}, {"a.md": "h1"}, {"a.md": "h1"})
    assert p.to_pc == ["a.md"]


# ------------------------------------------------------- standing conflicts


def test_an_unchanged_conflict_is_not_copied_a_second_time():
    p = plan(
        {"a.md": "mine"}, {"a.md": "theirs"},
        {}, {"a.md": ["mine", "theirs"]},
    )
    assert p.pending == ["a.md"]
    assert p.conflicts == []


def test_a_conflict_reopens_once_either_side_moves_again():
    p = plan(
        {"a.md": "mine2"}, {"a.md": "theirs"},
        {}, {"a.md": ["mine", "theirs"]},
    )
    assert p.conflicts == ["a.md"]
    assert p.pending == []


def test_a_conflict_resolved_to_the_same_content_is_simply_in_sync():
    p = plan(
        {"a.md": "agreed"}, {"a.md": "agreed"},
        {}, {"a.md": ["mine", "theirs"]},
    )
    assert p.in_sync == 1
    assert (p.conflicts, p.pending) == ([], [])


# ------------------------------------------------------------- exclusions


@pytest.mark.parametrize("rel", [
    ".cache/thumb.png",
    "backups/archive.zip",
    ".git/config",
    "worlds/realm/__pycache__/mod.cpython-312.pyc",
    "calendars/plugin.pyc",
    "worlds/realm/world.sync-conflict-20260101-abc.md",
    "worlds/realm/world.md.orig",
    ".usb_readable",
])
def test_excluded_paths(rel):
    assert gs._excluded(gs.PurePosixPath(rel)) is True


@pytest.mark.parametrize("rel", [
    "worlds/realm/world.md",
    "campaigns/saltmarch/scenes/1/scene.md",
    "calendars/plugin.py",
    ".char_macros_baked",
    # `backups` and `.cache` are reserved at the store root only, so a world
    # that happens to be called one of them still syncs.
    "worlds/backups/world.md",
    "worlds/.cache/world.md",
])
def test_included_paths(rel):
    assert gs._excluded(gs.PurePosixPath(rel)) is False


# ---------------------------------------------------------- conflict names


def test_a_conflict_copy_keeps_its_suffix_and_matches_the_apps_pattern():
    import fnmatch

    name = gs.conflict_name("worlds/realm/world.md", "20260818-120000", "SERIAL1")
    assert name == "worlds/realm/world.sync-conflict-20260818-120000-SERIAL1.md"
    # store/external.py's syncthing rule, which is what puts these in the
    # Configuration page's conflict list.
    assert fnmatch.fnmatch(Path(name).name.lower(), "*.sync-conflict-*")


def test_a_conflict_copy_of_an_extensionless_file_still_matches():
    name = gs.conflict_name("notes", "20260818-120000", "S")
    assert name == "notes.sync-conflict-20260818-120000-S"


# ---------------------------------------------------------------- baseline


def test_a_baseline_is_ignored_when_it_describes_other_directories(tmp_path, monkeypatch):
    """Roots are part of the baseline's identity.

    Reused across a different pairing, 'matches the baseline' would read as
    'the other side deleted it' for files that were simply never there.
    """
    monkeypatch.setattr(gs, "BASELINE_DIR", tmp_path)
    gs.save_baseline("S1", Path("/one"), "/phone/one", {"a.md": "h"}, {})

    files, conflicts = gs.load_baseline("S1", Path("/one"), "/phone/one")
    assert files == {"a.md": "h"}

    files, conflicts = gs.load_baseline("S1", Path("/two"), "/phone/one")
    assert (files, conflicts) == ({}, {})
    files, conflicts = gs.load_baseline("S1", Path("/one"), "/phone/two")
    assert (files, conflicts) == ({}, {})


def test_a_corrupt_baseline_reads_as_no_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "BASELINE_DIR", tmp_path)
    gs.baseline_path("S1").write_text("{not json", encoding="utf-8")
    assert gs.load_baseline("S1", Path("/one"), "/p") == ({}, {})


def test_a_serial_cannot_escape_the_baseline_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "BASELINE_DIR", tmp_path)
    path = gs.baseline_path("../../etc/passwd")
    assert path.parent == tmp_path


def test_saved_baselines_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "BASELINE_DIR", tmp_path)
    gs.save_baseline("S1", Path("/one"), "/p", {"a.md": "h"}, {"b.md": ["x", "y"]})
    written = json.loads(gs.baseline_path("S1").read_text(encoding="utf-8"))
    assert written["files"] == {"a.md": "h"}
    assert written["conflicts"] == {"b.md": ["x", "y"]}
    assert written["pc_root"] == str(Path("/one"))


# ------------------------------------------------------------------ quoting


def test_device_paths_with_a_quote_survive_the_shell():
    assert gs.quote("/sdcard/a'b") == "'/sdcard/a'\\''b'"
