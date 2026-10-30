"""``python -m grimoire.migrate_v1 scan``: exit codes, report publication and refusals."""

import json
import os
import stat

import pytest

from grimoire.migrate_v1 import __main__ as cli
from v1_fixture import build_library, tree_digest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    return h


@pytest.fixture
def library(tmp_path):
    return build_library(tmp_path / "v1")


def main(home, *args):
    return cli.main(["scan", *args], environ={}, home=home)


def scan_args(library, tmp_path, *extra):
    return ["--source", str(library), "--archive-id", "fixture", "--destination", str(tmp_path / "v2"), *extra]


def test_a_dry_run_prints_a_summary_and_writes_nothing(library, home, tmp_path, capsys):
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(home, *scan_args(library, tmp_path)) == 0
    assert "status: ready_for_review" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_the_report_is_written_privately_beside_the_source(library, home, tmp_path):
    report = tmp_path / "inventory.json"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 0
    manifest = json.loads(report.read_text(encoding="ascii"))
    assert manifest["status"] == "ready_for_review"
    if os.name == "posix":
        assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_an_existing_report_needs_overwrite(library, home, tmp_path):
    report = tmp_path / "inventory.json"
    report.write_text("keep", encoding="utf-8")
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 2
    assert report.read_text(encoding="utf-8") == "keep"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report), "--overwrite")) == 0
    assert json.loads(report.read_text(encoding="ascii"))["archive_id"] == "fixture"


@pytest.mark.parametrize("where", ["source", "destination", "directory"])
def test_f6_refused_report_locations(library, home, tmp_path, where):
    (tmp_path / "v2").mkdir()
    report = {"source": library / "r.json", "destination": tmp_path / "v2" / "r.json",
              "directory": tmp_path}[where]
    before = tree_digest(library)
    assert main(home, *scan_args(library, tmp_path, "--report", str(report), "--overwrite")) == 2
    assert tree_digest(library) == before
    assert list((tmp_path / "v2").iterdir()) == []


def test_f6_a_report_inside_the_default_v1_home_is_refused(library, home, tmp_path):
    (home / ".grimoire" / "worlds").mkdir(parents=True)
    report = home / ".grimoire" / "report.json"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 2
    assert not report.exists()


def test_f5_the_source_is_required_and_must_be_distinct(library, home, tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(home, "--archive-id", "fixture")
    assert error.value.code == 2
    assert main(home, "--source", str(library), "--archive-id", "fixture", "--destination", str(library)) == 3
    assert main(home, "--source", str(library), "--archive-id", "fixture",
                "--destination", str(library / "worlds" / "v2")) == 3
    (home / ".grimoire" / "worlds").mkdir(parents=True)
    assert main(home, "--source", str(library), "--archive-id", "fixture",
                "--destination", str(home / ".grimoire")) == 3
    assert not (library / "worlds" / "v2").exists()


@pytest.mark.parametrize("archive", ["Upper", "", "a b", "v1:x"])
def test_the_archive_id_follows_the_identity_grammar(library, home, tmp_path, archive):
    args = ["--source", str(library), "--archive-id", archive, "--destination", str(tmp_path / "v2")]
    assert main(home, *args) == 2


def test_f8_not_a_library(home, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert main(home, "--source", str(other), "--archive-id", "x", "--destination", str(tmp_path / "v2")) == 2


def test_a_blocked_inventory_exits_4_and_still_writes_its_report(library, home, tmp_path):
    (library / "config.md").write_text("---\ninference_format: 9\n---\n", encoding="utf-8")
    report = tmp_path / "inventory.json"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 4
    assert json.loads(report.read_text(encoding="ascii"))["status"] == "blocked"


def test_f7_a_live_source_exits_5_without_a_report(library, home, tmp_path, monkeypatch):
    from grimoire.migrate_v1 import _fs
    original = _fs.Snapshot.verify_unchanged

    def edit_then_verify(self):
        (library / "worlds" / "saltmarch" / "world.md").write_text("---\nname: Live\n---\n", encoding="utf-8")
        original(self)
    monkeypatch.setattr(_fs.Snapshot, "verify_unchanged", edit_then_verify)
    report = tmp_path / "inventory.json"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 5
    assert not report.exists()


def test_a_failed_publication_leaves_no_partial_report(library, home, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", broken)
    report = tmp_path / "inventory.json"
    assert main(home, *scan_args(library, tmp_path, "--report", str(report))) == 2
    assert not report.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_counts_summary_names_nothing(library, home, tmp_path, capsys):
    assert main(home, *scan_args(library, tmp_path, "--summary", "counts")) == 0
    out = capsys.readouterr().out
    assert "saltmarch" not in out and str(tmp_path) not in out
