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

**Format 2 carries the world's images** (spec section 10). A placement
(``image-refs/<name>.json``) names an object in the *global* image store, so
the world directory alone holds an id and no picture: a format-1 export
imported into another library would leave every placement unresolved (Codex
review). So the export adds an ``image-store/`` prefix beside ``world/``, with
one ``blobs/<xx>/<sha>.<ext>`` and one ``objects/<xx>/<id>.json`` per image any
placement in the world names. The object is a **projection** (`image_store.
project`): global fields plus this world's own associations and reviews, never
``sources``.

Nothing a bundle says about an image is trusted. Every blob is re-hashed
against its name *as received*, then ingested exactly as an upload is --
sanitized, which is idempotent on bytes a store already sanitized, so a
grimoire export re-imports unchanged, while a hand-built blob carrying text
chunks or trailing bytes cannot become the blob a later local upload of the
same pixels dedupes onto. Every object's id is recomputed *here*, and a
staged ref naming a bundle id that differs from the local one is rewritten
before the world is published: a bundle can never claim an id for pixels it
does not contain. Nor can it place one: a staged ref naming an id the bundle
carries no picture for loses its image, and a staged promotion journal is
removed (`_contain_refs`). Descriptions, associations and reviews are merged only after
`staging.publish` has named the world, under ``world:<final id>``, and only
ever fill what the local object lacks (`image_store.merge_projection`). A
failure there is logged rather than raised: the world already exists, and
answering "failed" would invite a retry that imports a second copy.

Blobs and objects are ingested before the world is published, so an import
that fails afterwards leaves an orphan picture behind -- a GC candidate, never
corruption (spec section 12). Format-1 bundles import exactly as before.

**Format 3 carries format-2 image collections.** A format-2 manifest names
image ids rather than library names, so the export packs those pictures as it
packs placed ones, and the import holds the manifest to the bundle's contents
as it holds a placement (`_contain_manifests`): each member is rewritten to its
local id, one the bundle carries no picture for is dropped, and a manifest left
with no members is removed. A bundle is written as format 3 only when it packs
a format-2 manifest, so a world without one stays importable by an older
grimoire; a bundle of format 1 or 2 carries format-1 manifests only, and is
still refused for any other. An export validates, with the store's own rule,
every manifest the import would refuse -- any that parses as a JSON object and
does not say format 1 -- so it never writes one its import refuses.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import hashlib
import importlib.metadata
import json
import os
import re
import stat
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .. import (
    assets,
    atomic,
    fetch,
    image_collections,
    image_refs,
    image_scopes,
    image_store,
    image_surfaces,
    logs,
    paths,
    ziputil,
)
from ..frontmatter import parse_frontmatter
from ..paths import ensure_home, now_iso, safe_id, slugify, uniquify
from ..worlds import paths as worlds_paths
from ..worlds import staging as worlds_staging
from ._contracts import _BLOB_MEMBER as _BLOB_MEMBER
from ._contracts import _COLLECTION_FORMATS as _COLLECTION_FORMATS
from ._contracts import _COLLECTIONS_DIR as _COLLECTIONS_DIR
from ._contracts import _COMPRESSIBLE as _COMPRESSIBLE
from ._contracts import _EXACT_NAMES as _EXACT_NAMES
from ._contracts import _OBJECT_MEMBER as _OBJECT_MEMBER
from ._contracts import _READABLE as _READABLE
from ._contracts import COLLECTIONS_FORMAT as COLLECTIONS_FORMAT
from ._contracts import FORMAT as FORMAT
from ._contracts import MANIFEST_NAME as MANIFEST_NAME
from ._contracts import MAX_BUNDLE_BLOB_BYTES as MAX_BUNDLE_BLOB_BYTES
from ._contracts import MAX_BUNDLE_BYTES as MAX_BUNDLE_BYTES
from ._contracts import MAX_COLLECTION_MANIFEST_BYTES as MAX_COLLECTION_MANIFEST_BYTES
from ._contracts import MAX_FORMAT as MAX_FORMAT
from ._contracts import MAX_IMPORTED_DESCRIPTION as MAX_IMPORTED_DESCRIPTION
from ._contracts import MAX_MEMBERS as MAX_MEMBERS
from ._contracts import MAX_OBJECT_BYTES as MAX_OBJECT_BYTES
from ._contracts import MAX_OBJECT_MEMBER_BYTES as MAX_OBJECT_MEMBER_BYTES
from ._contracts import MAX_UNCOMPRESSED as MAX_UNCOMPRESSED
from ._contracts import STORE_PREFIX as STORE_PREFIX
from ._contracts import WORLD_PREFIX as WORLD_PREFIX
from ._contracts import BundleConflict as BundleConflict
from ._contracts import BundleError as BundleError
from ._contracts import _checked_by_import as _checked_by_import
from ._contracts import _Entry as _Entry
from ._contracts import _fold as _fold
from ._contracts import _is_manifest as _is_manifest
from ._contracts import _Members as _Members
from ._contracts import _ObjectMeta as _ObjectMeta
from ._contracts import _strict_manifest as _strict_manifest
from ._export import _check_blob_sizes as _check_blob_sizes
from ._export import _check_whole as _check_whole
from ._export import _check_written as _check_written
from ._export import _image_entries as _image_entries
from ._export import _manifest_ids as _manifest_ids
from ._export import _oversized as _oversized
from ._export import _packable_blob as _packable_blob
from ._export import _packable_object as _packable_object
from ._export import _packed_manifests as _packed_manifests
from ._export import _pin as _pin
from ._export import _world_entries as _world_entries
from ._export import _write_blob as _write_blob
from ._export import app_version as app_version
from ._export import bundle_filename as bundle_filename
from ._export import write_bundle as write_bundle
from ._images import _contain_manifest as _contain_manifest
from ._images import _contain_manifests as _contain_manifests
from ._images import _contain_ref as _contain_ref
from ._images import _contain_refs as _contain_refs
from ._images import _folded_children as _folded_children
from ._images import _ingest_blobs as _ingest_blobs
from ._images import _merge_metadata as _merge_metadata
from ._images import _projection as _projection
from ._import import _world_name as _world_name
from ._import import import_bundle as import_bundle
from ._validation import _check_collections as _check_collections
from ._validation import _check_placement_spelling as _check_placement_spelling
from ._validation import _meta_of as _meta_of
from ._validation import _read_manifest as _read_manifest
from ._validation import _read_member as _read_member
from ._validation import _read_objects as _read_objects
from ._validation import _world_members as _world_members

# Original star-import surface, captured before extraction.
__all__ = [
    'COLLECTIONS_FORMAT',
    'FORMAT',
    'MANIFEST_NAME',
    'MAX_BUNDLE_BLOB_BYTES',
    'MAX_BUNDLE_BYTES',
    'MAX_COLLECTION_MANIFEST_BYTES',
    'MAX_FORMAT',
    'MAX_IMPORTED_DESCRIPTION',
    'MAX_MEMBERS',
    'MAX_OBJECT_BYTES',
    'MAX_OBJECT_MEMBER_BYTES',
    'MAX_UNCOMPRESSED',
    'STORE_PREFIX',
    'WORLD_PREFIX',
    'BundleConflict',
    'BundleError',
    'Path',
    'annotations',
    'app_version',
    'assets',
    'atomic',
    'bundle_filename',
    'dataclass',
    'ensure_home',
    'fetch',
    'hashlib',
    'image_collections',
    'image_refs',
    'image_scopes',
    'image_store',
    'image_surfaces',
    'import_bundle',
    'importlib',
    'json',
    'logs',
    'now_iso',
    'os',
    'parse_frontmatter',
    'paths',
    're',
    'safe_id',
    'slugify',
    'stat',
    'suppress',
    'uniquify',
    'worlds_paths',
    'worlds_staging',
    'write_bundle',
    'zipfile',
    'ziputil',
]
