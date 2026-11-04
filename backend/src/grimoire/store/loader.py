"""Reopen a library into a ``DefinitionIndex`` plus a report of everything it left out.

Loading never writes. Damage is contained as locally as blame allows:

- a bad record file is skipped and reported, but still counts as a revision;
- an entity whose head is missing or bad, or whose id is reused across kinds,
  is dropped and reported while the rest of its source loads;
- a bad link is dropped and reported (in-source links are registered before
  cross-source ones, so another source cannot get a world's own link blamed);
- a source whose manifest is unreadable, or which claims another source's
  records or links, is excluded.

References are never rebound: every unresolvable one is reported verbatim.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import (
    AcceptedRef, ChatRef, IdentityConflict, InstanceRef, KnowledgeRef, RelationRef, ScopeRef, SourceRef, WrapUpRef,
    validate_segment,
)
from grimoire.core.index import DIRECTED_RELATIONS, DefinitionIndex, DefinitionLink, SourceDescriptor

from .diskops import is_link, lstat_or_none
from .engine import Access
from .envelope import (
    AssetRecord, EnvelopeError, KnowledgeRecord, LinkSpec, RecordEnvelope, ScopeRecord, SourceManifest, link_problem,
    parse_asset_record, parse_knowledge_record, parse_record, parse_scope_record, parse_source,
)
from .errors import (
    AddressMismatch, LinkRefused, MalformedRecord, RecordNotFound, StoreError, UnstorableIdentifier,
    UnsupportedFormat,
)
from .jsonfmt import JsonError, etag, parse_strict
from .layout import (
    ACCEPTED, CAMPAIGNS, CHATS, CONFLICTS, EVENTS, EVIDENCE, EVIDENCE_FAMILIES, IMAGES, INSTANCES, PLACEMENTS, SCOPE,
    MANIFEST, MARKER, RECORDS, RELATIONS, RESERVED_CAMPAIGN_ENTRIES, RESERVED_ROOTS, SOURCE_DIR, SOURCES, SUFFIX, SYNC,
    TXN, WRAPUPS, Parts, classify, is_noise, manifest_parts, name_key,
)

EntityKey = tuple[str, str]  # (kind, entity_id) within one source


@dataclass(frozen=True)
class StoredRecord:
    envelope: RecordEnvelope
    etag: str
    previous: str | None


@dataclass(frozen=True)
class StoredScopeRecord:
    record: ScopeRecord
    etag: str
    previous: str | None


@dataclass(frozen=True)
class StoredKnowledgeRecord:
    record: KnowledgeRecord
    etag: str
    previous: None = None


@dataclass(frozen=True)
class KnowledgeScan:
    """One campaign's ``events/`` and ``evidence/`` trees as found on disk (020); never interpreted.

    ``damaged`` holds a code (``malformed``, ``unsupported_format``, ``address_mismatch``, ``link``)
    for each canonically named file that cannot be read. ``entries`` is the snapshot a writer
    compares before committing: every file and link at a canonical or stray position under both
    trees, as ``(path, type, etag)``. A non-canonical folder appears only in ``problems`` (and any
    problem already refuses an append); OS/editor noise and empty directories are not state.
    """

    records: Mapping[KnowledgeRef, StoredKnowledgeRecord]
    damaged: Mapping[KnowledgeRef, str]
    problems: tuple[LoadProblem, ...]
    entries: tuple[tuple[str, str, str | None], ...]


@dataclass(frozen=True)
class StoredAssetRecord:
    record: AssetRecord
    etag: str
    previous: str | None


@dataclass(frozen=True)
class StoredSource:
    manifest: SourceManifest
    etag: str
    previous: str | None


@dataclass(frozen=True)
class StoredDefinition:
    """What the index holds for one revision: identity, display name and the stored record."""

    ref: SourceRef
    name: str
    envelope: RecordEnvelope
    etag: str


@dataclass(frozen=True)
class LoadProblem:
    path: str
    code: str
    detail: str
    source: str | None = None


@dataclass(frozen=True)
class BrokenRef:
    """A reference that does not resolve, kept exactly as written."""

    path: str
    field: str
    text: str
    reason: str


@dataclass(frozen=True)
class LibraryView:
    index: DefinitionIndex
    sources: Mapping[str, StoredSource]
    instances: Mapping[InstanceRef, StoredRecord]
    problems: tuple[LoadProblem, ...]
    broken: tuple[BrokenRef, ...]
    excluded: tuple[str, ...]
    dropped: tuple[SourceRef, ...]
    #: play scopes and chats (107); an empty mapping where a library has none
    scopes: Mapping[ScopeRef, StoredScopeRecord] = field(default_factory=lambda: MappingProxyType({}))
    chats: Mapping[ChatRef, StoredScopeRecord] = field(default_factory=lambda: MappingProxyType({}))
    #: relation records (017); an empty mapping where a library has none
    relations: Mapping[RelationRef, StoredScopeRecord] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CampaignRecords:
    """One campaign's records of one family (relations, accepted events or wrap-ups) and the problems
    ``load()`` would report under that family's folder."""

    records: Mapping[RelationRef | AcceptedRef | WrapUpRef, StoredScopeRecord]
    problems: tuple[LoadProblem, ...]


#: the name 017 introduced; one campaign's relation records
CampaignRelations = CampaignRecords


def _text(parts: Parts) -> str:
    return "/".join(parts)


# -- reading one file ----------------------------------------------------------

def _document(access: Access, parts: Parts) -> tuple[Mapping[str, object], str]:
    """Parsed JSON and its etag; ``MalformedRecord`` for anything unreadable."""
    try:
        if access.too_large(parts):
            raise MalformedRecord(f"{_text(parts)} is too large to read", None)
        data = access.read(parts)
    except OSError as error:
        raise MalformedRecord(f"{_text(parts)} cannot be read: {error}", None) from error
    except UnstorableIdentifier as error:  # a directory, FIFO or device where a file belongs
        raise MalformedRecord(str(error), None) from error
    if data is None:
        raise RecordNotFound(_text(parts))
    try:
        return parse_strict(data), etag(data)
    except JsonError as error:
        raise MalformedRecord(f"{_text(parts)}: {error}", etag(data)) from None


def read_record(access: Access, parts: Parts) -> StoredRecord:
    """The record at ``parts``; its address must be the one its path implies."""
    document, tag = _document(access, parts)
    try:
        envelope, previous = parse_record(document)
    except EnvelopeError as error:
        kind = UnsupportedFormat if error.code == "unsupported_format" else MalformedRecord
        raise kind(f"{_text(parts)}: {error.detail}", tag) from None
    found = classify(parts)
    if found is None or found[1] != envelope.address:
        raise AddressMismatch(f"{_text(parts)} holds {envelope.address}, not the record its path names", tag,
                              envelope.address)
    return StoredRecord(envelope, tag, previous)


def read_scope_record(access: Access, parts: Parts) -> StoredScopeRecord:
    """The scope or chat record at ``parts``; its address must be the one its path implies."""
    document, tag = _document(access, parts)
    try:
        record, previous = parse_scope_record(document)
    except EnvelopeError as error:
        kind = UnsupportedFormat if error.code == "unsupported_format" else MalformedRecord
        raise kind(f"{_text(parts)}: {error.detail}", tag) from None
    found = classify(parts)
    if found is None or found[1] != record.address:
        raise AddressMismatch(f"{_text(parts)} holds {record.address}, not the record its path names", tag,
                              record.address)
    return StoredScopeRecord(record, tag, previous)


def read_knowledge_record(access: Access, parts: Parts) -> StoredKnowledgeRecord:
    """The knowledge record at ``parts``; its address must be the one its path implies.

    Every refusal carries a fixed detail: the file's own values (a duplicate key, a number) are never echoed.
    """
    try:
        document, tag = _document(access, parts)
    except MalformedRecord as error:
        unreadable = error
    else:
        unreadable = None
    if unreadable is not None:
        raise MalformedRecord(f"{_text(parts)}: not a readable knowledge record (malformed)", unreadable.etag)
    code = None
    try:
        record, _ = parse_knowledge_record(document)
    except EnvelopeError as error:  # keep only the code: the detail (and __context__) can echo file text
        code = error.code
    if code is not None:
        kind = UnsupportedFormat if code == "unsupported_format" else MalformedRecord
        raise kind(f"{_text(parts)}: not a readable knowledge record ({code})", tag)
    found = classify(parts)
    if found is None or found[1] != record.address:
        raise AddressMismatch(f"{_text(parts)} holds another knowledge record than its path names", tag,
                              record.address)
    return StoredKnowledgeRecord(record, tag)


def read_asset_record(access: Access, parts: Parts) -> StoredAssetRecord:
    """The image or placement record at ``parts``; its address must be the one its path implies."""
    document, tag = _document(access, parts)
    try:
        record, previous = parse_asset_record(document)
    except EnvelopeError as error:
        kind = UnsupportedFormat if error.code == "unsupported_format" else MalformedRecord
        raise kind(f"{_text(parts)}: {error.detail}", tag) from None
    found = classify(parts)
    if found is None or found[1] != record.address:
        raise AddressMismatch(f"{_text(parts)} holds {record.address}, not the record its path names", tag,
                              record.address)
    return StoredAssetRecord(record, tag, previous)


def read_source(access: Access, source_id: str) -> StoredSource:
    parts = manifest_parts(source_id)
    document, tag = _document(access, parts)
    try:
        manifest, previous = parse_source(document)
    except EnvelopeError as error:
        kind = UnsupportedFormat if error.code == "unsupported_format" else MalformedRecord
        raise kind(f"{_text(parts)}: {error.detail}", tag) from None
    if manifest.source != source_id:
        raise MalformedRecord(f"{_text(parts)} declares source {manifest.source}, not {source_id}", tag)
    return StoredSource(manifest, tag, previous)


# -- scanning one source --------------------------------------------------------

@dataclass
class SourceScan:
    source_id: str
    stored: StoredSource | None = None
    #: every revision file per entity; ``None`` marks a file that could not be used
    revisions: dict[EntityKey, dict[str, StoredDefinition | None]] = field(default_factory=dict)
    problems: list[LoadProblem] = field(default_factory=list)
    excluded_by: list[LoadProblem] = field(default_factory=list)
    #: an I/O error shaped this scan; it may not repeat, so a store never caches the scan
    io_error: bool = False


def _is_dir(access: Access, parts: Parts) -> bool:
    """A real directory (an unreadable or linked one is not entered)."""
    try:
        info = lstat_or_none(access.path(parts))
    except OSError:
        return False
    return info is not None and stat.S_ISDIR(info.st_mode) and not is_link(access.path(parts))


def _listing(access: Access, parts: Parts, problems: list[LoadProblem], source: str | None) -> list[str]:
    """Names in a directory, skipping noise and reporting (never following) links."""
    names = []
    try:
        listed = access.list_dir(parts)
    except OSError as error:
        problems.append(LoadProblem(_text(parts), "unreadable", f"cannot list: {error}", source))
        return []
    for name in listed:
        # Every file the store writes ends in ".json", so only a directory can be both
        # a noise name and a real identifier (a source `test.swp`, a campaign `desktop.ini`).
        if is_noise(name) and not (_valid_segment(name) and _is_dir(access, parts + (name,))):
            continue
        if is_link(access.path(parts + (name,))):
            problems.append(LoadProblem(_text(parts + (name,)), "link", "links are never followed", source))
            continue
        names.append(name)
    return names


def _has_files(access: Access, parts: Parts) -> bool:
    """Whether a directory holds any file (directories with none are not state)."""
    stack = [parts]
    while stack:
        current = stack.pop()
        if not _is_dir(access, current):
            return True
        try:
            names = access.list_dir(current)
        except OSError:
            return True
        for name in names:
            if is_link(access.path(current + (name,))):
                return True
            stack.append(current + (name,))
    return False


def _unrecognized(access: Access, parts: Parts, problems: list[LoadProblem], source: str | None) -> None:
    if not _has_files(access, parts):
        return
    name = parts[-1]
    folded = name_key(name.removesuffix(SUFFIX)) + (SUFFIX if name.endswith(SUFFIX) else "")
    code = "name_mismatch" if folded != name and folded.isascii() and folded.lower() == folded else "unrecognized"
    problems.append(LoadProblem(_text(parts), code, "not a file this store reads", source))


def scan_source(access: Access, source_id: str, *, assets: bool = False) -> SourceScan:
    """Read one source's manifest and every revision file without judging heads.

    ``assets=True`` also walks ``images/`` and ``placements/`` for stray names (``load()`` reports
    them). Admission never depends on art, so one-source checks and commits skip that walk: its
    cost would grow with the source's pictures.
    """
    scan = SourceScan(source_id)
    base = manifest_parts(source_id)[:-1]
    problems = scan.problems
    try:
        scan.stored = read_source(access, source_id)
    except RecordNotFound:
        pass
    except (MalformedRecord, StoreError, ValueError) as error:  # LinkRefused is a ValueError
        code = "unsupported_format" if isinstance(error, UnsupportedFormat) else "malformed"
        scan.excluded_by.append(LoadProblem(_text(base + (MANIFEST,)), code, str(error), source_id))
        scan.io_error |= _from_io(error)
    if scan.stored is not None:
        try:
            scan.stored.manifest.head_refs()
        except ValueError as error:
            scan.excluded_by.append(LoadProblem(_text(base + (MANIFEST,)), "malformed", str(error), source_id))
        for link in scan.stored.manifest.links:
            found = link_problem(link, source_id)
            if found is not None and found[0] == "ownership":
                scan.excluded_by.append(LoadProblem(_text(base + (MANIFEST,)), "ownership", found[1], source_id))
    if access.presence(base) != "present" or not _is_dir(access, base):
        return scan
    access.guard.check_read(base)  # never list through a linked ancestor (LinkRefused)
    names = _listing(access, base, problems, source_id)  # links are reported here, not entered
    for name in names:
        if name in (IMAGES, PLACEMENTS):
            if assets:
                walk_assets(access, base + (name,), 0 if name == IMAGES else 2,
                            "image" if name == IMAGES else "placement", problems, source_id)
        elif name not in (MANIFEST, RECORDS):
            _unrecognized(access, base + (name,), problems, source_id)
    records = base + (RECORDS,)
    if RECORDS not in names or not _is_dir(access, records):
        return scan
    for kind in _listing(access, records, problems, source_id):
        for entity_id in _listing(access, records + (kind,), problems, source_id) if _is_dir(
                access, records + (kind,)) else ():
            folder = records + (kind, entity_id)
            if not _is_dir(access, folder):
                _unrecognized(access, folder, problems, source_id)
                continue
            for name in _listing(access, folder, problems, source_id):
                parts = folder + (name,)
                found = classify(parts)
                if found is None or found[0] != "definition":
                    _unrecognized(access, parts, problems, source_id)
                    continue
                ref = found[1]
                assert isinstance(ref, SourceRef)
                revisions = scan.revisions.setdefault((ref.kind, ref.entity_id), {})
                revisions[ref.revision] = None  # type: ignore[index]
                try:
                    stored = read_record(access, parts)
                except AddressMismatch as error:
                    claimed = error.claimed
                    if isinstance(claimed, SourceRef) and claimed.source != source_id:
                        scan.excluded_by.append(LoadProblem(
                            _text(parts), "ownership", f"{source_id} holds a record of {claimed.source}",
                            source_id))
                    problems.append(LoadProblem(_text(parts), "address_mismatch", str(error), source_id))
                    continue
                except (StoreError, ValueError, OSError) as error:
                    problems.append(LoadProblem(_text(parts), _code(error), str(error), source_id))
                    scan.io_error |= _from_io(error)
                    continue
                envelope = stored.envelope
                revisions[ref.revision] = StoredDefinition(  # type: ignore[index]
                    ref, envelope.name or "", envelope, stored.etag)
        if not _is_dir(access, records + (kind,)):
            _unrecognized(access, records + (kind,), problems, source_id)
    return scan


def _from_io(error: BaseException) -> bool:
    return isinstance(error, OSError) or isinstance(error.__cause__, OSError)


def contained_scan(access: Access, source_id: str, *, assets: bool = False) -> SourceScan:
    """``scan_source``, with any I/O or link failure excluding only this source."""
    try:
        scan = scan_source(access, source_id, assets=assets)
        scan.io_error |= any(problem.code == "unreadable" for problem in scan.problems)  # an unlistable folder
        return scan
    except (OSError, StoreError, ValueError) as error:
        scan = SourceScan(source_id)
        code = "link" if isinstance(error, LinkRefused) else "unreadable"
        scan.excluded_by.append(LoadProblem(_text(manifest_parts(source_id)[:-1]), code, str(error), source_id))
        scan.io_error = code == "unreadable"
        return scan


def _code(error: BaseException) -> str:
    if isinstance(error, UnsupportedFormat):
        return "unsupported_format"
    if isinstance(error, AddressMismatch):
        return "address_mismatch"
    return "malformed"


def evaluate(scan: SourceScan) -> tuple[list[StoredDefinition], dict[SourceRef, str], set[EntityKey],
                                         list[LoadProblem]]:
    """Definitions and heads to register, entities dropped, and why."""
    problems: list[LoadProblem] = []
    manifest = scan.stored.manifest if scan.stored else None
    declared = manifest.head_refs() if manifest else {}
    where = _text(manifest_parts(scan.source_id))
    kinds: dict[str, set[str]] = {}
    for kind, entity_id in scan.revisions:
        kinds.setdefault(entity_id, set()).add(kind)
    dropped: set[EntityKey] = set()
    for entity_id, used in kinds.items():
        if len(used) > 1:
            dropped |= {(kind, entity_id) for kind in used}
            problems.append(LoadProblem(where, "kind_reuse",
                                        f"{entity_id} is used for {sorted(used)}", scan.source_id))
    for head_ref, revision in declared.items():
        if (head_ref.kind, head_ref.entity_id) not in scan.revisions:
            problems.append(LoadProblem(where, "bad_head", f"head of {head_ref} names no revision file",
                                        scan.source_id))
    definitions: list[StoredDefinition] = []
    heads: dict[SourceRef, str] = {}
    for key, revisions in sorted(scan.revisions.items()):
        if key in dropped:
            continue
        unpinned = SourceRef(scan.source_id, *key)
        head = declared.get(unpinned)
        if head is not None and revisions.get(head) is None:
            dropped.add(key)
            problems.append(LoadProblem(where, "bad_head", f"head of {unpinned} names missing or unreadable "
                                        f"revision {head}", scan.source_id))
            continue
        if head is None and len(revisions) > 1:
            dropped.add(key)
            problems.append(LoadProblem(where, "missing_head",
                                        f"{unpinned} has {len(revisions)} revisions and no head", scan.source_id))
            continue
        good = [definition for definition in revisions.values() if definition is not None]
        if not good:
            dropped.add(key)
            continue
        definitions.extend(good)
        if head is not None:
            heads[unpinned] = head
    return definitions, heads, dropped, problems


# -- links -----------------------------------------------------------------------

def _reaches(graph: dict[SourceRef, set[SourceRef]], start: SourceRef, goal: SourceRef) -> bool:
    seen, stack = {start}, [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        for following in graph.get(node, ()):
            if following not in seen:
                seen.add(following)
                stack.append(following)
    return False


def accept_links(stated: list[tuple[str, int, LinkSpec]], where: Mapping[str, str],
                  problems: list[LoadProblem]) -> list[DefinitionLink]:
    """Valid links; in-source ones first, then cross-source, each by declarer and file order."""
    def order(item: tuple[str, int, LinkSpec]) -> tuple[int, str, int]:
        declarer, position, spec = item
        cross = SourceRef.parse(spec.object).source != declarer
        return (int(cross), declarer, position)

    accepted: list[DefinitionLink] = []
    claims: set[tuple[object, ...]] = set()
    graph: dict[SourceRef, set[SourceRef]] = {}
    usable = []
    for declarer, position, spec in stated:
        found = link_problem(spec, declarer)
        if found is not None:
            problems.append(LoadProblem(where[declarer], "bad_link", found[1], declarer))
        else:
            usable.append((declarer, position, spec))
    for declarer, _, spec in sorted(usable, key=order):
        link = DefinitionLink(SourceRef.parse(spec.subject), spec.relation,  # type: ignore[arg-type]
                              SourceRef.parse(spec.object), declarer)
        claim = (link.identity, declarer)
        if claim in claims:
            problems.append(LoadProblem(where[declarer], "bad_link", f"duplicate link: {link}", declarer))
            continue
        if link.relation in DIRECTED_RELATIONS and _reaches(graph, link.object, link.subject):
            problems.append(LoadProblem(where[declarer], "bad_link", f"closes a derivation cycle: {link}",
                                        declarer))
            continue
        claims.add(claim)
        if link.relation in DIRECTED_RELATIONS:
            graph.setdefault(link.subject, set()).add(link.object)
        accepted.append(link)
    return accepted


# -- the whole library --------------------------------------------------------------

def discover_sources(access: Access, problems: list[LoadProblem] | None = None) -> list[str]:
    """Source ids with a ``_source`` directory, sorted; never through a link."""
    problems = [] if problems is None else problems
    if access.presence((SOURCES,)) != "present" or not _is_dir(access, (SOURCES,)) or is_link(
            access.path((SOURCES,))):
        return []
    found: list[str] = []
    stack: list[tuple[Parts, tuple[str, ...]]] = [((SOURCES,), ())]
    while stack:
        parts, segments = stack.pop()
        for name in _listing(access, parts, problems, None):
            child = parts + (name,)
            if name == SOURCE_DIR and segments and _is_dir(access, child):
                found.append(":".join(segments))
            elif _is_dir(access, child) and classify((SOURCES, *segments, name, SOURCE_DIR, MANIFEST)):
                stack.append((child, segments + (name,)))
            else:
                _unrecognized(access, child, problems, None)
    return sorted(found)


def _valid_segment(name: str) -> bool:
    try:
        validate_segment(name, "name")
    except ValueError:
        return False
    return True


@dataclass
class _Campaigns:
    instances: dict[InstanceRef, StoredRecord] = field(default_factory=dict)
    scopes: dict[ScopeRef, StoredScopeRecord] = field(default_factory=dict)
    chats: dict[ChatRef, StoredScopeRecord] = field(default_factory=dict)
    relations: dict[RelationRef, StoredScopeRecord] = field(default_factory=dict)
    accepted: dict[AcceptedRef, StoredScopeRecord] = field(default_factory=dict)
    wrapups: dict[WrapUpRef, StoredScopeRecord] = field(default_factory=dict)


def _discover_instances(access: Access, problems: list[LoadProblem]) -> _Campaigns:
    found = _Campaigns()
    for campaign in _listing(access, (CAMPAIGNS,), problems, None):
        try:
            _discover_campaign(access, campaign, found, problems)
        except OSError as error:  # an unreadable campaign folder: report it, load the rest
            problems.append(LoadProblem(_text((CAMPAIGNS, campaign)), "unreadable", str(error)))
    return found


def campaign_instances(access: Access, campaign: str) -> tuple[InstanceRef, ...]:
    """Canonically named instance files of one campaign (names only; links refused, never listed)."""
    base = (CAMPAIGNS, campaign, INSTANCES)
    if access.presence(base) != "present":
        return ()
    found = []
    for kind in access.list_dir(base):
        if not _valid_segment(kind) or not _is_dir(access, base + (kind,)):
            continue
        for name in access.list_dir(base + (kind,)):
            hit = classify(base + (kind, name))
            if hit is not None and hit[0] == "instance":
                found.append(hit[1])
    return tuple(sorted(found, key=str))  # type: ignore[arg-type]


def _is_file(access: Access, parts: Parts) -> bool:
    try:
        info = lstat_or_none(access.path(parts))
    except OSError:
        return False
    return info is not None and stat.S_ISREG(info.st_mode)


def campaign_exists(access: Access, campaign: str) -> bool:
    """A canonical, regular ``scope.json`` or at least one canonical, regular instance file (018 §11.4).

    Asset records alone never make a campaign exist, and neither does a stray file.
    """
    base = (CAMPAIGNS, campaign)
    try:
        if access.presence(base + (SCOPE,)) == "present" and _is_file(access, base + (SCOPE,)):
            return True
        refs = campaign_instances(access, campaign)
    except (OSError, StoreError, ValueError):  # a link, an instances/ that is a file, unreadable: none
        return False
    return any(_is_file(access, address_parts_of(ref)) for ref in refs)


def address_parts_of(ref: InstanceRef) -> Parts:
    return (CAMPAIGNS, ref.campaign_id, INSTANCES, ref.kind, ref.instance_id + SUFFIX)


def walk_assets(access: Access, folder: Parts, levels: int, kind: str, problems: list[LoadProblem],
                source: str | None) -> list[object]:
    """Canonical asset files under ``folder`` by listing only (contents are never read).

    ``levels`` directory levels of id segments sit above the files: 0 for ``images/`` and for one
    ``placements/<kind>/<id>/`` folder, 2 for a whole ``placements/``. Anything else is reported
    ``unrecognized`` (or ``name_mismatch``), and a link is reported and never entered.
    """
    if not _is_dir(access, folder):
        _unrecognized(access, folder, problems, source)
        return []
    found: list[object] = []
    for name in _listing(access, folder, problems, source):
        parts = folder + (name,)
        if levels:
            if _valid_segment(name) and _is_dir(access, parts):
                found.extend(walk_assets(access, parts, levels - 1, kind, problems, source))
            else:
                _unrecognized(access, parts, problems, source)
            continue
        hit = classify(parts)
        if hit is not None and hit[0] == kind and not _is_file(access, parts):
            # a record's own name that is not a regular file (even an empty folder) is always reported
            problems.append(LoadProblem(_text(parts), "unrecognized", "not a regular file", source))
            continue
        if hit is None or hit[0] != kind:
            _unrecognized(access, parts, problems, source)
            continue
        found.append(hit[1])
    return found


def _read_scoped(access: Access, parts: Parts, into: dict, problems: list[LoadProblem]) -> None:
    try:
        stored = read_scope_record(access, parts)
    except (StoreError, ValueError, OSError) as error:
        problems.append(LoadProblem(_text(parts), _code(error), str(error)))
        return
    into[stored.record.address] = stored


def _discover_chats(access: Access, base: Parts, found: _Campaigns, problems: list[LoadProblem]) -> None:
    folder = base + (CHATS,)
    for name in _listing(access, folder, problems, None):
        parts = folder + (name,)
        hit = classify(parts)
        if hit is None or hit[0] != "chat":
            _unrecognized(access, parts, problems, None)
            continue
        _read_scoped(access, parts, found.chats, problems)


#: campaign-scoped record families stored one file per record: folder -> (classified kind, ``_Campaigns`` field)
_FAMILIES = {RELATIONS: ("relation", "relations"), ACCEPTED: ("accepted", "accepted"), WRAPUPS: ("wrapup", "wrapups")}
#: families that grow with every played turn (021): like 020's events/evidence, ``load()`` skips them
_HISTORY = frozenset({ACCEPTED, WRAPUPS})


def _discover_family(access: Access, base: Parts, family: str, found: _Campaigns,
                     problems: list[LoadProblem]) -> None:
    kind, attribute = _FAMILIES[family]
    folder = base + (family,)
    for name in _listing(access, folder, problems, None):
        parts = folder + (name,)
        hit = classify(parts)
        if hit is None or hit[0] != kind:
            _unrecognized(access, parts, problems, None)
            continue
        _read_scoped(access, parts, getattr(found, attribute), problems)


def campaign_family(access: Access, campaign: str, family: str) -> CampaignRecords:
    """One campaign's records of one family, contained per file exactly as ``load()`` contains them.

    Reads only ``campaigns/<campaign>/<family>``; links are reported, never followed.
    """
    _, attribute = _FAMILIES[family]
    base = (CAMPAIGNS, campaign)
    problems: list[LoadProblem] = []
    found = _Campaigns()
    folder = base + (family,)
    if is_link(access.path(base)):
        problems.append(LoadProblem(_text(base), "link", "links are never followed"))
    elif access.presence(base) == "present" and not _is_dir(access, base):
        _unrecognized(access, base, problems, None)
    elif access.presence(base) == "present":
        if is_link(access.path(folder)):
            problems.append(LoadProblem(_text(folder), "link", "links are never followed"))
        elif access.presence(folder) == "present":
            if _is_dir(access, folder):
                _discover_family(access, base, family, found, problems)
            else:
                _unrecognized(access, folder, problems, None)
        for name in access.list_dir(base):  # a look-alike ("Relations") is reported, as load() reports it
            if name != family and name_key(name) == family:
                if is_link(access.path(base + (name,))):
                    problems.append(LoadProblem(_text(base + (name,)), "link", "links are never followed"))
                else:
                    _unrecognized(access, base + (name,), problems, None)
    return CampaignRecords(MappingProxyType(getattr(found, attribute)), tuple(problems))


def campaign_relations(access: Access, campaign: str) -> CampaignRecords:
    """One campaign's relation records (017), contained per file exactly as ``load()`` contains them."""
    return campaign_family(access, campaign, RELATIONS)


def _discover_campaign(access: Access, campaign: str, into: _Campaigns, problems: list[LoadProblem]) -> None:
    instances = into.instances
    base = (CAMPAIGNS, campaign)
    if not _is_dir(access, base) or not _valid_segment(campaign):
        _unrecognized(access, base, problems, None)
        return
    for entry in _listing(access, base, problems, None):
        if entry in RESERVED_CAMPAIGN_ENTRIES:
            continue
        if entry == SCOPE:
            _read_scoped(access, base + (entry,), into.scopes, problems)
            continue
        if entry == CHATS and _is_dir(access, base + (entry,)):
            _discover_chats(access, base, into, problems)
            continue
        if entry in _HISTORY and _is_dir(access, base + (entry,)):  # 021's per-turn history: never walked here
            continue
        if entry in _FAMILIES and _is_dir(access, base + (entry,)):
            _discover_family(access, base, entry, into, problems)
            continue
        if entry == IMAGES:
            walk_assets(access, base + (entry,), 0, "image", problems, None)
            continue
        if entry == PLACEMENTS:
            walk_assets(access, base + (entry,), 2, "placement", problems, None)
            continue
        if entry != INSTANCES or not _is_dir(access, base + (entry,)):
            _unrecognized(access, base + (entry,), problems, None)
            continue
        for kind in _listing(access, base + (INSTANCES,), problems, None):
            folder = base + (INSTANCES, kind)
            if not _is_dir(access, folder) or not _valid_segment(kind):
                _unrecognized(access, folder, problems, None)
                continue
            for name in _listing(access, folder, problems, None):
                parts = folder + (name,)
                found = classify(parts)
                if found is None or found[0] != "instance":
                    _unrecognized(access, parts, problems, None)
                    continue
                try:
                    instances[found[1]] = read_record(access, parts)  # type: ignore[index]
                except (StoreError, ValueError, OSError) as error:
                    problems.append(LoadProblem(_text(parts), _code(error), str(error)))


@dataclass
class Admission:
    """Which sources ``load()`` admits, and the index it builds from them."""

    index: DefinitionIndex
    dropped: set[tuple[str, EntityKey]]
    links: list[DefinitionLink]
    excluded: set[str]  # every source left out, including empty folders
    empty: set[str]  # folders with no manifest and no records: not sources at all
    reported: set[str]  # what load() reports as excluded


def admit(scans: Mapping[str, SourceScan], problems: list[LoadProblem]) -> Admission:
    """The one exclusion algorithm: per-source damage, then whole-library ``identity_conflict``s.

    Shared by ``load()`` and ``excluded_sources``, so a one-source check that consults the latter
    can never disagree with ``load()`` about which sources are admitted.
    """
    excluded: set[str] = set()
    for source_id, scan in scans.items():  # scans are never mutated: a store may cache and reuse them
        problems.extend(scan.problems)
        excluded_by = list(scan.excluded_by)
        if scan.stored is None and not excluded_by:
            if scan.revisions:
                excluded_by.append(LoadProblem(_text(manifest_parts(source_id)), "malformed",
                                               "a source with records needs a manifest", source_id))
            else:
                excluded.add(source_id)  # an empty source folder is not a source
                continue
        if excluded_by:
            problems.extend(excluded_by)
            excluded.add(source_id)
    empty = {source_id for source_id, scan in scans.items()
             if scan.stored is None and not scan.excluded_by and not scan.revisions}
    reported_excluded = excluded - empty

    def build() -> tuple[DefinitionIndex, set[tuple[str, EntityKey]], list[DefinitionLink]]:
        definitions: list[StoredDefinition] = []
        heads: dict[SourceRef, str] = {}
        dropped: set[tuple[str, EntityKey]] = set()
        stated: list[tuple[str, int, LinkSpec]] = []
        descriptors = []
        link_problems: list[LoadProblem] = []
        for source_id, scan in scans.items():
            if source_id in excluded:
                continue
            manifest = scan.stored.manifest  # type: ignore[union-attr]
            descriptors.append(SourceDescriptor(source_id, manifest.owner, manifest.title))
            kept, kept_heads, gone, entity_problems = evaluate(scan)
            link_problems.extend(entity_problems)
            definitions += kept
            heads.update(kept_heads)
            dropped |= {(source_id, key) for key in gone}
            stated += [(source_id, position, spec) for position, spec in enumerate(manifest.links)]
        where = {source_id: _text(manifest_parts(source_id)) for source_id in scans}
        links = accept_links(stated, where, link_problems)
        index = DefinitionIndex(descriptors, definitions, heads, links)
        problems.extend(link_problems)
        return index, dropped, links

    checkpoint = len(problems)
    while True:
        del problems[checkpoint:]
        try:
            index, dropped, links = build()
            break
        except IdentityConflict as conflict:
            blamed = set(conflict.sources) - excluded
            if not blamed:
                raise StoreError(f"cannot build the definition index: {conflict}") from conflict
            for source_id in sorted(blamed):
                problems.append(LoadProblem(_text(manifest_parts(source_id)), "identity_conflict",
                                            str(conflict), source_id))
            checkpoint = len(problems)
            excluded |= blamed
            reported_excluded |= blamed
    return Admission(index, dropped, links, excluded, empty, reported_excluded)


def transient(scan: SourceScan) -> bool:
    """Whether an I/O error shaped a scan. It may not repeat, so a store never caches the scan
    (a persistently unreadable source is simply scanned again on every check)."""
    return scan.io_error


def slim(scan: SourceScan) -> SourceScan:
    """The same scan without record payloads: all ``admit`` needs (refs, names, heads, links).

    What a store caches between calls, so a long-lived store does not hold every payload.
    """
    revisions = {key: {revision: None if found is None else dataclasses.replace(found, envelope=None)
                       for revision, found in files.items()}
                 for key, files in scan.revisions.items()}
    return SourceScan(scan.source_id, scan.stored, revisions, list(scan.problems), list(scan.excluded_by),
                      scan.io_error)


_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _entry(path: str, info: os.stat_result | None) -> tuple:
    if info is None:
        return (path, None)
    return (path, info.st_mode, info.st_size, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


def _walkable(info: os.stat_result) -> bool:
    """A real directory: never a link, junction or other reparse point."""
    return stat.S_ISDIR(info.st_mode) and not ((getattr(info, "st_file_attributes", 0) or 0) & _REPARSE)


def source_fingerprint(access: Access, source_id: str) -> tuple:
    """A stat-only fingerprint of what can change whether ``load()`` admits one source.

    Covers the source's ``manifest.json`` and everything under its ``records/`` (names, types,
    sizes, inodes, nanosecond modification and change times); no file is read. Its ``images/``,
    ``placements/`` and the ``_source`` folder's own times are left out: they never change
    admission, so writing art never invalidates a cached exclusion. Links and reparse points are
    recorded, never entered; an unlistable folder is recorded as such, so the fingerprint stays
    stable (and cacheable) while the damage persists. Plain ``os.scandir`` keeps this to one
    system call per entry.
    """
    # discovery already validated the id: build the path directly (no per-segment re-validation)
    base = os.path.join(os.fspath(access.root), SOURCES, *source_id.split(":"), SOURCE_DIR)
    try:
        base_info = os.lstat(base)
    except OSError:
        return ((base, None),)
    entries: list[tuple] = [(base, base_info.st_mode)]
    if not _walkable(base_info):
        return tuple(entries)
    try:  # the real spellings: a case-only rename of manifest.json keeps its stat on Windows/macOS
        with os.scandir(base) as listing:
            spellings = sorted(item.name for item in listing
                               if item.name.casefold() in (MANIFEST.casefold(), RECORDS.casefold()))
    except OSError as error:
        spellings = ["unlistable", type(error).__name__]
    entries.append(("names", tuple(spellings)))
    stack: list[str] = []
    for name in (MANIFEST, RECORDS):
        path = os.path.join(base, name)
        try:
            info = os.lstat(path)
        except OSError:
            entries.append((name, None))
            continue
        entries.append(_entry(name, info))
        if name == RECORDS and _walkable(info):
            stack.append(path)
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as listing:
                found = sorted(listing, key=lambda item: item.name)
        except OSError as error:
            entries.append((folder, "unlistable", type(error).__name__))
            continue
        for item in found:
            try:
                info = item.stat(follow_symlinks=False)
            except OSError:
                entries.append((item.path, None))
                continue
            entries.append(_entry(item.path[len(base):], info))
            if _walkable(info):
                stack.append(item.path)
    return tuple(entries)


def load(access: Access) -> LibraryView:
    with access.cached_listings():
        return _load(access)


def _load(access: Access) -> LibraryView:
    problems: list[LoadProblem] = []
    roots = set()
    for name in _listing(access, (), problems, None):  # links are reported here and never entered
        if name in (MARKER, TXN, CONFLICTS, SYNC) or name in RESERVED_ROOTS:
            continue
        if name in (SOURCES, CAMPAIGNS) and _is_dir(access, (name,)):
            roots.add(name)
            continue
        _unrecognized(access, (name,), problems, None)

    scans = {source_id: contained_scan(access, source_id, assets=True) for source_id in
             (discover_sources(access, problems) if SOURCES in roots else [])}
    admitted = admit(scans, problems)
    index, dropped, links = admitted.index, admitted.dropped, admitted.links
    excluded, empty, reported_excluded = admitted.excluded, admitted.empty, admitted.reported

    campaigns = _discover_instances(access, problems) if CAMPAIGNS in roots else _Campaigns()
    instances = campaigns.instances
    broken = _broken(index, links, excluded - empty, dropped, instances)
    sources = {source_id: scan.stored for source_id, scan in scans.items()
               if source_id not in excluded and scan.stored is not None}
    dropped_refs = tuple(sorted((SourceRef(source_id, *key) for source_id, key in dropped), key=str))
    return LibraryView(index, MappingProxyType(sources), MappingProxyType(instances), tuple(problems),
                       tuple(broken), tuple(sorted(reported_excluded)), dropped_refs,
                       scopes=MappingProxyType(campaigns.scopes), chats=MappingProxyType(campaigns.chats),
                       relations=MappingProxyType(campaigns.relations))


def _broken(index: DefinitionIndex, links: list[DefinitionLink], excluded: set[str],
            dropped: set[tuple[str, EntityKey]], instances: Mapping[InstanceRef, StoredRecord]) -> list[BrokenRef]:
    broken: list[BrokenRef] = []

    def reason(ref: SourceRef) -> str | None:
        found = index.check(ref)
        if found is None:
            return None
        if ref.source in excluded:
            return "excluded_source"
        if (ref.source, (ref.kind, ref.entity_id)) in dropped:
            return "entity_dropped"
        return found.reason

    for link in links:  # accepted links only; rejected ones are already reported as bad_link
        for label in ("subject", "object"):
            ref = getattr(link, label)
            why = reason(ref)
            if why is not None:
                broken.append(BrokenRef(_text(manifest_parts(link.declared_by)), f"links.{label}", str(ref), why))
    for address, stored in sorted(instances.items(), key=lambda item: str(item[0])):
        where = "/".join((CAMPAIGNS, address.campaign_id, INSTANCES, address.kind, address.instance_id + SUFFIX))
        envelope = stored.envelope
        for label in ("derived_from", "base"):
            text = getattr(envelope, label)
            if text is None:
                continue
            try:
                ref = SourceRef.parse(text)
            except ValueError:
                broken.append(BrokenRef(where, label, text, "invalid_reference"))
                continue
            if label == "base" and not ref.pinned:
                broken.append(BrokenRef(where, label, text, "invalid_reference"))
                continue
            why = reason(ref)
            if why is not None:
                broken.append(BrokenRef(where, label, text, why))
            elif label == "base" and envelope.base_etag is not None:
                definition = index.resolve(ref)
                if getattr(definition, "etag", None) != envelope.base_etag:
                    broken.append(BrokenRef(where, "base_etag", envelope.base_etag, "base_drift"))
    return broken


# -- one campaign's events and evidence (020) ---------------------------------------------------

_KNOWLEDGE_TREES = (EVENTS, EVIDENCE)


class _KnowledgeWalk:
    """Collects one campaign's knowledge files; problem details are fixed text, never file content."""

    def __init__(self, access: Access) -> None:
        self.access = access
        self.records: dict[KnowledgeRef, StoredKnowledgeRecord] = {}
        self.damaged: dict[KnowledgeRef, str] = {}
        self.problems: list[LoadProblem] = []
        self.entries: list[tuple[str, str, str | None]] = []

    def problem(self, parts: Parts, code: str, detail: str) -> None:
        self.problems.append(LoadProblem(_text(parts), code, detail))

    def listing(self, parts: Parts) -> list[str]:
        try:
            return sorted(self.access.list_dir(parts))
        except OSError:
            self.problem(parts, "unreadable", "cannot list this folder")
            return []

    def link(self, parts: Parts) -> None:
        self.problem(parts, "link", "links are never followed")
        self.entries.append((_text(parts), "link", None))
        found = classify(parts)
        if found is not None and found[0] == "knowledge":
            self.damaged[found[1]] = "link"  # type: ignore[index]

    def stray_etag(self, parts: Parts) -> str | None:
        try:
            if self.access.too_large(parts):
                return None
            data = self.access.read(parts)
        except (OSError, StoreError, ValueError):
            return None
        return None if data is None else etag(data)

    def file(self, parts: Parts) -> None:
        access = self.access
        if is_link(access.path(parts)):
            self.link(parts)
            return
        found = classify(parts)
        if found is not None and found[0] == "knowledge":
            ref = found[1]
            try:
                self.records[ref] = read_knowledge_record(access, parts)  # type: ignore[index]
            except (StoreError, ValueError, OSError) as error:
                self.damaged[ref] = _code(error)  # type: ignore[index]
                self.entries.append((_text(parts), "file", getattr(error, "etag", None)))
            else:
                self.entries.append((_text(parts), "file", self.records[ref].etag))  # type: ignore[index]
            return
        if is_noise(parts[-1]) and not _is_dir(access, parts):
            return
        _unrecognized(access, parts, self.problems, None)  # name_mismatch or unrecognized; none if empty
        if not _is_dir(access, parts):
            self.entries.append((_text(parts), "file", self.stray_etag(parts)))

    def tree(self, parts: Parts) -> None:
        access = self.access
        if is_link(access.path(parts)):
            self.link(parts)
            return
        if not _is_dir(access, parts):
            self.problem(parts, "unrecognized", "not a folder this store reads")
            self.entries.append((_text(parts), "file", self.stray_etag(parts)))
            return
        for name in self.listing(parts):
            child = parts + (name,)
            if parts[-1] == EVENTS:
                self.file(child)
            elif is_link(access.path(child)):
                self.link(child)
            elif name in EVIDENCE_FAMILIES and _is_dir(access, child):
                for leaf in self.listing(child):
                    self.file(child + (leaf,))
            else:
                self.file(child)  # a stray file, or an unknown/look-alike family folder

    def campaign(self, campaign: str) -> None:
        access = self.access
        base = (CAMPAIGNS, campaign)
        presence = access.presence(base)  # LinkRefused if campaigns/ itself is a link
        if presence == "absent":
            return
        if presence == "mismatch":
            self.problem(base, "name_mismatch", "only a look-alike campaign folder exists")
            return
        if is_link(access.path(base)):
            self.link(base)
            return
        if not _is_dir(access, base):
            self.problem(base, "unrecognized", "not a folder this store reads")
            return
        for name in self.listing(base):
            if name in _KNOWLEDGE_TREES:
                self.tree(base + (name,))
            elif name_key(name) in _KNOWLEDGE_TREES:  # a look-alike, reported as load() would
                child = base + (name,)
                if is_link(access.path(child)):
                    self.link(child)
                elif not _is_dir(access, child):
                    self.problem(child, "name_mismatch", "a look-alike of a knowledge folder")
                    self.entries.append((_text(child), "file", self.stray_etag(child)))
                elif _has_files(access, child):  # an empty folder is not state
                    self.problem(child, "name_mismatch", "a look-alike of a knowledge folder")


def campaign_knowledge(access: Access, campaign: str) -> KnowledgeScan:
    """Read only ``campaigns/<campaign>/events`` and ``.../evidence``; never follows a link, never writes."""
    walk = _KnowledgeWalk(access)
    walk.campaign(campaign)
    return KnowledgeScan(MappingProxyType(dict(sorted(walk.records.items(), key=lambda item: str(item[0])))),
                         MappingProxyType(dict(sorted(walk.damaged.items(), key=lambda item: str(item[0])))),
                         tuple(sorted(walk.problems, key=lambda p: (p.path, p.code))),
                         tuple(sorted(walk.entries)))
