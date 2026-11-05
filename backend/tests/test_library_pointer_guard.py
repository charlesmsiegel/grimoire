"""V1's bootstrap pointer ``~/.grimoire.json`` is guarded like a V1 library root.

Replacing it (with a migration report, say) would lose V1's relocated library; making it a
directory would leave every later resolve unable to read it. Synthetic homes only.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from grimoire.library import (
    LibraryPathError, V1IsolationError, check_report_destination, check_write_destination, resolve_library,
)
from grimoire.migrate_v1.__main__ import main

from v1_fixture import build_library

POINTER = ".grimoire.json"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h.resolve()


def _point(home: Path, library: Path) -> Path:
    pointer = home / POINTER
    pointer.write_text(json.dumps({"data_dir": str(library)}))
    return pointer


def test_a_migration_report_never_replaces_the_pointer(home, tmp_path):
    v1 = build_library(tmp_path / "relocated-v1")
    pointer = _point(home, v1)
    before = pointer.read_bytes()
    code = main(["scan", "--source", str(v1), "--archive-id", "a1", "--destination", str(tmp_path / "v2"),
                 "--report", str(pointer), "--overwrite", "--summary", "counts"], environ={}, home=home)
    assert code != 0
    assert pointer.read_bytes() == before


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("exists", [True, False], ids=["pointer", "no-pointer-yet"])
def test_the_pointer_is_never_a_report_destination(home, tmp_path, overwrite, exists):
    if exists:
        relocated = tmp_path / "v1lib"
        (relocated / "worlds").mkdir(parents=True)
        _point(home, relocated)
    with pytest.raises(V1IsolationError, match="bootstrap pointer"):
        check_report_destination(home / POINTER, overwrite=overwrite, environ={}, home=home)
    # A report beside it, in the same home, is fine.
    assert check_report_destination(home / "report.json", environ={}, home=home) == home / "report.json"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX names are case-sensitive; the guard is case-blind")
def test_a_case_alias_of_the_pointer_is_refused(home):
    with pytest.raises(V1IsolationError, match="bootstrap pointer"):
        check_report_destination(home / ".Grimoire.JSON", environ={}, home=home)


def test_a_link_to_the_pointer_is_refused(home, tmp_path):
    _point(home, tmp_path / "nowhere")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (elsewhere / "report.json").symlink_to(home / POINTER)
    except OSError:
        pytest.skip("symlinks need a privilege on this system")
    with pytest.raises((V1IsolationError, LibraryPathError)):
        check_report_destination(elsewhere / "report.json", overwrite=True, environ={}, home=home)


@pytest.mark.parametrize("below", [(), ("sub",), ("sub", "deeper")], ids=["itself", "child", "grandchild"])
def test_the_pointer_is_never_a_library_or_its_ancestor(home, below):
    target = home.joinpath(POINTER, *below)
    with pytest.raises(V1IsolationError, match="bootstrap pointer"):
        check_write_destination(target, environ={}, home=home)
    with pytest.raises(V1IsolationError, match="bootstrap pointer"):
        resolve_library(environ={"GRIMOIRE_V2_HOME": str(target)}, home=home)
    assert not (home / POINTER).exists()  # nothing was created


def test_resolving_still_works_after_a_refused_pointer_destination(home):
    with pytest.raises(V1IsolationError):
        check_write_destination(home / POINTER, environ={}, home=home)
    assert resolve_library(environ={}, home=home).path == home / ".grimoire-v2"
    assert os.listdir(home) == []
