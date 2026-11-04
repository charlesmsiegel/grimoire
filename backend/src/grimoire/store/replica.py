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
    EnvelopeError, RecordEnvelope, ScopeRecord, SourceManifest, parse_record, parse_scope_record, parse_source,
)
from .errors import InvalidEnvelope, LinkRefused, RevisionReferenced, UnstorableIdentifier
from .jsonfmt import MAX_FILE_BYTES, JsonError, parse_strict
from .layout import (
    CAMPAIGNS, CONFLICTS, MARKER, SOURCES, SYNC, TXN, Parts, classify, is_noise, name_key,
)

#: Root entries a scan walks for canonical records.
_RECORD_ROOTS = (SOURCES, CAMPAIGNS)
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


def parse_exact(parts: Parts, data: bytes) -> RecordEnvelope | SourceManifest | ScopeRecord:
    """The envelope ``data`` holds at canonical ``parts``; ``InvalidEnvelope`` (with ``code``) otherwise."""
    found = classify(parts)
    if found is None or found[0] == "ledger":
        raise _invalid("not_canonical", f"{'/'.join(parts)} is not a canonical record path")
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise _invalid("malformed", f"{'/'.join(parts)}: not bytes, or larger than {MAX_FILE_BYTES}")
    try:
        document = parse_strict(data)
    except JsonError as error:
        raise _invalid("malformed", f"{'/'.join(parts)}: {error}") from None
    try:
        if found[0] == "manifest":
            envelope: RecordEnvelope | SourceManifest | ScopeRecord = parse_source(document)[0]
            if envelope.source != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds the manifest of {envelope.source}")
        elif found[0] in ("scope", "chat"):  # 107's play-scope and chat records
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
            if found is not None and found[0] != "ledger":  # a directory where a record file belongs
                blocked[text] = "unreadable"
                continue
            _walk(access, child, files, blocked, skipped)
        elif kind == "file":
            found = classify(child)
            if found is None or found[0] == "ledger":
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


def check_unreferenced(access: Access, ref: SourceRef) -> None:
    """``RevisionReferenced`` unless nothing on this library can pin ``ref`` (a pinned revision).

    Every parsable manifest (also of an excluded source) and instance is checked: heads, link
    endpoints, ``derived_from`` and ``base``. A 107 scope or chat record's payload is opaque to the
    store, so any text in it that names the exact pinned revision counts as a reference (a chat's
    source bindings). A blocked manifest, instance, scope or chat, or a blocked directory under
    ``sources``/``campaigns``, means "cannot prove unreferenced".
    """
    found = scan(access)
    for path, reason in found.blocked.items():
        kind = classify(tuple(path.split("/")))
        if kind is None or kind[0] in ("manifest", "instance", "scope", "chat"):
            raise RevisionReferenced(f"cannot prove {ref} is unreferenced: {path} is {reason}")
    for path, data in found.files.items():
        envelope = parse_exact(tuple(path.split("/")), data)
        if isinstance(envelope, SourceManifest):
            if any(head == SourceRef(ref.source, ref.kind, ref.entity_id) and revision == ref.revision
                   for head, revision in envelope.head_refs().items()):
                raise RevisionReferenced(f"{path} names {ref} as its head")
            for link in envelope.links:
                if ref in (_pinned(link.subject), _pinned(link.object)):
                    raise RevisionReferenced(f"{path} links {ref}")
        elif isinstance(envelope, ScopeRecord):
            if ref in _payload_refs(envelope.payload):
                raise RevisionReferenced(f"{path} binds {ref}")
        elif isinstance(envelope.address, InstanceRef):
            if ref in (_pinned(envelope.derived_from), _pinned(envelope.base)):
                raise RevisionReferenced(f"{path} is based on {ref}")


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
