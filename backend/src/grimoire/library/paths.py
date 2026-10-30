"""Where the V2 library lives, and the guard that keeps V2 out of V1's.

V2's library is ``GRIMOIRE_V2_HOME`` when set, else ``~/.grimoire-v2``. V1's
``GRIMOIRE_HOME``, ``~/.grimoire`` and its bootstrap pointer ``~/.grimoire.json``
are never a source of that path; they are consulted only to *refuse* a V2 path
that is, sits inside or contains a V1 library. Resolving is read-only: nothing
is created, and no V1 directory is opened or listed -- only ``stat``/``lstat``
metadata, plus the one small pointer file, whose ``data_dir`` names a relocated
V1 library that would otherwise be undetectable while it is still young. On
Linux the OS mount table is read too, so a bind mount cannot give a path a
second name that hides containment.

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
import re
import stat
import sys
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
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
    resolved = _usable_directory(_parse_user_path(_fspath(path), home, "destination"))
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
    if source is None or not _fspath(source):
        raise LibraryPathError("a migration needs an explicitly chosen source; there is no default")
    environ = os.environ if environ is None else environ
    known = _known_home(home)
    src = _realpath(_parse_user_path(_fspath(source), known, "migration source"))
    dst = check_write_destination(destination, environ=environ, home=home)
    _check_isolation(dst, [("the migration source", src)], fingerprint=False)
    return src, dst


# ---- parsing and normalization ---------------------------------------------------

def _default_platform() -> str:
    # Before 3.13, CPython on Android reports "linux"; the API-level probe does not lie.
    return "android" if hasattr(sys, "getandroidapilevel") else sys.platform


def _fspath(path: object) -> object:
    try:
        return os.fspath(path)
    except TypeError as error:
        raise LibraryPathError(f"{path!r} is not a path") from error


def _working_directory() -> str:
    """The cwd V1 resolved a relative ``GRIMOIRE_HOME`` against (a seam for tests)."""
    return os.getcwd()


def _known_home(home: Path | None) -> Path | None:
    """The injected or process home; None when the process has none."""
    if home is None:
        try:
            home = Path.home()
        except (RuntimeError, KeyError, OSError):
            return None
    home = Path(home)
    _require_os_text(str(home), "the home directory")
    if not home.is_absolute():
        raise LibraryPathError(f"the home directory {str(home)!r} is not absolute")
    return home


def _expand_tilde(raw: str, home: Path | None, what: str) -> Path:
    """``~`` and ``~/...`` against `home` -- never the process HOME, never ``~user``.
    On Windows ``~\\...`` is the same, as ``expanduser`` reads it there."""
    separators = "/\\" if _default_platform() == "win32" else "/"
    cut = next((i for i, char in enumerate(raw) if char in separators), len(raw))
    head, rest = raw[:cut], raw[cut + 1:]
    if head != "~":
        raise LibraryPathError(f"{what}: {raw!r} names another user's home; spell the path out")
    if home is None:
        raise LibraryPathError(f"{what}: cannot expand {raw!r} without a home directory")
    rest = rest.lstrip(separators)  # V1's `expanduser` reads "~//x" as $HOME/x; joining "/x" would drop home
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


def _require_os_text(text: str, what: object, *, platform: str | None = None) -> None:
    """Refuse a name this OS cannot store, the same way on every platform.

    POSIX names are bytes: a name must encode with ``surrogateescape`` (so Python's
    surrogate-escaped spelling of an undecodable byte, ``\\udc80``-``\\udcff``,
    is a real name, but a lone ``\\ud800`` is not). Windows names are UTF-16 and
    its ``surrogatepass`` encoding would accept a lone surrogate that is not text,
    so there any surrogate is refused. (On a POSIX system whose filesystem
    encoding is not UTF-8, a name this accepts may still fail to resolve; that
    failure is a `LibraryPathError` too.)
    """
    platform = _default_platform() if platform is None else platform
    if platform == "win32":
        usable = not any("\ud800" <= char <= "\udfff" for char in text)
    else:
        try:
            text.encode("utf-8", "surrogateescape")
            usable = True
        except UnicodeEncodeError:
            usable = False
    if not usable:
        raise LibraryPathError(f"{what}: {text!r} cannot be used as a path on this system")


def _realpath(path: Path) -> Path:
    """`path` with every existing link followed. Python 3.13's `Path.resolve`
    returns a looping path unchanged, so this asks `realpath` strictly first."""
    _require_os_text(str(path), path)
    try:
        try:
            resolved = Path(os.path.realpath(path, strict=True))
        except (FileNotFoundError, NotADirectoryError):
            resolved = Path(os.path.realpath(path))
    except (OSError, RuntimeError, ValueError) as error:  # ValueError: a NUL or unencodable name
        raise LibraryPathError(f"cannot resolve {path}: {error}") from error
    _require_os_text(str(resolved), path)  # a link may lead to a name NTFS stores but is not text
    return resolved


def _probe(path: Path, *, follow: bool = True) -> os.stat_result | None:
    """Metadata for `path`, None only when it does not exist."""
    try:
        return os.stat(path) if follow else os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError) as error:
        raise LibraryPathError(f"cannot inspect {path}: {error}") from error


def _usable_directory(path: Path) -> Path:
    """`path` resolved; it, or failing that its nearest existing ancestor, must be a directory."""
    resolved = _realpath(path)
    for ancestor in _lineage(resolved):
        info = _probe(ancestor)
        if info is None:
            continue
        if not stat.S_ISDIR(info.st_mode):
            where = "exists" if ancestor == resolved else f"is under {ancestor}, which exists"
            raise LibraryPathError(f"{resolved} {where} but is not a directory")
        break
    return resolved


# ---- V1 isolation ------------------------------------------------------------------

def _guarded_roots(environ: Mapping[str, str], home: Path | None) -> list[tuple[str, Path]]:
    """Every place a V1 library is known to live, labelled for the refusal message."""
    roots: list[tuple[str, Path]] = []
    if home is not None:
        roots.append(("the default V1 library", home / V1_DEFAULT_DIRNAME))
    v1_home = environ.get(V1_HOME_ENV, "")
    if v1_home and not isinstance(v1_home, str):
        raise LibraryPathError(f"{V1_HOME_ENV}: {v1_home!r} is not a text path")
    if v1_home:
        # V1 read it as `Path(env)`: relative to its cwd and with no `~` expansion.
        try:
            # Joined, not `abspath`ed: collapsing "link/.." by name would guard a
            # different directory from the one V1's OS calls reached.
            literal = Path(v1_home) if os.path.isabs(v1_home) else Path(_working_directory(), v1_home)
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
        info = os.stat(pointer)
    except (FileNotFoundError, NotADirectoryError) as error:
        if _probe(pointer, follow=False) is None:
            return None
        # A link to an offline volume, say: V1's library is somewhere unknown.
        raise unusable("is a link whose target cannot be found") from error
    except (OSError, ValueError) as error:
        raise unusable(f"cannot be inspected ({error})") from error
    _require_small_file(info, unusable)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(pointer, flags)
    except OSError as error:  # including a pointer that vanished since the stat
        raise unusable(f"cannot be opened ({error})") from error
    try:
        # Typed again by the open descriptor: something swapped in after the
        # stat is refused before it is read (and the non-blocking open means a
        # swapped-in FIFO cannot hang us first).
        _require_small_file(os.fstat(fd), unusable)
        chunks, size = [], 0
        while size <= _POINTER_MAX_BYTES and (chunk := os.read(fd, _POINTER_MAX_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
        if size > _POINTER_MAX_BYTES:  # it grew after the stat
            raise unusable("is not a small regular file")
        text = b"".join(chunks)
    except OSError as error:
        raise unusable(f"cannot be read ({error})") from error
    finally:
        os.close(fd)
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


def _require_small_file(info: os.stat_result, unusable) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_size > _POINTER_MAX_BYTES:
        raise unusable("is not a small regular file")


def _component_key(part: str, windows: bool) -> str:
    """One name, compared the way the most permissive filesystem would.

    Case- and normalization-blind everywhere: refusing ``~/.Grimoire`` on a
    case-sensitive disk is the safe side of an APFS or NTFS alias. Windows also
    opens ``.grimoire.``, ``.grimoire `` and ``.grimoire::$INDEX_ALLOCATION`` as
    ``.grimoire``, so there a ``:stream`` suffix and trailing dots and spaces are
    ignored. On POSIX those are distinct names (``backup-10:00``), so they stay.
    """
    name = unicodedata.normalize("NFC", part).casefold()
    return name.split(":", 1)[0].rstrip(" .") if windows else name


def _anchor_key(anchor: str) -> str:
    """A drive or root, without the ``\\\\?\\`` or ``\\\\.\\`` prefix that names the same place."""
    key = anchor.casefold().replace("/", "\\")
    if key.startswith("\\\\?\\unc\\"):
        return "\\\\" + key[len("\\\\?\\unc\\"):]
    if key.startswith(("\\\\?\\", "\\\\.\\")):
        return key[len("\\\\?\\"):]
    return key


def _tail_key(parts: Iterable[str]) -> tuple[str, ...]:
    # A name that is nothing but dots or spaces vanishes on Windows ("data\\..." is "data").
    windows = _default_platform() == "win32"
    return tuple(key for part in parts if (key := _component_key(part, windows)))


def _key(path: PurePath) -> tuple[str, ...]:
    """Components compared as every supported filesystem might alias them."""
    anchor, rest = path.anchor, path.parts[1:] if path.anchor else path.parts
    if anchor.casefold().replace("/", "\\") == "\\\\.\\unc\\" and len(rest) >= 2:
        # pathlib leaves server and share out of a \\.\UNC\ anchor; \\?\UNC\ keeps them.
        anchor, rest = f"\\\\{rest[0]}\\{rest[1]}\\", rest[2:]
    return ((_anchor_key(anchor),) if anchor else ()) + _tail_key(rest)


def _lexical_relation(path: Path, root: Path) -> str | None:
    p, r = _key(path), _key(root)
    if p == r:
        return "is"
    if p[:len(r)] == r:
        return "is inside"
    if r[:len(p)] == p:
        return "contains"
    return None


@dataclass(frozen=True)
class _Mount:
    device: str   # major:minor
    root: Path    # the directory of that filesystem mounted here
    point: Path   # where it is mounted


#: Linux's (and Android's) mount table. Other platforms have no bind mounts to see.
_MOUNTINFO = Path("/proc/self/mountinfo")


def _has_mount_table() -> bool:
    return _default_platform() == "android" or sys.platform.startswith("linux")


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _mounts() -> list[_Mount]:
    try:
        text = _MOUNTINFO.read_text(encoding="utf-8", errors="surrogateescape")
    except (FileNotFoundError, NotADirectoryError) as error:
        if not _has_mount_table():
            return []
        # Without /proc, bind mounts still exist; we just cannot see them.
        raise LibraryPathError(f"cannot read the mount table {_MOUNTINFO} (is /proc mounted?): {error}") from error
    except OSError as error:
        raise LibraryPathError(f"cannot read the mount table {_MOUNTINFO}: {error}") from error
    mounts = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4:
            mounts.append(_Mount(fields[2], Path(_unescape(fields[3])), Path(_unescape(fields[4]))))
    return mounts


def _under(path: Path, top: Path) -> bool:
    return path.parts[:len(top.parts)] == top.parts


def _aliases(path: Path, mounts: list[_Mount]) -> set[Path]:
    """Every name `path` has through the mount table. A bind mount of an inner
    directory gives what sits below it a second name whose ancestors are not
    its physical ancestors, so neither names nor inodes reveal containment."""
    names = {path}
    holder = None
    for mount in mounts:  # the visible mount is the deepest, and among stacked ones the last
        if _under(path, mount.point) and (holder is None or len(mount.point.parts) >= len(holder.point.parts)):
            holder = mount
    if holder is None:
        return names
    physical = holder.root.joinpath(*path.parts[len(holder.point.parts):])
    for mount in mounts:
        if mount.device == holder.device and _under(physical, mount.root):
            names.add(mount.point.joinpath(*physical.parts[len(mount.root.parts):]))
    return names


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _lineage(path: Path) -> Iterator[Path]:
    yield path
    yield from path.parents


def _nearest_existing(path: Path) -> tuple[os.stat_result, tuple[str, ...]] | None:
    """The deepest existing ancestor's identity, and the keyed names below it."""
    for ancestor in _lineage(path):
        info = _probe(ancestor)
        if info is not None:
            return info, _tail_key(path.parts[len(ancestor.parts):])
    return None


def _anchored_relation(path: Path, root: Path) -> str | None:
    """Whether `path` is, is inside or contains `root` once they meet at `root`'s
    nearest existing ancestor -- which also covers a root that does not exist
    yet but lives under a directory `path` reaches by another name."""
    anchor = _nearest_existing(root)
    if anchor is None:
        return None
    root_info, root_tail = anchor
    for ancestor in _lineage(path):
        info = _probe(ancestor)
        if info is None or not _same_file(info, root_info):
            continue
        tail = _tail_key(path.parts[len(ancestor.parts):])
        if tail == root_tail:
            return "is"
        if tail[:len(root_tail)] == root_tail:
            return "is inside"
        if root_tail[:len(tail)] == tail:
            return "contains"
    return None


_CONVERSE = {"is": "is", "is inside": "contains", "contains": "is inside"}


def _identity_relation(path: Path, root: Path) -> str | None:
    """The relation by device and inode, which sees aliases names do not:
    case-insensitive filesystems, bind mounts, hard-linked directories, macOS
    firmlinks -- also for a root (or path) that does not exist yet."""
    relation = _anchored_relation(path, root)
    if relation is None and (converse := _anchored_relation(root, path)) is not None:
        relation = _CONVERSE[converse]
    return relation


def _looks_like_v1(directory: Path) -> bool:
    has_layout = any(
        (info := _probe(directory / name)) is not None and stat.S_ISDIR(info.st_mode)
        for name in _V1_LAYOUT_DIRS)
    return has_layout and any(
        _probe(directory / name, follow=False) is not None for name in V1_ROOT_MARKERS)


def _check_isolation(path: Path, roots: list[tuple[str, Path]], *, fingerprint: bool = True) -> None:
    mounts = _mounts()
    names = _aliases(path, mounts)
    for label, root in roots:
        root_names = _aliases(root, mounts)
        relation = next((r for name in names for root_name in root_names
                         if (r := _lexical_relation(name, root_name))), None)
        relation = relation or _identity_relation(path, root)
        if relation is not None:
            raise V1IsolationError(f"refusing {path}: it {relation} {label} ({root})")
    if fingerprint:
        for name in names:
            for ancestor in _lineage(name):
                if _looks_like_v1(ancestor):
                    where = "is" if ancestor == name else "is inside"
                    raise V1IsolationError(
                        f"refusing {path}: it {where} what looks like a V1 library ({ancestor})")
