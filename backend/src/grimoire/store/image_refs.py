"""Placement records: which image a logical name holds (spec section 4).

A placement is ``<d>/image-refs/<name>.json``::

    {"format": 1, "image": "px1-..."}
    {"format": 1, "image": "px1-...", "focus": 43}
    {"format": 1, "focus": 43}

It holds the image id plus occurrence-only data (today just ``focus``). A
placement with no ``image`` is an occurrence override: for every image reader
it is *not an image*, and the slot falls through exactly as if it were absent.

The file is ``json.dumps(obj, sort_keys=True) + "\\n"`` and nothing else, so two
copies of the same placement are byte-identical (campaign slimming compares
them with ``filecmp``). Every write goes through `store.atomic`.

**Reads are tolerant.** A file that is unreadable, not JSON, not an object, of
another ``format``, or whose ``image`` is present but not a valid id is *absent*
(``read`` returns None, ``scan`` omits it). A ``focus`` outside 0-100, a bool or
a non-int reads as ``None`` and the placement is kept. A placement with neither
a valid image nor a focus is absent.

``.promote.json`` beside the placements is the promotion journal (a transient,
see `is_transient`); `scan` never reports it.

This module sits below `assets`: it must not import `assets`,
`image_descriptions`, `overlay` or any route. Locking is the caller's.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import atomic, image_hash, image_store
from .paths import safe_id

REFS_DIR = "image-refs"
JOURNAL = ".promote.json"
FORMAT = 1
_SUFFIX = ".json"


@dataclass(frozen=True)
class Ref:
    name: str
    image: str | None
    focus: int | None


@dataclass(frozen=True)
class ResolvedImage:
    name: str
    image_id: str
    blob_sha256: str
    #: Built by `image_store.blob_path`, never `.resolve()`d:
    #: `image_store.blob_sha_of` recognises store-built paths only.
    blob_path: Path
    ext: str
    mime: str
    width: int | None
    height: int | None
    focus: int | None


def _valid_name(name: object) -> bool:
    """Same rule as `assets._addressable_name` (copied: no `assets` import here).

    The shared id guard, no ".", and none of the glob metacharacters. It also
    rejects NUL, which `safe_id` admits but no path operation accepts (a
    deliberate difference from `assets`, which has the same latent gap).
    """
    return (isinstance(name, str) and safe_id(name) and "." not in name
            and not any(c in name for c in "*?[]\0"))


def _dir(d: Path) -> Path:
    return Path(d) / REFS_DIR


def ref_path(d: Path, name: str) -> Path:
    if not _valid_name(name):
        raise ValueError(f"unsafe placement name: {name!r}")
    return _dir(d) / f"{name}{_SUFFIX}"


def _focus_ok(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100


def _parse(name: str, text: str) -> Ref | None:
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
        return None
    if not isinstance(obj, dict):
        return None
    fmt = obj.get("format")
    if type(fmt) is not int or fmt != FORMAT:
        return None
    image = obj.get("image")
    if "image" in obj and not (isinstance(image, str) and image_hash.is_image_id(image)):
        return None
    focus = obj.get("focus")
    focus = focus if _focus_ok(focus) else None
    if image is None and focus is None:
        return None
    return Ref(name, image, focus)


def _read_file(path: Path, name: str) -> Ref | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return _parse(name, text)


def read(d: Path, name: str) -> Ref | None:
    if not _valid_name(name):
        return None
    return _read_file(ref_path(d, name), name)


def _dump(obj: dict) -> bytes:
    """Bytes, not text: a text-mode write would turn "\\n" into "\\r\\n" on Windows."""
    return (json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")


def delete(d: Path, name: str) -> bool:
    """Remove the placement; True when a file was actually removed, False
    when there was none. Any other failure (permissions, a read-only mount)
    raises: a placement left standing must not read as removed."""
    if not _valid_name(name):
        return False
    try:
        ref_path(d, name).unlink()
    except FileNotFoundError:
        return False
    return True


def write(d: Path, name: str, image: str | None, *, focus: int | None = None) -> None:
    """Write the placement atomically; ``image`` and ``focus`` both None deletes it."""
    path = ref_path(d, name)  # raises ValueError for an unsafe name
    if image is not None and not (isinstance(image, str) and image_hash.is_image_id(image)):
        raise ValueError(f"not an image id: {image!r}")
    if focus is not None and not _focus_ok(focus):
        raise ValueError(f"focus must be an int 0-100: {focus!r}")
    if image is None and focus is None:
        delete(d, name)
        return
    obj: dict = {"format": FORMAT}
    if image is not None:
        obj["image"] = image
    if focus is not None:
        obj["focus"] = focus
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes(path, _dump(obj))


def scan(d: Path) -> dict[str, Ref]:
    """Every valid placement in ``d/image-refs/``, from one directory scan."""
    out: dict[str, Ref] = {}
    try:
        with os.scandir(_dir(d)) as it:
            entries = list(it)
    except OSError:
        return out
    for e in entries:
        fname = e.name
        if fname == JOURNAL or not fname.endswith(_SUFFIX):
            continue
        stem = fname[: -len(_SUFFIX)]
        if not _valid_name(stem):
            continue
        try:
            if not e.is_file():
                continue
        except OSError:
            continue
        ref = _read_file(Path(e.path), stem)
        if ref is not None:
            out[stem] = ref
    return out


def walk(root: Path) -> Iterator[tuple[Path, Ref]]:
    """Every valid placement under `root` that holds an image, as `(the
    directory that owns it, its ref)`: the ``**/image-refs/*.json``, the
    promotion journal excluded.

    Symlinks are not followed -- neither a linked directory nor a linked ref
    file -- because the caller (world bundle export) packs what this names into
    a file the user hands to somebody else, and a link must not reach past the
    tree it was asked about. Unreadable directories are skipped.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if Path(dirpath).name != REFS_DIR:
            continue
        dirnames[:] = []                    # nothing nests inside image-refs/
        for fname in sorted(filenames):
            if fname == JOURNAL or not fname.endswith(_SUFFIX):
                continue
            stem = fname[: -len(_SUFFIX)]
            path = Path(dirpath) / fname
            if not _valid_name(stem) or path.is_symlink():
                continue
            ref = _read_file(path, stem)
            if ref is not None and ref.image is not None:
                yield Path(dirpath).parent, ref


class ImageRefParseError(ValueError):
    """`walk_strict` met something it cannot vouch for. `path` names it and
    `reason` says what it is (`UNREADABLE`, `SYMLINK`, `UNPARSEABLE`,
    `UNKNOWN_FORMAT`, `NOT_A_FILE`, `NOT_A_DIRECTORY`)."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{reason}: {path}")
        self.path = Path(path)
        self.reason = reason


UNREADABLE = "unreadable"
SYMLINK = "symlink"
UNPARSEABLE = "unparseable"
UNKNOWN_FORMAT = "unknown-format"
NOT_A_FILE = "not-a-regular-file"
NOT_A_DIRECTORY = "not-a-directory"

#: Files an OS or a file browser drops into any folder it shows. Grimoire never
#: writes one, and none can hold a placement, so a strict walk passes them by
#: rather than refusing a collection over a folder somebody opened in Finder.
OS_LITTER = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def _linkish(e: os.DirEntry) -> bool:
    """A symlink, or (on Windows) a junction, which `is_symlink` does not
    report and which leads outside the tree just as well."""
    return e.is_symlink() or bool(getattr(e, "is_junction", lambda: False)())


def strict_dirs(root: Path) -> Iterator[tuple[Path, list[os.DirEntry]]]:
    """Every directory under `root`, `root` included, with its entries --
    and an `ImageRefParseError` for a directory that does not list, an entry
    that cannot be tested, or a link anywhere. Nothing is followed."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise ImageRefParseError(d, UNREADABLE) from exc
        for e in entries:
            try:
                if _linkish(e):
                    raise ImageRefParseError(Path(e.path), SYMLINK)
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
            except OSError as exc:
                raise ImageRefParseError(Path(e.path), UNREADABLE) from exc
        yield d, entries


def ids_in(obj: object) -> Iterator[str]:
    """Every image id anywhere in a parsed JSON value, keys included."""
    if isinstance(obj, str):
        if image_hash.is_image_id(obj):
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from ids_in(k)
            yield from ids_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from ids_in(v)


def load_json_strict(path: Path) -> object:
    """`path` parsed, or `ImageRefParseError`: a file the walk listed and
    cannot read (gone since, a permission, bad UTF-8) or parse is a root
    nobody can vouch for."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ImageRefParseError(path, UNREADABLE) from exc
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ImageRefParseError(path, UNPARSEABLE) from exc


def strict_ref_ids(path: Path) -> list[str]:
    """The image id a placement file names (a list of none or one), or
    `ImageRefParseError` for anything that is not a placement this format
    describes: not JSON, not an object, a ``format`` other than this one, or
    an ``image`` that is not an id. A focus-only override names none."""
    obj = load_json_strict(path)
    if not isinstance(obj, dict):
        raise ImageRefParseError(path, UNPARSEABLE)
    fmt = obj.get("format")
    if type(fmt) is not int or fmt != FORMAT:
        raise ImageRefParseError(path, UNKNOWN_FORMAT)
    if "image" not in obj:
        return []
    image = obj["image"]
    if not image_hash.is_image_id(image):
        raise ImageRefParseError(path, UNPARSEABLE)
    return [image]


def _strict_file(path: Path) -> list[str]:
    """The ids one entry of an ``image-refs/`` folder holds (module docs of
    `walk_strict`)."""
    if path.name == JOURNAL:
        obj = load_json_strict(path)
        if not isinstance(obj, dict):
            raise ImageRefParseError(path, UNPARSEABLE)
        return sorted(set(ids_in(obj)))
    if atomic.is_write_temp(path):
        try:
            return sorted(set(ids_in(json.loads(path.read_text(encoding="utf-8")))))
        except (OSError, ValueError, RecursionError):
            return []               # a temp caught mid-write: its writer owns it
    return strict_ref_ids(path)


def walk_strict(root: Path) -> Iterator[tuple[Path, str]]:
    """`(file, image id)` for every id any file in any ``image-refs/`` folder
    under `root` names -- the garbage collector's roots (stage 4, M12), where
    `walk` is an exporter's view and forgives what it cannot read.

    Strict where `walk` is tolerant, because a reference this cannot read is a
    reference it cannot rule out:

    - **every regular file** in an ``image-refs/`` folder is parsed, whatever
      its stem: a sync client's ``avatar (conflicted copy).json`` names a
      picture somebody may still want;
    - the promotion journal (``.promote.json``) gives every id in it, both
      sides of the swap;
    - an atomic temp (`atomic.is_write_temp`) is parsed when it can be and
      passed by when it cannot -- its writer is mid-write and owns it;
    - an OS's folder litter (`OS_LITTER`) is passed by.

    Raises `ImageRefParseError` on anything else it cannot vouch for: a file
    that does not parse as a placement or names an unknown ``format``, a
    directory that does not list (`os.scandir` failing anywhere), any symlink
    or junction under `root`, `root` itself a link or not a directory, and a
    non-regular file in an ``image-refs/`` folder. A missing `root` holds
    nothing. Nothing is followed, written or cached.

    A generator: the error surfaces when iteration reaches the bad entry, so
    a caller treats the walk as all-or-nothing.
    """
    root = Path(root)
    if not _strict_root(root):
        return
    for d, entries in strict_dirs(root):
        if d.name != REFS_DIR:
            continue
        for e in entries:
            for image_id in _strict_entry(e):
                yield Path(e.path), image_id


def _strict_root(root: Path) -> bool:
    """Whether `root` is a directory to walk: False when absent, and
    `ImageRefParseError` when it is a link, not a directory, or unreadable."""
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ImageRefParseError(root, UNREADABLE) from exc
    if stat.S_ISLNK(st.st_mode):
        raise ImageRefParseError(root, SYMLINK)
    if not stat.S_ISDIR(st.st_mode):
        raise ImageRefParseError(root, NOT_A_DIRECTORY)
    return True


def _strict_entry(e: os.DirEntry) -> list[str]:
    """The ids one entry of an ``image-refs/`` folder holds; a subdirectory
    holds none here (`strict_dirs` walks it as a directory of its own)."""
    path = Path(e.path)
    try:
        if e.is_dir(follow_symlinks=False):
            return []
        regular = e.is_file(follow_symlinks=False)
    except OSError as exc:
        raise ImageRefParseError(path, UNREADABLE) from exc
    if e.name.casefold() in OS_LITTER:
        return []
    if not regular:
        raise ImageRefParseError(path, NOT_A_FILE)
    return _strict_file(path)


def walk_ids(root: Path) -> set[str]:
    """Every valid image id placed anywhere under `root` (`walk`)."""
    return {ref.image for _d, ref in walk(root) if ref.image is not None}


def image_names(d: Path) -> set[str]:
    """Names whose placement holds an image (focus-only overrides excluded)."""
    return {n for n, r in scan(d).items() if r.image is not None}


def resolve_ref(ref: Ref) -> ResolvedImage | None:
    """The placement's image, or None unless object *and* blob are both present
    (the blob as a regular file)."""
    if ref.image is None:
        return None
    obj = image_store.read(ref.image)
    if obj is None:
        return None
    try:
        blob = image_store.blob_path(obj.blob_sha256, obj.ext)
    except ValueError:
        return None
    if not blob.is_file():          # a directory there is no picture either
        return None
    return ResolvedImage(
        name=ref.name, image_id=obj.id, blob_sha256=obj.blob_sha256,
        blob_path=blob, ext=obj.ext, mime=obj.mime, width=obj.width,
        height=obj.height, focus=ref.focus)


def resolve(d: Path, name: str) -> ResolvedImage | None:
    ref = read(d, name)
    return resolve_ref(ref) if ref is not None else None


def is_transient(path: Path) -> bool:
    """True for the promotion journal, which is bookkeeping rather than content."""
    return path.name == JOURNAL and path.parent.name == REFS_DIR


def read_journal(d: Path) -> dict | None:
    try:
        obj = json.loads((_dir(d) / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def write_journal(d: Path, journal: dict) -> None:
    path = _dir(d) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes(path, _dump(journal))


def clear_journal(d: Path) -> None:
    with contextlib.suppress(OSError):
        (_dir(d) / JOURNAL).unlink()
