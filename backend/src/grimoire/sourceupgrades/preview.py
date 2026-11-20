"""Raw-scan previews of removing or updating one source (079).

Every canonical file outside the changed source is read as JSON and walked; any string that
parses as a definition or image ref of that source, and any bare source id under a ``source`` key,
is a reference. Like 042's history scan, this is raw, so a record shape this version does not know
still counts. Each reference is then resolved by its exact qualified id on both sides of the
change. Nothing is matched by name, slug, list position or a lookalike source id.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import ImageRef, SourceRef, validate_source_id
from grimoire.core.index import MissingDefinition
from grimoire.store import CanonicalScan, LibraryStore, LibraryView, StoredDefinition, etag
from grimoire.store.jsonfmt import JsonError, parse_strict
from grimoire.store.layout import SOURCE_DIR, SOURCES, classify, image_parts

from .model import EntityChange, Problem, Reference, SourceChangePreview, SourceUpgradeError

#: nesting the scan follows; stored payloads are far shallower, and a deeper one is not searched
MAX_DEPTH = 64


@dataclass(frozen=True)
class _Side:
    """One side of the change for the named source only; an empty side is a removal."""

    source: str
    view: LibraryView | None = None
    scan: CanonicalScan | None = None
    files: Mapping[str, bytes] = field(default_factory=dict)

    @property
    def manifest_etag(self) -> str | None:
        stored = None if self.view is None else self.view.sources.get(self.source)
        return None if stored is None else stored.etag

    def definition(self, ref: SourceRef) -> StoredDefinition | None:
        if self.view is None or self.source not in self.view.sources:
            return None
        try:
            found = self.view.index.resolve(ref)
        except MissingDefinition:
            return None
        return found if isinstance(found, StoredDefinition) else None

    def heads(self) -> dict[SourceRef, tuple[str, str]]:
        """Each head entity of the source (unpinned) to (revision, name), from the loaded view."""
        if self.view is None or self.source not in self.view.sources:
            return {}
        found = {}
        for ref in self.view.sources[self.source].manifest.head_refs():
            stored = self.definition(ref)
            if stored is not None:
                found[ref] = (stored.ref.revision, stored.name)
        return found

    def file_etag(self, path: str) -> str | None:
        data = self.files.get(path)
        return None if data is None else etag(data)


def _owner(path: str) -> str | None:
    """The source a canonical path belongs to, or ``None`` for a campaign file."""
    parts = path.split('/')
    if parts[0] != SOURCES or SOURCE_DIR not in parts:
        return None
    return ':'.join(parts[1:parts.index(SOURCE_DIR)])


def _record(path: str) -> str:
    try:
        found = classify(tuple(path.split('/')))
    except ValueError:
        found = None
    return 'unknown' if found is None else found[0]


def _references(payload: object, source: str) -> set[tuple[str, str]]:
    """``(kind, text)`` for every string naming ``source``: ``definition``, ``image`` or ``source``."""
    found: set[tuple[str, str]] = set()
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, str):
            if '#image/' in value:
                try:
                    image = ImageRef.parse(value)
                except ValueError:
                    continue
                if image.source == source:
                    found.add(('image', value))
                continue
            try:
                ref = SourceRef.parse(value)
            except ValueError:
                continue
            if ref.source == source:
                found.add(('definition', value))
        elif depth < MAX_DEPTH and isinstance(value, Mapping):
            if value.get('source') == source:
                found.add(('source', source))
            stack.extend((item, depth + 1) for item in (*value.keys(), *value.values()))
        elif depth < MAX_DEPTH and isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
    return found


def _status(before: str | None, after: str | None, before_etag: str | None, after_etag: str | None,
            moved: str) -> str:
    if before is None:
        return 'still_missing' if after is None else 'restored'
    if after is None:
        return 'missing'
    if before != after:
        return moved
    return 'unchanged' if before_etag == after_etag else 'edited'


def _definition_row(path: str, record: str, text: str, old: _Side, new: _Side) -> Reference:
    ref = SourceRef.parse(text)
    was, now = old.definition(ref), new.definition(ref)
    status = _status(was and was.ref.revision, now and now.ref.revision, was and was.etag, now and now.etag,
                     'follows_head')
    newer = None
    if ref.pinned and now is not None:
        head = new.definition(ref.unpinned())
        if head is not None and head.ref.revision != ref.revision:
            newer = head.ref.revision
    return Reference(path, record, text, status, was and was.ref.revision, now and now.ref.revision, newer)


def _etag_row(path: str, record: str, text: str, before: str | None, after: str | None, moved: str) -> Reference:
    return Reference(path, record, text, _status(before, after, before, after, moved), before, after)


def _scan_side(store: LibraryStore, source: str) -> tuple[_Side, CanonicalScan]:
    with store.read_pass():  # one locked pass: the scan and the load see the same library
        scan, view = store.scan(), store.load()
    files = {path: data for path, data in scan.files.items() if _owner(path) == source}
    return _Side(source, view, scan, files), scan


def _preview(store: LibraryStore, source: str, new: _Side, action: str) -> SourceChangePreview:
    try:
        validate_source_id(source)
    except (ValueError, TypeError) as error:
        raise SourceUpgradeError('invalid_source', str(error)) from None
    old, scan = _scan_side(store, source)
    problems = [Problem(path, reason) for path, reason in sorted(scan.blocked.items())]
    for side, label in ((old, 'before'), (new, 'after')):
        if side.view is not None:
            problems.extend(Problem(problem.path, problem.code, label)
                            for problem in side.view.problems if problem.source == source)
    rows: dict[tuple[str, str], Reference] = {}
    for path, data in sorted(scan.files.items()):
        if _owner(path) == source:
            continue
        try:
            payload = parse_strict(data)
        except JsonError:
            problems.append(Problem(path, 'unreadable'))
            continue
        record = _record(path)
        found = _references(payload, source)
        pinned = {SourceRef.parse(text).entity_key for kind, text in found
                  if kind == 'definition' and SourceRef.parse(text).pinned}
        for kind, text in sorted(found):
            if kind == 'definition':
                ref = SourceRef.parse(text)
                if not ref.pinned and ref.entity_key in pinned:
                    # Beside a pin of the same entity (an art layer, an actor's base), an unpinned
                    # mention is that pin's identity, not a follow; the pinned row reports it.
                    continue
                row = _definition_row(path, record, text, old, new)
            elif kind == 'image':
                where = '/'.join(image_parts(ImageRef.parse(text)))
                row = _etag_row(path, record, text, old.file_etag(where), new.file_etag(where), 'edited')
            else:
                row = _etag_row(path, record, text, old.manifest_etag, new.manifest_etag, 'changed')
            rows[(path, text)] = row
    before, after = old.heads(), new.heads()
    entities = []
    for ref in sorted(before.keys() | after.keys(), key=str):
        (was, was_name), (now, now_name) = before.get(ref, (None, None)), after.get(ref, (None, None))
        change = EntityChange(ref, was, now, was_name, now_name)
        if change.status != 'unchanged' or change.renamed:
            entities.append(change)
    edited = []
    for path, data in sorted(old.files.items()):
        found = classify(tuple(path.split('/')))
        if found is not None and found[0] == 'definition' and path in new.files and new.files[path] != data:
            edited.append(found[1])
    return SourceChangePreview(source, action, old.manifest_etag, new.manifest_etag, tuple(entities),
                               tuple(edited), tuple(rows[key] for key in sorted(rows)), tuple(problems))


def preview_source_removal(store: LibraryStore, source: str) -> SourceChangePreview:
    """Every reference from outside ``source`` and what it would reach once ``source`` is gone.

    Disabling a source has the same impact. A source that is already absent may be previewed: its
    leftover references show as ``still_missing``.
    """
    return _preview(store, source, _Side(source), 'remove')


def preview_source_update(store: LibraryStore, source: str, candidate: LibraryStore) -> SourceChangePreview:
    """Compare ``source`` here with the same source id in ``candidate`` (an incoming copy, such as
    an unpacked update, opened read-only). Other sources in ``candidate`` are ignored."""
    try:
        validate_source_id(source)
    except (ValueError, TypeError) as error:
        raise SourceUpgradeError('invalid_source', str(error)) from None
    side, _ = _scan_side(candidate, source)
    if source not in side.view.sources:
        raise SourceUpgradeError('candidate_missing_source', source)
    return _preview(store, source, side, 'update')
