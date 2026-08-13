"""World bundles: a world directory zipped whole, importable as a new world (#54).

The store layout *is* the exchange format. A world directory is already
self-contained -- ``world.md``, the entity kind-folders, ``characters/`` with
their per-version assets, ``greetings/``, ``pcs/``, ``sheets/``,
``plotmap.json``, ``tags.md``, ``calendar.json`` -- so the export walks it and
the import puts it back. Nothing enumerates the kinds, which is the point: a
kind added next month rides along without touching this file.

A ``grimoire-bundle.json`` manifest sits at the archive root beside the
``world/`` prefix, recording the format version, the source world id and its
name. It buys two things a bare directory zip cannot: an import can refuse a
bundle from a future grimoire with an honest message instead of half-extracting
one, and it carries the **source world id**, which the import needs.

That id is the one thing that does not travel: ``store/localize.py`` writes
absolute serving URLs into card and greeting text --
``/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}`` and
``/api/worlds/{wid}/greetings/{gid}/images/{name}`` -- so a world landing under
a new id would render every localized image as a 404. Import rewrites that
prefix across the text records, and only the text records: an asset's bytes are
copied verbatim.

An import always creates a **new** world and never merges into an existing one.
Campaigns bind to a world by id, and merging would silently change what an
existing campaign inherits and corrupt its sync bases.

Safety, shared with module-pack import via ``store.ziputil``: every member is
checked -- traversal, absolute/UNC/drive names, symlinks, case collisions,
member count and expanded size -- *before* anything is written, and the tree is
built in a private staging directory that is published with a single rename or
discarded whole. A rejected import leaves no partial world in the library.
"""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from pathlib import Path

from . import atomic, ziputil
from .frontmatter import parse_frontmatter
from .paths import ensure_home, home, now_iso, safe_id, slugify, uniquify
from .worlds import paths as worlds_paths

FORMAT = 1
MANIFEST_NAME = "grimoire-bundle.json"
WORLD_PREFIX = "world"

# Sized for a real library rather than a module pack: worlds here run to
# thousands of files and a gigabyte of character art, so these are a guard
# against a hostile archive filling the disk, not a policy on world size.
MAX_MEMBERS = 100_000
MAX_UNCOMPRESSED = 8 * 1024 * 1024 * 1024

# What the import rewrites and the export bothers to compress. Everything else
# -- PNG, WebP, JPEG -- is already compressed, so deflating it costs real CPU
# on a gigabyte-scale world and saves nothing, and rewriting it would corrupt
# it.
_TEXT_SUFFIXES = frozenset({".md", ".json", ".txt", ".csv", ".css", ".html",
                            ".svg", ".yaml", ".yml"})


class BundleError(Exception):
    """A bundle that cannot be read, or is not one."""


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES


def _staging_root() -> Path:
    return home() / ".world-staging"


def bundle_filename(wid: str) -> str:
    return f"{wid}-world.zip"


# ---- export ----

def _is_write_temp(path: Path) -> bool:
    """An ``store.atomic`` temp caught mid-write (``.<name>.xxxx.tmp``).

    Not part of the world, and the writer that owns it will rename or unlink it
    out from under the walk -- so packing one is both wrong and racy.
    """
    return path.name.startswith(".") and path.name.endswith(".tmp")


def write_bundle(wid: str, dest: Path) -> None:
    """Zip the world at `wid` into `dest`. Raises ``WorldNotFound``.

    Written to a path rather than returned as bytes: a world with a full
    character gallery runs past a gigabyte, and holding that in memory to hand
    to a response is not something a phone-sized process survives.

    A best-effort snapshot, not a locked one -- a world edited during the walk
    can be packed half-old and half-new. Files that vanish mid-walk are skipped
    rather than failing the export: by then they are genuinely not part of the
    world any more.
    """
    root = worlds_paths.world_root(wid)                     # rejects an unsafe id
    meta_path = worlds_paths.world_meta_path(wid)
    if not meta_path.exists():
        raise worlds_paths.WorldNotFound(wid)
    meta, _body = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    manifest = {"format": FORMAT, "kind": "world", "world_id": wid,
                "name": meta.get("name", wid), "exported": now_iso()}

    with zipfile.ZipFile(dest, "w") as z:
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n",
                   compress_type=zipfile.ZIP_DEFLATED)
        for p in sorted(root.rglob("*")):
            if _is_write_temp(p) or not p.is_file():
                continue
            arc = f"{WORLD_PREFIX}/{p.relative_to(root).as_posix()}"
            compress = zipfile.ZIP_DEFLATED if _is_text(p) else zipfile.ZIP_STORED
            try:
                z.write(p, arc, compress_type=compress)
            except FileNotFoundError:
                continue                                    # deleted mid-walk


# ---- import ----

def _read_manifest(z: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> dict:
    """The bundle's manifest, validated. Everything this returns is trusted by
    the rest of the import, so it is all checked here."""
    if not any(i.filename == MANIFEST_NAME for i in infos):
        raise BundleError(f"not a world bundle: no {MANIFEST_NAME}")
    try:
        manifest = json.loads(z.read(MANIFEST_NAME))
    except (ValueError, OSError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile) as e:
        raise BundleError(f"unreadable {MANIFEST_NAME}: {e}")
    if not isinstance(manifest, dict):
        raise BundleError(f"{MANIFEST_NAME} is not an object")
    if manifest.get("kind") != "world":
        raise BundleError(f"not a world bundle: kind is {manifest.get('kind')!r}")
    fmt = manifest.get("format")
    if fmt != FORMAT:
        # Named separately because the fix differs: a newer bundle needs a
        # newer grimoire, anything else is a broken file.
        if isinstance(fmt, int) and fmt > FORMAT:
            raise BundleError(
                f"bundle format {fmt} is newer than this grimoire understands ({FORMAT})")
        raise BundleError(f"unsupported bundle format: {fmt!r}")
    if not safe_id(manifest.get("world_id")):
        raise BundleError(f"bundle names an unusable world id: {manifest.get('world_id')!r}")
    return manifest


def _world_members(infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """The members under ``world/``, with the archive's shape checked.

    Exactly two things may sit at the archive root: the manifest and the world
    directory. Anything else means this is not a bundle we understand, and
    guessing at it is how an import writes files nobody asked for.
    """
    members = []
    for i in infos:
        if i.filename == MANIFEST_NAME:
            continue
        parts = ziputil.member_parts(i.filename, min_parts=2, err=BundleError)
        if parts[0] != WORLD_PREFIX:
            raise BundleError(f"unexpected entry outside {WORLD_PREFIX}/: {i.filename}")
        members.append(i)
    if not any(ziputil.member_parts(i.filename)[1:] == ["world.md"] for i in members):
        raise BundleError(f"not a world bundle: no {WORLD_PREFIX}/world.md")
    return members


def _world_name(staging: Path, manifest: dict) -> str:
    """The imported world's display name.

    The extracted ``world.md`` wins over the manifest: the manifest is a
    convenience header, the record is the world. A hand-assembled bundle whose
    header disagrees still imports as the world it actually contains.
    """
    try:
        text = (staging / "world.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise BundleError(f"unreadable {WORLD_PREFIX}/world.md: {e}")
    meta, _body = parse_frontmatter(text)
    for candidate in (meta.get("name"), manifest.get("name"), manifest.get("world_id")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Imported World"


def _repoint_urls(staging: Path, old_wid: str, new_wid: str) -> int:
    """Rewrite localized image URLs from `old_wid` to `new_wid` in place.

    Byte-level, and only over the text records: a substitution that had to
    decode every file would fail on the first asset, and one that touched an
    asset would corrupt it. The prefix carries its trailing slash so a world id
    that is a prefix of another (``realm`` beside ``realm-2``) cannot be
    rewritten by half.
    """
    old = f"/api/worlds/{old_wid}/".encode()
    new = f"/api/worlds/{new_wid}/".encode()
    touched = 0
    for p in staging.rglob("*"):
        if not p.is_file() or not _is_text(p):
            continue
        data = p.read_bytes()
        if old in data:
            atomic.write_bytes(p, data.replace(old, new))
            touched += 1
    return touched


def _publish(staging: Path, wid: str) -> str:
    dest = worlds_paths.world_root(wid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Lost a race with another import that took this id between our
        # uniquify and here. Rare, and a plain rename would merge into it on
        # POSIX when the directory happens to be empty -- so refuse rather than
        # publish a world that is half somebody else's.
        raise BundleError(f"world id {wid} was taken while the import ran")
    staging.rename(dest)
    return wid


def import_bundle(path: Path) -> str:
    """Import the bundle at `path` as a brand-new world; returns its id."""
    ensure_home()
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BundleError(f"not a zip archive: {e}")
    with z:
        infos = ziputil.scan(z, max_members=MAX_MEMBERS,
                             max_uncompressed=MAX_UNCOMPRESSED, err=BundleError)
        manifest = _read_manifest(z, infos)
        members = _world_members(infos)
        base = _staging_root() / uuid.uuid4().hex
        try:
            staging = base / WORLD_PREFIX
            staging.mkdir(parents=True)
            ziputil.extract(z, members, staging, strip=1, err=BundleError)
            wid = uniquify(slugify(_world_name(staging, manifest)),
                           lambda c: worlds_paths.world_root(c).exists())
            if wid != manifest["world_id"]:
                _repoint_urls(staging, manifest["world_id"], wid)
            return _publish(staging, wid)
        finally:
            shutil.rmtree(base, ignore_errors=True)
