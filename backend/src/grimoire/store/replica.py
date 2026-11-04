"""Exact-bytes access for synchronization (019): validate, scan and reference-check canonical files.

Sync copies another device's file *byte for byte*, so both devices converge on one etag; the
store's own ``put`` would re-render it and rewrite ``previous``. Everything here only reads.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, SourceRef

from .diskops import is_link, lstat_or_none
from .engine import Access
from .envelope import (
    EnvelopeError, KnowledgeRecord, RecordEnvelope, ScopeRecord, SourceManifest, parse_knowledge_record,
    parse_record, parse_scope_record, parse_source,
)
from .errors import InvalidEnvelope, LinkRefused, RevisionReferenced, UnstorableIdentifier
from .jsonfmt import MAX_FILE_BYTES, JsonError, parse_strict
from .jsonfmt import etag as _etag
from .layout import (
    CAMPAIGNS, CONFLICTS, MARKER, SCOPE, SOURCES, SUFFIX, SYNC, TXN, Parts, classify, is_noise, name_key,
)

SCOPE_FILE = SCOPE

#: Root entries a scan walks for canonical records.
_RECORD_ROOTS = (SOURCES, CAMPAIGNS)
#: The record kinds this module reads, copies and checks: every kind sync may own. 018's blobs,
#: images and placements and 021's accepted events and wrap-ups are not among them yet (a later
#: slice); ledgers never are.
EXACT_KINDS = frozenset({"definition", "manifest", "instance", "scope", "chat", "relation", "knowledge"})
#: Root entries that are device-local or the library's own marker: never transferred, never reported.
_LOCAL = frozenset({TXN, CONFLICTS, SYNC, MARKER})


@dataclass(frozen=True)
class CanonicalScan:
    """Every canonical record file of one library, by root-relative path.

    ``blocked`` maps a path (a file, or a directory whose whole subtree is unusable) to a reason:
    ``link``, ``lookalike``, ``unreadable``, ``malformed``, ``unsupported_format`` or
    ``address_mismatch``. ``skipped`` lists entries that are not canonical records.
    """

    files: Mapping[str, bytes] = field(default_factory=dict)
    blocked: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    def blocked_reason(self, path: str) -> str | None:
        """Why ``path`` is unusable here: blocked itself or below a blocked directory."""
        if path in self.blocked:
            return self.blocked[path]
        parts = path.split("/")
        for index in range(1, len(parts)):
            reason = self.blocked.get("/".join(parts[:index]))
            if reason is not None:
                return reason
        return None


def parse_exact(parts: Parts, data: bytes) -> RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord:
    """The envelope ``data`` holds at canonical ``parts``; ``InvalidEnvelope`` (with ``code``) otherwise."""
    found = classify(parts)
    if found is None or found[0] not in EXACT_KINDS:
        raise _invalid("not_canonical", f"{'/'.join(parts)} is not a canonical record path")
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise _invalid("malformed", f"{'/'.join(parts)}: not bytes, or larger than {MAX_FILE_BYTES}")
    try:
        document = parse_strict(data)
    except JsonError as error:
        raise _invalid("malformed", f"{'/'.join(parts)}: {error}") from None
    try:
        if found[0] == "manifest":
            envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord = parse_source(document)[0]
            if envelope.source != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds the manifest of {envelope.source}")
        elif found[0] == "knowledge":  # 020's create-only events, evidence and log batches
            envelope = parse_knowledge_record(document)[0]
            if envelope.address != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds {envelope.address}")
        elif found[0] in ("scope", "chat", "relation"):  # 107's scope and chat, 017's relation records
            envelope = parse_scope_record(document)[0]
            if envelope.address != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds {envelope.address}")
        else:
            envelope = parse_record(document)[0]
            if envelope.address != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds {envelope.address}")
    except EnvelopeError as error:
        raise _invalid(error.code, f"{'/'.join(parts)}: {error.detail}") from None
    try:
        envelope.check_writable()
    except InvalidEnvelope as error:
        raise _invalid("malformed", str(error)) from None
    return envelope


def _invalid(code: str, detail: str) -> InvalidEnvelope:
    error = InvalidEnvelope(detail)
    error.code = code  # type: ignore[attr-defined]
    return error


def previous_of(data: bytes) -> str | None:
    """The ``previous`` etag a record file declares, or ``None`` (unreadable files have none)."""
    try:
        value = parse_strict(data).get("previous")
    except JsonError:
        return None
    return value if isinstance(value, str) else None


def scan(access: Access) -> CanonicalScan:
    """Walk the library through the seam; links are blocked, never listed or followed."""
    files: dict[str, bytes] = {}
    blocked: dict[str, str] = {}
    skipped: list[str] = []
    with access.cached_listings():
        for name, kind in _children(access, (), blocked):
            if kind == "skip" or name in _LOCAL or is_noise(name):
                continue
            if name in _RECORD_ROOTS and kind == "dir":
                _walk(access, (name,), files, blocked, skipped)
            else:
                skipped.append(name)
    return CanonicalScan(MappingProxyType(files), MappingProxyType(blocked), tuple(sorted(skipped)))


def _children(access: Access, parts: Parts, blocked: dict[str, str]) -> list[tuple[str, str]]:
    """(name, "dir" | "file" | "skip") for each entry; links and look-alike spellings are blocked."""
    try:
        names = access.list_dir(parts)
    except LinkRefused:
        blocked["/".join(parts)] = "link"
        return []
    except OSError:
        blocked["/".join(parts)] = "unreadable"
        return []
    keys: dict[str, int] = {}
    for name in names:
        if not is_noise(name):
            keys[name_key(name)] = keys.get(name_key(name), 0) + 1
    found = []
    for name in names:
        child = parts + (name,)
        text = "/".join(child)
        if is_noise(name):
            found.append((name, "skip"))
            continue
        if is_link(access.path(child)):
            blocked[text] = "link"
            found.append((name, "skip"))
            continue
        if keys.get(name_key(name), 0) > 1:
            blocked[text] = "lookalike"
            found.append((name, "skip"))
            continue
        info = lstat_or_none(access.path(child))
        if info is None:
            continue
        found.append((name, "dir" if stat.S_ISDIR(info.st_mode) else "file"))
    return found


def _walk(access: Access, parts: Parts, files: dict[str, bytes], blocked: dict[str, str],
          skipped: list[str]) -> None:
    for name, kind in _children(access, parts, blocked):
        child = parts + (name,)
        text = "/".join(child)
        if kind == "dir":
            found = classify(child)
            if found is not None and found[0] in EXACT_KINDS:  # a directory where a record file belongs
                blocked[text] = "unreadable"
                continue
            _walk(access, child, files, blocked, skipped)
        elif kind == "file":
            found = classify(child)
            if found is None or found[0] not in EXACT_KINDS:  # ledgers, and 018's images (not synced yet)
                skipped.append(text)
                continue
            try:
                if access.too_large(child):
                    blocked[text] = "unreadable"
                    continue
                data = access.read(child)
            except (OSError, UnstorableIdentifier):
                blocked[text] = "unreadable"
                continue
            if data is None:
                continue
            try:
                parse_exact(child, data)
            except InvalidEnvelope as error:
                blocked[text] = getattr(error, "code", "malformed")
                continue
            files[text] = data


_RECORD_FOLDERS = frozenset({"instances", "chats", "relations", "events", "evidence"})


def _may_hold_records(parts: Parts) -> bool:
    """Whether a blocked path could be, or contain, a canonical record (else it cannot pin anything)."""
    found = classify(parts)
    if found is not None:
        return found[0] in EXACT_KINDS  # 018 images and placements attach to entities, never revisions
    if parts[0] == SOURCES:
        return "." not in parts[-1] or parts[-1].endswith(SUFFIX)  # a folder, or a JSON file
    if parts[0] != CAMPAIGNS:
        return False  # the library root's other entries never hold records
    if len(parts) <= 2:
        return True  # campaigns/ itself, or one campaign folder
    return parts[2] in _RECORD_FOLDERS or parts[2] == SCOPE_FILE


def check_unreferenced(access: Access, ref: SourceRef, *, allowed: frozenset[str] = frozenset()) -> None:
    """``RevisionReferenced`` unless nothing on this library can pin ``ref`` (a pinned revision).

    Checked across every parsable canonical record, those of excluded sources included:
    - manifests: heads and both link endpoints;
    - instances: ``derived_from``, ``base``, and any pinned reference in the payload or extra fields;
    - definitions: any pinned reference in another revision's payload;
    - 107 scope/chat, 017 relation and 020 knowledge records: any text in the opaque payload that
      names the revision (a pinned reference, or an unpinned one beside a ``resolved_revision``).
    A blocked path that could be or hold such a record means "cannot prove unreferenced"; a blocked
    entry that cannot (a root-level link, a non-JSON note) does not. ``allowed`` names manifest
    etags whose references are accepted (``resolve``: manifests identical to the winner's).
    """
    found = scan(access)
    for path, reason in found.blocked.items():
        if _may_hold_records(tuple(path.split("/"))):
            raise RevisionReferenced(f"cannot prove {ref} is unreferenced: {path} is {reason}")
    for path, data in found.files.items():
        envelope = parse_exact(tuple(path.split("/")), data)
        if isinstance(envelope, SourceManifest):
            if _etag(data) in allowed:
                continue
            if any(head == SourceRef(ref.source, ref.kind, ref.entity_id) and revision == ref.revision
                   for head, revision in envelope.head_refs().items()):
                raise RevisionReferenced(f"{path} names {ref} as its head")
            for link in envelope.links:
                if ref in (_pinned(link.subject), _pinned(link.object)):
                    raise RevisionReferenced(f"{path} links {ref}")
        elif isinstance(envelope, (ScopeRecord, KnowledgeRecord)):
            if ref in _payload_refs(envelope.payload):
                raise RevisionReferenced(f"{path} binds {ref}")
        elif isinstance(envelope.address, InstanceRef):
            if ref in (_pinned(envelope.derived_from), _pinned(envelope.base)):
                raise RevisionReferenced(f"{path} is based on {ref}")
            if ref in _payload_refs(envelope.payload) | _payload_refs(envelope.extra):
                raise RevisionReferenced(f"{path} pins {ref} in its payload")
        elif envelope.address != ref and ref in _payload_refs(envelope.payload) | _payload_refs(envelope.extra):
            raise RevisionReferenced(f"{path} pins {ref} in its payload")


def _payload_refs(value: object) -> set[SourceRef]:
    """Every pinned revision an opaque payload can mean, conservatively.

    Any text that is a pinned reference counts, and so does an unpinned reference beside a
    ``resolved_revision`` (107 stores a head-following binding that way: the revision it meant
    when it was introduced).
    """
    found: set[SourceRef] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            pinned = _pinned(item)
            if pinned is not None:
                found.add(pinned)
        elif isinstance(item, Mapping):
            revision = item.get("resolved_revision")
            for key, child in item.items():
                stack.append(key)
                stack.append(child)
                if isinstance(child, str) and isinstance(revision, str):
                    try:
                        ref = SourceRef.parse(child)
                        found.add(ref if ref.pinned else ref.at(revision))
                    except ValueError:
                        pass
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


def _pinned(text: object) -> SourceRef | None:
    if not isinstance(text, str):
        return None
    try:
        ref = SourceRef.parse(text)
    except ValueError:
        return None
    return ref if ref.pinned else None


__all__ = ["CanonicalScan", "check_unreferenced", "parse_exact", "previous_of", "scan"]
