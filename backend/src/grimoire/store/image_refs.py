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
    """Remove the placement; True when a file was actually removed."""
    if not _valid_name(name):
        return False
    try:
        ref_path(d, name).unlink()
    except OSError:  # missing, or unremovable: nothing was removed
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


def image_names(d: Path) -> set[str]:
    """Names whose placement holds an image (focus-only overrides excluded)."""
    return {n for n, r in scan(d).items() if r.image is not None}


def resolve_ref(ref: Ref) -> ResolvedImage | None:
    """The placement's image, or None unless object *and* blob are both present."""
    if ref.image is None:
        return None
    obj = image_store.read(ref.image)
    if obj is None:
        return None
    try:
        blob = image_store.blob_path(obj.blob_sha256, obj.ext)
    except ValueError:
        return None
    if not blob.exists():
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
