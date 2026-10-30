"""The byte-level file seam and tree snapshots used by fault-injection tests."""

import os

import pytest

from grimoire.testing import OsFileOps, SandboxViolation, snapshot


def test_os_file_ops_round_trip(tmp_path):
    ops = OsFileOps()
    ops.make_dirs(tmp_path / "records" / "nested")
    ops.write_bytes(tmp_path / "records" / "b.json", b"{}")
    temp = ops.make_temp(tmp_path / "records", prefix="a.json.", suffix=".new")
    assert os.path.isabs(temp) and ops.read_bytes(temp) == b""
    ops.write_bytes(temp, b'{"v": 1}')
    ops.replace(temp, tmp_path / "records" / "a.json")
    assert not ops.exists(temp)
    assert ops.read_bytes(tmp_path / "records" / "a.json") == b'{"v": 1}'
    assert ops.list_dir(tmp_path / "records") == ["a.json", "b.json", "nested"]
    ops.remove(tmp_path / "records" / "b.json")
    assert not ops.exists(tmp_path / "records" / "b.json")


def test_snapshot_records_directories_and_posix_paths(tmp_path):
    (tmp_path / "campaign" / "scenes").mkdir(parents=True)
    (tmp_path / "campaign" / "scenes" / "one.md").write_bytes(b"synthetic")
    (tmp_path / "empty").mkdir()
    assert snapshot(tmp_path) == {
        "campaign": None,
        "campaign/scenes": None,
        "campaign/scenes/one.md": b"synthetic",
        "empty": None,
    }


def test_snapshot_refuses_links_inside_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(SandboxViolation):
        snapshot(root)
