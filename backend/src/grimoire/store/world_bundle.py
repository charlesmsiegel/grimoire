"""World bundles: a world directory zipped whole, importable as a new world (#54).

The store layout *is* the exchange format. A world directory is already
self-contained -- ``world.md``, the entity kind-folders, ``characters/`` and
``pcs/`` with their per-version assets, ``greetings/``, ``sheets/``,
``plotmap.json``, ``tags.md``, ``calendar.json`` -- so the export walks it and
the import puts it back. Nothing enumerates the kinds, which is the point: a
kind added next month rides along without touching this file.

A ``grimoire-bundle.json`` manifest sits at the archive root beside the
``world/`` prefix, recording the format version, the source world id, its name
and the exporting grimoire's version. It buys two things a bare directory zip
cannot: an import can refuse a bundle from a future grimoire with an honest
message instead of half-extracting one, and it carries the **source world id**,
which the import needs.

The ``world/`` prefix is a deliberate departure from #54's "paths relative to
the world root", and the manifest is what forces it: with world files at the
archive root there is no way to tell bundle metadata from world content except
by knowing every filename grimoire will ever use, and the whole point of
zipping the directory is that no such list exists. One prefix keeps the two
apart forever. Anything outside it is refused rather than guessed at.

That id is the one thing that does not travel: ``store/localize.py`` writes
absolute serving URLs into card and greeting text --
``/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}`` and
``/api/worlds/{wid}/greetings/{gid}/images/{name}`` -- and a PC persona can
carry the matching ``/pcs/{pid}/versions/{vid}/images/{name}`` by hand, so a
world landing under a new id would render every localized image as a 404. Import rewrites that
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

import importlib.metadata
import json
import re
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

# Two different questions, deliberately answered by two different sets (Codex
# review found them conflated, and an `.svg` portrait rewritten as a result).
#
# What is worth deflating on the way out: anything textual. Getting this wrong
# costs CPU, nothing else.
_COMPRESSIBLE = frozenset({".md", ".json", ".txt", ".csv", ".css", ".html",
                           ".svg", ".yaml", ".yml"})
# What the import may rewrite: exactly the two extensions the store writes its
# *records* in. Getting this wrong edits a user's asset, so it is a closed list
# rather than "textual and not under a directory called assets" -- `.svg` is a
# text format and an image at once, and no directory-name heuristic can tell
# which one a given file is.
_REWRITABLE = frozenset({".md", ".json"})


class BundleError(Exception):
    """A bundle that cannot be read, or is not one."""


class BundleConflict(BundleError):
    """A readable bundle that could not be published -- a lost id race, not a
    bad file. Separated so the route can answer 409 rather than blaming the
    upload with a 400 (Codex review)."""


def _staging_root() -> Path:
    return home() / ".world-staging"


def app_version() -> str:
    """The running grimoire's version, for the manifest. Purely informational:
    compatibility is decided by ``format``, and this is what someone reads when
    a bundle behaves oddly. Best-effort -- an uninstalled source checkout (or a
    packaging layout without metadata, which the Android build may be) has no
    distribution to ask, and that must not fail an export."""
    try:
        return importlib.metadata.version("grimoire")
    except Exception:  # noqa: BLE001 -- any metadata problem is "unknown", never a failed export
        return "unknown"


def bundle_filename(wid: str) -> str:
    return f"{wid}-world.zip"


# ---- export ----

def _is_write_temp(path: Path) -> bool:
    """An ``store.atomic`` temp caught mid-write.

    Not part of the world, and the writer that owns it will rename or unlink it
    out from under the walk -- so packing one is both wrong and racy.

    Matched against what ``atomic._mkstemp_beside`` actually produces --
    ``.<target-name>.<mkstemp's 8 random chars>.tmp`` -- rather than the
    dot-prefix-and-.tmp-suffix approximation this started as, which also
    swallowed a legitimate ``.notes.tmp`` sitting in the world (Codex review).
    """
    return bool(_ATOMIC_TEMP.fullmatch(path.name))


_ATOMIC_TEMP = re.compile(r"\..+\.[a-z0-9_]{8}\.tmp")


def write_bundle(wid: str, dest: Path) -> None:
    """Zip the world at `wid` into `dest`. Raises ``WorldNotFound``.

    Written to a path rather than returned as bytes: a world with a full
    character gallery runs past a gigabyte, and holding that in memory to hand
    to a response is not something a phone-sized process survives.

    A best-effort snapshot, not a locked one -- a world edited during the walk
    can be packed half-old and half-new. Files that vanish mid-walk are skipped
    rather than failing the export: by then they are genuinely not part of the
    world any more.

    Symlinks are skipped. ``is_file()`` follows them, so a link inside the world
    would otherwise be packed as a *copy of whatever it points at* -- and the
    bundle is a file the user hands to somebody else, which makes that an
    exfiltration path out of a directory the user may not have written
    themselves (Codex review). Import already refuses symlink members, so
    nothing that round-trips through here can contain one either way.
    """
    root = worlds_paths.world_root(wid)                     # rejects an unsafe id
    meta_path = worlds_paths.world_meta_path(wid)
    if not meta_path.exists():
        raise worlds_paths.WorldNotFound(wid)
    meta, _body = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    manifest = {"format": FORMAT, "kind": "world", "world_id": wid,
                "name": meta.get("name", wid), "app_version": app_version(),
                "exported": now_iso()}

    with zipfile.ZipFile(dest, "w") as z:
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n",
                   compress_type=zipfile.ZIP_DEFLATED)
        for p in sorted(root.rglob("*")):
            try:
                if _is_write_temp(p) or p.is_symlink() or not p.is_file():
                    continue
            except OSError:
                continue                                    # vanished mid-walk
            arc = f"{WORLD_PREFIX}/{p.relative_to(root).as_posix()}"
            compress = (zipfile.ZIP_DEFLATED if p.suffix.lower() in _COMPRESSIBLE
                        else zipfile.ZIP_STORED)
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
    # `type(...) is int`, not isinstance: JSON `true` is a Python bool, bool is
    # a subclass of int, and `True == 1` -- so `{"format": true}` would have
    # been read as format 1 (Codex review).
    if type(fmt) is not int or fmt != FORMAT:
        # Named separately because the fix differs: a newer bundle needs a
        # newer grimoire, anything else is a broken file.
        if type(fmt) is int and fmt > FORMAT:
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

    Byte-level, and over ``.md``/``.json`` only -- the two extensions the store
    writes its records in, which is also exactly the scope #54 specified. A
    substitution that had to decode every file would fail on the first asset,
    and one that touched an asset would corrupt it. The prefix carries its
    trailing slash so a world id that is a prefix of another (``realm`` beside
    ``realm-2``) cannot be rewritten by half.

    A file's *extension* decides this, with no exception for where it sits.
    Widening to every textual suffix pulled in ``.svg``, which is a text format
    and an image at once, so a portrait got edited (Codex review); narrowing
    that back out with a "not under a directory called assets" rule only traded
    one guess for another, and would have skipped a genuine ``.md`` record that
    happened to live under such a directory. The sidecars that do sit under
    ``assets/`` are scanned along with everything else. ``subjects.json`` and
    ``focus.json`` hold ids and offsets, so for them this is a no-op.
    ``descriptions.json`` is the one that holds free prose an author wrote, and
    so *can* contain a URL — which is a reason to scan it rather than to skip
    it: a description naming an image by its world-scoped URL should follow the
    world being renamed exactly as a record body does. The prefix carries its
    trailing slash, so the substitution is as precise here as in a ``.md``.
    """
    old = f"/api/worlds/{old_wid}/".encode()
    new = f"/api/worlds/{new_wid}/".encode()
    touched = 0
    for p in staging.rglob("*"):
        if p.suffix.lower() not in _REWRITABLE or not p.is_file():
            continue
        data = p.read_bytes()
        if old in data:
            atomic.write_bytes(p, data.replace(old, new))
            touched += 1
    return touched


_PUBLISH_ATTEMPTS = 4


def _publish(staging: Path, base: str, current: str) -> str:
    """Move the staged tree into the library under a free id; return that id.

    ``uniquify`` picked ``current`` a moment ago, so a concurrent import can
    have taken it in between -- and a plain rename would *merge into* it on
    POSIX when the destination happens to be an empty directory. So the id is
    re-picked and retried rather than refused: losing a race is not a reason to
    reject a perfectly good bundle (Codex review). Each retry re-points the
    URLs from the id the records currently carry to the new candidate, so the
    published world always references itself.
    """
    for attempt in range(_PUBLISH_ATTEMPTS):
        dest = worlds_paths.world_root(current)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            try:
                staging.rename(dest)
                return current
            except OSError:
                pass          # lost the race inside the check-to-rename window
        if attempt == _PUBLISH_ATTEMPTS - 1:
            break
        nxt = uniquify(base, lambda c: worlds_paths.world_root(c).exists())
        _repoint_urls(staging, current, nxt)
        current = nxt
    raise BundleConflict(
        f"could not claim a world id for the import (last tried {current})")


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
            base = slugify(_world_name(staging, manifest))
            wid = uniquify(base, lambda c: worlds_paths.world_root(c).exists())
            if wid != manifest["world_id"]:
                _repoint_urls(staging, manifest["world_id"], wid)
            return _publish(staging, base, wid)
        finally:
            shutil.rmtree(base, ignore_errors=True)
