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

import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import IdentityConflict, InstanceRef, SourceRef, validate_segment
from grimoire.core.index import DIRECTED_RELATIONS, DefinitionIndex, DefinitionLink, SourceDescriptor

from .diskops import is_link, lstat_or_none
from .engine import Access
from .envelope import EnvelopeError, LinkSpec, RecordEnvelope, SourceManifest, link_problem, parse_record, parse_source
from .errors import (
    AddressMismatch, LinkRefused, MalformedRecord, RecordNotFound, StoreError, UnstorableIdentifier,
    UnsupportedFormat,
)
from .jsonfmt import JsonError, etag, parse_strict
from .layout import (
    CAMPAIGNS, CONFLICTS, INSTANCES, MANIFEST, MARKER, RECORDS, RESERVED_CAMPAIGN_ENTRIES, RESERVED_ROOTS,
    SOURCE_DIR, SOURCES, SUFFIX, TXN, Parts, classify, is_noise, manifest_parts, name_key,
)

EntityKey = tuple[str, str]  # (kind, entity_id) within one source


@dataclass(frozen=True)
class StoredRecord:
    envelope: RecordEnvelope
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


def scan_source(access: Access, source_id: str) -> SourceScan:
    """Read one source's manifest and every revision file without judging heads."""
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
        if name not in (MANIFEST, RECORDS):
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
                    continue
                envelope = stored.envelope
                revisions[ref.revision] = StoredDefinition(  # type: ignore[index]
                    ref, envelope.name or "", envelope, stored.etag)
        if not _is_dir(access, records + (kind,)):
            _unrecognized(access, records + (kind,), problems, source_id)
    return scan


def contained_scan(access: Access, source_id: str) -> SourceScan:
    """``scan_source``, with any I/O or link failure excluding only this source."""
    try:
        return scan_source(access, source_id)
    except (OSError, StoreError, ValueError) as error:
        scan = SourceScan(source_id)
        code = "link" if isinstance(error, LinkRefused) else "unreadable"
        scan.excluded_by.append(LoadProblem(_text(manifest_parts(source_id)[:-1]), code, str(error), source_id))
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


def _discover_instances(access: Access, problems: list[LoadProblem]) -> dict[InstanceRef, StoredRecord]:
    instances: dict[InstanceRef, StoredRecord] = {}
    for campaign in _listing(access, (CAMPAIGNS,), problems, None):
        try:
            _discover_campaign(access, campaign, instances, problems)
        except OSError as error:  # an unreadable campaign folder: report it, load the rest
            problems.append(LoadProblem(_text((CAMPAIGNS, campaign)), "unreadable", str(error)))
    return instances


def _discover_campaign(access: Access, campaign: str, instances: dict[InstanceRef, StoredRecord],
                       problems: list[LoadProblem]) -> None:
    base = (CAMPAIGNS, campaign)
    if not _is_dir(access, base) or not _valid_segment(campaign):
        _unrecognized(access, base, problems, None)
        return
    for entry in _listing(access, base, problems, None):
        if entry in RESERVED_CAMPAIGN_ENTRIES:
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


def load(access: Access) -> LibraryView:
    with access.cached_listings():
        return _load(access)


def _load(access: Access) -> LibraryView:
    problems: list[LoadProblem] = []
    roots = set()
    for name in _listing(access, (), problems, None):  # links are reported here and never entered
        if name in (MARKER, TXN, CONFLICTS) or name in RESERVED_ROOTS:
            continue
        if name in (SOURCES, CAMPAIGNS) and _is_dir(access, (name,)):
            roots.add(name)
            continue
        _unrecognized(access, (name,), problems, None)

    scans = {source_id: contained_scan(access, source_id) for source_id in
             (discover_sources(access, problems) if SOURCES in roots else [])}
    excluded: set[str] = set()
    for source_id, scan in scans.items():
        problems.extend(scan.problems)
        if scan.stored is None and not scan.excluded_by:
            if scan.revisions:
                scan.excluded_by.append(LoadProblem(_text(manifest_parts(source_id)), "malformed",
                                                    "a source with records needs a manifest", source_id))
            else:
                excluded.add(source_id)  # an empty source folder is not a source
                continue
        if scan.excluded_by:
            problems.extend(scan.excluded_by)
            excluded.add(source_id)
    empty = {source_id for source_id, scan in scans.items() if scan.stored is None and not scan.excluded_by}
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

    instances = _discover_instances(access, problems) if CAMPAIGNS in roots else {}
    broken = _broken(index, links, excluded - empty, dropped, instances)
    sources = {source_id: scan.stored for source_id, scan in scans.items()
               if source_id not in excluded and scan.stored is not None}
    dropped_refs = tuple(sorted((SourceRef(source_id, *key) for source_id, key in dropped), key=str))
    return LibraryView(index, MappingProxyType(sources), MappingProxyType(instances), tuple(problems),
                       tuple(broken), tuple(sorted(reported_excluded)), dropped_refs)


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
