"""Safe reading of an untrusted zip: everything between a file the user picked
and a directory tree in the store.

These checks were written for module-pack import (#234) and hardened twice in
Codex review; world bundles (#54) need exactly the same ones. They live here
rather than being copied because a *second* copy is how the defense drifts --
the same failure mode ``test_atomic_guard.py`` exists to catch. The rule the
callers keep is: **every check runs before any extraction**, so a hostile name
is refused rather than caught part-way through writing files.

Callers raise their own domain error; each entry point takes an ``err`` factory
so ``ArchiveError`` never has to leak through a module or route boundary that
already has a vocabulary for "this upload is no good".
"""

from __future__ import annotations

import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

ErrFactory = Callable[[str], Exception]


class ArchiveError(Exception):
    """Default error for a zip that cannot be safely extracted."""


def _err(err: ErrFactory | None, message: str) -> Exception:
    """The caller's error type, or ours. Returned rather than raised so every
    call site reads as an explicit ``raise`` and no reader has to know that a
    helper is total."""
    return (err or ArchiveError)(message)


_DRIVE_OR_UNC = re.compile(r"^[A-Za-z]:|^[/\\]{2}")
_S_IFMT, _S_IFLNK = 0o170000, 0o120000

# Win32 resolves these to devices, extension and case regardless -- opening
# ``NUL`` for writing *succeeds* and discards every byte, so an archive member
# named after one would vanish during extraction with no error to notice
# (Codex review). Rejected on every platform for the same reason ``safe_id``
# rejects trailing dots on every platform: a store is synced between them, and
# a name has to mean the same thing on both.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)})

_COPY_BUF = 1024 * 1024


def _names_a_device(component: str) -> bool:
    return component.split(".", 1)[0].upper() in _RESERVED


def member_parts(raw_name: str, *, min_parts: int = 1, err: ErrFactory | None = None) -> list[str]:
    """Normalized path components for a zip member, or raise.

    Rejects absolute paths, drive-qualified and UNC names, and EMPTY / '.' /
    '..' components (codex plan review: 'pack//module.md' passes a naive split
    -- the stripped remainder '/module.md' then resolves to the drive root).
    Also rejects any component containing ':' -- the whole-name
    ``_DRIVE_OR_UNC`` check only anchors at the start, so a mid-path drive
    segment like 'pack/C:evil.txt' would otherwise pass here and then get
    collapsed onto the drive root by ``Path.joinpath``, escaping staging before
    the containment recheck ever runs (review finding: all checks must happen
    before any extraction, not be caught mid-extraction).

    ``min_parts`` is how many components the caller's layout requires: a module
    pack is always ``<root>/<file>`` (2), a world bundle also has a manifest
    sitting alone at the archive root (1).
    """
    name = raw_name.replace("\\", "/")
    if _DRIVE_OR_UNC.match(name) or name.startswith("/"):
        raise _err(err, f"unsafe zip entry: {raw_name}")
    parts = name.split("/")
    if len(parts) < min_parts or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise _err(err, f"unsafe zip entry: {raw_name}")
    # Both of these are silent-corruption cases rather than escapes, which is
    # why they are rejected rather than sanitized: Win32 trims a trailing dot
    # or space off a path component, so `item.md.` and `item.md` are one file
    # and the second member overwrites the first; and a reserved device name
    # swallows its member's bytes whole (Codex review).
    for p in parts:
        if p != p.rstrip(". ") or _names_a_device(p):
            raise _err(err, f"unsafe zip entry: {raw_name}")
    return parts


def scan(z: zipfile.ZipFile, *, max_members: int, max_uncompressed: int,
         min_parts: int = 1, err: ErrFactory | None = None) -> list[zipfile.ZipInfo]:
    """Validate every member and return the file entries, directories dropped.

    Caps come from the caller because the two archives are nothing alike in
    scale: a module pack is a handful of small files, a world carries every
    character portrait its owner ever downloaded.
    """
    everything = z.infolist()
    # Counted before directories are dropped: an archive of a million empty
    # directory entries has two *files* in it, so a cap applied to the filtered
    # list would wave through the very thing the cap exists to stop (Codex
    # review).
    if len(everything) > max_members:
        raise _err(err, f"zip has too many entries (> {max_members})")
    infos = [i for i in everything if not i.is_dir()]
    if sum(i.file_size for i in infos) > max_uncompressed:
        raise _err(err, "zip expands past the size cap")
    seen_ci: set[str] = set()
    dirs_ci: dict[str, str] = {}         # folded directory prefix -> as spelled
    for i in infos:
        if (i.external_attr >> 16) & _S_IFMT == _S_IFLNK:
            raise _err(err, f"zip contains a symlink: {i.filename}")
        parts = member_parts(i.filename, min_parts=min_parts, err=err)
        # Two spellings of one directory (`world/Foo/a.md`, `world/foo/b.md`)
        # are distinct paths but one directory on a case-insensitive
        # filesystem, so the extracted tree would not be the archive's.
        for n in range(1, len(parts)):
            prefix = "/".join(parts[:n])
            if dirs_ci.setdefault(prefix.casefold(), prefix) != prefix:
                raise _err(err, f"case-colliding zip directories: {i.filename}")
        ci = "/".join(parts).casefold()   # normalized + case-folded collisions
        if ci in seen_ci:
            raise _err(err, f"case-colliding zip entries: {i.filename}")
        seen_ci.add(ci)
    return infos


def top_level_names(infos: list[zipfile.ZipInfo]) -> set[str]:
    """First component of every member -- what the archive puts at its root."""
    return {member_parts(i.filename)[0] for i in infos}


def extract(z: zipfile.ZipFile, infos: list[zipfile.ZipInfo], staging: Path, *,
            strip: int = 0, err: ErrFactory | None = None) -> None:
    """Write ``infos`` into ``staging``, dropping ``strip`` leading components.

    ``staging`` must be a private directory the caller publishes with a single
    rename (or discards whole); this function never touches the live store.
    Every member is re-checked for containment after joining, because a name
    that passed ``member_parts`` can still resolve outside on a filesystem with
    its own opinions about the components.
    """
    staging_resolved = staging.resolve()
    for i in infos:
        parts = member_parts(i.filename, err=err)[strip:]
        if not parts:
            raise _err(err, f"unsafe zip entry: {i.filename}")
        dest = staging.joinpath(*parts)
        try:  # containment check (no Path.is_relative_to -- 3.8-safe)
            dest.resolve().relative_to(staging_resolved)
        except ValueError:
            raise _err(err, f"unsafe zip entry: {i.filename}")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Copied through a bounded buffer, not `z.read(i)`: a world bundle
            # legitimately carries a single multi-gigabyte asset, and reading a
            # member whole would turn that into an OOM rather than an import
            # (Codex review).
            # atomic-ok: unpublished staging tree, published as a unit by the
            # caller's single rename; a per-member temp+fsync would only slow
            # large imports
            with z.open(i) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, _COPY_BUF)
        except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
            # pathological names (reserved device names CON/NUL, trailing dots
            # or spaces on Windows) can raise a raw OSError from mkdir or
            # write_bytes; z.read(i) itself can raise RuntimeError (encrypted
            # member), NotImplementedError (unsupported compression) or
            # BadZipFile (bad CRC/corrupt data) -- none of those may escape
            # uncontained (codex review finding).
            raise _err(err, f"unextractable zip entry: {i.filename}")
