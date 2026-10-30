"""V2 library location and V1 isolation. Every test runs against a synthetic
home under ``tmp_path``; the real user home and environment are never consulted."""

import json
import os
import sys
from pathlib import Path

import pytest

from grimoire.library import (
    DEFAULT_DIRNAME, LibraryLocation, LibraryPathError, V1IsolationError,
    check_migration_paths, check_write_destination,
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


def test_android_is_detected_on_pythons_that_report_linux(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "getandroidapilevel", lambda: 34, raising=False)
    with pytest.raises(LibraryPathError, match="Android"):
        resolve_library(environ={}, home=home)


def test_defaults_read_process_environment(monkeypatch, tmp_path):
    decoy = Path(os.environ["HOME"]).resolve()
    assert resolve_library().path == decoy / ".grimoire-v2"
    monkeypatch.setenv("GRIMOIRE_V2_HOME", str(tmp_path / "lib"))
    assert resolve_library() == LibraryLocation((tmp_path / "lib").resolve(), "env")


# ---- V1 isolation -------------------------------------------------------------

from grimoire.library import paths  # noqa: E402  -- private helpers for the identity cases


def refused(home, override, environ=None, match=None):
    with pytest.raises(V1IsolationError, match=match):
        resolve_library(environ={"GRIMOIRE_V2_HOME": str(override), **(environ or {})}, home=home)


@pytest.mark.parametrize("override", ["~/.grimoire", "~/.grimoire/v2", "~", "~/.GRIMOIRE", "~/.Grimoire/x"])
def test_override_into_default_v1_library_is_refused(home, override):
    v1_library(home / ".grimoire")
    refused(home, override)


def test_v1_default_is_refused_even_before_it_exists(home):
    refused(home, "~/.grimoire", match="is the default V1 library")


def test_symlink_into_v1_is_refused(home, tmp_path):
    v1_library(home / ".grimoire")
    (tmp_path / "innocent").symlink_to(home / ".grimoire")
    refused(home, tmp_path / "innocent" / "v2", match="is inside the default V1 library")


def test_sibling_prefix_is_not_inside_v1(home):
    v1_library(home / ".grimoire")
    (home / ".grimoire-v2").mkdir()
    assert resolve_library(environ={}, home=home).path == home / ".grimoire-v2"


@pytest.mark.parametrize("override", ["{v1}", "{v1}/sub", "{v1}/.."])
def test_v1_home_variable_is_guarded(home, tmp_path, override):
    v1 = v1_library(tmp_path / "v1lib")
    refused(home, override.format(v1=v1), {"GRIMOIRE_HOME": str(v1)})


def test_v1_home_variable_relative_and_tilde_forms_are_guarded(home, tmp_path):
    # V1 resolved `Path(env)` against its cwd; a `~` form is guarded expanded too.
    refused(home, tmp_path / "rel-v1" / "x", {"GRIMOIRE_HOME": "rel-v1"})
    refused(home, home / "v1", {"GRIMOIRE_HOME": "~/v1"})


def test_v1_library_at_home_refuses_the_default(home):
    with pytest.raises(V1IsolationError, match="is inside the V1 library in GRIMOIRE_HOME"):
        resolve_library(environ={"GRIMOIRE_HOME": str(home)}, home=home)


def test_unicode_normalization_is_ignored(home, tmp_path):
    nfc, nfd = "café", "café"
    refused(home, tmp_path / nfd / "x", {"GRIMOIRE_HOME": str(tmp_path / nfc)})


def test_existing_v1_default_does_not_block_missing_v2_default(home):
    v1_library(home / ".grimoire")
    assert not (home / ".grimoire-v2").exists()
    assert resolve_library(environ={}, home=home).origin == "default"


# Identity: what names cannot see. `resolve` undoes a symlink before the lexical
# test, so these drive the helper with unresolved aliases, as a bind mount or a
# case-insensitive disk would present them.

def test_identity_sees_a_path_inside_an_aliased_root(tmp_path):
    root = tmp_path / "root"
    (root / "inner").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(root)
    assert paths._lexical_relation(tmp_path / "alias" / "inner", root) is None
    assert paths._identity_relation(tmp_path / "alias" / "inner", root) == "is inside"
    assert paths._identity_relation(tmp_path / "alias", root) == "is"


def test_identity_sees_a_path_containing_an_aliased_root(tmp_path):
    (tmp_path / "outer" / "root").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "outer")
    assert paths._identity_relation(tmp_path / "alias", tmp_path / "outer" / "root") == "contains"


def test_identity_check_is_part_of_the_isolation_check(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "alias").symlink_to(root)
    with pytest.raises(V1IsolationError, match="it is the probe"):
        paths._check_isolation(tmp_path / "alias", [("the probe", root)])


# Fingerprint: a V1 library that is not at any guarded root.

def test_relocated_v1_library_is_refused_by_its_layout(home, tmp_path):
    lib = v1_library(tmp_path / "copied-v1")
    refused(home, lib, match="looks like a V1 library")
    refused(home, lib / "worlds" / "v2", match="looks like a V1 library")


def test_marker_without_v1_layout_is_not_v1(home, tmp_path):
    lib = tmp_path / "presets"
    (lib / "llm_connections").mkdir(parents=True)
    assert resolve_library(environ={"GRIMOIRE_V2_HOME": str(lib)}, home=home).path == lib.resolve()


def test_common_files_in_home_are_not_mistaken_for_v1(home):
    (home / "config.md").write_text("notes\n")  # common, so never a V1 marker
    (home / "worlds").mkdir()                    # a game's save folder, say
    (home / "regex.json").write_text("{}\n")
    (home / "worlds").rmdir()                    # a V1 marker with no V1 layout beside it
    assert resolve_library(environ={}, home=home).path == home / ".grimoire-v2"
    (home / "regex.json").unlink()
    (home / "worlds").mkdir()
    assert resolve_library(environ={}, home=home).path == home / ".grimoire-v2"


def test_dangling_marker_link_still_counts(home, tmp_path):
    lib = tmp_path / "v1"
    (lib / "campaigns").mkdir(parents=True)
    (lib / "chores.json").symlink_to(tmp_path / "missing")
    refused(home, lib, match="looks like a V1 library")


# The V1 bootstrap pointer: read only to refuse what it names.

def write_pointer(home, payload):
    (home / ".grimoire.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))


def test_pointer_names_a_young_v1_library(home, tmp_path):
    young = v1_library(tmp_path / "Sync" / "grimoire", young=True)
    write_pointer(home, {"data_dir": str(young)})
    refused(home, young, match="named by ~/.grimoire.json")
    refused(home, young / "v2", match="named by ~/.grimoire.json")


def test_pointer_tilde_is_expanded_against_home(home):
    write_pointer(home, {"data_dir": "~/young"})
    refused(home, home / "young" / "v2", match="named by ~/.grimoire.json")


def test_pointer_is_never_a_v2_source(home, tmp_path):
    write_pointer(home, {"data_dir": str(tmp_path / "v1")})
    assert resolve_library(environ={}, home=home).path == home / ".grimoire-v2"


@pytest.mark.parametrize("payload", [{}, {"data_dir": ""}, {"data_dir": None}, {"other": 1}])
def test_pointer_without_data_dir_adds_nothing(home, payload):
    write_pointer(home, payload)
    assert resolve_library(environ={}, home=home).origin == "default"


@pytest.mark.parametrize("payload", ["not json", "[]", '{"data_dir": 5}', '{"data_dir": "rel/dir"}',
                                     '{"data_dir": "~else/x"}', b"\xff\xfe"])
def test_unusable_pointer_fails_closed(home, payload):
    if isinstance(payload, bytes):
        (home / ".grimoire.json").write_bytes(payload)
    else:
        write_pointer(home, payload)
    with pytest.raises(V1IsolationError, match=r"\.grimoire\.json"):
        resolve_library(environ={}, home=home)


def test_pointer_that_is_not_a_regular_file_is_never_opened(home):
    os.mkfifo(home / ".grimoire.json")  # opening a FIFO would block forever
    with pytest.raises(V1IsolationError, match="not a small regular file"):
        resolve_library(environ={}, home=home)


# Fail closed when the filesystem will not answer.

def test_permission_error_while_probing_fails_closed(home, monkeypatch):
    real_stat = os.stat
    blocked = home / ".grimoire"

    def stat(path, *args, **kwargs):
        if Path(path) == blocked:
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(paths.os, "stat", stat)
    with pytest.raises(LibraryPathError, match="cannot inspect"):
        resolve_library(environ={}, home=home)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_unreadable_ancestor_fails_closed(home, tmp_path):
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    locked.chmod(0)
    try:
        with pytest.raises(LibraryPathError):
            resolve_library(environ={"GRIMOIRE_V2_HOME": str(locked / "inner" / "lib")}, home=home)
    finally:
        locked.chmod(0o700)


# The proving scenario: launching with no override never opens V1.

_AUDITED: list[str] | None = None


def _audit(event, args):
    if _AUDITED is not None and event in ("open", "os.listdir", "os.scandir") and args and args[0] is not None:
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            _AUDITED.append(os.path.realpath(os.fsdecode(target)))


sys.addaudithook(_audit)


def touches(paths_seen, root: Path) -> list[str]:
    key = Path(os.path.realpath(root)).parts
    return [p for p in paths_seen if Path(p).parts[:len(key)] == key]


def test_default_launch_never_opens_or_lists_v1(home, tmp_path):
    global _AUDITED
    v1 = v1_library(home / ".grimoire")
    write_pointer(home, {"data_dir": str(v1)})
    before = snapshot(tmp_path)
    _AUDITED = []
    try:
        os.listdir(v1)                       # positive control: the hook sees a V1 listing
        (v1 / "worlds" / "w" / "world.md").open().close()  # ...and a V1 open
        assert len(touches(_AUDITED, v1)) == 2
        _AUDITED.clear()
        location = resolve_library(environ={}, home=home)
        seen = list(_AUDITED)
    finally:
        _AUDITED = None
    assert location.path == home / ".grimoire-v2"
    assert touches(seen, v1) == []
    assert seen == [str(home / ".grimoire.json")]   # the pointer is the only file read
    assert snapshot(tmp_path) == before


# ---- write destination --------------------------------------------------------

def test_write_destination_accepts_a_fresh_path_without_creating_it(home, tmp_path):
    before = snapshot(tmp_path)
    target = tmp_path / "new" / "library"
    assert check_write_destination(target, environ={}, home=home) == target.resolve()
    assert check_write_destination("~/lib", environ={}, home=home) == home / "lib"
    assert snapshot(tmp_path) == before


def test_write_destination_refuses_a_file(home, tmp_path):
    (tmp_path / "file").write_text("x")
    with pytest.raises(LibraryPathError, match="not a directory"):
        check_write_destination(tmp_path / "file", environ={}, home=home)


@pytest.mark.parametrize("raw", ["", "relative", "~other/x", " /x"])
def test_write_destination_refuses_malformed_paths(home, raw):
    with pytest.raises(LibraryPathError):
        check_write_destination(raw, environ={}, home=home)


def test_write_destination_refuses_every_v1_location(home, tmp_path):
    v1_library(home / ".grimoire")
    pointed = v1_library(tmp_path / "pointed", young=True)
    write_pointer(home, {"data_dir": str(pointed)})
    env_v1 = tmp_path / "env-v1"
    copied = v1_library(tmp_path / "copied")
    for target in (home / ".grimoire", home / ".grimoire" / "x", pointed, env_v1 / "x", copied, home):
        with pytest.raises(V1IsolationError):
            check_write_destination(target, environ={"GRIMOIRE_HOME": str(env_v1)}, home=home)


# ---- migration pair -----------------------------------------------------------

@pytest.mark.parametrize("source", [None, ""])
def test_migration_needs_an_explicit_source(home, tmp_path, source):
    with pytest.raises(LibraryPathError, match="explicitly chosen source"):
        check_migration_paths(source, tmp_path / "dest", environ={}, home=home)


def test_migration_accepts_v1_source_and_distinct_destination(home, tmp_path):
    v1 = v1_library(home / ".grimoire")
    backup = tmp_path / "backup.zip"  # the source's kind is the migration sprint's concern
    backup.write_bytes(b"PK")
    before = snapshot(tmp_path)
    assert check_migration_paths("~/.grimoire", "~/.grimoire-v2", environ={}, home=home) == (
        v1, home / ".grimoire-v2")
    assert check_migration_paths(backup, tmp_path / "dest", environ={}, home=home)[0] == backup.resolve()
    assert snapshot(tmp_path) == before


def test_migration_refuses_overlapping_paths(home, tmp_path):
    area = tmp_path / "area"
    source = area / "frozen"
    (source / "inner").mkdir(parents=True)
    (tmp_path / "link").symlink_to(source)
    for destination, relation in ((source, "is"), (source / "inner", "is inside"),
                                  (area, "contains"), (tmp_path / "link" / "v2", "is inside")):
        with pytest.raises(V1IsolationError, match=f"it {relation} the migration source"):
            check_migration_paths(source, destination, environ={}, home=home)


def test_migration_destination_still_refuses_v1(home, tmp_path):
    source = v1_library(tmp_path / "frozen-copy")
    v1_library(home / ".grimoire")
    with pytest.raises(V1IsolationError, match="default V1 library"):
        check_migration_paths(source, home / ".grimoire" / "v2", environ={}, home=home)


# ---- command line -------------------------------------------------------------

import ast  # noqa: E402
import subprocess  # noqa: E402

from grimoire.library.__main__ import main  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"


def test_cli_reports_location_without_creating_it(home, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert main([], environ={}, home=home) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"library: {home / '.grimoire-v2'}", "origin: default", "exists: no"]
    assert snapshot(tmp_path) == before


def test_cli_exit_codes_distinguish_refusals(home, capsys):
    assert main([], environ={"GRIMOIRE_V2_HOME": "~/.grimoire"}, home=home) == 3
    assert "default V1 library" in capsys.readouterr().err
    assert main([], environ={"GRIMOIRE_V2_HOME": "relative"}, home=home) == 2
    assert "relative" in capsys.readouterr().err
    assert main([], environ={}, home=home, platform="android") == 2
    assert main(["--bogus"], environ={}, home=home) == 2
    assert main(["--help"], environ={}, home=home) == 0
    assert "GRIMOIRE_V2_HOME" in capsys.readouterr().out


def test_cli_runs_as_a_module(home, tmp_path):
    (home / "lib").mkdir()
    before = snapshot(tmp_path)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC),
           "GRIMOIRE_V2_HOME": "~/lib"}
    done = subprocess.run([sys.executable, "-m", "grimoire.library"], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [f"library: {home / 'lib'}", "origin: env", "exists: yes"]
    assert snapshot(tmp_path) == before


def test_core_stays_free_of_the_filesystem_adapter():
    for file in (SRC / "grimoire" / "core").rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(), str(file))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [f"{node.module or ''}.{alias.name}" for alias in node.names]
                names = [("." * node.level + n) if node.level else n for n in names]
            else:
                continue
            for name in names:
                assert "library" not in name.split("."), f"{file} imports {name}"
