"""Where a read-only tool may write one report file. Synthetic homes under
``tmp_path`` only; the real user home and environment are never consulted."""

import os
from pathlib import Path

import pytest

from grimoire.library import LibraryPathError, V1IsolationError, check_report_destination

ENV: dict[str, str] = {}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h.resolve()


def v1_library(root: Path) -> Path:
    (root / "worlds").mkdir(parents=True)
    (root / "campaigns").mkdir()
    (root / "llm_connections").mkdir()
    return root


def check(path, home, **kwargs):
    return check_report_destination(path, environ=kwargs.pop("environ", ENV), home=home, **kwargs)


def test_a_report_beside_a_v1_library_is_allowed(home):
    v1_library(home / ".grimoire")
    target = check(home / "report.json", home)
    assert target == home / "report.json"
    assert not target.exists()


def test_a_report_beside_the_protected_source_is_allowed(tmp_path, home):
    source = v1_library(tmp_path / "frozen")
    target = check(tmp_path / "report.json", home, protected=[("the migration source", source)])
    assert target == tmp_path.resolve() / "report.json"


@pytest.mark.parametrize("where", ["inside", "nested"])
def test_a_report_inside_a_protected_root_is_refused(tmp_path, home, where):
    source = tmp_path / "frozen"
    (source / "worlds" / "w").mkdir(parents=True)
    parent = source if where == "inside" else source / "worlds" / "w"
    with pytest.raises(V1IsolationError, match="is inside the migration source"):
        check(parent / "report.json", home, protected=[("the migration source", source)])


def test_a_report_inside_the_default_v1_home_is_refused_even_when_it_is_not_the_source(tmp_path, home):
    v1_library(home / ".grimoire")
    with pytest.raises(V1IsolationError, match="default V1 library"):
        check(home / ".grimoire" / "report.json", home,
              protected=[("the migration source", tmp_path / "elsewhere")])


def test_a_report_inside_something_that_looks_like_v1_is_refused(tmp_path, home):
    lookalike = v1_library(tmp_path / "copy")
    with pytest.raises(V1IsolationError, match="looks like a V1 library"):
        check(lookalike / "report.json", home)


def test_a_report_inside_grimoire_home_is_refused(tmp_path, home):
    v1 = tmp_path / "v1"
    v1.mkdir()
    with pytest.raises(V1IsolationError, match="GRIMOIRE_HOME"):
        check(v1 / "r.json", home, environ={"GRIMOIRE_HOME": str(v1)})


def test_a_symlinked_parent_into_the_source_is_refused(tmp_path, home):
    source = tmp_path / "frozen"
    source.mkdir()
    link = tmp_path / "innocent"
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    with pytest.raises(V1IsolationError):
        check(link / "report.json", home, protected=[("the migration source", source)])


def test_a_case_variant_spelling_of_the_source_is_refused(tmp_path, home):
    source = tmp_path / "Frozen"
    source.mkdir()
    variant = tmp_path / "frozen"
    if not variant.exists():
        pytest.skip("this filesystem distinguishes case")
    with pytest.raises(V1IsolationError):
        check(variant / "report.json", home, protected=[("the migration source", source)])


def test_existing_files_need_overwrite_and_must_be_regular(tmp_path, home):
    existing = tmp_path / "report.json"
    existing.write_text("old", encoding="utf-8")
    with pytest.raises(LibraryPathError, match="already exists"):
        check(existing, home)
    assert check(existing, home, overwrite=True) == existing.resolve()
    (tmp_path / "dir").mkdir()
    with pytest.raises(LibraryPathError, match="not a regular file"):
        check(tmp_path / "dir", home, overwrite=True)


def test_an_existing_link_is_never_overwritten(tmp_path, home):
    target = tmp_path / "victim.json"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "report.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    with pytest.raises(LibraryPathError, match="not a regular file"):
        check(link, home, overwrite=True)


def test_the_parent_must_exist_and_nothing_is_created(tmp_path, home):
    with pytest.raises(LibraryPathError, match="does not exist"):
        check(tmp_path / "missing" / "report.json", home)
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("raw", ["relative.json", "", " /x.json"])
def test_unusable_spellings_are_refused(home, raw):
    with pytest.raises(LibraryPathError):
        check(raw, home)


def test_a_path_naming_no_file_is_refused(tmp_path, home):
    with pytest.raises(LibraryPathError, match="does not name a file"):
        check(os.path.join(str(tmp_path), ".."), home)
