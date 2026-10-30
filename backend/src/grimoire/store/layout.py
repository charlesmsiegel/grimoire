"""Where records live: an injective, portable mapping from identity to paths.

Each ``:`` segment of a source id is one directory and a source's own files
live in its ``_source/`` directory. An ID segment never starts with ``_``, so
``_source`` cannot collide with a sub-source: ``world:harbor:dlc`` is a
*sibling* of harbor's ``_source/``. Every authored path segment must be a valid
``ResourcePath`` segment, so it is portable (no Windows device names, no
trailing dots) and the whole path is the record's logical address for
authorization. Identifiers that cannot be mapped are refused, never renamed.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Literal

from grimoire.core.identity import InstanceRef, SourceRef, validate_segment, validate_source_id
from grimoire.security import InvalidResourcePath, ResourcePath

from .errors import UnstorableIdentifier

Parts = tuple[str, ...]

MARKER = "grimoire-library.json"
LIBRARY_FORMAT = "grimoire.library/1"
SOURCES = "sources"
CAMPAIGNS = "campaigns"
TXN = ".txn"
CONFLICTS = ".conflicts"
LOCK = "lock"
JOURNAL = "journal.json"
DISCARD_MARKER = "discard.json"  # an interrupted discard_journal: never publish, finish the discard
SOURCE_DIR = "_source"
MANIFEST = "manifest.json"
RECORDS = "records"
INSTANCES = "instances"
SUFFIX = ".json"

ROOT_ENTRIES = frozenset({MARKER, SOURCES, CAMPAIGNS, TXN, CONFLICTS})
#: Reserved for later record families; the loader skips them.
RESERVED_ROOTS = frozenset({"authority", "config", "assets", "secrets"})
RESERVED_CAMPAIGN_ENTRIES = frozenset({"scenes", "events", "evidence", "relations"})
_NOISE = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

Kind = Literal["definition", "manifest", "instance"]


def is_noise(name: str) -> bool:
    """Operating-system and editor droppings, ignored without a report."""
    return (name in _NOISE or name.endswith("~") or name.endswith((".swp", ".swo"))
            or name.startswith(".#"))


def name_key(name: str) -> str:
    """The comparison a case-insensitive or normalizing filesystem might make."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", name).casefold())


def logical(parts: Parts) -> ResourcePath:
    """The record's logical address; refuses what cannot be a portable path."""
    try:
        return ResourcePath(tuple(parts))
    except InvalidResourcePath as error:
        raise UnstorableIdentifier(f"{'/'.join(parts)!r} cannot be stored portably: {error}") from None


def _checked(parts: Parts) -> Parts:
    logical(parts)
    return parts


def source_parts(source_id: str) -> Parts:
    return _checked((SOURCES, *source_id.split(":"), SOURCE_DIR))


def manifest_parts(source_id: str) -> Parts:
    return _checked(source_parts(source_id) + (MANIFEST,))


def definition_parts(ref: SourceRef) -> Parts:
    if not ref.pinned:
        raise ValueError(f"a definition file is one pinned revision, not {ref}")
    return _checked(source_parts(ref.source) + (RECORDS, ref.kind, ref.entity_id, ref.revision + SUFFIX))


def instance_parts(ref: InstanceRef) -> Parts:
    return _checked((CAMPAIGNS, ref.campaign_id, INSTANCES, ref.kind, ref.instance_id + SUFFIX))


def address_parts(address: SourceRef | InstanceRef) -> Parts:
    if isinstance(address, InstanceRef):
        return instance_parts(address)
    return definition_parts(address)


def _stem(name: str) -> str | None:
    if not name.endswith(SUFFIX):
        return None
    stem = name[: -len(SUFFIX)]
    try:
        validate_segment(stem, "file name")
    except ValueError:
        return None
    return stem


def classify(parts: Parts) -> tuple[Kind, SourceRef | InstanceRef | str] | None:
    """The record a canonical path holds, or ``None``; exact inverse of the mappings."""
    parts = tuple(parts)
    found: tuple[Kind, SourceRef | InstanceRef | str] | None = None
    try:
        if len(parts) == 5 and parts[0] == CAMPAIGNS and parts[2] == INSTANCES:
            stem = _stem(parts[4])
            if stem is not None:
                found = ("instance", InstanceRef(parts[1], parts[3], stem))
        elif parts and parts[0] == SOURCES and SOURCE_DIR in parts:
            at = parts.index(SOURCE_DIR)
            source_id = ":".join(parts[1:at])
            validate_source_id(source_id)
            tail = parts[at + 1:]
            if tail == (MANIFEST,):
                found = ("manifest", source_id)
            elif len(tail) == 4 and tail[0] == RECORDS:
                stem = _stem(tail[3])
                if stem is not None:
                    found = ("definition", SourceRef(source_id, tail[1], tail[2], stem))
    except ValueError:
        return None
    if found is None:
        return None
    kind, value = found
    try:
        canonical = manifest_parts(value) if kind == "manifest" else address_parts(value)  # type: ignore[arg-type]
    except (ValueError, UnstorableIdentifier):
        return None
    return found if canonical == parts else None


def conflict_parts(target: Parts, new_etag: str, attempt: int = 0) -> Parts:
    """A bounded, flat home for a preserved version of ``target``.

    ``attempt`` distinguishes different bytes that would otherwise share a name
    (an edited conflict file, or two versions with the same digest prefix).
    """
    folder = hashlib.sha256("/".join(target).encode("utf-8")).hexdigest()[:16]
    name = f"{target[-1]}~{new_etag.removeprefix('sha256:')[:12]}"
    return (CONFLICTS, folder, name if not attempt else f"{name}.{attempt}")
