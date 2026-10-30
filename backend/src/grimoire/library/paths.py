"""Where the V2 library lives, and the guard that keeps V2 out of V1's.

V2's library is ``GRIMOIRE_V2_HOME`` when set, else ``~/.grimoire-v2``. V1's
``GRIMOIRE_HOME``, ``~/.grimoire`` and its bootstrap pointer ``~/.grimoire.json``
are never a source of that path; they are consulted only to *refuse* a V2 path
that is, sits inside or contains a V1 library. Resolving is read-only: nothing
is created, and no V1 directory is opened or listed -- only ``stat``/``lstat``
metadata, plus the one small pointer file, whose ``data_dir`` names a relocated
V1 library that would otherwise be undetectable while it is still young.

Probing fails closed. Only "no such file" means absent; a permission error,
symlink loop or unreadable pointer refuses, because it leaves V2 unable to show
the candidate is not the user's V1 library.

Obligations of every writer (native store, import/apply, initialization). The
checks here are point-in-time, so a writer must:

- call `check_write_destination` immediately before creating the library;
- create only the final component, requiring its parent to exist (never
  ``parents=True``: an unmounted volume must not become a library on the root
  filesystem);
- re-run the check after creating it;
- record the root's ``(st_dev, st_ino)`` and re-verify it at the start of each
  write session, so a root swapped for a link into V1 is noticed;
- never create a `V1_ROOT_MARKERS` name at a library root.

Known limit: a V1 library nested somewhere *below* a candidate is refused only
when it is one of the guarded roots; the candidate is not scanned.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

V2_HOME_ENV = "GRIMOIRE_V2_HOME"
V1_HOME_ENV = "GRIMOIRE_HOME"
DEFAULT_DIRNAME = ".grimoire-v2"
V1_DEFAULT_DIRNAME = ".grimoire"
V1_POINTER_NAME = ".grimoire.json"

#: Root entries only a V1 (0.2.1) library has. With a ``worlds`` or ``campaigns``
#: directory beside one of them, a directory is taken to be a V1 library.
#: Reserved: a V2 library root must never create any of these names.
V1_ROOT_MARKERS = (
    "llm_connections", "sampler_presets", "response_presets", ".world-staging",
    ".module-staging", ".char_macros_baked", "prompt_layout.json", "regex.json",
    "chores.json",
)
_V1_LAYOUT_DIRS = ("worlds", "campaigns")
_POINTER_MAX_BYTES = 1 << 20


class LibraryPathError(ValueError):
    """A library path that cannot be used: malformed, unresolvable or not a directory."""


class V1IsolationError(LibraryPathError):
    """A V2 path that is, sits inside or contains a V1 library."""


@dataclass(frozen=True)
class LibraryLocation:
    """The V2 library a process would use, and what selected it."""

    path: Path
    origin: Literal["default", "env"]


def resolve_library(*, environ: Mapping[str, str] | None = None, home: Path | None = None,
                    platform: str | None = None) -> LibraryLocation:
    """The V2 library location; refuses any V1 library. Creates nothing."""
    environ = os.environ if environ is None else environ
    platform = _default_platform() if platform is None else platform
    home = _known_home(home)
    raw = environ.get(V2_HOME_ENV, "")
    if raw:
        path, origin = _parse_user_path(raw, home, V2_HOME_ENV), "env"
    elif platform == "android":
        raise LibraryPathError(
            f"Android has no default V2 library yet; set {V2_HOME_ENV} to an app-private directory")
    elif home is None:
        raise LibraryPathError(f"cannot determine the home directory; set {V2_HOME_ENV}")
    else:
        path, origin = home / DEFAULT_DIRNAME, "default"
    resolved = _usable_directory(path)
    _check_isolation(resolved, _guarded_roots(environ, home))
    return LibraryLocation(resolved, origin)


def check_write_destination(path: str | os.PathLike[str], *, environ: Mapping[str, str] | None = None,
                            home: Path | None = None) -> Path:
    """`path` resolved, if V2 may create or write a library there. Creates nothing."""
    environ = os.environ if environ is None else environ
    home = _known_home(home)
    resolved = _usable_directory(_parse_user_path(os.fspath(path), home, "destination"))
    _check_isolation(resolved, _guarded_roots(environ, home))
    return resolved


def check_migration_paths(source: str | os.PathLike[str] | None, destination: str | os.PathLike[str], *,
                          environ: Mapping[str, str] | None = None,
                          home: Path | None = None) -> tuple[Path, Path]:
    """An explicit migration source and a distinct, writable V2 destination.

    Only explicitness and non-overlap are checked here: the source's kind,
    format and the user's approval belong to the migration sprints. The source
    may well be a V1 library -- it is never written, and nothing here reads it.
    """
    if source is None or not os.fspath(source):
        raise LibraryPathError("a migration needs an explicitly chosen source; there is no default")
    environ = os.environ if environ is None else environ
    known = _known_home(home)
    src = _realpath(_parse_user_path(os.fspath(source), known, "migration source"))
    dst = check_write_destination(destination, environ=environ, home=home)
    _check_isolation(dst, [("the migration source", src)], fingerprint=False)
    return src, dst


# ---- parsing and normalization ---------------------------------------------------

def _default_platform() -> str:
    # Before 3.13, CPython on Android reports "linux"; the API-level probe does not lie.
    return "android" if hasattr(sys, "getandroidapilevel") else sys.platform


def _known_home(home: Path | None) -> Path | None:
    """The injected or process home; None when the process has none."""
    if home is None:
        try:
            home = Path.home()
        except (RuntimeError, KeyError, OSError):
            return None
    home = Path(home)
    if not home.is_absolute():
        raise LibraryPathError(f"the home directory {str(home)!r} is not absolute")
    return home


def _expand_tilde(raw: str, home: Path | None, what: str) -> Path:
    """``~`` and ``~/...`` against `home` -- never the process HOME, never ``~user``."""
    head, sep, rest = raw.partition("/")
    if head != "~":
        raise LibraryPathError(f"{what}: {raw!r} names another user's home; spell the path out")
    if home is None:
        raise LibraryPathError(f"{what}: cannot expand {raw!r} without a home directory")
    rest = rest.lstrip("/")  # V1's `expanduser` reads "~//x" as $HOME/x; joining "/x" would drop home
    return home / rest if rest else home


def _parse_user_path(raw: object, home: Path | None, what: str) -> Path:
    if not isinstance(raw, str):
        raise LibraryPathError(f"{what}: {raw!r} is not a text path")
    if not raw or raw != raw.strip():
        raise LibraryPathError(f"{what}: {raw!r} is empty or has surrounding whitespace")
    path = _expand_tilde(raw, home, what) if raw.startswith("~") else Path(raw)
    if not path.is_absolute():
        raise LibraryPathError(f"{what}: {raw!r} is relative; a library path must be absolute")
    return path


def _realpath(path: Path) -> Path:
    """`path` with every existing link followed. Python 3.13's `Path.resolve`
    returns a looping path unchanged, so this asks `realpath` strictly first."""
    try:
        return Path(os.path.realpath(path, strict=True))
    except (FileNotFoundError, NotADirectoryError):
        return Path(os.path.realpath(path))
    except (OSError, RuntimeError, ValueError) as error:  # ValueError: an embedded NUL
        raise LibraryPathError(f"cannot resolve {path}: {error}") from error


def _probe(path: Path, *, follow: bool = True) -> os.stat_result | None:
    """Metadata for `path`, None only when it does not exist."""
    try:
        return os.stat(path) if follow else os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError) as error:
        raise LibraryPathError(f"cannot inspect {path}: {error}") from error


def _usable_directory(path: Path) -> Path:
    resolved = _realpath(path)
    info = _probe(resolved)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise LibraryPathError(f"{resolved} exists but is not a directory")
    return resolved


# ---- V1 isolation ------------------------------------------------------------------

def _guarded_roots(environ: Mapping[str, str], home: Path | None) -> list[tuple[str, Path]]:
    """Every place a V1 library is known to live, labelled for the refusal message."""
    roots: list[tuple[str, Path]] = []
    if home is not None:
        roots.append(("the default V1 library", home / V1_DEFAULT_DIRNAME))
    v1_home = environ.get(V1_HOME_ENV, "")
    if v1_home:
        # V1 read it as `Path(env)`: relative to its cwd and with no `~` expansion.
        try:
            literal = Path(os.path.abspath(v1_home))
        except (OSError, ValueError) as error:  # a vanished cwd, an embedded NUL
            raise LibraryPathError(f"cannot locate the V1 library in {V1_HOME_ENV}: {error}") from error
        roots.append((f"the V1 library in {V1_HOME_ENV}", literal))
        if v1_home.startswith("~") and home is not None:
            try:
                roots.append((f"the V1 library in {V1_HOME_ENV}", _expand_tilde(v1_home, home, V1_HOME_ENV)))
            except LibraryPathError:
                pass  # `~user` cannot be V1's `Path(env)` reading either; the literal form is guarded
    if home is not None:
        pointed = _pointer_library(home)
        if pointed is not None:
            roots.append((f"the V1 library named by ~/{V1_POINTER_NAME}", pointed))
    return [(label, _realpath(root)) for label, root in roots]


def _pointer_library(home: Path) -> Path | None:
    """The ``data_dir`` V1's bootstrap pointer names, read only so it can be refused."""
    pointer = home / V1_POINTER_NAME

    def unusable(reason: str) -> V1IsolationError:
        return V1IsolationError(
            f"V1's bootstrap pointer {pointer} {reason}, so V2 cannot tell where the V1 library "
            f"is; fix or remove that file")

    try:
        # Non-blocking, and typed by the open descriptor: a FIFO swapped in after
        # a separate stat would otherwise block this open forever.
        fd = os.open(pointer, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise unusable(f"cannot be opened ({error})") from error
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _POINTER_MAX_BYTES:
            raise unusable("is not a small regular file")
        try:
            text = handle.read(_POINTER_MAX_BYTES + 1)
        except OSError as error:
            raise unusable(f"cannot be read ({error})") from error
    try:
        data = json.loads(text.decode("utf-8"))
    except (ValueError, RecursionError) as error:  # bad UTF-8, bad JSON, absurd nesting
        raise unusable(f"cannot be read ({error})") from error
    if not isinstance(data, dict):
        raise unusable("is not a JSON object")
    raw = data.get("data_dir")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or "\0" in raw:
        raise unusable("has a data_dir that is not a usable path string")
    if raw.startswith("~"):
        try:
            return _expand_tilde(raw, home, "pointer")
        except LibraryPathError as error:
            raise unusable("has a data_dir V2 cannot expand") from error
    if not Path(raw).is_absolute():
        raise unusable("has a relative data_dir")
    return Path(raw)


def _key(path: Path) -> tuple[str, ...]:
    """Components compared case- and normalization-blind: refusing ``~/.Grimoire``
    on a case-sensitive disk is the safe side of an APFS or NTFS alias."""
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _lexical_relation(path: Path, root: Path) -> str | None:
    p, r = _key(path), _key(root)
    if p == r:
        return "is"
    if p[:len(r)] == r:
        return "is inside"
    if r[:len(p)] == p:
        return "contains"
    return None


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _lineage(path: Path) -> Iterator[Path]:
    yield path
    yield from path.parents


def _identity_relation(path: Path, root: Path) -> str | None:
    """The relation by device and inode, which sees aliases names do not:
    case-insensitive filesystems, bind mounts, hard-linked directories."""
    root_info = _probe(root)
    if root_info is not None:
        for ancestor in _lineage(path):
            info = _probe(ancestor)
            if info is not None and _same_file(info, root_info):
                return "is" if ancestor == path else "is inside"
    path_info = _probe(path)
    if path_info is not None:  # a missing path cannot contain anything
        for ancestor in _lineage(root):
            info = _probe(ancestor)
            if info is not None and _same_file(info, path_info):
                return "is" if ancestor == root else "contains"
    return None


def _looks_like_v1(directory: Path) -> bool:
    has_layout = any(
        (info := _probe(directory / name)) is not None and stat.S_ISDIR(info.st_mode)
        for name in _V1_LAYOUT_DIRS)
    return has_layout and any(
        _probe(directory / name, follow=False) is not None for name in V1_ROOT_MARKERS)


def _check_isolation(path: Path, roots: list[tuple[str, Path]], *, fingerprint: bool = True) -> None:
    for label, root in roots:
        relation = _lexical_relation(path, root) or _identity_relation(path, root)
        if relation is not None:
            raise V1IsolationError(f"refusing {path}: it {relation} {label} ({root})")
    if fingerprint:
        for ancestor in _lineage(path):
            if _looks_like_v1(ancestor):
                where = "is" if ancestor == path else "is inside"
                raise V1IsolationError(f"refusing {path}: it {where} what looks like a V1 library ({ancestor})")
