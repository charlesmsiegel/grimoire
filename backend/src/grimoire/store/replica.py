"""Exact-bytes access for synchronization (019): validate, scan and reference-check canonical files.

Sync copies another device's file *byte for byte*, so both devices converge on one etag; the
store's own ``put`` would re-render it and rewrite ``previous``. Everything here only reads.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import TEXT_MAX, ChatRef, ImageRef, InstanceRef, SourceRef, validate_segment

from .diskops import is_link, lstat_or_none
from .engine import Access
from .envelope import (
    AssetRecord, EnvelopeError, KnowledgeRecord, RecordEnvelope, ScopeRecord, SourceManifest, parse_asset_record,
    parse_knowledge_record, parse_record, parse_scope_record, parse_source,
)
from .errors import InvalidEnvelope, LinkRefused, RevisionReferenced, UnstorableIdentifier
from .jsonfmt import MAX_FILE_BYTES, JsonError, parse_strict
from .jsonfmt import etag as _etag
from .layout import (
    ASSETS, BLOBS, CAMPAIGNS, CONFLICTS, IMAGES, MARKER, PLACEMENTS, SCOPE, SECRETS, SOURCE_DIR, SOURCES, SUFFIX, SYNC,
    TXN,
    Parts, blob_parts, classify, chat_parts, definition_parts, instance_parts, is_noise, name_key,
)
from .media import MAX_BLOB_BYTES, MEDIA

SCOPE_FILE = SCOPE
#: The blocked reason of a canonical spelling that exists here only as a look-alike (``R9.json`` for ``r9.json``).
SHADOWED = "shadowed"

#: Root entries a scan walks for canonical records.
_RECORD_ROOTS = (SOURCES, CAMPAIGNS)
#: The JSON record kinds this module reads, copies and checks: every kind sync may own except
#: 018's blobs (binary, listed by name and size in ``CanonicalScan.blobs``); ledgers never are.
EXACT_KINDS = frozenset({"definition", "manifest", "instance", "scope", "chat", "relation", "knowledge",
                         "accepted", "wrapup", "holdings", "image", "placement", "transcript"})
#: 018's image objects and placements: strict-JSON asset records that never pin a revision.
ASSET_RECORD_KINDS = frozenset({"image", "placement"})
#: The load problem (and sync blocked reason) of an instance id held by two kinds in one campaign.
INSTANCE_ID_CONFLICT = "instance_id_conflict"
_EXTENSION = {media: ext for ext, media in MEDIA.items()}
#: Root entries that are device-local or the library's own marker: never transferred, never reported.
#: ``secrets`` holds 028's pairing records (device credentials).
_LOCAL = frozenset({TXN, CONFLICTS, SYNC, SECRETS, MARKER})


@dataclass(frozen=True)
class CanonicalScan:
    """Every canonical record file of one library, by root-relative path.

    ``blocked`` maps a path (a file, or a directory whose whole subtree is unusable) to a reason:
    ``link``, ``lookalike``, ``unreadable``, ``malformed``, ``unsupported_format`` or
    ``address_mismatch``. ``skipped`` lists entries that are not canonical records.

    ``blobs`` maps each canonical 018 blob file to its size: listed and ``lstat``-ed, never read
    (a blob's bytes never enter a scan). ``pairs`` names every instance file whose id another kind
    also holds in its campaign (``instance_id_conflict``): such a file stays in ``files`` (its pins
    still count) but is unusable for sync, like a blocked one.
    """

    files: Mapping[str, bytes] = field(default_factory=dict)
    blocked: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    blobs: Mapping[str, int] = field(default_factory=dict)
    pairs: Mapping[str, str] = field(default_factory=dict)

    def blocked_reason(self, path: str) -> str | None:
        """Why ``path`` is unusable here: blocked itself, below a blocked directory, or an id pair."""
        if path in self.blocked:
            return self.blocked[path]
        if "" in self.blocked:  # the library root itself could not be listed: nothing in it is known
            return self.blocked[""]
        parts = path.split("/")
        for index in range(1, len(parts)):
            reason = self.blocked.get("/".join(parts[:index]))
            if reason is not None:
                return reason
        return self.pairs.get(path)


def parse_exact(parts: Parts, data: bytes
                ) -> RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord | AssetRecord:
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
            envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord | AssetRecord = \
                parse_source(document)[0]
            if envelope.source != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds the manifest of {envelope.source}")
        elif found[0] == "knowledge":  # 020's create-only events, evidence and log batches
            envelope = parse_knowledge_record(document)[0]
            if envelope.address != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds {envelope.address}")
        elif found[0] in ("scope", "chat", "relation", "accepted", "wrapup", "holdings",
                          "transcript"):  # 107/017/021/023/036
            envelope = parse_scope_record(document)[0]
            if envelope.address != found[1]:
                raise _invalid("address_mismatch", f"{'/'.join(parts)} holds {envelope.address}")
        elif found[0] in ASSET_RECORD_KINDS:  # 018's image objects and placements
            envelope = parse_asset_record(document)[0]
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
    blobs: dict[str, int] = {}
    with access.cached_listings():
        for name, kind in _children(access, (), blocked):
            if kind == "skip" or name in _LOCAL or is_noise(name):
                continue
            if name in _RECORD_ROOTS and kind == "dir":
                _walk(access, (name,), files, blocked, skipped)
            elif name == ASSETS and kind == "dir":
                _walk_blobs(access, blobs, blocked, skipped)
            else:
                skipped.append(name)
    return CanonicalScan(MappingProxyType(files), MappingProxyType(blocked), tuple(sorted(skipped)),
                         MappingProxyType(blobs), MappingProxyType(_instance_pairs([*files, *blocked])))


def _walk_blobs(access: Access, blobs: dict[str, int], blocked: dict[str, str], skipped: list[str]) -> None:
    """018's ``assets/blobs/<xx>/<sha256>.<ext>``: each canonical blob by size, never read. Links,
    look-alike spellings and unlistable folders are blocked as anywhere; a size no blob can have
    (empty, over ``MAX_BLOB_BYTES``) is ``unreadable``; anything else under ``assets/`` is skipped."""
    for name, kind in _children(access, (ASSETS,), blocked):
        if kind == "skip":
            continue
        if name != BLOBS or kind != "dir":
            skipped.append(f"{ASSETS}/{name}")
            continue
        for prefix, prefix_kind in _children(access, (ASSETS, BLOBS), blocked):
            if prefix_kind == "skip":
                continue
            if prefix_kind != "dir":
                skipped.append(f"{ASSETS}/{BLOBS}/{prefix}")
                continue
            for file_name, file_kind in _children(access, (ASSETS, BLOBS, prefix), blocked):
                child = (ASSETS, BLOBS, prefix, file_name)
                text = "/".join(child)
                if file_kind == "skip":
                    continue
                found = classify(child)
                if found is None or found[0] != "blob":
                    skipped.append(text)
                    continue
                if file_kind != "file":  # a directory where a blob belongs
                    blocked[text] = "unreadable"
                    continue
                try:
                    info = lstat_or_none(access.path(child))
                except OSError:
                    blocked[text] = "unreadable"
                    continue
                if info is None:
                    continue
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BLOB_BYTES:
                    blocked[text] = "unreadable"
                    continue
                blobs[text] = info.st_size


def _instance_pairs(paths: list[str]) -> dict[str, str]:
    """Every canonical instance path whose id another kind also holds in its campaign (A2): all
    members of such a group, never one picked, exactly as ``load()`` excludes them."""
    groups: dict[tuple[str, str], set[str]] = {}
    for path in paths:
        found = classify(tuple(path.split("/")))
        if found is not None and found[0] == "instance":
            ref = found[1]
            assert isinstance(ref, InstanceRef)
            groups.setdefault((ref.campaign_id, ref.instance_id), set()).add(path)
    return {path: INSTANCE_ID_CONFLICT for members in groups.values() if len(members) > 1 for path in members}


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
    noise = {name for name in names if is_noise(name) and not _real_identifier(access, parts + (name,))}
    keys: dict[str, int] = {}
    for name in names:
        if name not in noise:
            keys[name_key(name)] = keys.get(name_key(name), 0) + 1
    found = []
    for name in names:
        child = parts + (name,)
        text = "/".join(child)
        if name in noise:
            found.append((name, "skip"))
            continue
        if _misspelled(name):  # the loader's name_mismatch: alone, it is still the folder on a folding filesystem
            blocked[text] = "lookalike"
            if _folded(name) not in names:  # and the canonical spelling every reference uses is unknown too
                blocked["/".join(parts + (_folded(name),))] = SHADOWED
            found.append((name, "skip"))
            continue
        try:
            linked = is_link(access.path(child))
            info = None if linked else lstat_or_none(access.path(child))
        except OSError:  # listed but not inspectable (a folder with read but no search permission)
            blocked[text] = "unreadable"
            found.append((name, "skip"))
            continue
        if linked:
            blocked[text] = "link"
            found.append((name, "skip"))
            continue
        if keys.get(name_key(name), 0) > 1 and not _the_canonical_one(name, names, noise):
            blocked[text] = "lookalike"
            found.append((name, "skip"))
            continue
        if info is None:
            continue
        found.append((name, "dir" if stat.S_ISDIR(info.st_mode) else "file"))
    return found


def _folded(name: str) -> str:
    """``name`` as a case-insensitive or normalizing filesystem compares it (the ``.json`` suffix kept)."""
    return name_key(name.removesuffix(SUFFIX)) + (SUFFIX if name.endswith(SUFFIX) else "")


def _misspelled(name: str) -> bool:
    """Not a spelling the store writes, but one that folds to one (``Campaigns``, ``C9``, ``Wren-a.json``):
    exactly the loader's ``name_mismatch`` test."""
    folded = _folded(name)
    return folded != name and folded.isascii() and folded.lower() == folded and name != SOURCE_DIR


def _valid_name(name: str) -> bool:
    try:
        validate_segment(name, "name")
    except ValueError:
        return False
    return True


def _the_canonical_one(name: str, names: list[str], noise: set[str]) -> bool:
    """In a group of look-alike spellings, the one valid identifier segment (store names are
    lowercase segments, so at most one can be) is the record and stays readable; only the others
    are blocked. A stray empty ``Campaigns`` beside ``campaigns`` never stops the real folder."""
    group = [other for other in names if other not in noise and name_key(other) == name_key(name)]
    return _canonical(name) and sum(_canonical(other) for other in group) == 1


def _canonical(name: str) -> bool:
    """A spelling the store itself writes: a valid identifier segment, or the layout's ``_source``."""
    return name == SOURCE_DIR or _valid_name(name)


def _real_identifier(access: Access, parts: Parts) -> bool:
    """The loader's rule: every file the store writes ends in ``.json``, so a noise-looking name
    (``desktop.ini``, ``test.swp``) is real only as a directory (or link) whose name, as a folding
    filesystem compares it, is a valid identifier: ``Notes.swp`` can be ``world:notes.swp``."""
    try:
        validate_segment(_folded(parts[-1]), "name")
    except ValueError:
        return False
    try:
        info = lstat_or_none(access.path(parts))
        # a link with an identifier name is never noise: the walk then blocks it as a link
        return info is not None and (stat.S_ISDIR(info.st_mode) or is_link(access.path(parts)))
    except OSError:
        return True  # cannot be inspected: never skipped as noise; the walk blocks it as unreadable


#: Deeper than any canonical path can be (a source id is at most ``TEXT_MAX`` characters, so at most
#: half that many segments, plus the fixed folders around them): nothing below can be a record.
_MAX_DEPTH = TEXT_MAX // 2 + 16


def _walk(access: Access, parts: Parts, files: dict[str, bytes], blocked: dict[str, str],
          skipped: list[str]) -> None:
    if len(parts) > _MAX_DEPTH:
        skipped.append("/".join(parts))  # never recursed into: a stray tree cannot stop the scan
        return
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
            if found is None or found[0] not in EXACT_KINDS:  # ledgers, notes, stray files
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


_RECORD_FOLDERS = frozenset({"instances", "chats", "relations", "events", "evidence", "accepted", "wrapups",
                             "holdings", "transcripts"})


def _may_hold_records(access: Access, original: Parts) -> bool:
    """Whether a blocked path could be, or contain, a canonical record (else it cannot pin anything).

    Judged by its folded spelling: on a case-insensitive filesystem ``Campaigns`` or ``C9`` *is* the
    folder the store reads, so a look-alike can hold records exactly as its canonical spelling can.
    """
    if original in ((), ("",)):
        return True  # the root itself: anything could be below it
    parts = tuple(_folded(part) for part in original)
    found = classify(parts)
    if found is not None:
        return found[0] in EXACT_KINDS - ASSET_RECORD_KINDS  # 018 art attaches to entities, never revisions
    if parts[0] == SOURCES:
        if SOURCE_DIR in parts and parts[parts.index(SOURCE_DIR) + 1:][:1] in ((IMAGES,), (PLACEMENTS,)):
            return False  # 018's images and placements attach to entities, never to revisions
        return not _regular_file(access, original)  # by kind, not name: source folder names may hold dots
    if parts[0] != CAMPAIGNS:
        return False  # the library root's other entries never hold records
    if len(parts) < 2:
        return True  # campaigns/ itself
    if _regular_file(access, original):
        return False  # a regular file that is no record path (``README.md``, ``Desktop.ini``) holds none
    return len(parts) == 2 or parts[2] in _RECORD_FOLDERS or parts[2] == SCOPE_FILE


def _regular_file(access: Access, original: Parts) -> bool:
    """Whether ``original`` is certainly a regular file (one that is no canonical record path, here):
    a folder, a link, or anything that cannot be inspected may hold records."""
    try:
        info = lstat_or_none(access.path(original))
    except OSError:
        return False
    return info is not None and stat.S_ISREG(info.st_mode)


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
        if reason == SHADOWED:
            continue  # the look-alike that shadows it is blocked too, and judged by what it is
        if _may_hold_records(access, tuple(path.split("/"))):
            raise RevisionReferenced(f"cannot prove {ref} is unreferenced: {path} is {reason}")
    for path, data in found.files.items():
        envelope = parse_exact(tuple(path.split("/")), data)
        if isinstance(envelope, AssetRecord):
            continue  # 018 art attaches to entities (its provenance text is not a pin)
        if isinstance(envelope, SourceManifest):
            if _etag(data) in allowed:
                continue
            heads = envelope.head_refs()
            if any(head == SourceRef(ref.source, ref.kind, ref.entity_id) and revision == ref.revision
                   for head, revision in heads.items()):
                raise RevisionReferenced(f"{path} names {ref} as its head")
            if envelope.source == ref.source and ref.unpinned() not in heads and _sole_revision(found, ref):
                raise RevisionReferenced(f"{path} has {ref} as its implicit head (its entity's only revision)")
            for link in envelope.links:
                if ref in {_pinned(link.subject), _pinned(link.object)} | _payload_refs(link.extra):
                    raise RevisionReferenced(f"{path} links {ref}")
            if ref in _payload_refs(envelope.extra):
                raise RevisionReferenced(f"{path} pins {ref} in its unknown fields")
        elif isinstance(envelope, (ScopeRecord, KnowledgeRecord)):
            if ref in _payload_refs(envelope.payload) | _payload_refs(envelope.extra):
                raise RevisionReferenced(f"{path} binds {ref}")
        elif isinstance(envelope.address, InstanceRef):
            if ref in (_pinned(envelope.derived_from), _pinned(envelope.base)):
                raise RevisionReferenced(f"{path} is based on {ref}")
            if ref in _payload_refs(envelope.payload) | _payload_refs(envelope.extra):
                raise RevisionReferenced(f"{path} pins {ref} in its payload")
        elif envelope.address != ref and ref in _payload_refs(envelope.payload) | _payload_refs(envelope.extra):
            raise RevisionReferenced(f"{path} pins {ref} in its payload")


def _sole_revision(found: CanonicalScan, ref: SourceRef) -> bool:
    """Whether ``ref`` is the only revision of its entity on this library: then it is the head even
    with no head declared (the loader resolves it so), and an unpinned reference follows it."""
    revisions = set()
    for path in (*found.files, *found.blocked):
        kind = classify(tuple(path.split("/")))
        if kind is not None and kind[0] == "definition" and kind[1].unpinned() == ref.unpinned():  # type: ignore[union-attr]
            revisions.add(kind[1].revision)  # type: ignore[union-attr]
    return revisions == {ref.revision}


def pinned_by(parts: Parts, data: bytes) -> frozenset[SourceRef]:
    """Every pinned revision the record at ``parts`` can mean: exactly what ``check_unreferenced``
    counts as a reference, read from one record (sync's dependency rule, 019)."""
    envelope = parse_exact(parts, data)
    found: set[SourceRef] = set()
    if isinstance(envelope, AssetRecord):
        return frozenset()  # 018 art never pins a revision
    if isinstance(envelope, SourceManifest):
        found |= {head.at(revision) for head, revision in envelope.head_refs().items()}
        for link in envelope.links:
            found |= {ref for ref in (_pinned(link.subject), _pinned(link.object)) if ref is not None}
            found |= _payload_refs(link.extra)
        found |= _payload_refs(envelope.extra)
    elif isinstance(envelope, (ScopeRecord, KnowledgeRecord)):
        found |= _payload_refs(envelope.payload) | _payload_refs(envelope.extra)
    else:
        if isinstance(envelope.address, InstanceRef):
            found |= {ref for ref in (_pinned(envelope.derived_from), _pinned(envelope.base)) if ref is not None}
        found |= _payload_refs(envelope.payload) | _payload_refs(envelope.extra)
        found.discard(envelope.address)  # type: ignore[arg-type]
    return frozenset(ref for ref in found if _storable(definition_parts, ref))


def _storable(parts_of, ref) -> bool:  # type: ignore[no-untyped-def]
    """A reference no library can store (a reserved device name, say) names nothing that could exist."""
    try:
        parts_of(ref)
    except (ValueError, UnstorableIdentifier):
        return False
    return True


def instances_named(parts: Parts, data: bytes) -> frozenset[InstanceRef]:
    """The campaign instances a scope, chat, relation or 020 knowledge record names anywhere in its
    payload (107's roster actors, 017 endpoints, 020 holders and participants): sync never delivers
    such a record to a device that lacks one of them."""
    return frozenset(ref for ref in _named(parts, data, InstanceRef.parse) if _storable(instance_parts, ref))


def chats_named(parts: Parts, data: bytes) -> frozenset[ChatRef]:
    """The chats a scope, chat, relation or 020 knowledge record names anywhere in its payload
    (020 records the chat a batch came from): sync never delivers such a record without them."""
    return frozenset(ref for ref in _named(parts, data, ChatRef.parse) if _storable(chat_parts, ref))


def _named(parts: Parts, data: bytes, parse) -> set:  # type: ignore[no-untyped-def,type-arg]
    """Every ``campaign:`` text in the record's payload or extra that ``parse`` accepts."""
    envelope = parse_exact(parts, data)
    if not isinstance(envelope, (ScopeRecord, KnowledgeRecord)):
        return set()
    found: set = set()  # type: ignore[type-arg]
    stack: list[object] = [envelope.payload, envelope.extra]
    while stack:
        item = stack.pop()
        if isinstance(item, str) and item.startswith("campaign:"):
            try:
                found.add(parse(item))
            except ValueError:
                pass
        elif isinstance(item, Mapping):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


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


# -- structural readings of 018 art (sync's dependency rule; raw text only: the store never imports
# grimoire.images, and the payload schema id is not interpreted) ----------------------------------

def _payload(data: bytes) -> Mapping[str, object] | None:
    try:
        payload = parse_strict(data).get("payload")
    except JsonError:
        return None
    return payload if isinstance(payload, Mapping) else None


def image_blob(data: bytes) -> Parts | None:
    """The blob an image object's bytes name (``payload.blob.sha256`` and ``media_type``), or ``None``
    when that is absent, not text, or names no storable blob."""
    blob = (_payload(data) or {}).get("blob")
    if not isinstance(blob, Mapping):
        return None
    sha256, media = blob.get("sha256"), blob.get("media_type")
    if not isinstance(sha256, str) or not isinstance(media, str) or media not in _EXTENSION:
        return None
    try:
        return blob_parts(sha256, _EXTENSION[media])
    except (ValueError, UnstorableIdentifier):
        return None


def placement_target(data: bytes) -> tuple[str | None, ImageRef | None, bool]:
    """``(mode, image, readable)`` of a placement's bytes: ``readable`` is false when ``mode`` is not
    text or an ``image`` is present that is no image reference."""
    payload = _payload(data)
    if payload is None:
        return None, None, False
    mode, image = payload.get("mode"), payload.get("image")
    if not isinstance(mode, str):
        return None, None, False
    if image is None:
        return mode, None, True
    try:
        return mode, ImageRef.parse(image), True
    except ValueError:
        return mode, None, False


def instance_derivation(derived_from: object, base: object) -> tuple[SourceRef | None, str | None]:
    """The unpinned entity an instance derives from (``derived_from``, else ``base``) or a problem
    code (``invalid_derivation``, ``ambiguous_derivation``): 018's one derivation rule (W7), shared
    by ``grimoire.imagestore`` and sync so they can never disagree."""
    entities = set()
    for text in (derived_from, base):
        if text is None:
            continue
        try:
            entities.add(SourceRef.parse(text).unpinned())
        except ValueError:
            return None, "invalid_derivation"
    if len(entities) > 1:
        return None, "ambiguous_derivation"
    return (entities.pop(), None) if entities else (None, None)


def instance_derivation_of(data: bytes) -> tuple[SourceRef | None, str | None]:
    """``instance_derivation`` of an instance file's raw bytes (unreadable: ``invalid_derivation``)."""
    try:
        document = parse_strict(data)
    except JsonError:
        return None, "invalid_derivation"
    return instance_derivation(document.get("derived_from"), document.get("base"))


__all__ = ["ASSET_RECORD_KINDS", "CanonicalScan", "INSTANCE_ID_CONFLICT", "check_unreferenced", "image_blob",
           "instance_derivation", "instance_derivation_of", "instances_named", "parse_exact", "pinned_by",
           "placement_target", "previous_of", "scan"]
