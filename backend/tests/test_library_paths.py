"""V2 library location and V1 isolation. Every test runs against a synthetic
home under ``tmp_path``; the real user home and environment are never consulted."""

import os
from pathlib import Path

import pytest

from grimoire.library import (
    DEFAULT_DIRNAME, LibraryLocation, LibraryPathError, V1IsolationError,
    resolve_library,
)


def snapshot(root: Path) -> dict:
    """Every entry under `root` with enough metadata to see a create or rewrite."""
    seen = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            seen[os.path.relpath(path, root)] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return seen


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point HOME at an empty decoy, drop both library variables and work from
    `tmp_path`, so a resolver that consults the real process state is caught."""
    decoy = tmp_path / "decoy-home"
    decoy.mkdir()
    monkeypatch.setenv("HOME", str(decoy))
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    monkeypatch.delenv("GRIMOIRE_V2_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    assert list(decoy.iterdir()) == []


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h.resolve()


def v1_library(root: Path, *, young: bool = False) -> Path:
    """A synthetic V1 library: `young` has only what V1 creates on first run."""
    (root / "worlds" / "w").mkdir(parents=True)
    (root / "campaigns").mkdir()
    (root / "worlds" / "w" / "world.md").write_text("synthetic\n")
    if not young:
        (root / "llm_connections").mkdir()
        (root / "config.md").write_text("synthetic\n")
    return root


# ---- resolution ---------------------------------------------------------------

def test_default_is_grimoire_v2_under_home(home, tmp_path):
    before = snapshot(tmp_path)
    assert resolve_library(environ={}, home=home) == LibraryLocation(home / ".grimoire-v2", "default")
    assert DEFAULT_DIRNAME == ".grimoire-v2"
    assert snapshot(tmp_path) == before


def test_default_ignores_v1_home_variable(home, tmp_path):
    other = v1_library(tmp_path / "elsewhere")
    location = resolve_library(environ={"GRIMOIRE_HOME": str(other)}, home=home)
    assert location == LibraryLocation(home / ".grimoire-v2", "default")


def test_override_selects_absolute_path(home, tmp_path):
    target = tmp_path / "libs" / "mine"
    location = resolve_library(environ={"GRIMOIRE_V2_HOME": str(target)}, home=home)
    assert location == LibraryLocation(target.resolve(), "env")


def test_override_tilde_expands_against_injected_home(home):
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": "~/lib"}, home=home).path == home / "lib"
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": "~/lib/"}, home=home).path == home / "lib"


def test_missing_override_is_not_created(home, tmp_path):
    before = snapshot(tmp_path)
    target = tmp_path / "not" / "yet" / "here"
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": str(target)}, home=home).path == target.resolve()
    assert snapshot(tmp_path) == before


def test_empty_override_means_unset(home):
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": ""}, home=home).origin == "default"


@pytest.mark.parametrize("raw", ["relative/lib", " ", "/abs/lib\n", " /abs/lib", "~someone/lib"])
def test_malformed_override_is_rejected(home, raw):
    with pytest.raises(LibraryPathError) as error:
        resolve_library(environ={"GRIMOIRE_V2_HOME": raw}, home=home)
    assert not isinstance(error.value, V1IsolationError)


def test_override_naming_a_file_is_rejected(home, tmp_path):
    (tmp_path / "file").write_text("x")
    with pytest.raises(LibraryPathError, match="not a directory"):
        resolve_library(environ={"GRIMOIRE_V2_HOME": str(tmp_path / "file")}, home=home)


def test_override_symlink_is_followed(home, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": str(tmp_path / "alias")}, home=home).path == real.resolve()


def test_symlink_loop_fails_closed(home, tmp_path):
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    with pytest.raises(LibraryPathError):
        resolve_library(environ={"GRIMOIRE_V2_HOME": str(tmp_path / "loop" / "lib")}, home=home)


def test_relative_home_is_rejected():
    with pytest.raises(LibraryPathError):
        resolve_library(environ={}, home=Path("relative-home"))


def test_unknown_home_needs_an_override(monkeypatch, tmp_path):
    def no_home():
        raise RuntimeError("Could not determine home directory.")
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    with pytest.raises(LibraryPathError):
        resolve_library(environ={})
    target = tmp_path / "lib"
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": str(target)}).path == target.resolve()
    with pytest.raises(LibraryPathError):
        resolve_library(environ={"GRIMOIRE_V2_HOME": "~/lib"})


def test_android_has_no_default_yet(home, tmp_path):
    with pytest.raises(LibraryPathError, match="Android"):
        resolve_library(environ={}, home=home, platform="android")
    target = tmp_path / "lib"
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": str(target)}, home=home,
                           platform="android").path == target.resolve()


def test_defaults_read_process_environment(monkeypatch, tmp_path):
    decoy = Path(os.environ["HOME"]).resolve()
    assert resolve_library().path == decoy / ".grimoire-v2"
    monkeypatch.setenv("GRIMOIRE_V2_HOME", str(tmp_path / "lib"))
    assert resolve_library() == LibraryLocation((tmp_path / "lib").resolve(), "env")
