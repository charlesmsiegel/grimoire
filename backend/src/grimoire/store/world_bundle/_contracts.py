"""Bundle formats, bounded archive vocabulary and shared manifest rules."""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .. import fetch, image_collections, image_refs, image_store

#: The format written for a world that packs no format-2 collection manifest,
#: so an older grimoire can still import it.
FORMAT = 2
#: The format written when the export packs a format-2 collection manifest.
COLLECTIONS_FORMAT = 3
#: The highest format this grimoire reads.
MAX_FORMAT = 3
#: Every format this grimoire reads. Format 1 is format 2 without images, and
#: format 2 is format 3 without format-2 collection manifests.
_READABLE = frozenset({1, 2, 3})
MANIFEST_NAME = "grimoire-bundle.json"
WORLD_PREFIX = "world"
STORE_PREFIX = "image-store"

# The only shapes an `image-store/` member may take. Hex is lower-case, and the
# shard must equal the hash's own prefix (checked separately: a regex cannot
# say "these two groups agree" without a backreference that reads worse).
_BLOB_MEMBER = re.compile(
    r"image-store/blobs/([0-9a-f]{2})/([0-9a-f]{64})\.(png|jpg|gif|webp)")
_OBJECT_MEMBER = re.compile(
    r"image-store/objects/([0-9a-f]{2})/(px1-([0-9a-f]{64}))\.json")
# The largest image blob a bundle carries, held on BOTH sides: export refuses
# a world holding a bigger one before it writes anything (`_check_blob_sizes`),
# and import refuses a bigger member -- so an export never produces a bundle
# its own import refuses. It is the fetch cap, the largest image the app takes
# from anywhere else; record uploads themselves are not capped, which is why
# export has to ask.
MAX_BUNDLE_BLOB_BYTES = fetch.MAX_BYTES
# A projected sidecar is a few hundred bytes. This caps the *sum* over every
# object member, checked from the headers before any is read: a per-object cap
# alone let a small archive of many objects naming one tiny blob expand into
# gigabytes of parsed metadata (review).
MAX_OBJECT_BYTES = 64 * 1024 * 1024
# ...and each object on its own, refused from its header before it is read:
# the total cap alone still let one 64 MiB object be parsed whole.
MAX_OBJECT_MEMBER_BYTES = 1024 * 1024
# A collection manifest holds at most 10 000 member names or ids, well under a
# megabyte; this is room for that and a refusal for anything shaped otherwise.
MAX_COLLECTION_MANIFEST_BYTES = 16 * 1024 * 1024
#: The collection manifest formats each bundle format may carry. Before format
#: 3 a bundle carried format-1 manifests only, and still may not carry another.
_COLLECTION_FORMATS = {1: frozenset({1}), 2: frozenset({1}), 3: frozenset({1, 2})}
#: Where a collection manifest sits, relative to the world root, as a
#: case-insensitive filesystem compares it (`_fold`).
_COLLECTIONS_DIR = ("assets", "image-collections")
# The longest bundle description merged. Longer is not a description of a
# picture; it is dropped (and logged) and the image imports undescribed. The
# store's one cap, so an import cannot carry text a description write refuses.
MAX_IMPORTED_DESCRIPTION = image_store.MAX_DESCRIPTION

# Sized for a real library rather than a module pack: worlds here run to
# thousands of files and a gigabyte of character art, so these are a guard
# against a hostile archive filling the disk, not a policy on world size.
MAX_MEMBERS = 100_000
MAX_UNCOMPRESSED = 8 * 1024 * 1024 * 1024
# The largest bundle the import route takes. Here rather than in the route so
# that export can hold its own output to it (`_check_written`).
MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024

# What is worth deflating on the way out: anything textual. Getting this wrong
# costs CPU, nothing else. Deliberately NOT the same set as the one the import
# may rewrite (`worlds.staging._REWRITABLE`) -- Codex review found the two
# conflated, and an `.svg` portrait rewritten as a result.
_COMPRESSIBLE = frozenset({".md", ".json", ".txt", ".csv", ".css", ".html",
                           ".svg", ".yaml", ".yml"})


class BundleError(Exception):
    """A bundle that cannot be read, or is not one."""


class BundleConflict(BundleError):
    """A readable bundle that could not be published -- a lost id race, not a
    bad file. Separated so the route can answer 409 rather than blaming the
    upload with a 400 (Codex review).

    The bundle-shaped face of `worlds.staging.WorldIdConflictError`, which fork
    raises too: this stays a `BundleError` so one `except` in the route still
    covers everything an import can refuse for."""


@dataclass(frozen=True)
class _Entry:
    """One member `write_bundle` will write: a file copied from `src`, or
    `data` written as is."""
    arc: str
    compress: int = zipfile.ZIP_STORED
    src: Path | None = None
    data: bytes | None = None
    size: int = 0
    #: A collection's member position cannot be dropped on import (#478).
    required: str | None = None
    #: The regular blob validated before packing, not a replacement path (#478).
    blob_stat: os.stat_result | None = None
    #: A projection travels only with its blob, even if GC wins mid-pack (#478).
    requires_blob: Path | None = None

    def __post_init__(self) -> None:
        if self.data is not None:
            object.__setattr__(self, "size", len(self.data))


def _is_manifest(rel: list[str]) -> bool:
    """Is the world-relative path `rel` a collection manifest, as a
    case-insensitive filesystem would open it? Both sides of a bundle ask this
    one question, so export validates exactly what import checks and contains."""
    return (len(rel) == 3 and tuple(_fold(p) for p in rel[:2]) == _COLLECTIONS_DIR
            and _fold(rel[2]).endswith(".json"))


def _strict_manifest(data: bytes) -> dict:
    """`data` as the store reads a manifest: strict UTF-8, then the store's own
    rule. Raises `ValueError` (a `CollectionInvalidError`, a JSON error or a
    `UnicodeDecodeError`) or `RecursionError` for one that does not read."""
    return image_collections.validate(json.loads(data.decode("utf-8")))


def _checked_by_import(data: bytes) -> bool:
    """Does `_check_collections` hold this manifest to a rule? Not when it is no
    JSON object (parsed as the import parses it) or says format 1."""
    try:
        raw = json.loads(data)
    except (ValueError, RecursionError):
        return False
    if not isinstance(raw, dict):
        return False
    fmt = raw.get("format")
    return not (type(fmt) is int and fmt == 1)


@dataclass
class _Members:
    world: list[zipfile.ZipInfo]
    #: blob sha -> its member.
    blobs: dict[str, zipfile.ZipInfo]
    #: bundle-claimed image id -> its member.
    objects: dict[str, zipfile.ZipInfo]


#: Names a placement reader opens by exact spelling. On a case-insensitive
#: filesystem any other spelling opens the same file, so it is refused.
_EXACT_NAMES = (image_refs.REFS_DIR, image_refs.JOURNAL)


def _fold(name: str) -> str:
    """`name` as a case-insensitive filesystem compares it. Upper-casing first
    is what catches a dotless i (U+0131), which ``casefold`` leaves alone but NTFS
    upper-cases to ``I``."""
    return name.upper().casefold()


@dataclass(frozen=True)
class _ObjectMeta:
    """What the post-publish merge needs from one bundled object, and nothing
    else: the parsed sidecar is dropped as soon as this is taken from it."""
    blob_sha: str
    description: str | None
    #: The bundle's own-scope associations, scope still the source world's.
    associations: tuple[dict, ...]
    #: Whether the source world's scope is among the reviewed subjects.
    reviewed: bool
